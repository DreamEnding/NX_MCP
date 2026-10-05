"""Acceptance test for a dedicated runner with a live Siemens NX session."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from nx_mcp.bridge import (
    BridgeClient,
    BridgeDescriptor,
    DescriptorBridgeClient,
    default_descriptor_path,
)
from nx_mcp.provenance import git_state
from nx_mcp.real_smoke import _call, run, run_line_profile
from nx_mcp.runtime import NXToolError

pytestmark = pytest.mark.real_nx


@pytest.mark.asyncio
@pytest.mark.parametrize("units", ["mm", "inch"])
async def test_real_nx_status_units_survive_save_and_reopen(units) -> None:
    if os.environ.get("NX_MCP_REAL_NX") != "1":
        pytest.skip("requires a dedicated Siemens NX runner")
    prefix = os.environ.get("NX_MCP_REAL_NX_RUN_PREFIX", "pytest-real-nx")
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "nx_mcp.server"], env=dict(os.environ)
    )
    async with Client(parameters) as client:
        initial = await _call(client, "nx_status", {})
        assert initial["active_part"] is None, "Do not run against a user work part"
        assert initial["units"] is None
        path = f"{prefix}/units-{units}.prt"
        workspace = Path(os.environ["NX_MCP_WORKSPACE"]).resolve()
        assert not (workspace / path).exists()
        owned_id = None
        failed = False
        try:
            created = await _call(client, "nx_create_part", {"path": path, "units": units})
            owned_id = created["part"]["part_id"]
            status = await _call(client, "nx_status", {})
            assert status["active_part"]["part_id"] == owned_id
            assert status["units"] == units
            await _call(client, "nx_save_part", {})
            await _call(client, "nx_close_part", {"save": False})
            owned_id = None
            closed = await _call(client, "nx_status", {})
            assert closed["active_part"] is None
            assert closed["units"] is None
            reopened = await _call(client, "nx_open_part", {"path": path})
            owned_id = reopened["part"]["part_id"]
            assert (await _call(client, "nx_status", {}))["units"] == units
        except BaseException:
            failed = True
            raise
        finally:
            if owned_id is not None:
                try:
                    current = (await _call(client, "nx_status", {}))["active_part"]
                    assert current is not None and current["part_id"] == owned_id
                    await _call(client, "nx_close_part", {"save": False})
                except Exception as cleanup_error:
                    if not failed:
                        raise
                    print(f"Units acceptance cleanup failed: {cleanup_error}", file=sys.stderr)


@pytest.mark.asyncio
async def test_real_nx_certified_workflow(record_testsuite_property) -> None:
    if os.environ.get("NX_MCP_REAL_NX") != "1":
        pytest.skip("requires NX_MCP_REAL_NX=1 on a dedicated Siemens NX runner")

    workspace = Path(os.environ["NX_MCP_WORKSPACE"]).resolve()
    iterations = int(os.environ.get("NX_MCP_REAL_NX_ITERATIONS", "20"))
    prefix = os.environ.get("NX_MCP_REAL_NX_RUN_PREFIX", "pytest-real-nx")
    git_commit, git_dirty = git_state(Path(__file__).resolve().parents[1])
    record_testsuite_property("git_commit", git_commit)
    record_testsuite_property("git_dirty", str(git_dirty).lower())
    record_testsuite_property("acceptance_iterations", iterations)
    record_testsuite_property("run_prefix", prefix)
    workspace.mkdir(parents=True, exist_ok=True)

    results = await run(workspace, iterations, prefix)

    assert len(results) == iterations
    assert all(result["nx_version"] for result in results)
    expected = os.environ.get("NX_MCP_EXPECTED_BRIDGE")
    assert all(
        result["bridge_implementation"] in {"python_batch", "csharp_gui"} for result in results
    )
    if expected:
        assert all(result["bridge_implementation"] == expected for result in results)
    assert all(result["feature_id"] and result["body_id"] for result in results)
    record_testsuite_property("bridge_implementation", results[0]["bridge_implementation"])
    record_testsuite_property("nx_version", results[0]["nx_version"])


@pytest.mark.asyncio
async def test_real_nx_sketch_line() -> None:
    if os.environ.get("NX_MCP_REAL_NX") != "1":
        pytest.skip("requires a dedicated Siemens NX runner")
    workspace = Path(os.environ["NX_MCP_WORKSPACE"]).resolve()
    prefix = os.environ.get("NX_MCP_REAL_NX_RUN_PREFIX", "pytest-real-nx")
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "nx_mcp.server"], env=dict(os.environ)
    )
    async with Client(parameters) as client:
        await run_line_profile(client, workspace, prefix)


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
