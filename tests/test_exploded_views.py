"""Observed NX explosion contracts; native fixtures separately verify NX geometry."""

import copy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp.hardened import IDENTITY, matmul, matvec, rows, transpose, xyz
from nx_mcp.runtime import NXToolError
from tests.fakes import Collection, Component, Object, point

pytestmark = pytest.mark.fake_nx


def compose(a, b):
    ar, at = a
    br, bt = b
    return matmul(ar, br), [x + y for x, y in zip(matvec(ar, bt), at, strict=True)]


def inverse(pose):
    rotation, position = pose
    inv = transpose(rotation)
    return inv, matvec(inv, [-x for x in position])


@pytest.fixture
def explosions(rig):
    r = rig
    r.e._sheet_units = lambda _: "mm"
    r.e._view_sheet = lambda _: None
    r.e._place_drawing_view = Mock()
    r.uf.Disp = NS(RegenerateDisplay=Mock())
    r.uf.View = NS(AskViewDependentStatus=lambda _: (0, ""))
    root = Component("root")
    parent = Component("parent", parent=root)
    leaf = Component("leaf", parent=parent)
    base = Component("base", parent=root)
    for c, pos in [(parent, [10, 20, 30]), (leaf, [10, 40, 30])]:
        c.position = point(*pos)
        c.rotation = r.e._nx_matrix([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    r.part.ComponentAssembly.RootComponent = root
    for c in (root, parent, leaf, base):
        c.OwningPart = r.part
    r.parent, r.leaf, r.base = parent, leaf, base
    exs = Collection()
    r.part.ComponentAssembly.Explosions = exs
    r.part.DrawingSheets = Collection()
    r.part.DrawingSheets.CurrentDrawingSheet = None
    r.part.Drafting = NS(ExitDraftingApplication=Mock())
    r.part.ModelingViews = Collection()
    work = Object("Work")
    work.OwningPart = r.part
    work.Fit = Mock()
    r.part.ModelingViews.append(work)
    r.part.ModelingViews.WorkView = work
    r.part.ModelingViews.FindObject = lambda name: work
    r.part.DraftingViews = Collection()
    r.part.DraftingViews.UpdateViews = Mock()
    association = {}

    class ExplodedComponent:
        def __init__(self, component, explosion):
            self.component, self.explosion = component, explosion

        def GetComponent(self):
            return self.component

        def GetChildren(self):
            return [ExplodedComponent(c, self.explosion) for c in self.component.GetChildren()]

        def GetPosition(self):
            c = self.component
            baseline = rows(c.rotation), xyz(c.position)
            if c.Parent:
                pp, pr = ExplodedComponent(c.Parent, self.explosion).GetPosition()
                inherited = compose(
                    (rows(pr), xyz(pp)), inverse((rows(c.Parent.rotation), xyz(c.Parent.position)))
                )
                baseline = compose(inherited, baseline)
            delta = self.explosion.deltas.get(c.Tag, IDENTITY4)
            rotation, position = compose(
                baseline, ([v[:3] for v in delta[:3]], [v[3] for v in delta[:3]])
            )
            return point(*position), r.e._nx_matrix(rotation)

    class Explosion(Object):
        def __init__(self, name):
            super().__init__(name)
            self.OwningPart = r.part
            self.deltas = {}
            self.RootComponent = ExplodedComponent(root, self)

        def Delete(self):
            exs.remove(self)

    def create(name):
        ex = Explosion(name)
        exs.append(ex)
        return ex

    exs.Create = Mock(side_effect=create)
    r.ex = create("Service")

    def ex_by_tag(tag):
        return next(e for e in exs if e.Tag == tag)

    r.uf.Assem = NS(
        AskViewExplosion=lambda tag: association.get(tag, 0),
        SetViewExplosion=Mock(side_effect=lambda view, ex: association.__setitem__(view, ex)),
        UnexplodeComponent=Mock(side_effect=lambda ex, c: ex_by_tag(ex).deltas.pop(c, None)),
        ExplodeComponent=Mock(
            side_effect=lambda ex, c, transform: ex_by_tag(ex).deltas.__setitem__(c, transform)
        ),
    )
    r.exref = r.ref(r.ex, "explosion")
    r.pref, r.lref, r.bref = [r.ref(c, "component") for c in (parent, leaf, base)]
    r.association = association
    original_mark, original_undo = r.session.SetUndoMark, r.session.UndoToMark
    snapshots = {}

    def mark(*args):
        value = original_mark(*args)
        snapshots[value] = list(exs), [(e, copy.deepcopy(e.deltas)) for e in exs], dict(association)
        return value

    def undo(value, *args):
        original_undo(value, *args)
        saved, poses, views = snapshots[value]
        exs[:] = saved
        for e, delta in poses:
            e.deltas = delta
        association.clear()
        association.update(views)

    r.session.SetUndoMark = mark
    r.session.UndoToMark = undo
    return r


IDENTITY4 = [IDENTITY[i] + [0] for i in range(3)] + [[0, 0, 0, 1]]


def test_native_local_transform_converted_to_absolute_nested_pose(explosions):
    r = explosions
    placements = [
        {
            "component": r.lref,
            "translation": [70, 60, 50],
            "rotation_matrix": [[-1, 0, 0], [0, -1, 0], [0, 0, 1]],
        },
        {"component": r.pref, "translation": [50, 20, 30]},
    ]
    result = r.e.execute(
        "nx_edit_explosion",
        {"explosion": r.exref, "placements": placements, "operation_id": "layout-01"},
    )
    assert result["assembled_placements_unchanged"]
    assert result["affected_component_count"] == 2
    actual = {c["occurrence_path"][-1]: c for c in result["components"]}
    assert actual["leaf"]["translation"] == [70, 60, 50]
    assert actual["parent"]["translation"] == [50, 20, 30]
    assert xyz(r.parent.position) == [10, 20, 30]
    assert r.ex.deltas[r.parent.Tag][:3] == [[1, 0, 0, 0], [0, 1, 0, -40], [0, 0, 1, 0]]
    calls = r.uf.Assem.ExplodeComponent.call_count
    assert r.e.execute(
        "nx_edit_explosion",
        {"explosion": r.exref, "placements": placements, "operation_id": "layout-01"},
    )["replayed"]
    assert r.uf.Assem.ExplodeComponent.call_count == calls
    assert r.e._edit_explosion(r.exref, placements)["affected_component_count"] == 0
    reset = r.e._edit_explosion(r.exref, reset_components=[r.lref])
    assert reset["components"][0]["translation"] == [50, 40, 30]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"placements": []},
        {"placements": True},
        {"reset_components": "bad"},
        {"placements": [{}]},
        {"placements": [{"component": "p", "translation": [1, 2, 3], "ignored": 1}]},
        {"placements": [{"component": "p", "translation": [1, 2]}]},
        {"placements": [{"component": "p", "translation": [1, 2, float("inf")]}]},
        {
            "placements": [
                {
                    "component": "p",
                    "translation": [1, 2, 3],
                    "rotation_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, -1]],
                }
            ]
        },
        {
            "placements": [
                {"component": "p", "translation": [1, 2, 3]},
                {"component": "p", "translation": [2, 3, 4]},
            ]
        },
        {"placements": [{"component": "p", "translation": [1, 2, 3]}], "reset_components": ["p"]},
        {"reset_components": [False]},
        {"reset_components": ["missing"]},
        {"reset_components": ["p"] * 1001},
    ],
)
def test_preflight_rejects_all_invalid_items_before_native_mutation(explosions, payload):
    r = explosions

    def substitute(v):
        if isinstance(v, list):
            return [substitute(x) for x in v]
        if isinstance(v, dict):
            return {k: substitute(x) for k, x in v.items()}
        return r.pref if v == "p" else v

    with pytest.raises(NXToolError):
        r.e._edit_explosion(r.exref, **substitute(payload))
    r.uf.Assem.UnexplodeComponent.assert_not_called()
    r.uf.Assem.ExplodeComponent.assert_not_called()


