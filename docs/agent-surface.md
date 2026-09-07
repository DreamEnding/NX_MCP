# Agent surface

The full profile remains compatible: 189 tools with existing defaults. The opt-in
agent profile lists 13 tools: eight core tools plus `nx_discover_tools`, `nx_invoke`
`nx_result`, `nx_inspect` and `nx_result_cleanup`. All 189 underlying tools remain available, subject to their
existing capability status and native prerequisites.

For stdio, set `NX_MCP_SURFACE=agent`, `NX_MCP_ENABLE_EXPERIMENTAL=1` and
`NX_MCP_WORKSPACE`. The dual HTTP entrypoint `python -m nx_mcp.http_surface` serves
full compatibility at `/mcp` and the agent profile at `/agent/mcp` on port 8765.
Both use the same bridge and serial NX main-thread queue. They must not be used
to issue concurrent mutations. The host must restrict network access as before.

## Discover, invoke, inspect

1. `nx_discover_tools(query="nx_extrude", include_schema=true)` returns the exact
   original input/output schemas, description and agent defaults. Broad queries
   return at most ten descriptions by default (maximum twenty); use `next_offset`.
2. `nx_invoke(tool="nx_extrude", arguments={"sketch_id":"...","distance":5,
   "operation_id":"extrude_unique_001"})` validates the original schema and
   calls the existing implementation. Unsupported fields still fail before NX.
3. Compact results retain opaque references, owner-part identity, occurrence
   paths, units, all warnings, mutation outcome and change counts. Long arrays
   contain at most twenty entries with explicit `omitted` metadata. Repeated
   reference metadata is omitted. Unknown change counts remain null.
4. `nx_result(result_id="result_...", field="/bodies", offset=20)` expands an
   immutable response snapshot. `detail="full"` expands selected object metadata;
   selected arrays remain paged. This never repeats the original mutation.

`nx_invoke(detail="full")` selects the legacy defaults and full original result
**when making a new call**. Do not repeat a mutation to obtain detail; use
`nx_result` instead. After uncertain transport delivery, query
`nx_operation_status` with the original operation ID before retrying. Snapshot
IDs are not mutation receipt IDs. Snapshots persist under `.nx-mcp/agent-results`
subject to the configured retention policy; they do not keep NX
references alive. Storage failures fall back to the original full response and
never relabel committed geometry as a failed operation.

Part/component inventories default to compact pages of twenty. Component poses
are omitted unless `include_transforms=true`. Existing filters and explicit
arguments override profile defaults. Every inventory page remains explicit about
its returned and total counts. Geometry vectors/scalars are preserved; use full
snapshot detail for omitted nested arrays and metadata.

### Oversized native results

The bridge keeps a bounded receipt when a native result exceeds 512 KiB. It retains the operation outcome and restore ID and adds `full_result` with a snapshot ID, checksum and size. Read it with `nx_invoke(tool="nx_read_result", arguments={"result_id":"result_...","field":"/objects","offset":0,"limit":20})`, or call `nx_read_result` directly on the full surface. Arrays, strings and objects are paged; nested omissions provide paths for further reads. Do not repeat the mutation to obtain omitted data.

Bridge snapshots live in `.nx-mcp/bridge-results` under the configured retention age/size limits. These are separate snapshot stores: `nx_result` reads agent snapshots; `nx_read_result` reads bridge snapshots. Expiration does not delete durable operation records. Delivery/storage errors preserve known outcomes; interrupted calls with unknown outcomes still require `nx_operation_status`.

## Artifacts

Downloads default to metadata; eligible files have an MCP resource link at
`nx-artifact://workspace/...`. Resource reads return binary content up to 8 MiB.
Internal `.nx-mcp` service files and paths outside the workspace are rejected.
PNG captures retain native MCP image content. Resource bytes should be consumed
by the client outside model text; client behavior determines actual image/token
cost. Larger files use existing chunked downloads programmatically:

```sh
python scripts/download_artifact.py project/model.step ./model.step \
  --url http://192.168.52.10:8765/agent/mcp
```

