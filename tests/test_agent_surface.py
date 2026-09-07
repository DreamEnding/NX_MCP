"""Agent-profile contracts across a real in-memory MCP boundary (no NX kernel)."""

from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from nx_mcp.agent_surface import ResultStore, compact
from nx_mcp.server import create_server
from nx_mcp.workspace import Workspace


def test_projection_preserves_identity_warnings_and_marks_truncation():
    omitted = {}
    ref = {
        "id": "opaque",
        "kind": "component",
        "part_id": "owner",
        "occurrence_path": "root/child",
        "journal_id": "long",
    }
    value = compact(
        {"objects": [ref] * 25, "warnings": ["w"] * 30, "data_base64": "secret"}, omitted=omitted
    )
    assert len(value["objects"]) == 20 and len(value["warnings"]) == 30
    assert value["objects"][0]["occurrence_path"] == "root/child"
    assert "journal_id" not in value["objects"][0]
    assert "data_base64" not in value and omitted["/objects"]["total_count"] == 25


def test_snapshot_persistence_and_path_rejection(tmp_path):
    store = ResultStore(tmp_path)
    identity = store.put({"answer": 42})
    assert ResultStore(tmp_path).get(identity) == {"answer": 42}
    with pytest.raises(ValueError):
        store.get("../../secret")


@pytest.mark.asyncio
async def test_discovery_defaults_dispatch_and_expansion(tmp_path):
    bridge = AsyncMock()
    bridge.call.return_value = {
        "status": "success",
        "parts": [],
        "count": 0,
        "warnings": [],
        "units": "mm",
    }
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True, surface="agent")
    async with create_connected_server_and_client_session(server) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 13
        assert "nx_extrude" not in {t.name for t in tools}
        result = await client.call_tool(
            "nx_discover_tools", {"query": "nx_extrude", "include_schema": True}
        )
        assert result.structuredContent["total_count"] == 1
        assert "distance" in result.structuredContent["tools"][0]["inputSchema"]["properties"]
        result = await client.call_tool("nx_list_open_parts", {})
        assert not result.isError
        assert bridge.call.call_args.args[1]["limit"] == 20
        assert bridge.call.call_args.args[1]["compact"] is True
        snapshot = await client.call_tool(
            "nx_result", {"result_id": result.structuredContent["result_id"], "field": "/parts"}
        )
        assert snapshot.structuredContent["items"] == []
        bridge.call.reset_mock()
        bad = await client.call_tool(
            "nx_invoke", {"tool": "nx_extrude", "arguments": {"bogus": True}}
        )
        assert bad.isError and not bridge.call.called
        good = await client.call_tool(
            "nx_invoke", {"tool": "nx_list_open_parts", "arguments": {}, "detail": "full"}
        )
        assert "result_id" not in good.structuredContent
        assert bridge.call.call_args.args[1]["limit"] is None


@pytest.mark.asyncio
async def test_artifact_metadata_resource_and_reserved_path(tmp_path):
    (tmp_path / "test.txt").write_bytes(b"binary bytes")
    server = create_server(
        AsyncMock(), Workspace(tmp_path), enable_experimental=True, surface="agent"
    )
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("nx_download_file", {"path": "test.txt"})
        assert not result.isError and "data_base64" not in result.structuredContent
        link = next(c for c in result.content if c.type == "resource_link")
        artifact = await client.read_resource(link.uri)
        assert artifact.contents[0].blob
        assert len((await client.list_resource_templates()).resourceTemplates) == 1
        bad = await client.call_tool("nx_download_file", {"path": ".nx-mcp/secret"})
        assert bad.isError


@pytest.mark.asyncio
async def test_failed_mutation_retains_outcome_and_operation_id(tmp_path):
    bridge = AsyncMock()
    bridge.call.return_value = {
        "status": "error",
        "code": "NX_TEST",
        "message": "failed",
        "warnings": ["important"],
        "operation_id": "test_operation",
        "mutation_outcome": "rolled_back",
    }
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True, surface="agent")
    result = await server.call_tool(
        "nx_invoke",
        {
            "tool": "nx_extrude",
            "arguments": {"sketch_id": "x", "distance": 1, "operation_id": "test_operation"},
        },
    )
    assert result.isError and result.structuredContent["mutation_outcome"] == "rolled_back"
    assert result.structuredContent["operation_id"] == "test_operation"


