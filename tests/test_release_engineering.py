"""Preflight, ownership and verification failures for native release engineering."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from nx_mcp.release_engineering import sheet_point
from nx_mcp.runtime import NXToolError
from tests.test_freeform_manufacturing import ff as _fixture


@pytest.fixture
def ff(rig, monkeypatch):
    value = _fixture.__wrapped__(rig, monkeypatch)
    value.part.Annotations = MagicMock()
    value.part.DraftingViews = MagicMock()
    value.part.Dimensions = []
    return value


@pytest.mark.parametrize("value", [None, [], [1], [1, 2, 3], [float("nan"), 2], ["x", 2]])
def test_invalid_sheet_point(value):
    with pytest.raises((NXToolError, ValueError)):
        sheet_point(value)


def test_view_edit_preflight_and_native_readback(ff):
    obj = NS(Tag=2)
    ff.e._drawing_object = lambda *_: obj
    sheet = MagicMock()
    ff.e._view_sheet = lambda _: sheet
    ff.uf.Draw = MagicMock()
    with pytest.raises(NXToolError):
        ff.e._edit_drawing_view("view")
    with pytest.raises(NXToolError):
        ff.e._edit_drawing_view("view", scale=-1)
    ff.uf.Draw.SetViewScale.assert_not_called()
    ff.e._drawing_view_info = lambda _: {"scale": 2, "position": [5, 6], "object": {"id": "view"}}
    assert ff.e._edit_drawing_view("view", position=[5, 6], scale=2)["modified"] == [{"id": "view"}]
    with pytest.raises(NXToolError, match="alignment or scale"):
        ff.e._edit_drawing_view("view", position=[7, 8])


def test_section_rejects_nonplanar_or_parallel_directions(ff):
    for step, arrow in [([1, 0, 0], [1, 0, 0]), ([0, 0, 1], [1, 0, 0])]:
        with pytest.raises(NXToolError, match="perpendicular"):
            ff.e._add_section_drawing_view("view", "edge", [10, 20], step, arrow)
    ff.uf.Draw.CreateSimpleSxview.assert_not_called()


@pytest.mark.parametrize(
    "anchor",
    [{}, {"version": 2}, {"version": 1, "owner_part": "part", "kind": "component", "handle": "h"}],
)
def test_anchor_schema_rejects_foreign_kinds(ff, anchor):
    with pytest.raises(NXToolError):
        ff.e._resolve_geometry_anchor(anchor)
    ff.uf.Tag.AskTagOfHandle.assert_not_called()


def test_anchor_owner_and_missing_entity_fail_closed(ff):
    part = ff.e._work_part()
    anchor = {"version": 1, "owner_part": part.FullPath, "kind": "body", "handle": "native"}
    with pytest.raises(NXToolError, match="another part"):
        ff.e._resolve_geometry_anchor({**anchor, "owner_part": "wrong"})
    ff.uf.Tag.AskTagOfHandle.return_value = 912345
    with pytest.raises(NXToolError, match="no longer exists"):
        ff.e._resolve_geometry_anchor(anchor)
    body = list(part.Bodies)[0]
    ff.uf.Tag.AskTagOfHandle.return_value = body.Tag
    assert ff.e._resolve_geometry_anchor(anchor)["object"]["kind"] == "body"


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "approval"},
        {"rows": []},
        {"rows": [["one"]]},
        {"widths": [-1, 20]},
        {"rows": [[1, "two"]]},
        {"rows": [["x" * 1025, "two"]]},
        {"row_height": 0},
    ],
)
def test_table_preflight_prevents_partial_authoring(ff, overrides):
    args = {
        "drawing": "sheet",
        "kind": "revision",
        "rows": [["A", "Initial"]],
        "widths": [10, 20],
        "position": [0, 0],
    }
    with pytest.raises(NXToolError):
        ff.e._drawing_table(**{**args, **overrides})
    ff.part.Annotations.TableSections.CreateTableSectionBuilder.assert_not_called()


@pytest.fixture
def drawing(ff, monkeypatch):
    import sys

    from tests.fakes import Object, point

    d = NS(
        DrawingSheet=NS(Unit=NS(Millimeters=2, Inches=1)),
        DetailViewBuilder=NS(Types=NS(Circular=1)),
        ViewScaleBuilder=NS(Type=NS(Ratio=1)),
    )
    monkeypatch.setitem(sys.modules, "NXOpen.Drawings", d)
    ff.nx.Drawings = d
    ff.nx.NXObject = NS(AttributeType=NS(String=1))
    ff.uf.Tag.AskHandleFromTag.side_effect = lambda tag: str(tag)
    sheet = Object("Sheet")
    sheet.Length, sheet.Height, sheet.Units = 297, 210, 2
    sheet.Open = MagicMock()
    view = Object("Top")
    view.OwningPart = ff.part
    view.GetDrawingReferencePoint = lambda: point(100, 90, 0)
    view.Matrix = NS(Xx=1, Xy=0, Xz=0)
    view.HasUserAttribute = MagicMock(return_value=False)
    view.SetAttribute = MagicMock()
    view.UpdateAutomaticViewBound = MagicMock()
    import contextlib

    ff.e._drawing_save_context = lambda *a, **k: contextlib.nullcontext()
    sheet.GetDraftingViews = lambda: [view]
    ff.part.DrawingSheets = [sheet]
    ff.e._drawing_object = lambda ref, kind: sheet if kind == "drawing_sheet" else view
    ff.uf.Draw.AskViewBorders.return_value = [60, 70, 140, 110]
    ff.uf.Draw.AskViewScale.return_value = (0, 1.0)
    ff.uf.View.MapModelToDrawing.side_effect = lambda _, p: [p[0] + 100, p[1] + 90]
    ff.part.SettingsManager = MagicMock()
    style = ff.part.SettingsManager.CreateDrawingEditViewSettingsBuilder.return_value.ViewStyle
    from nx_mcp.drawing_preferences import STYLE_PROPERTIES

    for key, (group, prop) in STYLE_PROPERTIES.items():
        setattr(getattr(style, group), prop, 1 if key.endswith(("font", "width")) else True)
    ff.sheet, ff.view = sheet, view
    return ff


def test_sheet_units_and_physical_point_conversion(drawing):
    f = drawing
    assert f.e._drawing_view_info("v")["inside_sheet"]
    f.sheet.Units = 1
    assert f.e._sheet_units(f.sheet) == "in"
    result = f.e._sheet_point3d(f.sheet, [1, 2])
    assert (result.X, result.Y) == (25.4, 50.8)
    assert f.e._drawing_coordinates(f.sheet, [1, 2])["position_mm"] == [25.4, 50.8]
    f.part.PartUnits = "inch"
    f.sheet.Units = 2
    assert pytest.approx(1) == f.e._sheet_point3d(f.sheet, [25.4, 50.8]).X
    f.sheet.Units = 912
    with pytest.raises(NXToolError, match="Unknown native"):
        f.e._sheet_units(f.sheet)


def test_view_ownership_and_native_placement_verification(drawing):
    f = drawing
    f.e._place_drawing_view(f.view, f.sheet, [100, 90])
    with pytest.raises(NXToolError, match="placement differs"):
        f.e._place_drawing_view(f.view, f.sheet, [100, 91])
    f.sheet.GetDraftingViews = lambda: []
    with pytest.raises(NXToolError, match="one work-part sheet"):
        f.e._view_sheet(f.view)


def test_detail_maps_model_boundary_to_sheet_and_releases_builder(drawing):
    f = drawing
    b = MagicMock()
    b.Commit.return_value = f.view
    f.part.DraftingViews.CreateDetailViewBuilder.return_value = b
    f.e._edit_drawing_view = lambda *a, **k: {"scale": k["scale"]}
    assert f.e._add_detail_drawing_view("v", [0, 0, 0], 5, [100, 100], 3)["scale"] == 3
    calls = f.part.Points.CreatePoint.call_args_list
    assert calls[0].args[0].X == 100 and calls[1].args[0].X == 105
    b.Destroy.assert_called_once()
    b.Validate.return_value = False
    with pytest.raises(NXToolError, match="did not validate"):
        f.e._add_detail_drawing_view("v", [0, 0, 0], 5, [100, 100])
    assert b.Destroy.call_count == 2


def test_section_native_associativity_and_explicit_scale(drawing):
    f = drawing
    f.nx.UF.Drf = NS(Object=lambda: NS(), AssocType=NS(ARC_CENTER=2, END_POINT=1))
    f.nx.TaggedObjectManager = NS(GetTaggedObject=lambda _: f.view)
    f.e._edit_drawing_view = lambda *a, **k: {"scale": k["scale"]}
    face_ref = f.ref(f.body.GetEdges()[0], "edge")
    for assoc, expected, modifier in [("start", 1, 1), ("end", 1, 2), ("arc_center", 2, 0)]:
        result = f.e._add_section_drawing_view(
            "v", face_ref, [100, 100], [1, 0, 0], [0, 1, 0], 2, assoc
        )
        assert result["scale"] == 2
        arg = f.uf.Draw.CreateSimpleSxview.call_args.args[5]
        assert (arg.ObjectAssocType, arg.ObjectAssocModifier) == (expected, modifier)
    with pytest.raises(NXToolError, match="cut_association"):
        f.e._add_section_drawing_view("v", face_ref, [100, 100], [1, 0, 0], [0, 1, 0], 2, "bad")


def test_detail_refresh_requires_surviving_parent(drawing):
    import json

    f = drawing
    f.view.HasUserAttribute.return_value = True
    f.view.GetStringAttribute = lambda _: json.dumps(
        {"parent": "handle", "center": [0, 0, 0], "radius": 5}
    )
    f.uf.Tag.AskTagOfHandle.return_value = f.view.Tag
    f.e._refresh_detail_boundaries()
    f.part.DraftingViews.CreateDetailViewBuilder.return_value.Commit.assert_called_once()
    f.uf.Tag.AskTagOfHandle.return_value = -100
    with pytest.raises(NXToolError, match="parent no longer"):
        f.e._refresh_detail_boundaries()


def test_table_edits_preserve_native_identity_and_evaluated_cells(drawing):
    from tests.fakes import Object

    f = drawing
    section = Object("Revision")
    section.OwningPart, section.IsOccurrence = f.part, False
    attrs = {}
    section.SetAttribute = lambda k, v: attrs.__setitem__(k, v)
    section.HasUserAttribute = lambda k, *_: k in attrs
    section.GetStringAttribute = lambda k: attrs[k]
    sheet_attrs = {}
    sheet = f.e._drawing_object("s", "drawing_sheet")
    sheet.SetAttribute = lambda k, v: sheet_attrs.__setitem__(k, v)
    sheet.HasUserAttribute = lambda k, *_: k in sheet_attrs
    sheet.GetStringAttribute = lambda k: sheet_attrs[k]
    section.AnnotationOrigin = None
    b = f.part.Annotations.TableSections.CreateTableSectionBuilder.return_value
    b.Commit.return_value = section
    f.uf.Tag.AskHandleFromTag.side_effect = lambda t: str(t)
    tab = f.uf.Tabnot
    data = [["", ""]]
    tab.AskNmColumns.return_value = 2
    tab.AskNmRows.side_effect = lambda _: len(data)
    tab.AskNthRow.side_effect = lambda _, i: i
    tab.AskNthColumn.side_effect = lambda _, i: i
    tab.AskCellAtRowCol.side_effect = lambda r, c: (r, c)
    tab.SetCellText.side_effect = lambda cell, v: data[cell[0]].__setitem__(cell[1], v)
    tab.AskEvaluatedCellText.side_effect = lambda cell: data[cell[0]][cell[1]]
    tab.AddRow.side_effect = lambda *_: data.append(["", ""])
    tab.RemoveRow.side_effect = lambda i: data.pop(i)
    args = {
        "drawing": "s",
        "kind": "revision",
        "rows": [["REV", "TEXT"], ["A", "Initial"]],
        "widths": [20, 70],
        "position": [20, 250],
    }
    result = f.e._drawing_table(**args)
    assert result["rows"] == args["rows"]
    ref = result["table"]["id"]
    args["rows"] = [["B", "Changed"]]
    assert f.e._drawing_table(**args, table=ref)["table"]["id"] == ref
    assert data == [["B", "Changed"]]
    args["kind"] = "title_block"
    with pytest.raises(NXToolError, match="same kind"):
        f.e._drawing_table(**args, table=ref)
    assert b.Commit.call_count == 1


def test_assembly_refresh_unsatisfied_constraints_releases_network(drawing, monkeypatch):
    import sys

    f = drawing
    monkeypatch.setitem(
        sys.modules, "NXOpen.Positioning", NS(Constraint=NS(SolverStatus=NS(Solved=1)))
    )
    f.e._explosion_context = lambda: f.part
    constraint = NS(GetConstraintStatus=lambda: 2)
    f.e._assembly_constraints = lambda _: [constraint]
    positioner = MagicMock()
    f.part.ComponentAssembly.Positioner = positioner
    with pytest.raises(NXToolError, match="did not solve"):
        f.e._update_assembly_documentation()
    positioner.ClearNetwork.assert_called_once()
    positioner.EndAssemblyConstraints.assert_called_once()
    f.part.Annotations.PartsLists = []
    f.e._assembly_constraints = lambda _: []
    f.e._explosions = lambda _: []
    f.e._refresh_annotations = lambda: {"updated_count": 0}
    result = f.e._update_assembly_documentation()
    assert result["health"]["healthy"] and len(result["views"]) == 1


def test_native_mass_unit_is_explicit(ff):
    props = NS(Volume=8193.532, Dispose=MagicMock())
    ff.part.MeasureManager.NewMassProperties = lambda *a: props
    result = ff.e._measure_volume()
    assert props.InformationUnit == ff.nx.MeasureBodies.AnalysisUnit.KilogramMillimeter
    assert result["volume_mm3"] == 8193.532
    props.Dispose.assert_called_once()


def test_retained_dimensions_are_not_valid_measurements(drawing):
    from tests.fakes import Object, point

    d = Object("Retained dimension")
    d.ComputedSize, d.IsRetained, d.AnnotationOrigin = 8, True, point(0, 0, 0)
    drawing.part.Dimensions = [d]
    result = drawing.e._list_dimensions()["dimensions"][0]
    assert result["retained"] and not result["measurement_valid"]
    d.IsRetained = False
    assert drawing.e._list_dimensions()["dimensions"][0]["measurement_valid"]
