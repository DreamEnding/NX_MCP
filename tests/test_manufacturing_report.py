"""Manufacturing report regressions: contracts, transport recovery and analytic DXF."""

import pytest
from pydantic import TypeAdapter, ValidationError

from nx_mcp.integration_server import BatchOperation
from nx_mcp.planar_dxf import dxf_text
from nx_mcp.result_transport import bound_result, encoded, read_result
from nx_mcp.runtime import NXToolError


def test_batch_discriminator_matches_native_sketch_contract():
    adapter = TypeAdapter(BatchOperation)
    rectangle = {
        "method": "nx_sketch_rectangle",
        "params": {
            "sketch_id": "sketch",
            "corner1": {"x": 0, "y": 0},
            "corner2": {"x": 80, "y": 30},
        },
    }
    assert adapter.validate_python(rectangle).params.corner2.x == 80
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"method": "nx_sketch_rectangle", "params": {"x1": 0, "y1": 0, "x2": 1, "y2": 1}}
        )
    with pytest.raises(ValidationError):
        adapter.validate_python({"method": "nx_save_part", "params": {}})
    assert (
        adapter.validate_python(
            {
                "method": "nx_sketch_arc",
                "params": {"cx": 0, "cy": 0, "radius": 2, "start_angle": 0, "end_angle": 360},
            }
        ).params.radius
        == 2
    )


def test_result_storage_failure_preserves_commit_and_restore(tmp_path, monkeypatch):
    from pathlib import Path

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(NXToolError) as error:
        bound_result(
            {
                "operation_id": "op",
                "mutation_outcome": "committed",
                "restore_id": "restore",
                "objects": ["x" * 4096] * 300,
            },
            tmp_path,
        )
    assert error.value.code == "NX_RESULT_STORAGE_FAILED"
    assert error.value.details["mutation_outcome"] == "committed"
    assert error.value.details["restore_id"] == "restore"


def test_nested_oversize_pages_remain_bounded_and_addressable(tmp_path):
    value = {str(i): ["x" * 2048] * 150 for i in range(120)}
    full = {
        "status": "success",
        "operation_id": "op",
        "mutation_outcome": "committed",
        "objects": value,
    }
    receipt = bound_result(full, tmp_path)
    key = receipt["full_result"]["id"]
    page = read_result(tmp_path, key, "/objects", offset=100, limit=20)
    assert list(page["value"]) == [str(i) for i in range(100, 120)]
    assert page["total_count"] == 120 and page["next_offset"] is None
    assert len(encoded(page)) < 512 * 1024
    child = read_result(tmp_path, key, "/objects/101", offset=140, limit=10)
    assert len(child["value"]) == 10 and child["value"][0] == "x" * 2048


def test_dxf_retains_analytic_entities_and_units():
    text = dxf_text(
        [
            {"type": "LINE", "layer": "OUTLINE", "start": [0.0, 0.0], "end": [80.0, 0.0]},
            {
                "type": "ARC",
                "layer": "OUTLINE",
                "center": [80.0, 2.0],
                "radius": 2.0,
                "start_angle": 270.0,
                "end_angle": 0.0,
            },
            {"type": "CIRCLE", "layer": "HOLES", "center": [10.0, 10.0], "radius": 2.0},
        ]
    )
    tokens = text.splitlines()
    pairs = list(zip(tokens[::2], tokens[1::2], strict=True))
    assert ("9", "$INSUNITS") in pairs
    assert pairs[pairs.index(("9", "$INSUNITS")) + 1] == ("70", "4")
    assert [v for k, v in pairs if k == "0" and v in {"LINE", "ARC", "CIRCLE"}] == [
        "LINE",
        "ARC",
        "CIRCLE",
    ]
    assert ("50", "270") in pairs and ("51", "0") in pairs and ("2", "HOLES") in pairs


