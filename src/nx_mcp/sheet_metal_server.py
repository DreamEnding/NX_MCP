"""Public contracts for native sheet-metal operations."""

from __future__ import annotations

from typing import Any, Literal

SheetMetalOperation = Literal[
    "tab",
    "flange",
    "contour_flange",
    "lofted_flange",
    "bend",
    "jog",
    "hem",
    "normal_cutout",
    "bead",
    "dimple",
    "louver",
    "drawn_cutout",
    "gusset",
    "edge_rip",
    "break_corner",
    "closed_corner",
    "three_bend_corner",
    "bridge_bend",
    "bend_taper",
    "resize_bend_angle",
    "resize_bend_radius",
    "resize_neutral_factor",
    "convert",
    "from_solid",
    "flat_solid",
    "flat_pattern",
    "advanced_flange",
    "variational_flange",
    "joggle",
    "lightening_cutout",
    "solid_punch",
    "bulge_relief",
    "unbend",
    "rebend",
]
READ_ONLY = {"nx_sheet_metal_schema", "nx_sheet_metal_info", "nx_sheet_metal_defaults"}
NON_MODEL = {"nx_export_flat_pattern", "nx_sheet_metal_context"}


def nx_create_path_sketch(
    edges: list[str],
    help_point: list[float],
    percent: float = 0,
    orienting_face: str | None = None,
    reverse_normal: bool = False,
    reverse_axis: bool = False,
    name: str | None = None,
):
    """Create and activate a native sketch along a work-part edge path, normal to the path. percent is arc-length percentage 0..100 from the NX section start, not the curve's mathematical parameter. help_point=[x,y,z] anchors section selection in work-part units. Optional orienting_face controls local axes relative to a face. Return the actual origin, basis and normal; add curves in that local frame and finish the sketch. Needed for secondary contour flanges. Finish another active sketch first; work/display parts must match."""


def nx_sheet_metal_schema(operation: SheetMetalOperation | None = None):
    """Discover native sheet-metal operations or the strict creation/edit JSON parameter schema for one operation. Every operation reports its actual native validation status; experimental does not mean geometrically verified. Lengths use work-part units, expression angles degrees, neutral factor is unitless. Use typed geometry IDs from nx_list_topology/nx_find_geometry and finished sketch IDs for sections."""


def nx_sheet_metal_context():
    """Enter modern NX Sheet Metal (UG_APP_SBSM) for the current displayed work part. Finish any active sketch first. Changes the interactive application, not model geometry; inspect checkpoint state afterward. Batch sessions use native builder application context. Sheet-metal feature tools require this context and never silently switch into Modeling."""


def nx_sheet_metal_feature(
    operation: SheetMetalOperation, parameters: dict[str, Any], feature: str | None = None
):
    """Create a native sheet-metal feature or edit an owned feature by typed ID. First call nx_sheet_metal_schema(operation) for exact parameter names, enums and required geometry. Enter nx_sheet_metal_context before authoring interactively. All references must belong to the work part; unknown parameters are rejected. All changes run serially under an NX rollback mark. Returns all resulting bodies and native feature type. Availability and geometric verification vary by operation and installation; see schema status."""


def nx_sheet_metal_info(body: str | None = None, offset: int = 0, limit: int = 50):
    """Inspect sheet-metal bodies, actual native thickness and bend parameters. Omit body to inspect owned work-part bodies. Does not infer manufacturability or constant thickness of arbitrary solids. Returned references use the current part generation."""


def nx_sheet_metal_defaults():
    """Read this part's sheet-metal stock thickness, radius, neutral factor, relief dimensions and native material/tool/bend-definition settings. These are feature creation defaults; existing feature overrides retain their own expressions."""


def nx_set_sheet_metal_defaults(
    parameter_entry: Literal["Value", "MaterialTable", "ToolIdTable"] | None = None,
    thickness: float | None = None,
    bend_radius: float | None = None,
    neutral_factor: float | None = None,
    bend_relief_width: float | None = None,
    bend_relief_depth: float | None = None,
    material: str | None = None,
    tool: str | None = None,
    bend_definition: Literal[
        "NeutralFactorValue",
        "BendTable",
        "BendAllowanceFormula",
        "MaterialTable",
        "ToolTable",
        "BendAllowanceTable",
        "BendDeductionTable",
        "BendDeductionFormula",
        "Din6935Formula",
    ]
    | None = None,
    bend_table: str | None = None,
    bend_allowance_formula: str | None = None,
    bend_deduction_formula: str | None = None,
):
    """Update native part sheet-metal defaults and return read-back. Lengths use work-part units; thickness must be positive and neutral factor 0..1. Material/tool names must exist in installed tables. Bend-table paths must be existing files inside the allowed workspace. Formula strings use NX syntax. Failed native changes roll back."""


def nx_export_flat_pattern(
    flat_pattern: str,
    path: str,
    format: Literal["dxf", "geo"] = "dxf",
    revision: Literal[
        "R12", "R13", "R14", "R2000", "R2004", "R2005", "R2007", "R20102012", "R20132016", "R2018"
    ] = "R2018",
    bend_up: bool = True,
    bend_down: bool = True,
    bend_tangent: bool = False,
    interior_cutout: bool = True,
    interior_feature: bool = False,
    inner_mold: bool = False,
    outer_mold: bool = False,
    added_top: bool = False,
    added_bottom: bool = False,
    tolerance: float = 0.01,
):
    """Export an existing native Flat Pattern feature to a new workspace DXF or Trumpf GEO file. File suffix must match format; existing files are never overwritten. Positive tolerance uses work-part units. Returns actual path, options, units, size and SHA-256 for nx_download_file. Inner/outer mold and DXF revision are DXF-only. This export does not create a flat pattern from a folded body."""


def nx_add_flat_pattern_view(drawing: str, flat_pattern: str, position: list[float] | None = None):
    """Place the native named view belonging to an existing Flat Pattern feature on a drawing sheet. Position is [x,y] in sheet units; default [100,100]. Uses actual developed geometry and bend lines from NX, with the existing flat pattern's settings. Returns a typed drawing-view reference for projection, dimensions and PDF export. Does not copy or move the folded solid."""


def nx_sheet_metal_annotation(
    kind: Literal["body", "bend"],
    body: str,
    position: list[float],
    faces: list[str] | None = None,
    annotation: str | None = None,
    automatic: bool = False,
):
    """Create or refresh native Sheet Metal PMI attached to an owned body or bend faces. Text contains a measured snapshot of actual thickness or bend radius/angle/neutral factor; it does not automatically refresh after geometry edits. Call again with the annotation ID to refresh. kind=bend requires faces from that body; kind=body forbids faces. Position is [x,y,z] in work-part coordinates and units. Returns annotation references, measured data and native text."""
