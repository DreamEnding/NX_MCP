"""Contracts, preflight and transaction regressions; native geometry tested separately."""

import inspect
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock

import pytest

from nx_mcp import documentation_editing_server
from nx_mcp.hardened import READ_ONLY
from nx_mcp.runtime import NXToolError
from tests.test_freeform_manufacturing import ff as _freeform_fixture


@pytest.fixture
def ff(rig, monkeypatch):
    return _freeform_fixture.__wrapped__(rig, monkeypatch)


def test_contracts(rig):
    for name, fn in vars(documentation_editing_server).items():
        if name.startswith("nx_") and inspect.isfunction(fn):
            assert (
                inspect.signature(fn).parameters.keys()
                == inspect.signature(rig.e._handlers[name]).parameters.keys()
            )
    assert documentation_editing_server.READ_ONLY <= READ_ONLY


def test_catalog_reads_local_installed_table_only(ff, tmp_path, monkeypatch):
    directory = tmp_path / "UGII" / "modeling_standards"
    directory.mkdir(parents=True)
    (directory / "NX_Thread_Standard.xml").write_text(
        '<Root><ThreadedHole Standard="Fixture" Size="M6" Unit="mm" Method="CUT" RadialEngage="0.75" Pitch="1"/><ThreadedHole Standard="Fixture" Size="M8" Unit="mm"/></Root>'
    )
    monkeypatch.setenv("UGII_BASE_DIR", str(tmp_path))
    assert ff.e._thread_catalog()["items"] == ["Fixture"]
    assert ff.e._thread_catalog(standard="Fixture", limit=1)["next_offset"] == 1
    assert ff.e._thread_catalog(standard="Fixture", size="M6")["items"][0]["Pitch"] == "1"
    with pytest.raises(NXToolError, match="Select a standard"):
        ff.e._thread_catalog(size="M6")
    with pytest.raises(NXToolError):
        ff.e._thread_catalog(standard="absent")
    monkeypatch.delenv("UGII_BASE_DIR")
    with pytest.raises(NXToolError):
        ff.e._thread_catalog()


def test_standard_thread_requires_unambiguous_catalog_selection(ff):
    row = {"Standard": "Fixture", "Size": "M6", "Method": "CUT", "RadialEngage": "0.75"}
    ff.e._thread_rows = lambda: [row, {**row, "RadialEngage": "0.5"}]
    with pytest.raises(NXToolError) as error:
        ff.e._standard_thread("face", "start", "Fixture", "M6", 4)
    assert error.value.code == "NX_AMBIGUOUS_THREAD"
    ff.e._freeform_builder.assert_not_called()
    result = ff.e._standard_thread(
        ff.ref(ff.faces[0], "face"),
        ff.ref(ff.faces[1], "face"),
        "Fixture",
        "M6",
        4,
        radial_engage="0.75",
    )
    assert result["standard"] == "Fixture"
    assert ff.b.ThreadInput == ff.nx.Features.ThreadBuilder.Input.ThreadTable
    ff.b.Destroy.assert_called_once()


def test_column_preflight_prevents_mutations(ff):
    ff.e._parts_list_object = lambda _: NS(Tag=123)
    ff.uf.Tabnot.AskNmColumns.return_value = 3
    for args in [
        {"action": "append"},
        {"action": "edit", "index": 3},
        {"action": "remove", "index": 0, "title": "bad"},
        {"action": "append", "title": "x", "field": "x", "width": -1},
    ]:
        with pytest.raises(NXToolError):
            ff.e._parts_list_column("table", **args)
    ff.uf.Plist.CreateColumn.assert_not_called()
    ff.uf.Tabnot.RemoveColumn.assert_not_called()


