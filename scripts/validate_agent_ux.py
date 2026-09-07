"""Run serial agent-UX fixtures against NX; retain artifacts and preserve loaded parts."""

import argparse
import asyncio
import base64
import hashlib
import json
import math
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class Client:
    def __init__(self, session, output):
        self.c = session
        self.out = output
        self.out.mkdir(parents=True, exist_ok=False)
        self.schemas = {}
        self.tools = {}
        self.agent = False

    async def call(self, tool_name, **params):
        if "operation_id" in self.schemas[tool_name].get("properties", {}):
            params.setdefault("operation_id", "ux_" + uuid.uuid4().hex)
        with (self.out / "operations.jsonl").open("a") as f:
            f.write(json.dumps({"state": "submitted", "tool": tool_name, "params": params}) + "\n")
        result = (
            await self.c.call_tool(
                "nx_invoke", {"tool": tool_name, "arguments": params, "detail": "full"}
            )
            if self.agent
            else await self.c.call_tool(tool_name, params)
        )
        with (self.out / "operations.jsonl").open("a") as f:
            f.write(
                json.dumps(
                    {
                        "state": "response",
                        "tool": tool_name,
                        "result": result.structuredContent,
                        "error": result.isError,
                    }
                )
                + "\n"
            )
        if result.isError:
            raise RuntimeError((tool_name, result.structuredContent))
        return result.structuredContent

    async def artifact(self, meta, name):
        data = bytearray()
        while True:
            result = await self.call("nx_download_file", path=meta["path"], offset=len(data))
            data.extend(base64.b64decode(result["data_base64"]))
            if result["eof"]:
                break
        assert hashlib.sha256(data).hexdigest() == meta["sha256"]
        (self.out / name).write_bytes(data)


async def run(url, output):
    async with streamablehttp_client(url) as (r, w, _), ClientSession(r, w) as session:
        await session.initialize()
        client = Client(session, output)
        catalog = (await session.list_tools()).tools
        client.agent = any(t.name == "nx_invoke" for t in catalog)
        if client.agent:
            catalog = []
            offset = 0
            while True:
                result = await session.call_tool(
                    "nx_discover_tools", {"include_schema": True, "offset": offset, "limit": 20}
                )
                if result.isError:
                    raise RuntimeError(result.structuredContent)
                catalog.extend(SimpleNamespace(**t) for t in result.structuredContent["tools"])
                offset = result.structuredContent["next_offset"]
                if offset is None:
                    break
        client.tools = {t.name: t for t in catalog}
        client.schemas = {t.name: t.inputSchema for t in catalog}
        await main(client)


