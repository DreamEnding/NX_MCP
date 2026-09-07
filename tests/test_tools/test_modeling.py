"""Tests for modeling tools (11 tools)."""

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
    """Build a self-contained mock NXOpen module tree."""
    nxopen = types.ModuleType("NXOpen")

    # --- Session ---
    mock_session = MagicMock()
    mock_session.Parts = MagicMock()
    mock_work_part = MagicMock()
    mock_work_part.Name = "test_part"
    mock_session.Parts.Work = mock_work_part
    nxopen.Session = MagicMock()
    nxopen.Session.GetSession = MagicMock(return_value=mock_session)
    nxopen._mock_session = mock_session

    # --- Features ---
    mock_work_part.Features = MagicMock()
    mock_work_part.Features.ToArray = MagicMock(return_value=[])

    # Builder that records calls and returns a mock feature on commit
    _created_builders: list[MagicMock] = []

    def _create_builder(method_name, *args, **kwargs):
        builder = MagicMock()
        mock_feature = MagicMock()
        mock_feature.Name = f"MockFeature_{len(_created_builders)}"
        builder.Commit = MagicMock(return_value=mock_feature)
        builder.Destroy = MagicMock()
        _created_builders.append(builder)
        return builder

    mock_work_part.Features.CreateExtrudeBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Extrude", *a, **kw)
    )
    mock_work_part.Features.CreateRevolveBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Revolve", *a, **kw)
    )
    mock_work_part.Features.CreateEdgeBlendBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Blend", *a, **kw)
    )
    mock_work_part.Features.CreateChamferBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Chamfer", *a, **kw)
    )
    mock_work_part.Features.CreateHoleBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Hole", *a, **kw)
    )
    mock_work_part.Features.CreateSweepBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Sweep", *a, **kw)
    )
    mock_work_part.Features.CreatePatternBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Pattern", *a, **kw)
    )
    mock_work_part.Features.CreateBooleanBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Boolean", *a, **kw)
    )
    mock_work_part.Features.CreateMirrorBodyBuilder = MagicMock(
        side_effect=lambda *a, **kw: _create_builder("Mirror", *a, **kw)
    )
    mock_work_part.Features.Delete = MagicMock()

    nxopen._created_builders = _created_builders

    # --- Sketches ---
    def _make_named(name):
        obj = MagicMock()
        obj.Name = name
        return obj

    mock_sketches = [_make_named("Circle1"), _make_named("Path1")]
    mock_work_part.Sketches = MagicMock()
    mock_work_part.Sketches.ToArray = MagicMock(return_value=mock_sketches)
    mock_work_part.Sketches.__iter__.side_effect = lambda: iter(mock_sketches)

    # --- Curves ---
    mock_work_part.Curves = MagicMock()
    mock_work_part.Curves.ToArray = MagicMock(return_value=[])

    # --- Vector3d ---
    nxopen.Vector3d = MagicMock(return_value=MagicMock())

    # --- Expression ---
    expr_mod = types.ModuleType("NXOpen.Expression")
    expr_mod.ValueType = MagicMock()
    expr_mod.ValueType.Double = "Double"
    mock_expr = MagicMock()
    expr_mod.Expression = mock_expr
    nxopen.Expression = expr_mod

    # --- Unit ---
    unit_mod = types.ModuleType("NXOpen.Unit")
    unit_mod.CollectionType = MagicMock()
    unit_mod.CollectionType.Millimeter = "Millimeter"
    unit_mod.CollectionType.Degree = "Degree"
    nxopen.Unit = unit_mod

    # --- Feature.BooleanType ---
    feat_mod = types.ModuleType("NXOpen.Feature")
    feat_mod.BooleanType = MagicMock()
    feat_mod.BooleanType.Unite = "Unite"
    feat_mod.BooleanType.Subtract = "Subtract"
    feat_mod.BooleanType.Intersect = "Intersect"
    nxopen.Feature = feat_mod

    # --- Sections ---
    mock_work_part.Sections = MagicMock()

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

        # Import the module so decorators register
        import nx_mcp.tools.modeling as mod  # noqa: F401

        yield nxopen, work_part

        ToolRegistry.clear()
        NXSession._instance = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtrude:
    """Test nx_extrude tool."""

    @pytest.mark.asyncio
    async def test_extrude_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_extrude")
        assert handler is not None

        result = await handler(distance=10.0)
        text = result.to_text()
        parsed = json.loads(text)

        assert parsed["status"] == "success"
        assert parsed["data"]["distance"] == 10.0
        assert parsed["data"]["direction"] == "Z"
        work_part.Features.CreateExtrudeBuilder.assert_called_once()

    @pytest.mark.asyncio
    async def test_extrude_with_boolean(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_extrude")

        result = await handler(distance=5.0, direction="X", boolean="unite")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["direction"] == "X"


class TestBlend:
    """Test nx_blend tool."""

    @pytest.mark.asyncio
    async def test_blend_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_blend")
        assert handler is not None

        result = await handler(edges=["Edge1", "Edge2"], radius=3.0)
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["radius"] == 3.0
        assert parsed["data"]["edges"] == ["Edge1", "Edge2"]
        work_part.Features.CreateEdgeBlendBuilder.assert_called_once()

        # Verify the builder was used — it's stored in _created_builders
        builders = nxopen._created_builders
        assert any(b.Commit.called for b in builders), "Builder.Commit was never called"
        assert any(b.Destroy.called for b in builders), "Builder.Destroy was never called"


class TestBoolean:
    """Test nx_boolean tool."""

    @pytest.mark.asyncio
    async def test_boolean_unite(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_boolean")
        assert handler is not None

        result = await handler(boolean_type="unite", targets=["Body1", "Body2"])
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["boolean_type"] == "unite"
        assert parsed["data"]["targets"] == ["Body1", "Body2"]
        work_part.Features.CreateBooleanBuilder.assert_called_once()

    @pytest.mark.asyncio
    async def test_boolean_invalid_type(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_boolean")

        result = await handler(boolean_type="merge", targets=["Body1"])
        text = result.to_text()
        parsed = json.loads(text)

        assert parsed["status"] == "error"
        assert "merge" in parsed["message"]


class TestDeleteFeature:
    """Test nx_delete_feature tool."""

    @pytest.mark.asyncio
    async def test_delete_found(self, _setup_nx):
        nxopen, work_part = _setup_nx

        # Set up a mock feature to find
        mock_feat = MagicMock()
        mock_feat.Name = "Extrude(1)"
        work_part.Features.ToArray = MagicMock(return_value=[mock_feat])

        handler = ToolRegistry.get_handler("nx_delete_feature")
        result = await handler(name="Extrude(1)")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["deleted"] == "Extrude(1)"
        work_part.Features.Delete.assert_called_once_with(mock_feat)

    @pytest.mark.asyncio
    async def test_delete_not_found(self, _setup_nx):
        nxopen, work_part = _setup_nx

        # Some other features exist
        mock_feat = MagicMock()
        mock_feat.Name = "Extrude(1)"
        work_part.Features.ToArray = MagicMock(return_value=[mock_feat])

        handler = ToolRegistry.get_handler("nx_delete_feature")
        result = await handler(name="Blend(99)")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"
        assert "Blend(99)" in parsed["message"]
        assert "Extrude(1)" in parsed["suggestion"]

    @pytest.mark.asyncio
    async def test_delete_empty_part(self, _setup_nx):
        nxopen, work_part = _setup_nx
        work_part.Features.ToArray = MagicMock(return_value=[])

        handler = ToolRegistry.get_handler("nx_delete_feature")
        result = await handler(name="Anything")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"


class TestRevolve:
    """Test nx_revolve tool."""

    @pytest.mark.asyncio
    async def test_revolve_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_revolve")
        assert handler is not None

        result = await handler(angle=360.0, axis="Z")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["angle"] == 360.0
        assert parsed["data"]["axis"] == "Z"
        work_part.Features.CreateRevolveBuilder.assert_called_once()

    @pytest.mark.asyncio
    async def test_revolve_partial_angle(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_revolve")

        result = await handler(angle=90.0, axis="X", boolean="unite")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["angle"] == 90.0
        assert parsed["data"]["axis"] == "X"

    @pytest.mark.asyncio
    async def test_revolve_invalid_axis(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_revolve")

        result = await handler(axis="W")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"


class TestSweep:
    """Test nx_sweep tool."""

    @pytest.mark.asyncio
    async def test_sweep_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_sweep")
        assert handler is not None

        result = await handler(section="Circle1", guide="Path1")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["section"] == "Circle1"
        assert parsed["data"]["guide"] == "Path1"
        work_part.Features.CreateSweepBuilder.assert_called_once()

        builders = nxopen._created_builders
        assert any(b.Commit.called for b in builders)
        assert any(b.Destroy.called for b in builders)


class TestChamfer:
    """Test nx_chamfer tool."""

    @pytest.mark.asyncio
    async def test_chamfer_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_chamfer")
        assert handler is not None

        result = await handler(edges=["Edge1", "Edge2"], offset=2.0)
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["offset"] == 2.0
        assert parsed["data"]["edges"] == ["Edge1", "Edge2"]
        work_part.Features.CreateChamferBuilder.assert_called_once()

        builders = nxopen._created_builders
        assert any(b.Commit.called for b in builders)
        assert any(b.Destroy.called for b in builders)


class TestHole:
    """Test nx_hole tool."""

    @pytest.mark.asyncio
    async def test_hole_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_hole")
        assert handler is not None

        result = await handler(diameter=10.0, depth=25.0, x=50.0, y=30.0, z=0.0)
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["diameter"] == 10.0
        assert parsed["data"]["depth"] == 25.0
        assert parsed["data"]["location"] == [50.0, 30.0, 0.0]
        work_part.Features.CreateHoleBuilder.assert_called_once()


class TestPattern:
    """Test nx_pattern tool."""

    @pytest.mark.asyncio
    async def test_pattern_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_pattern")
        assert handler is not None

        result = await handler(
            features=["Extrude(1)"],
            pattern_type="linear",
            count=5,
            spacing=20.0,
            direction="X",
        )
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["count"] == 5
        assert parsed["data"]["spacing"] == 20.0
        assert parsed["data"]["features"] == ["Extrude(1)"]
        work_part.Features.CreatePatternBuilder.assert_called_once()

    @pytest.mark.asyncio
    async def test_pattern_invalid_direction(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_pattern")

        result = await handler(
            features=["Extrude(1)"],
            pattern_type="linear",
            count=3,
            spacing=10.0,
            direction="W",
        )
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"


class TestMirrorBody:
    """Test nx_mirror_body tool."""

    @pytest.mark.asyncio
    async def test_mirror_success(self, _setup_nx):
        nxopen, work_part = _setup_nx
        handler = ToolRegistry.get_handler("nx_mirror_body")
        assert handler is not None

        result = await handler(body="Body1", plane="DatumPlane1")
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["body"] == "Body1"
        assert parsed["data"]["plane"] == "DatumPlane1"
        work_part.Features.CreateMirrorBodyBuilder.assert_called_once()

        builders = nxopen._created_builders
        assert any(b.Commit.called for b in builders)
        assert any(b.Destroy.called for b in builders)


class TestEditFeature:
    """Test nx_edit_feature tool."""

    @pytest.mark.asyncio
    async def test_edit_feature_success(self, _setup_nx):
        nxopen, work_part = _setup_nx

        mock_feat = MagicMock()
        mock_feat.Name = "Extrude(1)"
        mock_expr = MagicMock()
        mock_feat.GetExpression = MagicMock(return_value=mock_expr)
        work_part.Features.ToArray = MagicMock(return_value=[mock_feat])

        handler = ToolRegistry.get_handler("nx_edit_feature")
        result = await handler(name="Extrude(1)", params={"p0": 50.0})
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "success"
        assert parsed["data"]["feature"] == "Extrude(1)"
        assert parsed["data"]["updated"] == {"p0": 50.0}
        mock_feat.GetExpression.assert_called_once_with("p0")
        mock_expr.SetFormula.assert_called_once_with("50.0")

    @pytest.mark.asyncio
    async def test_edit_feature_not_found(self, _setup_nx):
        nxopen, work_part = _setup_nx
        work_part.Features.ToArray = MagicMock(return_value=[])

        handler = ToolRegistry.get_handler("nx_edit_feature")
        result = await handler(name="NonExistent", params={"p0": 10.0})
        parsed = json.loads(result.to_text())

        assert parsed["status"] == "error"
        assert "NonExistent" in parsed["message"]


class TestToolRegistration:
    """Verify all 11 tools are registered."""

    def test_all_tools_registered(self, _setup_nx):
        expected = {
            "nx_extrude",
            "nx_revolve",
            "nx_sweep",
            "nx_blend",
            "nx_chamfer",
            "nx_hole",
            "nx_pattern",
            "nx_boolean",
            "nx_delete_feature",
            "nx_edit_feature",
            "nx_mirror_body",
        }
        registered = set(ToolRegistry.get_tool_names())
        assert expected == registered
