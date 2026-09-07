import threading

import pytest

from nx_mcp.bridge import MainThreadDispatcher
from nx_mcp.inspection import envelope_gap
from nx_mcp.runtime import NXToolError


def test_expired_queued_mutation_never_executes():
    calls = []
    dispatcher = MainThreadDispatcher(lambda m, p: calls.append(m) or {}, timeout=0.01)
    with pytest.raises(NXToolError) as error:
        dispatcher.call("nx_reposition_component", {})
    assert error.value.details["mutation_outcome"] == "not_started"
    dispatcher.drain()
    assert calls == []


def test_timeout_of_running_mutation_reports_unknown_without_duplicate():
    started = threading.Event()
    release = threading.Event()
    errors = []
    calls = []

    def execute(m, p):
        calls.append(m)
        started.set()
        release.wait(2)
        return {"done": True}

    dispatcher = MainThreadDispatcher(execute, timeout=0.05)

    def caller():
        try:
            dispatcher.call("mutation", {})
        except NXToolError as e:
            errors.append(e)

    client = threading.Thread(target=caller)
    client.start()
    worker = threading.Thread(target=lambda: dispatcher.drain(timeout=1))
    worker.start()
    assert started.wait(1)
    client.join(1)
    assert errors and errors[0].details["mutation_outcome"] == "unknown"
    release.set()
    worker.join(1)
    assert calls == ["mutation"]


def test_conservative_bounds_gap():
    assert envelope_gap([0, 0, 0, 10, 10, 10], [12, 0, 0, 22, 10, 10]) == 2
    assert envelope_gap([0, 0, 0, 10, 10, 10], [5, 0, 0, 15, 10, 10]) == 0
    assert envelope_gap([0, 0, 0, 10, 10, 10], [13, 14, 10, 23, 24, 20]) == 5


@pytest.mark.asyncio
async def test_mcp_points_cross_bridge_as_json_objects(tmp_path):
    from nx_mcp.server import create_server
    from nx_mcp.workspace import Workspace

    class Bridge:
        async def call(self, method, params):
            assert params["corner1"] == {"x": 0.0, "y": 0.0}
            assert params["corner2"] == {"x": 10.0, "y": 10.0}
            import json

            json.dumps(params)
            return {"status": "success"}

    server = create_server(Bridge(), Workspace(tmp_path), enable_experimental=True)
    result = await server.call_tool(
        "nx_sketch_rectangle",
        {"sketch_id": "sketch-test", "corner1": {"x": 0, "y": 0}, "corner2": {"x": 10, "y": 10}},
    )
    assert not result.isError, result


@pytest.mark.asyncio
async def test_capture_returns_inline_image_and_rejects_changed_artifact(tmp_path):
    import hashlib

    from nx_mcp.server import create_server
    from nx_mcp.workspace import Workspace

    data = b"\x89PNG\r\n\x1a\n" + b"fixture"
    file = tmp_path / "view.png"
    file.write_bytes(data)

    class Bridge:
        async def call(self, method, params):
            return {
                "path": str(file),
                "sha256": hashlib.sha256(data).hexdigest(),
                "model_preview": True,
            }

    server = create_server(Bridge(), Workspace(tmp_path), enable_experimental=True)
    result = await server.call_tool("nx_screenshot", {})
    assert not result.isError and result.structuredContent["model_preview"]
    assert len([item for item in result.content if item.type == "image"]) == 1
    file.write_bytes(b"changed")
    result = await server.call_tool("nx_screenshot", {})
    assert result.isError and result.structuredContent["code"] == "NX_ARTIFACT_CHANGED"


def test_agent_ui_lock_is_not_nested_and_manual_handoff_releases_input():
    from types import SimpleNamespace

    from nx_mcp.interactive import InteractiveHost

    class UI:
        count = 1

        def AskLockStatus(self):
            return int(self.count > 0)

        def LockAccess(self):
            self.count += 1

        def UnlockAccess(self):
            self.count = max(0, self.count - 1)

        def CanOpenPart(self):
            return self.count == 0

    class Parts:
        Display = None

        def __iter__(self):
            return iter(())

    host = InteractiveHost.__new__(InteractiveHost)
    host.thread = threading.get_ident()
    host.mode = "agent"
    host.owns_lock = True
    host.window_disabled = False
    host.ui = UI()
    host.nx = SimpleNamespace(UI=SimpleNamespace(Status=SimpleNamespace(Lock=1)))
    host.main_hwnd = 1
    enabled = []
    host.user = SimpleNamespace(EnableWindow=lambda h, v: enabled.append(v))
    host.session = SimpleNamespace(Parts=Parts())
    host.completed = 0
    host.status = lambda: {}
    host.publish = lambda: None

    def execute(method, params):
        if method == "nx_create_part":
            assert host.ui.count == 0  # FileNew must run without an NX UI lock.
        return {"status": "success"}

    host.executor = SimpleNamespace(execute=execute, _history=[1], _checkpoints={"cp": 1})
    host.execute("nx_list_bodies", {})
    assert host.ui.count == 1  # Do not increment an already-held lock.
    host.execute("nx_create_part", {})
    assert host.ui.count == 1  # Reacquire the lock released by FileNew exactly once.
    host.control("manual")
    assert host.ui.count == 0 and not enabled
    assert not host.executor._history and not host.executor._checkpoints


@pytest.mark.asyncio
async def test_interactive_transport_services_health_during_blocked_request():
    import asyncio

    from nx_mcp.bridge import BridgeClient, BridgeServer

    entered, release = threading.Event(), threading.Event()

    def dispatch(method, params):
        if method == "work":
            entered.set()
            release.wait(5)
            return {"done": True}
        return {"activity": "running", "snapshot_only": True}

    server = BridgeServer(dispatch, token="test", concurrent_requests=True)
    server.start()
    try:
        client = BridgeClient("127.0.0.1", server.port, token="test", timeout=2)
        running = asyncio.create_task(client.call("work", {}))
        assert await asyncio.to_thread(entered.wait, 1)
        status = await asyncio.wait_for(client.call("nx_ui_control", {}), 1)
        assert status["snapshot_only"] and not running.done()
        release.set()
        assert (await running)["done"]
    finally:
        release.set()
        server.stop()
