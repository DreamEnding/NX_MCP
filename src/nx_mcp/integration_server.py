"""Uniform MCP envelopes and workspace-scoped artifacts for the NX 2606 bridge."""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import os
import struct
import uuid
from typing import Annotated, Literal

from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from nx_mcp import (
    assembly_documentation_server,
    authoring_server,
    documentation_editing_server,
    freeform_server,
    manufacturing_server,
    sheet_metal_server,
)
from nx_mcp.output_schemas import output_schema
from nx_mcp.recovery import OperationStore
from nx_mcp.runtime import NXToolError
from nx_mcp.workspace import WorkspaceViolation


def nx_list_open_parts(
    compact: bool = False,
    path_prefix: str | None = None,
    modified: bool | None = None,
    active_only: bool = False,
    offset: int = 0,
    limit: int | None = None,
):
    """List loaded parts. Defaults preserve full inventory. compact retains opaque identity; path_prefix matches an absolute NX-host path (for example D:/CAD/NX_MCP_WORKSPACE/validation/), case-insensitively with either slash style; workspace-relative prefixes do not match. active_only selects work/display parts. Filters precede paging; count is returned rows, total_count is matching rows. Read-only; never saves parts."""


def nx_list_components(
    compact: bool = False,
    include_transforms: bool = True,
    name_contains: str | None = None,
    suppressed: bool | None = None,
    offset: int = 0,
    limit: int | None = None,
):
    """List recursive component occurrences without loading prototypes. load_state reports fully_loaded, partially_loaded or unloaded; part_path can be null if NX cannot resolve it. Unloaded subassemblies may hide descendants. Use nx_open_part(load_components=true) for explicit recovery. compact retains occurrence paths and omits repeated identity metadata and legacy rotation. include_transforms=False omits pose fields. Name filtering is case-insensitive. Filters precede paging; total_count is matching rows. Defaults preserve existing full results."""


def nx_flat_pattern_orientation_edges(upward_face: str):
    """List current straight boundary edges of an owned planar sheet-metal web face, with endpoints and adjacent face IDs. Pass a returned edge ID as flat_pattern.x_axis_edge. Geometric eligibility only; NX validates the final feature. Does not mutate or invalidate references."""


def nx_list_reference_sets():
    """List work-part custom reference sets, exact direct members and automatic-add setting. Built-in Entire Part and Empty are listed separately."""


def nx_create_reference_set(name: str, objects: list[str]):
    """Create a custom reference set with explicit owned body/curve/datum/direct-component IDs. Use only solid body IDs to exclude prototype datums from assembly drawings. No automatic component membership; duplicate names are rejected. Changes the work part; save it to persist."""


def nx_set_component_reference_set(components: list[str], name: str):
    """Assign an exact existing prototype reference-set name to direct children of the work assembly. Activate a nested owning assembly before editing its children. Preflights every target; does not change prototype membership or component poses. Returns previous/current assignments. Save the assembly to persist."""


def nx_list_datums():
    """List work-part datum planes, axes and coordinate systems with IDs and blanked state. Does not recurse into component prototypes."""


def nx_set_datum_visibility(visible: bool = False):
    """Show/hide all owned datums and coordinate systems in the work/display part. Returns restore_id for nx_restore_display. To exclude component prototype datums from drawings, assign a body-only reference set instead. Changes can persist when saved."""


def nx_boolean(
    boolean_type: Literal["unite", "subtract", "intersect"],
    targets: Annotated[list[str], Field(min_length=2)],
):
    pass


def nx_display_info(objects: list[str], count_only: bool = False):
    pass


def nx_set_display(
    objects: list[str],
    color_index: int | None = None,
    transparency: int | None = None,
    color: Literal[
        "red", "green", "blue", "yellow", "cyan", "magenta", "orange", "white", "black", "gray"
    ]
    | None = None,
):
    pass


def nx_set_visibility(objects: list[str], mode: Literal["show", "hide", "isolate"] = "show"):
    pass


def nx_restore_display(restore_id: str):
    pass


def nx_highlight_collisions(obj1: str, obj2: str, include_contact: bool = False):
    pass


def nx_clear_highlights():
    pass


def nx_list_sections():
    pass


def nx_section_view(
    origin: list[float],
    normal: list[float],
    section: str | None = None,
    name: str = "MCP section",
    cap: bool = True,
):
    pass


def nx_section_control(section: str, action: Literal["enable", "disable", "delete"]):
    pass


def nx_sketch_diagnostics(sketch_id: str):
    pass


def nx_ui_control(mode: Literal["status", "manual", "agent"] = "status"):
    pass


def nx_view_info():
    pass


def nx_screenshot(
    path: str | None = None,
    width: int = 1600,
    height: int = 1000,
    background: Literal["white", "original", "transparent"] = "white",
    style: Literal["current", "shaded", "shaded_with_edges", "wireframe"] = "shaded_with_edges",
    fit: bool = False,
):
    pass


def nx_check_interference(obj1: str, obj2: str):
    pass


def nx_check_clearance(
    objects: list[str] | None = None,
    minimum_clearance: float = 0.0,
    max_pairs: int = 1000,
    include_clear: bool = False,
):
    pass


