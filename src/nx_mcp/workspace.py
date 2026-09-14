"""Filesystem boundary for NX MCP file operations."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


class WorkspaceViolation(ValueError):
    """Raised when a requested path escapes the configured workspace."""


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, relative_path: str) -> Path:
        requested = Path(relative_path)
        if requested.is_absolute() or PureWindowsPath(relative_path).anchor:
            raise WorkspaceViolation("Path must stay inside the configured workspace")

        return self.ensure_inside(self.root / requested)

    def ensure_inside(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkspaceViolation("Path must stay inside the configured workspace")
        return resolved
