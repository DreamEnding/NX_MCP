"""Download NX artifact bytes directly to disk, outside an agent's textual context."""

import argparse
import asyncio
import base64
import hashlib
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def download(url, remote, output):
    async with streamablehttp_client(url) as (r, w, _), ClientSession(r, w) as client:
        await client.initialize()
        names = {t.name for t in (await client.list_tools()).tools}

        async def call(arguments):
            result = (
                await client.call_tool(
                    "nx_invoke",
                    {"tool": "nx_download_file", "arguments": arguments, "detail": "full"},
                )
                if "nx_invoke" in names
                else await client.call_tool("nx_download_file", arguments)
            )
            if result.isError:
                raise RuntimeError(result.structuredContent)
            return result.structuredContent

        meta = await call({"path": remote, "delivery": "metadata"})
        temporary = output.with_suffix(output.suffix + ".partial")
        digest = hashlib.sha256()
        offset = 0
        # Exclusive temporary file prevents accidental overwrite or concurrent downloader races.
        if output.exists():
            raise FileExistsError(output)
        with temporary.open("xb") as stream:
            while True:
                chunk = await call({"path": remote, "delivery": "base64", "offset": offset})
                if chunk["sha256"] != meta["sha256"]:
                    raise RuntimeError("Artifact changed during transfer")
                data = base64.b64decode(chunk["data_base64"], validate=True)
                stream.write(data)
                digest.update(data)
                offset += len(data)
                if chunk["eof"]:
                    break
                if not data:
                    raise RuntimeError("Download made no progress")
        if offset != meta["size"] or digest.hexdigest() != meta["sha256"]:
            raise RuntimeError("Artifact checksum/size mismatch")
        temporary.rename(output)
        print(f"Downloaded {offset} bytes to {output}; SHA-256 {digest.hexdigest()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8765/agent/mcp")
    args = parser.parse_args()
    asyncio.run(download(args.url, args.path, args.output))


if __name__ == "__main__":
    main()
