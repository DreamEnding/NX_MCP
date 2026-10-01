"""The journal emits versioned facts using only NXOpen and the standard library."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nx_mcp.provenance import file_version

pytestmark = pytest.mark.fake_nx


@pytest.fixture
def probe(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "examples/nx_runtime_probe.py"
    session = SimpleNamespace(
        GetEnvironmentVariableValue=lambda name: {
            "UGII_BASE_DIR": "D:/NX",
            "UGII_VERSION": "NX2506",
        }[name]
    )
    monkeypatch.setitem(
        sys.modules, "NXOpen", SimpleNamespace(Session=SimpleNamespace(GetSession=lambda: session))
    )
    spec = importlib.util.spec_from_file_location("nx_runtime_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("nx_mcp.provenance.file_version", lambda path: "2506.4021")
    monkeypatch.setattr("nx_mcp.provenance.git_state", lambda checkout: ("a" * 40, False))
    return module


@pytest.mark.parametrize("switches", ["[]", "null"])
def test_runtime_probe_reports_exact_build_provenance_and_launch_mode(
    probe, monkeypatch, tmp_path, switches
):
    output = tmp_path / "probe.json"
    monkeypatch.setenv("NX_MCP_PROBE_OUTPUT", str(output))
    monkeypatch.setenv("NX_MCP_RUN_JOURNAL_SWITCHES", switches)
    probe.main()
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["nx_build"] == "2506.4021"
    assert data["git_commit"] == "a" * 40
    assert data["git_dirty"] is False
    assert data["run_journal_switches"] == json.loads(switches)
    assert data["captured_at_utc"] and data["os"] and data["python_architecture"]
    assert data["bridge_import_error"] is None


@pytest.mark.parametrize("switches", ['"-nx"', "{}", "[1]"])
def test_runtime_probe_rejects_malformed_launch_metadata(probe, monkeypatch, tmp_path, switches):
    output = tmp_path / "probe.json"
    monkeypatch.setenv("NX_MCP_PROBE_OUTPUT", str(output))
    monkeypatch.setenv("NX_MCP_RUN_JOURNAL_SWITCHES", switches)
    with pytest.raises(ValueError, match="JSON string array"):
        probe.main()
    assert not output.exists()


def test_runtime_probe_requires_explicit_output(probe, monkeypatch):
    monkeypatch.delenv("NX_MCP_PROBE_OUTPUT", raising=False)
    with pytest.raises(RuntimeError, match="NX_MCP_PROBE_OUTPUT"):
        probe.main()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows version resource API")
def test_file_version_reads_real_executable_and_rejects_missing_file(probe, tmp_path):
    # This import retains the native helper while the fixture patches the journal's lookup.
    version = file_version(Path(sys.executable))
    assert version.startswith(f"{sys.version_info.major}.{sys.version_info.minor}.")
    with pytest.raises(OSError):
        file_version(tmp_path / "missing.exe")
