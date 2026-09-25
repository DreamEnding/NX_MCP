# NX MCP Server

NX MCP is a local Model Context Protocol server for Siemens NX automation. The
`0.2.0.dev0` line replaces the unverified direct-attach design with two explicit
processes:

```text
MCP client <--stdio--> Python sidecar <--authenticated loopback JSON-RPC--> NX bridge <--NXOpen--> NX
```

The sidecar can start without NX. Tool calls fail with `NX_BRIDGE_UNAVAILABLE`
until an NX journal starts the bridge.

## Current status

The sidecar, bridge protocol, input/output schemas, workspace confinement, and
core workflow have automated coverage. The Python bridge passed the documented
20-run batch workflow on Siemens NX 2506 (`ugraf` 2506.4021) on 2026-08-21.
It remains opt-in while a non-blocking NX GUI event pump is validated; the
bundled Python Journal runner is intentionally batch-only. The safety-hardening
changes have local regression coverage and a successful NX runtime import probe
on 2026-09-14, but still need a new full real-NX acceptance run; the historical
20-run result does not certify the changed save/close/undo behavior.

The default `tools/list` exposes only these 16 tools:

- Status: `nx_status`
- Files: `nx_create_part`, `nx_open_part`, `nx_save_part`, `nx_close_part`, `nx_export_step`
- Queries: `nx_list_sketches`, `nx_list_bodies`, `nx_list_features`
- Sketch: `nx_create_sketch`, `nx_sketch_line`, `nx_sketch_rectangle`, `nx_finish_sketch`
- Modeling: `nx_extrude`
- Recovery/view: `nx_undo`, `nx_fit_view`

The 34 old tools outside the certified surface remain unverified and hidden by
default. `NX_MCP_ENABLE_EXPERIMENTAL=1` registers them through the bridge;
Journal tools additionally require `NX_MCP_ENABLE_JOURNAL=1`.

## Agent modeling workflow

The canonical [NX modeling skill](skills/nx-modeling/SKILL.md) explains discovery,
part ownership, units, sketch/extrude sequencing, result checks, and recovery.
It composes existing tools; it does not add permissions or replace code-enforced
safety checks. Read it from this checkout; no global skill installation or
agent-directory copies are created automatically.

### Capabilities and validation

"Implemented", "default", "locally tested", and "real-NX accepted" are separate
claims. The historical target is native NX 2506 (`ugraf` 2506.4021), batch mode;
there is no blanket certification of every tool or failure branch.

| Capability | Availability | Local evidence | Current real-runtime boundary |
| --- | --- | --- | --- |
| Rectangle/sketch/extrude, queries, STEP, undo, save/reopen | Default tools | Automated core workflow tests | Historical 2026-08-21 batch loop; current safety/SDK changes await a fresh loop |
| Other default-tool branches, including sketch lines | Default tools | Automated tests | Only scenarios explicitly recorded in the acceptance guide are accepted |
| Authentication, workspace boundaries, stale/wrong-kind IDs | Enforced by default | Boundary and fake-NX tests | Current real-NX negative cases await rerun |
| Uncertain execution and forced rollback failure | Enforced recovery rules | Local fault injection | No real-NX fault-injection acceptance claimed |
| Bridge/process restart | Existing lifecycle | Automated bridge tests | Current independent-process restart test awaits rerun |
| Interactive GUI scheduling | C# add-in in `nx_gui_bridge/`, loaded on demand | Protocol checks against the live add-in | NX2206 acceptance only (2026-09-18, re-validated 2026-09-20); see [GUI bridge](docs/gui-bridge.md) |
| Legacy tools / arbitrary Journals | Disabled by default | Mock-NX coverage only | Unverified; explicit opt-in is not certification |
| STEP-to-USD sample validation | Separate example, not MCP | Unit tests and opt-in OpenUSD checker fixtures | Fresh NX STEP-to-USD chain remains pending |

See [real NX evidence](docs/real-nx-validation.md) for dates and exact coverage.
Session object IDs are not permanent asset IDs. Saving/closing the work part
must not be described as saving/closing all assembly components.

### Independent STEP-to-USD validation

The [USD validation guide](docs/usd-validation.md) runs a small acceptance script
in a separate Python 3.12 converter environment. It verifies the STEP block from
this run, its converted mesh, units, world-space dimensions, and dependencies.
Neither the NX interpreter nor the sidecar needs `usd-convert-cad` or `pxr`.
No `nx_export_usd` tool, SDK/schema change, or default CI dependency is introduced.

## MCP SDK v2 support

The sidecar now uses the official Python SDK `mcp>=2.2,<3` and
`pydantic>=2.12,<3`, with Python 3.10+ retained. `MCPServer` replaces the SDK's
old `FastMCP` class; this is not a migration to the separate FastMCP package.
Modern clients use MCP `2026-07-28` discovery over stdio, while initialize-based
clients remain supported. The smoke client negotiates automatically.

Default tools, JSON field names on the wire, existing NX error codes, and the
internal NX bridge protocol v1 remain unchanged. The SDK major version is
independent of this project's `0.2.0.dev0` release gate. See
[MCP SDK v2 migration](docs/migration-mcp-sdk-2.md) for setup and compatibility.

## Requirements

- Windows with a local native Siemens NX installation (validated on NX 2506)
- Python 3.10+
- The package installed in the sidecar interpreter
- An NX journal that can import `nx_mcp` (the bundled Journal examples load
  the checkout's `src` directory automatically; the NX side has no `mcp` or
  `pydantic` dependency)
