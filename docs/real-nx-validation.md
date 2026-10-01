# Real NX validation gate

## M1 development validation (2026-09-30)

The current development tree passed the NX2506 (`2506.4021`) Python batch
acceptance: 20 block iterations, independent sketch-line validation, negative
cases and two-process restart. The evidence explicitly records `git_dirty:true`
at base commit `5a404c9`; it does not certify that base commit or a release tag.
The C# GUI/NX2206 candidate run remains pending. See
[M1 status](m1-status.md) for commands, artifacts, local checks and review results.

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

The M1 release matrix requires Python batch/NX2506 and C# GUI/NX2206 on the
same clean candidate commit. Supported-build claims follow those results;
historical runs do not certify the current candidate. Native parts only.

## Versioned M1 evidence

The journal `examples/nx_runtime_probe.py` uses only NXOpen and the standard
library. It records `schema_version: 1`, UTC capture time, NX release, the
installed `NXBIN/ugraf.exe` file version as `nx_build`, embedded Python and
architecture, OS, `git_commit`, `git_dirty`, probe launch switches and import
diagnostics. Git must be on the NX process's PATH. The executable version is
read with the Windows version-resource API, rather than inferred from the
release name. Use the same NX installation for the probe and live bridge.

Set `NX_MCP_RUN_JOURNAL_SWITCHES` to the JSON array of switches actually used
for a successful probe invocation: `[]` for the canonical batch command below.
Use `null` when running the probe inside the GUI. This variable only records
provenance; it never adds arguments to `run_journal` or discovers other switches.

After acceptance, the sidecar command `python -m nx_mcp.evidence` validates
that probe and observes `nx_status` on the connected bridge. It writes a new
evidence file, adding the actual `bridge_implementation`, protocol and UTC
observation time. A failed probe, an unidentified/wrong bridge or a different
NX release is rejected. It refuses to overwrite prior evidence and excludes
the descriptor, session token, port and active-part information.
Run the collector from the same checkout: it rejects a changed Git commit or
clean/dirty state since the probe. The final JSON also records the sidecar
Python and MCP SDK versions and the configured `run_prefix`. Reprobe and repeat acceptance after code changes;
this check does not make a dirty tree eligible for release or verify a GUI DLL's
source by itself. Build and load the candidate DLL as recorded in the GUI guide.

Acceptance and restart JUnit suites carry `git_commit`, `git_dirty`,
`run_prefix`, bridge implementation and NX release. Restart also records the
exact NX build; compare these properties with the evidence JSON before release.
Evidence JSON describes the runtime; the accompanying JUnit reports prove
which acceptance/restart tests passed. Preserve all three together. A dirty
working tree is explicitly recorded and is development evidence, not release
certification. Release evidence requires both environments at the same clean
tagged candidate; see [RELEASE.md](../RELEASE.md).

### Python batch reproduction

Use a dedicated NX machine with no existing NX session. From the checkout in
PowerShell 7, with the package already installed in the sidecar environment:

```powershell
$env:NX_RUN_JOURNAL = "C:\Program Files\Siemens\NX2506\NXBIN\run_journal.exe"
$env:NX_MCP_WORKSPACE = "D:\NX_MCP_WORKSPACE\m1-$(Get-Date -Format yyyyMMdd-HHmmss)"
$env:NX_MCP_STATE_DIR = Join-Path $env:NX_MCP_WORKSPACE "state"
$env:NX_MCP_PROBE_OUTPUT = Join-Path $env:NX_MCP_WORKSPACE "nx-runtime-probe.json"
$env:NX_MCP_BRIDGE_STOP_FILE = Join-Path $env:NX_MCP_WORKSPACE "stop-bridge"
$env:NX_MCP_RUN_JOURNAL_SWITCHES = "[]"
$env:NX_MCP_ALLOW_UNVERIFIED_PYTHON_BRIDGE = "1"
$env:NX_MCP_REAL_NX = "1"
$env:NX_MCP_EXPECTED_BRIDGE = "python_batch"
$env:NX_MCP_REAL_NX_ITERATIONS = "20"
$env:NX_MCP_REAL_NX_RUN_PREFIX = "acceptance"
New-Item -ItemType Directory -Path $env:NX_MCP_WORKSPACE | Out-Null
if (Get-Process ugraf,run_journal -ErrorAction SilentlyContinue) { throw "Use a dedicated idle NX machine." }
& $env:NX_RUN_JOURNAL (Join-Path $PWD "examples\nx_runtime_probe.py")
if ($LASTEXITCODE -ne 0) { throw "Runtime probe failed." }
$bridgeScript = Join-Path $PWD "examples\start_nx_bridge.py"
$bridgeProcess = Start-Process -FilePath $env:NX_RUN_JOURNAL -ArgumentList ('"' + $bridgeScript + '"') -PassThru -WindowStyle Hidden
try {
    $descriptor = Join-Path $env:NX_MCP_STATE_DIR "bridge.json"
    $deadline = [DateTime]::UtcNow.AddMinutes(2)
    while (-not (Test-Path -LiteralPath $descriptor)) {
        if ($bridgeProcess.HasExited -or [DateTime]::UtcNow -ge $deadline) { throw "Bridge startup failed." }
        Start-Sleep -Milliseconds 200
    }
    python -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx.py --junitxml="$env:NX_MCP_WORKSPACE/acceptance.xml"
    if ($LASTEXITCODE -ne 0) { throw "Acceptance failed." }
    python -m nx_mcp.evidence --probe $env:NX_MCP_PROBE_OUTPUT --output "$env:NX_MCP_WORKSPACE/evidence.json" --bridge python_batch
    if ($LASTEXITCODE -ne 0) { throw "Evidence collection failed." }
} finally {
    New-Item -ItemType File -Path $env:NX_MCP_BRIDGE_STOP_FILE -Force | Out-Null
    if (-not $bridgeProcess.WaitForExit(120000)) { throw "Bridge did not stop cleanly." }
    if ($bridgeProcess.ExitCode -ne 0) { throw "Bridge exited unsuccessfully." }
}
python -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx_restart.py::test_real_nx_client_reconnects_after_restart --junitxml="$env:NX_MCP_WORKSPACE/restart.xml"
```

