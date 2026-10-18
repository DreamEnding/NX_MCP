"""STEP validation orchestration and numerical checks without a CAD dependency."""

from __future__ import annotations

import copy
import json
from importlib.metadata import PackageNotFoundError

import pytest
from examples import validate_step_geometry as validation


@pytest.fixture
def measurements():
    return {
        "file_length_units": ["millimetre"],
        "solid_count": 1,
        "length_units": "mm",
        "volume_units": "mm^3",
        "coordinate_system": "step_absolute",
        "min": [0.0, 0.0, 0.0],
        "max": [20.0, 10.0, 12.5],
        "dimensions": [20.0, 10.0, 12.5],
        "volume": 2500.0,
    }


@pytest.fixture
def inspection(tmp_path, monkeypatch, measurements):
    source = tmp_path / "block with spaces.stp"
    source.write_bytes(b"fixture STEP")
    monkeypatch.setattr(validation.metadata, "version", lambda name: "7.9.3.1.1")
    monkeypatch.setattr(validation, "_inspect_step", lambda path: copy.deepcopy(measurements))
    return tmp_path, source


def test_success_records_input_identity_units_geometry_and_tolerances(inspection):
    workspace, source = inspection
    assert validation.run(workspace, source.name, "check") == 0
    report = json.loads((workspace / "check/report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["stage"] == "complete"
    assert report["ocp_version"] == "7.9.3.1.1"
    assert len(report["input_sha256"]) == 64
    assert report["measurements"]["volume"] == 2500
    assert report["measurements"]["length_units"] == "mm"
    assert report["measurements"]["volume_units"] == "mm^3"
    assert report["checks"]["length_tolerance_mm"] == 1e-6
    assert report["checks"]["volume_tolerance_mm3"] == 1e-3
    assert source.read_bytes() == b"fixture STEP"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("solid_count", 0),
        ("solid_count", 2),
        ("min", [1, 0, 0]),
        ("max", [20, 10, 13]),
        ("dimensions", [20, 10, 13]),
        ("volume", 2400),
        ("volume", float("nan")),
        ("volume", float("inf")),
        ("dimensions", [20, 10, float("nan")]),
        ("min", [0, 0]),
        ("file_length_units", []),
        ("file_length_units", ["unknown"]),
    ],
)
def test_wrong_geometry_cannot_pass(measurements, field, value):
    measurements[field] = value
    with pytest.raises(ValueError):
        validation._geometry_checks(measurements)


def test_tolerances_are_absolute_and_accept_small_roundoff(measurements):
    measurements["max"][0] += 0.5e-6
    measurements["volume"] += 0.5e-3
    validation._geometry_checks(measurements)
    measurements["max"][0] += 2e-6
    with pytest.raises(ValueError, match="max"):
        validation._geometry_checks(measurements)


def test_numerical_failure_preserves_measurements(inspection, measurements):
    workspace, source = inspection
    measurements["volume"] = 2400
    assert validation.run(workspace, source.name, "wrong") == 1
    report = json.loads((workspace / "wrong/report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["stage"] == "validation"
    assert report["measurements"]["volume"] == 2400
    assert report["checks"]["expected_volume_mm3"] == 2500
    assert report["checks"]["volume_tolerance_mm3"] == 1e-3
    assert "volume" in report["error"]


@pytest.mark.parametrize("argument", ["input", "output"])
@pytest.mark.parametrize("path", ["../escape.stp", "/absolute", "C:part.stp", "\\rooted", " "])
def test_path_preflight_rejects_escaping_and_absolute_paths(inspection, argument, path):
    workspace, source = inspection
    args = (path, "check") if argument == "input" else (source.name, path)
    assert validation.run(workspace, *args) == 1
    assert not (workspace / "check").exists()


def test_resolved_link_cannot_escape_workspace(inspection, monkeypatch):
    workspace, source = inspection
    original = validation.Path.resolve

    def resolve(path, *args, **kwargs):
        if path == workspace / "link":
            return workspace.parent / "outside"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(validation.Path, "resolve", resolve)
    assert validation.run(workspace, source.name, "link") == 1


@pytest.mark.parametrize("problem", ["missing", "empty", "directory", "format"])
def test_input_must_be_a_nonempty_step_file(inspection, problem):
    workspace, source = inspection
    if problem == "missing":
        source.unlink()
    elif problem == "empty":
        source.write_bytes(b"")
    elif problem == "directory":
        source.unlink()
        source.mkdir()
    else:
        source = source.rename(source.with_suffix(".prt"))
    assert validation.run(workspace, source.name, "check") == 1
    assert not (workspace / "check").exists()


def test_rerun_cannot_overwrite_existing_evidence(inspection):
    workspace, source = inspection
    assert validation.run(workspace, source.name, "check") == 0
    report = workspace / "check/report.json"
    original = report.read_bytes()
    assert validation.run(workspace, source.name, "check") == 1
    assert report.read_bytes() == original


@pytest.mark.parametrize("problem", ["dependency", "read"])
def test_kernel_failures_are_reported_without_retry(inspection, monkeypatch, problem):
    workspace, source = inspection
    calls = []

    def fail(*args):
        calls.append(args)
        if problem == "dependency":
            raise PackageNotFoundError("cadquery-ocp")
        raise ValueError("STEP transfer failed")

    if problem == "dependency":
        monkeypatch.setattr(validation.metadata, "version", fail)
    else:
        monkeypatch.setattr(validation, "_inspect_step", fail)
    assert validation.run(workspace, source.name, "check") == 1
    report = json.loads((workspace / "check/report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["error"]
    assert len(calls) == 1


def test_report_write_failure_returns_nonzero(inspection, monkeypatch, capsys):
    workspace, source = inspection
    original = validation.Path.write_text

    def fail_report(path, *args, **kwargs):
        if path.name == "report.json":
            raise OSError("report denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(validation.Path, "write_text", fail_report)
    assert validation.run(workspace, source.name, "check") == 1
    assert "report denied" in capsys.readouterr().err
