"""Bounded bridge receipts with immutable, independently pageable full results."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from nx_mcp.result_retention import LOCK, maintain, settings
from nx_mcp.runtime import NXToolError


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def preview(value, path="", omitted=None, budget=None):
    omitted = {} if omitted is None else omitted
    budget = [2000] if budget is None else budget
    budget[0] -= 1
    if budget[0] <= 0:
        omitted.setdefault(path, {"reason": "Read this field separately"})
        return None
    if isinstance(value, dict):
        if len(value) > 100:
            omitted[path] = {"total_keys": len(value), "returned_keys": 100}
        return {
            k: preview(v, path + "/" + k.replace("~", "~0").replace("/", "~1"), omitted, budget)
            for k, v in list(value.items())[:100]
        }
    if isinstance(value, list):
        if len(value) > 5:
            omitted[path] = {"total_count": len(value), "returned_count": 5}
        return [preview(v, path + "/" + str(i), omitted, budget) for i, v in enumerate(value[:5])]
    if isinstance(value, str) and len(value) > 2048:
        omitted[path] = {"total_characters": len(value), "returned_characters": 2048}
        return value[:2048]
    return value


def bound_result(result, directory):
    raw = encoded(result)
    if len(raw) < 512 * 1024:
        return result
    if directory is None:
        raise NXToolError(
            "NX_RESPONSE_TOO_LARGE",
            "Bridge result exceeds the delivery budget",
            details={
                "operation_id": result.get("operation_id"),
                "mutation_outcome": result.get("mutation_outcome", "unknown"),
            },
        )
    root = Path(directory)
    identifier = "result_" + uuid.uuid4().hex
    try:
        root.mkdir(parents=True, exist_ok=True)
        with LOCK:
            config = settings()
            age, budget = config["max_age_seconds"], config["max_bytes"]
            if len(raw) > budget:
                raise OSError("Result exceeds snapshot storage budget")
            p = root / (identifier + ".json")
            tmp = p.with_suffix(".tmp")
            try:
                tmp.write_bytes(raw)
                tmp.replace(p)
            finally:
                tmp.unlink(missing_ok=True)
            maintain(root, apply=True, max_age_seconds=age, max_bytes=budget, protect=p.stem)
    except OSError as error:
        raise NXToolError(
            "NX_RESULT_STORAGE_FAILED",
            "Result delivery failed after execution; inspect durable operation status before retrying",
            details={
                "operation_id": result.get("operation_id"),
                "mutation_outcome": result.get("mutation_outcome", "unknown"),
                "restore_id": result.get("restore_id"),
                "cause": str(error),
            },
        ) from error
    omitted = {}
    summary = preview(result, omitted=omitted)
    if len(encoded({"value": summary, "omitted": omitted})) > 400 * 1024:
        summary = {
            k: result[k]
            for k in (
                "status",
                "operation_id",
                "session_id",
                "mutation_outcome",
                "restore_id",
                "body_count",
                "object_count",
                "expanded_count",
            )
            if k in result
        }
        omitted = {"/": {"reason": "Full result requires pagination"}}
    summary["full_result"] = {
        "id": identifier,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "tool": "nx_read_result",
        "immutable": True,
    }
    summary["omitted"] = omitted
    return summary


def read_result(root, result_id, field="", offset=0, limit=20):
    if not re.fullmatch(r"result_[0-9a-f]{32}", result_id) or offset < 0 or not 1 <= limit <= 100:
        raise NXToolError(
            "NX_INVALID_ARGUMENT", "Use a returned result ID, offset >=0 and limit 1..100"
        )
    p = Path(root) / (result_id + ".json")
    with LOCK:
        if not p.is_file() or p.is_symlink():
            raise NXToolError("NX_RESULT_EXPIRED", "Result snapshot is unavailable")
        value = json.loads(p.read_text(encoding="utf-8"))
    if field and not field.startswith("/"):
        raise NXToolError("NX_INVALID_ARGUMENT", "field must be a JSON pointer")
    try:
        for key in field.split("/")[1:]:
            key = key.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, ValueError, TypeError) as error:
        raise NXToolError("NX_INVALID_ARGUMENT", "Unknown result field") from error
    total = len(value) if isinstance(value, (list, str, dict)) else None
    selected = (
        dict(list(value.items())[offset : offset + limit])
        if isinstance(value, dict)
        else value[offset : offset + limit]
        if total is not None
        else value
    )
    omitted = {}
    data = (
        [preview(v, field + "/" + str(offset + i), omitted) for i, v in enumerate(selected)]
        if isinstance(selected, list)
        else preview(selected, field, omitted)
    )
    if len(encoded({"value": data, "omitted": omitted})) > 400 * 1024:
        if isinstance(selected, list):
            data = [
                {"field": field + "/" + str(offset + i), "read_separately": True}
                for i in range(len(selected))
            ]
        elif isinstance(selected, dict):
            data = {
                k: {
                    "field": field + "/" + k.replace("~", "~0").replace("/", "~1"),
                    "read_separately": True,
                }
                for k in list(selected)[:100]
            }
        else:
            data = None
        omitted = {field: {"reason": "Read child fields separately"}}
    return {
        "status": "success",
        "result_id": result_id,
        "field": field,
        "value": data,
        "total_count": total,
        "offset": offset,
        "next_offset": min(offset + limit, total)
        if total is not None and offset + limit < total
        else None,
        "omitted": omitted,
        "mutation_outcome": "not_applicable",
    }
