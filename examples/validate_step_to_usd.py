"""Validate the real_smoke STEP block with an isolated Python 3.12 converter environment.

This is a downstream acceptance example, not a general CAD exporter or MCP tool.
The expected block is 20 x 10 x 12.5 mm. No NX or sidecar imports are needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path, PureWindowsPath
from typing import Any

EXPECTED_DIMENSIONS_M = (0.020, 0.010, 0.0125)
TOLERANCE_M = 0.00001
TIMEOUT_SECONDS = 120


def _workspace_path(workspace: Path, relative: str) -> Path:
    requested = Path(relative)
    if not relative.strip() or requested.is_absolute() or PureWindowsPath(relative).anchor:
        raise ValueError("Paths must be nonempty and relative to the workspace")
    resolved = (workspace / requested).resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError("Resolved path escapes the workspace")
    return resolved


def _geometry_checks(
    mesh_count: int, meters_per_unit: float, up_axis: str, dimensions: Sequence[float]
) -> dict[str, Any]:
    if mesh_count < 1:
        raise ValueError("USD contains no nonempty visible mesh")
    if not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
        raise ValueError("USD metersPerUnit must be positive and finite")
    if up_axis != "Z":
        raise ValueError("USD must be Z-up")
    measured = [float(value) * meters_per_unit for value in dimensions]
    if len(measured) != 3 or any(
        not math.isfinite(actual)
        or not math.isclose(actual, expected, rel_tol=0, abs_tol=TOLERANCE_M)
        for actual, expected in zip(measured, EXPECTED_DIMENSIONS_M, strict=True)
    ):
        raise ValueError(f"USD dimensions {measured} m differ from {EXPECTED_DIMENSIONS_M} m")
    return {
        "mesh_count": mesh_count,
        "meters_per_unit": meters_per_unit,
        "up_axis": up_axis,
        "dimensions_m": measured,
        "expected_dimensions_m": list(EXPECTED_DIMENSIONS_M),
        "tolerance_m": TOLERANCE_M,
    }


def _inspect_usd(path: Path) -> dict[str, Any]:
    # Importing the wheel configures its bundled pxr and native-library paths.
    import usd_convert_cad  # noqa: F401
    from pxr import Gf, Usd, UsdGeom, UsdUtils

    _, _, unresolved = UsdUtils.ComputeAllDependencies(str(path))
    if unresolved:
        raise ValueError(f"Unresolved USD dependencies: {list(unresolved)}")
    stage = Usd.Stage.Open(str(path), load=Usd.Stage.LoadAll)
    if stage is None:
        raise ValueError("USD stage could not be opened")
    if stage.GetCompositionErrors():
        raise ValueError(f"USD composition errors: {stage.GetCompositionErrors()}")
    if not stage.HasAuthoredMetadata("metersPerUnit"):
        raise ValueError("USD must explicitly author metersPerUnit")

    transforms = UsdGeom.XformCache(Usd.TimeCode.Default())
    bounds = Gf.Range3d()
    mesh_count = 0
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        if UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts or not indices:
            continue
        valid, reason = UsdGeom.Mesh.ValidateTopology(indices, counts, len(points))
        if not valid or any(count < 3 for count in counts):
            raise ValueError(f"Invalid mesh topology at {prim.GetPath()}: {reason}")
        transform = transforms.GetLocalToWorldTransform(prim)
        # Measure actual points, not potentially stale authored extent hints.
        for point in points:
            world_point = transform.Transform(Gf.Vec3d(point))
            if not all(math.isfinite(value) for value in world_point):
                raise ValueError(f"Nonfinite mesh point at {prim.GetPath()}")
            bounds.UnionWith(world_point)
        mesh_count += 1
    checks = _geometry_checks(
        mesh_count,
        UsdGeom.GetStageMetersPerUnit(stage),
        str(UsdGeom.GetStageUpAxis(stage)),
        tuple(bounds.GetSize()),
    )
    checks["dependencies_resolved"] = True
    checks["usd_version"] = list(Usd.GetVersion())
    return checks


def _run_process(command: list[str], output_dir: Path, log_name: str) -> int:
    with (
        (output_dir / f"{log_name}.stdout.log").open("wb") as stdout,
        (output_dir / f"{log_name}.stderr.log").open("wb") as stderr,
    ):
        result = subprocess.run(
            command,
            cwd=output_dir,
            stdout=stdout,
            stderr=stderr,
            timeout=TIMEOUT_SECONDS,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    return result.returncode


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
        # There is no safely owned report directory when path preflight fails.
        print(f"Preflight failed: {error}", file=sys.stderr)
        return 1

    report: dict[str, Any] = {
        "status": "failed",
        "stage": "preflight",
        "input": source.relative_to(root).as_posix(),
        "converter_version": None,
        "exit_code": None,
    }
    try:
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        report["input_sha256"] = digest.hexdigest()
        if sys.version_info[:2] != (3, 12):
            raise ValueError("Run this script in the isolated Python 3.12 converter environment")
        report["converter_version"] = metadata.version("usd-convert-cad")
        help_command = [sys.executable, "-m", "usd_convert_cad", "--help"]
        if _run_process(help_command, destination, "help") != 0:
            raise RuntimeError("Converter --help failed; see help.stderr.log")
        help_text = (destination / "help.stdout.log").read_text(encoding="utf-8", errors="replace")
        if "--up-axis" not in help_text:
            raise ValueError("Installed converter does not advertise --up-axis")

        output = destination / "scene.usdc"
        command = [
            sys.executable,
            "-m",
            "usd_convert_cad",
            "-i",
            str(source),
            "-o",
            str(output),
            "--up-axis",
            "z",
        ]
        report["command"] = command
        report["stage"] = "conversion"
        report["exit_code"] = _run_process(command, destination, "conversion")
        if report["exit_code"] != 0:
            raise RuntimeError(
                f"Converter exited with {report['exit_code']}; see conversion.stderr.log"
            )
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError("Converter did not produce a nonempty scene.usdc")
        report["stage"] = "validation"
        report["checks"] = _inspect_usd(output)
        report["status"] = "passed"
        report["stage"] = "complete"
    except Exception as error:
        # Third-party converter/USD failures belong in the report, never a retry.
        report["error"] = str(error)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    if report["status"] != "passed":
        print(f"{report['stage']} failed: {report['error']}", file=sys.stderr)
    try:
        (destination / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        print(f"Report could not be written: {error}", file=sys.stderr)
        return 1
    if report["status"] != "passed":
        return 1
    print(f"Validated: {destination / 'scene.usdc'}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--input", required=True, help="Workspace-relative acceptance STEP path")
    parser.add_argument(
        "--output-dir", required=True, help="New workspace-relative output directory"
    )
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.workspace, arguments.input, arguments.output_dir))


if __name__ == "__main__":
    main()
