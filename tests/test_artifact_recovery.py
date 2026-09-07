"""Artifact integrity, interrupted uploads and publication boundaries."""

import base64
import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nx_mcp.integration_server import artifact_call, package_assembly
from nx_mcp.recovery import OperationStore
from nx_mcp.runtime import NXToolError
from nx_mcp.server import create_server
from nx_mcp.workspace import Workspace


def upload_params(**overrides):
    return {
        "path": "fixture.step",
        "offset": 0,
        "total_size": 4,
        "sha256": hashlib.sha256(b"abcd").hexdigest(),
        "data_base64": base64.b64encode(b"ab").decode(),
        **overrides,
    }


@pytest.mark.parametrize(
    "override,code",
    [
        ({"path": "script.exe"}, "NX_UNSUPPORTED_FILE_TYPE"),
        ({"offset": -1}, "NX_INVALID_ARGUMENT"),
        ({"total_size": 300 * 1024 * 1024}, "NX_INVALID_ARGUMENT"),
        ({"sha256": "x" * 64}, "NX_INVALID_ARGUMENT"),
        ({"sha256": "a"}, "NX_INVALID_ARGUMENT"),
        ({"data_base64": base64.b64encode(b"abcde").decode()}, "NX_INVALID_ARGUMENT"),
        ({"offset": 2}, "NX_UPLOAD_GAP"),
    ],
)
def test_upload_rejects_invalid_chunks_without_publishing(tmp_path, override, code):
    with pytest.raises(NXToolError) as error:
        artifact_call("nx_upload_file", upload_params(**override), Workspace(tmp_path))
    assert error.value.code == code
    assert not (tmp_path / "fixture.step").exists()


def test_interrupted_upload_conflict_gap_and_checksum_do_not_publish(tmp_path):
    w = Workspace(tmp_path)
    p = upload_params()
    assert not artifact_call("nx_upload_file", p, w)["committed"]
    for override, code in [
        ({"data_base64": base64.b64encode(b"xx").decode()}, "NX_UPLOAD_CONFLICT"),
        ({"offset": 3, "data_base64": base64.b64encode(b"d").decode()}, "NX_UPLOAD_GAP"),
        ({"offset": 2, "data_base64": base64.b64encode(b"xx").decode()}, "NX_CHECKSUM_MISMATCH"),
    ]:
        with pytest.raises(NXToolError) as error:
            artifact_call("nx_upload_file", dict(p, **override), w)
        assert error.value.code == code and not (tmp_path / "fixture.step").exists()
    # Recovery uses a corrected digest; bytes from an invalid upload cannot be silently reused.
    corrected = b"zzzz"
    q = upload_params(
        sha256=hashlib.sha256(corrected).hexdigest(),
        data_base64=base64.b64encode(corrected).decode(),
    )
    assert artifact_call("nx_upload_file", q, w)["committed"]
    assert (tmp_path / "fixture.step").read_bytes() == corrected


