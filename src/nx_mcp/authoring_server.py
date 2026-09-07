"""Public signatures and contracts for authoring/review tools."""

from __future__ import annotations

from typing import Any, Literal

READ_ONLY = {
    "nx_list_explosions",
    "nx_explosion_info",
    "nx_find_geometry",
    "nx_list_expressions",
    "nx_model_health",
    "nx_model_summary",
    "nx_resolve_geometry",
    "nx_recognize_holes",
    "nx_list_component_patterns",
    "nx_sketch_conflicts",
    "nx_feature_parameters",
}
NON_MODEL = {
    "nx_highlight_objects",
    "nx_save_presentation",
    "nx_inspection_report",
    "nx_preview_change",
}


def nx_find_geometry(
    owner: str | None = None,
    kind: Literal["face", "edge"] = "face",
    geometry_type: Literal["any", "plane", "cylinder", "circle", "line"] = "any",
    normal: list[float] | None = None,
    radius: float | None = None,
    near: list[float] | None = None,
    order: Literal["nearest", "highest", "lowest"] = "nearest",
    axis: Literal["X", "Y", "Z"] = "Z",
    tolerance: float = 0.001,
    offset: int = 0,
    limit: int = 50,
):
    """Find geometry within a body/feature/component or full assembly. Coordinates and radii use work-part units/frame. Nearest uses native BREP point-to-face/edge minimum distance and closest points; highest/lowest use conservative bounds centers. Returns a reusable geometric selector; resolve it explicitly after edits with nx_resolve_geometry. nearest requires near=[x,y,z]. Normal filter requires planar faces; tolerance is 1-dot for normals and absolute length for radii. Returns paginated candidates; never silently selects one."""


def nx_highlight_objects(objects: list[str]):
    """Preview candidate body/face/edge/curve/component references using native selection highlighting. Replaces MCP-owned highlights; clear with nx_clear_highlights. No persistent recoloring."""


def nx_list_expressions(name_contains: str | None = None, offset: int = 0, limit: int = 50):
    """Paginate work-part expressions, formulas, numeric values in expression units, editability and stored immediate dependencies. Names are case-sensitive. Non-numeric values are null. Conditional dependencies may be incomplete."""


def nx_set_expression(
    expression: str,
    formula: str,
    create: bool = False,
    units: Literal["unitless", "mm", "inch", "deg", "rad"] = "unitless",
):
    """Create a named Number expression or edit a local editable expression ID/name. Formula is NX expression syntax. Units apply only on creation; edits retain original units. Rebuild dependent features and roll back on errors. New name must be a simple identifier. Locked/interpart expressions are rejected."""


def nx_bind_parameter(
    feature: str, parameter: Literal["start", "end", "count", "spacing"], expression: str
):
    """Bind EXTRUDE start/end limits or PATTERN_FEATURE count/spacing to an existing Number expression. Native units/dimensional compatibility applies; failed updates roll back. Returns feature dependencies and expression read-back."""


def nx_model_health(scope: Literal["part", "assembly"] = "part", offset: int = 0, limit: int = 50):
    """Inspect native feature errors/warnings, suppression, unloaded prototype availability and UF body consistency. Assembly scope checks unique loaded unsuppressed prototypes. Does not force rebuild or certify design intent. Paginate issues; counts cover the whole query."""


def nx_rebuild_model():
    """Run native DoUpdate for pending changes and return model health. Update errors roll back this operation; does not force every already-current feature to regenerate."""


def nx_edit_sketch(sketch_id: str, operations: list[dict[str, Any]]):
    """Reopen an existing sketch for 1–100 atomic local edits; restore prior activation and return solver diagnostics. Points are local [x,y], angles degrees. Operations: {action:add_line,start,end}; {action:line,curve,start,end}; {action:arc,curve,center,radius,start_angle,end_angle}; {action:delete,object} for owned curve/constraint; {action:constraint,curve,type:fixed|horizontal|vertical}. Curve ownership is checked; another active sketch is rejected. Edit dimensional constraints through their expression IDs with nx_set_expression. Deleting or recreating constraints is explicit; no automatic constraint removal."""


