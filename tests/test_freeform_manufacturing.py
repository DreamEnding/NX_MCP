"""Boundary/cleanup regressions; native geometry evidence is kept separately."""

import inspect
import math
import sys
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock

import pytest

from nx_mcp import assembly_documentation_server, freeform_server, manufacturing_server
from nx_mcp.animation import interpolate_rotation
from nx_mcp.freeform import points3
from nx_mcp.hardened import IDENTITY, READ_ONLY
from nx_mcp.runtime import NXToolError
from tests.fakes import Body, Face, Feature, Object, point


@pytest.fixture
def ff(rig, monkeypatch):
    r = rig
    r.body = Body()
    r.body.IsOccurrence = False
    r.body.OwningPart = r.part
    r.part.Bodies.append(r.body)
    r.feature = Feature(bodies=[r.body])
    r.feature.FeatureType = "STUDIO_SPLINE"
    r.feature.OwningPart = r.part
    r.feature.IsOccurrence = False
    r.part.Features.append(r.feature)
    r.body.faces.append(Face("other"))
    r.faces = list(r.body.GetFaces())
    for obj in [*r.faces, *r.body.GetEdges()]:
        obj.IsOccurrence = False
        obj.OwningPart = r.part
        obj.GetBody = lambda: r.body
    r.nx.SmartObject = NS(UpdateOption=NS(WithinModeling=1))
    r.nx.Point3d = lambda *coords: point(*coords)
    r.part.Points = NS(CreatePoint=Mock(return_value=Object()))
    r.part.ScRuleFactory = MagicMock()
    r.part.Sections = MagicMock()
    r.nx.Section = NS(AllowTypes=NS(OnlyCurves=1), Mode=NS(Create=1))
    f = NS(
        StudioSplineBuilderEx=NS(Types=NS(ThroughPoints=1, ByPoles=2)),
        ThroughCurveMeshBuilder=NS(BodyPreferenceTypes=NS(Sheet=1)),
        BridgeSurfaceBuilder=NS(EndObjectType=NS(Edge=1)),
        SewBuilder=NS(Types=NS(Sheet=1), BodyPreferenceTypes=NS(Solid=2, Sheet=1)),
        DeleteFaceBuilder=NS(SelectTypes=NS(Face=1)),
        TrimSheetBuilder=NS(KeepDiscardOption=NS(Keep=1, Discard=2)),
        ThreadBuilder=NS(
            Input=NS(Manual=1, ThreadTable=2),
            Type=NS(Detailed=2, Symbolic=1),
            LimitOption=NS(Value=1),
            Handedness=NS(LeftHand=1, RightHand=2),
        ),
    )
    g = NS(
        Continuity=NS(ContinuityTypes=NS(G0=0, G1=1, G2=2)), ModlMotion=NS(Options=NS(Distance=1))
    )
    monkeypatch.setitem(sys.modules, "NXOpen.Features", f)
    monkeypatch.setitem(sys.modules, "NXOpen.GeometricUtilities", g)
    r.nx.Features = f
    r.nx.GeometricUtilities = g
    r.uf = MagicMock()
    r.uf.Modeling.AskFaceData.return_value = [16, None, None, None, 1.0]
    u = NS(UFSession=NS(GetUFSession=lambda: r.uf))
    monkeypatch.setitem(sys.modules, "NXOpen.UF", u)
    r.nx.UF = u
    r.b = MagicMock()
    r.b.Validate.return_value = True
    r.b.CommitFeature.return_value = r.feature
    r.b.Curve = Object()
    r.b.Curve.Order = 4
    r.b.Curve.Periodic = False
    r.b.GetUnsewnBodies.return_value = []
    r.e._freeform_builder = Mock(return_value=r.b)
    r.e._engineering_direction = Mock(return_value=Object())
    r.e._update_model = Mock()
    r.e._model_health = Mock(return_value={"healthy": True, "issues": []})
    r.ref = lambda o, kind: r.e._reference(o, kind, r.part, kind)["id"]
    return r


def test_new_contracts_match_native_handlers(rig):
    for module in [freeform_server, manufacturing_server, assembly_documentation_server]:
        for name, function in vars(module).items():
            if name.startswith("nx_") and inspect.isfunction(function):
                assert name in rig.e._handlers
                assert (
                    inspect.signature(function).parameters.keys()
                    == inspect.signature(rig.e._handlers[name]).parameters.keys()
                )
        assert module.READ_ONLY <= READ_ONLY


