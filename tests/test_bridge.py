from pathlib import Path
from threading import Thread, get_ident

import pytest

from nx_mcp.bridge import (
    BridgeClient,
    BridgeDescriptor,
    BridgeServer,
    DescriptorBridgeClient,
    MainThreadDispatcher,
    default_descriptor_path,
)
from nx_mcp.contracts import NXToolError

pytestmark = pytest.mark.integration


def test_default_descriptor_path_honors_explicit_state_directory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NX_MCP_STATE_DIR", "C:/shared-state")
    monkeypatch.setenv("LOCALAPPDATA", "C:/nx-state")

    assert default_descriptor_path() == Path("C:/shared-state") / "bridge.json"


def test_default_descriptor_path_prefers_local_app_data(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NX_MCP_STATE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "C:/nx-state")
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg-state")

    assert default_descriptor_path() == Path("C:/nx-state") / "nx-mcp" / "bridge.json"


def test_default_descriptor_path_uses_xdg_state_when_local_app_data_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("NX_MCP_STATE_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg-state")

    assert default_descriptor_path() == Path("/xdg-state") / "nx-mcp" / "bridge.json"


def test_default_descriptor_path_uses_home_state_as_final_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("NX_MCP_STATE_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    assert default_descriptor_path() == Path.home() / ".local" / "state" / "nx-mcp" / "bridge.json"


@pytest.mark.asyncio
async def test_bridge_round_trip_uses_versioned_authenticated_json_rpc():
    server = BridgeServer(
        lambda method, params: {"method": method, "params": params},
        token="test-token",
    )
    server.start()
    try:
        client = BridgeClient("127.0.0.1", server.port, token="test-token")

        result = await client.call("nx_status", {"detail": True})

        assert result == {"method": "nx_status", "params": {"detail": True}}
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_descriptor_client_discovers_a_restarted_bridge(tmp_path: Path):
    descriptor_path = tmp_path / "bridge.json"
    first = BridgeServer(lambda method, params: {"generation": 1}, token="first-token")
    first.start()
    BridgeDescriptor.create(first.port, "NX test", token="first-token").write(descriptor_path)
    client = DescriptorBridgeClient(descriptor_path)
    try:
        assert await client.call("nx_status", {}) == {"generation": 1}
    finally:
        first.stop()

    second = BridgeServer(lambda method, params: {"generation": 2}, token="second-token")
    second.start()
    BridgeDescriptor.create(second.port, "NX test", token="second-token").write(descriptor_path)
    try:
        assert await client.call("nx_status", {}) == {"generation": 2}
    finally:
        second.stop()


@pytest.mark.asyncio
async def test_bridge_rejects_invalid_session_token():
    server = BridgeServer(lambda method, params: {}, token="correct-token")
    server.start()
    try:
        client = BridgeClient("127.0.0.1", server.port, token="wrong-token")
        with pytest.raises(NXToolError) as error:
            await client.call("nx_status", {})
    finally:
        server.stop()

    assert error.value.code == "NX_AUTH_FAILED"


@pytest.mark.asyncio
async def test_descriptor_client_fails_closed_on_protocol_mismatch(tmp_path: Path):
    descriptor_path = tmp_path / "bridge.json"
    descriptor = BridgeDescriptor.create(12345, "NX test", token="token")
    descriptor.protocol_version = 999
    descriptor.write(descriptor_path)

    with pytest.raises(NXToolError) as error:
        await DescriptorBridgeClient(descriptor_path).call("nx_status", {})

    assert error.value.code == "NX_PROTOCOL_VERSION_MISMATCH"


@pytest.mark.asyncio
async def test_bridge_supports_responses_larger_than_default_stream_limit():
    payload = "x" * 100_000
    server = BridgeServer(lambda method, params: {"payload": payload}, token="token")
    server.start()
    try:
        result = await BridgeClient("127.0.0.1", server.port, token="token").call(
            "nx_list_features", {}
        )
    finally:
        server.stop()

    assert result == {"payload": payload}


def test_main_thread_dispatcher_executes_requests_only_when_pumped():
    executed_by: list[int] = []
    dispatcher = MainThreadDispatcher(
        lambda method, params: executed_by.append(get_ident()) or {"method": method}
    )
    result: dict[str, object] = {}
    caller = Thread(
        target=lambda: result.update(dispatcher.call("nx_status", {})),
        daemon=True,
    )

    caller.start()

    assert dispatcher.drain(timeout=1) == 1
    caller.join(timeout=1)
    assert not caller.is_alive()
    assert result == {"method": "nx_status"}
    assert executed_by == [get_ident()]
