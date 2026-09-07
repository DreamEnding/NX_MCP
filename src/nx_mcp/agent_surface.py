"""Opt-in bounded agent surface; the full registry remains the validation authority."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote, unquote

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, ResourceLink, TextContent, ToolAnnotations
from pydantic import Field

from nx_mcp.agent_guidance import guidance, next_actions
from nx_mcp.result_retention import LOCK, maintain, settings

CORE = {
    "nx_status",
    "nx_workspace_info",
    "nx_operation_status",
    "nx_cancel_operation",
    "nx_list_open_parts",
    "nx_list_components",
    "nx_screenshot",
    "nx_download_file",
}
DOMAINS = {
    "sketch": ("sketch", "constraint"),
    "assembly": ("component", "assembly", "explosion", "reference_set", "mate"),
    "drawing": ("drawing", "balloon", "bom", "pmi", "annotation"),
    "manufacturing": ("sheet_metal", "flat_pattern", "thread", "draft_analysis", "thickness"),
    "inspection": ("measure", "bounding", "interference", "collision", "topology", "diagnostic"),
    "display": ("view", "display", "visibility", "section", "render", "screenshot", "datum"),
    "files": (
        "part",
        "workspace",
        "directory",
        "upload",
        "download",
        "export",
        "import",
        "package",
    ),
}
DEFAULTS: dict[str, dict[str, Any]] = {
    "nx_list_open_parts": {"compact": True, "limit": 20},
    "nx_list_components": {"compact": True, "include_transforms": False, "limit": 20},
    "nx_list_topology": {"compact": True},
    "nx_download_file": {"delivery": "metadata"},
    "nx_workspace_list": {"limit": 20},
}


def category(name):
    return next(
        (key for key, words in DOMAINS.items() if any(w in name for w in words)), "modeling"
    )


def compact(value, path="", omitted=None, paged_keys=()):
    """Preserve scalars, coordinate vectors, identities and all warnings; bound other arrays."""
    omitted = {} if omitted is None else omitted
    if isinstance(value, dict):
        if "id" in value and "kind" in value:
            return {
                k: v
                for k, v in value.items()
                if k in {"id", "kind", "name", "part_id", "occurrence_path"}
            }
        result = {}
        for key, item in value.items():
            location = f"{path}/{key}"
            if key == "data_base64":
                omitted[location] = "binary omitted; use resources/read or a programmatic download"
            elif key in {"warnings", "retry_guidance", "recovery"}:
                result[key] = item
            else:
                result[key] = (
                    [compact(row, f"{location}/{i}", omitted) for i, row in enumerate(item)]
                    if key in paged_keys and isinstance(item, list)
                    else compact(item, location, omitted)
                )
        return result
    if isinstance(value, list):
        if len(value) > 20:
            omitted[path] = {"total_count": len(value), "returned_count": 20}
        return [compact(item, f"{path}/{i}", omitted) for i, item in enumerate(value[:20])]
    return value


def compact_payload(full, result_id, method=None):
    if full.get("status") == "error":
        return full
    omitted: dict[str, Any] = {}
    # A backend page is already bounded by its requested limit. Never truncate its
    # rows while retaining the backend count/cursor: that silently skips entries.
    paged_keys = ("parts", "components", "entries", "items") if "next_offset" in full else ()
    payload = compact(full, omitted=omitted, paged_keys=paged_keys)
    payload.update(result_id=result_id, detail="compact")
    if omitted:
        payload["omitted"] = omitted
    changes = full.get("changes")
    if isinstance(changes, dict):
        payload["change_counts"] = {
            k: len(v) if isinstance(v, list) else None
            for k, v in changes.items()
            if k in {"created", "modified", "deleted"}
        }
    if actions := next_actions(method, full):
        payload["next_actions"] = actions
    return payload


class ResultStore:
    """Durable inspection snapshots, separate from authoritative mutation receipts."""

    def __init__(self, root):
        self.root = Path(root) / ".nx-mcp" / "agent-results"
        settings()  # Validate configuration before any mutation can be dispatched.

    def put(self, payload):
        with LOCK:
            return self._put(payload)

    def _put(self, payload):
        if len(json.dumps(payload).encode("utf-8")) > settings()["max_bytes"]:
            raise OSError("Response exceeds snapshot storage limit")
        self.root.mkdir(parents=True, exist_ok=True)
        result_id = "result_" + uuid.uuid4().hex
        path = self.root / (result_id + ".json")
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        maintain(self.root, apply=True, protect=result_id)
        return result_id

    def get(self, result_id):
        if not re.fullmatch(r"result_[a-f0-9]{32}", result_id):
            raise ValueError("Invalid result_id")
        return json.loads((self.root / (result_id + ".json")).read_text(encoding="utf-8"))


def configure(mcp, workspace):
    from nx_mcp.integration_server import envelope

    registry = dict(mcp._tool_manager._tools)
    original_call = mcp.call_tool
    original_list = mcp.list_tools
    store = ResultStore(workspace.root)

    @mcp.resource("nx-artifact://workspace/{path}", mime_type="application/octet-stream")
    def artifact(path: str) -> bytes:
        """Read a workspace artifact (8 MiB limit); keep binary content outside model text."""
        file = workspace.resolve(unquote(path))
        if ".nx-mcp" in {p.casefold() for p in file.relative_to(workspace.root).parts}:
            raise ValueError("Internal service state is not an artifact")
        if file.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Resource exceeds 8 MiB; use programmatic nx_download_file chunks")
        return file.read_bytes()

    def present(response, mode, method=None):
        if not isinstance(response, CallToolResult) or mode == "full" or response.isError:
            return response
        full = response.structuredContent
        if not isinstance(full, dict):
            return response
        # Never turn an already-committed operation into an error if caching fails.
        try:
            payload = compact_payload(full, store.put(full), method)
        except OSError:
            return response
        result = envelope(payload)
        result.content.extend(c for c in response.content if not isinstance(c, TextContent))
        if full.get("path") and full.get("sha256") and full.get("size", 0) <= 8 * 1024 * 1024:
            file = workspace.resolve(full["path"])
            relative = file.relative_to(workspace.root).as_posix()
            result.content.append(
                ResourceLink(
                    type="resource_link",
                    name=file.name,
                    uri="nx-artifact://workspace/" + quote(relative, safe=""),
                    size=full.get("size"),
                )
            )
        return result

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def nx_discover_tools(
        query: str = "",
        domain: str | None = None,
        include_schema: bool = False,
        include_output_schema: bool = True,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=20)] = 10,
    ) -> CallToolResult:
        """Discover task tools by name/description or domain. Domains: modeling, sketch, assembly, drawing, manufacturing, inspection, display, files. Spaced queries match all words, with tool-name matches ranked first. Request exact-name schema before nx_invoke; include_output_schema=false omits the repeated full output contract; results are paged. Discovery does not mutate NX or the session's catalog."""
        if offset < 0 or not 1 <= limit <= 20:
            raise ValueError("offset >= 0; limit 1..20")
        if domain is not None and domain not in {*DOMAINS, "modeling"}:
            raise ValueError("Unknown domain")
        words = re.findall(r"[a-z0-9]+", query.casefold())

        def score(tool):
            name_words = re.findall(r"[a-z0-9]+", tool.name.casefold())
            haystack = " ".join(name_words) + " " + tool.description.casefold()
            if not all(word in haystack for word in words):
                return -1
            return sum(4 if word in name_words else 1 for word in words)

        rows = [
            t
            for n, t in registry.items()
            if (domain is None or category(n) == domain) and score(t) >= 0
        ]
        rows.sort(key=lambda t: (-score(t), t.name))
        # Exact name wins over incidental description matches.
        if query in registry and (domain is None or category(query) == domain):
            rows = [registry[query]]
        selected = []
        for tool in rows[offset : offset + limit]:
            row = {
                "name": tool.name,
                "description": tool.description,
                "domain": category(tool.name),
                "defaults": DEFAULTS.get(tool.name, {}),
            }
            if include_schema:
                row.update(
                    inputSchema=tool.parameters,
                    guidance=guidance(tool),
                )
                if include_output_schema:
                    row["outputSchema"] = tool.fn_metadata.output_schema
            selected.append(row)
        return envelope(
            {
                "tools": selected,
                "total_count": len(rows),
                "next_offset": offset + len(selected)
                if offset + len(selected) < len(rows)
                else None,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def nx_invoke(
        tool: str, arguments: dict[str, Any], detail: Literal["compact", "full"] = "compact"
    ) -> CallToolResult:
        """Call a discovered NX tool using its exact input schema. Mutations execute serially on the NX thread. Supply operation_id inside arguments and reuse it after transport failure; nx_operation_status is authoritative. Compact returns result_id for detail expansion. Full preserves the original response. This gateway may mutate NX; inspect the discovered tool semantics first."""
        if tool not in registry:
            raise ValueError("Unknown tool; use nx_discover_tools")
        params = {**DEFAULTS.get(tool, {}), **arguments} if detail == "compact" else arguments
        return present(await original_call(tool, params), detail, tool)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def nx_result(
        result_id: str,
        field: str = "",
        offset: int = 0,
        limit: int = 20,
        detail: Literal["compact", "full"] = "compact",
    ) -> CallToolResult:
        """Expand a retained response snapshot without repeating NX operations. field is a JSON Pointer (/bodies, /changes/created); arrays are paged with limit 1..100. Empty field returns compact data plus available top-level fields. detail=full returns full selected object/scalar data; arrays remain paged. Snapshots survive server restarts until workspace cleanup; references may be stale after model changes. Use nx_operation_status for authoritative mutation recovery."""
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset >= 0; limit 1..100")
        value = store.get(result_id)
        if field:
            if not field.startswith("/"):
                raise ValueError("field must be a JSON Pointer")
            for token in field[1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                value = value[int(token)] if isinstance(value, list) else value[token]
        if isinstance(value, list):
            return envelope(
                {
                    "result_id": result_id,
                    "field": field,
                    "items": value[offset : offset + limit],
                    "total_count": len(value),
                    "next_offset": offset + limit if offset + limit < len(value) else None,
                }
            )
        omitted: dict[str, Any] = {}
        payload = compact(value, omitted=omitted) if detail == "compact" else value
        return envelope(
            {
                "result_id": result_id,
                "field": field,
                "value": payload,
                "fields": list(value) if isinstance(value, dict) else [],
                "omitted": omitted,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def nx_result_cleanup(
        dry_run: bool = True,
        max_age_seconds: int | None = None,
        max_bytes: int | None = None,
    ) -> CallToolResult:
        """Inspect or delete disposable response snapshots. Default dry_run=true previews eligible counts/bytes. Limits are positive integers; default retention is seven days/256 MiB, configurable with NX_MCP_RESULT_MAX_AGE_SECONDS and NX_MCP_RESULT_MAX_BYTES. Automatic pruning runs on snapshot writes. Mutation recovery receipts and CAD files are never included. Call with dry_run=false to apply; old result_ids may then expire."""
        return envelope(
            maintain(
                store.root, apply=not dry_run, max_age_seconds=max_age_seconds, max_bytes=max_bytes
            )
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def nx_inspect(
        tool: Literal[
            "nx_list_features", "nx_list_sketches", "nx_list_topology", "nx_list_annotations"
        ],
        arguments: dict[str, Any] | None = None,
        collection: Literal["objects", "faces", "edges", "items"] | None = None,
        name_contains: str = "",
        kind: str | None = None,
        offset: int = 0,
        limit: int = 20,
        result_id: str | None = None,
    ) -> CallToolResult:
        """Filter and page features, sketches, topology or annotations. Filter by case-insensitive name/text and exact object kind before pagination. Topology requires arguments.body and collection=faces or edges. Returns total_count, count, next_offset, units and reported coordinate frame. Reuse result_id to page the same snapshot without another NX call; omit it to refresh after edits. Snapshot references can expire. Only the four named read-only tools are accepted."""
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset >= 0; limit 1..100")
        allowed = {
            "nx_list_features": {"objects"},
            "nx_list_sketches": {"objects"},
            "nx_list_topology": {"faces", "edges"},
            "nx_list_annotations": {"items"},
        }
        collection = collection or (
            "faces"
            if tool == "nx_list_topology"
            else ("items" if tool == "nx_list_annotations" else "objects")
        )
        if collection not in allowed[tool]:
            raise ValueError("Unsupported collection for this tool")
        if result_id:
            if arguments:
                raise ValueError(
                    "arguments cannot be combined with result_id; omit result_id to refresh"
                )
            snapshot = store.get(result_id)
            if snapshot.get("inspection_tool") != tool:
                raise ValueError("Snapshot belongs to a different inspection tool")
            full = snapshot["result"]
        else:
            arguments = arguments or {}
            if tool == "nx_list_annotations" and ({"offset", "limit"} & arguments.keys()):
                raise ValueError(
                    "Use nx_inspect offset/limit, not underlying annotation pagination"
                )
            response = await original_call(tool, arguments)
            if response.isError:
                return response
            full = response.structuredContent
            if tool == "nx_list_annotations":
                while full.get("next_offset") is not None:
                    response = await original_call(
                        tool, {"offset": full["next_offset"], "limit": 100}
                    )
                    if response.isError:
                        return response
                    more = response.structuredContent
                    if more.get("total") != full.get("total") or more.get(
                        "next_offset"
                    ) == full.get("next_offset"):
                        raise ValueError(
                            "Annotation inventory changed during capture; refresh inspection"
                        )
                    full["items"].extend(more["items"])
                    full["next_offset"] = more.get("next_offset")
            result_id = store.put({"inspection_tool": tool, "result": full})
        rows = full[collection]

        def matches(row):
            ref = row.get("object", row)
            text = str(ref.get("name", "")) + " " + str(row.get("text", ""))
            return name_contains.casefold() in text.casefold() and (
                kind is None or ref.get("kind") == kind
            )

        selected = [row for row in rows if matches(row)]
        items = selected[offset : offset + limit]
        omitted: dict[str, Any] = {}
        rendered = [compact(row, f"/items/{i}", omitted) for i, row in enumerate(items)]
        return envelope(
            {
                "result_id": result_id,
                "tool": tool,
                "collection": collection,
                "items": rendered,
                "omitted": omitted,
                "count": len(items),
                "total_count": len(selected),
                "unfiltered_count": len(rows),
                "offset": offset,
                "next_offset": offset + len(items) if offset + len(items) < len(selected) else None,
                "units": full.get("units"),
                "coordinate_frame": full.get(
                    "coordinate_frame", full.get("position_frame", "not_reported")
                ),
                "warnings": full.get("warnings", []),
            }
        )

    helper_names = {
        "nx_discover_tools",
        "nx_invoke",
        "nx_result",
        "nx_inspect",
        "nx_result_cleanup",
    }
    for name in helper_names:
        tool = mcp._tool_manager.get_tool(name)
        tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
        tool.fn_metadata.arg_model.model_rebuild(force=True)
        tool.parameters = tool.fn_metadata.arg_model.model_json_schema()

    async def call(name, arguments):
        try:
            if name in helper_names:
                return await mcp._tool_manager.call_tool(name, arguments, convert_result=False)
            if name not in registry:
                raise ValueError("Unknown tool")
            return present(
                await original_call(name, {**DEFAULTS.get(name, {}), **(arguments or {})}),
                "compact",
                name,
            )
        except (ValueError, KeyError, IndexError, OSError, TypeError, ToolError) as error:
            from nx_mcp.runtime import NXToolError

            return envelope(
                NXToolError(
                    "NX_RESULT_EXPIRED"
                    if isinstance(error, FileNotFoundError)
                    or isinstance(error.__cause__, FileNotFoundError)
                    else "NX_INVALID_ARGUMENT",
                    str(error),
                    details={"mutation_outcome": "not_started"},
                ).as_dict(),
                True,
            )

    async def listing():
        result = [t for t in await original_list() if t.name in CORE | helper_names]
        for tool in result:
            # Agent presentation is an extensible envelope; exact full contracts are discoverable.
            tool.outputSchema = None
            if tool.name in DEFAULTS:
                tool.inputSchema = json.loads(json.dumps(tool.inputSchema))
                for key, value in DEFAULTS[tool.name].items():
                    if key in tool.inputSchema.get("properties", {}):
                        tool.inputSchema["properties"][key]["default"] = value
                tool.description += (
                    " Agent profile defaults: " + json.dumps(DEFAULTS[tool.name]) + "."
                )
        return result

    mcp.call_tool = call
    mcp.list_tools = listing
    mcp._mcp_server.call_tool(validate_input=False)(call)
    mcp._mcp_server.list_tools()(listing)
    mcp._mcp_server.instructions = "NX agent profile. Use nx_inspect for filtered stable inventory pages and nx_result_cleanup for snapshot maintenance. Discover exact schemas with nx_discover_tools, then call nx_invoke. Inventories default to 20 rows; follow next_offset. Compact responses retain result_id for nx_result expansion. Supply durable operation_id for mutations and query nx_operation_status before retrying uncertain operations. File downloads default to metadata; retrieve bytes programmatically, never paste base64 into model context. Full compatibility surface remains available with NX_MCP_SURFACE=full."
