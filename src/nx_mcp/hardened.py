"""NX 2606 integration: explicit frames, guarded mutations and inspectable results.

NXOpen is accessed only by the existing main-thread dispatcher.
"""

from __future__ import annotations

import inspect
import math
import uuid
from pathlib import Path

from nx_mcp import (
    assembly_documentation_server,
    documentation_editing_server,
    freeform_server,
    manufacturing_server,
    sheet_metal_server,
)
from nx_mcp.advanced_authoring import AdvancedAuthoringMixin
from nx_mcp.annotation_updates import AnnotationUpdatesMixin
from nx_mcp.assembly_documentation import AssemblyDocumentationMixin
from nx_mcp.authoring import AuthoringMixin
from nx_mcp.authoring_server import NON_MODEL as AUTHORING_NON_MODEL
from nx_mcp.authoring_server import READ_ONLY as AUTHORING_READ_ONLY
from nx_mcp.documentation_editing import DocumentationEditingMixin
from nx_mcp.drawing_preferences import DrawingPreferencesMixin
from nx_mcp.engineering import EngineeringMixin
from nx_mcp.exploded_views import ExplodedViewsMixin
from nx_mcp.freeform import FreeformMixin
from nx_mcp.inspection import InspectionMixin
from nx_mcp.inventory import compact_reference, page
from nx_mcp.legacy_repairs import LegacyRepairsMixin
from nx_mcp.manufacturing import ManufacturingMixin
from nx_mcp.nx_bridge import NXOpenExecutor
from nx_mcp.planar_dxf import PlanarDxfMixin
from nx_mcp.recovery import OperationStore, timestamp
from nx_mcp.reference_geometry import ReferenceGeometryMixin
from nx_mcp.release_engineering import ReleaseEngineeringMixin
from nx_mcp.review_tools import ReviewToolsMixin
from nx_mcp.runtime import NXToolError
from nx_mcp.sheet_metal import SheetMetalMixin
from nx_mcp.thread_standards import ThreadStandardsMixin
from nx_mcp.visual_tools import VisualToolsMixin

READ_ONLY = {
    "nx_flat_pattern_orientation_edges",
    "nx_list_reference_sets",
    "nx_list_datums",
    "nx_display_info",
    "nx_list_sections",
    "nx_sketch_diagnostics",
    "nx_view_info",
    "nx_check_interference",
    "nx_check_clearance",
    "nx_status",
    "nx_list_sketches",
    "nx_list_features",
    "nx_list_bodies",
    "nx_list_components",
    "nx_list_open_parts",
    "nx_get_bounding_box",
    "nx_measure_volume",
    "nx_measure_distance",
    "nx_measure_angle",
    "nx_get_feature_info",
    "nx_sketch_info",
    "nx_list_topology",
    "nx_checkpoint_state",
    "nx_capabilities",
    "nx_operation_status",
}
# Files, session lifecycle, and undo itself cannot be reversed by a model undo mark.
NON_MODEL = {
    "nx_copy_project",
    "nx_highlight_collisions",
    "nx_clear_highlights",
    "nx_create_part",
    "nx_open_part",
    "nx_activate_part",
    "nx_close_part",
    "nx_save_part",
    "nx_save_as",
    "nx_export_step",
    "nx_screenshot",
    "nx_render_view",
    "nx_export_drawing_pdf",
    "nx_undo",
    "nx_checkpoint",
    "nx_rollback",
}


def vector(value, name="vector"):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise NXToolError("NX_INVALID_ARGUMENT", name + " must contain three numbers")
    result = [float(v) for v in value]
    if not all(math.isfinite(v) for v in result):
        raise NXToolError("NX_INVALID_ARGUMENT", name + " must be finite")
    return result


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=False))


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def xyz(p):
    return [p.X, p.Y, p.Z]


def rows(m):
    return [[m.Xx, m.Yx, m.Zx], [m.Xy, m.Yy, m.Zy], [m.Xz, m.Yz, m.Zz]]


def matvec(m, p):
    return [dot(row, p) for row in m]


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transpose(m):
    return [list(v) for v in zip(*m, strict=False)]


def add(a, b):
    return [x + y for x, y in zip(a, b, strict=False)]


IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


READ_ONLY.update(
    AUTHORING_READ_ONLY
    | sheet_metal_server.READ_ONLY
    | freeform_server.READ_ONLY
    | manufacturing_server.READ_ONLY
    | assembly_documentation_server.READ_ONLY
    | documentation_editing_server.READ_ONLY
)
NON_MODEL.update(
    AUTHORING_NON_MODEL
    | sheet_metal_server.NON_MODEL
    | freeform_server.NON_MODEL
    | manufacturing_server.NON_MODEL
    | assembly_documentation_server.NON_MODEL
    | documentation_editing_server.NON_MODEL
)


