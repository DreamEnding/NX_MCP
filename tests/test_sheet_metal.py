"""Sheet-metal contracts and failure recovery; these are not NX kernel tests."""

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nx_mcp import sheet_metal_server
from nx_mcp.runtime import NXToolError
from nx_mcp.sheet_metal import APPLICATION, CATALOG, field_schema
from tests.fakes import Body, Edge, Face, Feature, Object, Sketch

pytestmark = pytest.mark.fake_nx


@pytest.fixture
def sm(rig, monkeypatch):
    r = rig
    r.session.ApplicationName = APPLICATION
    r.session.ApplicationSwitchImmediate = Mock(
        side_effect=lambda name: setattr(r.session, "ApplicationName", name)
    )
    module = NS(
        ApplicationContext=NS(NxSheetMetal=1),
        SheetmetalBendState=NS(Bent=1, Flat=2),
        Tab=Feature,
        MultiFlange=Feature,
        FlatPattern=Feature,
    )
    r.nx.Features.SheetMetal = module
    monkeypatch.setitem(sys.modules, "NXOpen.Features", r.nx.Features)
    monkeypatch.setitem(sys.modules, "NXOpen.Features.SheetMetal", module)
    r.sm = module
    r.nx.ObjectList = NS(DeleteOption=NS(Delete="delete"))
    r.part.Features.SheetmetalManager = NS()
    r.owned = []

    def own(obj, kind):
        obj.IsOccurrence = False
        if isinstance(obj, Sketch):
            obj.Feature = Feature()
        obj.OwningPart = r.part
        r.owned.append(obj)
        return r.ref(obj, kind)

    r.own = own
    return r


def test_public_signatures_match_executor_and_schema(sm):
    for name, fn in vars(sheet_metal_server).items():
        if name.startswith("nx_") and inspect.isfunction(fn):
            native = sm.e._handlers[name]
            assert set(inspect.signature(fn).parameters) == set(
                inspect.signature(native).parameters
            )
    schemas = sm.e._sheet_metal_schema()
    assert len(schemas["operations"]) == len(CATALOG)
    for operation, spec in CATALOG.items():
        result = sm.e._sheet_metal_schema(operation)
        schema = result["parameters_schema"]
        assert set(schema["required"]) <= set(schema["properties"])
        assert not schema["additionalProperties"]
        assert result["validation_status"] == spec["native_status"]
    with pytest.raises(NXToolError, match="Unknown"):
        sm.e._sheet_metal_schema("__dict__")


def test_context_is_explicit_and_rejects_active_sketch(sm):
    sm.session.ApplicationName = "UG_APP_MODELING"
    with pytest.raises(NXToolError, match="context"):
        sm.e._sm_require_context()
    sm.session.ApplicationSwitchImmediate.assert_not_called()
    result = sm.e.execute("nx_sheet_metal_context", {})
    assert result["application"] == APPLICATION
    assert not sm.session.marks
    sm.session.ActiveSketch = object()
    with pytest.raises(NXToolError, match="Finish"):
        sm.e._sm_prepare()
    with pytest.raises(NXToolError, match="Finish"):
        sm.e._sm_require_context()
    sm.session.ActiveSketch = None
    sm.session.Parts.Display = None
    with pytest.raises(NXToolError, match="work and display"):
        sm.e._sm_prepare()


def test_context_failure_and_batch(sm):
    sm.session.ApplicationName = "UG_APP_MODELING"
    sm.session.ApplicationSwitchImmediate.side_effect = None
    with pytest.raises(NXToolError, match="did not enter"):
        sm.e._sm_prepare()
    sm.session.IsBatch = True
    assert sm.e._sheet_metal_context()["application"] == "batch"
    sm.e._sm_require_context()


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"section": "missing", "thickness": 2, "ignored": 1},
        {"section": "missing", "thickness": float("nan")},
        {"thickness": -1},
        {"thickness": 0},
        {"thickness": True},
    ],
)
def test_bad_inputs_do_not_construct_native_builder(sm, params):
    factory = Mock()
    sm.part.Features.SheetmetalManager.CreateTabFeatureBuilder = factory
    with pytest.raises(NXToolError):
        sm.e.execute("nx_sheet_metal_feature", {"operation": "tab", "parameters": params})
    factory.assert_not_called()
    assert not list(sm.part.Bodies)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("integer", True),
        ("boolean", 1),
        ("enum", "ValueOf"),
        ("string", "bad\nname"),
        ("point3d", [1, 2]),
        ("direction", [0, 0, 0]),
        ("flange_list", []),
        ("face_pairs", [["same", "same"]]),
        ("collector", []),
        ("csys", {"origin": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [1, 0, 0]}),
        ("plane", {"origin": [0, 0, 0]}),
    ],
)
def test_strict_nested_validation(sm, kind, value):
    field = {"kind": kind, "values": ["Valid"], "fields": {}, "objects": "edge"}
    with pytest.raises(NXToolError):
        sm.e._sm_validate({"input": field}, {"input": value})


