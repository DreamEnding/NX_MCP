# Real NX validation gate

## Historical upstream batch run

- Date: 2026-08-21
- NX: v2506 (`ugraf.exe` 2506.4021; `run_journal.exe` 2506.4000)
- NX embedded Python: 3.12.9
- Sidecar: Python 3.12.7 with `mcp` 1.27.0
- Host: Windows 11 build 26200
- Mode: `run_journal.exe` batch journal with main-thread request pumping
- Result: one acceptance run and 20 consecutive runs passed.

This validates the Python bridge for the recorded batch environment. It does
not validate non-blocking interactive NX GUI responsiveness.

## Record before testing

- Exact NX release/build and installed maintenance pack
- NX Python version and architecture
- Sidecar Python and `mcp` versions
- Whether NX is native or Teamcenter-managed mode
- Test machine identifier and Windows version

The upstream baseline was scoped to that build. The fork adds separately recorded NX 2606 graphical fixtures.

## Preconditions

- Use a disposable `NX_MCP_WORKSPACE`; do not copy production parts into it.
- Install this package in the sidecar interpreter. The supplied NX journal
  examples load the checkout's `src` directory and require only NX's standard
  Python library, not `mcp` or `pydantic`.
- Before starting the bridge, set `NX_MCP_PROBE_OUTPUT` to a JSON file inside
  the disposable workspace and run `examples/nx_runtime_probe.py` as an NX
  journal. It must report `"bridge_import_error": null`.
- Enable `NX_MCP_ALLOW_UNVERIFIED_PYTHON_BRIDGE=1` only during feasibility.

Set `NX_MCP_BRIDGE_STOP_FILE` to a new path inside the disposable workspace
before running `start_nx_bridge.py` with `run_journal.exe`. The journal pumps
each bridge request on NX's main thread, keeps NX alive until that file is
created, then stops the bridge cleanly. The supplied Python runner requires
this batch mode.

## Acceptance command

```powershell
python -m nx_mcp.real_smoke --workspace D:\NX_MCP_WORKSPACE --iterations 20 --run-prefix acceptance
```

Every iteration must connect, create a metric part, create and finish an XY
rectangle sketch, extrude a new body, query the result, fit the view, export
STEP, undo the extrude, verify the original body count, save, and close. A
prefix must be unique for each rerun because NX will not overwrite a part.

## Pass criteria

- All 20 iterations pass without retry.
- The batch journal does not crash or hang.
- Every STEP and part file stays within the workspace.
- No partial geometry remains after a failed command or undo.
- Bridge stop/start and NX restart are followed by successful reconnection.
- Invalid token, path traversal, no work part, wrong-kind ID, and stale ID fail
  with their documented error codes.

If clean unload or GUI responsiveness fails, do not remove the feasibility
gate. Implement the minimal C# NX-side bridge or a non-blocking UI scheduler
and rerun this entire matrix before changing the package version from
`0.2.0.dev0` to `0.2.0`.

## GitHub Actions self-hosted gate

The repository provides `.github/workflows/real-nx.yml` for this acceptance
gate. It is intentionally manual so ordinary pull requests are not blocked
until a dedicated NX runner exists. The runner must have the labels
`self-hosted`, `windows`, and `nx`, plus a runner-level `NX_RUN_JOURNAL`
environment variable containing the absolute path to `run_journal.exe`.

The workflow creates a disposable workspace, runs the embedded-runtime probe,
starts the Python bridge, then runs `pytest -m real_nx`. It requests the bridge
to stop even when acceptance fails. Once the runner is reliable, make this
workflow a required release/branch gate in the repository settings; the normal
hosted CI deliberately excludes `real_nx` because it cannot provide Siemens NX.

## NX 2606 integration evidence

Dev18 runtime `9254c028eac8ffdfeed54201377ba62a052b0a1c` passed 868 ordinary tests
at 79.08% branch coverage, sidecar type checks and hosted CI. One dedicated NX
runner test was skipped in that suite. Separate graphical checks verified UI-thread
dispatch, stdio/HTTP, installed-source identity and native PNG delivery. Session
preservation checked 38 saved original parts and 116 occurrence paths, poses,
suppression states and reference sets.

Two fresh agents completed analytic plate, three-instance assembly and STEP
round-trip fixtures. The comparison used dev17 full versus initial dev18 agent;
final pagination/discovery fixes received a separate follow-up. Provider usage
was unavailable; response tokenizer counts are not total model costs.

