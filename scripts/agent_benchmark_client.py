"""Transparent MCP CLI for isolated agent trials; records every explicit request/response."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(args):
    url = args.url.rstrip("/") + ("/agent/mcp" if args.profile == "agent" else "/mcp")
    async with streamablehttp_client(url) as (r, w, _), ClientSession(r, w) as client:
        await client.initialize()

        def log(entry):
            args.log.parent.mkdir(parents=True, exist_ok=True)
            with args.log.open("a") as stream:
                stream.write(json.dumps(entry) + "\n")

        async def call(name, params):
            started = time.monotonic()
            log({"event": "request", "tool": name, "arguments": params})
            result = await client.call_tool(name, params)
            value = result.structuredContent
            log(
                {
                    "event": "response",
                    "tool": name,
                    "result": value,
                    "error": bool(result.isError),
                    "seconds": time.monotonic() - started,
                    "non_text_content": [c.type for c in result.content if c.type != "text"],
                }
            )
            return value

        if args.action == "list":
            catalog = (await client.list_tools()).tools
            value = {"tools": [t.model_dump(exclude_none=True) for t in catalog]}
            log({"event": "catalog", "profile": args.profile, "presented": value})
        elif args.action == "discover":
            if args.profile == "agent":
                value = await call(
                    "nx_discover_tools", {"query": args.tool, "include_schema": True}
                )
            else:
                started = time.monotonic()
                catalog = (await client.list_tools()).tools
                selected = [
                    t
                    for t in catalog
                    if args.tool.casefold() in (t.name + " " + t.description).casefold()
                ]
                exact = [t for t in selected if t.name == args.tool]
                value = {
                    "tools": [t.model_dump(exclude_none=True) for t in (exact or selected)[:10]],
                    "matching_count": len(exact or selected),
                }
                log(
                    {
                        "event": "discovery",
                        "profile": "full",
                        "catalog_count": len(catalog),
                        "query": args.tool,
                        "presented": value,
                        "seconds": time.monotonic() - started,
                    }
                )
        else:
            params = json.loads(args.arguments)
            if args.profile == "agent" and args.tool not in {
                "nx_inspect",
                "nx_result",
                "nx_result_cleanup",
                "nx_discover_tools",
            }:
                value = await call("nx_invoke", {"tool": args.tool, "arguments": params})
            else:
                value = await call(args.tool, params)
        print(json.dumps(value, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["full", "agent"], required=True)
    parser.add_argument("--url", default="http://192.168.52.10:8765")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("action", choices=["discover", "call", "list"])
    parser.add_argument("tool", nargs="?", default="")
    parser.add_argument("arguments", nargs="?", default="{}")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
