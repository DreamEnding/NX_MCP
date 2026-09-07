"""Failure boundaries and selection semantics; native fixtures test NX geometry."""

import copy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp.runtime import NXToolError
from tests.fakes import Component, Curve, Face, Object, Sketch, point
from tests.test_authoring_review import Expression
from tests.test_authoring_review import author as author_fixture

pytestmark = pytest.mark.fake_nx


@pytest.fixture
def author(rig, monkeypatch):
    import sys
    from types import ModuleType

    r = author_fixture.__wrapped__(rig)
    r.e._active_mark = None
    module = ModuleType("NXOpen.GeometricUtilities")
    module.PatternDefinition = NS(PatternEnum=NS(Linear="linear"))
    monkeypatch.setitem(sys.modules, "NXOpen.GeometricUtilities", module)
    r.nx.GeometricUtilities = module
    return r


def test_exact_distance_ranks_surface_not_center(author):
    a = author.body.faces[0]
    b = Face()
    b.box = [0, 0, 9, 1, 1, 9]
    b.native_type, b.normal, b.radius = 22, [0, 0, 1], 0
    a.box = [-100, -100, 10, 100, 100, 10]
    author.body.faces.append(b)
    author.uf.Modeling.AskMinimumDist3.side_effect = lambda level, tag, *args: (
        1 if tag == a.Tag else 5,
        [0, 0, 10],
        [0, 0, 11],
        0,
    )
    result = author.e._find_geometry(near=[0, 0, 11])
    assert result["items"][0]["object"]["id"] == author.ref(a, "face")
    assert result["items"][0]["distance"] == 1
    assert result["ranking"] == "native BREP minimum distance"
    author.uf.Modeling.AskMinimumDist3.assert_called_with(
        2, b.Tag, 0, 0, [0.0, 0.0, 0.0], 1, [0.0, 0.0, 11.0]
    )


def test_selection_rule_requeries_and_rejects_ties(author):
    result = author.e._find_geometry(near=[0, 0, 11])
    selector = result["selector"]
    assert author.e._resolve_geometry(selector)["match"]["distance"] == 2
    author.uf.Modeling.AskMinimumDist3.return_value = (8, [0, 0, 3], [0, 0, 11], 0)
    assert author.e._resolve_geometry(selector)["match"]["distance"] == 8
    author.body.faces.append(copy.copy(author.body.faces[0]))
    author.body.faces[-1].Tag += 100000
    with pytest.raises(NXToolError, match="tied"):
        author.e._resolve_geometry(selector)


@pytest.mark.parametrize("change", ["version", "extra", "query", "owner_part", "owner"])
def test_malformed_and_wrong_owner_rules_rejected(author, change):
    selector = author.e._find_geometry(near=[0, 0, 11])["selector"]
    if change == "extra":
        selector["extra"] = 1
    elif change == "version":
        selector["version"] = 2
    elif change == "query":
        selector["query"]["extra"] = 1
    elif change == "owner_part":
        selector["owner_part"] = "other.prt"
    else:
        selector["owner"] = {"kind": "face"}
    with pytest.raises(NXToolError):
        author.e._resolve_geometry(selector)


def test_saved_owner_resolution_and_no_match(author):
    owner = author.ref(author.body, "body")
    selector = author.e._find_geometry(owner=owner, near=[0, 0, 11])["selector"]
    assert selector["owner"]["kind"] == "body"
    assert author.e._resolve_geometry(selector)["match"]
    selector["query"]["geometry_type"] = "cylinder"
    with pytest.raises(NXToolError, match="No geometry"):
        author.e._resolve_geometry(selector)


