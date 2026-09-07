"""Native engineering builders; all calls run in the existing NX transaction dispatcher."""

from __future__ import annotations

import math

from nx_mcp.authoring import finite
from nx_mcp.runtime import NXToolError
from nx_mcp.visual_tools import unit_normal


class EngineeringMixin:
    def _engineering_owned(self, ref, kind):
        obj = self._resolve(ref, {kind})
        if obj.IsOccurrence or obj.OwningPart != self._work_part():
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Select geometry owned by the work part")
        return obj

    def _engineering_section(self, sketch):
        part = self._work_part()
        section = part.Sections.CreateSection()
        options = part.ScRuleFactory.CreateRuleOptions()
        try:
            rule = part.ScRuleFactory.CreateRuleCurveFeature([sketch.Feature], None, options)
        finally:
            options.Dispose()
        section.AddToSection(
            [rule], None, None, None, sketch.Origin, self.nxopen.Section.Mode.Create, False
        )
        return section

    def _engineering_collector(self, objects, kind):
        part = self._work_part()
        factory = getattr(part.ScRuleFactory, "CreateRule" + kind + "Dumb")
        rule = factory(objects)
        collector = part.ScCollectors.CreateCollector()
        collector.ReplaceRules([rule], False)
        return collector

    def _engineering_result(self, feature, modified=()):
        part = self._work_part()
        bodies = [self._reference(b, "body", part, "Body") for b in feature.GetBodies()]
        ref = self._reference(feature, "feature", part, "Feature")
        return {
            "feature": ref,
            "bodies": bodies,
            "body_count": len(bodies),
            "body": bodies[0] if bodies else None,
            "units": self._units(),
            "coordinate_frame": "work_part",
            "created": [ref],
            "modified": [self._reference(b, "body", part, "Body") for b in modified],
        }

    def _engineering_direction(self, direction, origin=(0, 0, 0)):
        return self._work_part().Directions.CreateDirection(
            self.nxopen.Point3d(*map(float, origin)),
            self.nxopen.Vector3d(*unit_normal(direction)),
            self.nxopen.SmartObject.UpdateOption.WithinModeling,
        )

    def _extrude(
        self,
        sketch_id,
        distance=None,
        reverse=False,
        start=0.0,
        end_type="distance",
        symmetric=False,
        direction=None,
        target_face=None,
        boolean="none",
        targets=None,
    ):
        if (
            start == 0
            and end_type == "distance"
            and not symmetric
            and direction is None
            and target_face is None
            and boolean == "none"
            and not targets
        ):
            if distance is None:
                raise NXToolError("NX_INVALID_ARGUMENT", "distance is required")
            return self._simple_extrude(sketch_id, distance, reverse)
        import NXOpen.GeometricUtilities as G

        start = finite(start, "start")
        if end_type not in {"distance", "through_all", "up_to_face"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported end_type")
        if boolean not in {"none", "unite", "subtract", "intersect"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported boolean")
        if symmetric and (end_type != "distance" or start != 0):
            raise NXToolError("NX_INVALID_ARGUMENT", "Symmetric uses distance with start=0")
        if (end_type == "up_to_face") != (target_face is not None):
            raise NXToolError("NX_INVALID_ARGUMENT", "target_face is required only for up_to_face")
        if end_type == "distance":
            distance = finite(distance, "distance", True)
            if not symmetric and distance <= start:
                raise NXToolError("NX_INVALID_ARGUMENT", "End distance must exceed start")
        elif distance is not None:
            raise NXToolError("NX_INVALID_ARGUMENT", "distance is ignored by non-distance limits")
        bodies = [self._engineering_owned(r, "body") for r in targets or []]
        if (boolean == "none") == bool(bodies):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Booleans require explicit targets; none forbids them"
            )
        if end_type == "through_all" and not bodies:
            raise NXToolError("NX_INVALID_ARGUMENT", "Through-all requires boolean targets")
        sketch = self._engineering_owned(sketch_id, "sketch")
        face = self._engineering_owned(target_face, "face") if target_face else None
        axis = unit_normal(direction or self._sketch_frame(sketch)["normal"])
        if reverse:
            axis = [-v for v in axis]
        part = self._work_part()
        b = part.Features.CreateExtrudeBuilder(None)
        try:
            b.Section = self._engineering_section(sketch)
            b.Direction = self._engineering_direction(
                axis, [sketch.Origin.X, sketch.Origin.Y, sketch.Origin.Z]
            )
            b.Limits.StartExtend.TrimType = G.Extend.ExtendType.Value
            b.Limits.StartExtend.Value.RightHandSide = str(-distance / 2 if symmetric else start)
            end = b.Limits.EndExtend
            end.TrimType = {
                "distance": G.Extend.ExtendType.Value,
                "through_all": G.Extend.ExtendType.ThroughAll,
                "up_to_face": G.Extend.ExtendType.UntilSelected,
            }[end_type]
            if end_type == "distance":
                end.Value.RightHandSide = str(distance / 2 if symmetric else distance)
            if face:
                end.Target = face
            b.BooleanOperation.Type = getattr(
                G.BooleanOperation.BooleanType,
                {
                    "none": "Create",
                    "unite": "Unite",
                    "subtract": "Subtract",
                    "intersect": "Intersect",
                }[boolean],
            )
            if bodies:
                b.BooleanOperation.SetTargetBodies(bodies)
            feature = b.CommitFeature()
        finally:
            b.Destroy()
        result = self._engineering_result(feature, bodies)
        result["limits"] = {
            "start": start,
            "end_type": end_type,
            "distance": distance,
            "symmetric": symmetric,
            "direction": axis,
        }
        result["boolean"] = boolean
        return result

    def _shell(self, body, thickness, remove_faces=None, outward=False):
        target = self._engineering_owned(body, "body")
        thickness = finite(thickness, "thickness", True)
        faces = [self._engineering_owned(ref, "face") for ref in remove_faces or []]
        if any(f.GetBody() != target for f in faces):
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Removed faces must belong to body")
        b = self._work_part().Features.CreateShellBuilder(None)
        try:
            b.Tolerance = 0.001 if self._units() == "mm" else 0.001 / 25.4
            b.Body = target
            b.DefaultThickness.RightHandSide = str(thickness)
            b.DefaultThicknessFlip = not outward
            if faces:
                b.RemovedFacesCollector = self._engineering_collector(faces, "Face")
            feature = b.CommitFeature()
        finally:
            b.Destroy()
        return self._engineering_result(feature, [target])

    def _loft(self, sketches, solid=True):
        if not 2 <= len(sketches) <= 20 or len(set(sketches)) != len(sketches):
            raise NXToolError("NX_INVALID_ARGUMENT", "Use 2–20 distinct ordered sketches")
        sections = [self._engineering_owned(s, "sketch") for s in sketches]
        b = self._work_part().Features.CreateThroughCurvesBuilder(None)
        try:
            b.BodyPreference = b.BodyPreferenceTypes.Solid if solid else b.BodyPreferenceTypes.Sheet
            for sketch in sections:
                b.SectionsList.Append(self._engineering_section(sketch))
            feature = b.CommitFeature()
        finally:
            b.Destroy()
        return self._engineering_result(feature)

    def _sketch_primitive(self, sketch_id, primitive, center, width=None, height=None, radius=None):
        sketch = self._engineering_owned(sketch_id, "sketch")
        if primitive not in {"circle", "slot", "rounded_rectangle"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown primitive")
        cx, cy = [finite(x, "center") for x in center]
        curves = []

        def line(a, b):
            curves.append(
                self._create_sketch_line(
                    sketch, self._work_part(), {"x": a[0], "y": a[1]}, {"x": b[0], "y": b[1]}
                )
            )

        def arc(x, y, r, a, b):
            self._sketch_arc_legacy(x, y, r, a, b, sketch_id)

        before = {int(c.Tag) for c in sketch.GetAllGeometry()}
        if primitive == "circle":
            if width is not None or height is not None:
                raise NXToolError("NX_INVALID_ARGUMENT", "Circle accepts radius only")
            radius = finite(radius, "radius", True)
            with self._editing_sketch(sketch):
                arc(cx, cy, radius, 0, 360)
        else:
            width, height = finite(width, "width", True), finite(height, "height", True)
            if primitive == "slot":
                if radius is not None or width <= height:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", "Horizontal slot needs width>height and no radius"
                    )
                radius = height / 2
            else:
                radius = finite(radius, "radius", True)
                if radius >= min(width, height) / 2:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT",
                        "Corner radius must be less than half the shortest side",
                    )
            left, r, b, t = cx - width / 2, cx + width / 2, cy - height / 2, cy + height / 2
            with self._editing_sketch(sketch):
                line((left + radius, b), (r - radius, b))
                line((r - radius, t), (left + radius, t))
                if primitive == "slot":
                    arc(r - radius, cy, radius, -90, 90)
                    arc(left + radius, cy, radius, 90, 270)
                else:
                    line((r, b + radius), (r, t - radius))
                    line((left, t - radius), (left, b + radius))
                    for x, y, a in [
                        (r - radius, b + radius, 270),
                        (r - radius, t - radius, 0),
                        (left + radius, t - radius, 90),
                        (left + radius, b + radius, 180),
                    ]:
                        arc(x, y, radius, a, a + 90)
        created = [
            self._reference(c, "curve", self._work_part(), "Curve")
            for c in sketch.GetAllGeometry()
            if int(c.Tag) not in before
        ]
        return {
            "curves": created,
            "curve_count": len(created),
            "created": created,
            "diagnostics": self._check_sketch_result(sketch_id),
        }

    def _copy_project(self, path, prefix, activate=False):
        import hashlib
        import json
        import os
        import shutil
        from pathlib import Path

        import NXOpen.UF

        destination = self.workspace.ensure_inside(path)
        if destination.exists():
            raise NXToolError("NX_FILE_EXISTS", "Project destination must be a new directory")
        if not prefix or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in prefix
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Use a nonempty simple filename prefix for the new project"
            )
        part = self._work_part()
        sources = {Path(part.FullPath).resolve()}
        for component, _ in self._walk_components(part):
            prototype = component.Prototype
            if prototype is None or not hasattr(prototype, "FullPath"):
                raise NXToolError(
                    "NX_UNLOADED_COMPONENT", "Load all project prototypes before copying"
                )
            sources.add(Path(prototype.FullPath).resolve())
        for loaded in self.session.Parts:
            if Path(loaded.FullPath).resolve() in sources and loaded.IsModified:
                raise NXToolError("NX_UNSAVED_PART", "Save all project prototypes before copying")
        for source in sources:
            self.workspace.ensure_inside(source)
            if not source.is_file():
                raise NXToolError("NX_FILE_NOT_FOUND", str(source))
        base = Path(os.path.commonpath([str(p.parent) for p in sources]))
        if destination.is_relative_to(base) and any(destination == p.parent for p in sources):
            raise NXToolError("NX_INVALID_ARGUMENT", "Choose a separate project directory")
        mapping = {p: destination / p.relative_to(base).parent / (prefix + p.name) for p in sources}
        loaded_names = {Path(p.FullPath).name.casefold() for p in self.session.Parts}
        if any(p.name.casefold() in loaded_names for p in mapping.values()):
            raise NXToolError(
                "NX_FILE_EXISTS", "Choose a prefix that does not collide with loaded part basenames"
            )
        originals = {int(p.Tag) for p in self.session.Parts}
        hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        clone = NXOpen.UF.UFSession.GetUFSession().Clone
        self._require_api(
            clone, "Initialise", "AddAssembly", "SetNaming", "PerformClone", "Terminate"
        )
        created = False
        try:
            destination.mkdir(parents=True, exist_ok=False)
            created = True
            for p in mapping.values():
                p.parent.mkdir(parents=True, exist_ok=True)
            clone.Initialise(clone.OperationClass.CLONE_OPERATION)
            try:
                clone.SetDefAction(clone.Action.CLONE)
                clone.AddAssembly(str(Path(part.FullPath).resolve()))
                for source, target in mapping.items():
                    clone.SetNaming(str(source), clone.NamingTechnique.USER_NAME, str(target))
                clone.PerformClone(clone.InitNamingFailures())
            finally:
                clone.Terminate()
            for source, target in mapping.items():
                if not target.is_file():
                    raise NXToolError(
                        "NX_CLONE_FAILED", "Native clone did not produce every mapped part"
                    )
                if hashlib.sha256(source.read_bytes()).hexdigest() != hashes[str(source)]:
                    raise NXToolError("NX_SOURCE_CHANGED", "Source changed during project copy")
            new_top = mapping[Path(part.FullPath).resolve()]
            self._open_part(str(new_top))
            copied = self._work_part()
            resolved = {
                Path(c.Prototype.FullPath).resolve() for c, _ in self._walk_components(copied)
            }
            expected = set(mapping.values()) - {new_top}
            if resolved != expected:
                raise NXToolError(
                    "NX_CLONE_REFERENCE_MISMATCH",
                    "Copied assembly does not resolve to mapped prototypes",
                )
            manifest = {
                "source_root": str(base),
                "destination": str(destination),
                "top_part": str(new_top),
                "files": [
                    {
                        "source": str(s),
                        "path": str(t),
                        "sha256": hashlib.sha256(t.read_bytes()).hexdigest(),
                    }
                    for s, t in sorted(mapping.items())
                ],
                "dependency_count": len(expected),
                "references_verified": True,
                "originals_preserved": True,
            }
            (destination / "nx-project-manifest.json").write_text(json.dumps(manifest, indent=2))
            if not activate:
                for p in list(self.session.Parts):
                    if int(p.Tag) not in originals and Path(p.FullPath).resolve().is_relative_to(
                        destination
                    ):
                        p.Close(
                            self.nxopen.BasePart.CloseWholeTree.FalseValue,
                            self.nxopen.BasePart.CloseModified.CloseModified,
                            None,
                        )
                self._activate_part(self._reference(part, "part", part, "Part")["id"])
            return manifest
        except Exception:
            for p in list(self.session.Parts):
                if int(p.Tag) not in originals and Path(p.FullPath).resolve().is_relative_to(
                    destination
                ):
                    p.Close(
                        self.nxopen.BasePart.CloseWholeTree.FalseValue,
                        self.nxopen.BasePart.CloseModified.CloseModified,
                        None,
                    )
            self._activate_part(self._reference(part, "part", part, "Part")["id"])
            if created:
                shutil.rmtree(destination)
            raise

    def _mass_properties(self, body=None, scope="auto"):
        import NXOpen.UF

        from nx_mcp.hardened import rows, xyz

        bodies = self._geometry(body, scope)
        if not bodies or any(not b.IsSolidBody for b in bodies):
            raise NXToolError("NX_NOT_SOLID", "Mass properties require solid bodies")
        uf = NXOpen.UF.UFSession.GetUFSession()
        mass, accuracy = uf.Modeling.AskMassProps3d(
            [b.Tag for b in bodies], len(bodies), 1, 4, 0.0, 1, [0.999] + [0.0] * 10
        )
        frame = self._work_part().WCS.CoordinateSystem
        return {
            "mass_kg": mass[2],
            "volume_m3": mass[1],
            "area_m2": mass[0],
            "center_of_gravity_m": list(mass[3:6]),
            "inertia_tensor_centroid_kg_m2": [
                [mass[12], -mass[19], -mass[21]],
                [-mass[19], mass[13], -mass[20]],
                [-mass[21], -mass[20], mass[14]],
            ],
            "principal_moments_kg_m2": list(mass[31:34]),
            "density_kg_m3": mass[46],
            "body_count": len(bodies),
            "bodies": [self._reference(b, "body", self._work_part(), "Body") for b in bodies],
            "coordinate_frame": "work_part_wcs",
            "wcs_origin_in_part_units": xyz(frame.Origin),
            "wcs_rotation": rows(frame.Orientation.Element),
            "native_error_estimates": list(accuracy),
            "semantics": "sum_of_included_bodies_with_assigned_densities",
            "warnings": [
                "Overlapping solids are counted separately. Review assigned densities before using mass results."
            ],
        }

    def _draft(self, faces, stationary_face, direction, angle):
        angle = finite(angle, "angle")
        if not 0 < abs(angle) < 89:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Draft angle must have magnitude between 0 and 89 degrees"
            )
        selected = [self._engineering_owned(f, "face") for f in faces]
        fixed = self._engineering_owned(stationary_face, "face")
        if (
            not selected
            or fixed in selected
            or any(f.GetBody() != fixed.GetBody() for f in selected)
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Draft faces must share the stationary face's body and exclude that face",
            )
        part = self._work_part()
        b = part.Features.CreateDraftBuilder(None)
        try:
            b.AngleTolerance = 0.1
            b.DistanceTolerance = 0.001 if self._units() == "mm" else 0.001 / 25.4
            b.TypeOfDraft = b.Type.Face
            b.Direction = self._engineering_direction(direction)
            stationary = self._engineering_collector([fixed], "Face")
            b.StationaryReference.ReplaceRules(stationary.GetRules(), False)
            group = part.CreateExpressionCollectorSet(
                self._engineering_collector(selected, "Face"), str(angle), "Angle", 0
            )
            b.FaceSetAngleExpressionList.Append(group)
            feature = b.CommitFeature()
        finally:
            b.Destroy()
        return self._engineering_result(feature, [fixed.GetBody()])

    def _transform_bodies(
        self, translation, rotation_matrix, bodies=None, copy=False, feature=None
    ):
        from nx_mcp.hardened import IDENTITY, vector

        rotation = self._validate_rotation(rotation_matrix)
        translation = vector(translation, "translation")
        if feature is not None and (bodies is not None or copy):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "When editing a motion feature, omit bodies and copy"
            )
        selected = [self._engineering_owned(r, "body") for r in bodies or []]
        if feature is None and not selected:
            raise NXToolError("NX_INVALID_ARGUMENT", "Select bodies for a new motion feature")
        existing = self._engineering_owned(feature, "feature") if feature else None
        part = self._work_part()
        copied_feature = None
        if copy:
            extract = part.Features.CreateExtractFaceBuilder(None)
            try:
                extract.Type = extract.ExtractType.Body
                extract.Associative = True
                extract.HideOriginal = False
                extract.InheritDisplayProperties = True
                collector = self._engineering_collector(selected, "Body")
                extract.ExtractBodyCollector.ReplaceRules(collector.GetRules(), False)
                copied_feature = extract.CommitFeature()
                selected = list(copied_feature.GetBodies())
            finally:
                extract.Destroy()
        before_features = {int(f.Tag) for f in part.Features}
        b = part.BaseFeatures.CreateMoveObjectBuilder(existing)
        try:
            b.Associative = True
            b.MoveParents = False
            if existing is None:
                b.ObjectToMoveObject.Add(selected)
                b.MoveObjectResult = b.MoveObjectResultOptions.MoveOriginal
            b.TransformMotion.Option = b.TransformMotion.Options.CsysToCsys
            b.TransformMotion.FromCsys = part.CoordinateSystems.CreateCoordinateSystem(
                self.nxopen.Point3d(0.0, 0.0, 0.0), self._nx_matrix(IDENTITY), False
            )
            b.TransformMotion.ToCsys = part.CoordinateSystems.CreateCoordinateSystem(
                self.nxopen.Point3d(*translation), self._nx_matrix(rotation), False
            )
            b.MoveParents = False
            b.Associative = True
            result = b.Commit()
            if result is None:
                created = [f for f in part.Features if int(f.Tag) not in before_features]
                result = existing or (created[0] if len(created) == 1 else None)
                if result is None:
                    raise NXToolError(
                        "NX_MOTION_RESULT_MISSING",
                        "Native motion did not expose one associative feature",
                    )
        finally:
            b.Destroy()
        if not isinstance(result, self.nxopen.Features.MoveObject):
            raise NXToolError(
                "NX_MOTION_NOT_ASSOCIATIVE",
                "NX did not produce an editable MoveObject feature; rolling back",
            )
        out = self._engineering_result(result)
        out.update(
            copy_feature=self._reference(copied_feature, "feature", part, "Associative copy")
            if copied_feature
            else None,
            translation=translation,
            rotation_matrix=rotation,
            transform_semantics="absolute mapping from feature input coordinates; edit feature to replace transform",
        )
        return out

    def _component_array(
        self,
        component,
        pattern_type,
        count,
        spacing=None,
        direction=None,
        count_y=1,
        spacing_y=None,
        direction_y=None,
        center=None,
        axis=None,
        angle=None,
    ):
        from nx_mcp.hardened import cross, dot, vector

        if (
            type(count) is not int
            or not 2 <= count <= 100
            or type(count_y) is not int
            or not 1 <= count_y <= 100
            or count * count_y > 100
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Array requires 2–100 total instances including the seed"
            )
        seed = self._resolve(component, {"component"})
        part = self._work_part()
        if seed.Parent != part.ComponentAssembly.RootComponent or seed.IsSuppressed:
            raise NXToolError(
                "NX_UNSUPPORTED_SCOPE", "Select an unsuppressed immediate child occurrence"
            )
        if pattern_type == "rectangular":
            if center is not None or axis is not None or angle is not None:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Rectangular arrays forbid circular parameters"
                )
            dx = unit_normal(direction)
            spacing = finite(spacing, "spacing", True)
            if count_y > 1:
                dy = unit_normal(direction_y)
                spacing_y = finite(spacing_y, "spacing_y", True)
                if dot(cross(dx, dy), cross(dx, dy)) < 1e-10:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", "Array directions must not be parallel"
                    )
            elif direction_y is not None or spacing_y is not None:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Second direction parameters require count_y>1"
                )
        elif pattern_type == "circular":
            if (
                spacing is not None
                or direction is not None
                or count_y != 1
                or spacing_y is not None
                or direction_y is not None
            ):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Circular arrays forbid rectangular parameters"
                )
            center = vector(center, "center")
            axis = unit_normal(axis)
            angle = finite(angle, "angle", True)
            if (count - 1) * angle >= 360:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Pitch must not repeat an angular position"
                )
        else:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown pattern_type")
        b = part.ComponentAssembly.CreateComponentPatternBuilder(None)
        try:
            b.Associative = True
            b.ComponentPatternSet.Add(seed)
            if pattern_type == "rectangular":
                b.PatternService.PatternType = b.PatternService.PatternEnum.Linear
                d = b.PatternService.RectangularDefinition
                d.XDirection = self._engineering_direction(dx)
                d.XSpacing.NCopies.RightHandSide = str(count)
                d.XSpacing.PitchDistance.RightHandSide = str(spacing)
                d.UseYDirectionToggle = count_y > 1
                d.YSpacing.NCopies.RightHandSide = str(count_y)
                if count_y > 1:
                    d.YDirection = self._engineering_direction(dy)
                    d.YSpacing.PitchDistance.RightHandSide = str(spacing_y)
            else:
                b.PatternService.PatternType = b.PatternService.PatternEnum.Circular
                d = b.PatternService.CircularDefinition
                point = part.Points.CreatePoint(self.nxopen.Point3d(*center))
                d.RotationAxis = part.Axes.CreateAxis(
                    point,
                    self._engineering_direction(axis),
                    self.nxopen.SmartObject.UpdateOption.WithinModeling,
                )
                d.AngularSpacing.NCopies.RightHandSide = str(count)
                d.AngularSpacing.PitchAngle.RightHandSide = str(angle)
                d.RadialSpacing.NCopies.RightHandSide = "1"
            pattern = b.Commit()
        finally:
            b.Destroy()
        self._update_model()
        result = self._component_pattern_record(pattern)
        if result["total_instances"] != count * count_y:
            raise NXToolError(
                "NX_PATTERN_VERIFICATION_FAILED", "Native array count differs; rolling back"
            )
        result.update(
            pattern_type=pattern_type,
            count=count,
            count_y=count_y,
            spacing=spacing,
            spacing_y=spacing_y,
            angle_degrees=angle,
        )
        return result

    def _set_material(self, bodies, name, density):
        import NXOpen.UF

        density = finite(density, "density", True)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise NXToolError("NX_INVALID_ARGUMENT", "Provide a material name of 1–100 characters")
        selected = [self._engineering_owned(r, "body") for r in bodies]
        if not selected or any(not b.IsSolidBody for b in selected):
            raise NXToolError("NX_NOT_SOLID", "Material assignment requires owned solid bodies")
        part = self._work_part()
        materials = part.MaterialManager.PhysicalMaterials
        if any(m.Name.casefold() == name.casefold() for m in materials):
            raise NXToolError(
                "NX_MATERIAL_EXISTS",
                "Choose a new material name to avoid changing other bodies implicitly",
            )
        b = materials.CreatePhysicalMaterialBuilder(self.nxopen.PhysicalMaterial.Type.Isotropic)
        try:
            b.Name = name
            b.PropertyTable.SetBaseScalarWithDataPropertyValue(
                "MassDensity", density, part.UnitCollection.FindObject("KilogramPerCubicMeter")
            )
            material = b.Commit()
        finally:
            b.Destroy()
        material.AssignObjects(selected)
        uf = NXOpen.UF.UFSession.GetUFSession()
        actual = [
            uf.Modeling.AskBodyDensity(body.Tag, NXOpen.UF.Modl.DensityUnits.KILOGRAMS_METERS)
            for body in selected
        ]
        if any(abs(v - density) > max(1e-8, density * 1e-8) for v in actual):
            raise NXToolError(
                "NX_MATERIAL_VERIFICATION_FAILED",
                "Assigned native body densities differ; rolling back",
            )
        return {
            "material_name": material.Name,
            "density_kg_m3": density,
            "bodies": [self._reference(body, "body", part, "Body") for body in selected],
            "verified_densities_kg_m3": actual,
            "properties_defined": ["mass_density"],
            "warnings": [
                "This local material defines density only; elastic, thermal and appearance properties are not inferred."
            ],
        }

    def _material_info(self, body=None, scope="auto"):
        import NXOpen.UF

        part = self._work_part()
        uf = NXOpen.UF.UFSession.GetUFSession()
        rows = []
        for obj in self._geometry(body, scope):
            prototype = obj.Prototype if obj.IsOccurrence else obj
            material = prototype.OwningPart.MaterialManager.PhysicalMaterials.AskMaterialOfObject(
                prototype
            )
            rows.append(
                {
                    "body": self._reference(obj, "body", part, "Body"),
                    "material_name": material.Name if material else None,
                    "density_kg_m3": uf.Modeling.AskBodyDensity(
                        prototype.Tag, NXOpen.UF.Modl.DensityUnits.KILOGRAMS_METERS
                    ),
                }
            )
        return {"bodies": rows, "body_count": len(rows)}

    def _sketch_angle(self, sketch_id, line1, line2, value, origin):
        sketch = self._engineering_owned(sketch_id, "sketch")
        one, two = self._owned_curve(sketch, line1), self._owned_curve(sketch, line2)
        if one == two or not all(isinstance(c, self.nxopen.Line) for c in [one, two]):
            raise NXToolError("NX_INVALID_ARGUMENT", "Select two different sketch lines")
        value = finite(value, "value", True)
        if value >= 180:
            raise NXToolError("NX_INVALID_ARGUMENT", "Angle must be between 0 and 180 degrees")
        a, b = self.nxopen.Sketch.DimensionGeometry(), self.nxopen.Sketch.DimensionGeometry()
        a.Geometry = one
        b.Geometry = two
        with self._editing_sketch(sketch):
            constraint = sketch.CreateDimension(
                self.nxopen.Sketch.ConstraintType.AngularDim,
                a,
                b,
                self._sketch_local_point(sketch, origin),
                None,
                self.nxopen.Sketch.DimensionOption.CreateAsDriving,
            )
            exp = constraint.AssociatedExpression
            self._work_part().Expressions.EditExpression(exp, str(value))
            sketch.Update()
            diagnostics = self._check_sketch_result(sketch_id)
        return {
            "constraint": self._reference(
                constraint, "constraint", self._work_part(), "Angular dimension"
            ),
            "expression": self._expression_record(exp),
            "diagnostics": diagnostics,
        }

    def _sketch_tangent(self, sketch_id, curve1, curve2):
        from nx_mcp.hardened import cross, dot, xyz

        sketch = self._engineering_owned(sketch_id, "sketch")
        a, b = self._owned_curve(sketch, curve1), self._owned_curve(sketch, curve2)
        if (
            a == b
            or not all(isinstance(c, (self.nxopen.Line, self.nxopen.Arc)) for c in [a, b])
            or all(isinstance(c, self.nxopen.Line) for c in [a, b])
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Select a line and arc, or two arcs")
        with self._editing_sketch(sketch):
            before = {
                int(c.Tag)
                for c in sketch.GetAllConstraintsOfType(
                    self.nxopen.Sketch.ConstraintClass.Any, self.nxopen.Sketch.ConstraintType.NoCon
                )
            }
            builder = self._work_part().Sketches.CreateSketchMakeTangentBuilder()
            try:
                builder.StationaryObject.Value = a
                builder.MotionObjects.Add(b)
                builder.SetCreateConstraints(True)
                builder.FindRelations()
                builder.Commit()
                sketch.Update()
                constraints = [
                    c
                    for c in sketch.GetAllConstraintsOfType(
                        self.nxopen.Sketch.ConstraintClass.Any,
                        self.nxopen.Sketch.ConstraintType.NoCon,
                    )
                    if int(c.Tag) not in before
                ]
            finally:
                builder.Destroy()
            diagnostics = self._check_sketch_result(sketch_id)
            if isinstance(a, self.nxopen.Line) or isinstance(b, self.nxopen.Line):
                line, circle = (a, b) if isinstance(a, self.nxopen.Line) else (b, a)
                v = [x - y for x, y in zip(xyz(line.EndPoint), xyz(line.StartPoint), strict=True)]
                d = [
                    x - y
                    for x, y in zip(xyz(circle.CenterPoint), xyz(line.StartPoint), strict=True)
                ]
                normal = cross(v, d)
                residual = abs((dot(normal, normal) / dot(v, v)) ** 0.5 - circle.Radius)
            else:
                distance = (
                    sum(
                        (x - y) ** 2
                        for x, y in zip(xyz(a.CenterPoint), xyz(b.CenterPoint), strict=True)
                    )
                    ** 0.5
                )
                residual = min(
                    abs(distance - a.Radius - b.Radius), abs(distance - abs(a.Radius - b.Radius))
                )
            if residual > 1e-6 or not constraints:
                raise NXToolError(
                    "NX_CONSTRAINT_UNSATISFIED",
                    "Native tangent relation was not satisfied persistently",
                    details={"residual": residual, "created_constraints": len(constraints)},
                )
        return {
            "constraints": [
                self._reference(c, "constraint", self._work_part(), "Tangent") for c in constraints
            ],
            "residual": residual,
            "diagnostics": diagnostics,
        }

    def _sketch_symmetry(self, sketch_id, curve1, curve2, centerline):
        from nx_mcp.hardened import dot, xyz

        sketch = self._engineering_owned(sketch_id, "sketch")
        a, b, c = [self._owned_curve(sketch, r) for r in [curve1, curve2, centerline]]
        if len({int(o.Tag) for o in [a, b, c]}) != 3 or not all(
            isinstance(o, self.nxopen.Line) for o in [a, b, c]
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Select two lines and a distinct straight centerline"
            )

        def constraints():
            return list(
                sketch.GetAllConstraintsOfType(
                    self.nxopen.Sketch.ConstraintClass.Any, self.nxopen.Sketch.ConstraintType.NoCon
                )
            )

        with self._editing_sketch(sketch):
            before = {int(o.Tag) for o in constraints()}
            builder = self._work_part().Sketches.CreateSketchSymmetricBuilder()
            try:
                builder.StationaryObject.Value = a
                builder.MotionObjects.Add(b)
                builder.CenterLine.Value = c
                builder.ConvertCenterlineToReference = False
                builder.SetCreateConstraints(True)
                builder.FindRelations()
                builder.Commit()
            finally:
                builder.Destroy()
            sketch.Update()
            origin = xyz(c.StartPoint)
            axis = [x - y for x, y in zip(xyz(c.EndPoint), origin, strict=True)]

            def reflect(point):
                delta = [x - y for x, y in zip(xyz(point), origin, strict=True)]
                t = dot(delta, axis) / dot(axis, axis)
                return [o + 2 * t * d - v for o, d, v in zip(origin, axis, delta, strict=True)]

            def error(points):
                return max(
                    sum((x - y) ** 2 for x, y in zip(reflect(p), xyz(q), strict=True)) ** 0.5
                    for p, q in zip([a.StartPoint, a.EndPoint], points, strict=True)
                )

            residual = min(error([b.StartPoint, b.EndPoint]), error([b.EndPoint, b.StartPoint]))
            after = constraints()
            created = [o for o in after if int(o.Tag) not in before]
            if residual > 1e-6 or not created or not before <= {int(o.Tag) for o in after}:
                raise NXToolError(
                    "NX_CONSTRAINT_UNSATISFIED",
                    "Native symmetry was not satisfied persistently without deleting constraints",
                    details={"residual": residual, "created_constraints": len(created)},
                )
            diagnostics = self._check_sketch_result(sketch_id)
        return {
            "constraints": [
                self._reference(o, "constraint", self._work_part(), "Symmetry") for o in created
            ],
            "residual": residual,
            "diagnostics": diagnostics,
        }

    def _sketch_trim_extend(self, sketch_id, curve, boundaries, pick, action):
        sketch = self._engineering_owned(sketch_id, "sketch")
        selected = self._owned_curve(sketch, curve)
        limits = [self._owned_curve(sketch, r) for r in boundaries]
        if not limits or selected in limits or action not in {"trim", "extend"}:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Select distinct boundary curves and trim/extend action"
            )
        point = self._sketch_local_point(sketch, pick)
        with self._editing_sketch(sketch):
            fn = (
                self._work_part().Sketches.CreateQuickTrimBuilder
                if action == "trim"
                else self._work_part().Sketches.CreateQuickExtendBuilder
            )
            builder = fn()
            try:
                builder.BoundaryObjects.Add(limits)
                builder.ExtendBound = False
                curves = builder.TrimmedCurves if action == "trim" else builder.ExtendedCurves
                curves.Add(selected, self._work_part().ModelingViews.WorkView, point)
                builder.Commit()
            finally:
                builder.Destroy()
            diagnostics = self._check_sketch_result(sketch_id)
        return {"sketch": self._sketch_info(sketch_id), "diagnostics": diagnostics}

    def _render_view(
        self,
        path=None,
        width=1600,
        height=1000,
        background="white",
        color=None,
        style="studio",
        lighting=None,
    ):
        import hashlib
        import struct
        import uuid

        if self.session.IsBatch:
            raise NXToolError(
                "NX_VIEWPORT_UNAVAILABLE", "Rendering requires the interactive NX bridge"
            )
        if any(type(n) is not int or not 128 <= n <= 4096 for n in [width, height]):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Resolution must be 128–4096 pixels per dimension"
            )
        if background not in {"white", "original", "transparent", "color"} or style not in {
            "studio",
            "shaded",
            "shaded_with_edges",
        }:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported rendering options")
        if background == "color":
            if (
                not isinstance(color, list)
                or len(color) != 3
                or any(not 0 <= finite(v, "color") <= 1 for v in color)
            ):
                raise NXToolError("NX_INVALID_ARGUMENT", "color requires three RGB values in [0,1]")
        elif color is not None:
            raise NXToolError("NX_INVALID_ARGUMENT", "color requires background=color")
        if lighting is not None and (type(lighting) is not int or not 1 <= lighting <= 5):
            raise NXToolError("NX_INVALID_ARGUMENT", "lighting must be a native preset number 1–5")
        file = (
            self.workspace.ensure_inside(path)
            if path
            else self.workspace.root / "captures" / ("render-" + uuid.uuid4().hex + ".png")
        )
        if file.suffix.lower() != ".png" or file.exists():
            raise NXToolError("NX_INVALID_ARGUMENT", "Choose a new .png path")
        part = self.session.Parts.Display
        if part is None:
            raise NXToolError("NX_NO_DISPLAY_PART", "Open a display part first")
        file.parent.mkdir(parents=True, exist_ok=True)
        view = part.ModelingViews.WorkView
        original_style = view.RenderingStyle
        lights = builder = None
        previous_lighting = None
        try:
            view.RenderingStyle = getattr(
                self.nxopen.View.RenderingStyleType,
                {"studio": "Studio", "shaded": "Shaded", "shaded_with_edges": "ShadedWithEdges"}[
                    style
                ],
            )
            if lighting is not None:
                lights = part.Views.CreateLighting(None)
                previous_lighting = lights.LightsShadedViewsLightingCollection
                lights.LightsShadedViewsLightingCollection = getattr(
                    lights.LightingCollectionType, "Lighting" + str(lighting)
                )
                lights.Commit()
            view.UpdateDisplay()
            camera = self._view_info()
            builder = part.Views.CreateStudioImageCaptureBuilder()
            builder.Source = builder.SourceType.WorkView
            builder.UnitsEnum = builder.UnitsEnumType.Pixels
            builder.SetImageDimensionsInteger([height, width])
            builder.NativeFileBrowser = str(file)
            builder.BackgroundOption = getattr(
                builder.BackgroundOptions,
                {
                    "white": "CustomColor",
                    "color": "CustomColor",
                    "original": "Original",
                    "transparent": "Transparent",
                }[background],
            )
            if background in {"white", "color"}:
                builder.SetCustomBackgroundColor(
                    [1.0, 1.0, 1.0] if background == "white" else color
                )
            builder.Commit()
            data = file.read_bytes()
            if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
                raise NXToolError("NX_CAPTURE_FAILED", "NX did not produce a valid PNG")
            resolution = list(struct.unpack(">II", data[16:24]))
            if resolution != [width, height]:
                raise NXToolError(
                    "NX_CAPTURE_RESOLUTION_MISMATCH",
                    "Native rendering did not honor the requested resolution",
                )
        except Exception:
            file.unlink(missing_ok=True)
            raise
        finally:
            try:
                try:
                    if builder is not None:
                        builder.Destroy()
                finally:
                    try:
                        if lights is not None:
                            try:
                                if previous_lighting is not None:
                                    lights.LightsShadedViewsLightingCollection = previous_lighting
                                    lights.Commit()
                            finally:
                                lights.Destroy()
                    finally:
                        view.RenderingStyle = original_style
                        view.UpdateDisplay()
            except Exception as error:
                file.unlink(missing_ok=True)
                raise NXToolError(
                    "NX_RENDER_RESTORE_FAILED",
                    "Native rendering cleanup failed",
                    details={"mutation_outcome": "partial", "cleanup_error": str(error)},
                ) from error
        return {
            "path": str(file),
            "artifact_path": str(file.relative_to(self.workspace.root)),
            "capture_kind": "nx_native_render",
            "model_preview": True,
            "resolution": resolution,
            "requested_resolution": [width, height],
            "camera": camera,
            "background": background,
            "color": color,
            "style": style,
            "lighting_preset": lighting,
            "rendering": "NX StudioImageCaptureBuilder",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "warnings": [],
        }

    def _assembly_constraints(self, part):
        positioner = getattr(getattr(part, "ComponentAssembly", None), "Positioner", None)
        return list(positioner.Constraints) if positioner is not None else []

    def _assembly_constraint_record(self, constraint):
        import NXOpen.Positioning

        from nx_mcp.visual_tools import enum_name

        part = self._work_part()
        cls = NXOpen.Positioning.Constraint
        references = []
        for ref in constraint.GetReferences():
            obj, geom = ref.GetMovableObject(), ref.GetGeometry()
            kind = next(
                (
                    name
                    for name, typ in [
                        ("face", self.nxopen.Face),
                        ("edge", self.nxopen.Edge),
                        ("body", self.nxopen.Body),
                        ("component", self.nxopen.Assemblies.Component),
                    ]
                    if isinstance(geom, typ)
                ),
                None,
            )
            references.append(
                {
                    "component": self._reference(obj, "component", part, "Component")
                    if isinstance(obj, self.nxopen.Assemblies.Component)
                    else None,
                    "geometry": self._reference(geom, kind, part, kind.title()) if kind else None,
                    "native_geometry_type": type(geom).__name__,
                }
            )
        expression = (
            constraint.Expression
            if constraint.ConstraintType in {cls.Type.Distance, cls.Type.Angle}
            else None
        )
        return {
            "object": self._reference(
                constraint, "assembly_constraint", part, "Assembly constraint"
            ),
            "constraint_type": enum_name(constraint.ConstraintType, cls.Type),
            "alignment": enum_name(constraint.ConstraintAlignment, cls.Alignment),
            "solver_status": enum_name(constraint.GetConstraintStatus(), cls.SolverStatus),
            "suppressed": constraint.Suppressed,
            "expression": self._expression_record(expression) if expression else None,
            "references": references,
        }

    def _list_assembly_constraints(self):
        rows = [
            self._assembly_constraint_record(c)
            for c in self._assembly_constraints(self._work_part())
        ]
        return {
            "constraints": rows,
            "constraint_count": len(rows),
            "coordinate_frame": "work_part",
            "units": self._units(),
        }

    def _assembly_constraint(
        self,
        constraint_type,
        component,
        geometry=None,
        target_component=None,
        target_geometry=None,
        value=None,
        alignment="infer",
    ):
        import NXOpen.Positioning

        cls = NXOpen.Positioning.Constraint
        types = {
            "fix": "Fix",
            "touch": "Touch",
            "distance": "Distance",
            "parallel": "Parallel",
            "perpendicular": "Perpendicular",
            "angle": "Angle",
            "concentric": "Concentric",
        }
        alignments = {"infer": "InferAlign", "same": "CoAlign", "opposite": "ContraAlign"}
        if constraint_type not in types or alignment not in alignments:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported constraint type or alignment")
        moving = self._resolve(component, {"component"})
        if (
            moving.Parent != self._work_part().ComponentAssembly.RootComponent
            or moving.IsSuppressed
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Select an unsuppressed immediate child component"
            )
        refs = []
        if constraint_type == "fix":
            if (
                any(v is not None for v in [geometry, target_component, target_geometry, value])
                or alignment != "infer"
            ):
                raise NXToolError("NX_INVALID_ARGUMENT", "Fix takes only component")
            refs = [(moving, moving)]
        else:
            if any(
                not isinstance(v, str) or not v
                for v in [geometry, target_component, target_geometry]
            ):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Provide moving/target geometry and target_component"
                )
            target = self._resolve(target_component, {"component"})
            if target == moving or target.Parent != moving.Parent or target.IsSuppressed:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Target must be a distinct unsuppressed sibling"
                )
            for component_obj, geometry_ref in [(moving, geometry), (target, target_geometry)]:
                geom = self._resolve(geometry_ref, {"face", "edge"})
                if not geom.IsOccurrence or geom.OwningComponent != component_obj:
                    raise NXToolError(
                        "NX_OBJECT_OWNER_MISMATCH",
                        "Select occurrence geometry belonging to the specified component",
                    )
                refs.append((component_obj, geom))
            if constraint_type in {"distance", "angle"}:
                value = finite(value, "value")
                if value < 0 or (constraint_type == "angle" and value > 180):
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT",
                        "Distance must be nonnegative; angle must be 0–180 degrees",
                    )
            elif value is not None:
                raise NXToolError("NX_INVALID_ARGUMENT", "value applies only to distance/angle")
        positioner = self._work_part().ComponentAssembly.Positioner
        positioner.BeginAssemblyConstraints()
        try:
            network = positioner.EstablishNetwork()
            network.MoveObjectsState = True
            constraint = positioner.CreateConstraint(True)
            constraint.ConstraintType = getattr(cls.Type, types[constraint_type])
            constraint.ConstraintAlignment = getattr(cls.Alignment, alignments[alignment])
            for component_obj, geom in refs:
                constraint.CreateConstraintReference(component_obj, geom, False, False)
            if value is not None:
                constraint.SetExpression(str(value))
            network.AddConstraint(constraint)
            network.Solve()
            network.ApplyToModel()
            self._update_model()
            if constraint.GetConstraintStatus() != cls.SolverStatus.Solved:
                raise NXToolError(
                    "NX_CONSTRAINT_UNSATISFIED",
                    "Native assembly solver did not satisfy the constraint",
                    details=self._assembly_constraint_record(constraint),
                )
            return self._assembly_constraint_record(constraint)
        finally:
            try:
                positioner.ClearNetwork()
            finally:
                positioner.EndAssemblyConstraints()

    def _edit_assembly_constraint(self, constraint, value=None, suppressed=None, alignment=None):
        import NXOpen.Positioning

        obj = self._resolve(constraint, {"assembly_constraint"})
        cls = NXOpen.Positioning.Constraint
        if all(v is None for v in [value, suppressed, alignment]):
            raise NXToolError("NX_INVALID_ARGUMENT", "Provide value, suppressed, or alignment")
        if value is not None:
            value = finite(value, "value")
            if (
                obj.ConstraintType not in {cls.Type.Distance, cls.Type.Angle}
                or value < 0
                or (obj.ConstraintType == cls.Type.Angle and value > 180)
            ):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "value requires distance>=0 or angle 0–180 degrees"
                )
        if suppressed is not None and type(suppressed) is not bool:
            raise NXToolError("NX_INVALID_ARGUMENT", "suppressed must be boolean")
        names = {"infer": "InferAlign", "same": "CoAlign", "opposite": "ContraAlign"}
        if alignment is not None and alignment not in names:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported alignment")
        positioner = self._work_part().ComponentAssembly.Positioner
        positioner.BeginAssemblyConstraints()
        try:
            if value is not None:
                obj.SetExpression(str(value))
            if suppressed is not None:
                obj.Suppressed = suppressed
            if alignment is not None:
                obj.ConstraintAlignment = getattr(cls.Alignment, names[alignment])
            network = positioner.EstablishNetwork()
            network.MoveObjectsState = True
            network.AddConstraint(obj)
            network.Solve()
            network.ApplyToModel()
            self._update_model()
            if not obj.Suppressed and obj.GetConstraintStatus() != cls.SolverStatus.Solved:
                raise NXToolError(
                    "NX_CONSTRAINT_UNSATISFIED", "Edited assembly constraint was not solved"
                )
            return self._assembly_constraint_record(obj)
        finally:
            try:
                positioner.ClearNetwork()
            finally:
                positioner.EndAssemblyConstraints()

    def _blend(self, edges, radius):
        radius = finite(radius, "radius", True)
        selected = [self._engineering_owned(r, "edge") for r in edges]
        if not selected or len({int(e.GetBody().Tag) for e in selected}) != 1:
            raise NXToolError("NX_INVALID_ARGUMENT", "Select edges of one owned body")
        target = selected[0].GetBody()
        b = self._work_part().Features.CreateEdgeBlendBuilder(None)
        try:
            b.Tolerance = 0.001 if self._units() == "mm" else 0.001 / 25.4
            b.AddChainset(self._engineering_collector(selected, "Edge"), str(radius))
            result = b.CommitFeature()
        finally:
            b.Destroy()
        return self._engineering_result(result, [target])

    def _chamfer(self, edges, offset):
        offset = finite(offset, "offset", True)
        selected = [self._engineering_owned(r, "edge") for r in edges]
        if not selected or len({int(e.GetBody().Tag) for e in selected}) != 1:
            raise NXToolError("NX_INVALID_ARGUMENT", "Select edges of one owned body")
        target = selected[0].GetBody()
        b = self._work_part().Features.CreateChamferBuilder(None)
        try:
            b.Tolerance = 0.001 if self._units() == "mm" else 0.001 / 25.4
            b.SmartCollector = self._engineering_collector(selected, "Edge")
            b.Option = b.ChamferOption.SymmetricOffsets
            b.FirstOffsetExp.RightHandSide = str(offset)
            result = b.CommitFeature()
        finally:
            b.Destroy()
        return self._engineering_result(result, [target])

    def _hole(self, diameter, depth, x, y, z, body=None, direction=None):
        import NXOpen.GeometricUtilities

        diameter, depth = finite(diameter, "diameter", True), finite(depth, "depth", True)
        location = [finite(v, "location") for v in [x, y, z]]
        targets = (
            [self._engineering_owned(body, "body")] if body else list(self._work_part().Bodies)
        )
        if len(targets) != 1 or not targets[0].IsSolidBody:
            raise NXToolError("NX_AMBIGUOUS_TARGET", "Specify one owned solid body")
        axis = unit_normal(direction if direction is not None else [0, 0, 1])
        b = self._work_part().Features.CreateCylinderBuilder(None)
        try:
            b.Origin = self.nxopen.Point3d(*location)
            b.Direction = self.nxopen.Vector3d(*axis)
            b.Diameter.RightHandSide = str(diameter)
            b.Height.RightHandSide = str(depth)
            b.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Subtract
            b.BooleanOption.SetTargetBodies(targets)
            result = b.CommitFeature()
        finally:
            b.Destroy()
        out = self._engineering_result(result, targets)
        out.update(
            diameter=diameter,
            depth=depth,
            location=location,
            direction=axis,
            native_feature="cylindrical subtract",
        )
        return out

    def _sweep(self, section, guide, boolean="none", targets=None):
        if boolean not in {"none", "unite", "subtract", "intersect"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported boolean operation")
        if (boolean == "none") != (targets is None):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Boolean sweep requires explicit targets; none forbids targets",
            )
        selected = [self._engineering_owned(t, "body") for t in targets or []]
        if boolean != "none" and len(selected) != 1:
            raise NXToolError("NX_INVALID_ARGUMENT", "Select exactly one boolean target")
        a, b = self._engineering_owned(section, "sketch"), self._engineering_owned(guide, "sketch")
        if a == b:
            raise NXToolError("NX_INVALID_ARGUMENT", "Section and guide must be different sketches")
        builder = self._work_part().Features.CreateSweptBuilder(None)
        try:
            builder.G0Tolerance = 0.001 if self._units() == "mm" else 0.001 / 25.4
            builder.G1Tolerance = 0.1
            builder.SectionList.Append(self._engineering_section(a))
            builder.GuideList.Append(self._engineering_section(b))
            feature = builder.CommitFeature()
        finally:
            builder.Destroy()
        out = self._engineering_result(feature)
        if boolean != "none":
            out = self._boolean(boolean, targets + [r["id"] for r in out["bodies"]])
        return out

    def _mate_component(self, component, mate_type, references=None, offset=0.0):
        offset = finite(offset, "offset")
        if mate_type not in {"touch", "align", "orient", "center", "align_angle"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported mate type")
        if not references or len(references) != 2:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Provide moving and target occurrence face/edge references"
            )
        target_geom = self._resolve(references[1], {"face", "edge"})
        if not target_geom.IsOccurrence:
            raise NXToolError("NX_INVALID_ARGUMENT", "Target geometry must be an occurrence")
        target = self._reference(
            target_geom.OwningComponent, "component", self._work_part(), "Component"
        )["id"]
        kind = {
            "touch": "touch",
            "align": "touch",
            "orient": "parallel",
            "center": "concentric",
            "align_angle": "angle",
        }[mate_type]
        if offset and kind not in {"touch", "angle"}:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Offset applies only to touch/align or align_angle"
            )
        if kind == "touch" and offset:
            kind = "distance"
        return self._assembly_constraint(
            kind,
            component,
            references[0],
            target,
            references[1],
            offset if kind in {"distance", "angle"} else None,
            "opposite" if mate_type == "touch" else "same" if mate_type == "align" else "infer",
        )

    def _mirror_body(self, body, plane):
        from nx_mcp.hardened import IDENTITY

        matrices = {
            "XY": IDENTITY,
            "XZ": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
            "YZ": [[0, 0, 1], [1, 0, 0], [0, 1, 0]],
        }
        if plane not in matrices:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "plane must be XY, XZ or YZ through the work-part origin"
            )
        target = self._engineering_owned(body, "body")
        part = self._work_part()
        datum = part.Datums.CreateFixedDatumPlane(
            self.nxopen.Point3d(0.0, 0.0, 0.0), self._nx_matrix(matrices[plane])
        )
        b = part.Features.CreateMirrorBodyBuilder(None)
        try:
            b.MirrorBodyCollector.ReplaceRules(
                self._engineering_collector([target], "Body").GetRules(), False
            )
            b.Plane.Value = datum
            b.DeleteSourceBody = False
            result = b.CommitFeature()
        finally:
            b.Destroy()
        return self._engineering_result(result)

    def _drawing_object(self, ref, kind):
        if ref.startswith("obj_"):
            return self._resolve(ref, {kind})
        pool = (
            self._work_part().DrawingSheets
            if kind == "drawing_sheet"
            else self._work_part().DraftingViews
        )
        matches = [o for o in pool if o.Name.casefold() == ref.casefold()]
        if len(matches) != 1:
            raise NXToolError(
                "NX_NOT_FOUND", "Use a typed reference to an existing drawing sheet/view"
            )
        return matches[0]

    def _create_drawing(self, name="Sheet1", size="A3", scale=1.0, units="mm"):
        if units not in {"mm", "in"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Drawing units must be mm or in")
        dimensions = {
            "A0": (1189, 841),
            "A1": (841, 594),
            "A2": (594, 420),
            "A3": (420, 297),
            "A4": (297, 210),
        }
        if (
            size not in dimensions
            or not name.strip()
            or any(s.Name.casefold() == name.casefold() for s in self._work_part().DrawingSheets)
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Choose A0–A4 and a unique nonempty sheet name"
            )
        scale = finite(scale, "scale", True)
        b = self._work_part().DraftingDrawingSheets.CreateDraftingDrawingSheetBuilder(None)
        try:
            b.Option = b.SheetOption.CustomSize
            b.Units = b.SheetUnits.Metric if units == "mm" else b.SheetUnits.English
            b.Length, b.Height = [
                float(v) / (25.4 if units == "in" else 1) for v in dimensions[size]
            ]
            b.Name = name
            b.ScaleNumerator = scale
            b.ScaleDenominator = 1.0
            b.ProjectionAngle = b.SheetProjectionAngle.First
            sheet = b.Commit()
        finally:
            b.Destroy()
        sheet.Open()
        sheet.SetParameters(
            sheet.Height, sheet.Length, scale, 1.0, sheet.Units, sheet.ProjectionAngle
        )
        actual_scale = list(sheet.GetScale())
        if not math.isclose(actual_scale[0] / actual_scale[1], scale, abs_tol=1e-10):
            raise NXToolError("NX_VERIFICATION_FAILED", "Native sheet scale did not persist")
        return {
            "object": self._reference(sheet, "drawing_sheet", self._work_part(), "Drawing sheet"),
            "sheet_name": sheet.Name,
            "size": size,
            "dimensions_mm": list(dimensions[size]),
            "dimensions": [sheet.Length, sheet.Height],
            "scale": actual_scale[0] / actual_scale[1],
            "scale_ratio": actual_scale,
            "projection": "first_angle",
            "units": units,
        }

    def _configure_base_view(self, builder, sheet):
        import NXOpen.Preferences as P

        numerator, denominator = sheet.GetScale()
        builder.Scale.ScaleType = builder.Scale.Type.Ratio
        builder.Scale.Numerator = numerator
        builder.Scale.Denominator = denominator
        hidden = builder.Style.ViewStyleHiddenLines
        hidden.HiddenLine = True
        hidden.Font = P.Font.Dashed
        hidden.SelfHidden = True
        builder.Style.ViewStyleVisibleLines.VisibleFont = P.Font.Solid

    def _add_base_view(self, drawing, body, view, position=None):
        names = {
            "top": "Top",
            "front": "Front",
            "back": "Back",
            "right": "Right",
            "left": "Left",
            "bottom": "Bottom",
            "isometric": "Isometric",
        }
        if view not in names:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported named model view")
        target = self._engineering_owned(body, "body")
        part = self._work_part()
        if len(list(part.Bodies)) != 1 or list(self._walk_components(part)):
            raise NXToolError(
                "NX_UNSUPPORTED_SCOPE",
                "Base-view body selection currently requires a single-body part",
            )
        sheet = self._drawing_object(drawing, "drawing_sheet")
        point = [100.0, 100.0] if position is None else [finite(v, "position") for v in position]
        if len(point) != 2:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "position must be two sheet coordinates in sheet units"
            )
        sheet.Open()
        b = part.DraftingViews.CreateBaseViewBuilder(None)
        try:
            self._configure_base_view(b, sheet)
            b.SelectModelView.SelectedView = part.ModelingViews.FindObject(names[view])
            b.Placement.Placement.SetValue(None, None, self._sheet_point3d(sheet, point))
            result = b.Commit()
        finally:
            b.Destroy()
        self._drawing_construction_visibility(result, False)
        self._place_drawing_view(result, sheet, point)
        return {
            "object": self._reference(result, "drawing_view", part, "Base view"),
            "view_name": result.Name,
            "drawing": self._reference(sheet, "drawing_sheet", part, "Drawing sheet"),
            "body": self._reference(target, "body", part, "Body"),
            "orientation": view,
            **self._drawing_coordinates(sheet, point),
        }

    def _export_drawing_pdf(self, path):
        import hashlib

        file = self.workspace.ensure_inside(path)
        if file.suffix.lower() != ".pdf" or file.exists():
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Choose a new .pdf path; existing files are never overwritten",
                details={"mutation_outcome": "not_started"},
            )
        from nx_mcp.assembly_loading import require_loaded

        require_loaded(self, self._work_part())
        sheets = list(self._work_part().DrawingSheets)
        if not sheets:
            raise NXToolError("NX_NO_DRAWING", "Create a drawing sheet before PDF export")
        file.parent.mkdir(parents=True, exist_ok=True)
        with self._drawing_save_context(self._work_part(), force_display=True):
            # Opening each sheet refreshes its display/CGM presentation before plotting.
            # PDF export is a non-model operation and has no active modeling undo mark.
            views = [view for sheet in sheets for view in sheet.GetDraftingViews()]
            if views:
                self._work_part().DraftingViews.UpdateViews(views)
            for sheet in sheets:
                sheet.Open()
            b = self._work_part().PlotManager.CreatePrintPdfbuilder()
            try:
                b.Filename = str(file)
                b.Action = b.ActionOption.Native
                b.Size = b.SizeOption.FullScale
                b.Units = b.UnitsOption.Metric
                b.OutputText = b.OutputTextOption.Text
                b.RasterImages = True
                b.ShadedGeometry = False
                b.ImageResolution = b.ImageResolutionOption.High
                b.Colors = b.Color.AsDisplayed
                b.SourceBuilder.SetSheets(sheets)
                b.Commit()
                data = file.read_bytes()
                if not data.startswith(b"%PDF-"):
                    raise NXToolError("NX_EXPORT_FAILED", "Native exporter did not produce a PDF")
            except Exception:
                file.unlink(missing_ok=True)
                raise
            finally:
                b.Destroy()
        return {
            "path": str(file),
            "artifact_path": str(file.relative_to(self.workspace.root)),
            "sheet_count": len(sheets),
            "sheets": [s.Name for s in sheets],
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "units": "mm",
            "scale": "full_sheet_scale",
            "output_settings": {
                "raster_images": True,
                "shaded_as_wireframe": False,
                "image_resolution": "high",
                "colors": "as_displayed",
                "views_updated": True,
            },
            "warnings": [],
        }

    def _add_projection_view(self, base_view, direction, spacing=60.0):
        offsets = {"right": (1, 0), "left": (-1, 0), "top": (0, 1), "bottom": (0, -1)}
        if direction not in offsets:
            raise NXToolError("NX_INVALID_ARGUMENT", "direction must be right, left, top or bottom")
        spacing = finite(spacing, "spacing", True)
        view = self._drawing_object(base_view, "drawing_view")
        center = view.GetDrawingReferencePoint()
        dx, dy = offsets[direction]
        sheet = self._view_sheet(view)
        target = [center.X + dx * spacing, center.Y + dy * spacing]
        point = self._sheet_point3d(sheet, target)
        b = self._work_part().DraftingViews.CreateProjectedViewBuilder(None)
        try:
            b.Parent.View.Value = view
            b.Placement.AlignmentMethod = (
                b.Placement.Method.Horizontal if dx else b.Placement.Method.Vertical
            )
            b.Placement.AlignmentOption = b.Placement.Option.ToView
            b.Placement.AlignmentView.Value = view
            b.Placement.Associative = True
            b.Placement.Placement.SetValue(None, None, point)
            result = b.Commit()
        finally:
            b.Destroy()
        self._place_drawing_view(result, sheet, target, axes=(0,) if dx else (1,))
        return {
            "object": self._reference(result, "drawing_view", self._work_part(), "Projected view"),
            "view_name": result.Name,
            "base_view": base_view,
            **self._drawing_coordinates(
                sheet, [result.GetDrawingReferencePoint().X, result.GetDrawingReferencePoint().Y]
            ),
            "alignment": "native associative alignment; the constrained reference coordinate can differ from the parent",
            "direction": direction,
            "spacing": spacing,
            "spacing_mm": spacing
            * (25.4 if self._sheet_units(self._view_sheet(view)) == "in" else 1),
            "sheet_units": self._sheet_units(self._view_sheet(view)),
        }

    def _add_dimension(
        self, view, object1, object2=None, dim_type="aligned", origin=None, dimension=None
    ):
        methods = {"aligned": "PointToPoint", "horizontal": "Horizontal", "vertical": "Vertical"}
        if dim_type not in methods:
            raise NXToolError(
                "NX_UNSUPPORTED_ARGUMENT", "Use aligned, horizontal or vertical linear dimensions"
            )
        drawing_view = self._drawing_object(view, "drawing_view")
        a = self._drawing_dimension_edge(object1)
        b = self._drawing_dimension_edge(object2) if object2 else a
        pa = a.GetVertices()[0]
        pb = b.GetVertices()[1] if object2 is None else b.GetVertices()[0]
        point = [100.0, 80.0] if origin is None else [finite(v, "origin") for v in origin]
        if len(point) != 2:
            raise NXToolError("NX_INVALID_ARGUMENT", "origin must be [x,y] in sheet units")
        existing = self._engineering_owned(dimension, "dimension") if dimension else None
        builder = self._work_part().Dimensions.CreateLinearDimensionBuilder(existing)
        try:
            snap = self.nxopen.InferSnapType.SnapType
            empty = self.nxopen.Point3d(0.0, 0.0, 0.0)
            builder.FirstAssociativity.SetValue(snap.Start, a, drawing_view, pa, None, None, empty)
            builder.SecondAssociativity.SetValue(
                snap.End if object2 is None else snap.Start, b, drawing_view, pb, None, None, empty
            )
            builder.Measurement.Method = getattr(
                builder.Measurement.MeasurementMethod, methods[dim_type]
            )
            builder.Origin.OriginPoint = self._sheet_point3d(self._view_sheet(drawing_view), point)
            result = builder.Commit()
        finally:
            builder.Destroy()
        return {
            "object": self._reference(result, "dimension", self._work_part(), "Drawing dimension"),
            "dimension_name": result.Name,
            "view": view,
            "dim_type": dim_type,
            "measured_value": result.ComputedSize,
            **self._drawing_coordinates(self._view_sheet(drawing_view), point, "origin"),
            "units": self._units(),
            "association": "edge start/end" if object2 is None else "edge start points",
            "edited": dimension is not None,
        }
