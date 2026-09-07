"""NX 2606 legacy-tool acceptance on disposable fixtures.

Set NX_MCP_URL and NX_VALIDATION_OUTPUT; defaults target loopback and ./native-results.
Original parts must be saved. Records every response, including failed attempts.
"""

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import uuid
import zipfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    prefix = "validation/remaining-" + uuid.uuid4().hex[:8]
    log = []
    output = Path(os.environ.get("NX_VALIDATION_OUTPUT", "native-results"))
    output.mkdir(parents=True, exist_ok=True)
    async with (
        streamablehttp_client(os.environ.get("NX_MCP_URL", "http://127.0.0.1:8765/mcp")) as (
            r,
            w,
            _,
        ),
        ClientSession(r, w) as c,
    ):
        await c.initialize()

        async def call(n, **p):
            v = await c.call_tool(n, p)
            d = v.structuredContent
            if d is None:
                d = json.loads(v.content[0].text)
            log.append({"tool": n, "arguments": p, "error": bool(v.isError), "result": d})
            (output / "remaining-tools.json").write_text(json.dumps(log, indent=2))
            if v.isError and n != "nx_cancel_operation":
                raise AssertionError((n, d))
            return d

        before = (await call("nx_list_open_parts", limit=100))["parts"]
        assert not any(x["modified"] for x in before)
        original = next(x for x in before if x["work"])
        created = []
        try:
            status = await call("nx_status")
            assert (
                status["connected"]
                and status["ui"]["main_thread_id"] == status["ui"]["callback_thread_id"]
            )
            capabilities = await call("nx_capabilities", tool="nx_status")
            assert set(capabilities["tools"]) == {"nx_status"}
            d = await call("nx_create_part", path=prefix + "/probe.prt", units="mm")
            created.append(d["part"]["id"])
            s = (await call("nx_create_sketch"))["object"]["id"]
            a = await call(
                "nx_sketch_line", sketch_id=s, start={"x": 0, "y": 0}, end={"x": 10, "y": 0}
            )
            b = await call(
                "nx_sketch_line", sketch_id=s, start={"x": 0, "y": 0}, end={"x": 0, "y": 10}
            )
            info = await call("nx_sketch_info", sketch_id=s)
            assert info["frame"]["normal"] == [0, 0, 1] and len(info["curves"]) == 2
            angle = await call("nx_measure_angle", obj1=a["object"]["id"], obj2=b["object"]["id"])
            assert math.isclose(angle["angle_deg"], 90, abs_tol=1e-8) and angle["units"] == "deg"
            constraint = await call(
                "nx_sketch_constraint", constraint_type="horizontal", targets=[a["object"]["id"]]
            )
            assert constraint["edit_count"] == 1
            await call("nx_finish_sketch", sketch_id=s)
            s2 = (await call("nx_create_sketch"))["object"]["id"]
            await call(
                "nx_sketch_rectangle",
                sketch_id=s2,
                corner1={"x": 0, "y": 0},
                corner2={"x": 10, "y": 10},
            )
            await call("nx_finish_sketch", sketch_id=s2)
            e = await call("nx_extrude", sketch_id=s2, distance=10)
            bodies = await call("nx_list_bodies")
            assert len(bodies["objects"]) == 1
            fs = await call("nx_list_features")
            assert len(fs["objects"]) >= 3
            sketches = await call("nx_list_sketches")
            assert len(sketches["objects"]) == 2
            feature = e["feature"]["id"]
            await call("nx_rename_object", object_id=feature, name="ProbeExtrusion")
            for v in ["Top", "Back", "Isometric"]:
                view = await call("nx_set_view", orientation=v)
                assert view["orientation"] == v.lower() and view["viewport_available"]
            await call("nx_fit_view")
            await call("nx_save_part")
            await call("nx_save_as", path=prefix + "/nested/copy.prt")
            page = await call("nx_workspace_list", path=prefix, limit=1)
            assert page["count"] == 1 and page["next_offset"] == 1 and page["total_count"] == 2
            feature = next(
                f["id"]
                for f in (await call("nx_list_features"))["objects"]
                if f["name"] == "ProbeExtrusion"
            )
            deleted = await call("nx_delete_feature", name=feature)
            assert deleted["deleted"]
            assert not (await call("nx_list_bodies"))["objects"]
            await call("nx_save_part")
            # Use original saved cube as a component prototype.
            d = await call("nx_create_part", path=prefix + "/assembly.prt", units="mm")
            created.append(d["part"]["id"])
            comp = (await call("nx_add_component", part_path=prefix + "/probe.prt", name="Cube"))[
                "object"
            ]["id"]
            oid = "remaining-relative-" + uuid.uuid4().hex
            await call("nx_reposition_component", component=comp, dx=20, rz=90, operation_id=oid)
            retry = await call(
                "nx_reposition_component", component=comp, dx=20, rz=90, operation_id=oid
            )
            assert retry["replayed"]
            pose = (await call("nx_list_components"))["components"][0]
            assert pose["translation"] == [20, 0, 0]
            expected_rotation = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
            assert all(
                math.isclose(a, b, abs_tol=1e-8)
                for actual, expected in zip(pose["rotation_matrix"], expected_rotation, strict=True)
                for a, b in zip(actual, expected, strict=True)
            )
            ex = (await call("nx_create_explosion", name="Disposable"))["object"]["id"]
            await call("nx_delete_explosion", explosion=ex)
            assert not (await call("nx_list_explosions"))["explosions"]
            await call("nx_save_part")
            await call("nx_package_assembly", path=prefix + "/assembly.zip")
            chunk = await call("nx_download_file", path=prefix + "/assembly.zip")
            assert chunk["eof"]
            data = base64.b64decode(chunk["data_base64"])
            assert hashlib.sha256(data).hexdigest() == chunk["sha256"]
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                assert (
                    len([n for n in z.namelist() if n.endswith(".prt")]) == 2
                    and z.testzip() is None
                )
            cancel = await call("nx_cancel_operation", operation_id="remaining-not-running")
            assert cancel["code"] == "NX_NOT_CANCELLABLE"
        finally:
            now = (await call("nx_list_open_parts", limit=100))["parts"]
            for x in reversed(now):
                if "/" + prefix + "/" in x["path"].replace("\\", "/"):
                    await call("nx_close_part", part=x["part"]["id"], save=False)
            await call("nx_activate_part", part=original["part"]["id"], work=True, display=True)
            after = (await call("nx_list_open_parts", limit=100))["parts"]
            assert len(after) == len(before) and not any(x["modified"] for x in after)
        print(
            "Completed; original session restored; expected rejection:",
            [x["tool"] for x in log if x["error"]],
        )


asyncio.run(main())