def test_bore_groups_and_partial_faces(author):
    faces = [
        {
            "object": {"id": "a"},
            "cylindrical_role": "bore",
            "axis": [0, 0, 1],
            "axis_point": [0, 0, 0],
        },
        {
            "object": {"id": "b"},
            "cylindrical_role": "bore",
            "axis": [0, 0, -1],
            "axis_point": [0, 0, 10],
        },
        {
            "object": {"id": "c"},
            "cylindrical_role": "boss",
            "axis": [0, 0, 1],
            "axis_point": [10, 0, 0],
        },
    ]
    author.e._find_geometry = Mock(return_value={"items": faces, "next_offset": None})
    author.e._resolve = Mock(return_value=Object())
    author.uf.Modeling.AskFaceUvMinmax = Mock(
        side_effect=[[0, 6.283185307179586, 0, 10], [0, 3.14, 0, 10]]
    )
    r = author.e._recognize_holes()
    assert r["total"] == 2 and len(r["coaxial_groups"]) == 1
    assert r["items"][0]["full_circumference"] and not r["items"][1]["full_circumference"]


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"missing": "1"},
        {"height": ""},
        {"height": " " * 5},
        {"height": 123},
        {"height": "x" * 4097},
    ],
)
def test_feature_parameter_preflight(author, values):
    with pytest.raises(NXToolError):
        author.e._set_feature_parameters(author.ref(author.feature, "feature"), values)
    author.part.Expressions.EditExpression.assert_not_called()


def test_feature_parameters_ownership_and_atomic_update(author):
    r = author.e._feature_parameters(author.ref(author.feature, "feature"))
    assert r["parameters"][0]["name"] == "height"
    author.e._set_feature_parameters(author.ref(author.feature, "feature"), {"height": "18"})
    assert author.expression.RightHandSide == "18"
    other = Expression("unrelated")
    author.part.Expressions.append(other)
    with pytest.raises(NXToolError, match="not owned"):
        author.e._set_feature_parameters(
            author.ref(author.feature, "feature"), {"height": "20", "unrelated": "30"}
        )
    assert author.expression.RightHandSide == "18"
    with pytest.raises(NXToolError, match="Duplicate"):
        author.e._set_feature_parameters(
            author.ref(author.feature, "feature"),
            {"height": "20", author.ref(author.expression, "expression"): "30"},
        )
    author.expression.IsNoEdit = True
    with pytest.raises(NXToolError, match="editable"):
        author.e._set_feature_parameters(author.ref(author.feature, "feature"), {"height": "20"})


@pytest.mark.parametrize(
    "count,spacing", [(1, 1), (101, 1), (True, 1), (2.5, 1), (2, 0), (2, float("nan"))]
)
def test_native_pattern_invalid_inputs_precede_builder(author, count, spacing):
    with pytest.raises(NXToolError):
        author.e._native_component_pattern("missing", [1, 0, 0], spacing, count)


@pytest.fixture
def patterned(author):
    r = author
    root = Component("root")
    r.part.ComponentAssembly.RootComponent = root
    seed = Component("seed", parent=root)
    r.nx.GeometricUtilities.PatternDefinition = NS(PatternEnum=NS(Linear="linear"))
    r.nx.Vector3d = point
    r.nx.SmartObject = NS(UpdateOption=NS(WithinModeling="model"))
    r.part.Directions = NS(CreateDirection=Mock(return_value="direction"))
    count = Expression("count")
    count.Value = 4
    pitch = Expression("pitch")
    pitch.Value = 20
    pattern = Object("pattern")
    pattern.GetComponentsToPattern = lambda: [seed]
    others = [Component("copy" + str(i), parent=root) for i in range(3)]
    for i, c in enumerate(others):
        c.position = point((i + 1) * 20, 0, 0)
    pattern.GetAllPatternMembers = lambda: [NS(GetAllComponents=lambda: [seed, *others])]
    b = NS(
        Associative=True,
        ComponentPatternSet=NS(Add=Mock()),
        PatternService=NS(
            PatternType="linear",
            PatternEnum=NS(Linear="linear", Circular="circular"),
            RectangularDefinition=NS(
                UseYDirectionToggle=False,
                XSpacing=NS(NCopies=count, PitchDistance=pitch),
                YSpacing=NS(NCopies=Expression("y")),
            ),
        ),
        Commit=Mock(return_value=pattern),
        Destroy=Mock(),
    )
    r.part.ComponentAssembly.CreateComponentPatternBuilder = Mock(return_value=b)
    r.part.ComponentAssembly.ComponentPatterns = NS(GetAllComponentPatterns=lambda: [pattern])
    r.seed, r.pattern, r.pattern_builder = seed, pattern, b
    return r