Historical release receipts are retained in git history and local evidence, rather
than one documentation file per deployment. The [capability matrix](capability-matrix.md)
is generated from the runtime manifest. Do not promote a label based on builder
presence, a mock test or an unrelated native fixture.

## Running integration suites

Use isolated fixtures and a configured public MCP endpoint. Native scripts live
under `examples/validate_*.py`; each documents its endpoint/output variables.
`scripts/validate_native_release.py` coordinates the release suites. Run mutations
serially and preserve the original loaded parts, modified flags and work/display
selection. After failures, record outcomes and restore the session before retrying.

`scripts/build_release.py --output <directory>` packages a clean committed checkout
with locked Windows dependencies. Installation/rollback scripts preserve backups;
verify archive and installed-source hashes. Record exact runtime commit, NX build,
fixture checks and artifact hashes with each native run. Re-run changed behavior
on the release candidate instead of relabeling earlier evidence.

## Remaining legacy entry points

The dev19 closeout reproduced three failures in dev18: typed-reference angle
lookup, the missing sketch constraint enum, and feature deletion through
`FeatureCollection.ToArray`. The replacement handlers use live references and
supported native editing/update paths. `examples/validate_remaining_tools.py`
checks perpendicular-line angles, horizontal constraints, inventories, rename,
nested Save As, extrusion deletion, view selection, relative-placement replay,
explosion deletion and downloaded assembly ZIP contents. Save As references are
reacquired before subsequent edits. Non-running cancellation is an expected error;
cooperative cancellation is classified from sidecar/batch-boundary tests, not
from an unperformed mid-builder interruption test.

The initial isolated run exposed the stale-reference assumption in the test;
it restored the original session and was retained as a failed attempt. The
corrected run passed on candidate `b2584eeef629791b04cf6ad68fe40a428bfe5aec`.
Per-option limits remain in the capability matrix even when the overall entry
point is marked tested. This does not make every exposed variant native-tested.

