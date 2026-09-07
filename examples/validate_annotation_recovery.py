"""Follow the documentation suite with automatic table, retry, opt-out and rollback checks."""

import asyncio
import json
import os
import traceback
import uuid
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

out = Path(os.environ.get("NX_VALIDATION_OUTPUT", "documentation-manufacturing-results"))
fixture = json.loads((out / "documentation-manufacturing-validation.json").read_text())["fixture"]


async def main():
    result = {}
    async with (
        streamablehttp_client(os.environ["NX_MCP_URL"]) as (r, w, _),
        ClientSession(r, w) as c,
    ):
        await c.initialize()

        async def call(n, **p):
            v = await c.call_tool(n, p)
            assert not v.isError, (n, v.structuredContent)
            return v.structuredContent

        before = (await call("nx_list_open_parts"))["parts"]
        original = next(p for p in before if p["work"])
        original_display = next(p for p in before if p["display"])
        assert fixture.startswith("documentation-validation-")
        assert not any(p["modified"] or fixture in p["path"] for p in before), (
            "Save user parts and close prior validation fixtures first"
        )
        checkpoint = None
        try:
            await call("nx_open_part", path=fixture + "/bracket.prt")
            await call("nx_activate_drawing")
            checkpoint = await call("nx_checkpoint", label="automatic annotation acceptance")
            expressions = (await call("nx_list_expressions", limit=200))["items"]
            angles = [x for x in expressions if x["units"] == "°" and x["value"] == 80]
            assert len(angles) == 1
            token = "annotation-refresh-" + uuid.uuid4().hex
            params = {
                "expression": angles[0]["object"]["id"],
                "formula": "85",
                "operation_id": token,
            }
            first = await call("nx_set_expression", **params)
            again = await call("nx_set_expression", **params)
            assert (
                first["refreshed_annotations"] == again["refreshed_annotations"]
                and again["replayed"]
            )
            sheets = (await call("nx_list_drawings"))["sheets"]
            await call("nx_activate_drawing", drawing=sheets[0]["object"]["id"])
            notes = (await call("nx_list_annotations"))["items"]
            table = next(x for x in notes if x["native_type"] == "BendTable")
            assert any("85,00" in row or "85.00" in row for row in table["rows"]), table
            pmi = next(x for x in notes if x.get("managed_refresh"))
            assert "85.000 deg" in " ".join(pmi["text"])
            result["automatic_without_table_edit"] = True
            result["operation_retry_deduplicated"] = True
            result["table_rows"] = table["rows"]
            await call("nx_activate_drawing")
            body = (await call("nx_list_bodies"))["objects"][0]["id"]
            info = (await call("nx_sheet_metal_info", body=body))["items"][0]
            await call(
                "nx_sheet_metal_annotation",
                kind="bend",
                body=body,
                faces=[info["bends"][0]["face"]["id"]],
                position=pmi["position"],
                annotation=pmi["object"]["id"],
                automatic=False,
            )
            edit = await call(
                "nx_set_expression", expression=angles[0]["object"]["id"], formula="82"
            )
            assert all(
                x["id"] != pmi["object"]["id"] for x in edit.get("refreshed_annotations", [])
            ), "Disabled PMI was refreshed"
            notes = (await call("nx_list_annotations"))["items"]
            assert any(
                "85.000 deg" in " ".join(x.get("text", [])) and x.get("managed_refresh") is False
                for x in notes
            )
            result["disable_preserves_measured_snapshot"] = True
            await call("nx_rollback", checkpoint_id=checkpoint["checkpoint_id"])
            checkpoint = None
            notes = (await call("nx_list_annotations"))["items"]
            assert any(
                "80.000 deg" in " ".join(x.get("text", [])) and x.get("managed_refresh")
                for x in notes
            )
            result["rollback_restored_annotation_and_source"] = True
            result["passed"] = True
        except Exception:
            result["error"] = traceback.format_exc()
            raise
        finally:
            if checkpoint:
                await call("nx_rollback", checkpoint_id=checkpoint["checkpoint_id"])
            await call("nx_open_part", path=original["path"])
            parts = (await call("nx_list_open_parts"))["parts"]
            for p in parts:
                if fixture in p["path"]:
                    await call("nx_close_part", part=p["part"]["id"], save=True)
            if original_display["path"] != original["path"]:
                await call("nx_open_part", path=original_display["path"], work=False, display=True)
            after = (await call("nx_list_open_parts"))["parts"]
            assert {p["path"] for p in before} == {p["path"] for p in after}
            result["session_restored"] = True
            (out / "refresh-check.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
