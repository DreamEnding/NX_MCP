# M2 inspection contract

Updated: 2026-10-02 (Asia/Shanghai).
Tracking: [Roadmap #10, M2](https://github.com/DreamEnding/NX_MCP/issues/10).

The status/units section below is implemented. Measurement sections are a
reviewable design draft for the Roadmap's inspection-contract issue; they are
not accepted APIs and do not expose new default tools. Settle that issue before
implementing or promoting measurements in either bridge.

## Status and units (implemented)

`nx_status` adds `units: "mm" | "inch" | null`. Read the active work part's
`BasePart.PartUnits` on every call. Unit display preferences, the display part
and a previous `nx_create_part` request do not determine this value.

- No work part: `active_part: null`, `units: null`.
- Older protocol-v1 bridge without the field: the sidecar returns `units: null`.
  A caller must confirm units before interpreting dimensions on an existing part.
- Unknown native unit: fail with `NX_UNSUPPORTED_UNITS`; never guess.
- NXOpen failures retain `NX_API_ERROR` and the native `nx_code` when available.

Sketch coordinates and extrusion distances already use these native length
units. This change reports them without converting existing inputs or changing
protocol v1. Count bodies, features and sketches through the existing list
tools; status does not introduce separate cached counts.

## Measurement units and coordinates (proposed)

Return native part units explicitly in every result: lengths as `mm` or `inch`,
volumes as `mm^3` or `inch^3`. Do not silently convert to display units.
Numeric values must be finite; errors do not carry placeholder measurements.

Bounding-box coordinates use the work part's absolute Cartesian frame, named
`work_part_absolute`, independent of the current WCS or view orientation.
Distance is the scalar minimum separation between two bodies in that part.
Inspection is read-only: no temporary persistent geometry, undo mark or WCS
change. Both bridges must implement the same input and output contracts.

## Initial surface (proposed)

| Tool | Input | Result |
| --- | --- | --- |
| `nx_get_bounding_box` | One `body_id` | Absolute-frame `min`, `max`, `dimensions` (each x/y/z), explicit length `units`, `coordinate_system`, `exact` |
| `nx_measure_volume` | One solid `body_id` | `volume`, explicit cubic `units` |
| `nx_measure_distance` | Two `body_id` values | `distance`, explicit length `units` |

Use opaque IDs returned by `nx_list_bodies` or `nx_extrude`. Do not accept names,
native tags, part/feature IDs or inferred selections. Both distance bodies must
belong to the current work part. Measuring one body against itself yields zero.
Sheet bodies may have a bounding box and distance, but volume requires a solid.

Bounding boxes should use an exact geometry API, aligned with the absolute
frame. `exact` describes the API used, not arithmetic without floating-point
error. If a bridge cannot implement this, settle an explicitly approximate
contract in the issue instead of falling back silently.

## Errors (proposed measurements)

Reuse `NX_NO_WORK_PART`, `NX_OBJECT_NOT_FOUND`, `NX_OBJECT_STALE`,
`NX_OBJECT_TYPE_MISMATCH`, `NX_INVALID_ARGUMENT`, `NX_UNSUPPORTED_UNITS` and
`NX_API_ERROR`. Propose `NX_NOT_SOLID` for volume requested on a sheet body.
Unknown/stale/cross-part IDs fail before NXOpen measurement; native failures
retain `nx_code`. Reads remain available during `NX_ROLLBACK_FAILED` recovery.

## Numeric acceptance (proposed)

Create a metric `20 x 10` XY rectangle, extrude `12.5`, then verify through MCP:
one solid body; minimum `(0, 0, 0)`, maximum/dimensions `(20, 10, 12.5)`;
volume `2500 mm^3`. Negative extrusion direction must move the z minimum to
`-12.5`. Set a nondefault WCS and confirm absolute-frame results are unchanged.
Save, close and reopen before checking the same results again.

Proposed canonical-test bounds: `1e-6 mm` absolute tolerance per bounding-box
coordinate and `1e-3 mm^3` absolute tolerance for volume. These are test bounds
for this fixture, not universal NX accuracy guarantees. Repeat with equivalent
inch geometry and scaled bounds. For distance, create two bodies separated by
a known gap; test self-distance, touching and separated bodies.

Export STEP and measure its B-rep in an independent Python environment, checking
file units, dimensions, solid count and volume. Mesh bounds alone do not prove
volume. Include wrong-kind/stale IDs, sheet-volume rejection and native errors.
Require both-bridge parity, local failure tests and real-NX numeric acceptance
before adding measurements to the default tool list.
