"""Native bend tables and transactional refresh of measured sheet-metal PMI."""

import json

from nx_mcp.runtime import NXToolError


class AnnotationUpdatesMixin:
    def _bend_table(self, view, position, table=None, automatic=True, columns=None):
        import NXOpen.Annotations as A

        from nx_mcp.freeform import points3

        target = self._drawing_object(view, "drawing_view")
        point = points3([list(position) + [0] if len(position) == 2 else position])[0]
        allowed = {"BendID", "BendName", "BendAngle", "BendDirection", "BendRadius"}
        if columns is not None and (
            not columns or len(set(columns)) != len(columns) or not set(columns) <= allowed
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply unique native bend-table columns")
        existing = self._engineering_owned(table, "annotation") if table else None
        part = self._work_part()
        b = part.Annotations.BendTables.CreateBendTableBuilder(existing)
        try:
            b.FlatPatternView.Value = target
            b.Style.BendTable.AutomaticUpdate = automatic
            if columns is not None:
                b.Style.BendTable.SetColumnOrder(
                    [getattr(A.BendTableSettingsBuilder.ColumnType, x) for x in columns]
                )
            b.AnnotationOrigin.Origin.SetValue(None, None, self.nxopen.Point3d(*point))
            if not b.Validate():
                raise NXToolError(
                    "NX_ANNOTATION_INVALID", "Select an actual flat-pattern drafting view"
                )
            obj = b.Commit()
            if obj is None:
                raise NXToolError("NX_VERIFICATION_FAILED", "NX returned no bend table")
            self._update_model()
            ref = self._reference(obj, "annotation", part, "Bend table")
            return {
                "table": ref,
                "rows": self._table_cells(obj),
                "columns": [str(x).split(".")[-1] for x in b.Style.BendTable.GetColumnOrder()],
                "created": [] if existing else [ref],
                "modified": [ref] if existing else [],
                "automatic_update": b.Style.BendTable.AutomaticUpdate,
                "units": self._units(),
                "coordinate_frame": "drawing_sheet",
            }
        finally:
            b.Destroy()

    def _table_cells(self, obj):
        import NXOpen.UF as U

        tab = U.UFSession.GetUFSession().Tabnot
        columns = [tab.AskNthColumn(obj.Tag, i) for i in range(tab.AskNmColumns(obj.Tag))]
        return [
            [
                tab.AskEvaluatedCellText(tab.AskCellAtRowCol(tab.AskNthRow(obj.Tag, i), c))
                for c in columns
            ]
            for i in range(tab.AskNmRows(obj.Tag))
        ]

    def _remember_managed_annotation(self, annotation, kind, body, faces, measured):
        import NXOpen.UF as U

        tags = U.UFSession.GetUFSession().Tag
        record = {
            "kind": kind,
            "body": tags.AskHandleFromTag(body.Tag),
            "faces": [tags.AskHandleFromTag(f.Tag) for f in faces],
            "measured": measured,
        }
        annotation.SetAttribute("NX_MCP_MEASURED_PMI_V1", json.dumps(record, sort_keys=True))

    def _refresh_annotations(self):
        import NXOpen.UF as U

        from nx_mcp.hardened import xyz

        part = self._work_part()
        self._refresh_detail_boundaries()
        updated = []
        for obj in self._documentation_annotations(part):
            if not hasattr(obj, "HasUserAttribute") or not obj.HasUserAttribute(
                "NX_MCP_MEASURED_PMI_V1", self.nxopen.NXObject.AttributeType.String, -1
            ):
                continue
            tags = U.UFSession.GetUFSession().Tag
            raw = obj.GetStringAttribute("NX_MCP_MEASURED_PMI_V1")
            if raw == "disabled":
                continue
            record = json.loads(raw)
            try:
                bodytag = int(tags.AskTagOfHandle(record["body"]))
                facetags = [int(tags.AskTagOfHandle(x)) for x in record["faces"]]
                body = next(b for b in part.Bodies if int(b.Tag) == bodytag)
                faces = [next(f for f in body.GetFaces() if int(f.Tag) == tag) for tag in facetags]
            except Exception as error:
                raise NXToolError(
                    "NX_STALE_ANNOTATION_SOURCE",
                    "A managed annotation source was deleted or no longer resolves",
                ) from error
            manager = self._sm_manager()
            if record["kind"] == "body":
                measured = {"thickness": float(manager.GetBodyThickness(body))}
            else:
                values = [manager.GetBendParameters(f) for f in faces]
                measured = {
                    "bends": [
                        {
                            "inner_radius": float(v.InnerRadius),
                            "angle_degrees": float(v.BendAngle),
                            "neutral_factor": float(v.NeutralFactor),
                        }
                        for v in values
                    ]
                }
            if measured == record["measured"]:
                continue
            result = self._sheet_metal_annotation(
                record["kind"],
                self._reference(body, "body", part, "Body")["id"],
                xyz(obj.AnnotationOrigin),
                faces=[self._reference(f, "face", part, "Face")["id"] for f in faces]
                if faces
                else None,
                annotation=self._reference(obj, "annotation", part, "PMI")["id"],
                automatic=True,
            )
            updated.extend(result["modified"])
        tables = getattr(getattr(part, "Annotations", None), "BendTables", None)
        for table in list(tables or []):
            builder = tables.CreateBendTableBuilder(table)
            try:
                if not builder.Style.BendTable.AutomaticUpdate:
                    continue
                before = self._table_cells(table)
                if not builder.Validate():
                    raise NXToolError(
                        "NX_ANNOTATION_INVALID", "An automatic bend table no longer validates"
                    )
                obj = builder.Commit()
                if obj is None:
                    raise NXToolError("NX_VERIFICATION_FAILED", "NX returned no updated bend table")
                if self._table_cells(obj) != before:
                    updated.append(self._reference(obj, "annotation", part, "Bend table"))
            finally:
                builder.Destroy()
        return {
            "updated": updated,
            "updated_count": len(updated),
            "units": self._units(),
            "semantics": "Managed PMI refresh after MCP mutations; invoke explicitly after manual NX edits. Automatic bend tables are rebuilt through their native builder in the same transaction.",
        }