# Signature-only definitions are used to publish the actual bridge arguments.
def nx_create_sketch(
    plane: Literal["XY", "XZ", "YZ"] = "XY",
    name: str | None = None,
    origin: list[float] | None = None,
    x_axis: list[float] | None = None,
    y_axis: list[float] | None = None,
):
    pass


def nx_open_part(path: str, work: bool = True, display: bool = True, load_components: bool = False):
    pass


def nx_activate_part(part: str, work: bool = True, display: bool = True):
    pass


def nx_close_part(save: bool = True, part: str | None = None):
    pass


def nx_sketch_constraint(
    constraint_type: Literal[
        "horizontal",
        "vertical",
        "fix",
        "fixed",
        "parallel",
        "perpendicular",
        "equal_length",
        "equal_radius",
        "concentric",
        "tangent",
        "coincident",
        "distance",
        "length",
        "radius",
        "diameter",
        "angle",
    ],
    targets: list[str],
    value: float | None = None,
):
    """Apply a supported native constraint to curves owned by one sketch."""


def nx_sketch_info(sketch_id: str):
    pass


def nx_sketch_arc(
    cx: float,
    cy: float,
    radius: float,
    start_angle: float,
    end_angle: float,
    sketch_id: str | None = None,
):
    pass


def nx_edit_feature(name: str, params: dict[str, float]):
    pass


def nx_pattern(
    features: list[str],
    pattern_type: Literal["linear"] = "linear",
    direction: Literal["X", "Y", "Z", "-X", "-Y", "-Z"] = "X",
    spacing: float = 10,
    count: int = 2,
):
    pass


def nx_import_geometry(
    path: str,
    flatten: bool = False,
    target: Literal["work_part", "new_part"] = "work_part",
    output_path: str | None = None,
):
    pass


def nx_get_bounding_box(
    body: str | None = None,
    scope: Literal["auto", "part", "assembly"] = "auto",
    precision: Literal["conservative", "exact"] = "conservative",
):
    pass


def nx_checkpoint(label: str = "checkpoint"):
    pass


def nx_checkpoint_state():
    pass


def nx_rollback(checkpoint_id: str):
    pass


def nx_operation_status(operation_id: str):
    pass


def nx_cancel_operation(operation_id: str):
    pass


def nx_list_topology(
    body: str, face: str | None = None, include_adjacency: bool = False, compact: bool = False
):
    pass


def nx_measure_volume(body: str | None = None, scope: Literal["auto", "part", "assembly"] = "auto"):
    pass


def nx_package_assembly(path: str):
    pass


def nx_add_component(
    part_path: str,
    name: str | None = None,
    translation: list[float] | None = None,
    rotation_matrix: list[list[float]] | None = None,
):
    pass


def nx_set_component_transform(
    component: str, translation: list[float], rotation_matrix: list[list[float]]
):
    pass


def nx_read_result(
    result_id: str,
    field: str = "",
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
):
    """Read a stored oversized bridge result without repeating its operation. field is a JSON pointer; arrays/strings are paged. Nested omissions remain explicit. Snapshot expiry does not delete operation receipts."""


class BatchParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SketchPoint(BatchParameters):
    x: float
    y: float


class SegmentParameters(BatchParameters):
    sketch_id: str
    start: SketchPoint
    end: SketchPoint


class RectangleParameters(BatchParameters):
    sketch_id: str
    corner1: SketchPoint
    corner2: SketchPoint


class ArcParameters(BatchParameters):
    cx: float
    cy: float
    radius: Annotated[float, Field(gt=0)]
    start_angle: float
    end_angle: float
    sketch_id: str | None = None


Vector3 = Annotated[list[float], Field(min_length=3, max_length=3)]
Matrix3 = Annotated[list[Vector3], Field(min_length=3, max_length=3)]


class ComponentParameters(BatchParameters):
    part_path: str
    name: str | None = None
    translation: Vector3 | None = None
    rotation_matrix: Matrix3 | None = None


class TransformParameters(BatchParameters):
    component: str
    translation: Vector3
    rotation_matrix: Matrix3


class RepositionParameters(BatchParameters):
    component: str
    dx: float = 0
    dy: float = 0
    dz: float = 0
    rx: float = 0
    ry: float = 0
    rz: float = 0


class SegmentOperation(BatchParameters):
    method: Literal["nx_sketch_line"]
    params: SegmentParameters


class RectangleOperation(BatchParameters):
    method: Literal["nx_sketch_rectangle"]
    params: RectangleParameters


class ArcOperation(BatchParameters):
    method: Literal["nx_sketch_arc"]
    params: ArcParameters


class ComponentOperation(BatchParameters):
    method: Literal["nx_add_component"]
    params: ComponentParameters


class TransformOperation(BatchParameters):
    method: Literal["nx_set_component_transform"]
    params: TransformParameters


class RepositionOperation(BatchParameters):
    method: Literal["nx_reposition_component"]
    params: RepositionParameters


BatchOperation = Annotated[
    SegmentOperation
    | RectangleOperation
    | ArcOperation
    | ComponentOperation
    | TransformOperation
    | RepositionOperation,
    Field(discriminator="method"),
]


def nx_batch(operations: Annotated[list[BatchOperation], Field(min_length=1, max_length=100)]):
    pass


def nx_capabilities(tool: str | None = None, prefix: str | None = None):
    pass


