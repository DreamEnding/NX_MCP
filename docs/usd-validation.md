# Independent STEP-to-USD sample validation

This example validates the **20 x 10 x 12.5 mm** solid exported by the current
real-NX acceptance run. It is not a general CAD exporter or an MCP tool. Follow
[real NX validation](real-nx-validation.md) first; a historical STEP cannot stand
in for a failed or blocked current run.

The runner exports `acceptance/run-01.stp` **before** undoing the extrusion and
saving the `.prt`. That saved `.prt` therefore is not an equivalent solid input.
Do not replace the STEP with it or reconstruct the expected USD by hand.

## Isolated environment

Run PowerShell 7 from the NX_MCP repository. Keep the converter environment outside
both repositories, separate from the sidecar and NX's embedded interpreter:

```powershell
$env:HTTP_PROXY = $env:HTTPS_PROXY = "http://127.0.0.1:7897"
$env:NO_PROXY = "localhost,127.0.0.1"
$usdEnv = Join-Path $env:LOCALAPPDATA "nx-mcp/usd-validation-venv"
# The existing sidecar interpreter must be Python 3.12 to use it as the venv seed.
.\.venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3, 12)"
if ($LASTEXITCODE -ne 0) { throw "Use an installed Python 3.12 interpreter instead." }
if (-not (Test-Path -LiteralPath $usdEnv)) {
    .\.venv\Scripts\python.exe -m venv $usdEnv
    if ($LASTEXITCODE -ne 0) { throw "Could not create converter environment." }
}
$usdPython = Join-Path $usdEnv "Scripts/python.exe"
& $usdPython -m pip install "usd-convert-cad==0.2.0"
if ($LASTEXITCODE -ne 0) { throw "Converter installation failed." }
& $usdPython -m usd_convert_cad --help
if ($LASTEXITCODE -ne 0) { throw "Converter help probe failed." }
```

Version 0.2.0 is the wheel checked for this example. The script records the
installed version and probes its actual help before conversion. Do not install
`usd-core` or a separate `pxr`: the converter wheel bundles its own USD runtime.
The script imports the converter before loading that runtime for inspection.
Missing packages, licenses, network access, or execution prerequisites are
failures to report, not reasons to install a different converter automatically.

## Run against this acceptance run

Set `$workspace` to the disposable directory whose **current** NX acceptance
passed. Both input and output arguments are relative to this directory:

```powershell
$workspace = "D:\NX_MCP_WORKSPACE" # Replace with this run's actual workspace.
& $usdPython examples/validate_step_to_usd.py --workspace $workspace `
    --input acceptance/run-01.stp --output-dir usd-check-a
if ($LASTEXITCODE -ne 0) { throw "First USD validation failed; inspect its report/logs." }
& $usdPython examples/validate_step_to_usd.py --workspace $workspace `
    --input acceptance/run-01.stp --output-dir usd-check-b
if ($LASTEXITCODE -ne 0) { throw "Second USD validation failed; inspect its report/logs." }
```

The output directory must not already exist. Reruns need new directory names;
there is no overwrite, cleanup, retry, or installation inside the script. Input
must be a nonempty `.step` or `.stp`. Absolute, drive-relative, and workspace-
escaping paths (including resolved links) are rejected. Use only trusted,
disposable acceptance files; path checks are not an OS sandbox for the native
converter or arbitrary asset resolvers.

The subprocess uses the dedicated interpreter, argument arrays, a 120-second
timeout, `.usdc` output, and `--up-axis z`; other conversion options remain at the
installed version's defaults. Timeout handling stops that subprocess, not NX.

## Success contract and evidence

Exit code 0 requires all of the following:

- Conversion returns 0 and creates a new, nonempty `scene.usdc`.
- The stage opens with payloads loaded, dependencies resolve, and there are no
  composition errors.
- At least one visible nonempty mesh has valid topology and finite points.
  Instance proxies are included. Bounds use actual world-transformed points,
  not authored extent hints.
- The stage explicitly authors a positive finite `metersPerUnit` and is Z-up.
- World-space dimensions are `0.020 x 0.010 x 0.0125` meters, within `0.00001`
  meter absolute error per axis. This tests scale and extents, not full geometric
  equivalence, volume, manufacturing accuracy, or CAD semantics.

Each owned output directory contains `scene.usdc` if conversion produced it,
`report.json`, and separate help/conversion stdout/stderr logs. The **script**,
not NVIDIA's converter, writes the report. It includes status/failure stage,
source SHA-256, converter version, invocation, converter exit code when available,
elapsed time, and validation measurements. Path preflight errors go to stderr
without creating an unsafe report location. A failure to write the report also
returns nonzero. If validation or conversion also failed, stderr preserves that
original diagnostic even when `report.json` cannot be written. Keep failed-run
evidence; do not interpret partial files as success.

Two fresh output directories must pass for the same current STEP; identical USD
bytes are not required. Reports/logs stay in the disposable workspace, not Git.
They contain local paths and converter diagnostics; review them before sharing
and do not publish confidential CAD assets or bridge credentials.

## Testing the checker without claiming CAD conversion

Ordinary sidecar tests use substitutes and do not import the converter or `pxr`.
Generated USD fixtures can additionally test the inspection code in the dedicated
converter environment; this is **not** an alternative to STEP-to-USD acceptance:

```powershell
& $usdPython -m pip install pytest pytest-asyncio
if ($LASTEXITCODE -ne 0) { throw "Checker test dependencies could not be installed." }
$env:NX_MCP_USD_CHECKER_TESTS = "1"
try {
    & $usdPython -m pytest -q -p no:cacheprovider `
        tests/test_usd_validation.py tests/test_usd_validation_runtime.py
    if ($LASTEXITCODE -ne 0) { throw "USD checker tests failed." }
} finally {
    Remove-Item Env:NX_MCP_USD_CHECKER_TESTS -ErrorAction SilentlyContinue
}
```

Run only these tests in this environment; it deliberately has no NX sidecar
installation. The runtime fixtures cover transformed points/stale extents,
instancing, payloads, missing dependencies, units/axis errors, invalid topology,
nonfinite points, invisible/empty meshes, and unreadable USD.
Orchestration tests also cover unreadable source/help files, log creation and
report write failures, and converter launch failures without retry or false success.

## Validation status

On 2026-09-14, the isolated Python 3.12.7 environment installed
`usd-convert-cad` 0.2.0 and passed its help probe. Its bundled OpenUSD reports
version 0.25.11. **48 orchestration unit tests and 13 real OpenUSD checker
tests passed** in that environment (61 total, no warnings). Generated USD
fixtures and mocked conversion processes are separate evidence from actual CAD
conversion. The sidecar still has neither `usd-convert-cad` nor `pxr` installed.

The current real-NX launch was rejected by the execution environment, so there
is no eligible fresh STEP and **neither of the two real conversion runs has
passed**. The new Skill's live NX exercise is also pending. Do not remove the
existing NX opt-in gates or claim an end-to-end release based on checker tests.

NX `.prt` remains the native editing source; STEP is an exchange snapshot and
USD is a downstream scene artifact. This workflow makes no claim of preserving
B-rep topology, feature history, PMI, assembly constraints, permanent CAD object
identity, or simulation-ready physics.
