"""Regression contracts for observed NX failures; native receipts validate geometry.

These seams verify validation, result cardinality, transform semantics, and cleanup.
They do not claim to emulate the NX geometric kernel.
"""

import contextlib
import struct
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp.hardened import IDENTITY
from nx_mcp.runtime import NXToolError
from tests.fakes import Body, Edge, Feature, Object, Sketch, point

pytestmark = pytest.mark.fake_nx


@pytest.fixture
def eng(rig, monkeypatch):
    r = rig
    r.body = Body()
    r.feature = Feature(bodies=[r.body])
    r.sketch = Sketch(r.session)
    monkeypatch.setattr(Sketch, "Feature", r.feature, raising=False)
    r.part.Bodies.append(r.body)
    r.part.Features.append(r.feature)
    r.part.Sketches.append(r.sketch)
    for o in [r.body, r.feature, r.sketch, *r.body.faces, *r.body.edges]:
        o.OwningPart = r.part
        o.IsOccurrence = False
    for o in [*r.body.faces, *r.body.edges]:
        o.GetBody = lambda: r.body
    r.nx.SmartObject = NS(UpdateOption=NS(WithinModeling=1))
    r.nx.Features.MoveObject = Feature
    r.part.Directions = NS(CreateDirection=lambda p, v, _: NS(origin=p, vector=v))
    r.part.CoordinateSystems = NS(
        CreateCoordinateSystem=lambda p, m, _: NS(Origin=p, Orientation=m)
    )
    r.part.ScRuleFactory = NS(CreateRuleOptions=lambda: NS(Dispose=Mock()))
    for kind in ["Edge", "Body", "Face"]:
        setattr(r.part.ScRuleFactory, "CreateRule" + kind + "Dumb", lambda objects: objects)
    r.part.ScRuleFactory.CreateRuleCurveFeature = lambda objects, *_: objects

    class Collector:
        def ReplaceRules(self, rules, *_):
            self.rules = rules

        def GetRules(self):
            return self.rules

    r.part.ScCollectors = NS(CreateCollector=Collector)
    r.part.Sections = NS(CreateSection=lambda: NS(AddToSection=Mock()))
    r.nx.Section = NS(Mode=NS(Create=1))
    g = NS(
        Extend=NS(ExtendType=NS(Value="value", ThroughAll="all", UntilSelected="face")),
        BooleanOperation=NS(
            BooleanType=NS(
                Create="create", Unite="unite", Subtract="subtract", Intersect="intersect"
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "NXOpen.GeometricUtilities", g)
    r.nx.GeometricUtilities = g
    r.e._update_model = Mock()
    r.e._check_sketch_result = Mock(return_value={"valid": True})
    r.e._editing_sketch = lambda _: contextlib.nullcontext()
    r.builders = []

    def builder(*_):
        def expression():
            return NS(RightHandSide="0")

        b = NS(
            Destroy=Mock(),
            CommitFeature=Mock(return_value=r.feature),
            Commit=Mock(return_value=r.feature),
            DefaultThickness=expression(),
            StationaryReference=Collector(),
            FaceSetAngleExpressionList=NS(Append=Mock()),
            Type=NS(Face=1),
            SectionsList=NS(Append=Mock()),
            BodyPreferenceTypes=NS(Solid=1, Sheet=2),
            Limits=NS(StartExtend=NS(Value=expression()), EndExtend=NS(Value=expression())),
            BooleanOperation=NS(SetTargetBodies=Mock()),
            BooleanOption=NS(SetTargetBodies=Mock()),
            Diameter=expression(),
            Height=expression(),
            FirstOffsetExp=expression(),
            ChamferOption=NS(SymmetricOffsets=1),
            AddChainset=Mock(),
            ExtractType=NS(Body=1),
            ExtractBodyCollector=Collector(),
            ObjectToMoveObject=NS(Add=Mock()),
            MoveObjectResultOptions=NS(MoveOriginal=1),
            TransformMotion=NS(Options=NS(CsysToCsys=1)),
            MirrorBodyCollector=Collector(),
            Plane=NS(),
            SectionList=NS(Append=Mock()),
            GuideList=NS(Append=Mock()),
        )
        r.builders.append(b)
        return b

    r.builder = builder
    for name in [
        "Extrude",
        "Shell",
        "ThroughCurves",
        "Draft",
        "EdgeBlend",
        "Chamfer",
        "Cylinder",
        "ExtractFace",
        "MirrorBody",
        "Swept",
    ]:
        setattr(r.part.Features, "Create" + name + "Builder", builder)
    r.part.BaseFeatures = NS(CreateMoveObjectBuilder=builder)
    r.part.Datums = NS(CreateFixedDatumPlane=Mock(return_value=Object("plane")))
    r.part.CreateExpressionCollectorSet = lambda *args: args
    return r


def test_owned_geometry_rejects_occurrences_and_other_parts(eng):
    ref = eng.ref(eng.body)
    eng.body.IsOccurrence = True
    with pytest.raises(NXToolError, match="owned"):
        eng.e._engineering_owned(ref, "body")
    eng.body.IsOccurrence = False
    eng.body.OwningPart = object()
    with pytest.raises(NXToolError, match="owned"):
        eng.e._engineering_owned(ref, "body")


@pytest.mark.parametrize(
    "params",
    [
        {"distance": 8, "start": -2},
        {"distance": 8, "symmetric": True},
        {"distance": 8, "direction": [0, 1, 1], "reverse": True},
        {"end_type": "through_all", "boolean": "subtract"},
        {"end_type": "up_to_face"},
    ],
)
def test_extended_extrude_preserves_limits_normal_and_multibody_results(eng, params):
    r = eng
    if params.get("boolean"):
        params = {**params, "targets": [r.ref(r.body)]}
    if params.get("end_type") == "up_to_face":
        params = {**params, "target_face": r.ref(r.body.faces[0], "face")}
    second = Body()
    r.feature.bodies.append(second)
    out = r.e._extrude(r.ref(r.sketch, "sketch"), **params)
    assert out["body_count"] == 2 and len(out["bodies"]) == 2
    b = r.builders[-1]
    assert b.Destroy.call_count == 1
    if params.get("symmetric"):
        assert float(b.Limits.StartExtend.Value.RightHandSide) == -4
        assert float(b.Limits.EndExtend.Value.RightHandSide) == 4
    if params.get("reverse"):
        assert out["limits"]["direction"][1] < 0
    if params.get("boolean"):
        b.BooleanOperation.SetTargetBodies.assert_called_once_with([r.body])


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"start": 1, "end_type": "bogus"},
        {"start": 1, "boolean": "Create"},
        {"start": 1, "symmetric": True, "distance": 4},
        {"end_type": "up_to_face"},
        {"start": 5, "distance": 4},
        {"end_type": "through_all", "distance": 2},
        {"end_type": "through_all"},
        {"start": 1, "distance": 4, "boolean": "subtract"},
        {"distance": float("nan"), "start": 1},
        {"start": 1, "distance": 4, "direction": [0, 0, 0]},
    ],
)
def test_invalid_extrude_arguments_do_not_allocate_builders(eng, params):
    with pytest.raises(NXToolError):
        eng.e._extrude(eng.ref(eng.sketch, "sketch"), **params)
    assert not eng.builders


