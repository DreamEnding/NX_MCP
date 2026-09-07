# Integration tool contracts

Use on-demand schemas for exact argument types and the [capability matrix](capability-matrix.md) for tested scope. The integration profile remains opt-in. This reference consolidates modeling, file and display semantics.

## Project folders and explicit file paths

### Example workflow

```json
{"tool":"nx_workspace_info","arguments":{}}
{"tool":"nx_create_directory","arguments":{"path":"projects/controller/parts"}}
{"tool":"nx_create_part","arguments":{"path":"projects/controller/parts/controller_base.prt","units":"mm"}}
{"tool":"nx_save_part","arguments":{}}
{"tool":"nx_save_as","arguments":{"path":"projects/controller/revisions/controller_base_r02.prt"}}
{"tool":"nx_open_part","arguments":{"path":"D:/CAD/NX_MCP_WORKSPACE/projects/controller/parts/controller_base.prt","work":true,"display":true}}
{"tool":"nx_workspace_list","arguments":{"path":"projects/controller"}}
```

`nx_create_directory` creates missing parents and succeeds when the directory already exists. Part creation, Save As, and upload also create missing parent directories. Save As rejects existing files and changes the active part's filename. `nx_save_part` saves at the part's current filename; it takes no destination path. Opening an already-loaded file reuses it and supports explicit work/display activation.

Use subfolders such as `parts/`, `assemblies/`, `revisions/`, `vendor/`, and `exports/`. Give simultaneously loaded parts unique basenames: native NX can reject two different files named `base.prt`, even in different directories. Save As does not relocate an assembly's referenced prototypes. Use `nx_package_assembly` for a package with dependencies; moving a whole existing project requires updating references separately.

Absolute paths outside the workspace, traversal escapes, symlink escapes, and internal `.nx-mcp` state are rejected. To use another root, configure `NX_MCP_WORKSPACE` consistently for both bridge and sidecar and restart them after preserving the session. Local Mac files require `nx_upload_file` or an existing shared folder; a Mac path is not a Windows path.

This changes path support only; it does not reorganize existing CAD files.


## NX MCP visualization tools — 5 September 2026

### Examples

```json
{"tool":"nx_highlight_collisions","arguments":{"obj1":"DIRECT_CUBE","obj2":"ROTATED_SUB"}}
{"tool":"nx_set_display","arguments":{"objects":["ROTATED_SUB"],"color":"green","transparency":30}}
{"tool":"nx_set_visibility","arguments":{"objects":["ROTATED_SUB"],"mode":"isolate"}}
{"tool":"nx_section_view","arguments":{"origin":[0,0,5],"normal":[0,0,1],"cap":true}}
{"tool":"nx_sketch_diagnostics","arguments":{"sketch_id":"<returned sketch ID>"}}
```

Use returned opaque IDs where possible. Capture any result with `nx_screenshot`; native viewport PNGs arrive inline through MCP and as checksum-addressed workspace artifacts.


### Semantics and limits

- Origins use display-part units and coordinates. Normals are normalized. **NX v2606 retains `dot(point-origin, normal) <= 0`.** The red lower / blue upper fixture verifies both normal directions in actual viewport images. No internal feature-toggle API is required.
- Work and display parts must match for visual tools. Edit an existing section by ID; an active section is not silently replaced. Sections clip the view and do not create sliced solids or drawing views.
- Restore appearance/visibility snapshots in reverse order. All references are preflighted before restoration. Manual handoff, rollback or part closure can invalidate snapshots. Restoration restores explicit values, not inherited occurrence-override state or the part's modified flag. Display changes may persist when saved.
- Visibility responses report explicit NX blank flags. Parent component, suppression, layer and reference-set state can further affect visible geometry. Isolation covers loaded bodies/components and their ancestors; datum/reference-curve visibility is outside its scope.
- Sketch diagnostics temporarily activate the target and evaluate the entire sketch, then restore native editing/work-region state through an invisible undo mark. Another active sketch is rejected. Persistent constraint counts exclude inferred solver relations; no minimal conflict set is invented. Over/inconsistent status is passed through when NX returns it; those solver states were not separately forced in this release's fixtures.
- Material assignment and photorealistic rendering remain future work. Untested legacy modeling, mates and constraint-authoring tools retain their prior experimental status.


### Verification

The release includes native-probe, public-MCP and local-test evidence separately. Native tests cover active/inactive and fully fixed sketches, appearance/visibility restoration, and section lifecycle. Public tests cover nested collision highlights, instance appearance, isolation restoration, native renders, saved-state preservation, and manual handoff. The original controller session is preserved separately from disposable fixtures.

## Authoring and review tools (NX v2606)

### Select geometry and parameters

`nx_find_geometry` enumerates faces or edges within a body, feature, component or full assembly. Filter planar faces by oriented normal, cylinders and circular edges by radius, and order by exact BREP nearest distance (dev6) or highest/lowest conservative bounding-box center. Coordinates and radii use work-part units. Nearest results include the closest point and native accuracy. Candidate results are paginated; use `nx_highlight_objects` to inspect choices, then `nx_clear_highlights`.

`nx_list_expressions` returns formulas, numeric values, units, editability and stored immediate dependencies. Conditional formulas can have incomplete stored dependencies. `nx_set_expression` creates named Number expressions with mm/inch/degree/radian/unitless units or edits existing local, unlocked Number expressions. Edits preserve units. NX formula errors and failed updates roll back. `nx_bind_parameter` connects an existing expression to EXTRUDE start/end or PATTERN_FEATURE count/spacing. Units and dimensional compatibility are enforced by NX. This is not a general interface to every feature builder.


### Health and local editing

