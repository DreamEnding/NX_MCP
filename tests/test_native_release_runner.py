"""Native release receipts must distinguish failures and changed user sessions."""

import hashlib
import runpy
from pathlib import Path

import pytest

RUNNER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/validate_native_release.py")
)


def test_session_verification_checks_placements_and_unsaved_state():
    before = {
        "parts": [{"path": "a.prt", "work": True, "display": True, "modified": False}],
        "components": [{"path": "p.prt", "translation": [1, 2, 3]}],
    }
    RUNNER["verify_session"](before, before)
    with pytest.raises(RuntimeError, match="component source"):
        RUNNER["verify_session"](before, {**before, "components": []})
    with pytest.raises(RuntimeError, match="saved state"):
        RUNNER["verify_session"](
            before, {**before, "parts": [{**before["parts"][0], "modified": True}]}
        )


def test_receipt_manifest_hashes_nested_artifacts(tmp_path):
    (tmp_path / "sheets").mkdir()
    (tmp_path / "sheets" / "view.png").write_bytes(b"fixture")
    (tmp_path / "release-validation.json").write_text("not self-hashed")
    assert RUNNER["manifest_files"](tmp_path) == [
        {"path": "sheets/view.png", "size": 7, "sha256": hashlib.sha256(b"fixture").hexdigest()}
    ]


@pytest.mark.asyncio
async def test_agent_ux_client_forwards_tool_name_arguments(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    runner = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/validate_agent_ux.py")
    )
    session = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(isError=False, structuredContent={"status": "success"})
        )
    )
    client = runner["Client"](session, tmp_path / "ux")
    client.schemas = {"nx_create_reference_set": {"properties": {}}}
    await client.call("nx_create_reference_set", name="SOLIDS", objects=["body"])
    session.call_tool.assert_awaited_once_with(
        "nx_create_reference_set", {"name": "SOLIDS", "objects": ["body"]}
    )


def test_native_examples_accept_runner_profile_count():
    """An added tool must not strand unrelated native suites on an old literal."""
    examples = Path(__file__).resolve().parents[1] / "examples"
    for name in [
        "advanced_tools",
        "authoring_tools",
        "engineering_tools",
        "freeform_manufacturing",
        "project_folders",
        "sheet_metal",
        "visual_tools",
    ]:
        source = (examples / f"validate_{name}.py").read_text()
        assert 'os.environ.get("NX_EXPECTED_TOOL_COUNT", "189")' in source
        assert "== 179" not in source
