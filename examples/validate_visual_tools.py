"""Public MCP visualization regressions; uses a unique disposable workspace folder."""

import asyncio
import base64
import hashlib
import json
import os
import traceback
import uuid
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

outdir = Path(os.environ.get("NX_VISUAL_RESULTS", "outputs/nx-mcp-visual-tools"))
outdir.mkdir(parents=True, exist_ok=True)
root = "visual-validation-" + uuid.uuid4().hex[:8]
report = {"tests": [], "workspace": root}


async def run(client):
    async def call(method, **args):
        response = await client.call_tool(method, args)
        if response.isError:
            raise RuntimeError(response.structuredContent or response.content)
        data = response.structuredContent
        if method == "nx_screenshot":
            items = [i for i in response.content if i.type == "image"]
            assert len(items) == 1
            data_bytes = base64.b64decode(items[0].data)
            assert hashlib.sha256(data_bytes).hexdigest() == data["sha256"]
            file = outdir / (Path(data["artifact_path"].replace("\\", "/")).name)
            file.write_bytes(data_bytes)
            data["local_image"] = str(file.resolve())
        return data

    async def test(name, fn):
        try:
            report["tests"].append({"name": name, "status": "passed", "result": await fn()})
        except Exception:
            report["tests"].append(
                {"name": name, "status": "failed", "error": traceback.format_exc()}
            )
        (outdir / "public-visual-validation.json").write_text(json.dumps(report, indent=2))
        print(name, report["tests"][-1]["status"], flush=True)

    async def capture(name):
        return await call("nx_screenshot", path=root + "/" + name + ".png", style="current")

    async def cube(path):
        await call("nx_create_part", path=path)
        sk = (await call("nx_create_sketch"))["object"]["id"]
        await call(
            "nx_sketch_rectangle",
            sketch_id=sk,
            corner1={"x": 0, "y": 0},
            corner2={"x": 10, "y": 10},
        )
        await call("nx_finish_sketch", sketch_id=sk)
        body = (await call("nx_extrude", sketch_id=sk, distance=10))["bodies"][0]["id"]
        return sk, body

    async def schema():
        names = {x.name for x in (await client.list_tools()).tools}
        assert len(names) == int(os.environ.get("NX_EXPECTED_TOOL_COUNT", "189")), len(names)
        return await call("nx_status")

    await test("schemas_and_visible_ui", schema)
    proto = root + "/cube.prt"
    sub = root + "/sub.prt"
    top = root + "/assembly.prt"
    sk, body = await cube(proto)

    async def diagnostics():
        await call("nx_save_part")
        before = await call("nx_list_open_parts")
        checkpoint = await call("nx_checkpoint", label="Before diagnostics")
        result = await call("nx_sketch_diagnostics", sketch_id=sk)
        assert (
            result["solver_status"] == "UnderConstrained"
            and result["remaining_degrees_of_freedom"] > 0
        ), result
        after = await call("nx_list_open_parts")
        assert [p["modified"] for p in before["parts"]] == [p["modified"] for p in after["parts"]]
        assert (await call("nx_checkpoint_state"))["checkpoints"], checkpoint
        return result

    await test("underconstrained_diagnostics_preserve_state", diagnostics)

    async def appearance():
        before = await call("nx_display_info", objects=[body])
        result = await call("nx_set_display", objects=[body], color="red", transparency=55)
        faces = [r for r in result["objects"] if r["object"]["kind"] == "face"]
        assert all(r["transparency"] == 55 for r in faces), result
        await call("nx_set_view", orientation="isometric")
        await call("nx_fit_view")
        image = await capture("appearance")
        await call("nx_restore_display", restore_id=result["restore_id"])
        after = await call("nx_display_info", objects=[body])
        assert before["objects"] == after["objects"], after
        return {"changed": result, "image": image}

    await test("color_transparency_and_face_restore", appearance)

    async def visibility():
        before = await call("nx_display_info", objects=[body])
        color = await call("nx_set_display", objects=[body], color="blue")
        hide = await call("nx_set_visibility", objects=[body], mode="hide")
        assert hide["objects"][0]["blanked"]
        try:
            await call("nx_restore_display", restore_id=color["restore_id"])
        except RuntimeError as ex:
            assert "NX_RESTORE_ORDER" in str(ex), ex
        else:
            raise AssertionError("Out-of-order restore accepted")
        await call("nx_restore_display", restore_id=hide["restore_id"])
        await call("nx_restore_display", restore_id=color["restore_id"])
        assert before["objects"] == (await call("nx_display_info", objects=[body]))["objects"]
        return {"hide": hide, "out_of_order_rejected": True}

    await test("visibility_restore_and_order_preflight", visibility)

    async def sections():
        before = (await call("nx_measure_volume", body=body))["volume_mm3"]
        plane = await call("nx_section_view", origin=[0, 0, 5], normal=[0, 0, 2], name="Z midplane")
        ref = plane["object"]["id"]
        assert plane["normal"] == [0, 0, 1]
        assert plane["retained_side"] == "negative_normal"
        await call("nx_save_part")
        saved = await call("nx_list_open_parts")
        listing = await call("nx_list_sections")
        assert [p["modified"] for p in saved["parts"]] == [
            p["modified"] for p in (await call("nx_list_open_parts"))["parts"]
        ]
        assert listing["count"] == 1 and listing["view_sectioning_enabled"], listing
        image = await capture("section-z")
        moved = await call(
            "nx_section_view",
            section=ref,
            origin=[5, 0, 0],
            normal=[1, 1, 0],
            name="Diagonal section",
        )
        diagonal = await capture("section-diagonal")
        await call("nx_section_control", section=ref, action="disable")
        assert not (await call("nx_list_sections"))["view_sectioning_enabled"]
        await call("nx_section_control", section=ref, action="enable")
        await call("nx_section_control", section=ref, action="delete")
        assert (await call("nx_list_sections"))["count"] == 0
        assert abs((await call("nx_measure_volume", body=body))["volume_mm3"] - before) < 1e-7
        return {
            "plane": plane,
            "listing": listing,
            "image": image,
            "edited": moved,
            "diagonal": diagonal,
        }

    await test("native_section_lifecycle_and_saved_state", sections)
    await call("nx_save_part")
    # Shared prototype under a rotated subassembly and direct instances.
    await call("nx_create_part", path=sub)
    await call("nx_add_component", part_path=proto, name="NESTED_CUBE", translation=[3, 0, 0])
    await call("nx_save_part")
    await call("nx_create_part", path=top)
    await call("nx_add_component", part_path=proto, name="DIRECT_CUBE")
    await call(
        "nx_add_component",
        part_path=sub,
        name="ROTATED_SUB",
        translation=[15, -3, 0],
        rotation_matrix=[[0, -1, 0], [1, 0, 0], [0, 0, 1]],
    )
    await call("nx_add_component", part_path=proto, name="CLEAR_CUBE", translation=[25, 0, 0])
    await call("nx_save_part")
    await call("nx_set_view", orientation="isometric")
    await call("nx_fit_view")

    async def highlights():
        before = await call("nx_display_info", objects=["DIRECT_CUBE", "ROTATED_SUB", "CLEAR_CUBE"])
        result = await call("nx_highlight_collisions", obj1="DIRECT_CUBE", obj2="ROTATED_SUB")
        assert (
            result["highlighted_count"] == 2
            and abs(result["pairs"][0]["interference_volume_mm3"] - 500) < 1e-7
        ), result
        image = await capture("collision-highlight")
        clear = await call("nx_clear_highlights")
        assert clear["cleared_count"] == 2, clear
        assert (
            before["objects"]
            == (
                await call("nx_display_info", objects=["DIRECT_CUBE", "ROTATED_SUB", "CLEAR_CUBE"])
            )["objects"]
        )
        empty = await call("nx_highlight_collisions", obj1="DIRECT_CUBE", obj2="CLEAR_CUBE")
        assert empty["highlighted_count"] == 0
        return {"collision": result, "image": image, "clear_pair": empty}

    await test("nested_collision_highlighting", highlights)

    async def isolate():
        state = await call("nx_display_info", objects=["DIRECT_CUBE", "ROTATED_SUB", "CLEAR_CUBE"])
        hidden = await call("nx_set_visibility", objects=["CLEAR_CUBE"], mode="hide")
        isolated = await call("nx_set_visibility", objects=["ROTATED_SUB"], mode="isolate")
        image = await capture("isolated-nested-component")
        await call("nx_restore_display", restore_id=isolated["restore_id"])
        # Existing hidden geometry stays hidden after ending isolation.
        listed = await call("nx_set_visibility", objects=["CLEAR_CUBE"], mode="show")
        await call("nx_restore_display", restore_id=listed["restore_id"])
        await call("nx_restore_display", restore_id=hidden["restore_id"])
        assert (
            state["objects"]
            == (
                await call("nx_display_info", objects=["DIRECT_CUBE", "ROTATED_SUB", "CLEAR_CUBE"])
            )["objects"]
        )
        return {"isolated": isolated, "image": image}

    await test("nested_isolation_and_previous_visibility", isolate)

    async def occurrence_color():
        before = await call("nx_display_info", objects=["DIRECT_CUBE", "CLEAR_CUBE"])
        changed = await call(
            "nx_set_display", objects=["ROTATED_SUB"], color="green", transparency=30
        )
        assert (
            before["objects"]
            == (await call("nx_display_info", objects=["DIRECT_CUBE", "CLEAR_CUBE"]))["objects"]
        )
        image = await capture("occurrence-color")
        parts = await call("nx_list_open_parts")
        prototypes = [p for p in parts["parts"] if p["path"].endswith("cube.prt")]
        assert all(not p["modified"] for p in prototypes), prototypes
        await call("nx_restore_display", restore_id=changed["restore_id"])
        return {"change": changed, "image": image, "prototype_modified": False}

    await test("occurrence_appearance_does_not_recolor_prototype", occurrence_color)

    async def assembly_section():
        before = await call("nx_get_bounding_box", scope="assembly")
        plane = await call(
            "nx_section_view", origin=[0, 0, 5], normal=[0, 0, 1], name="Assembly slice"
        )
        image = await capture("assembly-section")
        after = await call("nx_get_bounding_box", scope="assembly")
        assert before["min"] == after["min"] and before["max"] == after["max"]
        await call("nx_section_control", section=plane["object"]["id"], action="delete")
        return {"plane": plane, "image": image}

    await test("assembly_section_preserves_geometry", assembly_section)

    async def fixed_fixture():
        path = os.environ.get("NX_FIXED_SKETCH_FIXTURE")
        assert path, "NX_FIXED_SKETCH_FIXTURE is required"
        await call("nx_open_part", path=path)
        sketches = await call("nx_list_sketches")
        ref = sketches["objects"][0]["id"]
        result = await call("nx_sketch_diagnostics", sketch_id=ref)
        assert (
            result["solver_status"] == "WellConstrained"
            and result["remaining_degrees_of_freedom"] == 0
        ), result
        assert result["constraint_count"] > 0 and any(
            x["constraints"] for x in result["geometry"]
        ), result
        return result

    await test("fully_constrained_fixture_diagnostics", fixed_fixture)

    async def handoff():
        await call("nx_open_part", path=top)
        await call("nx_highlight_collisions", obj1="DIRECT_CUBE", obj2="ROTATED_SUB")
        manual = await call("nx_ui_control", mode="manual")
        assert not manual["actual_ui_lock"] and manual["nx_window_input_enabled"], manual
        agent = await call("nx_ui_control", mode="agent")
        assert agent["actual_ui_lock"] and not agent["nx_window_input_enabled"], agent
        cleared = await call("nx_clear_highlights")
        assert cleared["cleared_count"] == 0, cleared
        return {"manual": manual, "agent": agent, "highlight_cleanup": cleared}

    await test("manual_handoff_clears_highlights", handoff)
    report["passed"] = sum(t["status"] == "passed" for t in report["tests"])
    report["total"] = len(report["tests"])
    (outdir / "public-visual-validation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    if report["passed"] != report["total"]:
        raise RuntimeError("See public-visual-validation.json")


async def main():
    async with (
        streamablehttp_client(os.environ["NX_MCP_TEST_ENDPOINT"]) as (r, w, _),
        ClientSession(r, w) as client,
    ):
        await client.initialize()
        await run(client)


if __name__ == "__main__":
    asyncio.run(main())
