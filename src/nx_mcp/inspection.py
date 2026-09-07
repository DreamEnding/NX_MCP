"""Native geometry inspection and viewport export, verified against installed NX APIs."""

from __future__ import annotations

import hashlib
import itertools
import math
import struct
import uuid

from nx_mcp.runtime import NXToolError


def envelope_gap(a, b):
    return math.sqrt(sum(max(a[i] - b[i + 3], b[i] - a[i + 3], 0) ** 2 for i in range(3)))


class InspectionMixin:
    def _view_info(self):
        from nx_mcp.hardened import rows, xyz

        part = self.session.Parts.Display
        if part is None:
            raise NXToolError("NX_NO_DISPLAY_PART", "Open a display part first")
        view = part.ModelingViews.WorkView
        return {
            "part": self._reference(part, "part", part, "Display part"),
            "name": view.Name,
            "rotation": rows(view.Matrix),
            "origin": xyz(view.Origin),
            "absolute_origin": xyz(view.AbsoluteOrigin),
            "scale": view.Scale,
            "coordinate_frame": "display_part",
            "matrix_layout": "row-major 3x3; columns are NX view axes",
            "rendering_style": next(
                (
                    name
                    for name, member in [
                        ("shaded", self.nxopen.View.RenderingStyleType.Shaded),
                        (
                            "studio",
                            getattr(self.nxopen.View.RenderingStyleType, "Studio", object()),
                        ),
                        ("shaded_with_edges", self.nxopen.View.RenderingStyleType.ShadedWithEdges),
                        ("wireframe", self.nxopen.View.RenderingStyleType.StaticWireframe),
                    ]
                    if member == view.RenderingStyle
                ),
                "nx_style_" + str(view.RenderingStyle),
            ),
            "interactive": not self.session.IsBatch,
        }

    def _capture_view(
        self,
        path=None,
        width=1600,
        height=1000,
        background="white",
        style="shaded_with_edges",
        fit=False,
    ):
        import NXOpen.Gateway

        if self.session.IsBatch:
            raise NXToolError(
                "NX_VIEWPORT_UNAVAILABLE",
                "Viewport export requires the interactive NX bridge; no desktop capture fallback",
            )
        if (
            type(width) is not int
            or type(height) is not int
            or not (128 <= width <= 4096 and 128 <= height <= 4096)
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "width and height must be 128–4096 pixels")
        if background not in {"white", "original", "transparent"} or style not in {
            "current",
            "shaded",
            "shaded_with_edges",
            "wireframe",
        }:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported background or rendering style")
        file = (
            self.workspace.ensure_inside(path)
            if path
            else self.workspace.root / "captures" / ("nx-" + uuid.uuid4().hex + ".png")
        )
        if file.suffix.lower() != ".png":
            raise NXToolError("NX_INVALID_ARGUMENT", "Viewport export requires a .png path")
        if file.exists():
            raise NXToolError("NX_FILE_EXISTS", "Choose a new capture path")
        file.parent.mkdir(parents=True, exist_ok=True)
        part = self.session.Parts.Display
        if not part:
            raise NXToolError("NX_NO_DISPLAY_PART", "Open a display part first")
        view = part.ModelingViews.WorkView
        old_style = view.RenderingStyle
        builder = None
        try:
            styles = {
                "shaded": "Shaded",
                "shaded_with_edges": "ShadedWithEdges",
                "wireframe": "StaticWireframe",
            }
            if style != "current":
                view.RenderingStyle = getattr(self.nxopen.View.RenderingStyleType, styles[style])
            if fit:
                view.Fit()
            view.UpdateDisplay()
            camera = self._view_info()
            builder = part.Views.CreateImageExportBuilder()
            builder.FileName = str(file)
            builder.FileFormat = NXOpen.Gateway.ImageExportBuilder.FileFormats.Png
            builder.RegionMode = False
            builder.DeviceWidth = width
            builder.DeviceHeight = height
            backgrounds = NXOpen.Gateway.ImageExportBuilder.BackgroundOptions
            builder.BackgroundOption = {
                "white": backgrounds.CustomColor,
                "original": backgrounds.Original,
                "transparent": backgrounds.Transparent,
            }[background]
            if background == "white":
                builder.SetCustomBackgroundColor([1.0, 1.0, 1.0])
            builder.EnhanceEdges = True
            builder.Commit()
        finally:
            try:
                if builder:
                    builder.Destroy()
            finally:
                view.RenderingStyle = old_style
        data = file.read_bytes()
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise NXToolError("NX_CAPTURE_FAILED", "NX did not produce a valid PNG")
        resolution = list(struct.unpack(">II", data[16:24]))
        return {
            "path": str(file),
            "artifact_path": str(file.relative_to(self.workspace.root)),
            "capture_kind": "nx_model_viewport",
            "model_preview": True,
            "resolution": resolution,
            "requested_resolution": [width, height],
            "camera": camera,
            "background": background,
            "rendering": "NX viewport rasterization",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "warnings": (
                []
                if resolution == [width, height]
                else ["NX returned a different resolution than requested"]
            ),
        }

    def _interference_pair(self, a, b):
        import NXOpen.GeometricAnalysis

        part = self._work_part()
        baseline = ([int(x.Tag) for x in part.Bodies], [int(x.Tag) for x in part.Features])
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "NX MCP temporary interference"
        )
        builder = None
        try:
            builder = part.AnalysisManager.CreateSimpleInterferenceObject()
            builder.InterferenceType = (
                NXOpen.GeometricAnalysis.SimpleInterference.InterferenceMethod.InterferenceSolid
            )
            builder.FirstBody.Value = a
            builder.SecondBody.Value = b
            result = builder.PerformCheck()
            enum = NXOpen.GeometricAnalysis.SimpleInterference.Result
            volume = 0.0
            if result == enum.InterferenceExists:
                classification = "penetration"
                units = [
                    part.UnitCollection.FindObject(n)
                    for n in [
                        "SquareMilliMeter",
                        "CubicMilliMeter",
                        "Kilogram",
                        "MilliMeter",
                        "Newton",
                    ]
                ]
                for body in builder.GetInterferenceResults():
                    if not body.IsSolidBody:
                        continue
                    props = part.MeasureManager.NewMassProperties(units, 0.999, [body])
                    try:
                        props.InformationUnit = (
                            self.nxopen.MeasureBodies.AnalysisUnit.KilogramMillimeter
                        )
                        volume += float(props.Volume)
                    finally:
                        props.Dispose()
            elif result == enum.OnlyEdgesOrFacesInterfere:
                classification = "contact"
            elif result == enum.NoInterference:
                classification = "clear"
            else:
                raise NXToolError(
                    "NX_INTERFERENCE_UNRESOLVED", "Native NX could not classify this pair"
                )
            return {"classification": classification, "interference_volume_mm3": volume}
        finally:
            try:
                if builder:
                    try:
                        builder.Reset()
                    finally:
                        builder.Destroy()
            finally:
                try:
                    self.session.UndoToMark(mark, None)
                    self.session.DeleteUndoMark(mark, None)
                    after = ([int(x.Tag) for x in part.Bodies], [int(x.Tag) for x in part.Features])
                    if after != baseline:
                        raise RuntimeError("Temporary interference geometry remains")
                except Exception as exc:
                    raise NXToolError(
                        "NX_ROLLBACK_FAILED",
                        "Interference cleanup failed: " + str(exc),
                        details={"mutation_outcome": "partial"},
                    ) from exc

    def _check_clearance(
        self, objects=None, minimum_clearance=0.0, max_pairs=1000, include_clear=False
    ):

        if (
            not math.isfinite(minimum_clearance)
            or minimum_clearance < 0
            or type(max_pairs) is not int
            or not 1 <= max_pairs <= 10000
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Require nonnegative finite clearance and 1–10000 max_pairs"
            )
        if objects is not None and (not isinstance(objects, list) or len(objects) < 2):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Specify at least two references or omit objects for the assembly",
            )
        # Flatten selected groups, deduplicate occurrences, then inspect each distinct body pair once.
        bodies = (
            list({int(b.Tag): b for ref in objects for b in self._geometry(ref)}.values())
            if objects
            else self._geometry(scope="assembly")
        )
        if any(not b.IsSolidBody for b in bodies):
            raise NXToolError("NX_NOT_SOLID", "Clearance requires solid bodies")
        return self._analyze_pairs(
            bodies,
            list(itertools.combinations(bodies, 2)),
            minimum_clearance,
            max_pairs,
            include_clear,
        )

    def _analyze_pairs(self, bodies, pairs, minimum_clearance, max_pairs, include_clear):
        import NXOpen.UF

        from nx_mcp.hardened import xyz

        n = len(pairs)
        if n > max_pairs:
            raise NXToolError(
                "NX_PAIR_LIMIT",
                f"{n} pairs exceed max_pairs={max_pairs}; select smaller groups or raise the explicit limit",
            )
        part = self._work_part()
        uf = NXOpen.UF.UFSession.GetUFSession()
        boxes = {int(b.Tag): list(uf.ModlGeneral.AskBoundingBox(b.Tag)) for b in bodies}
        reports = []
        skipped = 0
        counts = {"penetration": 0, "contact": 0, "below_clearance": 0, "clear": 0}
        for a, b in pairs:
            gap = envelope_gap(boxes[int(a.Tag)], boxes[int(b.Tag)])
            if gap > minimum_clearance + 1e-7 and not include_clear:
                skipped += 1
                counts["clear"] += 1
                continue
            distance, p1, p2, accuracy = self.session.Measurement.GetMinimumDistance(a, b)
            hit = (
                self._interference_pair(a, b)
                if distance <= 1e-7
                else {"classification": "clear", "interference_volume_mm3": 0.0}
            )
            if hit["classification"] == "clear" and distance < minimum_clearance:
                hit["classification"] = "below_clearance"
            counts[hit["classification"]] += 1
            if hit["classification"] != "clear" or include_clear:
                reports.append(
                    {
                        **hit,
                        "objects": [
                            self._reference(x, "body", part, "Body occurrence") for x in [a, b]
                        ],
                        "distance": distance,
                        "closest_points": [xyz(p1), xyz(p2)],
                        "accuracy": None,
                        "accuracy_note": "No validated numerical error bound is exposed by this NX binding",
                        "method": "NX minimum distance + native solid interference"
                        if distance <= 1e-7
                        else "NX minimum distance",
                    }
                )
        return {
            "pairs": reports,
            "counts": counts,
            "body_count": len(bodies),
            "pair_count": n,
            "reported_pair_count": len(reports),
            "broad_phase_clear_pairs": skipped,
            "minimum_clearance": minimum_clearance,
            "units": self._units(),
            "volume_units": "mm^3",
            "coordinate_frame": "work_part",
            "complete": True,
            "semantics": "Pairwise body occurrence checks; interference volumes are not a geometric union",
            "warnings": [
                "Broad-phase clear pairs use conservative bounding separation; reported distances use native geometry."
            ],
        }

    def _check_interference(self, obj1, obj2):
        a = self._geometry(obj1)
        b = self._geometry(obj2)
        if any(not x.IsSolidBody for x in a + b):
            raise NXToolError("NX_NOT_SOLID", "Interference requires solid bodies")
        unique = {
            tuple(sorted((int(x.Tag), int(y.Tag)))): (x, y) for x in a for y in b if x.Tag != y.Tag
        }
        if not unique:
            raise NXToolError("NX_INVALID_ARGUMENT", "References resolve only to the same body")
        bodies = list({int(x.Tag): x for x in a + b}.values())
        result = self._analyze_pairs(bodies, list(unique.values()), 0.0, 10000, True)
        result["selected_references"] = [obj1, obj2]
        return result
