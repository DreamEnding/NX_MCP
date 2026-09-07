"""Native sheet-metal authoring through reviewed NX 2606 builder contracts."""

from __future__ import annotations

import json
from pathlib import Path

from nx_mcp.authoring import finite, page
from nx_mcp.runtime import NXToolError
from nx_mcp.visual_tools import enum_name, unit_normal

CATALOG = json.loads(Path(__file__).with_name("sheet_metal_catalog.json").read_text())
APPLICATION = "UG_APP_SBSM"


def field_schema(field):
    kind = field["kind"]
    if kind == "section":
        return {
            "oneOf": [
                {"type": "string", "description": "Finished work-part sketch ID"},
                *[
                    {
                        "type": "object",
                        "properties": {
                            objects: field_schema({"kind": "reference_list"}),
                            "help_point": field_schema({"kind": "point3d"}),
                        },
                        "required": [objects, "help_point"],
                        "additionalProperties": False,
                    }
                    for objects in ("edges", "curves")
                ],
            ]
        }
    if kind in {"expression", "number"}:
        return {"type": "number"}
    if kind in {"boolean", "integer", "string"}:
        return {"type": kind}
    if kind == "enum":
        return {"type": "string", "enum": field["values"]}
    if kind == "object":
        return fields_schema(field["fields"])
    if kind in {"flange_list", "joggle_list"}:
        return {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": fields_schema(
                field["fields"],
                field["required"],
            ),
        }
    if kind == "point_section":
        return {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": field_schema({"kind": "point3d"}),
        }
    if kind == "object_list":
        return {
            "type": "array",
            "minItems": 0,
            "maxItems": 100,
            "items": fields_schema(field["fields"], field["required"]),
        }
    if kind == "face_pairs":
        return {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string"}},
        }
    if kind in {"point", "point3d", "direction"}:
        return {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}}
    if kind in {"plane", "csys"}:
        props = {"origin": field_schema({"kind": "point3d"})}
        props.update(
            {
                key: field_schema({"kind": "direction"})
                for key in (["normal"] if kind == "plane" else ["x_axis", "y_axis"])
            }
        )
        return {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        }
    if kind in {"collector", "reference_list", "select_faces", "select_edges", "select_bodies"}:
        return {
            "type": "array",
            "minItems": 1,
            "maxItems": 1000,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "description": "Typed work-part " + field.get("objects", "geometry") + " ID",
            },
        }
    return {
        "type": "string",
        "description": "Finished work-part sketch ID"
        if kind == "section"
        else "Typed work-part " + kind + " ID",
    }


def fields_schema(fields, required=()):
    return {
        "type": "object",
        "properties": {
            k: {
                **field_schema(v),
                **({"description": v["description"]} if "description" in v else {}),
            }
            for k, v in fields.items()
        },
        "required": list(required),
        "additionalProperties": False,
    }


