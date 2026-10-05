# Changelog

## Unreleased — 0.2.0.dev0

### Added

- M2 foundation: native work-part units in `nx_status` for both bridges, with
  `null` for no part or an older bridge; unit-aware modeling guidance.
- Explicit `readOnlyHint` and `destructiveHint` on all 16 certified MCP tools.
- Metric/inch save-and-reopen acceptance and status-output parity checks.
- Inspection contract draft for the remaining M2 measurement tools.
- Official MCP Python SDK v2 stdio sidecar with typed certified tool contracts.
- Authenticated loopback protocol v1, workspace confinement, session-scoped IDs,
  and explicit mutation rollback/recovery behavior.
- C# interactive GUI bridge and Python batch bridge for the existing 16 tools.
- Hosted-CI parity checks for command parameters/defaults, mutation sets,
  protocol version, message limit, receive deadline and certified tool names.
- `nx_status.bridge_implementation` (`python_batch` or `csharp_gui`). The
  sidecar reports `unknown` when an older protocol-v1 bridge omits the field.
- Versioned runtime/evidence JSON with exact NX build, embedded Python, OS,
  launch switches, commit/working-tree state and observed bridge identity.
- An operator-assisted GUI/NX restart acceptance test producing JUnit evidence.
- Contribution, security, validation-report and release checklists/templates.

### Fixed

- GUI restart acceptance confirms the original NX process exited and rejects
  changed NX installations, file builds, releases or candidate revisions.
- Restart JUnit records candidate/runtime provenance and the acceptance run prefix.
- Sketch-line acceptance runs through MCP and proves a four-line profile can
  extrude and export solid STEP geometry; metadata-only responses cannot pass.
- Modeling errors remain primary when failure cleanup also fails; normal-path
  cleanup failures still fail acceptance without retrying the close.
- STEP export stages output and rejects empty/stale translator results in both bridges.
- Both bridges serialize descriptor startup across processes, use per-writer
  temporary files and refuse an authenticated live descriptor.
- Canonical `run_journal.exe <journal-file>` launch no longer adds `-nx`.

### Release status

`0.2.0` is pending fresh, clean-candidate acceptance and restart evidence for
Python batch/NX2506 and C# GUI/NX2206 at the same revision. Historical runs,
fake-NX tests and uncommitted-tree evidence do not satisfy this gate. See
[RELEASE.md](RELEASE.md) and [validation](docs/real-nx-validation.md).
