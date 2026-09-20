# Interactive NX GUI bridge

`nx_gui_bridge/` contains an NX Open .NET add-in that serves bridge protocol v1
from an interactive NX session. NX stays usable and the model updates on screen
while MCP tools run. The Python sidecar, the 16 MCP tools, the descriptor file,
the JSON-RPC envelope and the error codes are unchanged.

## Why a C# add-in

The bundled Python runner pumps requests in a blocking journal loop, so it is
batch-only. A non-blocking Python scheduler is not possible on NX2206: NX runs
Python startup scripts in a sub-interpreter (interpreter ID 1), and its UI
thread holds the GIL while idle. A ctypes Win32 timer callback deadlocked that
thread on its first tick. Every responsiveness probe over 20 s reported the
window as hung, no tick ran, and a Python daemon thread made no progress. This
is the case [architecture](architecture.md) reserves for a minimal C# plugin.

## How it works

- A background .NET thread owns the loopback listener: `127.0.0.1`, a random
  port, a 256-bit token, a 1 MiB limit per message and a 5 s receive deadline.
  Requests are handled one at a time, as in `nx_mcp.bridge`.
- Each call is handed to NX's UI thread through a hidden WinForms control. It
  starts only after `UF_UI_lock_ug_access(UF_UI_FROM_CUSTOM)` returns
  `UF_UI_LOCK_SET`, and the lock is released after the call.
- While a dialog or command owns NX, the call retries every 250 ms. After 60 s
  it fails with `NX_MAIN_THREAD_UNAVAILABLE` (`not_started`, retryable).
- The executor is a C# port of `NXOpenExecutor` for the 16 certified commands:
  - undo marks, rollback, and stale or wrong-kind IDs;
  - workspace confinement on both sides of the process boundary;
  - the STEP export settings (`InputFile`, layers `1-256`, solids and surfaces),
    including the temporary directory whose file becomes the destination only
    after the translator wrote a non-empty STEP.

  Legacy and experimental commands are not available.
- The NX status bar shows `NX MCP: <command>` while a call runs.

## Build

The build uses the in-box .NET Framework 4.x `csc.exe`; no SDK is needed:

```powershell
powershell -ExecutionPolicy Bypass -File nx_gui_bridge\build.ps1 -NxRoot "C:\Program Files\Siemens\NX2206" -OutputDirectory "$env:LOCALAPPDATA\nx-mcp\gui-bridge"
```

NX loads an unsigned add-in only with an NX Open .NET author license
(`dotnet_author`). Otherwise, sign the DLL with NX's `SignDotNet.exe`. Build
to a local folder: NX locks the DLL while it is loaded.

## Configure and run

1. Create `gui-bridge.json` next to the DLL:

   ```json
   {"workspace": "C:\\NX_MCP_WORKSPACE", "state_dir": "C:\\Users\\<you>\\.nx-mcp"}
   ```

   If there is no file next to the DLL, the bridge reads
   `%LOCALAPPDATA%\nx-mcp\gui-bridge.json`. Environment variables override
   both files.
   - `workspace` (or `NX_MCP_WORKSPACE`) is required.
   - `state_dir` (or `NX_MCP_STATE_DIR`) is where the bridge writes
     `bridge.json` and its log. The default is `%LOCALAPPDATA%\nx-mcp`.
2. Load the add-in:
   - **On demand:** use File > Execute > NX Open (Ctrl+U) and select
     `NxMcpGuiBridge.dll`. A message shows the port and the descriptor path.
     Running it again stops the bridge.
   - **At startup:** put the DLL and its `gui-bridge.json` in the `startup`
     folder of a custom directory listed in `UGII_CUSTOM_DIRECTORY_FILE`.
3. If you set `state_dir`, start the sidecar with the same `NX_MCP_STATE_DIR`.

An optional `stop_file` config value, or `NX_MCP_BRIDGE_STOP_FILE`, stops the
bridge when that file appears. A stale stop file is deleted at start.