def test_workspace_metadata_and_cancellation_are_scoped(tmp_path):
    w = Workspace(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    store = OperationStore(tmp_path)
    store.put({"operation_id": "batch-operation", "state": "running", "method": "nx_batch"})
    assert artifact_call("nx_cancel_operation", {"operation_id": "batch-operation"}, w)[
        "cancellation_requested"
    ]
    assert store.path("batch-operation").with_suffix(".cancel").exists()
    with pytest.raises(NXToolError):
        artifact_call("nx_cancel_operation", {"operation_id": "not-seen"}, w)
    assert (
        artifact_call("nx_operation_status", {"operation_id": "batch-operation"}, w)["state"]
        == "running"
    )
    listed = artifact_call("nx_workspace_list", {"path": "."}, w)
    assert [v["kind"] for v in listed["entries"]] == ["file", "directory"]
    assert listed["entries"][0]["sha256"] == hashlib.sha256(b"hello").hexdigest()
    for offset, length in [(-1, 1), (0, 0), (0, 262145)]:
        with pytest.raises(NXToolError):
            artifact_call(
                "nx_download_file", {"path": "a.txt", "offset": offset, "length": length}, w
            )


@pytest.mark.asyncio
async def test_package_dependency_manifest_and_no_overwrite(tmp_path):
    files = [tmp_path / "assembly.prt", tmp_path / "prototype.prt"]
    for p in files:
        p.write_text(p.stem)
    part = {"path": str(files[0]), "work": True, "modified": False}
    component = {"part_path": str(files[1]), "translation": [1, 2, 3]}

    async def call(method, params):
        return (
            {"parts": [part]}
            if method == "nx_list_open_parts"
            else {"components": [component, component], "count": 2}
        )

    bridge = AsyncMock()
    bridge.call.side_effect = call
    result = await package_assembly(bridge, Workspace(tmp_path), "assembly.zip")
    assert result["component_instances"] == 2 and result["prototype_files"] == 1
    with zipfile.ZipFile(tmp_path / "assembly.zip") as z:
        manifest = json.loads(z.read("nx-assembly-manifest.json"))
        assert len(manifest["files"]) == 2 and manifest["components"]["count"] == 2
        assert z.read("prototype.prt") == b"prototype"
    before = (tmp_path / "assembly.zip").read_bytes()
    with pytest.raises(FileExistsError):
        await package_assembly(bridge, Workspace(tmp_path), "assembly.zip")
    assert (tmp_path / "assembly.zip").read_bytes() == before
    assert not list((tmp_path / ".nx-mcp").glob("package-*"))
    part["modified"] = True
    with pytest.raises(NXToolError, match="Save referenced"):
        await package_assembly(bridge, Workspace(tmp_path), "dirty.zip")
    part["modified"] = False
    files[1].unlink()
    with pytest.raises(NXToolError):
        await package_assembly(bridge, Workspace(tmp_path), "missing.zip")
    with pytest.raises(NXToolError):
        await package_assembly(bridge, Workspace(tmp_path), "wrong.txt")


@pytest.mark.asyncio
async def test_mcp_paths_are_validated_before_bridge_dispatch(tmp_path):
    bridge = AsyncMock()
    bridge.call.return_value = {"status": "success"}
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True)
    result = await server.call_tool("nx_import_geometry", {"path": "../secret.step"})
    assert result.isError and result.structuredContent["code"] == "NX_PATH_OUTSIDE_WORKSPACE"
    bridge.call.assert_not_called()
    result = await server.call_tool(
        "nx_import_geometry", {"path": "part.step", "target": "new_part", "output_path": "new.prt"}
    )
    assert not result.isError
    params = bridge.call.call_args.args[1]
    assert Path(params["output_path"]) == tmp_path / "new.prt"
    await server.call_tool(
        "nx_batch",
        {"operations": [{"method": "nx_add_component", "params": {"part_path": "p.prt"}}]},
    )
    assert (
        Path(bridge.call.call_args.args[1]["operations"][0]["params"]["part_path"])
        == tmp_path / "p.prt"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["valid", "changed", "large"])
@pytest.mark.parametrize("method", ["nx_screenshot", "nx_render_view"])
async def test_inline_capture_delivery_checks_committed_artifact(tmp_path, kind, method):
    p = tmp_path / "capture.png"
    data = b"png" if kind != "large" else b"x" * (8 * 1024 * 1024 + 1)
    p.write_bytes(data)
    result = {
        "status": "success",
        "path": str(p),
        "sha256": hashlib.sha256(data).hexdigest() if kind != "changed" else "0" * 64,
    }
    bridge = AsyncMock()
    bridge.call.return_value = result
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True)
    response = await server.call_tool(method, {"path": "capture.png"})
    images = [v for v in response.content if v.type == "image"]
    if kind == "valid":
        assert base64.b64decode(images[0].data) == data
    elif kind == "large":
        assert not images and response.structuredContent["warnings"]
    else:
        assert (
            response.isError
            and response.structuredContent["details"]["mutation_outcome"] == "committed"
        )


def test_folder_discovery_creation_and_safe_retry(tmp_path):
    w = Workspace(tmp_path)
    assert artifact_call("nx_workspace_info", {}, w)["root"] == str(tmp_path)
    destination = tmp_path / "projects" / "controller" / "parts"
    first = artifact_call("nx_create_directory", {"path": str(destination)}, w)
    assert first["created"] and destination.is_dir()
    repeated = artifact_call("nx_create_directory", {"path": "projects/controller/parts"}, w)
    assert not repeated["created"] and repeated["path"] == first["path"]
    (destination / "base.prt").write_bytes(b"fixture")
    with pytest.raises(NXToolError) as error:
        artifact_call("nx_create_directory", {"path": str(destination / "base.prt")}, w)
    assert error.value.code == "NX_DIRECTORY_ERROR"
    assert (destination / "base.prt").read_bytes() == b"fixture"


