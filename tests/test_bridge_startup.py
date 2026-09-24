"""Descriptor startup rules the Python bridge shares with the C# GUI bridge.

These mirror ``tests/test_gui_bridge_startup.py``, which pins the same contract on
``NxMcpGuiBridge.cs``. Nothing here needs NX or Windows: the sidecar's own checks
run on Linux and macOS too.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from nx_mcp.bridge import (
    BRIDGE_LOCK_NAME,
    BRIDGE_PROTOCOL_VERSION,
    BridgeDescriptor,
    BridgeServer,
    _is_foreign_port,
    descriptor_lock_path,
    descriptor_startup_lock,
    refuse_live_descriptor,
)
from nx_mcp.runtime import NXToolError

pytestmark = pytest.mark.integration

SOURCE_ROOT = str(Path(__file__).resolve().parents[1] / "src")


class Holder:
    """A separate process that takes the startup lock and waits to be released."""

    def __init__(self, descriptor: Path) -> None:
        script = textwrap.dedent(
            """
            import sys
            sys.path.insert(0, sys.argv[1])
            from nx_mcp.bridge import descriptor_startup_lock

            with descriptor_startup_lock(sys.argv[2]):
                print("LOCKED", flush=True)
                sys.stdin.readline()
            print("RELEASED", flush=True)
            """
        )
        self.process = subprocess.Popen(
            [sys.executable, "-c", script, SOURCE_ROOT, str(descriptor)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert self.process.stdout is not None
        assert self.process.stdout.readline().strip() == "LOCKED"

    def release(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write("go\n")
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()


@pytest.fixture
def holders(tmp_path):
    started: list[Holder] = []

    def start(descriptor=None):
        holder = Holder(descriptor or tmp_path / "bridge.json")
        started.append(holder)
        return holder

    yield start
    for holder in started:
        holder.close()


class Listener:
    """A loopback socket standing in for whatever holds a descriptor's port."""

    def __init__(self, answer: bytes | None, *, accept: bool = True) -> None:
        self.answer = answer
        self.requests: list[bytes] = []
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.port = self.socket.getsockname()[1]
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._serve, args=(accept,), daemon=True)
        self.thread.start()

    def _serve(self, accept: bool) -> None:
        if not accept:
            return
        try:
            self.socket.settimeout(10)
            connection, _ = self.socket.accept()
            with connection:
                connection.settimeout(10)
                request = b""
                while not request.endswith(b"\n"):
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                self.requests.append(request)
                if self.answer is not None:
                    connection.sendall(self.answer)
                else:
                    # Accept and stay silent, like a bridge waiting on NX's UI thread.
                    self.done.wait(4)
        except OSError:
            pass

    def close(self) -> None:
        self.done.set()
        self.socket.close()
        self.thread.join(timeout=10)


@pytest.fixture
def listeners():
    started: list[Listener] = []

    def start(answer, *, accept=True):
        listener = Listener(answer, accept=accept)
        started.append(listener)
        return listener

    yield start
    for listener in started:
        listener.close()


def dead_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- the startup lock -------------------------------------------------------


def test_lock_lives_beside_the_descriptor_under_the_shared_name(tmp_path):
    assert descriptor_lock_path(tmp_path / "bridge.json") == tmp_path / BRIDGE_LOCK_NAME


def test_lock_held_by_another_writer_times_out_without_publishing(holders, tmp_path):
    holders()
    started = time.monotonic()
    lock = descriptor_startup_lock(tmp_path / "bridge.json")
    with pytest.raises(TimeoutError) as caught, lock:
        pytest.fail("the lock was granted while another writer held it")
    elapsed = time.monotonic() - started
    assert "another NX process may be starting the bridge" in str(caught.value)
    assert 4.5 <= elapsed < 15
    assert not (tmp_path / "bridge.json").exists()


def test_waiting_writer_proceeds_once_the_holder_releases(holders, tmp_path):
    holder = holders()
    holder.release()
    assert holder.process.stdout is not None
    assert holder.process.stdout.readline().strip() == "RELEASED"
    with descriptor_startup_lock(tmp_path / "bridge.json", timeout=5):
        pass


def test_lock_is_released_when_its_holder_dies(holders, tmp_path):
    holder = holders()
    holder.process.kill()
    holder.process.wait(timeout=10)
    with descriptor_startup_lock(tmp_path / "bridge.json", timeout=5):
        pass


def test_stale_lock_file_is_taken_again_and_never_removed(tmp_path):
    descriptor = tmp_path / "bridge.json"
    lock = descriptor_lock_path(descriptor)
    with descriptor_startup_lock(descriptor):
        pass
    assert lock.is_file()
    started = time.monotonic()
    with descriptor_startup_lock(descriptor, timeout=5):
        pass
    # Its open handle owns the lock, so a file left behind is not a stale claim.
    assert time.monotonic() - started < 1
    assert lock.is_file()


