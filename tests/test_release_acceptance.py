"""Acceptance verifies installed bytes and never replays uncertain native work."""

import io
import json
import runpy
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

RUNNER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/accept_release.py"))


def package(tmp_path):
    install = tmp_path / "install"
    runtime = install / "venv/nx_mcp"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_bytes(b"version = 'test'\n")
    release = {"version": "1.0", "commit": "a" * 40}
    (install / "release.json").write_text(json.dumps(release))
    (install / "source").mkdir()
    (install / "source/file.py").write_bytes(b"source\n")
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("nx_mcp/__init__.py", (runtime / "__init__.py").read_bytes())
    files = {
        "release.json": json.dumps(release).encode(),
        "source/file.py": b"source\n",
        "wheels/nx_mcp-1.0-py3-none-any.whl": wheel.getvalue(),
    }
    files["manifest.json"] = json.dumps({k: RUNNER["digest"](v) for k, v in files.items()}).encode()
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return archive, RUNNER["digest"](archive.read_bytes()), release["commit"], install, runtime


def test_release_checks_source_wheel_commit_and_archive(tmp_path):
    args = package(tmp_path)
    result = RUNNER["verify_package"](*args)
    assert result["source_files_verified"] == result["runtime_files_verified"] == 1
    assert result["loaded_process_bytes_attested"] is False
    with pytest.raises(RuntimeError, match="SHA-256"):
        RUNNER["verify_package"](args[0], "0" * 64, *args[2:])
    with pytest.raises(RuntimeError, match="commit"):
        RUNNER["verify_package"](*args[:2], "b" * 40, *args[3:])
    (args[3] / "source/file.py").write_text("changed")
    with pytest.raises(RuntimeError, match="Installed source mismatch"):
        RUNNER["verify_package"](*args)


def test_release_rejects_shadow_runtime_and_changed_wheel(tmp_path):
    args = package(tmp_path)
    (args[4] / "unexpected.py").write_text("shadow")
    with pytest.raises(RuntimeError, match="Unpackaged"):
        RUNNER["verify_package"](*args)
    (args[4] / "unexpected.py").unlink()
    (args[4] / "__init__.py").write_text("changed")
    with pytest.raises(RuntimeError, match="differs from wheel"):
        RUNNER["verify_package"](*args)


@pytest.mark.parametrize("state", ["running", "failed"])
def test_resume_does_not_repeat_uncertain_native_phase(tmp_path, state):
    path = tmp_path / "acceptance.json"
    path.write_text(
        json.dumps({"identity": {"commit": "a"}, "phases": {"native": {"state": state}}})
    )
    with pytest.raises(RuntimeError, match="no automatic replay"):
        RUNNER["prepare_report"](path, {"commit": "a"}, True)


def test_resume_requires_same_inputs_and_complete_preservation(tmp_path):
    path = tmp_path / "acceptance.json"
    report = {"identity": {"commit": "a"}, "state": "verified", "phases": {}}
    path.write_text(json.dumps(report))
    assert RUNNER["prepare_report"](path, report["identity"], True) == report
    with pytest.raises(RuntimeError, match="inputs differ"):
        RUNNER["prepare_report"](path, {"commit": "b"}, True)
    report["phases"]["native"] = {"state": "passed"}
    path.write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match="reconcile session"):
        RUNNER["prepare_report"](path, report["identity"], True)


def test_phase_persists_running_before_work_and_failure_after(tmp_path):
    path = tmp_path / "acceptance.json"
    report = {"phases": {}}

    def fail():
        assert json.loads(path.read_text())["phases"]["native"]["state"] == "running"
        raise RuntimeError("uncertain outcome")

    with pytest.raises(RuntimeError, match="uncertain"):
        RUNNER["phase"](report, path, "native", fail)
    assert json.loads(path.read_text())["state"] == "failed"