def test_native_partial_failure_rolls_back_all_placements(explosions):
    r = explosions
    apply = r.uf.Assem.ExplodeComponent.side_effect

    def fail(ex, component, matrix):
        if component == r.leaf.Tag:
            raise RuntimeError("native failure")
        apply(ex, component, matrix)

    r.uf.Assem.ExplodeComponent.side_effect = fail
    with pytest.raises(NXToolError) as error:
        r.e.execute(
            "nx_edit_explosion",
            {
                "explosion": r.exref,
                "placements": [
                    {"component": r.pref, "translation": [50, 20, 30]},
                    {"component": r.lref, "translation": [70, 60, 50]},
                ],
            },
        )
    assert error.value.details["mutation_outcome"] == "rolled_back"
    assert r.ex.deltas == {}


def test_native_readback_mismatch_is_not_reported_as_success(explosions):
    r = explosions
    r.uf.Assem.ExplodeComponent.side_effect = None
    with pytest.raises(NXToolError, match="differs"):
        r.e._edit_explosion(r.exref, [{"component": r.pref, "translation": [100, 20, 30]}])


def test_changed_real_assembly_is_not_reported_as_safe(explosions):
    r = explosions
    apply = r.uf.Assem.ExplodeComponent.side_effect

    def invalid(ex, component, transform):
        apply(ex, component, transform)
        r.base.position = point(100, 0, 0)

    r.uf.Assem.ExplodeComponent.side_effect = invalid
    with pytest.raises(NXToolError, match="assembled placements"):
        r.e._edit_explosion(r.exref, [{"component": r.pref, "translation": [50, 20, 30]}])