@pytest.mark.parametrize("value", [[], [[0, 1]], [[0, 0, math.inf]], [[True, 0, 0]], "points"])
def test_invalid_point_sets(value):
    with pytest.raises(NXToolError):
        points3(value)


@pytest.mark.parametrize("axis", range(3))
def test_rotation_interpolation_stays_orthonormal_at_half_turn(axis):
    end = [[float(i == j) * (1 if i == axis else -1) for j in range(3)] for i in range(3)]
    for fraction in [0, 0.25, 0.5, 0.75, 1]:
        matrix = interpolate_rotation(IDENTITY, end, fraction)
        for i in range(3):
            for j in range(3):
                assert sum(matrix[i][k] * matrix[j][k] for k in range(3)) == pytest.approx(
                    float(i == j)
                )
    assert interpolate_rotation(IDENTITY, IDENTITY, 0.5) == IDENTITY
    for row, expected in zip(interpolate_rotation(IDENTITY, end, 1), end, strict=True):
        assert row == pytest.approx(expected)


@pytest.mark.parametrize("method", ["through_points", "poles"])
def test_spline_edit_replaces_constraints_and_returns_curve(ff, method):
    result = ff.e._spline(
        [[0, 0, 0], [1, 0, 1], [2, 1, 2], [3, 0, 4]],
        method=method,
        feature=ff.ref(ff.feature, "feature"),
    )
    assert result["curve"]["kind"] == "curve"
    ff.b.ConstraintManager.Clear.assert_called_once()
    assert ff.b.ConstraintManager.Append.call_count == 4
    assert ff.b.HasPlaneConstraint is False
    ff.b.Destroy.assert_called_once()


@pytest.mark.parametrize(
    "kwargs", [{"degree": True}, {"degree": 8}, {"degree": 4}, {"method": "unknown"}]
)
def test_spline_rejects_invalid_parameters_before_builder(ff, kwargs):
    with pytest.raises(NXToolError):
        ff.e._spline([[0, 0, 0], [1, 0, 1], [2, 1, 2], [3, 0, 4]], **kwargs)
    ff.e._freeform_builder.assert_not_called()


def test_builder_rejection_always_destroys_and_does_not_commit(ff):
    ff.b.Validate.return_value = False
    with pytest.raises(NXToolError):
        ff.e._thicken([ff.ref(ff.faces[0], "face")], 2)
    ff.b.CommitFeature.assert_not_called()
    ff.b.Destroy.assert_called_once()


@pytest.mark.parametrize(
    "action,kwargs",
    [
        ("move", {"distance": 2, "direction": [0, 0, 4]}),
        ("offset", {"distance": -2}),
        ("replace", {}),
        ("heal", {}),
    ],
)
def test_native_face_edit_configuration_and_cleanup(ff, action, kwargs):
    if action == "replace":
        kwargs["replacement"] = ff.ref(ff.faces[1], "face")
    result = ff.e._edit_faces([ff.ref(ff.faces[0], "face")], action, **kwargs)
    assert result["body_count"] == 1
    ff.b.Destroy.assert_called_once()
    if action == "move":
        assert ff.b.Motion.DistanceValue.RightHandSide == "2.0"
    elif action == "offset":
        assert ff.b.Distance.RightHandSide == "2.0" and ff.b.Direction is True
    elif action == "heal":
        assert ff.b.Heal is True and ff.b.AllowPartialDelete is False


@pytest.mark.parametrize(
    "action,kwargs",
    [
        ("move", {}),
        ("heal", {"distance": 2}),
        ("replace", {"direction": [0, 0, 1]}),
        ("unsupported", {}),
    ],
)
def test_ignored_face_edit_arguments_are_rejected(ff, action, kwargs):
    with pytest.raises(NXToolError):
        ff.e._edit_faces([ff.ref(ff.faces[0], "face")], action, **kwargs)
    ff.e._freeform_builder.assert_not_called()


def test_thicken_uses_explicit_native_tolerance(ff):
    ff.e._thicken([ff.ref(ff.faces[0], "face")], 2, -1)
    assert ff.b.Tolerance == 0.001
    assert ff.b.FirstOffset.RightHandSide == "2.0"
    assert ff.b.SecondOffset.RightHandSide == "-1.0"
    with pytest.raises(NXToolError):
        ff.e._thicken([ff.ref(ff.faces[0], "face")], 2, 2)


