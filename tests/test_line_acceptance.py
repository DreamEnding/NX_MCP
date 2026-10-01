"""Acceptance regressions use fake geometry and the real MCP tool boundary."""

from contextlib import asynccontextmanager

import pytest
from mcp.client import Client

from nx_mcp.nx_bridge import NXOpenExecutor
from nx_mcp.runtime import NXToolError
from nx_mcp.server import create_server
from nx_mcp.workspace import Workspace
from tests import test_real_nx as acceptance
from tests.test_nx_executor import FAKE_NXOPEN, FakeSession

pytestmark = [pytest.mark.fake_nx, pytest.mark.integration]


def _contains_error(error, message):
    # MCP's task groups can nest the original exception on Python 3.10 as well as 3.11+.
    return message in str(error) or any(
        _contains_error(child, message) for child in getattr(error, "exceptions", ())
    )


@pytest.fixture
def scenario(monkeypatch, tmp_path):
    def configure(*, no_op_lines=False, sketch_error=False, cleanup_error=None):
        monkeypatch.setenv("NX_MCP_REAL_NX", "1")
        monkeypatch.setenv("NX_MCP_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("NX_MCP_REAL_NX_RUN_PREFIX", "line-case")
        session = FakeSession()
        session.Parts.Work = session.Parts.Display = None
        executor = NXOpenExecutor(session, FAKE_NXOPEN, "NX test", Workspace(tmp_path))
        calls = []
        mcp_calls = []

        class Bridge:
            async def call(self, method, params):
                calls.append(method)
                if method == cleanup_error and (method != "nx_status" or calls.count(method) > 1):
                    raise NXToolError("NX_TEST_CLEANUP", "cleanup-failed")
                if method == "nx_create_sketch" and sketch_error:
                    raise NXToolError("NX_TEST_SKETCH", "original-sketch-failed")
                if method == "nx_sketch_line" and no_op_lines:
                    return {
                        "object": {
                            "id": "curve-empty",
                            "kind": "curve",
                            "name": "No geometry",
                            "part_id": self.part_id,
                        }
                    }
                if method == "nx_extrude":
                    # This double rejects an empty/open profile, as the native kernel must.
                    geometry = session.Parts.Work.Sketches.values[-1].geometry
                    if len(geometry) != 4:
                        raise NXToolError("NX_TEST_PROFILE", "No closed line profile")
                response = executor.execute(method, params)
                if method == "nx_create_part":
                    self.part_id = response["part"]["part_id"]
                return response

        bridge = Bridge()
        monkeypatch.setattr(acceptance, "DescriptorBridgeClient", lambda: bridge)

        @asynccontextmanager
        async def client_context(parameters):
            async with Client(create_server(bridge, Workspace(tmp_path))) as client:

                class RecordedClient:
                    async def call_tool(self, name, arguments):
                        mcp_calls.append(name)
                        return await client.call_tool(name, arguments)

                yield RecordedClient()

        monkeypatch.setattr(acceptance, "Client", client_context, raising=False)
        return calls, mcp_calls

    return configure


@pytest.mark.asyncio
async def test_line_acceptance_rejects_metadata_without_geometry(scenario):
    scenario(no_op_lines=True)
    with pytest.raises(Exception) as caught:
        await acceptance.test_real_nx_sketch_line()
    assert _contains_error(caught.value, "No closed line profile")


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_error", ["nx_status", "nx_close_part"])
async def test_line_acceptance_preserves_modeling_failure_when_cleanup_also_fails(
    scenario, cleanup_error, capsys
):
    scenario(sketch_error=True, cleanup_error=cleanup_error)
    with pytest.raises(Exception) as caught:
        await acceptance.test_real_nx_sketch_line()
    assert _contains_error(caught.value, "original-sketch-failed")
    assert not _contains_error(caught.value, "cleanup-failed")
    assert "cleanup" in capsys.readouterr().err.lower()


@pytest.mark.asyncio
async def test_line_acceptance_requires_closed_profile_and_solid_through_mcp(scenario):
    calls, mcp_calls = scenario()
    await acceptance.test_real_nx_sketch_line()
    assert mcp_calls.count("nx_sketch_line") == 4
    assert "nx_extrude" in mcp_calls and "nx_export_step" in mcp_calls
    assert calls.count("nx_close_part") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_error", ["nx_status", "nx_close_part"])
async def test_successful_modeling_still_fails_if_cleanup_fails_without_retry(
    scenario, cleanup_error
):
    calls, _ = scenario(cleanup_error=cleanup_error)
    with pytest.raises(Exception) as caught:
        await acceptance.test_real_nx_sketch_line()
    assert _contains_error(caught.value, "cleanup-failed")
    assert calls.count("nx_close_part") == (1 if cleanup_error == "nx_close_part" else 0)
