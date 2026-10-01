"""Evidence is observed through the bridge, never inferred from a descriptor."""

import json
from datetime import datetime, timezone

import pytest

from nx_mcp.evidence import collect


@pytest.fixture
def probe(tmp_path, monkeypatch):
    monkeypatch.setattr("nx_mcp.evidence.git_state", lambda checkout: ("a" * 40, False))
    path = tmp_path / "probe.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "nx_version": "NX2506",
                "nx_build": "2506.4021",
                "python_version": "3.12.9",
                "python_architecture": "64bit",
                "os": "Windows-11",
                "git_commit": "a" * 40,
                "git_dirty": False,
                "run_journal_switches": [],
                "nxopen_imported": True,
                "pydantic_available": False,
                "mcp_available": False,
                "bridge_import_error": None,
            }
        ),
        encoding="utf-8",
    )
    return path


class Bridge:
    def __init__(self, implementation="python_batch", version="NX2506"):
        self.calls = []
        self.implementation = implementation
        self.version = version

    async def call(self, method, params):
        self.calls.append((method, params))
        return {
            "connected": True,
            "nx_version": self.version,
            "bridge_protocol": 1,
            "bridge_implementation": self.implementation,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["python_batch", "csharp_gui"])
async def test_evidence_records_observed_bridge_without_session_secrets(probe, implementation):
    output = probe.parent / "evidence.json"
    bridge = Bridge(implementation)
    await collect(probe, output, implementation, bridge=bridge)
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["bridge_implementation"] == implementation
    assert evidence["git_commit"] == "a" * 40
    assert evidence["nx_build"] == "2506.4021"
    assert evidence["bridge_protocol"] == 1
    assert evidence["bridge_observed_at_utc"]
    assert bridge.calls == [("nx_status", {})]
    assert not {"token", "port", "host", "active_part"}.intersection(evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("git_commit", "unknown"),
        ("git_dirty", "false"),
        ("nx_build", ""),
        ("run_journal_switches", "-nx"),
        ("bridge_import_error", "ImportError: unavailable"),
    ],
)
async def test_invalid_or_failed_probe_cannot_become_evidence(probe, field, value):
    data = json.loads(probe.read_text(encoding="utf-8"))
    data[field] = value
    probe.write_text(json.dumps(data), encoding="utf-8")
    bridge = Bridge()
    output = probe.parent / "evidence.json"
    with pytest.raises(ValueError):
        await collect(probe, output, "python_batch", bridge=bridge)
    assert bridge.calls == []
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("implementation", "version"),
    [("csharp_gui", "NX2506"), ("unknown", "NX2506"), ("python_batch", "NX2206")],
)
async def test_evidence_rejects_wrong_bridge_or_probe_from_another_nx(
    probe, implementation, version
):
    output = probe.parent / "evidence.json"
    with pytest.raises(ValueError):
        await collect(probe, output, "python_batch", bridge=Bridge(implementation, version))
    assert not output.exists()


@pytest.mark.asyncio
async def test_evidence_preserves_prior_run(probe):
    with pytest.raises(FileExistsError):
        await collect(probe, probe, "python_batch", bridge=Bridge())
    assert json.loads(probe.read_text(encoding="utf-8"))["schema_version"] == 1


@pytest.mark.asyncio
async def test_evidence_preserves_dirty_tree_as_development_evidence(probe, monkeypatch):
    monkeypatch.setattr("nx_mcp.evidence.git_state", lambda checkout: ("a" * 40, True))
    data = json.loads(probe.read_text(encoding="utf-8"))
    data["git_dirty"] = True
    probe.write_text(json.dumps(data), encoding="utf-8")
    output = probe.parent / "evidence.json"
    await collect(probe, output, "python_batch", bridge=Bridge())
    assert json.loads(output.read_text(encoding="utf-8"))["git_dirty"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [("b" * 40, False), ("a" * 40, True)])
async def test_evidence_rejects_probe_from_another_checkout_state(probe, monkeypatch, state):
    monkeypatch.setattr("nx_mcp.evidence.git_state", lambda checkout: state)
    bridge = Bridge()
    output = probe.parent / "evidence.json"
    with pytest.raises(ValueError, match="Checkout changed"):
        await collect(probe, output, "python_batch", bridge=bridge)
    assert not output.exists()
    assert bridge.calls == []