@pytest.mark.parametrize("outward,flipped", [(False, True), (True, False)])
def test_shell_direction_regression_and_removed_face_ownership(eng, outward, flipped):
    r = eng
    r.e._shell(r.ref(r.body), 1, [r.ref(r.body.faces[0], "face")], outward)
    assert r.builders[-1].DefaultThicknessFlip is flipped
    assert r.builders[-1].Tolerance > 0
    r.body.faces[0].GetBody = lambda: Body()
    with pytest.raises(NXToolError, match="belong"):
        r.e._shell(r.ref(r.body), 1, [r.ref(r.body.faces[0], "face")])
    assert len(r.builders) == 1


@pytest.mark.parametrize(
    "method,args",
    [
        ("_shell", {"thickness": 0}),
        ("_blend", {"radius": 0}),
        ("_chamfer", {"offset": -1}),
        ("_draft", {"angle": 90}),
        ("_loft", {"sketches": []}),
    ],
)
def test_invalid_feature_dimensions_rejected(eng, method, args):
    if method == "_shell":
        args["body"] = eng.ref(eng.body)
    elif method in {"_blend", "_chamfer"}:
        args["edges"] = []
    elif method == "_draft":
        args.update(faces=[], stationary_face="", direction=[0, 0, 1])
    with pytest.raises(NXToolError):
        getattr(eng.e, method)(**args)
    assert not eng.builders


