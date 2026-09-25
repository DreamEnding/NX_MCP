"""Failure injection for the certified executor, not NXOpen certification."""

import pytest

from nx_mcp.bridge import BridgeDescriptor
from nx_mcp.nx_bridge import NXOpenExecutor
from nx_mcp.runtime import NXToolError
from nx_mcp.workspace import Workspace
from tests.test_nx_executor import FAKE_NXOPEN, FakePart, FakeSession

pytestmark = pytest.mark.fake_nx


@pytest.fixture
def executor(tmp_path):
    session = FakeSession()
    session.Parts.Work.FullPath = str(tmp_path / "part.prt")
    return NXOpenExecutor(session, FAKE_NXOPEN, "fake", Workspace(tmp_path))


@pytest.mark.parametrize(
    "method,params",
    [
        ("nx_create_sketch", {"plane": []}),
        ("nx_create_sketch", {"extra": 1}),
        ("nx_extrude", {"sketch_id": "missing", "distance": float("nan")}),
        ("nx_extrude", {"sketch_id": "missing", "distance": float("inf")}),
        ("nx_extrude", {"sketch_id": "missing", "distance": True}),
        (
            "nx_sketch_line",
            {"sketch_id": "missing", "start": {"x": 1, "y": 1}, "end": {"x": 1, "y": 1}},
        ),
        (
            "nx_sketch_rectangle",
            {"sketch_id": "missing", "corner1": {"x": 0, "y": 0}, "corner2": {"x": 0, "y": 1}},
        ),
        (
            "nx_sketch_line",
            {"sketch_id": "missing", "start": {"x": float("inf"), "y": 1}, "end": {"x": 1, "y": 2}},
        ),
        ("nx_close_part", {"save": "false"}),
    ],
)
def test_invalid_arguments_do_not_create_undo_marks(executor, method, params):
    with pytest.raises(NXToolError) as caught:
        executor.execute(method, params)
    assert caught.value.code == "NX_INVALID_ARGUMENT"
    assert executor.session.next_mark == 1


@pytest.mark.parametrize("method", ["nx_save_part", "nx_close_part"])
def test_saving_outside_part_is_rejected(executor, method):
    part = executor.session.Parts.Work
    part.FullPath = str(executor.workspace.root.parent / "outside.prt")
    with pytest.raises(NXToolError) as caught:
        executor.execute(method, {})
    assert caught.value.code == "NX_PATH_OUTSIDE_WORKSPACE"
    assert not part.saved
    assert executor.session.Parts.Work is part


def test_direct_bridge_path_violation_has_workspace_code(executor):
    with pytest.raises(NXToolError) as caught:
        executor.execute(
            "nx_open_part", {"path": str(executor.workspace.root.parent / "outside.prt")}
        )
    assert caught.value.code == "NX_PATH_OUTSIDE_WORKSPACE"


def test_failed_undo_preserves_mark(executor, monkeypatch):
    executor.execute("nx_create_sketch", {})
    before = list(executor._undo_marks)

    def fail(*args):
        raise RuntimeError("native undo failed")

    monkeypatch.setattr(executor.session, "UndoToMark", fail)
    with pytest.raises(NXToolError):
        executor.execute("nx_undo", {})
    assert executor._undo_marks == before


def test_part_switch_prevents_cross_part_undo(executor):
    executor.execute("nx_create_sketch", {})
    executor.session.Parts._set_active(FakePart("other", 99))
    with pytest.raises(NXToolError) as caught:
        executor.execute("nx_undo", {})
    assert caught.value.code == "NX_UNDO_UNAVAILABLE"
    assert executor.session.undo_to_marks == []


def fail_third_line(executor, monkeypatch):
    sketch = executor.execute("nx_create_sketch", {})["object"]
    curves = executor.session.Parts.Work.Curves
    original = curves.CreateLine
    calls = []

    def create(start, end):
        calls.append(start)
        if len(calls) == 3:
            raise RuntimeError("third line failed")
        return original(start, end)

    monkeypatch.setattr(curves, "CreateLine", create)
    return sketch


def test_partial_geometry_failure_invalidates_registered_objects(executor, monkeypatch):
    sketch = fail_third_line(executor, monkeypatch)
    with pytest.raises(NXToolError):
        executor.execute(
            "nx_sketch_rectangle",
            {"sketch_id": sketch["id"], "corner1": {"x": 0, "y": 0}, "corner2": {"x": 2, "y": 1}},
        )
    assert executor.objects._objects == {}
    assert executor._undo_marks == [1]
    assert 2 not in executor.session.marks


