"""Local bridge primitives shared by the NX process and MCP sidecar."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import socketserver
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from nx_mcp.runtime import NXToolError, ObjectKind, ObjectRef

BRIDGE_PROTOCOL_VERSION = 1
_MAX_MESSAGE_BYTES = 1024 * 1024


@dataclass
class BridgeDescriptor:
    port: int
    token: str
    pid: int
    nx_version: str
    protocol_version: int = BRIDGE_PROTOCOL_VERSION
    host: str = "127.0.0.1"

    @classmethod
    def create(
        cls,
        port: int,
        nx_version: str,
        *,
        token: str | None = None,
        pid: int | None = None,
    ) -> BridgeDescriptor:
        return cls(
            port=port,
            token=token or secrets.token_hex(32),
            pid=pid or os.getpid(),
            nx_version=nx_version,
        )

    @classmethod
    def read(cls, path: str | Path) -> BridgeDescriptor:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Bridge descriptor must be a JSON object")
        try:
            return cls(**payload)
        except TypeError as error:
            raise ValueError("Bridge descriptor is invalid") from error

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            temporary.chmod(0o600)
        temporary.replace(destination)


def default_descriptor_path() -> Path:
    if configured := os.environ.get("NX_MCP_BRIDGE_DESCRIPTOR"):
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "nx-mcp" / "bridge.json"
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home) / "nx-mcp" / "bridge.json"
    return Path.home() / ".local" / "state" / "nx-mcp" / "bridge.json"


def _error_payload(error: NXToolError) -> dict[str, Any]:
    return error.as_dict()


class _BridgeTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

    executor: Any
    token: str

    def __init__(self, executor: Any, token: str, result_directory: Path | None = None) -> None:
        self.result_directory = result_directory
        self.executor = executor
        self.token = token
        super().__init__(("127.0.0.1", 0), _BridgeRequestHandler)


class _BridgeRequestHandler(socketserver.StreamRequestHandler):
    server: _BridgeTCPServer

    def handle(self) -> None:
        raw = self.rfile.readline(_MAX_MESSAGE_BYTES + 1)
        request_id: str | None = None
        try:
            if len(raw) > _MAX_MESSAGE_BYTES:
                raise NXToolError("NX_REQUEST_TOO_LARGE", "Bridge request is too large")
            request = json.loads(raw)
            request_id = request.get("id")
            if request.get("jsonrpc") != "2.0":
                raise NXToolError("NX_PROTOCOL_ERROR", "Expected JSON-RPC 2.0")
            if request.get("protocol_version") != BRIDGE_PROTOCOL_VERSION:
                raise NXToolError(
                    "NX_PROTOCOL_VERSION_MISMATCH", "Bridge protocol version does not match"
                )
            if not hmac.compare_digest(str(request.get("token", "")), self.server.token):
                raise NXToolError("NX_AUTH_FAILED", "Bridge authentication failed")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise NXToolError("NX_INVALID_REQUEST", "Bridge method and params are invalid")
            result = self.server.executor(method, params)
            from nx_mcp.result_transport import bound_result

            result = bound_result(result, self.server.result_directory)
            response = {
                "jsonrpc": "2.0",
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "id": request_id,
                "ok": True,
                "result": result,
            }
        except NXToolError as error:
            response = {
                "jsonrpc": "2.0",
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "id": request_id,
                "ok": False,
                "error": _error_payload(error),
            }
        except Exception as error:
            response = {
                "jsonrpc": "2.0",
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "id": request_id,
                "ok": False,
                "error": NXToolError("NX_OPERATION_FAILED", str(error)).as_dict(),
            }
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")


class _ThreadedBridgeTCPServer(socketserver.ThreadingMixIn, _BridgeTCPServer):
    daemon_threads = True


class BridgeServer:
    """A serialized loopback JSON-RPC server for an NX-side executor."""

    def __init__(
        self,
        executor: Any,
        *,
        token: str,
        result_directory: Path | None = None,
        concurrent_requests: bool = False,
    ) -> None:
        # Interactive callers serialize NXOpen through MainThreadDispatcher.
        # Concurrent socket handling permits cached health reads during work;
        # the batch/default executor retains its original serialized transport.
        server_type = _ThreadedBridgeTCPServer if concurrent_requests else _BridgeTCPServer
        self._server = server_type(executor, token, result_directory)
        self._thread: Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(target=self._server.serve_forever, name="nx-mcp-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._thread = None


@dataclass
class _PendingBridgeCall:
    method: str
    params: dict[str, Any]
    complete: Event = field(default_factory=Event)
    result: dict[str, Any] | None = None
    error: Exception | None = None
    started: bool = False
    cancelled: bool = False


class MainThreadDispatcher:
    """Queues bridge calls for explicit execution by the NX journal thread."""

    def __init__(
        self,
        executor: Callable[[str, dict[str, Any]], dict[str, Any]],
        *,
        timeout: float = 120.0,
    ) -> None:
        self._executor = executor
        self._timeout = timeout
        self._calls: Queue[_PendingBridgeCall] = Queue()
        self._lock = Lock()
        self._stopped = False

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        pending = _PendingBridgeCall(method, params)
        with self._lock:
            if self._stopped:
                raise NXToolError(
                    "NX_BRIDGE_UNAVAILABLE",
                    "NX bridge is stopping.",
                    retryable=True,
                )
            self._calls.put(pending)

        if not pending.complete.wait(self._timeout):
            with self._lock:
                if not pending.started:
                    pending.cancelled = True
                outcome = "unknown" if pending.started else "not_started"
            raise NXToolError(
                "NX_MAIN_THREAD_UNAVAILABLE",
                "NX did not complete the bridge request before the timeout. Query the operation receipt before retrying a started mutation.",
                retryable=not pending.started,
                details={"mutation_outcome": outcome},
            )
        if pending.error is not None:
            raise pending.error
        if pending.result is None:
            raise NXToolError("NX_OPERATION_FAILED", "NX bridge returned no result")
        return pending.result

    def drain(self, timeout: float = 0.0, *, limit: int = 1) -> int:
        """Execute up to ``limit`` pending calls on the calling thread."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        try:
            pending = self._calls.get(timeout=timeout)
        except Empty:
            return 0

        processed = 0
        while True:
            self._execute(pending)
            processed += 1
            if processed == limit:
                return processed
            try:
                pending = self._calls.get_nowait()
            except Empty:
                return processed

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            while True:
                try:
                    pending = self._calls.get_nowait()
                except Empty:
                    return
                pending.error = NXToolError(
                    "NX_BRIDGE_UNAVAILABLE",
                    "NX bridge is stopping.",
                    retryable=True,
                )
                pending.complete.set()

    def _execute(self, pending: _PendingBridgeCall) -> None:
        with self._lock:
            if self._stopped or pending.cancelled:
                pending.error = NXToolError(
                    "NX_BRIDGE_UNAVAILABLE",
                    "Bridge stopped or queued request expired before execution.",
                    retryable=True,
                    details={"mutation_outcome": "not_started"},
                )
                pending.complete.set()
                return
            pending.started = True
        # The queue consumer is serialized; release the queue lock so a waiting
        # caller can distinguish queued expiry from a running, unknown outcome.
        try:
            pending.result = self._executor(pending.method, pending.params)
        except Exception as error:
            pending.error = error
        finally:
            pending.complete.set()


