"""Recovery invariants across native failures, save boundaries and part lifetimes."""

from unittest.mock import Mock

import pytest

from nx_mcp.runtime import NXToolError
from tests.fakes import Body, Part

pytestmark = pytest.mark.fake_nx


def mutation(rig):
    def add(fail=False):
        rig.part.Bodies.append(Body())
        rig.part.IsModified = True
        if fail:
            raise RuntimeError("after mutation")
        return {}

    rig.e._handlers["nx_test_add"] = add
    return lambda **p: rig.e.execute("nx_test_add", p)


def test_checkpoint_rollback_invalidates_objects_and_preserves_receipt(rig):
    add = mutation(rig)
    cp = rig.e.execute("nx_checkpoint", {})
    committed = add(operation_id="test-operation-001")
    ref = committed["changes"]["created"][0]["id"]
    assert rig.e._checkpoint_state()["undo_depth"] == 1
    rig.e.execute("nx_status", {})
    assert rig.e._checkpoint_state()["undo_depth"] == 1
    rig.e.execute("nx_rollback", {"checkpoint_id": cp["checkpoint_id"]})
    assert not rig.part.Bodies and not rig.part.IsModified
    with pytest.raises(NXToolError):
        rig.e.objects.resolve(ref)
    replay = add(operation_id="test-operation-001")
    assert replay["replayed"] and "reverted" in replay["warnings"][0]
    assert not rig.part.Bodies
    with pytest.raises(NXToolError, match="not in this session"):
        rig.e._rollback_checkpoint(cp["checkpoint_id"])


def test_undo_reverts_only_latest_operation_and_save_expires_marks(rig):
    add = mutation(rig)
    add(operation_id="first-operation")
    add(operation_id="second-operation")
    assert len(rig.part.Bodies) == 2
    assert rig.e.execute("nx_undo", {})["undone_operation_id"] == "second-operation"
    assert len(rig.part.Bodies) == 1
    cp = rig.e._checkpoint()
    saved = rig.e._save_part()
    assert saved["recovery"]["undo_depth"] == 0
    with pytest.raises(NXToolError, match="expired"):
        rig.e._rollback_checkpoint(cp["checkpoint_id"])
    with pytest.raises(NXToolError, match="No native undo"):
        rig.e._undo()


def test_cross_part_undo_and_checkpoint_refuse_unrelated_changes(rig, tmp_path):
    add = mutation(rig)
    cp = rig.e._checkpoint()
    add()
    other = Part(rig.session, tmp_path / "other.prt")
    with pytest.raises(NXToolError) as error:
        rig.e._undo()
    assert error.value.code == "NX_CROSS_PART_ROLLBACK"
    with pytest.raises(NXToolError) as error:
        rig.e._rollback_checkpoint(cp["checkpoint_id"])
    assert error.value.code == "NX_CROSS_PART_ROLLBACK"
    assert len(rig.part.Bodies) == 1 and not other.Bodies


def test_failed_update_rolls_back_and_rejects_retry(rig):
    add = mutation(rig)
    with pytest.raises(NXToolError) as error:
        add(fail=True, operation_id="failed-operation")
    assert error.value.details["mutation_outcome"] == "rolled_back"
    assert not rig.part.Bodies and not rig.part.IsModified
    with pytest.raises(NXToolError) as replay:
        add(fail=True, operation_id="failed-operation")
    assert replay.value.code == "NX_OPERATION_FAILED"
    assert rig.e._current_operation is None


def test_rollback_failure_is_partial_and_never_replayed_as_success(rig):
    add = mutation(rig)
    rig.session.UndoToMark = Mock(side_effect=RuntimeError("lost mark"))
    with pytest.raises(NXToolError) as error:
        add(fail=True, operation_id="partial-operation")
    assert error.value.code == "NX_ROLLBACK_FAILED"
    assert error.value.details["mutation_outcome"] == "partial"
    assert rig.e.store.get("partial-operation")["mutation_outcome"] == "partial"
    assert len(rig.part.Bodies) == 1


def test_receipt_write_failure_does_not_undo_committed_geometry(rig, monkeypatch):
    add = mutation(rig)
    put = rig.e.store.put

    def persist(record):
        if record["state"] == "committed":
            raise OSError("disk full")
        put(record)

    monkeypatch.setattr(rig.e.store, "put", persist)
    with pytest.raises(OSError):
        add(operation_id="durable-operation")
    assert len(rig.part.Bodies) == 1
    assert rig.e.store.get("durable-operation")["state"] == "running"
    rig.e.store.recover("next-session")
    assert rig.e.store.get("durable-operation")["state"] == "unknown"
    with pytest.raises(NXToolError) as error:
        add(operation_id="durable-operation")
    assert error.value.code == "NX_OPERATION_UNKNOWN"


def test_prior_session_receipt_warns_and_cannot_duplicate(rig):
    add = mutation(rig)
    add(operation_id="previous-session")
    r = rig.e.store.get("previous-session")
    r["session_id"] = "old"
    rig.e.store.put(r)
    result = add(operation_id="previous-session")
    assert any("earlier NX session" in w for w in result["warnings"])
    assert len(rig.part.Bodies) == 1