Final dev19 runtime `d7d5dc36b915a631bdb6fe23987b9cb93a6787ae` passed the same
native suite with additional status/frame/page/pose assertions and graphical view
metadata checks. Windows source hashes, stdio/HTTP and inline PNG validation passed.
881 ordinary tests passed at 78.96% branch coverage; sidecar type checking covered
50 modules. [Hosted CI](https://github.com/xuio/NX_MCP/actions/runs/34057628840)
passed on that commit. Final preservation checks retained the original 38 saved
parts and 116 occurrences. The manifest reports 178 native-scoped and seven
sidecar/contract-scoped tools; individual option limits still apply.

## Manufacturing regression checks (dev20)

Isolated NX v2606 fixtures preserved the existing 71-part session. Four A3 drawing
views used an explicit 2:1 scale; an 80 mm coupon measured 160 mm in the exported
PDF. An associative dimension retained its value and two associations while its
PDF showed `80.00 +0.05/-0.02`. A blind pocket was dashed and a through-hole solid
after construction curves were erased per view. Model visibility was preserved.
Managed revision-table editing also passed after saving and reopening a part.

Planar DXF checks covered XY, XZ, YZ, custom bases, analytic arcs, layer assignment
and inch-to-mm conversion. Independent parsing of the XY sketch and face exports
found four lines and one circle, 80 by 30 mm bounds, millimeter units and no DXF
audit errors. Arbitrary-origin/axis revolves were exercised separately.

A normal STEP imported into a new part with two bodies. Failed vendor imports
returned translator settings, output paths and logs, removed their empty new part
and preserved the session. One vendor STEP still failed in NX's kernel conversion
with multiple translator settings. Another vendor part had two self-intersecting
faces; diagnostics identified both faces and error 875315. Healing on a disposable
copy failed and was rolled back. These are unresolved vendor/native limitations,
not successful repairs. Drawing centerline setters also did not persist, so the
API exposes their readback and rejects edits explicitly.

Save-scope regression: the previous save implementation enabled native component
saving. It now saves only the active work part and does not run component-preview
saves. A native parent/child fixture confirmed that the child stayed modified and
its disk bytes were unchanged after saving the parent. The updated local suite
passed 907 tests at 78.74% branch coverage.

Deployed runtime `337b81f64e8fe16c703641836e0272f6674a306e` passed STEP import
into a completely empty session (two bodies). The full and 13-entry agent MCP
profiles delivered a 2,032-object appearance result as a bounded committed
receipt with a 2.89 MB immutable snapshot; 20-item pages, operation-status
readback, idempotent replay and appearance restoration passed. All 71 loaded
parts were restored, with 538 recorded occurrence paths/transforms unchanged.
Reopening the corrected drawing through the deployed MCP retained all four 2:1
views and physical tolerances; a newly downloaded PDF was parsed and visually
checked again. These checks used isolated fixtures, not production CAD edits.

## Follow-up investigations and save verification

The CLIFF model's outer shell and two void shells each imported when serialized
separately, but native recombination did not yield a solid that could pass volume
verification. This is diagnostic evidence, not an accepted conversion route.
Optimize Face with body cleanup and a 0.00001 mm tolerance completed on the Adam
Tech copy but left native health faults and volume unchanged; it was rolled back.
Independent OCP checks also found an invalid solid in the original Adam Tech STEP,
with eight unorientable faces. Reader findings are recorded separately; they do
not prove identical fault classification across kernels.

The native centerline toggle still read back as enabled after setting it to false
with either default or view-style inheritance. Existing centerline annotation
objects are present, but their separate visibility requires further verification.
No unsupported preference setter is advertised as repaired.

The dev21 candidate save audit passed in native NX with 73 loaded parts: the
parent save reported only its path, the edited child remained modified with
unchanged disk bytes, and all other loaded files/flags were unchanged. The
fixture was removed and the 71-part session preserved. Verification uses SHA-256,
size and modification time for loaded part files and native PartSaveStatus
errors. It does not cover external linked files or concurrent external writers.
Unexpected changes produce a partial-outcome error, not a disk-rollback claim.

CLIFF reconstruction blocked native cleanup and required an authorized restart.
The saved 71-part session and all 538 occurrence paths/transforms were restored.
A separate centerline annotation probe did not return a completed result; its
visibility behavior remains unverified. No production CAD repair was applied.

Deployed dev21 runtime `e9bb14f0b0e56b31d13e4b57dd1779013ee84501`
passed the same parent/edited-child save check through both full HTTP and the
agent gateway. Both returned committed audit receipts with the parent as the only
saved file and the child unchanged and still modified. The temporary fixture was
closed without saving the child, and all 71 original parts were retained.
The local suite passed 912 tests; sidecar type checks covered 55 modules.

## A02 drafting and unloaded-prototype regressions (dev22)

The section style failure was reproduced as native 630035 in per-view erasure:
`Part.Curves` included three sheet-owned section lines. UF view-dependency checks
exclude those lines while retaining model construction geometry. Section style
editing and adding another base view then succeeded; save/reopen retained style,
scale, arrows and section hatching.

PDF export previously left `RasterImages` false. Enabling shaded raster output
produced a 400 dpi image in the native high-resolution fixture. Independent PDF
rendering also exposed a contract ambiguity: `hidden_lines=false` disables native
processing and can show obscured edges as visible. With processing enabled and
font 0 (Invisible), the reopened shaded PDF omitted occluded edges. Native
readback and rendered output were both checked; this is fixture-scoped evidence.

A disposable parent/child test reproduced an unloaded `NXObject` prototype.
Inventory retained its source path and [10,20,30] placement. Explicit component
loading on the already loaded parent restored a 1000 mm3 solid. Closing an edited
referenced child was rejected before saving; no production component was closed.
The local suite passed 918 tests. CLIFF/Adam Tech source limitations and the
nonpersistent centerline preference are unchanged; no vendor conversion was
repeated or claimed repaired.

Final runtime `6125b7dac2c85bc0df2c974bc032690bab4e3de0` passed the
public-MCP section/style, save/reopen, PDF download/checksum, explicit component
loading and referenced-prototype close guard checks. The agent gateway returned
font-0 readback and discovered `nx_component_action`. PDF export uses drafting
view updates without assuming a modeling undo mark; the first deployed-context
check caught that distinction, and the regression now covers it. Installed source
hashes and Windows stdio/HTTP checks passed. The 28-part session and 340 recorded
occurrence paths/transforms were restored.

### Dev23 interactive UI follow-up

The deployed v2606 host keeps its control panel above the NX owner window. Whole-VM
capture verified that the active-operation text and Pause control remain visible.
During a controlled 12-second sleep on the NX UI thread, three public MCP
`nx_ui_control(mode="status")` calls returned in 141, 47 and 47 ms while the
request remained active. This tests queue/transport independence, not recovery
from a hung native kernel or a call holding the Python GIL. Manual handoff
restored window input; resuming reserved it again. All 28 saved parts and 340
occurrence placements were restored after deployment.

The regression suite passed 922 tests; the 17 focused UI tests also passed after
the final owner-window change. Source hash checks, Windows stdio/HTTP discovery
(189 tools), agent discovery (13 tools), and inline viewport PNG delivery passed.
