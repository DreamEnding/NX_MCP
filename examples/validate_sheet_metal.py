"""Native public-MCP sheet-metal workflow acceptance in disposable parts.

NX_MCP_URL selects the server. NX_VALIDATION_OUTPUT receives durable receipts and
checksum-verified PNG/PDF/DXF/GEO artifacts. Existing parts must be saved first.
This checks a bracket workflow, not every sheet-metal option or manufacturability.
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


def fixture_dxf_extents(path):
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


async def main():
    output = Path(os.environ.get("NX_VALIDATION_OUTPUT", "sheet-metal-results"))
    output.mkdir(parents=True, exist_ok=True)
    prefix = "sheet-metal-validation-" + uuid.uuid4().hex[:8]
    receipt = {"fixture": prefix, "checks": []}

    def save():
        (output / "sheet-metal-validation.json").write_text(json.dumps(receipt, indent=2))

    async with (
        streamablehttp_client(os.environ["NX_MCP_URL"]) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        async def call(method, **params):
            response = await client.call_tool(method, params)
            assert not response.isError, (method, response.structuredContent)
            return response.structuredContent

        async def reject(method, **params):
            response = await client.call_tool(method, params)
            assert response.isError, (method, response.structuredContent)
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
            return {"file": name, "sha256": meta["sha256"], "size": len(data)}

        async def nearest(body, kind, point):
            result = await call("nx_find_geometry", owner=body, kind=kind, near=point)
            assert result["items"]
            return result["items"][0]["object"]["id"]

        async def volume(body):
            return (await call("nx_measure_volume", body=body))["volume_mm3"]

        before = (await call("nx_list_open_parts"))["parts"]
        assert not any(p["modified"] for p in before), "Save existing parts before acceptance"
        original = next(p for p in before if p["work"])
        original_display = next(p for p in before if p["display"])
        try:
            assert len((await client.list_tools()).tools) == int(
                os.environ.get("NX_EXPECTED_TOOL_COUNT", "189")
            )
            catalog = await call("nx_sheet_metal_schema")
            assert len(catalog["operations"]) == 34
            await call("nx_create_part", path=prefix + "/bracket.prt", units="mm")
            await call("nx_sheet_metal_context")
            defaults = await call(
                "nx_set_sheet_metal_defaults", thickness=2, bend_radius=3, neutral_factor=0.33
            )
            assert defaults["parameters"]["thickness"]["value"] == 2
            sketch = (await call("nx_create_sketch"))["object"]["id"]
            await call(
                "nx_sketch_rectangle",
                sketch_id=sketch,
                corner1={"x": 0, "y": 0},
                corner2={"x": 100, "y": 80},
            )
            await call("nx_finish_sketch", sketch_id=sketch)
            tab = await call(
                "nx_sheet_metal_feature",
                operation="tab",
                parameters={"section": sketch, "thickness": 2},
            )
            body = tab["body"]["id"]
            assert math.isclose(await volume(body), 16000, rel_tol=1e-8)
            edge = await nearest(body, "edge", [50, 0, 0])
            token = "sheet-metal-flange-" + uuid.uuid4().hex
            params = {
                "operation": "flange",
                "parameters": {
                    "flanges": [
                        {"edges": [edge], "length": 20, "length_reference": "Inside", "angle": 90}
                    ]
                },
                "operation_id": token,
            }
            flange = await call("nx_sheet_metal_feature", **params)
            again = await call("nx_sheet_metal_feature", **params)
            assert again["feature"]["id"] == flange["feature"]["id"]
            info = (await call("nx_sheet_metal_info", body=body))["items"][0]
            assert info["thickness"] == 2 and info["bend_count"] == 1
            bend = info["bends"][0]
            assert math.isclose(bend["angle_degrees"], 90) and math.isclose(bend["inner_radius"], 3)
            note = await call(
                "nx_sheet_metal_annotation",
                kind="bend",
                body=body,
                faces=[bend["face"]["id"]],
                position=[50, -25, 20],
            )
            assert "90.000 deg" in note["text"][0][1]
            assert any(r["kind"] == "annotation" for r in note["changes"]["created"])
            receipt["checks"].append("tab_analytic_volume_flange_bend_info_retry_pmi")
            save()

            await call("nx_set_view", orientation="isometric")
            await call("nx_fit_view")
            render = await call(
                "nx_render_view", path=prefix + "/bracket.png", style="shaded_with_edges"
            )
            receipt["render"] = await artifact(render, "bracket.png")
            face = await nearest(body, "face", [50, 40, 0])
            xedge = await nearest(body, "edge", [50, 80, 0])
            flat = await call(
                "nx_sheet_metal_feature",
                operation="flat_pattern",
                parameters={"upward_face": face, "x_axis_edge": xedge, "associative": True},
            )
            bodies_before = (await call("nx_get_bounding_box"))["body_count"]
            receipt["exports"] = []
            for fmt in ("dxf", "geo"):
                exported = await call(
                    "nx_export_flat_pattern",
                    flat_pattern=flat["feature"]["id"],
                    path=prefix + "/bracket." + fmt,
                    format=fmt,
                )
                receipt["exports"].append(await artifact(exported, "bracket." + fmt))
            assert (await call("nx_get_bounding_box"))["body_count"] == bodies_before
            length_expression = [e for e in flange["expressions"] if e["value"] == 20]
            assert len(length_expression) == 1, "Fixture must identify length unambiguously"
            await call(
                "nx_set_expression", expression=length_expression[0]["object"]["id"], formula="25"
            )
            updated = await call(
                "nx_export_flat_pattern",
                flat_pattern=flat["feature"]["id"],
                path=prefix + "/bracket-edited.dxf",
            )
            receipt["edited_dxf"] = await artifact(updated, "bracket-edited.dxf")
            assert receipt["edited_dxf"]["sha256"] != receipt["exports"][0]["sha256"]
            # Inside height + thickness = outside height. Subtract two outer
            # setbacks and add the 90-degree neutral-axis bend allowance.
            for filename, inside_height in [("bracket.dxf", 20), ("bracket-edited.dxf", 25)]:
                expected = sorted(
                    [100, 80 + inside_height + 2 - 2 * (3 + 2) + math.pi / 2 * (3 + 0.33 * 2)]
                )
                actual = fixture_dxf_extents(output / filename)
                assert all(
                    math.isclose(a, b, abs_tol=1e-4) for a, b in zip(actual, expected, strict=True)
                ), (actual, expected)
                receipt.setdefault("developed_dimensions", {})[filename] = actual
            drawing = await call("nx_create_drawing", name="Flat", size="A4")
            view = await call(
                "nx_add_flat_pattern_view",
                drawing=drawing["object"]["id"],
                flat_pattern=flat["feature"]["id"],
                position=[148.5, 105],
            )
            receipt["flat_view"] = view
            pdf = await call("nx_export_drawing_pdf", path=prefix + "/bracket.pdf")
            receipt["pdf"] = await artifact(pdf, "bracket.pdf")
            await call("nx_save_part")
            await call("nx_close_part")
            await call("nx_open_part", path=prefix + "/bracket.prt")
            stale = await reject("nx_sheet_metal_info", body=body)
            assert stale["code"] == "NX_OBJECT_STALE"
            assert (await call("nx_model_health"))["healthy"]
            receipt["checks"].append("flat_pattern_dxf_geo_drawing_pdf_reopen_stale")
            save()

            # A fresh part isolates rollback from the flat-pattern derived bodies.
            await call("nx_create_part", path=prefix + "/rollback.prt", units="mm")
            await call("nx_sheet_metal_context")
            checkpoint = await call("nx_checkpoint", label="before_tab")
            sketch = (await call("nx_create_sketch"))["object"]["id"]
            await call(
                "nx_sketch_rectangle",
                sketch_id=sketch,
                corner1={"x": 0, "y": 0},
                corner2={"x": 10, "y": 10},
            )
            await call("nx_finish_sketch", sketch_id=sketch)
            tab = await call(
                "nx_sheet_metal_feature",
                operation="tab",
                parameters={"section": sketch, "thickness": 2},
            )
            before_volume = await volume(tab["body"]["id"])
            await reject(
                "nx_sheet_metal_feature",
                operation="tab",
                feature=tab["feature"]["id"],
                parameters={"unsupported": 42},
            )
            # A failed mutation invalidates references even when it rolls back.
            fresh = (await call("nx_sheet_metal_info"))["items"][0]["body"]["id"]
            assert await volume(fresh) == before_volume
            await call("nx_rollback", checkpoint_id=checkpoint["checkpoint_id"])
            assert not (await call("nx_sheet_metal_info"))["items"]
            receipt["checks"].append("unsupported_edit_unchanged_checkpoint_rollback")
            await call("nx_create_part", path=prefix + "/attached.prt", units="mm")
            await call("nx_sheet_metal_context")
            sketch = (await call("nx_create_sketch"))["object"]["id"]
            await call(
                "nx_sketch_rectangle",
                sketch_id=sketch,
                corner1={"x": 0, "y": 0},
                corner2={"x": 10, "y": 10},
            )
            await call("nx_finish_sketch", sketch_id=sketch)
            tab = await call(
                "nx_sheet_metal_feature",
                operation="tab",
                parameters={"section": sketch, "thickness": 2},
            )
            body = tab["body"]["id"]
            edge = await nearest(body, "edge", [5, 0, 0])
            face = await nearest(body, "face", [5, 5, 0])
            path_sketch = await call(
                "nx_create_path_sketch",
                edges=[edge],
                help_point=[5, 0, 0],
                percent=0,
                orienting_face=face,
            )
            frame = path_sketch["frame"]
            assert math.isclose(abs(frame["normal"][0]), 1)
            delta = [0, -5, 0]
            end = {
                k: sum(a * b for a, b in zip(delta, frame[axis], strict=True))
                for k, axis in [("x", "x_axis"), ("y", "y_axis")]
            }
            sketch = path_sketch["object"]["id"]
            await call("nx_sketch_line", sketch_id=sketch, start={"x": 0, "y": 0}, end=end)
            await call("nx_finish_sketch", sketch_id=sketch)
            # Re-resolve topology after sketch mutations.
            edge = await nearest(body, "edge", [5, 0, 0])
            attached = await call(
                "nx_sheet_metal_feature",
                operation="contour_flange",
                parameters={
                    "section": sketch,
                    "edge_chain": {"edges": [edge], "help_point": [5, 0, 0]},
                    "is_secondary": True,
                    "thickness": 2,
                    "sweep_distance": 10,
                },
            )
            assert math.isclose(await volume(attached["body"]["id"]), 300, rel_tol=1e-7)
            assert (await call("nx_model_health"))["healthy"]
            receipt["checks"].append("path_sketch_secondary_contour_analytic_volume")
            receipt["passed"] = True
        except Exception:
            receipt["passed"] = False
            receipt["error"] = traceback.format_exc()
            raise
        finally:
            try:
                await call("nx_open_part", path=original["path"], work=True, display=True)
                for part in (await call("nx_list_open_parts"))["parts"]:
                    if prefix in part["path"]:
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
