"""Record NX journal runtime facts without changing the active model."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import NXOpen


def main() -> None:
    output = os.environ.get("NX_MCP_PROBE_OUTPUT")
    if not output:
        raise RuntimeError("Set NX_MCP_PROBE_OUTPUT to a disposable JSON path")

    source_root = Path(__file__).resolve().parents[1] / "src"
    if source_root.is_dir():
        sys.path.insert(0, str(source_root))

    bridge_import_error = None
    try:
        import nx_mcp.nx_bridge  # noqa: F401
    except Exception as error:  # Report the runtime issue instead of mutating NX.
        bridge_import_error = f"{type(error).__name__}: {error}"

    experimental_import_error = None
    try:
        from nx_mcp.experimental import execute_legacy  # noqa: F401
    except Exception as error:
        experimental_import_error = f"{type(error).__name__}: {error}"

    session = NXOpen.Session.GetSession()
    result = {
        "python_version": sys.version,
        "nx_version": session.GetEnvironmentVariableValue("UGII_VERSION"),
        "nxopen_imported": True,
        "pydantic_available": importlib.util.find_spec("pydantic") is not None,
        "mcp_available": importlib.util.find_spec("mcp") is not None,
        "bridge_import_error": bridge_import_error,
        "experimental_import_error": experimental_import_error,
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