class BridgeClient:
    def __init__(self, host: str, port: int, *, token: str, timeout: float = 120.0) -> None:
        if host != "127.0.0.1":
            raise ValueError("NX bridge must use the 127.0.0.1 loopback address")
        self.host = host
        self.port = port
        self.token = token
        self.timeout = timeout

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = uuid4().hex
        request = {
            "jsonrpc": "2.0",
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "id": request_id,
            "token": self.token,
            "method": method,
            "params": params,
        }
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host,
                    self.port,
                    limit=_MAX_MESSAGE_BYTES + 1,
                ),
                timeout=self.timeout,
            )
            assert writer is not None
            writer.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        except ValueError as error:
            raise NXToolError(
                "NX_RESPONSE_TOO_LARGE",
                "Bridge response exceeded framing limit; inspect operation status before retry",
                details={"operation_id": params.get("operation_id"), "mutation_outcome": "unknown"},
            ) from error
        except (OSError, asyncio.TimeoutError) as error:
            raise NXToolError(
                "NX_BRIDGE_UNAVAILABLE",
                f"NX bridge is unavailable: {error}",
                retryable=True,
            ) from error
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()

        if not raw or len(raw) > _MAX_MESSAGE_BYTES:
            raise NXToolError("NX_PROTOCOL_ERROR", "NX bridge returned an invalid response size")
        try:
            response = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as parse_error:
            raise NXToolError(
                "NX_PROTOCOL_ERROR", "NX bridge returned invalid JSON"
            ) from parse_error
        if (
            response.get("id") != request_id
            or response.get("protocol_version") != BRIDGE_PROTOCOL_VERSION
        ):
            raise NXToolError("NX_PROTOCOL_ERROR", "NX bridge returned an invalid response")
        if not response.get("ok"):
            error_payload = response.get("error", {})
            raise NXToolError(
                error_payload.get("code", "NX_OPERATION_FAILED"),
                error_payload.get("message", "NX operation failed"),
                suggestion=error_payload.get("suggestion"),
                nx_code=error_payload.get("nx_code"),
                retryable=error_payload.get("retryable", False),
                details=error_payload.get("details"),
            )
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise NXToolError("NX_PROTOCOL_ERROR", "NX bridge result must be an object")
        return result


