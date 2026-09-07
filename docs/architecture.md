# NX MCP 0.2 architecture

## Runtime boundary

The MCP sidecar owns stdio, type validation, structured MCP results, and path
resolution. It never imports NXOpen. The manually loaded NX bridge owns the
live NX session, object registry, undo marks, builders, and all NXOpen calls.

The bridge listens on `127.0.0.1` using a random port. Its descriptor contains
the protocol version, host, port, random token, NX PID, and exact NX version.
The sidecar reloads the descriptor for every call, so restarting NX does not
leave a permanently disconnected singleton.

Requests are newline-delimited JSON-RPC objects containing `protocol_version`,
`id`, `token`, `method`, and `params`. Responses echo the ID and contain either
`result` or a stable error with `code`, `message`, optional `suggestion`, and
optional native `nx_code`. Payloads are limited to 1 MiB and calls time out
after 120 seconds.

## Object and operation lifecycle

NX objects are returned as `{id, kind, name, part_id}`. IDs map to live NXOpen
objects inside the bridge and are invalidated when their part closes. Commands
reject unknown, stale, or wrong-kind IDs instead of guessing by display name.

Model mutations are serialized. Each mutation creates a visible undo mark and
rolls back to it when execution fails. Successful marks are tracked so
`nx_undo` affects the last NX MCP mutation rather than an unrelated user
operation; saving clears these native marks. Builders are destroyed from
`finally` blocks. File operations are independently confined to the configured
workspace on both sides of the process boundary.

## Profiles and graphical hosting

The default exposes the original 16 tools. `NX_MCP_ENABLE_EXPERIMENTAL=1`
enables the extended integration; it does not classify every tool as untested.
The capability manifest separates native, sidecar-only and experimental evidence.
Journal execution retains a separate opt-in. The optional agent profile discovers
and invokes registered tools; it does not bypass their validation or permissions.

The batch runner calls `pump_bridge()` on the journal main thread. The graphical
runner retains a Win32 timer callback and returns from the journal. The callback
executes one queued operation at a time on the NX UI thread. Pause releases input
for manual editing and invalidates references/checkpoints; resume requires fresh
inspection. Agent mode intentionally disables the NX main window even while idle; use
`nx_ui_control(mode="manual")` or **Pause / manual** to navigate or edit. This
handoff invalidates object references and checkpoints. The panel distinguishes
reserved idle, active operation and manual mode, paints before native execution,
and reports the last operation duration. Its window is owned by NX so the Pause
control stays above NX without being globally topmost. It does not continuously repaint an
unchanged label.

`nx_ui_control(mode="status")` reads a timestamped snapshot without joining the
NX execution queue. `snapshot_age_seconds` is the age of the last main-thread
sample; `operation_elapsed_seconds` grows while an operation is running. These
are observations, not a hang detector or proof of kernel responsiveness. No
worker thread calls NXOpen. A native call holding the Python GIL can still delay
this endpoint. `.nx-mcp/ui-state.json` records the last sample before/after work
and approximately once a second while idle for external diagnosis.

A long native call can block the UI and the panel. Pause and Stop take effect
after it returns; they cannot abort a native builder. Cooperative batch cancellation
is checked between child operations, not during a native builder call.

## Integration references and recovery

References carry session/generation identity, owner-part context and separate
journal/display names. Occurrences also carry assembly context. Closed or rolled
back geometry cannot be resolved through stale IDs. Geometric selectors are
re-evaluated rules, not permanent topological identities.

Mutation receipts under `.nx-mcp/operations` store request fingerprints and explicit
outcomes. Retry with the same ID/arguments returns a committed receipt; conflicts
are rejected. An interrupted process can leave an unknown outcome requiring
reconciliation. Native checkpoints do not survive restart or expired NX undo marks.
Read-only inspection does not intentionally discard recovery history.

Result snapshots under `.nx-mcp/agent-results` are separate from mutation receipts.
Retention/cleanup affects only those snapshots. A snapshot does not keep referenced
NX objects alive. See [agent contracts](agent-surface.md).

## Files and transport

Both process boundaries confine paths to the workspace. Relative and absolute
NX-host paths are accepted inside it; traversal, external resolved links and
internal state access are rejected. Filesystem publication has separate rollback
semantics from model undo. Assembly packages include referenced dependencies.

The NX bridge is authenticated loopback IPC. The optional HTTP sidecar is a
separate transport and needs its own access controls; it is not authenticated
by the private bridge token. Stdio is the default transport.
