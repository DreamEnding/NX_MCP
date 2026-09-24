# NX MCP 0.2 architecture

## MCP compatibility boundary

The external stdio service is an official SDK v2 `MCPServer`, with explicit
project name and version metadata. The SDK serves modern `server/discover`
and pre-2026 `initialize` clients; NX MCP does not implement its own MCP
negotiation or framing. The smoke runner uses `Client(StdioServerParameters(...))`
so it negotiates on entry instead of always invoking the old handshake.

Python SDK fields are now snake_case (`is_error`, `structured_content`,
`input_schema`, `output_schema`); SDK serialization still uses the MCP wire
aliases (`isError`, `structuredContent`, `inputSchema`, `outputSchema`). No
CAD tool signatures or NX bridge messages change as part of the SDK upgrade.
Modern in-process SDK clients can dispatch without JSON-RPC, so the test suite
also checks actual subprocess stdio in both modern and legacy modes.

## Runtime boundary

The MCP sidecar owns stdio, type validation, structured MCP results, and path
resolution. It never imports NXOpen. The manually loaded NX bridge owns the
live NX session, object registry, undo marks, builders, and all NXOpen calls.

The bridge listens on `127.0.0.1` using a random port. Its descriptor contains
the protocol version, host, port, random token, NX PID, and exact NX version.
The sidecar reloads the descriptor for every call, so restarting NX does not
leave a permanently disconnected singleton.

Both bridges publish that descriptor under the same rules, so two NX processes
sharing a state directory cannot both claim it. A starting bridge holds an
exclusive lock on `bridge.lock` beside the descriptor for the whole
check-then-publish sequence, and for the token check its own shutdown makes
before deleting. It refuses to start when the descriptor's port still answers
as a bridge: the probe carries a deliberately invalid token, which either
bridge rejects on its socket thread without waiting for NX. Nothing listening,
a closed connection, or a reply that is not a bridge response proves the port
belongs to something else; silence does not, because a bridge busy with a call
answers late. Each writer publishes through a temporary of its own, named after
its process, and removes only that one.

Requests are newline-delimited JSON-RPC objects containing `protocol_version`,
`id`, `token`, `method`, and `params`. Responses echo the ID and contain either
`result` or a stable error with `code`, `message`, optional `suggestion`, and
optional native `nx_code`. Requests and responses (including the newline) are limited to 1 MiB. Non-object
JSON, non-finite numbers, invalid envelopes, and malformed descriptors are
rejected. A complete request must arrive within 5 seconds, not 5 seconds per
byte. The client has one 120-second deadline across connect, send, receive,
and close; NX main-thread dispatch has its own 120-second execution deadline.

A queued request that expires is cancelled and cannot execute later. Errors
known to precede execution report `details.execution_state = "not_started"`;
only known-unexecuted transient failures are retryable. A running timeout,
disconnect after sending, or malformed response reports `"unknown"` and is
not retryable. NXOpen calls already running cannot be safely interrupted.
There is no automatic replay: query the model and reconcile its state before
issuing another mutation. Stop cancels queued work and interrupts partial
request reception; it does not abort an executing NXOpen operation.

## Object and operation lifecycle

NX objects are returned as `{id, kind, name, part_id}`. IDs map to live NXOpen
objects inside the bridge and are invalidated when their part closes. Commands
reject unknown, stale, or wrong-kind IDs instead of guessing by display name.

Model mutations are serialized. Each mutation creates a visible undo mark and
rolls back to it when execution fails. Successful marks are tracked so
`nx_undo` affects the last NX MCP mutation rather than an unrelated user
operation; saving clears MCP's tracked undo history. Undo records are removed
only after native undo succeeds; switching work parts clears tracked history. Builders are destroyed from
`finally` blocks. File operations are independently confined to the configured
workspace on both sides of the process boundary. Save also validates the
current part's absolute `FullPath`; save and close target only the work part,
not all assembly components. Windows rooted, drive-relative, and UNC paths are
not accepted as sidecar-relative file arguments.

Signature, finite numeric, degenerate geometry, and sketch-reference checks run
before creating a native undo mark. Failed mutations roll back and invalidate
all registered references for that part; clients must query fresh IDs. A failed
rollback clears tracked undo history and blocks writes with `NX_ROLLBACK_FAILED`.
Queries and explicit `nx_close_part(save=False)` remain available. Discard-close
and reopen the disposable part, or restart the bridge, to leave this blocked
state; restarting does not itself repair uncertain model contents.

## Certification boundary

`server.py` explicitly registers the 16 certified tools. Legacy modules under
`tools/` are imported only when `NX_MCP_ENABLE_EXPERIMENTAL=1` is set on both
processes; Journal tools require `NX_MCP_ENABLE_JOURNAL=1` as well. A tool may
join the default surface only after strict boundary tests and a real-NX
contract test pass for the target build.

The listener thread only queues requests. `pump_bridge()` executes them on the
NX journal's main thread, which is required by NXOpen. The bundled runner pumps
while waiting for `NX_MCP_BRIDGE_STOP_FILE`, so it is a batch feasibility path,
not an interactive GUI integration. If a target build cannot provide a
non-blocking GUI scheduler, the NX-side executor moves to a minimal C# plugin;
the JSON-RPC and MCP contracts remain unchanged. NX2206 is such a build; the
plugin lives in `nx_gui_bridge/` (see [GUI bridge](gui-bridge.md)).
