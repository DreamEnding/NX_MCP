---
name: nx-modeling
description: Create or edit native Siemens NX parts through the existing NX_MCP tools, including sketch/extrude workflows, result checks, and recovery from stale references or uncertain execution. Not for arbitrary Journal execution or CAD-to-USD conversion.
---

# NX Modeling

Use the discovered default NX_MCP tools to complete the user's modeling task.
A tool returning successfully is not, by itself, proof of the intended model.

## Before changing a part

1. Discover the server's actual tool list and input/output schemas. Do not invent
   tools or assume experimental tools are available. Call `nx_status`; if the
   bridge is unavailable, report the missing prerequisite rather than attempting
   a direct NXOpen attach. Read the [setup and validation guide](../../docs/real-nx-validation.md)
   only when setup or acceptance is requested.
2. Establish the target part, requested changes, units, and expected result.
   `nx_status` identifies the active part but does not report its units. For an
   existing part, obtain units from verified context or ask before using numeric
   dimensions. Do not silently assume millimeters.
3. Use paths relative to the configured `NX_MCP_WORKSPACE`. For a new part, choose
   a fresh path and pass `units` explicitly. A disposable acceptance run requires
   no active part; ordinary editing may use the user's explicitly selected part.
   Do not close an unrelated part or overwrite files to make a workflow proceed.
4. Confirm that a mutation is within the user's requested task. Do not enable
   experimental or Journal flags to work around a missing capability. For current
   support and evidence, read the [capability matrix](../../README.md#capabilities-and-validation).

## Modeling workflow

Use explicit object IDs returned by tools, not guessed names. Query the relevant
objects before editing and retain the current part identity. If the active part
changes unexpectedly, stop and establish which part the user wants to modify.

For example, to create a **20 x 10 x 12.5 mm** rectangular solid in a new part:

1. `nx_create_part(path=<fresh relative .prt path>, units="mm")` and record the part ID.
2. `nx_list_bodies` to establish the baseline body count.
3. `nx_create_sketch(plane="XY")` and capture its `object.id` as `sketch_id`.
4. `nx_sketch_rectangle(sketch_id, corner1={x: 0, y: 0}, corner2={x: 20, y: 10})`.
5. `nx_finish_sketch(sketch_id)` before `nx_extrude(sketch_id, distance=12.5)`.
6. Query bodies/features and verify the expected new body and feature. These
   queries check model structure, not exact dimensional metrology.
7. Save or export STEP only as requested. Check the returned export path and, if
   filesystem access is available, verify a newly created nonempty file. Report
   any verification that could not be performed rather than claiming it passed.

Do not copy the acceptance runner's undo/cleanup into an ordinary modeling task:
that runner deliberately exports the solid, then undoes it before saving the
`.prt`. Saving normally should retain the user's intended model. Save and close
operate on the work part, not its entire assembly tree; `nx_close_part` saves by
default, so always choose its `save` argument deliberately when closing is requested.

## Recovery and stopping conditions

- On validation errors, correct the inputs; do not repeat an unchanged request.
- After undo or failed-mutation rollback, discard cached object references,
  including `nx_status.active_part.id`. Call `nx_status` and query fresh object
  IDs before continuing. IDs are session references, not persistent asset identities.
- For `details.execution_state="unknown"` (including uncertain timeout or
  disconnect), do not automatically replay a mutation. Reconnect if necessary,
  inspect the active part and relevant objects, then reconcile the observed state
  with the requested result. If queries cannot resolve the uncertainty, ask for
  inspection rather than guessing that the operation failed.
- `not_started` alone is not permission to retry: only a documented retryable
  failure whose cause has been resolved can be reissued. Never loop on errors.
- On `NX_ROLLBACK_FAILED`, stop writes. Read the [recovery contract](../../docs/architecture.md#object-and-operation-lifecycle).
  Discard-close/reopen only an owned disposable part or with explicit permission
  to discard unsaved work. Restarting a bridge does not repair an uncertain model.

Finish with the part/artifact paths, checks actually performed, and any remaining
uncertainty. Keep bridge tokens and descriptors out of reports. Batch acceptance
is not evidence of a responsive interactive NX GUI.