def test_listing_pagination_and_read_only_history(explosions):
    r = explosions
    result = r.e.execute("nx_explosion_info", {"explosion": r.exref, "limit": 1})
    assert result["total"] == 3 and result["next_offset"] == 1
    assert result["items"][0]["assembled_translation"] == [10, 20, 30]
    assert r.e._history == []
    assert r.e._list_explosions()["explosions"][0]["name"] == "Service"
    with pytest.raises(NXToolError):
        r.e._explosion_info(r.exref, limit=201)


@pytest.mark.parametrize(
    "name", ["", " ", " Service", "A/Other", "A\\Other", "A\nB", "a" * 133, "service"]
)
def test_invalid_or_duplicate_names_do_not_create(explosions, name):
    r = explosions
    with pytest.raises(NXToolError):
        r.e._create_explosion(name)
    r.part.ComponentAssembly.Explosions.Create.assert_not_called()


def test_create_and_delete_track_typed_reference_lifecycle(explosions):
    r = explosions
    result = r.e.execute("nx_create_explosion", {"name": "Maintenance"})
    ref = result["object"]["id"]
    assert result["component_count"] == 3
    assert any(x["kind"] == "explosion" for x in result["changes"]["created"])
    deleted = r.e.execute("nx_delete_explosion", {"explosion": ref})
    assert deleted["deleted"][0]["id"] == ref
    with pytest.raises(NXToolError):
        r.e._explosion(ref)


def test_show_hide_and_guarded_deletion(explosions):
    r = explosions
    shown = r.e._show_explosion(r.exref)
    assert shown["view_kind"] == "modeling"
    with pytest.raises(NXToolError, match="Detach"):
        r.e._delete_explosion(r.exref)
    r.e._show_explosion()
    r.e._delete_explosion(r.exref)
    assert not r.part.ComponentAssembly.Explosions


def test_saved_model_and_drawing_view_targets(explosions):
    r = explosions
    drawing = Object("Assembly view")
    drawing.OwningPart = r.part
    r.part.DraftingViews.append(drawing)
    ref = r.ref(drawing, "drawing_view")
    r.e._show_explosion(r.exref, drawing_view=ref)
    r.part.DraftingViews.UpdateViews.assert_called_with([drawing])
    r.e._edit_explosion(r.exref, [{"component": r.pref, "translation": [50, 20, 30]}])
    assert r.e._explosion_info(r.exref)["views"][0]["object"]["id"] == ref
    saved = r.ref(r.part.ModelingViews.WorkView, "modeling_view")
    r.e._show_explosion(r.exref, model_view=saved)
    with pytest.raises(NXToolError):
        r.e._show_explosion(r.exref, ref, saved)
    r.uf.Assem.SetViewExplosion.side_effect = None
    with pytest.raises(NXToolError, match="did not accept"):
        r.e._show_explosion(None, model_view=saved)


@pytest.mark.parametrize("case", ["sketch", "display", "owner", "suppressed", "foreign"])
def test_context_and_occurrence_ownership_guards(explosions, case):
    r = explosions
    if case == "sketch":
        r.session.ActiveSketch = object()
    elif case == "display":
        r.session.Parts.Display = object()
    elif case == "owner":
        r.ex.OwningPart = object()
    elif case == "suppressed":
        r.parent.IsSuppressed = True
    else:
        r.lref = r.ref(Component("foreign"), "component")
    with pytest.raises(NXToolError):
        r.e._edit_explosion(r.exref, [{"component": r.lref, "translation": [0, 0, 0]}])
    r.uf.Assem.UnexplodeComponent.assert_not_called()


def test_empty_assembly_explosion_rejected(explosions):
    r = explosions
    r.part.ComponentAssembly.RootComponent.children.clear()
    with pytest.raises(NXToolError, match="assembly part"):
        r.e._create_explosion("Empty")


