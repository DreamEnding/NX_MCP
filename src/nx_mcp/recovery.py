"""Durable request receipts. No NXOpen calls; safe for sidecar status queries."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from nx_mcp.runtime import NXToolError


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class OperationStore:
    def __init__(self, root):
        self.root = Path(root) / ".nx-mcp" / "operations"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{8,128}", operation_id
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "operation_id must be 8–128 ASCII letters, digits, underscores or hyphens",
            )
        return self.root / (operation_id + ".json")

    def get(self, operation_id):
        path = self.path(operation_id)
        if not path.exists():
            return {
                "operation_id": operation_id,
                "state": "unknown",
                "mutation_outcome": "unknown",
                "reason": "No durable receipt exists; do not infer failure.",
            }
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise NXToolError("NX_RECEIPT_UNREADABLE", str(e)) from e

    def put(self, record):
        path = self.path(record["operation_id"])
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)

    def recover(self, session_id):
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("state") == "running" and record.get("session_id") != session_id:
                record.update(
                    state="unknown",
                    mutation_outcome="unknown",
                    reason="NX process restarted before final receipt; manual reconciliation required.",
                )
                self.put(record)

    @staticmethod
    def fingerprint(method, params):
        return hashlib.sha256(
            json.dumps(
                [method, params], sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
