"""Backward-compatible projections for large native inventories."""

from nx_mcp.runtime import NXToolError


def compact_reference(reference):
    return {
        k: v
        for k, v in reference.items()
        if k in {"id", "kind", "name", "part_id", "occurrence_path"}
    }


def page(rows, offset=0, limit=None):
    if (
        type(offset) is not int
        or offset < 0
        or (limit is not None and (type(limit) is not int or not 1 <= limit <= 1000))
    ):
        raise NXToolError(
            "NX_INVALID_ARGUMENT", "offset must be nonnegative; limit must be 1–1000 or null"
        )
    selected = rows[offset:] if limit is None else rows[offset : offset + limit]
    end = offset + len(selected)
    return selected, {
        "total_count": len(rows),
        "count": len(selected),
        "offset": offset,
        "next_offset": end if end < len(rows) else None,
    }