def test_rollback_failure_blocks_writes_until_discard_close(executor, monkeypatch):
    sketch = fail_third_line(executor, monkeypatch)

    def fail(*args):
        raise RuntimeError("rollback failed")

    monkeypatch.setattr(executor.session, "UndoToMark", fail)
    with pytest.raises(NXToolError) as caught:
        executor.execute(
            "nx_sketch_rectangle",
            {"sketch_id": sketch["id"], "corner1": {"x": 0, "y": 0}, "corner2": {"x": 2, "y": 1}},
        )
    assert caught.value.code == "NX_ROLLBACK_FAILED"
    assert "third line failed" in caught.value.details["operation_error"]
    assert executor.objects._objects == {}
    assert executor._undo_marks == []
    for method, params in [
        ("nx_save_part", {}),
        ("nx_create_sketch", {}),
        ("nx_close_part", {"save": True}),
    ]:
        with pytest.raises(NXToolError, match="recovery"):
            executor.execute(method, params)
    assert executor.execute("nx_status", {})["connected"]
    executor.execute("nx_list_bodies", {})
    executor.execute("nx_close_part", {"save": False})
    executor.session.Parts._set_active(FakePart())
    executor.execute("nx_create_sketch", {})


@pytest.mark.parametrize("path", ["", "relative.prt", None])
def test_missing_save_path_does_not_save(executor, path):
    executor.session.Parts.Work.FullPath = path
    with pytest.raises(NXToolError) as caught:
        executor.execute("nx_save_part", {})
    assert caught.value.code == "NX_INVALID_ARGUMENT"
    assert not executor.session.Parts.Work.saved


def test_save_status_is_disposed(executor, monkeypatch):
    from types import SimpleNamespace

    disposed = []
    monkeypatch.setattr(
        executor.session.Parts.Work,
        "Save",
        lambda *args: SimpleNamespace(Dispose=lambda: disposed.append(True)),
    )
    executor.execute("nx_save_part", {})
    assert disposed == [True]


def test_descriptor_publish_failure_cleans_up(executor, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    import nx_mcp.nx_bridge as module

    events = []
    monkeypatch.setitem(
        sys.modules,
        "NXOpen",
        SimpleNamespace(Session=SimpleNamespace(GetSession=lambda: executor.session)),
    )
    monkeypatch.setattr(module, "NXOpenExecutor", lambda *args, **kwargs: executor)

    class Server:
        port = 12345

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    def fail_write(*args):
        raise OSError("descriptor unavailable")

    monkeypatch.setattr(module, "BridgeServer", Server)
    monkeypatch.setattr(module.BridgeDescriptor, "write", fail_write)
    with pytest.raises(OSError, match="descriptor unavailable"):
        module.start_bridge(tmp_path, allow_unverified_threading=True)
    assert events == ["start", "stop"]
    assert module._runtime is None


def _fake_nx(module, executor, monkeypatch, events):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "NXOpen",
        SimpleNamespace(Session=SimpleNamespace(GetSession=lambda: executor.session)),
    )
    monkeypatch.setattr(module, "NXOpenExecutor", lambda *args, **kwargs: executor)

    class Server:
        port = 12345

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(module, "BridgeServer", Server)


def test_start_bridge_refuses_a_descriptor_whose_bridge_still_answers(
    executor, tmp_path, monkeypatch
):
    import json
    import socket
    import threading

    import nx_mcp.nx_bridge as module

    module.stop_bridge()
    events = []
    _fake_nx(module, executor, monkeypatch, events)
    answered = threading.Event()

    with socket.socket() as live:
        live.bind(("127.0.0.1", 0))
        live.listen()

        def answer():
            try:
                live.settimeout(10)
                connection, _ = live.accept()
                with connection:
                    connection.settimeout(10)
                    while not connection.recv(4096).endswith(b"\n"):
                        pass
                    connection.sendall(b'{"ok": false, "error": {"code": "NX_AUTH_FAILED"}}\n')
                answered.set()
            except OSError:
                pass

        responder = threading.Thread(target=answer, daemon=True)
        responder.start()
        descriptor = tmp_path / "bridge.json"
        original = json.dumps({"port": live.getsockname()[1], "pid": 4242, "token": "previous"})
        descriptor.write_text(original, encoding="utf-8")
        with pytest.raises(NXToolError) as caught:
            module.start_bridge(
                tmp_path, descriptor_path=descriptor, allow_unverified_threading=True
            )
        assert answered.wait(10)
        responder.join(timeout=10)

    assert caught.value.code == "NX_BRIDGE_ALREADY_RUNNING"
    # Refused before anything was bound, and the running bridge keeps its descriptor.
    assert events == []
    assert module._runtime is None
    assert descriptor.read_text(encoding="utf-8") == original


