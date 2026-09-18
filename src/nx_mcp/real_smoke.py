"""Real-NX acceptance runner for the certified v0.2 workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from nx_mcp.workspace import Workspace


async def _call(client: Client, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    if result.is_error:
        text = getattr(result.content[0], "text", None) if result.content else None
        message = text if isinstance(text, str) else "unknown error"
        raise RuntimeError(f"{name} failed: {message}")
    if result.structured_content is None:
        raise RuntimeError(f"{name} returned no structured content")
    return result.structured_content


def _iteration_paths(workspace: Path, prefix: str, index: int) -> tuple[Path, Path]:
    if not prefix.strip():
        raise ValueError("run prefix must not be empty")
    boundary = Workspace(workspace)
    paths = (
        boundary.resolve(f"{prefix}/run-{index:02d}.prt"),
        boundary.resolve(f"{prefix}/run-{index:02d}.stp"),
    )
    if any(path.exists() for path in paths):
        raise ValueError("Acceptance output already exists; choose a unique run prefix")
    return paths


def _verify_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Acceptance output was not created or is empty: {path.name}")


def _verify_step_solid(path: Path) -> None:
    if b"MANIFOLD_SOLID_BREP" not in path.read_bytes():
        raise RuntimeError(f"STEP export contains no solid geometry: {path.name}")


async def run_iteration(
    client: Client,
    workspace: Path,
    index: int,
    *,
    prefix: str = "smoke",
) -> dict[str, Any]:
    expected_part, expected_step = _iteration_paths(workspace, prefix, index)
    part_path = f"{prefix}/run-{index:02d}.prt"
    step_path = f"{prefix}/run-{index:02d}.stp"
    status = await _call(client, "nx_status", {})
    if not status["connected"]:
        raise RuntimeError("NX bridge reports disconnected")

    if status.get("active_part") is not None:
        raise RuntimeError("Acceptance requires no existing work part")
    owned_part_id = None
    try:
        created = await _call(client, "nx_create_part", {"path": part_path, "units": "mm"})
        owned_part_id = created["part"]["part_id"]
        before = await _call(client, "nx_list_bodies", {})
        sketch = await _call(client, "nx_create_sketch", {"plane": "XY", "name": "PROFILE"})
        sketch_id = sketch["object"]["id"]
        await _call(
            client,
            "nx_sketch_rectangle",
            {
                "sketch_id": sketch_id,
                "corner1": {"x": 0, "y": 0},
                "corner2": {"x": 20, "y": 10},
            },
        )
        await _call(client, "nx_finish_sketch", {"sketch_id": sketch_id})
        extruded = await _call(
            client,
            "nx_extrude",
            {"sketch_id": sketch_id, "distance": 12.5, "reverse": False},
        )
        after = await _call(client, "nx_list_bodies", {})
        await _call(client, "nx_fit_view", {})
        exported = await _call(client, "nx_export_step", {"path": step_path})
        exported_path = Workspace(workspace).ensure_inside(workspace / exported["path"])
        if exported_path != expected_step:
            raise RuntimeError("STEP export returned an unexpected path")
        _verify_file(exported_path)
        _verify_step_solid(exported_path)
        await _call(client, "nx_undo", {})
        after_undo = await _call(client, "nx_list_bodies", {})
        await _call(client, "nx_save_part", {})
        await _call(client, "nx_close_part", {"save": False})
        owned_part_id = None
        _verify_file(expected_part)
        reopened = await _call(client, "nx_open_part", {"path": part_path})
        owned_part_id = reopened["part"]["part_id"]
        reopened_bodies = await _call(client, "nx_list_bodies", {})
        if len(reopened_bodies["objects"]) != len(before["objects"]):
            raise RuntimeError("Reopened part body count differs from the saved model")
        await _call(client, "nx_close_part", {"save": False})
        owned_part_id = None
    except Exception:
        if owned_part_id is not None:
            try:
                current = (await _call(client, "nx_status", {})).get("active_part")
                if current is not None and current["part_id"] == owned_part_id:
                    await _call(client, "nx_close_part", {"save": False})
                else:
                    print(
                        "Acceptance cleanup skipped: current part ownership changed.",
                        file=sys.stderr,
                    )
            except Exception:
                print(
                    "Acceptance cleanup could not confirm a safe close; inspect the disposable workspace.",
                    file=sys.stderr,
                )
        else:
            print(
                "Acceptance did not confirm ownership of an open part; no cleanup close attempted.",
                file=sys.stderr,
            )
        raise

    if len(after["objects"]) != len(before["objects"]) + 1:
        raise RuntimeError("Extrude did not add exactly one body")
    if len(after_undo["objects"]) != len(before["objects"]):
        raise RuntimeError(
            "Undo did not restore the original body count "
            f"(before={len(before['objects'])}, after_undo={len(after_undo['objects'])})"
        )
    return {
        "iteration": index,
        "nx_version": status["nx_version"],
        "feature_id": extruded["feature"]["id"],
        "body_id": extruded["body"]["id"],
    }


async def run(workspace: Path, iterations: int, prefix: str = "smoke") -> list[dict[str, Any]]:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    workspace = workspace.resolve()
    for index in range(1, iterations + 1):
        _iteration_paths(workspace, prefix, index)
    environment = dict(os.environ)
    environment["NX_MCP_WORKSPACE"] = str(workspace)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "nx_mcp.server"],
        env=environment,
    )
    async with Client(parameters) as client:
        return [
            await run_iteration(client, workspace, index, prefix=prefix)
            for index in range(1, iterations + 1)
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--run-prefix", default="smoke")
    arguments = parser.parse_args()
    if arguments.iterations < 1:
        parser.error("--iterations must be at least 1")
    if not arguments.run_prefix.strip():
        parser.error("--run-prefix must not be empty")
    workspace = arguments.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(run(workspace, arguments.iterations, arguments.run_prefix))
    print(json.dumps({"status": "passed", "runs": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