def nx_component_action(
    component: str,
    action: Literal["rename", "suppress", "unsuppress", "remove", "replace"],
    name: str | None = None,
    part_path: str | None = None,
):
    """Rename, replace, remove, suppress or unsuppress an immediate child occurrence of the work assembly. rename requires name; replace requires existing workspace .prt part_path and replaces only this occurrence while retaining relationships. Other actions reject those fields. Suppression affects all arrangements. Checks unchanged placement; native errors roll back. Prototype files are not deleted by remove."""


def nx_pattern_components(component: str, direction: list[float], spacing: float, count: int):
    """Create 2–100 total occurrences including the seed at a positive pitch in work-part units along normalized direction. Seed must be an unsuppressed immediate child. Returns independent positioned occurrences, not a native associative pattern. Runs serially in one rollback transaction."""


def nx_set_camera(rotation: list[list[float]], origin: list[float], scale: float):
    """Set absolute NX viewport camera. rotation is right-handed orthonormal row-major 3x3 with columns NX view axes; origin is the view-space point centered in the viewport; scale is positive absolute NX scale. Work and display part must match. Returns actual camera."""


def nx_save_presentation(path: str):
    """Save camera, one active section, explicit appearance and loaded assembly body/component visibility into a new workspace JSON file. Includes per-face colors/transparency and journal locators. Does not save the CAD part. Excludes datum visibility, materials and inherited-override semantics."""


def nx_restore_presentation(path: str):
    """Restore a saved presentation in its original owner part. Preflight all journal references before mutation; stale references reject restoration. Geometry is unchanged, but display attributes can mark the part modified. Across topology revisions verify journal references still denote intended geometry."""


def nx_inspection_report(
    path: str,
    objects: list[str] | None = None,
    minimum_clearance: float = 0.0,
    max_pairs: int = 100,
    include_clear: bool = False,
    capture: bool = True,
    section_planes: list[dict[str, list[float]]] | None = None,
):
    """Create a new workspace ZIP containing HTML/JSON clearance results, checksums, native viewport PNG, up to eight flagged-pair close-ups and six section screenshots. section_planes entries require origin and normal in work-part coordinates. Restores temporary view/visibility/section changes. max_pairs caps native clearance work; report does not imply unchecked pairs are clear. Retrieve with nx_download_file."""


def nx_model_summary(
    section: Literal["overview", "components", "features", "expressions", "sketches"] = "overview",
    offset: int = 0,
    limit: int = 50,
):
    """Compact work-part summary: counts, bounds, native health and paginated assembly hierarchy, feature parents/parameters, expressions or sketch frames. Owned-body and occurrence counts are separate. Does not infer missing design dimensions."""


def nx_preview_change(operations: list[dict[str, Any]], capture: bool = True):
    """Temporarily apply 1–25 edits under a native checkpoint, return before/after bounds/volume/parameters/health and optional screenshot, then roll back before returning. Each entry has method and params. Supports nx_set_expression, nx_bind_parameter, nx_edit_feature, nx_edit_sketch, nx_set_component_transform and nx_reposition_component. Returned geometry IDs are stale after rollback. Preview is session-scoped and expires on intervening mutations or manual handoff."""


def nx_finish_preview(preview_id: str, action: Literal["accept", "discard"]):
    """Accept by atomically reapplying stored preview edits only if its part and mutation epoch are unchanged, or discard the stored plan. Preview already restored original geometry. Accepted edits remain unsaved and undoable. Stable operation_id prevents double application."""


