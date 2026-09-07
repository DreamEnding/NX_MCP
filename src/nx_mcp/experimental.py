"""Explicit opt-in adapter for the unverified 0.1 tool modules."""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Coroutine
from functools import wraps
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

from nx_mcp.response import ToolError, ToolResult
from nx_mcp.runtime import NXToolError
from nx_mcp.tools.registry import ToolRegistry
from nx_mcp.workspace import Workspace, WorkspaceViolation

_LEGACY_MODULES = (
    "assembly",
    "drawing",
    "feature_tree",
    "file_ops",
    "measure",
    "modeling",
    "sketch",
    "utility",
)
JOURNAL_TOOL_NAMES = {"nx_run_journal", "nx_record_start", "nx_record_stop"}
_PATH_PARAMS = {
    "nx_add_component": "part_path",
    "nx_export_drawing_pdf": "path",
    "nx_import_geometry": "path",
    "nx_record_stop": "save_path",
    "nx_run_journal": "path",
    "nx_save_as": "path",
    "nx_screenshot": "path",
}


def load_legacy_handlers() -> dict[str, Callable[..., Coroutine[Any, Any, Any]]]:
    handlers: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}
    registry_snapshot = dict(ToolRegistry._tools)
    newly_loaded: list[str] = []
    try:
        for module_name in _LEGACY_MODULES:
            full_name = f"nx_mcp.tools.{module_name}"
            if full_name not in sys.modules:
                newly_loaded.append(full_name)
            module = importlib.import_module(full_name)
            for name, value in inspect.getmembers(module, inspect.iscoroutinefunction):
                if name.startswith("nx_"):
                    handlers[name] = value
    finally:
        ToolRegistry._tools = registry_snapshot
        for full_name in newly_loaded:
            sys.modules.pop(full_name, None)
    return handlers


def _secure_params(
    method: str,
    params: dict[str, Any],
    workspace: Workspace | None,
    *,
    already_resolved: bool,
) -> dict[str, Any]:
    path_param = _PATH_PARAMS.get(method)
    if path_param is None or params.get(path_param) is None:
        return params
    if workspace is None:
        raise NXToolError(
            "NX_WORKSPACE_NOT_CONFIGURED",
            "NX_MCP_WORKSPACE is required for experimental file operations.",
        )
    try:
        path = (
            workspace.ensure_inside(params[path_param])
            if already_resolved
            else workspace.resolve(params[path_param])
        )
    except WorkspaceViolation as error:
        raise NXToolError("NX_PATH_OUTSIDE_WORKSPACE", str(error)) from error
    if method in JOURNAL_TOOL_NAMES:
        journals_root = (workspace.root / "journals").resolve()
        if not path.is_relative_to(journals_root):
            raise NXToolError(
                "NX_PATH_OUTSIDE_WORKSPACE",
                "Journal files must stay inside the workspace journals directory.",
            )
    secured = dict(params)
    secured[path_param] = str(path)
    return secured


def add_experimental_tools(
    mcp: FastMCP,
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    certified_names: set[str],
    workspace: Workspace | None,
    *,
    enable_journal: bool,
) -> None:
    from mcp.server.fastmcp.exceptions import ToolError as MCPToolError

    for name, handler in load_legacy_handlers().items():
        if name in certified_names or (name in JOURNAL_TOOL_NAMES and not enable_journal):
            continue

        def make_proxy(
            tool_name: str,
            legacy_handler: Callable[..., Awaitable[Any]],
        ) -> Callable[..., Awaitable[dict[str, Any]]]:
            signature = inspect.signature(legacy_handler)

            @wraps(legacy_handler)
            async def proxy(*args: Any, **kwargs: Any) -> dict[str, Any]:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                try:
                    params = _secure_params(
                        tool_name,
                        dict(bound.arguments),
                        workspace,
                        already_resolved=False,
                    )
                    return await call(tool_name, params)
                except NXToolError as error:
                    raise MCPToolError(json.dumps(error.as_dict(), ensure_ascii=False)) from error

            return proxy

        mcp.add_tool(
            make_proxy(name, handler),
            name=name,
            description=f"EXPERIMENTAL: {handler.__doc__ or name}",
            structured_output=False,
        )


def execute_legacy(
    method: str,
    params: dict[str, Any],
    workspace: Workspace,
    *,
    enable_journal: bool,
) -> dict[str, Any]:
    if method in JOURNAL_TOOL_NAMES and not enable_journal:
        raise NXToolError("NX_TOOL_NOT_FOUND", f"Journal tool is disabled: {method}")
    handler = load_legacy_handlers().get(method)
    if handler is None:
        raise NXToolError("NX_TOOL_NOT_FOUND", f"Unsupported bridge command: {method}")
    secured = _secure_params(method, params, workspace, already_resolved=True)
    coroutine = handler(**secured)
    try:
        coroutine.send(None)
    except StopIteration as completed:
        result: Any = completed.value
    else:
        coroutine.close()
        raise NXToolError(
            "NX_EXPERIMENTAL_ASYNC_UNSUPPORTED",
            "Experimental NX handlers must complete without suspending.",
        )
    if isinstance(result, ToolError):
        raise NXToolError(
            result.error_code,
            result.message,
            suggestion=result.suggestion,
        )
    if isinstance(result, ToolResult):
        return json.loads(result.to_text())
    if isinstance(result, dict):
        return result
    return {"result": str(result)}
