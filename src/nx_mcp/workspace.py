"""Filesystem boundary for NX MCP file operations."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


class WorkspaceViolation(ValueError):
    """Raised when a requested path escapes the configured workspace."""


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, path: str) -> Path:
        """Resolve an NX-host absolute or workspace-relative path, never a client path."""
        requested = Path(path)
        windows = PureWindowsPath(path)
        # On POSIX a Windows drive would otherwise become an ordinary directory.
        # On Windows reject drive-relative and root-relative paths (C:foo, \foo).
        if windows.drive and not requested.is_absolute():
            raise WorkspaceViolation("Use an absolute NX-host path inside the workspace")
        if windows.root and not requested.is_absolute():
            raise WorkspaceViolation("Use a complete NX-host path inside the workspace")
        return self.ensure_inside(requested if requested.is_absolute() else self.root / requested)

    def ensure_inside(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkspaceViolation("Path must stay inside the configured workspace")
        if any(part.casefold() == ".nx-mcp" for part in resolved.relative_to(self.root).parts):
            raise WorkspaceViolation("Internal NX MCP state is not a user artifact")
        return resolved
