import sys
from pathlib import Path

import pytest

from nx_mcp.workspace import Workspace, WorkspaceViolation


def test_workspace_accepts_relative_paths_inside_root(tmp_path: Path):
    workspace = Workspace(tmp_path)

    assert workspace.resolve("parts/bracket.prt") == tmp_path / "parts" / "bracket.prt"


@pytest.mark.parametrize("path", ["../outside.prt", "C:/outside.prt"])
def test_workspace_rejects_paths_outside_root(tmp_path: Path, path: str):
    if sys.platform != "win32" and path.startswith("C:/"):
        pytest.skip("drive-letter paths are only absolute on Windows")
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceViolation, match="workspace"):
        workspace.resolve(path)


def test_workspace_accepts_absolute_path_already_resolved_inside_root(tmp_path: Path):
    workspace = Workspace(tmp_path)
    safe_path = tmp_path / "parts" / "bracket.prt"

    assert workspace.ensure_inside(safe_path) == safe_path


def test_workspace_rejects_absolute_path_outside_root(tmp_path: Path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceViolation, match="workspace"):
        workspace.ensure_inside(tmp_path.parent / "outside.prt")


@pytest.mark.parametrize("path", ["C:part.prt", "\\part.prt", "\\\\server\\share\\part.prt"])
def test_workspace_rejects_windows_anchored_paths_on_every_platform(tmp_path, path):
    with pytest.raises(WorkspaceViolation):
        Workspace(tmp_path).resolve(path)


def test_workspace_rejects_symlink_escape(tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this host")
    with pytest.raises(WorkspaceViolation):
        Workspace(root).resolve("link/escape.prt")
    with pytest.raises(WorkspaceViolation):
        Workspace(root).ensure_inside(link / "escape.prt")