`nx_model_health` reports native feature errors/warnings, suppression, unavailable prototypes and UF body-consistency faults. Assembly scope checks unique loaded unsuppressed prototypes. A sheet body is informational rather than automatically invalid. `healthy` only describes the listed checks; no design-intent, manufacturing, solver-conflict or unloaded-file certification is implied. `nx_rebuild_model` runs native DoUpdate for pending updates; it does not force every current feature to regenerate.

`nx_edit_sketch` reopens an existing sketch, preflights ownership and operation structure, and applies up to 100 edits under one rollback mark. It preserves the prior active/inactive state and returns whole-sketch diagnostics. It supports line endpoints, arc center/radius/angles, adding lines, deleting owned curves/constraints, and adding fixed/horizontal/vertical constraints. Points are local `[x,y]`; arc angles are degrees. Another active sketch is rejected. Constraints are never silently removed to permit an edit. Edit a dimensional constraint through the associated expression reported by diagnostics; more constraint types remain future work.

`nx_component_action` renames, suppresses, unsuppresses, removes or replaces an immediate child occurrence. Activate a nested component's owning assembly before editing it. Replacement targets one occurrence, requests relationship retention and checks placement afterward; native errors roll back. Suppression affects all arrangements. Removing an occurrence does not delete its prototype file. `nx_pattern_components` creates 2–100 total independent occurrences including the seed, with explicit direction and pitch. These are ordinary instances, not a native associative component pattern.


### Presentation and inspection artifacts

`nx_set_camera` sets absolute camera rotation, view-space origin and scale. Rotation is a row-major orthonormal 3×3 matrix whose columns are NX view axes. Work and display parts must match.

`nx_save_presentation` writes a new workspace JSON containing camera, active single-plane section, loaded geometry visibility, and explicit colors/transparency including per-face overrides. `nx_restore_presentation` resolves all saved journal locators before mutation. The owner part must match. Missing geometry rejects restoration; across revisions, verify journal identifiers still refer to intended entities. Datum visibility, materials and inherited-override semantics are outside this format. Display restoration may mark a part modified; it does not save the part.

`nx_inspection_report` produces a workspace ZIP with HTML, structured JSON, SHA-256 manifest, overview screenshot, up to eight flagged-pair close-ups, and up to six section screenshots. Pair results retain native distance/contact/interference distinctions. `max_pairs` is an explicit work limit; exceeding it errors instead of implying unchecked pairs are clear. Temporary camera, visibility and section state is restored. Retrieve the ZIP through `nx_download_file`. Captures are native viewport PNGs, not photorealistic rendering. Files are never silently overwritten.


### LLM review workflow

`nx_model_summary` provides overview counts/bounds/health plus paginated component, feature, expression and sketch sections. It separates owned-body counts from assembly occurrences and does not invent design dimensions.

`nx_preview_change` accepts up to 25 supported expression, parameter, extrusion/pattern edit, sketch edit or placement operations. It applies them temporarily under an invisible checkpoint, captures before/after parameters, volume, bounds, health and optional viewport image, then **rolls back before returning**. Returned geometry references are stale after rollback. The preview token stores a session-scoped plan, not a persistent undo mark.

`nx_finish_preview(action="accept")` re-resolves stored target locators and atomically reapplies the plan only while its owner part and mutation epoch are unchanged. Intervening mutations, failed mutations, saves, lifecycle changes or manual handoff expire acceptance. `action="discard"` removes the plan; the original geometry was already restored. Supply a stable operation ID to avoid applying an accepted plan twice. Accepted edits remain unsaved and undoable. Saves, imports, exports and other filesystem/session operations cannot be included in a preview plan.


## Advanced authoring on NX 2606

### Geometry selection

`nx_find_geometry` now ranks `nearest` by native `UF.Modeling.AskMinimumDist3` against the trimmed face or edge. Results include distance, closest point and native accuracy; all use work-part coordinates and units. Highest/lowest still rank conservative bounding-box centers. This changes nearest ordering relative to dev5; clients must use `distance`, not `distance_to_bounds_center`, for clearance decisions.

Each query returns a versioned `selector`. Pass that complete object to `nx_resolve_geometry` after a model edit or reopening its original part. The tool re-evaluates the rule and returns a fresh reference only when the best rank is unique within the requested tie tolerance. Owner journals must still resolve. A selector represents a geometric rule, such as “highest upward planar face,” rather than permanent topological identity. If topology changes, a different face can satisfy that rule. Ties and missing owners are explicit errors; refine the query rather than selecting an arbitrary candidate.

`nx_recognize_holes` reports inward cylindrical faces, radius, axis, angular coverage and coaxial groups. Full circumferences and partial faces are distinguished. Coaxial grouping uses 0.001 part-unit radial tolerance and axis dot-product tolerance of 1e-8. These are BREP bore candidates, not inferred manufacturing features: through/blind termination, threads, fits and compound-hole classification are outside this tool.


### Associative assembly patterns

Create a native linear pattern with:

```json
{"component":"<seed occurrence ID>","direction":[1,0,0],"spacing":16.5,"count":16}
```

Send this to `nx_native_component_pattern`. Count includes the seed and is limited to 2–100. The seed must be an unsuppressed immediate child of the work assembly. The native `NXOpen.Assemblies.ComponentPattern` remains editable and survives save/reopen. `nx_edit_component_pattern` changes pitch and/or count; `nx_list_component_patterns` returns native association, parameter expressions and member poses. A 14 mm wide seed with 16 total instances at 16.5 mm pitch spans 261.5 mm.

The installed Python collection requires `GetAllComponentPatterns()`; iterating it raises an NX argument error. The implementation checks the installed API and never substitutes independent occurrences for failed native pattern creation.


### Dimensions and relations

