"""Bounded native acceptance for damaged STEP, topology edits and nested units.

Set NX_MCP_URL, NX_VALIDATION_OUTPUT and NX_VENDOR_STEP (local public fixture).
Receipts describe tested cases; this is not general sheet-metal certification.
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


async def run_suite(call, reject, artifact, upload, output):
    prefix = "hard-geometry-" + uuid.uuid4().hex[:8]
    receipt = {"fixture": prefix, "checks": []}

    def save():
        (output / "hard-geometry-validation.json").write_text(json.dumps(receipt, indent=2))

    async def new(name, units="mm"):
        await call("nx_create_part", path=prefix + "/" + name + ".prt", units=units)

    async def sketch(x, y):
        s = (await call("nx_create_sketch"))["object"]["id"]
        await call(
            "nx_sketch_rectangle", sketch_id=s, corner1={"x": 0, "y": 0}, corner2={"x": x, "y": y}
        )
        await call("nx_finish_sketch", sketch_id=s)
        return s

    async def block(x, y, z):
        return await call("nx_extrude", sketch_id=await sketch(x, y), distance=z)

    async def near(kind, point, **params):
        return (await call("nx_find_geometry", kind=kind, near=point, **params))["items"][0][
            "object"
        ]["id"]

    async def checked(name):
        assert (await call("nx_model_health", scope="assembly"))["healthy"]
        receipt["checks"].append(name)
        save()

    before = (await call("nx_list_open_parts"))["parts"]
    assert not any(p["modified"] for p in before)
    original = next(p for p in before if p["work"])
    display = next(p for p in before if p["display"])
    try:
        await new("damaged-step")
        await block(10, 10, 10)
        prior = await call("nx_measure_volume")
        source = Path(os.environ["NX_VENDOR_STEP"]).read_bytes()
        damaged = output / "damaged.step"
        damaged.write_bytes(source[: len(source) // 3])
        await upload(damaged, prefix + "/damaged.step")
        receipt["damaged_import"] = await reject(
            "nx_import_geometry", path=prefix + "/damaged.step", flatten=True
        )
        assert receipt["damaged_import"]["details"]["mutation_outcome"] == "rolled_back"
        assert math.isclose(
            (await call("nx_measure_volume"))["volume_mm3"], prior["volume_mm3"], rel_tol=1e-8
        )
        assert (await call("nx_get_bounding_box"))["body_count"] == 1
        await checked("truncated_public_vendor_step_rejected_without_geometry_change")
        await new("heal")
        solid = await block(20, 20, 5)
        body = solid["body"]["id"]
        await call("nx_hole", body=body, diameter=4, depth=5, x=10, y=10, z=5, direction=[0, 0, -1])
        hole = await near("face", [12, 10, 2.5], geometry_type="cylinder")
        anchor = (await call("nx_geometry_anchor", object=hole))["anchor"]
        receipt["heal"] = await call("nx_edit_faces", faces=[hole], action="heal")
        assert math.isclose((await call("nx_measure_volume"))["volume_mm3"], 2000, rel_tol=1e-7)
        receipt["removed_anchor"] = await reject("nx_resolve_geometry_anchor", anchor=anchor)
        await checked("hole_face_healed_analytic_volume_deleted_anchor_rejected")
        await new("inch", units="inch")
        await block(1, 0.5, 0.25)
        await call("nx_save_part")
        await new("sub")
        await call(
            "nx_add_component",
            part_path=prefix + "/inch.prt",
            name="INCH",
            translation=[10, 20, 0],
            rotation_matrix=[[0, -1, 0], [1, 0, 0], [0, 0, 1]],
        )
        await call("nx_save_part")
        await new("top")
        await call(
            "nx_add_component",
            part_path=prefix + "/sub.prt",
            name="SUB",
            translation=[100, 0, 0],
            rotation_matrix=[[0, -1, 0], [1, 0, 0], [0, 0, 1]],
        )
        bounds = await call("nx_get_bounding_box", scope="assembly", precision="exact")
        for k, expected in [("min", [54.6, -2.7, 0]), ("max", [80, 10, 6.35])]:
            assert all(
                math.isclose(a, b, abs_tol=1e-6) for a, b in zip(bounds[k], expected, strict=True)
            ), (
                k,
                bounds,
            )
        assert math.isclose(
            (await call("nx_measure_volume", scope="assembly"))["volume_mm3"],
            25.4 * 12.7 * 6.35,
            rel_tol=1e-7,
        )
        # A second rotated nested occurrence has 5 mm exact clearance in Z.
        await call(
            "nx_add_component",
            part_path=prefix + "/sub.prt",
            name="SUB2",
            translation=[100, 0, 11.35],
            rotation_matrix=[[0, -1, 0], [1, 0, 0], [0, 0, 1]],
        )
        roots = [
            c
            for c in (await call("nx_list_components"))["components"]
            if c["name"] in ["SUB", "SUB2"]
        ]
        roots.sort(key=lambda component: component["name"])
        pair = {"obj1": roots[0]["object"]["id"], "obj2": roots[1]["object"]["id"]}
        receipt["nested"] = {
            "bounds": bounds,
            "clearance": await call("nx_measure_distance", **pair),
            "separate": await call("nx_check_interference", **pair),
        }
        assert math.isclose(receipt["nested"]["clearance"]["distance"], 5, abs_tol=1e-6)
        await call(
            "nx_set_component_transform",
            component=roots[1]["object"]["id"],
            translation=[100, 0, 5.35],
            rotation_matrix=[[0, -1, 0], [1, 0, 0], [0, 0, 1]],
        )
        receipt["nested"]["overlap"] = await call("nx_check_interference", **pair)
        assert receipt["nested"]["separate"]["counts"]["clear"] == 1
        overlap = receipt["nested"]["overlap"]["pairs"][0]
        assert overlap["classification"] == "penetration"
        assert math.isclose(overlap["interference_volume_mm3"], 25.4 * 12.7, rel_tol=1e-7)
        await checked("two_level_rotated_mixed_unit_bounds_volume_clearance_interference")
        await new("three-wall")
        await call("nx_sheet_metal_context")
        await call("nx_set_sheet_metal_defaults", thickness=2, bend_radius=3, neutral_factor=0.33)
        tab = await call(
            "nx_sheet_metal_feature",
            operation="tab",
            parameters={"section": await sketch(100, 80), "thickness": 2},
        )
        body = tab["body"]["id"]
        edges = [
            await near("edge", point, owner=body) for point in [[50, 0, 0], [0, 40, 0], [50, 80, 0]]
        ]
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
        assert info["bend_count"] == 3
        flat = await call(
            "nx_sheet_metal_feature",
            operation="flat_pattern",
            parameters={
                "upward_face": await near("face", [50, 40, 0], owner=body),
                "x_axis_edge": await near("edge", [100, 40, 0], owner=body),
                "associative": True,
            },
        )
        receipt["flat"] = await artifact(
            await call(
                "nx_export_flat_pattern",
                flat_pattern=flat["feature"]["id"],
                path=prefix + "/three-wall.dxf",
            ),
            "three-wall.dxf",
        )
        await call("nx_set_view", orientation="isometric")
        await call("nx_fit_view")
        receipt["render"] = await artifact(
            await call("nx_render_view", path=prefix + "/three-wall.png"), "three-wall.png"
        )
        await checked("three_adjacent_mitered_walls_native_health_and_flat_export")
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
                part = next((p for p in parts if p["path"].endswith("top.prt")), parts[0])
                await call("nx_close_part", part=part["part"]["id"], save=True)
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
    output = Path(os.environ.get("NX_VALIDATION_OUTPUT", "hard-geometry-results"))
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
