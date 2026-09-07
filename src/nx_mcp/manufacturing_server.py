"""Native manufacturing detail and bounded, explicitly sampled analysis contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from nx_mcp.schema_types import Field

READ_ONLY = {"nx_face_analysis", "nx_wall_thickness"}
NON_MODEL: set[str] = set()


def nx_thread(
    face: str,
    start_face: str,
    pitch: float,
    major_diameter: float,
    minor_diameter: float,
    length: float,
    angle: float = 60.0,
    detailed: bool = False,
    left_hand: bool = False,
    starts: int = 1,
    reverse: bool = False,
):
    """Create an associative native manual thread on a cylindrical face with an explicit start face on the same body. All lengths use part units; angle is the included profile angle in degrees. Symbolic threads preserve simplified geometry; detailed=True models the thread. Actual cylinder diameter is used for tap-drill/shaft diameter. Specify valid minor/major diameters, pitch, length, handedness and 1..16 starts. No standard or fit class is inferred from these manual dimensions. Returns native internal/external classification and parameter readback."""


def nx_pmi_datum(
    faces: list[str], letter: str, position: list[float], annotation: str | None = None
):
    """Create or edit a native geometry-associated PMI datum feature symbol on work-part faces. letter is 1..3 uppercase letters, position is [x,y,z] in part units. annotation edits an existing datum ID. Uses the current annotation plane. Datum schemes are supplied by the caller, not inferred from manufacturing intent."""


def nx_pmi_fcf(
    faces: list[str],
    characteristic: Literal[
        "Straightness",
        "Flatness",
        "Circularity",
        "Cylindricity",
        "ProfileOfALine",
        "ProfileOfASurface",
        "Angularity",
        "Perpendicularity",
        "Parallelism",
        "Position",
        "Concentricity",
        "Symmetry",
        "CircularRunout",
        "TotalRunout",
        "AxisIntersection",
    ],
    tolerance: float,
    position: list[float],
    datums: list[str] | None = None,
    annotation: str | None = None,
    material: Literal["none", "MMC", "LMC", "RFS"] = "none",
    zone_shape: Literal["none", "diameter", "spherical_diameter", "square"] = "none",
    datum_material: list[Literal["none", "MMC", "LMC", "RFS"]] | None = None,
    projected_height: float | None = None,
    tangent_plane: bool = False,
    free_state: bool = False,
):
    """Create/edit a native single-frame geometry-associated GD&T PMI feature-control frame. Select work-part faces, a positive tolerance in part units, [x,y,z] annotation position and up to three ordered existing datum annotation IDs. Form tolerances reject datum references. Supports explicit tolerance/datum material modifiers, zone shape, projected height, tangent-plane and free-state flags. Omitted modifiers reset to none on editing. Does not claim a complete standards compliance check. annotation edits an existing FCF ID."""


def nx_face_analysis(
    faces: list[str],
    samples_per_axis: int = 3,
    pull_direction: list[float] | None = None,
    minimum_draft: float = 1.0,
):
    """Sample native face normals and principal curvatures on a trimmed UV grid. Optional work-part pull_direction enables signed draft angles: asin(normal dot pull), with positive/negative/below-minimum classifications. minimum_draft is degrees. 1..20 samples per UV axis, at most 10000 total; skips points outside trimmed boundaries. Reports sampled values, not global curvature extrema, mold-release feasibility, or an undercut certification."""


def nx_wall_thickness(
    body: str,
    faces: list[str] | None = None,
    samples_per_axis: int = 3,
    tolerance: float = 0.001,
):
    """Measure a solid's sampled wall thickness using native inward-normal ray intersections. Select an owned work-part solid and optionally its faces. Sample a trimmed UV grid (1..20 per axis, max 10000); start each ray tolerance part-units inside the solid. Returns source/opposite faces and points, sampled min/max, unresolved counts and units. This is first-exit normal thickness, not global minimum or rolling-ball thickness. Thin regions smaller than tolerance require a smaller tolerance."""


def nx_thread_catalog(
    standard: str | None = None, size: str | None = None, offset: int = 0, limit: int = 50
):
    """Query installed NX thread-table choices in place. Without standard returns names; select standard for sizes and exact size for dimensional metadata, method and radial engagement. Paged, 1..200 rows. No catalog file transfer; no invented fit classes. Exact catalog strings are required by nx_standard_thread."""


def nx_standard_thread(
    face: str,
    start_face: str,
    standard: str,
    size: str,
    length: float,
    method: str | None = None,
    radial_engage: str | None = None,
    detailed: bool = False,
    left_hand: bool = False,
    reverse: bool = False,
):
    """Create a native ThreadTable thread from one installed standard/size row. Use nx_thread_catalog to disambiguate method and radial_engage. Select a cylindrical face and same-body start face; length uses part units. Symbolic or detailed, handedness and direction are explicit. Returns native dimensions and actual catalog selection; no manual approximation of a standard thread."""


READ_ONLY.add("nx_thread_catalog")


NON_MODEL.add("nx_export_planar_dxf")
Point3 = Annotated[list[float], Field(min_length=3, max_length=3)]


def nx_export_planar_dxf(
    source: str,
    path: str,
    origin: Point3 | None = None,
    x_axis: Point3 | None = None,
    y_axis: Point3 | None = None,
    layer: str = "OUTLINE",
    layers: dict[str, str] | None = None,
):
    """Export an owned sketch or planar face (all boundary loops, including holes) as analytic LINE/ARC/CIRCLE DXF at 1:1 mm. No model changes or save. Rejects splines and non-planar geometry, never approximates them. Default frame is the sketch basis or a deterministic face basis; response reports the exact frame. Supply both orthonormal axes and an origin in the source plane to choose PCB coordinates, in work-part units. layers maps current source curve/edge IDs to ASCII layer names; unlisted entities use layer. Existing files are rejected. This is separate from native sheet-metal flat-pattern export."""