def test_dependency_pins_checked_not_only_pip_compatibility(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text("mcp==1.0 \\\n    --hash=sha256:abc\n")
    assert RUNNER["verify_dependencies"](lock, lambda name: "1.0") == {"mcp": "1.0"}
    with pytest.raises(RuntimeError, match="dependency mismatch"):
        RUNNER["verify_dependencies"](lock, lambda name: "2.0")


@pytest.mark.parametrize("path", ["../file", "/file", "C:/file", "folder\\file"])
def test_package_paths_cannot_escape(path):
    with pytest.raises(RuntimeError, match="Unsafe"):
        RUNNER["safe_name"](path)


def test_native_exit_zero_still_requires_preservation_evidence(tmp_path, monkeypatch):
    (tmp_path / "native").mkdir()
    receipt = tmp_path / "native/release-validation.json"
    receipt.write_text(json.dumps({"passed": True, "session_restored": False}))
    monkeypatch.setattr(
        RUNNER["run_native"].__globals__["subprocess"],
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    with pytest.raises(RuntimeError, match="session_restored evidence"):
        RUNNER["run_native"](tmp_path, tmp_path, 179)
    receipt.write_text(json.dumps({"passed": True, "session_restored": True, "suites": []}))
    result = RUNNER["run_native"](tmp_path, tmp_path, 179)
    assert result["sha256"] == RUNNER["digest"](receipt.read_bytes())


def test_native_failure_still_checks_original_session(tmp_path, monkeypatch):
    execute = RUNNER["execute"]
    namespace = execute.__globals__
    snapshot = {"nx_version": "v2606", "tool_count": 179}
    verified = []

    async def read_snapshot():
        return snapshot

    monkeypatch.setattr(
        namespace["importlib"].util,
        "find_spec",
        lambda name: SimpleNamespace(origin=str(tmp_path / "venv/nx_mcp/__init__.py")),
    )
    monkeypatch.setitem(namespace, "verify_package", lambda *args: {})
    monkeypatch.setitem(namespace, "verify_dependencies", lambda *args: {})
    monkeypatch.setattr(
        namespace["subprocess"],
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok"),
    )
    monkeypatch.setattr(
        namespace["runpy"],
        "run_path",
        lambda *args: {
            "snapshot": read_snapshot,
            "verify_session": lambda a, b: verified.append((a, b)),
        },
    )

    def fail_native(*args):
        raise RuntimeError("native test failed")

    monkeypatch.setitem(namespace, "run_native", fail_native)
    args = SimpleNamespace(
        install_root=tmp_path,
        release_zip=tmp_path / "r.zip",
        sha256="a",
        expected_commit="b",
        expected_tool_count=179,
        expected_nx_version="v2606",
        verify_only=False,
        output=tmp_path,
    )
    report = {"phases": {}}
    with pytest.raises(RuntimeError, match="native test failed"):
        execute(args, tmp_path / "acceptance.json", report)
    assert verified == [(snapshot, snapshot)]
    assert report["state"] == "failed"
    assert report["phases"]["session_preservation"]["state"] == "passed"


def test_cli_accepts_new_shared_drive_output_when_canonical_resolution_fails(tmp_path, monkeypatch):
    """Reproduce WinError 1005 without requiring a Windows VirtIO mount."""
    main = RUNNER["main"]
    namespace = main.__globals__
    install = tmp_path / "install"
    install.mkdir()
    fixture = tmp_path / "authorized.step"
    fixture.write_text("fixture")
    archive = tmp_path / "release.zip"
    archive.write_text("fixture")
    output = tmp_path / "new-parent/acceptance"
    error = OSError("The volume does not contain a recognized file system")
    error.winerror = 1005

    def unsupported_resolve(*args, **kwargs):
        raise error

    monkeypatch.setattr(Path, "resolve", unsupported_resolve)
    monkeypatch.setattr(namespace["sys"], "platform", "win32")
    monkeypatch.setattr(namespace["sys"], "version_info", (3, 12, 0))
    monkeypatch.setattr(
        namespace["sys"],
        "argv",
        [
            "accept_release.py",
            "--release-zip",
            str(archive),
            "--sha256",
            "a" * 64,
            "--expected-commit",
            "b" * 40,
            "--install-root",
            str(install),
            "--output",
            str(output),
            "--verify-only",
        ],
    )
    monkeypatch.setenv("NX_MCP_URL", "http://127.0.0.1:8765/mcp")
    monkeypatch.setenv("NX_VENDOR_STEP", str(fixture))
    calls = []
    monkeypatch.setitem(namespace, "execute", lambda args, receipt, report: calls.append(report))
    main()
    assert len(calls) == 1
    assert (output / "acceptance.json").is_file()
    assert calls[0]["identity"]["vendor_step"] == str(fixture)
    assert calls[0]["identity"]["install_root"] == str(install)
    assert calls[0]["identity"]["release_zip"] == str(archive)
    assert not (install / "acceptance.lock").exists()