@pytest.mark.parametrize("solid", [True, False])
def test_loft_sections_are_ordered_and_all_results_reported(eng, solid):
    other = Sketch(eng.session)
    other.IsOccurrence = False
    other.OwningPart = eng.part
    eng.part.Sketches.append(other)
    result = eng.e._loft([eng.ref(eng.sketch, "sketch"), eng.ref(other, "sketch")], solid)
    assert result["body_count"] == 1
    assert eng.builders[-1].SectionsList.Append.call_count == 2
    assert eng.builders[-1].BodyPreference == (1 if solid else 2)


def test_builder_failure_always_destroys_native_handle(eng):
    b = eng.builder()
    b.CommitFeature.side_effect = RuntimeError("native failed")
    eng.part.Features.CreateShellBuilder = lambda _: b
    with pytest.raises(RuntimeError, match="native failed"):
        eng.e._shell(eng.ref(eng.body), 1)
    b.Destroy.assert_called_once()


@pytest.mark.parametrize("method,arg", [("_blend", "radius"), ("_chamfer", "offset")])
def test_edge_features_resolve_real_edges_and_reject_mixed_bodies(eng, method, arg):
    r = eng
    edge = r.body.edges[0]
    out = getattr(r.e, method)([r.ref(edge, "edge")], **{arg: 1})
    assert out["modified"][0]["id"] == r.ref(r.body)
    assert r.builders[-1].Destroy.call_count == 1
    foreign = Edge()
    foreign.OwningPart = r.part
    foreign.IsOccurrence = False
    foreign.GetBody = lambda: Body()
    with pytest.raises(NXToolError, match="one owned body"):
        getattr(r.e, method)([r.ref(edge, "edge"), r.ref(foreign, "edge")], **{arg: 1})


def test_hole_numeric_conversion_explicit_target_and_direction(eng):
    r = eng
    out = r.e._hole(2, 5, 1, 2, 3, body=r.ref(r.body), direction=[0, 0, -2])
    assert out["location"] == [1.0, 2.0, 3.0] and out["direction"] == [0, 0, -1]
    assert r.builders[-1].Origin.Z == 3.0
    r.part.Bodies.append(Body())
    with pytest.raises(NXToolError, match="one owned solid"):
        r.e._hole(2, 5, 1, 2, 3)


def test_draft_excludes_stationary_face_and_sets_tolerances(eng):
    from tests.fakes import Face

    r = eng
    fixed = r.body.faces[0]
    side = Face()
    side.IsOccurrence = False
    side.OwningPart = r.part
    side.GetBody = lambda: r.body
    r.e._draft([r.ref(side, "face")], r.ref(fixed, "face"), [0, 0, 1], 5)
    assert r.builders[-1].AngleTolerance > 0 and r.builders[-1].DistanceTolerance > 0
    with pytest.raises(NXToolError, match="exclude"):
        r.e._draft([r.ref(fixed, "face")], r.ref(fixed, "face"), [0, 0, 1], 5)


@pytest.mark.parametrize("copy", [False, True])
def test_body_transform_is_absolute_and_copy_uses_associative_extract(eng, copy):
    r = eng
    out = r.e._transform_bodies([20, 30, 40], IDENTITY, [r.ref(r.body)], copy)
    move = r.builders[-1]
    assert move.MoveParents is False and move.Associative is True
    assert move.TransformMotion.FromCsys.Origin.X == 0
    assert move.TransformMotion.ToCsys.Origin.X == 20
    assert bool(out["copy_feature"]) == copy
    edited = r.e._transform_bodies([50, 30, 40], IDENTITY, feature=out["feature"]["id"])
    assert edited["translation"] == [50, 30, 40]
    assert r.builders[-1].TransformMotion.ToCsys.Origin.X == 50
    with pytest.raises(NXToolError, match="omit"):
        r.e._transform_bodies([0, 0, 0], IDENTITY, [r.ref(r.body)], feature=out["feature"]["id"])
    with pytest.raises(NXToolError, match="Select bodies"):
        r.e._transform_bodies([0, 0, 0], IDENTITY)