The bridge logs each method, its outcome and its duration to `gui-bridge.log`
in the state directory. The token is never logged. Keep the add-in folder
writable only by you: whoever can change the DLL or its config controls the
bridge.

### MSIX-packaged MCP clients

Windows redirects AppData writes into a private copy for an MSIX-packaged app,
such as a Store-installed desktop client, and for every process it starts.
Its sidecar and an NX started from Explorer therefore never see the same
`%LOCALAPPDATA%\nx-mcp\bridge.json`. Put `state_dir` outside AppData, for
example under the user profile, and pass the same directory to the sidecar as
`NX_MCP_STATE_DIR`. The NX2206 test machine needed this for the Claude desktop
app.

## Behavior and limits

- Commands act on NX's current work part. Save, export, open and create stay
  inside the workspace, but sketch, extrude and undo change whichever part is
  current. Create or open a workspace part first.
- A running NXOpen call occupies the UI thread until it returns, like an
  interactive command. NX is free between calls.
- Stopping cancels queued calls. It does not abort a running call: a stop asked
  for during a call refuses new work at once and shuts down when that call
  returns.
- Only one bridge may own the descriptor. Start refuses while another bridge
  still answers an authenticated request on the descriptor's port, and ignores
  a descriptor whose port another process has taken over.

## Validation on NX2206 (build 2206.9101, 2026-09-18)

| Check | Result |
| --- | --- |
| Load from the `startup` folder | NX called `Startup()` on the UI thread |
| Hand-off to the UI thread when idle | about 0 ms |
| `UF_UI_lock_ug_access` when idle | returned `UF_UI_LOCK_SET` (5) |
| `python -m nx_mcp.real_smoke --iterations 1` | passed in 29 s |
| `pytest -m real_nx tests/test_real_nx.py`, 20 iterations plus negative cases | passed in 297 s |
| STEP output | 20 of 20 files had one solid, 6 faces, and a 20 × 10 × 12.5 mm extent |
| Protocol checks | 21 malformed and edge-case requests matched the Python server's codes and `execution_state` |
| Stop file | descriptor removed; later calls returned `NX_BRIDGE_UNAVAILABLE` (`not_started`) |
| NX started from Explorer, sidecar inside an MSIX package, shared `state_dir` | bridge found at once; 1-iteration smoke passed in 19 s |

UI responsiveness was measured during the whole acceptance run: 1,491 probes
over 570 s, one every 250 ms.

- Windows never reported the window as hung.
- The median probe latency was 0.26 ms.
- The window was busy only while an individual command ran. The longest
  continuous stretch was 4.5 s, and 28 probes timed out at 2 s during
  commands.

### Re-validation after the review changes (2026-09-20)

The add-in was rebuilt with the in-box `csc.exe` and loaded from the `startup`
folder of a test NX:

| Check | Result |
| --- | --- |
| `pytest -m real_nx tests/test_real_nx.py`, 20 iterations plus negative cases | passed in 435 s |
| STEP output | 20 of 20 files had one solid, and `FILE_NAME` in the header stayed the requested `run-01.stp`; the sizes match the runs before the change |
| Three exports to the same path: a fresh part, over the existing file, and after a save | each one replaced the destination with a one-solid STEP and left no temporary directory behind |
| Descriptor whose port an unrelated service holds | logged as stale and ignored; the bridge started |
| Descriptor served by a live bridge | start refused, naming that bridge's pid and port, and left the descriptor untouched |
| Stop file | descriptor removed |

Not yet exercised:

- the deferred stop. It needs a stop request that arrives while a call runs, and
  the stop-file watcher cannot produce one: it polls between requests, so it only
  ever stops an idle bridge. Ctrl+U, or another in-process caller, is the way in;
- the Ctrl+U start/stop toggle;
- running alongside other in-process NX plugins;
- NX 2506.
