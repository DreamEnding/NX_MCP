# Capability evidence matrix

Generated from `src/nx_mcp/capability_manifest.json`; do not edit this table by hand.
Run `python scripts/generate_capability_matrix.py` to regenerate, or add `--check` to detect drift.

Manifest revision: **2606-manufacturing-r1**. NX: **v2606**. Bridge protocol: **1**.
Canonical manifest SHA-256: `508138fabac46f909bad567d313ac31c714e59c99268d5ecb95f5a579b72097f`.

These labels report manifest evidence, not certification or independent verification of its claims. Native-tested means status `tested` with an evidence type beginning `real_NX_`; only the stated scope and NX version are covered. Contract/sidecar-tested does not establish native CAD correctness. Experimental includes untested entries and tested entries without a recognized evidence type. Unavailable capabilities are explicitly recorded by the manifest; absence from this matrix is not proof of availability or unavailability.

| Tool classification | Count |
| --- | ---: |
| Native-tested | 181 |
| Contract/sidecar-tested | 8 |
| Experimental | 0 |
| Unavailable | 0 |

## Tools

| Tool | Classification | Manifest status | Evidence type | Tested scope / caveat |
| --- | --- | --- | --- | --- |
| nx_activate_drawing | Native-tested | tested | real_NX_v2606_scoped | Native drawing/modeling switching on saved fixture parts; unrelated parts preserved. |
| nx_activate_part | Native-tested | tested | real_NX_v2606 | Used by loaded-part opening; work/display activation; modified flags preserved |
| nx_add_base_view | Native-tested | tested | real_NX_v2606_scoped | Native top base view of a single-body part; sheet placement verified in exported PDF. |
| nx_add_component | Native-tested | tested | real_NX_v2606 | Initial placement, typed references and nested source assembly |
| nx_add_detail_drawing_view | Native-tested | tested | real_NX_v2606_scoped | Circular detail with explicit model-coordinate center and radius mapped into the parent drawing view; scaled view and exported PDF. |
| nx_add_dimension | Native-tested | tested | real_NX_v2606_scoped | Native horizontal edge dimension with computed size10mm; exported PDF visually reviewed. Aligned/vertical variations are not separately kernel-tested. |
| nx_add_flat_pattern_view | Native-tested | tested | real_NX_v2606_scoped | Native Flat Pattern named view on metric drawing; PDF exported and visually reviewed. |
| nx_add_projection_view | Native-tested | tested | real_NX_v2606_scoped | Native right projected view and associative parent; exported PDF visually reviewed. |
| nx_add_section_drawing_view | Native-tested | tested | real_NX_v2606_scoped | Simple native section through a circular hole center; explicit scale and hatch visible in exported PDF. Complex stepped sections are not included. |
| nx_assembly_constraint | Native-tested | tested | real_NX_v2606_scoped | Native fix and face-distance constraints; actual separation measured. Other exposed relation types retain narrower validation. |
| nx_batch | Native-tested | tested | real_NX_v2606 | Two-step partial failure fully rolled back; serial execution on journal thread |
| nx_bend_table | Native-tested | tested | real_NX_v2606_scoped | Native flat-pattern bend table create/edit, column ordering, evaluated row readback and angle update from 75 to 80 degrees; PDF visually reviewed. |
| nx_bind_parameter | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Native EXTRUDE end expression binding and dependent update; EXTRUDE start and PATTERN count/spacing share builder paths but are not separately native-tested. |
| nx_blend | Native-tested | tested | real_NX_v2606_scoped | Native single-edge radius1 blend on a cube; installed AddChainset API and cleanup verified. |
| nx_boolean | Native-tested | tested | real_NX_v2606_scoped | Overlapping1000mm3 cubes: unite1500, subtract500, intersect500mm3 verified analytically. |
| nx_bridge_surface | Native-tested | tested | real_NX_v2606_scoped | Native full-edge planar G0/G1/G2 bridge creation; requested constraints are not independent geometric certification. |
| nx_cancel_operation | Contract/sidecar-tested | tested | local_contract_tests | Sidecar contract accepts cancellation only for running batches and creates the cancellation marker; batch boundary seam verifies rollback. Live non-running request rejected. No mid-NXOpen interruption claim. |
| nx_capabilities | Native-tested | tested | real_NX_v2606_scoped | Live exact-tool manifest filtering, NX version and API-detection response. API presence is not a geometry correctness certificate. |
| nx_chamfer | Native-tested | tested | real_NX_v2606_scoped | Native single-edge symmetric-offset chamfer on a cube; explicit edge collector and tolerance. |
| nx_check_clearance | Native-tested | tested | real_NX_v2606_interactive | Native 2 mm gap flagged below 3 mm requirement; bounded pair selection and conservative broad phase |
| nx_check_interference | Native-tested | tested | real_NX_v2606_interactive | 10 mm cubes: 500 mm^3 overlap, touching and 2 mm gap; rotated nested occurrence overlap; cleanup preserves saved flags and checkpoint |
| nx_checkpoint | Native-tested | tested | real_NX_v2606 | In-session model checkpoint and available-state inspection |
| nx_checkpoint_state | Native-tested | tested | real_NX_v2606 | Checks actual NX mark availability, including save expiration |
| nx_clear_highlights | Native-tested | tested | real_NX_v2606_public_MCP | Clears MCP-owned native highlights without persistent appearance changes |
| nx_close_part | Native-tested | tested | real_NX_v2606 | Blocks closing prototypes referenced by other loaded assemblies before saving. Native edited-child guard passed; close parents first. Reports all parts actually closed and invalidates their references. |
| nx_component_action | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_component_array | Native-tested | tested | real_NX_v2606_scoped | Native associative rectangular 3x2 and circular 4-instance patterns, including seed. |
| nx_copy_project | Native-tested | tested | real_NX_v2606_scoped | Native clone of saved assembly and prototype, rewritten dependencies, source hashes and manifest; partial-file cleanup covered locally. |
| nx_create_directory | Contract/sidecar-tested | tested | local_contract_tests | Workspace-scoped directory creation and idempotent existing-directory reporting. |
| nx_create_drawing | Native-tested | tested | real_NX_v2606_scoped | Native metric A3 sheet at 1:1 first-angle projection; sheet opening and typed reference. |
| nx_create_explosion | Native-tested | tested | real_NX_v2606_scoped | Native nested assembly explosion: absolute rotated parent/child poses, reset, repeat assignment, model/drawing association, persistence; ordinary assembled placements unchanged. |
| nx_create_part | Native-tested | tested | real_NX_v2606 | Fresh millimeter parts in isolated NX test workspace |
| nx_create_parts_list | Native-tested | tested | real_NX_v2606_scoped | Native assembly drawing BOM: three repeated instances aggregate to quantity 3 using installed column defaults. |
| nx_create_path_sketch | Native-tested | tested | real_NX_v2606_scoped | Native edge-path sketch with arc-length percentage, orienting face and frame read-back; successful secondary contour flange. |
| nx_create_reference_set | Native-tested | tested | real_NX_v2606_scoped | Native SOLIDS reference set containing one block body; saved and consumed by three assembly occurrences. |
| nx_create_sketch | Native-tested | tested | real_NX_v2606 | XY, XZ, YZ and an offset arbitrary orthonormal basis; actual frames and curve coordinates checked |
| nx_curve_analysis | Native-tested | tested | real_NX_v2606_scoped | Native derivative evaluation on owned line/spline curves; singular handling unit-tested; sampling is not a global extrema certificate. |
| nx_delete_explosion | Native-tested | tested | real_NX_v2606_scoped | Native unassociated explosion deletion followed by empty explosion inventory; in-use rejection and stale invalidation covered locally. |
| nx_delete_feature | Native-tested | tested | real_NX_v2606_scoped | Native extrusion deletion after Save As with a reacquired feature ID; resulting body count zero. Dependency cascades beyond this fixture are not independently tested. |
| nx_dimension_format | Native-tested | tested | real_NX_v2606_scoped | Native associative dimension readback, computed value, decimal precision, units and physical asymmetric tolerances. |
| nx_display_info | Native-tested | tested | real_NX_v2606_public_MCP | Body/face and nested occurrence color, transparency and explicit blank state |
| nx_download_file | Contract/sidecar-tested | tested | local_contract_test | Chunk bytes, full checksum and boundary/overwrite tests |
| nx_draft | Native-tested | tested | real_NX_v2606_scoped | Native 5 degree face draft with analytic volume and explicit angle/distance tolerances. |
| nx_drawing_table | Native-tested | tested | real_NX_v2606_scoped | Native title-block definition and editable revision table with evaluated cell readback and reviewed PDF. |
| nx_drawing_view_info | Native-tested | tested | real_NX_v2606_scoped | Native scale, sheet coordinates, border and containment readback for base, detail and section views; millimeter and inch sheets. |
| nx_edit_annotation | Native-tested | tested | real_NX_v2606_scoped | Native associative balloon movement; rename/delete have local contract coverage pending native acceptance. |
| nx_edit_assembly_constraint | Native-tested | tested | real_NX_v2606_scoped | Native suppression toggle and distance 5-&gt;12 edit; actual component separation verified after rebuilding solve network. |
| nx_edit_component_pattern | Native-tested | tested | real_NX_v2606_scoped | Native rectangular 4x3 and circular 5-instance edits; expression and instance readback. |
| nx_edit_dimension_format | Native-tested | tested | real_NX_v2606_scoped | Native 80 mm dimension formatted to two decimals and +0.05/-0.02 mm without changing its value or two associations; exported PDF visually verified. |
| nx_edit_drawing_view | Native-tested | tested | real_NX_v2606_scoped | Base and section style edits, placement, scale and save/reopen tested. Construction filtering excludes sheet-owned section curves. Fonts include 0 invisible. hidden_lines/self_hidden are native processing toggles: false can expose occluded edges. Centerline preference remains read-only. |
| nx_edit_explosion | Native-tested | tested | real_NX_v2606_scoped | Native nested assembly explosion: absolute rotated parent/child poses, reset, repeat assignment, model/drawing association, persistence; ordinary assembled placements unchanged. |
| nx_edit_explosion_trace | Native-tested | tested | real_NX_v2606_scoped | Native managed edge-anchored trace endpoint percentages and offsets edited in a two-component service assembly; rendered and included in drafting view. |
| nx_edit_faces | Native-tested | tested | real_NX_v2606_scoped | Native directed move, signed offset, replace and delete/heal on controlled solids; analytic volume checks. Arbitrary vendor imports unverified. |
| nx_edit_feature | Native-tested | tested | real_NX_v2606 | Extrusion distance 46.25 and native linear-pattern count/pitch; unsupported edit unchanged |
| nx_edit_sketch | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_explosion_info | Native-tested | tested | real_NX_v2606_scoped | Native exploded and assembled occurrence poses and typed associated view references, including nested assembly. |
| nx_explosion_trace | Native-tested | tested | real_NX_v2606_scoped | Native traceline with persistent component/edge handles; exact endpoints preserved after save/reopen and updated by MCP placement changes. Manual edits require MCP refresh. |
| nx_export_drawing_pdf | Native-tested | tested | real_NX_v2606_scoped | Native full-sheet PDF with explicit high-resolution shaded images and updated view lists. Saved/reopened shaded solid and section verified visually; unloaded unsuppressed prototypes fail preflight. Hidden-edge invisibility verified with processing on and font 0. |
| nx_export_explosion_animation | Native-tested | tested | real_NX_v2606_scoped | Three native frames with fixed camera, pose interpolation and restored state; fully framed visual review. Failure cleanup unit-tested. |
| nx_export_flat_pattern | Native-tested | tested | real_NX_v2606_scoped | Native DXF and Trumpf GEO export; staged file publication, checksums. DXF entity geometry inspected. |
| nx_export_planar_dxf | Native-tested | tested | real_NX_v2606_scoped | Analytic lines, circles and arcs from planar sketches and faces; principal/custom frames, layers and inch-to-mm conversion tested in NX. XY sketch/face output independently parsed with ezdxf; splines are rejected. |
| nx_export_step | Native-tested | tested | real_NX_v2606 | Solid/assembly exports verified by import counts, volumes, exact bounds and transforms |
| nx_extrude | Native-tested | tested | real_NX_v2606_scoped | Native offset/symmetric/arbitrary-direction extrusion, through-all subtraction and up-to-face solids; analytic volume and bounds checked. |
| nx_face_analysis | Native-tested | tested | real_NX_v2606_scoped | Native sampled plane normal/curvature and signed draft; trimmed-domain filtering. Not global draft certification. |
| nx_feature_parameters | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | Native extrusion-owned expression enumeration; other feature kinds depend on exposed GetExpressions results. |
| nx_find_geometry | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | Native trimmed BREP point-to-face/edge distance; selector queries, principal plane filter and radius filter. Highest/lowest retain conservative center ordering. |
| nx_finish_preview | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_finish_sketch | Native-tested | tested | real_NX_v2606 | Principal/custom sketch completion and subsequent extrusion |
| nx_fit_view | Native-tested | tested | real_NX_v2606_scoped | Native fit on a displayed solid fixture in graphical NX; framing quality remains view-dependent. |
| nx_flat_pattern_orientation_edges | Native-tested | tested | real_NX_v2606_scoped | Native planar formed-web straight boundary discovery; returned edge created a Flat Pattern on first attempt. Geometric candidates only; not a kernel acceptance guarantee. |
| nx_geometry_anchor | Native-tested | tested | real_NX_v2606_scoped | Owned face persistent handle, owner-part identity and exact native resolution after save/reopen; no nearest-geometry fallback. |
| nx_get_bounding_box | Native-tested | tested | real_NX_v2606 | Part and two-level assembly; conservative and exact with axis-aligned WCS |
| nx_get_feature_info | Native-tested | tested | real_NX_v2606 | Extrude and Pattern Feature expressions and dependencies |
| nx_highlight_collisions | Native-tested | tested | real_NX_v2606_public_MCP | Native highlights on two intersecting nested body occurrences; clear pair not highlighted; inline viewport verified |
| nx_highlight_objects | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_hole | Native-tested | tested | real_NX_v2606_scoped | Native cylindrical subtraction with numeric coordinates, target body and direction; not a threaded/drill-tip HolePackage feature. |
| nx_import_geometry | Native-tested | tested | real_NX_v2606 | STEP solids and nested assembly through WorkPart importer, normal new-part creation; source prototypes closed explicitly; names preflighted |
| nx_inspection_report | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_list_annotations | Native-tested | tested | real_NX_v2606_scoped | Native BOM, balloon and managed sheet-metal PMI enumeration and text. |
| nx_list_assembly_constraints | Native-tested | tested | real_NX_v2606_scoped | Native typed constraint references, geometry/occurrence references, expressions, suppression and solver statuses. |
| nx_list_bodies | Native-tested | tested | real_NX_v2606_scoped | Native work-part inventory reports one extruded body and zero after feature deletion. |
| nx_list_component_patterns | Native-tested | tested | real_NX_v2606_scoped | Native linear, two-direction rectangular and circular pattern metadata and actual occurrence transforms. |
| nx_list_components | Native-tested | tested | real_NX_v2606 | Native loaded and unloaded prototype inventory retains occurrence references, paths and transforms with explicit load_state. Unknown source paths are nullable; unloaded subassemblies can hide descendants. No implicit loading. |
| nx_list_datums | Native-tested | tested | real_NX_v2606_scoped | Native owned datum planes/axes and coordinate-system enumeration on template part. |
| nx_list_dimensions | Native-tested | tested | real_NX_v2606_scoped | Native computed size and retention diagnostics; occurrence-edge dimension follows extrusion resize and explicitly rebinds after replacement. |
| nx_list_drawings | Native-tested | tested | real_NX_v2606_scoped | Native A3 sheet/view enumeration, dimensions, scale and active state. |
| nx_list_explosions | Native-tested | tested | real_NX_v2606_scoped | Native nested assembly explosion: absolute rotated parent/child poses, reset, repeat assignment, model/drawing association, persistence; ordinary assembled placements unchanged. |
| nx_list_expressions | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_list_features | Native-tested | tested | real_NX_v2606_scoped | Native sketch/extrusion inventory and reacquisition of a renamed feature following Save As. |
| nx_list_open_parts | Native-tested | tested | real_NX_v2606 | Loaded names, paths, IDs, work/display status and modified flags |
| nx_list_reference_sets | Native-tested | tested | real_NX_v2606_scoped | Native body-only custom set enumeration and exact member count. |
| nx_list_sections | Native-tested | tested | real_NX_v2606_public_MCP | Native plane enumeration; active view state and saved flags preserved |
| nx_list_sketches | Native-tested | tested | real_NX_v2606_scoped | Native two-sketch work-part inventory with typed references. |
| nx_list_topology | Native-tested | tested | real_NX_v2606 | Face and edge enumeration; face references used in actual distance query Native face-restricted boundary enumeration and bidirectional adjacency used for flat-pattern orientation. |
| nx_loft | Native-tested | tested | real_NX_v2606_scoped | Native solid loft between square sections with analytic volume; sheet configuration has local contract coverage. |
| nx_mass_properties | Native-tested | tested | real_NX_v2606_scoped | Native solid mass, volume, center of gravity and centroidal inertia; 2700kg/m3 test cube matches analytic results. Nested rotated/translated two-body assembly: mass0.0054kg and CoG[0.005,0.035,0.035]m verified. |
| nx_mate_component | Native-tested | tested | real_NX_v2606_scoped | Native touch mate at zero clearance and offset mate at7mm verified by measured separation. Other mate types have narrower validation. |
| nx_material_info | Native-tested | tested | real_NX_v2606_scoped | Native physical material name and kg/m3 density readback; assembly occurrence prototypes supported. |
| nx_measure_angle | Native-tested | tested | real_NX_v2606_scoped | Native perpendicular sketch lines measure 90 degrees using typed references. Straight-edge and planar-face paths use native geometry; broader pair variants are not separately kernel-tested. |
| nx_measure_distance | Native-tested | tested | real_NX_v2606 | Body/body, face/face, nested component/body occurrences; closest points and units |
| nx_measure_volume | Native-tested | tested | real_NX_v2606 | Part and nested assembly sum, returned in mm^3; no union/mass claim Inch-part 0.5 cubic inch volume independently checked as 8193.532 mm3 using explicit native AnalysisUnit. |
| nx_mirror_body | Native-tested | tested | real_NX_v2606_scoped | Native body mirror about YZ origin plane; doubled total volume and reflected bounding box. |
| nx_model_health | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_model_summary | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_native_component_pattern | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | NX 2606 native associative linear pattern: 16 total occurrences of 14 mm seed at 16.5 mm pitch span 261.5 mm. |
| nx_open_part | Native-tested | tested | real_NX_v2606 | Open or activate saved workspace paths. Explicit load_components=true recovers unloaded prototypes on an already loaded parent without closing it; native occurrence pose and 1000 mm3 solid retained. |
| nx_operation_status | Contract/sidecar-tested | tested | local_contract_test | Durable committed/failed/unknown receipt tests; no crash reconstruction claimed |
| nx_package_assembly | Native-tested | tested | real_NX_v2606_scoped | Saved two-part assembly ZIP downloaded with verified checksum, two .prt members and valid archive CRCs; deeper dependency trees are outside this fixture. |
| nx_parts_list_balloons | Native-tested | tested | real_NX_v2606_scoped | Native associated grouped balloon created for an assembly drawing view. |
| nx_parts_list_column | Native-tested | tested | real_NX_v2606_scoped | Native BOM header/width edits, general column append/remove and evaluated values. |
| nx_parts_list_info | Native-tested | tested | real_NX_v2606_scoped | Native evaluated BOM rows and preference readback. |
| nx_pattern | Native-tested | tested | real_NX_v2606 | Native Pattern Feature; 16 total at 16.5 pitch, width 261.5; edit to 3 at 20 pitch |
| nx_pattern_components | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_pmi_datum | Native-tested | tested | real_NX_v2606_scoped | Native geometry-associated datum A on a planar face. |
| nx_pmi_fcf | Native-tested | tested | real_NX_v2606_scoped | Native single-frame flatness/parallelism annotations and datum A reference; all GD&amp;T modifiers are not exposed. |
| nx_preview_change | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_read_result | Contract/sidecar-tested | tested | local_contract_tests | Immutable bounded bridge snapshots, JSON-pointer pagination, result cardinality and committed-outcome preservation on storage failure; 2032-object response fixture. |
| nx_rebuild_model | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Native DoUpdate success and health read-back; failed-update rollback covered by local fault injection. |
| nx_recognize_holes | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | Annular solid: inner cylinder identified as bore, outer cylinder excluded; axis/radius/full circumference read-back. Coaxial grouping and partial-face reporting covered locally; no manufacturing feature inference. |
| nx_refresh_annotations | Native-tested | tested | real_NX_v2606_scoped | Native persistent bend PMI, transaction hook and save/reopen exercised through public MCP; automatic bend-table builders also rebuilt because the native flag alone left stale rows. |
| nx_rename_object | Native-tested | tested | real_NX_v2606_scoped | Native extrusion rename resolved again by returned name after Save As. Other object kinds are not independently covered by this fixture. |
| nx_render_view | Native-tested | tested | real_NX_v2606_scoped | Native Studio image capture, exact 800x600 and 640x480 PNGs; preset2/custom RGB tested; native viewport image visually reviewed. |
| nx_reposition_component | Native-tested | tested | real_NX_v2606_scoped | Native immediate-child 20-unit X translation and 90-degree Z rotation; identical operation-ID replay does not apply translation twice. |
| nx_resolve_geometry | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | Exact point-to-face query re-evaluated after extrusion edit and save/close/reopen; ties rejected. Geometric rule, not immutable topology identity. |
| nx_resolve_geometry_anchor | Native-tested | tested | real_NX_v2606_scoped | Owned face survives save/reopen and rollback in a public USB connector STEP fixture; stale and wrong-owner rejection tested locally. |
| nx_restore_display | Native-tested | tested | real_NX_v2606_public_MCP | Reverse-order restore; invalid order rejected before mutation; face IDs retained across appearance and camera changes |
| nx_restore_presentation | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_revolve | Native-tested | tested | real_NX_v2606 | XY rectangular profile around global Y, boolean none, case-insensitive name lookup |
| nx_rollback | Native-tested | tested | real_NX_v2606 | Explicit checkpoint rollback; stale references rejected afterward |
| nx_save_as | Native-tested | tested | real_NX_v2606_scoped | Native metric solid saved into a nested folder; work-part filename changes and references are reacquired before deletion. Original saved prototype loads into the test assembly. |
| nx_save_part | Native-tested | tested | real_NX_v2606 | Saves only the work part, preserving drawing-preview context. A native 73-loaded-part fixture verified the saved path and file hash, unchanged unrelated files/flags, and an edited child remaining unsaved. Native save errors and unexpected changes return a partial-outcome receipt. Activate and save each component explicitly. |
| nx_save_presentation | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_screenshot | Native-tested | tested | real_NX_v2606_interactive | Native viewport PNG, white/transparent backgrounds, shaded/shaded-with-edges; requested dimensions advisory; actual device resolution returned |
| nx_section_control | Native-tested | tested | real_NX_v2606_public_MCP | Enable, disable and delete native dynamic sections without modifying solids |
| nx_section_view | Native-tested | tested | real_NX_v2606_public_MCP | Principal and arbitrary single-plane clips on solids and assemblies; native cap images; geometry bounds and volume unchanged |
| nx_set_camera | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_set_component_reference_set | Native-tested | tested | real_NX_v2606_scoped | Native direct-child assignment to three occurrences, unchanged translations, persisted drawing excludes prototype datums. Nested children require activating owner. |
| nx_set_component_transform | Native-tested | tested | real_NX_v2606 | Absolute immediate-child placement; repeated identical pose |
| nx_set_datum_visibility | Native-tested | tested | real_NX_v2606_scoped | Native blank/unblank snapshot restoration, and assembly-owned datum suppression confirmed by exported drawing PDF visual review. |
| nx_set_display | Native-tested | tested | real_NX_v2606_public_MCP | Named color and transparency; face attribute restoration; nested occurrence override leaves shared prototypes unchanged |
| nx_set_expression | Native-tested | tested | real_NX_v2606_and_local_stateful_seams | Scoped v2606 native authoring/review acceptance and local failure-path regressions; see docs/tools.md for supported operations and limits. |
| nx_set_feature_parameters | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | Native extrusion-owned Number formula edit and resulting bounds; preflight/rollback boundary tests. No blanket verification of other feature kinds. |
| nx_set_material | Native-tested | tested | real_NX_v2606_scoped | Local density-only physical material assignment, verified by UF native body density. |
| nx_set_sheet_metal_defaults | Native-tested | tested | real_NX_v2606_scoped | Value-mode thickness/radius/neutral factor and numeric read-back. Material/tool tables and custom bend tables remain experimental. |
| nx_set_view | Native-tested | tested | real_NX_v2606_scoped | Native Top, Back and Isometric selection on a solid fixture. Other canned views follow the same API but are not individually verified. |
| nx_set_visibility | Native-tested | tested | real_NX_v2606_public_MCP | Show/hide, nested isolation and restoration of previously hidden components |
| nx_sew | Native-tested | tested | real_NX_v2606_scoped | Native adjacent planar-sheet sewing; incomplete-sew and solid-fallback rejection covered by unit tests. |
| nx_sheet_metal_annotation | Native-tested | tested | real_NX_v2606_scoped | Native body/bend PMI with measured snapshot text and explicit refresh; no automatic numeric text update claim. |
| nx_sheet_metal_context | Native-tested | tested | real_NX_v2606_scoped | Modern graphical UG_APP_SBSM context; no legacy application switch or model undo mark. |
| nx_sheet_metal_defaults | Native-tested | tested | real_NX_v2606_scoped | Numeric defaults and bend-definition read-back; unsafe native material catalog enumeration intentionally not called. |
| nx_sheet_metal_feature | Native-tested | tested | real_NX_v2606_scoped | 34 native creation fixtures on NX v2606; flange builder edit verified. Individual options and other edit combinations remain experimental. Two-bend partial-width channel with Square/Round reliefs and independently calculated developed DXF dimensions; adjacent mitered flanges and flat export. |
| nx_sheet_metal_info | Native-tested | tested | real_NX_v2606_scoped | Native body recognition, thickness, inner bend faces, angle/radius/neutral factor read-back. |
| nx_sheet_metal_schema | Native-tested | tested | real_NX_v2606_scoped | Strict schemas for 34 native operation families; per-operation examples and validation scopes. |
| nx_shell | Native-tested | tested | real_NX_v2606_scoped | Native open-box shell: inward 1 mm thickness on 10 mm cube gives 424 mm3. Outward configuration separately checked locally. |
| nx_show_explosion | Native-tested | tested | real_NX_v2606_scoped | Native nested assembly explosion: absolute rotated parent/child poses, reset, repeat assignment, model/drawing association, persistence; ordinary assembled placements unchanged. |
| nx_sketch_angle | Native-tested | tested | real_NX_v2606_scoped | Native driving angular dimension creation and expression; broader angle configurations remain unverified. |
| nx_sketch_arc | Native-tested | tested | real_NX_v2606 | Full circles on XY, XZ and YZ; resulting solid dimensions and volumes checked |
| nx_sketch_conflicts | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | Native no-conflict query and explicit horizontal+vertical contradiction, bounded single-removal relief, restored constraint count/status. Not a minimal conflict set or general legacy relation verifier. |
| nx_sketch_constraint | Native-tested | tested | real_NX_v2606_scoped | Native horizontal constraint on an owned sketch line through the supported sketch editor. Other routed relations/dimensions retain their dedicated-tool scopes; midpoint is explicitly unsupported. |
| nx_sketch_diagnostics | Native-tested | tested | real_NX_v2606_public_MCP | Active/inactive whole-sketch native evaluation; underconstrained and fully fixed fixtures; remaining DOF, constraints and curve links; saved state preserved |
| nx_sketch_dimension | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | Line length (XZ), horizontal/vertical distances and arc radius/diameter creation; associated expression edit. Reference mismatch guard covered locally. |
| nx_sketch_info | Native-tested | tested | real_NX_v2606_scoped | Native XY sketch with two perpendicular lines; frame and owned curve readback. Principal/custom-frame geometry also covered by the sketch creation fixtures. |
| nx_sketch_line | Native-tested | tested | real_NX_v2606 | Principal-plane profile coordinates checked against resultant solids |
| nx_sketch_primitive | Native-tested | tested | real_NX_v2606_scoped | Circle, horizontal slot and rounded rectangle: native curves and extruded analytic volumes. |
| nx_sketch_rectangle | Native-tested | tested | real_NX_v2606 | Principal/custom bases, multiple loops, retry deduplication and batch rollback |
| nx_sketch_relation | Native-tested | tested | real_NX_v2606_scoped_and_local_boundary_tests | Modern solver parallel/perpendicular/equal_length/equal_radius/concentric/coincident operations with actual geometric residual checks; line endpoints and arc centers. Legacy branch boundary-tested only. |
| nx_sketch_symmetry | Native-tested | tested | real_NX_v2606_scoped | Modern line-pair symmetry about a line; persistent Mirror relation and zero geometric residual. |
| nx_sketch_tangent | Native-tested | tested | real_NX_v2606_scoped | Modern line/circle tangent relation with persistent constraint enumeration and zero geometric residual. |
| nx_sketch_trim_extend | Native-tested | tested | real_NX_v2606_scoped | Native line trim and line extension against an explicit crossing line; reacquire geometry after edits. |
| nx_spline | Native-tested | tested | real_NX_v2606_scoped | Native associative 3D interpolation spline creation/edit and degree-2 control-pole curves; periodic variants unverified. |
| nx_standard_thread | Native-tested | tested | real_NX_v2606_scoped | Native Metric Coarse M6 x 1.0 internal/external symbolic/detailed threads; pitch and material-removal verification. Detailed Metric Fine M6x0.75 and Inch UNC 1/4-20, right and left handed. |
| nx_status | Native-tested | tested | real_NX_v2606_scoped | Live graphical NX 2606 connection, version and UI/main-thread status; restored saved session. |
| nx_surface_continuity | Native-tested | tested | real_NX_v2606_scoped | Bidirectional sampled UF geometry: matching planes pass G0/G1/G2, separated planes fail, tangent plane/quadratic join fails G2. Not a global certificate. |
| nx_surface_mesh | Native-tested | tested | real_NX_v2606_scoped | Native planar and quadratic Through Curve Mesh fixtures from two primary and two cross sections. |
| nx_sweep | Native-tested | tested | real_NX_v2606_scoped | Native square sketch swept along a straight guide sketch; boolean variants use existing boolean operation. |
| nx_thicken | Native-tested | tested | real_NX_v2606_scoped | Native 2 mm sheet thickening with independently checked 400 mm^3 solid volume. |
| nx_thread | Native-tested | tested | real_NX_v2606_scoped | Native manual symbolic and detailed internal/external thread creation with explicit start face and cylinder diameter. Standards-table fit classes not exposed. |
| nx_thread_catalog | Native-tested | tested | real_NX_v2606_scoped | Reads installed NX catalog in place; exact Metric Coarse M6 x 1.0 row selection without file transfer. |
| nx_transform_bodies | Native-tested | tested | real_NX_v2606_scoped | Associative body extraction plus native MoveObject; copy preserves source; absolute transform replacement verified by bounds. |
| nx_trim_sheet | Native-tested | tested | real_NX_v2606_scoped | Native line-boundary half-sheet trim using section and region point. |
| nx_ui_control | Native-tested | tested | real_NX_v2606_interactive | UI-thread identity, visible model mutations, manual pause rejection and resume; native panel |
| nx_undo | Native-tested | tested | real_NX_v2606 | Undo after read-only inspection; save boundary explicitly inspected |
| nx_update_assembly_documentation | Native-tested | tested | real_NX_v2606_scoped | Two-component assembly: extrusion resize and prototype replacement, fixed mates, clearance, explosion traces, BOM and drawing regeneration. Retained dimensions are reported and explicitly rebound. |
| nx_update_parts_list | Native-tested | tested | real_NX_v2606_scoped | Native BOM refresh after adding a fourth instance gives quantity 4. |
| nx_upload_file | Contract/sidecar-tested | tested | local_contract_test | Chunk replay, final checksum, atomic publication, no overwrite and workspace boundary tests |
| nx_view_info | Native-tested | tested | real_NX_v2606_interactive | Interactive display-part camera axes, origin and scale |
| nx_wall_thickness | Native-tested | tested | real_NX_v2606_scoped | Native inward-normal ray thickness: nine 5 mm plate samples. Not rolling-ball or global-minimum thickness. |
| nx_workspace_info | Contract/sidecar-tested | tested | live_sidecar_and_contract_tests | NX host workspace root and path conventions; no NX geometry calls. |
| nx_workspace_list | Contract/sidecar-tested | tested | live_sidecar_and_contract_tests | Live sidecar nested fixture directory with one-entry pagination; workspace boundary and pagination contracts tested locally. |

## Explicitly unavailable capabilities

These are capability names, separate from the tool counts above.

- batch_model_viewport_image
- union_of_all_pairwise_interference_volumes

## Manifest limitations

- No general certification
- Native save expires undo marks
- Checkpoint recovery does not survive NX process restart
- Exact bounds require axis-aligned WCS
- Assembly import can conflict with loaded STEP prototype names
- Modified-object tracking is explicit-only; null means not comprehensive
- Batch structural preflight is not a geometric dry run
- Cooperative cancellation happens between operations only
- Absolute placement currently supports immediate children
- MCP desktop clients must refresh tool schemas after deployment
- CLIFF vendor STEP conversion still returns no bodies with NX v2606 translators; logs and rolled-back new-part outcome are returned. No vendor-geometry repair is claimed.
- Adam Tech vendor geometry has two self-intersecting faces (875315); health diagnostics resolve faces and error text. Native healing failed on a disposable copy and was rolled back.
- Drawing centerline visibility is inspectable but not writable: the tested native setter did not persist.
