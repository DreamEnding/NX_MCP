"""Builder arguments, coordinate frames and transaction contracts, not kernel tests."""

import math
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp.hardened import IDENTITY, matmul, rows
from nx_mcp.runtime import NXToolError
from tests.fakes import Body, Component, Curve, Feature, Sketch, point

pytestmark = pytest.mark.fake_nx


def setup_sketch_builder(rig, mismatch=False, fail=False):
    part = rig.part
    built = []
    part.CoordinateSystems = NS(CreateCoordinateSystem=lambda p, m, _: NS(Origin=p, Orientation=m))

    def builder(_):
        b = NS(Destroy=Mock())

        def commit():
            if fail:
                raise RuntimeError("builder failed")
            sk = Sketch(rig.session)
            sk.Origin = b.Csystem.Origin
            sk.Orientation.Element = b.Csystem.Orientation
            if mismatch:
                sk.Origin = point(99, 99, 99)
            part.Sketches.append(sk)
            return sk

        b.Commit = commit
        built.append(b)
        return b

    part.Sketches.CreateSketchInPlaceBuilder2 = builder

    def line(start, end):
        c = Curve()
        c.StartPoint = start
        c.EndPoint = end
        part.Curves.append(c)
        return c

    def arc(center, x, y, r, start, end):
        c = Curve()
        c.CenterPoint = center
        c.Radius = r
        part.Curves.append(c)
        return c

    part.Curves.CreateLine = line
    part.Curves.CreateArc = arc
    return built


@pytest.mark.parametrize(
    "plane,normal,end",
    [("XY", [0, 0, 1], [3, 4, 0]), ("XZ", [0, -1, 0], [3, 0, 4]), ("YZ", [1, 0, 0], [0, 3, 4])],
)
def test_principal_sketch_curve_mapping(rig, plane, normal, end):
    built = setup_sketch_builder(rig)
    result = rig.e.execute("nx_create_sketch", {"plane": plane, "name": "profile"})
    assert result["frame"]["normal"] == normal
    sk = rig.session.ActiveSketch
    rig.e._create_sketch_line(sk, rig.part, {"x": 0, "y": 0}, {"x": 3, "y": 4})
    rig.e._sketch_arc_legacy(1, 2, 3, 0, 360, result["object"]["id"])
    info = rig.e._sketch_info(result["object"]["id"])
    assert info["curves"][0]["end"] == end and info["curve_count"] == 2
    assert info["curves"][1]["radius"] == 3
    assert built[0].Destroy.call_count == 1


def test_arbitrary_basis_and_failed_frame_verification_roll_back(rig):
    setup_sketch_builder(rig)
    result = rig.e._create_sketch(origin=[1, 2, 3], x_axis=[0, 1, 0], y_axis=[-1, 0, 0])
    sk = rig.session.ActiveSketch
    p = rig.e._point_on_sketch(sk, {"x": 2, "y": 5})
    assert [p.X, p.Y, p.Z] == [-4, 4, 3]
    assert result["frame"]["normal"] == [0, 0, 1]
    before = len(rig.part.Sketches)
    built = setup_sketch_builder(rig, mismatch=True)
    with pytest.raises(NXToolError) as error:
        rig.e.execute("nx_create_sketch", {})
    assert error.value.code == "NX_FRAME_MISMATCH" and len(rig.part.Sketches) == before
    assert built[0].Destroy.call_count == 1


@pytest.mark.parametrize(
    "params",
    [
        {"plane": "bad"},
        {"x_axis": [1, 0, 0]},
        {"x_axis": [2, 0, 0], "y_axis": [0, 1, 0]},
        {"x_axis": [1, 0, 0], "y_axis": [1, 0, 0]},
        {"origin": [0, float("inf"), 0]},
    ],
)
def test_sketch_basis_preflight(rig, params):
    built = setup_sketch_builder(rig)
    with pytest.raises(NXToolError):
        rig.e._create_sketch(**params)
    assert not built


def test_curve_owner_and_arc_limits(rig):
    setup_sketch_builder(rig)
    sk = Sketch(rig.session)
    with pytest.raises(NXToolError):
        rig.e._create_sketch_line(sk, rig.part, {"x": 0, "y": 0}, {"x": 1, "y": 1})
    with pytest.raises(NXToolError):
        rig.e._sketch_arc_legacy(0, 0, 1, 0, 90)
    rig.session.ActiveSketch = sk
    for radius, end in [(0, 90), (1, 0), (1, 361), (math.inf, 90)]:
        with pytest.raises(NXToolError):
            rig.e._sketch_arc_legacy(0, 0, radius, 0, end)
    with pytest.raises(NXToolError):
        rig.e._extrude("unresolved", float("nan"))


