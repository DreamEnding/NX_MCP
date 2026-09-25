"""Run this file as a Python journal inside Siemens NX to start the bridge."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

source_root = Path(__file__).resolve().parents[1] / "src"
if source_root.is_dir():
    sys.path.insert(0, str(source_root))

from nx_mcp.nx_bridge import pump_bridge, start_bridge, stop_bridge  # noqa: E402


def main() -> None:
    # NX captures a journal's stdout, so the bridge's own startup diagnostics
    # (a stale descriptor it ignored, for one) land in the session log.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    workspace = os.environ.get("NX_MCP_WORKSPACE")
    if not workspace:
        raise RuntimeError("Set NX_MCP_WORKSPACE before starting the NX MCP bridge")
    stop_file = os.environ.get("NX_MCP_BRIDGE_STOP_FILE")
    if not stop_file:
        raise RuntimeError(
            "Set NX_MCP_BRIDGE_STOP_FILE to run the Python feasibility bridge. "
            "The supplied journal runner is batch-only because it must pump NX calls on the journal thread."
        )

    descriptor = start_bridge(workspace)
    print(f"NX MCP bridge started on {descriptor.host}:{descriptor.port} ({descriptor.nx_version})")
    destination = Path(stop_file)
    print(f"NX MCP bridge waiting for stop file: {destination}")
    try:
        while not destination.exists():
            pump_bridge(timeout=0.1)
    finally:
        stop_bridge()


if __name__ == "__main__":
    main()
