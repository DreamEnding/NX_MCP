"""Live public MCP acceptance. Creates disposable parts; restore your original session afterward."""

import asyncio
import base64
import hashlib
import json
import math
import os
import traceback
import uuid
import zipfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(call, root):
    out = {"groups": []}

    async def new(name):
        return await call("nx_create_part", path=str(root / (name + ".prt")))

    async def box(name, w=10, h=10):
        await new(name)
        s = (await call("nx_create_sketch"))["object"]["id"]
        await call(
            "nx_sketch_rectangle", sketch_id=s, corner1={"x": 0, "y": 0}, corner2={"x": w, "y": 10}
        )
        await call("nx_finish_sketch", sketch_id=s)
        f = await call("nx_extrude", sketch_id=s, distance=h)
        return (s, f)

    async def group(name, fn):
        try:
            out["groups"].append({"name": name, "status": "passed", "result": await fn()})
        except Exception:
            out["groups"].append(
                {"name": name, "status": "failed", "error": traceback.format_exc()}
            )

    async def expressions():
        s, f = await box("expressions")
        await call("nx_set_expression", expression="height", formula="12", create=True, units="mm")
        await call(
            "nx_bind_parameter", feature=f["feature"]["id"], parameter="end", expression="height"
        )
        assert abs((await call("nx_get_bounding_box"))["max"][2] - 12) < 1e-07
        await call("nx_set_expression", expression="height", formula="18")
        assert abs((await call("nx_measure_volume"))["volume_mm3"] - 1800) < 1e-05
        before = await call("nx_list_expressions")
        r = await call("nx_model_health")
        assert r["healthy"], r
        try:
            await call("nx_set_expression", expression="height", formula="missing_symbol + 1")
        except Exception:
            pass
        else:
            raise AssertionError("invalid formula accepted")
        assert abs((await call("nx_get_bounding_box"))["max"][2] - 18) < 1e-07
        inch = await call(
            "nx_set_expression", expression="inch_probe", formula="1", create=True, units="inch"
        )
        assert abs(inch["expression"]["value"] - 1) < 1e-07, inch
        await call("nx_rebuild_model")
        return {"height": 18, "health": r, "expressions": before, "inch": inch}

    await group("expressions_binding_health_rollback", expressions)

    async def geometry():
        s, f = await box("selection")
        r = await call(
            "nx_find_geometry",
            kind="face",
            geometry_type="plane",
            normal=[0, 0, 1],
            order="highest",
        )
        assert r["total"] == 1, r
        assert abs(r["items"][0]["bounds_center"][2] - 10) < 1e-07
        await call("nx_highlight_objects", objects=[r["items"][0]["object"]["id"]])
        await call("nx_clear_highlights")
        await new("circular-selection")
        s = (await call("nx_create_sketch"))["object"]["id"]
        await call("nx_sketch_arc", sketch_id=s, cx=0, cy=0, radius=5, start_angle=0, end_angle=360)
        await call("nx_finish_sketch", sketch_id=s)
        await call("nx_extrude", sketch_id=s, distance=10)
        circles = await call(
            "nx_find_geometry", kind="edge", geometry_type="circle", radius=5, near=[0, 0, 10]
        )
        assert circles["total"] == 2, circles
        curve = (await call("nx_sketch_info", sketch_id=s))["curves"][0]["object"]["id"]
        await call(
            "nx_edit_sketch",
            sketch_id=s,
            operations=[
                {
                    "action": "arc",
                    "curve": curve,
                    "center": [0, 0],
                    "radius": 6,
                    "start_angle": 0,
                    "end_angle": 360,
                }
            ],
        )
        assert abs((await call("nx_get_bounding_box"))["max"][0] - 6) < 1e-07
        return {"planes": r, "circles": circles}

    await group("geometric_selection_and_highlight", geometry)

    async def sketches():
        await new("sketch-edit")
        s = (await call("nx_create_sketch", plane="XZ"))["object"]["id"]
        c = (
            await call("nx_sketch_line", sketch_id=s, start={"x": 0, "y": 0}, end={"x": 10, "y": 0})
        )["object"]["id"]
        await call("nx_finish_sketch", sketch_id=s)
        r = await call(
            "nx_edit_sketch",
            sketch_id=s,
            operations=[
                {"action": "line", "curve": c, "start": [0, 0], "end": [12, 0]},
                {"action": "constraint", "curve": c, "type": "horizontal"},
            ],
        )
        assert r["sketch"]["curves"][0]["end"] == [12, 0, 0], r
        cons = r["diagnostics"]["constraints"]
        assert cons, cons
        s = (await call("nx_list_sketches"))["objects"][0]["id"]
        ci = (await call("nx_sketch_info", sketch_id=s))["curves"][0]["object"]["id"]
        constraint = (await call("nx_sketch_diagnostics", sketch_id=s))["constraints"][0]["object"][
            "id"
        ]
        await call(
            "nx_edit_sketch",
            sketch_id=s,
            operations=[
                {"action": "delete", "object": constraint},
                {"action": "add_line", "start": [0, 0], "end": [0, 8]},
            ],
        )
        assert (await call("nx_sketch_info", sketch_id=s))["curve_count"] == 2
        s = (await call("nx_list_sketches"))["objects"][0]["id"]
        ci = (await call("nx_sketch_info", sketch_id=s))["curves"][-1]["object"]["id"]
        await call("nx_edit_sketch", sketch_id=s, operations=[{"action": "delete", "object": ci}])
        assert (await call("nx_sketch_info", sketch_id=s))["curve_count"] == 1
        return r

    await group("sketch_reopen_edit_constraints_delete", sketches)

    async def assembly():
        await box("proto-a")
        await call("nx_save_part")
        await box("proto-b", w=8)
        await call("nx_save_part")
        await new("assembly")
        a = (await call("nx_add_component", part_path=str(root / "proto-a.prt"), name="seed"))[
            "object"
        ]["id"]
        b = (
            await call(
                "nx_add_component",
                part_path=str(root / "proto-a.prt"),
                name="other",
                translation=[5, 0, 0],
            )
        )["object"]["id"]
        await call("nx_component_action", component=b, action="rename", name="second")
        await call("nx_component_action", component=b, action="suppress")
        assert any(c["suppressed"] for c in (await call("nx_list_components"))["components"])
        await call("nx_component_action", component=b, action="unsuppress")
        await call(
            "nx_component_action",
            component=b,
            action="replace",
            part_path=str(root / "proto-b.prt"),
        )
        rows = (await call("nx_list_components"))["components"]
        assert any(c["part_path"].endswith("proto-b.prt") for c in rows)
        a = next(c["object"]["id"] for c in rows if c["name"].casefold() == "seed")
        r = await call(
            "nx_pattern_components", component=a, direction=[1, 0, 0], spacing=20, count=4
        )
        assert r["total_instances"] == 4
        assert (await call("nx_list_components"))["count"] == 5
        last = (await call("nx_list_components"))["components"][-1]["object"]["id"]
        await call("nx_component_action", component=last, action="remove")
        assert (await call("nx_list_components"))["count"] == 4
        await call("nx_save_part")
        return r

    await group("assembly_maintenance_and_instances", assembly)

    async def views():
        s, f = await box("views")
        body = f["bodies"][0]["id"]
        await call("nx_set_display", objects=[body], color="blue", transparency=30)
        await call("nx_section_view", origin=[0, 0, 5], normal=[0, 0, 1])
        await call("nx_fit_view")
        camera = await call("nx_view_info")
        path = str(root / "presentation.json")
        await call("nx_save_presentation", path=path)
        await call("nx_set_display", objects=[body], color="red")
        await call(
            "nx_set_camera",
            rotation=camera["rotation"],
            origin=[1, 2, 3],
            scale=camera["scale"] * 0.8,
        )
        await call("nx_restore_presentation", path=path)
        after = await call("nx_view_info")
        assert math.dist(after["origin"], camera["origin"]) < 1e-07
        r = await call("nx_display_info", objects=[body])
        assert r["objects"][1]["transparency"] == 30, r
        await call("nx_save_part")
        return {"camera": after, "display": r}

    await group("saved_presentation_and_camera", views)

    async def report():
        await call("nx_open_part", path=str(root / "assembly.prt"))
        before = await call("nx_view_info")
        r = await call(
            "nx_inspection_report",
            path=str(root / "report.zip"),
            minimum_clearance=2,
            max_pairs=100,
            section_planes=[{"origin": [0, 0, 5], "normal": [0, 0, 1]}],
        )
        after = await call("nx_view_info")
        assert math.dist(before["origin"], after["origin"]) < 1e-07
        assert r["capture_count"] >= 2
        return r

    await group("inspection_report_artifacts_restore", report)

    async def preview():
        s, f = await box("preview")
        feature = f["feature"]["id"]
        r = await call(
            "nx_preview_change",
            operations=[
                {
                    "method": "nx_edit_feature",
                    "params": {"name": feature, "params": {"distance": 22}},
                }
            ],
        )
        assert abs((await call("nx_get_bounding_box"))["max"][2] - 10) < 1e-07
        await call("nx_finish_preview", preview_id=r["preview_id"], action="accept")
        assert abs((await call("nx_get_bounding_box"))["max"][2] - 22) < 1e-07
        feature = (await call("nx_list_features"))["objects"][-1]["id"]
        q = await call(
            "nx_preview_change",
            operations=[
                {
                    "method": "nx_edit_feature",
                    "params": {"name": feature, "params": {"distance": 30}},
                }
            ],
            capture=False,
        )
        await call("nx_set_expression", expression="marker", formula="1", create=True)
        try:
            await call("nx_finish_preview", preview_id=q["preview_id"], action="accept")
        except Exception:
            pass
        else:
            raise AssertionError("stale preview accepted")
        return r

    await group("change_preview_accept_and_staleness", preview)

    async def summary():
        r = {
            s: await call("nx_model_summary", section=s, limit=2)
            for s in ["overview", "components", "features", "expressions", "sketches"]
        }
        return r

    await group("compact_summary_pagination", summary)
    out["passed"] = sum(g["status"] == "passed" for g in out["groups"])
    out["total"] = len(out["groups"])
    return out