# Nested operation schemas are published explicitly; embedded NX validates the
# same exact key sets before changing sketch geometry.
POINT2_SCHEMA = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
SKETCH_OPERATION_SCHEMAS = []
_SKETCH_FIELDS: dict[str, dict[str, Any]] = {
    "add_line": {"start": POINT2_SCHEMA, "end": POINT2_SCHEMA},
    "line": {"curve": {"type": "string"}, "start": POINT2_SCHEMA, "end": POINT2_SCHEMA},
    "arc": {
        "curve": {"type": "string"},
        "center": POINT2_SCHEMA,
        "radius": {"type": "number", "exclusiveMinimum": 0},
        "start_angle": {"type": "number"},
        "end_angle": {"type": "number"},
    },
    "delete": {"object": {"type": "string"}},
    "constraint": {
        "curve": {"type": "string"},
        "type": {"type": "string", "enum": ["fixed", "horizontal", "vertical"]},
    },
}
for _action, _fields in _SKETCH_FIELDS.items():
    SKETCH_OPERATION_SCHEMAS.append(
        {
            "type": "object",
            "properties": {"action": {"const": _action}, **_fields},
            "required": ["action", *_fields],
            "additionalProperties": False,
        }
    )


def nx_resolve_geometry(selector: dict[str, Any], tie_tolerance: float = 0.001):
    """Re-evaluate a selector returned by nx_find_geometry in its original owner part. Returns a fresh face/edge ID only if the best match is unique within tie_tolerance in part units. No match, ambiguous rank or stale owner is an explicit error. Survives edits/reopen by geometric rule, not a promise of persistent topological identity. Whole-part rules may select new geometry that now satisfies the rule."""


def nx_recognize_holes(owner: str | None = None, offset: int = 0, limit: int = 50):
    """Recognize inward cylindrical BREP faces and return bore radius, axis, angular coverage and coaxial groups in work-part coordinates/units. Partial cylindrical faces are identified explicitly. Does not infer threads, manufacturing features, blind/through termination or fit classes."""


def nx_native_component_pattern(component: str, direction: list[float], spacing: float, count: int):
    """Create a native associative linear component pattern with 2–100 total occurrences including one immediate unsuppressed seed. Direction is normalized in work-part coordinates; spacing is positive part units. Native builder and pattern members are read back. Rollback on update/count failure. Existing nx_pattern_components retains independent-instance behavior."""


def nx_edit_component_pattern(
    pattern: str,
    spacing: float | None = None,
    count: int | None = None,
    count_y: int | None = None,
    spacing_y: float | None = None,
    angle: float | None = None,
):
    """Edit an associative single-seed rectangular or circular component pattern. Rectangular supports count/spacing and existing second-direction count_y/spacing_y. Circular supports count/angular pitch in degrees. Counts include seed and total instances must be <=100. Return native expressions and all actual placements; invalid edits roll back."""


def nx_list_component_patterns():
    """Enumerate native work-assembly component patterns, IDs, native type, association, count/pitch expressions for linear patterns and member occurrence poses. Independent instances are not patterns."""


def nx_sketch_dimension(
    sketch_id: str,
    curve: str,
    dimension_type: Literal["length", "horizontal", "vertical", "radius", "diameter"],
    value: float,
    origin: list[float],
    reference: bool = False,
):
    """Create a native sketch dimension: line endpoint length/horizontal/vertical distance or arc radius/diameter. Value is positive part units; annotation origin is local [x,y]. Driving dimensions use value, reference dimensions measure existing geometry and require value matching it within 0.001 part units. Returns the associated expression for later formula editing. Atomic and restores activation; conflicting solver state rolls back."""


def nx_sketch_relation(
    sketch_id: str,
    curve1: str,
    curve2: str,
    relation: Literal[
        "parallel", "perpendicular", "equal_length", "equal_radius", "concentric", "coincident"
    ],
    point1: Literal["start", "end", "center"] | None = None,
    point2: Literal["start", "end", "center"] | None = None,
):
    """Create a persistent two-curve sketch relation using the installed solver; modern sketches keep curve1 stationary and move curve2 as needed. Geometric residual is checked before success. Parallel/perpendicular/equal_length require lines; equal_radius/concentric require arcs; coincident requires explicit start/end (line) or center (arc) for each curve. Other relations reject point arguments. Ownership and types checked before mutation. Conflicting solver results roll back; no constraints are automatically removed."""


