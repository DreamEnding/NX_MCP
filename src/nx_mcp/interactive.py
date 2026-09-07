"""Windows NX UI-thread host. No background thread calls NXOpen.

The journal returns after installing a Win32 timer. NX's existing message pump
invokes TIMERPROC on the registering thread; the pinned callback drains one call.
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
import uuid
from pathlib import Path

from nx_mcp.runtime import NXToolError

_host = None
_retired = []


def nx_main_window():
    """Find the main window owned by this NX process, before creating our panel."""
    from ctypes import wintypes as w

    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    user.IsWindowVisible.argtypes = [w.HWND]
    windows = []

    def visit(hwnd, param):
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() and user.IsWindowVisible(hwnd):
            rect = w.RECT()
            user.GetWindowRect(hwnd, ctypes.byref(rect))
            windows.append(((rect.right - rect.left) * (rect.bottom - rect.top), hwnd))
        return True

    callback = callback_type(visit)
    user.EnumWindows(callback, 0)
    if not windows:
        raise RuntimeError("NX has no visible main window")
    return max(windows)[1]


class ControlPanel:
    def __init__(self, host):
        from ctypes import wintypes as w

        self.host = host
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", w.UINT),
                ("lpfnWndProc", self.proc_type),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE),
                ("hIcon", w.HICON),
                ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR),
            ]

        self.user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self.user.DefWindowProcW.restype = ctypes.c_ssize_t
        self.user.CreateWindowExW.argtypes = [
            w.DWORD,
            w.LPCWSTR,
            w.LPCWSTR,
            w.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            w.HWND,
            w.HMENU,
            w.HINSTANCE,
            ctypes.c_void_p,
        ]
        self.user.CreateWindowExW.restype = w.HWND
        self.user.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
        self.user.UpdateWindow.argtypes = [w.HWND]
        self.user.DestroyWindow.argtypes = [w.HWND]
        self.user.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
        self.user.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        self.proc = self.proc_type(self._message)
        cls = WNDCLASS()
        cls.lpfnWndProc = self.proc
        cls.hbrBackground = 6
        cls.lpszClassName = "NxMcpPanel" + uuid.uuid4().hex
        self._class = cls
        if not self.user.RegisterClassW(ctypes.byref(cls)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = self.user.CreateWindowExW(
            0x80,
            cls.lpszClassName,
            "NX MCP control",
            0x10C80000,
            40,
            60,
            560,
            195,
            self.host.main_hwnd,
            None,
            None,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.label = self.user.CreateWindowExW(
            0,
            "STATIC",
            "Starting interactive bridge...",
            0x50000000,
            12,
            10,
            530,
            85,
            self.hwnd,
            None,
            None,
            None,
        )
        for caption, x, ident in [
            ("Pause / manual", 12, 101),
            ("Resume agent", 152, 102),
            ("Stop bridge", 292, 103),
        ]:
            self.user.CreateWindowExW(
                0, "BUTTON", caption, 0x50010000, x, 110, 130, 30, self.hwnd, ident, None, None
            )

    def _message(self, hwnd, msg, wp, lp):
        try:
            if msg == 0x111:
                ident = int(wp) & 0xFFFF
                if ident == 101:
                    self.host.requested_mode = "manual"
                elif ident == 102:
                    self.host.requested_mode = "agent"
                elif ident == 103:
                    self.host.stop_requested = True
                return 0
            if msg == 0x10:
                self.host.stop_requested = True
                return 0
        except BaseException as exc:
            self.host.last_error = str(exc)
        return self.user.DefWindowProcW(hwnd, msg, wp, lp)

    def update(self, text):
        if text == getattr(self, "_text", None):
            return
        self._text = text
        self.user.SetWindowTextW(self.label, text)
        # Paint only this panel before native work. Never pump arbitrary NX
        # messages here: that would permit reentrant modeling calls.
        self.user.UpdateWindow(self.label)
        self.user.UpdateWindow(self.hwnd)

    def close(self):
        self.user.DestroyWindow(self.hwnd)
        self.user.UnregisterClassW(self._class.lpszClassName, None)


class InteractiveHost:
    def __init__(self, workspace, descriptor_path):
        import secrets

        import NXOpen

        from nx_mcp import nx_bridge
        from nx_mcp.bridge import BridgeDescriptor, BridgeServer, MainThreadDispatcher
        from nx_mcp.hardened import HardenedExecutor
        from nx_mcp.workspace import Workspace

        self.session = NXOpen.Session.GetSession()
        self.nx = NXOpen
        self.ui = NXOpen.UI.GetUI()
        if self.session.IsBatch:
            raise RuntimeError("Interactive host requires a graphical NX session")
        self.thread = threading.get_ident()
        self.native_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        self.root = Path(workspace)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_dir = self.root / ".nx-mcp"
        self.state_dir.mkdir(exist_ok=True)
        self.stop_file = self.state_dir / "ui-stop"
        self.stop_file.unlink(missing_ok=True)
        self.mode = "manual"
        self.requested_mode = None
        self.owns_lock = False
        self.busy = False
        self.stop_requested = False
        self.stopped = False
        self.ticks = 0
        self.completed = 0
        self.running_method = None
        self.operation_started = None
        self.last_duration_seconds = None
        self._snapshot = None
        self.last_method = None
        self.last_error = None
        self.started = time.time()
        batch_descriptor = Path(os.environ.get("LOCALAPPDATA", "")) / "nx-mcp" / "bridge.json"
        default_workspace = Path(os.environ.get("NX_MCP_WORKSPACE", r"D:\CAD\NX_MCP_WORKSPACE"))
        if self.root.resolve() == default_workspace.resolve() and batch_descriptor.exists():
            raise RuntimeError(
                "Stop the batch bridge before attaching the interactive host to this workspace"
            )
        self.executor = HardenedExecutor(
            self.session,
            NXOpen,
            nx_bridge._detect_nx_version(self.session),
            Workspace(self.root),
            enable_experimental=True,
            enable_journal=False,
        )
        self.executor._handlers["nx_ui_control"] = self.control
        token = secrets.token_hex(32)
        self.dispatcher = MainThreadDispatcher(self.execute)
        self.server = BridgeServer(
            self.dispatch,
            token=token,
            concurrent_requests=True,
            result_directory=Path(self.root) / ".nx-mcp" / "bridge-results",
        )
        self.descriptor_path = Path(descriptor_path)
        self.server.start()
        self.descriptor = BridgeDescriptor.create(
            self.server.port, self.executor.nx_version, token=token
        )
        self.main_hwnd = nx_main_window()
        self.window_disabled = False
        self.panel = ControlPanel(self)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.EnableWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.user.IsWindowEnabled.argtypes = [ctypes.c_void_p]
        self.timer_type = ctypes.WINFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_uint32
        )
        self.callback = self.timer_type(self.tick)
        self.user.SetTimer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint,
            self.timer_type,
        ]
        self.user.SetTimer.restype = ctypes.c_size_t
        self.user.KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self.timer = self.user.SetTimer(None, 0, 100, self.callback)
        if not self.timer:
            raise ctypes.WinError(ctypes.get_last_error())
        self.descriptor.write(self.descriptor_path)
        self.auto_start = True

    def dispatch(self, method, params):
        # Bridge-worker health reads use an immutable UI-thread snapshot only.
        # They must not wait behind the native operation they are diagnosing.
        snapshot = getattr(self, "_snapshot", None)
        if method == "nx_ui_control" and params.get("mode", "status") == "status" and snapshot:
            result = dict(snapshot)
            result["snapshot_age_seconds"] = max(
                0, time.monotonic() - result.pop("_sample_monotonic")
            )
            result["snapshot_only"] = True
            started = result.pop("_operation_monotonic", None)
            result["operation_elapsed_seconds"] = (
                max(0, time.monotonic() - started) if started is not None else None
            )
            return result
        return self.dispatcher.call(method, params)

    def publish(self):
        state = self.status()
        self._snapshot = {
            **state,
            "_sample_monotonic": time.monotonic(),
            "_operation_monotonic": self.operation_started,
        }
        self.panel.update(self.panel_text())
        temp = self.state_dir / "ui-state.tmp"
        temp.write_text(json.dumps(state))
        temp.replace(self.state_dir / "ui-state.json")

    def panel_text(self):
        if self.running_method:
            return (
                f"Running {self.running_method} — NX input reserved\n"
                "Native work may block repainting. Pause / Stop applies after it returns."
            )
        if self.mode == "manual":
            return "Manual editing enabled — agent requests paused\nFinish NX dialogs, then Resume agent."
        detail = self.last_error or (
            f"Last: {self.last_method} ({self.last_duration_seconds:.2f}s)"
            if self.last_duration_seconds is not None
            else "Waiting for MCP requests"
        )
        return (
            "Agent idle — NX input intentionally reserved\nUse Pause / manual to edit or navigate NX.\n"
            + detail
        )

    def status(self):
        return {
            "interactive": True,
            "pid": os.getpid(),
            "mode": self.mode,
            "activity": "running"
            if self.running_method
            else ("reserved_idle" if self.mode == "agent" else "manual"),
            "running_method": self.running_method,
            "operation_elapsed_seconds": (
                time.monotonic() - self.operation_started
                if self.operation_started is not None
                else None
            ),
            "last_duration_seconds": self.last_duration_seconds,
            "sampled_at": time.time(),
            "snapshot_only": False,
            "pause_semantics": "between operations; does not interrupt a native call",
            "ui_locked_by_bridge": self.owns_lock,
            "actual_ui_lock": self.ui.AskLockStatus() == self.nx.UI.Status.Lock,
            "native_lock_value": str(self.ui.AskLockStatus()),
            "can_open_part": self.ui.CanOpenPart(),
            "last_unlock": getattr(self, "last_unlock", None),
            "nx_window_input_enabled": bool(self.user.IsWindowEnabled(self.main_hwnd)),
            "main_thread_id": self.native_thread,
            "callback_thread_id": ctypes.windll.kernel32.GetCurrentThreadId(),
            "ticks": self.ticks,
            "completed_operations": self.completed,
            "last_method": self.last_method,
            "last_error": self.last_error,
            "uptime_seconds": time.time() - self.started,
            "scheduler": "Win32 UI-thread timer; one queued call per tick",
        }

    def control(self, mode="status"):
        if threading.get_ident() != self.thread:
            raise RuntimeError("UI control called off NX thread")
        if mode == "manual":
            if self.mode == "agent":
                if hasattr(self.executor, "_clear_highlights"):
                    self.executor._clear_highlights()
                # Manual edits have no bridge receipt. Do not let a later agent
                # rollback undo them, or resolve references across that boundary.
                for part in self.session.Parts:
                    self.executor.objects.invalidate_part(self.executor._part_id(part))
                self.executor._history.clear()
                self.executor._checkpoints.clear()
                self.executor._review_epoch = getattr(self.executor, "_review_epoch", 0) + 1
            if self.owns_lock:
                before = str(self.ui.AskLockStatus())
                self.ui.UnlockAccess()
                self.last_unlock = {
                    "before": before,
                    "after": str(self.ui.AskLockStatus()),
                    "can_open_part": self.ui.CanOpenPart(),
                }
                self.owns_lock = False
            if self.window_disabled:
                self.user.EnableWindow(self.main_hwnd, True)
                self.window_disabled = False
            self.mode = "manual"
            self.auto_start = False
        elif mode == "agent":
            if not self.owns_lock:
                if self.ui.AskLockStatus() == self.nx.UI.Status.Lock or not self.ui.CanOpenPart():
                    raise NXToolError(
                        "NX_UI_BUSY",
                        "Finish the current NX dialog before resuming agent control",
                        details={"mutation_outcome": "not_started"},
                    )
                self.ui.LockAccess()
                self.owns_lock = True
            self.user.EnableWindow(self.main_hwnd, False)
            self.window_disabled = True
            self.mode = "agent"
        elif mode != "status":
            raise NXToolError("NX_INVALID_ARGUMENT", "mode must be status, manual or agent")
        return self.status()

    def execute(self, method, params):
        if threading.get_ident() != self.thread:
            raise RuntimeError("NX request called off registering UI thread")
        if method == "nx_ui_control":
            return self.control(params.get("mode", "status"))
        if method == "nx_status":
            return {**self.executor.execute(method, params), "ui": self.status()}
        if self.mode != "agent" or not self.owns_lock:
            raise NXToolError(
                "NX_UI_PAUSED",
                "Agent control is paused. Finish manual edits and resume in NX MCP control.",
                details={"mutation_outcome": "not_started"},
            )
        # FileNew must not run inside LockAccess: NX v2606 can strand its
        # internal UI lock. The disabled main window reserves user input while
        # native operations execute on this same UI thread.
        self.ui.UnlockAccess()
        self.last_method = method
        self.running_method = method
        self.operation_started = time.monotonic()
        self.last_error = None
        try:
            try:
                self.publish()
            except Exception as exc:
                self.last_error = "UI status publication failed: " + str(exc)
            result = self.executor.execute(method, params)
            self.completed += 1
            part = self.session.Parts.Display
            from nx_mcp.hardened import READ_ONLY

            sheet = getattr(getattr(part, "DrawingSheets", None), "CurrentDrawingSheet", None)
            if part and method not in READ_ONLY and sheet is None:
                try:
                    part.ModelingViews.WorkView.UpdateDisplay()
                except Exception as exc:
                    result.setdefault("warnings", []).append("View refresh: " + str(exc))
            return result
        except BaseException as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.last_duration_seconds = time.monotonic() - self.operation_started
            self.running_method = None
            self.operation_started = None
            # Restore the between-operation native lock after all NX work.
            try:
                if self.ui.AskLockStatus() != self.nx.UI.Status.Lock:
                    self.ui.LockAccess()
            except Exception as exc:
                self.last_error = "Cannot restore agent UI reservation: " + str(exc)
                self.control("manual")
            try:
                self.publish()
            except Exception as exc:
                # A failed diagnostic write must not turn a committed operation
                # into a failure response that invites an unsafe retry.
                self.last_error = "UI status publication failed: " + str(exc)

    def tick(self, *args):
        if self.busy or self.stopped:
            return
        self.busy = True
        try:
            self.ticks += 1
            if self.stop_requested or self.stop_file.exists():
                self.stop()
                return
            if self.requested_mode:
                mode, self.requested_mode = self.requested_mode, None
                try:
                    self.control(mode)
                except NXToolError as exc:
                    self.last_error = str(exc)
            if self.auto_start:
                try:
                    self.control("agent")
                    self.auto_start = False
                except NXToolError:
                    pass
            self.dispatcher.drain(timeout=0, limit=1)
            self.panel.update(self.panel_text())
            if self.ticks % 10 == 0:
                self.publish()
        except BaseException as exc:
            self.last_error = str(exc)
            try:  # noqa: SIM105 - Last-resort native callback cleanup must not escape.
                self.control("manual")
            except BaseException:
                pass
        finally:
            self.busy = False

    def stop(self):
        from nx_mcp.bridge import BridgeDescriptor

        self.control("manual")
        self.stopped = True
        self.user.KillTimer(None, self.timer)
        self.dispatcher.stop()
        self.server.stop()
        self.panel.close()
        try:
            if BridgeDescriptor.read(self.descriptor_path).token == self.descriptor.token:
                self.descriptor_path.unlink()
        except (ValueError, OSError):
            pass


def start(workspace, descriptor_path):
    global _host
    if _host is not None and not _host.stopped:
        return _host.status()
    if _host is not None:
        _retired.append(_host)  # Keep native callback delegates alive until NX exits.
    candidate = InteractiveHost.__new__(InteractiveHost)
    try:
        candidate.__init__(workspace, descriptor_path)
    except BaseException:
        if getattr(candidate, "timer", None):
            candidate.user.KillTimer(None, candidate.timer)
        if hasattr(candidate, "server"):
            candidate.server.stop()
        if hasattr(candidate, "panel"):
            candidate.panel.close()
        _retired.append(candidate)
        raise
    _host = candidate
    return _host.status()