def test_session_lifecycle_and_generation_reject_closed_references(rig, tmp_path):
    part = rig.part
    body = Body()
    part.Bodies.append(body)
    old = rig.ref(body)
    p = rig.e._reference(part, "part", part, "Part")["id"]
    opened = rig.e._open_part(part.FullPath)
    assert opened["already_loaded"]
    assert opened["message"] == "Reused loaded part"
    assert rig.e._activate_part(part.Name, False, False)["part"]["id"] == p
    cp = rig.e._checkpoint()
    rig.e._close_part(save=True, part=p)
    assert not rig.session.Parts and not rig.e._checkpoints
    with pytest.raises(NXToolError):
        rig.e.objects.resolve(old)
    rig.session.Parts.append(part)
    rig.session.Parts.Work = rig.session.Parts.Display = part
    assert rig.ref(body) != old
    path = tmp_path / "imported.prt"
    path.write_text("fixture")
    new_part = rig.e._open_part(str(path), work=False, display=False)
    assert not new_part["already_loaded"]
    assert new_part["message"] == "Opened part"
    assert len(rig.e._list_open_parts()["parts"]) == 2
    with pytest.raises(NXToolError):
        rig.e._open_part(str(tmp_path / "missing.prt"))
    with pytest.raises(NXToolError):
        rig.e._activate_part("missing")
    assert cp["checkpoint_id"] not in rig.e._checkpoints


def test_snapshot_invalidation_preserves_topology_for_display_only(rig):
    b = Body()
    rig.part.Bodies.append(b)
    before = rig.e._snapshot(rig.part)
    face = rig.ref(b.faces[0], "face")
    rig.e._invalidate_deleted(before, before, False)
    assert rig.e.objects.resolve(face) is b.faces[0]
    rig.e._invalidate_deleted(before, {})
    for ref in [face, next(iter(before.values()))["id"]]:
        with pytest.raises(NXToolError):
            rig.e.objects.resolve(ref)


def test_lookup_ambiguity_ownership_and_kind_guards(rig, tmp_path):
    first = Body("A")
    second = Body("a")
    rig.part.Bodies.extend([first, second])
    ref = rig.ref(first)
    assert rig.e._resolve(first.JournalIdentifier, {"body"}) is first
    with pytest.raises(NXToolError) as error:
        rig.e._resolve("A")
    assert error.value.code == "NX_AMBIGUOUS_REFERENCE"
    with pytest.raises(NXToolError):
        rig.e._resolve(ref, {"feature"})
    with pytest.raises(NXToolError):
        rig.e._resolve("obj_missing")
    with pytest.raises(NXToolError):
        rig.e._resolve("missing")
    Part(rig.session, tmp_path / "other.prt")
    with pytest.raises(NXToolError):
        rig.e._resolve(ref)


def test_guard_failures_never_create_undo_marks(rig):
    with pytest.raises(NXToolError):
        rig.e.execute("nx_checkpoint", {"unexpected": 1})
    with pytest.raises(NXToolError):
        rig.e.execute("nx_no_such_tool", {})
    rig.e.enable_experimental = False
    with pytest.raises(NXToolError):
        rig.e.execute("nx_no_such_tool", {})
    assert not rig.session.marks


def test_open_reuses_loaded_part_with_equivalent_path_spelling(rig, tmp_path):
    path = tmp_path / "project" / "base.prt"
    path.parent.mkdir()
    rig.part.FullPath = str(path.parent / "sub" / ".." / path.name)
    opened = rig.e._open_part(str(path))
    assert opened["already_loaded"]
    assert len(rig.session.Parts) == 1


def test_close_invalidates_automatically_unloaded_prototypes(rig, tmp_path):
    child = Part(rig.session, tmp_path / "child.prt")
    child_ref = rig.e._reference(child, "part", child, "Child")["id"]
    target = rig.e._reference(rig.part, "part", rig.part, "Parent")["id"]
    rig.part.Close = Mock(side_effect=lambda *args: rig.session.Parts.clear())
    result = rig.e._close_part(save=False, part=target)
    assert result["closed_count"] == 2 and result["remaining_count"] == 0
    with pytest.raises(NXToolError) as error:
        rig.e.objects.resolve(child_ref)
    assert error.value.code == "NX_OBJECT_STALE"


def test_operation_status_preserves_target_receipt_metadata(rig):
    committed = mutation(rig)(operation_id="receipt-query-target")
    status = rig.e.execute("nx_operation_status", {"operation_id": "receipt-query-target"})
    for key in ["operation_id", "session_id", "mutation_outcome"]:
        assert status[key] == committed[key]
    assert status["query_operation_id"] != status["operation_id"]
    missing = rig.e.execute("nx_operation_status", {"operation_id": "receipt-not-recorded"})
    assert missing["operation_id"] == "receipt-not-recorded"
    assert missing["mutation_outcome"] == "unknown" and missing["session_id"] is None


def test_capability_filters_and_unit_conventions(rig):
    from types import SimpleNamespace

    rig.session.DexManager = SimpleNamespace()
    rig.session.Measurement = SimpleNamespace()
    one = rig.e._capabilities(tool="nx_close_part")
    assert list(one["tools"]) == ["nx_close_part"] and one["units"] is None
    group = rig.e._capabilities(prefix="nx_sheet")
    assert all(name.startswith("nx_sheet") for name in group["tools"])
    assert group["tool_count"] < group["total_tool_count"]
    for params in [{"tool": "nx_missing"}, {"tool": "nx_close_part", "prefix": "nx_"}]:
        with pytest.raises(NXToolError):
            rig.e._capabilities(**params)
