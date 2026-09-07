"""Live folder-path acceptance on disposable NX parts; preserves the loaded session."""

import asyncio
import base64
import hashlib
import json
import os
import uuid
from pathlib import Path, PureWindowsPath

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

out = Path(os.environ.get("NX_VALIDATION_OUTPUT", "folder-validation-output"))


async def main():
    out.mkdir(parents=True, exist_ok=True)
    async with (
        streamablehttp_client(os.environ["NX_MCP_URL"]) as (r, w, _),
        ClientSession(r, w) as c,
    ):
        await c.initialize()

        async def call(name, **p):
            response = await c.call_tool(name, p)
            if response.isError:
                raise RuntimeError((name, response.structuredContent))
            return response.structuredContent

        async def rejected(name, **p):
            response = await c.call_tool(name, p)
            assert response.isError, (name, response)
            return response.structuredContent

        before = await call("nx_list_open_parts")
        assert not any(p["modified"] for p in before["parts"]), (
            "Save the current session before this acceptance run"
        )
        active = next(p for p in before["parts"] if p["work"])
        checks = []
        prefix = "folder-validation-" + uuid.uuid4().hex[:10]
        try:
            assert len((await c.list_tools()).tools) == int(
                os.environ.get("NX_EXPECTED_TOOL_COUNT", "189")
            )
            info = await call("nx_workspace_info")
            root = PureWindowsPath(info["root"])

            def absolute(rel):
                return str(root / rel)

            checks.append({"name": "workspace_discovery", "passed": True})
            directory = prefix + "/exports"
            assert (await call("nx_create_directory", path=absolute(directory)))["created"]
            assert not (await call("nx_create_directory", path=directory))["created"]
            checks.append({"name": "directory_creation_absolute_relative_retry", "passed": True})
            source = prefix + "/parts/folder_base.prt"
            revision = prefix + "/revisions/r02/folder_base_r02.prt"
            await call("nx_create_part", path=source, units="mm")
            sk = (await call("nx_create_sketch"))["object"]["id"]
            await call(
                "nx_sketch_rectangle",
                sketch_id=sk,
                corner1={"x": 0, "y": 0},
                corner2={"x": 10, "y": 10},
            )
            await call("nx_finish_sketch", sketch_id=sk)
            await call("nx_extrude", sketch_id=sk, distance=10)
            volume = await call("nx_measure_volume")
            assert abs(volume["volume_mm3"] - 1000) < 1e-5, volume
            await call("nx_save_part")
            original = (await call("nx_workspace_list", path=prefix + "/parts"))["entries"][0]
            checks.append({"name": "nested_part_creation_save_1000mm3", "passed": True})
            saved = await call("nx_save_as", path=absolute(revision))
            assert PureWindowsPath(saved["path"]) == root / revision
            assert abs((await call("nx_measure_volume"))["volume_mm3"] - 1000) < 1e-5
            checks.append(
                {
                    "name": "absolute_save_as_creates_missing_parents_preserves_volume",
                    "passed": True,
                }
            )
            await rejected("nx_save_as", path=source)
            assert (await call("nx_workspace_list", path=prefix + "/parts"))["entries"][0][
                "sha256"
            ] == original["sha256"]
            checks.append({"name": "save_as_no_overwrite", "passed": True})
            await call("nx_close_part", save=False)
            reopened = await call("nx_open_part", path=absolute(revision))
            assert not reopened["already_loaded"]
            assert (await call("nx_open_part", path=revision))["already_loaded"]
            assert abs((await call("nx_measure_volume"))["volume_mm3"] - 1000) < 1e-5
            await call("nx_open_part", path=absolute(source))
            assert (await call("nx_open_part", path=absolute(revision)))["already_loaded"]
            checks.append({"name": "absolute_reopen_relative_reuse_and_activation", "passed": True})
            await call("nx_export_step", path=absolute(directory + "/folder_base.step"))
            listing = await call("nx_workspace_list", path=directory)
            assert any(
                PureWindowsPath(e["path"]).name == "folder_base.step" for e in listing["entries"]
            )
            artifact = await call(
                "nx_download_file", path=absolute(directory + "/folder_base.step")
            )
            data = base64.b64decode(artifact["data_base64"])
            assert artifact["eof"] and hashlib.sha256(data).hexdigest() == artifact["sha256"]
            copied = await call(
                "nx_upload_file",
                path=absolute(prefix + "/vendor/copy.step"),
                data_base64=artifact["data_base64"],
                sha256=artifact["sha256"],
                total_size=len(data),
            )
            assert copied["committed"]
            checks.append(
                {
                    "name": "nested_absolute_step_export_download_upload_integrity",
                    "passed": True,
                }
            )
            for path in [
                "../outside.prt",
                str(root.parent / "outside.prt"),
                str(root / ".NX-MCP" / "state.json"),
            ]:
                await rejected("nx_open_part", path=path)
            checks.append({"name": "outside_and_reserved_paths_rejected", "passed": True})
        finally:
            parts = (await call("nx_list_open_parts"))["parts"]
            for p in parts:
                if prefix in p["path"]:
                    await call("nx_close_part", part=p["part"]["id"], save=False)
            await call("nx_open_part", path=active["path"])
            after = await call("nx_list_open_parts")

            def norm(p):
                return str(PureWindowsPath(p["path"])).casefold()

            assert sorted(map(norm, before["parts"])) == sorted(map(norm, after["parts"]))
            assert not any(p["modified"] for p in after["parts"])
            (out / "path-validation.json").write_text(
                json.dumps(
                    {
                        "checks": checks,
                        "passed": len(checks),
                        "fixture": prefix,
                        "restored_parts": len(after["parts"]),
                    },
                    indent=2,
                )
            )
        print(json.dumps({"passed": len(checks), "restored_parts": len(after["parts"])}))


asyncio.run(main())
