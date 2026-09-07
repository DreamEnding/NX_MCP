"""Geometric queries and guarded authoring on the serialized NX thread."""

from __future__ import annotations

import math
import re

from nx_mcp.runtime import NXToolError
from nx_mcp.visual_tools import enum_name, unit_normal


def page(items, offset=0, limit=50):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise NXToolError("NX_INVALID_ARGUMENT", "offset >= 0 and limit 1–200 are required")
    end = offset + limit
    return {
        "items": items[offset:end],
        "total": len(items),
        "offset": offset,
        "next_offset": end if end < len(items) else None,
    }


def finite(value, name, positive=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (positive and value <= 0)
    ):
        raise NXToolError(
            "NX_INVALID_ARGUMENT", name + " must be finite" + (" and positive" if positive else "")
        )
    return float(value)


class AuthoringMixin:
    def _require_api(self, obj, *names):
        missing = [n for n in names if not hasattr(obj, n)]
        if missing:
            raise NXToolError("NX_API_UNAVAILABLE", "Installed NX API lacks: " + ", ".join(missing))

    def _update_model(self):
        count = self.session.UpdateManager.DoUpdate(self._active_mark)
        if count:
            raise NXToolError("NX_UPDATE_FAILED", str(count) + " native update errors")

    def _error_list(self, errors):
        if errors is None:
            return
        try:
            if errors.Length:
                messages = [str(errors.GetErrorInfo(i)) for i in range(errors.Length)]
                raise NXToolError("NX_UPDATE_FAILED", "; ".join(messages))
        finally:
            errors.Dispose()

    def _expression(self, reference):
        if reference.startswith("obj_"):
            return self._resolve(reference, {"expression"})
        matches = [e for e in self._work_part().Expressions if e.Name == reference]
        if len(matches) != 1:
            raise NXToolError(
                "NX_NOT_FOUND", "Expression names are case-sensitive; use an expression ID"
            )
        return matches[0]

    def _expression_record(self, exp):
        part = self._work_part()
        return {
            "object": self._reference(exp, "expression", part, "Expression"),
            "name": exp.Name,
            "formula": exp.RightHandSide,
            "type": exp.Type,
            "value": (
                exp.GetValueUsingUnits(self.nxopen.Expression.UnitsOption.Expression)
                if exp.Type == "Number"
                else exp.IntegerValue
                if exp.Type == "Integer"
                else None
            ),
            "units": exp.Units.Abbreviation if exp.Units else "unitless",
            "editable": not (
                exp.IsNoEdit or exp.IsRightHandSideLockedFromEdit or exp.IsInterpartExpression
            ),
            "parents": [
                self._reference(e, "expression", part, "Expression")
                for e in exp.GetExpressionParents()
            ],
            "dependents": [
                self._reference(e, "expression", part, "Expression")
                for e in exp.GetReferencingExpressions()
            ],
            "dependency_scope": "stored immediate expression dependencies; conditional branches may be incomplete",
        }

    def _list_expressions(self, name_contains=None, offset=0, limit=50):
        values = sorted(self._work_part().Expressions, key=lambda e: e.Name)
        if name_contains is not None:
            values = [e for e in values if name_contains.casefold() in e.Name.casefold()]
        result = page(values, offset, limit)
        result["items"] = [self._expression_record(e) for e in result["items"]]
        return result

    def _set_expression(self, expression, formula, create=False, units="unitless"):
        part = self._work_part()
        if not isinstance(formula, str) or not formula.strip() or len(formula) > 4096:
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "formula must contain 1–4096 characters of NX expression syntax",
            )
        self._require_api(part.Expressions, "EditExpression", "CreateNumberExpression")
        if create:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", expression):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "New expression requires a simple identifier"
                )
            if any(e.Name == expression for e in part.Expressions):
                raise NXToolError("NX_NAME_EXISTS", "Expression already exists")
            unit_names = {
                "mm": "MilliMeter",
                "inch": "Inch",
                "deg": "Degrees",
                "rad": "Radian",
                "unitless": None,
            }
            if units not in unit_names:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "units must be mm, inch, deg, rad or unitless"
                )
            unit = part.UnitCollection.FindObject(unit_names[units]) if unit_names[units] else None
            exp = part.Expressions.CreateNumberExpression(expression + "=" + formula, unit)
        else:
            if units != "unitless":
                raise NXToolError(
                    "NX_INVALID_ARGUMENT",
                    "Existing expressions retain their units; do not supply units when editing",
                )
            exp = self._expression(expression)
            if not self._expression_record(exp)["editable"] or exp.Type != "Number":
                raise NXToolError(
                    "NX_EXPRESSION_READ_ONLY",
                    "Only editable, local Number expressions are supported",
                )
            part.Expressions.EditExpression(exp, formula)
        self._update_model()
        row = self._expression_record(exp)
        return {
            "expression": row,
            "modified": [row["object"]],
            "created": [row["object"]] if create else [],
        }

    def _bind_parameter(self, feature, parameter, expression):
        f = self._resolve(feature, {"feature"})
        exp = self._expression(expression)
        if exp.Type != "Number":
            raise NXToolError("NX_INVALID_ARGUMENT", "Binding requires a Number expression")
        builders = {
            "EXTRUDE": ("CreateExtrudeBuilder", {"start", "end"}),
            "PATTERN_FEATURE": ("CreatePatternFeatureBuilder", {"count", "spacing"}),
        }
        kind = f.FeatureType.upper().replace(" ", "_")
        if kind not in builders or parameter not in builders[kind][1]:
            raise NXToolError(
                "NX_UNSUPPORTED_EDIT", "Supported: EXTRUDE start/end; PATTERN_FEATURE count/spacing"
            )
        collection = self._work_part().Features
        self._require_api(collection, builders[kind][0])
        b = getattr(collection, builders[kind][0])(f)
        try:
            target = (
                (b.Limits.StartExtend.Value if parameter == "start" else b.Limits.EndExtend.Value)
                if kind == "EXTRUDE"
                else (
                    b.PatternService.RectangularDefinition.XSpacing.NCopies
                    if parameter == "count"
                    else b.PatternService.RectangularDefinition.XSpacing.PitchDistance
                )
            )
            self._work_part().Expressions.EditExpression(target, exp.Name)
            b.CommitFeature()
        finally:
            b.Destroy()
        self._update_model()
        return {
            "feature": self._get_feature_info(feature),
            "parameter": parameter,
            "expression": self._expression_record(exp),
            "modified": [self._reference(f, "feature", self._work_part(), "Feature")],
        }

    def _find_geometry(
        self,
        owner=None,
        kind="face",
        geometry_type="any",
        normal=None,
        radius=None,
        near=None,
        order="nearest",
        axis="Z",
        tolerance=0.001,
        offset=0,
        limit=50,
    ):
        import NXOpen.UF

        from nx_mcp.hardened import dot, vector

        if kind not in {"face", "edge"} or geometry_type not in {
            "any",
            "plane",
            "cylinder",
            "circle",
            "line",
        }:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported kind or geometry_type")
        if (kind == "face" and geometry_type in {"circle", "line"}) or (
            kind == "edge" and geometry_type in {"plane", "cylinder"}
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "geometry_type does not apply to kind")
        if normal is not None and (kind != "face" or geometry_type != "plane"):
            raise NXToolError("NX_INVALID_ARGUMENT", "Normal filtering requires planar faces")
        if radius is not None and geometry_type not in {"cylinder", "circle"}:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Radius filtering requires cylinders or circles"
            )
        if order not in {"nearest", "highest", "lowest"} or axis not in {"X", "Y", "Z"}:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "order must be nearest/highest/lowest; axis X/Y/Z"
            )
        tol = finite(tolerance, "tolerance", True)
        target = vector(near, "near") if near is not None else None
        if order == "nearest" and target is None:
            raise NXToolError("NX_INVALID_ARGUMENT", "nearest ordering requires a work-part point")
        direction = unit_normal(normal) if normal is not None else None
        wanted_radius = finite(radius, "radius", True) if radius is not None else None
        uf = NXOpen.UF.UFSession.GetUFSession()
        self._require_api(uf.Modeling, "AskFaceData", "AskMinimumDist3")
        values = self._geometry(owner)
        entities = {}
        for body in values:
            if not isinstance(body, self.nxopen.Body):
                raise NXToolError(
                    "NX_OBJECT_TYPE_MISMATCH",
                    "owner must resolve to bodies, features or components",
                )
            for obj in body.GetFaces() if kind == "face" else body.GetEdges():
                entities[int(obj.Tag)] = obj
        if len(entities) > 20000:
            raise NXToolError("NX_OBJECT_LIMIT", "Narrow owner to at most 20000 candidate entities")
        result = []
        for obj in entities.values():
            row = {
                "object": self._reference(obj, kind, self._work_part(), kind.title()),
                "geometry_type": "other",
            }
            box = list(uf.ModlGeneral.AskBoundingBox(obj.Tag))
            if kind == "face":
                code, point, direct, box, rad, _, sign = uf.Modeling.AskFaceData(obj.Tag)
                row.update(
                    geometry_type={22: "plane", 16: "cylinder"}.get(code, "other"), native_type=code
                )
                if code == 22:
                    row["normal"] = list(direct)
                if code == 16:
                    row.update(
                        radius=rad,
                        axis=list(direct),
                        axis_point=list(point),
                        surface_orientation=sign,
                        cylindrical_role="bore" if sign < 0 else "boss",
                    )
            else:
                type_name = enum_name(obj.SolidEdgeType, self.nxopen.Edge.EdgeType)
                row.update(
                    geometry_type={"Circular": "circle", "Linear": "line"}.get(type_name, "other"),
                    native_type=type_name,
                )
                if row["geometry_type"] == "circle":
                    arc = uf.Curve.AskArcData(obj.Tag)
                    row.update(radius=arc.Radius)
            if geometry_type != "any" and row["geometry_type"] != geometry_type:
                continue
            if direction is not None and dot(direction, row["normal"]) < 1 - tol:
                continue
            if wanted_radius is not None and abs(row["radius"] - wanted_radius) > tol:
                continue
            center = [(box[i] + box[i + 3]) / 2 for i in range(3)]
            row.update(bounds=list(box), bounds_type="conservative", bounds_center=center)
            if target is not None:
                row["distance_to_bounds_center"] = math.dist(target, center)
                distance, on_geometry, on_point, accuracy = uf.Modeling.AskMinimumDist3(
                    2, obj.Tag, 0, 0, [0.0, 0.0, 0.0], 1, list(target)
                )
                row.update(distance=distance, closest_point=list(on_geometry), accuracy=accuracy)
            row["rank_value"] = row["distance"] if order == "nearest" else center["XYZ".index(axis)]
            result.append(row)
        result.sort(key=lambda r: (r["rank_value"], r["object"]["id"]), reverse=order == "highest")
        return {
            **page(result, offset, limit),
            "coordinate_frame": "work_part",
            "units": self._units(),
            "ranking": "native BREP minimum distance"
            if order == "nearest"
            else "conservative bounds center along axis",
            "selector": {
                "version": 1,
                "owner_part": self._work_part().FullPath,
                "owner": self._geometry_owner_locator(owner) if owner else None,
                "query": {
                    "kind": kind,
                    "geometry_type": geometry_type,
                    "normal": normal,
                    "radius": radius,
                    "near": near,
                    "order": order,
                    "axis": axis,
                    "tolerance": tolerance,
                },
            },
            "normal_tolerance": "dot(requested,outward_normal) >= 1-tolerance",
        }

    def _highlight_objects(self, objects):
        self._visual_part()
        values = self._display_targets(objects)
        self._clear_highlights()
        try:
            for obj in values:
                obj.Highlight()
                self._highlighted_objects.append(obj)
        except Exception:
            self._clear_highlights()
            raise
        return {"highlighted": [self._display_ref(v) for v in values], "count": len(values)}

    def _consistency_fault(self, owner, code, tag):
        result = {"code": int(code), "native_tag": int(tag), "object": None}
        try:
            result["message"] = self.nxopen.NXException(int(code)).GetMessage()
        except Exception:
            result["message"] = "Native diagnostic message unavailable"
        try:
            obj = self.nxopen.TaggedObjectManager.GetTaggedObject(tag)
            kind = next(
                k
                for k, cls in [
                    ("face", self.nxopen.Face),
                    ("edge", self.nxopen.Edge),
                    ("body", self.nxopen.Body),
                ]
                if isinstance(obj, cls)
            )
            result["object"] = self._reference(obj, kind, owner, "Faulty entity")
        except Exception as error:
            result["reference_warning"] = str(error)
        return result

    def _model_health(self, scope="part", offset=0, limit=50):
        import NXOpen.UF

        if scope not in {"part", "assembly"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "scope must be part or assembly")
        part = self._work_part()
        uf = NXOpen.UF.UFSession.GetUFSession()
        issues = []
        feature_count = 0
        parts = {int(part.Tag): part}
        components = self._walk_components(part) if scope == "assembly" else []
        for c, path in components:
            if c.IsSuppressed:
                issues.append(
                    {"severity": "info", "kind": "suppressed_component", "occurrence_path": path}
                )
            elif c.Prototype is None or not hasattr(c.Prototype, "Features"):
                issues.append(
                    {"severity": "warning", "kind": "unloaded_component", "occurrence_path": path}
                )
            else:
                parts[int(c.Prototype.Tag)] = c.Prototype
        checked = 0
        for owner in parts.values():
            for f in owner.Features:
                feature_count += 1
                self._require_api(f, "GetFeatureErrorMessages", "GetFeatureWarningMessages")
                for severity, messages in [
                    ("error", f.GetFeatureErrorMessages()),
                    ("warning", f.GetFeatureWarningMessages()),
                ]:
                    for message in messages:
                        issues.append(
                            {
                                "severity": severity,
                                "kind": "feature_diagnostic",
                                "part": owner.FullPath,
                                "feature": f.JournalIdentifier,
                                "message": message,
                            }
                        )
                if f.Suppressed:
                    issues.append(
                        {
                            "severity": "info",
                            "kind": "suppressed_feature",
                            "part": owner.FullPath,
                            "feature": f.JournalIdentifier,
                        }
                    )
            for body in owner.Bodies:
                checked += 1
                self._require_api(uf.Modeling, "AskBodyConsistency")
                n, codes, tags = uf.Modeling.AskBodyConsistency(body.Tag)
                if n:
                    issues.append(
                        {
                            "severity": "error",
                            "kind": "body_consistency",
                            "part": owner.FullPath,
                            "body": body.JournalIdentifier,
                            "object": self._reference(body, "body", owner, "Faulty body"),
                            "faults": [
                                self._consistency_fault(owner, code, tag)
                                for code, tag in zip(codes, tags, strict=False)
                            ],
                            "repair_guidance": "Inspect the identified native entities. Self-intersection requires repairing or replacing the source face; nx_edit_faces heal is an explicit local edit, not a guaranteed body repair. Re-run nx_model_health after any repair and compare bounds/volume before acceptance.",
                            "fault_codes": list(codes),
                            "native_fault_tags": [int(t) for t in tags],
                        }
                    )
                if not body.IsSolidBody:
                    issues.append(
                        {
                            "severity": "info",
                            "kind": "sheet_body",
                            "part": owner.FullPath,
                            "body": body.JournalIdentifier,
                        }
                    )
        errors = sum(i["severity"] == "error" for i in issues)
        warnings = sum(i["severity"] == "warning" for i in issues)
        return {
            **page(issues, offset, limit),
            "healthy": errors == 0 and warnings == 0,
            "error_count": errors,
            "warning_count": warnings,
            "parts_checked": len(parts),
            "features_checked": feature_count,
            "bodies_checked": checked,
            "scope": scope,
            "checks": [
                "native feature diagnostics",
                "suppression",
                "loaded prototype availability",
                "UF body consistency",
            ],
            "limitations": [
                "Does not force a rebuild, prove design intent or check external files not loaded into NX.",
                "Shared prototypes checked once; suppressed components are reported, not loaded.",
            ],
        }

    def _rebuild_model(self):
        self._update_model()
        return {"health": self._model_health(), "modified": None, "update": "native DoUpdate"}

    def _edit_sketch(self, sketch_id, operations):
        from nx_mcp.hardened import add

        sketch = self._resolve(sketch_id, {"sketch"})
        active = self.session.ActiveSketch
        if active and active != sketch:
            raise NXToolError("NX_SKETCH_ACTIVE", "Finish the other active sketch first")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
            raise NXToolError("NX_INVALID_ARGUMENT", "operations requires 1–100 sketch edits")
        geometry = {int(c.Tag): c for c in sketch.GetAllGeometry()}
        constraints = {
            int(c.Tag): c
            for c in sketch.GetAllConstraintsOfType(
                self.nxopen.Sketch.ConstraintClass.Any, self.nxopen.Sketch.ConstraintType.NoCon
            )
        }
        schemas = {
            "line": {"action", "curve", "start", "end"},
            "arc": {"action", "curve", "center", "radius", "start_angle", "end_angle"},
            "delete": {"action", "object"},
            "add_line": {"action", "start", "end"},
            "constraint": {"action", "curve", "type"},
        }
        prepared = []
        frame = self._sketch_frame(sketch)

        def point2(value):
            if not isinstance(value, list) or len(value) != 2:
                raise NXToolError("NX_INVALID_ARGUMENT", "Sketch points require local [x,y]")
            x, y = (finite(v, "coordinate") for v in value)
            return self.nxopen.Point3d(
                *add(
                    frame["origin"],
                    [x * frame["x_axis"][i] + y * frame["y_axis"][i] for i in range(3)],
                )
            )

        for op in operations:
            if (
                not isinstance(op, dict)
                or op.get("action") not in schemas
                or set(op) != schemas[op["action"]]
            ):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Unsupported or extra sketch edit arguments"
                )
            action = op["action"]
            obj = (
                self._resolve(op.get("curve", op.get("object")), {"curve", "constraint"})
                if "curve" in op or "object" in op
                else None
            )
            if (
                obj
                and int(obj.Tag) not in geometry
                and not (action == "delete" and int(obj.Tag) in constraints)
            ):
                raise NXToolError(
                    "NX_OBJECT_OWNER_MISMATCH", "Object does not belong to this sketch"
                )
            data = dict(op)
            if action in {"line", "add_line"}:
                data["start"], data["end"] = point2(op["start"]), point2(op["end"])
                if op["start"] == op["end"]:
                    raise NXToolError("NX_INVALID_ARGUMENT", "Line endpoints must differ")
                if obj:
                    self._require_api(obj, "SetEndpoints")
            if action == "arc":
                self._require_api(obj, "SetParameters")
                data["center"] = point2(op["center"])
                data["radius"] = finite(op["radius"], "radius", True)
                start, end = (
                    finite(op["start_angle"], "start_angle"),
                    finite(op["end_angle"], "end_angle"),
                )
                if not 0 < end - start <= 360:
                    raise NXToolError(
                        "NX_INVALID_ARGUMENT", "Arc sweep must be >0 and <=360 degrees"
                    )
                data["start_angle"], data["end_angle"] = math.radians(start), math.radians(end)
            if action == "constraint" and op["type"] not in {"fixed", "horizontal", "vertical"}:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Constraint type must be fixed, horizontal or vertical"
                )
            prepared.append((obj, data))
        activated = not active
        try:
            if activated:
                sketch.Activate(self.nxopen.Sketch.ViewReorient.FalseValue)
            for obj, op in prepared:
                action = op["action"]
                if action == "line":
                    obj.SetEndpoints(op["start"], op["end"])
                elif action == "arc":
                    obj.SetParameters(
                        op["radius"], op["center"], op["start_angle"], op["end_angle"]
                    )
                elif action == "add_line":
                    curve = self._work_part().Curves.CreateLine(op["start"], op["end"])
                    sketch.AddGeometry(
                        curve, self.nxopen.Sketch.InferConstraintsOption.InferNoConstraints
                    )
                elif action == "delete":
                    self._error_list(sketch.DeleteObjects([obj]))
                else:
                    names = {
                        "fixed": "CreateFixedConstraint",
                        "horizontal": "CreateHorizontalConstraint",
                        "vertical": "CreateVerticalConstraint",
                    }
                    geom = self.nxopen.Sketch.ConstraintGeometry()
                    geom.Geometry = obj
                    getattr(sketch, names[op["type"]])(geom)
            if activated:
                sketch.Deactivate(
                    self.nxopen.Sketch.ViewReorient.FalseValue, self.nxopen.Sketch.UpdateLevel.Model
                )
            self._update_model()
            return {
                "sketch": self._sketch_info(sketch_id),
                "diagnostics": self._sketch_diagnostics(sketch_id),
                "edit_count": len(prepared),
                "modified": [self._reference(sketch, "sketch", self._work_part(), "Sketch")],
            }
        finally:
            if activated and self.session.ActiveSketch == sketch:
                sketch.Deactivate(
                    self.nxopen.Sketch.ViewReorient.FalseValue, self.nxopen.Sketch.UpdateLevel.Model
                )

    def _component_action(self, component, action, name=None, part_path=None):
        from nx_mcp.hardened import rows, xyz

        part = self._work_part()
        c = self._resolve(component, {"component"})
        if c.Parent != part.ComponentAssembly.RootComponent:
            raise NXToolError(
                "NX_UNSUPPORTED_SCOPE",
                "Activate the immediate owning assembly to edit this occurrence",
            )
        if action not in {"rename", "suppress", "unsuppress", "remove", "replace"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported component action")
        if (name is not None) != (action == "rename") or (part_path is not None) != (
            action == "replace"
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "rename requires only name; replace requires only part_path"
            )
        ref = self._reference(c, "component", part, "Component")
        position, matrix = c.GetPosition()
        if action == "rename":
            if not name.strip() or len(name) > 132:
                raise NXToolError("NX_INVALID_ARGUMENT", "name requires 1–132 characters")
            c.SetName(name)
        elif action in {"suppress", "unsuppress"}:
            fn = (
                part.ComponentAssembly.SuppressComponents
                if action == "suppress"
                else part.ComponentAssembly.UnsuppressComponents
            )
            self._error_list(fn([c]))
        elif action == "remove":
            self.session.UpdateManager.AddToDeleteList(c)
            self._update_model()
        else:
            path = self.workspace.ensure_inside(part_path)
            if path.suffix.lower() != ".prt" or not path.is_file():
                raise NXToolError(
                    "NX_FILE_NOT_FOUND", "Replacement requires an existing workspace .prt"
                )
            self._require_api(part.AssemblyManager, "CreateReplaceComponentBuilder")
            b = part.AssemblyManager.CreateReplaceComponentBuilder()
            try:
                b.ComponentsToReplace.Add(c)
                b.ReplacementPart = str(path)
                b.ReplaceAllOccurrences = False
                b.MaintainRelationships = True
                b.Commit()
                self._error_list(b.GetErrorList())
            finally:
                b.Destroy()
        self._update_model()
        if action != "remove":
            after_p, after_m = c.GetPosition()
            if math.dist(xyz(position), xyz(after_p)) > 1e-8 or any(
                abs(x - y) > 1e-8
                for a, b in zip(rows(matrix), rows(after_m), strict=True)
                for x, y in zip(a, b, strict=True)
            ):
                raise NXToolError(
                    "NX_PLACEMENT_CHANGED", "Component edit changed placement; rolling back"
                )
        return {
            "action": action,
            "object": self._reference(c, "component", part, "Component")
            if action != "remove"
            else None,
            "deleted": [ref] if action == "remove" else [],
            "modified": [ref] if action != "remove" else [],
            "placement_preserved": True,
            "suppression_scope": "all arrangements"
            if action in {"suppress", "unsuppress"}
            else None,
        }

    def _pattern_components(self, component, direction, spacing, count):
        from nx_mcp.hardened import rows, xyz

        c = self._resolve(component, {"component"})
        part = self._work_part()
        if c.Parent != part.ComponentAssembly.RootComponent or c.IsSuppressed:
            raise NXToolError(
                "NX_UNSUPPORTED_SCOPE", "Seed must be an unsuppressed immediate child"
            )
        if type(count) is not int or not 2 <= count <= 100:
            raise NXToolError("NX_INVALID_ARGUMENT", "count includes seed and must be 2–100")
        step = finite(spacing, "spacing", True)
        axis = unit_normal(direction)
        p, m = c.GetPosition()
        start = xyz(p)
        result = []
        for i in range(1, count):
            r = self._add_component(
                c.Prototype.FullPath,
                c.Name + "_" + str(i + 1),
                [start[j] + axis[j] * step * i for j in range(3)],
                rows(m),
            )
            result.append(r)
        return {
            "seed": self._reference(c, "component", part, "Component"),
            "instances": result,
            "total_instances": count,
            "spacing": step,
            "associative": False,
            "pattern_type": "ordinary positioned occurrences",
            "warnings": [
                "Instances are independent; changes to pitch require explicit repositioning."
            ],
        }
