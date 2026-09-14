"""Restart acceptance owns its NX processes; run after the ordinary bridge stops."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from nx_mcp.bridge import BridgeDescriptor, DescriptorBridgeClient, default_descriptor_path
from nx_mcp.runtime import NXToolError

pytestmark = pytest.mark.real_nx


@asynccontextmanager
async def _running_bridge(workspace: Path):
    descriptor_path = default_descriptor_path()
    if descriptor_path.exists():
        raise RuntimeError("Existing bridge descriptor; refusing to replace another session")
    stop_file = workspace / f"stop-{uuid4().hex}"
    environment = dict(os.environ)
    environment["NX_MCP_BRIDGE_STOP_FILE"] = str(stop_file)
    script = Path(__file__).resolve().parents[1] / "examples" / "start_nx_bridge.py"
    with (workspace / f"restart-{uuid4().hex}.log").open("wb") as log:
        process = subprocess.Popen(
            [environment["NX_RUN_JOURNAL"], "-nx", str(script)],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            deadline = asyncio.get_running_loop().time() + 120
            while not descriptor_path.exists():
                if process.poll() is not None:
                    raise RuntimeError("Owned NX journal exited before publishing its descriptor")
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError("NX bridge startup timed out")
                await asyncio.sleep(0.2)
            yield BridgeDescriptor.read(descriptor_path)
        finally:
            stop_file.touch()
            deadline = asyncio.get_running_loop().time() + 120
            while process.poll() is None and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.2)
            if process.poll() is None:
                raise RuntimeError(f"Owned NX journal PID {process.pid} did not stop cleanly")
            if process.returncode != 0:
                raise RuntimeError(f"Owned NX journal exited with code {process.returncode}")


@pytest.mark.asyncio
async def test_real_nx_client_reconnects_after_restart() -> None:
    if os.environ.get("NX_MCP_REAL_NX") != "1" or sys.platform != "win32":
        pytest.skip("requires a dedicated Windows Siemens NX runner")
    if not os.environ.get("NX_RUN_JOURNAL"):
        pytest.skip("requires NX_RUN_JOURNAL")
    workspace = Path(os.environ["NX_MCP_WORKSPACE"]).resolve()
    client = DescriptorBridgeClient()
    async with _running_bridge(workspace) as first:
        assert (await client.call("nx_status", {}))["connected"]
    assert not default_descriptor_path().exists()
    with pytest.raises(NXToolError) as caught:
        await client.call("nx_status", {})
    assert caught.value.details == {"execution_state": "not_started"}
    async with _running_bridge(workspace) as second:
        token_rotated = second.token != first.token
        assert token_rotated, "Restart must rotate the token"
        assert (await client.call("nx_status", {}))["connected"]
    assert not default_descriptor_path().exists()