def test_nonassociative_copy_result_is_rejected(eng):
    eng.nx.Features.MoveObject = type("MoveObject", (Feature,), {})
    with pytest.raises(NXToolError, match="editable MoveObject"):
        eng.e._transform_bodies([1, 0, 0], IDENTITY, [eng.ref(eng.body)])
    assert eng.builders[-1].Destroy.call_count == 1


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
def test_mirror_preserves_original_and_uses_origin_plane(eng, plane):
    result = eng.e._mirror_body(eng.ref(eng.body), plane)
    assert result["body_count"] == 1 and eng.builders[-1].DeleteSourceBody is False
    assert eng.part.Datums.CreateFixedDatumPlane.call_args.args[0].X == 0
    with pytest.raises(NXToolError):
        eng.e._mirror_body(eng.ref(eng.body), "custom")


def test_sweep_requires_distinct_owned_sketches_and_explicit_boolean_target(eng):
    r = eng
    other = Sketch(r.session)
    other.IsOccurrence = False
    other.OwningPart = r.part
    a, b = r.ref(r.sketch, "sketch"), r.ref(other, "sketch")
    out = r.e._sweep(a, b)
    assert out["body_count"] == 1 and r.builders[-1].GuideList.Append.call_count == 1
    for args in [
        (a, a, "none", None),
        (a, b, "bad", None),
        (a, b, "subtract", None),
        (a, b, "none", [r.ref(r.body)]),
        (a, b, "subtract", []),
    ]:
        with pytest.raises(NXToolError):
            r.e._sweep(*args)


@pytest.fixture
def rendering(eng):
    r = eng
    r.nx.View.RenderingStyleType.Studio = "studio"
    view = NS(RenderingStyle="original", UpdateDisplay=Mock())
    r.part.ModelingViews = NS(WorkView=view)
    r.e._view_info = lambda: {"camera": "unchanged"}
    lights = NS(
        LightsShadedViewsLightingCollection="old",
        LightingCollectionType=NS(**{"Lighting" + str(i): i for i in range(1, 6)}),
        Commit=Mock(),
        Destroy=Mock(),
    )
    capture = NS(
        SourceType=NS(WorkView=1),
        UnitsEnumType=NS(Pixels=1),
        BackgroundOptions=NS(Original=1, CustomColor=2, Transparent=3),
        SetCustomBackgroundColor=Mock(),
        Destroy=Mock(),
    )

    def dimensions(v):
        capture.dimensions = v

    capture.SetImageDimensionsInteger = dimensions

    def commit():
        height, width = capture.dimensions
        data = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", width, height)
        Path(capture.NativeFileBrowser).write_bytes(data)

    capture.Commit = Mock(side_effect=commit)
    r.part.Views = NS(
        CreateStudioImageCaptureBuilder=lambda: capture, CreateLighting=lambda _: lights
    )
    r.view, r.lights, r.capture = view, lights, capture
    return r


@pytest.mark.parametrize(
    "background,color",
    [("white", None), ("original", None), ("transparent", None), ("color", [0.2, 0.3, 0.4])],
)
def test_render_artifact_dimensions_checksum_and_view_restoration(rendering, background, color):
    import hashlib

    r = rendering
    out = r.e._render_view(width=640, height=480, background=background, color=color, lighting=2)
    assert out["resolution"] == [640, 480]
    assert out["sha256"] == hashlib.sha256(Path(out["path"]).read_bytes()).hexdigest()
    assert r.view.RenderingStyle == "original"
    assert r.lights.LightsShadedViewsLightingCollection == "old"
    r.lights.Destroy.assert_called_once()
    r.capture.Destroy.assert_called_once()
    with pytest.raises(NXToolError):
        r.e._render_view(path=out["path"])


@pytest.mark.parametrize(
    "params",
    [
        {"width": True},
        {"height": 100},
        {"background": "bad"},
        {"style": "bad"},
        {"color": [1, 1, 1]},
        {"background": "color"},
        {"background": "color", "color": [0, 0, 2]},
        {"lighting": 0},
        {"lighting": True},
        {"path": "bad.jpg"},
    ],
)
def test_invalid_render_requests_have_no_side_effects(rendering, params):
    if "path" in params:
        params = {**params, "path": str(rendering.e.workspace.root / params["path"])}
    with pytest.raises(NXToolError):
        rendering.e._render_view(**params)
    rendering.capture.Commit.assert_not_called()
    assert rendering.view.RenderingStyle == "original"


