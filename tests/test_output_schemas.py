"""Contract acceptance and rejection, including MCP image/error transport."""

import copy
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator, ValidationError
from mcp.shared.memory import create_connected_server_and_client_session

from nx_mcp.integration_server import IntegrationEnvelope, envelope
from nx_mcp.output_schemas import PAYLOADS, output_schema
from nx_mcp.runtime import NXToolError
from nx_mcp.server import create_server
from nx_mcp.workspace import Workspace


def validate(name, payload):
    schema = output_schema(name, IntegrationEnvelope.model_json_schema())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(envelope(payload).structuredContent)


@pytest.mark.parametrize("name", list(PAYLOADS))
def test_success_requires_payload_but_error_has_independent_contract(name):
    with pytest.raises(ValidationError):
        validate(name, {})
    validate(
        name,
        NXToolError(
            "NX_TEST", "Actionable failure", details={"mutation_outcome": "rolled_back"}
        ).as_dict(),
    )
    with pytest.raises(ValidationError):
        validate(name, {"status": "error", "message": "missing code and retry guidance"})


def test_recovery_states_and_unknown_are_typed_without_inventing_failure():
    unknown = {"operation_id": "unknown_123", "state": "unknown", "mutation_outcome": "unknown"}
    validate("nx_operation_status", unknown)
    for key, value in [("state", "complete"), ("mutation_outcome", "probably_ok")]:
        with pytest.raises(ValidationError):
            validate("nx_operation_status", {**unknown, key: value})
    validate(
        "nx_operation_status",
        {
            **unknown,
            "state": "committed",
            "mutation_outcome": "committed",
            "result": {"object": {}},
        },
    )


def test_geometry_cardinality_units_and_closest_points():
    ref = {"id": "body123", "kind": "body", "name": "", "part_id": "part123"}
    volume = {
        "bodies": [{"body": ref, "volume_mm3": 12}],
        "body_count": 1,
        "volume_mm3": 12,
        "volume_units": "mm^3",
        "semantics": "sum_of_included_bodies",
        "scope": "part",
    }
    validate("nx_measure_volume", volume)
    with pytest.raises(ValidationError):
        validate("nx_measure_volume", {**volume, "volume_units": "in^3"})
    distance = {
        "distance": 5,
        "closest_points": [[0, 0, 0], [5, 0, 0]],
        "references": ["a", "b"],
        "resolved_tags": [1, 2],
        "coordinate_frame": "work_part",
        "pair_count": 1,
        "method": "NX",
        "accuracy": None,
    }
    validate("nx_measure_distance", distance)
    bad = copy.deepcopy(distance)
    bad["closest_points"][0].pop()
    with pytest.raises(ValidationError):
        validate("nx_measure_distance", bad)


@pytest.mark.asyncio
async def test_fresh_mcp_client_accepts_artifacts_and_structured_errors(tmp_path):
    (tmp_path / "test.txt").write_text("hello")
    server = create_server(AsyncMock(), Workspace(tmp_path), enable_experimental=True)
    async with create_connected_server_and_client_session(server) as client:
        for args in [{"delivery": "metadata"}, {"length": 2}, {"offset": 5}]:
            result = await client.call_tool("nx_download_file", {"path": "test.txt", **args})
            assert not result.isError
            validate("nx_download_file", result.structuredContent)
        error = await client.call_tool("nx_download_file", {"path": "absent.txt"})
        assert error.isError and error.structuredContent["code"] == "NX_FILE_NOT_FOUND"
        validate("nx_download_file", error.structuredContent)
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert "sketch_name" in tools["nx_revolve"].inputSchema["required"]
        assert "Saves the part" in tools["nx_export_step"].description
        assert "checkpoints can expire" in tools["nx_export_step"].description
        bad = await client.call_tool("nx_revolve", {})
        assert bad.isError and bad.structuredContent["code"] == "NX_INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_new_inspection_tools_publish_actionable_docstrings(tmp_path):
    server = create_server(AsyncMock(), Workspace(tmp_path), enable_experimental=True)
    tools = {t.name: t for t in await server.list_tools()}
    expected = {
        "nx_flat_pattern_orientation_edges": "x_axis_edge",
        "nx_create_reference_set": "solid body IDs",
        "nx_set_component_reference_set": "direct children",
        "nx_list_reference_sets": "direct members",
        "nx_list_datums": "coordinate systems",
        "nx_set_datum_visibility": "restore_id",
        "nx_list_open_parts": "total_count",
        "nx_list_components": "include_transforms=False",
    }
    for name, guidance in expected.items():
        assert guidance in tools[name].description
        assert (
            "semantics and installed API support have not been validated"
            not in tools[name].description
        )
