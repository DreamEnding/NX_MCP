"""JSON Schema payload contracts for the integration surface.

Schemas describe committed results; they do not run a second validation step after
an NX mutation. Errors have a separate branch so success cardinality requirements
never hide a useful kernel error. Additive native metadata remains permitted.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

S = {"type": "string"}
N = {"type": "number"}
COUNT = {"type": "integer", "minimum": 0}
B = {"type": "boolean"}
NULL = {"type": "null"}
OUTCOMES = [
    "not_started",
    "running",
    "committed",
    "rolled_back",
    "partial",
    "unknown",
    "not_applicable",
]


def arr(item: dict, count: int | None = None) -> dict:
    result: dict[str, Any] = {"type": "array", "items": item}
    if count is not None:
        result.update(minItems=count, maxItems=count)
    return result


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": True,
    }


VEC = arr(N, 3)
REF = obj(
    {
        "id": S,
        "kind": S,
        "name": S,
        "part_id": S,
        "session_id": S,
        "generation_id": S,
        "owner_part_path": S,
        "journal_id": S,
        "display_name": S,
    },
    ["id", "kind", "name", "part_id"],
)
REF["description"] = (
    "Opaque identity, separate from display name. Reacquire after close, rollback or manual handoff; use occurrence context for assemblies."
)
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
META = {"path": S, "size": COUNT, "sha256": HASH}
PAIR = obj(
    {
        "objects": arr(REF, 2),
        "distance": N,
        "closest_points": arr(VEC, 2),
        "classification": {"enum": ["clear", "contact", "penetration", "below_clearance"]},
        "interference_volume_mm3": N,
    }
)

# Required fields follow actual bridge implementations; optional metadata remains typed.
PAYLOADS: dict[str, dict[str, Any]] = {
    "nx_get_bounding_box": obj(
        {
            "min": VEC,
            "max": VEC,
            "dimensions": VEC,
            "coordinate_frame": S,
            "bounds_type": {"enum": ["exact", "conservative"]},
            "body_count": COUNT,
            "scope": S,
            "bodies": arr(obj({"body": REF, "box": arr(N, 6), "solid": B})),
        }
    ),
    "nx_measure_distance": obj(
        {
            "distance": N,
            "closest_points": arr(VEC, 2),
            "references": arr(S, 2),
            "resolved_tags": arr(COUNT, 2),
            "coordinate_frame": S,
            "pair_count": COUNT,
            "method": S,
            "accuracy": NULL,
        }
    ),
    "nx_measure_volume": obj(
        {
            "volume_mm3": N,
            "volume_units": {"const": "mm^3"},
            "semantics": {"const": "sum_of_included_bodies"},
            "body_count": COUNT,
            "scope": S,
            "bodies": arr(obj({"body": REF, "volume_mm3": N})),
        }
    ),
    "nx_list_topology": obj({"body": REF, "faces": arr(REF), "edges": arr(REF), "solid": B}),
    "nx_list_components": obj(
        {
            "count": COUNT,
            "matrix_convention": S,
            "components": arr(
                obj(
                    {
                        "object": REF,
                        "name": S,
                        "part_path": S,
                        "depth": COUNT,
                        "translation": VEC,
                        "rotation_matrix": arr(VEC, 3),
                        "coordinate_frame": S,
                        "suppressed": B,
                        "reference_set": S,
                    }
                )
            ),
        }
    ),
    "nx_operation_status": obj(
        {
            "operation_id": S,
            "state": {"enum": ["running", "committed", "failed", "unknown", "rolled_back"]},
            "mutation_outcome": {"enum": OUTCOMES},
            "method": S,
            "reason": S,
            "session_id": S,
            "started_at": S,
            "finished_at": S,
            "result": {"type": "object"},
            "error": {"type": "object"},
            "reverted_by": S,
            "query_operation_id": S,
            "query_session_id": S,
        },
        ["operation_id", "state", "mutation_outcome"],
    ),
    "nx_checkpoint": obj({"checkpoint_id": S, "message": S}),
    "nx_rollback": obj({"checkpoint_id": S, "message": S}),
    "nx_checkpoint_state": obj(
        {
            "checkpoints": arr(
                obj({"checkpoint_id": S, "part_id": S, "index": COUNT, "label": S, "available": B})
            ),
            "undo_depth": COUNT,
            "save_semantics": S,
            "retention": S,
        }
    ),
    "nx_workspace_list": obj(
        {
            "path": S,
            "count": COUNT,
            "total_count": COUNT,
            "offset": COUNT,
            "next_offset": {"anyOf": [COUNT, NULL]},
            "entries": arr(
                {
                    "oneOf": [
                        obj({**META, "kind": {"const": "file"}}),
                        obj({"path": S, "kind": {"const": "directory"}}),
                    ]
                }
            ),
        }
    ),
    "nx_download_file": {
        "oneOf": [
            obj({**META, "delivery": {"const": "metadata"}}),
            obj(
                {
                    **META,
                    "delivery": {"const": "image"},
                    "mime_type": {"const": "image/png"},
                    "resolution": arr(COUNT, 2),
                }
            ),
            obj(
                {
                    **META,
                    "offset": COUNT,
                    "bytes_returned": COUNT,
                    "next_offset": {"anyOf": [COUNT, NULL]},
                    "data_base64": S,
                    "eof": B,
                }
            ),
        ]
    },
}
for name in ("nx_check_interference", "nx_check_clearance"):
    PAYLOADS[name] = obj(
        {
            "pairs": arr(PAIR),
            "counts": {"type": "object", "additionalProperties": COUNT},
            "body_count": COUNT,
            "pair_count": COUNT,
            "reported_pair_count": COUNT,
            "broad_phase_clear_pairs": COUNT,
            "minimum_clearance": N,
            "volume_units": {"const": "mm^3"},
            "coordinate_frame": S,
            "complete": B,
            "semantics": S,
        }
    )
for name in ("nx_extrude", "nx_pattern"):
    PAYLOADS[name] = obj(
        {"feature": REF, "body": {"anyOf": [REF, NULL]}, "bodies": arr(REF), "body_count": COUNT},
        ["feature", "bodies"],
    )
PAYLOADS["nx_revolve"] = obj({"feature": REF, "angle": N, "axis": S})
for name in (
    "nx_screenshot",
    "nx_render_view",
    "nx_export_step",
    "nx_export_drawing_pdf",
    "nx_export_flat_pattern",
    "nx_package_assembly",
):
    PAYLOADS[name] = obj({**META, "mime_type": S, "resolution": arr(COUNT, 2)}, ["path", "sha256"])


# Lifecycle and authoring results use their actual native field names.
INTEGER = {"type": "integer"}
PAGE = {
    "count": COUNT,
    "total_count": COUNT,
    "offset": COUNT,
    "next_offset": {"anyOf": [COUNT, NULL]},
}
EXPRESSION = obj(
    {
        "object": REF,
        "name": S,
        "formula": S,
        "type": S,
        "value": {"anyOf": [N, NULL]},
        "units": S,
        "editable": B,
        "parents": arr(REF),
        "dependents": arr(REF),
        "dependency_scope": S,
    }
)
PAYLOADS.update(
    {
        "nx_create_part": obj({"part": REF, "message": S}),
        "nx_open_part": obj(
            {"part": REF, "work": B, "display": B, "already_loaded": B, "path": S, "message": S}
        ),
        "nx_activate_part": obj({"part": REF, "work": B, "display": B, "message": S}),
        "nx_save_as": obj({"part": REF, "path": S, "message": S}),
        "nx_save_part": obj(
            {
                "path": S,
                "message": S,
                "recovery": PAYLOADS["nx_checkpoint_state"],
                "requested_file": S,
                "saved_files": arr(S),
                "observed_changed_files": arr(S),
                "save_scope": S,
                "verification_scope": S,
                "verified_part_count": COUNT,
                "target_file": {"type": "object"},
                "unrelated_modified_parts": arr({"type": "object"}),
                "unexpected_changes": arr({"type": "object"}),
                "unexpected_new_parts": arr({"type": "object"}),
                "native_save_errors": arr({"type": "object"}),
            }
        ),
        "nx_close_part": obj(
            {
                "closed_parts": arr(REF),
                "closed_count": COUNT,
                "remaining_count": COUNT,
                "message": S,
            }
        ),
        "nx_list_open_parts": obj(
            {
                "parts": arr(
                    obj({"part": REF, "name": S, "path": S, "work": B, "display": B, "modified": B})
                ),
                **PAGE,
            }
        ),
        "nx_sketch_info": obj(
            {
                "object": REF,
                "frame": obj(
                    {
                        "origin": VEC,
                        "x_axis": VEC,
                        "y_axis": VEC,
                        "normal": VEC,
                        "coordinate_frame": S,
                    }
                ),
                "curves": arr(obj({"object": REF, "type": S})),
                "curve_count": COUNT,
            }
        ),
        "nx_sketch_diagnostics": obj(
            {
                "sketch": REF,
                "solver_status": S,
                "remaining_degrees_of_freedom": {"anyOf": [INTEGER, NULL]},
                "native_dof_value": INTEGER,
                "constraints": arr(
                    obj({"object": REF, "type": S, "expression": EXPRESSION}, ["object", "type"])
                ),
                "constraint_count": COUNT,
                "geometry": arr(obj({"object": REF, "constraints": arr(S)})),
                "geometry_count": COUNT,
                "evaluation": S,
                "conflicting_constraints": NULL,
                "work_region_handling": S,
            }
        ),
        "nx_sheet_metal_feature": obj(
            {
                "feature": REF,
                "bodies": arr(REF),
                "body_count": COUNT,
                "body": {"anyOf": [REF, NULL]},
                "coordinate_frame": S,
                "created": arr(REF),
                "modified": arr(REF),
                "operation": S,
                "native_feature_type": S,
                "requested_parameters": {"type": "object"},
                "expressions": arr(EXPRESSION),
                "model_view_name": S,
            },
            [
                "feature",
                "bodies",
                "body_count",
                "body",
                "coordinate_frame",
                "created",
                "modified",
                "operation",
                "native_feature_type",
                "requested_parameters",
                "expressions",
            ],
        ),
        "nx_sheet_metal_info": obj(
            {
                "items": arr(
                    obj(
                        {
                            "body": REF,
                            "sheet_metal": B,
                            "thickness": N,
                            "bends": arr(
                                obj(
                                    {
                                        "face": REF,
                                        "state": S,
                                        "inner_radius": N,
                                        "angle_degrees": N,
                                        "neutral_factor": N,
                                    }
                                )
                            ),
                            "bend_count": COUNT,
                        },
                        ["body", "sheet_metal"],
                    )
                ),
                "total": COUNT,
                "offset": COUNT,
                "next_offset": {"anyOf": [COUNT, NULL]},
                "coordinate_frame": S,
            }
        ),
        "nx_list_drawings": obj(
            {
                "sheets": arr(
                    obj(
                        {
                            "object": REF,
                            "name": S,
                            "active": B,
                            "width": N,
                            "height": N,
                            "units": S,
                            "scale": arr(N, 2),
                            "views": arr(obj({"object": REF, "name": S})),
                        }
                    )
                ),
                "units": {"const": "per_sheet"},
                "coordinate_frame": {"const": "drawing_sheet"},
                "modeling_active": B,
            }
        ),
        "nx_drawing_view_info": obj(
            {
                "object": REF,
                "drawing": REF,
                "native_type": S,
                "position": arr(N, 2),
                "scale": N,
                "bounds": arr(N, 4),
                "inside_sheet": B,
                "units": S,
                "coordinate_frame": S,
                "bounds_semantics": S,
            }
        ),
    }
)
PAYLOADS["nx_activate_drawing"] = deepcopy(PAYLOADS["nx_list_drawings"])
PAYLOADS["nx_edit_drawing_view"] = deepcopy(PAYLOADS["nx_drawing_view_info"])
component_fields = PAYLOADS["nx_list_components"]["properties"]["components"]["items"]
component_fields["properties"].update(
    part_path={"anyOf": [S, NULL]}, load_state=S, prototype_type={"anyOf": [S, NULL]}
)
component_fields["required"] = [
    x
    for x in component_fields["required"]
    if x not in {"translation", "rotation_matrix", "coordinate_frame"}
]
PAYLOADS["nx_list_components"]["properties"].update(PAGE)


REFERENCE_SET = obj(
    {
        "object": REF,
        "name": S,
        "member_count": COUNT,
        "members": arr(REF),
        "add_components_automatically": B,
    }
)
DISPLAY_ROW = obj({"object": REF, "blanked": B})
PAYLOADS.update(
    {
        "nx_create_reference_set": REFERENCE_SET,
        "nx_list_reference_sets": obj(
            {"reference_sets": arr(REFERENCE_SET), "count": COUNT, "built_in": arr(S)}
        ),
        "nx_set_component_reference_set": obj(
            {
                "components": arr(
                    obj({"object": REF, "previous_reference_set": S, "reference_set": S})
                ),
                "count": COUNT,
                "prototype_parts_modified": {"const": False},
            }
        ),
        "nx_list_datums": obj(
            {
                "datums": arr(obj({"object": REF, "native_type": S, "blanked": B})),
                "count": COUNT,
                "scope": {"const": "work_part"},
            }
        ),
        "nx_set_datum_visibility": obj(
            {
                "restore_id": S,
                "objects": arr(DISPLAY_ROW),
                "count": COUNT,
                "visible": B,
                "scope": {"const": "work_part"},
            }
        ),
        "nx_flat_pattern_orientation_edges": obj(
            {
                "upward_face": REF,
                "edges": arr(
                    obj({"edge": REF, "start": VEC, "end": VEC, "adjacent_faces": arr(S)})
                ),
                "count": COUNT,
                "coordinate_frame": S,
                "eligibility": S,
            }
        ),
        "nx_create_drawing": obj(
            {
                "object": REF,
                "sheet_name": S,
                "size": S,
                "dimensions_mm": arr(N, 2),
                "dimensions": arr(N, 2),
                "scale": N,
                "projection": S,
            }
        ),
        "nx_list_dimensions": obj(
            {
                "dimensions": arr(
                    obj(
                        {
                            "object": REF,
                            "native_type": S,
                            "computed_value": N,
                            "retained": B,
                            "measurement_valid": B,
                            "origin": VEC,
                        }
                    )
                ),
                "coordinate_frame": S,
            }
        ),
        "nx_sheet_metal_defaults": obj(
            {
                "parameters": {
                    "type": "object",
                    "additionalProperties": {"anyOf": [EXPRESSION, NULL]},
                },
                "parameter_entry": S,
                "bend_definition": S,
                "bend_table": S,
                "bend_allowance_formula": S,
                "bend_deduction_formula": S,
                "material": S,
                "tool": S,
                "material_catalog_status": S,
            }
        ),
    }
)
PAYLOADS["nx_set_sheet_metal_defaults"] = deepcopy(PAYLOADS["nx_sheet_metal_defaults"])


PAYLOADS.update(
    {
        "nx_read_result": obj(
            {
                "result_id": S,
                "field": S,
                "value": {},
                "total_count": {"anyOf": [COUNT, NULL]},
                "offset": COUNT,
                "next_offset": {"anyOf": [COUNT, NULL]},
                "omitted": {"type": "object"},
            }
        ),
        "nx_dimension_format": obj(
            {
                "object": REF,
                "computed_value": N,
                "measurement_units": S,
                "measurement_valid": B,
                "decimal_places": COUNT,
                "tolerance_decimal_places": COUNT,
                "upper_tolerance": N,
                "lower_tolerance": N,
                "tolerance_type": S,
                "trailing_zeros": B,
                "display_units": S,
                "decimal_separator": S,
                "association_count": COUNT,
            }
        ),
        "nx_export_planar_dxf": obj(
            {
                "path": S,
                "size": COUNT,
                "sha256": S,
                "units": {"const": "mm"},
                "scale": {"const": 1.0},
                "coordinate_frame": {"type": "object"},
                "entity_count": COUNT,
                "entity_counts": {"type": "object"},
                "entities": arr({"type": "object"}),
                "all_boundary_loops_included": B,
            }
        ),
    }
)
PAYLOADS["nx_edit_dimension_format"] = deepcopy(PAYLOADS["nx_dimension_format"])


def output_schema(name: str, common: dict) -> dict:
    """Keep an object root for MCP; discriminate errors before success payloads."""
    schema = deepcopy(common)
    schema["title"] = name + " result"
    schema["description"] = "Structured success/error envelope with additive metadata. " + (
        "Tool-specific payload is typed."
        if name in PAYLOADS
        else "Tool-specific payload remains extensible."
    )
    schema["properties"].update(
        {
            "full_result": obj(
                {
                    "id": S,
                    "sha256": S,
                    "size": COUNT,
                    "tool": {"const": "nx_read_result"},
                    "immutable": {"const": True},
                }
            ),
            "omitted": {"type": "object"},
            "code": S,
            "message": S,
            "retryable": B,
            "suggestion": S,
            "nx_code": {"type": ["integer", "string"]},
            "details": obj({"operation_id": S, "mutation_outcome": {"enum": OUTCOMES}}, []),
        }
    )
    schema["properties"]["mutation_outcome"] = {"anyOf": [{"enum": OUTCOMES}, NULL]}
    schema["allOf"] = [
        {
            "if": {"properties": {"status": {"const": "error"}}},
            "then": {"required": ["code", "message", "retryable"]},
        }
    ]
    if name in PAYLOADS:
        schema["allOf"].append(
            {
                "if": {
                    "properties": {"status": {"const": "success"}},
                    "not": {"required": ["full_result"]},
                },
                "then": deepcopy(PAYLOADS[name]),
            }
        )
    return schema
