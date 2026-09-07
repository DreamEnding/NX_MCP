"""Native drawing/annotation editing; NXOpen calls remain on the NX thread."""

from nx_mcp.authoring import finite, page
from nx_mcp.runtime import NXToolError


class DocumentationEditingMixin:
    def _list_drawings(self):
        part = self._work_part()
        current = part.DrawingSheets.CurrentDrawingSheet
        return {
            "sheets": [
                {
                    "object": self._reference(s, "drawing_sheet", part, "Sheet"),
                    "name": s.Name,
                    "active": s == current,
                    "width": s.Length,
                    "height": s.Height,
                    "units": self._sheet_units(s),
                    "scale": list(s.GetScale()),
                    "views": [
                        {"object": self._reference(v, "drawing_view", part, "View"), "name": v.Name}
                        for v in s.GetDraftingViews()
                    ],
                }
                for s in part.DrawingSheets
            ],
            "units": "per_sheet",
            "coordinate_frame": "drawing_sheet",
            "modeling_active": current is None,
        }

    def _activate_drawing(self, drawing=None):
        part = self._explosion_context()
        if drawing is None:
            part.Drafting.ExitDraftingApplication()
        else:
            self._drawing_object(drawing, "drawing_sheet").Open()
        return self._list_drawings()

    def _list_annotations(self, offset=0, limit=100):
        from nx_mcp.hardened import xyz

        part = self._work_part()
        values = [(x, "annotation") for x in self._documentation_annotations(part)] + [
            (x, "traceline") for x in part.Tracelines
        ]
        selected_page = page(values, offset, limit)
        items = []
        for value, kind in selected_page["items"]:
            item = {
                "object": self._reference(value, kind, part, "Annotation"),
                "native_type": type(value).__name__,
            }
            if hasattr(value, "HasUserAttribute") and value.HasUserAttribute(
                "NX_MCP_MEASURED_PMI_V1", self.nxopen.NXObject.AttributeType.String, -1
            ):
                item["managed_refresh"] = (
                    value.GetStringAttribute("NX_MCP_MEASURED_PMI_V1") != "disabled"
                )
            if hasattr(value, "IsRetained"):
                item["retained"] = bool(value.IsRetained)
            if hasattr(value, "AnnotationOrigin"):
                item["position"] = xyz(value.AnnotationOrigin)
            if hasattr(value, "GetText"):
                item["text"] = list(value.GetText())
            if hasattr(value, "HasUserAttribute") and value.HasUserAttribute(
                "NX_MCP_DRAWING_TABLE_V1", self.nxopen.NXObject.AttributeType.String, -1
            ):
                item["table_kind"] = value.GetStringAttribute("NX_MCP_DRAWING_TABLE_V1")
            if type(value).__name__ == "BendTable":
                item["rows"] = self._table_cells(value)
            if kind == "traceline":
                item.update(
                    start=xyz(value.StartPoint.Coordinates),
                    end=xyz(value.EndPoint.Coordinates),
                    start_offset=value.StartOffset,
                    end_offset=value.EndOffset,
                )
            items.append(item)
        return {
            "items": items,
            "total": len(values),
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(values) else None,
            "units": self._units(),
            "position_frame": "native annotation frame: sheet for drafting, work part for PMI; assembly for traces",
        }

    def _edit_annotation(self, annotation, position=None, name=None, delete=False):
        from nx_mcp.freeform import points3

        obj = self._resolve(annotation, {"annotation", "traceline"})
        if delete and (position is not None or name is not None):
            raise NXToolError("NX_INVALID_ARGUMENT", "Delete cannot be combined with edits")
        if not delete and position is None and name is None:
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply an edit")
        point = points3([position])[0] if position is not None else None
        if point is not None and not hasattr(obj, "AnnotationOrigin"):
            raise NXToolError(
                "NX_UNSUPPORTED_EDIT", "Use the dedicated trace editor for trace geometry"
            )
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise NXToolError("NX_INVALID_ARGUMENT", "Name must not be empty")
        if delete:
            self.session.UpdateManager.AddToDeleteList([obj])
            self._update_model()
            return {"deleted": [annotation]}
        if point is not None:
            obj.AnnotationOrigin = self.nxopen.Point3d(*point)
        if name is not None:
            obj.SetName(name)
        self._update_model()
        return {
            "modified": [
                self._reference(
                    obj,
                    "traceline" if hasattr(obj, "AskExplosion") else "annotation",
                    self._work_part(),
                    "Annotation",
                )
            ],
            "position": point,
            "units": self._units(),
        }

    def _parts_list_column(
        self,
        parts_list,
        action,
        index=None,
        title=None,
        width=None,
        field=None,
        key=None,
        protected=None,
    ):
        import NXOpen.UF as U

        obj = self._parts_list_object(parts_list)
        uf = U.UFSession.GetUFSession()
        tab = uf.Tabnot
        count = tab.AskNmColumns(obj.Tag)
        if action not in {"edit", "append", "remove"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unknown column action")
        if action == "append":
            if index is not None:
                raise NXToolError("NX_INVALID_ARGUMENT", "Append does not take an index")
            if width is None or title is None or field is None:
                raise NXToolError("NX_INVALID_ARGUMENT", "Append requires title, width and field")
        elif type(index) is not int or not 0 <= index < count:
            raise NXToolError("NX_INVALID_ARGUMENT", "Column index is out of range")
        if action == "remove" and (
            count <= 1 or any(x is not None for x in [title, width, field, key, protected])
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Remove only takes index; retain at least one column"
            )
        if action == "edit" and all(x is None for x in [title, width, field, key, protected]):
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply a column edit")
        if width is not None:
            width = finite(width, "width", True)
        for value in [title, field]:
            if value is not None and (not isinstance(value, str) or len(value) > 1024):
                raise NXToolError(
                    "NX_INVALID_ARGUMENT", "Column strings must be at most 1024 characters"
                )
        if action == "append":
            prefs = uf.Plist.AskDefaultColPrefs()
            prefs.DefaultString = field
            prefs.IsKeyField = bool(key)
            prefs.IsProtected = bool(protected)
            col = uf.Plist.CreateColumn(width, prefs, U.Plist.ColumnType.COLUMN_TYPE_GENERAL)
            tab.AddColumn(obj.Tag, col, count)
        else:
            col = tab.AskNthColumn(obj.Tag, index)
        if action == "remove":
            tab.RemoveColumn(col)
            uf.Obj.DeleteObject(col)
        else:
            prefs = uf.Plist.AskColPrefs(col)
            for attr, value in [
                ("DefaultString", field),
                ("IsKeyField", key),
                ("IsProtected", protected),
            ]:
                if value is not None:
                    setattr(prefs, attr, value)
            uf.Plist.SetColPrefs(col, prefs)
            if width is not None:
                tab.SetColumnWidth(col, width)
            if title is not None:
                if not tab.AskNmHeaderRows(obj.Tag):
                    raise NXToolError("NX_NO_TABLE_HEADER", "Parts list has no header row")
                tab.SetCellText(tab.AskCellAtRowCol(tab.AskNthHeaderRow(obj.Tag, 0), col), title)
        uf.Plist.Update(obj.Tag)
        return self._parts_list_info(parts_list)

    def _edit_explosion_trace(
        self, traceline, start_percent=None, end_percent=None, start_offset=None, end_offset=None
    ):
        import json

        from nx_mcp.hardened import xyz

        line = self._resolve(traceline, {"traceline"})
        ex = line.AskExplosion()
        if all(x is None for x in [start_percent, end_percent, start_offset, end_offset]):
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply an edit")
        if not line.HasUserAttribute(
            "NX_MCP_TRACE_V1", self.nxopen.NXObject.AttributeType.String, -1
        ):
            raise NXToolError("NX_UNSUPPORTED_EDIT", "Select a managed MCP trace")
        records = json.loads(line.GetStringAttribute("NX_MCP_TRACE_V1"))
        for record, value in zip(records, [start_percent, end_percent], strict=True):
            if value is not None:
                value = finite(value, "percent")
                if not 0 <= value <= 100:
                    raise NXToolError("NX_INVALID_ARGUMENT", "Percentage must be 0..100")
                record["percent"] = value
        offsets = [
            finite(x, "offset") if x is not None else None for x in [start_offset, end_offset]
        ]
        line.SetAttribute("NX_MCP_TRACE_V1", json.dumps(records))
        for attr, value in zip(["StartOffset", "EndOffset"], offsets, strict=True):
            if value is not None:
                setattr(line, attr, value)
        self._refresh_explosion_traces(ex)
        self._regenerate_explosion_display()
        return {
            "traceline": self._reference(line, "traceline", self._work_part(), "Trace"),
            "start": xyz(line.StartPoint.Coordinates),
            "end": xyz(line.EndPoint.Coordinates),
            "start_offset": line.StartOffset,
            "end_offset": line.EndOffset,
            "units": self._units(),
        }
