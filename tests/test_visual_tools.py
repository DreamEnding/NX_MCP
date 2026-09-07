from types import SimpleNamespace

import pytest

from nx_mcp.runtime import NXToolError
from nx_mcp.visual_tools import VisualToolsMixin, unit_normal


def test_section_normal_rejects_degenerate_or_nonfinite_planes():
    for value in ([0, 0, 0], [float("nan"), 0, 1], [0, 1]):
        with pytest.raises(NXToolError):
            unit_normal(value)
    assert unit_normal([0, 0, 5]) == [0, 0, 1]


def test_restore_preflights_all_references_before_any_mutation():
    host = VisualToolsMixin()
    host._visual_part = lambda: None
    host._work_part = lambda: None
    host._part_id = lambda p: "part-1"
    host._display_snapshots = {
        "snapshot": {
            "part_id": "part-1",
            "records": [
                {"object": {"id": "live"}, "blanked": True},
                {"object": {"id": "stale"}, "blanked": False},
            ],
        }
    }
    changes = []

    def resolve(ref):
        if ref == "stale":
            raise NXToolError("NX_OBJECT_STALE", "closed part")
        return SimpleNamespace(Blank=lambda: changes.append("blank"))

    host._resolve = resolve
    with pytest.raises(NXToolError, match="closed part"):
        host._restore_display("snapshot")
    assert changes == [] and "snapshot" in host._display_snapshots


def test_restore_rejects_out_of_order_changes():
    host = VisualToolsMixin()
    host._visual_part = lambda: None
    host._work_part = lambda: None
    host._part_id = lambda p: "part-1"
    host._display_snapshots = {
        key: {"part_id": "part-1", "records": []} for key in ("first", "second")
    }
    with pytest.raises(NXToolError, match="reverse order"):
        host._restore_display("first")


@pytest.mark.asyncio
async def test_visual_tools_publish_enums_and_native_capture_description(tmp_path):
    from nx_mcp.server import create_server
    from nx_mcp.workspace import Workspace

    server = create_server(SimpleNamespace(), Workspace(tmp_path), enable_experimental=True)
    tools = {t.name: t for t in await server.list_tools()}
    assert len(tools) == 189
    assert tools["nx_set_visibility"].inputSchema["properties"]["mode"]["enum"] == [
        "show",
        "hide",
        "isolate",
    ]
    assert "native" in tools["nx_section_view"].description.lower()
    assert "desktop to PNG" not in tools["nx_screenshot"].description
    assert "inline" in tools["nx_screenshot"].description
    assert tools["nx_sketch_diagnostics"].annotations.readOnlyHint
    assert not tools["nx_set_display"].annotations.readOnlyHint
