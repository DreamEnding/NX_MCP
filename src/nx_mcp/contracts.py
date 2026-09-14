"""Typed public contracts shared by MCP tools and the NX bridge."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nx_mcp.runtime import NXToolError, ObjectKind, ObjectRef

__all__ = [
    "ExportResult",
    "ExtrudeResult",
    "NXToolError",
    "ObjectKind",
    "ObjectListResult",
    "ObjectRef",
    "ObjectResult",
    "OperationResult",
    "PartResult",
    "Point2D",
    "StatusResult",
    "ToolSuccess",
]


class Point2D(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class ToolSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    message: str


class StatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    connected: bool
    nx_version: str
    bridge_protocol: int
    active_part: ObjectRef | None = None


class PartResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    part: ObjectRef
    message: str = ""


class ObjectResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    object: ObjectRef
    message: str = ""


class ObjectListResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    objects: list[ObjectRef]
    message: str = ""


class ExtrudeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    feature: ObjectRef
    body: ObjectRef
    message: str = ""


class ExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    path: str
    message: str = ""
