# Release checklist

`0.2.0` is gated by [M1 in Roadmap #10](https://github.com/DreamEnding/NX_MCP/issues/10).
Keep `0.2.0.dev0` while any required evidence is missing. A successful import
probe, hosted tests, a full C# build and historical NX results are different
claims from release acceptance.

## Candidate

- [ ] Changes have been reviewed against the tracking issue and repository standards.
- [ ] Both bridge contracts pass hosted parity checks, including negative parser/drift tests.
- [ ] Pre-commit, mypy, non-real-NX tests with branch coverage >= 78%, and wheel checks pass.
- [ ] The complete C# DLL builds against each validation installation's NX assemblies.
- [ ] Select a reviewed, clean candidate commit and tag it as a release candidate.
      Run real NX only on that trusted ref; never on untrusted PR code.

## Required real-NX matrix

| Environment | Required artifacts at the same candidate commit |
| --- | --- |
| Python batch / NX2506 | Versioned evidence JSON, 20-iteration acceptance plus negative-case JUnit, process-restart JUnit |
| C# GUI / NX2206 | Versioned evidence JSON, 20-iteration acceptance plus negative-case JUnit, operator GUI/NX-restart JUnit |

- [ ] Both JSON files have `schema_version: 1`, the expected observed bridge,
      exact NX build, embedded Python/architecture, OS and matching `git_commit`.
- [ ] Both record `git_dirty: false`; development-tree evidence cannot certify a release.
- [ ] Probe launch switches are recorded: `[]` for the canonical successful
      `run_journal.exe <journal-file>` invocation, `null` for a probe run inside the GUI.
- [ ] Each acceptance report passes all 20 iterations and the negative cases without retries.
- [ ] Each restart report passes the test for its bridge; no skipped or missing tests.
      Python restart evidence does not certify the GUI bridge.
- [ ] Acceptance/restart JUnit properties match the evidence JSON's commit,
      cleanliness, run prefix, bridge and NX environment. The GUI test confirms
      the original NX process exited and the restarted installation/build matches.
- [ ] Confirm the GUI remains responsive, defers behind dialogs and stops cleanly.
- [ ] Artifact links and reproducible commands are recorded in the validation report.
      Retain only allowlisted evidence; keep generated CAD and NX assemblies local.

The exact commands and operator GUI restart sequence are in
[real-NX validation](docs/real-nx-validation.md). Missing licences/runners are
recorded as unavailable checks, never as passing results.

## Version and publication

- [ ] After the matrix above passes, update both `pyproject.toml` and
      `src/nx_mcp/__init__.py` to `0.2.0`, update wheel version assertions in
      `.github/workflows/ci.yml`, and
      finalize `CHANGELOG.md` and the supported-build documentation.
- [ ] Commit those final changes, tag the final candidate and rerun the local
      checks and both matrix cells. The final release commit must be the one
      named by both evidence files; an earlier candidate cannot cover new code.
- [ ] Inspect the wheel from an isolated directory and verify imports and entry points.
- [ ] Create `v0.2.0` on that same final candidate commit only after all checks pass.
- [ ] Publish the reviewed wheel and release notes with evidence links. Verify
      the published version, then mark M1 complete in the Roadmap.

## Signed GUI bridge assets (Issue #15)

- [ ] From the same clean release commit, build with `nx_gui_bridge/build.ps1
      -NxRoot <installation> -OutputDirectory <output> -Sign` on a machine with
      the matching NX Open programming tools and `dotnet_author` license.
      Require successful signing and `SignDotNet.exe -verify`; an unsigned
      build or hosted fake-signer test is not a signed release asset.
- [ ] Validate the final signed DLL in an interactive NX session without
      `dotnet_author`: on-demand load, `startup` load, certified acceptance and
      GUI restart. Record the exact NX build and results. The signature does
      not supply the NX feature licenses used by the tools.
- [ ] Publish a DLL for each supported NX release, or explicitly list the
      releases/builds tested with that DLL. Distinguish compilation, journal
      playback and signed-DLL runtime results; do not infer compatibility.
- [ ] In the release notes, record the full source commit, NX/SDK build,
      compiler and signing-tool versions, build command, verification result,
      acceptance evidence links and SHA-256 of the final signed DLL. Generate
      the hash after signing with `Get-FileHash -Algorithm SHA256` and compare
      it again after downloading the uploaded asset.
- [ ] Attach only the project DLL (and its public evidence), keeping Siemens
      SDK assemblies/resources, bridge credentials and generated CAD local.
- [ ] Update [the GUI bridge download instructions](docs/gui-bridge.md#users-without-an-author-license)
      to point to the actual signed release asset. Until it exists and passes
      these checks, leave Issue #15 open and describe the missing evidence.

Do not remove the Python feasibility gate as a consequence of batch acceptance;
changing its interactive support claim requires its own real GUI evidence.