def test_owned_typed_references_and_normalized_basis(sm):
    face = Face()
    ref = sm.own(face, "face")
    assert sm.e._sm_reference(ref, "face_or_edge") is face
    face.IsOccurrence = True
    with pytest.raises(NXToolError, match="work part"):
        sm.e._sm_reference(ref, "face")
    with pytest.raises(NXToolError, match="typed"):
        sm.e._sm_reference(12, "face")
    edge = sm.own(Edge(), "edge")
    with pytest.raises(NXToolError, match="unique"):
        sm.e._sm_validate(
            {"edges": {"kind": "collector", "objects": "edge"}}, {"edges": [edge, edge]}
        )
    sk = Sketch(sm.session)
    sid = sm.own(sk, "sketch")
    sm.session.ActiveSketch = sk
    with pytest.raises(NXToolError, match="Finish"):
        sm.e._sm_reference(sid, "sketch")
    values = sm.e._sm_validate(
        {"plane": {"kind": "plane"}}, {"plane": {"origin": [1, 2, 3], "normal": [0, 0, 2]}}
    )
    assert values["plane"]["normal"] == [0, 0, 1]


def test_face_pair_edit_replaces_old_pairs(sm):
    old = [(Face(), Face()), (Face(), Face())]
    pairs = list(old)
    builder = NS(
        GetNumberOfFacePairs=lambda: len(pairs),
        GetFacePair=lambda i: pairs[i],
        RemoveFacePair=lambda a, b: pairs.remove((a, b)),
        AddFacePair=lambda a, b: pairs.append((a, b)),
    )
    new = (Face(), Face())
    sm.e._sm_apply(builder, {"pairs": {"kind": "face_pairs"}}, {"pairs": [new]})
    assert pairs == [new]


def test_flange_list_uses_dedicated_factory_and_owns_failed_entry(sm):
    entries = []
    sequence = NS(Clear=Mock(side_effect=lambda _: entries.clear()), Append=entries.append)
    item = NS(Length=NS(RightHandSide="1"))
    factory = Mock(return_value=item)
    b = NS(
        FlangePropertiesList=NS(
            FeatureBendPropertiesList=sequence, CreateFlangeBendProperties=factory
        )
    )
    fields = {
        "flanges": {
            "kind": "flange_list",
            "fields": {"length": {"kind": "expression", "path": "Length"}},
        }
    }
    sm.e._sm_apply(b, fields, {"flanges": [{"length": 20}]})
    assert entries == [item] and item.Length.RightHandSide == "20"
    sequence.Clear.assert_called_once_with("delete")
    fields["flanges"]["fields"]["bad"] = {"kind": "expression", "path": "Missing"}
    with pytest.raises(AttributeError):
        sm.e._sm_apply(b, fields, {"flanges": [{"bad": 1}]})
    assert entries == [item]  # Destroy of the parent builder can now clean it up.


def make_tab_builder(sm, fail=False):
    b = NS(
        Thickness=NS(RightHandSide="1"),
        Section=None,
        SetApplicationContext=Mock(),
        Validate=Mock(return_value=True),
        Destroy=Mock(),
    )

    def commit():
        body = Body()
        sm.part.Bodies.append(body)
        if fail:
            raise RuntimeError("native partial failure")
        f = Feature(bodies=[body])
        f.FeatureType = "Base Tab"
        f.expressions = []
        sm.part.Features.append(f)
        return f

    b.CommitFeature = Mock(side_effect=commit)
    sm.part.Features.SheetmetalManager.CreateTabFeatureBuilder = Mock(return_value=b)
    sm.e._engineering_section = Mock(return_value=object())
    sm.e._update_model = Mock()
    return b