`nx_sketch_dimension` creates line length, horizontal or vertical endpoint distances, or arc radius/diameter dimensions. Values use part units; annotation origin is local `[x,y]`. Reference dimensions measure current geometry and reject a supplied value that differs from the measured value by more than 0.001 part units. Driving dimensions return an editable expression ID; use `nx_set_expression` to change its formula later.

`nx_sketch_relation` creates parallel, perpendicular, equal-length, equal-radius, concentric or coincident persistent relations. Coincident relations require explicit line start/end or arc center choices. Modern sketches use native Make Relation builders with curve1 stationary; curve2 and any connected geometry move through the native solver. A geometric residual check rejects unsatisfied results. Curves must belong to the named sketch. Operations restore its prior activation state and reject another active sketch. They never remove constraints implicitly.

`nx_feature_parameters` exposes the expressions NX associates with a feature. `nx_set_feature_parameters` atomically changes 1–25 owned, editable local Number expressions by ID or exact expression name. It validates every target before editing and retains each expression's units. This broadens editing without guessing semantic labels or builder options. Native acceptance covers extrusion expressions; availability on another feature type is determined by its exposed expressions and editability, not by a blanket correctness claim about that feature.


### Conflict diagnostics

`nx_sketch_conflicts` combines native solver status with bounded single-constraint-removal trials. Each trial and the surrounding activation/work-region changes are restored with NX undo marks. It reports checked/total, trial errors, completeness and constraints whose individual removal relieves the detected conflict. This is a sensitivity check, not a minimal unsatisfiable constraint set; several independent conflicts can yield no single-removal relief.

NX 2606 can report `UnderConstrained` for a nonzero line with both persistent horizontal and vertical relations. The tool reports that directly provable contradiction separately as `explicit_conflict_pairs`. This additional rule covers that pair only; native status and an empty pair list do not prove every legacy relation is consistent. Cleanup failure is an explicit partial mutation outcome.


## Engineering tools on NX v2606

### Solid modeling

- `nx_extrude` adds start offsets, symmetric total length, arbitrary work-part direction, through-all and up-to-face ends. A Boolean requires explicit target bodies. `distance` is the end coordinate along the extrusion direction; with `symmetric=true` it is the total length. Unsupported combinations are rejected.
- `nx_shell` uses positive wall thickness and optional removed face IDs. Its default thickness goes inward; `outward=true` reverses it. Native testing caught and corrected the NX flip convention.
- `nx_loft` joins ordered sketch profiles. Solid and sheet options are explicit; the native acceptance fixture covers a solid square-to-square loft.
- `nx_draft` requires faces, a stationary face on the same body, a direction and signed angle in degrees. Explicit native tolerances avoid zero-tolerance failures.
- `nx_transform_bodies` creates or edits an associative move. The mapping is `p_output = R * p_input + translation`; `R` is right-handed, orthonormal and row-major. Editing the returned feature replaces the mapping. Copying uses associative body extraction before the MoveObject feature, because the installed builder's CopyOriginal mode produced a non-associative BREP.
- `nx_blend` and `nx_chamfer` accept typed owned edge IDs from one body. They use native chain/collector APIs. Topology references must be reacquired afterward.
- `nx_hole` makes a cylindrical subtract along an explicit direction, default +Z. This preserves the earlier simple-cut semantics; it is not a threaded or drill-tip HolePackage feature. Specify a body when the part has multiple bodies.
- `nx_sweep` accepts distinct section and guide sketch IDs. Optional Boolean output requires one explicit target body.
- `nx_mirror_body` preserves the source and creates a native mirror feature about an origin XY/XZ/YZ datum plane.

Feature results include all output bodies and result count. Invalid operations roll back; inspect the operation receipt after uncertain transport failure before retrying.


### Sketches

`nx_sketch_primitive` adds editable circles, horizontal slots or rounded rectangles in sketch-local coordinates. `nx_sketch_trim_extend` uses explicit boundary curves and a local pick point. `nx_sketch_angle` creates a driving angular dimension. `nx_sketch_tangent` and `nx_sketch_symmetry` use the modern NX solver builders and verify actual geometric residuals and persistent constraints. Symmetry currently accepts two lines about a third line. Native tests cover line/circle tangency and line-pair symmetry; other curve combinations have narrower validation.


### Assemblies and materials

`nx_component_array` creates native associative rectangular or circular patterns. Counts include the seed, with at most 100 total instances. Rectangular arrays can use two directions. Circular pitch is degrees and cannot wrap to duplicate the seed. `nx_edit_component_pattern` edits count/pitch and existing second-direction parameters; read-back includes native expressions and actual placements.

`nx_assembly_constraint`, `nx_edit_assembly_constraint` and `nx_list_assembly_constraints` expose persistent constraints with typed references, expressions, suppression and native solver status. Creation uses immediate unsuppressed child occurrences and their occurrence faces/edges. Solving can move components. Native acceptance checks actual face separation after editing, because a solved status plus an updated expression initially left a stale pose until the network was rebuilt after the edit. `nx_mate_component` now maps its earlier mate names onto these native operations; touch/offset mating is checked geometrically.

`nx_set_material` assigns a named local density-only physical material in kg/m³. It does not invent elastic, thermal or appearance properties. `nx_material_info` reads native assignments. `nx_mass_properties` returns summed solid mass, area, volume, center of gravity and centroidal inertia in SI, with explicit work-part WCS origin/basis. Overlaps are counted separately. The native fixtures include translated and rotated nested assemblies.

`nx_copy_project` clones a saved loaded assembly into a new workspace directory, preserves relative prototype subfolders and rewrites native dependencies. A required basename prefix avoids loaded-part name conflicts. Source hashes and copied dependencies are checked, and a manifest is written. It copies rather than deletes source files; unsaved parts, existing destinations and unloaded dependencies are rejected.