@pytest.mark.asyncio
async def test_folder_tools_are_exposed_and_absolute_part_paths_are_forwarded(tmp_path):
    bridge = AsyncMock()
    bridge.call.return_value = {"path": str(tmp_path / "projects" / "base.prt")}
    server = create_server(bridge=bridge, workspace=Workspace(tmp_path), enable_experimental=True)
    info = await server.call_tool("nx_workspace_info", {})
    assert info.structuredContent["root"] == str(tmp_path)
    created = await server.call_tool("nx_create_directory", {"path": "projects/parts"})
    assert created.structuredContent["created"]
    destination = str(tmp_path / "projects" / "base.prt")
    opened = await server.call_tool("nx_open_part", {"path": destination})
    assert not opened.isError
    assert bridge.call.call_args.args[1]["path"] == destination


@pytest.mark.asyncio
async def test_inline_image_metadata_paging_and_output_contract(tmp_path):
    import struct

    from mcp.types import ImageContent

    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1, 1)
    (tmp_path / "view.png").write_bytes(png)
    for name in ["a.txt", "b.txt", "c.txt"]:
        (tmp_path / name).write_text("artifact")
    bridge = AsyncMock()
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True)
    tools = {t.name: t for t in await server.list_tools()}
    schema = tools["nx_download_file"].inputSchema["properties"]
    assert schema["length"]["maximum"] == 262144 and schema["offset"]["minimum"] == 0
    assert tools["nx_download_file"].outputSchema["required"] == ["status", "warnings", "units"]
    preview = await server.call_tool("nx_download_file", {"path": "view.png", "delivery": "image"})
    assert not preview.isError and preview.structuredContent["resolution"] == [1, 1]
    assert "data_base64" not in preview.structuredContent
    images = [v for v in preview.content if isinstance(v, ImageContent)]
    assert len(images) == 1 and base64.b64decode(images[0].data) == png
    assert png.hex() not in preview.content[0].text
    meta = await server.call_tool("nx_download_file", {"path": "view.png", "delivery": "metadata"})
    assert meta.structuredContent["size"] == len(png) and len(meta.content) == 1
    page = await server.call_tool("nx_workspace_list", {"limit": 2})
    assert page.structuredContent["count"] == 2 and page.structuredContent["next_offset"] == 2
    second = await server.call_tool("nx_workspace_list", {"offset": 2, "limit": 2})
    assert second.structuredContent["next_offset"] is None
    filtered = await server.call_tool("nx_workspace_list", {"prefix": "v"})
    assert filtered.structuredContent["total_count"] == 1
    error = await server.call_tool("nx_workspace_list", {"path": "view.png"})
    assert error.isError and error.structuredContent["code"] == "NX_NOT_DIRECTORY"
    for args in [
        {"length": 262145},
        {"delivery": "image", "offset": 1},
        {"delivery": "image", "path": "a.txt"},
    ]:
        assert (await server.call_tool("nx_download_file", {"path": "view.png", **args})).isError
    bridge.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_directory_and_boolean_operand_contract(tmp_path):
    bridge = AsyncMock()
    bridge.call.return_value = {"status": "success"}
    server = create_server(bridge, Workspace(tmp_path), enable_experimental=True)
    missing = await server.call_tool("nx_workspace_list", {"path": "missing"})
    assert missing.isError and missing.structuredContent["code"] == "NX_DIRECTORY_NOT_FOUND"
    tools = {t.name: t for t in await server.list_tools()}
    schema = tools["nx_boolean"].inputSchema["properties"]
    assert schema["boolean_type"]["enum"] == ["unite", "subtract", "intersect"]
    assert schema["targets"]["minItems"] == 2
    invalid = await server.call_tool("nx_boolean", {"boolean_type": "subtract", "targets": ["a"]})
    assert invalid.isError
    bridge.call.assert_not_awaited()
    await server.call_tool(
        "nx_boolean", {"boolean_type": "subtract", "targets": ["target", "tool"]}
    )
    assert bridge.call.call_args.args[1]["targets"] == ["target", "tool"]
