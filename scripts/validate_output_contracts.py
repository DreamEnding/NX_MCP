"""Validate fresh MCP response schemas on the saved active assembly, read-only."""

import argparse
import asyncio
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def validate(url, output):
    report = {"checks": [], "passed": False}
    async with streamablehttp_client(url) as (read, write, _), ClientSession(read, write) as client:
        await client.initialize()
        tools = {t.name: t for t in (await client.list_tools()).tools}
        for tool in tools.values():
            assert tool.outputSchema, tool.name
            Draft202012Validator.check_schema(tool.outputSchema)
        report["tool_count"] = len(tools)
        report["typed_payload_count"] = sum(
            "Tool-specific payload is typed." in t.outputSchema.get("description", "")
            for t in tools.values()
        )

        async def call(name, params=None, error=False):
            result = await client.call_tool(name, params or {})
            assert bool(result.isError) == error, (name, result.structuredContent)
            Draft202012Validator(tools[name].outputSchema).validate(result.structuredContent)
            report["checks"].append(name + (":error" if error else ":success"))
            return result.structuredContent

        before = await call("nx_list_open_parts")
        try:
            await call("nx_status")
            components = (await call("nx_list_components"))["components"]
            await call("nx_get_bounding_box")
            await call("nx_measure_volume")
            if len(components) >= 2:
                pair = {
                    "obj1": components[0]["object"]["id"],
                    "obj2": components[1]["object"]["id"],
                }
                await call("nx_measure_distance", pair)
                await call("nx_check_interference", pair)
            await call("nx_checkpoint_state")
            await call("nx_operation_status", {"operation_id": "contract_unknown_20260906"})
            await call("nx_workspace_list", {"limit": 2})
            await call(
                "nx_workspace_list", {"path": "contract-absent-directory-20260906"}, error=True
            )
            await call("nx_revolve", error=True)
            report["passed"] = True
        finally:
            after = await call("nx_list_open_parts")
            fields = ("path", "work", "display", "modified")

            def project(r):
                return sorted(tuple(p[k] for k in fields) for p in r["parts"])

            report["session_preserved"] = project(before) == project(after)
            report["passed"] = report["passed"] and report["session_preserved"]
            output.write_text(json.dumps(report, indent=2) + "\n")
    assert report["passed"], report
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(validate(args.url, args.output))