class SheetMetalMixin:
    def _create_path_sketch(
        self,
        edges,
        help_point,
        percent=0,
        orienting_face=None,
        reverse_normal=False,
        reverse_axis=False,
        name=None,
    ):
        import NXOpen.GeometricUtilities as G

        part = self._work_part()
        if self.session.ActiveSketch:
            raise NXToolError("NX_SKETCH_ACTIVE", "Finish the active sketch first")
        if not self.session.IsBatch and self.session.Parts.Display != part:
            raise NXToolError("NX_PART_CONTEXT", "Activate the same work and display part")
        values = self._sm_validate(
            {
                "edges": {"kind": "reference_list", "objects": "edge"},
                "help_point": {"kind": "point3d"},
                "percent": {"kind": "number"},
                "reverse_normal": {"kind": "boolean"},
                "reverse_axis": {"kind": "boolean"},
            },
            {
                "edges": edges,
                "help_point": help_point,
                "percent": percent,
                "reverse_normal": reverse_normal,
                "reverse_axis": reverse_axis,
            },
        )
        if not 0 <= values["percent"] <= 100:
            raise NXToolError("NX_INVALID_ARGUMENT", "Path percentage must be within 0..100")
        if name is not None and (
            not isinstance(name, str)
            or not name
            or len(name) > 132
            or any(ord(c) < 32 for c in name)
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Use a nonempty sketch name without control characters"
            )
        face = self._sm_reference(orienting_face, "face") if orienting_face else None
        self._require_api(part.Sketches, "CreateSketchAlongPathBuilder")
        builder = part.Sketches.CreateSketchAlongPathBuilder(None)
        try:
            self._sm_section(
                builder.Section, {"edges": values["edges"], "help_point": values["help_point"]}
            )
            builder.PlaneOrientation = (
                self.nxopen.SketchAlongPathBuilder.PlaneOrientationType.NormalToPath
            )
            builder.SketchOrient = getattr(
                self.nxopen.SketchAlongPathBuilder.SketchOrientationType,
                "RelativeToFace" if face else "Automatic",
            )
            if face:
                builder.OrientingFace.ReplaceRules(
                    [part.ScRuleFactory.CreateRuleFaceDumb([face])], False
                )
            location = builder.PlaneLocation
            location.IsParameterUsed = False
            location.IsPercentUsed = True
            location.Expression.RightHandSide = str(values["percent"])
            location.Update(G.OnPathDimensionBuilder.UpdateReason.Path)
            builder.ReversePlaneNormal = values["reverse_normal"]
            builder.ReverseAxis = values["reverse_axis"]
            if not builder.Validate():
                raise NXToolError("NX_SKETCH_INVALID", "Native path sketch validation failed")
            sketch = builder.Commit()
            if name:
                sketch.SetName(name)
            self._update_model()
            sketch.Activate(self.nxopen.Sketch.ViewReorient.FalseValue)
            return {
                "object": self._reference(sketch, "sketch", part, "Path sketch"),
                "frame": self._sketch_frame(sketch),
                "percent": values["percent"],
                "position_convention": "arc_length_percent",
                "units": self._units(),
            }
        finally:
            builder.Destroy()

    def _sm_manager(self):
        part = self._work_part()
        self._require_api(part.Features, "SheetmetalManager")
        return part.Features.SheetmetalManager

    def _sm_prepare(self):
        part = self._work_part()
        if not self.session.IsBatch and self.session.Parts.Display != part:
            raise NXToolError("NX_PART_CONTEXT", "Activate the same work and display part")
        if self.session.ActiveSketch:
            raise NXToolError("NX_SKETCH_ACTIVE", "Finish the sketch before sheet-metal authoring")
        if not self.session.IsBatch and self.session.ApplicationName != APPLICATION:
            self.session.ApplicationSwitchImmediate(APPLICATION)
            if self.session.ApplicationName != APPLICATION:
                raise NXToolError("NX_APPLICATION_UNAVAILABLE", "NX did not enter Sheet Metal")
        return part

    def _sheet_metal_context(self):
        self._sm_prepare()
        return {
            "application": "batch" if self.session.IsBatch else self.session.ApplicationName,
            "recovery": self._checkpoint_state(),
            "message": "Native Sheet Metal context is active",
        }

    def _sm_require_context(self):
        part = self._work_part()
        if self.session.ActiveSketch:
            raise NXToolError("NX_SKETCH_ACTIVE", "Finish the sketch before sheet-metal authoring")
        if not self.session.IsBatch and (
            self.session.ApplicationName != APPLICATION or self.session.Parts.Display != part
        ):
            raise NXToolError(
                "NX_PART_CONTEXT",
                "Activate the work/display part and call nx_sheet_metal_context first",
            )

    def _sheet_metal_schema(self, operation=None):
        if operation is None:
            return {
                "operations": [
                    {
                        "operation": op,
                        "status": spec["native_status"],
                        "builder": spec["builder"],
                        "tested_on": spec.get("tested_on"),
                        "edit_status": spec.get("edit_status", "experimental"),
                    }
                    for op, spec in CATALOG.items()
                ],
                "units": None,
                "unit_conventions": "Lengths use work-part units; expression angles are degrees; neutral factor is unitless",
                "unavailable": [
                    {
                        "operation": "remove_bends",
                        "reason": "NX v2606 installation feature toggle is disabled",
                    }
                ],
                "coordinate_frame": "work_part",
                "not_exposed": ["metaform", "nesting"],
            }
        if operation not in CATALOG:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown sheet-metal operation")
        spec = CATALOG[operation]
        return {
            "operation": operation,
            "parameters_schema": fields_schema(spec["fields"], spec["required"]),
            "edit_parameters_schema": fields_schema(spec["fields"]),
            "native_builder": spec["builder"],
            "validation_status": spec["native_status"],
            "tested_on": spec.get("tested_on"),
            "validation_scope": spec.get("validation_scope"),
            "edit_status": spec.get("edit_status", "experimental"),
            "example_parameters": spec.get("example_parameters"),
            "example_evidence": spec.get("example_evidence"),
            "example_note": "$input_N values are placeholders; select matching geometry from your own fixture",
            "defaults": "Unspecified properties retain native part/builder defaults; read feature parameters after creation.",
            "units": None,
            "coordinate_frame": "work_part",
            "unit_conventions": "Lengths use work-part units; expression angles are degrees; neutral factor is unitless",
            "prerequisites": [
                "Activate the owning work/display part and call nx_sheet_metal_context before authoring.",
                "Select work-part geometry IDs with nx_find_geometry or nx_list_topology; assembly occurrences are rejected.",
                "Finish section sketches before passing their IDs. Operation-specific geometry must match the native builder and tested example.",
            ]
            + spec.get("prerequisites", []),
        }

    def _sm_reference(self, reference, kind):
        if not isinstance(reference, str):
            raise NXToolError("NX_INVALID_ARGUMENT", "Use typed object IDs")
        kinds = {"face", "edge"} if kind == "face_or_edge" else {kind}
        obj = self._resolve(reference, kinds)
        if obj.IsOccurrence or obj.OwningPart != self._work_part():
            raise NXToolError(
                "NX_OBJECT_OWNER_MISMATCH", "Sheet-metal inputs must belong to the work part"
            )
        if kind == "sketch" and obj == self.session.ActiveSketch:
            raise NXToolError("NX_SKETCH_ACTIVE", "Finish the input sketch")
        return obj

    def _sm_validate(self, fields, parameters, required=()):
        if (
            not isinstance(parameters, dict)
            or set(parameters) - set(fields)
            or set(required) - set(parameters)
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Missing or unsupported sheet-metal parameters",
                details={"required": list(required), "supported": list(fields)},
            )
        result = {}
        for key, value in parameters.items():
            f = fields[key]
            kind = f["kind"]
            if kind == "section" and isinstance(value, dict):
                source = "edges" if "edges" in value else "curves"
                value = self._sm_validate(
                    {
                        source: {"kind": "reference_list", "objects": source[:-1]},
                        "help_point": {"kind": "point3d"},
                    },
                    value,
                    (source, "help_point"),
                )
            elif kind in {"expression", "number"}:
                value = finite(value, key)
                if "neutral_factor" in key and not 0 <= value <= 1:
                    raise NXToolError("NX_INVALID_ARGUMENT", "Neutral factor must be within 0–1")
                if "thickness" in key and value <= 0:
                    raise NXToolError("NX_INVALID_ARGUMENT", "Thickness must be positive")
                if "radius" in key and value < 0:
                    raise NXToolError("NX_INVALID_ARGUMENT", "Radius cannot be negative")
            elif kind == "integer":
                if type(value) is not int:
                    raise NXToolError("NX_INVALID_ARGUMENT", key + " must be an integer")
            elif kind == "boolean":
                if type(value) is not bool:
                    raise NXToolError("NX_INVALID_ARGUMENT", key + " must be boolean")
            elif kind == "enum":
                if not isinstance(value, str) or value not in f["values"]:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", key + " must be one of " + ", ".join(f["values"])
                    )
            elif kind == "string":
                if (
                    not isinstance(value, str)
                    or len(value) > 256
                    or any(ord(c) < 32 for c in value)
                ):
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT",
                        key + " must be a short string without control characters",
                    )
            elif kind == "object":
                value = self._sm_validate(f["fields"], value)
            elif kind == "point_section":
                if not isinstance(value, list) or not 1 <= len(value) <= 100:
                    raise NXToolError("NX_INVALID_ARGUMENT", "Use 1–100 point coordinates")
                value = [
                    self._sm_validate({"point": {"kind": "point3d"}}, {"point": p})["point"]
                    for p in value
                ]
            elif kind == "object_list":
                if not isinstance(value, list) or len(value) > 100:
                    raise NXToolError("NX_INVALID_ARGUMENT", key + " requires at most 100 entries")
                value = [self._sm_validate(f["fields"], item, f["required"]) for item in value]
            elif kind in {"flange_list", "joggle_list"}:
                if not isinstance(value, list) or not 1 <= len(value) <= 100:
                    raise NXToolError("NX_INVALID_ARGUMENT", key + " requires 1–100 entries")
                required_item = f["required"]
                value = [self._sm_validate(f["fields"], item, required_item) for item in value]
            elif kind in {
                "collector",
                "reference_list",
                "select_faces",
                "select_edges",
                "select_bodies",
                "face_pairs",
            }:
                if not isinstance(value, list) or not 1 <= len(value) <= 1000:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", key + " requires a nonempty reference list"
                    )
                if kind == "face_pairs":
                    if len(value) > 100:
                        raise NXToolError("NX_INVALID_ARGUMENT", "Use at most 100 face pairs")
                    if any(
                        not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                        for pair in value
                    ):
                        raise NXToolError("NX_INVALID_ARGUMENT", "Use distinct face pairs")
                    value = [[self._sm_reference(r, "face") for r in pair] for pair in value]
                else:
                    object_kind = (
                        f.get("objects")
                        or {
                            "select_faces": "face",
                            "select_edges": "edge",
                            "select_bodies": "body",
                        }[kind]
                    )
                    if any(not isinstance(r, str) for r in value) or len(set(value)) != len(value):
                        raise NXToolError(
                            "NX_INVALID_ARGUMENT", "References must be unique strings"
                        )
                    value = [self._sm_reference(r, object_kind) for r in value]
            elif kind in {"point", "point3d", "direction"}:
                if not isinstance(value, list) or len(value) != 3:
                    raise NXToolError("NX_INVALID_ARGUMENT", key + " requires three numbers")
                value = [finite(v, key) for v in value]
                if kind == "direction":
                    value = unit_normal(value)
            elif kind in {"plane", "csys"}:
                keys = ["origin", "normal"] if kind == "plane" else ["origin", "x_axis", "y_axis"]
                if not isinstance(value, dict) or set(value) != set(keys):
                    raise NXToolError("NX_INVALID_ARGUMENT", key + " requires " + ", ".join(keys))
                value = self._sm_validate(
                    {k: {"kind": "point3d" if k == "origin" else "direction"} for k in keys}, value
                )
                if kind == "csys":
                    from nx_mcp.hardened import dot

                    if abs(dot(value["x_axis"], value["y_axis"])) > 1e-8:
                        raise NXToolError(
                            "NX_INVALID_ARGUMENT", "Coordinate-system axes must be orthogonal"
                        )
            else:
                reference_kind = "sketch" if kind == "section" else kind.removeprefix("select_")
                value = self._sm_reference(value, reference_kind)
            result[key] = value
        return result

    def _sm_section(self, section, sketch):
        part = self._work_part()
        if isinstance(sketch, dict):
            source = "edges" if "edges" in sketch else "curves"
            factory = getattr(part.ScRuleFactory, "CreateRule" + source[:-1].title() + "Dumb")
            rule = factory(sketch[source])
            section.Clear()
            section.AddToSection(
                [rule],
                sketch[source][0],
                None,
                None,
                self.nxopen.Point3d(*sketch["help_point"]),
                self.nxopen.Section.Mode.Create,
                False,
            )
            return
        options = part.ScRuleFactory.CreateRuleOptions()
        try:
            rule = part.ScRuleFactory.CreateRuleCurveFeature([sketch.Feature], None, options)
        finally:
            options.Dispose()
        section.Clear()
        section.AddToSection(
            [rule], None, None, None, sketch.Origin, self.nxopen.Section.Mode.Create, False
        )

    def _sm_apply(self, builder, fields, values):
        import NXOpen.Features.SheetMetal as SM

        part = self._work_part()
        for key, value in values.items():
            f = fields[key]
            kind = f["kind"]
            if kind == "reference_list":
                getattr(builder, f["method"])(value)
                continue
            if kind == "face_pairs":
                count = (
                    builder.GetNumberOfFacePairs()
                    if hasattr(builder, "GetNumberOfFacePairs")
                    else builder.NumberOfFacePairs
                )
                pairs = [builder.GetFacePair(i) for i in range(count)]
                for pair in pairs:
                    builder.RemoveFacePair(*pair)
                for pair in value:
                    builder.AddFacePair(*pair)
                continue
            if kind in {"flange_list", "joggle_list", "object_list"}:
                if kind == "object_list":
                    container = getattr(builder, f["container"]) if f["container"] else builder
                    sequence = getattr(container, f["sequence"])
                    create = getattr(container, f["creator"])
                elif kind == "flange_list":
                    container = builder.FlangePropertiesList
                    sequence = container.FeatureBendPropertiesList
                    create = container.CreateFlangeBendProperties
                else:
                    sequence = builder.InputList
                    create = builder.CreateJoggleInputListItem
                sequence.Clear(self.nxopen.ObjectList.DeleteOption.Delete)
                for item in value:
                    entry = create()
                    sequence.Append(entry)
                    self._sm_apply(entry, f["fields"], item)
                continue
            path = f["path"]
            # Some assignable native sections throw when read before initialization.
            # Only read properties whose existing builder object we actually mutate.
            needs_target = kind in {
                "expression",
                "object",
                "section",
                "point_section",
                "collector",
                "select_faces",
                "select_edges",
                "select_bodies",
            } or kind.startswith("select_")
            target = getattr(builder, path) if needs_target and not f.get("assign") else None
            if f.get("getter"):
                target = target()
            if kind == "expression":
                target.RightHandSide = str(value)
            elif kind == "object":
                self._sm_apply(target, f["fields"], value)
            elif kind == "enum":
                setattr(builder, path, getattr(getattr(SM, f["enum_type"]), value))
            elif kind == "section":
                if f.get("assign"):
                    if isinstance(value, dict):
                        section = part.Sections.CreateSection()
                        setattr(builder, path, section)
                        self._sm_section(section, value)
                    else:
                        setattr(builder, path, self._engineering_section(value))
                else:
                    self._sm_section(target, value)
            elif kind == "point_section":
                points = [part.Points.CreatePoint(self.nxopen.Point3d(*p)) for p in value]
                for point in points:
                    point.Blank()
                rule = part.ScRuleFactory.CreateRuleCurveDumbFromPoints(points)
                target.Clear()
                target.AddToSection(
                    [rule],
                    None,
                    None,
                    None,
                    self.nxopen.Point3d(*value[0]),
                    self.nxopen.Section.Mode.Create,
                    False,
                )
            elif kind == "collector":
                if f.get("assign"):
                    target = part.ScCollectors.CreateCollector()
                    setattr(builder, path, target)
                factory = getattr(part.ScRuleFactory, "CreateRule" + f["objects"].title() + "Dumb")
                target.ReplaceRules([factory(value)], False)
            elif kind in {"select_faces", "select_edges", "select_bodies"}:
                target.Clear()
                target.Add(value)
            elif kind.startswith("select_"):
                target.Value = value
            elif kind == "point3d":
                setattr(builder, path, self.nxopen.Point3d(*value))
            elif kind == "point":
                setattr(builder, path, part.Points.CreatePoint(self.nxopen.Point3d(*value)))
            elif kind == "direction":
                setattr(builder, path, self._engineering_direction(value))
            elif kind == "plane":
                plane = part.Planes.CreatePlane(
                    self.nxopen.Point3d(*value["origin"]),
                    self.nxopen.Vector3d(*value["normal"]),
                    self.nxopen.SmartObject.UpdateOption.WithinModeling,
                )
                setattr(builder, path, plane)
            elif kind == "csys":
                from nx_mcp.hardened import cross, transpose

                matrix = self._nx_matrix(
                    transpose(
                        [value["x_axis"], value["y_axis"], cross(value["x_axis"], value["y_axis"])]
                    )
                )
                csys = part.CoordinateSystems.CreateCoordinateSystem(
                    self.nxopen.Point3d(*value["origin"]),
                    matrix,
                    False,
                )
                setattr(builder, path, csys)
            else:
                setattr(builder, path, value)

    def _sheet_metal_feature(self, operation, parameters, feature=None):
        if operation not in CATALOG:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown sheet-metal operation")
        spec = CATALOG[operation]
        self._sm_require_context()
        original = self._sm_reference(feature, "feature") if feature else None
        if original is not None:
            import NXOpen.Features.SheetMetal as SM

            native_type = getattr(SM, spec["builder"].removesuffix("Builder"), None)
            expected_type = {
                "tab": {"Base Tab", "Secondary Tab"},
                "flat_pattern": {"FLAT_PATTERN"},
            }.get(operation)
            if (native_type is not None and not isinstance(original, native_type)) or (
                expected_type is not None and original.FeatureType not in expected_type
            ):
                raise NXToolError(
                    "NX_OBJECT_TYPE_MISMATCH", "Feature does not match this sheet-metal operation"
                )
        values = self._sm_validate(spec["fields"], parameters, () if original else spec["required"])
        if operation == "tab" and (
            values.get("is_secondary")
            or (original is not None and original.FeatureType == "Secondary Tab")
        ):
            target = values.get("target_body")
            if target is None:
                bodies = list(original.GetBodies()) if original is not None else []
                if len(bodies) != 1:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", "Secondary tabs require an explicit target_body"
                    )
                target = bodies[0]
            if (
                "thickness" in values
                and abs(values["thickness"] - self._sm_manager().GetBodyThickness(target)) > 1e-8
            ):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Secondary tab thickness must match its target sheet"
                )
        if (
            operation == "advanced_flange"
            and original is None
            and values.get("type") == "ToReference"
            and not values.get("faces")
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "ToReference requires reference faces on the same sheet-metal body as the selected edges; a valid geometric combination is still required",
            )
        if not values:
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply sheet-metal parameters")
        manager = self._sm_manager()
        self._require_api(manager, spec["factory"])
        import NXOpen.Features.SheetMetal as SM

        b = getattr(manager, spec["factory"])(original)
        try:
            if original is None and hasattr(b, "SetApplicationContext"):
                b.SetApplicationContext(SM.ApplicationContext.NxSheetMetal)
            elif (
                original is not None
                and hasattr(b, "GetApplicationContext")
                and b.GetApplicationContext() != SM.ApplicationContext.NxSheetMetal
            ):
                raise NXToolError(
                    "NX_PART_CONTEXT", "Feature belongs to a different native application context"
                )
            self._sm_apply(b, spec["fields"], values)
            # NX 2606's legacy ValidateBuilderData returned varying nonzero/negative
            # values for an unchanged valid tab. Use the supported Builder.Validate
            # contract and transactional native commit instead (native api23 evidence).
            if not b.Validate():
                raise NXToolError("NX_SHEET_METAL_INVALID", "Native builder validation failed")
            f = b.CommitFeature()
            self._update_model()
            result = self._engineering_result(f)
            result.update(
                operation=operation,
                native_feature_type=f.FeatureType,
                requested_parameters=parameters,
                expressions=[self._expression_record(exp) for exp in f.GetExpressions()],
            )
            if operation == "flat_pattern":
                result["model_view_name"] = b.FlatPatternViewName
            return result
        finally:
            b.Destroy()

    def _sheet_metal_info(self, body=None, offset=0, limit=50):
        import NXOpen.Features.SheetMetal as SM

        manager = self._sm_manager()
        part = self._work_part()
        bodies = [self._sm_reference(body, "body")] if body else list(part.Bodies)
        result = []
        for b in bodies:
            record = {
                "body": self._reference(b, "body", part, "Body"),
                "sheet_metal": bool(manager.IsSheetmetalBody(b)),
            }
            if record["sheet_metal"]:
                faces, states = manager.GetInnerBendFaces(b)
                bends = []
                for face, state in zip(faces, states, strict=True):
                    params = manager.GetBendParameters(face)
                    bends.append(
                        {
                            "face": self._reference(face, "face", part, "Face"),
                            "state": enum_name(state, SM.SheetmetalBendState),
                            "inner_radius": params.InnerRadius,
                            "angle_degrees": params.BendAngle,
                            "neutral_factor": params.NeutralFactor,
                        }
                    )
                record.update(
                    thickness=manager.GetBodyThickness(b), bends=bends, bend_count=len(bends)
                )
            result.append(record)
        return {
            **page(result, offset, limit),
            "units": self._units(),
            "coordinate_frame": "work_part",
            "angle_convention": "degrees",
        }

    def _sheet_metal_defaults(self):
        import NXOpen.Preferences as P

        manager = self._work_part().Preferences.SheetMetalPreferences
        values = {}
        for key, method in {
            "thickness": "GetMaterialThickness",
            "bend_radius": "GetBendRadius",
            "neutral_factor": "GetNeutralFactor",
            "bend_relief_width": "GetBendReliefWidth",
            "bend_relief_depth": "GetBendReliefDepth",
        }.items():
            self._require_api(manager, method)
            expression = getattr(manager, method)()
            values[key] = self._expression_record(expression) if expression else None
        return {
            "parameters": values,
            "parameter_entry": enum_name(
                manager.GetParameterEntryType(), P.SheetMetalPreferencesBuilder.ParameterEntryTypes
            ),
            "bend_definition": enum_name(
                manager.GetBendDefinitionMethod(),
                P.SheetMetalPreferencesBuilder.BendDefinitionMethodOptions,
            ),
            "bend_table": manager.GetBendTable(),
            "bend_allowance_formula": manager.GetBendAllowanceFormula(),
            "bend_deduction_formula": manager.GetBendDeductionFormula(),
            "material": manager.GetMaterialName(),
            "tool": manager.GetToolName(),
            "material_catalog_status": "unavailable",
            "warnings": [
                "NX v2606 GetMaterialNames raised a native memory-access error on this installation; automatic catalog enumeration is disabled."
            ],
            "units": self._units(),
        }

    def _set_sheet_metal_defaults(
        self,
        parameter_entry=None,
        thickness=None,
        bend_radius=None,
        neutral_factor=None,
        bend_relief_width=None,
        bend_relief_depth=None,
        material=None,
        tool=None,
        bend_definition=None,
        bend_table=None,
        bend_allowance_formula=None,
        bend_deduction_formula=None,
    ):
        import NXOpen.Preferences as P

        numeric = {
            "MaterialThickness": thickness,
            "BendRadius": bend_radius,
            "NeutralFactor": neutral_factor,
            "BendReliefWidth": bend_relief_width,
            "BendReliefDepth": bend_relief_depth,
        }
        if all(
            v is None
            for v in [
                *numeric.values(),
                parameter_entry,
                material,
                tool,
                bend_definition,
                bend_table,
                bend_allowance_formula,
                bend_deduction_formula,
            ]
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Provide at least one sheet-metal default")
        for name, value in numeric.items():
            if value is not None:
                finite(value, name, name == "MaterialThickness")
                if value < 0 or name == "NeutralFactor" and value > 1:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT",
                        "Defaults require nonnegative lengths and a neutral factor within 0–1",
                    )
        manager = self._work_part().Preferences.SheetMetalPreferences
        for name in (material, tool):
            if name is not None and (
                not isinstance(name, str)
                or not name
                or len(name) > 256
                or any(ord(c) < 32 for c in name)
            ):
                raise NXToolError("NX_INVALID_ARGUMENT", "Use a valid installed material/tool name")
        for formula in (bend_allowance_formula, bend_deduction_formula):
            if formula is not None and (
                not isinstance(formula, str)
                or not formula
                or len(formula) > 1024
                or any(ord(c) < 32 for c in formula)
            ):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT",
                    "Use a nonempty NX bend formula without control characters",
                )
        table = self.workspace.resolve(bend_table) if bend_table is not None else None
        if table is not None and not table.is_file():
            raise NXToolError("NX_FILE_NOT_FOUND", "Upload the bend table to the workspace first")
        if parameter_entry is not None and parameter_entry not in {
            "Value",
            "MaterialTable",
            "ToolIdTable",
        }:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown sheet-metal parameter entry mode")
        bend_methods = {
            "NeutralFactorValue",
            "BendTable",
            "BendAllowanceFormula",
            "MaterialTable",
            "ToolTable",
            "BendAllowanceTable",
            "BendDeductionTable",
            "BendDeductionFormula",
            "Din6935Formula",
        }
        if bend_definition is not None and bend_definition not in bend_methods:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown native bend-definition method")
        b = manager.CreateSheetMetalPreferencesBuilder()
        try:
            if parameter_entry is not None:
                b.ParameterEntryType = getattr(
                    P.SheetMetalPreferencesBuilder.ParameterEntryTypes, parameter_entry
                )
            if material is not None:
                b.SetMaterial(material)
            if tool is not None:
                b.SetToolName(tool)
            if bend_definition is not None:
                choices = P.SheetMetalPreferencesBuilder.BendDefinitionMethodOptions
                b.SetBendDefinitionMethod(getattr(choices, bend_definition))
            if table is not None:
                b.SetBendTable(str(table))
            if bend_allowance_formula is not None:
                b.BendAllowanceFormula = bend_allowance_formula
            if bend_deduction_formula is not None:
                b.BendDeductionFormula = bend_deduction_formula
            for name, value in numeric.items():
                if value is not None:
                    getattr(b, name).RightHandSide = str(value)
            b.Commit()
            self._update_model()
        finally:
            b.Destroy()
        result = self._sheet_metal_defaults()
        for key, requested in {
            "material": material,
            "tool": tool,
            "parameter_entry": parameter_entry,
            "bend_definition": bend_definition,
        }.items():
            if requested is not None and result[key] != requested:
                raise NXToolError(
                    "NX_VERIFICATION_FAILED",
                    "Native sheet-metal default did not retain requested " + key,
                )
        for key, requested in {
            "thickness": thickness,
            "bend_radius": bend_radius,
            "neutral_factor": neutral_factor,
            "bend_relief_width": bend_relief_width,
            "bend_relief_depth": bend_relief_depth,
        }.items():
            if requested is not None:
                actual = result["parameters"][key]["value"]
                if actual is None or abs(actual - requested) > 1e-9 * max(1, abs(requested)):
                    raise NXToolError(
                        "NX_VERIFICATION_FAILED",
                        "Native sheet-metal default did not retain requested " + key,
                    )
        return result

    def _export_flat_pattern(
        self,
        flat_pattern,
        path,
        format="dxf",
        revision="R2018",
        bend_up=True,
        bend_down=True,
        bend_tangent=False,
        interior_cutout=True,
        interior_feature=False,
        inner_mold=False,
        outer_mold=False,
        added_top=False,
        added_bottom=False,
        tolerance=0.01,
    ):
        import hashlib
        import uuid

        import NXOpen.Features.SheetMetal as SM

        if format not in {"dxf", "geo"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Flat pattern format must be dxf or geo")
        destination = self.workspace.resolve(path)
        if destination.suffix.lower() != "." + format or destination.exists():
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Use an unused matching .dxf or .geo output path"
            )
        tolerance = finite(tolerance, "tolerance", True)
        feature = self._sm_reference(flat_pattern, "feature")
        if feature.FeatureType != "FLAT_PATTERN":
            raise NXToolError("NX_OBJECT_TYPE_MISMATCH", "Select a native Flat Pattern feature")
        options = {
            "BendUp": bend_up,
            "BendDown": bend_down,
            "BendTangent": bend_tangent,
            "InteriorCutout": interior_cutout,
            "InteriorFeature": interior_feature,
            "InnerMold": inner_mold,
            "OuterMold": outer_mold,
            "AddedTop": added_top,
            "AddedBottom": added_bottom,
        }
        if any(type(v) is not bool for v in options.values()):
            raise NXToolError("NX_INVALID_ARGUMENT", "Export options must be boolean")
        if format == "geo" and (inner_mold or outer_mold or revision != "R2018"):
            raise NXToolError("NX_INVALID_ARGUMENT", "DXF options are not supported for GEO")
        revisions = SM.ExportFlatPatternBuilder.DxfRevisionType
        if revision not in {
            "R12",
            "R13",
            "R14",
            "R2000",
            "R2004",
            "R2005",
            "R2007",
            "R20102012",
            "R20132016",
            "R2018",
        }:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported native DXF revision")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(".nx-export-" + uuid.uuid4().hex + destination.suffix)
        try:
            builder = self._sm_manager().CreateExportFlatPatternBuilder()
            try:
                builder.FlatPattern.Value = feature
                builder.OutputFile = str(temporary)
                builder.Type = getattr(
                    SM.ExportFlatPatternBuilder.FileType, "Dxf" if format == "dxf" else "TrumpfGeo"
                )
                builder.ExportLocation = SM.ExportFlatPatternBuilder.ExportLocationOptions.Native
                if format == "dxf":
                    builder.DxfRevision = getattr(revisions, revision)
                builder.DeviationalTolerance = tolerance
                for name, value in options.items():
                    setattr(builder, name, value)
                builder.Commit()
            finally:
                builder.Destroy()
            if not temporary.is_file() or not temporary.stat().st_size:
                raise NXToolError(
                    "NX_EXPORT_FAILED", "NX did not produce a nonempty flat-pattern file"
                )
            data = temporary.read_bytes()
            if (
                format == "dxf"
                and b"SECTION" not in data
                and not data.startswith(b"AutoCAD Binary DXF")
            ):
                raise NXToolError("NX_EXPORT_FAILED", "Native output is not recognized as DXF")
            with destination.open("xb") as output:
                try:
                    output.write(data)
                    output.flush()
                except BaseException:
                    output.close()
                    destination.unlink(missing_ok=True)
                    raise
            return {
                "path": str(destination),
                "format": format,
                "revision": revision if format == "dxf" else None,
                "options": options,
                "tolerance": tolerance,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "units": self._units(),
                "coordinate_frame": "native_flat_pattern",
                "flat_pattern": self._reference(
                    feature, "feature", self._work_part(), "FlatPattern"
                ),
            }
        finally:
            temporary.unlink(missing_ok=True)

    def _add_flat_pattern_view(self, drawing, flat_pattern, position=None):
        part = self._work_part()
        feature = self._sm_reference(flat_pattern, "feature")
        if feature.FeatureType != "FLAT_PATTERN":
            raise NXToolError("NX_OBJECT_TYPE_MISMATCH", "Select a native Flat Pattern feature")
        sheet = self._drawing_object(drawing, "drawing_sheet")
        point = [100.0, 100.0] if position is None else position
        if not isinstance(point, list) or len(point) != 2:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "position requires two sheet coordinates in sheet units"
            )
        point = [finite(v, "position") for v in point]
        definition = self._sm_manager().CreateFlatPatternBuilder(feature)
        try:
            model_view = part.ModelingViews.FindObject(definition.FlatPatternViewName)
        finally:
            definition.Destroy()
        sheet.Open()
        builder = part.DraftingViews.CreateBaseViewBuilder(None)
        try:
            builder.SelectModelView.SelectedView = model_view
            builder.Placement.Placement.SetValue(None, None, self._sheet_point3d(sheet, point))
            view = builder.Commit()
        finally:
            builder.Destroy()
        self._place_drawing_view(view, sheet, point)
        return {
            "view": self._reference(view, "drawing_view", part, "Flat pattern view"),
            "drawing": self._reference(sheet, "drawing_sheet", part, "Drawing sheet"),
            "flat_pattern": self._reference(feature, "feature", part, "FlatPattern"),
            "model_view_name": model_view.Name,
            **self._drawing_coordinates(sheet, point),
            "units": self._sheet_units(sheet),
        }

    def _sheet_metal_annotation(
        self, kind, body, position, faces=None, annotation=None, automatic=False
    ):
        import NXOpen.Annotations as A

        if kind not in {"body", "bend"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Annotation kind must be body or bend")
        target = self._sm_reference(body, "body")
        if not self._sm_manager().IsSheetmetalBody(target):
            raise NXToolError("NX_NOT_SHEET_METAL", "Select an actual native sheet-metal body")
        point = self._sm_validate({"position": {"kind": "point3d"}}, {"position": position})[
            "position"
        ]
        if kind == "body" and faces is not None:
            raise NXToolError("NX_INVALID_ARGUMENT", "Body annotation does not take bend faces")
        selected = []
        if kind == "bend":
            selected = self._sm_validate({"faces": {"kind": "select_faces"}}, {"faces": faces})[
                "faces"
            ]
            for face in selected:
                if face.GetBody() != target:
                    raise NXToolError(
                        "NX_OBJECT_OWNER_MISMATCH", "Bend faces must belong to the selected body"
                    )
                self._sm_manager().GetBendParameters(face)
        existing = self._sm_reference(annotation, "annotation") if annotation else None
        units = self._units()
        if kind == "body":
            measured = {"thickness": float(self._sm_manager().GetBodyThickness(target))}
            lines = [
                "Sheet metal (managed measurement)"
                if automatic
                else "Sheet metal (measured snapshot)",
                f"Thickness: {measured['thickness']:.3f} {units}",
            ]
        else:
            measured = {"bends": []}
            lines = [
                "Bend data (managed measurement)" if automatic else "Bend data (measured snapshot)"
            ]
            for index, face in enumerate(selected, 1):
                data = self._sm_manager().GetBendParameters(face)
                values = {
                    "inner_radius": float(data.InnerRadius),
                    "angle_degrees": float(data.BendAngle),
                    "neutral_factor": float(data.NeutralFactor),
                }
                measured["bends"].append(values)
                lines.append(
                    f"Bend {index}: R {values['inner_radius']:.3f} {units}; "
                    f"angle {values['angle_degrees']:.3f} deg; K {values['neutral_factor']:.3f}"
                )
        builder = self._sm_manager().CreateSheetMetalPmiBuilder(existing)
        try:
            builder.PMIType = getattr(A.SheetMetalPMIBuilder.Types, kind.capitalize())
            builder.SelectedBody.Value = target
            builder.SelectedFace.Clear()
            if selected:
                builder.SelectedFace.Add(selected)
            builder.AssociatedObjects.Nxobjects.Clear()
            builder.AssociatedObjects.Nxobjects.Add(selected or [target])
            builder.Text.TextBlock.SetText(lines)
            builder.Origin.SetInferRelativeToGeometry(True)
            builder.Origin.Origin.SetValue(None, None, self.nxopen.Point3d(*point))
            if not builder.Validate():
                raise NXToolError(
                    "NX_ANNOTATION_INVALID", "Native sheet-metal annotation validation failed"
                )
            result = builder.Commit()
            values = list(builder.GetCommittedObjects())
            if not values and result is not None:
                values = [result]
            if not values:
                raise NXToolError(
                    "NX_VERIFICATION_FAILED", "NX returned no sheet-metal annotations"
                )
            self._update_model()
            if automatic:
                for value in values:
                    self._remember_managed_annotation(value, kind, target, selected, measured)
            elif existing:
                for value in values:
                    value.SetAttribute("NX_MCP_MEASURED_PMI_V1", "disabled")
            refs = [
                self._reference(value, "annotation", self._work_part(), "Sheet-metal PMI")
                for value in values
            ]
            return {
                "annotations": refs,
                "annotation_count": len(refs),
                "created": [] if existing else refs,
                "modified": refs if existing else [],
                "text": [
                    list(value.GetText()) if hasattr(value, "GetText") else None for value in values
                ],
                "body": self._reference(target, "body", self._work_part(), "Body"),
                "kind": kind,
                "measured_parameters": measured,
                "text_semantics": "Automatically refreshed after MCP model mutations; explicitly refresh after manual NX changes"
                if automatic
                else "Measured snapshot; call this tool with the annotation ID to refresh after model edits",
                "automatic": automatic,
                "requested_position": point,
                "units": self._units(),
                "coordinate_frame": "work_part",
            }
        finally:
            builder.Destroy()
