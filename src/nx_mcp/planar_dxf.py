"""Analytic planar DXF export from native NX curves, without model mutation."""

import hashlib
import math
import re
from collections import Counter

from nx_mcp.runtime import NXToolError


def dxf_text(entities):
    """Write an ASCII DXF with explicit millimeter units and layer definitions."""
    rows = []

    def put(*pairs):
        for code, value in pairs:
            rows.extend(
                (str(code), format(value, ".15g") if isinstance(value, float) else str(value))
            )

    put(
        (0, "SECTION"),
        (2, "HEADER"),
        (9, "$ACADVER"),
        (1, "AC1027"),
        (9, "$INSUNITS"),
        (70, 4),
        (9, "$MEASUREMENT"),
        (70, 1),
        (0, "ENDSEC"),
    )
    layers = sorted({e["layer"] for e in entities} | {"0"})
    put(
        (0, "SECTION"),
        (2, "TABLES"),
        (0, "TABLE"),
        (2, "LTYPE"),
        (70, 1),
        (0, "LTYPE"),
        (2, "CONTINUOUS"),
        (70, 0),
        (3, "Solid line"),
        (72, 65),
        (73, 0),
        (40, 0.0),
        (0, "ENDTAB"),
        (0, "TABLE"),
        (2, "LAYER"),
        (70, len(layers)),
    )
    for layer in layers:
        put((0, "LAYER"), (2, layer), (70, 0), (62, 7), (6, "CONTINUOUS"))
    put((0, "ENDTAB"), (0, "ENDSEC"), (0, "SECTION"), (2, "ENTITIES"))
    for e in entities:
        kind = e["type"]
        put(
            (0, kind),
            (100, "AcDbEntity"),
            (8, e["layer"]),
            (100, "AcDbLine" if kind == "LINE" else "AcDbCircle"),
        )
        p = e["start"] if kind == "LINE" else e["center"]
        put((10, p[0]), (20, p[1]), (30, 0.0))
        if kind == "LINE":
            put((11, e["end"][0]), (21, e["end"][1]), (31, 0.0))
        else:
            put((40, e["radius"]))
            if kind == "ARC":
                put((100, "AcDbArc"), (50, e["start_angle"]), (51, e["end_angle"]))
    put((0, "ENDSEC"), (0, "EOF"))
    return "\n".join(rows) + "\n"


