"""NX 2606 native patterns, geometric rules and constrained parameter editing."""

from __future__ import annotations

import contextlib
import math

from nx_mcp.authoring import finite, page
from nx_mcp.runtime import NXToolError
from nx_mcp.visual_tools import enum_name, unit_normal


class AdvancedAuthoringMixin:
    def _geometry_owner_locator(self, owner):
        obj = self._resolve(owner, {"body", "feature", "component"})
        kind = (
            "body"
            if isinstance(obj, self.nxopen.Body)
            else "feature"
            if isinstance(obj, self.nxopen.Features.Feature)
            else "component"
        )
        return self._locator(obj, kind)

    def _resolve_geometry(self, selector, tie_tolerance=0.001):
        tol = finite(tie_tolerance, "tie_tolerance", True)
        fields = {"kind", "geometry_type", "normal", "radius", "near", "order", "axis", "tolerance"}
        if (
            not isinstance(selector, dict)
            or set(selector) != {"version", "owner_part", "owner", "query"}
            or selector["version"] != 1
            or not isinstance(selector["owner_part"], str)
            or not isinstance(selector["query"], dict)
            or set(selector["query"]) != fields
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Use the complete version 1 selector from nx_find_geometry"
            )
        if selector["owner_part"].casefold() != self._work_part().FullPath.casefold():
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Selector belongs to another part")
        owner = selector["owner"]
        if owner is not None:
            if (
                not isinstance(owner, dict)
                or set(owner) != {"kind", "journal_id", "owner_part"}
                or owner["kind"] not in {"body", "feature", "component"}
                or not isinstance(owner["journal_id"], str)
                or not isinstance(owner["owner_part"], str)
            ):
                raise NXToolError("NX_INVALID_ARGUMENT", "Invalid selector owner")
            obj = self._locate(owner)
            owner = self._reference(obj, owner["kind"], self._work_part(), "Owner")["id"]
        result = self._find_geometry(owner=owner, **selector["query"], limit=2)
        if not result["items"]:
            raise NXToolError("NX_NOT_FOUND", "No geometry satisfies the saved rule")
        if (
            len(result["items"]) > 1
            and abs(result["items"][0]["rank_value"] - result["items"][1]["rank_value"]) <= tol
        ):
            raise NXToolError(
                "NX_AMBIGUOUS_REFERENCE",
                "Best geometry candidates are tied; narrow the selection rule",
                details={"candidates": result["items"]},
            )
        return {
            "match": result["items"][0],
            "selector": selector,
            "resolution": "reevaluated geometric rule",
            "units": self._units(),
            "coordinate_frame": "work_part",
        }

    def _recognize_holes(self, owner=None, offset=0, limit=50):
        import NXOpen.UF

        from nx_mcp.hardened import dot

        uf = NXOpen.UF.UFSession.GetUFSession()
        self._require_api(uf.Modeling, "AskFaceUvMinmax")
        rows = []
        start = 0
        while True:
            batch = self._find_geometry(
                owner=owner, geometry_type="cylinder", order="lowest", offset=start, limit=200
            )
            for row in batch["items"]:
                if row["cylindrical_role"] != "bore":
                    continue
                face = self._resolve(row["object"]["id"], {"face"})
                uv = uf.Modeling.AskFaceUvMinmax(face.Tag)
                coverage = abs(uv[1] - uv[0])
                row.update(
                    angular_coverage_radians=coverage,
                    full_circumference=abs(coverage - 2 * math.pi) <= 1e-6,
                )
                rows.append(row)
            if batch["next_offset"] is None:
                break
            start = batch["next_offset"]
        groups = []
        for row in rows:
            axis = unit_normal(row["axis"])
            for group in groups:
                delta = [a - b for a, b in zip(row["axis_point"], group["axis_point"], strict=True)]
                axial = dot(delta, group["axis"])
                radial = math.sqrt(max(0, dot(delta, delta) - axial * axial))
                if abs(dot(axis, group["axis"])) >= 1 - 1e-8 and radial <= 0.001:
                    break
            else:
                group = {
                    "group": len(groups),
                    "axis": axis,
                    "axis_point": row["axis_point"],
                    "faces": [],
                }
                groups.append(group)
            group["faces"].append(row["object"])
            row["coaxial_group"] = group["group"]
        return {
            **page(rows, offset, limit),
            "coaxial_groups": groups,
            "units": self._units(),
            "coordinate_frame": "work_part",
            "recognition": "inward cylindrical BREP faces",
            "limitations": [
                "No blind/through, thread or manufacturing-feature inference; partial faces are reported."
            ],
        }

    def _component_pattern_record(self, pattern):
        import NXOpen.GeometricUtilities

        from nx_mcp.hardened import rows, xyz

        part = self._work_part()
        builder = part.ComponentAssembly.CreateComponentPatternBuilder(pattern)
        try:
            service = builder.PatternService
            linear = (
                service.PatternType
                == NXOpen.GeometricUtilities.PatternDefinition.PatternEnum.Linear
            )
            result = {
                "object": self._reference(pattern, "component_pattern", part, "Component pattern"),
                "native_type": "NXOpen.Assemblies.ComponentPattern",
                "pattern_type": "linear"
                if linear
                else enum_name(service.PatternType, service.PatternEnum),
                "associative": bool(builder.Associative),
            }
            if linear:
                spacing = service.RectangularDefinition.XSpacing
                result.update(
                    count_expression=self._expression_record(spacing.NCopies),
                    spacing_expression=self._expression_record(spacing.PitchDistance),
                    count_includes_seed=True,
                )
            if linear:
                d = service.RectangularDefinition
                result["second_direction_enabled"] = d.UseYDirectionToggle
                if d.UseYDirectionToggle:
                    result["count_y_expression"] = self._expression_record(d.YSpacing.NCopies)
                    result["spacing_y_expression"] = self._expression_record(
                        d.YSpacing.PitchDistance
                    )
            elif service.PatternType == service.PatternEnum.Circular:
                d = service.CircularDefinition
                result["count_expression"] = self._expression_record(d.AngularSpacing.NCopies)
                result["angle_expression"] = self._expression_record(d.AngularSpacing.PitchAngle)
                result["count_includes_seed"] = True
            components = {int(c.Tag): c for c in pattern.GetComponentsToPattern()}
            for member in pattern.GetAllPatternMembers():
                for c in member.GetAllComponents():
                    components[int(c.Tag)] = c
            result["instances"] = []
            for c in components.values():
                p, m = c.GetPosition()
                result["instances"].append(
                    {
                        "object": self._reference(c, "component", part, "Component"),
                        "translation": xyz(p),
                        "rotation_matrix": rows(m),
                        "suppressed": bool(c.IsSuppressed),
                    }
                )
            result["total_instances"] = len(components)
            return result
        finally:
            builder.Destroy()

    def _component_patterns(self, part):
        assembly = part.ComponentAssembly
        if not assembly.RootComponent or not hasattr(assembly, "ComponentPatterns"):
            return []
        self._require_api(assembly.ComponentPatterns, "GetAllComponentPatterns")
        return list(assembly.ComponentPatterns.GetAllComponentPatterns())

    def _list_component_patterns(self):
        assembly = self._work_part().ComponentAssembly
        self._require_api(assembly, "ComponentPatterns", "CreateComponentPatternBuilder")
        patterns = [
            self._component_pattern_record(p) for p in self._component_patterns(self._work_part())
        ]
        return {
            "patterns": patterns,
            "count": len(patterns),
            "units": self._units(),
            "coordinate_frame": "work_part",
        }

    def _pattern_inputs(self, spacing, count):
        if count is not None and (type(count) is not int or not 2 <= count <= 100):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "count must be integer 2–100 including the seed"
            )
        if spacing is not None:
            finite(spacing, "spacing", True)

    def _native_component_pattern(self, component, direction, spacing, count):
        import NXOpen.GeometricUtilities

        self._pattern_inputs(spacing, count)
        axis = unit_normal(direction)
        part = self._work_part()
        seed = self._resolve(component, {"component"})
        if seed.Parent != part.ComponentAssembly.RootComponent or seed.IsSuppressed:
            raise NXToolError(
                "NX_UNSUPPORTED_SCOPE", "Seed must be an unsuppressed immediate child"
            )
        self._require_api(part.ComponentAssembly, "CreateComponentPatternBuilder")
        b = part.ComponentAssembly.CreateComponentPatternBuilder(None)
        try:
            b.Associative = True
            b.ComponentPatternSet.Add(seed)
            b.PatternService.PatternType = (
                NXOpen.GeometricUtilities.PatternDefinition.PatternEnum.Linear
            )
            d = b.PatternService.RectangularDefinition
            d.XDirection = part.Directions.CreateDirection(
                self.nxopen.Point3d(0.0, 0.0, 0.0),
                self.nxopen.Vector3d(*axis),
                self.nxopen.SmartObject.UpdateOption.WithinModeling,
            )
            d.XSpacing.NCopies.RightHandSide = str(count)
            d.XSpacing.PitchDistance.RightHandSide = str(float(spacing))
            d.YSpacing.NCopies.RightHandSide = "1"
            pattern = b.Commit()
        finally:
            b.Destroy()
        self._update_model()
        result = self._component_pattern_record(pattern)
        if result["total_instances"] != count or not result["associative"]:
            raise NXToolError(
                "NX_PATTERN_VERIFICATION_FAILED",
                "Native association or member count differs; rolling back",
            )
        return result

    def _edit_component_pattern(
        self, pattern, spacing=None, count=None, count_y=None, spacing_y=None, angle=None
    ):
        if all(v is None for v in [spacing, count, count_y, spacing_y, angle]):
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply at least one parameter")
        self._pattern_inputs(spacing, count)
        if count_y is not None and (type(count_y) is not int or not 1 <= count_y <= 100):
            raise NXToolError("NX_INVALID_ARGUMENT", "count_y must be 1–100")
        if spacing_y is not None:
            spacing_y = finite(spacing_y, "spacing_y", True)
        if angle is not None:
            angle = finite(angle, "angle", True)
        obj = self._resolve(pattern, {"component_pattern"})
        b = self._work_part().ComponentAssembly.CreateComponentPatternBuilder(obj)
        try:
            service = b.PatternService
            if not b.Associative or len(obj.GetComponentsToPattern()) != 1:
                raise NXToolError(
                    "NX_UNSUPPORTED_EDIT", "Select an associative single-seed pattern"
                )
            if service.PatternType == service.PatternEnum.Linear:
                if angle is not None:
                    raise NXToolError("NX_INVALID_ARGUMENT", "angle requires a circular pattern")
                d = service.RectangularDefinition
                nx = count if count is not None else int(d.XSpacing.NCopies.Value)
                ny = (
                    count_y
                    if count_y is not None
                    else int(d.YSpacing.NCopies.Value)
                    if d.UseYDirectionToggle
                    else 1
                )
                if ny > 1 and not d.UseYDirectionToggle:
                    raise NXToolError(
                        "NX_UNSUPPORTED_EDIT",
                        "Create a two-direction array before increasing its second-direction count",
                    )
                if spacing_y is not None and ny == 1:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", "spacing_y requires a second direction"
                    )
                expected = nx * ny
                if expected > 100:
                    raise NXToolError("NX_INVALID_ARGUMENT", "At most 100 total instances")
                if count is not None:
                    d.XSpacing.NCopies.RightHandSide = str(count)
                if spacing is not None:
                    d.XSpacing.PitchDistance.RightHandSide = str(float(spacing))
                if count_y is not None:
                    d.YSpacing.NCopies.RightHandSide = str(count_y)
                    d.UseYDirectionToggle = count_y > 1
                if spacing_y is not None:
                    d.YSpacing.PitchDistance.RightHandSide = str(spacing_y)
            elif service.PatternType == service.PatternEnum.Circular:
                if any(v is not None for v in [spacing, count_y, spacing_y]):
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", "Circular edits accept count and angle only"
                    )
                d = service.CircularDefinition
                expected = count if count is not None else int(d.AngularSpacing.NCopies.Value)
                pitch = angle if angle is not None else d.AngularSpacing.PitchAngle.Value
                if (expected - 1) * pitch >= 360:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", "Angular positions must not duplicate the seed"
                    )
                if count is not None:
                    d.AngularSpacing.NCopies.RightHandSide = str(count)
                if angle is not None:
                    d.AngularSpacing.PitchAngle.RightHandSide = str(angle)
            else:
                raise NXToolError(
                    "NX_UNSUPPORTED_EDIT", "Only rectangular and circular patterns are supported"
                )
            b.Commit()
        finally:
            b.Destroy()
        self._update_model()
        result = self._component_pattern_record(obj)
        if result["total_instances"] != expected:
            raise NXToolError(
                "NX_PATTERN_VERIFICATION_FAILED", "Native member count differs; rolling back"
            )
        return result

    def _feature_parameters(self, feature):
        f = self._resolve(feature, {"feature"})
        return {
            "feature": self._reference(f, "feature", self._work_part(), "Feature"),
            "feature_type": f.FeatureType,
            "parameters": [self._expression_record(e) for e in f.GetExpressions()],
        }

    def _set_feature_parameters(self, feature, values):
        f = self._resolve(feature, {"feature"})
        if not isinstance(values, dict) or not 1 <= len(values) <= 25:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "values must contain 1–25 expression/formula pairs"
            )
        owned = {int(e.Tag) for e in f.GetExpressions()}
        prepared = []
        seen = set()
        for key, formula in values.items():
            exp = self._expression(key)
            if int(exp.Tag) not in owned:
                raise NXToolError(
                    "NX_OBJECT_OWNER_MISMATCH", "Expression is not owned by this feature"
                )
            if int(exp.Tag) in seen:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Duplicate expression specified by ID and name"
                )
            seen.add(int(exp.Tag))
            if not self._expression_record(exp)["editable"] or exp.Type != "Number":
                raise NXToolError(
                    "NX_EXPRESSION_READ_ONLY",
                    "Only editable local Number expressions are supported",
                )
            if not isinstance(formula, str) or not formula.strip() or len(formula) > 4096:
                raise NXToolError("NX_INVALID_ARGUMENT", "Each formula requires 1–4096 characters")
            prepared.append((exp, formula))
        for exp, formula in prepared:
            self._work_part().Expressions.EditExpression(exp, formula)
        self._update_model()
        return self._feature_parameters(feature)

    @contextlib.contextmanager
    def _editing_sketch(self, sketch):
        active = self.session.ActiveSketch
        if active and active != sketch:
            raise NXToolError("NX_SKETCH_ACTIVE", "Finish the other active sketch")
        try:
            if not active:
                sketch.Activate(self.nxopen.Sketch.ViewReorient.FalseValue)
            yield
        finally:
            if not active and self.session.ActiveSketch == sketch:
                sketch.Deactivate(
                    self.nxopen.Sketch.ViewReorient.FalseValue, self.nxopen.Sketch.UpdateLevel.Model
                )

    def _owned_curve(self, sketch, ref):
        curve = self._resolve(ref, {"curve"})
        if int(curve.Tag) not in {int(c.Tag) for c in sketch.GetAllGeometry()}:
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Curve is not owned by this sketch")
        return curve

    def _sketch_local_point(self, sketch, value):
        if not isinstance(value, list) or len(value) != 2:
            raise NXToolError("NX_INVALID_ARGUMENT", "origin requires local [x,y]")
        x, y = [finite(v, "origin") for v in value]
        frame = self._sketch_frame(sketch)
        return self.nxopen.Point3d(
            *[
                frame["origin"][i] + x * frame["x_axis"][i] + y * frame["y_axis"][i]
                for i in range(3)
            ]
        )

    def _check_sketch_result(self, sketch_id):
        self._update_model()
        diagnostics = self._sketch_diagnostics(sketch_id)
        if diagnostics["solver_status"] in {
            "OverConstrained",
            "InconsistentlyConstrained",
        } or self._explicit_constraint_conflicts(self._resolve(sketch_id, {"sketch"})):
            raise NXToolError(
                "NX_SKETCH_CONFLICT",
                "Constraint edit conflicts with the sketch; rolling back",
                details={"diagnostics": diagnostics},
            )
        return diagnostics

    def _sketch_dimension(self, sketch_id, curve, dimension_type, value, origin, reference=False):
        sketch = self._resolve(sketch_id, {"sketch"})
        obj = self._owned_curve(sketch, curve)
        val = finite(value, "value", True)
        if type(reference) is not bool:
            raise NXToolError("NX_INVALID_ARGUMENT", "reference must be boolean")
        radial = dimension_type in {"radius", "diameter"}
        if dimension_type not in {
            "length",
            "horizontal",
            "vertical",
            "radius",
            "diameter",
        } or not isinstance(obj, self.nxopen.Arc if radial else self.nxopen.Line):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Dimension requires a line or arc of the documented type"
            )
        point = self._sketch_local_point(sketch, origin)
        one = self.nxopen.Sketch.DimensionGeometry()
        one.Geometry = obj
        mode = (
            self.nxopen.Sketch.DimensionOption.CreateAsReference
            if reference
            else self.nxopen.Sketch.DimensionOption.CreateAsDriving
        )
        with self._editing_sketch(sketch):
            if radial:
                fn = (
                    sketch.CreateRadialDimension
                    if dimension_type == "radius"
                    else sketch.CreateDiameterDimension
                )
                constraint = fn(one, point, None, mode)
            else:
                one.AssocType = self.nxopen.Sketch.AssocType.StartPoint
                two = self.nxopen.Sketch.DimensionGeometry()
                two.Geometry = obj
                two.AssocType = self.nxopen.Sketch.AssocType.EndPoint
                dtype = {
                    "length": "ParallelDim",
                    "horizontal": "HorizontalDim",
                    "vertical": "VerticalDim",
                }[dimension_type]
                constraint = sketch.CreateDimension(
                    getattr(self.nxopen.Sketch.ConstraintType, dtype), one, two, point, None, mode
                )
            exp = constraint.AssociatedExpression
            if reference:
                if abs(self._expression_record(exp)["value"] - val) > 0.001:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT",
                        "Reference dimension value must match measured geometry",
                    )
            else:
                self._work_part().Expressions.EditExpression(exp, str(val))
            sketch.Update()
            diagnostics = self._check_sketch_result(sketch_id)
        return {
            "constraint": self._reference(constraint, "constraint", self._work_part(), "Dimension"),
            "expression": self._expression_record(exp),
            "diagnostics": diagnostics,
        }

    def _sketch_relation(self, sketch_id, curve1, curve2, relation, point1=None, point2=None):
        sketch = self._resolve(sketch_id, {"sketch"})
        a, b = self._owned_curve(sketch, curve1), self._owned_curve(sketch, curve2)
        methods = {
            "parallel": "CreateParallelConstraint",
            "perpendicular": "CreatePerpendicularConstraint",
            "equal_length": "CreateEqualLengthConstraint",
            "equal_radius": "CreateEqualRadiusConstraint",
            "concentric": "CreateConcentricConstraint",
            "coincident": "CreateCoincidentConstraint",
        }
        if relation not in methods or int(a.Tag) == int(b.Tag):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Requires a supported relation and two different curves"
            )
        if relation != "coincident" and (point1 is not None or point2 is not None):
            raise NXToolError("NX_INVALID_ARGUMENT", "Point arguments apply only to coincident")
        pair = []
        for curve, point in [(a, point1), (b, point2)]:
            g = self.nxopen.Sketch.ConstraintGeometry()
            g.Geometry = curve
            if relation == "coincident":
                names = {"start": "StartVertex", "end": "EndVertex", "center": "ArcCenter"}
                if (
                    point not in names
                    or not isinstance(curve, (self.nxopen.Line, self.nxopen.Arc))
                    or (point == "center" and not isinstance(curve, self.nxopen.Arc))
                ):
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", "Coincident requires a valid point on each line/arc"
                    )
                self._relation_point(curve, point)
                g.PointType = getattr(self.nxopen.Sketch.ConstraintPointType, names[point])
            elif not isinstance(
                curve,
                self.nxopen.Arc if relation in {"equal_radius", "concentric"} else self.nxopen.Line,
            ):
                raise NXToolError("NX_INVALID_ARGUMENT", "Curves do not support this relation")
            pair.append(g)
        with self._editing_sketch(sketch):
            before = {
                int(c.Tag)
                for c in sketch.GetAllConstraintsOfType(
                    self.nxopen.Sketch.ConstraintClass.Any, self.nxopen.Sketch.ConstraintType.NoCon
                )
            }
            if getattr(sketch, "UsesLegacySolver", True):
                self._require_api(sketch, methods[relation])
                getattr(sketch, methods[relation])(*pair)
            else:
                self._modern_relation(sketch, a, b, relation, point1, point2)
            sketch.Update()
            residual = self._relation_residual(a, b, relation, point1, point2)
            if residual > 1e-6:
                raise NXToolError(
                    "NX_RELATION_UNSATISFIED",
                    "Native relation did not satisfy geometry; rolling back",
                    details={"residual": residual, "relation": relation},
                )
            diagnostics = self._check_sketch_result(sketch_id)
            created = [
                c
                for c in sketch.GetAllConstraintsOfType(
                    self.nxopen.Sketch.ConstraintClass.Any, self.nxopen.Sketch.ConstraintType.NoCon
                )
                if int(c.Tag) not in before
            ]
            after_tags = {
                int(c.Tag)
                for c in sketch.GetAllConstraintsOfType(
                    self.nxopen.Sketch.ConstraintClass.Any, self.nxopen.Sketch.ConstraintType.NoCon
                )
            }
            if not before <= after_tags:
                raise NXToolError(
                    "NX_CONSTRAINT_REMOVED",
                    "Native solver removed an existing constraint; rolling back",
                )
            refs = [
                self._reference(c, "constraint", self._work_part(), "Relation") for c in created
            ]
        return {
            "constraint": refs[0] if refs else None,
            "constraints": refs,
            "created_count": len(refs),
            "diagnostics": diagnostics,
            "geometric_residual": residual,
            "relation_satisfied": True,
        }

    def _relation_point(self, curve, point):
        if isinstance(curve, self.nxopen.Line) and point in {"start", "end"}:
            return curve.StartPoint if point == "start" else curve.EndPoint
        if isinstance(curve, self.nxopen.Arc) and point == "center":
            return curve.CenterPoint
        raise NXToolError(
            "NX_INVALID_ARGUMENT", "Point selection supports line start/end or arc center"
        )

    def _relation_residual(self, a, b, relation, point1=None, point2=None):
        from nx_mcp.hardened import dot, xyz

        if relation == "equal_radius":
            return abs(a.Radius - b.Radius)
        if relation == "concentric":
            return math.dist(xyz(a.CenterPoint), xyz(b.CenterPoint))
        if relation == "coincident":
            return math.dist(
                xyz(self._relation_point(a, point1)), xyz(self._relation_point(b, point2))
            )
        av = [x - y for x, y in zip(xyz(a.EndPoint), xyz(a.StartPoint), strict=True)]
        bv = [x - y for x, y in zip(xyz(b.EndPoint), xyz(b.StartPoint), strict=True)]
        if relation == "equal_length":
            return abs(math.sqrt(dot(av, av)) - math.sqrt(dot(bv, bv)))
        cosine = abs(dot(unit_normal(av), unit_normal(bv)))
        return abs(1 - cosine) if relation == "parallel" else cosine

    def _modern_relation(self, sketch, a, b, relation, point1, point2):
        names = {
            "parallel": "CreateSketchMakeParallelBuilder",
            "perpendicular": "CreateSketchMakePerpendicularBuilder",
            "equal_length": "CreateSketchMakeEqualBuilder",
            "equal_radius": "CreateSketchMakeEqualBuilder",
            "coincident": "CreateSketchMakeCoincidentBuilder",
            "concentric": "CreateSketchMakeCoincidentBuilder",
        }
        sketches = self._work_part().Sketches
        self._require_api(sketches, names[relation])
        builder = getattr(sketches, names[relation])()
        try:
            if relation in {"coincident", "concentric"}:
                if relation == "concentric":
                    point1 = point2 = "center"
                view = self._work_part().ModelingViews.WorkView
                snaps = self.nxopen.InferSnapType.SnapType
                empty = self.nxopen.Point3d(0.0, 0.0, 0.0)
                builder.StationaryObject.SetValue(
                    getattr(snaps, point1.title()),
                    a,
                    view,
                    self._relation_point(a, point1),
                    None,
                    None,
                    empty,
                )
                builder.MotionPoints.Add(
                    getattr(snaps, point2.title()),
                    b,
                    view,
                    self._relation_point(b, point2),
                    None,
                    None,
                    empty,
                )
            else:
                builder.StationaryObject.Value = a
                builder.MotionObjects.Add(b)
            if relation in {"equal_length", "equal_radius"}:
                builder.EqualType = getattr(
                    self.nxopen.SketchMakeEqualBuilder.EqualTypes,
                    "Radius" if relation == "equal_radius" else "Length",
                )
            builder.SetCreateConstraints(True)
            builder.FindRelations()
            builder.Commit()
        finally:
            builder.Destroy()

    def _explicit_constraint_conflicts(self, sketch):
        # NX 2606's modern solver can report UnderConstrained for contradictory
        # persistent legacy relations. Report only directly provable pairs.
        from nx_mcp.hardened import xyz

        pairs = []
        for curve in sketch.GetAllGeometry():
            if (
                not isinstance(curve, self.nxopen.Line)
                or math.dist(xyz(curve.StartPoint), xyz(curve.EndPoint)) <= 0.001
            ):
                continue
            attached = sketch.GetConstraintsForGeometry(
                curve, self.nxopen.Sketch.ConstraintClass.Any
            )
            horizontal = [
                c
                for c in attached
                if enum_name(c.ConstraintType, self.nxopen.Sketch.ConstraintType) == "Horizontal"
            ]
            vertical = [
                c
                for c in attached
                if enum_name(c.ConstraintType, self.nxopen.Sketch.ConstraintType) == "Vertical"
            ]
            for a in horizontal:
                for b in vertical:
                    pairs.append(
                        {
                            "geometry": self._reference(curve, "curve", self._work_part(), "Line"),
                            "constraints": [
                                self._reference(c, "constraint", self._work_part(), "Constraint")
                                for c in (a, b)
                            ],
                            "reason": "A nonzero line cannot be both horizontal and vertical",
                        }
                    )
        return pairs

    @contextlib.contextmanager
    def _temporary_sketch_edit(self, sketch):
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "NX MCP temporary sketch inspection"
        )
        try:
            with self._editing_sketch(sketch):
                yield
        finally:
            try:
                self.session.UndoToMark(mark, None)
                self.session.DeleteUndoMark(mark, None)
            except Exception as exc:
                raise NXToolError(
                    "NX_ROLLBACK_FAILED",
                    "Sketch inspection cleanup failed: " + str(exc),
                    details={"mutation_outcome": "partial"},
                ) from exc

    def _sketch_conflicts(self, sketch_id, max_checks=20):
        if type(max_checks) is not int or not 1 <= max_checks <= 50:
            raise NXToolError("NX_INVALID_ARGUMENT", "max_checks requires 1–50")
        sketch = self._resolve(sketch_id, {"sketch"})
        baseline = self._sketch_diagnostics(sketch_id)
        bad = {"OverConstrained", "InconsistentlyConstrained"}
        explicit = self._explicit_constraint_conflicts(sketch)
        constraints = (
            baseline["constraints"] if baseline["solver_status"] in bad or explicit else []
        )
        trials = []
        with self._temporary_sketch_edit(sketch):
            for row in constraints[:max_checks]:
                obj = self._resolve(row["object"]["id"], {"constraint"})
                mark = self.session.SetUndoMark(
                    self.nxopen.Session.MarkVisibility.Invisible, "NX MCP conflict trial"
                )
                trial = {"constraint": row["object"], "relieves_conflict": False}
                try:
                    self._error_list(sketch.DeleteObjects([obj]))
                    # Entire-sketch evaluation uses its own reversible work-region scope.
                    status = self._sketch_diagnostics(sketch_id)["solver_status"]
                    trial.update(
                        solver_status=status,
                        relieves_conflict=status in {"UnderConstrained", "WellConstrained"}
                        and not self._explicit_constraint_conflicts(sketch),
                    )
                except Exception as exc:
                    trial["error"] = str(exc)
                finally:
                    try:
                        self.session.UndoToMark(mark, None)
                        self.session.DeleteUndoMark(mark, None)
                    except Exception as exc:
                        raise NXToolError(
                            "NX_ROLLBACK_FAILED",
                            "Conflict trial cleanup failed: " + str(exc),
                            details={"mutation_outcome": "partial"},
                        ) from exc
                trials.append(trial)
        return {
            "baseline_status": baseline["solver_status"],
            "explicit_conflict_pairs": explicit,
            "warnings": [
                "Native solver status can omit contradictory persistent legacy relations; explicit pair detection covers horizontal/vertical on a nonzero line only."
            ],
            "trials": trials,
            "checked": len(trials),
            "total_constraints": baseline["constraint_count"],
            "complete": len(trials) == len(constraints) and not any("error" in r for r in trials),
            "single_removal_relief": [r["constraint"] for r in trials if r["relieves_conflict"]],
            "method": "bounded single-removal sensitivity; not a minimal conflicting set",
            "geometry_restored": True,
        }
