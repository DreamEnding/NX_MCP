"""Stateful display, clipping and solver tests at the installed NX API seam."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp.runtime import NXToolError
from nx_mcp.visual_tools import enum_name
from tests.fakes import Body, Component, Curve, Feature, Object, Part, Sketch, point

pytestmark = pytest.mark.fake_nx


def test_appearance_restores_per_face_attributes_and_blank_flags(rig):
    b = Body()
    b.faces[0].Color = 21
    b.faces[0].transparency = 15
    b.IsBlanked = True
    rig.part.Bodies.append(b)
    ref = rig.ref(b)
    before = rig.e._display_info([ref])["objects"]
    result = rig.e.execute("nx_set_display", {"objects": [ref], "color": "red", "transparency": 60})
    assert b.Color == 3 and b.faces[0].Color == 3 and b.faces[0].transparency == 60
    assert all(not m.ApplyToOwningParts for m in rig.modifications)
    rig.e.execute("nx_restore_display", {"restore_id": result["restore_id"]})
    assert rig.e._display_info([ref])["objects"] == before
    assert all(m.Dispose.call_count == 1 for m in rig.modifications)
    with pytest.raises(NXToolError):
        rig.e._restore_display(result["restore_id"])


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"color": "invalid"},
        {"color": "red", "color_index": 3},
        {"color_index": True},
        {"color_index": 0},
        {"color_index": 217},
        {"transparency": -1},
        {"transparency": 101},
        {"transparency": 1.5},
    ],
)
def test_invalid_appearance_preflight_never_applies(rig, params):
    with pytest.raises(NXToolError):
        rig.e._set_display(["unresolved"], **params)
    assert not rig.modifications


def test_snapshot_order_and_stale_member_fail_before_any_restore(rig):
    b = Body()
    rig.part.Bodies.append(b)
    ref = rig.ref(b)
    first = rig.e._set_visibility([ref], "hide")["restore_id"]
    second = rig.e._set_display([ref], color="gray")["restore_id"]
    with pytest.raises(NXToolError) as error:
        rig.e._restore_display(first)
    assert error.value.code == "NX_RESTORE_ORDER" and b.IsBlanked
    face_ref = rig.e._display_snapshots[second]["records"][1]["object"]["id"]
    rig.e.objects._objects.pop(face_ref)
    n = len(rig.modifications)
    with pytest.raises(NXToolError):
        rig.e._restore_display(second)
    assert len(rig.modifications) == n and b.Color == 5


def test_isolation_keeps_nested_ancestors_and_restores_hidden_siblings(rig):
    seed = Body()
    root = Component("root")
    nested = Component("nested", parent=root)
    child = Component("child", [seed], nested)
    other = Component("other", [Body()], root)
    rig.part.ComponentAssembly.RootComponent = root
    root.IsBlanked = True
    other.IsBlanked = True
    ref = rig.ref(child, "component")
    body = child.FindOccurrence(seed)
    result = rig.e._set_visibility([ref], "isolate")
    assert not body.IsBlanked and not child.IsBlanked and not nested.IsBlanked
    assert other.IsBlanked
    rig.e._restore_display(result["restore_id"])
    assert other.IsBlanked and root.IsBlanked and not body.IsBlanked
    assert rig.e._display_info([ref])["count"] == 2


def test_display_target_kinds_and_part_guards(rig, tmp_path):
    b = Body()
    feature = Feature(bodies=[b])
    sk = Sketch(rig.session)
    sk.geometry = [Curve()]
    assert rig.e._display_targets([rig.ref(feature, "feature")]) == [b]
    assert rig.e._display_targets([rig.ref(sk, "sketch")]) == sk.geometry
    face = rig.ref(b.faces[0], "face")
    with pytest.raises(NXToolError):
        rig.e._set_visibility([face], "hide")
    for mode in ["invalid", "isolate"]:
        with pytest.raises(NXToolError):
            rig.e._set_visibility([rig.ref(sk.geometry[0], "curve")], mode)
    with pytest.raises(NXToolError):
        rig.e._set_display([rig.ref(sk.geometry[0], "curve")], transparency=10)
    for values in [[], None, ["a"] * 1001]:
        with pytest.raises(NXToolError):
            rig.e._display_targets(values)
    with pytest.raises(NXToolError):
        rig.e._display_records([Curve()] * 10001)
    sk.geometry = []
    with pytest.raises(NXToolError):
        rig.e._display_targets([rig.ref(sk, "sketch")])
    old = rig.part
    Part(rig.session, tmp_path / "other.prt")
    rig.session.Parts.Work = old
    with pytest.raises(NXToolError):
        rig.e._display_info([face])


def section_builder(rig):
    builders = []

    def create(*args):
        target = args[0] if len(args) == 2 else Object("section")
        if not hasattr(target, "origin"):
            target.origin = point()
            target.normal = point(0, 0, 1)
        b = NS(
            GetOrigin=lambda: target.origin,
            GetNormal=lambda: target.normal,
            Destroy=Mock(),
            ShowClip=True,
            ShowCap=True,
        )
        b.SetName = target.SetName
        b.SetNormal = lambda v: setattr(target, "normal", v)
        b.SetOrigin = lambda v: setattr(target, "origin", v)

        def commit():
            if target not in rig.part.DynamicSections:
                rig.part.DynamicSections.append(target)
            return target

        b.Commit = Mock(side_effect=commit)
        builders.append(b)
        return b

    rig.part.DynamicSections.CreateSectionBuilder = create
    rig.part.DynamicSections.DeleteSections = lambda _, v: [
        rig.part.DynamicSections.remove(x) for x in v
    ]
    return builders


def test_sections_create_edit_toggle_delete_and_inspection_cleanup(rig):
    builders = section_builder(rig)
    result = rig.e._section_view([1, 2, 3], [0, 0, 4])
    ref = result["object"]["id"]
    assert result["normal"] == [0, 0, 1] and not result["geometry_changed"]
    assert result["retained_side"] == "negative_normal"
    info = rig.e._list_sections()
    assert info["count"] == 1 and info["sections"][0]["active"]
    assert not rig.session.marks
    with pytest.raises(NXToolError):
        rig.e._section_view([0, 0, 0], [1, 0, 0])
    rig.e._section_view([4, 5, 6], [1, 0, 0], section=ref, cap=False)
    rig.e._section_control(ref, "disable")
    assert not rig.part.ModelingViews.WorkView.DisplaySectioningToggle
    rig.e._section_control(ref, "enable")
    assert rig.part.ModelingViews.WorkView.DisplaySectioningToggle
    with pytest.raises(NXToolError):
        rig.e._section_control(ref, "bad")
    rig.e._section_control(ref, "delete")
    assert not rig.part.DynamicSections
    assert all(b.Destroy.call_count == 1 for b in builders)


@pytest.mark.parametrize(
    "origin,normal,name",
    [
        ([0, 0], [0, 0, 1], "x"),
        ([0, 0, 0], [0, 0, 0], "x"),
        ([0, 0, 0], [0, 0, 1], ""),
        ([0, 0, 0], [float("nan"), 0, 0], "x"),
    ],
)
def test_invalid_section_parameters_do_not_create_builder(rig, origin, normal, name):
    builders = section_builder(rig)
    with pytest.raises(NXToolError):
        rig.e._section_view(origin, normal, name=name)
    assert not builders


@pytest.mark.parametrize(
    "status,dof,expected", [(1, 4, 4), (2, 0, 0), (3, -1, None), (999, 10, None)]
)
@pytest.mark.parametrize("active", [True, False])
def test_solver_status_and_constraint_links_preserve_edit_state(rig, status, dof, expected, active):
    sk = Sketch(rig.session)
    sk.status = (status, dof)
    curve = Curve()
    sk.geometry = [curve]
    constraint = Object()
    constraint.ConstraintType = 1
    from tests.test_authoring_review import Expression

    rig.nx.Expression = NS(UnitsOption=NS(Expression="expression"))
    constraint.AssociatedExpression = Expression("p1")
    constraint.AssociatedExpression.Value = 254.0
    constraint.AssociatedExpression.expression_value = 10.0
    sk.constraints = [constraint]
    rig.part.Sketches.append(sk)
    region = NS(Commit=Mock(), Destroy=Mock())
    rig.part.Sketches.CreateWorkRegionBuilder = lambda: region
    if active:
        rig.session.ActiveSketch = sk
    result = rig.e._sketch_diagnostics(rig.ref(sk, "sketch"))
    assert result["remaining_degrees_of_freedom"] == expected
    assert result["constraints"][0]["expression"]["value"] == 10
    assert result["geometry"][0]["constraints"] == [result["constraints"][0]["object"]["id"]]
    assert rig.session.ActiveSketch == (sk if active else None)
    assert not rig.session.marks and region.Destroy.call_count == 1
    assert region.Scope == "all" and result["conflicting_constraints"] is None


def test_diagnostics_native_failure_restores_activation_and_marks(rig):
    sk = Sketch(rig.session)
    region = NS(Commit=Mock(side_effect=RuntimeError("solver failed")), Destroy=Mock())
    rig.part.Sketches.CreateWorkRegionBuilder = lambda: region
    with pytest.raises(RuntimeError):
        rig.e._sketch_diagnostics(rig.ref(sk, "sketch"))
    assert rig.session.ActiveSketch is None and not rig.session.marks
    assert region.Destroy.call_count == 1
    rig.session.ActiveSketch = Sketch(rig.session)
    with pytest.raises(NXToolError):
        rig.e._sketch_diagnostics(rig.ref(sk, "sketch"))


def test_highlighting_only_includes_penetration_unless_contact_requested(rig):
    a = Body("a")
    b = Body("b")
    refs = [rig.e._reference(v, "body", rig.part, "body") for v in (a, b)]
    rig.e._check_interference = lambda *_: {
        "pairs": [{"classification": "contact", "objects": refs}]
    }
    assert rig.e._highlight_collisions("a", "b")["highlighted_count"] == 0
    assert rig.e._highlight_collisions("a", "b", True)["highlighted_count"] == 2
    assert a.highlighted and b.highlighted
    assert rig.e._clear_highlights()["cleared_count"] == 2 and not a.highlighted
    b.Highlight = Mock(side_effect=RuntimeError("deleted"))
    with pytest.raises(RuntimeError):
        rig.e._highlight_collisions("a", "b", True)
    assert not a.highlighted
    b.Unhighlight = Mock(side_effect=RuntimeError("deleted"))
    rig.e._highlighted_objects = [b]
    assert rig.e._clear_highlights()["cleared_count"] == 0
    rig.session.IsBatch = True
    with pytest.raises(NXToolError):
        rig.e._highlight_collisions("a", "b")
    with pytest.raises(NXToolError):
        rig.e._section_view([0, 0, 0], [0, 0, 1])
    assert enum_name(999, NS(A=1)) == "unknown_999"


def test_failed_sketch_deactivation_still_rolls_back_work_region(rig):
    sk = Sketch(rig.session)
    sk.Deactivate = Mock(side_effect=RuntimeError("deactivate failed"))
    region = NS(Commit=Mock(), Destroy=Mock())
    rig.part.Sketches.CreateWorkRegionBuilder = lambda: region
    with pytest.raises(RuntimeError):
        rig.e._sketch_diagnostics(rig.ref(sk, "sketch"))
    assert rig.session.ActiveSketch is None and not rig.session.marks
