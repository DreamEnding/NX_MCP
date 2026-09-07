"""Assembly documentation contracts with native associations and artifact delivery."""

from __future__ import annotations

from typing import Literal

READ_ONLY = {"nx_parts_list_info"}
NON_MODEL = {"nx_export_explosion_animation"}


def nx_create_parts_list(
    drawing: str, position: list[float], scope: Literal["leaves", "top_level", "all"] = "leaves"
):
    """Create a native automatically updating BOM on a work-part assembly drawing. position is [x,y] in sheet units. Scope controls assembly traversal. Uses installed native parts-list column defaults and returns their actual evaluated rows and quantities. Repeated instances aggregate according to the native key columns. No prototype part is modified."""


def nx_parts_list_info(parts_list: str):
    """Read a native parts list's evaluated rows, column/row counts and automatic-update setting. parts_list is the typed annotation ID returned by nx_create_parts_list. Reading does not refresh or mutate the table."""


def nx_update_parts_list(parts_list: str):
    """Explicitly update an existing native parts list after assembly changes and return its actual evaluated rows. Preserves native callout associations."""


def nx_parts_list_balloons(parts_list: str, view: str):
    """Ask NX to show native associative parts-list callout balloons in a drawing view, including an exploded view. Returns newly created balloon annotation IDs and count. Native automatic placement and grouping follow the parts-list preferences; inspect the drawing before publishing."""


def nx_explosion_trace(
    explosion: str,
    start_edge: str,
    end_edge: str,
    start_percent: float = 50.0,
    end_percent: float = 50.0,
    start_direction: list[float] | None = None,
    end_direction: list[float] | None = None,
):
    """Create a native automatic explosion trace anchored to two unsuppressed component-occurrence edges. Percentages are 0..100 along edge arc length. Directions are in assembly coordinates and default to +Z; NX infers the trace routing from them. Trace belongs to the named explosion and can appear in its associated drawing views. Native component/edge handles persist; MCP explosion edits/show/animation refresh their exploded endpoints. After manual NX changes, call nx_show_explosion to refresh. Returns trace and endpoint IDs without moving assembled components."""


def nx_export_explosion_animation(
    explosion: str,
    path: str,
    frames: int = 12,
    fps: int = 12,
    width: int = 960,
    height: int = 600,
):
    """Render a self-contained HTML animation from assembled to current exploded poses, with native PNG frames, playback and a scrubber. Requires interactive NX with a modeling view active and an unused workspace .html path. Uses linear translation and quaternion rotation, 2..30 frames, 1..60 fps, dimensions 128..1600. Keeps the current camera fixed: frame the full motion before export. Runs serially on the NX thread and restores model/view state under an explicit temporary undo mark. Returns checksum and per-frame metadata; retrieve with nx_download_file. This is a presentation animation, not a collision-certified disassembly path."""
