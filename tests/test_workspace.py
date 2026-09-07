from pathlib import Path

import pytest

from nx_mcp.workspace import Workspace, WorkspaceViolation


def test_workspace_accepts_relative_paths_inside_root(tmp_path: Path):
    workspace = Workspace(tmp_path)

    assert workspace.resolve("parts/bracket.prt") == tmp_path / "parts" / "bracket.prt"


@pytest.mark.parametrize("path", ["../outside.prt", "C:/outside.prt"])
def test_workspace_rejects_paths_outside_root(tmp_path: Path, path: str):
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


def test_resolve_accepts_absolute_inside_root(tmp_path):
    path = tmp_path / "projects" / "controller" / "part.prt"
    assert Workspace(tmp_path).resolve(str(path)) == path


@pytest.mark.parametrize("path", ["C:relative.prt", r"\relative.prt", ".NX-MCP/state.json"])
def test_rejects_ambiguous_or_reserved_paths(tmp_path, path):
    with pytest.raises(WorkspaceViolation):
        Workspace(tmp_path).resolve(path)


def test_symlink_cannot_escape_workspace(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks require host privileges")
    with pytest.raises(WorkspaceViolation):
        Workspace(root).resolve("escape/outside.prt")
