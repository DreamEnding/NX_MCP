"""Native thread and geometric manufacturing inspection operations."""

from __future__ import annotations

from nx_mcp.authoring import finite
from nx_mcp.runtime import NXToolError


class ManufacturingMixin:
    def _thread(
        self,
        face,
        start_face,
        pitch,
        major_diameter,
        minor_diameter,
        length,
        angle=60.0,
        detailed=False,
        left_hand=False,
        starts=1,
        reverse=False,
    ):
        import NXOpen.Features as F

        target = self._engineering_owned(face, "face")
        start = self._engineering_owned(start_face, "face")
        import NXOpen.UF as U

        cylinder = U.UFSession.GetUFSession().Modeling.AskFaceData(target.Tag)
        if cylinder[0] != 16 or start.GetBody() != target.GetBody():
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Select a cylindrical face and a start face on the same body"
            )
        diameter = 2 * cylinder[4]
        values = {
            k: finite(v, k, True)
            for k, v in {
                "pitch": pitch,
                "major_diameter": major_diameter,
                "minor_diameter": minor_diameter,
                "length": length,
                "angle": angle,
            }.items()
        }
        if (
            values["minor_diameter"] >= values["major_diameter"]
            or angle >= 180
            or type(starts) is not int
            or not 1 <= starts <= 16
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Require minor < major, angle < 180 degrees and 1..16 starts"
            )
        b = self._freeform_builder("CreateThreadBuilder")
        try:
            b.ThreadInput = F.ThreadBuilder.Input.Manual
            b.SmartThread = False
            b.ThreadType = (
                F.ThreadBuilder.Type.Detailed if detailed else F.ThreadBuilder.Type.Symbolic
            )
            b.CylindricalFace.Value = target
            b.StartObject.Value = start
            b.TapDrillDiameterExp.RightHandSide = str(diameter)
            b.ShaftDiameterExp.RightHandSide = str(diameter)
            b.ThreadLimit = F.ThreadBuilder.LimitOption.Value
            b.ThreadHandedness = (
                F.ThreadBuilder.Handedness.LeftHand
                if left_hand
                else F.ThreadBuilder.Handedness.RightHand
            )
            b.NumStarts = starts
            b.ReverseThreadDirection = reverse
            for prop, key in [
                ("PitchExp", "pitch"),
                ("MajorDiameterExp", "major_diameter"),
                ("MinorDiameterExp", "minor_diameter"),
                ("ThreadLength", "length"),
                ("AngleExp", "angle"),
            ]:
                getattr(b, prop).RightHandSide = str(values[key])
            result = self._freeform_commit(b)
            result.update(
                {
                    "representation": "detailed" if detailed else "symbolic",
                    "internal": b.IsInternalThread,
                    "pitch": b.Pitch,
                    "major_diameter": b.MajorDiameter,
                    "minor_diameter": b.MinorDiameter,
                    "length": b.ThreadLength.Value,
                    "starts": b.NumStarts,
                }
            )
            return result
        finally:
            b.Destroy()

    def _pmi_datum(self, faces, letter, position, annotation=None):
        import re

        from nx_mcp.freeform import points3

        targets = self._freeform_refs(faces, "face")
        if not isinstance(letter, str) or not re.fullmatch(r"[A-Z]{1,3}", letter):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Datum identifier must be 1..3 uppercase letters"
            )
        point = points3([position])[0]
        existing = self._engineering_owned(annotation, "annotation") if annotation else None
        b = self._work_part().Annotations.Datums.CreatePmiDatumFeatureSymbolBuilder(existing)
        try:
            b.Letter = letter
            return self._commit_pmi(b, targets, point, existing)
        finally:
            b.Destroy()

    def _pmi_fcf(
        self,
        faces,
        characteristic,
        tolerance,
        position,
        datums=None,
        annotation=None,
        material="none",
        zone_shape="none",
        datum_material=None,
        projected_height=None,
        tangent_plane=False,
        free_state=False,
    ):
        import NXOpen.Annotations as A

        from nx_mcp.freeform import points3

        allowed = {
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
        }
        if characteristic not in allowed:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported geometric characteristic")
        tolerance = finite(tolerance, "tolerance", True)
        targets = self._freeform_refs(faces, "face")
        point = points3([position])[0]
        if datums is not None and (not isinstance(datums, list) or len(datums) > 3):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "At most three datum annotation IDs are supported"
            )
        refs = [self._engineering_owned(r, "annotation") for r in (datums or [])]
        if characteristic in {"Straightness", "Flatness", "Circularity", "Cylindricity"} and refs:
            raise NXToolError("NX_INVALID_ARGUMENT", "Form tolerances do not take datum references")
        letters = []
        for obj in refs:
            reader = self._work_part().Annotations.Datums.CreatePmiDatumFeatureSymbolBuilder(obj)
            try:
                letters.append(reader.Letter)
            finally:
                reader.Destroy()
        modifiers = {
            "none": "NotSet",
            "MMC": "MaximumMaterialCondition",
            "LMC": "LeastMaterialCondition",
            "RFS": "RegardlessOfFeatureSize",
        }
        zones = {
            "none": "NotSet",
            "diameter": "Diameter",
            "spherical_diameter": "SphericalDiameter",
            "square": "Square",
        }
        if material not in modifiers or zone_shape not in zones:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown tolerance modifier or zone shape")
        if datum_material is None:
            datum_material = ["none"] * len(refs)
        if len(datum_material) != len(refs) or any(x not in modifiers for x in datum_material):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Specify one supported material modifier per datum"
            )
        if projected_height is not None:
            projected_height = finite(projected_height, "projected_height", True)
        if characteristic in {"Straightness", "Flatness", "Circularity", "Cylindricity"} and (
            material != "none" or projected_height is not None
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "These form-tolerance modifier combinations are not supported",
            )
        existing = self._engineering_owned(annotation, "annotation") if annotation else None
        b = self._work_part().Annotations.CreatePmiFeatureControlFrameBuilder(existing)
        try:
            b.Characteristic = getattr(
                A.FeatureControlFrameBuilder.FcfCharacteristic, characteristic
            )
            b.FrameStyle = A.FeatureControlFrameBuilder.FcfFrameStyle.SingleFrame
            frame = b.FeatureControlFrameDataList.FindItem(0)
            frame.ToleranceValue = str(tolerance)
            frame.MaterialModifier = getattr(
                A.FeatureControlFrameDataBuilder.ToleranceMaterialModifier, modifiers[material]
            )
            frame.ZoneShape = getattr(
                A.FeatureControlFrameDataBuilder.ToleranceZoneShape, zones[zone_shape]
            )
            frame.Projected = projected_height is not None
            frame.ProjectedValue = str(projected_height or 0)
            frame.TangentPlane = tangent_plane
            frame.FreeState = free_state
            datum_fields = [
                "PrimaryDatumReference",
                "SecondaryDatumReference",
                "TertiaryDatumReference",
            ]
            for index, name in enumerate(datum_fields):
                datum = getattr(frame, name)
                datum.Letter = letters[index] if index < len(letters) else ""
                datum.MaterialCondition = getattr(
                    A.DatumReferenceBuilder.DatumReferenceMaterialCondition,
                    modifiers[datum_material[index] if index < len(datum_material) else "none"],
                )
            result = self._commit_pmi(b, targets, point, existing)
            result.update(
                {
                    "characteristic": characteristic,
                    "tolerance": tolerance,
                    "datum_letters": letters,
                    "material": material,
                    "zone_shape": zone_shape,
                    "datum_material": datum_material,
                    "projected_height": projected_height,
                    "tangent_plane": tangent_plane,
                    "free_state": free_state,
                }
            )
            return result
        finally:
            b.Destroy()

    def _commit_pmi(self, builder, targets, position, existing):
        builder.AssociatedObjects.Nxobjects.Clear()
        builder.AssociatedObjects.Nxobjects.Add(targets)
        builder.Origin.SetInferRelativeToGeometry(True)
        builder.Origin.Origin.SetValue(None, None, self.nxopen.Point3d(*position))
        if not builder.Validate():
            raise NXToolError("NX_ANNOTATION_INVALID", "Native PMI builder validation failed")
        obj = builder.Commit()
        self._update_model()
        if obj is None:
            raise NXToolError("NX_VERIFICATION_FAILED", "NX returned no PMI object")
        ref = self._reference(obj, "annotation", self._work_part(), "PMI")
        return {
            "annotation": ref,
            "created": [] if existing else [ref],
            "modified": [ref] if existing else [],
            "units": self._units(),
            "coordinate_frame": "work_part",
            "geometry_associated": True,
        }

    def _face_samples(self, faces, samples_per_axis):
        import NXOpen.UF as U

        if type(samples_per_axis) is not int or not 1 <= samples_per_axis <= 20:
            raise NXToolError("NX_INVALID_ARGUMENT", "samples_per_axis must be 1..20")
        if len(faces) * samples_per_axis**2 > 10000:
            raise NXToolError(
                "NX_SAMPLE_LIMIT", "Select fewer faces or lower sample density (maximum 10000)"
            )
        uf = U.UFSession.GetUFSession().Modeling
        samples = []
        skipped = 0
        for face in faces:
            bounds = uf.AskFaceUvMinmax(face.Tag)
            for i in range(samples_per_axis):
                for j in range(samples_per_axis):
                    uv = [
                        bounds[0] + (bounds[1] - bounds[0]) * (i + 0.5) / samples_per_axis,
                        bounds[2] + (bounds[3] - bounds[2]) * (j + 0.5) / samples_per_axis,
                    ]
                    data = uf.AskFaceProps(face.Tag, uv)
                    if uf.AskPointContainment(data[0], face.Tag) == 2:
                        skipped += 1
                        continue
                    samples.append((face, uv, data))
        return samples, skipped

    def _face_analysis(self, faces, samples_per_axis=3, pull_direction=None, minimum_draft=1.0):
        import math

        from nx_mcp.visual_tools import unit_normal

        objects = self._freeform_refs(faces, "face")
        direction = unit_normal(pull_direction) if pull_direction is not None else None
        minimum = finite(minimum_draft, "minimum_draft")
        if not 0 <= minimum < 90 or (direction is None and minimum_draft != 1.0):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "minimum_draft requires a pull direction and must be 0..<90 degrees",
            )
        samples, skipped = self._face_samples(objects, samples_per_axis)
        reports = []
        for face, uv, data in samples:
            point, _, _, _, _, normal, radii = data
            curvature = [0.0 if abs(r) > 1e25 else 1 / r if abs(r) > 1e-12 else None for r in radii]
            record = {
                "face": self._reference(face, "face", self._work_part(), "Face"),
                "uv": uv,
                "point": list(point),
                "normal": list(normal),
                "principal_radii": [None if abs(r) > 1e25 else r for r in radii],
                "principal_curvatures": curvature,
            }
            if direction is not None:
                cosine = sum(a * b for a, b in zip(normal, direction, strict=False))
                draft = math.degrees(math.asin(max(-1.0, min(1.0, cosine))))
                record.update(
                    {
                        "signed_draft_degrees": draft,
                        "classification": "negative"
                        if draft < -1e-7
                        else "below_minimum"
                        if draft < minimum
                        else "positive",
                    }
                )
            reports.append(record)
        return {
            "samples": reports,
            "sample_count": len(reports),
            "skipped_outside_trim": skipped,
            "units": self._units(),
            "curvature_units": "1/" + self._units(),
            "coordinate_frame": "work_part",
            "pull_direction": direction,
            "minimum_draft_degrees": minimum if direction is not None else None,
            "method": "native face derivatives at a trimmed UV grid; sampled values, not certified global extrema or mold-release feasibility",
        }

    def _wall_thickness(self, body, faces=None, samples_per_axis=3, tolerance=0.001):
        import math

        import NXOpen.UF as U

        target = self._engineering_owned(body, "body")
        if not target.IsSolidBody:
            raise NXToolError("NX_NOT_SOLID", "Wall thickness requires a solid body")
        tolerance = finite(tolerance, "tolerance", True)
        selected = (
            self._freeform_refs(faces, "face") if faces is not None else list(target.GetFaces())
        )
        if any(f.GetBody() != target for f in selected):
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Faces must belong to the selected body")
        samples, skipped = self._face_samples(selected, samples_per_axis)
        uf = U.UFSession.GetUFSession().Modeling
        face_map = {int(f.Tag): f for f in target.GetFaces()}
        identity = [1.0 if i % 5 == 0 else 0.0 for i in range(16)]
        reports = []
        for face, _uv, data in samples:
            point, normal = data[0], data[5]
            direction = [-v for v in normal]
            origin = [p + tolerance * d for p, d in zip(point, direction, strict=False)]
            if uf.AskPointContainment(origin, target.Tag) != 1:
                reports.append(
                    {
                        "source_face": self._reference(face, "face", self._work_part(), "Face"),
                        "point": list(point),
                        "status": "unresolved",
                        "reason": "Inward offset is not inside solid; decrease tolerance or sample away from boundaries",
                    }
                )
                continue
            _, hits = uf.TraceARay(1, [target.Tag], origin, direction, identity, 0)
            hit = next(
                (
                    h
                    for h in hits
                    if sum(
                        (a - b) * d for a, b, d in zip(h.HitPoint, origin, direction, strict=False)
                    )
                    > 0
                ),
                None,
            )
            record = {
                "source_face": self._reference(face, "face", self._work_part(), "Face"),
                "point": list(point),
                "status": "unresolved",
            }
            if hit is not None:
                distance = math.dist(point, hit.HitPoint)
                record.update(
                    {
                        "status": "measured",
                        "thickness": distance,
                        "opposite_point": list(hit.HitPoint),
                        "opposite_face": self._reference(
                            face_map[int(hit.HitFace)], "face", self._work_part(), "Face"
                        ),
                    }
                )
            reports.append(record)
        distances = [x["thickness"] for x in reports if x["status"] == "measured"]
        return {
            "body": self._reference(target, "body", self._work_part(), "Body"),
            "samples": reports,
            "sample_count": len(reports),
            "measured_count": len(distances),
            "skipped_outside_trim": skipped,
            "minimum_sampled_thickness": min(distances) if distances else None,
            "maximum_sampled_thickness": max(distances) if distances else None,
            "tolerance": tolerance,
            "units": self._units(),
            "coordinate_frame": "work_part",
            "method": "first exit along inward native face normal; sampled wall distance, not global minimum thickness or rolling-ball thickness",
        }
