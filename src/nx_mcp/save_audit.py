"""Verify work-part saves against loaded-part state and disk fingerprints."""

import hashlib
from pathlib import Path

from nx_mcp.runtime import NXToolError


def fingerprint(path):
    if not path or not Path(path).is_file():
        return {"exists": False}
    file = Path(path)
    before = file.stat()
    with file.open("rb") as stream:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
        digest = hasher.hexdigest()
    stat = file.stat()
    if (before.st_size, before.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise OSError(f"File changed while verifying: {path}")
    return {"exists": True, "sha256": digest, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def snapshot(session):
    return {
        int(p.Tag): {
            "path": p.FullPath,
            "modified": bool(p.IsModified),
            "file": fingerprint(p.FullPath),
        }
        for p in session.Parts
    }


def verify(before, after, target_tag, native_errors):
    target = after.get(target_tag)
    unrelated = []
    unexpected = []
    for tag, row in before.items():
        if tag == target_tag:
            continue
        current = after.get(tag)
        unchanged = current == row
        if row["modified"]:
            unrelated.append(
                {
                    "path": row["path"],
                    "modified_before": True,
                    "modified_after": current["modified"] if current else None,
                    "unchanged": unchanged,
                }
            )
        if not unchanged:
            unexpected.append({"path": row["path"], "before": row, "after": current})
    new_parts = [row for tag, row in after.items() if tag not in before]
    saved = bool(
        target
        and target["path"] == before[target_tag]["path"]
        and not target["modified"]
        and target["file"]["exists"]
        and not native_errors
    )
    result = {
        "requested_file": before[target_tag]["path"],
        "saved_files": [target["path"]] if saved else [],
        "observed_changed_files": [
            row["path"]
            for tag, row in after.items()
            if tag not in before or row["file"] != before[tag]["file"]
        ],
        "save_scope": "work_part_only",
        "verification_scope": "loaded_part_flags_and_files",
        "verified_part_count": len(before),
        "target_file": {
            "before": before[target_tag]["file"],
            "after": target["file"] if target else None,
        },
        "unrelated_modified_parts": unrelated,
        "unexpected_changes": unexpected,
        "unexpected_new_parts": new_parts,
        "native_save_errors": native_errors,
    }
    if not saved or unexpected or new_parts:
        raise NXToolError(
            "NX_SAVE_VERIFICATION_FAILED",
            "Save did not satisfy its file/state contract; inspect the receipt before retrying",
            details={**result, "mutation_outcome": "partial"},
        )
    return result
