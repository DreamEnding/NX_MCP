"""Acceptance test for a dedicated runner with a live Siemens NX session."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nx_mcp.bridge import (
    BridgeClient,
    BridgeDescriptor,
    DescriptorBridgeClient,
    default_descriptor_path,
)
from nx_mcp.real_smoke import run
from nx_mcp.runtime import NXToolError

pytestmark = pytest.mark.real_nx


@pytest.mark.asyncio
async def test_real_nx_certified_workflow() -> None:
    if os.environ.get("NX_MCP_REAL_NX") != "1":
        pytest.skip("requires NX_MCP_REAL_NX=1 on a dedicated Siemens NX runner")

    workspace = Path(os.environ["NX_MCP_WORKSPACE"]).resolve()
    iterations = int(os.environ.get("NX_MCP_REAL_NX_ITERATIONS", "20"))
    prefix = os.environ.get("NX_MCP_REAL_NX_RUN_PREFIX", "pytest-real-nx")
    workspace.mkdir(parents=True, exist_ok=True)

    results = await run(workspace, iterations, prefix)

    assert len(results) == iterations
    assert all(result["nx_version"] for result in results)
    assert all(result["feature_id"] and result["body_id"] for result in results)


@pytest.mark.asyncio
async def test_real_nx_rejects_unsafe_requests() -> None:
    if os.environ.get("NX_MCP_REAL_NX") != "1":
        pytest.skip("requires a dedicated Siemens NX runner")
    client = DescriptorBridgeClient()
    status = await client.call("nx_status", {})
    assert status["active_part"] is None, "Do not run acceptance against a user work part"
    descriptor = BridgeDescriptor.read(default_descriptor_path())
    unauthorized = BridgeClient(descriptor.host, descriptor.port, token="invalid-token")
    with pytest.raises(NXToolError) as caught:
        await unauthorized.call("nx_status", {})
    assert caught.value.code == "NX_AUTH_FAILED"
    for method, params, code in [
        ("nx_save_part", {}, "NX_NO_WORK_PART"),
        (
            "nx_open_part",
            {"path": str(Path(os.environ["NX_MCP_WORKSPACE"]).parent / "outside.prt")},
            "NX_PATH_OUTSIDE_WORKSPACE",
        ),
        ("nx_create_sketch", {"plane": "invalid"}, "NX_INVALID_ARGUMENT"),
    ]:
        with pytest.raises(NXToolError) as caught:
            await client.call(method, params)
        assert caught.value.code == code
    prefix = os.environ.get("NX_MCP_REAL_NX_RUN_PREFIX", "pytest-real-nx")
    path = Path(os.environ["NX_MCP_WORKSPACE"]) / prefix / "negative.prt"
    assert not path.exists()
    created = await client.call("nx_create_part", {"path": str(path)})
    owned_id = created["part"]["part_id"]
    try:
        with pytest.raises(NXToolError) as caught:
            await client.call("nx_finish_sketch", {"sketch_id": created["part"]["id"]})
        assert caught.value.code == "NX_OBJECT_TYPE_MISMATCH"
        sketch = await client.call("nx_create_sketch", {})
        sketch_id = sketch["object"]["id"]
        with pytest.raises(NXToolError) as caught:
            await client.call(
                "nx_sketch_rectangle",
                {
                    "sketch_id": sketch_id,
                    "corner1": {"x": 0, "y": 0},
                    "corner2": {"x": 0, "y": 1},
                },
            )
        assert caught.value.code == "NX_INVALID_ARGUMENT"
        await client.call("nx_undo", {})
        with pytest.raises(NXToolError) as caught:
            await client.call("nx_finish_sketch", {"sketch_id": sketch_id})
        assert caught.value.code == "NX_OBJECT_STALE"
    finally:
        current = (await client.call("nx_status", {}))["active_part"]
        if current is not None and current["part_id"] == owned_id:
            await client.call("nx_close_part", {"save": False})
