"""NX-process command executor and manual bridge lifecycle."""

from __future__ import annotations

import inspect
import math
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nx_mcp.bridge import (
    BRIDGE_PROTOCOL_VERSION,
    BridgeDescriptor,
    BridgeServer,
    MainThreadDispatcher,
    ObjectRegistry,
    default_descriptor_path,
)
from nx_mcp.runtime import NXToolError, ObjectKind
from nx_mcp.workspace import Workspace, WorkspaceViolation


class NXOpenExecutor:
    """Executes the certified bridge commands against one live NX session."""

    _MODEL_MUTATIONS = {
        "nx_create_sketch",
        "nx_sketch_line",
        "nx_sketch_rectangle",
        "nx_finish_sketch",
        "nx_extrude",
    }

    def __init__(
        self,
        session: Any,
        nxopen: Any,
        nx_version: str,
        workspace: Workspace,
        *,
        enable_experimental: bool = False,
        enable_journal: bool = False,
    ) -> None:
        self.session = session
        self.nxopen = nxopen
        self.nx_version = nx_version
        self.workspace = workspace
        self.enable_experimental = enable_experimental
        self.enable_journal = enable_experimental and enable_journal
        self.objects = ObjectRegistry()
        self._undo_marks: list[Any] = []
        self._undo_part_id: str | None = None
        self._unsafe_parts: set[str] = set()
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "nx_status": self._status,
            "nx_create_part": self._create_part,
            "nx_open_part": self._open_part,
            "nx_save_part": self._save_part,
            "nx_close_part": self._close_part,
            "nx_export_step": self._export_step,
            "nx_list_sketches": lambda: self._list_objects("Sketches", "sketch"),
            "nx_list_bodies": lambda: self._list_objects("Bodies", "body"),
            "nx_list_features": lambda: self._list_objects("Features", "feature"),
            "nx_create_sketch": self._create_sketch,
            "nx_sketch_line": self._sketch_line,
            "nx_sketch_rectangle": self._sketch_rectangle,
            "nx_finish_sketch": self._finish_sketch,
            "nx_extrude": self._extrude,
            "nx_undo": self._undo,
            "nx_fit_view": self._fit_view,
        }

    def execute(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        part = self._work_part(required=False)
        part_id = self._part_id(part) if part is not None else None
        self._sync_undo_part(part_id)
        if part_id in self._unsafe_parts and not (
            method in {"nx_status", "nx_list_sketches", "nx_list_bodies", "nx_list_features"}
            or (method == "nx_close_part" and params.get("save") is False)
        ):
            raise NXToolError(
                "NX_ROLLBACK_FAILED",
                "This part requires recovery: close without saving and reopen, or restart the bridge.",
            )
        handler = self._handlers.get(method)
        if handler is None:
            if self.enable_experimental:
                from nx_mcp.experimental import execute_legacy

                return execute_legacy(
                    method,
                    params,
                    self.workspace,
                    enable_journal=self.enable_journal,
                )
            raise NXToolError("NX_TOOL_NOT_FOUND", f"Unsupported bridge command: {method}")
        self._validate_params(handler, params)
        undo_mark = None
        try:
            if method in self._MODEL_MUTATIONS:
                self._work_part()
                undo_mark = self.session.SetUndoMark(
                    self.nxopen.Session.MarkVisibility.Visible,
                    f"NX MCP: {method}",
                )
            result = handler(**params)
            current_part = self._work_part(required=False)
            self._sync_undo_part(self._part_id(current_part) if current_part is not None else None)
            if undo_mark is not None:
                self._undo_marks.append(undo_mark)
            return result
        except WorkspaceViolation as error:
            raise NXToolError("NX_PATH_OUTSIDE_WORKSPACE", str(error)) from error
        except NXToolError as error:
            if undo_mark is not None:
                self._rollback(undo_mark, error, part_id)
            raise
        except Exception as error:
            if undo_mark is not None:
                self._rollback(undo_mark, error, part_id)
            raise NXToolError(
                "NX_API_ERROR",
                str(error),
                nx_code=getattr(error, "ErrorCode", None),
            ) from error

    def _sync_undo_part(self, part_id: str | None) -> None:
        if self._undo_part_id != part_id:
            self._undo_marks.clear()
            self._undo_part_id = part_id

    @staticmethod
    def _number(value: Any) -> float:
        try:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError
            return float(value)
        except (ValueError, OverflowError) as error:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Coordinates and distances must be finite numbers"
            ) from error

    def _validate_params(self, handler: Callable[..., Any], params: dict[str, Any]) -> None:
        try:
            bound = inspect.signature(handler).bind(**params)
        except TypeError as error:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Command parameters do not match its signature"
            ) from error
        bound.apply_defaults()
        values = bound.arguments
        for field, choices in (("units", ("mm", "inch")), ("plane", ("XY", "XZ", "YZ"))):
            if field in values and values[field] not in choices:
                raise NXToolError("NX_INVALID_ARGUMENT", f"Invalid {field}")
        for field in ("path", "sketch_id"):
            if field in values and (
                not isinstance(values[field], str)
                or not values[field].strip()
                or "\0" in values[field]
            ):
                raise NXToolError("NX_INVALID_ARGUMENT", f"{field} must be a non-empty string")
        if values.get("name") is not None and not isinstance(values["name"], str):
            raise NXToolError("NX_INVALID_ARGUMENT", "name must be a string or null")
        for field in ("save", "reverse"):
            if field in values and type(values[field]) is not bool:
                raise NXToolError("NX_INVALID_ARGUMENT", f"{field} must be a boolean")
        if "distance" in values and self._number(values["distance"]) <= 0:
            raise NXToolError("NX_INVALID_ARGUMENT", "distance must be greater than zero")
        points = {}
        for field in ("start", "end", "corner1", "corner2"):
            if field in values:
                point = values[field]
                if not isinstance(point, dict) or set(point) != {"x", "y"}:
                    raise NXToolError("NX_INVALID_ARGUMENT", f"{field} must contain x and y")
                points[field] = (self._number(point["x"]), self._number(point["y"]))
        if "start" in points and points["start"] == points["end"]:
            raise NXToolError("NX_INVALID_ARGUMENT", "Line length must be non-zero")
        if "corner1" in points and any(
            a == b for a, b in zip(points["corner1"], points["corner2"], strict=True)
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Rectangle width and height must be non-zero")
        if "sketch_id" in values:
            self.objects.resolve(
                values["sketch_id"],
                expected_kind="sketch",
                part_id=self._part_id(self._work_part()),
            )

    def _rollback(self, undo_mark: Any, original_error: Exception, part_id: str | None) -> None:
        try:
            self.session.UndoToMark(undo_mark, None)
            self.session.DeleteUndoMark(undo_mark, None)
        except Exception as rollback_error:
            self._undo_marks.clear()
            if part_id is not None:
                self._unsafe_parts.add(part_id)
            raise NXToolError(
                "NX_ROLLBACK_FAILED",
                "Operation failed and rollback requires recovery: close without saving and reopen, or restart the bridge.",
                details={
                    "operation_error": str(original_error),
                    "rollback_error": str(rollback_error),
                },
            ) from rollback_error
        finally:
            if part_id is not None:
                self.objects.invalidate_part(part_id)

    def _work_part(self, *, required: bool = True) -> Any | None:
        part = getattr(self.session.Parts, "Work", None)
        if part is None and required:
            raise NXToolError(
                "NX_NO_WORK_PART",
                "No work part is open.",
                suggestion="Use nx_open_part or nx_create_part first.",
            )
        return part

    @staticmethod
    def _name(value: Any, fallback: str) -> str:
        name = getattr(value, "Name", fallback)
        return str(name() if callable(name) else name)

    @staticmethod
    def _part_id(part: Any) -> str:
        native_id = getattr(part, "Tag", None)
        return f"part_{native_id if native_id is not None else id(part)}"

    def _reference(self, value: Any, kind: ObjectKind, part: Any, fallback: str) -> dict[str, Any]:
        return self.objects.register(
            value,
            kind=kind,
            name=self._name(value, fallback),
            part_id=self._part_id(part),
        ).model_dump()

    def _status(self) -> dict[str, Any]:
        part = self._work_part(required=False)
        return {
            "connected": True,
            "nx_version": self.nx_version,
            "bridge_protocol": BRIDGE_PROTOCOL_VERSION,
            "active_part": self._reference(part, "part", part, "Part") if part else None,
        }

    def _list_objects(self, collection_name: str, kind: ObjectKind) -> dict[str, Any]:
        part = self._work_part()
        collection = getattr(part, collection_name)
        values = list(collection)
        return {
            "objects": [self._reference(value, kind, part, kind.title()) for value in values],
            "message": f"Found {len(values)} {kind}(s).",
        }

    def _create_part(self, path: str, units: str = "mm") -> dict[str, Any]:
        if units not in {"mm", "inch"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "units must be 'mm' or 'inch'")
        destination = self.workspace.ensure_inside(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        builder = self.session.Parts.FileNew()
        try:
            builder.TemplateFileName = (
                "model-plain-1-mm-template.prt"
                if units == "mm"
                else "model-plain-1-inch-template.prt"
            )
            builder.Units = (
                self.nxopen.Part.Units.Millimeters
                if units == "mm"
                else self.nxopen.Part.Units.Inches
            )
            builder.NewFileName = str(destination)
            builder.DisplayPartOption = self.nxopen.DisplayPartOption.AllowAdditional
            part = builder.Commit() or self._work_part()
        finally:
            builder.Destroy()
        return {
            "part": self._reference(part, "part", part, destination.stem),
            "message": f"Created part: {self._name(part, destination.stem)}",
        }

    def _open_part(self, path: str) -> dict[str, Any]:
        source = self.workspace.ensure_inside(path)
        if not source.is_file():
            raise NXToolError("NX_FILE_NOT_FOUND", f"Part file does not exist: {source.name}")
        load_status = None
        try:
            part, load_status = self.session.Parts.OpenBaseDisplay(str(source))
        finally:
            if load_status is not None:
                load_status.Dispose()
        work_part = self._work_part(required=False)
        if work_part is not None:
            part = work_part
        return {
            "part": self._reference(part, "part", part, source.stem),
            "message": f"Opened part: {self._name(part, source.stem)}",
        }

    def _save_part(self) -> dict[str, Any]:
        part = self._work_part()
        path = getattr(part, "FullPath", "")
        if not isinstance(path, str) or not path or not Path(path).is_absolute():
            raise NXToolError("NX_INVALID_ARGUMENT", "The work part has no absolute save path")
        self.workspace.ensure_inside(path)
        save_status = None
        try:
            save_status = part.Save(
                self.nxopen.BasePart.SaveComponents.FalseValue,
                self.nxopen.BasePart.CloseAfterSave.FalseValue,
            )
        finally:
            if save_status is not None:
                save_status.Dispose()
        self._undo_marks.clear()
        return {"message": f"Saved part: {self._name(part, 'Part')}"}

    def _close_part(self, save: bool = True) -> dict[str, Any]:
        part = self._work_part()
        part_name = self._name(part, "Part")
        part_id = self._part_id(part)
        if save:
            self._save_part()
        part.Close(
            self.nxopen.BasePart.CloseWholeTree.FalseValue,
            self.nxopen.BasePart.CloseModified.CloseModified,
            None,
        )
        self.objects.invalidate_part(part_id)
        self._unsafe_parts.discard(part_id)
        self._undo_marks.clear()
        return {"message": f"Closed part: {part_name}"}

    def _export_step(self, path: str) -> dict[str, Any]:
        part = self._work_part()
        destination = self.workspace.ensure_inside(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        builder = self.session.DexManager.CreateStepCreator()
        try:
            # NXOpen does not load the interactive STEP defaults (ugstep214.def): the
            # layer mask starts empty and every object type is off, so the file would
            # contain no geometry. A saved, unmodified part is translated from
            # InputFile, which also starts empty; a modified part still exports its
            # in-session model. Hold makes Commit wait for the translator.
            builder.InputFile = part.FullPath
            builder.LayerMask = "1-256"
            builder.ObjectTypes.Solids = True
            builder.ObjectTypes.Surfaces = True
            builder.ProcessHoldFlag = True
            builder.OutputFile = str(destination)
            builder.Commit()
        finally:
            builder.Destroy()
        if not destination.is_file() or destination.stat().st_size == 0:
            raise NXToolError(
                "NX_OPERATION_FAILED",
                "The STEP translator wrote no output; "
                f"see {destination.with_suffix('.log').name} in the workspace.",
            )
        return {
            "path": str(destination),
            "message": f"Exported STEP: {destination.name}",
        }

    def _create_sketch(self, plane: str = "XY", name: str | None = None) -> dict[str, Any]:
        normals = {"XY": (0.0, 0.0, 1.0), "XZ": (0.0, 1.0, 0.0), "YZ": (1.0, 0.0, 0.0)}
        if plane not in normals:
            raise NXToolError("NX_INVALID_ARGUMENT", "plane must be XY, XZ, or YZ")
        part = self._work_part()
        normal = self.nxopen.Vector3d(*normals[plane])
        placement_plane = part.Planes.CreatePlane(
            self.nxopen.Point3d(0.0, 0.0, 0.0),
            normal,
            self.nxopen.SmartObject.UpdateOption.WithinModeling,
        )
        builder = part.Sketches.CreateSketchInPlaceBuilder2(self.nxopen.Sketch.Null)
        try:
            builder.PlaneReference = placement_plane
            sketch = builder.Commit()
        finally:
            builder.Destroy()
        if name:
            sketch.SetName(name)
        sketch.Activate(self.nxopen.Sketch.ViewReorient.TrueValue)
        reference = self.objects.register(
            sketch,
            kind="sketch",
            name=self._name(sketch, name or "Sketch"),
            part_id=self._part_id(part),
        )
        return {
            "object": reference.model_dump(),
            "message": f"Created sketch: {reference.name}",
        }

    def _create_sketch_line(
        self,
        sketch: Any,
        part: Any,
        start: dict[str, Any],
        end: dict[str, Any],
    ) -> dict[str, Any]:
        start_point = self.nxopen.Point3d(float(start["x"]), float(start["y"]), 0.0)
        end_point = self.nxopen.Point3d(float(end["x"]), float(end["y"]), 0.0)
        curve = part.Curves.CreateLine(start_point, end_point)
        sketch.AddGeometry(curve, self.nxopen.Sketch.InferConstraintsOption.InferNoConstraints)
        return self._reference(curve, "curve", part, "Line")

    def _sketch_line(
        self,
        sketch_id: str,
        start: dict[str, Any],
        end: dict[str, Any],
    ) -> dict[str, Any]:
        part = self._work_part()
        sketch = self.objects.resolve(
            sketch_id,
            expected_kind="sketch",
            part_id=self._part_id(part),
        )
        reference = self._create_sketch_line(sketch, part, start, end)
        return {"object": reference, "message": f"Created line: {reference['name']}"}

    def _sketch_rectangle(
        self,
        sketch_id: str,
        corner1: dict[str, Any],
        corner2: dict[str, Any],
    ) -> dict[str, Any]:
        part = self._work_part()
        sketch = self.objects.resolve(
            sketch_id,
            expected_kind="sketch",
            part_id=self._part_id(part),
        )
        x1, y1 = float(corner1["x"]), float(corner1["y"])
        x2, y2 = float(corner2["x"]), float(corner2["y"])
        if x1 == x2 or y1 == y2:
            raise NXToolError("NX_INVALID_ARGUMENT", "Rectangle width and height must be non-zero")
        corners = [
            {"x": x1, "y": y1},
            {"x": x2, "y": y1},
            {"x": x2, "y": y2},
            {"x": x1, "y": y2},
        ]
        references = [
            self._create_sketch_line(sketch, part, corners[index], corners[(index + 1) % 4])
            for index in range(4)
        ]
        return {"objects": references, "message": "Created sketch rectangle"}

    def _finish_sketch(self, sketch_id: str) -> dict[str, Any]:
        part = self._work_part()
        sketch = self.objects.resolve(
            sketch_id,
            expected_kind="sketch",
            part_id=self._part_id(part),
        )
        sketch.Deactivate(
            self.nxopen.Sketch.ViewReorient.TrueValue,
            self.nxopen.Sketch.UpdateLevel.Model,
        )
        reference = self.objects.register(
            sketch,
            kind="sketch",
            name=self._name(sketch, "Sketch"),
            part_id=self._part_id(part),
        )
        return {"object": reference.model_dump(), "message": f"Finished sketch: {reference.name}"}

    def _extrude(self, sketch_id: str, distance: float, reverse: bool = False) -> dict[str, Any]:
        if distance <= 0:
            raise NXToolError("NX_INVALID_ARGUMENT", "distance must be greater than zero")
        part = self._work_part()
        sketch = self.objects.resolve(
            sketch_id,
            expected_kind="sketch",
            part_id=self._part_id(part),
        )
        bodies_before = list(part.Bodies)
        section = part.Sections.CreateSection()
        rule = part.ScRuleFactory.CreateRuleCurveFeature(
            [sketch.Feature],
            self.nxopen.DisplayableObject.Null,
            part.ScRuleFactory.CreateRuleOptions(),
        )
        section.AddToSection(
            [rule],
            self.nxopen.NXObject.Null,
            self.nxopen.NXObject.Null,
            self.nxopen.NXObject.Null,
            self.nxopen.Point3d(0.0, 0.0, 0.0),
            self.nxopen.Section.Mode.Create,
            False,
        )
        direction = part.Directions.CreateDirection(
            sketch,
            self.nxopen.Sense.Reverse if reverse else self.nxopen.Sense.Forward,
            self.nxopen.SmartObject.UpdateOption.WithinModeling,
        )
        builder = part.Features.CreateExtrudeBuilder(self.nxopen.Features.Feature.Null)
        try:
            builder.Section = section
            builder.Direction = direction
            builder.Limits.StartExtend.Value.RightHandSide = "0"
            builder.Limits.EndExtend.Value.RightHandSide = str(distance)
            builder.BooleanOperation.Type = (
                self.nxopen.GeometricUtilities.BooleanOperation.BooleanType.Create
            )
            builder.AllowSelfIntersectingSection(True)
            feature = builder.CommitFeature()
        finally:
            builder.Destroy()
        feature_bodies = list(feature.GetBodies()) if hasattr(feature, "GetBodies") else []
        if feature_bodies:
            body = feature_bodies[0]
        else:
            new_bodies = [body for body in part.Bodies if body not in bodies_before]
            if not new_bodies:
                raise NXToolError("NX_OPERATION_FAILED", "Extrude did not create a body")
            body = new_bodies[0]
        return {
            "feature": self._reference(feature, "feature", part, "Extrude"),
            "body": self._reference(body, "body", part, "Body"),
            "message": f"Extruded {self._name(sketch, 'Sketch')} by {distance}",
        }

    def _undo(self) -> dict[str, Any]:
        if not self._undo_marks:
            raise NXToolError("NX_UNDO_UNAVAILABLE", "No NX MCP operation is available to undo")
        part = self._work_part()
        self.session.UndoToMark(self._undo_marks[-1], None)
        self._undo_marks.pop()
        self.objects.invalidate_part(self._part_id(part))
        return {"message": "Undo successful"}

    def _fit_view(self) -> dict[str, Any]:
        part = self._work_part()
        part.ModelingViews.WorkView.Fit()
        return {"message": "View fitted"}


@dataclass
class BridgeRuntime:
    server: BridgeServer
    dispatcher: MainThreadDispatcher
    descriptor: BridgeDescriptor
    descriptor_path: Path

    def stop(self) -> None:
        self.dispatcher.stop()
        self.server.stop()
        try:
            current = BridgeDescriptor.read(self.descriptor_path)
            if current.token == self.descriptor.token:
                self.descriptor_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


_runtime: BridgeRuntime | None = None


def _detect_nx_version(session: Any) -> str:
    try:
        value = session.GetEnvironmentVariableValue("UGII_VERSION")
        if value:
            return str(value)
    except Exception:
        pass
    return "NX build unknown"


def start_bridge(
    workspace_root: str | Path,
    *,
    descriptor_path: str | Path | None = None,
    allow_unverified_threading: bool = False,
) -> BridgeDescriptor:
    """Start the Python feasibility bridge from inside NX.

    NXOpen threading must be validated on the target build before this gate is
    enabled for a pilot. If it fails, the agreed fallback is a minimal C# bridge.
    """
    global _runtime
    if _runtime is not None:
        return _runtime.descriptor
    if (
        not allow_unverified_threading
        and os.environ.get("NX_MCP_ALLOW_UNVERIFIED_PYTHON_BRIDGE") != "1"
    ):
        raise RuntimeError(
            "Python bridge execution has not been verified on this NX build. "
            "Set NX_MCP_ALLOW_UNVERIFIED_PYTHON_BRIDGE=1 only for the real-NX feasibility test."
        )

    import NXOpen

    session = NXOpen.Session.GetSession()
    executor = NXOpenExecutor(
        session,
        NXOpen,
        _detect_nx_version(session),
        Workspace(workspace_root),
        enable_experimental=os.environ.get("NX_MCP_ENABLE_EXPERIMENTAL") == "1",
        enable_journal=os.environ.get("NX_MCP_ENABLE_JOURNAL") == "1",
    )
    token = secrets.token_hex(32)
    dispatcher = MainThreadDispatcher(executor.execute)
    server = BridgeServer(dispatcher.call, token=token)
    server.start()
    descriptor = BridgeDescriptor.create(
        server.port,
        executor.nx_version,
        token=token,
    )
    destination = Path(descriptor_path) if descriptor_path else default_descriptor_path()
    try:
        descriptor.write(destination)
    except Exception:
        dispatcher.stop()
        server.stop()
        raise
    _runtime = BridgeRuntime(server, dispatcher, descriptor, destination)
    return descriptor


def pump_bridge(timeout: float = 0.1) -> int:
    """Run pending bridge calls on the NX journal's main thread."""
    if _runtime is None:
        return 0
    return _runtime.dispatcher.drain(timeout=timeout)


def stop_bridge() -> None:
    global _runtime
    if _runtime is not None:
        _runtime.stop()
        _runtime = None
