"""Standard-library-only types used inside the NX embedded Python runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ObjectKind = Literal[
    "reference_set",
    "datum",
    "part",
    "sketch",
    "curve",
    "point",
    "feature",
    "body",
    "component",
    "face",
    "edge",
    "section",
    "constraint",
    "expression",
    "component_pattern",
    "assembly_constraint",
    "drawing_sheet",
    "drawing_view",
    "dimension",
    "annotation",
    "traceline",
    "explosion",
    "modeling_view",
]


@dataclass(frozen=True)
class ObjectRef:
    id: str
    kind: ObjectKind
    name: str
    part_id: str

    def model_dump(self) -> dict[str, str]:
        """Match the sidecar model interface without importing Pydantic in NX."""
        return asdict(self)


class NXToolError(Exception):
    """A recoverable NX operation error that can be shown to an MCP caller."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        suggestion: str | None = None,
        nx_code: int | str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.nx_code = nx_code
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "error",
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.suggestion:
            result["suggestion"] = self.suggestion
        if self.nx_code is not None:
            result["nx_code"] = self.nx_code
        if self.details:
            result["details"] = self.details
        return result
