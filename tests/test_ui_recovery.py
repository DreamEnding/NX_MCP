"""UI handoff and scheduler failures; Win32 calls are explicit seams."""

import ctypes
import os
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp import interactive
from nx_mcp.bridge import BridgeDescriptor
from nx_mcp.interactive import ControlPanel, InteractiveHost
from nx_mcp.runtime import NXToolError
from tests.fakes import Body, Collection

pytestmark = pytest.mark.fake_nx


@pytest.fixture
def host(rig, tmp_path, monkeypatch):
    lock = {"value": 0}
    enabled = {"value": True}
    ui = NS(
        AskLockStatus=lambda: lock["value"],
        CanOpenPart=lambda: not lock["value"],
        LockAccess=lambda: lock.update(value=1),
        UnlockAccess=lambda: lock.update(value=0),
    )
    rig.nx.UI = NS(Status=NS(Lock=1))
    h = InteractiveHost.__new__(InteractiveHost)
    h.__dict__.update(
        thread=threading.get_ident(),
        mode="manual",
        owns_lock=False,
        window_disabled=False,
        ui=ui,
        nx=rig.nx,
        session=rig.session,
        executor=rig.e,
        main_hwnd=1,
        native_thread=123,
        ticks=0,
        completed=0,
        running_method=None,
        operation_started=None,
        last_duration_seconds=None,
        last_method=None,
        last_error=None,
        started=time.time(),
        auto_start=False,
        busy=False,
        stopped=False,
        stop_requested=False,
        requested_mode=None,
        stop_file=tmp_path / "stop",
        state_dir=tmp_path,
        panel=NS(update=Mock(), close=Mock()),
        dispatcher=NS(drain=Mock(), stop=Mock()),
        server=NS(stop=Mock()),
        timer=7,
    )
    h.user = NS(
        EnableWindow=lambda _, v: enabled.update(value=v),
        IsWindowEnabled=lambda _: enabled["value"],
        KillTimer=Mock(),
    )
    monkeypatch.setattr(
        ctypes, "windll", NS(kernel32=NS(GetCurrentThreadId=lambda: 123)), raising=False
    )
    h.descriptor = BridgeDescriptor.create(12345, "test")
    h.descriptor_path = tmp_path / "bridge.json"
    h.descriptor.write(h.descriptor_path)
    return h


def test_manual_handoff_invalidates_references_and_checkpoints(host, rig):
    body = Body()
    rig.part.Bodies.append(body)
    ref = rig.ref(body)
    rig.e._checkpoint()
    assert host.control("agent")["actual_ui_lock"]
    host.control("manual")
    assert not host.owns_lock and host.status()["nx_window_input_enabled"]
    assert not rig.e._checkpoints
    with pytest.raises(NXToolError):
        rig.e.objects.resolve(ref)
    with pytest.raises(NXToolError):
        host.control("bad")
    host.ui.CanOpenPart = lambda: False
    with pytest.raises(NXToolError, match="dialog"):
        host.control("agent")
    host.thread = -1
    with pytest.raises(RuntimeError):
        host.control("manual")
    with pytest.raises(RuntimeError):
        host.execute("nx_status", {})


def test_operation_failure_relocks_ui_and_refresh_failure_is_warning(host, rig):
    with pytest.raises(NXToolError, match="paused"):
        host.execute("nx_list_bodies", {})
    host.execute("nx_ui_control", {"mode": "agent"})
    assert host.execute("nx_status", {})["ui"]["mode"] == "agent"
    rig.e._handlers["nx_test_fail"] = Mock(side_effect=RuntimeError("native error"))
    with pytest.raises(NXToolError):
        host.execute("nx_test_fail", {})
    assert host.ui.AskLockStatus() == 1
    rig.part.ModelingViews.WorkView.UpdateDisplay.side_effect = RuntimeError("refresh")
    result = host.execute("nx_list_bodies", {})
    assert not any("View refresh" in w for w in result["warnings"])
    rig.e._handlers["nx_test_edit"] = lambda: {}
    edited = host.execute("nx_test_edit", {})
    assert any("View refresh" in w for w in edited["warnings"])
    rig.part.DrawingSheets = Collection()
    rig.part.DrawingSheets.CurrentDrawingSheet = object()
    drawing = host.execute("nx_test_edit", {})
    assert not any("View refresh" in w for w in drawing["warnings"])
    host.ui.LockAccess = Mock(side_effect=RuntimeError("lock failure"))
    host.execute("nx_list_bodies", {})
    assert host.mode == "manual" and "Cannot restore" in host.last_error


def test_tick_modes_status_persistence_and_exception_handoff(host):
    host.auto_start = True
    host.tick()
    assert host.mode == "agent" and not host.auto_start
    host.requested_mode = "manual"
    host.ticks = 9
    host.tick()
    assert host.mode == "manual" and (host.state_dir / "ui-state.json").is_file()
    host.requested_mode = "bad"
    host.tick()
    assert "mode must" in host.last_error
    host.auto_start = True
    host.ui.CanOpenPart = lambda: False
    host.tick()
    assert host.auto_start
    host.dispatcher.drain.side_effect = RuntimeError("dispatcher")
    host.tick()
    assert not host.busy and host.last_error == "dispatcher"
    host.busy = True
    n = host.ticks
    host.tick()
    assert host.ticks == n
    host.busy = False
    host.stop_requested = True
    host.tick()
    assert host.stopped and not host.descriptor_path.exists()
    host.user.KillTimer.assert_called_once()
    host.server.stop.assert_called_once()


