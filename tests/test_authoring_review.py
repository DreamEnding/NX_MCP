"""Stateful contracts for authoring and review; native geometry is tested separately."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp.authoring import finite, page
from nx_mcp.hardened import IDENTITY, xyz
from nx_mcp.runtime import NXToolError
from tests.fakes import Body, Collection, Component, Curve, Feature, Object, Sketch, point

pytestmark = pytest.mark.fake_nx


class Expression(Object):
    def __init__(self, name="height", formula="10", unit=None):
        super().__init__(name)
        self.RightHandSide = formula
        self.Value = 10.0
        self.Type = "Number"
        self.Units = unit
        self.IsNoEdit = self.IsRightHandSideLockedFromEdit = self.IsInterpartExpression = False
        self.parents = []
        self.dependents = []

    def GetValueUsingUnits(self, option):
        return getattr(self, "expression_value", self.Value)

    def GetExpressionParents(self):
        return self.parents

    def GetReferencingExpressions(self):
        return self.dependents


@pytest.fixture
def author(rig):
    p, s = rig.part, rig.session
    rig.nx.Expression = NS(UnitsOption=NS(Expression="expression"))
    p.Expressions = Collection()
    p.Expressions.CreateNumberExpression = Mock(side_effect=lambda spec, unit: create(spec, unit))

    def create(spec, unit):
        name, formula = spec.split("=", 1)
        exp = Expression(name, formula, unit)
        p.Expressions.append(exp)
        return exp

    def edit(exp, formula):
        if formula == "invalid":
            raise RuntimeError("native formula error")
        exp.RightHandSide = formula

    p.Expressions.EditExpression = Mock(side_effect=edit)
    p.UnitCollection.FindObject = lambda n: NS(
        Name=n,
        Abbreviation={"MilliMeter": "mm", "Inch": "in", "Degrees": "deg", "Radian": "rad"}.get(
            n, n
        ),
    )
    rig.uf.Modeling = NS(
        AskBodyConsistency=Mock(return_value=(0, [], [])),
        AskFaceData=lambda tag: geom(tag),
        AskMinimumDist3=Mock(return_value=(2.0, [0, 0, 10], [0, 0, 12], 0.0)),
    )

    def geom(tag):
        face = next(f for b in p.Bodies for f in b.faces if f.Tag == tag)
        return face.native_type, [0, 0, 0], face.normal, face.box, face.radius, 0, 1

    rig.uf.Curve = NS(AskArcData=lambda tag: NS(Radius=5))
    b = Body()
    p.Bodies.append(b)
    for f in b.faces:
        f.box = [0, 0, 10, 10, 10, 10]
        f.native_type = 22
        f.normal = [0, 0, 1]
        f.radius = 5
    for edge in b.edges:
        edge.box = [0, 0, 0, 10, 10, 0]
        edge.SolidEdgeType = 1
    rig.nx.Edge.EdgeType = NS(Circular=1, Linear=2)
    f = Feature(bodies=[b])
    f.Suppressed = False
    f.GetFeatureErrorMessages = lambda: []
    f.GetFeatureWarningMessages = lambda: []
    p.Features.append(f)
    exp = Expression()
    p.Expressions.append(exp)
    f.expressions = [exp]
    target = Expression("p_limit")
    p.Expressions.append(target)
    builder = NS(
        Limits=NS(StartExtend=NS(Value=target), EndExtend=NS(Value=target)),
        CommitFeature=Mock(),
        Destroy=Mock(),
    )
    p.Features.CreateExtrudeBuilder = lambda _: builder
    view = p.ModelingViews.WorkView

    def camera(m, o, scale):
        view.Matrix = m
        view.Origin = o
        view.Scale = scale

    view.SetRotationTranslationScale = Mock(side_effect=camera)
    rig.ref(b)
    rig.ref(f, "feature")
    rig.ref(exp, "expression")
    for face in b.faces:
        rig.ref(face, "face")
    for edge in b.edges:
        rig.ref(edge, "edge")
    # Save/restore expression and sketch state in addition to existing model seam state.
    original_mark, original_undo = s.SetUndoMark, s.UndoToMark
    extra = {}

    def mark(*args):
        m = original_mark(*args)
        extra[m] = (
            [(x, x.RightHandSide, x.Value) for x in p.Expressions],
            [(sk, list(sk.geometry), list(sk.constraints)) for sk in p.Sketches],
        )
        return m

    def undo(m, *args):
        original_undo(m, *args)
        expressions, sketches = extra[m]
        p.Expressions[:] = [x for x, _, _ in expressions]
        for x, formula, value in expressions:
            x.RightHandSide = formula
            x.Value = value
        for sk, geometry, constraints in sketches:
            sk.geometry[:] = geometry
            sk.constraints[:] = constraints

    s.SetUndoMark = mark
    s.UndoToMark = undo
    rig.body, rig.feature, rig.expression, rig.builder = b, f, exp, builder
    return rig


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), "1", -1, 0])
def test_positive_number_validation(value):
    with pytest.raises(NXToolError):
        finite(value, "value", True)


@pytest.mark.parametrize("offset,limit", [(-1, 1), (0, 0), (0, 201), (True, 1), (0, False)])
def test_page_rejects_unbounded_requests(offset, limit):
    with pytest.raises(NXToolError):
        page([1], offset, limit)


def test_pagination_is_stable_and_complete():
    assert page([1, 2, 3], 0, 2) == {"items": [1, 2], "total": 3, "offset": 0, "next_offset": 2}
    assert page([1, 2, 3], 2, 2)["next_offset"] is None


def test_expressions_creation_binding_and_dependencies(author):
    r = author
    e = r.e
    row = e.execute(
        "nx_set_expression", {"expression": "width", "formula": "5", "create": True, "units": "mm"}
    )["expression"]
    assert row["units"] == "mm" and row["formula"] == "5"
    exp = r.part.Expressions[-1]
    exp.parents = [r.expression]
    r.expression.dependents = [exp]
    assert e._list_expressions("width")["items"][0]["parents"][0]["name"] == "height"
    e.execute("nx_set_expression", {"expression": row["object"]["id"], "formula": "height*2"})
    assert exp.RightHandSide == "height*2"
    f = r.ref(r.feature, "feature")
    e.execute("nx_bind_parameter", {"feature": f, "parameter": "end", "expression": "width"})
    assert r.builder.Limits.EndExtend.Value.RightHandSide == "width"
    r.builder.CommitFeature.assert_called_once()
    r.builder.Destroy.assert_called_once()
    assert e._expression_record(r.expression)["dependents"]


@pytest.mark.parametrize(
    "params",
    [
        {"expression": "height", "formula": "5", "create": True},
        {"expression": "a b", "formula": "5", "create": True},
        {"expression": "a", "formula": "", "create": True},
        {"expression": "a", "formula": "5", "create": True, "units": "bad"},
        {"expression": "height", "formula": "5", "units": "mm"},
        {"expression": "absent", "formula": "5"},
    ],
)
def test_expression_rejections_do_not_modify(author, params):
    before = [(e.Name, e.RightHandSide) for e in author.part.Expressions]
    with pytest.raises(NXToolError):
        author.e.execute("nx_set_expression", params)
    assert [(e.Name, e.RightHandSide) for e in author.part.Expressions] == before


@pytest.mark.parametrize(
    "flag", ["IsNoEdit", "IsRightHandSideLockedFromEdit", "IsInterpartExpression"]
)
def test_locked_expression_is_never_changed(author, flag):
    setattr(author.expression, flag, True)
    with pytest.raises(NXToolError):
        author.e.execute("nx_set_expression", {"expression": "height", "formula": "20"})
    assert author.expression.RightHandSide == "10"


def test_failed_native_update_restores_expression(author):
    author.session.UpdateManager.DoUpdate.return_value = 1
    with pytest.raises(NXToolError) as error:
        author.e.execute("nx_set_expression", {"expression": "height", "formula": "25"})
    assert error.value.details["mutation_outcome"] == "rolled_back"
    assert author.expression.RightHandSide == "10"


@pytest.mark.parametrize(
    "kind,parameter,type_",
    [("OTHER", "end", "Number"), ("EXTRUDE", "spacing", "Number"), ("EXTRUDE", "end", "String")],
)
def test_parameter_binding_rejects_unsupported_targets(author, kind, parameter, type_):
    author.feature.FeatureType = kind
    author.expression.Type = type_
    with pytest.raises(NXToolError):
        author.e._bind_parameter(author.ref(author.feature, "feature"), parameter, "height")
    author.builder.CommitFeature.assert_not_called()


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "curve"},
        {"geometry_type": "bad"},
        {"kind": "face", "geometry_type": "circle"},
        {"kind": "edge", "geometry_type": "plane"},
        {"normal": [0, 0, 1]},
        {"radius": 1},
        {"order": "bad"},
        {"axis": "A"},
        {"tolerance": 0},
        {"order": "nearest"},
        {"near": [0, 1]},
        {"normal": [0, 0, 0], "geometry_type": "plane"},
    ],
)
def test_selection_rejects_ambiguous_or_ignored_options(author, params):
    with pytest.raises(NXToolError):
        author.e._find_geometry(**params)


def test_selection_reports_plane_orientation_radius_and_rank(author):
    e = author.e
    r = e._find_geometry(geometry_type="plane", normal=[0, 0, 1], order="highest")
    assert r["total"] == 1 and r["items"][0]["bounds_center"] == [5, 5, 10]
    assert e._find_geometry(geometry_type="plane", normal=[0, 0, -1], order="lowest")["total"] == 0
    edge = e._find_geometry(kind="edge", geometry_type="circle", radius=5, near=[0, 0, 0])["items"][
        0
    ]
    assert edge["radius"] == 5 and edge["distance_to_bounds_center"] > 0
    assert (
        e._find_geometry(kind="edge", geometry_type="circle", radius=6, near=[0, 0, 0])["total"]
        == 0
    )
    author.body.faces[0].native_type = 16
    assert e._find_geometry(geometry_type="cylinder", radius=5, order="lowest")["total"] == 1
    author.body.edges[0].SolidEdgeType = 2
    assert e._find_geometry(kind="edge", geometry_type="line", order="highest")["total"] == 1


def test_query_highlight_failure_clears_partial_selection(author):
    r = author
    e = r.e
    e._highlight_objects([r.ref(r.body)])
    assert r.body.highlighted
    r.body.faces[0].Highlight = Mock(side_effect=RuntimeError("highlight failed"))
    with pytest.raises(RuntimeError):
        e._highlight_objects([r.ref(r.body), r.ref(r.body.faces[0], "face")])
    assert not r.body.highlighted and not e._highlighted_objects


def test_health_reports_faults_suppression_and_paginates(author):
    r = author
    e = r.e
    assert e._model_health()["healthy"]
    r.feature.GetFeatureErrorMessages = lambda: ["broken reference"]
    r.feature.GetFeatureWarningMessages = lambda: ["out of date"]
    r.feature.Suppressed = True
    r.body.IsSolidBody = False
    r.uf.Modeling.AskBodyConsistency.return_value = (1, [100], [r.body.faces[0].Tag])
    report = e._model_health(limit=1)
    assert (
        report["error_count"] == 2 and report["warning_count"] == 1 and report["next_offset"] == 1
    )
    assert not report["healthy"] and report["bodies_checked"] == 1
    assert e._model_health(offset=99)["items"] == []


def test_health_assembly_unique_prototypes_and_unloaded(author):
    r = author
    root = Component("root")
    r.part.ComponentAssembly.RootComponent = root
    a = Component("a", parent=root)
    a.Prototype = r.part
    b = Component("b", parent=root)
    b.Prototype = r.part
    b.IsSuppressed = True
    Component("unloaded", parent=root).Prototype = None
    report = r.e._model_health("assembly")
    assert report["parts_checked"] == 1 and report["warning_count"] == 1
    assert {i["kind"] for i in report["items"]} == {"suppressed_component", "unloaded_component"}
    with pytest.raises(NXToolError):
        r.e._model_health("bad")


def test_health_missing_api_is_explicit(author):
    del author.uf.Modeling.AskBodyConsistency
    with pytest.raises(NXToolError, match="AskBodyConsistency"):
        author.e._model_health()


def test_model_rebuild_propagates_failure(author):
    assert author.e.execute("nx_rebuild_model", {})["health"]["healthy"]
    author.session.UpdateManager.DoUpdate.return_value = 2
    with pytest.raises(NXToolError):
        author.e.execute("nx_rebuild_model", {})


class Line(Curve):
    def __init__(self):
        super().__init__("line")
        self.StartPoint = point()
        self.EndPoint = point(10, 0, 0)

    def SetEndpoints(self, a, b):
        self.StartPoint, self.EndPoint = a, b


@pytest.fixture
def sketch(author):
    r = author
    s = Sketch(r.session)
    r.part.Sketches.append(s)
    line = Line()
    s.geometry = [line]
    r.part.Curves.append(line)
    r.nx.Sketch.ConstraintGeometry = lambda: NS()
    r.nx.Sketch.ConstraintPointType = NS()

    def constraint(g):
        c = Object("constraint")
        c.ConstraintType = 0
        s.constraints.append(c)
        return c

    s.CreateFixedConstraint = s.CreateHorizontalConstraint = s.CreateVerticalConstraint = constraint

    def delete(values):
        for v in values:
            if v in s.geometry:
                s.geometry.remove(v)
                r.part.Curves.remove(v)
            if v in s.constraints:
                s.constraints.remove(v)
        return NS(Length=0, Dispose=Mock())

    s.DeleteObjects = delete
    r.part.Sketches.CreateWorkRegionBuilder = lambda: NS(Scope=None, Commit=Mock(), Destroy=Mock())
    r.part.Curves.CreateLine = lambda a, b: create_line(a, b)

    def create_line(a, b):
        v = Line()
        v.SetEndpoints(a, b)
        r.part.Curves.append(v)
        return v

    r.sketch = s
    r.line = line
    r.sid = r.ref(s, "sketch")
    r.cid = r.ref(line, "curve")
    return r


def test_sketch_edits_constraints_and_deletes_owned_objects(sketch):
    r = sketch
    e = r.e
    result = e.execute(
        "nx_edit_sketch",
        {
            "sketch_id": r.sid,
            "operations": [
                {"action": "line", "curve": r.cid, "start": [1, 2], "end": [8, 2]},
                {"action": "constraint", "curve": r.cid, "type": "horizontal"},
                {"action": "add_line", "start": [1, 2], "end": [1, 8]},
            ],
        },
    )
    assert result["edit_count"] == 3 and r.session.ActiveSketch is None
    assert xyz(r.line.EndPoint) == [8, 2, 0] and len(r.sketch.constraints) == 1
    constraint = r.ref(r.sketch.constraints[0], "constraint")
    result = e.execute(
        "nx_edit_sketch",
        {
            "sketch_id": r.sid,
            "operations": [
                {"action": "delete", "object": constraint},
                {"action": "delete", "object": r.cid},
            ],
        },
    )
    assert len(r.sketch.geometry) == 1 and not r.sketch.constraints


@pytest.mark.parametrize(
    "op",
    [
        {"action": "unknown"},
        {"action": "add_line", "start": [0, 0], "end": [0, 0]},
        {"action": "add_line", "start": [0], "end": [1, 1]},
        {"action": "add_line", "start": [0, 0], "end": [1, 1], "extra": True},
    ],
)
def test_sketch_preflight_rejections_preserve_activation(sketch, op):
    with pytest.raises(NXToolError):
        sketch.e.execute("nx_edit_sketch", {"sketch_id": sketch.sid, "operations": [op]})
    assert sketch.session.ActiveSketch is None and sketch.sketch.geometry == [sketch.line]


def test_sketch_wrong_owner_and_active_sketch_rejected(sketch):
    r = sketch
    foreign = r.ref(Line(), "curve")
    with pytest.raises(NXToolError):
        r.e._edit_sketch(r.sid, [{"action": "delete", "object": foreign}])
    r.session.ActiveSketch = Sketch(r.session)
    with pytest.raises(NXToolError):
        r.e._edit_sketch(r.sid, [{"action": "delete", "object": r.cid}])


def test_sketch_edit_keeps_previously_active_sketch(sketch):
    r = sketch
    r.session.ActiveSketch = r.sketch
    r.e.execute(
        "nx_edit_sketch",
        {
            "sketch_id": r.sid,
            "operations": [{"action": "constraint", "curve": r.cid, "type": "fixed"}],
        },
    )
    assert r.session.ActiveSketch is r.sketch


def test_sketch_arc_edit_units_and_limits(sketch):
    r = sketch
    r.line.SetParameters = Mock()
    op = {
        "action": "arc",
        "curve": r.cid,
        "center": [2, 3],
        "radius": 4,
        "start_angle": 0,
        "end_angle": 180,
    }
    r.e.execute("nx_edit_sketch", {"sketch_id": r.sid, "operations": [op]})
    args = r.line.SetParameters.call_args.args
    assert (
        args[0] == 4 and xyz(args[1]) == [2, 3, 0] and args[3] == pytest.approx(3.141592653589793)
    )
    with pytest.raises(NXToolError):
        r.e._edit_sketch(r.sid, [{**op, "end_angle": 400}])


@pytest.fixture
def assembly(author):
    r = author
    root = Component("root")
    a = Component("seed", parent=root)
    r.part.ComponentAssembly.RootComponent = root
    a.Prototype.FullPath = str(r.e.workspace.root / "prototype.prt")
    Path(a.Prototype.FullPath).write_bytes(b"part")
    r.part.ComponentAssembly.SuppressComponents = lambda cs: suppress(cs, True)
    r.part.ComponentAssembly.UnsuppressComponents = lambda cs: suppress(cs, False)

    def suppress(cs, value):
        for c in cs:
            c.IsSuppressed = value
        return NS(Length=0, Dispose=Mock())

    r.session.UpdateManager.AddToDeleteList = lambda c: root.children.remove(c)

    def add(path, refset, name, pos, matrix, layer):
        c = Component(name, parent=root)
        c.position = pos
        c.rotation = matrix
        c.Prototype.FullPath = path
        return c, NS(Dispose=Mock())

    r.part.ComponentAssembly.AddComponent = add
    builder = NS(
        ComponentsToReplace=NS(Add=Mock()),
        ReplaceAllOccurrences=False,
        MaintainRelationships=True,
        ReplacementPart=None,
        Commit=Mock(),
        Destroy=Mock(),
        GetErrorList=lambda: NS(Length=0, Dispose=Mock()),
    )
    r.part.AssemblyManager = NS(CreateReplaceComponentBuilder=lambda: builder)
    r.component = a
    r.component_id = r.ref(a, "component")
    r.replace_builder = builder
    return r


def test_component_maintenance_preserves_pose_and_other_occurrences(assembly):
    r = assembly
    e = r.e
    for action, args in [
        ("rename", {"name": "changed"}),
        ("suppress", {}),
        ("unsuppress", {}),
        ("replace", {"part_path": r.component.Prototype.FullPath}),
    ]:
        result = e.execute(
            "nx_component_action", {"component": r.component_id, "action": action, **args}
        )
        assert result["placement_preserved"]
    assert r.component.Name == "changed" and not r.component.IsSuppressed
    r.replace_builder.Destroy.assert_called_once()
    e.execute("nx_component_action", {"component": r.component_id, "action": "remove"})
    assert not r.part.ComponentAssembly.RootComponent.children
    assert Path(r.component.Prototype.FullPath).exists()


@pytest.mark.parametrize(
    "params",
    [
        {"action": "bad"},
        {"action": "rename"},
        {"action": "rename", "name": ""},
        {"action": "suppress", "name": "ignored"},
        {"action": "replace", "part_path": "missing.prt"},
    ],
)
def test_component_action_invalid_args_do_not_mutate(assembly, params):
    with pytest.raises(NXToolError):
        assembly.e.execute("nx_component_action", {"component": assembly.component_id, **params})
    assert assembly.component.Name == "seed"


def test_component_nested_edits_rejected(assembly):
    r = assembly
    r.component.Parent = Component("nested")
    with pytest.raises(NXToolError):
        r.e._component_action(r.component_id, "remove")
    with pytest.raises(NXToolError):
        r.e._pattern_components(r.component_id, [1, 0, 0], 10, 4)


def test_component_pattern_count_pitch_and_placement(assembly):
    r = assembly
    result = r.e.execute(
        "nx_pattern_components",
        {"component": r.component_id, "direction": [2, 0, 0], "spacing": 16.5, "count": 16},
    )
    assert result["total_instances"] == 16 and not result["associative"]
    positions = [xyz(c.position) for c in r.part.ComponentAssembly.RootComponent.children]
    assert len(positions) == 16 and positions[-1] == [247.5, 0, 0]
    for count in [1, 101, True]:
        with pytest.raises(NXToolError):
            r.e._pattern_components(r.component_id, [1, 0, 0], 1, count)


def test_native_error_lists_are_disposed_and_reported(author):
    errors = NS(Length=1, GetErrorInfo=lambda i: "conflicting mate", Dispose=Mock())
    with pytest.raises(NXToolError, match="conflicting mate"):
        author.e._error_list(errors)
    errors.Dispose.assert_called_once()
    author.e._error_list(None)


def sections_stub(r):
    r.e._list_sections = lambda: {"sections": [], "view_sectioning_enabled": False}


def test_saved_presentation_restores_camera_and_face_colors(author):
    r = author
    e = r.e
    sections_stub(r)
    path = str(r.e.workspace.root / "view.json")
    r.body.faces[0].Color = 33
    e.execute(
        "nx_set_camera",
        {"rotation": [[0, -1, 0], [1, 0, 0], [0, 0, 1]], "origin": [2, 3, 4], "scale": 2},
    )
    e.execute("nx_save_presentation", {"path": path})
    r.body.faces[0].Color = 5
    r.body.IsBlanked = True
    e.execute("nx_set_camera", {"rotation": IDENTITY, "origin": [0, 0, 0], "scale": 1})
    e.execute("nx_restore_presentation", {"path": path})
    assert r.body.faces[0].Color == 33 and not r.body.IsBlanked
    assert e._view_info()["origin"] == [2, 3, 4] and e._view_info()["scale"] == 2
    with pytest.raises(NXToolError):
        e._save_presentation(path)


@pytest.mark.parametrize("fault", ["owner", "format", "missing_face", "bad_camera"])
def test_presentation_preflight_leaves_display_unchanged(author, fault):
    r = author
    e = r.e
    sections_stub(r)
    p = r.e.workspace.root / "view.json"
    e._save_presentation(str(p))
    d = json.loads(p.read_text())
    if fault == "owner":
        d["part_path"] = "different.prt"
    if fault == "format":
        d["version"] = 999
    if fault == "missing_face":
        d["display"][-1]["locator"]["journal_id"] = "removed"
        r.part.FindObject = Mock(side_effect=RuntimeError("not found"))
    if fault == "bad_camera":
        d["camera"]["scale"] = 0
    p.write_text(json.dumps(d))
    r.body.Color = 13
    with pytest.raises(NXToolError):
        e.execute("nx_restore_presentation", {"path": str(p)})
    assert r.body.Color == 13


def test_camera_validation_and_readback(author):
    e = author.e
    with pytest.raises(NXToolError):
        e._set_camera([[1, 0, 0]] * 3, [0, 0, 0], 1)
    with pytest.raises(NXToolError):
        e._set_camera(IDENTITY, [1, 2], 1)
    assert e._set_camera(IDENTITY, [1, 2, 3], 3)["origin"] == [1, 2, 3]


def test_temporary_presentation_restores_on_exception(author):
    e = author.e
    before = e._view_info()
    with pytest.raises(RuntimeError), e._temporary_view():
        author.body.Blank()
        e._set_camera(IDENTITY, [5, 5, 5], 2)
        raise RuntimeError("capture")
    assert not author.body.IsBlanked and e._view_info()["origin"] == before["origin"]


def test_temporary_presentation_cleanup_failure_is_partial(author):
    author.session.UndoToMark = Mock(side_effect=RuntimeError("undo"))
    with pytest.raises(NXToolError) as err, author.e._temporary_view():
        pass
    assert err.value.details["mutation_outcome"] == "partial"


@pytest.mark.parametrize(
    "section", ["overview", "features", "components", "expressions", "sketches"]
)
def test_compact_summaries_have_counts_and_bounded_pages(author, section):
    result = author.e._model_summary(section, limit=1)
    if section == "overview":
        assert result["counts"]["bodies"] == 1 and result["diagnostics"]["healthy"]
    else:
        assert len(result["items"]) <= 1


def test_summary_empty_part_and_bad_section(author):
    author.part.Bodies.clear()
    assert author.e._model_summary()["bounds"] is None
    with pytest.raises(NXToolError):
        author.e._model_summary("bad")


def test_inspection_report_packages_evidence_and_restores_view(author):
    r = author
    e = r.e
    e._check_clearance = lambda *args: {
        "pairs": [
            {
                "classification": "penetration",
                "objects": [e._reference(r.body, "body", r.part, "Body")],
            }
        ],
        "complete": True,
        "counts": {"penetration": 1},
    }

    def capture(path=None, **kw):
        p = Path(path)
        p.write_bytes(b"fixture-image")
        return {"path": str(p), "camera": e._view_info()}

    e._capture_view = capture
    e._section_view = lambda **kw: None
    path = str(e.workspace.root / "report.zip")
    result = e.execute(
        "nx_inspection_report",
        {"path": path, "section_planes": [{"origin": [0, 0, 5], "normal": [0, 0, 1]}]},
    )
    assert result["capture_count"] == 3 and not r.body.IsBlanked
    assert result["artifact_path"] == "report.zip"
    with zipfile.ZipFile(path) as z:
        assert {
            "index.html",
            "report.json",
            "manifest.json",
            "overview.png",
            "pair-1.png",
            "section-1.png",
        } == set(z.namelist())
        assert json.loads(z.read("report.json"))["clearance"]["complete"]
    with pytest.raises(NXToolError):
        e._inspection_report(path)


@pytest.mark.parametrize(
    "params",
    [
        {"path": "bad.txt"},
        {"path": "a.zip", "section_planes": [{"normal": [0, 0, 1]}]},
        {
            "path": "a.zip",
            "capture": False,
            "section_planes": [{"normal": [0, 0, 1], "origin": [0, 0, 0]}],
        },
        {"path": "a.zip", "section_planes": [{}] * 7},
    ],
)
def test_report_rejects_invalid_plans_before_output(author, params):
    with pytest.raises(NXToolError):
        author.e._inspection_report(
            **{**params, "path": str(author.e.workspace.root / params["path"])}
        )
    assert not (author.e.workspace.root / "a.zip").exists()


def test_preview_rolls_back_parameters_and_accepts_exact_plan(author):
    r = author
    e = r.e
    ref = r.ref(r.expression, "expression")
    r1 = e.execute(
        "nx_preview_change",
        {
            "operations": [
                {"method": "nx_set_expression", "params": {"expression": ref, "formula": "25"}}
            ],
            "capture": False,
        },
    )
    assert r.expression.RightHandSide == "10" and r1["model_outcome"] == "rolled_back"
    assert next(x for x in r1["after"]["parameters"] if x["name"] == "height")["formula"] == "25"
    r2 = e.execute(
        "nx_finish_preview",
        {"preview_id": r1["preview_id"], "action": "accept", "operation_id": "accept-once"},
    )
    assert r.expression.RightHandSide == "25" and r2["geometry_changed"]
    assert e.execute(
        "nx_finish_preview",
        {"preview_id": r1["preview_id"], "action": "accept", "operation_id": "accept-once"},
    )["replayed"]


def test_preview_failure_rolls_back_prior_edits(author):
    r = author
    e = r.e
    with pytest.raises(NXToolError):
        e.execute(
            "nx_preview_change",
            {
                "operations": [
                    {
                        "method": "nx_set_expression",
                        "params": {"expression": "height", "formula": "20"},
                    },
                    {
                        "method": "nx_set_expression",
                        "params": {"expression": "height", "formula": "invalid"},
                    },
                ],
                "capture": False,
            },
        )
    assert r.expression.RightHandSide == "10"


def test_preview_expires_on_intervening_mutation(author):
    e = author.e
    r = e.execute(
        "nx_preview_change",
        {
            "operations": [
                {"method": "nx_set_expression", "params": {"expression": "height", "formula": "20"}}
            ],
            "capture": False,
        },
    )
    e.execute("nx_set_expression", {"expression": "height", "formula": "30"})
    with pytest.raises(NXToolError, match="changed"):
        e.execute("nx_finish_preview", {"preview_id": r["preview_id"], "action": "accept"})
    assert author.expression.RightHandSide == "30"
    assert (
        e.execute("nx_finish_preview", {"preview_id": r["preview_id"], "action": "discard"})[
            "geometry_changed"
        ]
        is False
    )


@pytest.mark.parametrize(
    "operations",
    [[], [{"method": "nx_save_part", "params": {}}], [{"method": "nx_edit_feature", "params": {}}]],
)
def test_preview_preflight_rejects_unrecoverable_or_invalid_operations(author, operations):
    with pytest.raises((NXToolError, TypeError)):
        author.e._preview_plan(operations)
    assert author.expression.RightHandSide == "10"


def test_preview_discards_unknown_tokens_and_invalid_actions(author):
    for action in ["accept", "unknown"]:
        with pytest.raises(NXToolError):
            author.e._finish_preview("missing", action)


def test_expression_value_uses_its_declared_unit_not_part_unit(author):
    exp = author.expression
    exp.Units = NS(Abbreviation="in")
    exp.Value = 25.4
    exp.expression_value = 1.0
    record = author.e._expression_record(exp)
    assert record["value"] == 1 and record["units"] == "in"


@pytest.mark.asyncio
async def test_authoring_tool_schemas_are_explicit_and_path_scoped(tmp_path):
    from unittest.mock import AsyncMock

    from nx_mcp.server import create_server
    from nx_mcp.workspace import Workspace

    bridge = NS(call=AsyncMock(return_value={"status": "success"}))
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True)
    tools = {t.name: t for t in await server.list_tools()}
    variants = tools["nx_edit_sketch"].inputSchema["properties"]["operations"]["items"]["oneOf"]
    assert {v["properties"]["action"]["const"] for v in variants} == {
        "line",
        "arc",
        "add_line",
        "delete",
        "constraint",
    }
    assert all(v["additionalProperties"] is False for v in variants)
    assert tools["nx_find_geometry"].annotations.readOnlyHint
    assert not tools["nx_preview_change"].annotations.readOnlyHint
    await server.call_tool(
        "nx_component_action",
        {"component": "obj_example", "action": "replace", "part_path": "replacement.prt"},
    )
    args = bridge.call.call_args.args[1]
    assert args["part_path"] == str(tmp_path / "replacement.prt") and args["operation_id"]