@pytest.mark.parametrize("failure", ["native", "invalid_png", "wrong_resolution"])
def test_render_failure_removes_partial_file_and_restores_view(rendering, failure):
    r = rendering
    file = r.e.workspace.root / "bad.png"

    def commit():
        file.write_bytes(b"partial")
        if failure == "native":
            raise RuntimeError("failed")
        if failure == "wrong_resolution":
            file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 1, 1))

    r.capture.Commit.side_effect = commit
    with pytest.raises((RuntimeError, NXToolError)):
        r.e._render_view(path=str(file), lighting=1)
    assert not file.exists() and r.view.RenderingStyle == "original"
    assert r.lights.LightsShadedViewsLightingCollection == "old"


def test_render_requires_interactive_display_part(rendering):
    r = rendering
    r.session.IsBatch = True
    with pytest.raises(NXToolError, match="interactive"):
        r.e._render_view()
    r.session.IsBatch = False
    r.session.Parts.Display = None
    with pytest.raises(NXToolError, match="display part"):
        r.e._render_view()


@pytest.fixture
def material(eng):
    r = eng
    r.nx.PhysicalMaterial = NS(Type=NS(Isotropic=1))
    materials = []

    class Materials(list):
        def CreatePhysicalMaterialBuilder(self, _):
            b = NS(
                PropertyTable=NS(
                    SetBaseScalarWithDataPropertyValue=lambda key, value, unit: setattr(
                        r, "density", value
                    )
                ),
                Destroy=Mock(),
            )

            def commit():
                m = NS(Name=b.Name, AssignObjects=Mock())
                self.append(m)
                return m

            b.Commit = commit
            r.material_builder = b
            return b

        def AskMaterialOfObject(self, _):
            return self[0] if self else None

    r.materials = Materials(materials)
    r.part.MaterialManager = NS(PhysicalMaterials=r.materials)
    r.part.UnitCollection = NS(FindObject=lambda name: name)
    r.nx.UF.Modl = NS(DensityUnits=NS(KILOGRAMS_METERS=1))
    r.density = 1000
    r.uf.Modeling = NS(AskBodyDensity=lambda *_: r.density)
    return r


def test_material_assignment_checks_native_density_and_names(material):
    r = material
    assert r.e._material_info()["bodies"][0]["material_name"] is None
    out = r.e._set_material([r.ref(r.body)], "Aluminum", 2700)
    assert out["verified_densities_kg_m3"] == [2700]
    assert r.e._material_info()["bodies"][0]["material_name"] == "Aluminum"
    r.materials[0].AssignObjects.assert_called_once_with([r.body])
    with pytest.raises(NXToolError, match="new material name"):
        r.e._set_material([r.ref(r.body)], "ALUMINUM", 1000)
    r.material_builder.Destroy.assert_called_once()


@pytest.mark.parametrize(
    "name,density,bodies", [("", 1000, True), ("a", -1, True), ("a", 1000, False)]
)
def test_invalid_material_assignment_precedes_creation(material, name, density, bodies):
    r = material
    with pytest.raises(NXToolError):
        r.e._set_material([r.ref(r.body)] if bodies else [], name, density)
    assert not r.materials


def test_material_density_readback_mismatch_is_error(material):
    r = material
    r.uf.Modeling.AskBodyDensity = lambda *_: 42
    with pytest.raises(NXToolError, match="densities differ"):
        r.e._set_material([r.ref(r.body)], "Aluminum", 2700)
    r.material_builder.Destroy.assert_called_once()


def test_mass_tensor_uses_centroidal_values_and_product_signs(material):
    from tests.fakes import matrix

    r = material
    values = list(range(47))
    r.uf.Modeling.AskMassProps3d = Mock(return_value=(values, [0] * 13))
    r.part.WCS = NS(CoordinateSystem=NS(Origin=point(1, 2, 3), Orientation=NS(Element=matrix())))
    out = r.e._mass_properties()
    assert out["center_of_gravity_m"] == [3, 4, 5]
    assert out["inertia_tensor_centroid_kg_m2"] == [[12, -19, -21], [-19, 13, -20], [-21, -20, 14]]
    assert out["wcs_origin_in_part_units"] == [1, 2, 3]
    assert out["semantics"] == "sum_of_included_bodies_with_assigned_densities"