def feature_builders(rig):
    f = Feature()
    rig.part.Features.append(f)

    def expr():
        return NS(RightHandSide="")

    spacing = NS(NCopies=expr(), PitchDistance=expr())
    b = NS(
        Limits=NS(EndExtend=NS(Value=expr())),
        PatternService=NS(RectangularDefinition=NS(XSpacing=spacing, YSpacing=NS(NCopies=expr()))),
        FeatureList=NS(Add=Mock()),
        CommitFeature=Mock(return_value=f),
        Destroy=Mock(),
    )
    rig.part.Features.CreateExtrudeBuilder = lambda _: b
    rig.part.Features.CreatePatternFeatureBuilder = lambda _: b
    rig.nx.Features.PatternFeatureBuilder = NS(PatternMethodOptions=NS(Simple="simple"))
    rig.nx.Features.Feature.Null = None
    rig.nx.GeometricUtilities = NS(PatternDefinition=NS(PatternEnum=NS(Linear="linear")))
    import sys

    sys.modules["NXOpen.GeometricUtilities"] = rig.nx.GeometricUtilities
    rig.nx.SmartObject = NS(UpdateOption=NS(WithinModeling="model"))
    rig.part.Directions = NS(CreateDirection=lambda p, v, _: v)
    rig.e._active_mark = 1
    return f, b


def test_supported_feature_edits_and_native_pattern_parameters(rig):
    f, b = feature_builders(rig)
    ref = rig.ref(f, "feature")
    info = rig.e._edit_feature(ref, {"distance": 46.25})
    assert b.Limits.EndExtend.Value.RightHandSide == "46.25" and info["modified"] == [
        info["feature"]
    ]
    f.FeatureType = "PATTERN_FEATURE"
    rig.e._edit_feature(ref, {"count": 16, "spacing": 16.5})
    assert b.PatternService.RectangularDefinition.XSpacing.NCopies.RightHandSide == "16"
    result = rig.e._pattern([ref], direction="-Y", count=16, spacing=16.5)
    assert result["count_includes_seed"] and result["count"] == 16
    assert b.PatternService.RectangularDefinition.XDirection.Y == -1
    assert b.Destroy.call_count == 3
    rig.session.UpdateManager.DoUpdate.return_value = 1
    with pytest.raises(NXToolError, match="update errors"):
        rig.e._edit_feature(ref, {"count": 3})


@pytest.mark.parametrize("params", [{}, {"bad": 1}, {"distance": 0}, {"distance": float("inf")}])
def test_unsupported_feature_edits_leave_builder_uncommitted(rig, params):
    f, b = feature_builders(rig)
    with pytest.raises(NXToolError):
        rig.e._edit_feature(rig.ref(f, "feature"), params)
    b.CommitFeature.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pattern_type": "circular"},
        {"count": True},
        {"count": 1},
        {"spacing": 0},
        {"direction": "north"},
    ],
)
def test_pattern_validation_before_builder(rig, kwargs):
    _, b = feature_builders(rig)
    with pytest.raises(NXToolError):
        rig.e._pattern([], **kwargs)
    b.CommitFeature.assert_not_called()


def test_transform_readback_and_relative_composition(rig, tmp_path):
    root = Component("root")
    c = Component("instance", parent=root)
    rig.part.ComponentAssembly.RootComponent = root

    def move(obj, delta, rotation):
        obj.position = point(
            obj.position.X + delta.X, obj.position.Y + delta.Y, obj.position.Z + delta.Z
        )
        obj.rotation = rig.e._nx_matrix(matmul(rows(rotation), rows(obj.rotation)))

    rig.part.ComponentAssembly.MoveComponent = move
    ref = rig.ref(c, "component")
    r = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    result = rig.e._set_component_transform(ref, [1, 2, 3], r)
    assert result["translation"] == [1, 2, 3] and result["rotation_matrix"] == r
    result = rig.e._reposition_component(ref, dx=4, rz=90)
    assert result["translation"] == [5, 2, 3] and abs(result["rotation_matrix"][0][0] + 1) < 1e-8
    rig.part.ComponentAssembly.MoveComponent = Mock()
    with pytest.raises(NXToolError) as error:
        rig.e._set_component_transform(ref, [9, 9, 9], IDENTITY)
    assert error.value.code == "NX_PLACEMENT_MISMATCH"
    c.Parent = Component("other")
    with pytest.raises(NXToolError):
        rig.e._set_component_transform(ref, [0, 0, 0], IDENTITY)
    c.Parent = root
    rig.part.ComponentAssembly.AddComponent = lambda *args: (c, NS(Dispose=Mock()))
    assert rig.e._add_component(str(tmp_path / "proto.prt"), translation=[1, 2, 3])[
        "translation"
    ] == [1, 2, 3]
    assert rig.e._list_components()["count"] == 1
    new = rig.e._rename_object(ref, "renamed")["object"]["id"]
    assert new != ref and c.Name == "renamed"
    with pytest.raises(NXToolError):
        rig.e._rename_object(new, "")


