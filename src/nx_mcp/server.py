"""MCP server entry point — stdio transport."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from nx_mcp.bridge import DescriptorBridgeClient
from nx_mcp.certified import BridgeCaller, create_certified_server
from nx_mcp.workspace import Workspace

logger = logging.getLogger("nx_mcp")


def create_server(
    bridge: BridgeCaller | None = None,
    workspace: Workspace | None = None,
    *,
    enable_experimental: bool | None = None,
    enable_journal: bool | None = None,
    surface: str | None = None,
) -> FastMCP:
    """Create the certified v0.2 MCP server."""
    if workspace is None and (workspace_root := os.environ.get("NX_MCP_WORKSPACE")):
        workspace = Workspace(Path(workspace_root))
    if enable_experimental is None:
        enable_experimental = os.environ.get("NX_MCP_ENABLE_EXPERIMENTAL") == "1"
    if enable_journal is None:
        enable_journal = os.environ.get("NX_MCP_ENABLE_JOURNAL") == "1"
    surface = surface or os.environ.get("NX_MCP_SURFACE", "full")
    if surface not in {"full", "agent"}:
        raise ValueError("NX_MCP_SURFACE must be full or agent")
    if surface == "agent" and (not enable_experimental or workspace is None):
        raise ValueError("Agent surface requires experimental integration and workspace")
    server = create_certified_server(
        bridge or DescriptorBridgeClient(),
        workspace,
        enable_experimental=enable_experimental,
        enable_journal=enable_experimental and enable_journal,
    )

    if surface == "agent":
        from nx_mcp.agent_surface import configure

        configure(server, workspace)
    return server


async def async_main() -> None:
    """Run the MCP server with stdio transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    server = create_server()
    logger.info("NX MCP Server starting (stdio transport)")
    await server.run_stdio_async()


def main() -> None:
    """Entry point for the nx-mcp console script."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
