"""Conservative task guidance, with unknown effects explicit rather than guessed."""

import json
from pathlib import Path

MANIFEST = json.loads(Path(__file__).with_name("capability_manifest.json").read_text())

# Reviewed contracts; omitted tools retain their descriptions and unknown effects.
GUIDANCE = {
    "nx_create_part": (
        ["Workspace-relative .prt path; no existing file"],
        [],
        {"path": "project/bracket.prt"},
    ),
    "nx_open_part": (["Existing workspace .prt path"], ["part"], {"path": "project/bracket.prt"}),
    "nx_create_sketch": (["Active work part"], ["part"], {"plane": "XY", "name": "profile"}),
    "nx_sketch_rectangle": (
        ["Active owned sketch; coordinates in sketch-local units"],
        ["sketch"],
        {"sketch_id": "<sketch_id>", "corner1": {"x": 0, "y": 0}, "corner2": {"x": 20, "y": 10}},
    ),
    "nx_finish_sketch": (["Existing active sketch"], ["sketch"], {"sketch_id": "<sketch_id>"}),
    "nx_extrude": (
        ["Finished sketch with suitable closed section; lengths in work-part units"],
        ["sketch"],
        {"sketch_id": "<sketch_id>", "distance": 5},
    ),
    "nx_list_topology": (
        ["Current body reference; reacquire topology after edits"],
        ["body", "face"],
        {"body": "<body_id>", "include_adjacency": True},
    ),
    "nx_measure_volume": (
        ["Active part or assembly with solid geometry"],
        ["body", "part", "component"],
        {},
    ),
    "nx_add_component": (
        ["Active assembly; saved prototype path"],
        ["part"],
        {"part_path": "project/bracket.prt", "name": "bracket"},
    ),
    "nx_set_component_transform": (
        ["Current occurrence reference; absolute transform in documented frame"],
        ["component"],
        None,
    ),
    "nx_save_part": (
        [
            "Active work part with writable path; saves only this part, never component files. Returns saved paths and verifies loaded-file hashes and unrelated modified flags"
        ],
        ["part"],
        {},
    ),
    "nx_close_part": (
        ["Current part reference; explicitly choose whether to save"],
        ["part"],
        {"part": "<part_id>", "save": True},
    ),
    "nx_export_step": (
        ["Active work part; export also saves it"],
        ["part"],
        {"path": "project/bracket.step"},
    ),
    "nx_screenshot": (
        ["Graphical NX with active part; agent mode"],
        ["part"],
        {"path": "project/review.png"},
    ),
    "nx_download_file": (
        ["Existing workspace artifact"],
        [],
        {"path": "project/review.png", "delivery": "metadata"},
    ),
    "nx_sketch_diagnostics": (
        ["Current owned sketch reference"],
        ["sketch"],
        {"sketch_id": "<sketch_id>"},
    ),
    "nx_rollback": (["Live explicit checkpoint; no intervening manual edits"], [], None),
}
GEOMETRY = {
    "nx_create_sketch",
    "nx_sketch_rectangle",
    "nx_finish_sketch",
    "nx_extrude",
    "nx_add_component",
    "nx_set_component_transform",
    "nx_rollback",
}
VISIBILITY = {
    "nx_set_display",
    "nx_set_visibility",
    "nx_set_datum_visibility",
    "nx_restore_display",
    "nx_set_view",
    "nx_set_camera",
    "nx_fit_view",
    "nx_show_explosion",
    "nx_section_view",
    "nx_section_control",
    "nx_highlight_collisions",
    "nx_clear_highlights",
}
SAVES = {"nx_save_part", "nx_save_as", "nx_export_step", "nx_close_part", "nx_export_drawing_pdf"}
FILES = SAVES | {
    "nx_screenshot",
    "nx_render_view",
    "nx_upload_file",
    "nx_export_drawing_pdf",
    "nx_export_flat_pattern",
    "nx_package_assembly",
    "nx_create_directory",
}
INVALIDATES = {
    "nx_rollback",
    "nx_undo",
    "nx_close_part",
    "nx_edit_feature",
    "nx_edit_faces",
    "nx_rename_object",
    "nx_set_expression",
}


def guidance(tool):
    readonly = bool(tool.annotations and tool.annotations.readOnlyHint)
    reviewed = GUIDANCE.get(tool.name)

    def effect(group):
        return True if tool.name in group else (False if readonly else None)

    return {
        "prerequisites": reviewed[0]
        if reviewed
        else [
            "Inspect tool description and exact schema; additional native prerequisites may apply"
        ],
        "supported_object_kinds": reviewed[1] if reviewed else None,
        "minimal_example": reviewed[2] if reviewed else None,
        "example_convention": "Angle-bracket IDs are placeholders; use live references. Add a unique operation_id for mutations.",
        "native_evidence": MANIFEST["tools"].get(
            tool.name, {"status": "unavailable", "scope": "No manifest entry"}
        ),
        "effects": {
            "read_only": readonly,
            "geometry_or_assembly": effect(GEOMETRY),
            "visibility_or_view": effect(VISIBILITY),
            "saves_part": effect(SAVES),
            "writes_files": effect(FILES),
            "invalidates_references": effect(INVALIDATES),
            "expires_undo_checkpoints": effect(
                SAVES | {"nx_import_geometry", "nx_open_part", "nx_create_part"}
            ),
            "recovery": "Saves and part lifecycle changes can expire native undo marks. Inspect nx_checkpoint_state and create a new checkpoint after these operations; durable operation receipts remain available.",
            "unknown_semantics": "null means not reviewed; true can be conditional on arguments. Read the exact tool description.",
        },
    }


def next_actions(method, payload):
    """Only suggest actions with already returned IDs, never new mutations."""
    if method in {"nx_list_topology", "nx_download_file", "nx_sketch_diagnostics"}:
        return []
    actions = []

    def identity(value):
        return value.get("id") if isinstance(value, dict) else None

    body = next((identity(x) for x in payload.get("bodies", []) if identity(x)), None)
    body = body or identity(payload.get("body"))
    obj = payload.get("object", {})
    if isinstance(obj, dict) and obj.get("kind") == "body":
        body = body or identity(obj)
    if body:
        actions.append(
            {
                "tool": "nx_list_topology",
                "arguments": {"body": body},
                "purpose": "Inspect current faces and edges before selecting geometry",
            }
        )
    if isinstance(obj, dict) and obj.get("kind") == "sketch":
        actions.append(
            {
                "tool": "nx_sketch_diagnostics",
                "arguments": {"sketch_id": obj["id"]},
                "purpose": "Check sketch constraints and remaining degrees of freedom",
            }
        )
    if payload.get("path") and payload.get("sha256"):
        actions.append(
            {
                "tool": "nx_download_file",
                "arguments": {"path": payload["path"], "delivery": "metadata"},
                "purpose": "Retrieve artifact metadata before programmatic download",
            }
        )
    return actions
