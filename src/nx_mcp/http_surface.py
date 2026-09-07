"""Serve compatible and compact profiles against one serial NX bridge."""

from contextlib import AsyncExitStack, asynccontextmanager

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from nx_mcp.server import create_server


def create_app(bridge, workspace=None, host="192.168.52.10", port=8765):
    servers = [create_server(bridge, workspace, surface=profile) for profile in ("full", "agent")]
    for server in servers:
        server.settings.streamable_http_path = "/mcp"
        server.settings.json_response = True
        server.settings.stateless_http = True
        server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{address}:{port}" for address in (host, "127.0.0.1", "localhost")],
            allowed_origins=[
                f"http://{address}:{port}" for address in (host, "127.0.0.1", "localhost")
            ],
        )
    apps = [server.streamable_http_app() for server in servers]

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for server in servers:
                await stack.enter_async_context(server.session_manager.run())
            yield

    return Starlette(
        routes=[Mount("/agent", app=apps[1]), Mount("/", app=apps[0])], lifespan=lifespan
    )


def main():
    import os
    from pathlib import Path

    import uvicorn

    from nx_mcp.bridge import DescriptorBridgeClient

    descriptor = Path(
        os.environ.get(
            "NX_MCP_BRIDGE_DESCRIPTOR",
            str(Path(os.environ["LOCALAPPDATA"]) / "nx-mcp" / "bridge.json"),
        )
    )
    app = create_app(DescriptorBridgeClient(descriptor, timeout=120))
    uvicorn.run(app, host="0.0.0.0", port=8765)


if __name__ == "__main__":
    main()
