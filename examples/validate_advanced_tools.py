"""Live MCP acceptance for NX 2606 advanced authoring on disposable parts."""

import asyncio
import json
import math
import os
import traceback
import uuid
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(call, root):
    out = {"groups": []}

    async def new(name):
        return await call("nx_create_part", path=str(root / (name + ".prt")))

    async def box(name, w=14, h=10):
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

    async def geometry():
        s, f = await box("dev6-selection")
        r = await call("nx_find_geometry", geometry_type="plane", normal=[0, 0, 1], near=[1, 2, 14])
        assert abs(r["items"][0]["distance"] - 4) < 1e-07, r
        selector = r["selector"]
        await call("nx_edit_feature", name=f["feature"]["id"], params={"distance": 12})
        r = await call("nx_resolve_geometry", selector=selector)
        assert abs(r["match"]["distance"] - 2) < 1e-07, r
        q = await call("nx_find_geometry", near=[7, 5, 6])
        try:
            await call("nx_resolve_geometry", selector=q["selector"])
        except Exception:
            pass
        else:
            raise AssertionError("Ambiguous selection accepted")
        await call("nx_save_part")
        await call("nx_close_part")
        await call("nx_open_part", path=str(root / "dev6-selection.prt"))
        r = await call("nx_resolve_geometry", selector=selector)
        assert abs(r["match"]["distance"] - 2) < 1e-07
        return r

    await group("exact_selection_edit_reopen_ambiguity", geometry)

    async def holes():
        await new("dev6-bores")
        s = (await call("nx_create_sketch"))["object"]["id"]
        for rad in [10, 5]:
            await call(
                "nx_sketch_arc", sketch_id=s, cx=0, cy=0, radius=rad, start_angle=0, end_angle=360
            )
        await call("nx_finish_sketch", sketch_id=s)
        await call("nx_extrude", sketch_id=s, distance=10)
        r = await call("nx_recognize_holes")
        assert r["total"] == 1, r
        assert abs(r["items"][0]["radius"] - 5) < 1e-07
        assert r["items"][0]["full_circumference"]
        return r

    await group("bore_axis_recognition", holes)

    async def pattern():
        await box("dev6-pattern-proto", h=4.6)
        await call("nx_save_part")
        await new("dev6-pattern-assembly")
        c = (
            await call(
                "nx_add_component", part_path=str(root / "dev6-pattern-proto.prt"), name="seed"
            )
        )["object"]["id"]
        r = await call(
            "nx_native_component_pattern", component=c, direction=[1, 0, 0], spacing=16.5, count=16
        )
        assert r["total_instances"] == 16 and r["associative"], r
        bounds = await call("nx_get_bounding_box", scope="assembly")
        assert abs(bounds["max"][0] - bounds["min"][0] - 261.5) < 1e-06, bounds
        p = r["object"]["id"]
        edited = await call("nx_edit_component_pattern", pattern=p, count=4, spacing=20)
        assert edited["total_instances"] == 4, edited
        assert sorted(round(i["translation"][0], 6) for i in edited["instances"]) == [
            0,
            20,
            40,
            60,
        ], edited
        await call("nx_save_part")
        await call("nx_close_part")
        await call("nx_open_part", path=str(root / "dev6-pattern-assembly.prt"))
        read = await call("nx_list_component_patterns")
        assert read["count"] == 1 and read["patterns"][0]["associative"]
        return {"created": r, "edited": edited, "bounds": bounds, "reopened": read}

    await group("native_pattern_261_5_span_edit_reopen", pattern)

    async def dimensions():
        await new("dev6-dimensions")
        s = (await call("nx_create_sketch", plane="XZ"))["object"]["id"]
        c = (
            await call("nx_sketch_line", sketch_id=s, start={"x": 0, "y": 0}, end={"x": 10, "y": 0})
        )["object"]["id"]
        await call("nx_finish_sketch", sketch_id=s)
        r = await call(
            "nx_sketch_dimension",
            sketch_id=s,
            curve=c,
            dimension_type="length",
            value=15,
            origin=[5, 3],
        )
        assert abs(r["expression"]["value"] - 15) < 1e-06, r
        await call("nx_set_expression", expression=r["expression"]["object"]["id"], formula="18")
        info = await call("nx_sketch_info", sketch_id=s)
        assert abs(math.dist(info["curves"][0]["start"], info["curves"][0]["end"]) - 18) < 1e-06, (
            info
        )
        await new("dev6-radius")
        s = (await call("nx_create_sketch"))["object"]["id"]
        c = (
            await call(
                "nx_sketch_arc", sketch_id=s, cx=0, cy=0, radius=5, start_angle=0, end_angle=360
            )
        )["object"]["id"]
        await call("nx_finish_sketch", sketch_id=s)
        r = await call(
            "nx_sketch_dimension",
            sketch_id=s,
            curve=c,
            dimension_type="radius",
            value=7,
            origin=[8, 8],
        )
        assert abs(r["expression"]["value"] - 7) < 1e-06, r
        return r

    await group("native_dimensions_and_expression_edit", dimensions)

    async def relations():
        await new("dev6-relations")
        s = (await call("nx_create_sketch"))["object"]["id"]
        curves = []
        for y in [0, 5]:
            curves.append(
                (
                    await call(
                        "nx_sketch_line", sketch_id=s, start={"x": 0, "y": y}, end={"x": 10, "y": y}
                    )
                )["object"]["id"]
            )
        await call("nx_finish_sketch", sketch_id=s)
        r = await call(
            "nx_sketch_relation",
            sketch_id=s,
            curve1=curves[0],
            curve2=curves[1],
            relation="parallel",
        )
        assert r["constraint"]
        d = await call("nx_sketch_conflicts", sketch_id=s)
        assert d["baseline_status"] in ["UnderConstrained", "WellConstrained"]
        return {"relation": r, "diagnostic": d}

    await group("native_relations_and_diagnostics", relations)

    async def parameters():
        s, f = await box("dev6-parameters")
        rows = (await call("nx_feature_parameters", feature=f["feature"]["id"]))["parameters"]
        exp = next(r for r in rows if r["value"] == 10 and r["units"] == "mm")
        r = await call(
            "nx_set_feature_parameters",
            feature=f["feature"]["id"],
            values={exp["object"]["id"]: "22"},
        )
        bounds = await call("nx_get_bounding_box")
        assert abs(bounds["max"][2] - 22) < 1e-06, bounds
        return r

    await group("owned_feature_parameters", parameters)

    async def extras():
        results = []
        for dtype in ["horizontal", "vertical", "diameter"]:
            await call("nx_create_part", path=str(root / ("dev6-" + dtype + ".prt")))
            s = (await call("nx_create_sketch"))["object"]["id"]
            if dtype == "diameter":
                c = (
                    await call(
                        "nx_sketch_arc",
                        sketch_id=s,
                        cx=0,
                        cy=0,
                        radius=5,
                        start_angle=0,
                        end_angle=360,
                    )
                )["object"]["id"]
            else:
                c = (
                    await call(
                        "nx_sketch_line",
                        sketch_id=s,
                        start={"x": 0, "y": 0},
                        end={
                            "x": 10 if dtype == "horizontal" else 0,
                            "y": 10 if dtype == "vertical" else 0,
                        },
                    )
                )["object"]["id"]
            await call("nx_finish_sketch", sketch_id=s)
            r = await call(
                "nx_sketch_dimension",
                sketch_id=s,
                curve=c,
                dimension_type=dtype,
                value=16,
                origin=[15, 15],
            )
            assert abs(r["expression"]["value"] - 16) < 1e-06
            results.append(r)
        return results

    await group("horizontal_vertical_diameter_dimensions", extras)

    async def relations():
        results = []
        for relation in [
            "perpendicular",
            "equal_length",
            "equal_radius",
            "concentric",
            "coincident",
        ]:
            await call("nx_create_part", path=str(root / ("dev6-rel-" + relation + ".prt")))
            s = (await call("nx_create_sketch"))["object"]["id"]
            curves = []
            for i in [0, 1]:
                if relation in ["equal_radius", "concentric"]:
                    c = (
                        await call(
                            "nx_sketch_arc",
                            sketch_id=s,
                            cx=i * 12,
                            cy=0,
                            radius=5 + i,
                            start_angle=0,
                            end_angle=360,
                        )
                    )["object"]["id"]
                else:
                    c = (
                        await call(
                            "nx_sketch_line",
                            sketch_id=s,
                            start={"x": 0, "y": i * 5},
                            end={"x": 10 + i, "y": i * 5},
                        )
                    )["object"]["id"]
                curves.append(c)
            await call("nx_finish_sketch", sketch_id=s)
            kw = {"point1": "end", "point2": "start"} if relation == "coincident" else {}
            r = await call(
                "nx_sketch_relation",
                sketch_id=s,
                curve1=curves[0],
                curve2=curves[1],
                relation=relation,
                **kw,
            )
            assert r["constraint"]
            info = await call("nx_sketch_info", sketch_id=s)
            r["geometry_readback"] = info
            results.append(r)
        return results

    await group("two_curve_relation_types", relations)
    out["passed"] = sum(g["status"] == "passed" for g in out["groups"])
    out["total"] = len(out["groups"])
    return out


async def main():
    endpoint = os.environ["NX_MCP_TEST_ENDPOINT"]
    output = Path(os.environ.get("NX_ADVANCED_RESULTS", "advanced-results"))
    output.mkdir(parents=True, exist_ok=True)
    root = Path("dev6-validation-" + uuid.uuid4().hex[:8])
    async with (
        streamablehttp_client(endpoint) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert len(tools) == int(os.environ.get("NX_EXPECTED_TOOL_COUNT", "189"))
        assert tools["nx_resolve_geometry"].annotations.readOnlyHint

        async def call(method, **params):
            r = await client.call_tool(method, params)
            if r.isError:
                raise RuntimeError(r.structuredContent or r.content)
            return r.structuredContent

        result = await run(call, root)
        result["workspace"] = str(root)
        (output / "public-advanced-validation.json").write_text(json.dumps(result, indent=2))
        for group in result["groups"]:
            print(group["name"], group["status"], group.get("error", ""), flush=True)
        if result["passed"] != result["total"]:
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
