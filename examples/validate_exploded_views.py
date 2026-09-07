"""Public MCP native explosion acceptance; preserves the initially saved session.

Set NX_MCP_URL and optionally NX_VALIDATION_OUTPUT. Tests use isolated parts.
"""

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


async def main():
    output = Path(os.environ.get("NX_VALIDATION_OUTPUT", "explosion-results"))
    output.mkdir(parents=True, exist_ok=True)
    prefix = "explosion-validation-" + uuid.uuid4().hex[:8]
    receipt = {"fixture": prefix, "groups": []}
    async with (
        streamablehttp_client(os.environ["NX_MCP_URL"]) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        async def call(method, **params):
            response = await client.call_tool(method, params)
            assert not response.isError, (method, response.structuredContent)
            return response.structuredContent

        async def rejected(method, **params):
            response = await client.call_tool(method, params)
            assert response.isError, (method, response.structuredContent)
            return response.structuredContent

        async def new(name):
            await call("nx_create_part", path=f"{prefix}/{name}.prt", units="mm")

        async def work_path():
            return next(p["path"] for p in (await call("nx_list_open_parts"))["parts"] if p["work"])

        async def poses(ex):
            info = await call("nx_explosion_info", explosion=ex, limit=200)
            return {i["occurrence_path"][-1]: i for i in info["items"]}

        async def artifact(meta, name):
            data = bytearray()
            while True:
                chunk = await call("nx_download_file", path=meta["path"], offset=len(data))
                data.extend(base64.b64decode(chunk["data_base64"]))
                if chunk["eof"]:
                    break
            assert hashlib.sha256(data).hexdigest() == meta["sha256"]
            (output / name).write_bytes(data)

        before = (await call("nx_list_open_parts"))["parts"]
        assert not any(p["modified"] for p in before), "Save original parts before acceptance"
        work = next((p for p in before if p["work"]), None)
        display = next((p for p in before if p["display"]), None)
        try:
            await new("proto")
            s = (await call("nx_create_sketch"))["object"]["id"]
            await call(
                "nx_sketch_rectangle",
                sketch_id=s,
                corner1={"x": 0, "y": 0},
                corner2={"x": 10, "y": 10},
            )
            await call("nx_finish_sketch", sketch_id=s)
            await call("nx_extrude", sketch_id=s, distance=10)
            await call("nx_save_part")
            source = await work_path()
            await new("sub")
            await call("nx_add_component", part_path=source, name="leaf", translation=[20, 0, 0])
            await call("nx_save_part")
            sub = await work_path()
            await new("assembly")
            await call("nx_add_component", part_path=source, name="base")
            parent = (
                await call(
                    "nx_add_component",
                    part_path=sub,
                    name="parent",
                    translation=[10, 20, 30],
                    rotation_matrix=[[0, -1, 0], [1, 0, 0], [0, 0, 1]],
                )
            )["object"]["id"]
            await call("nx_add_component", part_path=source, name="cap", translation=[40, 0, 0])
            assembled = (await call("nx_list_components"))["components"]
            ex = (await call("nx_create_explosion", name="Service"))["object"]["id"]
            items = await poses(ex)
            leaf, cap = [items[n]["component"]["id"] for n in ("LEAF", "CAP")]
            targets = [
                {
                    "component": leaf,
                    "translation": [70, 60, 50],
                    "rotation_matrix": [[-1, 0, 0], [0, -1, 0], [0, 0, 1]],
                },
                {"component": parent, "translation": [50, 20, 30]},
                {"component": cap, "translation": [100, 0, 50]},
            ]
            token = "explosion-" + uuid.uuid4().hex
            first = await call(
                "nx_edit_explosion", explosion=ex, placements=targets, operation_id=token
            )
            repeat = await call(
                "nx_edit_explosion", explosion=ex, placements=targets, operation_id=token
            )
            assert repeat["operation_id"] == first["operation_id"]
            assert (await call("nx_edit_explosion", explosion=ex, placements=targets))[
                "affected_component_count"
            ] == 0
            items = await poses(ex)
            assert items["LEAF"]["translation"] == [70, 60, 50]
            assert items["PARENT"]["translation"] == [50, 20, 30]
            await call("nx_edit_explosion", explosion=ex, reset_components=[leaf])
            assert (await poses(ex))["LEAF"]["translation"] == [50, 40, 30]
            await call("nx_edit_explosion", explosion=ex, placements=[targets[0]])
            receipt["groups"].append({"name": "absolute_nested_poses_reset_retry", "passed": True})

            await call("nx_show_explosion", explosion=ex)
            png = await call("nx_screenshot", path=prefix + "/exploded.png")
            await artifact(png, "exploded.png")
            sheet = (await call("nx_create_drawing", name="ServiceSheet", size="A3", scale=1))[
                "object"
            ]["id"]
            await call(
                "nx_add_base_view",
                drawing=sheet,
                scope="assembly",
                view="isometric",
                position=[70, 100],
            )
            view = await call(
                "nx_add_base_view",
                drawing=sheet,
                scope="assembly",
                view="isometric",
                position=[200, 100],
                explosion=ex,
            )
            await call(
                "nx_add_projection_view",
                base_view=view["object"]["id"],
                direction="right",
                spacing=80,
            )
            edit = await call(
                "nx_edit_explosion",
                explosion=ex,
                placements=[{"component": cap, "translation": [120, 0, 50]}],
            )
            assert len(edit["updated_drawing_views"]) == 2
            pdf = await call("nx_export_drawing_pdf", path=prefix + "/exploded.pdf")
            await artifact(pdf, "exploded.pdf")
            await call("nx_show_explosion", explosion=ex)
            await call("nx_save_part")
            assert not next(
                p["modified"] for p in (await call("nx_list_open_parts"))["parts"] if p["work"]
            )
            await call("nx_save_as", path=prefix + "/assembly_saved_as.prt")
            saved_path = await work_path()
            await call("nx_close_part", save=True)
            await call("nx_open_part", path=saved_path)
            await rejected("nx_explosion_info", explosion=ex)
            ex = (await call("nx_list_explosions"))["explosions"][0]["object"]["id"]
            items = await poses(ex)
            assert items["LEAF"]["translation"] == [70, 60, 50]
            assert items["CAP"]["translation"] == [120, 0, 50]
            fields = ("name", "part_path", "translation", "rotation_matrix")
            after = (await call("nx_list_components"))["components"]
            assert [{k: c[k] for k in fields} for c in assembled] == [
                {k: c[k] for k in fields} for c in after
            ]
            receipt["groups"].append(
                {"name": "views_pdf_save_as_reopen", "passed": True, "png": png, "pdf": pdf}
            )

            await rejected("nx_delete_explosion", explosion=ex)
            ex = (await call("nx_list_explosions"))["explosions"][0]["object"]["id"]
            initial = await poses(ex)
            cap = initial["CAP"]["component"]["id"]
            await rejected(
                "nx_edit_explosion",
                explosion=ex,
                placements=[
                    {"component": cap, "translation": [300, 0, 0]},
                    {"component": cap, "translation": [400, 0, 0]},
                ],
            )
            ex = (await call("nx_list_explosions"))["explosions"][0]["object"]["id"]
            assert (await poses(ex))["CAP"]["translation"] == [120, 0, 50]
            checkpoint = await call("nx_checkpoint", label="explosion before edit")
            cap = (await poses(ex))["CAP"]["component"]["id"]
            await call(
                "nx_edit_explosion",
                explosion=ex,
                placements=[{"component": cap, "translation": [300, 0, 0]}],
            )
            await call("nx_rollback", checkpoint_id=checkpoint["checkpoint_id"])
            ex = (await call("nx_list_explosions"))["explosions"][0]["object"]["id"]
            assert (await poses(ex))["CAP"]["translation"] == [120, 0, 50]
            views = (await call("nx_explosion_info", explosion=ex))["views"]
            for v in views:
                await call(
                    "nx_show_explosion",
                    **{
                        "drawing_view" if v["kind"] == "drawing" else "model_view": v["object"][
                            "id"
                        ]
                    },
                )
            await call("nx_delete_explosion", explosion=ex)
            await rejected("nx_explosion_info", explosion=ex)
            assert not (await call("nx_list_explosions"))["explosions"]
            receipt["groups"].append(
                {"name": "preflight_rollback_view_guard_delete_stale", "passed": True}
            )
        except Exception:
            receipt["groups"].append(
                {"name": "failure", "passed": False, "error": traceback.format_exc()}
            )
        finally:
            for p in (await call("nx_list_open_parts"))["parts"]:
                if prefix in p["path"]:
                    await call("nx_close_part", part=p["part"]["id"], save=False)
            if display:
                await call("nx_open_part", path=display["path"])
            if work:
                await call(
                    "nx_activate_part", part=work["part"]["id"], work=True, display=work == display
                )
            after = (await call("nx_list_open_parts"))["parts"]
            assert sorted(p["path"].casefold() for p in before) == sorted(
                p["path"].casefold() for p in after
            )
            assert not any(p["modified"] for p in after)
            receipt["restored_parts"] = len(after)
            (output / "explosion-validation.json").write_text(json.dumps(receipt, indent=2))
        assert all(g["passed"] for g in receipt["groups"]), receipt
        print("PASS", len(receipt["groups"]), "groups; restored", len(after), "parts")


if __name__ == "__main__":
    asyncio.run(main())
