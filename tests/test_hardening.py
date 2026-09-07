"""Safety and MCP contract regressions; these do not substitute for real NX tests."""

import asyncio
import base64
import hashlib
from threading import Event
from types import SimpleNamespace

import pytest

from nx_mcp.bridge import BridgeClient, BridgeServer
from nx_mcp.hardened import HardenedExecutor
from nx_mcp.integration_server import artifact_call
from nx_mcp.runtime import NXToolError
from nx_mcp.server import create_server
from nx_mcp.workspace import Workspace, WorkspaceViolation


class FakeSession:
    def __init__(self):
        self.Parts = SimpleNamespace(Work=None)
        self.values = []
        self.marks = {}
        self.next_mark = 0

    def SetUndoMark(self, *args):
        self.next_mark += 1
        self.marks[self.next_mark] = list(self.values)
        return self.next_mark

    def UndoToMark(self, mark, *args):
        self.values[:] = self.marks[mark]

    def DeleteUndoMark(self, mark, *args):
        self.marks.pop(mark, None)

    def DoesUndoMarkExist(self, mark, *args):
        return mark in self.marks


@pytest.fixture
def executor(tmp_path):
    session = FakeSession()
    e = HardenedExecutor(
        session,
        SimpleNamespace(Session=SimpleNamespace(MarkVisibility=SimpleNamespace(Visible=1))),
        "test",
        Workspace(tmp_path),
        enable_experimental=True,
    )

    def mutate(value=1, fail=False):
        session.values.append(value)
        if fail:
            raise NXToolError("TEST_FAIL", "failure after a model change")
        return {"values": list(session.values)}

    e._handlers["nx_test_mutate"] = mutate
    return e


def test_retry_committed_mutation_is_deduplicated(executor):
    p = {"value": 1, "operation_id": "same-request-123"}
    a = executor.execute("nx_test_mutate", p)
    b = executor.execute("nx_test_mutate", p)
    assert executor.session.values == [1]
    assert b["replayed"] and a["operation_id"] == b["operation_id"]
    with pytest.raises(NXToolError, match="different arguments"):
        executor.execute("nx_test_mutate", dict(p, value=2))


def test_failed_partial_mutation_rolls_back_and_cannot_reapply(executor):
    p = {"value": 4, "fail": True, "operation_id": "failed-request-123"}
    with pytest.raises(NXToolError) as error:
        executor.execute("nx_test_mutate", p)
    assert error.value.details["mutation_outcome"] == "rolled_back"
    assert executor.session.values == []
    assert executor.store.get(p["operation_id"])["state"] == "failed"
    with pytest.raises(NXToolError):
        executor.execute("nx_test_mutate", p)
    assert executor.session.values == []


def test_restarted_pending_receipt_becomes_unknown(executor):
    store = executor.store
    store.put(
        {
            "operation_id": "crashed-request-123",
            "session_id": "old",
            "state": "running",
            "fingerprint": "a",
        }
    )
    store.recover(executor.session_id)
    assert store.get("crashed-request-123")["state"] == "unknown"
    assert store.get("never-seen-request")["mutation_outcome"] == "unknown"


@pytest.mark.asyncio
async def test_transport_timeout_does_not_duplicate_mutation(executor):
    handler = executor._handlers["nx_test_mutate"]
    entered, release = Event(), Event()

    def delayed(**params):
        entered.set()
        assert release.wait(15), "Test did not release the received mutation"
        return handler(**params)

    executor._handlers["nx_test_mutate"] = delayed
    server = BridgeServer(executor.execute, token="test-token")
    server.start()
    p = {"value": 9, "operation_id": "lost-response-123"}
    first = asyncio.create_task(
        BridgeClient("127.0.0.1", server.port, token="test-token", timeout=10).call(
            "nx_test_mutate", p
        )
    )
    try:
        # Start the response deadline only after the request reached the server.
        # A 20ms connect deadline can expire before receipt on Windows CI.
        assert await asyncio.to_thread(entered.wait, 5), "Server did not receive first request"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first, timeout=0.02)
        release.set()
        result = await BridgeClient("127.0.0.1", server.port, token="test-token", timeout=10).call(
            "nx_test_mutate", p
        )
        assert result["replayed"] and executor.session.values == [9]
    finally:
        release.set()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        server.stop()


@pytest.mark.parametrize(
    "path", ["../outside.step", "/tmp/outside.step", ".nx-mcp/operations/x.json"]
)
def test_artifacts_reject_outside_or_reserved_paths(tmp_path, path):
    with pytest.raises((WorkspaceViolation, NXToolError)):
        artifact_call(
            "nx_download_file", {"path": path, "offset": 0, "length": 1}, Workspace(tmp_path)
        )


def test_chunk_upload_retry_checksum_download_and_no_overwrite(tmp_path):
    w = Workspace(tmp_path)
    data = b"0123456789" * 100
    digest = hashlib.sha256(data).hexdigest()

    def upload(offset, chunk, sha=digest):
        return artifact_call(
            "nx_upload_file",
            {
                "path": "vendor/test.step",
                "offset": offset,
                "total_size": len(data),
                "sha256": sha,
                "data_base64": base64.b64encode(chunk).decode(),
            },
            w,
        )

    assert not upload(0, data[:400])["committed"]
    assert not upload(0, data[:400])["committed"]
    assert upload(400, data[400:])["committed"]
    assert upload(400, data[400:])["replayed"]
    result = artifact_call(
        "nx_download_file", {"path": "vendor/test.step", "offset": 0, "length": 262144}, w
    )
    assert base64.b64decode(result["data_base64"]) == data
    assert result["sha256"] == digest and result["eof"]
    with pytest.raises(NXToolError):
        upload(0, b"x" * 1000, "0" * 64)
    assert (tmp_path / "vendor/test.step").read_bytes() == data


@pytest.mark.asyncio
async def test_uniform_structured_success_failure_and_schema(tmp_path):
    class Caller:
        async def call(self, method, params):
            if method == "nx_edit_feature":
                raise NXToolError("NX_UNSUPPORTED_EDIT", "unsupported edit")
            return {"status": "success", "method": method, "params": params}

    server = create_server(Caller(), Workspace(tmp_path), enable_experimental=True)
    tools = await server.list_tools()
    pattern = next(t for t in tools if t.name == "nx_pattern")
    assert pattern.inputSchema["properties"]["pattern_type"]["const"] == "linear"
    assert "operation_id" in pattern.inputSchema["properties"]
    success = await server.call_tool("nx_status", {})
    assert success.structuredContent["status"] == "success" and not success.isError
    for name, args in [
        ("nx_edit_feature", {"name": "f", "params": {"x": 1}}),
        ("nx_pattern", {"features": [], "ignored": 123}),
    ]:
        result = await server.call_tool(name, args)
        assert result.isError and result.structuredContent["status"] == "error"
    assert not next(t for t in tools if t.name == "nx_sketch_rectangle").annotations.readOnlyHint


def test_reference_namespace_rejects_previous_session(executor, tmp_path):
    obj = SimpleNamespace(Tag=123, Name="Body")
    first = executor.objects.register(obj, kind="body", name="Body", part_id="part_test")
    assert first.id.startswith("obj_" + executor.session_id + "_")
    another = HardenedExecutor(
        FakeSession(),
        SimpleNamespace(Session=SimpleNamespace(MarkVisibility=SimpleNamespace(Visible=1))),
        "test",
        Workspace(tmp_path / "other"),
        enable_experimental=True,
    )
    with pytest.raises(NXToolError) as error:
        another.objects.resolve(first.id)
    assert error.value.code == "NX_OBJECT_STALE"
