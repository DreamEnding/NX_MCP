"""Model summaries, reproducible views, inspection artifacts and reversible previews."""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import uuid
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from nx_mcp.authoring import finite, page
from nx_mcp.runtime import NXToolError


class ReviewToolsMixin:
    def _set_camera(self, rotation, origin, scale):
        from nx_mcp.hardened import vector

        view = self._visual_part().ModelingViews.WorkView
        matrix = self._validate_rotation(rotation)
        point = vector(origin, "origin")
        value = finite(scale, "scale", True)
        self._require_api(view, "SetRotationTranslationScale")
        view.SetRotationTranslationScale(
            self._nx_matrix(matrix), self.nxopen.Point3d(*point), value
        )
        view.UpdateDisplay()
        return self._view_info()

    def _restore_camera(self, camera):
        self._set_camera(camera["rotation"], camera["origin"], camera["scale"])
        styles = {
            "shaded": "Shaded",
            "shaded_with_edges": "ShadedWithEdges",
            "wireframe": "StaticWireframe",
        }
        if camera["rendering_style"] in styles:
            self._work_part().ModelingViews.WorkView.RenderingStyle = getattr(
                self.nxopen.View.RenderingStyleType, styles[camera["rendering_style"]]
            )

    @contextlib.contextmanager
    def _temporary_view(self):
        camera = self._view_info()
        snapshot_keys = set(getattr(self, "_display_snapshots", {}))
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "NX MCP temporary presentation"
        )
        try:
            yield
        finally:
            try:
                self.session.UndoToMark(mark, None)
                self.session.DeleteUndoMark(mark, None)
            except Exception as exc:
                raise NXToolError(
                    "NX_ROLLBACK_FAILED",
                    "Temporary presentation cleanup failed: " + str(exc),
                    details={"mutation_outcome": "partial"},
                ) from exc
            finally:
                for key in set(getattr(self, "_display_snapshots", {})) - snapshot_keys:
                    self._display_snapshots.pop(key, None)
                self._restore_camera(camera)
                self._clear_highlights()

    def _locator(self, obj, kind):
        return {
            "kind": kind,
            "journal_id": str(obj.JournalIdentifier),
            "owner_part": self._work_part().FullPath,
        }

    def _locate(self, locator):
        part = self._work_part()
        if locator["owner_part"].casefold() != part.FullPath.casefold():
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Saved reference belongs to another part")
        kind = locator["kind"]
        if kind == "expression":
            candidates = list(part.Expressions)
        elif kind == "component":
            candidates = [c for c, _ in self._walk_components(part)]
        elif kind == "feature":
            candidates = list(part.Features)
        elif kind == "sketch":
            candidates = list(part.Sketches)
        elif kind in {"face", "edge"}:
            bodies = self._geometry(scope="assembly")
            candidates = [
                v for b in bodies for v in (b.GetFaces() if kind == "face" else b.GetEdges())
            ]
        elif kind in {"curve", "body"}:
            candidates = list(part.Curves if kind == "curve" else part.Bodies)
        else:
            candidates = []
        matches = [c for c in candidates if c.JournalIdentifier == locator["journal_id"]]
        if len(matches) == 1:
            return matches[0]
        # Occurrence faces/bodies have assembly-context journal identifiers.
        try:
            obj = part.FindObject(locator["journal_id"])
        except Exception as exc:
            raise NXToolError(
                "NX_SAVED_REFERENCE_STALE",
                "Saved geometry no longer resolves: " + locator["journal_id"],
            ) from exc
        if obj is None:
            raise NXToolError("NX_SAVED_REFERENCE_STALE", "Saved reference is unavailable")
        valid = {
            "body": self.nxopen.Body,
            "face": self.nxopen.Face,
            "edge": self.nxopen.Edge,
            "sketch": self.nxopen.Sketch,
        }
        if kind in valid and not isinstance(obj, valid[kind]):
            raise NXToolError("NX_OBJECT_TYPE_MISMATCH", "Saved reference changed kind")
        return obj

    def _save_presentation(self, path):
        part = self._visual_part()
        file = self.workspace.ensure_inside(path)
        if file.suffix.lower() != ".json" or file.exists():
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Choose a new workspace .json presentation path"
            )
        try:
            bodies = self._geometry(scope="assembly")
        except NXToolError as err:
            if err.code != "NX_NO_TARGET_BODY":
                raise
            bodies = []
        values = bodies + [c for c, _ in self._walk_components(part)]
        records = self._display_records(values, True)
        saved = []
        for row in records:
            obj = self._resolve(row["object"]["id"])
            saved.append(
                {
                    **{k: v for k, v in row.items() if k != "object"},
                    "locator": self._locator(obj, row["object"]["kind"]),
                }
            )
        sections = self._list_sections()
        data = {
            "format": "nx-mcp-presentation",
            "version": 1,
            "part_path": part.FullPath,
            "camera": self._view_info(),
            "display": saved,
            "section": next(
                (
                    {k: r[k] for k in ("origin", "normal", "cap")}
                    for r in sections["sections"]
                    if r["active"]
                ),
                None,
            ),
            "sectioning_enabled": sections["view_sectioning_enabled"],
        }
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
        return {
            "path": str(file),
            "artifact_path": str(file.relative_to(self.workspace.root)),
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "size": file.stat().st_size,
            "objects_saved": len(saved),
            "warnings": [
                "Restores explicit appearance attributes. Across revisions, journal identifiers must still identify the intended geometry."
            ],
        }

    def _restore_presentation(self, path):
        file = self.workspace.ensure_inside(path)
        if file.stat().st_size > 8 * 1024 * 1024:
            raise NXToolError("NX_OBJECT_LIMIT", "Presentation exceeds 8 MiB")
        data = json.loads(file.read_text(encoding="utf-8"))
        if data.get("format") != "nx-mcp-presentation" or data.get("version") != 1:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported presentation format")
        if data["part_path"].casefold() != self._visual_part().FullPath.casefold():
            raise NXToolError(
                "NX_OBJECT_OWNER_MISMATCH",
                "Activate the presentation owner as work and display part",
            )
        if len(data["display"]) > 10000:
            raise NXToolError("NX_OBJECT_LIMIT", "Presentation exceeds 10000 display records")
        resolved = [(self._locate(r["locator"]), r) for r in data["display"]]
        # Preflight all saved entities and camera values before changing the viewport.
        self._validate_rotation(data["camera"]["rotation"])
        finite(data["camera"]["scale"], "scale", True)
        before = self._view_info()
        try:
            for obj, row in resolved:
                if "color_index" in row:
                    self._apply_appearance([obj], row["color_index"], row.get("transparency"))
                (obj.Blank if row["blanked"] else obj.Unblank)()
            view = self._work_part().ModelingViews.WorkView
            section = data["section"]
            if section:
                current = view.ActiveDynamicSection
                ref = (
                    self._reference(current, "section", self._work_part(), "Section")["id"]
                    if current
                    else None
                )
                self._section_view(**section, section=ref, name="Presentation section")
            view.DisplaySectioningToggle = bool(data["sectioning_enabled"])
            self._restore_camera(data["camera"])
        except Exception:
            self._restore_camera(before)
            raise
        return {
            "path": str(file),
            "restored_objects": len(resolved),
            "camera": self._view_info(),
            "geometry_changed": False,
            "modified": None,
        }

    def _model_summary(self, section="overview", offset=0, limit=50):
        part = self._work_part()
        if section not in {"overview", "components", "features", "expressions", "sketches"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported summary section")
        if section == "expressions":
            return self._list_expressions(offset=offset, limit=limit)
        if section == "components":
            return page(self._list_components()["components"], offset, limit)
        if section == "features":
            result = page(list(part.Features), offset, limit)
            result["items"] = [
                {
                    "object": self._reference(f, "feature", part, "Feature"),
                    "type": f.FeatureType,
                    "suppressed": bool(f.Suppressed),
                    "parents": [
                        self._reference(v, "feature", part, "Feature") for v in f.GetParents()
                    ],
                    "expressions": [
                        {"name": e.Name, "formula": e.RightHandSide} for e in f.GetExpressions()
                    ],
                }
                for f in result["items"]
            ]
            return result
        if section == "sketches":
            result = page(list(part.Sketches), offset, limit)
            result["items"] = [
                {
                    "object": self._reference(s, "sketch", part, "Sketch"),
                    "curve_count": len(s.GetAllGeometry()),
                    "frame": self._sketch_frame(s),
                }
                for s in result["items"]
            ]
            return result
        try:
            bounds = self._get_bounding_box()
        except NXToolError as err:
            if err.code != "NX_NO_TARGET_BODY":
                raise
            bounds = None
        return {
            "part": self._reference(part, "part", part, "Part"),
            "modified": bool(part.IsModified),
            "units": self._units(),
            "counts": {
                "bodies": len(list(part.Bodies)),
                "features": len(list(part.Features)),
                "sketches": len(list(part.Sketches)),
                "expressions": len(list(part.Expressions)),
                "component_occurrences": len(self._walk_components(part)),
            },
            "bounds": {
                k: bounds[k]
                for k in ("min", "max", "dimensions", "bounds_type", "coordinate_frame")
            }
            if bounds
            else None,
            "diagnostics": self._model_health(limit=10),
            "sections": ["components", "features", "expressions", "sketches"],
            "warning": "Counts of owned bodies differ from recursive assembly geometry.",
        }

    def _inspection_report(
        self,
        path,
        objects=None,
        minimum_clearance=0.0,
        max_pairs=100,
        include_clear=False,
        capture=True,
        section_planes=None,
    ):
        import html

        from nx_mcp.hardened import vector
        from nx_mcp.visual_tools import unit_normal

        file = self.workspace.ensure_inside(path)
        if file.suffix.lower() != ".zip" or file.exists():
            raise NXToolError("NX_INVALID_ARGUMENT", "Choose a new workspace .zip report path")
        planes = section_planes or []
        if len(planes) > 6:
            raise NXToolError("NX_INVALID_ARGUMENT", "At most six section planes")
        for plane in planes:
            if set(plane) != {"origin", "normal"}:
                raise NXToolError("NX_INVALID_ARGUMENT", "Section requires origin and normal only")
            vector(plane["origin"])
            unit_normal(plane["normal"])
        if planes and not capture:
            raise NXToolError("NX_INVALID_ARGUMENT", "Section screenshots require capture=true")
        file.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="inspection-", dir=self.workspace.root) as tmp:
            root = Path(tmp)
            checks = self._check_clearance(objects, minimum_clearance, max_pairs, include_clear)
            report = {
                "format": "nx-mcp-inspection",
                "version": 1,
                "summary": self._model_summary(),
                "clearance": checks,
                "captures": [],
            }
            if capture:
                with self._temporary_view():
                    report["captures"].append(
                        self._capture_view(str(root / "overview.png"), fit=True)
                    )
                    # Capture bounded close-ups for actual flagged pairs, not every clear envelope.
                    pairs = [
                        p
                        for p in checks["pairs"]
                        if p["classification"] in {"penetration", "contact", "below_clearance"}
                    ][:8]
                    for i, pair in enumerate(pairs):
                        refs = [r["id"] for r in pair["objects"]]
                        with self._temporary_view():
                            self._set_visibility(refs, "isolate")
                            self._highlight_objects(refs)
                            report["captures"].append(
                                self._capture_view(str(root / f"pair-{i + 1}.png"), fit=True)
                            )
                    for i, plane in enumerate(planes):
                        with self._temporary_view():
                            current = self._work_part().ModelingViews.WorkView.ActiveDynamicSection
                            ref = (
                                self._reference(current, "section", self._work_part(), "Section")[
                                    "id"
                                ]
                                if current
                                else None
                            )
                            self._section_view(**plane, section=ref)
                            report["captures"].append(
                                self._capture_view(str(root / f"section-{i + 1}.png"), fit=True)
                            )
            for image in report["captures"]:
                image["path"] = Path(image["path"]).name
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            body = (
                "<h1>NX inspection report</h1><p>Units: "
                + html.escape(self._units())
                + "</p><pre>"
                + html.escape(json.dumps(checks, indent=2))
                + "</pre>"
            )
            body += "".join(
                '<figure><img style="max-width:100%" src="'
                + html.escape(r["path"], quote=True)
                + '"><figcaption>'
                + html.escape(r["path"])
                + "</figcaption></figure>"
                for r in report["captures"]
            )
            (root / "index.html").write_text(
                '<!doctype html><meta charset="utf-8"><title>NX inspection</title>' + body,
                encoding="utf-8",
            )
            manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
            (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            # Exclusive creation prevents overwriting reports; partial ZIPs are removed on failure.
            created = False
            try:
                with file.open("xb") as stream:
                    created = True
                    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                        for p in sorted(root.iterdir()):
                            archive.write(p, p.name)
            except Exception:
                if created:
                    file.unlink(missing_ok=True)
                raise
        return {
            "path": str(file),
            "artifact_path": str(file.relative_to(self.workspace.root)),
            "size": file.stat().st_size,
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "capture_count": len(report["captures"]),
            "clearance": checks,
            "geometry_changed": False,
            "warnings": [
                "PNG views are native viewport captures, not photorealistic renders. Pair close-ups are capped at eight."
            ],
        }

    def _preview_plan(self, operations):
        allowed = {
            "nx_set_expression",
            "nx_bind_parameter",
            "nx_edit_feature",
            "nx_edit_sketch",
            "nx_set_component_transform",
            "nx_reposition_component",
        }
        if not isinstance(operations, list) or not 1 <= len(operations) <= 25:
            raise NXToolError("NX_INVALID_ARGUMENT", "Preview requires 1–25 supported edits")
        locators = {}

        def visit(value):
            if isinstance(value, str) and value.startswith("obj_"):
                obj = self._resolve(value)
                kind = self.objects._objects[value].reference.kind
                locators[value] = self._locator(obj, kind)
            elif isinstance(value, dict):
                for v in value.values():
                    visit(v)
            elif isinstance(value, list):
                for v in value:
                    visit(v)

        for op in operations:
            if set(op) != {"method", "params"} or op["method"] not in allowed:
                raise NXToolError(
                    "NX_INVALID_ARGUMENT",
                    "Preview supports expression, parameter, feature, sketch and placement edits only",
                )
            inspect.signature(self._handlers[op["method"]]).bind(**op["params"])
            visit(op["params"])
        return locators

    def _preview_snapshot(self):
        result = self._model_summary()
        result["parameters"] = [
            {k: self._expression_record(e)[k] for k in ("name", "formula", "value", "units")}
            for e in self._work_part().Expressions
            if e.Type == "Number"
        ]
        try:
            result["volume"] = self._measure_volume()
        except NXToolError as err:
            if err.code != "NX_NO_TARGET_BODY":
                raise
            result["volume"] = None
        return result

    def _preview_change(self, operations, capture=True):
        import copy

        locators = self._preview_plan(operations)
        before = self._preview_snapshot()
        part = self._work_part()
        pid = self._part_id(part)
        mark = self.session.SetUndoMark(
            self.nxopen.Session.MarkVisibility.Invisible, "NX MCP change preview"
        )
        previous = self._active_mark
        camera = self._view_info() if capture else None
        try:
            self._active_mark = mark
            for op in operations:
                self._handlers[op["method"]](**op["params"])
            after = self._preview_snapshot()
            image = self._capture_view(fit=True) if capture else None
        finally:
            self._active_mark = previous
            try:
                self.session.UndoToMark(mark, None)
                self.session.DeleteUndoMark(mark, None)
                self.objects.invalidate_part(pid)
            except Exception as exc:
                raise NXToolError(
                    "NX_ROLLBACK_FAILED",
                    "Preview rollback failed: " + str(exc),
                    details={"mutation_outcome": "partial"},
                ) from exc
            finally:
                if camera:
                    self._restore_camera(camera)
        token = "preview_" + uuid.uuid4().hex
        if not hasattr(self, "_previews"):
            self._previews = {}
        if len(self._previews) >= 20:
            self._previews.pop(next(iter(self._previews)))
        self._previews[token] = {
            "operations": copy.deepcopy(operations),
            "locators": locators,
            "part_id": pid,
            "epoch": getattr(self, "_review_epoch", 0),
        }
        return {
            "preview_id": token,
            "before": before,
            "after": after,
            "capture": image,
            "model_outcome": "rolled_back",
            "warnings": [
                "Preview geometry has already been rolled back; returned geometry references are stale. Accept reapplies the stored edits only if no intervening mutation or manual handoff occurred."
            ],
        }

    def _finish_preview(self, preview_id, action):
        if action not in {"accept", "discard"}:
            raise NXToolError("NX_INVALID_ARGUMENT", "action must be accept or discard")
        preview = getattr(self, "_previews", {}).get(preview_id)
        if preview is None:
            raise NXToolError("NX_PREVIEW_STALE", "Unknown or consumed preview")
        if action == "discard":
            del self._previews[preview_id]
            return {"preview_id": preview_id, "action": "discard", "geometry_changed": False}
        if preview["epoch"] != getattr(self, "_review_epoch", 0) or preview[
            "part_id"
        ] != self._part_id(self._work_part()):
            raise NXToolError("NX_PREVIEW_STALE", "Model/session changed; create a fresh preview")
        replacements = {
            key: self._reference(self._locate(v), v["kind"], self._work_part(), "Preview target")[
                "id"
            ]
            for key, v in preview["locators"].items()
        }

        def translate(value):
            if isinstance(value, str):
                return replacements.get(value, value)
            if isinstance(value, dict):
                return {k: translate(v) for k, v in value.items()}
            if isinstance(value, list):
                return [translate(v) for v in value]
            return value

        results = [
            self._handlers[op["method"]](**translate(op["params"])) for op in preview["operations"]
        ]
        del self._previews[preview_id]
        return {
            "preview_id": preview_id,
            "action": "accept",
            "results": results,
            "geometry_changed": True,
        }
