"""SDK v2 negotiation and real stdio framing without a Siemens NX session."""

import json
import os
import sys

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from nx_mcp import __version__
from nx_mcp.bridge import BRIDGE_PROTOCOL_VERSION, BridgeDescriptor, BridgeServer
from nx_mcp.certified import CERTIFIED_TOOL_NAMES
from nx_mcp.server import create_server
from tests.test_certified_server import StubBridge

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy", "2026-07-28"])
async def test_sdk_v2_negotiates_and_preserves_tool_contract(mode):
    async with Client(create_server(StubBridge()), mode=mode) as client:
        assert client.protocol_version == ("2025-11-25" if mode == "legacy" else "2026-07-28")
        # A pinned modern version skips discovery and has no server identity yet.
        if mode != "2026-07-28":
            assert client.server_info is not None
            assert client.server_info.name == "nx-mcp"
            assert client.server_info.version == __version__
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == CERTIFIED_TOOL_NAMES
        assert all(tool.output_schema is not None for tool in tools)
        result = await client.call_tool("nx_status", {})
        assert not result.is_error
        assert result.structured_content["bridge_protocol"] == BRIDGE_PROTOCOL_VERSION == 1
        wire = result.model_dump(by_alias=True)
        assert "structuredContent" in wire
        assert "structured_content" not in wire
        assert "isError" in wire


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy", "2026-07-28"])
async def test_stdio_entrypoint_negotiates_without_nx(tmp_path, mode):
    environment = os.environ | {
        "LOCALAPPDATA": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path),
        "NX_MCP_WORKSPACE": str(tmp_path),
        "NX_MCP_ENABLE_EXPERIMENTAL": "0",
        "NX_MCP_ENABLE_JOURNAL": "0",
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "nx_mcp.server"],
        env=environment,
    )
    async with Client(parameters, mode=mode, read_timeout_seconds=15) as client:
        assert client.protocol_version == ("2025-11-25" if mode == "legacy" else "2026-07-28")
        assert {tool.name for tool in (await client.list_tools()).tools} == CERTIFIED_TOOL_NAMES
        result = await client.call_tool("nx_status", {})
        assert result.is_error
        # MCPServer prefixes ToolError text with the tool name, as SDK v1 did.
        text = result.content[0].text
        error = json.loads(text[text.index("{") :])
        assert error["code"] == "NX_BRIDGE_UNAVAILABLE"
        assert error["details"] == {"execution_state": "not_started"}
        rejected = await client.call_tool("nx_open_part", {"path": "../outside.prt"})
        assert rejected.is_error
        assert "NX_PATH_OUTSIDE_WORKSPACE" in rejected.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_stdio_structured_success_through_bridge(tmp_path, mode):
    status = {"connected": True, "nx_version": "fake", "bridge_protocol": 1, "active_part": None}
    bridge = BridgeServer(lambda method, params: status, token="test-token")
    descriptor = BridgeDescriptor.create(bridge.port, "fake", token="test-token")
    descriptor.write(tmp_path / "nx-mcp" / "bridge.json")
    environment = os.environ | {
        "LOCALAPPDATA": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path),
        "NX_MCP_WORKSPACE": str(tmp_path),
        "NX_MCP_ENABLE_EXPERIMENTAL": "0",
        "NX_MCP_ENABLE_JOURNAL": "0",
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "nx_mcp.server"],
        env=environment,
    )
    bridge.start()
    try:
        async with Client(parameters, mode=mode, read_timeout_seconds=15) as client:
            result = await client.call_tool("nx_status", {})
            assert not result.is_error
            assert result.structured_content == {"status": "success", **status}
            text = json.loads(result.content[0].text)
            assert text == result.structured_content
    finally:
        bridge.stop()