def test_pdf_export_reports_actual_artifact_and_refuses_overwrite(eng):
    r = eng
    from contextlib import nullcontext

    r.e._drawing_save_context = lambda *_, **__: nullcontext()
    sheet = Object("Sheet1")
    sheet.Open = Mock()
    sheet.GetDraftingViews = Mock(return_value=[Object("View")])
    r.part.DrawingSheets = [sheet]
    b = NS(
        ActionOption=NS(Native=1),
        SizeOption=NS(FullScale=1),
        UnitsOption=NS(Metric=1),
        OutputTextOption=NS(Text=1),
        ImageResolutionOption=NS(High=3),
        Color=NS(AsDisplayed=0),
        SourceBuilder=NS(SetSheets=Mock()),
        Destroy=Mock(),
    )
    b.Commit = lambda: Path(b.Filename).write_bytes(b"%PDF-1.7\nfixture")
    r.e._active_mark = None
    r.e._update_model = Mock(side_effect=AssertionError("Export has no modeling mark"))
    r.part.PlotManager = NS(CreatePrintPdfbuilder=lambda: b)
    r.part.DraftingViews = NS(UpdateViews=Mock())
    file = r.e.workspace.root / "drawings" / "test.pdf"
    result = r.e._export_drawing_pdf(str(file))
    assert result["sheet_count"] == 1 and result["size"] == file.stat().st_size
    b.SourceBuilder.SetSheets.assert_called_once_with([sheet])
    assert b.RasterImages and not b.ShadedGeometry
    assert b.ImageResolution == b.ImageResolutionOption.High
    r.part.DraftingViews.UpdateViews.assert_called_once_with(sheet.GetDraftingViews())
    b.Destroy.assert_called_once()
    with pytest.raises(NXToolError):
        r.e._export_drawing_pdf(str(file))
    file.unlink()
    r.part.DrawingSheets = []
    with pytest.raises(NXToolError, match="drawing sheet"):
        r.e._export_drawing_pdf(str(file))


def test_invalid_pdf_output_is_removed(eng):
    r = eng
    from contextlib import nullcontext

    r.e._drawing_save_context = lambda *_, **__: nullcontext()
    sheet = Object("sheet")
    sheet.Open = Mock()
    sheet.GetDraftingViews = Mock(return_value=[Object("View")])
    r.part.DrawingSheets = [sheet]
    b = NS(
        ActionOption=NS(Native=1),
        SizeOption=NS(FullScale=1),
        UnitsOption=NS(Metric=1),
        OutputTextOption=NS(Text=1),
        ImageResolutionOption=NS(High=3),
        Color=NS(AsDisplayed=0),
        SourceBuilder=NS(SetSheets=Mock()),
        Destroy=Mock(),
    )
    b.Commit = lambda: Path(b.Filename).write_bytes(b"not PDF")
    r.e._active_mark = None
    r.e._update_model = Mock(side_effect=AssertionError("Export has no modeling mark"))
    r.part.PlotManager = NS(CreatePrintPdfbuilder=lambda: b)
    r.part.DraftingViews = NS(UpdateViews=Mock())
    file = r.e.workspace.root / "bad.pdf"
    with pytest.raises(NXToolError, match="did not produce"):
        r.e._export_drawing_pdf(str(file))
    assert not file.exists()
    b.Destroy.assert_called_once()


@pytest.fixture
def project(eng):
    import shutil

    from tests.fakes import Component, Part

    r = eng
    source = Path(r.part.FullPath)
    source.write_bytes(b"original assembly")
    prototype = source.parent / "prototype" / "body.prt"
    prototype.parent.mkdir()
    prototype.write_bytes(b"original body")
    root = Component("root")
    child = Component("child", parent=root)
    child.Prototype = NS(FullPath=str(prototype))
    root.children = [child]
    r.part.ComponentAssembly.RootComponent = root
    naming = {}
    clone = NS(
        OperationClass=NS(CLONE_OPERATION=1),
        Action=NS(CLONE=1),
        NamingTechnique=NS(USER_NAME=1),
        Initialise=Mock(),
        SetDefAction=Mock(),
        AddAssembly=Mock(),
        InitNamingFailures=lambda: None,
        Terminate=Mock(),
    )
    clone.SetNaming = lambda src, _, dst: naming.update({src: dst})
    clone.PerformClone = lambda _: [shutil.copyfile(src, dst) for src, dst in naming.items()]
    r.uf.Clone = clone

    def open_part(path):
        part = Part(r.session, Path(path))
        part.IsModified = False
        parent = Component("root")
        kid = Component("child", parent=parent)
        kid.Prototype = NS(FullPath=naming[str(prototype.resolve())])
        parent.children = [kid]
        part.ComponentAssembly.RootComponent = parent
        return {}

    r.e._open_part = open_part
    r.part.IsModified = False
    r.clone, r.naming, r.prototype = clone, naming, prototype
    return r


