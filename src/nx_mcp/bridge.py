"""Local bridge primitives shared by the NX process and MCP sidecar."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import socket
import socketserver
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread, get_ident
from time import monotonic
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
            descriptor = cls(**payload)
            if (
                descriptor.host != "127.0.0.1"
                or type(descriptor.port) is not int
                or not 1 <= descriptor.port <= 65535
                or type(descriptor.pid) is not int
                or descriptor.pid < 1
                or type(descriptor.protocol_version) is not int
                or not isinstance(descriptor.token, str)
                or not descriptor.token
                or not descriptor.token.isascii()
                or not isinstance(descriptor.nx_version, str)
                or not descriptor.nx_version
            ):
                raise ValueError("Bridge descriptor fields are invalid")
            return descriptor
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
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "nx-mcp" / "bridge.json"
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home) / "nx-mcp" / "bridge.json"
    return Path.home() / ".local" / "state" / "nx-mcp" / "bridge.json"


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON numbers are not allowed")


def _decode_message(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise NXToolError("NX_PROTOCOL_ERROR", "Invalid bridge JSON") from error
    if not isinstance(payload, dict):
        raise NXToolError("NX_PROTOCOL_ERROR", "Bridge message must be an object")
    return payload


class _BridgeTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, executor: Any, token: str, read_timeout: float) -> None:
        self.executor = executor
        self.token = token
        self.read_timeout = read_timeout
        self.connection_lock = Lock()
        self.connection: socket.socket | None = None
        self.stopping = False
        super().__init__(("127.0.0.1", 0), _BridgeRequestHandler)


class _BridgeRequestHandler(socketserver.BaseRequestHandler):
    server: _BridgeTCPServer

    def handle(self) -> None:
        with self.server.connection_lock:
            if self.server.stopping:
                return
            self.server.connection = self.request
        try:
            self._respond()
        finally:
            with self.server.connection_lock:
                self.server.connection = None

    def _respond(self) -> None:
        request_id: str | int | None = None
        started = False
        response: dict[str, Any] = {
            "jsonrpc": "2.0",
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "id": None,
        }
        try:
            deadline = monotonic() + self.server.read_timeout
            raw = bytearray()
            while b"\n" not in raw:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Bridge request receive deadline expired")
                with self.server.connection_lock:
                    if self.server.stopping:
                        return
                # Windows socket shutdown does not reliably interrupt a timed receive.
                self.request.settimeout(min(remaining, 0.1))
                try:
                    chunk = self.request.recv(min(65536, _MAX_MESSAGE_BYTES + 1 - len(raw)))
                except TimeoutError:
                    continue
                if not chunk:
                    raise NXToolError("NX_PROTOCOL_ERROR", "Incomplete bridge request")
                raw.extend(chunk)
                if len(raw) > _MAX_MESSAGE_BYTES:
                    raise NXToolError("NX_REQUEST_TOO_LARGE", "Bridge request is too large")
            request = _decode_message(bytes(raw))
            candidate = request.get("id")
            if type(candidate) not in (str, int):
                raise NXToolError("NX_INVALID_REQUEST", "Bridge request ID is invalid")
            request_id = candidate
            response["id"] = request_id
            if request.get("jsonrpc") != "2.0":
                raise NXToolError("NX_PROTOCOL_ERROR", "Expected JSON-RPC 2.0")
            version = request.get("protocol_version")
            if type(version) is not int or version != BRIDGE_PROTOCOL_VERSION:
                raise NXToolError(
                    "NX_PROTOCOL_VERSION_MISMATCH", "Bridge protocol version does not match"
                )
            token = request.get("token")
            if (
                not isinstance(token, str)
                or not token.isascii()
                or not hmac.compare_digest(token, self.server.token)
            ):
                raise NXToolError("NX_AUTH_FAILED", "Bridge authentication failed")
            method, params = request.get("method"), request.get("params", {})
            if not isinstance(method, str) or not method or not isinstance(params, dict):
                raise NXToolError("NX_INVALID_REQUEST", "Bridge method and params are invalid")
            started = True
            result = self.server.executor(method, params)
            if not isinstance(result, dict):
                raise NXToolError("NX_PROTOCOL_ERROR", "Bridge result must be an object")
            response.update(ok=True, result=result)
        except NXToolError as error:
            if not started:
                error.details["execution_state"] = "not_started"
            response.update(ok=False, error=error.as_dict())
        except (OSError, ValueError) as error:
            response.update(
                ok=False,
                error=NXToolError(
                    "NX_PROTOCOL_ERROR",
                    str(error),
                    details={"execution_state": "unknown" if started else "not_started"},
                ).as_dict(),
            )
        except Exception:
            response.update(
                ok=False,
                error=NXToolError(
                    "NX_OPERATION_FAILED",
                    "Bridge execution failed",
                    details={"execution_state": "unknown"},
                ).as_dict(),
            )
        try:
            encoded = (
                json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
            )
            if len(encoded) > _MAX_MESSAGE_BYTES:
                raise ValueError("Bridge response is too large")
        except (TypeError, ValueError, RecursionError):
            encoded = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "protocol_version": BRIDGE_PROTOCOL_VERSION,
                        "id": request_id,
                        "ok": False,
                        "error": NXToolError(
                            "NX_PROTOCOL_ERROR",
                            "Bridge response cannot be encoded within the size limit",
                            details={"execution_state": "unknown" if started else "not_started"},
                        ).as_dict(),
                    }
                ).encode()
                + b"\n"
            )
        with contextlib.suppress(OSError):
            self.request.settimeout(self.server.read_timeout)
            self.request.sendall(encoded)


class BridgeServer:
    """A serialized loopback JSON-RPC server for an NX-side executor."""

    def __init__(self, executor: Any, *, token: str, read_timeout: float = 5.0) -> None:
        self._server = _BridgeTCPServer(executor, token, read_timeout)
        self._thread: Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.1),
            name="nx-mcp-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        with self._server.connection_lock:
            self._server.stopping = True
            if self._server.connection is not None:
                with contextlib.suppress(OSError):
                    self._server.connection.shutdown(socket.SHUT_RDWR)
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._server.server_close()


@dataclass
class _PendingBridgeCall:
    method: str
    params: dict[str, Any]
    deadline: float
    state: str = "queued"
    complete: Event = field(default_factory=Event)
    result: dict[str, Any] | None = None
    error: Exception | None = None


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
        self._owner = get_ident()

    @staticmethod
    def _unavailable() -> NXToolError:
        return NXToolError(
            "NX_BRIDGE_UNAVAILABLE",
            "NX bridge is stopping.",
            retryable=True,
            details={"execution_state": "not_started"},
        )

    @staticmethod
    def _timeout_error(started: bool) -> NXToolError:
        return NXToolError(
            "NX_MAIN_THREAD_UNAVAILABLE",
            "NX did not complete the request before the timeout.",
            retryable=not started,
            details={"execution_state": "unknown" if started else "not_started"},
        )

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        pending = _PendingBridgeCall(method, params, monotonic() + self._timeout)
        with self._lock:
            if self._stopped:
                raise self._unavailable()
            self._calls.put(pending)
        if not pending.complete.wait(max(0.0, pending.deadline - monotonic())):
            with self._lock:
                if pending.state == "queued":
                    pending.state = "cancelled"
                    pending.error = self._timeout_error(False)
                    pending.complete.set()
                elif pending.state == "running":
                    raise self._timeout_error(True)
        if pending.error is not None:
            raise pending.error
        if pending.result is None:
            raise NXToolError("NX_OPERATION_FAILED", "NX bridge returned no result")
        return pending.result

    def drain(self, timeout: float = 0.0, *, limit: int = 1) -> int:
        """Execute up to ``limit`` queued calls on the creating thread."""
        if get_ident() != self._owner:
            raise NXToolError(
                "NX_MAIN_THREAD_UNAVAILABLE", "NX bridge must be pumped on its creating thread."
            )
        if limit < 1:
            raise ValueError("limit must be at least 1")
        processed = 0
        while processed < limit:
            try:
                pending = self._calls.get(timeout=timeout if processed == 0 else 0)
            except Empty:
                break
            self._execute(pending)
            processed += 1
        return processed

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            while True:
                try:
                    pending = self._calls.get_nowait()
                except Empty:
                    return
                pending.state = "cancelled"
                pending.error = self._unavailable()
                pending.complete.set()

    def _execute(self, pending: _PendingBridgeCall) -> None:
        with self._lock:
            if pending.state == "cancelled":
                return
            if self._stopped or monotonic() >= pending.deadline:
                pending.state = "cancelled"
                pending.error = self._unavailable() if self._stopped else self._timeout_error(False)
                pending.complete.set()
                return
            pending.state = "running"
        result = None
        error = None
        try:
            result = self._executor(pending.method, pending.params)
        except Exception as caught:
            error = caught
        with self._lock:
            pending.result, pending.error = result, error
            pending.state = "done"
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
        try:
            encoded = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "protocol_version": BRIDGE_PROTOCOL_VERSION,
                        "id": request_id,
                        "token": self.token,
                        "method": method,
                        "params": params,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError, RecursionError) as error:
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Bridge arguments must be finite JSON values",
                details={"execution_state": "not_started"},
            ) from error
        if len(encoded) > _MAX_MESSAGE_BYTES:
            raise NXToolError(
                "NX_REQUEST_TOO_LARGE",
                "Bridge request is too large",
                details={"execution_state": "not_started"},
            )
        writer: asyncio.StreamWriter | None = None
        sent = False
        deadline = monotonic() + self.timeout
        try:
            reader, connected_writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host,
                    self.port,
                    limit=_MAX_MESSAGE_BYTES + 1,
                ),
                timeout=max(0.0, deadline - monotonic()),
            )
            writer = connected_writer
            sent = True
            connected_writer.write(encoded)
            await asyncio.wait_for(
                connected_writer.drain(), timeout=max(0.0, deadline - monotonic())
            )
            raw = await asyncio.wait_for(
                reader.readline(), timeout=max(0.0, deadline - monotonic())
            )
        except (OSError, asyncio.TimeoutError) as error:
            raise NXToolError(
                "NX_BRIDGE_UNAVAILABLE",
                "NX bridge connection failed or timed out.",
                retryable=not sent,
                details={"execution_state": "unknown" if sent else "not_started"},
            ) from error
        except ValueError as error:
            raise NXToolError(
                "NX_PROTOCOL_ERROR",
                "NX bridge returned an oversized response",
                details={"execution_state": "unknown"},
            ) from error
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError, asyncio.TimeoutError):
                    await asyncio.wait_for(
                        writer.wait_closed(), timeout=max(0.0, deadline - monotonic())
                    )
        try:
            if not raw.endswith(b"\n") or len(raw) > _MAX_MESSAGE_BYTES:
                raise NXToolError(
                    "NX_PROTOCOL_ERROR", "NX bridge returned an invalid response size"
                )
            response = _decode_message(raw)
            if (
                response.get("jsonrpc") != "2.0"
                or response.get("id") != request_id
                or type(response.get("protocol_version")) is not int
                or response["protocol_version"] != BRIDGE_PROTOCOL_VERSION
                or type(response.get("ok")) is not bool
            ):
                raise NXToolError("NX_PROTOCOL_ERROR", "NX bridge returned an invalid response")
            if response["ok"]:
                result = response.get("result")
                if not isinstance(result, dict) or "error" in response:
                    raise NXToolError("NX_PROTOCOL_ERROR", "NX bridge result must be an object")
                return result
            payload = response.get("error")
            if (
                not isinstance(payload, dict)
                or "result" in response
                or not isinstance(payload.get("code"), str)
                or not payload["code"]
                or not isinstance(payload.get("message"), str)
                or type(payload.get("retryable", False)) is not bool
                or (payload.get("details") is not None and not isinstance(payload["details"], dict))
                or (
                    payload.get("suggestion") is not None
                    and not isinstance(payload["suggestion"], str)
                )
                or (
                    payload.get("nx_code") is not None
                    and type(payload["nx_code"]) not in (int, str)
                )
            ):
                raise NXToolError("NX_PROTOCOL_ERROR", "NX bridge returned an invalid error")
        except NXToolError as error:
            error.details["execution_state"] = "unknown"
            raise
        details = payload.get("details") or {}
        raise NXToolError(
            payload["code"],
            payload["message"],
            suggestion=payload.get("suggestion"),
            nx_code=payload.get("nx_code"),
            details=details,
            retryable=payload.get("retryable", False)
            and details.get("execution_state") == "not_started",
        )


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
                details={"execution_state": "not_started"},
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
        self._objects: dict[str, _ObjectEntry] = {}
        self._stale_ids: set[str] = set()
        self._identities: dict[tuple[str, ObjectKind, str], str] = {}

    def register(self, value: Any, *, kind: ObjectKind, name: str, part_id: str) -> ObjectRef:
        native_identity = getattr(value, "Tag", None)
        identity = str(native_identity if native_identity is not None else id(value))
        identity_key = (part_id, kind, identity)
        if object_id := self._identities.get(identity_key):
            return self._objects[object_id].reference
        reference = ObjectRef(
            id=f"obj_{uuid4().hex}",
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
            code = "NX_OBJECT_STALE" if object_id in self._stale_ids else "NX_OBJECT_NOT_FOUND"
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
