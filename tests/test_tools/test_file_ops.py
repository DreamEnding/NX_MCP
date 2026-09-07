"""Tests for file operation tools (8 tools)."""

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
    """Build a self-contained mock NXOpen module tree for file_ops tests."""
    nxopen = types.ModuleType("NXOpen")

    # --- Session ---
    mock_session = MagicMock()
    mock_session.Parts = MagicMock()
    mock_work_part = MagicMock()
    mock_work_part.Name = "test_part"
    mock_work_part.FullPath = "C:\\test\\test_part.prt"
    mock_session.Parts.Work = mock_work_part
    mock_session.Parts.Display = mock_work_part
    nxopen.Session = MagicMock()
    nxopen.Session.GetSession = MagicMock(return_value=mock_session)
    nxopen._mock_session = mock_session

    # --- BasePart enums ---
    base_part = MagicMock()
    base_part.Units = MagicMock()
    base_part.Units.Millimeters = "Millimeters"
    base_part.Units.Inches = "Inches"
    base_part.SaveComponents = MagicMock()
    base_part.SaveComponents.TrueValue = "TrueValue"
    base_part.SaveComponents.FalseValue = "FalseValue"
    base_part.CloseAfterSave = MagicMock()
    base_part.CloseAfterSave.FalseValue = "FalseValue"
    base_part.CloseModified = MagicMock()
    base_part.CloseModified.CloseModified = "CloseModified"
    nxopen.BasePart = base_part

    # --- FileNew ---
    file_new = MagicMock()
    file_new.Apply = MagicMock()
    mock_session.Parts.FileNew = MagicMock(return_value=file_new)

    # --- OpenBaseDisplay ---
    mock_base_part = MagicMock()
    mock_load_status = MagicMock()
    mock_session.Parts.OpenBaseDisplay = MagicMock(return_value=(mock_base_part, mock_load_status))

    # --- DexManager ---
    dex_manager = MagicMock()
    mock_step_creator = MagicMock()
    mock_step_creator.Apply = MagicMock()
    mock_iges_creator = MagicMock()
    mock_iges_creator.Apply = MagicMock()
    mock_stl_creator = MagicMock()
    mock_stl_creator.Apply = MagicMock()
    mock_ps_creator = MagicMock()
    mock_ps_creator.Apply = MagicMock()
    dex_manager.CreateStepCreator = MagicMock(return_value=mock_step_creator)
    dex_manager.CreateIgesCreator = MagicMock(return_value=mock_iges_creator)
    dex_manager.CreateStlCreator = MagicMock(return_value=mock_stl_creator)
    dex_manager.CreateParasolidTranslator = MagicMock(return_value=mock_ps_creator)

    mock_step_importer = MagicMock()
    mock_step_importer.Apply = MagicMock()
    mock_iges_importer = MagicMock()
    mock_iges_importer.Apply = MagicMock()
    mock_ps_importer = MagicMock()
    mock_ps_importer.Apply = MagicMock()
    dex_manager.CreateStepImporter = MagicMock(return_value=mock_step_importer)
    dex_manager.CreateIgesImporter = MagicMock(return_value=mock_iges_importer)
    dex_manager.CreateParasolidImporter = MagicMock(return_value=mock_ps_importer)
    mock_session.DexManager = dex_manager

    # --- CloseAll ---
    mock_session.Parts.CloseAll = MagicMock()

    # --- UF ---
    uf = types.ModuleType("NXOpen.UF")
    uf.UFSession = MagicMock()
    uf.UFSession.GetUFSession = MagicMock(return_value=MagicMock())
    nxopen.UF = uf

    nxopen._file_new = file_new
    nxopen._mock_base_part = mock_base_part
    nxopen._dex_manager = dex_manager
    nxopen._mock_step_creator = mock_step_creator

    modules = {"NXOpen": nxopen, "NXOpen.UF": uf}
    return modules, nxopen, mock_work_part


@pytest.fixture(autouse=True)
def _setup_nx():
    """Patch NXOpen and reset session/registry for each test."""
    modules, nxopen, work_part = _make_mock_nxopen()
    with patch.dict(sys.modules, modules):
        NXSession._instance = None
        ToolRegistry.clear()

        import nx_mcp.tools.file_ops as mod  # noqa: F401

        yield nxopen, work_part

        ToolRegistry.clear()
        NXSession._instance = None


