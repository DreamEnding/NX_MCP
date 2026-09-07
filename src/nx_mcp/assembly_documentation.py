"""Native parts lists, associative callouts and explosion documentation."""

from __future__ import annotations

from nx_mcp.authoring import finite
from nx_mcp.runtime import NXToolError


class AssemblyDocumentationMixin:
    @staticmethod
    def _documentation_annotations(part):
        values = [*getattr(part, "Notes", []), *getattr(part, "Labels", [])]
        annotations = getattr(part, "Annotations", None)
        if annotations is not None:
            for name in [
                "Datums",
                "Fcfs",
                "IdSymbols",
                "PartsLists",
                "BendTables",
                "TableSections",
                "Tables",
            ]:
                values.extend(getattr(annotations, name, []))
        values.extend(getattr(getattr(part, "DraftingManager", None), "TitleBlocks", []))
        return list({int(obj.Tag): obj for obj in values}.values())

    def _parts_list_object(self, reference):
        obj = self._engineering_owned(reference, "annotation")
        if obj not in list(self._work_part().Annotations.PartsLists):
            raise NXToolError("NX_OBJECT_TYPE_MISMATCH", "Select a native parts list")
        return obj

    def _parts_list_info(self, parts_list):
        import NXOpen.UF as U

        obj = self._parts_list_object(parts_list)
        uf = U.UFSession.GetUFSession()
        columns = [
            uf.Tabnot.AskNthColumn(obj.Tag, i) for i in range(uf.Tabnot.AskNmColumns(obj.Tag))
        ]
        rows = []
        for i in range(uf.Tabnot.AskNmRows(obj.Tag)):
            row = uf.Tabnot.AskNthRow(obj.Tag, i)
            rows.append(
                [
                    uf.Tabnot.AskEvaluatedCellText(uf.Tabnot.AskCellAtRowCol(row, col))
                    for col in columns
                ]
            )
        column_info = []
        for col in columns:
            cp = uf.Plist.AskColPrefs(col)
            header = (
                uf.Tabnot.AskNthHeaderRow(obj.Tag, 0)
                if uf.Tabnot.AskNmHeaderRows(obj.Tag)
                else None
            )
            column_info.append(
                {
                    "width": uf.Tabnot.AskColumnWidth(col),
                    "field": cp.DefaultString,
                    "key": cp.IsKeyField,
                    "protected": cp.IsProtected,
                    "title": uf.Tabnot.AskCellText(uf.Tabnot.AskCellAtRowCol(header, col))
                    if header
                    else None,
                }
            )
        prefs = uf.Plist.AskPrefs(obj.Tag)
        return {
            "parts_list": self._reference(obj, "annotation", self._work_part(), "Parts list"),
            "rows": rows,
            "columns": column_info,
            "row_count": len(rows),
            "column_count": len(columns),
            "automatic_update": prefs.AutoUpdate,
            "units": self._units(),
            "coordinate_frame": "drawing_sheet",
            "columns_source": "native NX parts-list defaults",
        }

    def _create_parts_list(self, drawing, position, scope="leaves"):
        import NXOpen.UF as U

        if scope not in {"leaves", "top_level", "all"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported parts-list scope")
        if not isinstance(position, list) or len(position) != 2:
            raise NXToolError("NX_INVALID_ARGUMENT", "position must contain two sheet coordinates")
        point = [finite(v, "position") for v in position] + [0.0]
        sheet = self._drawing_object(drawing, "drawing_sheet")
        if sheet.OwningPart != self._work_part():
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Drawing must belong to work part")
        if not self._walk_components(self._work_part()):
            raise NXToolError("NX_NO_COMPONENTS", "A parts list requires an assembly")
        sheet.Open()
        uf = U.UFSession.GetUFSession()
        prefs = uf.Plist.AskDefaultPrefs()
        prefs.AutoUpdate = True
        prefs.CreateNewRowsAsLocked = False
        prefs.InitialCalloutField = "1"
        prefs.MainSymbolText = "$~C"
        prefs.SymbolType = U.Plist.SymbolType.SYMBOL_TYPE_ID_SYMBOL_CIRCLE
        tag = uf.Plist.Create(prefs, point)
        settings = uf.Plist.AskTraversalSettings(tag)
        settings.LeavesOnly = scope == "leaves"
        settings.TopLevelOnly = scope == "top_level"
        uf.Plist.SetTraversalSettings(tag, settings)
        uf.Plist.Update(tag)
        obj = next(o for o in self._work_part().Annotations.PartsLists if int(o.Tag) == int(tag))
        ref = self._reference(obj, "annotation", self._work_part(), "Parts list")
        result = self._parts_list_info(ref["id"])
        result.update({"created": [ref], "scope": scope})
        return result

    def _update_parts_list(self, parts_list):
        obj = self._parts_list_object(parts_list)
        obj.Update()
        return self._parts_list_info(parts_list)

    def _parts_list_balloons(self, parts_list, view):
        obj = self._parts_list_object(parts_list)
        target = self._drawing_object(view, "drawing_view")
        before = {int(a.Tag) for a in self._work_part().Annotations.IdSymbols}
        obj.ShowBalloonsInView(target)
        created = [
            self._reference(a, "annotation", self._work_part(), "Balloon")
            for a in self._work_part().Annotations.IdSymbols
            if int(a.Tag) not in before
        ]
        return {
            "parts_list": self._reference(obj, "annotation", self._work_part(), "Parts list"),
            "view": self._reference(target, "drawing_view", self._work_part(), "Drawing view"),
            "balloons": created,
            "created": created,
            "balloon_count": len(created),
            "association": "native parts-list callout groups",
            "units": self._units(),
            "coordinate_frame": "drawing_sheet",
        }

    def _explosion_trace(
        self,
        explosion,
        start_edge,
        end_edge,
        start_percent=50.0,
        end_percent=50.0,
        start_direction=None,
        end_direction=None,
    ):
        from nx_mcp.hardened import IDENTITY

        part = self._explosion_context()
        ex = self._explosion(explosion)
        tree = self._exploded_tree(ex)
        edges = [self._resolve(r, {"edge"}) for r in (start_edge, end_edge)]
        for edge in edges:
            component = edge.OwningComponent
            if (
                not edge.IsOccurrence
                or component is None
                or int(component.Tag) not in tree
                or tree[int(component.Tag)][3]
            ):
                raise NXToolError(
                    "NX_OBJECT_OWNER_MISMATCH",
                    "Select edges of unsuppressed component occurrences in this explosion",
                )
        values = [finite(v, "percent") for v in [start_percent, end_percent]]
        if any(not 0 <= v <= 100 for v in values):
            raise NXToolError("NX_INVALID_ARGUMENT", "Edge percentage must be 0..100")
        directions = [
            self._engineering_direction(v or [0, 0, 1]) for v in [start_direction, end_direction]
        ]
        import NXOpen.UF as U

        tags = U.UFSession.GetUFSession().Tag
        self._require_api(tags, "AskHandleFromTag")
        self._require_api(tags, "AskTagOfHandle")
        records = [
            {
                "edge": tags.AskHandleFromTag(edge.Prototype.Tag),
                "component": tags.AskHandleFromTag(edge.OwningComponent.Tag),
                "percent": percent,
            }
            for edge, percent in zip(edges, values, strict=True)
        ]
        positions = [self._trace_position(ex, record) for record in records]
        points = [part.Points.CreatePoint(self.nxopen.Point3d(*position)) for position in positions]
        for point in points:
            point.Blank()
        line = part.Tracelines.CreateAutomaticTraceline(
            ex,
            points[0],
            directions[0],
            points[1],
            directions[1],
            self._nx_matrix(IDENTITY),
            self.nxopen.AutomaticTraceline.ModeOption.Infer,
            0,
            0.0,
            0.0,
            [],
            [],
        )
        import json

        encoded = json.dumps(records)
        line.SetAttribute("NX_MCP_TRACE_V1", encoded)
        if line.GetStringAttribute("NX_MCP_TRACE_V1") != encoded:
            raise NXToolError("NX_VERIFICATION_FAILED", "Trace anchor metadata was not retained")
        self._update_model()
        ref = self._reference(line, "traceline", part, "Explosion trace")
        return {
            "traceline": ref,
            "created": [ref],
            "explosion": self._reference(ex, "explosion", part, "Explosion"),
            "endpoints": [self._reference(p, "point", part, "Trace endpoint") for p in points],
            "association": "Native persistent component/edge handles; exploded endpoints refreshed by MCP explosion edits, show, and animation. After manual NX edits, call nx_show_explosion to refresh.",
            "units": self._units(),
            "coordinate_frame": "assembly",
        }

    def _trace_position(self, explosion, record):
        from nx_mcp.hardened import matvec, rows, transpose, xyz

        part = self._work_part()
        import NXOpen.UF as U

        try:
            tags = U.UFSession.GetUFSession().Tag
            component_tag = int(tags.AskTagOfHandle(record["component"]))
            edge_tag = int(tags.AskTagOfHandle(record["edge"]))
        except Exception as error:
            raise NXToolError(
                "NX_STALE_TRACE_ANCHOR", "A persistent trace handle no longer resolves"
            ) from error
        matches = [
            entry
            for entry in self._exploded_tree(explosion).values()
            if int(entry[1].Tag) == component_tag
        ]
        if len(matches) != 1:
            raise NXToolError(
                "NX_STALE_TRACE_ANCHOR", "A managed trace component is missing or ambiguous"
            )
        child, component, _, suppressed = matches[0]
        if suppressed:
            raise NXToolError("NX_STALE_TRACE_ANCHOR", "A managed trace component is suppressed")
        edges = [
            edge
            for body in component.Prototype.Bodies
            for edge in body.GetEdges()
            if int(edge.Tag) == edge_tag
        ]
        if len(edges) != 1:
            raise NXToolError(
                "NX_STALE_TRACE_ANCHOR", "A managed trace edge is missing or ambiguous"
            )
        edge = component.FindOccurrence(edges[0])
        if edge is None:
            raise NXToolError("NX_STALE_TRACE_ANCHOR", "Trace edge occurrence is unavailable")
        percent = finite(record["percent"], "percent")
        if not 0 <= percent <= 100:
            raise NXToolError("NX_INVALID_ARGUMENT", "Stored trace percentage must be 0..100")
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "Evaluate trace anchor"
        )
        try:
            scalar = part.Scalars.CreateScalar(
                percent,
                self.nxopen.Scalar.DimensionalityType.NotSet,
                self.nxopen.SmartObject.UpdateOption.WithinModeling,
            )
            point = part.Points.CreatePoint(
                edge,
                scalar,
                self.nxopen.PointCollection.PointOnCurveLocationOption.PercentArcLength,
                self.nxopen.SmartObject.UpdateOption.WithinModeling,
            )
            if self.session.UpdateManager.DoUpdate(mark):
                raise NXToolError("NX_UPDATE_FAILED", "Trace anchor evaluation failed")
            coordinates = xyz(point.Coordinates)
        finally:
            self.session.UndoToMark(mark, None)
            self.session.DeleteUndoMark(mark, None)
        assembled_position, assembled_rotation = component.GetPosition()
        exploded_position, exploded_rotation = child.GetPosition()
        local = matvec(
            transpose(rows(assembled_rotation)),
            [a - b for a, b in zip(coordinates, xyz(assembled_position), strict=True)],
        )
        return [
            a + b
            for a, b in zip(
                matvec(rows(exploded_rotation), local), xyz(exploded_position), strict=True
            )
        ]

    def _refresh_explosion_traces(self, explosion):
        import json

        pending = []
        for line in getattr(self._work_part(), "Tracelines", []):
            if line.AskExplosion() != explosion or not line.HasUserAttribute(
                "NX_MCP_TRACE_V1", self.nxopen.NXObject.AttributeType.String, -1
            ):
                continue
            records = json.loads(line.GetStringAttribute("NX_MCP_TRACE_V1"))
            positions = [self._trace_position(explosion, record) for record in records]
            pending.append((line, positions))
        if not pending:
            return
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "Refresh explosion traces"
        )
        try:
            for line, positions in pending:
                import math

                if math.dist(*positions) <= 1e-9:
                    line.Blank()
                    continue
                line.Unblank()
                for point, position in zip(
                    [line.StartPoint, line.EndPoint], positions, strict=True
                ):
                    point.SetCoordinates(self.nxopen.Point3d(*position))
            if self.session.UpdateManager.DoUpdate(mark):
                raise NXToolError("NX_UPDATE_FAILED", "Managed trace refresh failed")
        except Exception:
            self.session.UndoToMark(mark, None)
            raise
        finally:
            self.session.DeleteUndoMark(mark, None)

    def _export_explosion_animation(
        self, explosion, path, frames=12, fps=12, width=960, height=600
    ):
        import base64
        import hashlib
        import json
        import shutil
        import tempfile
        from pathlib import Path

        import NXOpen.UF as U

        from nx_mcp.animation import interpolate_rotation

        if (
            type(frames) is not int
            or not 2 <= frames <= 30
            or type(fps) is not int
            or not 1 <= fps <= 60
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "frames must be 2..30 and fps 1..60")
        if any(type(v) is not int or not 128 <= v <= 1600 for v in [width, height]):
            raise NXToolError("NX_INVALID_ARGUMENT", "Frame dimensions must be 128..1600 pixels")
        part = self._explosion_context()
        if self.session.IsBatch:
            raise NXToolError(
                "NX_VIEWPORT_UNAVAILABLE", "Animation rendering requires interactive NX"
            )
        if part.DrawingSheets.CurrentDrawingSheet is not None:
            raise NXToolError(
                "NX_DRAWING_ACTIVE", "Show a modeling view before exporting an animation"
            )
        display = U.UFSession.GetUFSession().Disp
        self._require_api(display, "RegenerateDisplay")
        ex = self._explosion(explosion)
        entries = [
            self._exploded_record(entry)
            for entry in self._exploded_tree(ex).values()
            if not entry[3]
        ]
        if not entries or len(entries) > 1000:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Explosion must have 1..1000 unsuppressed components"
            )
        destination = self.workspace.resolve(path)
        if destination.suffix.lower() != ".html" or destination.exists():
            raise NXToolError("NX_INVALID_ARGUMENT", "Choose an unused workspace .html path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".nx-animation-", dir=destination.parent)
        view = part.ModelingViews.WorkView
        uf = self._explosion_uf()
        original_explosion = uf.AskViewExplosion(view.Tag)
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "NX MCP animation preview"
        )
        previous_mark = getattr(self, "_active_mark", None)
        self._active_mark = mark
        images = []
        reports = []
        try:
            uf.SetViewExplosion(view.Tag, ex.Tag)
            for index in range(frames):
                fraction = index / (frames - 1)
                placements = [
                    {
                        "component": r["component"]["id"],
                        "translation": [
                            a + fraction * (b - a)
                            for a, b in zip(
                                r["assembled_translation"], r["translation"], strict=True
                            )
                        ],
                        "rotation_matrix": interpolate_rotation(
                            r["assembled_rotation_matrix"], r["rotation_matrix"], fraction
                        ),
                    }
                    for r in entries
                ]
                self._edit_explosion(explosion, placements=placements)
                self._refresh_explosion_traces(ex)
                display.RegenerateDisplay()
                result = self._render_view(
                    path=str(Path(stage) / f"{index:03d}.png"),
                    width=width,
                    height=height,
                    style="shaded_with_edges",
                )
                data = (Path(stage) / f"{index:03d}.png").read_bytes()
                images.append("data:image/png;base64," + base64.b64encode(data).decode("ascii"))
                reports.append(
                    {
                        "index": index,
                        "fraction": fraction,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                        "camera": result.get("camera"),
                    }
                )
        finally:
            try:
                self.session.UndoToMark(mark, None)
                self.session.DeleteUndoMark(mark, None)
                uf.SetViewExplosion(view.Tag, original_explosion)
                display.RegenerateDisplay()
                view.UpdateDisplay()
            finally:
                self._active_mark = previous_mark
                shutil.rmtree(stage)
        payload = json.dumps(images)
        html = (
            '<!doctype html><meta charset="utf-8"><title>NX explosion animation</title><style>body{font:16px system-ui;background:#eee;margin:2rem}img{display:block;max-width:100%;margin:auto}button,input{margin:1rem}input{width:60%}</style><h1>Assembly explosion</h1><img id="frame" alt="NX assembly animation frame"><button id="play">Pause</button><input id="seek" type="range" min="0" max="'
            + str(frames - 1)
            + '" value="0"><span id="label"></span><script>const frames='
            + payload
            + ';let i=0,playing=true;const image=document.getElementById("frame"),seek=document.getElementById("seek"),button=document.getElementById("play");function show(){image.src=frames[i];seek.value=i;document.getElementById("label").textContent=(i+1)+" / "+frames.length}button.onclick=()=>{playing=!playing;button.textContent=playing?"Pause":"Play"};seek.oninput=()=>{i=Number(seek.value);show()};setInterval(()=>{if(playing){i=(i+1)%frames.length;show()}},'
            + str(1000 / fps)
            + ");show();</script>"
        )
        # Exclusive creation prevents a race from overwriting an existing artifact.
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(html)
        data = destination.read_bytes()
        return {
            "path": str(destination),
            "mime_type": "text/html",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "frames": reports,
            "frame_count": frames,
            "fps": fps,
            "width": width,
            "height": height,
            "units": self._units(),
            "coordinate_frame": "assembly",
            "model_restored": True,
            "interpolation": "linear translation and shortest-arc quaternion rotation from assembled to current exploded poses",
            "camera": "fixed current NX modeling camera; frame the full motion before export",
        }