def test_native_failure_rolls_back_partial_body_and_disposes_builder(sm):
    b = make_tab_builder(sm, fail=True)
    sid = sm.own(Sketch(sm.session), "sketch")
    with pytest.raises(NXToolError, match="native partial failure"):
        sm.e.execute(
            "nx_sheet_metal_feature",
            {
                "operation": "tab",
                "parameters": {"section": sid, "thickness": 2},
                "operation_id": "sm-failure",
            },
        )
    assert len(sm.part.Bodies) == 0
    b.Destroy.assert_called_once()
    receipt = sm.e.store.get("sm-failure")
    assert receipt["mutation_outcome"] == "rolled_back"


def test_native_creation_and_idempotent_retry(sm):
    b = make_tab_builder(sm)
    sid = sm.own(Sketch(sm.session), "sketch")
    params = {
        "operation": "tab",
        "parameters": {"section": sid, "thickness": 2},
        "operation_id": "sm-once-01",
    }
    result = sm.e.execute("nx_sheet_metal_feature", params)
    assert result["body_count"] == 1 and result["native_feature_type"] == "Base Tab"
    assert result["requested_parameters"]["thickness"] == 2
    assert sm.e.execute("nx_sheet_metal_feature", params)["replayed"]
    b.CommitFeature.assert_called_once()


@pytest.mark.parametrize("failure", ["commit", "destroy", "invalid", "empty"])
def test_export_failure_removes_staging_and_preserves_existing(sm, tmp_path, failure):
    obj = Feature()
    obj.FeatureType = "FLAT_PATTERN"
    feature = sm.own(obj, "feature")
    b = NS(FlatPattern=NS(Value=None))

    def commit():
        Path(b.OutputFile).write_bytes(
            b"" if failure == "empty" else b"invalid" if failure == "invalid" else b"0\nSECTION\n"
        )
        if failure == "commit":
            raise RuntimeError("export failed")

    def destroy():
        if failure == "destroy":
            raise RuntimeError("destroy failed")

    b.Commit, b.Destroy = commit, Mock(side_effect=destroy)
    sm.part.Features.SheetmetalManager.CreateExportFlatPatternBuilder = lambda: b
    sm.sm.ExportFlatPatternBuilder = NS(
        DxfRevisionType=NS(R2018=2018),
        FileType=NS(Dxf=1, TrumpfGeo=2),
        ExportLocationOptions=NS(Native=1),
    )
    with pytest.raises((NXToolError, RuntimeError)):
        sm.e._export_flat_pattern(feature, "new.dxf")
    assert not (tmp_path / "new.dxf").exists()
    assert not list(tmp_path.glob(".nx-export-*"))
    (tmp_path / "existing.dxf").write_text("preserve")
    with pytest.raises(NXToolError):
        sm.e._export_flat_pattern(feature, "existing.dxf")
    assert (tmp_path / "existing.dxf").read_text() == "preserve"


def test_schema_primitives_and_required_lists():
    for kind in [
        "expression",
        "number",
        "boolean",
        "integer",
        "string",
        "point",
        "point3d",
        "direction",
        "plane",
        "csys",
        "face_pairs",
        "collector",
        "reference_list",
        "select_faces",
        "select_edges",
        "select_bodies",
        "face",
        "section",
    ]:
        schema = field_schema({"kind": kind})
        assert "type" in schema or "oneOf" in schema
    assert field_schema(CATALOG["flange"]["fields"]["flanges"])["items"]["required"] == [
        "edges",
        "length",
        "angle",
    ]
    assert field_schema(CATALOG["joggle"]["fields"]["inputs"])["items"]["required"] == [
        "faces",
        "depth",
    ]


def test_standard_validation_rejects_before_commit(sm):
    b = make_tab_builder(sm)
    b.Validate.return_value = False
    sid = sm.own(Sketch(sm.session), "sketch")
    with pytest.raises(NXToolError, match="validation failed"):
        sm.e.execute(
            "nx_sheet_metal_feature",
            {"operation": "tab", "parameters": {"section": sid, "thickness": 2}},
        )
    b.CommitFeature.assert_not_called()
    b.Destroy.assert_called_once()


