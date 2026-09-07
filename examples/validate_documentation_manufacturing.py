"""Public MCP acceptance for realistic geometry and editable manufacturing documentation.

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
    output = Path(os.environ.get("NX_VALIDATION_OUTPUT", "documentation-manufacturing-results"))
    output.mkdir(parents=True, exist_ok=True)
    prefix = "documentation-validation-" + uuid.uuid4().hex[:8]
    receipt = {"fixture": prefix, "checks": [], "artifacts": [], "operations": []}

    def save():
        (output / "documentation-manufacturing-validation.json").write_text(
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
            await new("enclosure")
            sk = (await call("nx_create_sketch"))["object"]["id"]
            await call(
                "nx_sketch_primitive",
                sketch_id=sk,
                primitive="rounded_rectangle",
                center=[0, 0],
                width=120,
                height=80,
                radius=8,
            )
            await call("nx_finish_sketch", sketch_id=sk)
            ext = await call("nx_extrude", sketch_id=sk, distance=30)
            body = ext["body"]["id"]
            outer_area = 120 * 80 - (4 - math.pi) * 8**2
            assert math.isclose(await volume(body), outer_area * 30, rel_tol=1e-7)
            await call(
                "nx_shell",
                body=body,
                thickness=2,
                remove_faces=[await nearest(body, "face", [0, 0, 30])],
            )
            inner_area = 116 * 76 - (4 - math.pi) * 6**2
            expected = outer_area * 30 - inner_area * 28
            assert math.isclose(await volume(body), expected, rel_tol=1e-7)
            walls = await call(
                "nx_wall_thickness", body=body, faces=[await nearest(body, "face", [60, 0, 15])]
            )
            assert math.isclose(walls["minimum_sampled_thickness"], 2, abs_tol=1e-6)
            receipt["enclosure_volume"] = expected
            receipt["enclosure_walls"] = walls
            original_bounds = await call("nx_get_bounding_box", body=body, precision="exact")
            await call("nx_set_view", orientation="isometric")
            await call("nx_fit_view")
            await artifact(
                await call(
                    "nx_render_view", path=prefix + "/enclosure.png", style="shaded_with_edges"
                ),
                "enclosure.png",
            )
            await call("nx_export_step", path=prefix + "/enclosure.step")
            await call("nx_save_part")
            await new("imported-housing")
            await call("nx_import_geometry", path=prefix + "/enclosure.step")
            bodies = (await call("nx_list_bodies"))["objects"]
            assert len(bodies) == 1
            body = bodies[0]["id"]
            assert math.isclose(await volume(body), expected, rel_tol=1e-7)
            imported_bounds = await call("nx_get_bounding_box", body=body, precision="exact")
            for key in ["min", "max"]:
                assert all(
                    math.isclose(a, b, abs_tol=1e-6)
                    for a, b in zip(original_bounds[key], imported_bounds[key], strict=True)
                )
            checkpoint = await call("nx_checkpoint", label="imported housing before local edit")
            await call(
                "nx_edit_faces",
                faces=[await nearest(body, "face", [0, 0, 2])],
                action="offset",
                distance=1,
            )
            assert not math.isclose(await volume(body), expected, rel_tol=1e-7)
            await call("nx_rollback", checkpoint_id=checkpoint["checkpoint_id"])
            body = (await call("nx_list_bodies"))["objects"][0]["id"]
            assert math.isclose(await volume(body), expected, rel_tol=1e-7)
            await call(
                "nx_edit_faces",
                faces=[await nearest(body, "face", [0, 0, 2])],
                action="offset",
                distance=0.5,
            )
            edited_volume = await volume(body)
            await call("nx_save_part")
            await call("nx_close_part")
            await call("nx_open_part", path=prefix + "/imported-housing.prt")
            stale = await reject("nx_measure_volume", body=body)
            assert stale["code"] == "NX_OBJECT_STALE"
            body = (await call("nx_list_bodies"))["objects"][0]["id"]
            assert math.isclose(await volume(body), edited_volume, rel_tol=1e-7)
            await checked(
                "rounded_enclosure_analytic_shell_step_roundtrip_local_edit_rollback_reopen"
            )

            async def curved_patch(poles):
                primary = []
                for y in [0, 10]:
                    primary.append(
                        [await spline([[x, y, z] for x, z in poles], degree=2, method="poles")]
                    )
                cross = [[await spline([[x, 0, z], [x, 10, z]])] for x, z in [poles[0], poles[-1]]]
                return (await call("nx_surface_mesh", primary=primary, cross=cross))["body"]["id"]

            await new("curved-continuity")
            a = await curved_patch([[0, 0], [10, 0], [20, 5]])
            b = await curved_patch([[20, 5], [30, 10], [40, 20]])
            result = await call(
                "nx_surface_continuity",
                first=await nearest(a, "edge", [20, 5, 5]),
                second=await nearest(b, "edge", [20, 5, 5]),
                samples=7,
            )
            receipt["curved_continuity"] = result
            assert result["checks"] == {"G0": True, "G1": True, "G2": True}
            c = await curved_patch([[20, 5], [30, 15], [40, 30]])
            result = await call(
                "nx_surface_continuity",
                first=await nearest(a, "edge", [20, 5, 5]),
                second=await nearest(c, "edge", [20, 5, 5]),
                samples=7,
            )
            assert result["checks"]["G0"] and not result["checks"]["G1"]
            await checked("curved_parabolic_G2_join_and_deliberate_tangent_discontinuity")
            await new("thin-wall")
            body = await block(height=0.2)
            face = await nearest(body, "face", [10, 5, 0.2])
            thin = await call("nx_wall_thickness", body=body, faces=[face], tolerance=0.001)
            assert math.isclose(thin["minimum_sampled_thickness"], 0.2, abs_tol=1e-7)
            unresolved = await call("nx_wall_thickness", body=body, faces=[face], tolerance=0.3)
            assert (
                unresolved["measured_count"] == 0
                and unresolved["minimum_sampled_thickness"] is None
            )
            receipt["thin_wall"] = thin
            await checked("thin_wall_measurement_and_outside_ray_origin_rejection")
            await new("draft-transitions")
            body = await block(height=20)
            side = await nearest(body, "face", [20, 5, 10])
            bottom = await nearest(body, "face", [10, 5, 0])
            await call(
                "nx_draft", faces=[side], stationary_face=bottom, direction=[0, 0, 1], angle=5
            )
            face = await nearest(body, "face", [20, 5, 10])
            positive = await call(
                "nx_face_analysis", faces=[face], pull_direction=[0, 0, 1], minimum_draft=6
            )
            negative = await call(
                "nx_face_analysis", faces=[face], pull_direction=[0, 0, -1], minimum_draft=6
            )
            angles = [x["signed_draft_degrees"] for x in positive["samples"]]
            assert all(math.isclose(abs(x), 5, abs_tol=1e-5) for x in angles)
            assert all(
                math.isclose(x["signed_draft_degrees"], -y["signed_draft_degrees"], abs_tol=1e-5)
                for x, y in zip(positive["samples"], negative["samples"], strict=True)
            )
            assert {
                positive["samples"][0]["classification"],
                negative["samples"][0]["classification"],
            } == {"negative", "below_minimum"}
            receipt["draft"] = {"forward": positive, "reverse": negative}
            await checked("known_five_degree_draft_signed_transition_and_threshold")

            await new("bracket")
            await call("nx_sheet_metal_context")
            await call(
                "nx_set_sheet_metal_defaults", thickness=2, bend_radius=3, neutral_factor=0.33
            )
            sk = (await call("nx_create_sketch"))["object"]["id"]
            await call(
                "nx_sketch_rectangle",
                sketch_id=sk,
                corner1={"x": 0, "y": 0},
                corner2={"x": 100, "y": 80},
            )
            await call("nx_finish_sketch", sketch_id=sk)
            tab = await call(
                "nx_sheet_metal_feature",
                operation="tab",
                parameters={"section": sk, "thickness": 2},
            )
            body = tab["body"]["id"]
            flange = await call(
                "nx_sheet_metal_feature",
                operation="flange",
                parameters={
                    "flanges": [
                        {
                            "edges": [await nearest(body, "edge", [50, 0, 0])],
                            "length": 20,
                            "length_reference": "Inside",
                            "angle": 90,
                        }
                    ]
                },
            )
            info = (await call("nx_sheet_metal_info", body=body))["items"][0]
            await call(
                "nx_sheet_metal_annotation",
                kind="bend",
                body=body,
                faces=[info["bends"][0]["face"]["id"]],
                position=[50, -25, 20],
                automatic=True,
            )
            angle = next(x["object"]["id"] for x in flange["expressions"] if x["value"] == 90)
            edited = await call("nx_set_expression", expression=angle, formula="75")
            assert len(edited["refreshed_annotations"]) == 1
            notes = await call("nx_list_annotations")
            assert any("75.000 deg" in " ".join(x.get("text", [])) for x in notes["items"])
            flat = await call(
                "nx_sheet_metal_feature",
                operation="flat_pattern",
                parameters={
                    "upward_face": await nearest(body, "face", [50, 40, 0]),
                    "x_axis_edge": await nearest(body, "edge", [50, 80, 0]),
                    "associative": True,
                },
            )
            sheet = (await call("nx_create_drawing", name="Flat", size="A3"))["object"]["id"]
            view = await call(
                "nx_add_flat_pattern_view",
                drawing=sheet,
                flat_pattern=flat["feature"]["id"],
                position=[150, 150],
            )
            table = await call(
                "nx_bend_table",
                view=view["view"]["id"],
                position=[20, 270],
                columns=["BendID", "BendAngle", "BendRadius"],
            )
            receipt["bend_table_before"] = table
            await call("nx_activate_drawing")
            await call("nx_set_expression", expression=angle, formula="80")
            await call("nx_activate_drawing", drawing=sheet)
            automatic_tables = [
                x
                for x in (await call("nx_list_annotations"))["items"]
                if x["native_type"] == "BendTable"
            ]
            assert automatic_tables[0]["rows"] != table["rows"], (
                "Automatic table remained stale before any table edit"
            )
            table2 = await call(
                "nx_bend_table",
                view=view["view"]["id"],
                table=table["table"]["id"],
                position=[20, 270],
            )
            receipt["bend_table_after"] = table2
            assert table2["rows"] != table["rows"]
            await artifact(
                await call("nx_export_drawing_pdf", path=prefix + "/bracket.pdf"), "bracket.pdf"
            )
            await call("nx_save_part")
            await call("nx_close_part")
            await call("nx_open_part", path=prefix + "/bracket.prt")
            await call("nx_activate_drawing")
            refreshed = await call("nx_refresh_annotations")
            assert refreshed["updated_count"] == 0
            await checked(
                "native_bend_table_managed_PMI_transactional_update_and_persistent_reopen"
            )

            for detailed in [False, True]:
                await new("standard-thread-" + str(detailed))
                rows = (await call("nx_thread_catalog", standard="Metric Coarse", size="M6 x 1.0"))[
                    "items"
                ]
                row = next(r for r in rows if r["RadialEngage"] == "0.75" and r["Method"] == "CUT")
                body = await block(height=10)
                await call(
                    "nx_hole",
                    diameter=float(row["TapDrillDia"]),
                    depth=10,
                    x=10,
                    y=5,
                    z=10,
                    body=body,
                    direction=[0, 0, -1],
                )
                v0 = await volume(body)
                thread = await call(
                    "nx_standard_thread",
                    face=await nearest(body, "face", [12.5, 5, 5], "cylinder"),
                    start_face=await nearest(body, "face", [5, 5, 10]),
                    standard=row["Standard"],
                    size=row["Size"],
                    method=row["Method"],
                    radial_engage=row["RadialEngage"],
                    length=8,
                    detailed=detailed,
                )
                assert math.isclose(thread["pitch"], 1, abs_tol=1e-8) and thread["internal"]
                v1 = await volume(body)
                assert v1 < v0 if detailed else math.isclose(v0, v1, rel_tol=1e-8)
                datum = await call(
                    "nx_pmi_datum",
                    faces=[await nearest(body, "face", [5, 5, 10])],
                    letter="A",
                    position=[25, 0, 10],
                )
                fcf = await call(
                    "nx_pmi_fcf",
                    faces=[await nearest(body, "face", [12.5, 5, 5], "cylinder")],
                    characteristic="Position",
                    tolerance=0.1,
                    position=[25, 5, 10],
                    datums=[datum["annotation"]["id"]],
                    material="MMC",
                    zone_shape="diameter",
                    datum_material=["RFS"],
                    projected_height=5,
                )
                receipt.setdefault("threads", []).append(
                    {"result": thread, "before": v0, "after": v1, "fcf": fcf}
                )
                await checked("standard_table_thread_" + str(detailed) + "_and_GDT_modifiers")

            await new("assembly")
            for name, path, translation in [
                ("Housing", "enclosure", [0, 0, 0]),
                ("Bracket", "bracket", [-50, -40, 4]),
            ]:
                await call(
                    "nx_add_component",
                    part_path=prefix + "/" + path + ".prt",
                    name=name,
                    translation=translation,
                )
            components = (await call("nx_list_components"))["components"]
            assert len(components) == 2
            bounds = await call("nx_get_bounding_box", scope="assembly", precision="exact")
            receipt["assembly_bounds"] = bounds
            explosion = (await call("nx_create_explosion", name="Service"))["object"]["id"]
            placements = [
                {
                    "component": c["object"]["id"],
                    "translation": [
                        c["translation"][0],
                        c["translation"][1],
                        c["translation"][2] + i * 60,
                    ],
                }
                for i, c in enumerate(components)
            ]
            await call("nx_edit_explosion", explosion=explosion, placements=placements)
            await call("nx_show_explosion", explosion=explosion)
            edges = [await nearest(c["object"]["id"], "edge", [0, 0, 10]) for c in components]
            trace = await call(
                "nx_explosion_trace", explosion=explosion, start_edge=edges[0], end_edge=edges[1]
            )
            moved = await call(
                "nx_edit_explosion_trace",
                traceline=trace["traceline"]["id"],
                start_percent=25,
                end_percent=75,
                start_offset=3,
                end_offset=4,
            )
            assert moved["start_offset"] == 3 and moved["end_offset"] == 4
            await artifact(
                await call(
                    "nx_render_view", path=prefix + "/assembly.png", style="shaded_with_edges"
                ),
                "assembly.png",
            )
            sheet = (await call("nx_create_drawing", name="Service", size="A3"))["object"]["id"]
            bom = await call("nx_create_parts_list", drawing=sheet, position=[25, 270])
            edited = await call(
                "nx_parts_list_column",
                parts_list=bom["parts_list"]["id"],
                action="edit",
                index=1,
                title="COMPONENT",
                width=65,
            )
            assert edited["columns"][1]["title"] == "COMPONENT"
            appended = await call(
                "nx_parts_list_column",
                parts_list=bom["parts_list"]["id"],
                action="append",
                title="PART",
                width=45,
                field=bom["columns"][1]["field"],
            )
            assert appended["column_count"] == 4
            await call(
                "nx_parts_list_column", parts_list=bom["parts_list"]["id"], action="remove", index=3
            )
            view = (
                await call(
                    "nx_add_base_view",
                    drawing=sheet,
                    scope="assembly",
                    explosion=explosion,
                    position=[190, 160],
                )
            )["object"]["id"]
            balloons = await call(
                "nx_parts_list_balloons", parts_list=bom["parts_list"]["id"], view=view
            )
            assert balloons["balloon_count"] >= 1
            annotation = balloons["balloons"][0]["id"]
            await call(
                "nx_edit_annotation",
                annotation=annotation,
                position=[300, 150, 0],
                name="Service callout",
            )
            annotations = await call("nx_list_annotations")
            assert any(x.get("position") == [300, 150, 0] for x in annotations["items"])
            sheets = await call("nx_list_drawings")
            assert sheets["sheets"][0]["views"]
            await artifact(
                await call("nx_export_drawing_pdf", path=prefix + "/service.pdf"), "service.pdf"
            )
            await call("nx_activate_drawing")
            assert (await call("nx_list_drawings"))["modeling_active"]
            await call("nx_activate_drawing", drawing=sheet)
            await call("nx_save_part")
            await checked(
                "sheet_metal_assembly_explosion_trace_edit_BOM_columns_balloon_placement_drawings"
            )
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