Use the sidecar environment's Python executable or activate it before these
commands. Substitute the installed NX path. The two-process restart test owns
its journal processes and requires the first bridge to have stopped fully.

### C# GUI reproduction and restart

On a dedicated NX2206 machine, create a separate disposable workspace and
set the same workspace, absolute state directory, probe output and real-NX
variables before launching NX. Set `NX_MCP_EXPECTED_BRIDGE=csharp_gui` and
`NX_MCP_RUN_JOURNAL_SWITCHES=null`. Build the full add-in against that NX
installation and load it using the [GUI bridge guide](gui-bridge.md).
Use the same reviewed checkout and final candidate revision as the Python run.

In the idle GUI, run `examples/nx_runtime_probe.py` through File > Execute >
NX Open as a Python journal. It observes the embedded runtime without changing
the work part. Then run from the sidecar PowerShell 7 terminal:

```powershell
python -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx.py --junitxml="$env:NX_MCP_WORKSPACE/acceptance.xml"
if ($LASTEXITCODE -ne 0) { throw "GUI acceptance failed." }
python -m nx_mcp.evidence --probe $env:NX_MCP_PROBE_OUTPUT --output "$env:NX_MCP_WORKSPACE/evidence.json" --bridge csharp_gui
if ($LASTEXITCODE -ne 0) { throw "GUI evidence collection failed." }
$env:NX_MCP_REAL_NX_GUI_RESTART = "1"
python -m pytest -s -q -p no:cacheprovider -m real_nx tests/test_real_nx_restart.py::test_real_nx_gui_client_reconnects_after_operator_restart --junitxml="$env:NX_MCP_WORKSPACE/restart.xml"
```

The line test runs a separate MCP stdio client, creates a closed profile using
four `nx_sketch_line` calls, extrudes it and checks a new body plus a nonempty
STEP containing solid geometry. Curve metadata alone cannot pass this test.
Cleanup closes only its confirmed work part. If modeling and cleanup both fail,
the modeling failure remains primary and cleanup is logged; cleanup failure on
an otherwise successful path still fails acceptance without retrying the close.

The GUI restart test requires no open work part. Follow its two printed
instructions: first stop the add-in and wait for the test to confirm the
descriptor disappeared and calls fail with `not_started`; then close NX,
restart NX from the configured environment and load the same DLL. Each phase
has a 180-second deadline. The test holds a read-only Windows handle to the
original process and refuses a new instance if that process has not exited.
It keeps one descriptor client, checks a rotated token and a changed NX PID,
and verifies reconnection to `csharp_gui` on the same executable installation,
exact file build and NX release. The candidate commit/cleanliness must also
remain unchanged during restart. It observes processes without closing them.
It observes the lifecycle; the operator owns NX and must stop the final bridge
afterward. Reloading a DLL in the same process fails the NX-restart requirement.

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
gate. It is intentionally manual: dispatch only reviewed, trusted refs, never
untrusted PR code on this privileged runner. The runner must have the labels
`self-hosted`, `windows`, and `nx`, plus a runner-level `NX_RUN_JOURNAL`
environment variable containing the absolute path to `run_journal.exe`.
Set `UGII_BASE_DIR` to the same NX installation root, containing the four
NXOpen assemblies under `NXBIN/managed`. PowerShell 7, the in-box .NET Framework
compiler, and Actions Runner 2.327.1 or later (for the Node 24 actions) are required.

The workflow creates a disposable workspace and invokes `nx_gui_bridge/build.ps1`
to compile the complete `NxMcpGuiBridge.dll` against that NX installation. It
explicitly checks the DLL exists under the run's workspace. This is a full C#
build, not a GUI runtime acceptance run. It then runs the embedded-runtime probe,
starts the Python bridge, then runs `pytest -m real_nx`. It requests the bridge
to stop even when acceptance fails. After that bridge stops, a separate
`tests/test_real_nx_restart.py` test owns two successive NX journal processes
and verifies that one descriptor client reconnects without being recreated.
Do not run this test while another bridge or NX session is active. It requires
`NX_RUN_JOURNAL`, `NX_MCP_WORKSPACE`, and the existing opt-in flags. Ordinary
acceptance also checks authentication, path rejection, no work part, invalid
geometry, wrong-kind IDs, and stale IDs. No failed operation is retried.

Artifacts are explicitly limited to the runtime probe, versioned evidence JSON, acceptance/restart
JUnit XML reports, and the compiled `NxMcpGuiBridge.dll` (without NX assemblies).
Never upload `bridge.json`, tokens, raw protocol traffic,
or the entire workspace. Keep generated CAD files local to the disposable
workspace. Once the runner is reliable, make this
workflow a required release/branch gate in the repository settings; the normal
hosted CI deliberately excludes `real_nx` because it cannot provide Siemens NX.