def test_legacy_validator_is_not_used_for_valid_tab(sm):
    b = make_tab_builder(sm)
    b.ValidateBuilderData = Mock(side_effect=RuntimeError("Unreliable NX 2606 method"))
    sid = sm.own(Sketch(sm.session), "sketch")
    result = sm.e.execute(
        "nx_sheet_metal_feature",
        {"operation": "tab", "parameters": {"section": sid, "thickness": 2}},
    )
    assert result["body_count"] == 1
    b.ValidateBuilderData.assert_not_called()
    assert not hasattr(b, "Sketch")  # External sketch sections are not consumed internally.


def test_edit_preserves_application_context(sm):
    b = make_tab_builder(sm)
    b.GetApplicationContext = Mock(return_value=1)
    f = Feature()
    f.FeatureType = "Base Tab"
    fid = sm.own(f, "feature")
    sm.e.execute(
        "nx_sheet_metal_feature",
        {"operation": "tab", "feature": fid, "parameters": {"thickness": 3}},
    )
    b.SetApplicationContext.assert_not_called()
    b.GetApplicationContext.assert_called_once()


@pytest.fixture
def preferences(sm, monkeypatch):
    r = sm
    modes = NS(Value=0, MaterialTable=1, ToolIdTable=2)
    methods = NS(
        NeutralFactorValue=0,
        BendTable=1,
        BendAllowanceFormula=2,
        MaterialTable=3,
        ToolTable=4,
        BendAllowanceTable=5,
        BendDeductionTable=6,
        BendDeductionFormula=7,
        Din6935Formula=8,
    )
    pref = NS(
        SheetMetalPreferencesBuilder=NS(
            ParameterEntryTypes=modes, BendDefinitionMethodOptions=methods
        )
    )
    r.nx.Preferences = pref
    monkeypatch.setitem(sys.modules, "NXOpen.Preferences", pref)
    values = {
        key: NS(RightHandSide="2")
        for key in [
            "MaterialThickness",
            "BendRadius",
            "NeutralFactor",
            "BendReliefWidth",
            "BendReliefDepth",
        ]
    }
    values["NeutralFactor"].RightHandSide = ".33"
    b = NS(
        **values,
        ParameterEntryType=0,
        BendAllowanceFormula="",
        BendDeductionFormula="",
        Commit=Mock(),
        Destroy=Mock(),
    )
    state = {"material": "", "tool": "", "method": 0, "table": ""}
    b.SetMaterial = lambda x: state.update(material=x)
    b.SetToolName = lambda x: state.update(tool=x)
    b.SetBendDefinitionMethod = lambda x: state.update(method=x)
    b.SetBendTable = lambda x: state.update(table=x)
    manager = NS(
        **{"Get" + key: (lambda value=exp: value) for key, exp in values.items()},
        GetParameterEntryType=lambda: b.ParameterEntryType,
        GetBendDefinitionMethod=lambda: state["method"],
        GetMaterialName=lambda: state["material"],
        GetToolName=lambda: state["tool"],
        GetBendTable=lambda: state["table"],
        GetBendAllowanceFormula=lambda: b.BendAllowanceFormula,
        GetBendDeductionFormula=lambda: b.BendDeductionFormula,
        GetMaterialNames=Mock(side_effect=RuntimeError("unsafe native catalog")),
        CreateSheetMetalPreferencesBuilder=Mock(return_value=b),
    )
    r.part.Preferences = NS(SheetMetalPreferences=manager)
    r.e._expression_record = lambda exp: {
        "value": float(exp.RightHandSide),
        "formula": exp.RightHandSide,
    }
    r.e._update_model = Mock()
    r.pref_builder, r.pref_manager = b, manager
    return r