def test_native_pattern_creation_readback_edit_and_cleanup(patterned):
    r = patterned
    result = r.e._native_component_pattern(r.ref(r.seed, "component"), [1, 0, 0], 20, 4)
    assert result["associative"] and result["total_instances"] == 4
    assert result["native_type"] == "NXOpen.Assemblies.ComponentPattern"
    assert r.e._list_component_patterns()["count"] == 1
    r.e._edit_component_pattern(result["object"]["id"], spacing=22, count=4)
    assert (
        r.pattern_builder.PatternService.RectangularDefinition.XSpacing.PitchDistance.RightHandSide
        == "22.0"
    )
    assert r.pattern_builder.Destroy.call_count == 5


def test_pattern_failure_count_and_unsupported_edits(patterned):
    r = patterned
    with pytest.raises(NXToolError, match="count differs"):
        r.e._native_component_pattern(r.ref(r.seed, "component"), [1, 0, 0], 20, 5)
    with pytest.raises(NXToolError, match="Supply"):
        r.e._edit_component_pattern(r.ref(r.pattern, "component_pattern"))
    r.pattern_builder.Associative = False
    with pytest.raises(NXToolError, match="associative"):
        r.e._edit_component_pattern(r.ref(r.pattern, "component_pattern"), spacing=20)
    r.seed.IsSuppressed = True
    with pytest.raises(NXToolError, match="unsuppressed"):
        r.e._native_component_pattern(r.ref(r.seed, "component"), [1, 0, 0], 20, 4)


@pytest.fixture
def constrained(author):
    r = author

    class Line(Curve):
        pass

    class Arc(Curve):
        pass

    r.nx.Line, r.nx.Arc = Line, Arc
    sk = Sketch(r.session)
    r.part.Sketches.append(sk)
    sk.Update = Mock()
    a, b, arc = Line(), Line(), Arc()
    for c in (a, b):
        c.StartPoint, c.EndPoint = point(), point(10, 0, 0)
    sk.geometry = [a, b, arc]
    r.nx.Sketch.DimensionGeometry = lambda: NS(Geometry=None, AssocType=None)
    r.nx.Sketch.ConstraintGeometry = lambda: NS(Geometry=None, PointType=None)
    r.nx.Sketch.DimensionOption = NS(CreateAsReference="reference", CreateAsDriving="driving")
    r.nx.Sketch.AssocType = NS(StartPoint="start", EndPoint="end")
    r.nx.Sketch.ConstraintPointType = NS(StartVertex="start", EndVertex="end", ArcCenter="center")
    for name in ["ParallelDim", "HorizontalDim", "VerticalDim"]:
        setattr(r.nx.Sketch.ConstraintType, name, name)
    constraint = Object()
    constraint.AssociatedExpression = Expression("dim")
    for name in [
        "CreateDimension",
        "CreateRadialDimension",
        "CreateDiameterDimension",
        "CreateParallelConstraint",
        "CreatePerpendicularConstraint",
        "CreateEqualLengthConstraint",
        "CreateEqualRadiusConstraint",
        "CreateConcentricConstraint",
        "CreateCoincidentConstraint",
    ]:
        setattr(sk, name, Mock(return_value=constraint))
    r.e._relation_residual = Mock(return_value=0.0)
    r.e._sketch_diagnostics = Mock(
        return_value={"solver_status": "UnderConstrained", "constraints": [], "constraint_count": 0}
    )
    for name in [
        "CreateParallelConstraint",
        "CreatePerpendicularConstraint",
        "CreateEqualLengthConstraint",
        "CreateEqualRadiusConstraint",
        "CreateConcentricConstraint",
        "CreateCoincidentConstraint",
    ]:

        def create(*args):
            sk.constraints.append(constraint)
            return constraint

        getattr(sk, name).side_effect = create
    constraint.ConstraintType = 99
    r.sk, r.lines, r.arc = sk, [a, b], arc
    return r