### Rendering and drafting

`nx_render_view` uses native Studio image capture for exact 128–4096 pixel dimensions. It supports original, white, transparent or custom RGB backgrounds, native lighting presets 1–5, and studio/shaded/edge styles. It restores temporary style and lighting settings and returns camera metadata, checksum, artifact path and an inline MCP image. This is NX rendering, not an AI reconstruction. The native fixtures cover preset 2 and exact 800×600 and 640×480 output; full-VM capture is a separate troubleshooting activity.

Drawing creation uses native metric A0–A4 landscape sheets with first-angle projection. `nx_add_base_view` currently requires a single-body part and accepts sheet placement in mm. `nx_add_projection_view` creates an associative projected view. `nx_add_dimension` supports aligned/horizontal/vertical linear dimensions: one edge measures its endpoints; two edges measure their start vertices. `nx_export_drawing_pdf` exports all sheets at full sheet scale with text and a checksum. Native acceptance produced an A3 PDF with two views and a computed 10 mm dimension, then parsed and visually reviewed it.

These are authoring tools, not a complete manufacturing drawing package: title blocks, GD&T, arbitrary detail/section drawings, radial dimensions and automatic annotation layout are not supplied by this release.


## Native exploded views (NX v2606)

### Placement and recovery

Each placement contains `component`, `translation: [x,y,z]`, and optionally
`rotation_matrix: [[...],[...],[...]]`. Translation is in the work assembly's
units and coordinates. Rotation is a right-handed orthonormal row-major matrix:
`p_assembly = R * p_component + translation`. Omitted rotation retains the
current exploded world orientation at request start.

Up to 1000 unique occurrences can be placed/reset in one request. Inputs are
validated before mutation. Parents are processed before children, independently
of input order. Moving a parent carries its descendants; resetting a child
removes its local explosion offset and retains the parent's exploded placement.
Specifying both parent and child absolute placements gives each its requested
world pose. NX stores local post-transforms; the bridge converts and verifies
the actual native result before committing.

Use a stable `operation_id` when retrying after a transport failure and query
`nx_operation_status`. Absolute placement is also repeatable under a new ID.
Checkpoint and rollback use the existing recovery system. Drawing views that
reference an edited explosion are updated before the edit commits.


### 3D and drawings

With no view argument, `nx_show_explosion` returns to modeling, assigns the
explosion to the work view and fits it. Explicit model-view assignment updates
that saved view without activating it. Explicit drawing-view assignment updates
the drawing view. `nx_explosion_info.views` provides both kinds of typed IDs.

`nx_add_base_view(drawing, scope="assembly", explosion=...)` creates an exploded
assembly view. Omit `explosion` for an assembled view. Body scope remains the
default for compatibility. Projected views retain their parent's explosion.
Native screenshot/render and PDF export tools work with these views; check
returned dimensions, warnings and artifact checksums. Datum/reference geometry
visibility affects output and should be configured for the intended drawing.

When saving drawing parts, the bridge temporarily displays a drawing sheet to
preserve NX CGM preview data without a modal Save CGM prompt, then restores the
previous part/presentation. It does not disable global CGM preferences.

Ordinary bounds, mass, clearance and collision queries still measure assembled
geometry. This release does not add exploded-state collision queries, automatic
explode layouts, trace lines, animation, BOMs or balloons.


### Verification

`examples/validate_exploded_views.py` exercises the public MCP surface against
native NX, using an isolated nested assembly and restoring the original saved
session. It covers rotated parents, child absolute placement and reset, safe
retry, 3D capture, assembled/exploded/projected drawing views, update propagation,
PDF transfer, Save As/reopen, rollback, deletion guards and stale references.
Local tests separately cover partial native failure and strict input validation;
mocked failure injection is not evidence of native transport interruption.

## Native sheet metal

### Workflow

1. Create or activate a work/display part with the existing path-aware part tools.
2. Finish any active sketch, then call `nx_sheet_metal_context`. This selects the
   modern `UG_APP_SBSM` application. It does not enter the retired sheet-metal app.
3. Inspect/set stock defaults with `nx_sheet_metal_defaults` and
   `nx_set_sheet_metal_defaults`. Creation defaults do not override existing features.
4. Call `nx_sheet_metal_schema(operation)` before creating/editing a feature. It
   returns strict schemas, actual enums, native validation scope and example inputs.
5. Call `nx_sheet_metal_feature(operation, parameters, feature?)`. References must
   belong to the work part. A feature ID requests an edit. Unknown fields are errors.
6. Inspect actual thickness and bend data with `nx_sheet_metal_info`, and check
   native feature/body consistency with `nx_model_health`.
7. Create a `flat_pattern` feature; export it with `nx_export_flat_pattern` or place
   its native named view on a drawing with `nx_add_flat_pattern_view`.

All mutation calls run serially on the NX thread under the bridge's existing undo
and operation-receipt handling. Reuse the same operation ID when reconciling a lost
response. After rollback, reacquire object references; deliberately stale IDs are
rejected. Saving follows NX save-boundary semantics: inspect `nx_checkpoint_state`
before relying on an earlier checkpoint. Read-only schema and inspection calls do
not clear recovery history.


### Feature families