def test_stop_does_not_delete_another_session_descriptor(host):
    replacement = BridgeDescriptor.create(12346, "other")
    replacement.write(host.descriptor_path)
    host.stop()
    assert host.descriptor_path.exists()
    host.descriptor_path.write_text("malformed")
    host.stop()


def test_panel_commands_and_cleanup(host):
    panel = ControlPanel.__new__(ControlPanel)
    panel.host = host
    panel.user = NS(
        DefWindowProcW=Mock(return_value=9),
        SetWindowTextW=Mock(),
        UpdateWindow=Mock(),
        DestroyWindow=Mock(),
        UnregisterClassW=Mock(),
    )
    panel.label = 2
    panel.hwnd = 3
    panel._class = NS(lpszClassName="class")
    for code, mode in [(101, "manual"), (102, "agent")]:
        assert panel._message(1, 0x111, code, 0) == 0 and host.requested_mode == mode
    panel._message(1, 0x111, 103, 0)
    assert host.stop_requested
    host.stop_requested = False
    panel._message(1, 0x10, 0, 0)
    assert host.stop_requested
    assert panel._message(1, 999, 0, 0) == 9
    assert panel._message(1, 0x111, None, 0) == 9 and host.last_error
    panel.update("hello")
    panel.close()
    panel.user.DestroyWindow.assert_called_once_with(3)


def test_main_window_selection_requires_own_visible_window(monkeypatch):
    user = NS(
        EnumWindows=Mock(),
        GetWindowThreadProcessId=Mock(),
        GetWindowRect=Mock(),
        IsWindowVisible=Mock(return_value=True),
    )
    user.EnumWindows.side_effect = lambda fn, _: [fn(h, 0) for h in (1, 2, 3)]
    user.GetWindowThreadProcessId.side_effect = lambda h, p: setattr(
        p._obj, "value", os.getpid() if h != 3 else os.getpid() + 1
    )

    def rect(h, p):
        p._obj.right = h * 100
        p._obj.bottom = 100

    user.GetWindowRect.side_effect = rect
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: user, raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *a: lambda fn: fn, raising=False)
    assert interactive.nx_main_window() == 2
    user.IsWindowVisible.return_value = False
    with pytest.raises(RuntimeError, match="visible"):
        interactive.nx_main_window()


def test_start_reuses_live_host_and_cleans_partial_initialization(monkeypatch, host):
    monkeypatch.setattr(interactive, "_host", host)
    monkeypatch.setattr(interactive, "_retired", [])
    assert interactive.start("unused", "unused")["pid"] == os.getpid()
    host.stopped = True
    resources = NS(user=NS(KillTimer=Mock()), server=NS(stop=Mock()), panel=NS(close=Mock()))

    def fail(self, *_):
        self.timer = 2
        self.user = resources.user
        self.server = resources.server
        self.panel = resources.panel
        raise RuntimeError("initialization failed")

    monkeypatch.setattr(InteractiveHost, "__init__", fail)
    with pytest.raises(RuntimeError):
        interactive.start("unused", "unused")
    resources.server.stop.assert_called_once()
    resources.panel.close.assert_called_once()
    assert len(interactive._retired) == 2


def test_running_snapshot_is_readable_without_ui_queue_or_nx_calls(host, rig):
    host.control("agent")
    observed = []

    def operation():
        def reader():
            observed.append(host.dispatch("nx_ui_control", {"mode": "status"}))

        thread = threading.Thread(target=reader)
        thread.start()
        thread.join(1)
        assert not thread.is_alive()
        state = __import__("json").loads((host.state_dir / "ui-state.json").read_text())
        assert state["running_method"] == "nx_test_busy"
        assert "Running nx_test_busy" in host.panel.update.call_args.args[0]
        return {}

    rig.e._handlers["nx_test_busy"] = operation
    host.execute("nx_test_busy", {})
    assert observed[0]["activity"] == "running"
    assert observed[0]["snapshot_only"] is True
    assert observed[0]["operation_elapsed_seconds"] >= 0
    host.dispatcher.drain.assert_not_called()
    assert host.status()["activity"] == "reserved_idle"
    assert host.status()["last_duration_seconds"] >= 0
    assert "intentionally reserved" in host.panel_text()
    assert not host.status()["nx_window_input_enabled"]


def test_diagnostic_write_failure_does_not_fail_committed_operation(host, rig):
    host.control("agent")
    host.publish = Mock(side_effect=OSError("disk full"))
    result = host.execute("nx_list_bodies", {})
    assert result["status"] == "success"
    assert host.running_method is None and host.operation_started is None
    assert host.owns_lock


def test_panel_unchanged_text_does_not_repaint(host):
    panel = ControlPanel.__new__(ControlPanel)
    panel.user = NS(SetWindowTextW=Mock(), UpdateWindow=Mock())
    panel.label, panel.hwnd = 2, 3
    panel.update("Idle")
    panel.update("Idle")
    panel.user.SetWindowTextW.assert_called_once()
    assert panel.user.UpdateWindow.call_count == 2
