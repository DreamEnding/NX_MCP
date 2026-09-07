"""Freeform and direct-face features using NX's associative native builders."""

from __future__ import annotations

from nx_mcp.authoring import finite
from nx_mcp.runtime import NXToolError
from nx_mcp.visual_tools import unit_normal


def points3(values, minimum=1, maximum=1000):
    if not isinstance(values, list) or not minimum <= len(values) <= maximum:
        raise NXToolError("NX_INVALID_ARGUMENT", f"Expected {minimum}..{maximum} points")
    result = []
    for value in values:
        if not isinstance(value, list) or len(value) != 3:
            raise NXToolError("NX_INVALID_ARGUMENT", "Each point must contain x, y, z")
        result.append([finite(v, "coordinate") for v in value])
    return result


class FreeformMixin:
    def _freeform_refs(self, refs, kind):
        if not isinstance(refs, list) or not 1 <= len(refs) <= 1000 or len(set(refs)) != len(refs):
            raise NXToolError("NX_INVALID_ARGUMENT", "Select 1..1000 distinct objects")
        return [self._engineering_owned(r, kind) for r in refs]

    def _freeform_builder(self, name, feature=None):
        part = self._work_part()
        self._require_api(part.Features, name)
        factory = getattr(part.Features, name)
        obj = self._engineering_owned(feature, "feature") if feature else None
        return factory(obj)

    def _freeform_commit(self, builder):
        if not builder.Validate():
            raise NXToolError("NX_INVALID_GEOMETRY", "NX rejected the feature inputs")
        feature = builder.CommitFeature()
        result = self._engineering_result(feature)
        result["feature_type"] = feature.FeatureType
        return result

    def _spline(self, points, degree=3, method="through_points", periodic=False, feature=None):
        import NXOpen.Features as F

        values = points3(points, 2)
        if isinstance(degree, bool) or not isinstance(degree, int) or not 1 <= degree <= 7:
            raise NXToolError("NX_INVALID_ARGUMENT", "degree must be an integer from 1 to 7")
        if len(values) <= degree or method not in {"through_points", "poles"}:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Need more points than degree and a supported method"
            )
        b = self._freeform_builder("CreateStudioSplineBuilderEx", feature)
        try:
            b.IsAssociative = True
            b.HasPlaneConstraint = False
            b.Degree = degree
            b.IsPeriodic = periodic
            b.Type = getattr(
                F.StudioSplineBuilderEx.Types,
                "ThroughPoints" if method == "through_points" else "ByPoles",
            )
            manager = b.ConstraintManager
            manager.Clear()
            for coords in values:
                c = manager.CreateGeometricConstraintData()
                c.Point = self._work_part().Points.CreatePoint(self.nxopen.Point3d(*coords))
                manager.Append(c)
            result = self._freeform_commit(b)
            spline = b.Curve
            result["curve"] = self._reference(spline, "curve", self._work_part(), "Spline")
            result["degree"] = spline.Order - 1
            result["periodic"] = spline.Periodic
            return result
        finally:
            b.Destroy()

    def _curve_section(self, refs):
        part = self._work_part()
        objects = self._freeform_refs(refs, "curve")
        section = part.Sections.CreateSection(0.001, 0.001, 0.5)
        section.SetAllowedEntityTypes(self.nxopen.Section.AllowTypes.OnlyCurves)
        rule = part.ScRuleFactory.CreateRuleCurveDumb(objects)
        section.AddToSection(
            [rule],
            objects[0],
            None,
            None,
            self.nxopen.Point3d(0.0, 0.0, 0.0),
            self.nxopen.Section.Mode.Create,
            False,
        )
        return section

    def _surface_mesh(self, primary, cross, tolerance=0.001):
        import NXOpen.Features as F

        tolerance = finite(tolerance, "tolerance", True)
        for sections in (primary, cross):
            if not isinstance(sections, list) or not 2 <= len(sections) <= 100:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Need 2..100 sections in each mesh direction"
                )
            for refs in sections:
                self._freeform_refs(refs, "curve")
        b = self._freeform_builder("CreateThroughCurveMeshBuilder")
        try:
            b.BodyPreference = F.ThroughCurveMeshBuilder.BodyPreferenceTypes.Sheet
            b.PositionTolerance = tolerance
            b.IntersectionTolerance = tolerance
            for items, target in [(primary, b.PrimaryCurvesList), (cross, b.CrossCurvesList)]:
                for refs in items:
                    target.Append(self._curve_section(refs))
            return self._freeform_commit(b)
        finally:
            b.Destroy()

    def _bridge_surface(self, first, second, continuity="G0", reverse_second=False):
        import NXOpen.Features as F
        import NXOpen.GeometricUtilities as G

        if continuity not in {"G0", "G1", "G2"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "continuity must be G0, G1, or G2")
        edges = [self._engineering_owned(r, "edge") for r in (first, second)]
        b = self._freeform_builder("CreateBridgeSurfaceBuilder")
        try:
            b.FirstEndObjectType = F.BridgeSurfaceBuilder.EndObjectType.Edge
            b.SecondEndObjectType = F.BridgeSurfaceBuilder.EndObjectType.Edge
            b.FirstEdgeSelection.Value = edges[0]
            b.SecondEdgeSelection.Value = edges[1]
            b.FirstEdgeContinuity.ContinuityType = getattr(G.Continuity.ContinuityTypes, continuity)
            b.SecondEdgeContinuity.ContinuityType = getattr(
                G.Continuity.ContinuityTypes, continuity
            )
            b.IsSecondEdgeReversed = reverse_second
            b.IsFirstEdgeLimitEndToEnd = True
            b.IsSecondEdgeLimitEndToEnd = True
            return self._freeform_commit(b)
        finally:
            b.Destroy()

    def _sew(self, target, tools, tolerance=0.001, solid=False):
        import NXOpen.Features as F

        targets = self._freeform_refs([target], "body")
        others = self._freeform_refs(tools, "body")
        if target in tools:
            raise NXToolError("NX_INVALID_ARGUMENT", "Target cannot also be a tool")
        tolerance = finite(tolerance, "tolerance", True)
        b = self._freeform_builder("CreateSewBuilder")
        try:
            b.Type = F.SewBuilder.Types.Sheet
            b.BodyPreference = getattr(
                F.SewBuilder.BodyPreferenceTypes, "Solid" if solid else "Sheet"
            )
            b.Tolerance = tolerance
            for collector, bodies in [
                (b.TargetBodiesCollector, targets),
                (b.ToolBodiesCollector, others),
            ]:
                collector.ReplaceRules(
                    [self._work_part().ScRuleFactory.CreateRuleBodyDumb(bodies)], False
                )
            result = self._freeform_commit(b)
            unsewn = b.GetUnsewnBodies()
            if unsewn:
                raise NXToolError(
                    "NX_INCOMPLETE_SEW",
                    "Some input bodies could not be sewn; operation rolled back",
                )
            if solid and any(
                not self._resolve(x["id"], {"body"}).IsSolidBody for x in result["bodies"]
            ):
                raise NXToolError(
                    "NX_INCOMPLETE_SEW", "NX produced a sheet instead of the requested solid"
                )
            return result
        finally:
            b.Destroy()

    def _thicken(self, faces, first_offset, second_offset=0.0):
        objects = self._freeform_refs(faces, "face")
        first = finite(first_offset, "first_offset")
        second = finite(second_offset, "second_offset")
        if first == second:
            raise NXToolError("NX_INVALID_ARGUMENT", "Offsets must differ")
        b = self._freeform_builder("CreateThickenBuilder")
        try:
            b.FaceCollector.ReplaceRules(
                [self._work_part().ScRuleFactory.CreateRuleFaceDumb(objects)], False
            )
            b.Tolerance = 0.001
            b.FirstOffset.RightHandSide = str(first)
            b.SecondOffset.RightHandSide = str(second)
            return self._freeform_commit(b)
        finally:
            b.Destroy()

    def _edit_faces(self, faces, action, distance=None, direction=None, replacement=None):
        import NXOpen.Features as F

        objects = self._freeform_refs(faces, "face")
        factories = {
            "move": "CreateAdmMoveFaceBuilder",
            "offset": "CreateOffsetFaceBuilder",
            "replace": "CreateReplaceFaceBuilder",
            "heal": "CreateDeleteFaceBuilder",
        }
        if action not in factories:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported face edit")
        if (
            (distance is not None) != (action in {"move", "offset"})
            or (direction is not None) != (action == "move")
            or (replacement is not None) != (action == "replace")
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "move requires distance/direction; offset distance; replace replacement; heal no extra parameters",
            )
        if distance is not None:
            distance = finite(distance, "distance")
        if direction is not None:
            direction = unit_normal(direction)
        replacing = self._engineering_owned(replacement, "face") if replacement else None
        b = self._freeform_builder(factories[action])
        try:
            rule = self._work_part().ScRuleFactory.CreateRuleFaceDumb(objects)
            if action == "move":
                import NXOpen.GeometricUtilities as G

                b.FaceToMove.FaceCollector.ReplaceRules([rule], False)
                b.Motion.Option = G.ModlMotion.Options.Distance
                b.Motion.DistanceVector = self._engineering_direction(direction)
                b.Motion.DistanceValue.RightHandSide = str(distance)
            elif action == "offset":
                b.FaceCollector.ReplaceRules([rule], False)
                b.Distance.RightHandSide = str(abs(distance))
                b.Direction = distance < 0
            elif action == "replace":
                b.ReplaceFaces.ReplaceRules([rule], False)
                b.ReplacementFaces.ReplaceRules(
                    [self._work_part().ScRuleFactory.CreateRuleFaceDumb([replacing])], False
                )
            else:
                b.Type = F.DeleteFaceBuilder.SelectTypes.Face
                b.FaceCollector.ReplaceRules([rule], False)
                b.Heal = True
                b.AllowPartialDelete = False
            result = self._freeform_commit(b)
            health = self._model_health()
            if not health["healthy"]:
                raise NXToolError(
                    "NX_INVALID_GEOMETRY",
                    "Face edit left native geometry errors",
                    details={"health": health},
                )
            result["repair_diagnostics"] = {
                "action": action,
                "source_faces": faces,
                "health": health,
            }
            return result
        except Exception as error:
            if isinstance(error, NXToolError):
                error.details.update(action=action, source_faces=faces)
                raise
            raise NXToolError(
                "NX_FACE_EDIT_FAILED",
                str(error),
                nx_code=getattr(error, "ErrorCode", None),
                details={"action": action, "source_faces": faces},
                suggestion="Inspect native diagnostics and explicitly refine the face selection; reacquire IDs after rollback.",
            ) from error
        finally:
            b.Destroy()

    def _curve_analysis(self, curve, samples=21):
        import math

        import NXOpen.UF as U

        from nx_mcp.hardened import cross, dot

        if type(samples) is not int or not 2 <= samples <= 1000:
            raise NXToolError("NX_INVALID_ARGUMENT", "samples must be 2..1000")
        obj = self._resolve(curve, {"curve"})
        if obj.IsOccurrence or obj.OwningPart != self._work_part():
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Select owned work-part geometry")
        uf = U.UFSession.GetUFSession()
        values = []
        for i in range(samples):
            t = i / (samples - 1)
            data = uf.ModlGeneral.EvaluateCurve(obj.Tag, t, 2)
            point, first, second = data[:3], data[3:6], data[6:9]
            speed = math.sqrt(dot(first, first))
            if speed <= 1e-12:
                values.append(
                    {
                        "parameter": t,
                        "point": point,
                        "singular": True,
                        "curvature": None,
                        "tangent": None,
                    }
                )
                continue
            product = cross(first, second)
            curvature = math.sqrt(dot(product, product)) / speed**3
            values.append(
                {
                    "parameter": t,
                    "point": point,
                    "singular": False,
                    "curvature": curvature,
                    "radius": 1 / curvature if curvature > 1e-12 else None,
                    "tangent": [v / speed for v in first],
                }
            )
        result = {
            "curve": self._reference(
                obj,
                "curve",
                self._work_part(),
                "Curve",
            ),
            "samples": values,
            "sample_count": samples,
            "parameterization": "normalized_native_parameter_0_to_1",
            "units": self._units(),
            "curvature_units": "1/" + self._units(),
            "coordinate_frame": "work_part",
            "method": "native first and second derivatives; sampled, not global extrema",
        }
        if hasattr(obj, "Get3DPoles"):
            from nx_mcp.hardened import xyz

            result["spline"] = {
                "degree": obj.Order - 1,
                "periodic": obj.Periodic,
                "knots": list(obj.GetKnots()),
                "poles": [xyz(p) for p in obj.Get3DPoles()],
            }
        return result

    def _surface_continuity(
        self,
        first,
        second,
        position_tolerance=0.001,
        angle_tolerance=0.1,
        curvature_tolerance=0.01,
        samples=21,
    ):
        import math

        import NXOpen.UF as U

        from nx_mcp.surface_math import continuity_difference, shape_operator

        if type(samples) is not int or not 2 <= samples <= 200:
            raise NXToolError("NX_INVALID_ARGUMENT", "samples must be 2..200 per edge")
        edges = [self._engineering_owned(r, "edge") for r in (first, second)]
        adjacent = [list(edge.GetFaces()) for edge in edges]
        if any(len(faces) != 1 for faces in adjacent):
            raise NXToolError(
                "NX_AMBIGUOUS_GEOMETRY", "Select boundary edges with exactly one adjacent face"
            )
        tolerances = [
            finite(v, "tolerance", True)
            for v in [position_tolerance, angle_tolerance, curvature_tolerance]
        ]
        uf = U.UFSession.GetUFSession()
        self._require_api(U.UFConstants, "UF_MODL_EVAL_DERIV2")
        reports = []
        for side in range(2):
            source, target = edges[side], edges[1 - side]
            face_a, face_b = adjacent[side][0], adjacent[1 - side][0]
            evaluator = uf.Eval.Initialize2(source.Tag)
            limits = uf.Eval.AskLimits(evaluator)
            for i in range(samples):
                t = i / (samples - 1)
                point = uf.Eval.EvaluateUnitVectors(
                    evaluator, limits[0] + t * (limits[1] - limits[0])
                )[0]
                distance, _, closest, _ = uf.Modeling.AskMinimumDist3(
                    2, 0, target.Tag, 1, point, 0, [0.0] * 3
                )
                record = {
                    "source_edge": side,
                    "parameter": t,
                    "point": list(point),
                    "closest_point": list(closest),
                    "gap": distance,
                }
                try:
                    operators = []
                    for face, coords in [(face_a, point), (face_b, closest)]:
                        uv, _ = uf.Modeling.AskFaceParm(face.Tag, coords)
                        data = uf.Modeling.EvaluateFace(
                            face.Tag, U.UFConstants.UF_MODL_EVAL_DERIV2, uv
                        )
                        operators.append(
                            shape_operator(
                                data.SrfDu, data.SrfDv, data.SrfD2u, data.SrfDudv, data.SrfD2v
                            )
                        )
                    angle, curvature = continuity_difference(*operators)
                    if not all(math.isfinite(v) for v in [distance, angle, curvature]):
                        raise NXToolError("NX_SINGULAR_SURFACE", "Nonfinite differential geometry")
                    record.update(
                        {
                            "normal_angle_degrees": angle,
                            "curvature_difference": curvature,
                            "status": "measured",
                        }
                    )
                except NXToolError as error:
                    record.update({"status": "unresolved", "reason": str(error)})
                reports.append(record)
        valid = [r for r in reports if r["status"] == "measured"]
        max_gap = max(r["gap"] for r in reports)
        max_angle = max((r["normal_angle_degrees"] for r in valid), default=None)
        max_curvature = max((r["curvature_difference"] for r in valid), default=None)
        g0 = max_gap <= tolerances[0]
        complete = len(valid) == len(reports)
        g1 = g0 and max_angle <= tolerances[1] if complete else None
        g2 = g1 and max_curvature <= tolerances[2] if complete else None
        return {
            "checks": {"G0": g0, "G1": g1, "G2": g2},
            "maximum_gap": max_gap,
            "maximum_normal_angle_degrees": max_angle,
            "maximum_curvature_difference": max_curvature,
            "samples": reports,
            "sample_count": len(reports),
            "unresolved_count": len(reports) - len(valid),
            "tolerances": {
                "position": tolerances[0],
                "angle_degrees": tolerances[1],
                "curvature": tolerances[2],
            },
            "units": self._units(),
            "curvature_units": "1/" + self._units(),
            "method": "Bidirectional normalized-parameter edge samples; native closest points and surface derivative tensors. G2 compares orientation-aligned 3D shape operators (Frobenius norm). Sampled checks, not a global continuity certificate.",
        }

    def _trim_sheet(self, body, boundaries, region_point, keep=True):
        import NXOpen.Features as F

        target = self._engineering_owned(body, "body")
        if target.IsSolidBody:
            raise NXToolError("NX_NOT_SHEET", "Select a sheet body")
        self._freeform_refs(boundaries, "curve")
        point = points3([region_point])[0]
        b = self._freeform_builder("CreateTrimsheetBuilder")
        try:
            b.TargetBodies.Add(target)
            b.BoundaryObjects.Add(self._curve_section(boundaries))
            b.KeepDiscardMethod = (
                F.TrimSheetBuilder.KeepDiscardOption.Keep
                if keep
                else F.TrimSheetBuilder.KeepDiscardOption.Discard
            )
            b.Tolerance = 0.001
            b.OutputExactGeometry = True
            p = self._work_part().Points.CreatePoint(self.nxopen.Point3d(*point))
            b.Regions.Append(self._work_part().CreateRegionPoint(p, target))
            return self._freeform_commit(b)
        finally:
            b.Destroy()
