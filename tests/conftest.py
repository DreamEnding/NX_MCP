"""Shared test fixtures — NX Open mocks for testing without NX."""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from tests.fakes import rig  # noqa: F401


def create_mock_nxopen_modules() -> dict[str, types.ModuleType]:
    """Create a full mock NXOpen module tree for testing."""
    nxopen = types.ModuleType("NXOpen")

    # Session
    mock_session = MagicMock()
    mock_session.Parts = MagicMock()
    mock_session.Parts.Work = MagicMock()
    mock_session.Parts.Display = MagicMock()
    mock_session.LogFile = MagicMock()
    mock_session.LogFile.WriteLine = MagicMock()
    nxopen.Session = MagicMock()
    nxopen.Session.GetSession = MagicMock(return_value=mock_session)
    nxopen._mock_session = mock_session

    # Part
    mock_work_part = mock_session.Parts.Work
    mock_work_part.Name = "test_part"
    mock_work_part.FullPath = "C:\\test\\test_part.prt"
    mock_work_part.PartUnits = MagicMock()
    mock_work_part.Features = MagicMock()
    mock_work_part.Features.ToArray = MagicMock(return_value=[])
    mock_work_part.Bodies = MagicMock()
    mock_work_part.Bodies.ToArray = MagicMock(return_value=[])
    mock_work_part.Sketches = MagicMock()
    mock_work_part.Curves = MagicMock()

    # UF
    uf = types.ModuleType("NXOpen.UF")
    uf.UFSession = MagicMock()
    uf.UFSession.GetUFSession = MagicMock(return_value=MagicMock())
    nxopen.UF = uf

    # Display
    display = types.ModuleType("NXOpen.Display")
    display.Imaging = MagicMock()
    display.Imaging.FileType = MagicMock()
    nxopen.Display = display

    # View
    view_mod = types.ModuleType("NXOpen.View")
    view_mod.ViewOrientation = MagicMock()
    nxopen.View = view_mod

    # Annotations
    nxopen.Annotations = MagicMock()

    # Assemblies
    nxopen.Assemblies = MagicMock()

    # BasePart
    nxopen.BasePart = MagicMock()

    modules = {
        "NXOpen": nxopen,
        "NXOpen.UF": uf,
        "NXOpen.Display": display,
        "NXOpen.View": view_mod,
    }
    return modules


def _uses_legacy_modules(request: pytest.FixtureRequest) -> bool:
    return request.node.get_closest_marker("legacy") is not None


@pytest.fixture(autouse=True)
def _reset_registry(request: pytest.FixtureRequest):
    """Reset tool registry between tests."""
    if not _uses_legacy_modules(request):
        yield
        return
    from nx_mcp.tools.registry import ToolRegistry

    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


@pytest.fixture(autouse=True)
def _reset_session(request: pytest.FixtureRequest):
    """Reset NX session singleton between tests."""
    if not _uses_legacy_modules(request):
        yield
        return
    from nx_mcp.nx_session import NXSession

    NXSession._instance = None
    yield
    NXSession._instance = None


@pytest.fixture
def mock_nx():
    """Patch NXOpen into sys.modules with full mock tree."""
    modules = create_mock_nxopen_modules()
    with patch.dict(sys.modules, modules):
        yield modules["NXOpen"]


@pytest.fixture
def mock_session(mock_nx):
    """Shortcut: return the mock NX session."""
    return mock_nx._mock_session


@pytest.fixture
def mock_work_part(mock_session):
    """Shortcut: return the mock work part."""
    return mock_session.Parts.Work
