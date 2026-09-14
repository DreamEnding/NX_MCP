"""Orchestration tests use no CAD converter, OpenUSD, or live NX session."""

from __future__ import annotations

import json
import subprocess
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from examples import validate_step_to_usd as validation


@pytest.fixture
def conversion(tmp_path, monkeypatch):
    source = tmp_path / "sample with spaces.stp"
    source.write_bytes(b"current acceptance STEP")
    calls = []

    def fake_run(command, *, cwd, stdout, stderr, timeout, check, **kwargs):
        calls.append(command)
        assert cwd == tmp_path / "usd"
        assert timeout == 120
        assert check is False
        if "--help" in command:
            stdout.write(b"--input --output --up-axis {file,y,z}\n")
        else:
            Path(command[command.index("-o") + 1]).write_bytes(b"mock USD")
            stdout.write(b"conversion log\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(validation.subprocess, "run", fake_run)
    monkeypatch.setattr(validation.metadata, "version", lambda name: "0.2.0")
    monkeypatch.setattr(validation.sys, "version_info", (3, 12, 7))
    monkeypatch.setattr(
        validation,
        "_inspect_usd",
        lambda path: validation._geometry_checks(1, 0.001, "Z", (20, 10, 12.5)),
    )
    return tmp_path, source, calls


def run_conversion(conversion):
    workspace, source, _ = conversion
    return validation.run(workspace, source.name, "usd")


def report(conversion):
    return json.loads((conversion[0] / "usd" / "report.json").read_text(encoding="utf-8"))


def test_success_records_version_command_digest_and_physical_dimensions(conversion):
    assert run_conversion(conversion) == 0
    result = report(conversion)
    assert result["status"] == "passed"
    assert result["stage"] == "complete"
    assert result["converter_version"] == "0.2.0"
    assert len(result["input_sha256"]) == 64
    assert result["exit_code"] == 0
    assert result["elapsed_seconds"] >= 0
    assert result["checks"]["dimensions_m"] == [0.020, 0.010, 0.0125]
    assert result["command"] == conversion[2][1]
    assert result["command"][-2:] == ["--up-axis", "z"]
    assert result["command"][result["command"].index("-i") + 1] == str(conversion[1])
    assert (conversion[0] / "usd" / "conversion.stdout.log").read_bytes() == b"conversion log\n"


def test_second_output_directory_is_required(conversion):
    assert run_conversion(conversion) == 0
    original = (conversion[0] / "usd" / "report.json").read_bytes()
    assert run_conversion(conversion) == 1
    assert (conversion[0] / "usd" / "report.json").read_bytes() == original
    assert len(conversion[2]) == 2


@pytest.mark.parametrize("value", ["../escape.stp", "/absolute.stp", "C:part.stp", "\\rooted.stp"])
@pytest.mark.parametrize("argument", ["input", "output"])
def test_paths_must_be_workspace_relative(conversion, value, argument):
    workspace, source, calls = conversion
    args = (value, "usd") if argument == "input" else (source.name, value)
    assert validation.run(workspace, *args) == 1
    assert not (workspace / "usd").exists()
    assert calls == []


def test_resolved_link_cannot_escape_workspace(conversion, monkeypatch):
    workspace, source, calls = conversion
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == workspace / "link":
            return workspace.parent / "outside"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert validation.run(workspace, source.name, "link") == 1
    assert calls == []


@pytest.mark.parametrize("problem", ["missing", "empty", "wrong_format", "directory"])
def test_bad_input_never_starts_converter(conversion, problem):
    workspace, source, calls = conversion
    if problem == "missing":
        source.unlink()
    elif problem == "empty":
        source.write_bytes(b"")
    elif problem == "wrong_format":
        source = workspace / "sample.prt"
        source.write_bytes(b"not STEP")
    else:
        source = workspace / "directory.stp"
        source.mkdir()
    assert validation.run(workspace, source.name, "usd") == 1
    assert not (workspace / "usd").exists()
    assert calls == []


def test_missing_workspace_is_not_created(tmp_path):
    workspace = tmp_path / "missing"
    assert validation.run(workspace, "input.stp", "usd") == 1
    assert not workspace.exists()


def test_wrong_python_is_a_preflight_failure(conversion, monkeypatch):
    monkeypatch.setattr(validation.sys, "version_info", (3, 11, 9))
    assert run_conversion(conversion) == 1
    assert report(conversion)["stage"] == "preflight"
    assert "3.12" in report(conversion)["error"]
    assert conversion[2] == []


def test_missing_wheel_is_not_installed_automatically(conversion, monkeypatch):
    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(validation.metadata, "version", missing)
    assert run_conversion(conversion) == 1
    assert report(conversion)["stage"] == "preflight"
    assert conversion[2] == []


@pytest.mark.parametrize("problem", ["exit_code", "missing_flag", "timeout", "launch_error"])
def test_failed_help_probe_never_attempts_conversion(conversion, monkeypatch, problem):
    def help_failure(command, **kwargs):
        conversion[2].append(command)
        assert command[-1] == "--help"
        if problem == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        if problem == "launch_error":
            raise OSError("cannot launch interpreter")
        return subprocess.CompletedProcess(command, 2 if problem == "exit_code" else 0)

    monkeypatch.setattr(validation.subprocess, "run", help_failure)
    assert run_conversion(conversion) == 1
    assert report(conversion)["stage"] == "preflight"
    assert report(conversion)["status"] == "failed"
    assert len(conversion[2]) == 1


@pytest.mark.parametrize("problem", ["exit_code", "timeout", "launch_error", "missing", "empty"])
def test_conversion_failure_is_not_retried_or_validated(conversion, monkeypatch, problem):
    original = validation.subprocess.run

    def failed(command, **kwargs):
        if "--help" in command:
            return original(command, **kwargs)
        conversion[2].append(command)
        if problem == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        if problem == "launch_error":
            raise OSError("cannot launch converter")
        if problem in {"exit_code", "empty"}:
            (conversion[0] / "usd" / "scene.usdc").write_bytes(b"")
        kwargs["stderr"].write(b"converter diagnostic\n")
        return subprocess.CompletedProcess(command, 7 if problem == "exit_code" else 0)

    def unexpected_inspection(path):
        pytest.fail("USD inspection must not follow failed conversion")

    monkeypatch.setattr(validation.subprocess, "run", failed)
    monkeypatch.setattr(validation, "_inspect_usd", unexpected_inspection)
    assert run_conversion(conversion) == 1
    result = report(conversion)
    assert result["status"] == "failed"
    assert result["stage"] == "conversion"
    assert len(conversion[2]) == 2
    if problem == "exit_code":
        assert result["exit_code"] == 7
        assert (conversion[0] / "usd" / "conversion.stderr.log").read_bytes()


@pytest.mark.parametrize(
    "message", ["invalid USD", "unresolved dependency", "empty mesh", "size mismatch"]
)
def test_failed_usd_validation_is_reported(conversion, monkeypatch, message):
    def invalid(path):
        raise ValueError(message)

    monkeypatch.setattr(validation, "_inspect_usd", invalid)
    assert run_conversion(conversion) == 1
    result = report(conversion)
    assert result["stage"] == "validation"
    assert result["exit_code"] == 0
    assert result["error"] == message


@pytest.mark.parametrize(
    ("mesh_count", "unit", "axis", "dimensions"),
    [
        (0, 0.001, "Z", (20, 10, 12.5)),
        (1, 0, "Z", (20, 10, 12.5)),
        (1, float("nan"), "Z", (20, 10, 12.5)),
        (1, float("inf"), "Z", (20, 10, 12.5)),
        (1, 0.001, "Y", (20, 10, 12.5)),
        (1, 1, "Z", (20, 10, 12.5)),
        (1, 0.001, "Z", (20, 10, 13)),
        (1, 0.001, "Z", (20, 10, float("nan"))),
        (1, 0.001, "Z", (20, 10, float("inf"))),
    ],
)
def test_geometry_rejects_empty_nonfinite_wrong_axis_or_wrong_scale(
    mesh_count, unit, axis, dimensions
):
    with pytest.raises(ValueError):
        validation._geometry_checks(mesh_count, unit, axis, dimensions)


def test_geometry_tolerance_is_absolute_in_meters():
    checks = validation._geometry_checks(1, 0.001, "Z", (20.005, 10, 12.5))
    assert checks["dimensions_m"][0] == pytest.approx(0.020005)
    with pytest.raises(ValueError, match="dimensions"):
        validation._geometry_checks(1, 0.001, "Z", (20.02, 10, 12.5))


def test_help_does_not_require_converter_or_usd(monkeypatch, capsys):
    monkeypatch.setattr(validation.sys, "argv", ["validate_step_to_usd.py", "--help"])
    with pytest.raises(SystemExit) as caught:
        validation.main()
    assert caught.value.code == 0
    assert "--output-dir" in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["help", "conversion"])