| Area | Operations |
|---|---|
| Base and attached material | `tab`, `flange`, `contour_flange`, `lofted_flange` |
| Bending and edge finishing | `bend`, `jog`, `hem`, `break_corner` |
| Cuts and formed details | `normal_cutout`, `bead`, `dimple`, `louver`, `drawn_cutout`, `gusset`, `edge_rip` |
| Corners | `closed_corner`, `three_bend_corner`, `bulge_relief` |
| Conversion and flattening | `convert`, `from_solid`, `flat_solid`, `flat_pattern`, `unbend`, `rebend` |
| Bend modification | `bend_taper`, `resize_bend_angle`, `resize_bend_radius`, `resize_neutral_factor` |
| Advanced construction | `advanced_flange`, `variational_flange`, `bridge_bend`, `joggle`, `lightening_cutout`, `solid_punch` |

Use the schema's real enum names. For example, modern closed corners use
`overlap_type`; the retired `closure_type` property is deliberately excluded.
Flanges use `CreateMultiFlangeBuilder`, and each list entry owns its edge selection,
length, angle and bend overrides. Supplying a list replaces that builder list.
Use returned expression IDs with `nx_set_expression` to edit dimensions without
reselecting the feature's original support geometry.

### Choosing flange and unbend inputs

The operation schema includes `prerequisites` and, where recorded, `example_evidence`
with a repository source and the tested fixture scope. Example coordinates and
dimensions describe that fixture's units; they are not converted for an inch part.
Replace every `$input_N` with a freshly selected typed reference.

- `flange`: the public fixture creates a 100 × 80 × 2 mm XY tab, selects its
  boundary edge nearest `[50, 0, 0]`, then uses
  `{"flanges":[{"edges":["$input_1"],"length":20,"length_reference":"Inside","angle":90}]}`.
  Each entry requires length and angle even with `length_option=Keypoint`; that
  mode also needs its keypoint, and is not validated by this numeric-length
  example. The separate channel fixture verifies `width_option=AtCenter` with
  width 60 on a 100 mm edge. It does not verify every other width-position mode.
- `advanced_flange`: the recorded fixture uses the same tab boundary edge with
  `{"edges":["$input_1"],"length":20,"angle":90}`. It leaves the mode and optional
  references at native defaults. `ToReference`, inferred length, face collectors
  and plane combinations need additional native validation; the exposed fields
  alone do not establish their conditional requirements.
- `unbend` / `rebend`: the recorded fixture adds a 20 mm, 90-degree flange to
  the tab. `$input_1` is its bend face from
  `nx_sheet_metal_info.items[].bends[].face.id`, and `$input_2` is the original
  largest planar web face from the same body. Use
  `{"face_collector":["$input_1"],"reference_entity":"$input_2"}`.
  After unbend, reacquire the current bend and web references before rebend.
  Keep the original web stationary; selecting the flattened bend strip as the
  stationary reference is not equivalent. Edge stationary references are exposed
  but were not exercised by this fixture.

For flat patterns, `x_axis_edge` must be a valid orientation edge on the selected
upward web face. Forming a flange changes that face boundary: reacquire a current
straight boundary/tangent edge instead of searching the original tab outer-edge
location. The four-wall agent fixture recovered from an invalid selection by
using the current web boundary. Invalid orientation was rolled back; this does
not establish that every edge on a curved or complex web is valid.

Secondary contour flanges require an **along-path sketch**, created with
`nx_create_path_sketch`. Use its returned origin/basis/normal to place the profile.
An ordinary planar sketch in the same position is not equivalent. Secondary tabs
require a target body and matching thickness. Both attached workflows were
verified in native NX.

Sections accept either a finished sketch ID or
`{"edges": ["edge ID"], "help_point": [x,y,z]}` /
`{"curves": ["curve ID"], "help_point": [x,y,z]}`. The explicit point controls the
native section selection. Existing sketches remain external inputs; the bridge
does not consume them as internal sketches.

Coordinates and lengths use work-part units; expression angles use degrees and
neutral factors are unitless. Plane inputs use origin/normal; coordinate systems
use orthogonal x/y axes. Direction vectors are normalized. Sections and native
feature prerequisites still matter: a successful schema check is not proof that
selected geometry can be formed.


### Native fixture findings

- Tabs, flanges, default thickness/radius/neutral factor and flange edits were
  measured in native NX. A 100 × 80 × 2 mm base tab has volume 16,000 mm³.
- Lofted flange fixtures use open sections, endpoint points on those sections and
  a positive bending segment count. Closed profiles were not a valid test fixture.
- Bead fixtures need positive angle and punched width; finite normal cutouts need
  a positive depth. Native zero defaults are not necessarily usable.
- Louver sections must be assigned before use. Reading the uninitialized native
  Section property throws. Formed and lanced, unrounded examples passed in both
  depth directions; rounded variants remain unverified.
- Edge rip passed with a short interior slit on a planar sheet face. Ripping
  selected edges of the exploratory tube did not pass and remains unverified.
- Three-bend corner passed on a convex corner with matching adjacent bends.
  Bulge relief passed with an eligible bend-end edge. Arbitrary edge selections
  were rejected; this does not show that the native feature is broken.
- Unbend/rebend use an initialized native face collector and the original web as
  the stationary face. Selecting the newly flattened bend strip is not equivalent.
- Standard `Builder.Validate()` is used. The older `ValidateBuilderData` returned
  inconsistent values on an unchanged valid tab and is not used for acceptance.


### Flat patterns, drawings and artifacts

`nx_export_flat_pattern` exports native DXF or Trumpf GEO to a **new** workspace
file. It reports path, units, options, size and SHA-256. Files are staged beside the
destination, checked and published without overwriting existing artifacts. Retrieve
bytes with `nx_download_file`. Manufacturing format details beyond the tested
fixtures, including downstream machine compatibility, are not certified.

`nx_add_flat_pattern_view` uses the model view generated by the Flat Pattern
feature, preserving the native developed geometry and bend lines. Position is in
sheet millimeters. Existing drawing/PDF tools apply. This does not automatically
create a dimensioned manufacturing drawing, bend table, BOM or balloon layout.