class HardenedExecutor(
    LegacyRepairsMixin,
    PlanarDxfMixin,
    DrawingPreferencesMixin,
    ReleaseEngineeringMixin,
    ReferenceGeometryMixin,
    DocumentationEditingMixin,
    AnnotationUpdatesMixin,
    ThreadStandardsMixin,
    FreeformMixin,
    ManufacturingMixin,
    AssemblyDocumentationMixin,
    SheetMetalMixin,
    ExplodedViewsMixin,
    EngineeringMixin,
    AdvancedAuthoringMixin,
    AuthoringMixin,
    ReviewToolsMixin,
    VisualToolsMixin,
    InspectionMixin,
    NXOpenExecutor,
):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = uuid.uuid4().hex
        self.objects.session_id = self.session_id
        self._part_generations = {}
        self._history = []
        self._checkpoints = {}
        self.store = OperationStore(self.workspace.root)
        self.store.recover(self.session_id)
        self._current_operation = None
        for module in [freeform_server, manufacturing_server, assembly_documentation_server]:
            for name in vars(module):
                if name.startswith("nx_"):
                    self._handlers[name] = getattr(self, "_" + name[3:])
        for name, fn in vars(documentation_editing_server).items():
            if name.startswith("nx_") and inspect.isfunction(fn):
                self._handlers[name] = getattr(self, "_" + name[3:])
        self._handlers.update(
            {
                "nx_flat_pattern_orientation_edges": self._flat_pattern_orientation_edges,
                "nx_list_reference_sets": self._list_reference_sets,
                "nx_create_reference_set": self._create_reference_set,
                "nx_set_component_reference_set": self._set_component_reference_set,
                "nx_list_datums": self._list_datums,
                "nx_set_datum_visibility": self._set_datum_visibility,
                "nx_resolve_geometry": self._resolve_geometry,
                "nx_sheet_metal_schema": self._sheet_metal_schema,
                "nx_create_path_sketch": self._create_path_sketch,
                "nx_add_flat_pattern_view": self._add_flat_pattern_view,
                "nx_sheet_metal_annotation": self._sheet_metal_annotation,
                "nx_sheet_metal_context": self._sheet_metal_context,
                "nx_sheet_metal_feature": self._sheet_metal_feature,
                "nx_sheet_metal_info": self._sheet_metal_info,
                "nx_sheet_metal_defaults": self._sheet_metal_defaults,
                "nx_set_sheet_metal_defaults": self._set_sheet_metal_defaults,
                "nx_export_flat_pattern": self._export_flat_pattern,
                "nx_create_explosion": self._create_explosion,
                "nx_list_explosions": self._list_explosions,
                "nx_explosion_info": self._explosion_info,
                "nx_edit_explosion": self._edit_explosion,
                "nx_show_explosion": self._show_explosion,
                "nx_delete_explosion": self._delete_explosion,
                "nx_shell": self._shell,
                "nx_set_material": self._set_material,
                "nx_render_view": self._render_view,
                "nx_mirror_body": self._mirror_body,
                "nx_create_drawing": self._create_drawing,
                "nx_add_base_view": self._add_base_view,
                "nx_add_projection_view": self._add_projection_view,
                "nx_add_dimension": self._add_dimension,
                "nx_export_drawing_pdf": self._export_drawing_pdf,
                "nx_blend": self._blend,
                "nx_chamfer": self._chamfer,
                "nx_sweep": self._sweep,
                "nx_mate_component": self._mate_component,
                "nx_list_assembly_constraints": self._list_assembly_constraints,
                "nx_assembly_constraint": self._assembly_constraint,
                "nx_edit_assembly_constraint": self._edit_assembly_constraint,
                "nx_material_info": self._material_info,
                "nx_sketch_angle": self._sketch_angle,
                "nx_sketch_tangent": self._sketch_tangent,
                "nx_sketch_symmetry": self._sketch_symmetry,
                "nx_sketch_trim_extend": self._sketch_trim_extend,
                "nx_component_array": self._component_array,
                "nx_draft": self._draft,
                "nx_transform_bodies": self._transform_bodies,
                "nx_mass_properties": self._mass_properties,
                "nx_copy_project": self._copy_project,
                "nx_loft": self._loft,
                "nx_sketch_primitive": self._sketch_primitive,
                "nx_recognize_holes": self._recognize_holes,
                "nx_native_component_pattern": self._native_component_pattern,
                "nx_edit_component_pattern": self._edit_component_pattern,
                "nx_list_component_patterns": self._list_component_patterns,
                "nx_sketch_dimension": self._sketch_dimension,
                "nx_sketch_relation": self._sketch_relation,
                "nx_sketch_conflicts": self._sketch_conflicts,
                "nx_feature_parameters": self._feature_parameters,
                "nx_set_feature_parameters": self._set_feature_parameters,
                "nx_view_info": self._view_info,
                "nx_find_geometry": self._find_geometry,
                "nx_highlight_objects": self._highlight_objects,
                "nx_list_expressions": self._list_expressions,
                "nx_set_expression": self._set_expression,
                "nx_bind_parameter": self._bind_parameter,
                "nx_model_health": self._model_health,
                "nx_rebuild_model": self._rebuild_model,
                "nx_edit_sketch": self._edit_sketch,
                "nx_component_action": self._component_action,
                "nx_pattern_components": self._pattern_components,
                "nx_set_camera": self._set_camera,
                "nx_save_presentation": self._save_presentation,
                "nx_restore_presentation": self._restore_presentation,
                "nx_inspection_report": self._inspection_report,
                "nx_model_summary": self._model_summary,
                "nx_preview_change": self._preview_change,
                "nx_finish_preview": self._finish_preview,
                "nx_display_info": self._display_info,
                "nx_set_display": self._set_display,
                "nx_set_visibility": self._set_visibility,
                "nx_restore_display": self._restore_display,
                "nx_highlight_collisions": self._highlight_collisions,
                "nx_clear_highlights": self._clear_highlights,
                "nx_list_sections": self._list_sections,
                "nx_section_view": self._section_view,
                "nx_section_control": self._section_control,
                "nx_sketch_diagnostics": self._sketch_diagnostics,
                "nx_check_interference": self._check_interference,
                "nx_check_clearance": self._check_clearance,
                "nx_activate_part": self._activate_part,
                "nx_sketch_info": self._sketch_info,
                "nx_edit_feature": self._edit_feature,
                "nx_measure_distance": self._measure_distance,
                "nx_pattern": self._pattern,
                "nx_import_geometry": self._import_geometry,
                "nx_checkpoint": self._checkpoint,
                "nx_checkpoint_state": self._checkpoint_state,
                "nx_rollback": self._rollback_checkpoint,
                "nx_operation_status": lambda operation_id: self.store.get(operation_id),
                "nx_list_topology": self._list_topology,
                "nx_set_component_transform": self._set_component_transform,
                "nx_batch": self._batch,
                "nx_capabilities": self._capabilities,
                "nx_save_as": self._save_as,
                "nx_rename_object": self._rename_object,
                "nx_delete_feature": self._delete_feature,
                "nx_measure_angle": self._measure_angle,
                "nx_sketch_constraint": self._sketch_constraint,
            }
        )

    def _part_id(self, part):
        tag = int(part.Tag)
        if tag not in self._part_generations:
            self._part_generations[tag] = uuid.uuid4().hex
        return "part_" + self.session_id + "_" + self._part_generations[tag]

    def _reference(self, value, kind, part, fallback):
        ref = super()._reference(value, kind, part, fallback)
        ref.update(
            session_id=self.session_id,
            generation_id=self._part_generations[int(part.Tag)],
            owner_part_path=part.FullPath,
            journal_id=str(getattr(value, "JournalIdentifier", "")),
            display_name=str(getattr(value, "Name", "")),
        )
        return ref

    @staticmethod
    def _name(value, fallback):
        return str(
            getattr(value, "Name", "") or getattr(value, "JournalIdentifier", "") or fallback
        )

    def _units(self):
        return (
            "mm"
            if self._work_part().PartUnits == self.nxopen.BasePart.Units.Millimeters
            else "inch"
        )

    def execute(self, method, params):
        params = dict(params)
        supplied_id = params.pop("operation_id", None) if method != "nx_operation_status" else None
        mutable = method not in READ_ONLY
        op_id = supplied_id or ("op_" + uuid.uuid4().hex)
        handler = self._handlers.get(method)
        if handler is None:
            if not self.enable_experimental:
                raise NXToolError("NX_TOOL_NOT_FOUND", method)
            from nx_mcp.experimental import execute_legacy, load_legacy_handlers

            legacy = load_legacy_handlers().get(method)
            if legacy is None:
                raise NXToolError("NX_TOOL_NOT_FOUND", method)
            inspect.signature(legacy).bind(**params)

            def handler(**p):
                return execute_legacy(method, p, self.workspace, enable_journal=self.enable_journal)
        else:
            try:
                inspect.signature(handler).bind(**params)
            except TypeError as e:
                raise NXToolError("NX_INVALID_ARGUMENT", str(e)) from e
        record = None
        mark = None
        part = self._work_part(required=False)
        part_id = self._part_id(part) if part else None
        if mutable:
            try:
                fingerprint = self.store.fingerprint(method, params)
            except (ValueError, TypeError) as error:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Arguments must be finite JSON values"
                ) from error
            existing = self.store.get(op_id)
            if "fingerprint" in existing:
                if existing["fingerprint"] != fingerprint:
                    raise NXToolError(
                        "NX_IDEMPOTENCY_CONFLICT",
                        "operation_id was already used with different arguments",
                        details={"operation_id": op_id},
                    )
                if existing["state"] == "committed":
                    result = dict(existing["result"])
                    result["replayed"] = True
                    if existing.get("reverted_by"):
                        result["warnings"] = result.get("warnings", []) + [
                            "This operation was subsequently reverted by "
                            + existing["reverted_by"]
                            + "; replay does not recreate geometry."
                        ]
                    if existing["session_id"] != self.session_id:
                        result["warnings"] = result.get("warnings", []) + [
                            "Receipt is from an earlier NX session; reacquire object references."
                        ]
                    return result
                raise NXToolError(
                    "NX_OPERATION_" + existing["state"].upper(),
                    "Request was already recorded; inspect nx_operation_status before proceeding",
                    details=existing,
                )
            record = {
                "operation_id": op_id,
                "method": method,
                "fingerprint": fingerprint,
                "session_id": self.session_id,
                "part_id": part_id,
                "state": "running",
                "mutation_outcome": "unknown",
                "started_at": timestamp(),
            }
            self.store.put(record)
        before = {}
        previous = self._current_operation
        self._current_operation = op_id
        try:
            if method == "nx_restore_display":
                self._validate_display_restore(params["restore_id"])
            if (
                mutable
                and method not in NON_MODEL
                and not (method == "nx_import_geometry" and params.get("target") == "new_part")
                and part
            ):
                before = self._snapshot(part)
            if (
                mutable
                and method not in NON_MODEL
                and not (method == "nx_import_geometry" and params.get("target") == "new_part")
            ):
                mark = self.session.SetUndoMark(
                    self.nxopen.Session.MarkVisibility.Visible, "NX MCP: " + method
                )
            self._active_mark = mark
            result = handler(**params)
            if (
                mark is not None
                and part is not None
                and method
                not in {"nx_refresh_annotations", "nx_sheet_metal_annotation", "nx_bend_table"}
            ):
                refreshed = self._refresh_annotations()
                if refreshed["updated_count"]:
                    result["refreshed_annotations"] = refreshed["updated"]
            if result.get("status") == "error":
                raise NXToolError(
                    result.get("code", result.get("error_code", "NX_OPERATION_FAILED")),
                    result.get("message", "Operation failed"),
                )
            receipt_metadata = (
                {key: result.get(key) for key in ("operation_id", "session_id", "mutation_outcome")}
                if method == "nx_operation_status"
                else None
            )
            result = {
                "status": "success",
                **result,
                "operation_id": op_id,
                "session_id": self.session_id,
                "mutation_outcome": "committed" if mutable else "not_applicable",
                "warnings": result.get("warnings", []),
            }
            if receipt_metadata is not None:
                result.update(receipt_metadata)
                result.update(query_operation_id=op_id, query_session_id=self.session_id)
            if part:
                result.setdefault(
                    "units", self._units() if self._work_part(required=False) else None
                )
            if mark is not None:
                after = self._snapshot(part) if part else {}
                result["changes"] = {
                    "created": [v for k, v in after.items() if k not in before],
                    "deleted": [v for k, v in before.items() if k not in after],
                    "modified": result.get("modified"),
                    "modified_tracking": "explicit only; null means not fully tracked",
                }
                self._invalidate_deleted(
                    before,
                    after,
                    invalidate_topology=method
                    not in {
                        "nx_create_explosion",
                        "nx_edit_explosion",
                        "nx_show_explosion",
                        "nx_delete_explosion",
                        "nx_set_camera",
                        "nx_restore_presentation",
                        "nx_set_display",
                        "nx_set_visibility",
                        "nx_set_datum_visibility",
                        "nx_create_reference_set",
                        "nx_set_component_reference_set",
                        "nx_restore_display",
                        "nx_section_view",
                        "nx_section_control",
                        "nx_set_view",
                        "nx_fit_view",
                    },
                )
                self._history.append(
                    {"mark": mark, "part_id": part_id, "operation_id": op_id, "method": method}
                )
        except Exception as error:
            if mutable:
                self._review_epoch = getattr(self, "_review_epoch", 0) + 1
            outcome = "not_started" if mark is None else "partial"
            if mark is not None:
                try:
                    self.session.UndoToMark(mark, None)
                    self.objects.invalidate_part(part_id)
                    self.session.DeleteUndoMark(mark, None)
                    outcome = "rolled_back"
                except Exception as rollback_error:
                    error = NXToolError(
                        "NX_ROLLBACK_FAILED",
                        str(rollback_error),
                        details={"operation_error": str(error)},
                    )
            elif mutable and (method in NON_MODEL or method == "nx_import_geometry"):
                outcome = "unknown"
            err = (
                error
                if isinstance(error, NXToolError)
                else NXToolError(
                    "NX_API_ERROR", str(error), nx_code=getattr(error, "ErrorCode", None)
                )
            )
            if mark is None:
                # Inspections may create temporary native geometry. Preserve a
                # handler's explicit cleanup outcome when no outer rollback ran.
                outcome = err.details.get("mutation_outcome", outcome)
            err.details.update(operation_id=op_id, mutation_outcome=outcome)
            if record:
                record.update(
                    state="failed",
                    mutation_outcome=outcome,
                    error=err.as_dict(),
                    finished_at=timestamp(),
                )
                self.store.put(record)
            raise err from error
        finally:
            self._current_operation = previous
        if mutable and method != "nx_preview_change":
            self._review_epoch = getattr(self, "_review_epoch", 0) + 1
        if record:
            record.update(
                state="committed",
                mutation_outcome="committed",
                result=result,
                finished_at=timestamp(),
            )
            # Receipt persistence is outside the rollback block: a persistence failure must
            # never undo an already reported commit. A running receipt becomes unknown on restart.
            self.store.put(record)
        return result

    def _resolve(self, ref, kinds=None, part=None):
        part = part or self._work_part()
        if ref.startswith("obj_"):
            try:
                value = self.objects.resolve(ref, part_id=self._part_id(part))
            except NXToolError as error:
                if error.code == "NX_OBJECT_NOT_FOUND":
                    raise NXToolError(
                        "NX_OBJECT_STALE", "Reference is not live in this NX session"
                    ) from error
                raise
            entry = self.objects._objects[ref]
            if kinds and entry.reference.kind not in kinds:
                raise NXToolError("NX_OBJECT_TYPE_MISMATCH", "Unsupported object kind")
            return value
        pools = {
            "feature": list(part.Features),
            "body": list(part.Bodies),
            "sketch": list(part.Sketches),
            "component": [c for c, _ in self._walk_components(part)],
            "section": list(part.DynamicSections),
        }
        candidates = []
        for kind, values in pools.items():
            if kinds and kind not in kinds:
                continue
            for v in values:
                if ref.casefold() in {
                    self._name(v, "").casefold(),
                    str(getattr(v, "JournalIdentifier", "")).casefold(),
                } and all(int(c.Tag) != int(v.Tag) for c in candidates):
                    candidates.append(v)
        if len(candidates) != 1:
            raise NXToolError(
                "NX_AMBIGUOUS_REFERENCE" if candidates else "NX_NOT_FOUND",
                "Use an opaque ID; name did not resolve uniquely: " + ref,
            )
        return candidates[0]

    def _resolve_body(self, body, part):
        return self._resolve(body, {"body"}, part)

    def _find_sketch(self, name):
        return self._resolve(name, {"sketch"})

    def _open_part(self, path, work=True, display=True, load_components=False):
        source = self.workspace.ensure_inside(path)
        loaded = next(
            (
                p
                for p in self.session.Parts
                if str(Path(p.FullPath).resolve()).casefold() == str(source).casefold()
            ),
            None,
        )
        already_loaded = loaded is not None
        if loaded is None:
            if not source.is_file():
                raise NXToolError("NX_FILE_NOT_FOUND", str(source))
            loaded, status = self.session.Parts.OpenBase(str(source))
            if status:
                status.Dispose()
        result = self._activate_part(
            self._reference(loaded, "part", loaded, "Part")["id"], work, display
        )
        if load_components:
            from nx_mcp.assembly_loading import load_components as load

            result["component_loading"] = load(self, loaded)
        result.update(
            already_loaded=already_loaded,
            path=str(source),
            message="Reused loaded part" if already_loaded else "Opened part",
        )
        return result

    def _activate_part(self, part, work=True, display=True):
        if part.startswith("obj_"):
            target = self.objects.resolve(part, expected_kind="part")
        else:
            matches = [
                p
                for p in self.session.Parts
                if part.casefold() in {p.FullPath.casefold(), p.Name.casefold()}
            ]
            if len(matches) != 1:
                raise NXToolError("NX_NOT_FOUND", "Loaded part must resolve uniquely")
            target = matches[0]
        if display:
            _, status = self.session.Parts.SetDisplay(target, False, False)
            if status:
                status.Dispose()
        if work:
            self.session.Parts.SetWork(target)
        return {
            "part": self._reference(target, "part", target, "Part"),
            "work": self.session.Parts.Work == target,
            "display": self.session.Parts.Display == target,
            "message": "Activated loaded part" if work or display else "Resolved loaded part",
        }

    def _save_part(self):
        from nx_mcp.save_audit import snapshot, verify

        part = self._work_part()
        try:
            before = snapshot(self.session)
        except Exception as error:
            raise NXToolError(
                "NX_SAVE_PREFLIGHT_FAILED", str(error), details={"mutation_outcome": "not_started"}
            ) from error
        native_errors = []
        save_error = None
        save_called = False
        native_returned = False
        try:
            with self._drawing_save_context(part):
                save_called = True
                status = part.Save(
                    self.nxopen.BasePart.SaveComponents.FalseValue,
                    self.nxopen.BasePart.CloseAfterSave.FalseValue,
                )
                native_returned = True
                try:
                    if status:
                        for i in range(status.NumberUnsavedParts):
                            native_errors.append(
                                {"path": status.GetPart(i).FullPath, "nx_code": status.GetStatus(i)}
                            )
                        for i in range(status.NumberUnsavedObjects):
                            native_errors.append(
                                {"object_index": i, "nx_code": status.GetObjectStatus(i)}
                            )
                finally:
                    if status:
                        status.Dispose()
        except Exception as error:
            if not save_called:
                if isinstance(error, NXToolError):
                    error.details["mutation_outcome"] = "not_started"
                raise
            save_error = error
            if not native_returned:
                native_errors.append({"message": str(error)})
        try:
            audit = verify(before, snapshot(self.session), int(part.Tag), native_errors)
        except NXToolError as error:
            if save_error:
                error.details["cause_code"] = getattr(save_error, "code", None)
            raise
        except Exception as error:
            raise NXToolError(
                "NX_SAVE_VERIFICATION_FAILED",
                str(error),
                details={"mutation_outcome": "partial", "path": part.FullPath},
            ) from error
        if save_error:
            if isinstance(save_error, NXToolError):
                save_error.details.update(audit, mutation_outcome="partial")
                raise save_error
            raise NXToolError(
                "NX_SAVE_FAILED", str(save_error), details={**audit, "mutation_outcome": "partial"}
            ) from save_error
        state = self._checkpoint_state()
        return {
            "message": "Saved and verified work-part file; native NX save may invalidate undo marks",
            "path": part.FullPath,
            "recovery": state,
            **audit,
            "warnings": [
                "NX v2606 save invalidates native undo/checkpoints. Establish a new checkpoint before further edits.",
                "Verification covers loaded part files and flags, not external linked files or concurrent external writers.",
            ],
        }

    def _save_as(self, path):
        part = self._work_part()
        dest = self.workspace.ensure_inside(path)
        if dest.exists():
            raise NXToolError("NX_FILE_EXISTS", "Save-as does not overwrite existing files")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._drawing_save_context(part):
            status = part.SaveAs(str(dest))
        if status and hasattr(status, "Dispose"):
            status.Dispose()
        return {
            "path": str(dest),
            "part": self._reference(part, "part", part, "Part"),
            "message": "Saved as",
        }

    def _close_part(self, save=True, part=None):
        from nx_mcp.assembly_loading import dependent_assemblies

        target = self.objects.resolve(part, expected_kind="part") if part else self._work_part()
        parents = dependent_assemblies(self, target)
        if parents:
            raise NXToolError(
                "NX_PART_IN_USE",
                "Close the loaded parent assemblies before closing this prototype",
                details={"parent_assemblies": parents, "mutation_outcome": "not_started"},
            )
        # NX may unload unused prototypes even with CloseWholeTree.FalseValue.
        # Capture references before Close; querying an unloaded NX proxy can fail.
        loaded = {int(p.Tag): self._reference(p, "part", p, "Part") for p in self.session.Parts}
        if save:
            with self._drawing_save_context(target):
                status = target.Save(
                    self.nxopen.BasePart.SaveComponents.FalseValue,
                    self.nxopen.BasePart.CloseAfterSave.FalseValue,
                )
            if status and hasattr(status, "Dispose"):
                status.Dispose()
        target.Close(
            self.nxopen.BasePart.CloseWholeTree.FalseValue,
            self.nxopen.BasePart.CloseModified.CloseModified,
            None,
        )
        remaining = {int(p.Tag) for p in self.session.Parts}
        closed = [ref for tag, ref in loaded.items() if tag not in remaining]
        closed_ids = {ref["part_id"] for ref in closed}
        for tag, ref in loaded.items():
            if tag not in remaining:
                self.objects.invalidate_part(ref["part_id"])
                self._part_generations.pop(tag, None)
        self._history = [h for h in self._history if h["part_id"] not in closed_ids]
        self._checkpoints = {
            k: v for k, v in self._checkpoints.items() if v["part_id"] not in closed_ids
        }
        return {
            "message": "Closed part; NX may also unload unused assembly prototypes",
            "closed_parts": closed,
            "closed_count": len(closed),
            "remaining_count": len(remaining),
            "warnings": ["Re-list open parts before closing another assembly dependency."],
        }

    def _list_open_parts(
        self,
        compact=False,
        path_prefix=None,
        modified=None,
        active_only=False,
        offset=0,
        limit=None,
    ):
        page([], offset, limit)
        rows = []
        for p in self.session.Parts:
            active = p == self.session.Parts.Work or p == self.session.Parts.Display
            if path_prefix is not None and not p.FullPath.casefold().replace("\\", "/").startswith(
                path_prefix.casefold().replace("\\", "/")
            ):
                continue
            if modified is not None and bool(p.IsModified) != modified:
                continue
            if active_only and not active:
                continue
            ref = self._reference(p, "part", p, "Part")
            rows.append(
                {
                    "part": compact_reference(ref) if compact else ref,
                    "name": p.Name,
                    "path": p.FullPath,
                    "work": p == self.session.Parts.Work,
                    "display": p == self.session.Parts.Display,
                    "modified": bool(p.IsModified),
                }
            )
        selected, metadata = page(rows, offset, limit)
        return {"parts": selected, **metadata}

    def _sketch_frame(self, sketch):
        m = sketch.Orientation.Element
        return {
            "origin": xyz(sketch.Origin),
            "x_axis": [m.Xx, m.Xy, m.Xz],
            "y_axis": [m.Yx, m.Yy, m.Yz],
            "normal": [m.Zx, m.Zy, m.Zz],
            "coordinate_frame": "part",
        }

    def _create_sketch(self, plane="XY", name=None, origin=None, x_axis=None, y_axis=None):
        bases = {
            "XY": ([1, 0, 0], [0, 1, 0]),
            "XZ": ([1, 0, 0], [0, 0, 1]),
            "YZ": ([0, 1, 0], [0, 0, 1]),
        }
        if plane not in bases:
            raise NXToolError("NX_INVALID_ARGUMENT", "plane must be XY, XZ or YZ")
        if (x_axis is None) != (y_axis is None):
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply both basis axes")
        x = vector(x_axis if x_axis is not None else bases[plane][0])
        y = vector(y_axis if y_axis is not None else bases[plane][1])
        o = vector(origin or [0, 0, 0])
        if abs(dot(x, x) - 1) > 1e-8 or abs(dot(y, y) - 1) > 1e-8 or abs(dot(x, y)) > 1e-8:
            raise NXToolError("NX_INVALID_ARGUMENT", "Sketch basis must be orthonormal")
        n = cross(x, y)
        part = self._work_part()
        matrix = self._nx_matrix(transpose([x, y, n]))
        csys = part.CoordinateSystems.CreateCoordinateSystem(self.nxopen.Point3d(*o), matrix, False)
        builder = part.Sketches.CreateSketchInPlaceBuilder2(self.nxopen.Sketch.Null)
        try:
            builder.Csystem = csys
            sketch = builder.Commit()
        finally:
            builder.Destroy()
        if name:
            sketch.SetName(name)
        actual = self._sketch_frame(sketch)
        if any(
            abs(a - b) > 1e-7
            for k, v in [("origin", o), ("x_axis", x), ("y_axis", y), ("normal", n)]
            for a, b in zip(actual[k], v, strict=False)
        ):
            raise NXToolError(
                "NX_FRAME_MISMATCH",
                "NX sketch frame differs from requested frame",
                details={"actual": actual},
            )
        sketch.Activate(self.nxopen.Sketch.ViewReorient.FalseValue)
        return {
            "object": self._reference(sketch, "sketch", part, "Sketch"),
            "frame": actual,
            "message": "Created sketch",
        }

    def _point_on_sketch(self, sketch, p):
        f = self._sketch_frame(sketch)
        return self.nxopen.Point3d(
            *[
                f["origin"][i] + float(p["x"]) * f["x_axis"][i] + float(p["y"]) * f["y_axis"][i]
                for i in range(3)
            ]
        )

    def _create_sketch_line(self, sketch, part, start, end):
        if self.session.ActiveSketch != sketch:
            raise NXToolError(
                "NX_SKETCH_NOT_ACTIVE", "Activate the owning sketch before adding geometry"
            )
        curve = part.Curves.CreateLine(
            self._point_on_sketch(sketch, start), self._point_on_sketch(sketch, end)
        )
        sketch.AddGeometry(curve, self.nxopen.Sketch.InferConstraintsOption.InferNoConstraints)
        return self._reference(curve, "curve", part, "Line")

    def _sketch_arc_legacy(self, cx, cy, radius, start_angle, end_angle, sketch_id=None):
        sketch = self._resolve(sketch_id, {"sketch"}) if sketch_id else self.session.ActiveSketch
        if sketch is None or self.session.ActiveSketch != sketch:
            raise NXToolError("NX_SKETCH_NOT_ACTIVE", "An explicit active sketch is required")
        if not math.isfinite(radius) or radius <= 0 or not 0 < end_angle - start_angle <= 360:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Require positive radius and 0 < end-start <= 360 degrees"
            )
        part = self._work_part()
        f = self._sketch_frame(sketch)
        arc = part.Curves.CreateArc(
            self._point_on_sketch(sketch, {"x": cx, "y": cy}),
            self.nxopen.Vector3d(*f["x_axis"]),
            self.nxopen.Vector3d(*f["y_axis"]),
            float(radius),
            math.radians(start_angle),
            math.radians(end_angle),
        )
        sketch.AddGeometry(arc, self.nxopen.Sketch.InferConstraintsOption.InferNoConstraints)
        return {
            "object": self._reference(arc, "curve", part, "Arc"),
            "frame": f,
            "message": "Created sketch arc",
        }

    def _sketch_info(self, sketch_id):
        sketch = self._resolve(sketch_id, {"sketch"})
        part = self._work_part()
        geometry = []
        for c in sketch.GetAllGeometry():
            row = {"object": self._reference(c, "curve", part, "Curve"), "type": type(c).__name__}
            if hasattr(c, "StartPoint"):
                row.update(start=xyz(c.StartPoint), end=xyz(c.EndPoint))
            if hasattr(c, "CenterPoint"):
                row.update(center=xyz(c.CenterPoint), radius=c.Radius)
            geometry.append(row)
        return {
            "object": self._reference(sketch, "sketch", part, "Sketch"),
            "frame": self._sketch_frame(sketch),
            "curves": geometry,
            "curve_count": len(geometry),
        }

    def _simple_extrude(self, sketch_id, distance, reverse=False):
        if not math.isfinite(distance):
            raise NXToolError("NX_INVALID_ARGUMENT", "distance must be finite")
        result = NXOpenExecutor._extrude(self, sketch_id, distance, reverse)
        feature = self.objects.resolve(result["feature"]["id"])
        bodies = list(feature.GetBodies())
        result.update(
            bodies=[self._reference(b, "body", self._work_part(), "Body") for b in bodies],
            body_count=len(bodies),
        )
        result["created"] = [result["feature"]] + result["bodies"]
        result["modified"] = []
        result["deleted"] = []
        return result

    def _get_feature_info(self, name):
        f = self._resolve(name, {"feature"})
        part = self._work_part()
        return {
            "feature": self._reference(f, "feature", part, "Feature"),
            "name": self._name(f, "Feature"),
            "type": f.FeatureType,
            "expressions": [
                {"name": e.Name, "formula": e.RightHandSide, "value": e.Value}
                for e in f.GetExpressions()
            ],
            "parents": [self._reference(v, "feature", part, "Feature") for v in f.GetParents()],
            "children": [self._reference(v, "feature", part, "Feature") for v in f.GetChildren()],
        }

    def _edit_feature(self, name, params):
        f = self._resolve(name, {"feature"})
        part = self._work_part()
        kind = f.FeatureType.upper().replace(" ", "_")
        allowed = (
            {"distance"}
            if kind == "EXTRUDE"
            else {"count", "spacing"}
            if kind in {"PATTERN_FEATURE", "PATTERN"}
            else set()
        )
        if not params or set(params) - allowed:
            raise NXToolError(
                "NX_UNSUPPORTED_EDIT",
                "Supported edits: EXTRUDE distance; PATTERN_FEATURE count/spacing",
            )
        if any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
            for v in params.values()
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Edit values must be finite and positive")
        if "count" in params and (int(params["count"]) != params["count"] or params["count"] < 2):
            raise NXToolError("NX_INVALID_ARGUMENT", "count must be integer >= 2")
        if kind == "EXTRUDE":
            builder = part.Features.CreateExtrudeBuilder(f)
            try:
                builder.Limits.EndExtend.Value.RightHandSide = str(params["distance"])
                builder.CommitFeature()
            finally:
                builder.Destroy()
        else:
            builder = part.Features.CreatePatternFeatureBuilder(f)
            try:
                spacing = builder.PatternService.RectangularDefinition.XSpacing
                if "count" in params:
                    spacing.NCopies.RightHandSide = str(int(params["count"]))
                if "spacing" in params:
                    spacing.PitchDistance.RightHandSide = str(params["spacing"])
                builder.CommitFeature()
            finally:
                builder.Destroy()
        errors = self.session.UpdateManager.DoUpdate(self._active_mark)
        if errors:
            raise NXToolError("NX_UPDATE_FAILED", str(errors) + " update errors")
        result = self._get_feature_info(self._reference(f, "feature", part, "Feature")["id"])
        result.update(modified=[result["feature"]], created=[], deleted=[])
        return result

    def _checkpoint(self, label="checkpoint"):
        part = self._work_part()
        key = "cp_" + uuid.uuid4().hex
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Visible, "NX MCP checkpoint: " + label
        )
        self._checkpoints[key] = {
            "mark": mark,
            "part_id": self._part_id(part),
            "index": len(self._history),
            "label": label,
        }
        return {
            "checkpoint_id": key,
            "message": "In-session checkpoint created; disk saves are not rolled back",
        }

    def _checkpoint_state(self):
        return {
            "checkpoints": [
                {k: v for k, v in c.items() if k != "mark"}
                | {
                    "checkpoint_id": key,
                    "available": self.session.DoesUndoMarkExist(c["mark"], None),
                }
                for key, c in self._checkpoints.items()
            ],
            "undo_depth": sum(
                self.session.DoesUndoMarkExist(h["mark"], None) for h in self._history
            ),
            "save_semantics": "NX v2606 save removes native marks. Expired checkpoints cannot be rolled back; create a new checkpoint after save.",
            "retention": "Current NX process only; cross-part rollback is rejected if it would undo another part.",
        }

    def _rollback_checkpoint(self, checkpoint_id):
        if checkpoint_id not in self._checkpoints:
            raise NXToolError("NX_CHECKPOINT_STALE", "Checkpoint is not in this session")
        cp = self._checkpoints[checkpoint_id]
        if not self.session.DoesUndoMarkExist(cp["mark"], None):
            raise NXToolError(
                "NX_CHECKPOINT_STALE",
                "Native NX checkpoint expired, commonly at save or part activation",
            )
        pid = self._part_id(self._work_part())
        if cp["part_id"] != pid or any(h["part_id"] != pid for h in self._history[cp["index"] :]):
            raise NXToolError(
                "NX_CROSS_PART_ROLLBACK",
                "Rollback would affect a different part; activate the checkpoint part or reconcile intervening changes",
            )
        self.session.UndoToMark(cp["mark"], None)
        self.objects.invalidate_part(pid)
        self._record_reverted(self._history[cp["index"] :], checkpoint_id)
        self._history = self._history[: cp["index"]]
        self._checkpoints = {k: v for k, v in self._checkpoints.items() if v["index"] < cp["index"]}
        return {
            "message": "Rolled back checkpoint; reacquire object references",
            "checkpoint_id": checkpoint_id,
        }

    def _undo(self):
        if not self._history or not self.session.DoesUndoMarkExist(self._history[-1]["mark"], None):
            raise NXToolError(
                "NX_UNDO_UNAVAILABLE",
                "No native undo mark remains; save and part lifecycle changes can expire history",
            )
        last = self._history[-1]
        pid = self._part_id(self._work_part())
        if last["part_id"] != pid:
            raise NXToolError(
                "NX_CROSS_PART_ROLLBACK", "Activate the part owning the latest mutation"
            )
        self.session.UndoToMark(last["mark"], None)
        self._record_reverted([last], self._current_operation)
        self._history.pop()
        self.objects.invalidate_part(pid)
        self._checkpoints = {
            k: v for k, v in self._checkpoints.items() if v["index"] <= len(self._history)
        }
        return {
            "message": "Undone; reacquire object references",
            "undone_operation_id": last["operation_id"],
        }

    def _nx_matrix(self, m):
        matrix = self.nxopen.Matrix3x3()
        for i, prefix in enumerate("XYZ"):
            for j, suffix in enumerate("xyz"):
                setattr(matrix, prefix + suffix, m[j][i])
        return matrix

    def _walk_components(self, part):
        root = part.ComponentAssembly.RootComponent

        def walk(parent, path):
            for c in parent.GetChildren():
                p = path + [self._name(c, "Component")]
                yield c, p
                yield from walk(c, p)

        return list(walk(root, [])) if root else []

    def _list_components(
        self,
        compact=False,
        include_transforms=True,
        name_contains=None,
        suppressed=None,
        offset=0,
        limit=None,
    ):
        from nx_mcp.assembly_loading import component_info

        page([], offset, limit)
        part = self._work_part()
        candidates = [
            (c, path)
            for c, path in self._walk_components(part)
            if (name_contains is None or name_contains.casefold() in c.Name.casefold())
            and (suppressed is None or bool(c.IsSuppressed) == suppressed)
        ]
        selected, metadata = page(candidates, offset, limit)
        result = []
        for c, path in selected:
            ref = self._reference(c, "component", part, "Component")
            ref["occurrence_path"] = path
            row = {
                "object": compact_reference(ref) if compact else ref,
                "name": c.Name,
                **component_info(c),
                "depth": len(path) - 1,
                "suppressed": bool(c.IsSuppressed),
                "reference_set": c.ReferenceSet,
            }
            if include_transforms:
                p, m = c.GetPosition()
                row.update(translation=xyz(p), rotation_matrix=rows(m), coordinate_frame="assembly")
                if not compact:
                    row["rotation"] = [m.Xx, m.Xy, m.Xz, m.Yx, m.Yy, m.Yz, m.Zx, m.Zy, m.Zz]
            result.append(row)
        return {
            "components": result,
            **metadata,
            "matrix_convention": "rotation_matrix is row-major; p_assembly = R p_local + translation. Legacy rotation lists axis vectors.",
        }

    def _list_topology(self, body, face=None, include_adjacency=False, compact=False):
        b = self._resolve(body, {"body"})
        part = self._work_part()
        faces = list(b.GetFaces())
        if face is not None:
            selected = self._resolve(face, {"face"})
            if selected not in faces:
                raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "face must belong to body")
            faces = [selected]
        edges = (
            list({int(e.Tag): e for f in faces for e in f.GetEdges()}.values())
            if face
            else list(b.GetEdges())
        )

        def ref(value, kind):
            r = self._reference(value, kind, part, kind.title())
            return compact_reference(r) if compact else r

        result = {
            "body": ref(b, "body"),
            "faces": [ref(f, "face") for f in faces],
            "edges": [ref(e, "edge") for e in edges],
            "solid": b.IsSolidBody,
            "face_count": len(faces),
            "edge_count": len(edges),
        }
        if include_adjacency:
            result["face_edges"] = [
                {
                    "face": ref(f, "face")["id"],
                    "edges": [ref(e, "edge")["id"] for e in f.GetEdges()],
                }
                for f in faces
            ]
            result["edge_faces"] = [
                {
                    "edge": ref(e, "edge")["id"],
                    "faces": [ref(f, "face")["id"] for f in e.GetFaces()],
                }
                for e in edges
            ]
        return result

    def _rename_object(self, object_id, name):
        if not name or len(name) > 132:
            raise NXToolError("NX_INVALID_ARGUMENT", "Name must contain 1–132 characters")
        value = self._resolve(object_id)
        value.SetName(name)
        ref = self.objects._objects[object_id].reference
        self.objects.invalidate_part(ref.part_id)
        return {
            "object": self._reference(value, ref.kind, self._work_part(), name),
            "message": "Renamed; references refreshed",
        }

    def _geometry(self, ref=None, scope="auto"):
        part = self._work_part()
        if scope not in {"auto", "part", "assembly"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "scope must be auto, part or assembly")
        values = []
        if ref:
            obj = self._resolve(ref, {"body", "face", "edge", "component", "feature"})
            if hasattr(obj, "FindOccurrence"):
                selected = [obj] + [
                    c for c, _ in self._walk_components(part) if self._descendant(c, obj)
                ]
                for c in selected:
                    values.extend(self._occurrence_bodies(c))
            elif hasattr(obj, "GetBodies") and not hasattr(obj, "IsSolidBody"):
                values = list(obj.GetBodies())
            else:
                values = [obj]
        else:
            values = list(part.Bodies)
            if scope == "assembly" or (scope == "auto" and part.ComponentAssembly.RootComponent):
                for c, _ in self._walk_components(part):
                    values.extend(self._occurrence_bodies(c))
        if not values:
            raise NXToolError(
                "NX_NO_TARGET_BODY",
                "No included geometry; inspect suppression, loading and reference sets",
            )
        return values

    @staticmethod
    def _descendant(c, parent):
        while c.Parent:
            c = c.Parent
            if c == parent:
                return True
        return False

    def _occurrence_bodies(self, c):
        p = c
        while p:
            if p.IsSuppressed:
                return []
            p = p.Parent
        bodies = []
        for b in c.Prototype.Bodies:
            occurrence = c.FindOccurrence(b)
            if occurrence is not None:
                bodies.append(occurrence)
        return bodies

    def _get_bounding_box(self, body=None, scope="auto", precision="conservative"):
        import NXOpen.UF

        part = self._work_part()
        uf = NXOpen.UF.UFSession.GetUFSession()
        objects = self._geometry(body, scope)
        if precision not in {"conservative", "exact"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "precision must be conservative or exact")
        if precision == "exact":
            wcs = rows(part.WCS.CoordinateSystem.Orientation.Element)
            if any(abs(wcs[i][j] - IDENTITY[i][j]) > 1e-8 for i in range(3) for j in range(3)):
                raise NXToolError(
                    "NX_UNSUPPORTED_FRAME",
                    "Exact absolute bounds currently require axis-aligned WCS",
                )
        result = []
        for item in objects:
            if precision == "exact":
                low, directions, lengths = uf.ModlGeneral.AskBoundingBoxExact(item.Tag, 0)
                if len(directions) == 9:
                    directions = [directions[i : i + 3] for i in range(0, 9, 3)]
                corners = [
                    [
                        low[i]
                        + sum(directions[j][i] * lengths[j] * ((mask >> j) & 1) for j in range(3))
                        for i in range(3)
                    ]
                    for mask in range(8)
                ]
                box = [min(p[i] for p in corners) for i in range(3)] + [
                    max(p[i] for p in corners) for i in range(3)
                ]
            else:
                box = list(uf.ModlGeneral.AskBoundingBox(item.Tag))
            result.append(
                {
                    "body": self._reference(item, "body", part, "Body"),
                    "box": box,
                    "solid": bool(item.IsSolidBody),
                }
            )
        low = [min(row["box"][i] for row in result) for i in range(3)]
        high = [max(row["box"][i + 3] for row in result) for i in range(3)]
        return {
            "min": low,
            "max": high,
            "dimensions": [b - a for a, b in zip(low, high, strict=False)],
            "units": self._units(),
            "coordinate_frame": "work_part",
            "bounds_type": precision,
            "bodies": result,
            "body_count": len(result),
            "scope": scope,
            "message": "Native UF "
            + precision
            + " bounds of included body occurrences; suppression and reference sets honored",
        }

    def _measure_distance(self, obj1, obj2):
        a = self._geometry(obj1)
        b = self._geometry(obj2)
        best = None
        for x in a:
            for y in b:
                dist, p1, p2, accuracy = self.session.Measurement.GetMinimumDistance(x, y)
                if best is None or dist < best["distance"]:
                    best = {
                        "distance": dist,
                        "closest_points": [xyz(p1), xyz(p2)],
                        "accuracy": None,
                        "accuracy_note": "No validated numerical error bound is exposed by this NX binding",
                        "resolved_tags": [int(x.Tag), int(y.Tag)],
                    }
        return {
            **best,
            "units": self._units(),
            "coordinate_frame": "work_part",
            "references": [obj1, obj2],
            "method": "NX Measurement.GetMinimumDistance",
            "pair_count": len(a) * len(b),
            "warnings": ["Zero distance alone does not distinguish contact from penetration."],
        }

    def _pattern(self, features, pattern_type="linear", direction="X", spacing=10, count=2):
        import NXOpen.GeometricUtilities

        if pattern_type != "linear":
            raise NXToolError(
                "NX_UNSUPPORTED_ARGUMENT", "Only native linear feature patterns are implemented"
            )
        if (
            type(count) is not int
            or count < 2
            or count > 1000
            or not math.isfinite(spacing)
            or spacing <= 0
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Require 2–1000 total instances and positive finite pitch"
            )
        vectors = {
            "X": [1, 0, 0],
            "Y": [0, 1, 0],
            "Z": [0, 0, 1],
            "-X": [-1, 0, 0],
            "-Y": [0, -1, 0],
            "-Z": [0, 0, -1],
        }
        if direction not in vectors:
            raise NXToolError("NX_INVALID_ARGUMENT", "Invalid principal direction")
        seeds = [self._resolve(f, {"feature"}) for f in features]
        if not seeds:
            raise NXToolError("NX_INVALID_ARGUMENT", "At least one feature is required")
        part = self._work_part()
        builder = part.Features.CreatePatternFeatureBuilder(self.nxopen.Features.Feature.Null)
        try:
            builder.FeatureList.Add(seeds)
            builder.PatternMethod = (
                self.nxopen.Features.PatternFeatureBuilder.PatternMethodOptions.Simple
            )
            builder.PatternService.PatternType = (
                NXOpen.GeometricUtilities.PatternDefinition.PatternEnum.Linear
            )
            definition = builder.PatternService.RectangularDefinition
            definition.XDirection = part.Directions.CreateDirection(
                self.nxopen.Point3d(0.0, 0.0, 0.0),
                self.nxopen.Vector3d(*[float(v) for v in vectors[direction]]),
                self.nxopen.SmartObject.UpdateOption.WithinModeling,
            )
            definition.XSpacing.NCopies.RightHandSide = str(count)
            definition.XSpacing.PitchDistance.RightHandSide = str(spacing)
            definition.YSpacing.NCopies.RightHandSide = "1"
            feature = builder.CommitFeature()
        finally:
            builder.Destroy()
        return {
            "feature": self._reference(feature, "feature", part, "Pattern"),
            "feature_type": feature.FeatureType,
            "count": count,
            "count_includes_seed": True,
            "spacing": spacing,
            "direction": direction,
            "bodies": [self._reference(b, "body", part, "Body") for b in feature.GetBodies()],
        }

    def _import_geometry(self, path, flatten=False, target="work_part", output_path=None):
        # A new-part import owns every part it creates and restores the prior selection on failure.
        before = {int(p.Tag) for p in self.session.Parts}
        work, display = self.session.Parts.Work, self.session.Parts.Display
        try:
            return self._import_geometry_inner(path, flatten, target, output_path)
        except Exception as error:
            if target == "new_part":
                created = [p for p in self.session.Parts if int(p.Tag) not in before]
                cleanup_errors = []
                for part in reversed(created):
                    try:
                        ref = self._reference(part, "part", part, "Import part")
                        self._close_part(part=ref["id"], save=False)
                    except Exception as cleanup:
                        cleanup_errors.append(str(cleanup))
                try:
                    if display is not None:
                        self._activate_part(
                            self._reference(display, "part", display, "Part")["id"],
                            work=False,
                            display=True,
                        )
                    if work is not None:
                        self._activate_part(
                            self._reference(work, "part", work, "Part")["id"],
                            work=True,
                            display=False,
                        )
                except Exception as cleanup:
                    cleanup_errors.append(str(cleanup))
                err = (
                    error
                    if isinstance(error, NXToolError)
                    else NXToolError(
                        "NX_API_ERROR", str(error), nx_code=getattr(error, "ErrorCode", None)
                    )
                )
                err.details.update(
                    mutation_outcome="partial"
                    if cleanup_errors
                    else "rolled_back"
                    if created
                    else "not_started",
                    cleanup_errors=cleanup_errors,
                )
                if err is error:
                    raise
                raise err from error
            raise

    def _import_diagnostics(self, import_dir, settings_file, part):
        return {
            "translator": "Step214Importer",
            "settings_file": str(settings_file),
            "staging_directory": str(import_dir),
            "output_search_paths": [str(import_dir), str(part.FullPath)],
            "translator_files": [
                {
                    "path": str(f),
                    "size": f.stat().st_size,
                    "text": f.read_text(errors="replace")[-16000:]
                    if f.suffix.lower() in {".log", ".err", ".txt"}
                    else None,
                }
                for f in sorted(import_dir.iterdir())
                if f.is_file() and f.name != "input.step"
            ],
        }

    def _import_geometry_inner(self, path, flatten=False, target="work_part", output_path=None):
        import re
        import shutil

        source = self.workspace.ensure_inside(path)
        if source.suffix.lower() not in {".step", ".stp"}:
            raise NXToolError("NX_UNSUPPORTED_ARGUMENT", "Only STEP import is implemented")
        if not source.is_file():
            raise NXToolError("NX_FILE_NOT_FOUND", str(source))
        if target not in {"work_part", "new_part"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "target must be work_part or new_part")
        if (target == "new_part") != bool(output_path):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Supply output_path exactly when target is new_part"
            )
        output = self.workspace.ensure_inside(output_path) if output_path else None
        if output and output.exists():
            raise NXToolError("NX_FILE_EXISTS", "Import destination already exists")
        text = source.read_text(errors="replace")
        framing = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL).strip("\ufeff \t\r\n")
        if not re.match(r"ISO-10303-21\s*;", framing, re.IGNORECASE) or not re.search(
            r"END-ISO-10303-21\s*;\s*$", framing, re.IGNORECASE
        ):
            raise NXToolError(
                "NX_INVALID_STEP",
                "STEP exchange-file opening or closing marker is missing; file may be truncated",
                suggestion="Obtain a complete STEP file. No translator was started.",
            )
        product_names = set()
        for pair in re.findall(
            r"PRODUCT\s*\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'", text, re.IGNORECASE
        ):
            product_names.update(Path(v.replace("''", "'")).stem.casefold() for v in pair)
        conflicts = [
            p.FullPath
            for p in self.session.Parts
            if Path(p.FullPath).stem.casefold() in product_names
        ]
        if conflicts and not flatten and "NEXT_ASSEMBLY_USAGE_OCCURRENCE" in text.upper():
            raise NXToolError(
                "NX_IMPORT_NAME_CONFLICT",
                "STEP prototype names are already loaded. Close those saved source parts explicitly or use a fresh NX session.",
                details={"loaded_parts": conflicts},
            )
        # Use the verified WorkPart importer for both modes. NX 2606's NewPart
        # translator mode returned no output in testing; normal part creation is explicit.
        if output:
            self._create_part(str(output), units="mm")
        part = self._work_part()
        before = {int(b.Tag) for b in part.Bodies}
        component_before = {int(c.Tag) for c, _ in self._walk_components(part)}
        import_dir = self.workspace.root / "imports" / self._current_operation
        import_dir.mkdir(parents=True, exist_ok=False)
        staged = import_dir / "input.step"
        shutil.copyfile(source, staged)
        builder = self.session.DexManager.CreateStep214Importer()
        try:
            settings_file = str(
                Path(
                    __import__("os").environ.get(
                        "UGII_BASE_DIR", r"C:\Program Files\Siemens\Designcenter2606"
                    )
                )
                / "STEP214UG"
                / "step214ug.def"
            )
            builder.SettingsFile = settings_file
            builder.InputFile = str(staged)
            builder.ImportTo = self.nxopen.Step214Importer.ImportToOption.WorkPart
            builder.FileOpenFlag = False
            builder.FlattenAssembly = flatten
            builder.SimplifyGeometry = False
            builder.ObjectTypes.Solids = True
            builder.ObjectTypes.Surfaces = True
            builder.ObjectTypes.Curves = True
            builder.ProcessHoldFlag = True
            try:
                builder.Commit()
            except Exception as error:
                raise NXToolError(
                    "NX_IMPORT_FAILED",
                    str(error),
                    nx_code=getattr(error, "ErrorCode", None),
                    details=self._import_diagnostics(import_dir, settings_file, part),
                ) from error
        finally:
            builder.Destroy()
        bodies = [
            self._reference(b, "body", part, "Body")
            for b in part.Bodies
            if int(b.Tag) not in before
        ]
        components = self._list_components()
        added_components = [
            c for c, _ in self._walk_components(part) if int(c.Tag) not in component_before
        ]
        if not bodies and not added_components:
            raise NXToolError(
                "NX_IMPORT_NO_OUTPUT",
                "Translator returned without imported bodies or occurrences",
                details=self._import_diagnostics(import_dir, settings_file, part),
            )
        return {
            "path": str(source),
            "staging_directory": str(import_dir),
            "diagnostics": self._import_diagnostics(import_dir, settings_file, part),
            "bodies": bodies,
            "body_count": len(bodies),
            "components": components,
            "created_component_count": len(added_components),
            "target": target,
            "part": self._reference(part, "part", part, "Part"),
            "units": self._units(),
            "translator": "NX v2606 Step214Importer WorkPart",
            "flatten": flatten,
            "warnings": [
                "Translator-generated prototypes are staged in the returned import directory. Save to persist them; native undo does not delete translator artifacts."
            ],
        }

    def _add_component(self, part_path, name=None, translation=None, rotation_matrix=None):
        part = self._work_part()
        path = self.workspace.ensure_inside(part_path)
        t = vector(translation or [0, 0, 0])
        r = self._validate_rotation(rotation_matrix or IDENTITY)
        c, status = part.ComponentAssembly.AddComponent(
            str(path),
            "Entire Part",
            name or path.stem,
            self.nxopen.Point3d(*t),
            self._nx_matrix(r),
            -1,
        )
        if status:
            status.Dispose()
        return {
            "component": c.Name,
            "object": self._reference(c, "component", part, "Component"),
            "path": str(path),
            "translation": t,
            "rotation_matrix": r,
            "coordinate_frame": "work_part",
            "message": "Added and placed component",
        }

    @staticmethod
    def _validate_rotation(matrix):
        if len(matrix) != 3:
            raise NXToolError("NX_INVALID_ARGUMENT", "rotation_matrix must be 3x3")
        m = [vector(r) for r in matrix]
        if (
            any(
                abs(dot(m[i], m[j]) - (1 if i == j else 0)) > 1e-8
                for i in range(3)
                for j in range(3)
            )
            or abs(dot(m[0], cross(m[1], m[2])) - 1) > 1e-8
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "rotation_matrix must be a right-handed orthonormal matrix"
            )
        return m

    def _set_component_transform(self, component, translation, rotation_matrix):
        c = self._resolve(component, {"component"})
        part = self._work_part()
        if c.Parent != part.ComponentAssembly.RootComponent:
            raise NXToolError(
                "NX_UNSUPPORTED_ARGUMENT",
                "Absolute placement currently supports immediate children; activate their owning subassembly",
            )
        t = vector(translation)
        r = self._validate_rotation(rotation_matrix)
        p, m = c.GetPosition()
        delta = matmul(r, transpose(rows(m)))
        # NX MoveComponent rotates orientation about the component origin and adds translation.
        shift = [a - b for a, b in zip(t, xyz(p), strict=False)]
        part.ComponentAssembly.MoveComponent(
            c, self.nxopen.Vector3d(*shift), self._nx_matrix(delta)
        )
        actual_p, actual_m = c.GetPosition()
        if any(abs(a - b) > 1e-7 for a, b in zip(xyz(actual_p), t, strict=False)) or any(
            abs(rows(actual_m)[i][j] - r[i][j]) > 1e-7 for i in range(3) for j in range(3)
        ):
            raise NXToolError(
                "NX_PLACEMENT_MISMATCH", "Placement read-back differs; transaction will roll back"
            )
        return {
            "object": self._reference(c, "component", part, "Component"),
            "translation": xyz(actual_p),
            "rotation_matrix": rows(actual_m),
            "coordinate_frame": "work_part",
        }

    def _reposition_component(self, component, dx=0, dy=0, dz=0, rx=0, ry=0, rz=0):
        c = self._resolve(component, {"component"})
        p, m = c.GetPosition()
        a, b, d = [math.radians(v) for v in vector([rx, ry, rz])]
        sx, cx, sy, cy, sz, cz = (
            math.sin(a),
            math.cos(a),
            math.sin(b),
            math.cos(b),
            math.sin(d),
            math.cos(d),
        )
        r = [
            [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx],
        ]
        return self._set_component_transform(
            component, add(xyz(p), vector([dx, dy, dz])), matmul(r, rows(m))
        )

    def _batch(self, operations):
        if not 1 <= len(operations) <= 100:
            raise NXToolError("NX_INVALID_ARGUMENT", "Batch requires 1–100 operations")
        allowed = {
            "nx_sketch_line",
            "nx_sketch_rectangle",
            "nx_sketch_arc",
            "nx_add_component",
            "nx_set_component_transform",
            "nx_reposition_component",
        }
        # Preflight is structural and validates all signatures before any NX mutation.
        for op in operations:
            if set(op) != {"method", "params"} or op["method"] not in allowed:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT",
                    "Batch supports sketch curves and assembly placement only",
                )
            inspect.signature(self._handlers[op["method"]]).bind(**op["params"])
        results = []
        key = self._current_operation
        for i, op in enumerate(operations):
            if self.store.path(key).with_suffix(".cancel").exists():
                raise NXToolError(
                    "NX_CANCELLED",
                    "Cancelled between operations",
                    details={"completed_before_rollback": i},
                )
            result = self._handlers[op["method"]](**op["params"])
            results.append({"index": i, "method": op["method"], "result": result})
            record = self.store.get(key)
            record.update(progress={"completed": i + 1, "total": len(operations)})
            self.store.put(record)
        return {
            "results": results,
            "count": len(results),
            "atomic": True,
            "message": "Batch committed on NX journal thread",
        }

    def _capabilities(self, tool=None, prefix=None):
        import json

        manifest = json.loads(Path(__file__).with_name("capability_manifest.json").read_text())
        part = self._work_part(required=False)
        manifest.update(
            session_id=self.session_id,
            actual_nx_version=self.nx_version,
            execution=(
                "serialized NX batch journal thread"
                if self.session.IsBatch
                else "serialized NX UI thread; visible model viewport"
            ),
            interactive=not self.session.IsBatch,
            api_detection={
                "native_display_modification": hasattr(
                    self.session.DisplayManager, "NewDisplayModification"
                ),
                "dynamic_sections": bool(part and hasattr(part, "DynamicSections")),
                "sketch_solver_status": hasattr(self.nxopen.Sketch, "CalculateStatus"),
                "step_import": hasattr(self.session.DexManager, "CreateStep214Importer"),
                "native_pattern": bool(
                    part and hasattr(part.Features, "CreatePatternFeatureBuilder")
                ),
                "minimum_distance": hasattr(self.session.Measurement, "GetMinimumDistance"),
                "native_component_pattern": bool(
                    part and hasattr(part.ComponentAssembly, "CreateComponentPatternBuilder")
                ),
                "sketch_dimensions": hasattr(self.nxopen.Sketch, "CreateDimension"),
            },
            coordinate_conventions={
                "lengths": "work-part units unless explicitly named mm3",
                "angles": "degrees",
                "rotation_order": "Rz * Ry * Rx",
                "matrix": "row-major 3x3; p_parent = R*p_local + t",
                "legacy_rotation_list": "NX axis vectors X,Y,Z, not row-major",
                "sketch_XZ_normal": [0, -1, 0],
            },
        )
        if self.session.IsBatch:
            for name in ("nx_screenshot", "nx_render_view", "nx_ui_control"):
                manifest["tools"][name].update(
                    status="unavailable", scope="Requires the interactive NX host"
                )
        if self.nx_version != "v2606":
            for entry in manifest["tools"].values():
                entry.update(status="experimental", scope="This NX version has not been tested")
        if tool is not None and prefix is not None:
            raise NXToolError("NX_INVALID_ARGUMENT", "Use tool or prefix, not both")
        total = len(manifest["tools"])
        if tool is not None:
            if tool not in manifest["tools"]:
                raise NXToolError("NX_NOT_FOUND", "Tool is not in the capability manifest")
            manifest["tools"] = {tool: manifest["tools"][tool]}
        elif prefix is not None:
            manifest["tools"] = {k: v for k, v in manifest["tools"].items() if k.startswith(prefix)}
        manifest.update(tool_count=len(manifest["tools"]), total_tool_count=total, units=None)
        return manifest

    def _finish_sketch(self, sketch_id):
        result = super()._finish_sketch(sketch_id)
        sketch = self.objects.resolve(sketch_id)
        result["object"] = self._reference(sketch, "sketch", self._work_part(), "Sketch")
        return result

    def _snapshot(self, part):
        groups = [
            ("explosion", self._explosions(part)),
            ("modeling_view", self._modeling_views(part)),
            ("assembly_constraint", self._assembly_constraints(part)),
            ("drawing_sheet", getattr(part, "DrawingSheets", [])),
            ("drawing_view", getattr(part, "DraftingViews", [])),
            ("dimension", getattr(part, "Dimensions", [])),
            ("annotation", self._documentation_annotations(part)),
            ("traceline", getattr(part, "Tracelines", [])),
            (
                "component_pattern",
                self._component_patterns(part),
            ),
            ("expression", getattr(part, "Expressions", [])),
            ("body", part.Bodies),
            ("feature", part.Features),
            ("curve", part.Curves),
            ("point", getattr(part, "Points", [])),
            ("sketch", part.Sketches),
            ("section", getattr(part, "DynamicSections", [])),
            ("component", [c for c, _ in self._walk_components(part)]),
        ]
        return {
            (kind, int(v.Tag)): self._reference(v, kind, part, kind.title())
            for kind, values in groups
            for v in values
        }

    def _invalidate_deleted(self, before, after, invalidate_topology=True):
        removed = {v["id"] for k, v in before.items() if k not in after}
        for key, entry in list(self.objects._objects.items()):
            if key in removed or (invalidate_topology and entry.reference.kind in {"face", "edge"}):
                self.objects._objects.pop(key, None)
                self.objects._stale_ids.add(key)
        self.objects._identities = {
            k: v for k, v in self.objects._identities.items() if v in self.objects._objects
        }

    def _record_reverted(self, history, by):
        for item in history:
            record = self.store.get(item["operation_id"])
            if "fingerprint" in record:
                record["reverted_by"] = by
                self.store.put(record)

    def _measure_volume(self, body=None, scope="auto"):
        part = self._work_part()
        bodies = self._geometry(body, scope)
        units = [
            part.UnitCollection.FindObject(n)
            for n in ["SquareMilliMeter", "CubicMilliMeter", "Kilogram", "MilliMeter", "Newton"]
        ]
        result = []
        for b in bodies:
            if not b.IsSolidBody:
                raise NXToolError("NX_NOT_SOLID", "Volume requires solid bodies")
            props = part.MeasureManager.NewMassProperties(units, 0.999, [b])
            try:
                props.InformationUnit = self.nxopen.MeasureBodies.AnalysisUnit.KilogramMillimeter
                result.append(
                    {
                        "body": self._reference(b, "body", part, "Body"),
                        "volume_mm3": float(props.Volume),
                    }
                )
            finally:
                props.Dispose()
        return {
            "bodies": result,
            "body_count": len(result),
            "volume_mm3": sum(r["volume_mm3"] for r in result),
            "volume_units": "mm^3",
            "semantics": "sum_of_included_bodies",
            "scope": scope,
            "warnings": [
                "Overlapping bodies are counted separately; this is not union volume or a mass estimate."
            ],
        }

    def _export_step(self, path):
        import hashlib

        result = super()._export_step(path)
        file = Path(result["path"])
        result.update(
            size=file.stat().st_size,
            sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
            units=self._units(),
            components=self._list_components()["count"],
            options={
                "translator": "StepCreator",
                "schema": "AP214",
                "solids": True,
                "surfaces": True,
                "curves": False,
                "layers": "1-256",
            },
            validation="Nonempty file with solid BREP records; geometry equivalence requires round-trip validation",
            warnings=["Export saves the part; native NX undo/checkpoints can expire."],
        )
        return result

    def _screenshot(
        self,
        path=None,
        width=1600,
        height=1000,
        background="white",
        style="shaded_with_edges",
        fit=False,
    ):
        return self._capture_view(path, width, height, background, style, fit)