def test_lock_that_cannot_be_opened_is_reported_as_itself(tmp_path):
    descriptor_lock_path(tmp_path / "bridge.json").mkdir()
    started = time.monotonic()
    lock = descriptor_startup_lock(tmp_path / "bridge.json")
    with pytest.raises(OSError) as caught, lock:
        pytest.fail("a lock that cannot be opened must not be granted")
    assert not isinstance(caught.value, TimeoutError)
    assert "Timed out" not in str(caught.value)
    assert time.monotonic() - started < 1


def test_lock_creates_a_missing_state_directory(tmp_path):
    descriptor = tmp_path / "state" / "bridge.json"
    with descriptor_startup_lock(descriptor):
        pass
    assert descriptor_lock_path(descriptor).is_file()


# --- the per-writer temporary descriptor ------------------------------------


def test_write_publishes_through_a_temporary_of_its_own(tmp_path):
    descriptor = tmp_path / "bridge.json"
    BridgeDescriptor.create(4321, "NX test", token="only", pid=99).write(descriptor)
    assert BridgeDescriptor.read(descriptor).token == "only"
    assert list(tmp_path.glob("bridge.json.tmp*")) == []


def test_writers_use_unique_temporaries_and_remove_only_their_own(tmp_path, monkeypatch):
    descriptor = tmp_path / "bridge.json"
    descriptor.mkdir()  # Fail publication, after each writer has created its temporary.
    sentinel = tmp_path / "bridge.json.tmp"
    sentinel.write_text("another writer", encoding="utf-8")
    observed = set()
    publish = os.replace

    def record(source, target):
        # The temporary still exists here; each writer removes its own afterwards.
        observed.add(Path(source).name)
        assert Path(source).is_file()
        publish(source, target)

    monkeypatch.setattr(os, "replace", record)
    for attempt in range(10):
        # Two NX processes publishing at once differ by pid as well as by suffix.
        monkeypatch.setattr(os, "getpid", lambda attempt=attempt: 1000 + attempt % 2)
        with pytest.raises(OSError):
            BridgeDescriptor.create(4321, "NX test", token=f"token-{attempt}").write(descriptor)
    assert len(observed) == 10
    assert {name.split(".")[3] for name in observed} == {"1000", "1001"}
    # The shared name the descriptor used before is nobody's temporary now.
    assert sentinel.read_text(encoding="utf-8") == "another writer"
    assert list(tmp_path.glob("bridge.json.tmp.*")) == []


def test_write_failure_leaves_no_temporary_behind(tmp_path, monkeypatch):
    descriptor = tmp_path / "bridge.json"

    def fail_replace(*args, **kwargs):
        raise OSError("injected publication failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected publication failure"):
        BridgeDescriptor.create(4321, "NX test").write(descriptor)
    assert list(tmp_path.glob("bridge.json.tmp*")) == []
    assert not descriptor.exists()


# --- the invalid-token liveness probe ---------------------------------------


def test_probe_is_answered_without_ever_reaching_nx():
    """The token is compared on the socket thread, so a wedged NX side still answers.

    A probe carrying the real token would be dispatched and hang behind this
    executor; the invalid one is refused before dispatch.
    """
    dispatched = threading.Event()
    finish = threading.Event()

    def executor(method, params):
        dispatched.set()
        finish.wait(30)
        return {"method": method}

    server = BridgeServer(executor, token="the-real-token", read_timeout=5.0)
    server.start()
    try:
        started = time.monotonic()
        assert _is_foreign_port(server.port) is False
        assert time.monotonic() - started < 1
        assert not dispatched.is_set()
    finally:
        finish.set()
        server.stop()


def test_probe_does_not_declare_a_bridge_foreign_while_it_runs_a_call():
    """Both bridges accept one connection at a time, so a call in flight means silence."""
    running = threading.Event()
    finish = threading.Event()

    def executor(method, params):
        running.set()
        finish.wait(30)
        return {"method": method}

    server = BridgeServer(executor, token="the-real-token", read_timeout=5.0)
    server.start()
    occupied = threading.Thread(target=_call, args=(server.port, "the-real-token"), daemon=True)
    try:
        occupied.start()
        assert running.wait(10), "the bridge never started the blocking call"
        # Silence proves nothing, so the busy bridge keeps its descriptor.
        assert _is_foreign_port(server.port) is False
    finally:
        finish.set()
        server.stop()
        occupied.join(timeout=10)


