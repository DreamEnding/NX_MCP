import os
import time
from unittest.mock import AsyncMock

import pytest

from nx_mcp.agent_guidance import guidance
from nx_mcp.agent_surface import ResultStore, compact_payload
from nx_mcp.result_retention import maintain
from nx_mcp.server import create_server
from nx_mcp.workspace import Workspace


@pytest.mark.asyncio
async def test_guidance_examples_match_real_input_schemas(tmp_path):
    from nx_mcp.agent_guidance import GUIDANCE

    server = create_server(AsyncMock(), Workspace(tmp_path), enable_experimental=True)
    for name, (_, _, example) in GUIDANCE.items():
        tool = server._tool_manager.get_tool(name)
        assert tool is not None
        if example is not None:
            tool.fn_metadata.arg_model.model_validate(example)
        result = guidance(tool)
        assert result["native_evidence"]["status"] in {"tested", "experimental", "unavailable"}
    assert (
        guidance(server._tool_manager.get_tool("nx_export_step"))["effects"]["saves_part"] is True
    )
    assert (
        guidance(server._tool_manager.get_tool("nx_measure_volume"))["effects"][
            "geometry_or_assembly"
        ]
        is False
    )


def test_next_actions_use_returned_ids_without_fabrication():
    ref = {"id": "body_live", "kind": "body", "part_id": "owner"}
    result = compact_payload(
        {"bodies": [ref], "warnings": ["review"], "mutation_outcome": "committed"},
        "result_id",
        "nx_extrude",
    )
    assert result["next_actions"][0]["arguments"] == {"body": "body_live"}
    assert result["warnings"] == ["review"]
    assert "next_actions" not in compact_payload({"status": "error"}, "result_id")


def test_retention_dry_run_protection_and_untouched_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("NX_MCP_RESULT_MAX_BYTES", "1000")
    store = ResultStore(tmp_path)
    old = store.put({"old": "x" * 100})
    path = store.root / (old + ".json")
    os.utime(path, (time.time() - 100, time.time() - 100))
    unrelated = store.root / "not-a-snapshot.json"
    unrelated.write_text("keep")
    receipt = tmp_path / ".nx-mcp/operations/operation.json"
    receipt.parent.mkdir()
    receipt.write_text("keep")
    preview = maintain(store.root, max_age_seconds=10)
    assert preview["selected_count"] == 1 and path.exists()
    result = maintain(store.root, apply=True, max_age_seconds=10)
    assert result["applied"] and not path.exists()
    assert unrelated.read_text() == receipt.read_text() == "keep"
    current = store.put({"current": "x" * 100})
    result = maintain(store.root, apply=True, max_bytes=1, protect=current)
    assert result["selected_count"] == 0
    with pytest.raises(ValueError):
        maintain(store.root, max_bytes=0)
    with pytest.raises(OSError):
        store.put({"large": "x" * 2000})


@pytest.mark.asyncio
async def test_filtered_snapshot_pages_reuse_single_native_read(tmp_path):
    refs = [
        {"id": str(i), "kind": "feature", "name": f"Bolt {i}", "part_id": "p"} for i in range(31)
    ]
    bridge = AsyncMock()
    bridge.call.return_value = {"objects": refs, "units": "mm"}
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True, surface="agent")
    first = await server.call_tool(
        "nx_inspect", {"tool": "nx_list_features", "name_contains": "bolt", "limit": 10}
    )
    value = first.structuredContent
    assert not first.isError and value["count"] == 10 and value["total_count"] == 31
    second = await server.call_tool(
        "nx_inspect",
        {
            "tool": "nx_list_features",
            "result_id": value["result_id"],
            "name_contains": "bolt",
            "offset": 10,
            "limit": 10,
        },
    )
    assert second.structuredContent["items"][0]["id"] == "10"
    assert bridge.call.await_count == 1
    assert (await server.call_tool("nx_inspect", {"tool": "nx_extrude"})).isError
    assert (
        await server.call_tool(
            "nx_inspect", {"tool": "nx_list_sketches", "result_id": value["result_id"]}
        )
    ).isError
    bad = await server.call_tool("nx_inspect", {"tool": "nx_list_features", "collection": "faces"})
    assert bad.isError
    filtered = await server.call_tool(
        "nx_inspect",
        {"tool": "nx_list_features", "result_id": value["result_id"], "kind": "sketch"},
    )
    assert filtered.structuredContent["count"] == 0


@pytest.mark.asyncio
async def test_annotation_capture_collects_all_pages_and_preserves_frame(tmp_path):
    bridge = AsyncMock()
    bridge.call.side_effect = [
        {
            "items": [{"object": {"id": "a", "name": "First", "kind": "annotation"}}],
            "total": 2,
            "next_offset": 1,
            "position_frame": "sheet",
        },
        {
            "items": [{"object": {"id": "b", "name": "Second", "kind": "annotation"}}],
            "total": 2,
            "next_offset": None,
        },
    ]
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True, surface="agent")
    response = await server.call_tool(
        "nx_inspect", {"tool": "nx_list_annotations", "name_contains": "second"}
    )
    assert not response.isError
    assert response.structuredContent["total_count"] == 1
    assert response.structuredContent["unfiltered_count"] == 2
    assert response.structuredContent["coordinate_frame"] == "sheet"


@pytest.mark.asyncio
async def test_cleanup_tool_defaults_to_preview(tmp_path):
    store = ResultStore(tmp_path)
    key = store.put({"large": "x" * 100})
    server = create_server(
        AsyncMock(), Workspace(tmp_path), enable_experimental=True, surface="agent"
    )
    response = await server.call_tool("nx_result_cleanup", {"max_bytes": 1})
    assert response.structuredContent["selected_count"] == 1
    assert store.get(key)
    response = await server.call_tool("nx_result_cleanup", {"max_bytes": 1, "dry_run": False})
    assert response.structuredContent["applied"]
    with pytest.raises(FileNotFoundError):
        store.get(key)


def test_backend_page_cardinality_is_not_silently_truncated():
    refs = [{"id": str(i), "kind": "part"} for i in range(38)]
    payload = compact_payload(
        {"parts": refs, "count": 38, "total_count": 38, "next_offset": None}, "result_id"
    )
    assert len(payload["parts"]) == payload["count"] == 38
    assert payload["next_offset"] is None
    assert "/parts" not in payload.get("omitted", {})
    assert len(compact_payload({"objects": refs}, "result_id")["objects"]) == 20


def test_next_action_does_not_repeat_the_current_inspection():
    from nx_mcp.agent_guidance import next_actions

    assert not next_actions("nx_list_topology", {"body": {"id": "body", "kind": "body"}})
    assert not next_actions("nx_download_file", {"path": "a.png", "sha256": "x"})


@pytest.mark.asyncio
async def test_spaced_discovery_and_optional_output_schema(tmp_path):
    server = create_server(
        AsyncMock(), Workspace(tmp_path), enable_experimental=True, surface="agent"
    )
    result = await server.call_tool(
        "nx_discover_tools",
        {"query": "create part", "include_schema": True, "include_output_schema": False},
    )
    rows = result.structuredContent["tools"]
    assert rows[0]["name"] == "nx_create_part"
    assert "inputSchema" in rows[0] and "outputSchema" not in rows[0]
    result = await server.call_tool(
        "nx_discover_tools", {"query": "nx_create_part", "include_schema": True}
    )
    assert "outputSchema" in result.structuredContent["tools"][0]