@pytest.mark.parametrize("kind", ["length", "horizontal", "vertical", "radius", "diameter"])
def test_dimensions_apply_formula_restore_activation(constrained, kind):
    r = constrained
    result = r.e._sketch_dimension(
        r.ref(r.sk, "sketch"),
        r.ref(r.arc if kind in {"radius", "diameter"} else r.lines[0], "curve"),
        kind,
        12,
        [0, 2],
    )
    assert result["expression"]["formula"] == "12.0"
    assert r.session.ActiveSketch is None


@pytest.mark.parametrize(
    "kind,value,origin,reference",
    [
        ("bad", 1, [0, 0], False),
        ("radius", 1, [0, 0], False),
        ("length", 0, [0, 0], False),
        ("length", 1, [0], False),
        ("length", 1, [0, 0], 1),
        ("length", 15, [0, 0], True),
    ],
)
def test_dimension_rejections(constrained, kind, value, origin, reference):
    r = constrained
    with pytest.raises(NXToolError):
        r.e._sketch_dimension(
            r.ref(r.sk, "sketch"), r.ref(r.lines[0], "curve"), kind, value, origin, reference
        )
    assert r.session.ActiveSketch is None


@pytest.mark.parametrize("relation", ["parallel", "perpendicular", "equal_length", "coincident"])
def test_line_relations_restore_activation(constrained, relation):
    r = constrained
    kw = {"point1": "end", "point2": "start"} if relation == "coincident" else {}
    result = r.e._sketch_relation(
        r.ref(r.sk, "sketch"), *[r.ref(c, "curve") for c in r.lines], relation, **kw
    )
    assert result["constraint"] and r.session.ActiveSketch is None


@pytest.mark.parametrize(
    "relation,points",
    [
        ("bad", {}),
        ("parallel", {"point1": "start"}),
        ("equal_radius", {}),
        ("coincident", {}),
        ("coincident", {"point1": "center", "point2": "end"}),
    ],
)
def test_relation_preflight(constrained, relation, points):
    r = constrained
    with pytest.raises(NXToolError):
        r.e._sketch_relation(
            r.ref(r.sk, "sketch"), *[r.ref(c, "curve") for c in r.lines], relation, **points
        )
    r.sk.CreateParallelConstraint.assert_not_called()


def test_conflicting_solver_state_rejected(constrained):
    r = constrained
    r.e._sketch_diagnostics.return_value["solver_status"] = "OverConstrained"
    with pytest.raises(NXToolError, match="conflicts"):
        r.e._sketch_relation(
            r.ref(r.sk, "sketch"), *[r.ref(c, "curve") for c in r.lines], "parallel"
        )
    assert r.session.ActiveSketch is None


def test_other_active_sketch_and_foreign_curve_rejected(constrained):
    r = constrained
    r.session.ActiveSketch = Sketch(r.session)
    with pytest.raises(NXToolError, match="other active"):
        r.e._sketch_dimension(
            r.ref(r.sk, "sketch"), r.ref(r.lines[0], "curve"), "length", 10, [0, 0]
        )
    with pytest.raises(NXToolError, match="not owned"):
        r.e._owned_curve(r.sk, r.ref(Curve(), "curve"))


def test_conflict_trials_are_bounded_and_restored(constrained):
    r = constrained
    constraints = [{"object": {"id": r.ref(Object(), "constraint")}} for _ in range(3)]
    r.e._sketch_diagnostics.side_effect = [
        {"solver_status": "OverConstrained", "constraints": constraints, "constraint_count": 3},
        {"solver_status": "UnderConstrained"},
        {"solver_status": "OverConstrained"},
    ]
    r.sk.DeleteObjects = Mock(return_value=None)
    result = r.e._sketch_conflicts(r.ref(r.sk, "sketch"), 2)
    assert result["checked"] == 2 and not result["complete"]
    assert result["single_removal_relief"] == [constraints[0]["object"]]
    assert not r.session.marks and r.session.ActiveSketch is None