def test_probe_reports_an_answering_bridge_as_live(listeners):
    listener = listeners(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "id": "nx-mcp-descriptor-probe",
                "ok": False,
                "error": {"status": "error", "code": "NX_AUTH_FAILED"},
            }
        ).encode()
        + b"\n"
    )
    assert _is_foreign_port(listener.port) is False
    request = json.loads(listener.requests[0])
    assert request["token"] == "nx-mcp-descriptor-probe"
    assert request["method"] == "nx_status"
    assert request["protocol_version"] == BRIDGE_PROTOCOL_VERSION


def test_probe_reports_a_dead_port_as_foreign():
    assert _is_foreign_port(dead_port()) is True


def test_probe_reports_a_closing_connection_as_foreign(listeners):
    assert _is_foreign_port(listeners(b"").port) is True


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(b"HTTP/1.0 400 Bad Request\r\n\r\n", id="unrelated-service"),
        pytest.param(b'{"jsonrpc": "2.0", "id": 1}\n', id="json-without-a-result"),
        pytest.param(b'{"ok": true}\n', id="ok-without-a-result"),
        pytest.param(b"[1, 2, 3]\n", id="json-that-is-not-an-object"),
    ],
)
def test_probe_reports_an_answer_that_is_not_a_bridge_response_as_foreign(listeners, answer):
    assert _is_foreign_port(listeners(answer).port) is True


def test_probe_does_not_call_a_silent_port_foreign(listeners):
    """Silence after the request went out proves nothing: a busy bridge answers late."""
    started = time.monotonic()
    assert _is_foreign_port(listeners(None).port) is False
    assert time.monotonic() - started < 6


# --- refusing to replace a live descriptor ----------------------------------


def test_refusal_names_the_running_bridge_and_leaves_the_descriptor(listeners, tmp_path):
    listener = listeners(b'{"ok": false, "error": {"code": "NX_AUTH_FAILED"}}\n')
    descriptor = tmp_path / "bridge.json"
    original = json.dumps({"port": listener.port, "pid": 4242, "token": "previous"})
    descriptor.write_text(original, encoding="utf-8")
    with pytest.raises(NXToolError) as caught:
        refuse_live_descriptor(descriptor)
    assert caught.value.code == "NX_BRIDGE_ALREADY_RUNNING"
    assert "already running (pid 4242" in caught.value.message
    assert str(descriptor) in caught.value.message
    assert descriptor.read_text(encoding="utf-8") == original


def test_refusal_accepts_a_descriptor_whose_port_answers_as_something_else(listeners, tmp_path):
    descriptor = tmp_path / "bridge.json"
    descriptor.write_text(
        json.dumps({"port": listeners(b"HTTP/1.0 400 Bad Request\r\n\r\n").port}),
        encoding="utf-8",
    )
    refuse_live_descriptor(descriptor)


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param(None, id="no-descriptor"),
        pytest.param("not json at all", id="unparseable"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"token": "previous"}', id="no-port"),
        pytest.param('{"port": "8080"}', id="port-is-not-an-integer"),
        pytest.param('{"port": true}', id="port-is-a-boolean"),
        pytest.param('{"port": 0}', id="port-below-range"),
        pytest.param('{"port": 65536}', id="port-above-range"),
    ],
)
def test_refusal_ignores_a_descriptor_it_cannot_probe(tmp_path, contents):
    descriptor = tmp_path / "bridge.json"
    if contents is not None:
        descriptor.write_text(contents, encoding="utf-8")
    refuse_live_descriptor(descriptor)


def test_refusal_reports_a_stale_descriptor(tmp_path, caplog):
    descriptor = tmp_path / "bridge.json"
    descriptor.write_text(json.dumps({"port": dead_port()}), encoding="utf-8")
    with caplog.at_level("INFO", logger="nx_mcp"):
        refuse_live_descriptor(descriptor)
    assert "does not answer as an NX MCP bridge" in caplog.text


def test_refusal_reports_an_out_of_range_port(tmp_path, caplog):
    descriptor = tmp_path / "bridge.json"
    descriptor.write_text(json.dumps({"port": 65536}), encoding="utf-8")
    with caplog.at_level("INFO", logger="nx_mcp"):
        refuse_live_descriptor(descriptor)
    assert "port 65536 is out of range" in caplog.text


def _call(port: int, token: str) -> None:
    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "occupying-call",
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "token": token,
                "method": "nx_status",
                "params": {},
            }
        ).encode()
        + b"\n"
    )
    try:
        with socket.create_connection(("127.0.0.1", port), 10) as connection:
            connection.settimeout(10)
            connection.sendall(request)
            connection.recv(4096)  # Either the late answer or a shutdown ends this call.
    except OSError:
        pass