def nx_rename_object(object_id: str, name: str):
    pass


def nx_revolve(
    sketch_name: Annotated[str, Field(min_length=1)],
    angle: float = 360,
    axis: Literal["X", "Y", "Z", "-X", "-Y", "-Z"] = "Z",
    boolean: Literal["none", "unite", "subtract", "intersect"] = "none",
    axis_origin: Vector3 | None = None,
    axis_direction: Vector3 | None = None,
):
    pass


def nx_workspace_info():
    pass


def nx_create_directory(path: str):
    pass


def nx_workspace_list(
    path: str = ".",
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=1000)] = 100,
    prefix: str = "",
):
    pass


def nx_download_file(
    path: str,
    offset: Annotated[int, Field(ge=0)] = 0,
    length: Annotated[int, Field(ge=1, le=262144)] = 262144,
    delivery: Literal["base64", "image", "metadata"] = "base64",
):
    pass


def nx_upload_file(path: str, data_base64: str, sha256: str, total_size: int, offset: int = 0):
    pass


DESCRIPTIONS = {
    "nx_export_step": "Export the active work part as STEP inside the workspace. Saves the part before translation; native undo marks and checkpoints can expire. Use a disposable copy for review-only exports when source saves are unwanted. Returns path, size, SHA-256, units, component count, translator options and validation scope.",
    "nx_boolean": "Boolean solid bodies: unite, subtract or intersect. targets[0] is the target body; targets[1:] are tool bodies. Native cube subtraction and volume checks are scoped in nx_capabilities(tool='nx_boolean'); not general certification.",
    "nx_revolve": "Requires sketch_name (finished sketch ID/name). Revolve about a principal axis through the part origin, or supply both axis_origin=[x,y,z] and nonzero axis_direction in work-part coordinates. Custom axes require leaving axis at its default Z. Angles are degrees, lengths in work-part units; boolean is none/unite/subtract/intersect. Inspect nx_capabilities(tool='nx_revolve') for tested scope.",
    "nx_workspace_list": "List a workspace directory with prefix filtering and pagination (offset>=0, limit=1..1000, default 100). Returns entries/count for this page, total_count and next_offset. File entries include size/SHA-256. Use nx_download_file(delivery='metadata') to inspect one file.",
    "nx_workspace_info": "Discover the NX host workspace root and path rules. Paths refer to the NX machine, not the MCP client's filesystem. No session-wide current directory is changed.",
    "nx_create_directory": "Create a directory and missing parents inside the NX workspace. Accepts workspace-relative or in-workspace absolute host paths. Idempotent: an existing directory succeeds; an existing file fails. Returns actual path and created status.",
    "nx_create_part": "Create a new NX part at an explicit workspace-relative or absolute in-workspace NX-host path, e.g. projects/controller/parts/base.prt. Missing parent folders are created. Units: mm or inch. Use unique part basenames for simultaneously loaded NX parts.",
    "nx_delete_feature": "Delete a work-part feature by typed ID or unambiguous name using native update/undo. Dependent geometry may be deleted; inspect changes.deleted and reacquire topology afterward.",
    "nx_measure_angle": "Measure 0..180 degrees between typed work-part line, straight-edge or planar-face references. Uses line start/end, edge vertex order or outward face normals. Curved entities and component occurrences are unsupported; directions are not an oriented dihedral angle.",
    "nx_sketch_constraint": "Apply a constraint to owned curve IDs in one sketch. Types: horizontal, vertical, fix/fixed, parallel, perpendicular, equal_length, equal_radius, concentric, tangent, coincident, distance/length, radius, diameter, angle. Only dimensions require value in part units or degrees. Coincident means start-to-start; use nx_sketch_relation for explicit endpoints. Midpoint is not supported.",
    "nx_save_as": "Save the active work part to a new .prt path inside the NX workspace, creating missing parent folders. Accepts relative or absolute NX-host paths. Existing files are never overwritten. Save As changes the work part's filename; it does not move an entire assembly dependency tree.",
    "nx_display_info": "Inspect color-table indices, blank state and face transparency for body, component, feature, face or curve references. Components expand to loaded occurrence geometry.",
    "nx_set_display": "Set an NX color index (1–216) or named color, and/or transparency (0 opaque, 100 transparent). Component/feature targets expand to bodies and faces, with a cap of 10000 unique objects. Preflight with nx_display_info(count_only=true); split larger selections after checking counts. Occurrence overrides do not recolor prototypes. Returns restore_id; restore in reverse order. Changes can persist on save.",
    "nx_set_visibility": "Show, hide or isolate body/component geometry. Isolation preserves a restorable snapshot and includes ancestor components. Reference curves and datum geometry are not isolated. Explicit show/hide also accepts curves. Returns restore_id.",
    "nx_restore_display": "Restore explicit appearance/visibility attributes using a same-session restore_id, in reverse order. All references are preflighted; manual handoff, rollback or close can make snapshots stale. Does not reset a part modified flag or remove inherited occurrence overrides.",
    "nx_highlight_collisions": "Measure native solid interference and highlight the involved body occurrences using NX selection highlighting. Replaces previous MCP highlights. Contacts are optional; clear pairs are never highlighted. Returns measured pairs and entity references. No persistent recoloring.",
    "nx_clear_highlights": "Remove only highlights created by MCP. Geometry, persistent colors and visibility are unchanged.",
    "nx_list_sections": "Inspect native dynamic sections and the active view clipping toggle in the display/work part.",
    "nx_section_view": "Create or edit a native single-plane section in visible NX. origin is in display-part units; normal is normalized in display-part coordinates. Solids are unchanged. Specify section ID to edit an existing active section. NX v2606 retains dot(point-origin, normal) <= 0; reversing normal reverses the retained side. Returns actual plane geometry.",
    "nx_section_control": "Enable, disable or delete the specified native section. Disabling turns off clipping when that section is active. Deletion removes the section object, not model solids.",
    "nx_sketch_diagnostics": "Evaluate native solver status and remaining DOF for the entire sketch; enumerate persistent constraints and their curve links. Temporarily activates an inactive sketch and restores the prior state. Rejects another active sketch. Temporarily evaluates the entire sketch and restores the work-region state. Does not infer a minimal conflict set or automatically constrain geometry.",
    "nx_ui_control": "Inspect UI activity without queueing behind native work, or switch control modes. Agent mode reserves NX input even when idle. Status is a timestamped snapshot, not proof that a native call is responsive. Manual handoff invalidates references/checkpoints. Pause/stop cannot interrupt a native call. Finish NX dialogs before resuming.",
    "nx_view_info": "Return the displayed model view, camera matrix, scale, rendering style, and interactive state.",
    "nx_screenshot": "Export the actual interactive NX viewport as PNG and return an inline MCP image. Advisory 128–4096 pixel dimensions (NX can use the actual device size; response reports both), background, shaded/wireframe style and fit. No desktop capture. Paths are workspace-relative; omit for a unique capture path.",
    "nx_check_interference": "Check native solid interference between two body/component references, including nested occurrence geometry. Return penetration/contact/clear, closest points and pairwise interference volumes in mm^3. Temporary solids are rolled back.",
    "nx_check_clearance": "Check distinct solid body occurrences in selected groups or the full assembly. minimum_clearance uses part units. Conservative bounds prune clear pairs; reported distances and interference use native geometry. Pair limits are preflighted. Volumes are pairwise, not union volume.",
    "nx_create_sketch": "Create an active sketch with explicit part-space origin and orthonormal basis. XY: X,Y,+Z; XZ: X,Z,-Y; YZ: Y,Z,+X. Curve coordinates use the returned local basis. Lengths in work-part units.",
    "nx_sketch_info": "Read the actual sketch origin, basis, normal and owned curve coordinates in part space. IDs preferred.",
    "nx_sketch_arc": "Add an arc to the active owning sketch using local coordinates; radius in work-part units and angles in degrees. Full circle: start=0,end=360. Pass sketch_id explicitly.",
    "nx_edit_feature": "Edit native EXTRUDE distance or linear PATTERN_FEATURE count/spacing. Unsupported parameters fail and roll back. IDs or unique case-insensitive names/journal IDs accepted.",
    "nx_pattern": "Create an associative native linear feature pattern. Count includes seed; 16 instances at pitch 16.5 of a 14-wide seed span 261.5. Work-part units.",
    "nx_import_geometry": "Import STEP through installed NX Step214Importer into the work part for solids, or target=new_part with a new output_path for assemblies; flatten=false preserves structure. Reports new directly-owned bodies and resulting components. Translator files are not undone.",
    "nx_get_bounding_box": "Native UF bounds; precision selects conservative or exact (exact requires axis-aligned WCS). auto includes recursive assembly geometry when present; part includes directly owned bodies; assembly includes both. Coordinates and units are work-part absolute.",
    "nx_activate_part": "Activate an already loaded part by ID or unique path/name without closing other parts. Display activation also changes work part under NX rules.",
    "nx_open_part": "Optional load_components=true fully loads unsuppressed occurrence prototypes, including an already loaded parent; restores load preferences and preserves current reference sets. Accept a workspace-relative or absolute in-workspace NX-host path. Open or reuse a loaded workspace .prt and activate it; work/display flags are explicit. Does not recreate loaded parts.",
    "nx_close_part": "Closing a prototype referenced by a loaded assembly is rejected before saving; close parent assemblies first. Close the specified loaded part (ID), or current work part; save defaults true. NX may unload unused assembly prototypes. Returns all closed part references/counts; re-list open parts between closes.",
    "nx_checkpoint": "Create an in-session model undo checkpoint. NX v2606 saves expire native marks; create a new checkpoint after save. Restart/close also invalidates checkpoints.",
    "nx_checkpoint_state": "Inspect available checkpoint IDs and retained model-operation history. Read-only calls retain marks. Native NX save can expire them; availability is checked against NX.",
    "nx_rollback": "Rollback to an in-session checkpoint. Rejects rollback across mutations to unrelated parts. Reacquire object IDs afterward; save explicitly to persist.",
    "nx_operation_status": "Read durable request state without waiting on the NX thread: running, committed, failed, unknown. Unknown never authorizes blind retry.",
    "nx_cancel_operation": "Request cooperative cancellation of a running batch between child operations. Cannot interrupt a single NXOpen call; inspect final outcome.",
    "nx_batch": "Execute 1–100 sketch-curve/add-component/placement operations serially on the NX thread under one rollback mark. Structural preflight, progress, cancellation; all-or-rollback for model changes. Supply one stable operation_id for safe retry.",
    "nx_add_component": "Add a .prt occurrence with initial translation and right-handed row-major 3x3 rotation. Work-part coordinates; p_parent=R*p_local+t. Returns typed occurrence.",
    "nx_set_component_transform": "Assign absolute translation and row-major rotation to an immediate child. Read-back verified; repeating the same placement is idempotent. Activate owning subassembly for nested placement.",
    "nx_reposition_component": "Relative translation and rotation of immediate child in work-part coordinates. Degrees, Rz*Ry*Rx. Use a stable operation_id for retry; use nx_set_component_transform for absolute placement.",
    "nx_measure_distance": "Measure minimum BREP distance for body, face, edge, feature-body or component pairs, including nested occurrences. Returns closest points, accuracy and work-part units. Zero does not prove interference.",
    "nx_list_topology": "Enumerate body topology. Optional face restricts results to that face and its boundary edges; include_adjacency returns face_edges and edge_faces ID mappings. compact retains minimal typed IDs. Use current upward-face boundary edges for flat-pattern orientation rather than guessing a former outer edge. Reacquire after edits/rollback/close.",
    "nx_rename_object": "Rename a referenced object and return its actual NX-normalized display name. Reacquire references afterward.",
    "nx_download_file": "Read a workspace file. delivery=image returns an existing PNG inline as MCP image content (max 8 MiB), without base64 in text. delivery=metadata returns size/SHA-256 only. Default base64 returns chunks: offset>=0, length=1..262144, bytes_returned/next_offset/eof. Image/metadata modes require default chunk arguments. Paths are on the NX host.",
    "nx_upload_file": "Upload .prt/.step/.stp/.png/.json/.zip/.txt/.pdf chunks (max 256 KiB) into a new workspace file. Requires final SHA-256 and total size, sequential offsets. Repeated identical chunks are safe; existing differing files are never overwritten.",
    "nx_package_assembly": "Package the saved active assembly and all loaded prototype dependencies into a new workspace ZIP with a SHA-256 manifest. Refuses unsaved referenced parts and files outside the workspace.",
    "nx_capabilities": "Inspect NX-version-specific tested scope. Use tool=exact_name or prefix=nx_sheet to limit response size; omit both for all tools. API detection is separate from native test evidence. Batch NX has no model viewport.",
}

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
    "nx_ui_control",
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
    "nx_workspace_info",
    "nx_workspace_list",
    "nx_read_result",
    "nx_download_file",
}
READ_ONLY.update(
    authoring_server.READ_ONLY
    | sheet_metal_server.READ_ONLY
    | freeform_server.READ_ONLY
    | manufacturing_server.READ_ONLY
    | assembly_documentation_server.READ_ONLY
    | documentation_editing_server.READ_ONLY
)
DESCRIPTIONS.update(
    {
        name: obj.__doc__ or name
        for name, obj in {
            **vars(authoring_server),
            **vars(sheet_metal_server),
            **vars(freeform_server),
            **vars(manufacturing_server),
            **vars(assembly_documentation_server),
            **vars(documentation_editing_server),
        }.items()
        if name.startswith("nx_") and inspect.isfunction(obj)
    }
)

