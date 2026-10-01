"""Restart acceptance owns its NX processes; run after the ordinary bridge stops."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from nx_mcp.bridge import BridgeDescriptor, DescriptorBridgeClient, default_descriptor_path
from nx_mcp.provenance import file_version, git_state
from nx_mcp.runtime import NXToolError

pytestmark = pytest.mark.real_nx


@contextmanager
def _observe_process(pid: int):
    """Keep a handle to the original process, including after its PID is recycled."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION; never request mutation rights.
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        image = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())

        def exited():
            state = kernel.WaitForSingleObject(handle, 0)
            if state == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            return state == 0

        path = Path(image.value)
        yield SimpleNamespace(image=path, build=file_version(path), exited=exited)
    finally:
        kernel.CloseHandle(handle)


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
            [environment["NX_RUN_JOURNAL"], str(script)],
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
async def test_real_nx_client_reconnects_after_restart(record_testsuite_property) -> None:
    if os.environ.get("NX_MCP_REAL_NX") != "1" or sys.platform != "win32":
        pytest.skip("requires a dedicated Windows Siemens NX runner")
    if not os.environ.get("NX_RUN_JOURNAL"):
        pytest.skip("requires NX_RUN_JOURNAL")
    workspace = Path(os.environ["NX_MCP_WORKSPACE"]).resolve()
    client = DescriptorBridgeClient()
    async with _running_bridge(workspace) as first:
        status = await client.call("nx_status", {})
        assert status["connected"]
        assert status["bridge_implementation"] == "python_batch"
        first_version = status["nx_version"]
        first_build = file_version(Path(os.environ["NX_RUN_JOURNAL"]).with_name("ugraf.exe"))
        candidate = _record_environment(record_testsuite_property, status, first_build)
    assert not default_descriptor_path().exists()
    with pytest.raises(NXToolError) as caught:
        await client.call("nx_status", {})
    assert caught.value.details == {"execution_state": "not_started"}
    async with _running_bridge(workspace) as second:
        token_rotated = second.token != first.token
        assert token_rotated, "Restart must rotate the token"
        status = await client.call("nx_status", {})
        assert status["connected"]
        assert status["bridge_implementation"] == "python_batch"
        assert status["nx_version"] == first_version, "NX release changed during restart"
        assert (
            file_version(Path(os.environ["NX_RUN_JOURNAL"]).with_name("ugraf.exe")) == first_build
        ), "NX build changed during restart"
    assert not default_descriptor_path().exists()
    assert git_state(Path(__file__).resolve().parents[1]) == candidate, (
        "Candidate changed during restart"
    )


def _record_environment(record_testsuite_property, status, nx_build):
    candidate = git_state(Path(__file__).resolve().parents[1])
    for name, value in {
        "git_commit": candidate[0],
        "git_dirty": str(candidate[1]).lower(),
        "run_prefix": os.environ.get("NX_MCP_REAL_NX_RUN_PREFIX", "pytest-real-nx"),
        "bridge_implementation": status["bridge_implementation"],
        "nx_version": status["nx_version"],
        "nx_build": nx_build,
    }.items():
        record_testsuite_property(name, value)
    return candidate


@pytest.mark.asyncio
async def test_real_nx_gui_client_reconnects_after_operator_restart(
    record_testsuite_property,
) -> None:
    """An operator owns GUI lifecycle; this test observes it without closing NX."""
    if os.environ.get("NX_MCP_REAL_NX_GUI_RESTART") != "1" or sys.platform != "win32":
        pytest.skip("requires an operator and NX_MCP_REAL_NX_GUI_RESTART=1")
    client = DescriptorBridgeClient()
    status = await client.call("nx_status", {})
    assert status["bridge_implementation"] == "csharp_gui"
    assert status["active_part"] is None, "Do not restart a user work part"
    descriptor_path = default_descriptor_path()
    first = BridgeDescriptor.read(descriptor_path)
    first_version = status["nx_version"]

    async def wait_for(predicate):
        deadline = asyncio.get_running_loop().time() + 180
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("Operator GUI restart phase timed out after 180 seconds")
            await asyncio.sleep(0.2)

    with _observe_process(first.pid) as original:
        candidate = _record_environment(record_testsuite_property, status, original.build)
        print(
            "Stop the GUI bridge; wait for the next instruction before starting it again.",
            flush=True,
        )
        await wait_for(lambda: not descriptor_path.exists())
        with pytest.raises(NXToolError) as caught:
            await client.call("nx_status", {})
        assert caught.value.details == {"execution_state": "not_started"}
        print(
            "Close NX completely, restart NX, then load the same GUI add-in and configuration.",
            flush=True,
        )
        await wait_for(descriptor_path.exists)
        second = BridgeDescriptor.read(descriptor_path)
        token_rotated = second.token != first.token
        process_changed = second.pid != first.pid
        assert token_rotated, "GUI restart must rotate the token"
        assert process_changed, "Restart NX itself; reloading the add-in is insufficient"
        assert original.exited(), "Old NX process is still running"
        with _observe_process(second.pid) as restarted:
            same_installation = restarted.image == original.image
            assert same_installation, "NX installation changed during restart"
            assert restarted.build == original.build, "NX build changed during restart"
            status = await client.call("nx_status", {})
            assert status["connected"]
            assert status["bridge_implementation"] == "csharp_gui"
            assert status["active_part"] is None
            assert status["nx_version"] == first_version, "NX release changed during restart"
        assert git_state(Path(__file__).resolve().parents[1]) == candidate, (
            "Candidate changed during restart"
        )