This streams directly to disk and verifies size and SHA-256. Explicit base64
responses are available through `nx_invoke(detail="full")` for such clients;
compact text excludes binary data. Snapshot detail can contain original binary
fields: do not request these into model context.

## Measuring efficiency

Install the `benchmark` extra and run `scripts/benchmark_agent_surface.py` on an
operations JSONL transcript. It measures exact tokens under the named tokenizer,
not character estimates. Full and compact projections use the same recorded
native tasks. Reports explicitly exclude duplicate transport text, image tokens,
discovery and extra expansion calls. They are **not an autonomous-agent A/B
benchmark or a provider bill**. Optional observed usage JSON can be attached;
missing provider input/cache/output usage remains `unavailable`.

For a real agent comparison, run the same exploded drawing, sheet-metal tray and
imported-part edit tasks with fresh sessions on each profile. Record every
model's provider input/cached/output usage, discovery calls, expansions, retries,
artifact handling, and native task assertions. Sum all workers and repairs.
Do not claim total token savings from response projection alone.

## Reviewed discovery and task receipts (dev18)

Exact-schema discovery also returns prerequisites, supported object kinds and a
minimal example for reviewed common workflows, plus version-specific native
capability evidence. Unreviewed tools explicitly return null examples/object
kinds rather than guessed recipes. The original description and schema remain
authoritative. Effects identify geometry/assembly mutation, visibility, saving,
file writes and reference invalidation; null means not reviewed, and true may
be conditional on arguments. Read-only tools explicitly have false mutation
effects. Names alone do not establish safety.

Compact receipts suggest read-only next actions using references actually
returned by NX: topology inspection for new bodies, constraint diagnostics for
sketches and artifact metadata retrieval. They preserve geometry values and
recovery fields; next actions are never executed implicitly.

`nx_inspect` provides consistent name/text and kind filtering before paging for
features, sketches, faces, edges and annotations. Return counts and units are
explicit. Coordinate frames are passed through from the native result; absent
frame metadata is `not_reported`. Reuse `result_id` for stable pages without
another NX call, or omit it to capture current geometry. Annotations may require
multiple serial native reads; snapshot capture is not an atomic NX transaction.

Snapshot retention defaults to seven days and 256 MiB. Set positive integer
`NX_MCP_RESULT_MAX_AGE_SECONDS` and `NX_MCP_RESULT_MAX_BYTES` environment values
to configure it. Pruning runs on writes and protects the newly returned snapshot.
`nx_result_cleanup` defaults to a dry-run count/byte preview; `dry_run=false`
applies cleanup using optional overrides without changing persistent policy.
Only matching snapshot files are eligible. Symlinks, other files and the separate
mutation recovery directory are excluded. Missing/expired snapshot IDs return
`NX_RESULT_EXPIRED`; query mutation outcomes through `nx_operation_status`.

## Fresh-agent trials

`scripts/agent_benchmark_client.py` provides a transparent CLI for recording
actual agent tool requests and responses. Full-profile discovery filters the
full catalog locally before presenting matching schemas; agent-profile discovery
uses the server tool. This is a code-capable client comparison, not a measurement
assuming every full-profile schema is injected into model context. Each trial
uses a fresh agent, identical analytic tasks, isolated CAD paths and exclusive
serial ownership of NX. Record provider usage when exposed; otherwise mark it
unavailable. A single pair is diagnostic evidence, not a statistical efficiency
claim. Projection benchmarks remain separately labeled.

### Findings addressed after the fresh-agent trial

Spaced discovery queries now match all words, ranking tool-name matches first.
For example, `create part` finds `nx_create_part`. Set
`include_output_schema=false` with `include_schema=true` when only the input
contract and guidance are needed; the compatibility default still includes both.

Compact projection preserves complete backend pages and their count/cursor.
An explicitly requested 100-row page is not truncated again to twenty. Default
part/component pages remain twenty. Other summarized arrays retain explicit
omission metadata and snapshot expansion. Inspection hints do not point back
to the same topology, diagnostic or artifact-metadata call.