@pytest.mark.parametrize("rotation", [[], [[1, 0, 0]] * 3, [[1, 0, 0], [0, 1, 0], [0, 0, -1]]])
def test_rotation_rejects_scale_shear_or_reflection(rig, rotation):
    with pytest.raises(NXToolError):
        rig.e._validate_rotation(rotation)


def test_batch_preflight_cancellation_and_progress(rig):
    rig.e._current_operation = "batch-operation"
    rig.e.store.put({"operation_id": "batch-operation", "state": "running"})
    calls = []
    rig.e._handlers["nx_sketch_line"] = lambda start, end: calls.append((start, end)) or {}
    ops = [{"method": "nx_sketch_line", "params": {"start": 1, "end": 2}}] * 2
    assert rig.e._batch(ops)["count"] == 2 and len(calls) == 2
    assert rig.e.store.get("batch-operation")["progress"] == {"completed": 2, "total": 2}
    for bad in [
        [],
        [{"method": "nx_bad", "params": {}}],
        [{"method": "nx_sketch_line", "params": {}}],
    ]:
        with pytest.raises((NXToolError, TypeError)):
            rig.e._batch(bad)
    assert len(calls) == 2
    rig.e.store.path("batch-operation").with_suffix(".cancel").touch()
    with pytest.raises(NXToolError) as error:
        rig.e._batch(ops)
    assert error.value.code == "NX_CANCELLED" and len(calls) == 2


def test_step_import_validates_conflicts_and_reports_no_output(rig, tmp_path):
    source = tmp_path / "vendor.step"
    source.write_text(
        "ISO-10303-21; PRODUCT('test','test'); NEXT_ASSEMBLY_USAGE_OCCURRENCE END-ISO-10303-21;"
    )
    with pytest.raises(NXToolError) as error:
        rig.e._import_geometry(str(source))
    assert error.value.code == "NX_IMPORT_NAME_CONFLICT"
    rig.nx.Step214Importer = NS(ImportToOption=NS(WorkPart="work"))
    builder = NS(ObjectTypes=NS(), Commit=Mock(), Destroy=Mock())
    rig.session.DexManager = NS(CreateStep214Importer=lambda: builder)
    rig.e._current_operation = "import-operation"
    with pytest.raises(NXToolError, match="without imported"):
        rig.e._import_geometry(str(source), flatten=True)
    assert (
        builder.Destroy.call_count == 1
        and Path(builder.InputFile).read_text() == source.read_text()
    )
    rig.e._current_operation = "import-operation-2"
    builder.Commit = Mock(side_effect=lambda: rig.part.Bodies.append(Body("imported")))
    result = rig.e._import_geometry(str(source), flatten=True)
    assert result["body_count"] == 1 and result["translator"].endswith("WorkPart")
    for path, kwargs in [
        (str(tmp_path / "x.iges"), {}),
        (str(tmp_path / "missing.step"), {}),
        (str(source), {"target": "invalid"}),
        (str(source), {"target": "new_part"}),
        (str(source), {"output_path": "x.prt"}),
        (str(source), {"target": "new_part", "output_path": str(source)}),
    ]:
        with pytest.raises(NXToolError):
            rig.e._import_geometry(path, **kwargs)


def test_save_as_creates_parents_and_rejects_existing_destination(rig, tmp_path):
    destination = tmp_path / "project" / "revisions" / "base_r02.prt"

    def save(path):
        assert Path(path).parent.is_dir()
        Path(path).write_bytes(b"saved fixture")
        rig.part.FullPath = path
        return NS(Dispose=Mock())

    rig.part.SaveAs = Mock(side_effect=save)
    result = rig.e._save_as(str(destination))
    assert result["path"] == str(destination)
    with pytest.raises(NXToolError) as error:
        rig.e._save_as(str(destination))
    assert error.value.code == "NX_FILE_EXISTS"
    assert rig.part.SaveAs.call_count == 1
    assert destination.read_bytes() == b"saved fixture"


@pytest.mark.parametrize(
    "contents",
    ["ISO-10303-21; DATA; #1=BODY();", "garbage", "/* END-ISO-10303-21; */ ISO-10303-21;"],
)
def test_truncated_step_rejected_before_translator(rig, tmp_path, contents):
    source = tmp_path / "broken.step"
    source.write_text(contents)
    translator = Mock()
    rig.session.DexManager = NS(CreateStep214Importer=translator)
    with pytest.raises(NXToolError) as error:
        rig.e._import_geometry(str(source), flatten=True)
    assert error.value.code == "NX_INVALID_STEP"
    translator.assert_not_called()