class PlanarDxfMixin:
    def _export_planar_dxf(
        self, source, path, origin=None, x_axis=None, y_axis=None, layer="OUTLINE", layers=None
    ):
        import NXOpen.UF as U

        from nx_mcp.hardened import cross, dot, vector
        from nx_mcp.visual_tools import unit_normal

        destination = self.workspace.ensure_inside(path)
        if destination.suffix.lower() != ".dxf" or destination.exists():
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Choose a new .dxf path; existing files are never overwritten",
                details={"mutation_outcome": "not_started"},
            )
        layers = {} if layers is None else layers
        for name in [layer, *layers.values()]:
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT",
                    "DXF layer names require 1..64 ASCII letters, digits, underscore, period or hyphen",
                )
        if (x_axis is None) != (y_axis is None):
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply both output basis axes")
        obj = self._resolve(source, {"sketch", "face"})
        if obj.IsOccurrence or obj.OwningPart != self._work_part():
            raise NXToolError(
                "NX_OBJECT_OWNER_MISMATCH", "Select an owned sketch or face in the work part"
            )
        uf = U.UFSession.GetUFSession()
        if isinstance(obj, self.nxopen.Sketch):
            frame = self._sketch_frame(obj)
            curves = list(obj.GetAllGeometry())
            kind = "curve"
        else:
            typ, point, normal, _, _, _, _ = uf.Modeling.AskFaceData(obj.Tag)
            if typ != 22:
                raise NXToolError("NX_NON_PLANAR", "Select a planar face or planar sketch")
            normal = unit_normal(normal)
            seed = [1, 0, 0] if abs(normal[0]) < 0.9 else [0, 1, 0]
            x = unit_normal([seed[i] - dot(seed, normal) * normal[i] for i in range(3)])
            frame = {
                "origin": list(point),
                "x_axis": x,
                "y_axis": cross(normal, x),
                "normal": normal,
            }
            curves = list(obj.GetEdges())
            kind = "edge"
        if not curves or len(curves) > 20000:
            raise NXToolError("NX_OBJECT_LIMIT", "Select 1..20000 analytic curves")
        o = vector(origin, "origin") if origin is not None else frame["origin"]
        x = vector(x_axis, "x_axis") if x_axis is not None else frame["x_axis"]
        y = vector(y_axis, "y_axis") if y_axis is not None else frame["y_axis"]
        if abs(dot(x, x) - 1) > 1e-8 or abs(dot(y, y) - 1) > 1e-8 or abs(dot(x, y)) > 1e-8:
            raise NXToolError("NX_INVALID_ARGUMENT", "Output basis must be orthonormal")
        normal = cross(x, y)
        if abs(abs(dot(normal, frame["normal"])) - 1) > 1e-8:
            raise NXToolError("NX_NON_PLANAR", "Output basis must lie in the source plane")
        factor = {"mm": 1.0, "inch": 25.4}[self._units()]

        def point2(p):
            delta = [p[i] - o[i] for i in range(3)]
            if abs(dot(delta, normal)) * factor > 1e-6:
                raise NXToolError(
                    "NX_NON_PLANAR", "Source geometry or origin is outside the output plane"
                )
            return [dot(delta, x) * factor, dot(delta, y) * factor]

        entities = []
        used = set()
        for curve in curves:
            ref = self._reference(curve, kind, self._work_part(), "DXF curve")
            used.add(ref["id"])
            evaluator = uf.Eval.Initialize2(curve.Tag)
            limits = uf.Eval.AskLimits(evaluator)
            start = point2(uf.Eval.EvaluateUnitVectors(evaluator, limits[0])[0])
            end = point2(uf.Eval.EvaluateUnitVectors(evaluator, limits[1])[0])
            e = {"source": ref, "layer": layers.get(ref["id"], layer)}
            if uf.Eval.IsLine(evaluator):
                e.update(type="LINE", start=start, end=end)
            elif uf.Eval.IsArc(evaluator):
                arc = uf.Eval.AskArc(evaluator)
                center = point2(arc.Center)
                direction = dot(cross(list(arc.XAxis), list(arc.YAxis)), normal)
                if abs(abs(direction) - 1) > 1e-8:
                    raise NXToolError("NX_NON_PLANAR", "Circular curve is not in the output plane")
                e.update(
                    type="CIRCLE"
                    if abs(abs(limits[1] - limits[0]) - 2 * math.pi) < 1e-8
                    else "ARC",
                    center=center,
                    radius=arc.Radius * factor,
                )
                if e["type"] == "ARC":
                    if direction < 0:
                        start, end = end, start
                    e.update(
                        start_angle=math.degrees(
                            math.atan2(start[1] - center[1], start[0] - center[0])
                        )
                        % 360,
                        end_angle=math.degrees(math.atan2(end[1] - center[1], end[0] - center[0]))
                        % 360,
                    )
            else:
                raise NXToolError(
                    "NX_UNSUPPORTED_GEOMETRY",
                    "Planar DXF preserves lines/arcs/circles only; unsupported curves are never approximated",
                    details={"object": ref, "mutation_outcome": "not_started"},
                )
            entities.append(e)
        if set(layers) - used:
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Layer overrides must reference curves or edges of the selected source",
            )
        raw = dxf_text(entities).encode("ascii")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            try:
                stream.write(raw)
            except OSError:
                stream.close()
                destination.unlink(missing_ok=True)
                raise
        return {
            "path": str(destination),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "units": "mm",
            "scale": 1.0,
            "coordinate_frame": {
                "origin": o,
                "x_axis": x,
                "y_axis": y,
                "normal": normal,
                "source_units": self._units(),
            },
            "entity_count": len(entities),
            "entity_counts": dict(Counter(e["type"] for e in entities)),
            "entities": entities,
            "all_boundary_loops_included": True,
            "warnings": [
                "Exports the selected analytic geometry; does not certify loop validity or fabrication readiness."
            ],
        }
