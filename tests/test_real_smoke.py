"""Tests for the real-NX acceptance runner outside a live NX session."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from nx_mcp.real_smoke import run, run_iteration


class NonTextErrorClient:
    async def call_tool(self, name: str, arguments: dict):
        return SimpleNamespace(is_error=True, content=[SimpleNamespace()])


@pytest.mark.asyncio
async def test_real_smoke_reports_non_text_error_content(tmp_path: Path):
    with pytest.raises(RuntimeError, match="nx_status failed: unknown error"):
        await run_iteration(NonTextErrorClient(), tmp_path, 1)


@pytest.mark.asyncio
async def test_real_smoke_rejects_non_positive_iteration_count(tmp_path: Path):
    with pytest.raises(ValueError, match="iterations must be at least 1"):
        await run(tmp_path, 0)


class SafetyClient:
    def __init__(self, active_part=None, fail_at="nx_create_part", switch_part=False):
        self.active_part = active_part
        self.fail_at = fail_at
        self.switch_part = switch_part
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        if name == self.fail_at:
            if self.switch_part:
                self.active_part = {"part_id": "unrelated"}
            return SimpleNamespace(
                is_error=True, content=[SimpleNamespace(text="injected failure")]
            )
        data = {}
        if name == "nx_status":
            data = {"connected": True, "active_part": self.active_part}
        elif name == "nx_create_part":
            self.active_part = {"part_id": "owned"}
            data = {"part": self.active_part}
        return SimpleNamespace(is_error=False, structured_content=data, content=[])


@pytest.mark.asyncio
async def test_smoke_does_not_close_when_create_fails(tmp_path):
    client = SafetyClient()
    with pytest.raises(RuntimeError, match="injected failure"):
        await run_iteration(client, tmp_path, 1)
    assert "nx_close_part" not in client.calls


@pytest.mark.asyncio
async def test_smoke_refuses_existing_work_part(tmp_path):
    client = SafetyClient(active_part={"part_id": "user-part"})
    with pytest.raises(RuntimeError, match="work part"):
        await run_iteration(client, tmp_path, 1)
    assert client.calls == ["nx_status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("switch_part", [True, False])
async def test_smoke_cleanup_only_closes_its_own_part(tmp_path, switch_part):
    client = SafetyClient(fail_at="nx_list_bodies", switch_part=switch_part)
    with pytest.raises(RuntimeError, match="injected failure"):
        await run_iteration(client, tmp_path, 1)
    assert ("nx_close_part" in client.calls) is (not switch_part)


@pytest.mark.asyncio
async def test_smoke_refuses_existing_output_before_creating_part(tmp_path):
    (tmp_path / "smoke").mkdir()
    target = tmp_path / "smoke/run-01.prt"
    target.write_text("keep", encoding="utf-8")
    client = SafetyClient()
    with pytest.raises(ValueError, match="exists"):
        await run_iteration(client, tmp_path, 1)
    assert "nx_create_part" not in client.calls
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["../escape", "C:/escape", " "])
async def test_smoke_rejects_invalid_prefix_without_starting_sidecar(tmp_path, prefix):
    with pytest.raises(ValueError):
        await run(tmp_path, 1, prefix)


@pytest.mark.asyncio
async def test_missing_structured_content_is_reported(tmp_path):
    class Client:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(is_error=False, structured_content=None)

    with pytest.raises(RuntimeError, match="no structured content"):
        await run_iteration(Client(), tmp_path, 1)


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_original_error(tmp_path, capsys):
    class Client(SafetyClient):
        async def call_tool(self, name, arguments):
            if name == "nx_close_part":
                raise ConnectionError("cleanup disconnected")
            return await super().call_tool(name, arguments)

    with pytest.raises(RuntimeError, match="injected failure"):
        await run_iteration(Client(fail_at="nx_list_bodies"), tmp_path, 1)
    assert "could not confirm a safe close" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["missing", "empty", "directory"])
def test_output_must_be_a_nonempty_file(tmp_path, mode):
    from nx_mcp.real_smoke import _verify_file

    path = tmp_path / "output.stp"
    if mode == "empty":
        path.touch()
    elif mode == "directory":
        path.mkdir()
    with pytest.raises(RuntimeError, match="not created or is empty"):
        _verify_file(path)


def test_step_output_must_contain_solid_geometry(tmp_path):
    from nx_mcp.real_smoke import _verify_step_solid

    path = tmp_path / "output.stp"
    path.write_text("ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no solid geometry"):
        _verify_step_solid(path)