NX may create auxiliary solid bodies for flat-pattern representations. A hidden
source solid can also remain after conversion. Part-wide bounds and summed volume
include such bodies. **Measure the intended folded body by ID** when comparing
physical-part dimensions or volume. Export checks must not use stale DXF header
extents; the acceptance fixture checks actual LINE entity coordinates.

`nx_sheet_metal_annotation` creates native PMI associated with the selected body or
bend faces. Labels contain **measured snapshots**, explicitly identified in their
text. They contain actual thickness or bend angle/radius/neutral factor. Refresh
with the existing annotation ID after geometry changes; automatic numeric text
updates have not been implemented. Material catalog enumeration is not exposed:
`GetMaterialNames()` caused a native memory-access violation on the test host.


## Freeform, documentation and manufacturing

### Curves and surfaces

- `nx_spline`: create/edit associative 3D Studio Splines from interpolation points or control poles, including degree and periodicity.
- `nx_surface_mesh`: native Through Curve Mesh from intersecting primary/cross sections.
- `nx_bridge_surface`: full-edge bridge with G0, G1 or G2 constraints.
- `nx_trim_sheet`, `nx_sew`, `nx_thicken`: associative sheet trimming, sewing and signed face offsets. Incomplete sewing and a sheet fallback when a solid was requested are rejected and rolled back.
- `nx_curve_analysis`: sampled native derivatives, tangent, curvature, radius and spline data.
- `nx_surface_continuity`: bidirectional closest-point gaps, normal angles and orientation-aligned curvature-tensor differences. G2 uses the shape-operator Frobenius norm. Singular samples prevent a pass; sampled results do not certify global continuity.

Native fixtures exercised spline creation/editing, planar meshes, G0/G1/G2 bridge creation, trim, sew, thicken and derivative analysis. Continuity checks distinguished matching planar sheets, separated sheets, and a tangent plane/quadratic-surface join with different curvature. A bridge builder's requested continuity is distinct from independent geometric verification. Periodic splines and all possible network topologies are not covered by these fixtures.


### Imported-face editing

`nx_edit_faces` supports directed face translation, signed normal offset, replacement by another face, and deletion with healing. It creates native NX features and rejects irrelevant arguments. Reacquire face/edge references after topology changes. Tests used controlled solid fixtures with independently calculated volumes; this does not establish reliability on every vendor import or damaged B-rep.


### Assembly documentation

`nx_create_parts_list`, `nx_parts_list_info` and `nx_update_parts_list` expose native BOMs with actual evaluated rows, installed column defaults and assembly traversal scope. `nx_parts_list_balloons` creates NX-associated callout groups in drawing views. Repeated instances aggregate according to the native key columns; native automatic placement still needs visual review.

`nx_explosion_trace` creates a native automatic traceline attached to a named explosion. Its anchors store **native persistent handles** for component occurrences and prototype edges, not transient tags or journal strings. Save/reopen and subsequent placement changes were tested against exact endpoint coordinates. MCP explosion edits, show and animation refresh the endpoints. After manual NX geometry changes, call `nx_show_explosion` to refresh. Missing anchors fail explicitly. Collapsed traces are hidden; expanded managed traces are shown during refresh. This refresh mechanism is managed by MCP rather than an automatic NX callback.

`nx_export_explosion_animation` writes a self-contained HTML player with native PNG frames, a scrubber and per-frame metadata. It uses linear translation and shortest-arc quaternion rotation. Show a modeling view and frame the entire motion first; the camera remains fixed. A temporary undo mark restores poses, view association and model state even when capture fails. This is presentation animation, not a collision-certified disassembly sequence. Retrieve the file through `nx_download_file`. Native drawing PDF export now temporarily prepares all sheet presentations and restores the previous drawing/modeling view, including when invoked after returning to 3D. Single-sheet and two-sheet exports from a modeling view were verified.


### Manufacturing and PMI

- `nx_thread`: explicit manual pitch, diameters, length, start face, handedness and symbolic/detailed representation. Internal and external threads were created natively. These dimensions do not imply a standards-table fit class.
- `nx_pmi_datum` and `nx_pmi_fcf`: native geometry-associated datum symbols and single-frame geometric tolerances, with existing datum references and annotation editing. They cover the published fields, not every modifier or GD&T standard combination.
- `nx_face_analysis`: sampled normal, principal curvature/radius and signed draft angle against a pull direction. Draft is `asin(normal · pull)` in degrees.
- `nx_wall_thickness`: inward-normal rays from sampled face points to the first exit face, with exact native intersections and unresolved samples reported. A 5 mm plate measured 5 mm at every sampled point. It is neither a rolling-ball thickness algorithm nor a certified global minimum.

Native sheet-metal flat patterns and DXF/GEO export remain available from [dev10](tools.md). That document distinguishes tested feature families from unavailable or unverified options; this release does not claim complete coverage of every licensed NX manufacturing module.


### Drawing and assembly documentation

- `nx_list_drawings` enumerates work-part sheets, their sizes, scales and drafting views without activating them. `nx_activate_drawing` opens a sheet; a null drawing returns to modeling. Work/display parts must match and no sketch may be active.
- `nx_list_annotations` paginates notes, PMI, BOMs, balloons, bend tables and explosion traces, returning native subtypes and available text/positions. Drafting positions use sheet coordinates; PMI positions use part coordinates.
- `nx_edit_annotation` moves or renames annotations, or explicitly deletes one. Balloon movement retains native callout associations. Use the dedicated trace tool to change trace geometry.
- `nx_parts_list_column` edits, appends or removes zero-based BOM columns. Inspect existing `columns` first: `field` is the native default expression, such as `<W$=@$PART_NAME>`. An appended general column requires a title, width and field. Callout and quantity columns retain their native types. Widths use sheet units.
- `nx_edit_explosion_trace` changes managed trace endpoint percentages along the anchored edges and native endpoint offsets. Persistent component/edge handles remain attached to the named explosion. Offsets use assembly units. This does not change assembled component placement.