def test_new_part_failure_closes_created_parts_and_restores_selection():
    from types import SimpleNamespace as NS

    from nx_mcp.hardened import HardenedExecutor

    class Parts(list):
        Work = None
        Display = None

    parts = Parts()
    e = HardenedExecutor.__new__(HardenedExecutor)
    e.session = NS(Parts=parts)
    e._reference = lambda p, *_: {"id": str(p.Tag)}
    e._close_part = lambda part, save: parts.remove(next(p for p in parts if str(p.Tag) == part))

    def fail(*_):
        parts.append(NS(Tag=10))
        raise NXToolError("NX_IMPORT_NO_OUTPUT", "no geometry")

    e._import_geometry_inner = fail
    with pytest.raises(NXToolError) as error:
        e._import_geometry("vendor.step", target="new_part", output_path="vendor.prt")
    assert error.value.details["mutation_outcome"] == "rolled_back"
    assert not parts

    def precondition(*_):
        raise NXToolError("NX_FILE_NOT_FOUND", "missing")

    e._import_geometry_inner = precondition
    with pytest.raises(NXToolError) as error:
        e._import_geometry("missing.step", target="new_part", output_path="vendor.prt")
    assert error.value.details["mutation_outcome"] == "not_started"


@pytest.fixture
def dimension_fixture(monkeypatch):
    import sys
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from nx_mcp.drawing_preferences import DrawingPreferencesMixin

    annotation = NS(
        ToleranceType=NS(
            NotSet=0, BilateralTwoLines=1, BilateralOneLine=2, LimitTwoLines=3, Basic=4, Reference=5
        ),
        DimensionUnit=NS(Millimeters=0, Inches=1, Meters=2, Micrometers=3),
        DecimalPointCharacter=NS(Period=0, Comma=1),
    )
    monkeypatch.setitem(sys.modules, "NXOpen", NS(Annotations=annotation))
    monkeypatch.setitem(sys.modules, "NXOpen.Annotations", annotation)
    formatting = NS(
        DisplayTrailingZeros=False, PrimaryDimensionUnit=0, DecimalPointCharacter=0, Dispose=Mock()
    )
    prefs = NS(
        GetUnitsFormatPreferences=lambda: formatting,
        SetUnitsFormatPreferences=Mock(),
        Dispose=Mock(),
    )
    dim = NS(
        GetDimensionPreferences=lambda: prefs,
        SetDimensionPreferences=Mock(),
        ComputedSize=318.61,
        IsRetained=False,
        NumberOfAssociativities=2,
        NominalDecimalPlaces=1,
        ToleranceDecimalPlaces=1,
        UpperToleranceValue=0,
        LowerToleranceValue=0,
        UpperMetricToleranceValue=0,
        LowerMetricToleranceValue=0,
        MetricNominalDecimalPlaces=1,
        MetricToleranceDecimalPlaces=1,
        ToleranceType=0,
        RedisplayObject=Mock(),
    )
    e = DrawingPreferencesMixin()
    e._engineering_owned = lambda *args: dim
    e._reference = lambda *args: {"id": "dimension"}
    e._work_part = lambda: None
    e._units = lambda: "mm"
    return e, dim, formatting


def test_dimension_preferences_preserve_value_and_associations(dimension_fixture):
    e, dim, formatting = dimension_fixture
    result = e._edit_dimension_format(
        "dimension",
        decimal_places=2,
        trailing_zeros=True,
        units="mm",
        decimal_separator="comma",
        tolerance_type="bilateral",
        upper_tolerance=0.05,
        lower_tolerance=-0.02,
        tolerance_decimal_places=2,
    )
    assert result["computed_value"] == 318.61 and result["association_count"] == 2
    assert result["decimal_places"] == 2 and result["decimal_separator"] == "Comma"
    assert result["upper_tolerance"] == 0.05 and result["lower_tolerance"] == -0.02
    assert result["tolerance_type"] == "BilateralTwoLines"
    assert dim.NumberOfAssociativities == 2 and formatting.DisplayTrailingZeros


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"decimal_places": 9},
        {"units": "feet"},
        {"decimal_separator": "colon"},
        {"tolerance_type": "invented"},
    ],
)
def test_dimension_format_preflight_rejects_unsupported_changes(dimension_fixture, params):
    e, dim, _ = dimension_fixture
    with pytest.raises(NXToolError):
        e._edit_dimension_format("dimension", **params)
    dim.SetDimensionPreferences.assert_not_called()


def test_dimension_format_rejects_changed_measurement(dimension_fixture):
    e, dim, _ = dimension_fixture
    dim.SetDimensionPreferences.side_effect = lambda *_: setattr(dim, "ComputedSize", 0)
    with pytest.raises(NXToolError, match="measured value"):
        e._edit_dimension_format("dimension", decimal_places=2)