@pytest.mark.parametrize("activate", [False, True])
def test_project_copy_preserves_subfolders_sources_and_rewrites_dependencies(project, activate):
    r = project
    dest = r.e.workspace.root / "new" / "project"
    result = r.e._copy_project(str(dest), "COPY_", activate)
    assert result["references_verified"] and result["dependency_count"] == 1
    assert (dest / "prototype" / "COPY_body.prt").read_bytes() == b"original body"
    assert Path(r.part.FullPath).read_bytes() == b"original assembly"
    assert r.prototype.read_bytes() == b"original body"
    assert (dest / "nx-project-manifest.json").is_file()
    assert (r.session.Parts.Work == r.part) is (not activate)
    r.clone.Terminate.assert_called_once()


def test_project_copy_failure_removes_only_new_destination(project):
    r = project
    dest = r.e.workspace.root / "failed"

    def failure(_):
        first = next(iter(r.naming.values()))
        Path(first).write_bytes(b"partial")
        raise RuntimeError("translator stopped")

    r.clone.PerformClone = failure
    with pytest.raises(RuntimeError, match="translator stopped"):
        r.e._copy_project(str(dest), "COPY_")
    assert not dest.exists() and r.prototype.read_bytes() == b"original body"
    r.clone.Terminate.assert_called_once()


@pytest.mark.parametrize(
    "failure", ["existing", "prefix", "unsaved", "missing", "unloaded", "collision"]
)
def test_project_copy_preflight_prevents_unsafe_clone(project, failure):
    r = project
    dest = r.e.workspace.root / "new"
    prefix = "COPY_"
    if failure == "existing":
        dest.mkdir()
    if failure == "prefix":
        prefix = "../"
    if failure == "unsaved":
        r.part.IsModified = True
    if failure == "missing":
        r.prototype.unlink()
    if failure == "unloaded":
        r.part.ComponentAssembly.RootComponent.children[0].Prototype = None
    if failure == "collision":
        prefix = ""
        prefix = Path(r.part.FullPath).name.split(".")[0]  # explicit loaded collision below
    if failure == "collision":
        from tests.fakes import Part

        Part(r.session, r.e.workspace.root / ("COPY_" + Path(r.part.FullPath).name))
        prefix = "COPY_"
    with pytest.raises(NXToolError):
        r.e._copy_project(str(dest), prefix)
    r.clone.Initialise.assert_not_called()


@pytest.fixture
def assembly_constraints(eng, monkeypatch):
    from tests.fakes import Component, Face

    r = eng

    class Constraint(Object):
        Type = NS(Fix=1, Distance=2, Angle=3, Touch=4, Parallel=5, Perpendicular=6, Concentric=7)
        Alignment = NS(InferAlign=1, CoAlign=2, ContraAlign=3)
        SolverStatus = NS(Solved=1, NotSolved=2)

        def __init__(self):
            super().__init__("constraint")
            self.Suppressed = False
            self.Expression = 5
            self.refs = []
            self.status = 1

        def CreateConstraintReference(self, obj, geom, *_):
            self.refs.append(NS(GetMovableObject=lambda: obj, GetGeometry=lambda: geom))

        def SetExpression(self, value):
            self.Expression = float(value)

        def GetConstraintStatus(self):
            return self.status

        def GetReferences(self):
            return self.refs

    module = NS(Constraint=Constraint)
    monkeypatch.setitem(sys.modules, "NXOpen.Positioning", module)
    r.nx.Positioning = module
    r.nx.Assemblies = NS(Component=Component)
    root = Component("root")
    moving = Component("moving", parent=root)
    target = Component("target", parent=root)
    root.children = [moving, target]
    r.part.ComponentAssembly.RootComponent = root
    faces = []
    for c in [moving, target]:
        f = Face()
        f.IsOccurrence = True
        f.OwningPart = r.part
        f.OwningComponent = c
        faces.append(f)
    pos = NS(
        Constraints=[],
        BeginAssemblyConstraints=Mock(),
        EndAssemblyConstraints=Mock(),
        ClearNetwork=Mock(),
    )

    def create(_):
        c = Constraint()
        pos.Constraints.append(c)
        return c

    pos.CreateConstraint = create
    networks = []

    def network():
        captured = {c.Tag: c.Expression for c in pos.Constraints}
        net = NS(AddConstraint=Mock(), Solve=Mock(), ApplyToModel=Mock(), captured=captured)
        networks.append(net)
        return net

    pos.EstablishNetwork = network
    r.part.ComponentAssembly.Positioner = pos
    r.e._expression_record = lambda e: {"value": e}
    r.pos, r.networks, r.Constraint = pos, networks, Constraint
    r.moving, r.target, r.faces = moving, target, faces
    return r