def test_column_append_and_edit_use_native_preferences(ff, monkeypatch):
    ff.e._parts_list_object = lambda _: NS(Tag=123)
    ff.e._parts_list_info = lambda _: {"rows": [["one"]]}
    ff.uf.Tabnot.AskNmColumns.return_value = 3
    ff.uf.Plist.CreateColumn.return_value = 88
    ff.uf.Tabnot.AskNmHeaderRows.return_value = 1
    ff.nx.UF.Plist = NS(ColumnType=NS(COLUMN_TYPE_GENERAL=1))
    assert ff.e._parts_list_column("table", "append", title="Part", field="native field", width=30)[
        "rows"
    ] == [["one"]]
    ff.uf.Tabnot.AddColumn.assert_called_once_with(123, 88, 3)
    ff.uf.Plist.Update.assert_called_once_with(123)


def test_annotation_conflicting_edits_are_rejected(ff):
    ff.e._resolve = lambda *_: NS(AnnotationOrigin=None)
    for args in [{"delete": True, "name": "x"}, {}, {"name": ""}, {"position": [0, 1]}]:
        with pytest.raises(NXToolError):
            ff.e._edit_annotation("annotation", **args)
    ff.e._update_model.assert_not_called()


def test_managed_pmi_refresh_and_missing_source(ff):
    obj = Mock()
    obj.HasUserAttribute.return_value = True
    obj.GetStringAttribute.return_value = (
        '{"kind":"body","body":"body-handle","faces":[],"measured":{"thickness":2}}'
    )
    obj.AnnotationOrigin = ff.nx.Point3d(1, 2, 3)
    ff.nx.NXObject = NS(AttributeType=NS(String=1))
    ff.e._documentation_annotations = lambda _: [obj]
    ff.uf.Tag.AskTagOfHandle.return_value = ff.body.Tag
    ff.e._sm_manager = lambda: NS(GetBodyThickness=lambda _: 3)
    ff.e._sheet_metal_annotation = Mock(return_value={"modified": ["updated"]})
    assert ff.e._refresh_annotations()["updated"] == ["updated"]
    assert ff.e._sheet_metal_annotation.call_args.kwargs["automatic"]
    ff.uf.Tag.AskTagOfHandle.side_effect = RuntimeError("stale")
    with pytest.raises(NXToolError) as error:
        ff.e._refresh_annotations()
    assert error.value.code == "NX_STALE_ANNOTATION_SOURCE"
    obj.GetStringAttribute.return_value = "disabled"
    assert ff.e._refresh_annotations()["updated_count"] == 0


def test_annotation_refresh_failure_rolls_back_the_model_edit(rig):
    rig.e._handlers["nx_extrude"] = Mock(return_value={})
    rig.e._refresh_annotations = Mock(
        side_effect=NXToolError("NX_STALE_ANNOTATION_SOURCE", "stale source")
    )
    rig.session.UndoToMark = Mock()
    with pytest.raises(NXToolError) as error:
        rig.e.execute("nx_extrude", {})
    assert error.value.details["mutation_outcome"] == "rolled_back"
    rig.session.UndoToMark.assert_called_once()


def test_automatic_bend_table_rebuild_and_opt_out(ff):
    from tests.fakes import Object

    table = Object("bend table")
    builder = MagicMock()
    builder.Style.BendTable.AutomaticUpdate = True
    builder.Commit.return_value = table

    class Tables(list):
        def CreateBendTableBuilder(self, existing):
            assert existing is table
            return builder

    ff.part.Annotations = NS(BendTables=Tables([table]))
    ff.e._documentation_annotations = lambda _: []
    ff.e._table_cells = Mock(side_effect=[[["80"]], [["85"]]])
    assert ff.e._refresh_annotations()["updated_count"] == 1
    builder.Commit.assert_called_once()
    builder.Destroy.assert_called_once()
    builder.reset_mock()
    builder.Style.BendTable.AutomaticUpdate = False
    assert ff.e._refresh_annotations()["updated_count"] == 0
    builder.Commit.assert_not_called()
    builder.Destroy.assert_called_once()
