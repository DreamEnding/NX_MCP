"""Opt-in tests of the USD checker, not evidence of CAD conversion."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from examples.validate_step_to_usd import _inspect_usd

pytestmark = pytest.mark.skipif(
    os.environ.get("NX_MCP_USD_CHECKER_TESTS") != "1",
    reason="requires explicit opt-in in the isolated converter environment",
)


@pytest.fixture
def usd():
    import usd_convert_cad  # noqa: F401
    from pxr import Gf, Usd, UsdGeom

    return Gf, Usd, UsdGeom


@pytest.fixture
def block(tmp_path, usd):
    _, Usd, UsdGeom = usd

    def create(name="block.usdc", size=(20, 10, 12.5)):
        stage = Usd.Stage.CreateNew(str(tmp_path / name))
        UsdGeom.SetStageMetersPerUnit(stage, 0.001)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        parent = UsdGeom.Xform.Define(stage, "/Block")
        stage.SetDefaultPrim(parent.GetPrim())
        mesh = UsdGeom.Mesh.Define(stage, "/Block/Mesh")
        x, y, z = size
        mesh.CreatePointsAttr(
            [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, z), (x, 0, z), (x, y, z), (0, y, z)]
        )
        mesh.CreateFaceVertexCountsAttr([4] * 6)
        mesh.CreateFaceVertexIndicesAttr(
            [0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 1, 2, 6, 5, 2, 3, 7, 6, 3, 0, 4, 7]
        )
        return stage, parent, mesh

    return create


def inspect(stage):
    stage.GetRootLayer().Save()
    return _inspect_usd(Path(stage.GetRootLayer().realPath))


def test_checker_reads_real_usd_mesh(block):
    stage, _, _ = block()
    result = inspect(stage)
    assert result["mesh_count"] == 1
    assert result["dimensions_m"] == [0.020, 0.010, 0.0125]
    assert result["dependencies_resolved"] is True


def test_world_transforms_are_applied_and_stale_extents_are_ignored(block, usd):
    Gf, _, _ = usd
    stage, parent, mesh = block(size=(1, 1, 1))
    parent.AddTranslateOp().Set(Gf.Vec3d(100, 200, 300))
    parent.AddScaleOp().Set(Gf.Vec3f(20, 10, 12.5))
    mesh.CreateExtentAttr([(0, 0, 0), (99, 99, 99)])
    assert inspect(stage)["dimensions_m"] == [0.020, 0.010, 0.0125]


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ("missing_unit", "metersPerUnit"),
        ("scale", "dimensions"),
        ("axis", "Z-up"),
        ("invisible", "no nonempty"),
        ("empty", "no nonempty"),
        ("topology", "topology"),
        ("nonfinite", "Nonfinite"),
    ],
)
def test_checker_rejects_invalid_geometry(block, usd, problem, message):
    Gf, _, UsdGeom = usd
    stage, parent, mesh = block()
    if problem == "missing_unit":
        stage.ClearMetadata("metersPerUnit")
    elif problem == "scale":
        UsdGeom.SetStageMetersPerUnit(stage, 1)
    elif problem == "axis":
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    elif problem == "invisible":
        parent.MakeInvisible()
    elif problem == "empty":
        mesh.GetPointsAttr().Set([])
    elif problem == "topology":
        mesh.GetFaceVertexIndicesAttr().Set([999] * 24)
    else:
        points = mesh.GetPointsAttr().Get()
        points[0] = Gf.Vec3f(float("nan"), 0, 0)
        mesh.GetPointsAttr().Set(points)
    with pytest.raises(ValueError, match=message):
        inspect(stage)


@pytest.mark.parametrize("missing", [False, True])
def test_payloads_are_loaded_and_missing_dependencies_fail(block, missing):
    asset, _, _ = block("asset.usdc")
    asset.GetRootLayer().Save()
    stage, parent, _ = block()
    stage.RemovePrim("/Block/Mesh")
    parent.GetPrim().GetPayloads().AddPayload("missing.usdc" if missing else "asset.usdc")
    if missing:
        with pytest.raises(ValueError, match="Unresolved"):
            inspect(stage)
    else:
        assert inspect(stage)["mesh_count"] == 1


def test_instance_proxies_contribute_their_world_space_geometry(block, usd, tmp_path):
    Gf, Usd, UsdGeom = usd
    asset, _, _ = block("half.usdc", size=(10, 10, 12.5))
    asset.GetRootLayer().Save()
    stage = Usd.Stage.CreateNew(str(tmp_path / "instances.usdc"))
    UsdGeom.SetStageMetersPerUnit(stage, 0.001)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    for name, offset in [("A", 0), ("B", 10)]:
        instance = UsdGeom.Xform.Define(stage, "/" + name)
        instance.GetPrim().GetReferences().AddReference("half.usdc")
        instance.GetPrim().SetInstanceable(True)
        instance.AddTranslateOp().Set(Gf.Vec3d(offset, 0, 0))
    assert inspect(stage)["mesh_count"] == 2


def test_unreadable_usd_is_not_accepted(tmp_path, usd):
    from pxr import Tf

    path = tmp_path / "invalid.usdc"
    path.write_bytes(b"not a USD file")
    with pytest.raises(Tf.ErrorException):
        _inspect_usd(path)