@pytest.fixture
def drawing_save(rig):
    r = rig
    sheet = Object("Sheet")
    r.part.DrawingSheets = Collection([sheet])
    r.part.DrawingSheets.CurrentDrawingSheet = None
    r.part.SaveOptions = NS(DrawingCgmData=True)

    def show():
        r.part.DrawingSheets.CurrentDrawingSheet = sheet

    def exit_drawing():
        r.part.DrawingSheets.CurrentDrawingSheet = None

    sheet.Open = Mock(side_effect=show)
    r.part.Drafting = NS(ExitDraftingApplication=Mock(side_effect=exit_drawing))
    r.sheet = sheet
    return r


def test_save_preserves_cgm_and_restores_modeling_without_dirtying(drawing_save):
    r = drawing_save
    original = r.part.Save

    def save(*args):
        assert r.part.DrawingSheets.CurrentDrawingSheet is r.sheet
        assert r.part.SaveOptions.DrawingCgmData is True
        return original(*args)

    r.part.Save = save
    r.e._save_part()
    assert r.part.DrawingSheets.CurrentDrawingSheet is None
    assert not r.part.IsModified
    r.part.Drafting.ExitDraftingApplication.assert_called_once()


def test_save_restores_presentation_on_save_error(drawing_save):
    r = drawing_save
    r.part.Save = Mock(side_effect=RuntimeError("disk error"))
    with pytest.raises(NXToolError) as error:
        r.e._save_part()
    assert error.value.details["native_save_errors"] == [{"message": "disk error"}]
    assert r.part.DrawingSheets.CurrentDrawingSheet is None


def test_save_preserves_existing_active_drawing(drawing_save):
    r = drawing_save
    r.part.DrawingSheets.CurrentDrawingSheet = r.sheet
    r.e._save_part()
    assert r.part.DrawingSheets.CurrentDrawingSheet is r.sheet
    r.part.Drafting.ExitDraftingApplication.assert_not_called()


def test_save_does_not_change_disabled_cgm_preference(drawing_save):
    r = drawing_save
    r.part.SaveOptions.DrawingCgmData = False
    r.e._save_part()
    r.sheet.Open.assert_not_called()
    assert r.part.SaveOptions.DrawingCgmData is False


def test_save_restore_failure_is_explicitly_partial(drawing_save):
    r = drawing_save
    r.part.Drafting.ExitDraftingApplication.side_effect = RuntimeError("restore failure")
    with pytest.raises(NXToolError) as error:
        r.e.execute("nx_save_part", {})
    assert error.value.code == "NX_SAVE_VIEW_RESTORE_FAILED"
    assert error.value.details["mutation_outcome"] == "partial"


def test_drawing_save_active_sketch_rejected_before_open(drawing_save):
    r = drawing_save
    r.session.ActiveSketch = object()
    with pytest.raises(NXToolError):
        r.e._save_part()
    r.sheet.Open.assert_not_called()


def test_save_as_and_close_use_drawing_preview_context(drawing_save, tmp_path):
    r = drawing_save
    r.e._save_as(str(tmp_path / "copy.prt"))
    assert r.part.DrawingSheets.CurrentDrawingSheet is None
    r.e._close_part(save=True)
    assert r.sheet.Open.call_count == 2
    assert r.part not in r.session.Parts


def test_save_hidden_part_restores_original_work_and_display(drawing_save, monkeypatch):
    r = drawing_save
    other = Object("Original")
    other.FullPath = "original.prt"
    r.session.Parts.Work = r.session.Parts.Display = other

    def activate(ref, work, display):
        obj = r.e.objects.resolve(ref, expected_kind="part")
        if work:
            r.session.Parts.Work = obj
        if display:
            r.session.Parts.Display = obj

    monkeypatch.setattr(r.e, "_activate_part", activate)
    with r.e._drawing_save_context(r.part):
        assert r.session.Parts.Work is r.part and r.session.Parts.Display is r.part
    assert r.session.Parts.Work is other and r.session.Parts.Display is other


def test_modified_component_drawing_saved_once_before_parent(drawing_save):
    r = drawing_save
    prototype = r.part
    prototype.IsModified = True
    parent = NS()
    r.e._walk_components = lambda p: [
        (NS(Prototype=prototype), ["a"]),
        (NS(Prototype=prototype), ["b"]),
    ]
    save = Mock(wraps=prototype.Save)
    prototype.Save = save
    r.e._save_component_drawing_previews(parent)
    save.assert_called_once()
    assert not prototype.IsModified


