"""The explicitly certified v0.2 MCP tool surface."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Protocol

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError as MCPToolError
from pydantic import Field

from nx_mcp.contracts import (
    ExportResult,
    ExtrudeResult,
    NXToolError,
    ObjectListResult,
    ObjectResult,
    OperationResult,
    PartResult,
    Point2D,
    StatusResult,
)
from nx_mcp.workspace import Workspace, WorkspaceViolation


class BridgeCaller(Protocol):
    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


CERTIFIED_TOOL_NAMES = {
    "nx_status",
    "nx_create_part",
    "nx_open_part",
    "nx_save_part",
    "nx_close_part",
    "nx_export_step",
    "nx_list_sketches",
    "nx_list_bodies",
    "nx_list_features",
    "nx_create_sketch",
    "nx_sketch_line",
    "nx_sketch_rectangle",
    "nx_finish_sketch",
    "nx_extrude",
    "nx_undo",
    "nx_fit_view",
}


def create_certified_server(
    bridge: BridgeCaller,
    workspace: Workspace | None = None,
    *,
    enable_experimental: bool = False,
    enable_journal: bool = False,
) -> FastMCP:
    mcp = FastMCP(
        "nx-mcp",
        instructions="Siemens NX integration. Runtime support varies by NX version; no general certification is claimed.",
    )

    async def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return await bridge.call(method, params)
        except NXToolError as error:
            raise MCPToolError(json.dumps(error.as_dict(), ensure_ascii=False)) from error

    def resolve_path(path: str) -> str:
        if workspace is None:
            raise MCPToolError(
                json.dumps(
                    NXToolError(
                        "NX_WORKSPACE_NOT_CONFIGURED",
                        "NX_MCP_WORKSPACE must be configured before file operations are used.",
                    ).as_dict(),
                    ensure_ascii=False,
                )
            )
        try:
            return str(workspace.resolve(path))
        except WorkspaceViolation as error:
            tool_error = NXToolError("NX_PATH_OUTSIDE_WORKSPACE", str(error))
            raise MCPToolError(json.dumps(tool_error.as_dict(), ensure_ascii=False)) from error

    @mcp.tool()
    async def nx_status() -> StatusResult:
        """Report bridge, NX version, and active-part status."""
        return StatusResult(**await call("nx_status", {}))

    @mcp.tool()
    async def nx_create_part(path: str, units: Literal["mm", "inch"] = "mm") -> PartResult:
        """Create a part inside the configured workspace."""
        return PartResult(
            **await call("nx_create_part", {"path": resolve_path(path), "units": units})
        )

    @mcp.tool()
    async def nx_open_part(path: str) -> PartResult:
        """Open a part from the configured workspace."""
        return PartResult(**await call("nx_open_part", {"path": resolve_path(path)}))

    @mcp.tool()
    async def nx_save_part() -> OperationResult:
        """Save the active work part."""
        return OperationResult(**await call("nx_save_part", {}))

    @mcp.tool()
    async def nx_close_part(save: bool = True) -> OperationResult:
        """Close the active work part, optionally saving it first."""
        return OperationResult(**await call("nx_close_part", {"save": save}))

    @mcp.tool()
    async def nx_export_step(path: str) -> ExportResult:
        """Export the active work part as STEP inside the configured workspace."""
        return ExportResult(**await call("nx_export_step", {"path": resolve_path(path)}))

    @mcp.tool()
    async def nx_list_sketches() -> ObjectListResult:
        """List sketches in the active work part."""
        return ObjectListResult(**await call("nx_list_sketches", {}))

    @mcp.tool()
    async def nx_list_bodies() -> ObjectListResult:
        """List bodies in the active work part."""
        return ObjectListResult(**await call("nx_list_bodies", {}))

    @mcp.tool()
    async def nx_list_features() -> ObjectListResult:
        """List features in the active work part."""
        return ObjectListResult(**await call("nx_list_features", {}))

    @mcp.tool()
    async def nx_create_sketch(
        plane: Literal["XY", "XZ", "YZ"] = "XY", name: str | None = None
    ) -> ObjectResult:
        """Create and activate a sketch on a principal datum plane."""
        return ObjectResult(**await call("nx_create_sketch", {"plane": plane, "name": name}))

    @mcp.tool()
    async def nx_sketch_line(sketch_id: str, start: Point2D, end: Point2D) -> ObjectResult:
        """Add a line to an explicit sketch reference."""
        return ObjectResult(
            **await call(
                "nx_sketch_line",
                {"sketch_id": sketch_id, "start": start.model_dump(), "end": end.model_dump()},
            )
        )

    @mcp.tool()
    async def nx_sketch_rectangle(
        sketch_id: str, corner1: Point2D, corner2: Point2D
    ) -> ObjectListResult:
        """Add a rectangle to an explicit sketch reference."""
        return ObjectListResult(
            **await call(
                "nx_sketch_rectangle",
                {
                    "sketch_id": sketch_id,
                    "corner1": corner1.model_dump(),
                    "corner2": corner2.model_dump(),
                },
            )
        )

    @mcp.tool()
    async def nx_finish_sketch(sketch_id: str) -> ObjectResult:
        """Deactivate and finish an explicit sketch reference."""
        return ObjectResult(**await call("nx_finish_sketch", {"sketch_id": sketch_id}))

    @mcp.tool()
    async def nx_extrude(
        sketch_id: str,
        distance: Annotated[float, Field(gt=0)],
        reverse: bool = False,
    ) -> ExtrudeResult:
        """Extrude a sketch into a new body."""
        return ExtrudeResult(
            **await call(
                "nx_extrude",
                {"sketch_id": sketch_id, "distance": distance, "reverse": reverse},
            )
        )

    @mcp.tool()
    async def nx_undo() -> OperationResult:
        """Undo the last visible NX MCP operation."""
        return OperationResult(**await call("nx_undo", {}))

    @mcp.tool()
    async def nx_fit_view() -> OperationResult:
        """Fit the active modeling view."""
        return OperationResult(**await call("nx_fit_view", {}))

    if enable_experimental:
        from nx_mcp.experimental import add_experimental_tools

        add_experimental_tools(
            mcp,
            call,
            CERTIFIED_TOOL_NAMES,
            workspace,
            enable_journal=enable_journal,
        )

    if enable_experimental:
        from nx_mcp.integration_server import configure

        configure(mcp, bridge, workspace)

    return mcp