SIDE = {
    "nx_create_directory",
    "nx_workspace_info",
    "nx_workspace_list",
    "nx_read_result",
    "nx_download_file",
    "nx_upload_file",
    "nx_operation_status",
    "nx_cancel_operation",
}
PATHS = {
    "nx_export_planar_dxf": "path",
    "nx_export_explosion_animation": "path",
    "nx_export_flat_pattern": "path",
    "nx_set_sheet_metal_defaults": "bend_table",
    "nx_render_view": "path",
    "nx_copy_project": "path",
    "nx_component_action": "part_path",
    "nx_save_presentation": "path",
    "nx_restore_presentation": "path",
    "nx_inspection_report": "path",
    "nx_create_part": "path",
    "nx_open_part": "path",
    "nx_export_step": "path",
    "nx_add_component": "part_path",
    "nx_import_geometry": "path",
    "nx_screenshot": "path",
    "nx_save_as": "path",
    "nx_export_drawing_pdf": "path",
}


class IntegrationEnvelope(BaseModel):
    """Common output contract; each tool's result fields remain extensible."""

    model_config = ConfigDict(extra="allow")
    status: Literal["success", "error"]
    warnings: list[str]
    units: str | None
    operation_id: str | None = None
    mutation_outcome: str | None = None


def envelope(payload, error=False):
    if payload.get("status") not in {None, "success", "error"}:
        payload = {**payload, "validation_status": payload["status"], "status": "success"}
    payload = {"status": "error" if error else "success", "warnings": [], "units": None, **payload}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structuredContent=payload,
        isError=error,
    )


