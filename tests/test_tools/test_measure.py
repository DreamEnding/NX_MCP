"""Tests for measurement tools (3 tools: distance, angle, volume)."""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from nx_mcp.nx_session import NXSession
from nx_mcp.tools.registry import ToolRegistry

pytestmark = [pytest.mark.legacy, pytest.mark.fake_nx]

# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------


def _make_mock_nxopen():
    """Build a self-contained mock NXOpen module tree for measurement tests."""
    nxopen = types.ModuleType("NXOpen")

    # --- Session ---
    mock_session = MagicMock()
    mock_session.Parts = MagicMock()
    mock_work_part = MagicMock()
    mock_work_part.Name = "test_part"
    mock_work_part.FullPath = "C:\\test\\test_part.prt"
    mock_session.Parts.Work = mock_work_part
    nxopen.Session = MagicMock()
    nxopen.Session.GetSession = MagicMock(return_value=mock_session)
    nxopen._mock_session = mock_session

    # --- Mock objects for name resolution ---
    def _make_named(name):
        obj = MagicMock()
        obj.Name = name
        return obj

    mock_features = [
        _make_named(n) for n in ("Edge1", "Edge2", "Face1", "Face2", "PlaneA", "PlaneB")
    ]

    # --- Features ---
    mock_work_part.Features = MagicMock()
    mock_work_part.Features.ToArray = MagicMock(return_value=mock_features)
    mock_work_part.Features.__iter__.side_effect = lambda: iter(mock_features)

    # --- Bodies ---
    mock_work_part.Bodies = MagicMock()
    mock_work_part.Bodies.ToArray = MagicMock(return_value=[])

    # --- Curves ---
    mock_work_part.Curves = MagicMock()
    mock_work_part.Curves.ToArray = MagicMock(return_value=[])

    # --- ModelingViews ---
    mock_work_view = MagicMock()
    mock_work_part.ModelingViews = MagicMock()
    mock_work_part.ModelingViews.WorkView = mock_work_view

    # --- MeasureManager ---
    mock_measure_manager = MagicMock()
    mock_work_part.MeasureManager = mock_measure_manager

    mock_distance_measure = MagicMock()
    mock_distance_measure.Value = 42.5
    mock_measure_manager.NewDistance = MagicMock(return_value=mock_distance_measure)

    mock_angle_measure = MagicMock()
    mock_angle_measure.Value = 90.0
    mock_measure_manager.NewAngle = MagicMock(return_value=mock_angle_measure)

    nxopen._mock_measure_manager = mock_measure_manager
    nxopen._mock_distance_measure = mock_distance_measure
    nxopen._mock_angle_measure = mock_angle_measure

    # --- UF ---
    uf = types.ModuleType("NXOpen.UF")
    uf.UFSession = MagicMock()
    uf.UFSession.GetUFSession = MagicMock(return_value=MagicMock())
    nxopen.UF = uf

    modules = {"NXOpen": nxopen, "NXOpen.UF": uf}
    return modules, nxopen, mock_work_part


@pytest.fixture(autouse=True)
def _setup_nx():
    """Patch NXOpen and reset session/registry for each test."""
    modules, nxopen, work_part = _make_mock_nxopen()
    with patch.dict(sys.modules, modules):
        NXSession._instance = None
        ToolRegistry.clear()

        import nx_mcp.tools.measure as mod  # noqa: F401

        yield nxopen, work_part

        ToolRegistry.clear()
        NXSession._instance = None


# ---------------------------------------------------------------------------
# nx_measure_distance
# ---------------------------------------------------------------------------