def test_defaults_are_read_back_without_unsafe_catalog_enumeration(preferences):
    r = preferences
    result = r.e._set_sheet_metal_defaults(
        thickness=1.5,
        bend_radius=3,
        neutral_factor=0.4,
        parameter_entry="Value",
        bend_definition="NeutralFactorValue",
    )
    assert result["parameters"]["thickness"]["value"] == 1.5
    assert result["parameters"]["bend_radius"]["value"] == 3
    assert result["parameters"]["neutral_factor"]["value"] == 0.4
    assert result["parameter_entry"] == "Value"
    assert result["material_catalog_status"] == "unavailable"
    r.pref_manager.GetMaterialNames.assert_not_called()
    r.pref_builder.Destroy.assert_called_once()


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"thickness": 0},
        {"neutral_factor": 2},
        {"bend_radius": -1},
        {"parameter_entry": "invalid"},
        {"bend_definition": "ValueOf"},
        {"material": ""},
        {"tool": "bad\nname"},
        {"bend_allowance_formula": ""},
        {"bend_deduction_formula": "x\ny"},
        {"bend_table": "missing.tbl"},
    ],
)
def test_default_preflight_constructs_no_builder(preferences, params):
    r = preferences
    with pytest.raises(NXToolError):
        r.e._set_sheet_metal_defaults(**params)
    r.pref_manager.CreateSheetMetalPreferencesBuilder.assert_not_called()


def test_silently_ignored_default_is_a_verification_failure(preferences):
    r = preferences
    r.pref_builder.Commit.side_effect = lambda: setattr(
        r.pref_builder.MaterialThickness, "RightHandSide", "2"
    )
    with pytest.raises(NXToolError, match="did not retain"):
        r.e._set_sheet_metal_defaults(thickness=4)
    r.pref_builder.Destroy.assert_called_once()


def test_material_and_bend_table_settings_are_verified(preferences, tmp_path):
    r = preferences
    table = tmp_path / "bend.tbl"
    table.write_text("fixture")
    result = r.e._set_sheet_metal_defaults(
        parameter_entry="MaterialTable",
        material="Aluminum",
        tool="ToolA",
        bend_definition="BendTable",
        bend_table=str(table),
        bend_allowance_formula="1",
        bend_deduction_formula="2",
    )
    assert result["material"] == "Aluminum" and result["tool"] == "ToolA"
    assert result["bend_table"] == str(table)
    r.pref_builder.SetMaterial = lambda _: None
    with pytest.raises(NXToolError, match="did not retain requested material"):
        r.e._set_sheet_metal_defaults(material="MissingMaterial")


def test_assignable_collector_is_created_before_binding_rules(sm):
    collected = NS(ReplaceRules=Mock())
    sm.part.ScCollectors = NS(CreateCollector=Mock(return_value=collected))
    sm.part.ScRuleFactory = NS(CreateRuleFaceDumb=Mock(return_value="rule"))
    builder = NS(FaceCollector=None)
    faces = [Face()]
    sm.e._sm_apply(
        builder,
        {
            "faces": {
                "kind": "collector",
                "path": "FaceCollector",
                "objects": "face",
                "assign": True,
            }
        },
        {"faces": faces},
    )
    assert builder.FaceCollector is collected
    collected.ReplaceRules.assert_called_once_with(["rule"], False)
    sm.part.ScRuleFactory.CreateRuleFaceDumb.assert_called_once_with(faces)


def test_legacy_sheet_metal_builder_without_context_methods(sm):
    b = make_tab_builder(sm)
    del b.SetApplicationContext
    sid = sm.own(Sketch(sm.session), "sketch")
    result = sm.e.execute(
        "nx_sheet_metal_feature",
        {"operation": "tab", "parameters": {"section": sid, "thickness": 2}},
    )
    assert result["body_count"] == 1


def test_assignable_section_is_not_read_before_initialization(sm, monkeypatch):
    class NativeBuilder:
        @property
        def Section(self):
            raise RuntimeError("Native getter fails before initialization")

        @Section.setter
        def Section(self, section):
            self.assigned = section

    builder = NativeBuilder()
    sketch = Sketch(sm.session)
    section = object()
    monkeypatch.setattr(sm.e, "_engineering_section", lambda value: section)
    sm.e._sm_apply(
        builder,
        {"section": {"kind": "section", "path": "Section", "assign": True}},
        {"section": sketch},
    )
    assert builder.assigned is section