class DescriptorBridgeClient:
    """Reloads the bridge descriptor for every call so NX can restart independently."""

    def __init__(
        self, descriptor_path: str | Path | None = None, *, timeout: float = 120.0
    ) -> None:
        self.descriptor_path = (
            Path(descriptor_path) if descriptor_path else default_descriptor_path()
        )
        self.timeout = timeout

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            descriptor = BridgeDescriptor.read(self.descriptor_path)
        except (OSError, ValueError) as error:
            raise NXToolError(
                "NX_BRIDGE_UNAVAILABLE",
                "Start the NX MCP bridge inside Siemens NX before calling tools.",
                retryable=True,
            ) from error
        if descriptor.protocol_version != BRIDGE_PROTOCOL_VERSION:
            raise NXToolError(
                "NX_PROTOCOL_VERSION_MISMATCH",
                "NX bridge and sidecar use different protocol versions.",
            )
        return await BridgeClient(
            descriptor.host,
            descriptor.port,
            token=descriptor.token,
            timeout=self.timeout,
        ).call(method, params)


@dataclass
class _ObjectEntry:
    value: Any
    reference: ObjectRef


class ObjectRegistry:
    """Maps opaque, session-scoped IDs to live NXOpen objects."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self._objects: dict[str, _ObjectEntry] = {}
        self._stale_ids: set[str] = set()
        self._identities: dict[tuple[str, ObjectKind, str], str] = {}

    def register(self, value: Any, *, kind: ObjectKind, name: str, part_id: str) -> ObjectRef:
        native_identity = getattr(value, "Tag", None)
        identity = str(native_identity if native_identity is not None else id(value))
        identity_key = (part_id, kind, identity)
        if object_id := self._identities.get(identity_key):
            entry = self._objects[object_id]
            if entry.reference.name != name:
                entry.reference = replace(entry.reference, name=name)
            return entry.reference
        reference = ObjectRef(
            id=f"obj_{getattr(self, 'session_id', '') + '_' if getattr(self, 'session_id', None) else ''}{uuid4().hex}",
            kind=kind,
            name=name,
            part_id=part_id,
        )
        self._objects[reference.id] = _ObjectEntry(value=value, reference=reference)
        self._identities[identity_key] = reference.id
        return reference

    def resolve(
        self,
        object_id: str,
        *,
        expected_kind: ObjectKind | None = None,
        part_id: str | None = None,
    ) -> Any:
        entry = self._objects.get(object_id)
        if entry is None:
            foreign_session = bool(
                self.session_id is not None
                and not object_id.startswith("obj_" + self.session_id + "_")
            )
            code = (
                "NX_OBJECT_STALE"
                if object_id in self._stale_ids or foreign_session
                else "NX_OBJECT_NOT_FOUND"
            )
            raise NXToolError(code, f"Object reference is not valid: {object_id}")
        if expected_kind is not None and entry.reference.kind != expected_kind:
            raise NXToolError(
                "NX_OBJECT_TYPE_MISMATCH",
                f"Expected {expected_kind}, got {entry.reference.kind}",
            )
        if part_id is not None and entry.reference.part_id != part_id:
            raise NXToolError(
                "NX_OBJECT_STALE",
                "Object reference belongs to a different work part",
            )
        return entry.value

    def invalidate_part(self, part_id: str) -> None:
        invalid_ids = [
            object_id
            for object_id, entry in self._objects.items()
            if entry.reference.part_id == part_id
        ]
        for object_id in invalid_ids:
            del self._objects[object_id]
            self._stale_ids.add(object_id)
        self._identities = {
            key: object_id for key, object_id in self._identities.items() if key[0] != part_id
        }