# ---------------------------------------------------------------------------
# nx_create_part
# ---------------------------------------------------------------------------


class TestCreatePart:
    """Test nx_create_part tool."""

    @pytest.mark.asyncio
    async def test_create_part_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_create_part")
        assert handler is not None

        result = await handler(path="C:\\parts\\new.prt", units="mm")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["name"] == "test_part"
        nxopen._file_new.Apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_part_inches(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_create_part")

        result = await handler(path="C:\\parts\\new.prt", units="in")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"


# ---------------------------------------------------------------------------
# nx_open_part
# ---------------------------------------------------------------------------


class TestOpenPart:
    """Test nx_open_part tool."""

    @pytest.mark.asyncio
    async def test_open_part_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_open_part")
        assert handler is not None

        result = await handler(path="C:\\parts\\bracket.prt")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["name"] == "test_part"
        nxopen._mock_session.Parts.OpenBaseDisplay.assert_called_once_with("C:\\parts\\bracket.prt")


# ---------------------------------------------------------------------------
# nx_list_open_parts
# ---------------------------------------------------------------------------


class TestListOpenParts:
    """Tests for nx_list_open_parts tool."""

    @pytest.mark.asyncio
    async def test_list_parts_returns_json(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_list_open_parts")

        mock_part1 = MagicMock()
        mock_part1.Name = "bracket"
        mock_part1.FullPath = r"C:\parts\bracket.prt"

        mock_part2 = MagicMock()
        mock_part2.Name = "shaft"
        mock_part2.FullPath = r"C:\parts\shaft.prt"

        nxopen._mock_session.Parts.ToArray.return_value = [mock_part1, mock_part2]
        nxopen._mock_session.Parts.Work = mock_part1

        result = await handler()
        data = json.loads(result.to_text())

        assert data["status"] == "success"
        assert data["data"]["count"] == 2
        assert data["data"]["parts"][0]["name"] == "bracket"
        assert data["data"]["parts"][0]["is_work"] is True
        assert data["data"]["parts"][1]["is_work"] is False

    @pytest.mark.asyncio
    async def test_list_parts_empty(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_list_open_parts")

        nxopen._mock_session.Parts.ToArray.return_value = []
        nxopen._mock_session.Parts.Work = None

        result = await handler()
        data = json.loads(result.to_text())

        assert data["status"] == "success"
        assert data["data"]["count"] == 0


# ---------------------------------------------------------------------------
# nx_save_part
# ---------------------------------------------------------------------------


class TestSavePart:
    """Tests for nx_save_part tool."""

    @pytest.mark.asyncio
    async def test_save_part_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_save_part")

        result = await handler()
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert "test_part" in parsed["message"]
        work_part.Save.assert_called_once()


# ---------------------------------------------------------------------------
# nx_export_step
# ---------------------------------------------------------------------------


class TestExportStep:
    """Tests for nx_export_step tool."""

    @pytest.mark.asyncio
    async def test_export_invalid_format(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_export_step")

        result = await handler(path=r"C:\out\file.xyz", format="dxf")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"
        assert parsed["error_code"] == "NX_INVALID_PARAMS"
        assert "step" in parsed["suggestion"].lower() or "iges" in parsed["suggestion"].lower()

    @pytest.mark.asyncio
    async def test_export_step_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_export_step")

        result = await handler(path=r"C:\out\bracket.stp", format="step")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["format"] == "step"
        nxopen._mock_step_creator.Apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_export_iges_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_export_step")

        result = await handler(path=r"C:\out\part.igs", format="iges")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["format"] == "iges"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify all 8 file_ops tools are registered."""

    def test_all_tools_registered(self, _setup_nx):
        expected = {
            "nx_create_part",
            "nx_open_part",
            "nx_save_part",
            "nx_save_as",
            "nx_close_part",
            "nx_export_step",
            "nx_import_geometry",
            "nx_list_open_parts",
        }
        registered = set(ToolRegistry.get_tool_names())
        assert expected == registered
