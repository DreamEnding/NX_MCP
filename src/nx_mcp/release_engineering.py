"""Native drawing authoring and persistent imported-geometry references."""

import math
import uuid

from nx_mcp.authoring import finite
from nx_mcp.runtime import NXToolError


def sheet_point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise NXToolError("NX_INVALID_ARGUMENT", "Position requires two sheet coordinates")
    return [finite(x, "position") for x in value]


class ReleaseEngineeringMixin:
    def _sheet_units(self, sheet):
        import NXOpen.Drawings as D

        if sheet.Units == D.DrawingSheet.Unit.Millimeters:
            return "mm"
        if sheet.Units == D.DrawingSheet.Unit.Inches:
            return "in"
        raise NXToolError("NX_UNSUPPORTED_UNITS", "Unknown native drawing units")

    def _view_sheet(self, view):
        matches = [s for s in self._work_part().DrawingSheets if view in s.GetDraftingViews()]
        if len(matches) != 1:
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Select a view on one work-part sheet")
        return matches[0]

    def _drawing_view_info(self, view):
        import NXOpen.UF as U

        from nx_mcp.hardened import xyz

        obj = self._drawing_object(view, "drawing_view")
        sheet = self._view_sheet(obj)
        uf = U.UFSession.GetUFSession().Draw
        bounds = list(uf.AskViewBorders(obj.Tag))
        style = self._view_style(view)
        warnings = []
        if not style["hidden_lines"] or not style["self_hidden"]:
            warnings.append(
                "Hidden-line processing is disabled: occluded edges may be drawn as visible. "
                "To hide them use hidden_lines=true, self_hidden=true, hidden_font=0."
            )
        return {
            "object": self._reference(obj, "drawing_view", self._work_part(), "View"),
            "drawing": self._reference(sheet, "drawing_sheet", self._work_part(), "Sheet"),
            "native_type": type(obj).__name__,
            "style": style,
            "warnings": warnings,
            "position": xyz(obj.GetDrawingReferencePoint())[:2],
            "scale": uf.AskViewScale(obj.Tag)[1],
            "bounds": bounds,
            "inside_sheet": bounds[0] >= 0
            and bounds[1] >= 0
            and bounds[2] <= sheet.Length
            and bounds[3] <= sheet.Height,
            "units": self._sheet_units(sheet),
            "coordinate_frame": "drawing_sheet",
            "bounds_semantics": "native drafting view border, excluding separately placed annotations",
        }

    def _edit_drawing_view(self, view, position=None, scale=None, style=None):
        import NXOpen.UF as U

        style = {k: v for k, v in (style or {}).items() if v is not None}
        if position is None and scale is None and not style:
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply position, scale or style")
        point = sheet_point(position) if position is not None else None
        scale = finite(scale, "scale", True) if scale is not None else None
        obj = self._drawing_object(view, "drawing_view")
        self._view_sheet(obj).Open()
        uf = U.UFSession.GetUFSession().Draw
        self._require_api(uf, "SetViewScale", "MoveView")
        if scale is not None:
            uf.SetViewScale(obj.Tag, scale)
        if point is not None:
            uf.MoveView(obj.Tag, point)
        if style:
            self._view_style(view, {k: v for k, v in style.items() if v is not None})
        self._work_part().DraftingViews.UpdateViews([obj])
        self._refresh_detail_boundaries()
        result = self._drawing_view_info(view)
        if (scale is not None and not math.isclose(result["scale"], scale, abs_tol=1e-8)) or (
            point is not None and math.dist(result["position"], point) > 1e-6
        ):
            raise NXToolError(
                "NX_VERIFICATION_FAILED",
                "View alignment or scale prevented the requested placement; rolling back",
            )
        result["modified"] = [result["object"]]
        return result

    def _add_section_drawing_view(
        self,
        parent_view,
        cut_object,
        position,
        step_direction,
        arrow_direction,
        scale=1.0,
        cut_association="start",
    ):
        import NXOpen.UF as U

        from nx_mcp.hardened import dot
        from nx_mcp.visual_tools import unit_normal

        point = sheet_point(position)
        scale = finite(scale, "scale", True)
        step, arrow = (
            unit_normal(step_direction, "step_direction"),
            unit_normal(arrow_direction, "arrow_direction"),
        )
        if abs(step[2]) > 1e-8 or abs(arrow[2]) > 1e-8 or abs(dot(step, arrow)) > 1e-8:
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Step and arrow must be perpendicular vectors in the sheet XY plane",
            )
        parent = self._drawing_object(parent_view, "drawing_view")
        edge = self._engineering_owned(cut_object, "edge")
        sheet = self._view_sheet(parent)
        uf = U.UFSession.GetUFSession().Draw
        self._require_api(uf, "CreateSimpleSxview")
        sheet.Open()
        cut = U.Drf.Object()
        cut.ObjectTag = edge.Tag
        cut.ObjectViewTag = parent.Tag
        if cut_association not in {"start", "end", "arc_center"}:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "cut_association must be start, end or arc_center"
            )
        cut.ObjectAssocType = (
            U.Drf.AssocType.ARC_CENTER
            if cut_association == "arc_center"
            else U.Drf.AssocType.END_POINT
        )
        cut.ObjectAssocModifier = (
            0 if cut_association == "arc_center" else 1 if cut_association == "start" else 2
        )
        tag = uf.CreateSimpleSxview(sheet.Tag, scale, step, arrow, parent.Tag, cut, point)
        obj = self.nxopen.TaggedObjectManager.GetTaggedObject(tag)
        self._work_part().DraftingViews.UpdateViews([obj])
        ref = self._reference(obj, "drawing_view", self._work_part(), "Section view")
        return {
            **self._edit_drawing_view(ref["id"], scale=scale),
            "created": [ref],
            "parent": parent_view,
            "association": "native simple section anchored to edge",
        }

    def _add_detail_drawing_view(self, parent_view, center, radius, position, scale=2.0):
        import NXOpen.Drawings as D

        from nx_mcp.freeform import points3

        center = points3([center])[0]
        radius = finite(radius, "radius", True)
        point = sheet_point(position)
        scale = finite(scale, "scale", True)
        parent = self._drawing_object(parent_view, "drawing_view")
        sheet = self._view_sheet(parent)
        sheet.Open()
        part = self._work_part()
        self._require_api(part.DraftingViews, "CreateDetailViewBuilder")
        b = part.DraftingViews.CreateDetailViewBuilder(None)
        try:
            b.Parent.View.Value = parent
            b.Type = D.DetailViewBuilder.Types.Circular
            mapped = self._detail_boundary_points(parent, sheet, center, radius)
            b.BoundaryPoint1 = part.Points.CreatePoint(mapped[0])
            b.BoundaryPoint2 = part.Points.CreatePoint(mapped[1])
            b.BoundaryPoint1.Blank()
            b.BoundaryPoint2.Blank()
            b.Scale.ScaleType = D.ViewScaleBuilder.Type.Ratio
            b.Scale.Numerator = scale
            b.Scale.Denominator = 1.0
            b.Origin.Placement.SetValue(None, None, self._sheet_point3d(sheet, point))
            if not b.Validate():
                raise NXToolError("NX_DRAWING_INVALID", "Detail view did not validate")
            obj = b.Commit()
        finally:
            b.Destroy()
        import json

        import NXOpen.UF as U

        obj.SetAttribute(
            "NX_MCP_DETAIL_V1",
            json.dumps(
                {
                    "parent": U.UFSession.GetUFSession().Tag.AskHandleFromTag(parent.Tag),
                    "center": center,
                    "radius": radius,
                }
            ),
        )
        ref = self._reference(obj, "drawing_view", part, "Detail view")
        return {
            **self._edit_drawing_view(ref["id"], scale=scale),
            "created": [ref],
            "parent": parent_view,
            "boundary_frame": "work_part",
            "boundary_units": self._units(),
        }

    def _geometry_anchor(self, object):
        import NXOpen.UF as U

        obj = self._resolve(object, {"body", "face", "edge"})
        if obj.IsOccurrence or obj.OwningPart != self._work_part():
            raise NXToolError(
                "NX_OBJECT_OWNER_MISMATCH", "Anchor owned prototype geometry in its work part"
            )
        kind = (
            "body"
            if isinstance(obj, self.nxopen.Body)
            else "face"
            if isinstance(obj, self.nxopen.Face)
            else "edge"
        )
        part = self._work_part()
        if not part.FullPath:
            raise NXToolError("NX_UNSAVED_PART", "Save the part before creating persistent anchors")
        return {
            "anchor": {
                "version": 1,
                "owner_part": part.FullPath,
                "kind": kind,
                "handle": U.UFSession.GetUFSession().Tag.AskHandleFromTag(obj.Tag),
            },
            "semantics": "native persistent identity, survives save/reopen while entity survives; no geometric nearest-neighbor fallback",
        }

    def _resolve_geometry_anchor(self, anchor):
        import NXOpen.UF as U

        if (
            not isinstance(anchor, dict)
            or set(anchor) != {"version", "owner_part", "kind", "handle"}
            or anchor["version"] != 1
            or anchor["kind"] not in {"body", "face", "edge"}
            or not all(isinstance(anchor[k], str) and anchor[k] for k in ["owner_part", "handle"])
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Use an intact native geometry anchor")
        part = self._work_part()
        if anchor["owner_part"].casefold() != part.FullPath.casefold():
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Anchor belongs to another part")
        try:
            tag = int(U.UFSession.GetUFSession().Tag.AskTagOfHandle(anchor["handle"]))
            objects = (
                list(part.Bodies)
                if anchor["kind"] == "body"
                else [
                    x
                    for b in part.Bodies
                    for x in (b.GetFaces() if anchor["kind"] == "face" else b.GetEdges())
                ]
            )
            obj = next(o for o in objects if int(o.Tag) == tag)
        except Exception as error:
            raise NXToolError(
                "NX_STALE_REFERENCE",
                "Anchored entity no longer exists; explicitly select replacement geometry",
            ) from error
        return {
            "object": self._reference(obj, anchor["kind"], part, "Anchored geometry"),
            "anchor": anchor,
            "resolution": "native persistent handle and owner/type verification",
        }

    def _drawing_table(self, drawing, kind, rows, widths, position, table=None, row_height=7.0):
        import NXOpen.UF as U

        if (
            kind not in {"title_block", "revision"}
            or not isinstance(rows, list)
            or not 1 <= len(rows) <= 100
            or not isinstance(widths, list)
            or not 1 <= len(widths) <= 20
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Use title_block or revision with 1..100 rows and 1..20 columns",
            )
        if any(
            not isinstance(r, list)
            or len(r) != len(widths)
            or any(not isinstance(c, str) or len(c) > 1024 for c in r)
            for r in rows
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Rows must be rectangular strings, maximum 1024 characters per cell",
            )
        widths = [finite(w, "width", True) for w in widths]
        height = finite(row_height, "row_height", True)
        point = sheet_point(position)
        sheet = self._drawing_object(drawing, "drawing_sheet")
        part = self._work_part()
        section = self._engineering_owned(table, "annotation") if table else None
        attr = "NX_MCP_DRAWING_TABLE_V1"
        guid_attr = "NX_MCP_TABLE_SHEET_GUID_V2"
        string_type = self.nxopen.NXObject.AttributeType.String
        sheet_guid = (
            sheet.GetStringAttribute(guid_attr)
            if sheet.HasUserAttribute(guid_attr, string_type, -1)
            else None
        )
        if sheet_guid:
            matches = [
                s
                for s in part.DrawingSheets
                if s.HasUserAttribute(guid_attr, string_type, -1)
                and s.GetStringAttribute(guid_attr) == sheet_guid
            ]
            if len(matches) != 1:
                raise NXToolError(
                    "NX_OBJECT_OWNER_AMBIGUOUS",
                    "Drawing sheets share the same managed identity; resolve duplicated sheet metadata before editing tables",
                )
        if section is not None:
            kind_ok = (
                section.HasUserAttribute(attr, string_type, -1)
                and section.GetStringAttribute(attr) == kind
            )
            if section.HasUserAttribute(guid_attr, string_type, -1):
                owner_ok = (
                    sheet_guid is not None and section.GetStringAttribute(guid_attr) == sheet_guid
                )
            else:
                try:
                    owner_ok = (
                        U.UFSession.GetUFSession().Tag.AskTagOfHandle(
                            section.GetStringAttribute("NX_MCP_TABLE_SHEET_V1")
                        )
                        == sheet.Tag
                    )
                except Exception:
                    owner_ok = False
            if not kind_ok or not owner_ok:
                raise NXToolError(
                    "NX_OBJECT_OWNER_MISMATCH",
                    "Edit a managed table of the same kind on this sheet",
                )
        if sheet_guid is None:
            sheet_guid = uuid.uuid4().hex
            sheet.SetAttribute(guid_attr, sheet_guid)
        sheet.Open()
        if section is None:
            b = part.Annotations.TableSections.CreateTableSectionBuilder(None)
            try:
                b.NumberOfRows = len(rows)
                b.NumberOfColumns = len(widths)
                b.RowHeight = height
                b.ColumnWidth = widths[0]
                b.Origin.Origin.SetValue(None, None, self._sheet_point3d(sheet, point))
                section = b.Commit()
            finally:
                b.Destroy()
            section.SetAttribute(attr, kind)
            section.SetAttribute(
                "NX_MCP_TABLE_SHEET_V1", U.UFSession.GetUFSession().Tag.AskHandleFromTag(sheet.Tag)
            )
        section.SetAttribute(guid_attr, sheet_guid)
        tab = U.UFSession.GetUFSession().Tabnot
        tag = (
            U.UFSession.GetUFSession().Tag.AskTagOfHandle(
                section.GetStringAttribute("NX_MCP_NATIVE_TABLE_V1")
            )
            if type(section).__name__ == "TitleBlock"
            else tab.AskTabularNoteOfSection(section.Tag)
        )
        if tab.AskNmColumns(tag) != len(widths):
            raise NXToolError(
                "NX_UNSUPPORTED_EDIT", "Keep the existing column count when editing a table"
            )
        while tab.AskNmRows(tag) < len(rows):
            tab.AddRow(tag, tab.CreateRow(height), tab.AskNmRows(tag))
        while tab.AskNmRows(tag) > len(rows):
            old = tab.AskNthRow(tag, tab.AskNmRows(tag) - 1)
            tab.RemoveRow(old)
            U.UFSession.GetUFSession().Obj.DeleteObject(old)
        for i, values in enumerate(rows):
            row = tab.AskNthRow(tag, i)
            tab.SetRowHeight(row, height)
            for j, value in enumerate(values):
                col = tab.AskNthColumn(tag, j)
                tab.SetColumnWidth(col, widths[j])
                tab.SetCellText(tab.AskCellAtRowCol(row, col), value)
        section.AnnotationOrigin = self._sheet_point3d(sheet, point)
        title = None
        if kind == "title_block" and table is None:
            b = part.DraftingManager.TitleBlocks.CreateDefineTitleBlockBuilder(
                self.nxopen.Annotations.TitleBlock.Null
            )
            try:
                b.Components.Add(section)
                b.UpdateCells()
                title = b.Commit()
            finally:
                b.Destroy()
            section = title
            section.AnnotationOrigin = self._sheet_point3d(sheet, point)
            section.SetAttribute(attr, kind)
            section.SetAttribute(
                "NX_MCP_TABLE_SHEET_V1", U.UFSession.GetUFSession().Tag.AskHandleFromTag(sheet.Tag)
            )
            section.SetAttribute(
                "NX_MCP_NATIVE_TABLE_V1", U.UFSession.GetUFSession().Tag.AskHandleFromTag(tag)
            )
        section.SetAttribute(guid_attr, sheet_guid)
        self._update_model()
        ref = self._reference(section, "annotation", part, "Drawing table")
        actual = [
            [
                tab.AskEvaluatedCellText(
                    tab.AskCellAtRowCol(tab.AskNthRow(tag, i), tab.AskNthColumn(tag, j))
                )
                for j in range(len(widths))
            ]
            for i in range(len(rows))
        ]
        return {
            "table": ref,
            "kind": kind,
            "position": point,
            "position_anchor": "lower_right" if kind == "title_block" else "upper_left",
            "rows": actual,
            "row_count": len(actual),
            "column_count": len(widths),
            "native_title_block": type(section).__name__ if kind == "title_block" else None,
            "created": [ref] if table is None else [],
            "modified": [ref] if table else [],
            "units": self._sheet_units(sheet),
            "coordinate_frame": "drawing_sheet",
            "semantics": "Editable native table; revision entries are explicitly supplied, not inferred release approvals",
        }

    def _update_assembly_documentation(self):
        import NXOpen.Positioning as P
        import NXOpen.UF as U

        part = self._explosion_context()
        constraints = self._assembly_constraints(part)
        if constraints:
            positioner = part.ComponentAssembly.Positioner
            positioner.BeginAssemblyConstraints()
            try:
                network = positioner.EstablishNetwork()
                network.MoveObjectsState = True
                network.Solve()
                network.ApplyToModel()
                self._update_model()
                if any(
                    c.GetConstraintStatus() != P.Constraint.SolverStatus.Solved for c in constraints
                ):
                    raise NXToolError(
                        "NX_CONSTRAINT_UNSATISFIED",
                        "Assembly constraints did not solve; documentation refresh rolled back",
                    )
            finally:
                positioner.ClearNetwork()
                positioner.EndAssemblyConstraints()
        self._update_model()
        for ex in self._explosions(part):
            self._refresh_explosion_traces(ex)
        uf = U.UFSession.GetUFSession()
        boms = list(part.Annotations.PartsLists)
        for bom in boms:
            uf.Plist.Update(bom.Tag)
        annotation_result = self._refresh_annotations()
        views = [v for s in part.DrawingSheets for v in s.GetDraftingViews()]
        if views:
            with self._drawing_save_context(part, force_display=True):
                for sheet in part.DrawingSheets:
                    sheet.Open()
                    owned_views = list(sheet.GetDraftingViews())
                    if owned_views:
                        part.DraftingViews.UpdateViews(owned_views)
                        for view in owned_views:
                            view.UpdateAutomaticViewBound()
        retained_annotations = [
            self._reference(a, "annotation", part, "Retained annotation")
            for a in self._documentation_annotations(part)
            if getattr(a, "IsRetained", False)
        ]
        retained_dimensions = any(d.IsRetained for d in part.Dimensions)
        return {
            "constraints": [self._assembly_constraint_record(c) for c in constraints],
            "parts_lists": [
                self._parts_list_info(self._reference(b, "annotation", part, "BOM")["id"])
                for b in boms
            ],
            "views": [
                self._drawing_view_info(self._reference(v, "drawing_view", part, "View")["id"])
                for v in views
            ],
            "annotations": annotation_result,
            "dimensions": self._list_dimensions()["dimensions"],
            "retained_annotations": retained_annotations,
            "documentation_complete": not retained_dimensions and not retained_annotations,
            "warnings": [
                "Retained dimensions or annotations require explicit reassociation or replacement; use nx_add_dimension(dimension=...) for dimensions."
            ]
            if retained_dimensions or retained_annotations
            else [],
            "health": self._model_health(scope="assembly"),
            "semantics": "Explicit assembly update after prototype edits/replacement: solves constraints, refreshes anchored traces, BOMs and native drawing views. Missing managed anchors or unsolved mates roll back this refresh; previous prototype edits remain separate operations.",
        }

    def _drawing_coordinates(self, sheet, value, key="position"):
        units = self._sheet_units(sheet)
        return {
            key: value,
            key + "_mm": [v * (25.4 if units == "in" else 1) for v in value],
            "sheet_units": units,
            "coordinate_frame": "drawing_sheet",
        }

    def _sheet_point3d(self, sheet, values):
        """NXOpen placement points use part units even on a differently sized sheet."""
        factor = (25.4 if self._sheet_units(sheet) == "in" else 1.0) / (
            25.4 if self._units() == "inch" else 1.0
        )
        return self.nxopen.Point3d(*(float(v) * factor for v in values), 0.0)

    def _place_drawing_view(self, view, sheet, point, axes=(0, 1)):
        import NXOpen.UF as U

        U.UFSession.GetUFSession().Draw.MoveView(view.Tag, point)
        self._work_part().DraftingViews.UpdateViews([view])
        actual = view.GetDrawingReferencePoint()
        if any(abs([actual.X, actual.Y][i] - point[i]) > 1e-6 for i in axes):
            raise NXToolError(
                "NX_VERIFICATION_FAILED",
                f"Native view placement differs from requested sheet coordinates: actual {[actual.X, actual.Y]}, requested {point}",
            )

    def _detail_boundary_points(self, parent, sheet, center, radius):
        import NXOpen.UF as U

        matrix = parent.Matrix
        other = [
            c + radius * d for c, d in zip(center, [matrix.Xx, matrix.Xy, matrix.Xz], strict=True)
        ]
        mapper = U.UFSession.GetUFSession().View.MapModelToDrawing
        return [self._sheet_point3d(sheet, mapper(parent.Tag, p)) for p in [center, other]]

    def _refresh_detail_boundaries(self):
        import json

        import NXOpen.UF as U

        part = self._work_part()
        managed = [
            (sheet, view)
            for sheet in getattr(part, "DrawingSheets", [])
            for view in sheet.GetDraftingViews()
            if view.HasUserAttribute(
                "NX_MCP_DETAIL_V1", self.nxopen.NXObject.AttributeType.String, -1
            )
        ]
        if not managed:
            return
        with self._drawing_save_context(part, force_display=True):
            for sheet, view in managed:
                sheet.Open()
                record = json.loads(view.GetStringAttribute("NX_MCP_DETAIL_V1"))
                tag = int(U.UFSession.GetUFSession().Tag.AskTagOfHandle(record["parent"]))
                parent = next((v for v in sheet.GetDraftingViews() if int(v.Tag) == tag), None)
                if parent is None:
                    raise NXToolError(
                        "NX_STALE_ANNOTATION_SOURCE", "Detail-view parent no longer resolves"
                    )
                points = self._detail_boundary_points(
                    parent, sheet, record["center"], record["radius"]
                )
                builder = part.DraftingViews.CreateDetailViewBuilder(view)
                try:
                    builder.BoundaryPoint1.SetCoordinates(points[0])
                    builder.BoundaryPoint2.SetCoordinates(points[1])
                    builder.Commit()
                finally:
                    builder.Destroy()

    def _drawing_dimension_edge(self, reference):
        edge = self._resolve(reference, {"edge"})
        if edge.IsOccurrence:
            if edge.OwningComponent not in [c for c, _ in self._walk_components(self._work_part())]:
                raise NXToolError(
                    "NX_OBJECT_OWNER_MISMATCH", "Select geometry in the work assembly"
                )
        elif edge.OwningPart != self._work_part():
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Select owned work-part geometry")
        return edge

    def _list_dimensions(self):
        from nx_mcp.hardened import xyz

        part = self._work_part()
        return {
            "dimensions": [
                {
                    "object": self._reference(d, "dimension", part, "Dimension"),
                    "native_type": type(d).__name__,
                    "computed_value": d.ComputedSize,
                    "retained": bool(d.IsRetained),
                    "measurement_valid": not bool(d.IsRetained),
                    "origin": xyz(d.AnnotationOrigin),
                }
                for d in part.Dimensions
            ],
            "units": self._units(),
            "coordinate_frame": "native annotation frame",
        }