@pytest.mark.parametrize("value", [0, 51, True])
def test_conflict_limits(constrained, value):
    with pytest.raises(NXToolError):
        constrained.e._sketch_conflicts("missing", value)


def test_explicit_legacy_constraint_contradiction_even_if_native_status_undercounted(constrained):
    r = constrained
    r.nx.Sketch.ConstraintType.Horizontal = 10
    r.nx.Sketch.ConstraintType.Vertical = 11
    a, b = Object(), Object()
    a.ConstraintType, b.ConstraintType = 10, 11
    r.sk.constraints = [a, b]
    pairs = r.e._explicit_constraint_conflicts(r.sk)
    assert len(pairs) == 2
    assert pairs[0]["reason"] == "A nonzero line cannot be both horizontal and vertical"
    with pytest.raises(NXToolError, match="conflicts"):
        r.e._check_sketch_result(r.ref(r.sk, "sketch"))


def test_conflict_cleanup_failure_reports_partial(constrained):
    r = constrained
    r.session.UndoToMark = Mock(side_effect=RuntimeError("undo failed"))
    with pytest.raises(NXToolError) as error:
        r.e._sketch_conflicts(r.ref(r.sk, "sketch"))
    assert error.value.details["mutation_outcome"] == "partial"


def test_relation_noop_is_rejected(constrained):
    r = constrained
    r.e._relation_residual.return_value = 1.0
    with pytest.raises(NXToolError, match="did not satisfy"):
        r.e._sketch_relation(
            r.ref(r.sk, "sketch"), *[r.ref(c, "curve") for c in r.lines], "parallel"
        )
    assert r.session.ActiveSketch is None


def test_geometric_relation_residuals(constrained):
    from nx_mcp.advanced_authoring import AdvancedAuthoringMixin

    r = constrained

    def residual(a, b, rel, p1=None, p2=None):
        return AdvancedAuthoringMixin._relation_residual(r.e, a, b, rel, p1, p2)

    a, b = r.lines
    assert residual(a, b, "parallel") == 0
    assert residual(a, b, "perpendicular") == 1
    b.EndPoint = point(0, 20, 0)
    assert residual(a, b, "perpendicular") == 0
    assert residual(a, b, "equal_length") == 10
    assert residual(a, b, "coincident", "start", "start") == 0
    r.arc.Radius = 5
    r.arc.CenterPoint = point()
    other = type(r.arc)()
    other.Radius = 7
    other.CenterPoint = point(0, 0, 2)
    assert residual(r.arc, other, "equal_radius") == 2
    assert residual(r.arc, other, "concentric") == 2
    with pytest.raises(NXToolError):
        r.e._relation_point(r.arc, "start")


@pytest.mark.parametrize(
    "relation",
    ["parallel", "perpendicular", "equal_length", "equal_radius", "concentric", "coincident"],
)
def test_modern_builder_configuration_and_cleanup(constrained, relation):
    r = constrained
    builder = NS(
        StationaryObject=NS(SetValue=Mock()),
        MotionObjects=NS(Add=Mock()),
        MotionPoints=NS(Add=Mock()),
        SetCreateConstraints=Mock(),
        FindRelations=Mock(),
        Commit=Mock(),
        Destroy=Mock(),
    )
    for name in ["Parallel", "Perpendicular", "Equal", "Coincident"]:
        setattr(r.part.Sketches, "CreateSketchMake" + name + "Builder", lambda: builder)
    r.nx.InferSnapType = NS(SnapType=NS(Start="start", End="end", Center="center"))
    r.nx.SketchMakeEqualBuilder = NS(EqualTypes=NS(Radius="radius", Length="length"))
    a, b = r.lines
    if relation in ["concentric", "equal_radius"]:
        a = r.arc
        b = type(a)()
        a.CenterPoint = b.CenterPoint = point()
    r.e._modern_relation(r.sk, a, b, relation, "start", "end")
    builder.SetCreateConstraints.assert_called_once_with(True)
    builder.Commit.assert_called_once()
    builder.Destroy.assert_called_once()
