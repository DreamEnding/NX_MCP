# Official MCP Python SDK v2 migration

## Scope and versions

This upgrade targets official `mcp` **2.2.0**, with the dependency range
`mcp>=2.2,<3`. MCP protocol revisions use dates: the modern revision tested
here is **2026-07-28**, not a protocol string named `2.0`. The separate
`fastmcp` distribution is not installed. The project version remains
`0.2.0.dev0`: upgrading the SDK does not remove the real-NX release gate.

Python remains 3.10+. Pydantic's floor is raised to `2.12` to match SDK v2;
the project keeps its `<3` bound. The SDK installs and exact-pins its own
`mcp-types` dependency, so this project does not pin that distribution
independently. The NX bridge still imports with only the standard library;
never install sidecar dependencies into NX's embedded interpreter.

## Changes for Python integrators

| SDK v1 usage | SDK v2 usage in this project |
| --- | --- |
| `mcp.server.fastmcp.FastMCP` | `mcp.server.mcpserver.MCPServer` |
| `mcp.server.fastmcp.exceptions.ToolError` | `mcp.server.mcpserver.exceptions.ToolError` |
| `create_connected_server_and_client_session(server)` | `Client(server)` |
| `ClientSession` plus explicit `initialize()` in smoke | `Client(StdioServerParameters(...))` |
| `result.isError`, `result.structuredContent` | `result.is_error`, `result.structured_content` |
| `tool.inputSchema`, `tool.outputSchema` | `tool.input_schema`, `tool.output_schema` |

The wire format's camelCase fields do not change. Existing tool errors still
use `isError: true`; SDK-wrapped text contains the existing NX error payload.
No automatic mutation replay or new experimental tools are introduced.
Server identity explicitly reports NX MCP's project version, not the SDK's.

## Connection modes

- `Client(..., mode="auto")` discovers modern support, falling back to the
  initialize handshake for old servers. This is the smoke runner's default.
- `mode="legacy"` forces the initialize handshake. This is unrelated to this
  repository's `legacy` pytest marker or experimental CAD tools.
- `mode="2026-07-28"` pins the modern revision without discovery; server
  identity metadata may be absent until discovered. Do not force `initialize`
  on such a connection.

The sidecar remains stdio-only. Streamable HTTP, OAuth, sampling, elicitation,
and tasks are not added merely because SDK v2 provides additional APIs. The
NX TCP descriptor's `protocol_version` and the `nx_status.bridge_protocol`
field remain **1**, independently of MCP protocol negotiation.

## Upgrade and verify (PowerShell 7)

```powershell
$env:HTTP_PROXY = $env:HTTPS_PROXY = "http://127.0.0.1:7897"
$env:NO_PROXY = "localhost,127.0.0.1"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_v2.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m "not real_nx" --cov=nx_mcp --cov-branch --cov-fail-under=78
.\.venv\Scripts\python.exe -m mypy src/nx_mcp
.\.venv\Scripts\python.exe -m pre_commit run --all-files
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
```

Configure the MCP application to use this environment's absolute Python path,
then restart the sidecar. A globally installed SDK v1 cannot run the migrated
source. Keep experimental/Journal flags unchanged and retain workspace
confinement. If the public package index is unavailable through the local
proxy, an explicit trusted mirror can be selected with `--index-url`; no pip
configuration or proxy bypass is required.

The weekly/manual compatibility workflow retains the three-OS / Python
3.10–3.12 matrix. The Ubuntu/Python 3.10 job
also installs `mcp==2.2.0` and `pydantic==2.12.0` to check declared floors.
Tests cover modern and initialize-based connections, tool schemas, structured
success, errors, and workspace rejection through actual subprocess stdio,
as well as the fake-NX workflow. These do not replace a real-NX CAD run.

## Local validation (September 14, 2026)

On Windows with Python 3.12.7, official MCP SDK 2.2.0, and Pydantic 2.13.5:

- Non-real-NX suite: **249 passed**, three real-NX tests deselected;
  coverage with branch measurement: **82.92%** (required: 78%).
- Mypy: 15 source files passed. Ruff checks/formatting and pre-commit passed.
- A separate SDK 1.27.0 client connected to the upgraded stdio subprocess,
  negotiated `2025-11-25`, discovered 16 tools, and verified bridge-unavailable
  and workspace-rejection errors.
- The rebuilt wheel installed outside the checkout in a clean environment
  with SDK 2.2.0 and the Pydantic 2.12.0 floor. Dependency checks, server
  imports, modern negotiation, 16-tool discovery, and the smoke CLI help passed.
  This is a floor smoke check, not a full local Python 3.10 matrix run.

## Real NX gate

The historical 20-run NX 2506 acceptance used SDK v1. The September 14, 2026
runtime-only probe checks NX-side imports, not the new sidecar. Rerun the full
20-iteration workflow, negative cases, and restart test using the upgraded
sidecar before declaring the migrated release NX-certified. See
[real NX validation](real-nx-validation.md).

## Upstream reference

The implementation follows the official
[SDK v1-to-v2 migration guide](https://py.sdk.modelcontextprotocol.io/migration/).
