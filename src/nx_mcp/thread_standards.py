"""Read installed thread tables in place and use native table-driven builders."""

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from nx_mcp.authoring import finite, page
from nx_mcp.runtime import NXToolError


class ThreadStandardsMixin:
    def _thread_rows(self):
        base = os.environ.get("UGII_BASE_DIR")
        if not base:
            raise NXToolError("NX_CATALOG_UNAVAILABLE", "NX installation root is not configured")
        source = Path(base) / "UGII" / "modeling_standards" / "NX_Thread_Standard.xml"
        if not source.is_file():
            raise NXToolError("NX_CATALOG_UNAVAILABLE", "Installed NX thread table was not found")
        if source.stat().st_size > 20_000_000:
            raise NXToolError("NX_CATALOG_INVALID", "Thread catalog exceeds the supported size")
        return [dict(x.attrib) for x in ET.parse(source).getroot().iter("ThreadedHole")]

    def _thread_catalog(self, standard=None, size=None, offset=0, limit=50):
        rows = self._thread_rows()
        if standard is None:
            if size is not None:
                raise NXToolError("NX_INVALID_ARGUMENT", "Select a standard before a size")
            return {
                **page(sorted({r["Standard"] for r in rows}), offset, limit),
                "level": "standards",
                "source": "installed NX thread catalog; read in place",
            }
        rows = [
            r
            for r in rows
            if r.get("Standard") == standard and (size is None or r.get("Size") == size)
        ]
        if not rows:
            raise NXToolError(
                "NX_NOT_FOUND", "Requested standard/size is absent from the installed catalog"
            )
        keys = ["Standard", "Unit", "Size", "Method", "RadialEngage", "Callout"]
        if size is not None:
            keys += [
                "MajorDiameter",
                "MinorDiameter",
                "TapDrillDia",
                "ShaftDiameter",
                "Pitch",
                "Angle",
                "NumStarts",
                "Tapered",
            ]
        result = page([{k: r.get(k) for k in keys} for r in rows], offset, limit)
        result.update(
            level="sizes" if size is None else "selected_size",
            source="installed NX thread catalog; read in place",
        )
        return result

    def _standard_thread(
        self,
        face,
        start_face,
        standard,
        size,
        length,
        method=None,
        radial_engage=None,
        detailed=False,
        left_hand=False,
        reverse=False,
    ):
        import NXOpen.Features as F
        import NXOpen.UF as U

        rows = [
            r
            for r in self._thread_rows()
            if r.get("Standard") == standard
            and r.get("Size") == size
            and (method is None or r.get("Method") == method)
            and (radial_engage is None or r.get("RadialEngage") == radial_engage)
        ]
        if len(rows) != 1:
            raise NXToolError(
                "NX_AMBIGUOUS_THREAD" if rows else "NX_NOT_FOUND",
                "Select one installed catalog row using standard, size, method and radial_engage",
            )
        row = rows[0]
        target = self._engineering_owned(face, "face")
        start = self._engineering_owned(start_face, "face")
        data = U.UFSession.GetUFSession().Modeling.AskFaceData(target.Tag)
        if data[0] != 16 or start.GetBody() != target.GetBody():
            raise NXToolError(
                "NX_INVALID_ARGUMENT", "Select a cylinder and start face on the same body"
            )
        length = finite(length, "length", True)
        b = self._freeform_builder("CreateThreadBuilder")
        try:
            b.ThreadInput = F.ThreadBuilder.Input.ThreadTable
            b.SmartThread = False
            b.CylindricalFace.Value = target
            b.StartObject.Value = start
            b.ThreadType = (
                F.ThreadBuilder.Type.Detailed if detailed else F.ThreadBuilder.Type.Symbolic
            )
            b.ThreadStandard = row["Standard"]
            b.ThreadSize = row["Size"]
            b.ThreadMethod = row["Method"]
            b.RadialEngage = row["RadialEngage"]
            b.MatchThreadSizeToCylinder = False
            b.ShaftDiameterExp.RightHandSide = str(2 * data[4])
            b.TapDrillDiameterExp.RightHandSide = str(2 * data[4])
            b.ThreadLimit = F.ThreadBuilder.LimitOption.Value
            b.ThreadLength.RightHandSide = str(length)
            b.ThreadHandedness = (
                F.ThreadBuilder.Handedness.LeftHand
                if left_hand
                else F.ThreadBuilder.Handedness.RightHand
            )
            b.ReverseThreadDirection = reverse
            result = self._freeform_commit(b)
            if b.ThreadStandard != row["Standard"] or b.ThreadSize != row["Size"]:
                raise NXToolError(
                    "NX_VERIFICATION_FAILED",
                    "Native builder did not retain the selected standard/size",
                )
            result.update(
                standard=b.ThreadStandard,
                size=b.ThreadSize,
                method=b.ThreadMethod,
                radial_engage=b.RadialEngage,
                pitch=b.Pitch,
                major_diameter=b.MajorDiameter,
                minor_diameter=b.MinorDiameter,
                internal=b.IsInternalThread,
                handedness="left"
                if b.ThreadHandedness == F.ThreadBuilder.Handedness.LeftHand
                else "right",
                parameter_units=self._units(),
                representation="detailed" if detailed else "symbolic",
                catalog_callout=row.get("Callout"),
                catalog_units=row.get("Unit"),
            )
            return result
        finally:
            b.Destroy()