async def main(c):
    tools = c.tools
    original_call = c.call
    records = []
    phase = "inventory"

    async def call(n, **p):
        start = time.monotonic()
        result = await original_call(n, **p)
        Draft202012Validator(tools[n].outputSchema).validate(result)
        records.append(
            {
                "phase": phase,
                "tool": n,
                "seconds": time.monotonic() - start,
                "json_characters": len(json.dumps(result)),
            }
        )
        (c.out / "metrics.json").write_text(json.dumps(records, indent=2))
        return result

    c.call = call
    before = (await call("nx_list_open_parts"))["parts"]
    assert not any(p["modified"] for p in before), "Native UX fixtures require saved original parts"
    original = next(p for p in before if p["work"])
    assert original["display"], "Activate the original assembly as both work and display part"
    full = await call("nx_list_components")
    small = await call("nx_list_components", compact=True, include_transforms=False)
    assert [x["object"]["id"] for x in full["components"]] == [
        x["object"]["id"] for x in small["components"]
    ]
    assert all("translation" not in x for x in small["components"])
    compact = await call("nx_list_open_parts", compact=True, active_only=True)
    assert compact["count"] == 1 and compact["parts"][0]["part"]["id"] == original["part"]["id"]
    page = await call("nx_list_components", compact=True, offset=1, limit=2)
    assert page["count"] == len(full["components"][1:3]) and page["total_count"] == len(
        full["components"]
    )
    prefix = "validation/dev16-ux-" + uuid.uuid4().hex[:8]
    report = {"prefix": prefix, "checks": []}

    async def close_fixtures():
        parts = (await call("nx_list_open_parts"))["parts"]
        # Close assemblies before their prototypes; NX may unload unused dependencies.
        for p in reversed(parts):
            if prefix in p["path"].replace("\\", "/"):
                live = (await call("nx_list_open_parts"))["parts"]
                match = next((x for x in live if x["path"] == p["path"]), None)
                if match:
                    await call("nx_close_part", part=match["part"]["id"], save=True)

    async def rectangle(x, y):
        sk = (await call("nx_create_sketch"))["object"]["id"]
        await call(
            "nx_sketch_rectangle", sketch_id=sk, corner1={"x": 0, "y": 0}, corner2={"x": x, "y": y}
        )
        await call("nx_sketch_diagnostics", sketch_id=sk)
        await call("nx_finish_sketch", sketch_id=sk)
        return sk

    try:
        phase = "exploded"
        await call("nx_create_part", path=prefix + "/block.prt", units="mm")
        sk = await rectangle(20, 10)
        body = (await call("nx_extrude", sketch_id=sk, distance=4))["bodies"][0]["id"]
        datums = await call("nx_list_datums")
        assert datums["count"] > 0
        hidden = await call("nx_set_datum_visibility", visible=False)
        assert all(x["blanked"] for x in hidden["objects"])
        await call("nx_restore_display", restore_id=hidden["restore_id"])
        rs = await call("nx_create_reference_set", name="SOLIDS", objects=[body])
        assert rs["member_count"] == 1
        sets = await call("nx_list_reference_sets")
        assert any(x["name"] == "SOLIDS" for x in sets["reference_sets"])
        await call("nx_save_part")
        await call("nx_create_part", path=prefix + "/assembly.prt", units="mm")
        await call("nx_set_datum_visibility", visible=False)
        comps = []
        for i in range(3):
            comps.append(
                (
                    await call(
                        "nx_add_component",
                        part_path=prefix + "/block.prt",
                        name="block" + str(i),
                        translation=[30 * i, 0, 0],
                    )
                )["object"]["id"]
            )
        before_poses = (await call("nx_list_components"))["components"]
        changed = await call("nx_set_component_reference_set", components=comps, name="SOLIDS")
        assert changed["count"] == 3
        after_poses = (await call("nx_list_components"))["components"]
        assert [x["translation"] for x in before_poses] == [x["translation"] for x in after_poses]
        assert all(x["reference_set"] == "SOLIDS" for x in after_poses)
        explosion = (await call("nx_create_explosion", name="Service"))["object"]["id"]
        await call(
            "nx_edit_explosion",
            explosion=explosion,
            placements=[
                {"component": r, "translation": [30 * i, 0, 20 * i]} for i, r in enumerate(comps)
            ],
        )
        sheet = (await call("nx_create_drawing", name="Service review", size="A3"))["object"]["id"]
        view = (
            await call(
                "nx_add_base_view",
                drawing=sheet,
                scope="assembly",
                explosion=explosion,
                view="isometric",
                position=[125, 150],
            )
        )["object"]["id"]
        bom = (
            await call("nx_create_parts_list", drawing=sheet, position=[275, 250], scope="leaves")
        )["parts_list"]["id"]
        await call("nx_parts_list_balloons", parts_list=bom, view=view)
        await call("nx_drawing_view_info", view=view)
        await call("nx_list_drawings")
        await call("nx_save_part")
        pdf = await call("nx_export_drawing_pdf", path=prefix + "/review.pdf")
        await c.artifact(pdf, "review.pdf")
        report["checks"].append(
            "body-only reference sets assigned without pose changes; exploded drawing exported"
        )
        phase = "sheet_metal"
        await call("nx_create_part", path=prefix + "/sheet.prt", units="mm")
        await call("nx_sheet_metal_context")
        await call("nx_set_sheet_metal_defaults", thickness=2, bend_radius=3, neutral_factor=0.33)
        sk = await rectangle(100, 80)
        body = (
            await call(
                "nx_sheet_metal_feature",
                operation="tab",
                parameters={"section": sk, "thickness": 2},
            )
        )["body"]["id"]
        entries = []
        for point, width in [
            ([50, 0, 0], 80),
            ([50, 80, 0], 80),
            ([0, 40, 0], 60),
            ([100, 40, 0], 60),
        ]:
            edge = (
                await call(
                    "nx_find_geometry",
                    owner=body,
                    kind="edge",
                    geometry_type="line",
                    near=point,
                    limit=1,
                )
            )["items"][0]["object"]["id"]
            entries.append(
                {
                    "edges": [edge],
                    "length": 20,
                    "length_reference": "Inside",
                    "angle": 90,
                    "width_option": "AtCenter",
                    "width": width,
                    "bend_options": {
                        "bend_relief_type": "Square",
                        "use_global_relief_width": False,
                        "bend_relief_width": 1,
                        "use_global_relief_depth": False,
                        "bend_relief_depth": 3,
                    },
                }
            )
        body = (
            await call(
                "nx_sheet_metal_feature", operation="flange", parameters={"flanges": entries}
            )
        )["body"]["id"]
        info = (await call("nx_sheet_metal_info", body=body))["items"][0]
        assert info["bend_count"] == 4 and math.isclose(info["thickness"], 2)
        formed = (await call("nx_measure_volume"))["volume_mm3"]
        stationary = (
            await call(
                "nx_find_geometry",
                owner=body,
                kind="face",
                geometry_type="plane",
                near=[50, 40, 0],
                limit=1,
            )
        )["items"][0]["object"]["id"]
        body = (
            await call(
                "nx_sheet_metal_feature",
                operation="unbend",
                parameters={
                    "face_collector": [info["bends"][0]["face"]["id"]],
                    "reference_entity": stationary,
                },
            )
        )["body"]["id"]
        info = (await call("nx_sheet_metal_info", body=body))["items"][0]
        stationary = (
            await call(
                "nx_find_geometry",
                owner=body,
                kind="face",
                geometry_type="plane",
                near=[50, 40, 0],
                limit=1,
            )
        )["items"][0]["object"]["id"]
        body = (
            await call(
                "nx_sheet_metal_feature",
                operation="rebend",
                parameters={
                    "face_collector": [x["face"]["id"] for x in info["bends"]],
                    "reference_entity": stationary,
                },
            )
        )["body"]["id"]
        assert math.isclose((await call("nx_measure_volume"))["volume_mm3"], formed, rel_tol=1e-7)
        face = (
            await call(
                "nx_find_geometry",
                owner=body,
                kind="face",
                geometry_type="plane",
                near=[50, 40, 0],
                limit=1,
            )
        )["items"][0]["object"]["id"]
        topology = await call(
            "nx_list_topology", body=body, face=face, include_adjacency=True, compact=True
        )
        candidates = await call("nx_flat_pattern_orientation_edges", upward_face=face)
        assert candidates["count"] > 0
        # Prefer the longest straight web edge: deterministic geometric selection.
        selected = max(candidates["edges"], key=lambda x: math.dist(x["start"], x["end"]))
        assert selected["edge"]["id"] in topology["face_edges"][0]["edges"]
        flat = await call(
            "nx_sheet_metal_feature",
            operation="flat_pattern",
            parameters={
                "upward_face": face,
                "x_axis_edge": selected["edge"]["id"],
                "associative": True,
            },
        )
        dxf = await call(
            "nx_export_flat_pattern", flat_pattern=flat["feature"]["id"], path=prefix + "/sheet.dxf"
        )
        await c.artifact(dxf, "sheet.dxf")
        await call("nx_sheet_metal_info", body=body)
        await call("nx_save_part")
        report["checks"].append(
            "flat pattern committed first attempt using discovered face-boundary edge"
        )
        phase = "imported"
        await call("nx_create_part", path=prefix + "/seed.prt", units="mm")
        sk = await rectangle(20, 15)
        await call("nx_extrude", sketch_id=sk, distance=10)
        step = await call("nx_export_step", path=prefix + "/seed.step")
        await call("nx_create_part", path=prefix + "/import.prt", units="mm")
        imported = await call("nx_import_geometry", path=step["path"])
        (c.out / "import-result.json").write_text(json.dumps(imported, indent=2))
        body = (await call("nx_list_bodies"))["objects"][0]["id"]
        face = (
            await call(
                "nx_find_geometry",
                owner=body,
                kind="face",
                geometry_type="plane",
                normal=[0, 0, 1],
                order="highest",
                limit=1,
            )
        )["items"][0]["object"]["id"]
        # Use the already established native imported-face edit contract.
        await call("nx_edit_faces", faces=[face], action="move", distance=2, direction=[0, 0, 1])
        assert math.isclose((await call("nx_measure_volume"))["volume_mm3"], 3600, rel_tol=1e-7)
        await call("nx_save_part")
        await call("nx_close_part", save=False)
        await call("nx_open_part", path=prefix + "/import.prt")
        assert math.isclose((await call("nx_measure_volume"))["volume_mm3"], 3600, rel_tol=1e-7)
        report["checks"].append("imported-face edit 3000 to 3600 mm3 persisted through reopen")
        report["passed"] = True
    finally:
        phase = "cleanup"
        await close_fixtures()
        await call("nx_activate_part", part=original["part"]["id"])
        after = (await call("nx_list_open_parts"))["parts"]
        assert sorted(p["path"] for p in before) == sorted(p["path"] for p in after) and not any(
            p["modified"] for p in after
        )
        comps = (await call("nx_list_components"))["components"]
        fields = (
            "name",
            "part_path",
            "translation",
            "rotation_matrix",
            "suppressed",
            "reference_set",
        )
        assert [{k: x[k] for k in fields} for x in full["components"]] == [
            {k: x[k] for k in fields} for x in comps
        ]
        report["session_preserved"] = {"parts": len(after), "occurrences": len(comps)}
        (c.out / "result.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output))
