# Contributing

NX MCP changes are tracked against [Roadmap #10](https://github.com/DreamEnding/NX_MCP/issues/10).
Check existing issues and PRs before substantial work. Keep each PR focused on one
coherent change. New certified tools, bridge protocol changes, or more than about
500 lines of non-test code require an issue or RFC first. Both bridges must
implement the same certified command contract.

## Local development

Use Python 3.10+ in a dedicated environment and PowerShell 7 on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pre_commit install --install-hooks
.\.venv\Scripts\python.exe -m pre_commit run --all-files
.\.venv\Scripts\python.exe -m mypy src/nx_mcp
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m "not real_nx" --cov=nx_mcp --cov-branch --cov-fail-under=78
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
```

Follow the existing four-space indentation, double quotes and 100-character
line-length target. Keep sidecar imports independent of NX, and keep the Python
NX bridge independent of `mcp` and `pydantic`. Run NXOpen calls on NX's main
thread. Preserve validation at both process boundaries and opt-in gates.

For a fix, add a test that fails before the fix and passes afterwards. For new
validation, test rejected inputs and confirm they cannot mutate NX. For a
refactor, run the existing suite. The parity test runs in hosted CI and must
reject a missing C# command or an unreadable contract, rather than skipping it.
Fake-NX tests and a C# build do not certify native CAD behavior.

## Real NX evidence

Follow [real-NX validation](docs/real-nx-validation.md) using native, disposable
parts. Record the NX release/build, bridge implementation, exact Git commit,
working-tree state, commands and results. Attach the versioned evidence JSON
and acceptance/restart JUnit reports. Mark unperformed checks as pending and
separate test failures from runner, startup or licence failures.

The manual self-hosted workflow runs only reviewed, trusted refs. Never execute
untrusted PR code on that runner. Contributor reports from the NX validation
issue form are informative; they do not replace release acceptance at the same
candidate commit. Do not promote `0.2.0.dev0` or remove opt-in gates based on
historical results; follow [RELEASE.md](RELEASE.md).

Write documentation and skills in your own words. Do not copy Siemens
documentation or videos. Never commit or upload NX assemblies, licence files,
tokens, descriptors, raw protocol traffic or customer CAD. Use a small,
shareable sample to describe a reproducible problem.

## Pull requests

Use `feat:`, `fix:`, `chore:` or `ci:` commit subjects. Link the tracking issue,
explain the resulting behavior and include the checks actually run. Complete
the PR template's evidence section for NX-facing work. Disclose AI assistance;
the human author remains responsible for every line and reported validation.
Use [SECURITY.md](SECURITY.md) for security findings.