@pytest.mark.parametrize("detailed,left", [(False, False), (True, True)])
def test_thread_uses_actual_cylinder_size_and_explicit_start(ff, detailed, left):
    result = ff.e._thread(
        ff.ref(ff.faces[0], "face"),
        ff.ref(ff.faces[1], "face"),
        0.4,
        2.4,
        1.9,
        4,
        detailed=detailed,
        left_hand=left,
    )
    assert result["representation"] == ("detailed" if detailed else "symbolic")
    assert ff.b.TapDrillDiameterExp.RightHandSide == "2.0"
    assert ff.b.CylindricalFace.Value is ff.faces[0]
    assert ff.b.StartObject.Value is ff.faces[1]
    ff.b.Destroy.assert_called_once()


def test_sampled_thickness_retains_unresolved_samples(ff):
    source = ff.faces[0]
    data = ([0.0, 0.0, 5.0], None, None, None, None, [0.0, 0.0, 1.0], [1e30, 1e30])
    ff.e._face_samples = Mock(return_value=([(source, [0, 0], data)] * 2, 1))
    ff.uf.Modeling.AskPointContainment.side_effect = [1, 2]
    ff.uf.Modeling.TraceARay.return_value = (1, [NS(HitPoint=[0.0, 0.0, 0.0], HitFace=source.Tag)])
    result = ff.e._wall_thickness(ff.ref(ff.body, "body"))
    assert result["minimum_sampled_thickness"] == 5
    assert result["measured_count"] == 1
    assert result["sample_count"] == 2
    assert result["samples"][1]["status"] == "unresolved"
    assert result["skipped_outside_trim"] == 1


def test_face_sampling_reports_signed_draft_and_ignores_trimmed_outside(ff):
    ff.uf.Modeling.AskFaceUvMinmax.return_value = [0.0, 1.0, 0.0, 1.0]
    ff.uf.Modeling.AskFaceProps.return_value = (
        [0.0, 0.0, 0.0],
        None,
        None,
        None,
        None,
        [0.0, 0.0, -1.0],
        [1e30, 2.0],
    )
    ff.uf.Modeling.AskPointContainment.side_effect = [1, 2, 1, 1]
    result = ff.e._face_analysis(
        [ff.ref(ff.faces[0], "face")], samples_per_axis=2, pull_direction=[0, 0, 1]
    )
    assert result["sample_count"] == 3 and result["skipped_outside_trim"] == 1
    assert result["samples"][0]["signed_draft_degrees"] == -90
    assert result["samples"][0]["principal_curvatures"] == [0, 0.5]


def test_shape_operator_is_parameterization_invariant():
    from nx_mcp.surface_math import continuity_difference, shape_operator

    plane = shape_operator([1, 0, 0], [0, 1, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0])
    reversed_plane = shape_operator([0, 3, 0], [2, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0])
    assert continuity_difference(plane, reversed_plane) == (0, 0)
    cylinder = shape_operator([0, 2, 0], [0, 0, 1], [-2, 0, 0], [0, 0, 0], [0, 0, 0])
    scaled = shape_operator([0, 4, 0], [0, 0, 3], [-8, 0, 0], [0, 0, 0], [0, 0, 0])
    assert continuity_difference(cylinder, scaled) == pytest.approx((0, 0))
    assert abs(cylinder[1][1][1]) == 0.5
    with pytest.raises(NXToolError):
        shape_operator([0, 0, 0], [1, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0])


def test_sew_rejects_partial_or_sheet_fallback(ff):
    other = Body("other")
    other.OwningPart = ff.part
    ff.part.Bodies.append(other)
    target, tool = ff.ref(ff.body, "body"), ff.ref(other, "body")
    assert ff.e._sew(target, [tool])["body_count"] == 1
    ff.b.GetUnsewnBodies.return_value = [other]
    with pytest.raises(NXToolError, match="could not be sewn"):
        ff.e._sew(target, [tool])
    ff.b.GetUnsewnBodies.return_value = []
    ff.body.IsSolidBody = False
    with pytest.raises(NXToolError, match="instead of"):
        ff.e._sew(target, [tool], solid=True)
    with pytest.raises(NXToolError, match="also"):
        ff.e._sew(target, [target])


def test_surface_sections_and_trim_use_native_section_objects(ff):
    curve = Object("curve")
    curve.IsOccurrence = False
    curve.OwningPart = ff.part
    ref = ff.ref(curve, "curve")
    ff.e._surface_mesh([[ref], [ref]], [[ref], [ref]])
    assert ff.b.PrimaryCurvesList.Append.call_count == 2
    assert ff.b.CrossCurvesList.Append.call_count == 2
    with pytest.raises(NXToolError):
        ff.e._surface_mesh([[ref]], [[ref], [ref]])
    ff.body.IsSolidBody = False
    ff.part.CreateRegionPoint = Mock(return_value=Object())
    ff.e._trim_sheet(ff.ref(ff.body, "body"), [ref], [0, 0, 0], keep=False)
    ff.b.BoundaryObjects.Add.assert_called_with(ff.part.Sections.CreateSection.return_value)
    ff.b.Regions.Append.assert_called_once()


