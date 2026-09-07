"""Disk and native-state regressions for explicitly scoped saves."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp.runtime import NXToolError
from nx_mcp.save_audit import snapshot
from tests.fakes import Part


def child(rig, tmp_path):
    original = rig.part
    p = Part(rig.session, tmp_path / "child.prt")
    p.Save()
    p.IsModified = True
    rig.session.Parts.Work = rig.session.Parts.Display = original
    return p


def test_receipt_identifies_saved_file_and_preserved_child(rig, tmp_path):
    p = child(rig, tmp_path)
    before = snapshot(rig.session)[p.Tag]
    result = rig.e._save_part()
    assert result["saved_files"] == [rig.part.FullPath]
    assert result["unrelated_modified_parts"] == [
        {"path": p.FullPath, "modified_before": True, "modified_after": True, "unchanged": True}
    ]
    assert snapshot(rig.session)[p.Tag] == before
    assert result["target_file"]["after"]["sha256"]


@pytest.mark.parametrize("change", ["flag", "disk"])
def test_unexpected_child_change_is_partial_with_evidence(rig, tmp_path, change):
    from pathlib import Path

    p = child(rig, tmp_path)
    save = rig.part.Save

    def faulty_save(*args):
        if change == "flag":
            p.IsModified = False
        else:
            Path(p.FullPath).write_bytes(b"unintended write")
        return save(*args)

    rig.part.Save = faulty_save
    with pytest.raises(NXToolError) as error:
        rig.e._save_part()
    assert error.value.details["mutation_outcome"] == "partial"
    assert error.value.details["saved_files"] == [rig.part.FullPath]
    assert error.value.details["unexpected_changes"][0]["path"] == p.FullPath


def test_native_unsaved_status_is_not_reported_as_success(rig):
    status = NS(
        NumberUnsavedParts=1,
        NumberUnsavedObjects=0,
        GetPart=lambda i: rig.part,
        GetStatus=lambda i: 123,
        Dispose=Mock(),
    )
    rig.part.Save = Mock(return_value=status)
    with pytest.raises(NXToolError) as error:
        rig.e._save_part()
    assert error.value.details["native_save_errors"] == [
        {"path": rig.part.FullPath, "nx_code": 123}
    ]
    assert not error.value.details["saved_files"]
    status.Dispose.assert_called_once()


def test_preflight_read_failure_prevents_save(rig, monkeypatch):
    monkeypatch.setattr("nx_mcp.save_audit.snapshot", Mock(side_effect=PermissionError("denied")))
    rig.part.Save = Mock()
    with pytest.raises(NXToolError) as error:
        rig.e._save_part()
    assert error.value.details["mutation_outcome"] == "not_started"
    rig.part.Save.assert_not_called()
