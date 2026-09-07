"""Public MCP acceptance for freeform, face editing and assembly documentation.

Runs in disposable workspace parts, preserves previously saved session parts,
and downloads checksummed native artifacts. Requires interactive NX v2606.
"""

import asyncio
import base64
import hashlib
import json
import math
import os
import traceback
import uuid
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    output = Path(os.environ.get("NX_VALIDATION_OUTPUT", "freeform-manufacturing-results"))
    output.mkdir(parents=True, exist_ok=True)
    prefix = "freeform-validation-" + uuid.uuid4().hex[:8]
    receipt = {"fixture": prefix, "checks": [], "artifacts": [], "operations": []}

    def save():
        (output / "freeform-manufacturing-validation.json").write_text(
            json.dumps(receipt, indent=2)
        )

    async with (
        streamablehttp_client(os.environ["NX_MCP_URL"]) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        async def call(tool_name, **params):
            response = await client.call_tool(tool_name, params)
            receipt["operations"].append(
                {
                    "tool": tool_name,
                    "error": response.isError,
                    "operation_id": response.structuredContent.get("operation_id"),
                }
            )
            save()
            assert not response.isError, (tool_name, response.structuredContent)
            return response.structuredContent

        async def reject(tool_name, **params):
            response = await client.call_tool(tool_name, params)
            assert response.isError, (tool_name, response.structuredContent)
            return response.structuredContent

        async def artifact(meta, name):
            data = bytearray()
            while True:
                chunk = await call("nx_download_file", path=meta["path"], offset=len(data))
                data.extend(base64.b64decode(chunk["data_base64"]))
                if chunk["eof"]:
                    break
            assert hashlib.sha256(data).hexdigest() == meta["sha256"]
            (output / name).write_bytes(data)
            receipt["artifacts"].append({"file": name, "sha256": meta["sha256"], "size": len(data)})

        async def new(name):
            return await call("nx_create_part", path=prefix + "/" + name + ".prt", units="mm")

        async def nearest(owner, kind, coords, geometry_type="any"):
            result = await call(
                "nx_find_geometry", owner=owner, kind=kind, near=coords, geometry_type=geometry_type
            )
            assert result["items"]
            return result["items"][0]["object"]["id"]

        async def volume(body):
            return (await call("nx_measure_volume", body=body))["volume_mm3"]

        async def block(height=5, circle=False):
            sk = (await call("nx_create_sketch"))["object"]["id"]
            if circle:
                await call(
                    "nx_sketch_arc",
                    sketch_id=sk,
                    cx=0,
                    cy=0,
                    radius=5,
                    start_angle=0,
                    end_angle=360,
                )
            else:
                await call(
                    "nx_sketch_rectangle",
                    sketch_id=sk,
                    corner1={"x": 0, "y": 0},
                    corner2={"x": 20, "y": 10},
                )
            await call("nx_finish_sketch", sketch_id=sk)
            return (await call("nx_extrude", sketch_id=sk, distance=height))["body"]["id"]

        async def spline(points, degree=1, method="through_points"):
            return (await call("nx_spline", points=points, degree=degree, method=method))["curve"][
                "id"
            ]

        async def mesh(x0=0, x1=20, curved=False):
            primary = []
            for y in [0, 10]:
                pts = (
                    [[x0, y, 0], [x1, y, 0]]
                    if not curved
                    else [[x0, y, 0], [(x0 + x1) / 2, y, 0], [x1, y, 5]]
                )
                primary.append(
                    [
                        await spline(
                            pts,
                            degree=2 if curved else 1,
                            method="poles" if curved else "through_points",
                        )
                    ]
                )
            cross = [
                [await spline([[x0, 0, 0], [x0, 10, 0]])],
                [await spline([[x1, 0, 5 if curved else 0], [x1, 10, 5 if curved else 0]])],
            ]
            return (await call("nx_surface_mesh", primary=primary, cross=cross))["body"]["id"]

        async def checked(name):
            assert (await call("nx_model_health"))["healthy"]
            receipt["checks"].append(name)
            save()

        before = (await call("nx_list_open_parts"))["parts"]
        assert not any(p["modified"] for p in before), "Save existing parts first"
        original = next(p for p in before if p["work"])
        original_display = next(p for p in before if p["display"])
        try:
            assert len((await client.list_tools()).tools) == int(
                os.environ.get("NX_EXPECTED_TOOL_COUNT", "189")
            )
            await new("spline")
            token = "spline-" + uuid.uuid4().hex
            params = {
                "points": [[0, 0, 0], [10, 5, 3], [20, -5, 6], [30, 0, 10]],
                "operation_id": token,
            }
            first = await call("nx_spline", **params)
            repeated = await call("nx_spline", **params)
            assert first["feature"]["id"] == repeated["feature"]["id"]
            edited = await call(
                "nx_spline",
                feature=first["feature"]["id"],
                points=[[0, 0, 0], [10, 10, 3], [20, -10, 6], [30, 0, 15]],
            )
            analysis = await call("nx_curve_analysis", curve=edited["curve"]["id"], samples=5)
            assert math.dist(analysis["samples"][-1]["point"], [30, 0, 15]) < 1e-7
            await checked("associative_3d_spline_edit_and_idempotent_retry")

            await new("surfaces")
            a, b = await mesh(), await mesh(20, 40)
            edges = [await nearest(a, "edge", [20, 5, 0]), await nearest(b, "edge", [20, 5, 0])]
            continuity = await call(
                "nx_surface_continuity", first=edges[0], second=edges[1], samples=5
            )
            assert continuity["checks"] == {"G0": True, "G1": True, "G2": True}
            sewn = await call("nx_sew", target=a, tools=[b])
            body = sewn["body"]["id"]
            faces = (await call("nx_find_geometry", owner=body, kind="face", near=[10, 5, 0]))[
                "items"
            ]
            thick = await call(
                "nx_thicken", faces=[x["object"]["id"] for x in faces], first_offset=2
            )
            assert math.isclose(await volume(thick["body"]["id"]), 800, rel_tol=1e-7)
            await checked("native_mesh_sew_thicken_and_matching_surface_continuity")

            await new("trim")
            body = await mesh()
            boundary = await spline([[10, -1, 0], [10, 11, 0]])
            trimmed = await call(
                "nx_trim_sheet", body=body, boundaries=[boundary], region_point=[5, 5, 0]
            )
            bounds = await call("nx_get_bounding_box", body=trimmed["body"]["id"])
            receipt["trim_bounds"] = bounds
            assert all(
                math.isclose(v, expected, abs_tol=1e-6)
                for v, expected in zip(bounds["dimensions"], [10, 10, 0], strict=True)
            )
            await checked("native_sheet_trim")

            await new("bridge")
            a, b = await mesh(), await mesh(30, 50)
            first_edge, second_edge = (
                await nearest(a, "edge", [20, 5, 0]),
                await nearest(b, "edge", [30, 5, 0]),
            )
            gap = await call(
                "nx_surface_continuity", first=first_edge, second=second_edge, samples=5
            )
            assert not gap["checks"]["G0"] and math.isclose(gap["maximum_gap"], 10, abs_tol=1e-7)
            bridged = await call("nx_bridge_surface", first=first_edge, second=second_edge)
            assert bridged["body_count"] == 1
            await checked("native_bridge_and_deliberate_gap_detection")

            await new("curvature")
            a, b = await mesh(0, 20), await mesh(20, 40, curved=True)
            result = await call(
                "nx_surface_continuity",
                first=await nearest(a, "edge", [20, 5, 0]),
                second=await nearest(b, "edge", [20, 5, 0]),
                samples=5,
                curvature_tolerance=0.001,
            )
            receipt["curvature_continuity"] = result
            assert result["checks"] == {"G0": True, "G1": True, "G2": False}
            await checked("tangent_but_curvature_discontinuous_surface_pair")

            await new("direct")
            body = await block()
            top = await nearest(body, "face", [10, 5, 5])
            await call("nx_edit_faces", faces=[top], action="move", direction=[0, 0, 1], distance=2)
            assert math.isclose(await volume(body), 1400, rel_tol=1e-7)
            await call(
                "nx_edit_faces",
                faces=[await nearest(body, "face", [10, 5, 7])],
                action="offset",
                distance=1,
            )
            assert math.isclose(await volume(body), 1600, rel_tol=1e-7)
            other = await block(height=10)
            await call(
                "nx_edit_faces",
                faces=[await nearest(body, "face", [10, 5, 8])],
                action="replace",
                replacement=await nearest(other, "face", [10, 5, 10]),
            )
            assert math.isclose(await volume(body), 2000, rel_tol=1e-7)
            await checked("move_offset_replace_analytic_volumes")

            await new("heal")
            body = await block()
            await call(
                "nx_hole", diameter=2, depth=5, x=10, y=5, z=5, body=body, direction=[0, 0, -1]
            )
            cylinder = await nearest(body, "face", [11, 5, 2.5], "cylinder")
            await call("nx_edit_faces", faces=[cylinder], action="heal")
            assert math.isclose(await volume(body), 1000, rel_tol=1e-7)
            face = await nearest(body, "face", [10, 5, 5])
            walls = await call("nx_wall_thickness", body=body, faces=[face])
            assert walls["measured_count"] == 9
            assert math.isclose(walls["minimum_sampled_thickness"], 5, abs_tol=1e-7)
            draft = await call("nx_face_analysis", faces=[face], pull_direction=[0, 0, 1])
            assert all(
                math.isclose(x["signed_draft_degrees"], 90, abs_tol=1e-7) for x in draft["samples"]
            )
            datum = await call("nx_pmi_datum", faces=[face], letter="A", position=[25, 10, 5])
            fcf = await call(
                "nx_pmi_fcf",
                faces=[await nearest(body, "face", [10, 5, 5])],
                characteristic="Parallelism",
                tolerance=0.05,
                position=[25, 20, 5],
                datums=[datum["annotation"]["id"]],
            )
            assert fcf["geometry_associated"]
            await call(
                "nx_pmi_fcf",
                faces=[await nearest(body, "face", [10, 5, 5])],
                characteristic="Flatness",
                tolerance=0.1,
                position=[25, 20, 5],
                annotation=fcf["annotation"]["id"],
            )
            await checked("delete_heal_wall_thickness_draft_and_native_pmi")

            for detailed in [False, True]:
                await new("thread-" + str(detailed))
                body = await block()
                await call(
                    "nx_hole", diameter=2, depth=5, x=10, y=5, z=5, body=body, direction=[0, 0, -1]
                )
                baseline = await volume(body)
                result = await call(
                    "nx_thread",
                    face=await nearest(body, "face", [11, 5, 2.5], "cylinder"),
                    start_face=await nearest(body, "face", [5, 5, 5]),
                    pitch=0.4,
                    major_diameter=2.4,
                    minor_diameter=1.9,
                    length=4,
                    detailed=detailed,
                )
                assert result["internal"] and math.isclose(result["pitch"], 0.4, abs_tol=1e-8)
                final = await volume(body)
                assert final < baseline if detailed else math.isclose(final, baseline, rel_tol=1e-7)
                await checked("native_" + ("detailed" if detailed else "symbolic") + "_thread")

            await new("prototype")
            await block()
            await call("nx_save_part")
            await new("assembly")
            for i in range(3):
                await call(
                    "nx_add_component",
                    part_path=prefix + "/prototype.prt",
                    name="Cube" + str(i),
                    translation=[25 * i, 0, 0],
                )
            assembled = sorted(
                (await call("nx_list_components"))["components"], key=lambda c: c["translation"][0]
            )
            explosion = (await call("nx_create_explosion", name="Service"))["object"]["id"]
            await call(
                "nx_edit_explosion",
                explosion=explosion,
                placements=[
                    {"component": c["object"]["id"], "translation": [40 * i, 0, 0]}
                    for i, c in enumerate(assembled)
                ],
            )
            await call("nx_show_explosion", explosion=explosion)
            starts = [
                await nearest(c["object"]["id"], "edge", [20 if i == 0 else 25, 5, 5])
                for i, c in enumerate(assembled[:2])
            ]
            trace = await call(
                "nx_explosion_trace",
                explosion=explosion,
                start_edge=starts[0],
                end_edge=starts[1],
                start_direction=[1, 0, 0],
                end_direction=[-1, 0, 0],
            )
            assert trace["traceline"]["kind"] == "traceline"
            poses_before = (await call("nx_explosion_info", explosion=explosion))["items"]
            animation = await call(
                "nx_export_explosion_animation",
                explosion=explosion,
                path=prefix + "/animation.html",
                frames=3,
            )
            await artifact(animation, "animation.html")
            assert len({f["sha256"] for f in animation["frames"]}) == 3
            assert (await call("nx_explosion_info", explosion=explosion))["items"] == poses_before
            sheet = (await call("nx_create_drawing", name="Service", size="A3"))["object"]["id"]
            bom = await call("nx_create_parts_list", drawing=sheet, position=[25, 250])
            assert bom["rows"] == [["1", "PROTOTYPE", "3"]]
            assert (await call("nx_parts_list_info", parts_list=bom["parts_list"]["id"]))[
                "rows"
            ] == bom["rows"]
            await call(
                "nx_add_component",
                part_path=prefix + "/prototype.prt",
                name="Cube3",
                translation=[120, 0, 0],
            )
            assert (await call("nx_update_parts_list", parts_list=bom["parts_list"]["id"]))[
                "rows"
            ] == [["1", "PROTOTYPE", "4"]]
            view = (
                await call(
                    "nx_add_base_view",
                    drawing=sheet,
                    scope="assembly",
                    explosion=explosion,
                    position=[170, 120],
                )
            )["object"]["id"]
            balloons = await call(
                "nx_parts_list_balloons", parts_list=bom["parts_list"]["id"], view=view
            )
            assert balloons["balloon_count"] >= 1
            pdf = await call("nx_export_drawing_pdf", path=prefix + "/service.pdf")
            await artifact(pdf, "service.pdf")
            await checked("native_bom_quantity_update_balloons_trace_animation_and_pdf")
            receipt["passed"] = True
        except Exception:
            receipt["passed"] = False
            receipt["error"] = traceback.format_exc()
            raise
        finally:
            try:
                await call("nx_open_part", path=original["path"], work=True, display=True)
                fixtures = [
                    p for p in (await call("nx_list_open_parts"))["parts"] if prefix in p["path"]
                ]
                for part in reversed(fixtures):
                    await call("nx_close_part", part=part["part"]["id"], save=True)
                if original_display["path"] != original["path"]:
                    await call(
                        "nx_open_part", path=original_display["path"], work=False, display=True
                    )
                after = (await call("nx_list_open_parts"))["parts"]
                assert {p["path"] for p in after} == {p["path"] for p in before}
                assert not any(p["modified"] for p in after)
                receipt["session_restored"] = True
            finally:
                save()
    print(json.dumps({"passed": receipt["passed"], "checks": receipt["checks"]}))


if __name__ == "__main__":
    asyncio.run(main())
