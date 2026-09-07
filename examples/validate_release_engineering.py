"""Native release acceptance: drawings, propagation, vendor repair and mixed units.

NX_VENDOR_STEP selects a user-owned local STEP fixture, never redistributed.
All created parts are isolated; durable receipts record failure and restoration.
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


def dxf_extents(path):
    """Read LINE entity bounds for this rectangular ASCII DXF fixture."""
    lines = path.read_text(errors="strict").splitlines()
    pairs = [(int(lines[i]), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    points, record, entity, section = [], {}, None, None
    for code, value in pairs + [(0, "EOF")]:
        if code == 2 and entity == "SECTION":
            section = value
        if code == 0:
            if entity == "LINE" and section == "ENTITIES":
                points.extend(
                    [(float(record[10]), float(record[20])), (float(record[11]), float(record[21]))]
                )
            if value == "ENDSEC":
                section = None
            entity, record = value, {}
        else:
            record[code] = value
    assert points, "No LINE geometry in the native rectangular DXF"
    return sorted(max(p[i] for p in points) - min(p[i] for p in points) for i in (0, 1))


async def run_suite(call, reject, artifact, upload, output):
    prefix = "release-engineering-" + uuid.uuid4().hex[:8]
    receipt = {"fixture": prefix, "checks": [], "artifacts": []}

    def save():
        (output / "release-engineering-validation.json").write_text(json.dumps(receipt, indent=2))

    async def new(name, units="mm"):
        return await call("nx_create_part", path=prefix + "/" + name + ".prt", units=units)

    async def near(kind, point, **params):
        return (await call("nx_find_geometry", kind=kind, near=point, **params))["items"][0][
            "object"
        ]["id"]

    async def block(x=50, y=30, z=5, radius=None):
        sk = (await call("nx_create_sketch"))["object"]["id"]
        if radius is None:
            await call(
                "nx_sketch_rectangle",
                sketch_id=sk,
                corner1={"x": 0, "y": 0},
                corner2={"x": x, "y": y},
            )
        else:
            await call(
                "nx_sketch_arc",
                sketch_id=sk,
                cx=0,
                cy=0,
                radius=radius,
                start_angle=0,
                end_angle=360,
            )
        await call("nx_finish_sketch", sketch_id=sk)
        return await call("nx_extrude", sketch_id=sk, distance=z)

    async def checked(name):
        assert (await call("nx_model_health"))["healthy"]
        receipt["checks"].append(name)
        save()

    async def download(meta, name):
        receipt["artifacts"].append(await artifact(meta, name))
        save()

    before = (await call("nx_list_open_parts"))["parts"]
    assert before and not any(p["modified"] for p in before), "Save existing parts first"
    original = next(p for p in before if p["work"])
    display = next(p for p in before if p["display"])
    try:
        await new("drawing")
        solid = await block()
        body = solid["body"]["id"]
        await call("nx_hole", body=body, diameter=8, depth=5, x=25, y=15, z=5, direction=[0, 0, -1])
        await call("nx_save_part")
        face = await near("face", [10, 10, 5], geometry_type="plane")
        anchor = (await call("nx_geometry_anchor", object=face))["anchor"]
        sheet = (await call("nx_create_drawing", name="Service", scale=2))["object"]["id"]
        view = (
            await call(
                "nx_add_base_view", drawing=sheet, body=body, view="top", position=[100, 180]
            )
        )["object"]["id"]
        assert (await call("nx_drawing_view_info", view=view))["scale"] == 2
        edited = await call(
            "nx_edit_drawing_view",
            view=view,
            scale=1.5,
            position=[105, 180],
            style={
                "hidden_lines": True,
                "hidden_font": 2,
                "visible_font": 1,
                "construction_geometry": False,
            },
        )
        assert edited["scale"] == 1.5 and edited["position"] == [105, 180]
        revision_args = {
            "drawing": sheet,
            "kind": "revision",
            "rows": [["REV", "DESCRIPTION"], ["A", "Initial"]],
            "widths": [20, 70],
            "position": [20, 275],
        }
        rev = await call("nx_drawing_table", **revision_args)
        revision_args["rows"].append(["B", "Documentation verified"])
        updated = await call("nx_drawing_table", **revision_args, table=rev["table"]["id"])
        assert updated["rows"] == revision_args["rows"]
        title = await call(
            "nx_drawing_table",
            drawing=sheet,
            kind="title_block",
            rows=[["TITLE", "Service fixture"], ["PART", "DEV13"], ["SHEET", "1 / 1"]],
            widths=[25, 80],
            position=[395, 20],
        )
        assert title["native_title_block"] == "TitleBlock"
        edited_title = await call(
            "nx_drawing_table",
            drawing=sheet,
            kind="title_block",
            rows=[["TITLE", "Service fixture"], ["PART", "DEV13-R1"], ["SHEET", "1 / 1"]],
            widths=[25, 80],
            position=[395, 20],
            table=title["table"]["id"],
        )
        assert edited_title["rows"][1][1] == "DEV13-R1"
        detail = await call(
            "nx_add_detail_drawing_view",
            parent_view=view,
            center=[25, 15, 5],
            radius=8,
            position=[230, 180],
            scale=3,
        )
        assert detail["scale"] == 3 and detail["inside_sheet"]
        edge = await near("edge", [25, 0, 5])
        edge = await near("edge", [29, 15, 5], geometry_type="circle")
        section = await call(
            "nx_add_section_drawing_view",
            parent_view=view,
            cut_object=edge,
            position=[105, 90],
            cut_association="arc_center",
            step_direction=[1, 0, 0],
            arrow_direction=[0, 1, 0],
            scale=1,
        )
        assert section["native_type"] == "SectionView" and section["scale"] == 1
        edge = await near("edge", [25, 0, 5])
        dim = await call(
            "nx_add_dimension", view=view, object1=edge, dim_type="horizontal", origin=[105, 220]
        )
        assert math.isclose(dim["measured_value"], 50, abs_tol=1e-6)
        formatted = await call(
            "nx_edit_dimension_format",
            dimension=dim["object"]["id"],
            decimal_places=2,
            trailing_zeros=True,
            units="mm",
            tolerance_type="bilateral",
            upper_tolerance=0.05,
            lower_tolerance=-0.02,
            tolerance_decimal_places=2,
        )
        assert math.isclose(formatted["computed_value"], 50, abs_tol=1e-6)
        assert math.isclose(formatted["upper_tolerance"], 0.05, abs_tol=1e-9)
        assert math.isclose(formatted["lower_tolerance"], -0.02, abs_tol=1e-9)
        await download(
            await call("nx_export_planar_dxf", source=face, path=prefix + "/face.dxf"), "face.dxf"
        )
        await download(
            await call("nx_export_drawing_pdf", path=prefix + "/service.pdf"), "service.pdf"
        )
        await call("nx_save_part")
        await call("nx_close_part")
        await call("nx_open_part", path=prefix + "/drawing.prt")
        assert (await call("nx_resolve_geometry_anchor", anchor=anchor))["object"]["kind"] == "face"
        reopened_sheet = (await call("nx_list_drawings"))["sheets"][0]
        annotations = (await call("nx_list_annotations"))["items"]
        reopened_title = next(a for a in annotations if a.get("table_kind") == "title_block")
        revised = await call(
            "nx_drawing_table",
            drawing=reopened_sheet["object"]["id"],
            kind="title_block",
            rows=[["TITLE", "Service fixture"], ["PART", "DEV13-R2"], ["SHEET", "1 / 1"]],
            widths=[25, 80],
            position=[395, 20],
            table=reopened_title["object"]["id"],
        )
        assert revised["rows"][1][1] == "DEV13-R2"
        base_info = next(
            v for v in reopened_sheet["views"] if v["name"] == edited["object"]["name"]
        )
        await call("nx_edit_drawing_view", view=base_info["object"]["id"], position=[110, 180])
        await call("nx_edit_drawing_view", view=base_info["object"]["id"], position=[105, 180])
        await download(
            await call("nx_export_drawing_pdf", path=prefix + "/service-reopened.pdf"),
            "service-reopened.pdf",
        )
        await checked(
            "native_view_edits_section_detail_dimension_title_revision_pdf_and_anchor_reopen"
        )

        await new("inch-part", "inch")
        solid = await block(x=2, y=1, z=0.25)
        bounds = await call("nx_get_bounding_box")
        assert bounds["units"] == "inch" and math.isclose(bounds["max"][0], 2)
        assert math.isclose(
            (await call("nx_measure_volume", body=solid["body"]["id"]))["volume_mm3"],
            0.5 * 25.4**3,
            rel_tol=1e-7,
        )
        for units, pos in [("mm", [100, 100]), ("in", [4, 4])]:
            sheet = (await call("nx_create_drawing", name=units, size="A4", units=units))["object"][
                "id"
            ]
            v = (
                await call(
                    "nx_add_base_view",
                    drawing=sheet,
                    body=solid["body"]["id"],
                    view="top",
                    position=pos,
                )
            )["object"]["id"]
            projected = await call(
                "nx_add_projection_view",
                base_view=v,
                direction="right",
                spacing=80 if units == "mm" else 3,
            )
            projected_info = await call("nx_drawing_view_info", view=projected["object"]["id"])
            assert math.isclose(
                projected_info["position"][0], pos[0] + (80 if units == "mm" else 3), abs_tol=1e-6
            )
            assert projected_info["inside_sheet"]
            info = await call("nx_drawing_view_info", view=v)
            assert info["units"] == units and info["inside_sheet"]
            assert (await call("nx_list_drawings"))["sheets"][-1]["units"] == units
        await download(
            await call("nx_export_drawing_pdf", path=prefix + "/mixed-units.pdf"), "mixed-units.pdf"
        )
        await call("nx_save_part")
        await checked("inch_volume_and_metric_inch_sheets_in_inch_part")

        # Explicit standards from the installed NX table, with both hands.
        receipt["threads"] = []
        for standard, size, units, diameter, length in [
            ("Metric Fine", "M6 x 0.75", "mm", 6, 8),
            ("Inch UNC", "1/4-20", "inch", 0.25, 0.4),
        ]:
            rows = (await call("nx_thread_catalog", standard=standard, size=size))["items"]
            row = next(r for r in rows if r["Method"] == "CUT")
            for left in [False, True]:
                await new("thread-" + str(len(receipt["threads"])), units)
                await block(z=length * 1.5, radius=diameter / 2)
                cylinder = await near(
                    "face", [diameter / 2, 0, length / 2], geometry_type="cylinder"
                )
                start = await near("face", [0, 0, length * 1.5], geometry_type="plane")
                thread = await call(
                    "nx_standard_thread",
                    face=cylinder,
                    start_face=start,
                    standard=standard,
                    size=size,
                    length=length,
                    method=row["Method"],
                    radial_engage=row["RadialEngage"],
                    left_hand=left,
                    detailed=True,
                )
                assert thread["standard"] == standard and thread["size"] == size
                assert math.isclose(thread["pitch"], 0.75 if units == "mm" else 0.05, rel_tol=1e-7)
                assert math.isclose(thread["major_diameter"], diameter, rel_tol=1e-6)
                receipt["threads"].append(
                    {
                        k: thread[k]
                        for k in [
                            "standard",
                            "size",
                            "pitch",
                            "major_diameter",
                            "minor_diameter",
                            "representation",
                        ]
                    }
                    | {"left_hand": left, "part_units": units}
                )
                await checked("thread_" + standard + ("_left" if left else "_right"))

        await new("mixed-assembly")
        await call("nx_add_component", part_path=prefix + "/inch-part.prt", name="Imperial")
        mixed_bounds = await call("nx_get_bounding_box", scope="assembly", precision="exact")
        assert all(
            math.isclose(a, b, abs_tol=1e-6)
            for a, b in zip(mixed_bounds["dimensions"], [50.8, 25.4, 6.35], strict=True)
        )
        assert math.isclose(
            (await call("nx_measure_volume", scope="assembly"))["volume_mm3"],
            8193.532,
            rel_tol=1e-7,
        )
        await call(
            "nx_add_component",
            part_path=prefix + "/drawing.prt",
            name="Metric",
            translation=[0, 0, 20],
        )
        mixed_components = (await call("nx_list_components"))["components"]
        distance = await call(
            "nx_measure_distance",
            obj1=mixed_components[0]["object"]["id"],
            obj2=mixed_components[1]["object"]["id"],
        )
        assert math.isclose(distance["distance"], 13.65, abs_tol=1e-6)
        receipt["mixed_assembly"] = {"bounds": mixed_bounds, "clearance": distance}
        await checked("mixed_unit_component_bounds_volume_and_clearance")

        await new("prototype")
        await block()
        await call("nx_save_part")
        await new("replacement")
        await block(z=10)
        await call("nx_save_part")
        await new("assembly")
        for name, z in [("Base", 0), ("Lid", 20)]:
            await call(
                "nx_add_component",
                part_path=prefix + "/prototype.prt",
                name=name,
                translation=[0, 0, z],
            )
        components = sorted(
            (await call("nx_list_components"))["components"], key=lambda c: c["translation"][2]
        )
        for c in components:
            await call("nx_assembly_constraint", constraint_type="fix", component=c["object"]["id"])
        ex = (await call("nx_create_explosion", name="Service", scale=2))["object"]["id"]
        await call(
            "nx_edit_explosion",
            explosion=ex,
            placements=[{"component": components[1]["object"]["id"], "translation": [0, 0, 60]}],
        )
        edges = [
            await near("edge", [25, 0, z + 5], owner=c["object"]["id"])
            for c, z in zip(components, [0, 20], strict=True)
        ]
        await call("nx_explosion_trace", explosion=ex, start_edge=edges[0], end_edge=edges[1])
        sheet = (await call("nx_create_drawing", name="Assembly"))["object"]["id"]
        bom = await call("nx_create_parts_list", drawing=sheet, position=[20, 270])
        view = (
            await call(
                "nx_add_base_view",
                drawing=sheet,
                scope="assembly",
                view="front",
                position=[180, 150],
                explosion=ex,
            )
        )["object"]["id"]
        balloons = await call(
            "nx_parts_list_balloons", parts_list=bom["parts_list"]["id"], view=view
        )
        edge = await near("edge", [0, 0, 2.5], owner=components[0]["object"]["id"])
        dimension = await call(
            "nx_add_dimension", view=view, object1=edge, dim_type="vertical", origin=[120, 150]
        )
        assert math.isclose(dimension["measured_value"], 5, abs_tol=1e-6)
        initial = await call("nx_update_assembly_documentation")
        assert initial["parts_lists"][0]["row_count"] == 1

        await call("nx_save_part")
        await call("nx_open_part", path=prefix + "/prototype.prt")
        feature = next(
            f
            for f in (await call("nx_list_features"))["objects"]
            if f["journal_id"].startswith("EXTRUDE")
        )
        await call("nx_edit_feature", name=feature["id"], params={"distance": 8})
        await call("nx_save_part")
        await call("nx_open_part", path=prefix + "/assembly.prt")
        resized = await call("nx_update_assembly_documentation")
        assert all(c["solver_status"] == "Solved" for c in resized["constraints"])
        receipt["assembly_probe"] = {
            "initial": initial,
            "resized": resized,
            "bounds": await call("nx_get_bounding_box", scope="assembly"),
        }
        save()
        dimensions = (await call("nx_list_dimensions"))["dimensions"]
        assert len(dimensions) == 1 and math.isclose(
            dimensions[0]["computed_value"], 8, abs_tol=1e-6
        )
        assert math.isclose(
            (await call("nx_get_bounding_box", scope="assembly"))["max"][2], 28, abs_tol=1e-6
        )
        components = sorted(
            (await call("nx_list_components"))["components"], key=lambda c: c["translation"][2]
        )
        gap = await call(
            "nx_measure_distance",
            obj1=components[0]["object"]["id"],
            obj2=components[1]["object"]["id"],
        )
        assert math.isclose(gap["distance"], 12, abs_tol=1e-6)
        annotations = (await call("nx_list_annotations"))["items"]
        line = next(a for a in annotations if a["native_type"] == "AutomaticTraceline")
        receipt["trace_after_resize"] = line
        save()
        assert math.isclose(line["start"][2], 8, abs_tol=1e-6) and math.isclose(
            line["end"][2], 68, abs_tol=1e-6
        )
        # Replace after explicitly removing old prototype-edge trace anchors.
        await call("nx_edit_annotation", annotation=line["object"]["id"], delete=True)
        base = next(c for c in components if c["translation"][2] == 0)
        await call(
            "nx_component_action",
            component=base["object"]["id"],
            action="replace",
            part_path=prefix + "/replacement.prt",
        )
        replaced = await call("nx_update_assembly_documentation")
        assert replaced["parts_lists"][0]["row_count"] == 2
        retained = (await call("nx_list_dimensions"))["dimensions"][0]
        assert retained["retained"] and not replaced["documentation_complete"]
        assert replaced["warnings"]
        components = sorted(
            (await call("nx_list_components"))["components"], key=lambda c: c["translation"][2]
        )
        # Replacement invalidates original occurrence callouts; recreate explicitly.
        for balloon in balloons["balloons"]:
            await call("nx_edit_annotation", annotation=balloon["id"], delete=True)
        await call("nx_parts_list_balloons", parts_list=bom["parts_list"]["id"], view=view)
        replacement_edge = await near("edge", [0, 0, 5], owner=components[0]["object"]["id"])
        rebound = await call(
            "nx_add_dimension",
            view=view,
            object1=replacement_edge,
            dim_type="vertical",
            origin=[120, 150],
            dimension=retained["object"]["id"],
        )
        assert rebound["edited"] and math.isclose(rebound["measured_value"], 10, abs_tol=1e-6)
        assert not (await call("nx_list_dimensions"))["dimensions"][0]["retained"]
        gap = await call(
            "nx_measure_distance",
            obj1=components[0]["object"]["id"],
            obj2=components[1]["object"]["id"],
        )
        assert math.isclose(gap["distance"], 10, abs_tol=1e-6)
        assert sorted(c["translation"][2] for c in components) == [0, 20]
        edges = [
            await near(
                "edge",
                [25, 0, c["translation"][2] + (10 if c["translation"][2] == 0 else 8)],
                owner=c["object"]["id"],
            )
            for c in components
        ]
        ex = (await call("nx_list_explosions"))["explosions"][0]["object"]["id"]
        await call("nx_explosion_trace", explosion=ex, start_edge=edges[0], end_edge=edges[1])
        final = await call("nx_update_assembly_documentation")
        assert final["health"]["healthy"] and final["documentation_complete"]
        await download(
            await call("nx_export_drawing_pdf", path=prefix + "/assembly.pdf"), "assembly.pdf"
        )
        receipt["assembly"] = {
            "initial": initial,
            "resized": resized,
            "replaced": replaced,
            "final": final,
            "minimum_clearance": gap,
        }
        await checked(
            "prototype_resize_replace_mates_clearance_explosion_trace_BOM_and_drawing_propagation"
        )

        # Two opposite partial-width bends exercise square and round reliefs.
        await new("sheet-channel")
        await call("nx_sheet_metal_context")
        await call("nx_set_sheet_metal_defaults", thickness=2, bend_radius=3, neutral_factor=0.33)
        sk = (await call("nx_create_sketch"))["object"]["id"]
        await call(
            "nx_sketch_rectangle",
            sketch_id=sk,
            corner1={"x": 0, "y": 0},
            corner2={"x": 100, "y": 80},
        )
        await call("nx_finish_sketch", sketch_id=sk)
        tab = await call(
            "nx_sheet_metal_feature", operation="tab", parameters={"section": sk, "thickness": 2}
        )
        body = tab["body"]["id"]
        flange_edges = [await near("edge", [50, y, 0], owner=body) for y in [0, 80]]
        flanges = []
        for edge, length, relief in zip(flange_edges, [20, 25], ["Square", "Round"], strict=True):
            flanges.append(
                {
                    "edges": [edge],
                    "length": length,
                    "length_reference": "Inside",
                    "angle": 90,
                    "width_option": "AtCenter",
                    "width": 60,
                    "bend_options": {
                        "bend_relief_type": relief,
                        "use_global_relief_width": False,
                        "bend_relief_width": 1,
                        "use_global_relief_depth": False,
                        "bend_relief_depth": 3,
                    },
                }
            )
        await call("nx_sheet_metal_feature", operation="flange", parameters={"flanges": flanges})
        info = (await call("nx_sheet_metal_info", body=body))["items"][0]
        assert info["bend_count"] == 2 and math.isclose(info["thickness"], 2)
        assert all(
            math.isclose(b["inner_radius"], 3) and math.isclose(b["angle_degrees"], 90)
            for b in info["bends"]
        )
        flat = await call(
            "nx_sheet_metal_feature",
            operation="flat_pattern",
            parameters={
                "upward_face": await near("face", [50, 40, 0], owner=body),
                "x_axis_edge": await near("edge", [0, 40, 0], owner=body),
                "associative": True,
            },
        )
        await download(
            await call(
                "nx_export_flat_pattern",
                flat_pattern=flat["feature"]["id"],
                path=prefix + "/channel.dxf",
            ),
            "channel.dxf",
        )
        actual = dxf_extents(output / "channel.dxf")
        expected = sorted([100, 80 + 20 + 25 + 4 - 4 * 5 + math.pi * (3 + 0.33 * 2)])
        assert all(
            math.isclose(a, b, abs_tol=1e-4) for a, b in zip(actual, expected, strict=True)
        ), (actual, expected)
        receipt["sheet_channel"] = {
            "bend_count": info["bend_count"],
            "developed_dimensions": actual,
            "analytic_dimensions": expected,
            "reliefs": ["Square", "Round"],
        }
        await checked("two_bend_partial_width_channel_square_round_reliefs_analytic_flat_pattern")
        await new("sheet-corner")
        await call("nx_sheet_metal_context")
        sk = (await call("nx_create_sketch"))["object"]["id"]
        await call(
            "nx_sketch_rectangle",
            sketch_id=sk,
            corner1={"x": 0, "y": 0},
            corner2={"x": 100, "y": 80},
        )
        await call("nx_finish_sketch", sketch_id=sk)
        body = (
            await call(
                "nx_sheet_metal_feature",
                operation="tab",
                parameters={"section": sk, "thickness": 2},
            )
        )["body"]["id"]
        edges = [await near("edge", p, owner=body) for p in [[50, 0, 0], [0, 40, 0]]]
        await call(
            "nx_sheet_metal_feature",
            operation="flange",
            parameters={
                "flanges": [
                    {
                        "edges": [edge],
                        "length": 20,
                        "angle": 90,
                        "length_reference": "Inside",
                        "miter": True,
                    }
                    for edge in edges
                ]
            },
        )
        info = (await call("nx_sheet_metal_info", body=body))["items"][0]
        assert info["bend_count"] == 2
        flat = await call(
            "nx_sheet_metal_feature",
            operation="flat_pattern",
            parameters={
                "upward_face": await near("face", [50, 40, 0], owner=body),
                "x_axis_edge": await near("edge", [50, 80, 0], owner=body),
                "associative": True,
            },
        )
        await download(
            await call(
                "nx_export_flat_pattern",
                flat_pattern=flat["feature"]["id"],
                path=prefix + "/corner.dxf",
            ),
            "corner.dxf",
        )
        await call("nx_set_view", orientation="isometric")
        await call("nx_fit_view")
        await download(
            await call("nx_render_view", path=prefix + "/corner.png", style="shaded_with_edges"),
            "corner.png",
        )
        await checked("adjacent_mitered_flanges_native_validity_and_flat_export")

        # Imported vendor topology is user-provided and stays out of the repository.
        vendor = Path(os.environ["NX_VENDOR_STEP"])
        await new("vendor")
        vendor_path = prefix + "/vendor.step"
        await upload(vendor, vendor_path)
        imported = await call("nx_import_geometry", path=vendor_path, flatten=True)
        receipt["vendor"] = {
            "sha256": hashlib.sha256(vendor.read_bytes()).hexdigest(),
            "import": imported,
        }
        assert (await call("nx_model_health"))["healthy"]
        await call("nx_save_part")
        face = await near("face", [0, 0, 0], geometry_type="plane")
        anchor = (await call("nx_geometry_anchor", object=face))["anchor"]
        checkpoint = (await call("nx_checkpoint", label="vendor repair"))["checkpoint_id"]
        prior = await call("nx_get_bounding_box")
        # Large offsets may be invalid for this topology: require a clear outcome,
        # then rollback any accepted edit and compare geometry independently.
        repaired = await call("nx_edit_faces", faces=[face], action="offset", distance=0.01)
        assert (await call("nx_model_health"))["healthy"]
        receipt["vendor"]["repair"] = repaired
        await call("nx_rollback", checkpoint_id=checkpoint)
        after = await call("nx_get_bounding_box")
        assert all(
            math.isclose(a, b, abs_tol=1e-6)
            for k in ["min", "max"]
            for a, b in zip(prior[k], after[k], strict=True)
        )
        await call("nx_save_part")
        await call("nx_close_part")
        await call("nx_open_part", path=prefix + "/vendor.prt")
        assert (await call("nx_resolve_geometry_anchor", anchor=anchor))["object"]["kind"] == "face"
        await call("nx_fit_view")
        await download(await call("nx_render_view", path=prefix + "/vendor.png"), "vendor.png")
        await checked("vendor_step_native_offset_repair_rollback_and_persistent_face_reopen")
        receipt["passed"] = True
    except Exception:
        receipt["passed"] = False
        receipt["error"] = traceback.format_exc()
        raise
    finally:
        try:
            await call("nx_open_part", path=original["path"])
            while True:
                parts = [
                    p for p in (await call("nx_list_open_parts"))["parts"] if prefix in p["path"]
                ]
                if not parts:
                    break
                p = next((p for p in parts if p["path"].endswith("assembly.prt")), parts[0])
                await call("nx_close_part", part=p["part"]["id"], save=True)
            if display["path"] != original["path"]:
                await call("nx_open_part", path=display["path"], work=False, display=True)
            after = (await call("nx_list_open_parts"))["parts"]
            assert {p["path"] for p in before} == {p["path"] for p in after}
            assert not any(p["modified"] for p in after)
            receipt["session_restored"] = True
        finally:
            save()
    return receipt


async def main():
    output = Path(os.environ.get("NX_VALIDATION_OUTPUT", "release-engineering-results"))
    output.mkdir(parents=True, exist_ok=True)
    async with (
        streamablehttp_client(os.environ["NX_MCP_URL"]) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        async def call(tool_name, **params):
            response = await client.call_tool(tool_name, params)
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
            return {"file": name, "size": len(data), "sha256": meta["sha256"]}

        async def upload(source, path):
            data = source.read_bytes()
            for offset in range(0, len(data), 256 * 1024):
                await call(
                    "nx_upload_file",
                    path=path,
                    data_base64=base64.b64encode(data[offset : offset + 256 * 1024]).decode(),
                    offset=offset,
                    total_size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )

        result = await run_suite(call, reject, artifact, upload, output)
        print(json.dumps({"passed": result["passed"], "checks": result["checks"]}))


if __name__ == "__main__":
    asyncio.run(main())
