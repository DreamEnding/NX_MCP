"""Real-NX acceptance runner for the certified v0.2 workflow."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _call(client: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    if result.isError:
        text = getattr(result.content[0], "text", None) if result.content else None
        message = text if isinstance(text, str) else "unknown error"
        raise RuntimeError(f"{name} failed: {message}")
    if result.structuredContent is None:
        raise RuntimeError(f"{name} returned no structured content")
    return result.structuredContent


async def run_iteration(
    client: ClientSession,
    workspace: Path,
    index: int,
    *,
    prefix: str = "smoke",
) -> dict[str, Any]:
    part_path = f"{prefix}/run-{index:02d}.prt"
    step_path = f"{prefix}/run-{index:02d}.stp"
    status = await _call(client, "nx_status", {})
    if not status["connected"]:
        raise RuntimeError("NX bridge reports disconnected")

    try:
        await _call(client, "nx_create_part", {"path": part_path, "units": "mm"})
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
        # STEP export saves the part and invalidates native undo marks. Exercise
        # undo before that save boundary, then recreate the solid for export.
        await _call(client, "nx_undo", {})
        after_undo = await _call(client, "nx_list_bodies", {})
        sketches = await _call(client, "nx_list_sketches", {})
        sketch_id = next(
            item["id"] for item in sketches["objects"] if item["name"] == sketch["object"]["name"]
        )
        extruded = await _call(
            client,
            "nx_extrude",
            {"sketch_id": sketch_id, "distance": 12.5, "reverse": False},
        )
        exported = await _call(client, "nx_export_step", {"path": step_path})
        await _call(client, "nx_save_part", {})
        await _call(client, "nx_close_part", {"save": False})
    except Exception:
        with contextlib.suppress(Exception):
            await _call(client, "nx_close_part", {"save": False})
        raise

    if len(after["objects"]) != len(before["objects"]) + 1:
        raise RuntimeError("Extrude did not add exactly one body")
    if len(after_undo["objects"]) != len(before["objects"]):
        raise RuntimeError(
            "Undo did not restore the original body count "
            f"(before={len(before['objects'])}, after_undo={len(after_undo['objects'])})"
        )
    if not (workspace / exported["path"]).is_file() and not Path(exported["path"]).is_file():
        raise RuntimeError("STEP export file was not created")
    return {
        "iteration": index,
        "nx_version": status["nx_version"],
        "feature_id": extruded["feature"]["id"],
        "body_id": extruded["body"]["id"],
    }


async def run(workspace: Path, iterations: int, prefix: str = "smoke") -> list[dict[str, Any]]:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    environment = dict(os.environ)
    environment["NX_MCP_WORKSPACE"] = str(workspace)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "nx_mcp.server"],
        env=environment,
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as client,
    ):
        await client.initialize()
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
