"""Combine an NX runtime probe and live bridge status into shareable evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nx_mcp.bridge import DescriptorBridgeClient
from nx_mcp.certified import BridgeCaller
from nx_mcp.contracts import StatusResult
from nx_mcp.provenance import git_state


class RuntimeProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    captured_at_utc: datetime
    nx_version: str = Field(min_length=1)
    nx_build: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    python_architecture: str = Field(min_length=1)
    os: str = Field(min_length=1)
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    git_dirty: bool
    # None means the probe ran interactively, without run_journal.
    run_journal_switches: list[str] | None
    nxopen_imported: Literal[True]
    pydantic_available: bool
    mcp_available: bool
    bridge_import_error: str | None


async def collect(
    probe_path: Path,
    output_path: Path,
    expected_bridge: str,
    *,
    bridge: BridgeCaller | None = None,
) -> None:
    probe = RuntimeProbe.model_validate_json(probe_path.read_text(encoding="utf-8"))
    if probe.bridge_import_error is not None:
        raise ValueError(f"NX bridge import failed: {probe.bridge_import_error}")
    git_commit, git_dirty = git_state(Path.cwd())
    if (probe.git_commit, probe.git_dirty) != (git_commit, git_dirty):
        raise ValueError("Checkout changed after the runtime probe; rerun the probe and acceptance")
    caller = bridge if bridge is not None else DescriptorBridgeClient()
    status = StatusResult(**await caller.call("nx_status", {}))
    if expected_bridge not in {"python_batch", "csharp_gui"} or (
        not status.connected
        or status.bridge_protocol != 1
        or status.bridge_implementation != expected_bridge
        or status.nx_version != probe.nx_version
    ):
        raise ValueError("Live bridge does not match the requested bridge and NX runtime probe")
    if expected_bridge == "python_batch" and probe.run_journal_switches is None:
        raise ValueError("Record the switches of the successful run_journal probe invocation")
    evidence = probe.model_dump(mode="json") | {
        "bridge_implementation": status.bridge_implementation,
        "bridge_protocol": status.bridge_protocol,
        "bridge_observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "sidecar_python_version": sys.version,
        "mcp_version": version("mcp"),
        "run_prefix": os.environ.get("NX_MCP_REAL_NX_RUN_PREFIX"),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(evidence, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bridge", choices=("python_batch", "csharp_gui"), required=True)
    arguments = parser.parse_args()
    asyncio.run(collect(arguments.probe, arguments.output, arguments.bridge))


if __name__ == "__main__":
    main()