def test_start_bridge_replaces_a_descriptor_whose_port_is_dead(executor, tmp_path, monkeypatch):
    import json
    import socket

    import nx_mcp.nx_bridge as module

    module.stop_bridge()
    events = []
    _fake_nx(module, executor, monkeypatch, events)
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        dead = closed.getsockname()[1]
    descriptor = tmp_path / "bridge.json"
    descriptor.write_text(json.dumps({"port": dead, "token": "previous"}), encoding="utf-8")
    try:
        published = module.start_bridge(
            tmp_path, descriptor_path=descriptor, allow_unverified_threading=True
        )
        assert events == ["start"]
        assert published.token != "previous"
        assert BridgeDescriptor.read(descriptor).token == published.token
    finally:
        module.stop_bridge()
    assert not descriptor.exists()


def test_start_bridge_gives_up_when_another_process_holds_the_lock(executor, tmp_path, monkeypatch):
    import nx_mcp.nx_bridge as module
    from tests.test_bridge_startup import Holder

    module.stop_bridge()
    events = []
    _fake_nx(module, executor, monkeypatch, events)
    descriptor = tmp_path / "bridge.json"
    holder = Holder(descriptor)
    try:
        with pytest.raises(TimeoutError, match="another NX process may be starting"):
            module.start_bridge(
                tmp_path, descriptor_path=descriptor, allow_unverified_threading=True
            )
    finally:
        holder.close()
    assert events == []
    assert module._runtime is None
    assert not descriptor.exists()


def test_stopping_runtime_keeps_a_descriptor_it_cannot_check_under_the_lock(tmp_path):
    from types import SimpleNamespace

    from nx_mcp.bridge import BridgeDescriptor
    from nx_mcp.nx_bridge import BridgeRuntime
    from tests.test_bridge_startup import Holder

    path = tmp_path / "bridge.json"
    mine = BridgeDescriptor.create(12345, "fake", token="mine")
    mine.write(path)
    stopped = []
    runtime = BridgeRuntime(
        SimpleNamespace(stop=lambda: stopped.append("server")),
        SimpleNamespace(stop=lambda: stopped.append("dispatcher")),
        mine,
        path,
    )
    holder = Holder(path)
    try:
        runtime.stop()
    finally:
        holder.close()
    # The listener is down either way; an unverifiable descriptor is left to its owner.
    assert stopped == ["dispatcher", "server"]
    assert BridgeDescriptor.read(path).token == "mine"


def test_stopping_runtime_preserves_replacement_descriptor(tmp_path):
    from types import SimpleNamespace

    from nx_mcp.bridge import BridgeDescriptor
    from nx_mcp.nx_bridge import BridgeRuntime

    path = tmp_path / "bridge.json"
    original = BridgeDescriptor.create(12345, "fake", token="original")
    replacement = BridgeDescriptor.create(12346, "fake", token="replacement")
    replacement.write(path)
    stopped = []
    runtime = BridgeRuntime(
        SimpleNamespace(stop=lambda: stopped.append("server")),
        SimpleNamespace(stop=lambda: stopped.append("dispatcher")),
        original,
        path,
    )
    runtime.stop()
    assert stopped == ["dispatcher", "server"]
    assert BridgeDescriptor.read(path).token == "replacement"


def test_builder_commit_failure_destroys_builder_and_rolls_back(executor, monkeypatch):
    from tests.test_nx_executor import FakeSketchBuilder

    destroyed = []

    def fail_commit(self):
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(FakeSketchBuilder, "Commit", fail_commit)
    monkeypatch.setattr(FakeSketchBuilder, "Destroy", lambda self: destroyed.append(True))
    with pytest.raises(NXToolError) as caught:
        executor.execute("nx_create_sketch", {})
    assert caught.value.code == "NX_API_ERROR"
    assert destroyed == [True]
    assert executor.session.rollback_count == 1
    assert executor._undo_marks == []