def test_nested_catalog_required_fields_exist():
    def check(spec):
        assert set(spec.get("required", [])) <= set(spec.get("fields", {}))
        for field in spec.get("fields", {}).values():
            check(field)

    for spec in CATALOG.values():
        check(spec)


@pytest.fixture
def annotation_rig(sm, monkeypatch):
    r = sm
    module = NS(SheetMetalPMIBuilder=NS(Types=NS(Body="body", Bend="bend")))
    r.nx.Annotations = module
    monkeypatch.setitem(sys.modules, "NXOpen.Annotations", module)
    body = Body()
    r.body_id = r.own(body, "body")
    r.face = Face()
    r.face.GetBody = lambda: body
    r.face_id = r.own(r.face, "face")
    r.note = Object("PMI")
    r.note.OwningPart = r.part
    r.note.GetText = lambda: list(r.lines)
    r.lines = []
    r.builder = NS(
        SelectedBody=NS(Value=None),
        SelectedFace=NS(Clear=Mock(), Add=Mock()),
        AssociatedObjects=NS(Nxobjects=NS(Clear=Mock(), Add=Mock())),
        Text=NS(TextBlock=NS(SetText=lambda lines: setattr(r, "lines", list(lines)))),
        Origin=NS(SetInferRelativeToGeometry=Mock(), Origin=NS(SetValue=Mock())),
        Validate=Mock(return_value=True),
        Commit=Mock(side_effect=lambda: r.part.Notes.append(r.note) or r.note),
        GetCommittedObjects=Mock(return_value=[r.note]),
        Destroy=Mock(),
    )
    manager = r.part.Features.SheetmetalManager
    manager.CreateSheetMetalPmiBuilder = Mock(return_value=r.builder)
    manager.IsSheetmetalBody = lambda b: b is body
    manager.GetBodyThickness = lambda b: 2.0
    manager.GetBendParameters = lambda f: NS(InnerRadius=3.0, BendAngle=90.0, NeutralFactor=0.33)
    return r


def test_annotation_has_measured_text_and_geometry_association(annotation_rig):
    r = annotation_rig
    result = r.e.execute(
        "nx_sheet_metal_annotation",
        {
            "kind": "bend",
            "body": r.body_id,
            "faces": [r.face_id],
            "position": [10, 20, 30],
        },
    )
    assert "90.000 deg" in result["text"][0][1]
    assert result["measured_parameters"]["bends"][0]["inner_radius"] == 3
    assert "snapshot" in result["text_semantics"].lower()
    r.builder.AssociatedObjects.Nxobjects.Add.assert_called_once_with([r.face])
    assert result["annotation_count"] == 1
    r.builder.Destroy.assert_called_once()


def test_partial_annotation_commit_rolls_back_and_destroys_builder(annotation_rig):
    r = annotation_rig

    def fail():
        r.part.Notes.append(r.note)
        raise RuntimeError("native annotation failure")

    r.builder.Commit.side_effect = fail
    with pytest.raises(NXToolError) as error:
        r.e.execute(
            "nx_sheet_metal_annotation",
            {
                "kind": "body",
                "body": r.body_id,
                "position": [0, 0, 0],
            },
        )
    assert error.value.details["mutation_outcome"] == "rolled_back"
    assert not r.part.Notes
    r.builder.Destroy.assert_called_once()


def test_explicit_edge_section_validates_ownership_before_builder(sm):
    edge = Edge()
    edge_id = sm.own(edge, "edge")
    values = sm.e._sm_validate(
        {"section": {"kind": "section"}},
        {
            "section": {"edges": [edge_id], "help_point": [1, 2, 3]},
        },
    )
    assert values["section"]["edges"] == [edge]
    for invalid in [
        {"edges": [edge_id]},
        {"edges": [edge_id], "curves": [edge_id], "help_point": [0, 0, 0]},
        {"curves": [edge_id], "help_point": [0, 0, 0]},
    ]:
        with pytest.raises(NXToolError):
            sm.e._sm_validate({"section": {"kind": "section"}}, {"section": invalid})


