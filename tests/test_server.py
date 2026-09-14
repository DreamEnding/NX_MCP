"""Tests for the MCP server entry point."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from mcp.client import Client

from nx_mcp.certified import CERTIFIED_TOOL_NAMES
from nx_mcp.server import create_server

pytestmark = pytest.mark.integration


class StubBridge:
    async def call(self, method: str, params: dict):
        return {
            "connected": True,
            "nx_version": "NX test",
            "bridge_protocol": 1,
            "active_part": None,
        }


class RecordingBridge:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def call(self, method: str, params: dict):
        self.calls.append((method, params))
        return self.response


def test_server_lists_only_certified_tools_without_legacy_or_nx_imports():
    script = textwrap.dedent(
        """
        import asyncio
        import json
        import sys

        from nx_mcp.server import create_server

        async def main():
            server = create_server()
            tools = await server.list_tools()
            forbidden = [
                name
                for name in (
                    "NXOpen",
                    "nx_mcp.experimental",
                    "nx_mcp.nx_session",
                    "nx_mcp.response",
                    "nx_mcp.tools",
                    "nx_mcp.tools.registry",
                )
                if name in sys.modules
            ]
            print(json.dumps({"tools": [tool.name for tool in tools], "forbidden": forbidden}))

        asyncio.run(main())
        """
    )
    environment = os.environ | {
        "NX_MCP_ENABLE_EXPERIMENTAL": "0",
        "NX_MCP_ENABLE_JOURNAL": "0",
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload["tools"]) == CERTIFIED_TOOL_NAMES
    assert payload["forbidden"] == []


@pytest.mark.asyncio
async def test_server_reads_workspace_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("NX_MCP_WORKSPACE", str(tmp_path))
    bridge = RecordingBridge(
        {
            "part": {"id": "part-1", "kind": "part", "name": "bracket", "part_id": "part-1"},
            "message": "created",
        }
    )
    server = create_server(bridge)

    async with Client(server) as client:
        result = await client.call_tool(
            "nx_create_part", {"path": "parts/bracket.prt", "units": "mm"}
        )

    assert result.is_error is False
    assert bridge.calls == [
        (
            "nx_create_part",
            {"path": str(tmp_path / "parts" / "bracket.prt"), "units": "mm"},
        )
    ]


@pytest.mark.asyncio
async def test_server_reads_experimental_and_journal_opt_ins_from_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("NX_MCP_ENABLE_EXPERIMENTAL", "1")
    monkeypatch.setenv("NX_MCP_ENABLE_JOURNAL", "1")
    server = create_server(StubBridge())

    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {"nx_blend", "nx_run_journal", "nx_record_start", "nx_record_stop"} <= names


@pytest.mark.asyncio
async def test_server_keeps_journal_tools_disabled_without_experimental_opt_in(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("NX_MCP_ENABLE_JOURNAL", "1")
    server = create_server(StubBridge())

    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert names == CERTIFIED_TOOL_NAMES