class TestMeasureDistance:
    """Tests for nx_measure_distance tool."""

    @pytest.mark.asyncio
    async def test_distance_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_measure_distance")
        assert handler is not None

        result = await handler(obj1="Edge1", obj2="Edge2")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["obj1"] == "Edge1"
        assert parsed["data"]["obj2"] == "Edge2"
        assert parsed["data"]["distance_mm"] == 42.5
        work_part.MeasureManager.NewDistance.assert_called_once()

    @pytest.mark.asyncio
    async def test_distance_no_work_part(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_measure_distance")

        NXSession._instance = MagicMock(spec=NXSession)
        NXSession._instance.is_connected = True
        NXSession._instance.require_work_part.side_effect = RuntimeError("No work part is open.")

        result = await handler(obj1="A", obj2="B")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"


# ---------------------------------------------------------------------------
# nx_measure_angle
# ---------------------------------------------------------------------------


class TestMeasureAngle:
    """Tests for nx_measure_angle tool."""

    @pytest.mark.asyncio
    async def test_angle_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_measure_angle")
        assert handler is not None

        result = await handler(obj1="Face1", obj2="Face2")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["obj1"] == "Face1"
        assert parsed["data"]["obj2"] == "Face2"
        assert parsed["data"]["angle_deg"] == 90.0
        work_part.MeasureManager.NewAngle.assert_called_once()

    @pytest.mark.asyncio
    async def test_angle_custom_value(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_measure_angle")

        nxopen._mock_angle_measure.Value = 45.0

        result = await handler(obj1="PlaneA", obj2="PlaneB")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["angle_deg"] == 45.0


# ---------------------------------------------------------------------------
# nx_measure_volume
# ---------------------------------------------------------------------------


class TestMeasureVolume:
    """Tests for nx_measure_volume tool."""

    @pytest.mark.asyncio
    async def test_volume_all_bodies(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_measure_volume")

        body1 = MagicMock()
        body1.Name = "Block"
        mass_props1 = MagicMock()
        mass_props1.Volume = 50000.0
        body1.GetMassProperties = MagicMock(return_value=mass_props1)

        body2 = MagicMock()
        body2.Name = "Cylinder"
        mass_props2 = MagicMock()
        mass_props2.Volume = 30000.0
        body2.GetMassProperties = MagicMock(return_value=mass_props2)

        work_part.Bodies.ToArray = MagicMock(return_value=[body1, body2])

        result = await handler()
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert len(parsed["data"]["bodies"]) == 2
        assert parsed["data"]["bodies"][0]["body"] == "Block"
        assert parsed["data"]["bodies"][0]["volume_mm3"] == 50000.0
        assert parsed["data"]["bodies"][0]["volume_cm3"] == 50.0
        assert parsed["data"]["total_volume_mm3"] == 80000.0

    @pytest.mark.asyncio
    async def test_volume_single_body(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_measure_volume")

        body1 = MagicMock()
        body1.Name = "Block"
        mass_props1 = MagicMock()
        mass_props1.Volume = 25000.0
        body1.GetMassProperties = MagicMock(return_value=mass_props1)

        body2 = MagicMock()
        body2.Name = "Cylinder"
        mass_props2 = MagicMock()
        mass_props2.Volume = 10000.0
        body2.GetMassProperties = MagicMock(return_value=mass_props2)

        work_part.Bodies.ToArray = MagicMock(return_value=[body1, body2])

        result = await handler(body="Block")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert len(parsed["data"]["bodies"]) == 1
        assert parsed["data"]["bodies"][0]["body"] == "Block"
        assert "total_volume_mm3" not in parsed["data"]

    @pytest.mark.asyncio
    async def test_volume_body_not_found(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_measure_volume")

        body1 = MagicMock()
        body1.Name = "Block"
        body1.GetMassProperties = MagicMock(return_value=MagicMock(Volume=1000.0))
        work_part.Bodies.ToArray = MagicMock(return_value=[body1])

        result = await handler(body="Sphere")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"
        assert parsed["error_code"] == "NX_NOT_FOUND"
        assert "Sphere" in parsed["message"]
        assert "Block" in parsed["suggestion"]

    @pytest.mark.asyncio
    async def test_volume_no_bodies(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_measure_volume")

        work_part.Bodies.ToArray = MagicMock(return_value=[])

        result = await handler()
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"
        assert parsed["error_code"] == "NX_NOT_FOUND"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify all 3 measurement tools are registered."""

    def test_all_tools_registered(self, _setup_nx):
        expected = {
            "nx_measure_distance",
            "nx_measure_angle",
            "nx_measure_volume",
        }
        registered = set(ToolRegistry.get_tool_names())
        assert expected == registered