def nx_sketch_conflicts(sketch_id: str, max_checks: int = 20):
    """Diagnose an over/inconsistently constrained sketch by temporary single-constraint removal and native solver reevaluation, restoring each trial. max_checks 1–50 bounds serial NX work. Returns constraints whose removal relieves the conflict, statuses, checked/total and completeness. Also checks contradictory horizontal/vertical persistent relations on a nonzero line because NX solver status can omit them. This is not a minimal conflicting set; multiple independent conflicts may produce no single-removal relief. No geometry or constraints retained from trials."""


def nx_feature_parameters(feature: str):
    """List native expressions owned by a feature, with IDs, formulas, units, editability and dependencies. Parameter names are NX expression names, not inferred semantic labels. Applicable to feature types exposing GetExpressions."""


def nx_set_feature_parameters(feature: str, values: dict[str, str]):
    """Atomically set 1–25 owned, editable local Number expressions on a feature. Keys are expression IDs or exact names from nx_feature_parameters; values are NX formulas in existing expression units. Preflight ownership/editability; native update failures roll back all changes. Can bind to another expression by its name. Does not alter unexposed builder options or locked/interpart expressions."""


def nx_extrude(
    sketch_id: str,
    distance: float | None = None,
    reverse: bool = False,
    start: float = 0.0,
    end_type: Literal["distance", "through_all", "up_to_face"] = "distance",
    symmetric: bool = False,
    direction: list[float] | None = None,
    target_face: str | None = None,
    boolean: Literal["none", "unite", "subtract", "intersect"] = "none",
    targets: list[str] | None = None,
):
    """Extrude an owned sketch along its normal or a work-part direction. Lengths use part units. distance is the end coordinate from the sketch plane; start is the start coordinate. Symmetric uses +/- distance/2 and requires start=0. Omit distance for through_all/up_to_face; up_to_face requires target_face. Boolean operations require explicit owned target bodies; through_all requires a boolean. Returns every result body. reverse flips the chosen direction."""


def nx_shell(
    body: str, thickness: float, remove_faces: list[str] | None = None, outward: bool = False
):
    """Create a native shell in an owned solid body. Positive thickness uses part units; outward reverses the thickness side. remove_faces must belong to that body; omit for a closed hollow body. Native failures roll back."""


def nx_loft(sketches: list[str], solid: bool = True):
    """Create a native through-curves loft through 2–20 ordered owned sketches. Solid output requires compatible closed profiles; solid=false creates a sheet. Sketch order controls loft direction. Returns all bodies; native errors roll back."""


def nx_sketch_primitive(
    sketch_id: str,
    primitive: Literal["circle", "slot", "rounded_rectangle"],
    center: list[float],
    width: float | None = None,
    height: float | None = None,
    radius: float | None = None,
):
    """Add editable native sketch curves atomically in sketch-local coordinates. Circle needs radius only. Horizontal slot needs width>height and has end radius=height/2. Rounded rectangle needs width,height and corner radius less than half the shorter side. center is [x,y]; all lengths use part units. Returns curve IDs and solver diagnostics; no implicit constraints."""


def nx_copy_project(path: str, prefix: str, activate: bool = False):
    """Clone the saved work assembly and all loaded prototypes into a new workspace directory using native NX cloning. Preserve relative subfolders; prepend a required simple prefix to each part basename to avoid NX loaded-name conflicts. Verify rewritten dependencies and source hashes; write a manifest. Source files remain intact. activate selects the copied assembly; otherwise restore the original session. Reject existing destinations, unsaved sources and unloaded dependencies."""


def nx_mass_properties(
    body: str | None = None, scope: Literal["auto", "part", "assembly"] = "auto"
):
    """Measure native solid mass, volume, area, center of gravity and centroidal inertia using assigned densities. Outputs kg, m and kg*m^2 in the work-part WCS, including that WCS's origin/basis and native error estimates. Assembly scope includes loaded unsuppressed occurrence geometry. Overlaps are summed, not geometrically united; review assigned densities."""


READ_ONLY.add("nx_mass_properties")