@pytest.fixture
def assembly_drawing(explosions):
    r = explosions
    sheet = Object("Sheet")
    sheet.OwningPart = r.part
    sheet.GetScale = lambda: (2.0, 1.0)
    sheet.Open = Mock()
    r.part.DrawingSheets.append(sheet)
    r.sheetref = r.ref(sheet, "drawing_sheet")
    r.builder = NS(
        SelectModelView=NS(),
        Placement=NS(Placement=NS(SetValue=Mock())),
        Destroy=Mock(),
        Scale=NS(Type=NS(Ratio=1)),
        Style=NS(ViewStyleHiddenLines=NS(), ViewStyleVisibleLines=NS()),
    )

    def commit():
        view = Object("Base")
        view.SetAttribute = Mock()
        view.OwningPart = r.part
        r.part.DraftingViews.append(view)
        return view

    r.builder.Commit = Mock(side_effect=commit)
    r.part.DraftingViews.CreateBaseViewBuilder = lambda _: r.builder
    return r


def test_assembly_drawing_explosion_is_explicit_and_verified(assembly_drawing):
    r = assembly_drawing
    result = r.e._add_base_view(r.sheetref, scope="assembly", explosion=r.exref, position=[80, 100])
    view = r.e._resolve(result["object"]["id"], {"drawing_view"})
    assert r.association[view.Tag] == r.ex.Tag
    r.part.DraftingViews.UpdateViews.assert_called_with([view])
    r.builder.Destroy.assert_called_once()
    assert result["scope"] == "assembly"
    assert result["explosion"]["id"] == r.exref


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"scope": "invalid"},
        {"scope": "assembly", "body": "body"},
        {"scope": "body", "body": "body", "explosion": "ex"},
        {"scope": "assembly", "view": "unknown"},
        {"scope": "assembly", "position": [1]},
    ],
)
def test_drawing_scope_validation_precedes_builder(assembly_drawing, kw):
    r = assembly_drawing
    with pytest.raises(NXToolError):
        r.e._add_base_view(r.sheetref, **kw)
    r.builder.Commit.assert_not_called()


def test_drawing_builder_destroyed_on_failure(assembly_drawing):
    r = assembly_drawing
    r.builder.Commit.side_effect = RuntimeError("native view failure")
    with pytest.raises(RuntimeError):
        r.e._add_base_view(r.sheetref, scope="assembly")
    r.builder.Destroy.assert_called_once()


def test_projected_view_inherits_explosion(assembly_drawing, monkeypatch):
    from nx_mcp.engineering import EngineeringMixin

    r = assembly_drawing
    parent = r.e._add_base_view(r.sheetref, scope="assembly", explosion=r.exref)["object"]["id"]
    child = Object("Projected")
    child.OwningPart = r.part
    r.part.DraftingViews.append(child)
    child_ref = r.ref(child, "drawing_view")
    monkeypatch.setattr(
        EngineeringMixin, "_add_projection_view", lambda *args: {"object": {"id": child_ref}}
    )
    result = r.e._add_projection_view(parent, "right")
    assert result["exploded"] and r.association[child.Tag] == r.ex.Tag
    r.part.DraftingViews.UpdateViews.assert_called_with([child])


@pytest.mark.parametrize(
    "extra",
    [
        {"translation": [True, 0, 0]},
        {"translation": ["1", 0, 0]},
        {"rotation_matrix": None},
        {"rotation_matrix": [1, 2, 3]},
        {"rotation_matrix": [[True, 0, 0], [0, 1, 0], [0, 0, 1]]},
    ],
)
def test_strict_pose_types(explosions, extra):
    r = explosions
    ex = r.e._reference(r.ex, "explosion", r.part, "Explosion")["id"]
    component = r.e._reference(r.base, "component", r.part, "Component")["id"]
    with pytest.raises(NXToolError, match="numeric"):
        r.e._edit_explosion(ex, [{"component": component, "translation": [1, 2, 3], **extra}])
    assert not r.ex.deltas


def test_forced_drawing_display_restores_sheet_on_failure(drawing_save):
    r = drawing_save
    r.part.SaveOptions.DrawingCgmData = False
    original = r.sheet
    r.part.DrawingSheets.CurrentDrawingSheet = original
    with (
        pytest.raises(RuntimeError, match="plot failed"),
        r.e._drawing_save_context(r.part, force_display=True),
    ):
        r.part.DrawingSheets.CurrentDrawingSheet = Object("Other sheet")
        raise RuntimeError("plot failed")
    assert r.part.DrawingSheets.CurrentDrawingSheet is original


def test_forced_drawing_display_restores_modeling_view(drawing_save):
    r = drawing_save
    r.part.SaveOptions.DrawingCgmData = False
    with r.e._drawing_save_context(r.part, force_display=True):
        assert r.part.DrawingSheets.CurrentDrawingSheet is r.sheet
    assert r.part.DrawingSheets.CurrentDrawingSheet is None
