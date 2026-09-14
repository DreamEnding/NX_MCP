"""Protocol and scheduling regressions; no live NX is involved."""

import asyncio
import json
import socket
from dataclasses import asdict
from threading import Event, Thread
from time import monotonic

import pytest

from nx_mcp.bridge import (
    BridgeClient,
    BridgeDescriptor,
    BridgeServer,
    DescriptorBridgeClient,
    MainThreadDispatcher,
)
from nx_mcp.runtime import NXToolError

pytestmark = pytest.mark.integration


def test_timed_out_queued_request_never_executes():
    executed = []
    dispatcher = MainThreadDispatcher(
        lambda method, params: executed.append(method) or {}, timeout=0.01
    )
    with pytest.raises(NXToolError) as caught:
        dispatcher.call("nx_create_sketch", {})
    dispatcher.drain()
    assert executed == []
    assert caught.value.retryable
    assert caught.value.details == {"execution_state": "not_started"}


def test_running_timeout_is_not_retryable():
    caller_finished = Event()
    errors = []

    def execute(method, params):
        assert caller_finished.wait(2)
        return {}

    dispatcher = MainThreadDispatcher(execute, timeout=0.1)

    def call():
        try:
            dispatcher.call("nx_create_sketch", {})
        except NXToolError as error:
            errors.append(error)
        finally:
            caller_finished.set()

    caller = Thread(target=call)
    caller.start()
    dispatcher.drain(timeout=1)
    caller.join(2)
    assert not caller.is_alive()
    assert not errors[0].retryable
    assert errors[0].details == {"execution_state": "unknown"}


def test_dispatcher_rejects_pumping_from_another_thread():
    dispatcher = MainThreadDispatcher(lambda method, params: {})
    errors = []

    def pump():
        try:
            dispatcher.drain()
        except NXToolError as error:
            errors.append(error)

    thread = Thread(target=pump)
    thread.start()
    thread.join(1)
    assert errors and errors[0].code == "NX_MAIN_THREAD_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "localhost"),
        ("port", True),
        ("port", 70000),
        ("token", 4),
        ("token", "é"),
        ("pid", 0),
        ("nx_version", []),
        ("protocol_version", True),
    ],
)
async def test_invalid_descriptor_fails_with_stable_error(tmp_path, monkeypatch, field, value):
    async def unexpected_connection(*args, **kwargs):
        pytest.fail("Invalid descriptors must fail before connecting")

    monkeypatch.setattr(asyncio, "open_connection", unexpected_connection)
    payload = asdict(BridgeDescriptor.create(12345, "fake", token="test-token"))
    payload[field] = value
    path = tmp_path / "bridge.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(NXToolError) as caught:
        await DescriptorBridgeClient(path).call("nx_status", {})
    assert caught.value.code == "NX_BRIDGE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["array", "error_array", "wrong_jsonrpc", "truthy_ok", "large", "nonfinite"]
)
async def test_invalid_response_is_a_protocol_error(variant):
    async def reply(reader, writer):
        request = json.loads(await reader.readline())
        payload = {
            "jsonrpc": "2.0",
            "protocol_version": 1,
            "id": request["id"],
            "ok": True,
            "result": {},
        }
        if variant == "array":
            payload = []
        elif variant == "error_array":
            payload.update(ok=False, error=[])
        elif variant == "wrong_jsonrpc":
            payload["jsonrpc"] = "1.0"
        elif variant == "truthy_ok":
            payload["ok"] = "yes"
        elif variant == "large":
            payload["result"] = {"data": "x" * (1024 * 1024 + 100)}
        else:
            payload["result"] = {"data": float("nan")}
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(reply, "127.0.0.1", 0) as server:
        client = BridgeClient("127.0.0.1", server.sockets[0].getsockname()[1], token="test-token")
        with pytest.raises(NXToolError) as caught:
            await client.call("nx_status", {})
    assert caught.value.code == "NX_PROTOCOL_ERROR"
    assert not caught.value.retryable
    assert caught.value.details == {"execution_state": "unknown"}


