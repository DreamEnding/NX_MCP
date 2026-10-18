"""Check the real_smoke 20 x 10 x 12.5 mm STEP solid in an isolated OCP environment.

This downstream acceptance example imports neither Siemens NX nor the MCP sidecar.
Install cadquery-ocp==7.9.3.1.1 in a separate Python 3.12 environment to run it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from importlib import metadata
from pathlib import Path, PureWindowsPath
from typing import Any

EXPECTED_MIN_MM = (0.0, 0.0, 0.0)
EXPECTED_MAX_MM = (20.0, 10.0, 12.5)
EXPECTED_VOLUME_MM3 = 2500.0
LENGTH_TOLERANCE_MM = 1e-6
VOLUME_TOLERANCE_MM3 = 1e-3


def _workspace_path(workspace: Path, relative: str) -> Path:
    requested = Path(relative)
    if not relative.strip() or requested.is_absolute() or PureWindowsPath(relative).anchor:
        raise ValueError("Paths must be nonempty and relative to the workspace")
    resolved = (workspace / requested).resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError("Resolved path escapes the workspace")
    return resolved


def _geometry_checks(measured: dict[str, Any]) -> None:
    units = measured["file_length_units"]
    if not units or any(unit.lower() not in {"millimetre", "inch"} for unit in units):
        raise ValueError(f"STEP length units must explicitly be millimetre or inch: {units}")
    if measured["solid_count"] != 1:
        raise ValueError(f"STEP must contain exactly one solid, got {measured['solid_count']}")
    for name, expected in (
        ("min", EXPECTED_MIN_MM),
        ("max", EXPECTED_MAX_MM),
        ("dimensions", EXPECTED_MAX_MM),
    ):
        actual = measured[name]
        if len(actual) != 3 or any(
            not math.isfinite(value)
            or not math.isclose(value, target, rel_tol=0, abs_tol=LENGTH_TOLERANCE_MM)
            for value, target in zip(actual, expected, strict=True)
        ):
            raise ValueError(f"STEP {name} {actual} mm differs from {expected} mm")
    volume = measured["volume"]
    if not math.isfinite(volume) or not math.isclose(
        volume, EXPECTED_VOLUME_MM3, rel_tol=0, abs_tol=VOLUME_TOLERANCE_MM3
    ):
        raise ValueError(f"STEP volume {volume} mm^3 differs from {EXPECTED_VOLUME_MM3} mm^3")


def _inspect_step(path: Path) -> dict[str, Any]:
    # Keep the native geometry dependency out of the sidecar and NX interpreters.
    from OCP.Bnd import Bnd_Box
    from OCP.BRep import BRep_Tool
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString
    from OCP.TopAbs import (
        TopAbs_EDGE,
        TopAbs_FACE,
        TopAbs_SHELL,
        TopAbs_SOLID,
        TopAbs_VERTEX,
        TopAbs_WIRE,
    )
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopTools import TopTools_IndexedMapOfShape

    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError("STEP could not be read")
    lengths, angles, solid_angles = (TColStd_SequenceOfAsciiString() for _ in range(3))
    reader.FileUnits(lengths, angles, solid_angles)
    file_units = [lengths.Value(index).ToCString() for index in range(1, lengths.Length() + 1)]
    # OCCT expresses this scale in millimetres; set it after loading the STEP model.
    reader.SetSystemLengthUnit(1.0)
    roots = reader.NbRootsForTransfer()
    if roots < 1 or reader.TransferRoots() != roots:
        raise ValueError("STEP transfer failed or left untranslated roots")
    shape = reader.OneShape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError("STEP contains null or invalid B-rep geometry")
    solids = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_SOLID, solids)
    if solids.Extent() != 1:
        raise ValueError(f"STEP must contain exactly one solid, got {solids.Extent()}")
    for kind in (TopAbs_SHELL, TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE, TopAbs_VERTEX):
        if TopExp_Explorer(shape, kind, TopAbs_SOLID).More():
            raise ValueError("STEP contains geometry outside its solid")
    shells = TopExp_Explorer(shape, TopAbs_SHELL)
    while shells.More():
        if not BRep_Tool.IsClosed_s(shells.Current()):
            raise ValueError("STEP solid contains an open shell")
        shells.Next()
    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bounds, False, False)
    if bounds.IsVoid() or bounds.IsOpen():
        raise ValueError("STEP bounding box is empty or unbounded")
    minimum, maximum = list(bounds.Get()[:3]), list(bounds.Get()[3:])
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties, True, False, False)
    dimensions = [high - low for low, high in zip(minimum, maximum, strict=True)]
    volume = properties.Mass()
    if not all(math.isfinite(value) for value in [*minimum, *maximum, *dimensions, volume]):
        raise ValueError("STEP measurements are nonfinite")
    return {
        "file_length_units": file_units,
        "solid_count": solids.Extent(),
        "length_units": "mm",
        "volume_units": "mm^3",
        "coordinate_system": "step_absolute",
        "min": minimum,
        "max": maximum,
        "dimensions": dimensions,
        "volume": volume,
    }


def run(workspace: Path, input_path: str, output_dir: str) -> int:
    started = time.perf_counter()
    try:
        root = workspace.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Workspace must be an existing directory")
        source = _workspace_path(root, input_path)
        destination = _workspace_path(root, output_dir)
        if source.suffix.lower() not in {".step", ".stp"}:
            raise ValueError("Input must be a .step or .stp file from this acceptance run")
        if not source.is_file() or source.stat().st_size == 0:
            raise ValueError("Input STEP is missing or empty")
        destination.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as error:
        print(f"Preflight failed: {error}", file=sys.stderr)
        return 1

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "stage": "preflight",
        "input": source.relative_to(root).as_posix(),
        "python_version": sys.version,
        "ocp_version": None,
        "checks": {
            "expected_min_mm": list(EXPECTED_MIN_MM),
            "expected_max_mm": list(EXPECTED_MAX_MM),
            "expected_volume_mm3": EXPECTED_VOLUME_MM3,
            "length_tolerance_mm": LENGTH_TOLERANCE_MM,
            "volume_tolerance_mm3": VOLUME_TOLERANCE_MM3,
        },
    }
    try:
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        report["input_sha256"] = digest.hexdigest()
        report["ocp_version"] = metadata.version("cadquery-ocp")
        report["stage"] = "inspection"
        report["measurements"] = _inspect_step(source)
        report["stage"] = "validation"
        _geometry_checks(report["measurements"])
        report["status"] = "passed"
        report["stage"] = "complete"
    except Exception as error:
        report["error"] = str(error)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    if report["status"] != "passed":
        print(f"{report['stage']} failed: {report['error']}", file=sys.stderr)
    try:
        (destination / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as error:
        print(f"Report could not be written: {error}", file=sys.stderr)
        return 1
    if report["status"] != "passed":
        return 1
    print(f"Validated: {destination / 'report.json'}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--input", required=True, help="Workspace-relative acceptance STEP path")
    parser.add_argument(
        "--output-dir", required=True, help="New workspace-relative report directory"
    )
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.workspace, arguments.input, arguments.output_dir))


if __name__ == "__main__":
    main()