@pytest.mark.asyncio
async def test_compact_success_replay_receipt_and_full_snapshot(tmp_path):
    bridge = AsyncMock()
    ref = {"id": "a", "kind": "body", "part_id": "p", "session_id": "s"}
    bridge.call.return_value = {
        "status": "success",
        "units": "mm",
        "warnings": ["check"],
        "operation_id": "operation_123",
        "mutation_outcome": "committed",
        "bodies": [ref] * 25,
        "changes": {"created": [ref] * 25, "modified": None, "deleted": []},
    }
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True, surface="agent")
    result = await server.call_tool(
        "nx_invoke",
        {
            "tool": "nx_extrude",
            "arguments": {"sketch_id": "s", "distance": 2, "operation_id": "operation_123"},
        },
    )
    payload = result.structuredContent
    assert payload["change_counts"] == {"created": 25, "modified": None, "deleted": 0}
    assert payload["mutation_outcome"] == "committed" and payload["warnings"] == ["check"]
    bridge.call.reset_mock()
    expanded = await server.call_tool(
        "nx_result", {"result_id": payload["result_id"], "field": "/bodies", "offset": 20}
    )
    assert len(expanded.structuredContent["items"]) == 5
    assert expanded.structuredContent["items"][0]["session_id"] == "s"
    expanded = await server.call_tool(
        "nx_result", {"result_id": payload["result_id"], "detail": "full"}
    )
    assert len(expanded.structuredContent["value"]["bodies"]) == 25
    assert not bridge.call.called
    for args in [{"field": "bodies"}, {"field": "/absent"}, {"offset": -1}, {"limit": 101}]:
        bad = await server.call_tool("nx_result", {"result_id": payload["result_id"], **args})
        assert bad.isError


@pytest.mark.asyncio
async def test_storage_failure_does_not_relabel_committed_mutation(tmp_path, monkeypatch):
    bridge = AsyncMock()
    bridge.call.return_value = {
        "status": "success",
        "mutation_outcome": "committed",
        "operation_id": "operation_123",
    }
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True, surface="agent")

    def fail(*args):
        raise OSError("full disk")

    monkeypatch.setattr(ResultStore, "put", fail)
    result = await server.call_tool(
        "nx_invoke",
        {
            "tool": "nx_extrude",
            "arguments": {"sketch_id": "s", "distance": 2, "operation_id": "operation_123"},
        },
    )
    assert not result.isError and result.structuredContent["mutation_outcome"] == "committed"


@pytest.mark.asyncio
async def test_discovery_paging_and_argument_errors(tmp_path):
    server = create_server(
        AsyncMock(), Workspace(tmp_path), enable_experimental=True, surface="agent"
    )
    for args in [{"limit": 21}, {"offset": -1}, {"domain": "wrong"}]:
        assert (await server.call_tool("nx_discover_tools", args)).isError
    result = await server.call_tool(
        "nx_discover_tools", {"limit": 2, "offset": 2, "domain": "modeling"}
    )
    assert len(result.structuredContent["tools"]) == 2
    assert result.structuredContent["next_offset"] == 4
    assert (await server.call_tool("nx_invoke", {"tool": "missing", "arguments": {}})).isError
    assert (await server.call_tool("missing", {})).isError


def test_profile_validation(tmp_path):
    with pytest.raises(ValueError):
        create_server(surface="typo")
    with pytest.raises(ValueError):
        create_server(surface="agent", enable_experimental=False)


def test_dual_http_profiles(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from nx_mcp.http_surface import create_app

    monkeypatch.setenv("NX_MCP_ENABLE_EXPERIMENTAL", "1")
    app = create_app(AsyncMock(), Workspace(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for path, count in [("/mcp", 189), ("/agent/mcp", 13)]:
            response = client.post(
                path,
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert response.status_code == 200
            assert len(response.json()["result"]["tools"]) == count


@pytest.mark.asyncio
async def test_resource_traversal_and_size_limit(tmp_path):
    from mcp.server.fastmcp.exceptions import ResourceError

    server = create_server(
        AsyncMock(), Workspace(tmp_path), enable_experimental=True, surface="agent"
    )
    (tmp_path / ".nx-mcp").mkdir()
    (tmp_path / ".nx-mcp" / "secret").write_text("secret")
    large = tmp_path / "large.bin"
    with large.open("wb") as stream:
        stream.truncate(8 * 1024 * 1024 + 1)
    for uri in [
        "nx-artifact://workspace/.nx-mcp%2Fsecret",
        "nx-artifact://workspace/large.bin",
        "nx-artifact://workspace/..%2Fsecret",
    ]:
        with pytest.raises((ResourceError, ValueError)):
            await server.read_resource(uri)


@pytest.mark.asyncio
async def test_gateway_rejects_ignored_top_level_arguments(tmp_path):
    bridge = AsyncMock()
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True, surface="agent")
    result = await server.call_tool(
        "nx_invoke",
        {
            "tool": "nx_extrude",
            "arguments": {"sketch_id": "s", "distance": 2},
            "operation_id": "wrong_place",
        },
    )
    assert result.isError and not bridge.call.called


def test_benchmark_uses_shared_projection_and_exposes_usage_limitations():
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    script = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/benchmark_agent_surface.py")
    )
    encoding = SimpleNamespace(name="test", encode=lambda value, **kwargs: list(value))
    result = script["benchmark"](
        [
            {"state": "submitted"},
            {"state": "response", "result": {"status": "success", "data_base64": "x" * 10000}},
        ],
        encoding,
    )
    assert result["recorded_calls"] == 1 and result["binary_transfer_calls"] == 1
    assert result["compact_response_tokens"] < result["full_response_tokens"]
    assert result["provider_input_tokens"] == "unavailable"
    assert result["model_calls"] == 0