def test_assembly_distance_edit_builds_network_after_changing_expression(assembly_constraints):
    r = assembly_constraints
    result = r.e._assembly_constraint(
        "distance",
        r.ref(r.moving, "component"),
        r.ref(r.faces[0], "face"),
        r.ref(r.target, "component"),
        r.ref(r.faces[1], "face"),
        5,
        "opposite",
    )
    ref = result["object"]["id"]
    constraint = r.pos.Constraints[0]
    result = r.e._edit_assembly_constraint(ref, value=12, alignment="same")
    assert r.networks[-1].captured[constraint.Tag] == 12, "Network captured the old expression"
    assert result["expression"]["value"] == 12 and result["alignment"] == "CoAlign"
    r.e._edit_assembly_constraint(ref, suppressed=True)
    assert r.e._list_assembly_constraints()["constraints"][0]["suppressed"]
    assert r.pos.ClearNetwork.call_count == 3 and r.pos.EndAssemblyConstraints.call_count == 3


def test_assembly_fix_and_solver_failure_cleanup(assembly_constraints):
    r = assembly_constraints
    result = r.e._assembly_constraint("fix", r.ref(r.moving, "component"))
    constraint = r.pos.Constraints[0]
    constraint.status = 2
    with pytest.raises(NXToolError, match="not solved"):
        r.e._edit_assembly_constraint(result["object"]["id"], suppressed=False)
    assert r.pos.ClearNetwork.call_count == 2 and r.pos.EndAssemblyConstraints.call_count == 2
    with pytest.raises(NXToolError):
        r.e._edit_assembly_constraint(result["object"]["id"], value=12)
    with pytest.raises(NXToolError):
        r.e._edit_assembly_constraint(result["object"]["id"])


@pytest.mark.parametrize(
    "change",
    [
        "same_component",
        "wrong_owner",
        "bad_type",
        "bad_alignment",
        "missing_value",
        "negative",
        "fix_extra",
        "suppressed",
    ],
)
def test_invalid_assembly_constraints_do_not_create_network(assembly_constraints, change):
    r = assembly_constraints
    args = {
        "constraint_type": "distance",
        "component": r.ref(r.moving, "component"),
        "geometry": r.ref(r.faces[0], "face"),
        "target_component": r.ref(r.target, "component"),
        "target_geometry": r.ref(r.faces[1], "face"),
        "value": 5,
    }
    if change == "same_component":
        args["target_component"] = args["component"]
    if change == "wrong_owner":
        args["geometry"] = args["target_geometry"]
    if change == "bad_type":
        args["constraint_type"] = "bogus"
    if change == "bad_alignment":
        args["alignment"] = "bogus"
    if change == "missing_value":
        args["value"] = None
    if change == "negative":
        args["value"] = -5
    if change == "fix_extra":
        args["constraint_type"] = "fix"
    if change == "suppressed":
        r.moving.IsSuppressed = True
    with pytest.raises(NXToolError):
        r.e._assembly_constraint(**args)
    r.pos.BeginAssemblyConstraints.assert_not_called()


@pytest.mark.parametrize("handle", ["capture", "lights"])
def test_render_cleanup_failure_still_restores_style_and_removes_artifact(rendering, handle):
    r = rendering
    getattr(r, handle).Destroy.side_effect = RuntimeError("cleanup failed")
    path = r.e.workspace.root / "cleanup.png"
    with pytest.raises(NXToolError) as exc:
        r.e._render_view(path=str(path), lighting=1)
    assert exc.value.details["mutation_outcome"] == "partial"
    assert r.view.RenderingStyle == "original" and not path.exists()
    r.lights.Destroy.assert_called_once()
