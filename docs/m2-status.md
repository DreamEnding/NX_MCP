# M2 development status

Updated: 2026-10-02 (Asia/Shanghai).
Tracking: [Roadmap #10, M2](https://github.com/DreamEnding/NX_MCP/issues/10).

This increment implements part units and MCP operation annotations. The default
surface remains 16 tools, protocol v1 and version `0.2.0.dev0`. M1 release
acceptance still requires NX2206 GUI and matching clean-candidate evidence.

## Implemented

- `nx_status.units` reads native work-part units in both bridges: `mm`, `inch`,
  or `null` without a work part. Old-bridge omission stays compatible as `null`.
  Unrecognized units fail explicitly. Sketch/extrusion inputs retain native units.
- All certified tools publish `readOnlyHint` and `destructiveHint`. Status/list
  queries are read-only; save, close, STEP export and undo may be destructive.
- The canonical `nx-modeling` skill uses reported units, converts requested
  dimensions before modeling and asks when an old bridge cannot supply units.
- Hosted tests compare status result fields between Python and C#, in addition
  to the existing command/default/mutation/protocol parity checks.
- Real acceptance exercises metric and inch parts through MCP, including save,
  close and reopen. Cleanup closes only the test's confirmed work part.

## Validation

New local regressions were run failing before implementation and passing after
the change. They cover units, no work part, a changed work part, unrecognized
units, older-bridge omission and all tools' annotations. MCP negotiation tests
check the serialized hint names in modern and legacy modes.

Interpreter: sidecar Python 3.12.7, MCP 2.2.0; Windows 11 build 26200.
Commands used `.venv/Scripts/python.exe` from the repository root.

| Check | Result |
| --- | --- |
| `python -m pytest -q -p no:cacheprovider -m "not real_nx" --cov=nx_mcp --cov-branch --cov-fail-under=78` | 440 passed, 13 isolated-USD tests skipped, 7 real-NX tests deselected; 83.67% coverage with branch measurement |
| Final status-parity regression after placing it with the existing parity tests | 24 parity tests passed |
| Pre-commit, including new docs/Roadmap; Ruff checks/formatting; mypy; `git diff --check` | Passed; mypy checked 17 source files |
| `nx_gui_bridge/build.ps1 -NxRoot D:\Siemens -OutputDirectory build/m2-gui-bridge` | Complete DLL compiled against NX2506 assemblies |
| `python -m pip wheel . --no-deps -w build/m2-wheel`, isolated target install/imports | Built `0.2.0.dev0`; server/bridge imports and units schema passed |

C# compilation is not evidence of interactive GUI runtime behavior. No NX2206
or C# GUI runtime run occurred on this machine.

## Real Python batch evidence

- Date: 2026-10-02 (Asia/Shanghai).
- NX: `v2506`, installed `ugraf.exe` build `2506.4021`.
- Embedded Python: 3.12.9, 64-bit; bridge imports without MCP/Pydantic.
- Observed bridge: `python_batch`, protocol 1.
- Source: uncommitted development tree at base
  `8f5b123c70ef3300357e75bf4b5b00147b86c047`, `git_dirty: true`.
- Workspace: `build/m2-units-20261002-125906/`, with an isolated shared state
  directory and `NX_MCP_REAL_NX_RUN_PREFIX=m2-units`.

| Real result | Artifact |
| --- | --- |
| Metric/inch units through MCP, including save/close/reopen; 20 original block iterations, four-line profile/solid STEP and negative requests: 5 passed, 0 failures/errors/skips, 40.01 s | `acceptance.xml`; 20 fresh block STEP exports, separate line STEP and unit-test parts |
| Two independently owned NX processes, same client reconnects, token rotates and descriptor is removed: 1 passed, 0 failures/errors/skips, 100.88 s | `restart.xml` |
| Runtime build, Git state, bridge identity and run prefix agree across JSON and JUnit reports | `nx-runtime-probe.json`, `evidence.json` |

Commands, after the runtime probe and isolated bridge startup:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx.py --junitxml=build/m2-units-20261002-125906/acceptance.xml
.\.venv\Scripts\python.exe -m nx_mcp.evidence --probe build/m2-units-20261002-125906/nx-runtime-probe.json --output build/m2-units-20261002-125906/evidence.json --bridge python_batch
# Stop the owned bridge before the independent process restart test.
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx_restart.py::test_real_nx_client_reconnects_after_restart --junitxml=build/m2-units-20261002-125906/restart.xml
```

Setup follows [real-NX validation](real-nx-validation.md). The initial attempt
in `build/m2-units-20261002-125355/` exceeded a two-minute cold-start deadline
before running acceptance; its owned bridge subsequently exited cleanly.
The successful isolated rerun allowed five minutes for startup in the local
driver. The existing restart test retained its 120-second deadlines. Neither
attempt changed the product's timeout configuration.

Share only the evidence/probe JSON and JUnit reports; generated CAD and native
logs remain local. These results validate this Python development tree, not
the clean base commit or a release tag. M1's release matrix remains pending.

## Remaining

The [inspection contract](inspection-contract.md) contains the implemented
status contract plus a draft covering measurement units, coordinates, exactness,
tolerances, accepted IDs and errors. Publish/review the inspection design issue
before implementation. Bounding-box, volume and distance tools, numeric smoke
assertions, independent STEP metrology, a GUI-session skill and NX2206
compatibility notes remain pending. No measurement tool is promoted by this
increment and M2 is not complete.
