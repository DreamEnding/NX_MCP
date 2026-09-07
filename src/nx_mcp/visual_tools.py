"""NX-native visualization and solver diagnostics. All calls run on the NX thread."""

from __future__ import annotations

import math
import uuid

from nx_mcp.runtime import NXToolError


def enum_name(value, enum):
    for name in dir(enum):
        if not name.startswith("_") and getattr(enum, name) == value:
            return name
    return "unknown_" + str(value)


def unit_normal(value, argument="normal"):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise NXToolError("NX_INVALID_ARGUMENT", f"{argument} requires three finite numbers")
    v = [float(x) for x in value]
    length = math.sqrt(sum(x * x for x in v))
    if not math.isfinite(length) or length < 1e-12:
        raise NXToolError("NX_INVALID_ARGUMENT", f"{argument} must be finite and nonzero")
    return [x / length for x in v]


class VisualToolsMixin:
    def _visual_part(self):
        part = self._work_part()
        if part != self.session.Parts.Display:
            raise NXToolError(
                "NX_DISPLAY_PART_MISMATCH", "Activate the target as both work and display part"
            )
        return part

    def _display_targets(self, objects, expand=False):
        if not isinstance(objects, list) or not 1 <= len(objects) <= 1000:
            raise NXToolError("NX_INVALID_ARGUMENT", "objects requires 1–1000 references")
        values = []
        for ref in objects:
            obj = self._resolve(
                ref, {"body", "face", "edge", "curve", "sketch", "component", "feature", "datum"}
            )
            if isinstance(obj, self.nxopen.Features.Feature) or (
                expand and hasattr(obj, "FindOccurrence")
            ):
                values.extend(self._geometry(ref))
            elif isinstance(obj, self.nxopen.Sketch):
                values.extend(obj.GetAllGeometry())
            else:
                values.append(obj)
        values = list({int(x.Tag): x for x in values}.values())
        if not values:
            raise NXToolError("NX_NO_TARGET_BODY", "Selection contains no displayable objects")
        return values

    def _display_ref(self, obj):
        kind = (
            "datum"
            if isinstance(
                obj,
                tuple(
                    getattr(self.nxopen, n)
                    for n in ("DatumPlane", "DatumAxis", "CoordinateSystem")
                    if hasattr(self.nxopen, n)
                ),
            )
            else "component"
            if hasattr(obj, "FindOccurrence")
            else "body"
            if isinstance(obj, self.nxopen.Body)
            else "face"
            if isinstance(obj, self.nxopen.Face)
            else "edge"
            if isinstance(obj, self.nxopen.Edge)
            else "curve"
        )
        return self._reference(obj, kind, self._work_part(), "Display object")

    def _display_record(self, obj, appearance=False):
        record = {"object": self._display_ref(obj), "blanked": bool(obj.IsBlanked)}
        if appearance:
            record["color_index"] = obj.Color
            if isinstance(obj, self.nxopen.Face):
                import NXOpen.UF

                record["transparency"] = NXOpen.UF.UFSession.GetUFSession().Obj.AskTranslucency(
                    obj.Tag
                )
        return record

    def _display_records(self, values, appearance=False):
        if appearance:
            values = list(
                {
                    int(x.Tag): x
                    for obj in values
                    for x in (
                        [obj] + list(obj.GetFaces()) if isinstance(obj, self.nxopen.Body) else [obj]
                    )
                }.values()
            )
        if len(values) > 10000:
            raise NXToolError("NX_OBJECT_LIMIT", "Display change exceeds 10000 objects/faces")
        return [self._display_record(x, appearance) for x in values]

    def _save_display_snapshot(self, records):
        if not hasattr(self, "_display_snapshots"):
            self._display_snapshots = {}
        token = "display_" + uuid.uuid4().hex
        self._display_snapshots[token] = {
            "part_id": self._part_id(self._work_part()),
            "records": records,
        }
        return token

    def _display_info(self, objects, count_only=False):
        self._visual_part()
        if count_only:
            values = self._display_targets(objects, expand=True)
            count = len(
                {
                    int(x.Tag)
                    for obj in values
                    for x in (
                        [obj] + list(obj.GetFaces()) if isinstance(obj, self.nxopen.Body) else [obj]
                    )
                }
            )
            return {
                "count": count,
                "object_limit": 10000,
                "within_limit": count <= 10000,
                "expansion": "unique bodies and faces",
                "objects": [],
            }
        records = self._display_records(self._display_targets(objects, expand=True), True)
        return {
            "objects": records,
            "count": len(records),
            "transparency_scale": "0 opaque, 100 transparent",
            "color_system": "NX part color-table index",
            "coordinate_frame": "display_part",
        }

    def _apply_appearance(self, values, color_index=None, transparency=None):
        modification = self.session.DisplayManager.NewDisplayModification()
        try:
            modification.ApplyToOwningParts = False
            modification.ApplyToAllFaces = True
            if color_index is not None:
                modification.NewColor = color_index
            if transparency is not None:
                modification.NewTranslucency = transparency
            modification.Apply(values)
        finally:
            modification.Dispose()

    def _set_display(self, objects, color_index=None, transparency=None, color=None):
        self._visual_part()
        if color is not None:
            import NXOpen.UF

            names = {
                n: n.upper() + "_NAME"
                for n in [
                    "red",
                    "green",
                    "blue",
                    "yellow",
                    "cyan",
                    "magenta",
                    "orange",
                    "white",
                    "black",
                ]
            }
            names["gray"] = "MEDIUM_GRAY_NAME"
            if color not in names or color_index is not None:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT",
                    "Specify a supported named color or color_index, not both",
                )
            uf = NXOpen.UF.UFSession.GetUFSession()
            color_index = uf.Disp.AskClosestColorInDisplayedPart(
                getattr(uf.Disp.ColorName, names[color])
            )
        if color_index is None and transparency is None:
            raise NXToolError("NX_INVALID_ARGUMENT", "Specify color_index or transparency")
        if color_index is not None and (
            type(color_index) is not int or not 1 <= color_index <= 216
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "color_index must be an integer from 1 to 216")
        if transparency is not None and (
            type(transparency) is not int or not 0 <= transparency <= 100
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "transparency must be an integer from 0 to 100"
            )
        values = self._display_targets(objects, expand=True)
        if transparency is not None and any(
            not isinstance(x, (self.nxopen.Body, self.nxopen.Face)) for x in values
        ):
            raise NXToolError("NX_OBJECT_TYPE_MISMATCH", "Transparency requires bodies or faces")
        before = self._display_records(values, True)
        self._apply_appearance(values, color_index, transparency)
        token = self._save_display_snapshot(before)
        return {
            "restore_id": token,
            "objects": self._display_records(values, True),
            "modified": [r["object"] for r in before],
            "prototype_parts_modified": False,
            "warnings": [
                "Appearance changes can be saved in the part; restore_id restores explicit attributes, not inherited override state."
            ],
        }

    def _set_visibility(self, objects, mode="show"):
        part = self._visual_part()
        if mode not in {"show", "hide", "isolate"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "mode must be show, hide or isolate")
        selected = self._display_targets(objects)
        if any(isinstance(x, (self.nxopen.Face, self.nxopen.Edge)) for x in selected):
            raise NXToolError(
                "NX_OBJECT_TYPE_MISMATCH",
                "Visibility targets bodies, components or curves; faces/edges are unsupported",
            )
        if mode == "isolate":
            if any(
                not isinstance(x, self.nxopen.Body) and not hasattr(x, "FindOccurrence")
                for x in selected
            ):
                raise NXToolError(
                    "NX_OBJECT_TYPE_MISMATCH", "Isolation targets bodies or components"
                )
            bodies = self._geometry(scope="assembly")
            components = [c for c, _ in self._walk_components(part)]
            values = list({int(x.Tag): x for x in bodies + components}.values())
            keep = set()
            for ref in objects:
                for obj in self._geometry(ref):
                    keep.add(int(obj.Tag))
                    component = obj.OwningComponent if obj.IsOccurrence else None
                    while component:
                        keep.add(int(component.Tag))
                        component = component.Parent
            before = self._display_records(values)
            for obj in values:
                (obj.Unblank if int(obj.Tag) in keep else obj.Blank)()
        else:
            values = selected
            before = self._display_records(values)
            for obj in values:
                (obj.Unblank if mode == "show" else obj.Blank)()
        token = self._save_display_snapshot(before)
        return {
            "restore_id": token,
            "mode": mode,
            "objects": self._display_records(values),
            "modified": [r["object"] for r in before],
            "warnings": [
                "Isolation controls loaded body/component geometry; datum and reference-curve visibility is unchanged."
            ]
            if mode == "isolate"
            else [],
        }

    def _validate_display_restore(self, restore_id):
        self._visual_part()
        snapshots = getattr(self, "_display_snapshots", {})
        if restore_id not in snapshots:
            raise NXToolError(
                "NX_DISPLAY_SNAPSHOT_STALE", "Unknown or already restored display snapshot"
            )
        snapshot = snapshots[restore_id]
        pid = self._part_id(self._work_part())
        if snapshot["part_id"] != pid:
            raise NXToolError("NX_DISPLAY_PART_MISMATCH", "Activate the snapshot owner part")
        latest = next(k for k in reversed(snapshots) if snapshots[k]["part_id"] == pid)
        if latest != restore_id:
            raise NXToolError("NX_RESTORE_ORDER", "Restore display changes in reverse order")
        resolved = [(self._resolve(r["object"]["id"]), r) for r in snapshot["records"]]
        return resolved

    def _restore_display(self, restore_id):
        resolved = self._validate_display_restore(restore_id)
        snapshots = self._display_snapshots
        for obj, record in resolved:
            if "color_index" in record:
                # Bodies precede their faces, preserving per-face colors afterward.
                self._apply_appearance([obj], record["color_index"], record.get("transparency"))
            (obj.Blank if record["blanked"] else obj.Unblank)()
        del snapshots[restore_id]
        return {
            "restored": restore_id,
            "count": len(resolved),
            "modified": [r["object"] for _, r in resolved],
        }

    def _clear_highlights(self):
        cleared = 0
        for obj in getattr(self, "_highlighted_objects", []):
            try:
                obj.Unhighlight()
                cleared += 1
            except Exception:
                pass  # NX may have deleted an entity since the highlight.
        self._highlighted_objects = []
        return {"cleared_count": cleared, "scope": "MCP-owned highlights only"}

    def _highlight_collisions(self, obj1, obj2, include_contact=False):
        self._visual_part()
        if self.session.IsBatch:
            raise NXToolError("NX_VIEWPORT_UNAVAILABLE", "Highlighting requires interactive NX")
        result = self._check_interference(obj1, obj2)
        pairs = [
            p
            for p in result["pairs"]
            if p["classification"] == "penetration"
            or (include_contact and p["classification"] == "contact")
        ]
        refs = {r["id"]: r for p in pairs for r in p["objects"]}
        values = [self._resolve(r) for r in refs]
        self._clear_highlights()
        try:
            for obj in values:
                obj.Highlight()
                self._highlighted_objects.append(obj)
        except Exception:
            self._clear_highlights()
            raise
        return {
            **result,
            "highlighted": list(refs.values()),
            "highlighted_count": len(values),
            "highlight_style": "native NX selection highlight; no persistent color changes",
        }

    def _list_sections(self):
        from nx_mcp.hardened import xyz

        part = self._visual_part()
        view = part.ModelingViews.WorkView
        active = view.ActiveDynamicSection
        enabled = bool(view.DisplaySectioningToggle)
        sections = []
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "NX MCP inspect sections"
        )
        try:
            for section in part.DynamicSections:
                builder = part.DynamicSections.CreateSectionBuilder(section, view)
                try:
                    sections.append(
                        {
                            "object": self._reference(section, "section", part, "Section"),
                            "origin": xyz(builder.GetOrigin()),
                            "normal": xyz(builder.GetNormal()),
                            "active": active == section,
                            "clip_enabled": bool(builder.ShowClip),
                            "cap": bool(builder.ShowCap),
                        }
                    )
                finally:
                    builder.Destroy()
        finally:
            self.session.UndoToMark(mark, None)
            self.session.DeleteUndoMark(mark, None)
        return {
            "sections": sections,
            "count": len(sections),
            "view_sectioning_enabled": enabled,
            "coordinate_frame": "display_part",
        }

    def _section_view(self, origin, normal, section=None, name="MCP section", cap=True):
        import NXOpen.Display

        from nx_mcp.hardened import vector, xyz

        part = self._visual_part()
        view = part.ModelingViews.WorkView
        if self.session.IsBatch:
            raise NXToolError("NX_VIEWPORT_UNAVAILABLE", "Section views require interactive NX")
        point = vector(origin, "origin")
        direction = unit_normal(normal)
        if not isinstance(name, str) or not name.strip():
            raise NXToolError("NX_INVALID_ARGUMENT", "name cannot be empty")
        target = self._resolve(section, {"section"}) if section else None
        if target is None and view.ActiveDynamicSection:
            raise NXToolError(
                "NX_SECTION_EXISTS",
                "Specify the existing section ID to edit it, or delete it first",
            )
        builder = (
            part.DynamicSections.CreateSectionBuilder(target, view)
            if target
            else part.DynamicSections.CreateSectionBuilder(view)
        )
        try:
            builder.Type = NXOpen.Display.DynamicSectionTypes.Type.OnePlane
            builder.CsysType = NXOpen.Display.DynamicSectionTypes.CoordinateSystem.Absolute
            builder.SetName(name)
            builder.SetNormal(self.nxopen.Vector3d(*direction))
            builder.SetOrigin(self.nxopen.Point3d(*point))
            builder.ClipType = NXOpen.Display.DynamicSectionTypes.Clip.Section
            builder.ShowClip = True
            builder.ShowCap = cap
            builder.ShowViewer = False
            builder.ShowGrid = False
            result = builder.Commit()
            view.ActiveDynamicSection = result
            view.DisplaySectioningToggle = True
            return {
                "object": self._reference(result, "section", part, "Section"),
                "origin": xyz(builder.GetOrigin()),
                "normal": xyz(builder.GetNormal()),
                "cap": cap,
                "coordinate_frame": "display_part",
                "retained_side": "negative_normal",
                "retained_half_space": "dot(point - origin, normal) <= 0",
                "geometry_changed": False,
                "warnings": [
                    "Native view clipping; model solids and measurements remain unchanged."
                ],
            }
        finally:
            builder.Destroy()

    def _section_control(self, section, action):
        part = self._visual_part()
        view = part.ModelingViews.WorkView
        if action not in {"enable", "disable", "delete"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "action must be enable, disable or delete")
        target = self._resolve(section, {"section"})
        if action == "delete":
            part.DynamicSections.DeleteSections(False, [target])
        elif action == "enable":
            view.ActiveDynamicSection = target
            view.DisplaySectioningToggle = True
        elif view.ActiveDynamicSection == target:
            view.DisplaySectioningToggle = False
        return {"section_id": section, "action": action, "geometry_changed": False}

    def _sketch_diagnostics(self, sketch_id):
        sketch = self._resolve(sketch_id, {"sketch"})
        part = self._work_part()
        active = self.session.ActiveSketch
        if active and active != sketch:
            raise NXToolError(
                "NX_SKETCH_ACTIVE", "Finish the other active sketch before running diagnostics"
            )
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "NX MCP sketch diagnostics"
        )
        activated = False
        try:
            if not active:
                sketch.Activate(self.nxopen.Sketch.ViewReorient.FalseValue)
                activated = True
            region = part.Sketches.CreateWorkRegionBuilder()
            try:
                # Evaluate all geometry regardless of the current work-region UI
                # preference; the invisible undo mark restores this temporary change.
                region.Scope = self.nxopen.SketchWorkRegionBuilder.ScopeType.EntireSketch
                region.Commit()
            finally:
                region.Destroy()
            sketch.CalculateStatus()
            status, dof = sketch.GetStatus()
            status_name = enum_name(status, self.nxopen.Sketch.Status)
            constraints = list(
                sketch.GetAllConstraintsOfType(
                    self.nxopen.Sketch.ConstraintClass.Any, self.nxopen.Sketch.ConstraintType.NoCon
                )
            )
            constraint_records = []
            for constraint in constraints:
                ref = self._reference(constraint, "constraint", part, "Sketch constraint")
                row = {
                    "object": ref,
                    "type": enum_name(constraint.ConstraintType, self.nxopen.Sketch.ConstraintType),
                }
                if hasattr(constraint, "AssociatedExpression") and constraint.AssociatedExpression:
                    exp = constraint.AssociatedExpression
                    row["expression"] = self._expression_record(exp)
                constraint_records.append(row)
            geometry = []
            for curve in sketch.GetAllGeometry():
                attached = sketch.GetConstraintsForGeometry(
                    curve, self.nxopen.Sketch.ConstraintClass.Any
                )
                geometry.append(
                    {
                        "object": self._reference(curve, "curve", part, "Sketch geometry"),
                        "constraints": [
                            self._reference(c, "constraint", part, "Constraint")["id"]
                            for c in attached
                        ],
                    }
                )
            return {
                "sketch": self._reference(sketch, "sketch", part, "Sketch"),
                "solver_status": status_name,
                "remaining_degrees_of_freedom": dof
                if status_name in {"UnderConstrained", "WellConstrained"}
                else None,
                "native_dof_value": dof,
                "constraints": constraint_records,
                "constraint_count": len(constraints),
                "geometry": geometry,
                "geometry_count": len(geometry),
                "evaluation": "native CalculateStatus, entire sketch",
                "conflicting_constraints": None,
                "work_region_handling": "Temporarily evaluate entire sketch; restore native state with undo",
                "warnings": [
                    "Persistent constraints are enumerated; inferred solver relations are not individual persistent constraints.",
                    "NX overall status does not identify a minimal conflicting constraint set.",
                ],
            }
        finally:
            try:
                if activated and self.session.ActiveSketch == sketch:
                    sketch.Deactivate(
                        self.nxopen.Sketch.ViewReorient.FalseValue,
                        self.nxopen.Sketch.UpdateLevel.Model,
                    )
            finally:
                try:
                    self.session.UndoToMark(mark, None)
                    self.session.DeleteUndoMark(mark, None)
                except Exception as error:
                    raise NXToolError(
                        "NX_ROLLBACK_FAILED",
                        "Sketch diagnostic cleanup failed: " + str(error),
                        details={"mutation_outcome": "partial"},
                    ) from error