def nx_draft(faces: list[str], stationary_face: str, direction: list[float], angle: float):
    """Create native face draft about a stationary face on the same owned body. direction is in work-part coordinates; angle is signed degrees with magnitude <89. Selected faces must exclude the stationary face. Returns all result bodies; native errors roll back."""


def nx_transform_bodies(
    translation: list[float],
    rotation_matrix: list[list[float]],
    bodies: list[str] | None = None,
    copy: bool = False,
    feature: str | None = None,
):
    """Create or edit an associative in-part move/copy feature. Transform is p_out=R*p_input+translation, in work-part units with a right-handed orthonormal row-major R. New features require owned bodies; copy=true preserves originals. Pass the returned feature ID, omitting bodies/copy, to replace its absolute transform without accumulating motion. operation_id deduplicates retries. No assembly occurrence moves."""


def nx_component_array(
    component: str,
    pattern_type: Literal["rectangular", "circular"],
    count: int,
    spacing: float | None = None,
    direction: list[float] | None = None,
    count_y: int = 1,
    spacing_y: float | None = None,
    direction_y: list[float] | None = None,
    center: list[float] | None = None,
    axis: list[float] | None = None,
    angle: float | None = None,
):
    """Create an associative native component array with 2–100 total instances including seed. Rectangular uses direction/spacing and optional count_y/direction_y/spacing_y. Circular uses center/axis and positive angular pitch in degrees; positions must not wrap to duplicate the seed. Coordinates/lengths use work-part frame/units. Seed must be an unsuppressed immediate child. Parameters for the other pattern type are rejected."""


def nx_set_material(bodies: list[str], name: str, density: float):
    """Create a named local isotropic physical material with density in kg/m^3 and assign it to owned solid bodies. Reject an existing material name to avoid implicit edits to other assignments. Verify native body density. Defines density only; does not invent elastic, thermal or appearance properties."""


def nx_material_info(body: str | None = None, scope: Literal["auto", "part", "assembly"] = "auto"):
    """Read native physical material names and body densities in kg/m^3, including occurrence prototypes. Does not infer density from color or labels."""


def nx_sketch_angle(sketch_id: str, line1: str, line2: str, value: float, origin: list[float]):
    """Create a driving angular dimension between two owned sketch lines. value is 0–180 degrees exclusive; origin is the annotation position in local [x,y]. Returns its editable expression and solver diagnostics."""


def nx_sketch_tangent(sketch_id: str, curve1: str, curve2: str):
    """Create a persistent native tangent relation for line/arc or arc/arc pairs. First curve is stationary. Verify geometric tangency and solver diagnostics; native failures roll back."""


def nx_sketch_symmetry(sketch_id: str, curve1: str, curve2: str, centerline: str):
    """Create native sketch symmetry between two owned lines about a distinct owned straight centerline. Restore prior activation and return solver diagnostics; no implicit constraint deletion."""


def nx_sketch_trim_extend(
    sketch_id: str,
    curve: str,
    boundaries: list[str],
    pick: list[float],
    action: Literal["trim", "extend"],
):
    """Trim or extend an owned sketch curve against explicit owned boundary curves. pick=[x,y] in sketch-local coordinates identifies the segment to remove or the end to extend. Does not extend boundaries. Atomic native edit; reacquire curve references from the returned sketch after topology changes."""


READ_ONLY.add("nx_material_info")


def nx_render_view(
    path: str | None = None,
    width: int = 1600,
    height: int = 1000,
    background: Literal["white", "original", "transparent", "color"] = "white",
    color: list[float] | None = None,
    style: Literal["studio", "shaded", "shaded_with_edges"] = "studio",
    lighting: int | None = None,
):
    """Render the current interactive NX camera to a new PNG using native Studio image capture. Exact width/height in pixels (128–4096), optional native lighting preset 1–5, and original/white/transparent/custom RGB [0,1] background. Restore temporary view style and lighting afterward. Return camera, checksum, artifact path and inline image. Uses existing model appearance; does not invent physical materials. Requires installed native rendering capability."""


