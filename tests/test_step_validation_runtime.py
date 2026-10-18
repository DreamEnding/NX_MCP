"""Opt-in tests with real Open CASCADE B-reps, independent of NX acceptance."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from examples import validate_step_geometry as validation

pytestmark = pytest.mark.skipif(
    os.environ.get("NX_MCP_STEP_CHECKER_TESTS") != "1",
    reason="requires explicit opt-in in the isolated OCP environment",
)


@pytest.fixture
def step(tmp_path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    def write(shape, units="MM", name="block with spaces.stp"):
        writer = STEPControl_Writer()
        previous = Interface_Static.CVal_s("write.step.unit")
        try:
            assert Interface_Static.SetCVal_s("write.step.unit", units)
            writer.Model().SetLocalLengthUnit(1.0)
            assert writer.Transfer(shape, STEPControl_AsIs) == IFSelect_RetDone
            path = tmp_path / name
            assert writer.Write(str(path)) == IFSelect_RetDone
            return path
        finally:
            assert Interface_Static.SetCVal_s("write.step.unit", previous)

    return write


@pytest.fixture
def block():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(20, 10, 12.5).Shape()


def compound(*shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    result = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


@pytest.mark.parametrize(("units", "file_units"), [("MM", "millimetre"), ("INCH", "inch")])
def test_metric_and_equivalent_inch_files_normalize_to_mm(step, block, units, file_units):
    measured = validation._inspect_step(step(block, units))
    assert [unit.lower() for unit in measured["file_length_units"]] == [file_units]
    assert measured["solid_count"] == 1
    assert measured["min"] == pytest.approx([0, 0, 0], abs=1e-6)
    assert measured["max"] == pytest.approx([20, 10, 12.5], abs=1e-6)
    assert measured["volume"] == pytest.approx(2500, abs=1e-3)
    validation._geometry_checks(measured)


def test_import_units_do_not_depend_on_global_defaults(step, block):
    from OCP.Interface import Interface_Static

    path = step(block, "INCH")
    previous = Interface_Static.CVal_s("xstep.cascade.unit")
    try:
        assert Interface_Static.SetCVal_s("xstep.cascade.unit", "M")
        validation._geometry_checks(validation._inspect_step(path))
    finally:
        assert Interface_Static.SetCVal_s("xstep.cascade.unit", previous)


@pytest.mark.parametrize("problem", ["dimension", "translation", "hole"])
def test_wrong_solids_fail_numerically_even_with_valid_brep(step, block, problem):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    from OCP.TopLoc import TopLoc_Location

    if problem == "dimension":
        shape = BRepPrimAPI_MakeBox(20, 10, 13).Shape()
    elif problem == "translation":
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(1, 0, 0))
        shape = block.Moved(TopLoc_Location(transform))
    else:
        axis = gp_Ax2(gp_Pnt(10, 5, 0), gp_Dir(0, 0, 1))
        shape = BRepAlgoAPI_Cut(block, BRepPrimAPI_MakeCylinder(axis, 1, 12.5).Shape()).Shape()
    measured = validation._inspect_step(step(shape))
    if problem == "hole":
        assert measured["dimensions"] == pytest.approx([20, 10, 12.5], abs=1e-6)
    with pytest.raises(ValueError, match="volume" if problem == "hole" else "mm"):
        validation._geometry_checks(measured)


@pytest.mark.parametrize("problem", ["sheet", "shell", "two_solids", "extra_sheet"])
def test_noncanonical_topology_is_rejected(step, block, problem):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer

    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0, 0, 5), gp_Dir(0, 0, 1)), 0, 1, 0, 1).Face()
    if problem == "sheet":
        shape = face
    elif problem == "shell":
        shape = TopExp_Explorer(block, TopAbs_SHELL).Current()
    elif problem == "two_solids":
        shape = compound(block, BRepPrimAPI_MakeBox(gp_Pnt(30, 0, 0), 1, 1, 1).Shape())
    else:
        shape = compound(block, face)
    with pytest.raises(ValueError, match="solid|outside"):
        validation._inspect_step(step(shape))


def test_missing_declared_length_unit_cannot_use_reader_default(step, block):
    path = step(block)
    content, replacements = re.subn(
        r"GLOBAL_UNIT_ASSIGNED_CONTEXT\s*\(\(#\d+,",
        "GLOBAL_UNIT_ASSIGNED_CONTEXT((",
        path.read_text(encoding="utf-8"),
    )
    assert replacements == 1
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="length units"):
        validation._geometry_checks(validation._inspect_step(path))


def test_unreadable_step_is_rejected(tmp_path):
    path = tmp_path / "invalid.stp"
    path.write_bytes(b"not STEP")
    with pytest.raises(ValueError, match="read"):
        validation._inspect_step(path)


@pytest.mark.parametrize("wrong", [False, True])
def test_standalone_cli_writes_pass_or_failure_report(step, block, tmp_path, wrong):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    source = step(BRepPrimAPI_MakeBox(20, 10, 13).Shape() if wrong else block)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(validation.__file__).resolve()),
            "--workspace",
            str(tmp_path),
            "--input",
            source.name,
            "--output-dir",
            "report",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == (1 if wrong else 0), result.stderr
    report = json.loads((tmp_path / "report/report.json").read_text(encoding="utf-8"))
    assert report["status"] == ("failed" if wrong else "passed")
    assert report["measurements"]["volume"] == pytest.approx(2600 if wrong else 2500, abs=1e-3)
