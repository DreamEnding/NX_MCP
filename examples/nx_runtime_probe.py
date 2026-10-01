"""Record NX journal runtime facts without changing the active model."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import NXOpen


def main() -> None:
    output = os.environ.get("NX_MCP_PROBE_OUTPUT")
    if not output:
        raise RuntimeError("Set NX_MCP_PROBE_OUTPUT to a disposable JSON path")

    source_root = Path(__file__).resolve().parents[1] / "src"
    if source_root.is_dir():
        sys.path.insert(0, str(source_root))

    from nx_mcp.provenance import file_version, git_state

    bridge_import_error = None
    try:
        import nx_mcp.nx_bridge  # noqa: F401
    except Exception as error:  # Report the runtime issue instead of mutating NX.
        bridge_import_error = f"{type(error).__name__}: {error}"

    session = NXOpen.Session.GetSession()
    nx_root = session.GetEnvironmentVariableValue("UGII_BASE_DIR")
    switches = json.loads(os.environ.get("NX_MCP_RUN_JOURNAL_SWITCHES", "null"))
    if switches is not None and (
        not isinstance(switches, list) or any(not isinstance(value, str) for value in switches)
    ):
        raise ValueError("NX_MCP_RUN_JOURNAL_SWITCHES must be a JSON string array or null")
    git_commit, git_dirty = git_state(source_root.parent)
    result = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version,
        "python_architecture": platform.architecture()[0],
        "nx_version": session.GetEnvironmentVariableValue("UGII_VERSION"),
        "nx_build": file_version(Path(nx_root) / "NXBIN" / "ugraf.exe"),
        "os": platform.platform(),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "run_journal_switches": switches,
        "nxopen_imported": True,
        "pydantic_available": importlib.util.find_spec("pydantic") is not None,
        "mcp_available": importlib.util.find_spec("mcp") is not None,
        "bridge_import_error": bridge_import_error,
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