async def main():
    endpoint = os.environ["NX_MCP_TEST_ENDPOINT"]
    output = Path(os.environ.get("NX_AUTHORING_RESULTS", "authoring-results"))
    output.mkdir(parents=True, exist_ok=True)
    root = Path("authoring-validation-" + uuid.uuid4().hex[:8])
    async with (
        streamablehttp_client(endpoint) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert len(tools) == int(os.environ.get("NX_EXPECTED_TOOL_COUNT", "189"))
        assert tools["nx_model_health"].annotations.readOnlyHint
        assert not tools["nx_preview_change"].annotations.readOnlyHint

        async def call(method, **params):
            result = await client.call_tool(method, params)
            if result.isError:
                raise RuntimeError(result.structuredContent or result.content)
            return result.structuredContent

        result = await run(call, root)
        result["workspace"] = str(root)
        artifacts = []

        def collect(value):
            if isinstance(value, dict):
                if "path" in value and "sha256" in value:
                    artifacts.append(value)
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(result)
        for artifact in artifacts:
            content = bytearray()
            while True:
                chunk = await call(
                    "nx_download_file", path=artifact["artifact_path"], offset=len(content)
                )
                content.extend(base64.b64decode(chunk["data_base64"]))
                if chunk["eof"]:
                    break
            assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
            target = output / Path(artifact["path"].replace("\\", "/")).name
            target.write_bytes(content)
            artifact["local_path"] = str(target.resolve())
            if target.suffix == ".zip":
                with zipfile.ZipFile(target) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    for name, digest in manifest.items():
                        assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        (output / "public-authoring-validation.json").write_text(json.dumps(result, indent=2))
        for group in result["groups"]:
            print(group["name"], group["status"], group.get("error", ""), flush=True)
        if result["passed"] != result["total"]:
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