def configure(mcp, bridge, workspace):
    if workspace is None:
        return
    existing = dict(mcp._tool_manager._tools)
    definitions = {
        name: obj
        for name, obj in globals().items()
        if name.startswith("nx_") and inspect.isfunction(obj)
    }
    definitions.update(
        {
            name: obj
            for name, obj in {
                **vars(authoring_server),
                **vars(sheet_metal_server),
                **vars(freeform_server),
                **vars(manufacturing_server),
                **vars(assembly_documentation_server),
                **vars(documentation_editing_server),
            }.items()
            if name.startswith("nx_") and inspect.isfunction(obj)
        }
    )
    names = set(existing) | set(definitions)
    for name in names:
        old = existing.get(name)
        fn = definitions.get(name) or old.fn
        sig = inspect.signature(fn)
        # Resolve postponed annotations in the original callable's module.
        from typing import get_type_hints

        hints = get_type_hints(fn, include_extras=True)
        parameters = [
            p.replace(annotation=hints.get(p.name, p.annotation)) for p in sig.parameters.values()
        ]
        if name not in READ_ONLY and name not in SIDE and name != "nx_package_assembly":
            parameters.append(
                inspect.Parameter(
                    "operation_id",
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=str | None,
                )
            )
        sig = sig.replace(parameters=parameters, return_annotation=CallToolResult)

        def factory(method, signature):
            async def proxy(**kwargs):
                try:
                    bound = signature.bind(**kwargs)
                    bound.apply_defaults()
                    from pydantic_core import to_jsonable_python

                    params = to_jsonable_python(dict(bound.arguments))
                    if "operation_id" in params and method not in {
                        "nx_operation_status",
                        "nx_cancel_operation",
                    }:
                        params["operation_id"] = params["operation_id"] or (
                            "op_" + uuid.uuid4().hex
                        )
                    if method == "nx_package_assembly":
                        result = await package_assembly(bridge, workspace, params["path"])
                    elif method in SIDE:
                        result = artifact_call(method, params, workspace)
                    else:
                        if (path_key := PATHS.get(method)) and params.get(path_key) is not None:
                            params[path_key] = str(workspace.resolve(params[path_key]))
                        if method == "nx_import_geometry" and params.get("output_path"):
                            params["output_path"] = str(workspace.resolve(params["output_path"]))
                        if method == "nx_batch":
                            for op in params["operations"]:
                                if op.get("method") == "nx_add_component" and "part_path" in op.get(
                                    "params", {}
                                ):
                                    op["params"]["part_path"] = str(
                                        workspace.resolve(op["params"]["part_path"])
                                    )
                        result = await bridge.call(method, params)
                    response = envelope(result, error=result.get("status") == "error")
                    if (
                        method in {"nx_screenshot", "nx_render_view"}
                        or (method == "nx_download_file" and params["delivery"] == "image")
                    ) and not response.isError:
                        file = workspace.resolve(result["path"])
                        if file.stat().st_size <= 8 * 1024 * 1024:
                            data = file.read_bytes()
                            if hashlib.sha256(data).hexdigest() != result["sha256"]:
                                raise NXToolError(
                                    "NX_ARTIFACT_CHANGED",
                                    "Capture file changed after its operation committed",
                                    details={"mutation_outcome": "committed"},
                                )
                            response.content.append(
                                ImageContent(
                                    type="image",
                                    mimeType="image/png",
                                    data=base64.b64encode(data).decode(),
                                )
                            )
                        else:
                            result.setdefault("warnings", []).append(
                                "Image exceeds inline limit; use nx_download_file"
                            )
                            response = envelope(result)
                    return response
                except (NXToolError, WorkspaceViolation, ValueError, TypeError) as e:
                    error = (
                        e
                        if isinstance(e, NXToolError)
                        else NXToolError(
                            "NX_PATH_OUTSIDE_WORKSPACE"
                            if isinstance(e, WorkspaceViolation)
                            else "NX_INVALID_ARGUMENT",
                            str(e),
                        )
                    )
                    if "params" in locals() and params.get("operation_id"):
                        error.details.setdefault("operation_id", params["operation_id"])
                        error.details.setdefault("mutation_outcome", "unknown")
                    return envelope(error.as_dict(), True)

            proxy.__name__ = method
            proxy.__signature__ = signature
            return proxy

        if old:
            mcp.remove_tool(name)
        description = DESCRIPTIONS.get(
            name,
            (inspect.getdoc(definitions[name]) if name in definitions else None)
            or (old.description if old else name),
        )
        description = description.replace("EXPERIMENTAL: ", "")
        if description.strip() == name:
            description = (
                "Experimental NX operation; semantics and installed API support have not been validated. "
                + name
            )
        mcp.add_tool(
            factory(name, sig),
            name=name,
            description=description,
            structured_output=False,
            annotations=ToolAnnotations(
                readOnlyHint=name in READ_ONLY and name != "nx_ui_control",
                idempotentHint=name in READ_ONLY
                or name
                in {
                    "nx_set_component_transform",
                    "nx_create_directory",
                    "nx_edit_explosion",
                    "nx_show_explosion",
                },
            ),
        )
        tool = mcp._tool_manager.get_tool(name)
        tool.fn_metadata.output_model = IntegrationEnvelope
        tool.fn_metadata.output_schema = output_schema(
            name, IntegrationEnvelope.model_json_schema()
        )
        tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
        tool.fn_metadata.arg_model.model_rebuild(force=True)
        tool.parameters = tool.fn_metadata.arg_model.model_json_schema()
        if name == "nx_edit_sketch":
            tool.parameters["properties"]["operations"].update(
                minItems=1, maxItems=100, items={"oneOf": authoring_server.SKETCH_OPERATION_SCHEMAS}
            )
        if name == "nx_edit_explosion":
            for schema in tool.parameters["properties"]["placements"]["anyOf"]:
                if schema.get("type") == "array":
                    schema.update(maxItems=1000, items=authoring_server.EXPLOSION_PLACEMENT_SCHEMA)
    original_call = mcp.call_tool

    async def uniform_call(name, arguments):
        try:
            return await original_call(name, arguments)
        except Exception as error:
            return envelope(
                NXToolError(
                    "NX_INVALID_ARGUMENT", str(error), details={"mutation_outcome": "not_started"}
                ).as_dict(),
                True,
            )

    mcp.call_tool = uniform_call
    mcp._mcp_server.call_tool(validate_input=False)(uniform_call)
    mcp._mcp_server.instructions = "Siemens NX v2606 integration. Use nx_capabilities for tested scope. Discover the host root with nx_workspace_info. File paths are workspace-relative or absolute inside that root; use explicit project subfolders on every file call. Use client-supplied operation_id for mutation retry; query receipts after transport failure. No general certification is claimed."


