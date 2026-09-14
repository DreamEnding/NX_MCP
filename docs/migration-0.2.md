# Migrating from 0.1 to 0.2

Version 0.2 deliberately breaks the unverified 0.1 tool contract.

## Main changes

- MCP runs in an external sidecar; NXOpen runs behind an NX-journal-started
  local bridge.
- Default discovery is removed. `tools/list` contains exactly 16 certified
  tools.
- File paths are relative to `NX_MCP_WORKSPACE`.
- Units are strictly `mm` or `inch`; sketch planes are `XY`, `XZ`, or `YZ`.
- Sketch and modeling tools use opaque session IDs instead of object names or
  a global active sketch.
- Successes include structured content. Recoverable failures set MCP
  `isError=true` and include a stable error code.

## Parameter replacements

| 0.1 call | 0.2 call |
| --- | --- |
| `nx_sketch_line(x1,y1,x2,y2)` | `nx_sketch_line(sketch_id,start:{x,y},end:{x,y})` |
| `nx_sketch_rectangle(x1,y1,x2,y2)` | `nx_sketch_rectangle(sketch_id,corner1:{x,y},corner2:{x,y})` |
| `nx_finish_sketch()` | `nx_finish_sketch(sketch_id)` |
| `nx_extrude(distance,direction,boolean,sketch_name)` | `nx_extrude(sketch_id,distance,reverse=false)` |

Use `nx_create_sketch` or `nx_list_sketches` to obtain `sketch_id`. Object IDs
become stale after the part closes or is reopened and must then be queried
again.

The 34 remaining 0.1 tools are hidden until `NX_MCP_ENABLE_EXPERIMENTAL=1` is
set on both processes. They remain unsupported until certified on real NX.
Journal execution additionally requires `NX_MCP_ENABLE_JOURNAL=1`, and journal
paths are restricted to the workspace `journals/` directory.

## Safety hardening in the development line

- Tool names, protocol v1, and existing error codes are unchanged. Do not replay
  mutations after timeouts: inspect `details.execution_state`. `unknown` means
  execution may have occurred; only known-unexecuted transient errors are retryable.
- Coordinates and extrusion distances must be finite. Degenerate lines and
  rectangles are rejected before creating an undo mark.
- Saving a work part outside the configured workspace is refused, including
  save-before-close. Save and close no longer traverse assembly components.
- Query fresh object IDs after failed mutation rollback or undo. Tracked undo
  history does not cross work-part changes. Rollback failure blocks writes;
  discard-close/reopen or restart the bridge before recovery work.
- Smoke acceptance requires an empty work-part session and unused output paths.
  It checks disk outputs and reopens the saved part; it never blindly closes an
  unrelated active part on failure.

These changes retain `0.2.0.dev0`; see `real-nx-validation.md` for the distinction
between historical full acceptance and the current runtime-only probe.