def test_bridge_explicit_continuity_and_reverse(ff):
    edge = ff.body.GetEdges()[0]
    result = ff.e._bridge_surface(
        ff.ref(edge, "edge"), ff.ref(edge, "edge"), continuity="G2", reverse_second=True
    )
    assert result["body_count"] == 1
    assert ff.b.FirstEdgeContinuity.ContinuityType == 2
    assert ff.b.IsSecondEdgeReversed is True
    with pytest.raises(NXToolError):
        ff.e._bridge_surface("x", "y", continuity="C9")


def test_curve_analysis_keeps_singular_samples(ff):
    edge = ff.body.GetEdges()[0]
    ff.nx.Edge = type(edge)
    ff.uf.ModlGeneral.EvaluateCurve.side_effect = [
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 2.0, 0.0],
    ]
    result = ff.e._curve_analysis(ff.ref(edge, "curve"), samples=2)
    assert result["samples"][0]["singular"]
    assert result["samples"][1]["curvature"] == 2
    assert result["samples"][1]["radius"] == 0.5
    with pytest.raises(NXToolError):
        ff.e._curve_analysis("x", samples=1)


def test_pmi_association_and_existing_edit(ff, monkeypatch):
    datum = Object("datum")
    datum.OwningPart = ff.part
    datum.IsOccurrence = False
    fcf = Object("fcf")
    fcf.OwningPart = ff.part
    fcf.IsOccurrence = False
    ff.part.Annotations = MagicMock()
    db, fb = MagicMock(), MagicMock()
    db.Letter = "A"
    db.Commit.return_value = datum
    fb.Commit.return_value = fcf
    ff.part.Annotations.Datums.CreatePmiDatumFeatureSymbolBuilder.return_value = db
    ff.part.Annotations.CreatePmiFeatureControlFrameBuilder.return_value = fb
    a = NS(
        FeatureControlFrameBuilder=NS(
            FcfCharacteristic=NS(Parallelism=1, Flatness=2, Position=3),
            FcfFrameStyle=NS(SingleFrame=1),
        ),
        FeatureControlFrameDataBuilder=NS(
            ToleranceMaterialModifier=NS(
                NotSet=0,
                MaximumMaterialCondition=1,
                LeastMaterialCondition=2,
                RegardlessOfFeatureSize=3,
            ),
            ToleranceZoneShape=NS(NotSet=0, Diameter=1, SphericalDiameter=2, Square=3),
        ),
        DatumReferenceBuilder=NS(
            DatumReferenceMaterialCondition=NS(
                NotSet=0,
                MaximumMaterialCondition=1,
                LeastMaterialCondition=2,
                RegardlessOfFeatureSize=3,
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "NXOpen.Annotations", a)
    ff.nx.Annotations = a
    face = ff.ref(ff.faces[0], "face")
    result = ff.e._pmi_datum([face], "A", [10, 10, 0])
    assert result["geometry_associated"]
    db.AssociatedObjects.Nxobjects.Add.assert_called_with([ff.faces[0]])
    fc = ff.e._pmi_fcf([face], "Parallelism", 0.1, [10, 10, 0], datums=[result["annotation"]["id"]])
    assert fc["datum_letters"] == ["A"]
    assert fb.FeatureControlFrameDataList.FindItem.return_value.ToleranceValue == "0.1"
    updated = ff.e._pmi_fcf([face], "Flatness", 0.2, [10, 10, 0], annotation=fc["annotation"]["id"])
    assert updated["created"] == [] and len(updated["modified"]) == 1
    with pytest.raises(NXToolError):
        ff.e._pmi_fcf([face], "Flatness", 0.1, [0, 0, 0], datums=[result["annotation"]["id"]])
    with pytest.raises(NXToolError):
        ff.e._pmi_datum([face], "a", [0, 0, 0])
    db.Validate.return_value = False
    with pytest.raises(NXToolError):
        ff.e._pmi_datum([face], "B", [0, 0, 0])


def test_animation_renders_absolute_poses_and_restores_after_failure(ff, tmp_path):
    from pathlib import Path

    ff.e._explosion_context = lambda: ff.part
    ff.e._explosion = lambda _: NS(Tag=99)
    ff.e._exploded_tree = lambda _: {1: (None, None, None, False)}
    ff.e._exploded_record = lambda _: {
        "component": {"id": "component"},
        "assembled_translation": [0, 0, 0],
        "translation": [20, 10, 0],
        "assembled_rotation_matrix": IDENTITY,
        "rotation_matrix": IDENTITY,
    }
    ff.e._explosion_uf = lambda: ff.uf.Assem
    ff.uf.Assem.AskViewExplosion.return_value = 0
    ff.session.IsBatch = False
    ff.part.DrawingSheets = NS(CurrentDrawingSheet=None)
    ff.part.ModelingViews = NS(WorkView=NS(Tag=2, UpdateDisplay=Mock()))
    ff.session.UndoToMark = Mock()
    ff.session.DeleteUndoMark = Mock()
    ff.e._edit_explosion = Mock()

    def render(path, **_):
        Path(path).write_bytes(b"native png fixture")
        return {"camera": {"scale": 1}}

    ff.e._render_view = Mock(side_effect=render)
    destination = ff.e.workspace.root / "animation.html"
    result = ff.e._export_explosion_animation("ex", str(destination), frames=3)
    assert result["model_restored"] and result["frame_count"] == 3
    assert "data:image/png;base64," in destination.read_text()
    placements = ff.e._edit_explosion.call_args_list
    assert placements[1].kwargs["placements"][0]["translation"] == [10, 5, 0]
    ff.session.UndoToMark.assert_called_once()
    ff.e._render_view.side_effect = RuntimeError("capture failed")
    failure_path = ff.e.workspace.root / "failed.html"
    with pytest.raises(RuntimeError, match="capture failed"):
        ff.e._export_explosion_animation("ex", str(failure_path), frames=2)
    assert not failure_path.exists()
    assert not list(ff.e.workspace.root.glob(".nx-animation-*"))
    assert ff.session.UndoToMark.call_count == 2


def test_trace_anchor_maps_assembled_to_exploded_coordinates(ff):
    source = NS(Coordinates=point(10, 25, 0))
    ff.part.Points.CreatePoint.return_value = source
    ff.part.Scalars = NS(CreateScalar=Mock(return_value=Object()))
    ff.nx.Scalar = NS(DimensionalityType=NS(NotSet=0))
    ff.nx.PointCollection = NS(PointOnCurveLocationOption=NS(PercentArcLength=1))
    ff.session.UpdateManager.DoUpdate = Mock(return_value=0)
    component = NS(
        Tag=123,
        JournalIdentifier="component",
        GetPosition=lambda: (point(10, 20, 0), ff.e._nx_matrix([[0, -1, 0], [1, 0, 0], [0, 0, 1]])),
    )
    edge = ff.body.GetEdges()[0]
    edge.JournalIdentifier = "edge"
    ff.uf.Tag.AskTagOfHandle.side_effect = lambda name: {
        "component": 123,
        "edge": int(edge.Tag),
    }.get(name, 0)
    component.Prototype = NS(Bodies=[ff.body])
    component.FindOccurrence = lambda _: edge
    exploded = NS(GetPosition=lambda: (point(100, 0, 0), ff.e._nx_matrix(IDENTITY)))
    ff.e._exploded_tree = lambda _: {1: (exploded, component, ["component"], False)}
    assert ff.e._trace_position(
        "ex", {"edge": "edge", "percent": 50, "component": "component"}
    ) == [105, 0, 0]
    with pytest.raises(NXToolError, match="missing"):
        ff.e._trace_position("ex", {"edge": "edge", "percent": 50, "component": "missing"})


def test_managed_trace_refresh_handles_collapsed_and_expanded_states(ff):
    import json

    line = MagicMock()
    line.AskExplosion.return_value = "ex"
    line.HasUserAttribute.return_value = True
    line.GetStringAttribute.return_value = json.dumps([{"point": "first"}, {"point": "second"}])
    ff.part.Tracelines = [line]
    ff.nx.NXObject = NS(AttributeType=NS(String=1))
    ff.session.UpdateManager.DoUpdate = Mock(return_value=0)
    ff.e._trace_position = Mock(side_effect=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    ff.e._refresh_explosion_traces("ex")
    line.Blank.assert_called_once()
    line.StartPoint.SetCoordinates.assert_not_called()
    ff.e._trace_position.side_effect = [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]
    ff.e._refresh_explosion_traces("ex")
    line.Unblank.assert_called_once()
    assert line.EndPoint.SetCoordinates.call_args.args[0].X == 10
