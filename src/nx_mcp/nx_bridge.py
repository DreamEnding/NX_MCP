"""NX-process command executor and manual bridge lifecycle."""

from __future__ import annotations

import base64
import os
import secrets
import subprocess
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
from nx_mcp.workspace import Workspace


class NXOpenExecutor:
    """Executes the certified bridge commands against one live NX session."""

    _MODEL_MUTATIONS = {
        "nx_create_sketch",
        "nx_sketch_line",
        "nx_sketch_rectangle",
        "nx_finish_sketch",
        "nx_extrude",
        "nx_revolve",
        "nx_sketch_arc",
        "nx_hole",
        "nx_boolean",
        "nx_add_component",
        "nx_reposition_component",
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
            "nx_revolve": self._revolve,
            "nx_sketch_arc": self._sketch_arc_legacy,
            "nx_hole": self._hole,
            "nx_boolean": self._boolean,
            "nx_screenshot": self._screenshot,
            "nx_get_bounding_box": self._get_bounding_box,
            "nx_measure_volume": self._measure_volume,
            "nx_add_component": self._add_component,
            "nx_list_components": self._list_components,
            "nx_reposition_component": self._reposition_component,
            "nx_set_view": self._set_view,
            "nx_get_feature_info": self._get_feature_info,
            "nx_list_open_parts": self._list_open_parts,
        }

    def execute(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
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
        undo_mark = None
        try:
            if method in self._MODEL_MUTATIONS:
                undo_mark = self.session.SetUndoMark(
                    self.nxopen.Session.MarkVisibility.Visible,
                    f"NX MCP: {method}",
                )
            result = handler(**params)
            if undo_mark is not None:
                self._undo_marks.append(undo_mark)
            return result
        except NXToolError as error:
            if undo_mark is not None:
                self._rollback(undo_mark, error)
            raise
        except Exception as error:
            if undo_mark is not None:
                self._rollback(undo_mark, error)
            raise NXToolError(
                "NX_API_ERROR",
                str(error),
                nx_code=getattr(error, "ErrorCode", None),
            ) from error

    def _rollback(self, undo_mark: Any, original_error: Exception) -> None:
        try:
            self.session.UndoToMark(undo_mark, None)
        except Exception as rollback_error:
            raise NXToolError(
                "NX_ROLLBACK_FAILED",
                f"Operation failed and rollback also failed: {rollback_error}",
                details={"operation_error": str(original_error)},
            ) from rollback_error

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
        part.Save(
            self.nxopen.BasePart.SaveComponents.FalseValue,
            self.nxopen.BasePart.CloseAfterSave.FalseValue,
        )
        self._undo_marks.clear()
        return {"message": f"Saved part: {self._name(part, 'Part')}"}

    def _close_part(self, save: bool = True) -> dict[str, Any]:
        part = self._work_part()
        part_name = self._name(part, "Part")
        part_id = self._part_id(part)
        if save:
            self._save_part()
        part.Close(
            self.nxopen.BasePart.CloseWholeTree.TrueValue,
            self.nxopen.BasePart.CloseModified.CloseModified,
            None,
        )
        self.objects.invalidate_part(part_id)
        self._undo_marks.clear()
        return {"message": f"Closed part: {part_name}"}

    def _export_step(self, path: str) -> dict[str, Any]:
        part = self._work_part()
        destination = self.workspace.ensure_inside(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._save_part()
        builder = self.session.DexManager.CreateStepCreator()
        try:
            builder.SettingsFile = str(
                Path(os.environ.get("UGII_BASE_DIR", r"C:\Program Files\Siemens\Designcenter2606"))
                / "STEP214UG"
                / "ugstep214.def"
            )
            builder.LayerMask = "1-256"
            builder.InputFile = part.FullPath
            builder.ExportFrom = self.nxopen.StepCreator.ExportFromOption.ExistingPart
            builder.ObjectTypes.Solids = True
            builder.ObjectTypes.Surfaces = True
            builder.ObjectTypes.Curves = False
            builder.OutputFile = str(destination)
            builder.ProcessHoldFlag = True
            builder.Commit()
        finally:
            builder.Destroy()
        if not destination.is_file() or destination.stat().st_size == 0:
            log = destination.with_suffix(".log")
            detail = (
                log.read_text(errors="replace")[-1200:] if log.is_file() else "No translator log"
            )
            raise NXToolError("NX_EXPORT_FAILED", "STEP output was not created: " + detail)
        payload = destination.read_text(errors="replace")
        if not any(
            token in payload for token in ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS", "FACETED_BREP")
        ):
            raise NXToolError("NX_EXPORT_NO_SOLIDS", "STEP file contains no solid BREP entities")
        return {
            "path": str(destination),
            "message": f"Exported and verified STEP file: {destination.name}",
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
        self.session.UndoToMark(self._undo_marks.pop(), None)
        self.objects.invalidate_part(self._part_id(part))
        return {"message": "Undo successful"}

    def _fit_view(self) -> dict[str, Any]:
        part = self._work_part()
        part.ModelingViews.WorkView.Fit()
        return {"message": "View fitted"}

    def _find_sketch(self, name: str) -> Any:
        part = self._work_part()
        for sketch in part.Sketches:
            sketch_name = self._name(sketch, "Sketch")
            if sketch_name == name or sketch_name.endswith(name):
                return sketch
        raise NXToolError("NX_NOT_FOUND", f"Sketch not found: {name}")

    def _revolve(
        self,
        angle: float = 360.0,
        axis: str = "Z",
        sketch_name: str | None = None,
        boolean: str = "none",
        axis_origin: list[float] | None = None,
        axis_direction: list[float] | None = None,
    ) -> dict[str, Any]:
        if angle <= 0 or angle > 360:
            raise NXToolError("NX_INVALID_ARGUMENT", "angle must be greater than 0 and at most 360")
        if not sketch_name:
            raise NXToolError("NX_INVALID_ARGUMENT", "sketch_name is required")
        vectors = {
            "X": (1.0, 0.0, 0.0),
            "Y": (0.0, 1.0, 0.0),
            "Z": (0.0, 0.0, 1.0),
            "-X": (-1.0, 0.0, 0.0),
            "-Y": (0.0, -1.0, 0.0),
            "-Z": (0.0, 0.0, -1.0),
        }
        axis_key = axis.strip().upper()
        if axis_key not in vectors:
            raise NXToolError("NX_INVALID_ARGUMENT", "axis must be X, Y, Z, -X, -Y, or -Z")
        from nx_mcp.hardened import vector as point_vector
        from nx_mcp.visual_tools import unit_normal

        if (axis_origin is None) != (axis_direction is None):
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply both axis_origin and axis_direction")
        if axis_direction is not None and axis != "Z":
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Custom axis cannot be combined with a nondefault principal axis",
            )
        axis_point = (
            point_vector(axis_origin, "axis_origin") if axis_origin is not None else [0.0, 0.0, 0.0]
        )
        axis_vector = (
            unit_normal(axis_direction, "axis_direction")
            if axis_direction is not None
            else vectors[axis_key]
        )
        boolean_types = {
            "none": self.nxopen.GeometricUtilities.BooleanOperation.BooleanType.Create,
            "unite": self.nxopen.GeometricUtilities.BooleanOperation.BooleanType.Unite,
            "subtract": self.nxopen.GeometricUtilities.BooleanOperation.BooleanType.Subtract,
            "intersect": self.nxopen.GeometricUtilities.BooleanOperation.BooleanType.Intersect,
        }
        boolean_key = boolean.strip().lower()
        if boolean_key not in boolean_types:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "boolean must be none, unite, subtract, or intersect"
            )
        part = self._work_part()
        sketch = self._find_sketch(sketch_name)
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
        vector = self.nxopen.Vector3d(*axis_vector)
        origin = part.Points.CreatePoint(self.nxopen.Point3d(*axis_point))
        direction = part.Directions.CreateDirection(origin, vector)
        revolve_axis = part.Axes.CreateAxis(
            origin,
            direction,
            self.nxopen.SmartObject.UpdateOption.WithinModeling,
        )
        builder = part.Features.CreateRevolveBuilder(self.nxopen.Features.Feature.Null)
        try:
            builder.Section = section
            builder.Axis = revolve_axis
            builder.Limits.StartExtend.Value.RightHandSide = "0"
            builder.Limits.EndExtend.Value.RightHandSide = str(angle)
            builder.BooleanOperation.Type = boolean_types[boolean_key]
            if boolean_key != "none":
                bodies = list(part.Bodies)
                if not bodies:
                    raise NXToolError("NX_NO_TARGET_BODY", "No target body is available")
                builder.BooleanOperation.SetTargetBodies(bodies)
            feature = builder.CommitFeature()
        finally:
            builder.Destroy()
        return {
            "feature": self._reference(feature, "feature", part, "Revolve"),
            "angle": angle,
            "axis": axis_key if axis_direction is None else "custom",
            "axis_origin": list(axis_point),
            "axis_direction": list(axis_vector),
            "message": f"Revolved {self._name(sketch, sketch_name)} by {angle} degrees",
        }

    def _sketch_arc_legacy(
        self,
        cx: float,
        cy: float,
        radius: float,
        start_angle: float,
        end_angle: float,
    ) -> dict[str, Any]:
        import math

        if radius <= 0:
            raise NXToolError("NX_INVALID_ARGUMENT", "radius must be greater than zero")
        if start_angle == end_angle:
            raise NXToolError("NX_INVALID_ARGUMENT", "start_angle and end_angle must differ")
        part = self._work_part()
        arc = part.Curves.CreateArc(
            self.nxopen.Point3d(cx, cy, 0.0),
            self.nxopen.Vector3d(1.0, 0.0, 0.0),
            self.nxopen.Vector3d(0.0, 1.0, 0.0),
            radius,
            math.radians(start_angle),
            math.radians(end_angle),
        )
        sketch = self.session.ActiveSketch
        if sketch is None:
            raise NXToolError("NX_NO_ACTIVE_SKETCH", "Create and activate an XY sketch first")
        sketch.AddGeometry(arc, self.nxopen.Sketch.InferConstraintsOption.InferNoConstraints)
        reference = self._reference(arc, "curve", part, "Arc")
        return {
            "object": reference,
            "center": [cx, cy],
            "radius": radius,
            "start_angle": start_angle,
            "end_angle": end_angle,
            "message": "Created arc",
        }

    def _hole(
        self,
        diameter: float,
        depth: float,
        x: float,
        y: float,
        z: float,
    ) -> dict[str, Any]:
        if diameter <= 0 or depth <= 0:
            raise NXToolError("NX_INVALID_ARGUMENT", "diameter and depth must be greater than zero")
        part = self._work_part()
        bodies = list(part.Bodies)
        if not bodies:
            raise NXToolError("NX_NO_TARGET_BODY", "Create a solid body before creating a hole")
        builder = part.Features.CreateCylinderBuilder(self.nxopen.Features.Feature.Null)
        try:
            builder.Origin = self.nxopen.Point3d(x, y, z)
            builder.Direction = self.nxopen.Vector3d(0.0, 0.0, 1.0)
            builder.Diameter.RightHandSide = str(diameter)
            builder.Height.RightHandSide = str(depth)
            builder.BooleanOption.Type = (
                self.nxopen.GeometricUtilities.BooleanOperation.BooleanType.Subtract
            )
            builder.BooleanOption.SetTargetBodies([bodies[0]])
            feature = builder.CommitFeature()
        finally:
            builder.Destroy()
        return {
            "feature": self._reference(feature, "feature", part, "Hole"),
            "diameter": diameter,
            "depth": depth,
            "location": [x, y, z],
            "message": "Created cylindrical hole along +Z",
        }

    def _resolve_body(self, value: str, part: Any) -> Any:
        try:
            return self.objects.resolve(
                value,
                expected_kind="body",
                part_id=self._part_id(part),
            )
        except NXToolError:
            pass
        bodies = list(part.Bodies)
        for index, body in enumerate(bodies, start=1):
            names = {
                self._name(body, f"body_{index}"),
                f"body_{index}",
                str(getattr(body, "JournalIdentifier", "")),
            }
            if value in names:
                return body
        raise NXToolError("NX_NOT_FOUND", f"Body not found: {value}")

    def _boolean(self, boolean_type: str, targets: list[str]) -> dict[str, Any]:
        type_map = {
            "unite": self.nxopen.Features.FeatureBooleanType.Unite,
            "subtract": self.nxopen.Features.FeatureBooleanType.Subtract,
            "intersect": self.nxopen.Features.FeatureBooleanType.Intersect,
        }
        key = boolean_type.strip().lower()
        if key not in type_map:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "boolean_type must be unite, subtract, or intersect"
            )
        if len(targets) < 2:
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "targets must contain the target body followed by at least one tool body",
            )
        part = self._work_part()
        bodies = [self._resolve_body(value, part) for value in targets]
        builder = part.Features.CreateBooleanBuilder(self.nxopen.Features.BooleanFeature.Null)
        try:
            builder.Operation = type_map[key]
            builder.Target = bodies[0]
            builder.Tools.Add(bodies[1:])
            feature = builder.CommitFeature()
        finally:
            builder.Destroy()
        return {
            "feature": self._reference(feature, "feature", part, "Boolean"),
            "boolean_type": key,
            "targets": targets,
            "message": f"Boolean {key} completed",
        }

    def _get_bounding_box(self, body=None):
        import NXOpen.UF

        part = self._work_part()
        bodies = [self._resolve_body(body, part)] if body else list(part.Bodies)
        uf = NXOpen.UF.UFSession.GetUFSession()
        rows = []
        for item in bodies:
            box = list(uf.ModlGeneral.AskBoundingBox(item.Tag))
            rows.append(
                {
                    "body": self._reference(item, "body", part, "Body"),
                    "solid": bool(item.IsSolidBody),
                    "box": box,
                }
            )
        if not rows:
            raise NXToolError("NX_NO_TARGET_BODY", "No bodies in work part")
        low = [min(row["box"][i] for row in rows) for i in range(3)]
        high = [max(row["box"][i + 3] for row in rows) for i in range(3)]
        return {
            "min": low,
            "max": high,
            "dimensions": [b - a for a, b in zip(low, high, strict=False)],
            "units": str(part.PartUnits),
            "bodies": rows,
            "message": "UF bounding boxes; may be conservative for curved geometry",
        }

    def _measure_volume(self, body=None):
        part = self._work_part()
        bodies = [self._resolve_body(body, part)] if body else list(part.Bodies)
        units = [
            part.UnitCollection.FindObject(n)
            for n in ["SquareMilliMeter", "CubicMilliMeter", "Kilogram", "MilliMeter", "Newton"]
        ]
        rows = []
        for item in bodies:
            if not item.IsSolidBody:
                raise NXToolError("NX_NOT_SOLID", "Volume measurement requires solid bodies")
            props = part.MeasureManager.NewMassProperties(units, 0.999, [item])
            try:
                props.InformationUnit = self.nxopen.MeasureBodies.AnalysisUnit.KilogramMillimeter
                rows.append(
                    {
                        "body": self._reference(item, "body", part, "Body"),
                        "volume_mm3": float(props.Volume),
                    }
                )
            finally:
                props.Dispose()
        return {
            "bodies": rows,
            "volume_mm3": sum(r["volume_mm3"] for r in rows),
            "message": "Sum of solid body volumes; overlapping bodies are counted separately",
        }

    def _add_component(self, part_path, name=None):
        part = self._work_part()
        path = self.workspace.ensure_inside(part_path)
        matrix = self.nxopen.Matrix3x3()
        matrix.Xx = matrix.Yy = matrix.Zz = 1.0
        component, status = part.ComponentAssembly.AddComponent(
            str(path),
            "Entire Part",
            name or Path(path).stem,
            self.nxopen.Point3d(0.0, 0.0, 0.0),
            matrix,
            -1,
        )
        try:
            return {
                "component": component.Name,
                "tag": int(component.Tag),
                "path": str(path),
                "message": "Component added at origin",
            }
        finally:
            if status is not None:
                status.Dispose()

    def _list_components(self):
        part = self._work_part()
        root = part.ComponentAssembly.RootComponent
        rows = []

        def walk(parent, depth):
            for comp in parent.GetChildren():
                point, matrix = comp.GetPosition()
                rows.append(
                    {
                        "name": comp.Name,
                        "tag": int(comp.Tag),
                        "depth": depth,
                        "translation": [point.X, point.Y, point.Z],
                        "rotation": [
                            matrix.Xx,
                            matrix.Xy,
                            matrix.Xz,
                            matrix.Yx,
                            matrix.Yy,
                            matrix.Yz,
                            matrix.Zx,
                            matrix.Zy,
                            matrix.Zz,
                        ],
                        "part_path": comp.Prototype.FullPath,
                    }
                )
                walk(comp, depth + 1)

        if root is not None:
            walk(root, 0)
        return {"components": rows, "count": len(rows)}

    def _reposition_component(self, component, dx=0, dy=0, dz=0, rx=0, ry=0, rz=0):
        import math

        part = self._work_part()
        root = part.ComponentAssembly.RootComponent
        matches = [c for c in root.GetChildren() if c.Name == component]
        if len(matches) != 1:
            raise NXToolError(
                "NX_NOT_FOUND", "Component name must match exactly one immediate child"
            )
        a, b, c = [math.radians(v) for v in (rx, ry, rz)]
        sx, cx, sy, cy, sz, cz = (
            math.sin(a),
            math.cos(a),
            math.sin(b),
            math.cos(b),
            math.sin(c),
            math.cos(c),
        )
        matrix = self.nxopen.Matrix3x3()
        values = [
            cz * cy,
            sz * cy,
            -sy,
            cz * sy * sx - sz * cx,
            sz * sy * sx + cz * cx,
            cy * sx,
            cz * sy * cx + sz * sx,
            sz * sy * cx - cz * sx,
            cy * cx,
        ]
        for key, value in zip(
            ("Xx", "Xy", "Xz", "Yx", "Yy", "Yz", "Zx", "Zy", "Zz"), values, strict=False
        ):
            setattr(matrix, key, value)
        part.ComponentAssembly.MoveComponent(matches[0], self.nxopen.Vector3d(dx, dy, dz), matrix)
        return {"component": component, "message": "Applied relative translation and rotation"}

    def _set_view(self, orientation):
        options = {
            "isometric": "Isometric",
            "trimetric": "Trimetric",
            "front": "Front",
            "back": "Back",
            "top": "Top",
            "bottom": "Bottom",
            "left": "Left",
            "right": "Right",
        }
        key = orientation.strip().lower()
        if key not in options:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown view orientation")
        self._work_part().ModelingViews.WorkView.Orient(
            getattr(self.nxopen.View.Canned, options[key]), self.nxopen.View.ScaleAdjustment.Fit
        )
        return {
            "message": "View orientation set",
            "orientation": key,
            "viewport_available": not self.session.IsBatch,
        }

    def _get_feature_info(self, name):
        part = self._work_part()
        try:
            feature = self.objects.resolve(
                name, expected_kind="feature", part_id=self._part_id(part)
            )
        except NXToolError:
            matches = [f for f in part.Features if f.Name == name or f.JournalIdentifier == name]
            if len(matches) != 1:
                raise NXToolError(
                    "NX_NOT_FOUND", "Feature reference is not unique or does not exist"
                ) from None
            feature = matches[0]
        return {
            "name": feature.Name,
            "type": feature.FeatureType,
            "identifier": feature.JournalIdentifier,
            "expressions": [
                {"name": e.Name, "formula": e.RightHandSide} for e in feature.GetExpressions()
            ],
        }

    def _list_open_parts(self):
        return {"parts": [{"name": p.Name, "path": p.FullPath} for p in self.session.Parts]}

    def _screenshot(self, path: str) -> dict[str, Any]:
        destination = self.workspace.ensure_inside(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
  $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
  $bitmap.Save($env:NX_MCP_SCREENSHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
  $graphics.Dispose()
  $bitmap.Dispose()
}
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        environment = dict(os.environ)
        environment["NX_MCP_SCREENSHOT_PATH"] = str(destination)
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0 or not destination.is_file():
            message = (completed.stderr or completed.stdout or "screen capture failed").strip()
            raise NXToolError("NX_SCREENSHOT_FAILED", message)
        return {"path": str(destination), "message": f"Screenshot saved to {destination.name}"}


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
    from nx_mcp.hardened import HardenedExecutor

    executor = HardenedExecutor(
        session,
        NXOpen,
        _detect_nx_version(session),
        Workspace(workspace_root),
        enable_experimental=os.environ.get("NX_MCP_ENABLE_EXPERIMENTAL") == "1",
        enable_journal=os.environ.get("NX_MCP_ENABLE_JOURNAL") == "1",
    )
    token = secrets.token_hex(32)
    dispatcher = MainThreadDispatcher(executor.execute)
    server = BridgeServer(
        dispatcher.call,
        token=token,
        result_directory=Path(workspace_root) / ".nx-mcp" / "bridge-results",
    )
    server.start()
    descriptor = BridgeDescriptor.create(
        server.port,
        executor.nx_version,
        token=token,
    )
    destination = Path(descriptor_path) if descriptor_path else default_descriptor_path()
    descriptor.write(destination)
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
