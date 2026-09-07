"""Explicit reference-set membership and datum display controls for NX 2606."""

from nx_mcp.runtime import NXToolError


class ReferenceGeometryMixin:
    def _flat_pattern_orientation_edges(self, upward_face):
        import NXOpen.UF

        from nx_mcp.hardened import xyz

        face = self._engineering_owned(upward_face, "face")
        uf = NXOpen.UF.UFSession.GetUFSession()
        data = uf.Modeling.AskFaceData(face.Tag)
        if data[0] != 22:
            raise NXToolError("NX_INVALID_ARGUMENT", "Select a planar upward web face")
        if not self._sm_manager().IsSheetmetalBody(face.GetBody()):
            raise NXToolError("NX_OBJECT_TYPE_MISMATCH", "Select a sheet-metal face")
        items = []
        for edge in face.GetEdges():
            if edge.SolidEdgeType != self.nxopen.Edge.EdgeType.Linear:
                continue
            start, end = edge.GetVertices()
            items.append(
                {
                    "edge": self._reference(edge, "edge", self._work_part(), "Orientation edge"),
                    "start": xyz(start),
                    "end": xyz(end),
                    "adjacent_faces": [
                        self._reference(f, "face", self._work_part(), "Face")["id"]
                        for f in edge.GetFaces()
                    ],
                }
            )
        return {
            "upward_face": self._reference(face, "face", self._work_part(), "Upward face"),
            "edges": items,
            "count": len(items),
            "coordinate_frame": "work_part",
            "eligibility": "linear_boundary_of_planar_sheet_metal_face",
            "warnings": [
                "These geometric candidates avoid stale outer-edge guesses. Final validity depends on the native Flat Pattern commit; no temporary feature is committed by this inspection."
            ],
        }

    def _reference_set_record(self, value):
        members = list(value.AskAllDirectMembers())
        return {
            "object": self._reference(value, "reference_set", self._work_part(), "Reference set"),
            "name": value.Name,
            "member_count": len(members),
            "members": [self._display_ref(x) for x in members],
            "add_components_automatically": bool(value.GetAddComponentsAutomatically()),
        }

    def _list_reference_sets(self):
        part = self._work_part()
        self._require_api(part, "GetAllReferenceSets")
        records = [self._reference_set_record(x) for x in part.GetAllReferenceSets()]
        return {
            "reference_sets": records,
            "count": len(records),
            "built_in": ["Entire Part", "Empty"],
        }

    def _create_reference_set(self, name, objects):
        part = self._work_part()
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 132
            or name.casefold() in {"entire part", "empty"}
        ):
            raise NXToolError(
                "NX_INVALID_ARGUMENT",
                "Use a nonempty custom reference-set name of at most 132 characters",
            )
        self._require_api(part, "CreateReferenceSet", "GetAllReferenceSets")
        if any(x.Name.casefold() == name.casefold() for x in part.GetAllReferenceSets()):
            raise NXToolError("NX_ALREADY_EXISTS", "A reference set with this name already exists")
        if not isinstance(objects, list) or not 1 <= len(objects) <= 1000:
            raise NXToolError("NX_INVALID_ARGUMENT", "objects requires 1–1000 explicit references")
        values = [self._resolve(x, {"body", "curve", "component", "datum"}) for x in objects]
        if any(x.IsOccurrence and x.OwningPart != part for x in values):
            raise NXToolError("NX_OBJECT_OWNER_MISMATCH", "Members must be owned by the work part")
        result = part.CreateReferenceSet()
        result.SetName(name)
        result.SetAddComponentsAutomatically(False, False)
        result.AddObjectsToReferenceSet(values)
        self._update_model()
        return self._reference_set_record(result)

    def _set_component_reference_set(self, components, name):
        part = self._work_part()
        if not isinstance(components, list) or not 1 <= len(components) <= 1000:
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "components requires 1–1000 direct occurrence references"
            )
        values = [self._resolve(x, {"component"}) for x in components]
        direct = (
            list(part.ComponentAssembly.RootComponent.GetChildren())
            if part.ComponentAssembly.RootComponent
            else []
        )
        if any(x not in direct for x in values):
            raise NXToolError(
                "NX_OBJECT_OWNER_MISMATCH",
                "Activate the owning assembly; only direct children can be changed",
            )
        for c in values:
            names = [x.Name for x in c.Prototype.GetAllReferenceSets()] + ["Entire Part", "Empty"]
            if name not in names:
                raise NXToolError(
                    "NX_NOT_FOUND",
                    "Reference set is absent from a component prototype",
                    details={"component": c.Name, "reference_set": name},
                )
        self._require_api(part.ComponentAssembly, "ReplaceReferenceSet")
        before = [
            {
                "object": self._reference(c, "component", part, "Component"),
                "previous_reference_set": c.ReferenceSet,
            }
            for c in values
        ]
        for c in values:
            part.ComponentAssembly.ReplaceReferenceSet(c, name)
        self._update_model()
        if any(c.ReferenceSet != name for c in values):
            raise NXToolError("NX_VERIFICATION_FAILED", "Reference-set assignment did not persist")
        return {
            "components": [
                dict(r, reference_set=c.ReferenceSet) for r, c in zip(before, values, strict=True)
            ],
            "count": len(values),
            "prototype_parts_modified": False,
        }

    def _datum_objects(self):
        part = self._work_part()
        values = list(part.Datums) + list(part.CoordinateSystems)
        return list({int(x.Tag): x for x in values}.values())

    def _list_datums(self):
        values = self._datum_objects()
        return {
            "datums": [
                {
                    "object": self._reference(x, "datum", self._work_part(), "Datum"),
                    "native_type": type(x).__name__,
                    "blanked": bool(x.IsBlanked),
                }
                for x in values
            ],
            "count": len(values),
            "scope": "work_part",
        }

    def _set_datum_visibility(self, visible=False):
        self._visual_part()
        values = self._datum_objects()
        before = self._display_records(values)
        for x in values:
            (x.Unblank if visible else x.Blank)()
        return {
            "restore_id": self._save_display_snapshot(before),
            "objects": self._display_records(values),
            "count": len(values),
            "visible": visible,
            "scope": "work_part",
            "warnings": [
                "Changes owned datums and coordinate systems only. Use body-only component reference sets to exclude prototype datums from assembly drawings."
            ],
        }