- A dedicated test/project directory configured as `NX_MCP_WORKSPACE`

Install the sidecar and development dependencies in a dedicated environment
from PowerShell 7 (do not reuse an interpreter still running SDK v1):

```powershell
$env:NO_PROXY = "localhost,127.0.0.1"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Use the absolute path to `.venv/Scripts/python.exe` as the MCP client's
`command` below, or activate this environment before using `python` commands.

## Internal feasibility run

1. Set `NX_MCP_WORKSPACE` to a disposable directory.
2. For the target-build feasibility test only, set
   `NX_MCP_ALLOW_UNVERIFIED_PYTHON_BRIDGE=1` in the NX environment.
3. Set `NX_MCP_BRIDGE_STOP_FILE` to a new path inside the workspace, then run
   `examples/start_nx_bridge.py` as an NX journal. The canonical invocation
   is `run_journal.exe <journal-file>`, with no extra switches. The journal
   pumps requests on NX's main thread and writes an authenticated session
   descriptor to `%LOCALAPPDATA%\nx-mcp\bridge.json`.
4. Configure the MCP client to launch the sidecar:

```json
{
  "mcpServers": {
    "nx-mcp": {
      "command": "python",
      "args": ["-m", "nx_mcp.server"],
      "env": {
        "NX_MCP_WORKSPACE": "D:\\NX_MCP_WORKSPACE"
      }
    }
  }
}
```

5. Run the real-NX acceptance loop from an external PowerShell 7 terminal:

```powershell
python -m nx_mcp.real_smoke --workspace D:\NX_MCP_WORKSPACE --iterations 20 --run-prefix acceptance
```

6. Create the configured stop file when finished; the journal stops the bridge
   cleanly.

Do not use production parts for this test. The batch bridge is not evidence of
interactive GUI responsiveness; use a non-blocking NX UI scheduler or the
agreed minimal C# NX-side bridge before enabling an interactive pilot.

## Interactive NX GUI bridge

For live modeling in an open NX window, build and load the C# add-in in
`nx_gui_bridge/` instead of the batch journal. It serves the same bridge
protocol, so the sidecar and MCP tools are unchanged. See
[GUI bridge](docs/gui-bridge.md) for the build, configuration, limits and the
NX2206 validation record.

`NX_MCP_STATE_DIR` moves `bridge.json` out of `%LOCALAPPDATA%\nx-mcp`. Set it
on both sides when an MSIX-packaged MCP client and NX see different AppData
folders. It must be an absolute path, so that the NX bridge and the sidecar
name the same file. `bridge.json` carries the session token, so choose a
directory only your account can read; the C# add-in additionally writes the
descriptor with a rule that allows your account alone.

## Security model

- IPC binds only to `127.0.0.1` on a random port and requires a random 256-bit
  session token.
- Every file argument is relative to `NX_MCP_WORKSPACE`; traversal, absolute
  paths, and resolved links outside the workspace are rejected.
- Journal execution and all 34 legacy tools are disabled by default. Both the
  sidecar and NX bridge must receive the opt-in environment flags.
- Object IDs are opaque and valid only for the current part session. Undo and
  failed mutation rollback invalidate references; query new IDs before use.
- Save validates the active part's path; save and close affect only the work
  part, not its assembly tree. A failed rollback blocks writes until recovery.
- Timeout/disconnect errors distinguish `not_started` from `unknown` execution.
  Never automatically replay an uncertain mutation; inspect the model first.

## Local quality gates

The ordinary suite does not require NX. Install the Git hooks once, then use
the same checks as CI:

```powershell
python -m pip install -e ".[dev]"
python -m pre_commit install --install-hooks
python -m pre_commit run --all-files
python -m pytest -q -p no:cacheprovider -m "not real_nx" --basetemp .pytest-tmp
```

The pre-commit hook runs file and style checks. The pre-push hook runs the
non-real-NX pytest suite and the sidecar mypy gate. Tests marked `legacy` cover
the opt-in 0.1 surface; tests marked `fake_nx` do not validate NXOpen itself.
Hosted CI runs the core suite across supported Python and OS combinations,
runs legacy mock-NX tests separately, and enforces at least 78% branch
coverage in its canonical Ubuntu/Python 3.12 coverage job.

Real NX acceptance is intentionally separate. Dispatch
`.github/workflows/real-nx.yml` from a dedicated self-hosted Windows runner
labelled `self-hosted`, `windows`, and `nx`, with `NX_RUN_JOURNAL` set to the
absolute path of `run_journal.exe`.

See [architecture](docs/architecture.md), [0.1 migration](docs/migration-0.2.md),
[MCP SDK v2 migration](docs/migration-mcp-sdk-2.md),
and [real NX validation](docs/real-nx-validation.md) for implementation and
release gates.

## Star History

The chart updates automatically when this repository receives a star and daily
at 04:37 UTC (12:37 Asia/Shanghai). Changes to the Star History workflow on
`master` also trigger an update; manual runs remain available in GitHub Actions.
Daily refreshes reconcile missed events and removed stars. GitHub may delay
scheduled runs, so the chart is not a real-time counter. Charts are published
to the dedicated `star-history` branch without changing `master`.

<picture>
  <source
    media="(prefers-color-scheme: dark)"
    srcset="https://raw.githubusercontent.com/DreamEnding/NX_MCP/star-history/assets/star-history-dark.svg"
  />
  <img
    alt="Star History Chart"
    src="https://raw.githubusercontent.com/DreamEnding/NX_MCP/star-history/assets/star-history.svg"
  />
</picture>
