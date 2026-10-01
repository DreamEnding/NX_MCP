"""Local lifecycle doubles do not constitute real GUI restart evidence."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from nx_mcp.runtime import NXToolError
from tests import test_real_nx_restart as restart

pytestmark = pytest.mark.fake_nx


@pytest.fixture
def lifecycle(monkeypatch):
    def configure(
        *,
        pid=2,
        token="new",
        initial_bridge="csharp_gui",
        active_part=None,
        old_exited=True,
        new_version="v2206",
        new_build="2206.9101",
        new_image="D:/NX2206/NXBIN/ugraf.exe",
    ):
        monkeypatch.setenv("NX_MCP_REAL_NX_GUI_RESTART", "1")
        monkeypatch.setenv("NX_MCP_REAL_NX_RUN_PREFIX", "restart-case")
        monkeypatch.setattr(restart, "git_state", lambda checkout: ("a" * 40, False))
        configure.records = []
        configure.record = lambda name, value: configure.records.append((name, value))
        monkeypatch.setattr(restart.sys, "platform", "win32")
        states = iter([False, True])
        path = SimpleNamespace(exists=lambda: next(states))
        descriptors = iter(
            [SimpleNamespace(pid=1, token="old"), SimpleNamespace(pid=pid, token=token)]
        )
        monkeypatch.setattr(restart, "default_descriptor_path", lambda: path)
        monkeypatch.setattr(restart.BridgeDescriptor, "read", lambda path: next(descriptors))

        @contextmanager
        def observe_process(pid):
            yield SimpleNamespace(
                image=Path("D:/NX2206/NXBIN/ugraf.exe" if pid == 1 else new_image),
                build="2206.9101" if pid == 1 else new_build,
                exited=lambda: old_exited if pid == 1 else False,
            )

        monkeypatch.setattr(restart, "_observe_process", observe_process, raising=False)
        calls = []

        class Client:
            async def call(self, method, params):
                calls.append((method, params))
                if len(calls) == 2:
                    raise NXToolError(
                        "NX_BRIDGE_UNAVAILABLE",
                        "Stopped",
                        details={"execution_state": "not_started"},
                    )
                return {
                    "connected": True,
                    "bridge_implementation": initial_bridge,
                    "active_part": active_part,
                    "nx_version": "v2206" if len(calls) == 1 else new_version,
                }

        client = Client()
        monkeypatch.setattr(restart, "DescriptorBridgeClient", lambda: client)
        return calls

    return configure


@pytest.mark.asyncio
async def test_gui_restart_checks_unavailability_and_reuses_client(lifecycle):
    calls = lifecycle()
    await restart.test_real_nx_gui_client_reconnects_after_operator_restart(lifecycle.record)
    assert calls == [("nx_status", {})] * 3
    assert dict(lifecycle.records) == {
        "git_commit": "a" * 40,
        "git_dirty": "false",
        "run_prefix": "restart-case",
        "bridge_implementation": "csharp_gui",
        "nx_version": "v2206",
        "nx_build": "2206.9101",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"pid": 1}, "Restart NX itself"),
        ({"token": "old"}, "rotate the token"),
        ({"active_part": {"part_id": "user"}}, "user work part"),
    ],
)
async def test_gui_restart_cannot_certify_incomplete_or_unsafe_restart(lifecycle, options, message):
    lifecycle(**options)
    with pytest.raises(AssertionError, match=message):
        await restart.test_real_nx_gui_client_reconnects_after_operator_restart(lifecycle.record)


@pytest.mark.asyncio
async def test_gui_restart_cannot_use_python_bridge_evidence(lifecycle):
    lifecycle(initial_bridge="python_batch")
    with pytest.raises(AssertionError):
        await restart.test_real_nx_gui_client_reconnects_after_operator_restart(lifecycle.record)


@pytest.mark.asyncio
async def test_gui_restart_rejects_new_instance_while_original_process_is_alive(lifecycle):
    lifecycle(old_exited=False)
    with pytest.raises(AssertionError, match="Old NX process is still running"):
        await restart.test_real_nx_gui_client_reconnects_after_operator_restart(lifecycle.record)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"new_version": "v2506"}, "NX release changed"),
        ({"new_build": "2206.9200"}, "NX build changed"),
        ({"new_image": "D:/OtherNX2206/NXBIN/ugraf.exe"}, "NX installation changed"),
    ],
)
async def test_gui_restart_rejects_a_different_nx_environment(lifecycle, options, message):
    lifecycle(**options)
    with pytest.raises(AssertionError, match=message):
        await restart.test_real_nx_gui_client_reconnects_after_operator_restart(lifecycle.record)


@pytest.mark.skipif(restart.sys.platform != "win32", reason="Windows process handles")
def test_native_process_observer_keeps_identity_until_process_exits():
    import subprocess
    import sys

    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.readline()"],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        with restart._observe_process(process.pid) as observed:
            assert observed.image.name.lower() == "python.exe"
            assert observed.build.startswith(f"{sys.version_info.major}.{sys.version_info.minor}.")
            assert not observed.exited()
            process.communicate(b"exit\n", timeout=10)
            assert observed.exited()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdin.close()


@pytest.mark.asyncio
async def test_gui_restart_rejects_changed_candidate(lifecycle, monkeypatch):
    lifecycle()
    revisions = iter([("a" * 40, False), ("b" * 40, False)])
    monkeypatch.setattr(restart, "git_state", lambda checkout: next(revisions))
    with pytest.raises(AssertionError, match="Candidate changed"):
        await restart.test_real_nx_gui_client_reconnects_after_operator_restart(lifecycle.record)
