"""Native named explosions and associative assembly drawing views on NX 2606."""

from __future__ import annotations

from contextlib import contextmanager

from nx_mcp.authoring import finite, page
from nx_mcp.engineering import EngineeringMixin
from nx_mcp.runtime import NXToolError


class ExplodedViewsMixin:
    @contextmanager
    def _drawing_save_context(self, part, force_display=False):
        """Display native sheets for CGM-preserving saves, then restore the view."""
        sheets = list(getattr(part, "DrawingSheets", []))
        if not sheets or (not part.SaveOptions.DrawingCgmData and not force_display):
            yield
            return
        work, display = self.session.Parts.Work, self.session.Parts.Display
        if self.session.ActiveSketch:
            raise NXToolError(
                "NX_SKETCH_ACTIVE", "Finish the sketch before saving drawing preview data"
            )
        original_sheet = part.DrawingSheets.CurrentDrawingSheet
        changed_part = work != part or display != part
        try:
            if changed_part:
                self._activate_part(self._reference(part, "part", part, "Part")["id"], True, True)
            (original_sheet or sheets[0]).Open()
            yield
        finally:
            try:
                if original_sheet is None:
                    part.Drafting.ExitDraftingApplication()
                elif part.DrawingSheets.CurrentDrawingSheet != original_sheet:
                    original_sheet.Open()
                if changed_part:
                    if display:
                        self._activate_part(
                            self._reference(display, "part", display, "Part")["id"], False, True
                        )
                    if work:
                        self._activate_part(
                            self._reference(work, "part", work, "Part")["id"], True, False
                        )
            except Exception as error:
                raise NXToolError(
                    "NX_SAVE_VIEW_RESTORE_FAILED",
                    "Save presentation could not be restored",
                    details={"mutation_outcome": "partial", "restore_error": str(error)},
                ) from error

    def _save_component_drawing_previews(self, part):
        seen = set()
        for component, _ in reversed(self._walk_components(part)):
            prototype = component.Prototype
            if int(prototype.Tag) in seen:
                continue
            seen.add(int(prototype.Tag))
            if prototype.IsModified and list(getattr(prototype, "DrawingSheets", [])):
                with self._drawing_save_context(prototype):
                    status = prototype.Save(
                        self.nxopen.BasePart.SaveComponents.FalseValue,
                        self.nxopen.BasePart.CloseAfterSave.FalseValue,
                    )
                    if status:
                        status.Dispose()

    @staticmethod
    def _modeling_views(part):
        views = getattr(part, "ModelingViews", [])
        return list(views) if hasattr(views, "__iter__") else []

    def _explosions(self, part):
        return list(getattr(part.ComponentAssembly, "Explosions", []))

    def _explosion(self, reference):
        part = self._work_part()
        explosion = self._resolve(reference, {"explosion"})
        if explosion.OwningPart != part:
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Explosion must belong to the work part")
        return explosion

    def _explosion_context(self):
        part = self._work_part()
        if self.session.Parts.Display != part:
            raise NXToolError("NX_PART_CONTEXT", "Activate the same work and display part")
        if self.session.ActiveSketch:
            raise NXToolError("NX_SKETCH_ACTIVE", "Finish the active sketch first")
        return part

    @staticmethod
    def _explosion_uf():
        import NXOpen.UF

        return NXOpen.UF.UFSession.GetUFSession().Assem

    @staticmethod
    def _exploded_tree(explosion):
        result = {}

        def walk(parent, path, suppressed=False):
            for child in parent.GetChildren():
                component = child.GetComponent()
                child_path = path + [component.Name]
                hidden = suppressed or bool(component.IsSuppressed)
                result[int(component.Tag)] = (child, component, child_path, hidden)
                walk(child, child_path, hidden)

        walk(explosion.RootComponent, [])
        return result

    def _explosion_views(self, explosion):
        uf = self._explosion_uf()
        part = self._work_part()
        result = []
        for kind, views in [
            ("modeling", self._modeling_views(part)),
            ("drawing", part.DraftingViews),
        ]:
            for view in views:
                if int(uf.AskViewExplosion(view.Tag) or 0) == int(explosion.Tag):
                    result.append(
                        {
                            "name": view.Name,
                            "kind": kind,
                            "object": self._reference(
                                view,
                                "drawing_view" if kind == "drawing" else "modeling_view",
                                part,
                                "View",
                            ),
                        }
                    )
        return result

    def _explosion_record(self, explosion):
        return {
            "object": self._reference(explosion, "explosion", self._work_part(), "Explosion"),
            "name": explosion.Name,
            "views": self._explosion_views(explosion),
        }

    def _exploded_record(self, entry):
        from nx_mcp.hardened import rows, xyz

        child, component, path, suppressed = entry
        position, rotation = child.GetPosition()
        assembled_position, assembled_rotation = component.GetPosition()
        return {
            "component": self._reference(component, "component", self._work_part(), "Component"),
            "occurrence_path": path,
            "translation": xyz(position),
            "rotation_matrix": rows(rotation),
            "assembled_translation": xyz(assembled_position),
            "assembled_rotation_matrix": rows(assembled_rotation),
            "suppressed": suppressed,
        }

    def _list_explosions(self):
        return {
            "explosions": [self._explosion_record(x) for x in self._explosions(self._work_part())],
            "coordinate_frame": "assembly",
            "units": self._units(),
        }

    def _explosion_info(self, explosion, offset=0, limit=50):
        ex = self._explosion(explosion)
        values = [self._exploded_record(e) for e in self._exploded_tree(ex).values()]
        return {
            **self._explosion_record(ex),
            **page(values, offset, limit),
            "coordinate_frame": "assembly",
            "units": self._units(),
            "matrix_convention": "row-major, p_assembly = R * p_component + translation",
            "geometry_measurements": "ordinary body, clearance and mass tools use assembled geometry",
        }

    def _create_explosion(self, name):
        part = self._explosion_context()
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name) > 132
            or any(ord(c) < 32 or c in "/\\" for c in name)
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Use a nonempty NX name, at most 132 characters"
            )
        collection = part.ComponentAssembly.Explosions
        self._require_api(collection, "Create")
        if any(x.Name.casefold() == name.casefold() for x in collection):
            raise NXToolError("NX_NAME_CONFLICT", "Explosion name already exists; use its typed ID")
        if not self._walk_components(part):
            raise NXToolError("NX_NO_COMPONENTS", "Create an explosion in an assembly part")
        ex = collection.Create(name)
        return {**self._explosion_record(ex), "component_count": len(self._exploded_tree(ex))}

    def _edit_explosion(self, explosion, placements=None, reset_components=None):
        from nx_mcp.hardened import matmul, matvec, rows, transpose, vector, xyz

        self._explosion_context()
        ex = self._explosion(explosion)
        uf = self._explosion_uf()
        self._require_api(uf, "UnexplodeComponent", "ExplodeComponent")
        placements = [] if placements is None else placements
        resets = [] if reset_components is None else reset_components
        if (
            not isinstance(placements, list)
            or not isinstance(resets, list)
            or not 1 <= len(placements) + len(resets) <= 1000
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Provide 1–1000 placements/resets")
        tree = self._exploded_tree(ex)
        selected = set()

        def resolve(reference):
            if not isinstance(reference, str):
                raise NXToolError("NX_INVALID_ARGUMENT", "Component references must be strings")
            component = self._resolve(reference, {"component"})
            tag = int(component.Tag)
            if tag not in tree or tree[tag][3]:
                raise NXToolError(
                    "NX_UNSUPPORTED_SCOPE", "Select an unsuppressed occurrence in this explosion"
                )
            if tag in selected:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Each component may appear only once per request"
                )
            selected.add(tag)
            return tag

        reset_tags = [resolve(r) for r in resets]
        pending = []
        for item in placements:
            if (
                not isinstance(item, dict)
                or set(item) - {"component", "translation", "rotation_matrix"}
                or not {"component", "translation"} <= set(item)
            ):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT",
                    "Placement requires component and translation; optional rotation_matrix only",
                )
            tag = resolve(item["component"])
            raw_position = item["translation"]
            raw_rotation = item.get("rotation_matrix")
            if (
                not isinstance(raw_position, list)
                or len(raw_position) != 3
                or any(type(v) not in (int, float) for v in raw_position)
                or (
                    "rotation_matrix" in item
                    and (
                        not isinstance(raw_rotation, list)
                        or len(raw_rotation) != 3
                        or any(
                            not isinstance(row, list)
                            or len(row) != 3
                            or any(type(v) not in (int, float) for v in row)
                            for row in raw_rotation
                        )
                    )
                )
            ):
                raise NXToolError("NX_INVALID_ARGUMENT", "Use numeric 3-vectors and 3x3 matrices")
            position = vector(raw_position, "translation")
            rotation = (
                self._validate_rotation(item["rotation_matrix"])
                if "rotation_matrix" in item
                else rows(tree[tag][0].GetPosition()[1])
            )
            pending.append((tag, position, rotation))
        before = {tag: self._exploded_record(entry) for tag, entry in tree.items()}
        for tag in sorted(reset_tags, key=lambda t: len(tree[t][2])):
            uf.UnexplodeComponent(ex.Tag, tree[tag][1].Tag)
        for tag, position, rotation in sorted(pending, key=lambda p: len(tree[p[0]][2])):
            component = tree[tag][1]
            # NX stores a component-local post-transform. Reset before measuring
            # the inherited parent pose so assigning an absolute pose is repeatable.
            uf.UnexplodeComponent(ex.Tag, component.Tag)
            child = self._exploded_tree(ex)[tag][0]
            base_position, base_rotation = child.GetPosition()
            inverse = transpose(rows(base_rotation))
            delta_rotation = matmul(inverse, rotation)
            delta_position = matvec(
                inverse, [position[i] - xyz(base_position)[i] for i in range(3)]
            )
            matrix = [delta_rotation[i] + [delta_position[i]] for i in range(3)] + [
                [0.0, 0.0, 0.0, 1.0]
            ]
            uf.ExplodeComponent(ex.Tag, component.Tag, matrix)
        after = {
            tag: self._exploded_record(entry) for tag, entry in self._exploded_tree(ex).items()
        }
        for tag, position, rotation in pending:
            actual = after[tag]
            if any(
                abs(a - b) > 1e-7 for a, b in zip(actual["translation"], position, strict=True)
            ) or any(
                abs(actual["rotation_matrix"][i][j] - rotation[i][j]) > 1e-7
                for i in range(3)
                for j in range(3)
            ):
                raise NXToolError(
                    "NX_EXPLOSION_POSITION_MISMATCH",
                    "Native exploded pose differs from the requested absolute pose",
                    details={"actual": actual},
                )
        if set(before) != set(after) or any(
            before[t][k] != after[t][k]
            for t in before
            for k in ("assembled_translation", "assembled_rotation_matrix")
        ):
            raise NXToolError(
                "NX_EXPLOSION_ASSEMBLY_CHANGED",
                "Explosion unexpectedly changed assembled placements",
            )
        self._refresh_explosion_traces(ex)
        self._regenerate_explosion_display()
        views = [
            v
            for v in self._work_part().DraftingViews
            if int(uf.AskViewExplosion(v.Tag) or 0) == int(ex.Tag)
        ]
        if views:
            self._work_part().DraftingViews.UpdateViews(views)
        affected = sum(
            before[t]["translation"] != after[t]["translation"]
            or before[t]["rotation_matrix"] != after[t]["rotation_matrix"]
            for t in before
        )
        return {
            **self._explosion_record(ex),
            "components": [after[t] for t in sorted(selected)],
            "affected_component_count": affected,
            "assembled_placements_unchanged": True,
            "updated_drawing_views": [v.Name for v in views],
            "coordinate_frame": "assembly",
            "units": self._units(),
            "modified": [self._reference(ex, "explosion", self._work_part(), "Explosion")],
        }

    def _regenerate_explosion_display(self):
        if not self.session.IsBatch:
            import NXOpen.UF as U

            display = U.UFSession.GetUFSession().Disp
            self._require_api(display, "RegenerateDisplay")
            display.RegenerateDisplay()

    def _show_explosion(self, explosion=None, drawing_view=None, model_view=None):
        if drawing_view and model_view:
            raise NXToolError("NX_INVALID_ARGUMENT", "Select drawing_view or model_view, not both")
        part = self._explosion_context()
        ex = self._explosion(explosion) if explosion is not None else None
        uf = self._explosion_uf()
        self._require_api(uf, "SetViewExplosion", "AskViewExplosion")
        if drawing_view:
            view = self._drawing_object(drawing_view, "drawing_view")
            if view.OwningPart != part:
                raise NXToolError(
                    "NX_OBJECT_OWNER_MISMATCH", "Drawing view must belong to work part"
                )
        elif model_view:
            view = self._resolve(model_view, {"modeling_view"})
            if view.OwningPart != part:
                raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Model view must belong to work part")
        else:
            if list(part.DrawingSheets):
                part.Drafting.ExitDraftingApplication()
            view = part.ModelingViews.WorkView
        if ex is not None:
            self._refresh_explosion_traces(ex)
        uf.SetViewExplosion(view.Tag, ex.Tag if ex else 0)
        self._regenerate_explosion_display()
        if drawing_view:
            part.DraftingViews.UpdateViews([view])
        elif model_view is None:
            view.Fit()
        if int(uf.AskViewExplosion(view.Tag) or 0) != (int(ex.Tag) if ex else 0):
            raise NXToolError(
                "NX_EXPLOSION_VIEW_MISMATCH", "Native view did not accept the explosion"
            )
        return {
            "explosion": self._reference(ex, "explosion", part, "Explosion") if ex else None,
            "view_name": view.Name,
            "view_kind": "drawing" if drawing_view else "modeling",
            "assembled_placements_unchanged": True,
        }

    def _delete_explosion(self, explosion):
        self._explosion_context()
        ex = self._explosion(explosion)
        views = self._explosion_views(ex)
        if views:
            raise NXToolError(
                "NX_EXPLOSION_IN_USE",
                "Detach this explosion from its views before deletion",
                details={"views": views},
            )
        ref = self._reference(ex, "explosion", self._work_part(), "Explosion")
        ex.Delete()
        return {"deleted": [ref]}

    def _add_base_view(
        self, drawing, body=None, view="isometric", position=None, scope="body", explosion=None
    ):
        if scope == "body":
            if body is None or explosion is not None:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Body scope requires body and forbids explosion"
                )
            return EngineeringMixin._add_base_view(self, drawing, body, view, position)
        if scope != "assembly" or body is not None:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Use scope=assembly without body for an assembly view"
            )
        part = self._explosion_context()
        ex = self._explosion(explosion) if explosion is not None else None
        if not self._walk_components(part):
            raise NXToolError("NX_NO_COMPONENTS", "Assembly view requires component geometry")
        names = {
            n: n.title() for n in ("top", "front", "back", "right", "left", "bottom", "isometric")
        }
        if view not in names:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported model view")
        point = [100.0, 100.0] if position is None else [finite(v, "position") for v in position]
        if len(point) != 2:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "position must contain two sheet coordinates in sheet units"
            )
        sheet = self._drawing_object(drawing, "drawing_sheet")
        if sheet.OwningPart != part:
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Drawing sheet must belong to work part")
        uf = self._explosion_uf()
        self._require_api(uf, "SetViewExplosion", "AskViewExplosion")
        sheet.Open()
        builder = part.DraftingViews.CreateBaseViewBuilder(None)
        try:
            self._configure_base_view(builder, sheet)
            builder.SelectModelView.SelectedView = part.ModelingViews.FindObject(names[view])
            builder.Placement.Placement.SetValue(None, None, self._sheet_point3d(sheet, point))
            result = builder.Commit()
        finally:
            builder.Destroy()
        self._drawing_construction_visibility(result, False)
        self._place_drawing_view(result, sheet, point)
        uf.SetViewExplosion(result.Tag, ex.Tag if ex else 0)
        part.DraftingViews.UpdateViews([result])
        if int(uf.AskViewExplosion(result.Tag) or 0) != (int(ex.Tag) if ex else 0):
            raise NXToolError(
                "NX_EXPLOSION_VIEW_MISMATCH", "Drawing does not reference requested explosion"
            )
        return {
            "object": self._reference(result, "drawing_view", part, "Assembly base view"),
            "drawing": self._reference(sheet, "drawing_sheet", part, "Drawing sheet"),
            "scope": "assembly",
            "orientation": view,
            **self._drawing_coordinates(sheet, point),
            "explosion": self._reference(ex, "explosion", part, "Explosion") if ex else None,
        }

    def _add_projection_view(self, base_view, direction, spacing=60.0):
        if not self._explosions(self._work_part()):
            return EngineeringMixin._add_projection_view(self, base_view, direction, spacing)
        parent = self._drawing_object(base_view, "drawing_view")
        uf = self._explosion_uf()
        tag = uf.AskViewExplosion(parent.Tag) or 0
        result = EngineeringMixin._add_projection_view(self, base_view, direction, spacing)
        view = self._resolve(result["object"]["id"], {"drawing_view"})
        uf.SetViewExplosion(view.Tag, tag)
        self._work_part().DraftingViews.UpdateViews([view])
        if int(uf.AskViewExplosion(view.Tag) or 0) != int(tag):
            raise NXToolError(
                "NX_EXPLOSION_VIEW_MISMATCH", "Projected view did not retain its parent's explosion"
            )
        result["exploded"] = bool(tag)
        return result
