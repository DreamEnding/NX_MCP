# M1 implementation and validation status

Updated: 2026-10-01 (Asia/Shanghai). Initial implementation: 2026-09-30.
Tracking spec: [Roadmap #10, M1](https://github.com/DreamEnding/NX_MCP/issues/10).
Scope: M1 only. M2 contracts and measurement tools are not part of this change.

The remaining M1 development work is implemented and locally reviewed.
**M1 release acceptance is still pending**: this checkout is uncommitted, and
there is no NX2206 installation on this machine. Keep `0.2.0.dev0`; no release
tag was created. Follow [RELEASE.md](../RELEASE.md) for the final candidate gate.

## Requirement trace

| Roadmap requirement | Implementation / evidence | State |
| --- | --- | --- |
| Python startup lock and descriptor ownership | Already merged in `5a404c9` / #11 | Complete in baseline |
| Canonical journal launch without `-nx` | Already merged in `101c289` / #13; maintainer's Issue #10 comment defers configurable switches | Complete in baseline |
| Hosted Python/C# contract parity | Already merged in `0308df9` / #12; parser/drift negative tests pass locally | Complete in baseline |
| Additive bridge implementation in status | Both executors report `python_batch` / `csharp_gui`; sidecar defaults missing old-v1 field to `unknown`; protocol remains 1 | Implemented; Python real runtime passed; C# compiled |
| Versioned runtime/evidence JSON | Standard-library journal plus sidecar collector; exact NX executable build, embedded/sidecar Python, SDK, OS, launch provenance, Git commit/dirty state and live bridge identity | Implemented; local regression and Python real runtime passed |
| Python batch/NX2506 acceptance and restart at candidate tag | Development-tree acceptance and restart passed; clean candidate tag still required | Release evidence pending |
| C# GUI/NX2206 acceptance and restart at same candidate | Full C# build passes against local NX2506 assemblies; GUI operator restart test and documentation supplied | NX2206 runtime evidence pending |
| Contribution/security/evidence templates | `CONTRIBUTING.md`, `SECURITY.md`, PR template, NX validation issue form | Complete |
| Release checklist/changelog and `v0.2.0` | `RELEASE.md` and `CHANGELOG.md` supplied; release remains gated | Documentation complete; version/tag pending matrix |

## Initial local checks (2026-09-30)

Interpreter: Python 3.12.7; MCP SDK 2.2.0; Pydantic 2.13.5; Windows 11 build 26200.
Commands run from the checkout using its `.venv/Scripts/python.exe`:

| Check | Result |
| --- | --- |
| `python -m pytest -q -p no:cacheprovider -m "not real_nx" --cov=nx_mcp --cov-branch --cov-fail-under=78` | 414 passed, 13 isolated-USD tests skipped, 5 real-NX tests deselected; branch coverage 83.72% |
| `python -m pre_commit run --all-files` and `--files` for new artifacts | Passed |
| `python -m mypy src/nx_mcp` | Passed, 17 source files |
| `python -m ruff check src tests examples` and `ruff format --check` | Passed |
| `git diff --check` | Passed |
| `python -m pip wheel . --no-deps -w build/m1-wheel` | Built `nx_mcp-0.2.0.dev0-py3-none-any.whl` |
| Wheel installed into a separate target, imported with isolated Python outside the checkout | Server, bridge, evidence/provenance imports, both installed entry points, smoke/evidence CLI help passed |
| `nx_gui_bridge/build.ps1 -NxRoot D:\Siemens -OutputDirectory build/m1-gui-bridge` | Complete GUI DLL compiled against NX2506; compilation is not GUI runtime evidence |

Regression tests were run failing before the status/evidence implementations
and passed after implementation. Evidence tests reject malformed probe fields,
failed imports, mismatched bridge/release, changed commit or clean/dirty state,
and overwriting a prior result. GUI lifecycle doubles reject unchanged PID,
unchanged token, an existing user work part and Python evidence used as GUI
evidence. They are marked `fake_nx` and do not certify native GUI behavior.

## Initial real Python batch run (2026-09-30)

- NX: `v2506`; installed `ugraf.exe` 2506.4021, `run_journal.exe` 2506.4000.
- Embedded Python: 3.12.9, 64-bit; `mcp` and `pydantic` unavailable inside NX;
  `bridge_import_error:null`.
- Bridge: observed `python_batch`, protocol 1, native batch journal mode.
- Source: development working tree at base
  `5a404c9ea690d58567464fb62593d15a6ccec75d`, `git_dirty:true`.
- Launch: `D:\Siemens\NXBIN\run_journal.exe <journal-file>`, no extra switches.
- Workspace: `build/m1-final-20260930-171536`, with a separate absolute state
  directory shared by bridge and sidecar. All generated CAD remains local.

After running the runtime probe and starting the isolated bridge, the final
commands were:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx.py --junitxml=build/m1-final-20260930-171536/acceptance.xml
.\.venv\Scripts\python.exe -m nx_mcp.evidence --probe build/m1-final-20260930-171536/nx-runtime-probe.json --output build/m1-final-20260930-171536/evidence.json --bridge python_batch
# Stop the owned bridge completely before the separate process test.
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx_restart.py::test_real_nx_client_reconnects_after_restart --junitxml=build/m1-final-20260930-171536/restart.xml
```

The environment setup and lifecycle sequence are documented in
[real-NX validation](real-nx-validation.md#python-batch-reproduction).

| Result | Evidence |
| --- | --- |
| 20 block iterations, sketch-line metadata check and negative cases; 3 tests passed, no failures/errors/skips; 12.99 s | `acceptance.xml`, including iteration/commit/bridge properties; 20 nonempty STEP and 20 part files; the line geometry proof was strengthened on 2026-10-01 |
| Two separately owned NX journal processes, same client reconnects, token rotates, descriptor removed; 1 test passed, no failures/errors/skips; 61.64 s | `restart.xml` |
| Observed runtime and bridge identity; no secrets included | `nx-runtime-probe.json`, `evidence.json` |

Local artifacts are under the ignored `build/m1-final-20260930-171536/`
directory. Share only the JSON/JUnit files. These results do not certify the
unmodified base commit or a release tag: the changed source was uncommitted.

## Standards

The paragraphs below record the initial 2026-09-30 review. The comprehensive
review and its fixes are recorded in the dated section below.

The independent standards review found one missing `fake_nx` marker in the
runtime-probe tests; it was fixed and re-reviewed. Final outstanding findings:
**0**. The shared provenance module keeps NX imports standard-library-only,
and the GUI lifecycle tests make their fake/runtime boundary explicit.

## Spec

The independent Roadmap review found two gaps: the historical smoke flow did
not exercise three of the 16 tools, and the collector could copy stale Git
metadata from a probe. Both were fixed and re-reviewed. The final real-NX run
includes sketch/feature queries and independent line creation; the collector
rejects a different commit or clean/dirty state. Final outstanding technical
findings: **0**.

Review totals: Standards 1 original finding, 0 remaining; Spec 2 original
findings, 0 remaining. The pending NX2206 and clean-tag runs are disclosed
release requirements, not passing checks.

## Comprehensive review fixes (2026-10-01)

The comprehensive review found four P2 acceptance defects. Each was reproduced
by a failing regression before its fix; independent Standards/Spec re-review
closed all four and found no additional actionable problems.

| Finding | Fix and verification |
| --- | --- |
| Another GUI instance could pass while the original NX remained alive | Capture a read-only Windows process handle before operator actions; require the original object to have exited. A real Windows child-process test checks the handle observes exit; fake lifecycle tests reject an alive original process. |
| GUI restart could switch NX environment and still pass | Compare executable installation, exact file build and NX release across restart; require stable candidate Git state. Both restart suites record candidate/runtime metadata and run prefix in JUnit. Different release/build/path and changed-candidate cases are rejected. |
| Line test could pass on metadata without geometry, bypassing MCP | Use an MCP client to create four closed-profile lines, extrude a body and export nonempty solid STEP. The no-op-line regression fails at extrusion; the positive case observes all four MCP calls and downstream solid checks. |
| Cleanup failure could replace the modeling error | Preserve the modeling failure and log secondary cleanup errors; fail normal-path cleanup without retry. Status-query and close failures are tested on both failing and successful modeling paths. |

Updated local checks: **426 passed, 13 isolated-USD tests skipped, 5 real-NX
tests deselected; branch coverage 83.58%**. Pre-commit, Ruff/formatting,
Windows and Linux mypy checks, wheel build and isolated wheel imports passed.
The process observer uses standard-library Windows APIs and never terminates
operator-owned NX processes. The journal and observer share the existing
standard-library executable-version reader.

Fresh real-NX workspace: `build/m1-review-fixes-20261001-100755/`.
Environment: NX2506/2506.4021, embedded Python 3.12.9, sidecar Python 3.12.7,
MCP 2.2.0, Windows 11 build 26200, observed `python_batch`, protocol 1.
Source remains the uncommitted development tree at base
`5a404c9ea690d58567464fb62593d15a6ccec75d`, `git_dirty:true`.

| Fresh real result | Artifact |
| --- | --- |
| 20 rectangle block iterations, four-line closed profile/extrude/solid STEP, and negative cases: 3 passed, 0 failures/errors/skips, 13.62 s | `acceptance.xml`; 20 block STEP files and a separate 7991-byte `review-fixes/line/run-01.stp` containing solid geometry |
| Two separately owned Python NX processes, same client reconnects and token rotates: 1 passed, 0 failures/errors/skips, 58.14 s | `restart.xml` |
| Candidate, bridge, release and `run_prefix:review-fixes` agree across both JUnit suites and JSON; restart build matches 2506.4021 | `nx-runtime-probe.json`, `evidence.json`; descriptor removed after validation |

Commands matched the reproducible setup in [real-NX validation](real-nx-validation.md),
using a unique workspace, isolated state directory and `NX_MCP_REAL_NX_RUN_PREFIX=review-fixes`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx.py --junitxml=build/m1-review-fixes-20261001-100755/acceptance.xml
.\.venv\Scripts\python.exe -m nx_mcp.evidence --probe build/m1-review-fixes-20261001-100755/nx-runtime-probe.json --output build/m1-review-fixes-20261001-100755/evidence.json --bridge python_batch
# Stop the first bridge completely before running restart acceptance.
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m real_nx tests/test_real_nx_restart.py::test_real_nx_client_reconnects_after_restart --junitxml=build/m1-review-fixes-20261001-100755/restart.xml
```

These results are development-tree Python acceptance. The corrected GUI
restart logic passed local lifecycle regressions and native Windows handle
checks, but no C# GUI/NX2206 runtime run occurred on this machine.

## Remaining release work

Run the documented C# GUI/NX2206 acceptance and operator-assisted NX restart,
then rerun both matrix cells at the same reviewed, clean candidate tag. Retain
matching evidence JSON and JUnit artifacts before version/tag publication.

The collector matches NX release and Git commit/cleanliness. It does not
distinguish different maintenance builds with the same release, detect
dirty-to-dirty content edits, or prove which source produced a manually loaded
GUI DLL. Use the same recorded installation, rebuild/load the candidate DLL,
repeat acceptance after edits and enforce the clean release checklist.