### Threads and GD&T

`nx_thread_catalog` reads the installed NX thread XML **in place**. It lists standards or a bounded page of sizes; selecting an exact standard and size returns the dimensional metadata needed for modeling. It does not transfer the catalog file. `nx_standard_thread` requires an unambiguous catalog row, including method and radial engagement when necessary. It uses the native `ThreadTable` builder, the actual selected cylinder diameter and explicit start face. Symbolic and detailed representations, handedness and direction are supported. Dimensions do not imply an unexposed fit class or a complete standards compliance check.

`nx_pmi_fcf` additionally exposes tolerance and datum MMC/LMC/RFS modifiers, diameter/spherical-diameter/square zone shapes, projected height, tangent-plane and free-state flags. Omitted modifiers reset on editing. Native validation and the published preflight restrictions apply; this is not a full GD&T semantic standards validator.


### Bend tables and measured PMI

`nx_bend_table` creates or edits an NX associative bend table for a native flat-pattern drafting view. Columns include bend ID/name, angle, direction and radius, in caller-selected order. Native automatic updating defaults to enabled. The response includes evaluated rows and settings.

`nx_sheet_metal_annotation(automatic=true)` stores persistent source handles and measured values on the native annotation. Subsequent MCP model mutations refresh changed measurements **inside the same undo transaction**. If a source becomes invalid, the operation fails and rolls back rather than silently retaining obsolete values. Deleting the annotation first removes that dependency. Editing with `automatic=false` disables managed refresh and produces an explicit measured snapshot.

Manual NX edits do not run the MCP transaction hook. Call `nx_refresh_annotations` afterward. MCP also commits native automatic bend-table builders in the same transaction: NX v2606's automatic flag alone left stale rows after reopening. Explicit refresh performs this rebuild after manual edits. Saved source handles are resolved within the owning part, and missing sources are rejected explicitly.


### Drawings and units

- `nx_drawing_view_info`: actual view scale, sheet position, native border and sheet containment. Borders exclude separately placed annotations.
- `nx_list_dimensions`: computed native values and typed dimension references, including PMI and drawing dimensions, native retention status and whether the computed value remains valid. Linear drafting dimensions can reference assembly occurrence edges.
- `nx_edit_drawing_view`: absolute position and/or positive scale, with native readback. Aligned views can reject incompatible moves, which roll back.
- `nx_add_section_drawing_view`: a simple native section and section line. Select an owned edge and `cut_association` of `start`, `end`, or `arc_center`. Step and arrow directions are perpendicular vectors in the sheet XY plane. The scale is assigned explicitly; NX's creation API may otherwise inherit the parent's scale.
- `nx_add_detail_drawing_view`: a circular native detail. Center and radius use model coordinates/part units; the bridge maps them into the parent view. Managed boundary coordinates refresh with parent-view edits. The center is an explicit model coordinate, not a selected material point that moves with arbitrary geometry edits.
- `nx_drawing_table`: editable revision rows or a native NX title block. Caller supplies content; no approvals, dates or revisions are invented. Revision tables use their native section origin and title blocks their native annotation origin. Inspect the drawing before release. A native title-block definition replaces the original table section; the returned reference identifies the resulting title block.

`nx_create_drawing` supports `units="mm"` or `"in"` independently of the work part. A0–A4 retain their physical dimensions. Drawing inspection reports per-sheet units. NXOpen placement points are converted from sheet to part units; UF drawing moves use sheet coordinates. Legacy `position_mm`, `origin_mm` and `spacing_mm` fields remain millimeter values; the additional sheet-unit fields identify the caller's actual coordinates. Model-unit names remain `mm` and `inch`. Projected-view spacing is verified on its free movement axis; NX maintains native associative alignment on the other axis, whose reference coordinate can differ from the parent. The result reports the actual sheet position.

Native mass measurements require explicitly setting `MeasureBodies.InformationUnit` to `KilogramMillimeter`: supplying millimeter unit objects to `NewMassProperties` alone returns cubic inches in inch parts. Volume and interference volume are explicitly reported in mm³.


### Assembly changes and imported geometry

`nx_update_assembly_documentation` explicitly solves existing mates, refreshes managed explosion traces, BOMs, measured annotations and drafting views in the active assembly. It reports actual constraints, evaluated rows, view borders and health. It temporarily opens drawing sheets for native regeneration and restores the prior view. It does not undo earlier prototype edits. Missing trace anchors or unsatisfied constraints fail the refresh transaction.

Replacing a prototype may retain old drawing dimensions at their former values. Refresh reports `documentation_complete: false`, affected dimensions/annotations and warnings. Native retained balloons are also reported; delete obsolete callouts and recreate them from the updated BOM. Reassociate explicitly with `nx_add_dimension(dimension=existing_id, object1=replacement_edge, ...)`; this preserves the native dimension identity.

Replacing a prototype may invalidate its edge anchors. Explicitly remove obsolete traces and create anchors on replacement geometry; the bridge never guesses correspondence between unrelated faces.

`nx_geometry_anchor` stores a native persistent handle plus owner path and kind. `nx_resolve_geometry_anchor` verifies the owner and that the exact body, face or edge survives. A deleted entity returns `NX_STALE_REFERENCE`; geometric nearest-neighbor fallback is not automatic. Anchors are for owned prototype geometry, not assembly occurrences.