def artifact_call(method, p, workspace):
    store = OperationStore(workspace.root)
    if method == "nx_read_result":
        from nx_mcp.result_transport import read_result

        return read_result(workspace.root / ".nx-mcp" / "bridge-results", **p)
    if method == "nx_operation_status":
        return store.get(p["operation_id"])
    if method == "nx_cancel_operation":
        record = store.get(p["operation_id"])
        if record["state"] != "running" or record.get("method") != "nx_batch":
            raise NXToolError("NX_NOT_CANCELLABLE", "Only running batches accept cancellation")
        store.path(p["operation_id"]).with_suffix(".cancel").touch()
        return {
            "status": "success",
            "operation_id": p["operation_id"],
            "cancellation_requested": True,
        }
    if method == "nx_workspace_info":
        return {
            "status": "success",
            "root": str(workspace.root),
            "path_host": "NX server",
            "relative_to": str(workspace.root),
            "absolute_paths": "accepted inside workspace only",
            "parent_creation": ["nx_create_part", "nx_save_as", "nx_upload_file"],
            "current_directory": "No mutable current directory; use explicit paths on every call",
        }
    path = workspace.resolve(p["path"])
    if method == "nx_create_directory":
        existed = path.is_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise NXToolError("NX_DIRECTORY_ERROR", str(error)) from error
        return {
            "status": "success",
            "path": str(path),
            "relative_path": str(path.relative_to(workspace.root)),
            "created": not existed,
            "mutation_outcome": "committed",
        }
    if ".nx-mcp" in path.relative_to(workspace.root).parts:
        raise NXToolError("NX_PATH_RESERVED", "Internal service state is not an artifact")

    def metadata(file):
        h = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
        return {
            "path": str(file.relative_to(workspace.root)),
            "size": file.stat().st_size,
            "sha256": h.hexdigest(),
        }

    if method == "nx_workspace_list":
        if not path.exists():
            raise NXToolError(
                "NX_DIRECTORY_NOT_FOUND",
                "Workspace directory does not exist; inspect the parent directory or correct path",
            )
        if not path.is_dir():
            raise NXToolError(
                "NX_NOT_DIRECTORY",
                "path must name a workspace directory; use nx_download_file(delivery='metadata') for a file",
            )
        offset, limit, prefix = p.get("offset", 0), p.get("limit", 100), p.get("prefix", "")
        if offset < 0 or not 1 <= limit <= 1000:
            raise NXToolError("NX_INVALID_ARGUMENT", "offset must be >= 0; limit must be 1..1000")
        files = [
            f
            for f in sorted(path.iterdir())
            if f.name.casefold() != ".nx-mcp" and f.name.startswith(prefix)
        ]
        items = []
        for f in files[offset : offset + limit]:
            workspace.ensure_inside(f)
            items.append(
                metadata(f) | {"kind": "file"}
                if f.is_file()
                else {"path": str(f.relative_to(workspace.root)), "kind": "directory"}
            )
        next_offset = offset + len(items)
        return {
            "status": "success",
            "path": str(path),
            "entries": items,
            "count": len(items),
            "total_count": len(files),
            "offset": offset,
            "next_offset": next_offset if next_offset < len(files) else None,
        }
    if method == "nx_download_file":
        if p["offset"] < 0 or not 1 <= p["length"] <= 262144:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "offset must be >= 0; length must be 1..262144 bytes"
            )
        if not path.is_file():
            raise NXToolError("NX_FILE_NOT_FOUND", "path must name an existing workspace file")
        delivery = p.get("delivery", "base64")
        if delivery not in {"base64", "image", "metadata"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "delivery must be base64, image or metadata")
        if delivery != "base64" and (p["offset"] != 0 or p["length"] != 262144):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "image/metadata delivery requires default offset=0 and length=262144; chunk ranges apply only to base64",
            )
        meta = metadata(path)
        if delivery == "metadata":
            return {"status": "success", **meta, "delivery": delivery}
        if delivery == "image":
            if meta["size"] > 8 * 1024 * 1024:
                raise NXToolError(
                    "NX_IMAGE_TOO_LARGE", "Inline PNG limit is 8 MiB; use base64 chunks"
                )
            with path.open("rb") as stream:
                header = stream.read(24)
            if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
                raise NXToolError(
                    "NX_UNSUPPORTED_IMAGE",
                    "Inline delivery supports PNG files with an IHDR header; use base64 for other formats",
                )
            return {
                "status": "success",
                **meta,
                "delivery": delivery,
                "mime_type": "image/png",
                "resolution": list(struct.unpack(">II", header[16:24])),
            }
        with path.open("rb") as stream:
            stream.seek(p["offset"])
            data = stream.read(p["length"])
        next_offset = p["offset"] + len(data)
        return {
            "status": "success",
            **meta,
            "offset": p["offset"],
            "bytes_returned": len(data),
            "next_offset": next_offset if next_offset < meta["size"] else None,
            "data_base64": base64.b64encode(data).decode(),
            "eof": next_offset >= meta["size"],
        }
    if path.suffix.lower() not in {
        ".prt",
        ".step",
        ".stp",
        ".png",
        ".json",
        ".zip",
        ".txt",
        ".pdf",
    }:
        raise NXToolError(
            "NX_UNSUPPORTED_FILE_TYPE", "Upload is limited to CAD and review artifacts"
        )
    if not 0 <= p["offset"] <= p["total_size"] <= 256 * 1024 * 1024:
        raise NXToolError("NX_INVALID_ARGUMENT", "Invalid offset or total_size (max 256 MiB)")
    if len(p["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in p["sha256"]):
        raise NXToolError("NX_INVALID_ARGUMENT", "Expected lowercase SHA-256")
    data = base64.b64decode(p["data_base64"], validate=True)
    if len(data) > 262144 or p["offset"] + len(data) > p["total_size"]:
        raise NXToolError("NX_INVALID_ARGUMENT", "Chunk exceeds limit")
    if path.exists():
        meta = metadata(path)
        if meta["sha256"] == p["sha256"] and meta["size"] == p["total_size"]:
            return {"status": "success", **meta, "committed": True, "replayed": True}
        raise NXToolError("NX_FILE_EXISTS", "Existing file differs; choose a new destination")
    staging = workspace.root / ".nx-mcp" / "uploads"
    staging.mkdir(exist_ok=True, parents=True)
    key = hashlib.sha256(
        (str(path) + "|" + p["sha256"] + "|" + str(p["total_size"])).encode()
    ).hexdigest()
    temp = staging / key
    size = temp.stat().st_size if temp.exists() else 0
    if p["offset"] < size:
        with temp.open("rb") as f:
            f.seek(p["offset"])
            previous = f.read(len(data))
        if previous != data:
            raise NXToolError("NX_UPLOAD_CONFLICT", "Retried chunk differs from staged data")
    elif p["offset"] == size:
        with temp.open("ab") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    else:
        raise NXToolError("NX_UPLOAD_GAP", "Chunks must be contiguous")
    complete = temp.stat().st_size == p["total_size"]
    if complete:
        if metadata(temp)["sha256"] != p["sha256"]:
            raise NXToolError(
                "NX_CHECKSUM_MISMATCH",
                "Uploaded bytes do not match SHA-256; choose a corrected upload",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive destination creation prevents overwrite races between clients.
        os.link(temp, path)  # Atomic exclusive publication on the same filesystem.
        temp.unlink()
    return {
        "status": "success",
        "path": str(path.relative_to(workspace.root)),
        "received": size + len(data) if p["offset"] == size else size,
        "committed": complete,
        "sha256": p["sha256"],
    }


async def package_assembly(bridge, workspace, path):
    import zipfile

    destination = workspace.resolve(path)
    if destination.suffix.lower() != ".zip":
        raise NXToolError("NX_INVALID_ARGUMENT", "Package path must end with .zip")
    opened = await bridge.call("nx_list_open_parts", {})
    active = next(p for p in opened["parts"] if p["work"])
    components = await bridge.call("nx_list_components", {})
    paths = {active["path"]} | {c["part_path"] for c in components["components"]}
    for p in opened["parts"]:
        if p["path"] in paths and p["modified"]:
            raise NXToolError(
                "NX_UNSAVED_PART", "Save referenced parts before packaging: " + p["path"]
            )
    manifest = []
    for p in sorted(paths):
        file = workspace.ensure_inside(p)
        if not file.is_file():
            raise NXToolError("NX_FILE_NOT_FOUND", str(file))
        manifest.append(
            {
                "path": str(file.relative_to(workspace.root)).replace("\\", "/"),
                "size": file.stat().st_size,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = workspace.root / ".nx-mcp" / ("package-" + uuid.uuid4().hex + ".zip")
    temp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in manifest:
                archive.write(workspace.resolve(item["path"]), item["path"])
            archive.writestr(
                "nx-assembly-manifest.json",
                json.dumps(
                    {"assembly": active, "components": components, "files": manifest}, indent=2
                ),
            )
        os.link(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return {
        "status": "success",
        "path": str(destination.relative_to(workspace.root)),
        "size": destination.stat().st_size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "prototype_files": len(paths) - 1,
        "component_instances": components["count"],
        "files": manifest,
    }