def nx_list_assembly_constraints():
    """Inspect native constraints defined in the work assembly: typed IDs, component/geometry references, alignment, suppression, expressions and native solver status. Does not infer that unconstrained components are fixed."""


def nx_assembly_constraint(
    constraint_type: Literal[
        "fix", "touch", "distance", "parallel", "perpendicular", "angle", "concentric"
    ],
    component: str,
    geometry: str | None = None,
    target_component: str | None = None,
    target_geometry: str | None = None,
    value: float | None = None,
    alignment: Literal["infer", "same", "opposite"] = "infer",
):
    """Create a persistent native assembly constraint between unsuppressed immediate child components. Fix takes only component. Other types require geometry and target_geometry occurrence face/edge IDs owned by the respective components. Distance uses part units, angle degrees. Native solver must report Solved or operation rolls back. Can move components while solving; reacquire poses afterward."""


def nx_edit_assembly_constraint(
    constraint: str,
    value: float | None = None,
    suppressed: bool | None = None,
    alignment: Literal["infer", "same", "opposite"] | None = None,
):
    """Edit an existing typed assembly constraint's distance/angle, suppression, or alignment. Solve natively and roll back unsatisfied edits. Values are absolute, in part length units or degrees. Component positions may change during solving."""


READ_ONLY.add("nx_list_assembly_constraints")


def nx_blend(edges: list[str], radius: float):
    """Create an associative native edge blend on explicitly selected owned edge IDs from one body. Positive radius uses part units. Return all result bodies and roll back invalid blends."""


def nx_chamfer(edges: list[str], offset: float):
    """Create an associative symmetric-offset native chamfer on owned edge IDs from one body. Positive offset uses part units. Reacquire topology references after editing."""


def nx_hole(
    diameter: float,
    depth: float,
    x: float,
    y: float,
    z: float,
    body: str | None = None,
    direction: list[float] | None = None,
):
    """Cut a simple cylindrical hole from [x,y,z] along direction (default +Z) for positive depth. Part units and work-part coordinates. Uses native cylinder subtraction; no drill tip, thread or counterbore. Require explicit body in a multi-body part. Native failures roll back."""


def nx_sweep(
    section: str,
    guide: str,
    boolean: Literal["none", "unite", "subtract", "intersect"] = "none",
    targets: list[str] | None = None,
):
    """Create an associative native sweep from an owned section sketch along a distinct owned guide sketch. Closed compatible sections produce solids. Optional boolean requires exactly one explicit owned target body; none forbids targets. Return all output bodies; native failures roll back."""


def nx_mate_component(
    component: str,
    mate_type: Literal["touch", "align", "orient", "center", "align_angle"],
    references: list[str] | None = None,
    offset: float = 0.0,
):
    """Create a native assembly mate using two occurrence face/edge references [moving,target]. Touch uses opposite alignment; align uses same alignment; nonzero offset creates a distance. Orient is parallel, center is concentric, align_angle uses offset in degrees. Other lengths use work-part units. Prefer nx_assembly_constraint for explicit constraint semantics."""


def nx_mirror_body(body: str, plane: Literal["XY", "XZ", "YZ"]):
    """Create a native mirrored copy of an owned body about a principal plane through the work-part origin. Preserve source body; create a datum plane and editable mirror feature. Return every result body."""


def nx_create_drawing(
    name: str = "Sheet1",
    size: Literal["A0", "A1", "A2", "A3", "A4"] = "A3",
    scale: float = 1.0,
    units: Literal["mm", "in"] = "mm",
):
    """Create and open a native landscape A0–A4 sheet with independent mm/in sheet units, first-angle projection and positive scale. Sheet sizes retain their physical dimensions; coordinates use the selected sheet units, independent of model units."""


def nx_add_base_view(
    drawing: str,
    body: str | None = None,
    view: Literal["top", "front", "back", "right", "left", "bottom", "isometric"] = "isometric",
    position: list[float] | None = None,
    scope: Literal["body", "assembly"] = "body",
    explosion: str | None = None,
):
    """Add a native base view to a drawing sheet. scope=body requires body and a single-body part. scope=assembly requires no body, uses current component reference sets/suppression, and optionally associates a typed explosion from the same work part. Omitted explosion explicitly uses assembled positions. position=[x,y] uses sheet units, default [100,100]. Return typed view reference; open the target sheet."""