@pytest.fixture
def planar_fixture(tmp_path, monkeypatch):
    import math
    import sys
    from types import SimpleNamespace as NS

    from nx_mcp.planar_dxf import PlanarDxfMixin
    from nx_mcp.workspace import Workspace

    class Sketch:
        pass

    curves = [
        NS(Tag=1, kind="line", limits=[0, 1]),
        NS(Tag=2, kind="circle", limits=[0, 2 * math.pi]),
        NS(Tag=3, kind="arc", limits=[0, math.pi / 2]),
    ]
    arc = NS(Center=[1, 1, 0], Radius=0.25, XAxis=[1, 0, 0], YAxis=[0, 1, 0])

    def evaluate(curve, t):
        return (
            [
                [2 * t, 0, 0]
                if curve.kind == "line"
                else [1 + 0.25 * math.cos(t), 1 + 0.25 * math.sin(t), 0]
            ],
        )

    # UF returns a coordinate vector as the first tuple element.
    def point(curve, t):
        return evaluate(curve, t)[0]

    evaluator = NS(
        Initialize2=lambda tag: curves[tag - 1],
        AskLimits=lambda c: c.limits,
        EvaluateUnitVectors=lambda c, t: point(c, t),
        IsLine=lambda c: c.kind == "line",
        IsArc=lambda c: c.kind in {"arc", "circle"},
        AskArc=lambda _: arc,
    )
    uf = NS(Eval=evaluator)
    nx = NS(Sketch=Sketch)
    monkeypatch.setitem(sys.modules, "NXOpen", nx)
    monkeypatch.setitem(sys.modules, "NXOpen.UF", NS(UFSession=NS(GetUFSession=lambda: uf)))
    nx.UF = sys.modules["NXOpen.UF"]
    part = NS()
    sketch = Sketch()
    sketch.IsOccurrence = False
    sketch.OwningPart = part
    sketch.GetAllGeometry = lambda: curves
    e = PlanarDxfMixin()
    e.nxopen = nx
    e.workspace = Workspace(tmp_path)
    e._resolve = lambda *_: sketch
    e._work_part = lambda: part
    e._units = lambda: "inch"
    e._sketch_frame = lambda _: {
        "origin": [0, 0, 0],
        "x_axis": [1, 0, 0],
        "y_axis": [0, 1, 0],
        "normal": [0, 0, 1],
    }
    e._reference = lambda c, *_: {"id": str(c.Tag)}
    return e, sketch, curves


def test_planar_export_converts_inches_and_preserves_arc_layers(planar_fixture, tmp_path):
    e, _, _ = planar_fixture
    r = e._export_planar_dxf("sketch", str(tmp_path / "outline.dxf"), layers={"2": "HOLES"})
    assert r["units"] == "mm" and r["entity_counts"] == {"LINE": 1, "CIRCLE": 1, "ARC": 1}
    line, circle, arc = r["entities"]
    assert line["end"] == [50.8, 0] and circle["radius"] == 6.35
    assert circle["layer"] == "HOLES" and arc["start_angle"] == 0 and arc["end_angle"] == 90
    assert (tmp_path / "outline.dxf").stat().st_size == r["size"]
    with pytest.raises(NXToolError, match="existing"):
        e._export_planar_dxf("sketch", str(tmp_path / "outline.dxf"))


@pytest.mark.parametrize(
    "params",
    [
        {"origin": [0, 0, 1]},
        {"x_axis": [1, 0, 0]},
        {"x_axis": [1, 0, 0], "y_axis": [1, 0, 0]},
        {"layers": {"missing": "HOLES"}},
        {"layer": "invalid/name"},
    ],
)
def test_planar_export_preflights_before_writing(planar_fixture, tmp_path, params):
    e, _, _ = planar_fixture
    with pytest.raises(NXToolError):
        e._export_planar_dxf("sketch", str(tmp_path / "rejected.dxf"), **params)
    assert not (tmp_path / "rejected.dxf").exists()


def test_save_work_part_does_not_save_modified_components(rig):
    from unittest.mock import Mock

    original = rig.part.Save
    child = type("Child", (), {"IsModified": True})()

    def save(components, close):
        if components:
            child.IsModified = False
        return original(components, close)

    rig.part.Save = save
    rig.e._save_component_drawing_previews = Mock(
        side_effect=AssertionError("Implicit component save")
    )
    rig.e._save_part()
    assert child.IsModified
    rig.e._save_component_drawing_previews.assert_not_called()