def test_path_sketch_uses_arc_length_and_returns_actual_frame(sm, monkeypatch):
    g = NS(OnPathDimensionBuilder=NS(UpdateReason=NS(Path="path")))
    sm.nx.GeometricUtilities = g
    monkeypatch.setitem(sys.modules, "NXOpen.GeometricUtilities", g)
    sm.nx.SketchAlongPathBuilder = NS(
        PlaneOrientationType=NS(NormalToPath="normal"),
        SketchOrientationType=NS(Automatic="auto", RelativeToFace="face"),
    )
    edge = sm.own(Edge(), "edge")
    sk = Sketch(sm.session)
    sk.OwningPart = sm.part
    sk.Activate = Mock()
    builder = NS(
        Section=object(),
        PlaneLocation=NS(Expression=NS(RightHandSide="0"), Update=Mock()),
        Validate=Mock(return_value=True),
        Commit=Mock(return_value=sk),
        Destroy=Mock(),
    )
    sm.part.Sketches.CreateSketchAlongPathBuilder = Mock(return_value=builder)
    monkeypatch.setattr(sm.e, "_sm_section", Mock())
    result = sm.e.execute(
        "nx_create_path_sketch", {"edges": [edge], "help_point": [0, 0, 0], "percent": 25}
    )
    assert builder.PlaneLocation.IsParameterUsed is False
    assert builder.PlaneLocation.IsPercentUsed is True
    assert float(builder.PlaneLocation.Expression.RightHandSide) == 25
    assert result["frame"]["origin"] == [0, 0, 0]
    assert result["position_convention"] == "arc_length_percent"
    builder.Destroy.assert_called_once()
    for invalid in [-1, 101, True, float("nan")]:
        with pytest.raises(NXToolError):
            sm.e._create_path_sketch([edge], [0, 0, 0], percent=invalid)
    assert sm.part.Sketches.CreateSketchAlongPathBuilder.call_count == 1


def test_secondary_tab_rejects_inconsistent_thickness_before_builder(sm):
    b = make_tab_builder(sm)
    body = Body()
    body_id = sm.own(body, "body")
    sketch_id = sm.own(Sketch(sm.session), "sketch")
    sm.part.Features.SheetmetalManager.GetBodyThickness = lambda target: 2
    with pytest.raises(NXToolError, match="must match"):
        sm.e.execute(
            "nx_sheet_metal_feature",
            {
                "operation": "tab",
                "parameters": {
                    "section": sketch_id,
                    "target_body": body_id,
                    "is_secondary": True,
                    "thickness": 3,
                },
            },
        )
    b.CommitFeature.assert_not_called()
    sm.part.Features.SheetmetalManager.CreateTabFeatureBuilder.assert_not_called()


@pytest.mark.parametrize("operation", ["flange", "advanced_flange", "unbend", "rebend"])
def test_guided_examples_match_creation_contract_and_native_evidence(sm, operation):
    from jsonschema import Draft202012Validator

    result = sm.e._sheet_metal_schema(operation)
    Draft202012Validator(result["parameters_schema"]).validate(result["example_parameters"])
    source = result["example_evidence"]["source"]
    path, _, pointer = source.partition("#")
    evidence_path = Path(__file__).parents[1] / path
    assert evidence_path.is_file()
    if pointer:
        evidence = json.loads(evidence_path.read_text())
        for key in pointer.strip("/").split("/"):
            evidence = evidence[key]
        assert evidence["source_fixture"]
        if "parameters" in evidence:
            assert result["example_parameters"] == evidence["parameters"]
    assert len(result["prerequisites"]) > 3


def test_unbend_guidance_distinguishes_stationary_web_from_bend_strip(sm):
    result = sm.e._sheet_metal_schema("unbend")
    for key in ["parameters_schema", "edit_parameters_schema"]:
        fields = result[key]["properties"]
        assert "items[].bends[].face.id" in fields["face_collector"]["description"]
        assert "not the flattened bend strip" in fields["reference_entity"]["description"]
    assert (
        "Edge-based stationary references were not exercised" in result["example_evidence"]["scope"]
    )


def test_advanced_flange_does_not_claim_reference_modes_are_verified(sm):
    result = sm.e._sheet_metal_schema("advanced_flange")
    assert "unverified" in result["parameters_schema"]["properties"]["type"]["description"]
    assert result["parameters_schema"]["required"] == ["edges"]
    assert "type" not in result["example_parameters"]