@pytest.mark.asyncio
async def test_oversized_outgoing_request_is_not_executed():
    calls = []
    server = BridgeServer(lambda method, params: calls.append(method) or {}, token="test-token")
    server.start()
    try:
        with pytest.raises(NXToolError) as caught:
            await BridgeClient("127.0.0.1", server.port, token="test-token").call(
                "nx_status", {"data": "x" * 1024 * 1024}
            )
        assert caught.value.code == "NX_REQUEST_TOO_LARGE"
        assert calls == []
    finally:
        server.stop()


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", True),
        ("jsonrpc", "1.0"),
        ("protocol_version", True),
        ("token", "é"),
        ("method", []),
        ("params", []),
    ],
)
def test_invalid_request_never_reaches_executor(field, value):
    calls = []
    server = BridgeServer(lambda method, params: calls.append(method) or {}, token="test-token")
    request = {
        "jsonrpc": "2.0",
        "protocol_version": 1,
        "id": "test",
        "token": "test-token",
        "method": "nx_status",
        "params": {},
    }
    request[field] = value
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as connection:
            connection.sendall(json.dumps(request).encode() + b"\n")
            with connection.makefile("rb") as response:
                payload = json.loads(response.readline())
        assert payload["ok"] is False
        assert payload["error"]["details"]["execution_state"] == "not_started"
        assert calls == []
    finally:
        server.stop()


def test_partial_receive_has_total_deadline():
    calls = []
    server = BridgeServer(
        lambda method, params: calls.append(method) or {}, token="test-token", read_timeout=0.1
    )
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as connection:
            connection.sendall(b'{"jsonrpc":')
            started = monotonic()
            with connection.makefile("rb") as response:
                payload = json.loads(response.readline())
            assert monotonic() - started < 1.5
        assert payload["error"]["details"]["execution_state"] == "not_started"
        assert calls == []
    finally:
        server.stop()


def test_stop_interrupts_partial_receive():
    server = BridgeServer(lambda method, params: {}, token="test-token", read_timeout=30)
    server.start()
    with socket.create_connection(("127.0.0.1", server.port), timeout=2) as connection:
        connection.sendall(b"{")
        started = monotonic()
        server.stop()
        assert monotonic() - started < 2


def test_stopped_dispatcher_never_accepts_work():
    dispatcher = MainThreadDispatcher(lambda method, params: pytest.fail("must not execute"))
    dispatcher.stop()
    with pytest.raises(NXToolError) as caught:
        dispatcher.call("nx_status", {})
    assert caught.value.retryable
    assert caught.value.details == {"execution_state": "not_started"}


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{"value": float("nan")}, {"value": "x" * 1024 * 1024}, []])
async def test_unencodable_or_invalid_executor_result_is_protocol_error(result):
    server = BridgeServer(lambda method, params: result, token="test-token")
    server.start()
    try:
        with pytest.raises(NXToolError) as caught:
            await BridgeClient("127.0.0.1", server.port, token="test-token").call("nx_status", {})
        assert caught.value.code == "NX_PROTOCOL_ERROR"
        assert not caught.value.retryable
    finally:
        server.stop()


def test_stop_cancels_queued_calls():
    entered = Event()
    errors = []
    dispatcher = MainThreadDispatcher(lambda method, params: pytest.fail("must not execute"))
    original_put = dispatcher._calls.put

    def put(pending):
        original_put(pending)
        entered.set()

    dispatcher._calls.put = put

    def call():
        try:
            dispatcher.call("nx_create_sketch", {})
        except NXToolError as error:
            errors.append(error)

    caller = Thread(target=call)
    caller.start()
    try:
        assert entered.wait(2)
        dispatcher.stop()
        caller.join(2)
        assert not caller.is_alive()
        assert errors[0].details == {"execution_state": "not_started"}
        assert dispatcher.drain() == 0
    finally:
        dispatcher.stop()
        caller.join(2)


def test_trickled_request_cannot_extend_receive_deadline():
    calls = []
    server = BridgeServer(
        lambda method, params: calls.append(method) or {}, token="test-token", read_timeout=0.15
    )
    done = Event()
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as connection:

            def trickle():
                while not done.wait(0.02):
                    try:
                        connection.sendall(b" ")
                    except OSError:
                        return

            sender = Thread(target=trickle)
            started = monotonic()
            sender.start()
            try:
                with connection.makefile("rb") as response:
                    payload = json.loads(response.readline())
                assert monotonic() - started < 1.5
                assert payload["error"]["details"]["execution_state"] == "not_started"
                assert calls == []
            finally:
                done.set()
                sender.join(2)
    finally:
        server.stop()
