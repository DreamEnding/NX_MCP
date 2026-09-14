# Real NX validation gate

## Historical full workflow validation

- Date: 2026-08-21
- NX: v2506 (`ugraf.exe` 2506.4021; `run_journal.exe` 2506.4000)
- NX embedded Python: 3.12.9
- Sidecar: Python 3.12.7 with `mcp` 1.27.0
- Host: Windows 11 build 26200
- Mode: `run_journal.exe` batch journal with main-thread request pumping
- Result: one acceptance run and 20 consecutive runs passed.

This validates the Python bridge for the recorded batch environment. It does
not validate non-blocking interactive NX GUI responsiveness.

## Current hardening validation (2026-09-14)

The local runtime probe passed on NX v2506 / embedded Python 3.12.9 with
`bridge_import_error: null`; neither `mcp` nor `pydantic` was available inside
NX. This confirms the standard-library-only import boundary, not CAD behavior.
The execution environment rejected the command to launch the full acceptance
bridge. The changed save/close/undo paths, 20-iteration workflow, negative
cases, and process restart test therefore still require real-NX acceptance.
The historical run above does **not** certify these changes. Keep the opt-in
gates and version `0.2.0.dev0` until that acceptance passes.

### Workflow implementation recheck (2026-09-14)

- Source baseline: `04c04e8` (before the workflow/Skill/USD-only additions).
- Sidecar: Python 3.12.7, `mcp` 2.2.0, `pydantic` 2.13.5.
- Installed executable file versions inspected: `ugraf.exe` 2506.4021,
  `run_journal.exe` 2506.4000. This is not a new NX runtime probe.
- Local baseline: 249 tests passed; branch-measured coverage 82.92%; Ruff checks,
  formatting check, and sidecar mypy passed. The relocated checkout's stale
  editable-install path was repaired before testing; no runtime source changed.
- No NX process or bridge descriptor was present at preflight. The execution
  environment rejected the full acceptance launch command before execution.
  No new runtime probe, 20-run batch result, real negative cases, or restart
  acceptance resulted from this attempt.
- Therefore there is no fresh STEP eligible for the downstream USD acceptance.
  Historical CAD artifacts and generated USD checker fixtures must not substitute
  for that evidence. See [USD validation status](usd-validation.md#validation-status).

After the workflow additions, the local suite passed with **299 passed,
13 skipped, 3 real-NX tests deselected**, and 82.99% branch-measured `nx_mcp`
coverage. Ruff checks, formatting, mypy, and whitespace checks passed. The skipped
OpenUSD checker tests passed separately in the isolated converter environment.

The Skill passed static validation and an in-process MCP rehearsal with fake NX:
tool discovery and sketch/extrude structure checks, undo with fresh part/sketch
references, and a completed mutation with a simulated lost response reconciled
by queries without replay. No native NX or CAD artifact was involved. Its live
NX modeling/recovery exercise remains pending. Forced rollback failure and
uncertain-execution cases remain local fault-injection evidence, not real-NX
certification. Keep the existing opt-in gates and development version.

The recovery sequence is now a regression test in `tests/test_nx_executor.py`
for both automatic and legacy MCP negotiation. It checks stale part/sketch
references after undo and read-only reconciliation of a simulated lost mutation
response. This tests a scripted tool sequence, not an agent's autonomous use of
the Skill, a real network response loss, or native NX recovery.

## SDK v2 sidecar upgrade

The sidecar has migrated to official `mcp>=2.2,<3` and `pydantic>=2.12,<3`.
Use the upgraded sidecar interpreter for both the smoke runner and the MCP
server. The historical SDK 1.27.0 acceptance and the runtime-only probe above
do not certify the upgraded end-to-end workflow. The internal bridge remains
protocol v1 and does not depend on the SDK. Repeat this gate before release;
see `migration-mcp-sdk-2.md` for setup and protocol compatibility.

## Record before testing

- Exact NX release/build and installed maintenance pack
- NX Python version and architecture
- Sidecar Python and `mcp` versions
- Whether NX is native or Teamcenter-managed mode
- Test machine identifier and Windows version

Version 0.2 initially supports only this recorded NX build and native parts.

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
STEP, verify its nonempty output, undo the extrude, verify the original body
count, save, close, verify the nonempty part file, reopen, check the saved body
count, and close again. The runner refuses an existing work part, preflights all
output paths, and only attempts failure cleanup on its own current part. A
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
to stop even when acceptance fails. After that bridge stops, a separate
`tests/test_real_nx_restart.py` test owns two successive NX journal processes
and verifies that one descriptor client reconnects without being recreated.
Do not run this test while another bridge or NX session is active. It requires
`NX_RUN_JOURNAL`, `NX_MCP_WORKSPACE`, and the existing opt-in flags. Ordinary
acceptance also checks authentication, path rejection, no work part, invalid
geometry, wrong-kind IDs, and stale IDs. No failed operation is retried.

Artifacts are explicitly limited to the runtime probe and acceptance/restart
JUnit XML reports. Never upload `bridge.json`, tokens, raw protocol traffic,
or the entire workspace. Keep generated CAD files local to the disposable
workspace. Once the runner is reliable, make this
workflow a required release/branch gate in the repository settings; the normal
hosted CI deliberately excludes `real_nx` because it cannot provide Siemens NX.