def test_log_open_failure_stops_before_launch_and_is_reported(conversion, monkeypatch, stage):
    original = Path.open

    def open_file(path, *args, **kwargs):
        if path.name == f"{stage}.stdout.log":
            raise PermissionError("log is not writable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    assert run_conversion(conversion) == 1
    result = report(conversion)
    assert result["status"] == "failed"
    assert result["stage"] == ("preflight" if stage == "help" else "conversion")
    assert result["error"] == "log is not writable"
    assert len(conversion[2]) == (0 if stage == "help" else 1)


@pytest.mark.parametrize("valid_geometry", [True, False])
def test_report_write_failure_never_announces_success(
    conversion, monkeypatch, capsys, valid_geometry
):
    original = Path.write_text

    def write_file(path, *args, **kwargs):
        if path.name == "report.json":
            raise PermissionError("report is not writable")
        return original(path, *args, **kwargs)

    def invalid(path):
        raise ValueError("invalid geometry")

    monkeypatch.setattr(Path, "write_text", write_file)
    if not valid_geometry:
        monkeypatch.setattr(validation, "_inspect_usd", invalid)
    assert run_conversion(conversion) == 1
    captured = capsys.readouterr()
    assert "Report could not be written" in captured.err
    if not valid_geometry:
        assert "validation failed: invalid geometry" in captured.err
    assert "Validated:" not in captured.out
    assert len(conversion[2]) == 2
    assert (conversion[0] / "usd" / "scene.usdc").is_file()
    assert (conversion[0] / "usd" / "conversion.stdout.log").is_file()
    assert not (conversion[0] / "usd" / "report.json").exists()


def test_unreadable_source_is_reported_without_launch(conversion, monkeypatch):
    original = Path.open

    def open_file(path, *args, **kwargs):
        if path == conversion[1]:
            raise PermissionError("STEP is not readable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    assert run_conversion(conversion) == 1
    result = report(conversion)
    assert result["stage"] == "preflight"
    assert result["error"] == "STEP is not readable"
    assert "input_sha256" not in result
    assert conversion[2] == []


def test_unreadable_help_log_prevents_conversion(conversion, monkeypatch):
    original = Path.read_text

    def read_file(path, *args, **kwargs):
        if path.name == "help.stdout.log":
            raise PermissionError("help output is not readable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_file)
    assert run_conversion(conversion) == 1
    result = report(conversion)
    assert result["stage"] == "preflight"
    assert result["error"] == "help output is not readable"
    assert len(conversion[2]) == 1
