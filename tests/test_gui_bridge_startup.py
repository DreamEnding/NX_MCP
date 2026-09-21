"""Windows cross-process tests of production C# startup, with NX/UI test doubles."""

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.fake_nx,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs"),
]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    compiler = (
        Path(os.environ.get("WINDIR", "C:/Windows"))
        / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    if not compiler.exists():
        pytest.skip("in-box .NET Framework compiler unavailable")
    source = (ROOT / "nx_gui_bridge/NxMcpGuiBridge.cs").read_text(encoding="utf-8")
    # Slice complete production classes; startup, probes, ACLs and publication are unmodified.
    usings = source[source.index("using System;") : source.index("using NXOpen.Features;")]
    usings += "using NXOpen.UF;\n"
    protocol = source[
        source.index("    internal static class Protocol") : source.index(
            "    internal sealed class NxToolError"
        )
    ]
    start = source.index("    internal static class Json")
    # The next top-level class marks the end, including any intervening documentation.
    end = source.index("\n    internal ", start + 1)
    json_class = source[start:end]
    host = source[source.index("    internal static class BridgeHost") : source.rfind("}")]
    directory = tmp_path_factory.mktemp("gui-startup-build")
    extracted = directory / "Production.cs"
    extracted.write_text(
        usings + "namespace NxMcp.GuiBridge {\n" + protocol + json_class + host + "}\n",
        encoding="utf-8",
    )
    exe = directory / "StartupTests.exe"
    result = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/langversion:5",
            "/target:exe",
            f"/out:{exe}",
            str(extracted),
            str(ROOT / "tests/gui_bridge_startup_harness.cs"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return exe


class Starter:
    def __init__(self, exe, mode, state):
        self.process = subprocess.Popen(
            [str(exe), mode, str(state)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line.strip())

    def line(self, timeout=12):
        return self.lines.get(timeout=timeout)

    def release(self):
        self.process.stdin.write("continue\n")
        self.process.stdin.flush()

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


@pytest.fixture
def starters(harness, tmp_path):
    children = []

    def start(mode="normal"):
        child = Starter(harness, mode, tmp_path)
        children.append(child)
        return child

    yield start
    for child in children:
        child.close()


def test_two_processes_cannot_publish_same_descriptor(starters, tmp_path):
    first = starters("hold")
    assert first.line() == "ENTER"
    assert first.line() == "SESSION"  # A passed validation and holds the startup lock.
    second = starters()
    assert second.line() == "ENTER"
    with pytest.raises(queue.Empty):
        second.line(timeout=0.75)
    first.release()
    assert first.line() == "STARTED"
    descriptor = (tmp_path / "bridge.json").read_bytes()
    rejected = json.loads(second.line())
    assert "already running" in rejected["error"]
    assert rejected["started"] == 0
    assert (tmp_path / "bridge.json").read_bytes() == descriptor
    first.release()
    assert first.line() == "STOPPED"
    assert not (tmp_path / "bridge.json").exists()


def test_failed_owner_releases_lock_to_waiting_process(starters):
    first = starters("fail")
    assert first.line() == "ENTER"
    assert first.line() == "SESSION"
    second = starters()
    assert second.line() == "ENTER"
    with pytest.raises(queue.Empty):
        second.line(timeout=0.75)
    first.release()
    assert "injected session failure" in json.loads(first.line())["error"]
    assert second.line() == "STARTED"
    second.release()
    assert second.line() == "STOPPED"


@pytest.mark.parametrize(
    "mode", ["listenerfail", "writefail", "replacefail", "aclfail", "cleanupfail"]
)
def test_startup_failure_cleans_resources_and_allows_restart(starters, tmp_path, mode):
    descriptor = tmp_path / "bridge.json"
    if mode in ("replacefail", "aclfail"):
        descriptor.write_text('{"port":0,"token":"previous"}', encoding="utf-8")
    failed = starters(mode)
    assert failed.line() == "ENTER"
    result = json.loads(failed.line())
    assert result["started"] == 1
    assert result["listener_stopped"] == 1
    assert result["dispatcher_stopped"] == 1
    assert result["disposed"] == 1
    assert failed.process.poll() is None
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", result["port"]), timeout=1):
        pass
    assert not list(tmp_path.glob("bridge.json.tmp*"))
    assert "injected cleanup failure" not in result["error"]
    if mode in ("writefail", "cleanupfail"):
        descriptor.rmdir()
    elif mode in ("replacefail", "aclfail"):
        assert json.loads(descriptor.read_text())["token"] == "previous"
        if mode == "aclfail":
            # Remove the injected ACL fault (DELETE is still allowed), keeping stale contents.
            previous = descriptor.read_bytes()
            descriptor.unlink()
            descriptor.write_bytes(previous)
    else:
        assert not descriptor.exists()
    next_start = starters()
    assert next_start.line() == "ENTER"
    assert next_start.line() == "STARTED"
    next_start.release()
    assert next_start.line() == "STOPPED"


def test_startup_lock_has_bounded_timeout(starters):
    owner = starters("lock")
    assert owner.line() == "LOCKED"
    blocked = starters()
    assert blocked.line() == "ENTER"
    started = time.monotonic()
    result = json.loads(blocked.line())
    assert 4.5 <= time.monotonic() - started < 10
    assert "Timed out" in result["error"]
    assert "another NX process may be starting" in result["error"]
    assert result["started"] == 0
    owner.release()


def test_writers_use_unique_temps_and_only_clean_their_own(starters, tmp_path):
    (tmp_path / "bridge.json").mkdir()  # Fail atomic publication, after creating each temp.
    sentinel = tmp_path / "bridge.json.tmp"
    sentinel.write_text("another writer", encoding="utf-8")
    writers = [starters("writers"), starters("writers")]
    paths = set()
    for writer in writers:
        observed = json.loads(writer.line())
        assert len(observed) >= 10
        paths.update(observed)
    assert len(paths) == 20
    assert "bridge.json.tmp" not in paths
    assert sentinel.read_text() == "another writer"
    assert list(tmp_path.glob("bridge.json.tmp*")) == [sentinel]


def test_process_exit_releases_startup_lock(starters):
    first = starters("hold")
    assert first.line() == "ENTER"
    assert first.line() == "SESSION"
    second = starters()
    assert second.line() == "ENTER"
    with pytest.raises(queue.Empty):
        second.line(timeout=0.75)
    first.process.kill()
    first.process.wait(timeout=5)
    assert second.line() == "STARTED"
    second.release()
    assert second.line() == "STOPPED"


def test_shutdown_rechecks_token_under_publication_lock(starters, tmp_path):
    descriptor = tmp_path / "bridge.json"
    descriptor.write_text('{"token":"previous"}', encoding="utf-8")
    owner = starters("lock")
    assert owner.line() == "LOCKED"
    removing = starters("remove")
    assert removing.line() == "ENTER"
    with pytest.raises(queue.Empty):
        removing.line(timeout=0.75)
    descriptor.write_text('{"token":"new-owner"}', encoding="utf-8")
    owner.release()
    assert removing.line() == "REMOVED_OR_PRESERVED"
    assert json.loads(descriptor.read_text())["token"] == "new-owner"


@pytest.mark.parametrize("stale_port", [None, 0, 65536])
def test_publication_keeps_user_only_acls(starters, tmp_path, stale_port):
    if stale_port is not None:
        (tmp_path / "bridge.json").write_text(json.dumps({"port": stale_port}), encoding="utf-8")
    owner = starters()
    assert owner.line() == "ENTER"
    assert owner.line() == "STARTED"
    assert starters("acl").line() == "USER_ONLY"
    assert not list(tmp_path.glob("bridge.json.tmp*"))
    owner.release()
    assert owner.line() == "STOPPED"


@pytest.mark.parametrize("reply", [None, b"HTTP/1.0 400 Bad Request\r\n\r\n"])
def test_busy_bridge_is_preserved_but_unrelated_service_is_stale(starters, tmp_path, reply):
    with socket.socket() as service:
        service.bind(("127.0.0.1", 0))
        service.listen()
        service.settimeout(5)
        descriptor = tmp_path / "bridge.json"
        original = json.dumps({"port": service.getsockname()[1], "token": "previous"})
        descriptor.write_text(original, encoding="utf-8")
        contender = starters()
        assert contender.line() == "ENTER"
        connection, _ = service.accept()
        with connection:
            connection.settimeout(5)
            request = b""
            while not request.endswith(b"\n"):
                request += connection.recv(4096)
            assert json.loads(request)["token"] == "nx-mcp-descriptor-probe"
            if reply is None:
                result = json.loads(contender.line())
                assert "already running" in result["error"]
                assert result["started"] == 0
                assert descriptor.read_text() == original
            else:
                connection.sendall(reply)
                assert contender.line() == "STARTED"
                contender.release()
                assert contender.line() == "STOPPED"


def test_lock_open_error_starts_no_resources(starters, tmp_path):
    (tmp_path / "bridge.lock").mkdir()
    contender = starters()
    assert contender.line() == "ENTER"
    result = json.loads(contender.line())
    assert result["started"] == result["disposed"] == 0
    assert "Timed out" not in result["error"]
    assert not (tmp_path / "bridge.json").exists()