`nx_edit_faces` returns native health with its repair result. Failed native edits include the action, selected face references and NX error code when available. The enclosing transaction rolls back unhealthy results.


## Legacy-compatible inspection and edits

`nx_measure_angle` accepts typed work-part line, straight-edge and planar-face
references and returns degrees in [0,180]. Directions use line start/end, edge
vertex order or outward face normals. This is not an oriented dihedral angle;
curved entities and component occurrences are rejected.

`nx_delete_feature` resolves a feature ID or unambiguous name, adds it to the
native update manager deletion list and reports deleted references. Dependent
geometry can be removed; inspect change records and reacquire topology afterward.

`nx_sketch_constraint` routes owned curve IDs to supported sketch editors and
relation/dimension tools. Horizontal/vertical/fix use one curve; pair relations
use two curves in the same sketch. Only dimensional types accept a value.
Coincident uses start-to-start; use `nx_sketch_relation` for explicit endpoints.
Midpoint is explicitly unsupported. The input schema publishes the supported enum.

## Manufacturing imports, drawings and planar export

`nx_import_geometry(target="new_part", output_path="vendor/model.prt")` creates a millimeter part without requiring an existing work part. It uses installed STEP import settings (`step214ug.def`), preserves source bytes in an operation-specific staging directory and reports settings, output paths and translator log excerpts. Failed new-part imports close their newly created parts and restore the previous work/display selection. Retained diagnostic files are separate from model rollback. Translation success alone does not establish geometry health: run `nx_model_health`. Faults include decoded messages and typed face/edge/body references when resolvable. Self-intersecting vendor faces may require supplier repair; `nx_edit_faces(action="heal")` is not a general STEP repair guarantee.

Drawing creation assigns and verifies the sheet scale. New body and assembly base views use it explicitly. `nx_edit_drawing_view(style=...)` controls hidden/visible fonts and widths, smooth/tangent edges, and wireframe/partial/full shading. Fonts 1..7 are solid, dashed, phantom, centerline, dotted, long dashed and dotted dashed. Widths are `thin`, `normal`, `thick` or native numbered widths `"1"`..`"9"`. New base views distinguish dashed hidden edges from solid visible edges. Centerline visibility remains read-only because the exercised NX v2606 setter did not persist; unsupported edits are rejected.

`nx_dimension_format` reads native preferences. `nx_edit_dimension_format` sets decimal precision, trailing zeros, display units, separator and tolerance style/values. It preserves computed values and associativity; no measured text is overridden. Title blocks use a lower-right position anchor; revision tables use an upper-left anchor. Annotation moves require `[x,y,0]` in sheet coordinates. Section direction vectors require three components `[x,y,0]`. PDF exports reject existing paths before native mutation.

`nx_export_planar_dxf` exports an owned sketch or planar face directly to 1:1 millimeter DXF, including inner loops. It retains LINE, ARC and CIRCLE entities and rejects unsupported curves rather than approximating them. The result reports the exact origin/basis and entity counts. Supply both orthonormal axes and an origin in the source plane to choose PCB coordinates. Optional layer overrides map source curve/edge IDs to layer names. No sheet-metal feature is required; export does not certify loop validity or fabrication readiness.

`nx_display_info(objects=[...], count_only=true)` preflights appearance expansion without modifying display state. Changes are limited to 10,000 unique expanded bodies/faces; split larger selections after checking their counts. Oversized committed responses carry a paginated `full_result` handle; see the agent-surface recovery contract.

`nx_batch` publishes a discriminated `{method,params}` schema for each supported child method. Lines and rectangles require an owning sketch ID and structured points; arcs use center/radius/angles and an optional sketch ID. Execution remains serial under one rollback mark. Custom revolve axes use both `axis_origin` and nonzero `axis_direction` in work-part coordinates; leave the principal-axis selector at its default.

`nx_save_part` saves only the work-part file. Component edits require explicit
activation and saving of each component; saving an assembly does not imply
authorization to save its prototypes. This also applies to drawing-preview data.

Save receipts identify `saved_files`, observed disk changes, target before/after
SHA-256 fingerprints, and unrelated modified parts whose file/state was preserved.
Verification covers all loaded part files and flags. Native per-part/object save
errors are returned; unexpected writes or flag changes produce a partial-failure
receipt and must be reconciled before retrying. Unreadable preflight files prevent
the save. External linked files and concurrent external writers are outside the
verification guarantee.

### Assembly load state and drawing output

`nx_list_components` reports `load_state` and a nullable `part_path`; an unloaded
prototype does not abort the inventory. Descendants of an unloaded subassembly
may not be available. `nx_open_part(path, load_components=true)` explicitly loads
unsuppressed prototypes even when the parent is already loaded, restoring the
session's partial-loading preference afterward. Component reference sets are
retained. `nx_close_part` rejects a prototype still referenced by a loaded parent
before saving it; close parent assemblies first. PDF export rejects unloaded
unsuppressed prototypes instead of silently omitting them.

Drawing `hidden_lines` and `self_hidden` are native processing toggles, not simple
visibility switches. Disabling them can draw occluded edges as visible. To hide
obscured edges use `style={hidden_lines:true,self_hidden:true,hidden_font:0}`.
Font 0 is invisible, 1 solid, 2 dashed. Construction filtering preserves
sheet-owned section lines. PDF export updates all drawing views, includes shaded
raster images at native high resolution, and reports its effective output settings.

Component replacement/removal/suppression is already exposed by
`nx_component_action`; use its exact schema. Native relationship retention is
requested on replacement; operation-specific limitations remain in the capability
matrix. Radial/angular/ordinate drawing dimensions are not added by these fixes.
