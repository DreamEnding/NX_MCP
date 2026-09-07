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

The extended integration is hidden until `NX_MCP_ENABLE_EXPERIMENTAL=1` is
set on both processes. The compatibility flag name does not describe per-tool verification. Consult the capability matrix for native-tested, sidecar-tested and experimental scopes.
Journal execution additionally requires `NX_MCP_ENABLE_JOURNAL=1`, and journal
paths are restricted to the workspace `journals/` directory.