def nx_export_drawing_pdf(path: str):
    """Export all work-part drawing sheets to a new workspace PDF using native NX plotting. Temporarily display sheets to refresh their presentation, then restore the prior work/display part and drawing/modeling view. Full sheet scale, metric dimensions and searchable text. Returns actual path, sheet names/count, size and checksum. Reject missing drawings and existing files."""


def nx_add_projection_view(
    base_view: str, direction: Literal["right", "left", "top", "bottom"], spacing: float = 60.0
):
    """Create a native associative projected view on the currently open sheet. Direction describes sheet placement relative to the parent; projection follows the sheet convention. Positive spacing uses sheet units. Returns a typed drawing-view reference."""


def nx_add_dimension(
    view: str,
    object1: str,
    object2: str | None = None,
    dim_type: Literal["aligned", "horizontal", "vertical"] = "aligned",
    origin: list[float] | None = None,
    dimension: str | None = None,
):
    """Create a native associative linear drawing dimension from owned or work-assembly occurrence edge IDs. One edge measures start-to-end; two edges measure their start vertices. Types are aligned/horizontal/vertical in the drawing view. origin=[x,y] uses sheet units, default [100,80]. Pass dimension to rebind an existing linear dimension explicitly, retaining its identity; origin/default and dim_type apply to edits too. Returns actual computed size in model units and a typed dimension ID."""


def nx_create_explosion(name: str):
    """Create a named native explosion in the work/display assembly, initially at assembled positions. Names are unique case-insensitively, nonempty and <=132 characters. Return a typed explosion ID. Requires assemblies license. Does not reposition actual components or show the explosion automatically."""


def nx_list_explosions():
    """List native explosions owned by the work part, typed IDs, names and referencing model/drawing views. No model or view changes."""


def nx_explosion_info(explosion: str, offset: int = 0, limit: int = 50):
    """Inspect an explosion by typed ID. Paginate occurrences (limit 1–200), actual exploded and assembled positions, rotations, paths and suppression. Positions are absolute work-assembly coordinates in part units; matrices are right-handed row-major. Ordinary body/clearance/mass tools still measure assembled geometry."""


def nx_edit_explosion(
    explosion: str,
    placements: list[dict[str, Any]] | None = None,
    reset_components: list[str] | None = None,
):
    """Atomically replace absolute exploded poses or reset selected components to inherited parent-explosion positions. Provide 1–1000 unique occurrences across placements and reset_components. Each placement has component ID, translation=[x,y,z] in assembly coordinates/part units, and optional right-handed orthonormal row-major rotation_matrix; omitted rotation retains the current exploded world orientation at request start. Parents apply before children; descendants inherit parent changes. Resolves and validates every item before mutation, verifies final native poses and unchanged actual placements, and updates associated drawing views. Safe repeated absolute placement; use operation_id for transport retries."""


def nx_show_explosion(
    explosion: str | None = None, drawing_view: str | None = None, model_view: str | None = None
):
    """Display a native explosion, or assembled positions when explosion is null. Without drawing_view, return to 3D modeling and fit the work view. With an owned drawing-view ID, set that view's explosion association and update it. model_view alternatively targets an existing saved model-view ID without switching the visible view. The two targets are mutually exclusive. Work and display parts must match; finish active sketches first. Changes view presentation, never actual component placement."""


def nx_delete_explosion(explosion: str):
    """Delete a native explosion only when no model/drawing views reference it. Detach dependent views with nx_show_explosion(explosion=null) first. Native deletion is transactional; returned reference becomes stale. Actual assembly components are retained."""


EXPLOSION_PLACEMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["component", "translation"],
    "properties": {
        "component": {
            "type": "string",
            "description": "Typed occurrence reference in this explosion.",
        },
        "translation": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
        "rotation_matrix": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
        },
    },
}
