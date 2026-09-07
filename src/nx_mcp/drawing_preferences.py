"""Associative dimension formatting and native drafting view presentation."""

from nx_mcp.runtime import NXToolError
from nx_mcp.visual_tools import enum_name

UNITS = {"mm": "Millimeters", "in": "Inches", "m": "Meters", "um": "Micrometers"}
TOLERANCES = {
    "none": "NotSet",
    "bilateral": "BilateralTwoLines",
    "symmetric": "BilateralOneLine",
    "limits": "LimitTwoLines",
    "basic": "Basic",
    "reference": "Reference",
}
WIDTHS = {
    "original": "Original",
    "thin": "Thin",
    "normal": "Normal",
    "thick": "Thick",
    **{
        str(i): name
        for i, name in enumerate(
            ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"], 1
        )
    },
}
RENDERING = {
    "fully_shaded": "FullyShaded",
    "partially_shaded": "PartiallyShaded",
    "wireframe": "Wireframe",
}
STYLE_PROPERTIES = {
    "hidden_lines": ("ViewStyleHiddenLines", "HiddenLine"),
    "hidden_font": ("ViewStyleHiddenLines", "Font"),
    "hidden_width": ("ViewStyleHiddenLines", "Width"),
    "self_hidden": ("ViewStyleHiddenLines", "SelfHidden"),
    "visible_font": ("ViewStyleVisibleLines", "VisibleFont"),
    "visible_width": ("ViewStyleVisibleLines", "VisibleWidth"),
    "smooth_edges": ("ViewStyleSmoothEdges", "SmoothEdge"),
    "smooth_font": ("ViewStyleSmoothEdges", "Font"),
    "centerlines": ("ViewStyleGeneral", "Centerlines"),
    "rendering": ("ViewStyleShading", "RenderingStyle"),
}


class DrawingPreferencesMixin:
    def _dimension_format(self, dimension):
        import NXOpen.Annotations as A

        d = self._engineering_owned(dimension, "dimension")
        prefs = d.GetDimensionPreferences()
        units = prefs.GetUnitsFormatPreferences()
        try:
            return {
                "object": self._reference(d, "dimension", self._work_part(), "Dimension"),
                "computed_value": d.ComputedSize,
                "measurement_units": self._units(),
                "measurement_valid": not bool(d.IsRetained),
                "decimal_places": d.NominalDecimalPlaces,
                "tolerance_decimal_places": d.ToleranceDecimalPlaces,
                "upper_tolerance": d.UpperMetricToleranceValue
                if self._units() == "mm"
                else d.UpperToleranceValue,
                "lower_tolerance": d.LowerMetricToleranceValue
                if self._units() == "mm"
                else d.LowerToleranceValue,
                "tolerance_type": enum_name(d.ToleranceType, A.ToleranceType),
                "trailing_zeros": bool(units.DisplayTrailingZeros),
                "display_units": enum_name(units.PrimaryDimensionUnit, A.DimensionUnit),
                "decimal_separator": enum_name(
                    units.DecimalPointCharacter, A.DecimalPointCharacter
                ),
                "association_count": d.NumberOfAssociativities,
            }
        finally:
            units.Dispose()
            prefs.Dispose()

    def _edit_dimension_format(
        self,
        dimension,
        decimal_places=None,
        trailing_zeros=None,
        units=None,
        decimal_separator=None,
        tolerance_type=None,
        upper_tolerance=None,
        lower_tolerance=None,
        tolerance_decimal_places=None,
    ):
        import NXOpen.Annotations as A

        from nx_mcp.authoring import finite

        values = locals().copy()
        if all(
            values[k] is None
            for k in (
                "decimal_places",
                "trailing_zeros",
                "units",
                "decimal_separator",
                "tolerance_type",
                "upper_tolerance",
                "lower_tolerance",
                "tolerance_decimal_places",
            )
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Supply at least one formatting property")
        for key in ["decimal_places", "tolerance_decimal_places"]:
            if values[key] is not None and (
                type(values[key]) is not int or not 0 <= values[key] <= 8
            ):
                raise NXToolError("NX_INVALID_ARGUMENT", f"{key} must be 0..8")
        if (
            units is not None
            and units not in UNITS
            or tolerance_type is not None
            and tolerance_type not in TOLERANCES
            or decimal_separator is not None
            and decimal_separator not in {"period", "comma"}
        ):
            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported formatting enum")
        for key in ["upper_tolerance", "lower_tolerance"]:
            if values[key] is not None:
                values[key] = finite(values[key], key)
        d = self._engineering_owned(dimension, "dimension")
        before = (d.ComputedSize, d.NumberOfAssociativities, bool(d.IsRetained))
        prefs = d.GetDimensionPreferences()
        formatting = prefs.GetUnitsFormatPreferences()
        try:
            if trailing_zeros is not None:
                formatting.DisplayTrailingZeros = trailing_zeros
            if units is not None:
                formatting.PrimaryDimensionUnit = getattr(A.DimensionUnit, UNITS[units])
            if decimal_separator is not None:
                formatting.DecimalPointCharacter = getattr(
                    A.DecimalPointCharacter, "Period" if decimal_separator == "period" else "Comma"
                )
            prefs.SetUnitsFormatPreferences(formatting)
            d.SetDimensionPreferences(prefs)
        finally:
            formatting.Dispose()
            prefs.Dispose()
        if tolerance_type is not None:
            d.ToleranceType = getattr(A.ToleranceType, TOLERANCES[tolerance_type])
        for key, native, metric in [
            ("decimal_places", "NominalDecimalPlaces", "MetricNominalDecimalPlaces"),
            ("tolerance_decimal_places", "ToleranceDecimalPlaces", "MetricToleranceDecimalPlaces"),
        ]:
            if values[key] is not None:
                setattr(d, native, values[key])
                setattr(d, metric, values[key])
        for key, native, metric in [
            ("upper_tolerance", "UpperToleranceValue", "UpperMetricToleranceValue"),
            ("lower_tolerance", "LowerToleranceValue", "LowerMetricToleranceValue"),
        ]:
            if values[key] is not None:
                metric_value = values[key] * (25.4 if self._units() == "inch" else 1.0)
                setattr(d, native, metric_value / 25.4)
                setattr(d, metric, metric_value)
        d.RedisplayObject()
        if before != (d.ComputedSize, d.NumberOfAssociativities, bool(d.IsRetained)):
            raise NXToolError(
                "NX_VERIFICATION_FAILED", "Formatting changed measured value or association state"
            )
        result = self._dimension_format(dimension)
        result["modified"] = [result["object"]]
        return result

    def _drawing_construction_visibility(self, view, visible):
        import NXOpen.UF as U

        part = self._work_part()
        uf_view = U.UFSession.GetUFSession().View
        # Part.Curves also includes section lines and other sheet-owned curves.
        # They are annotations, not model construction geometry.
        values = [c for c in part.Curves if not uf_view.AskViewDependentStatus(c.Tag)[0]]
        values += self._datum_objects()
        for component, _ in self._walk_components(part):
            if component.IsSuppressed or component.Prototype is None:
                continue
            prototype = component.Prototype
            if not hasattr(prototype, "Curves"):
                continue
            for value in (
                list(prototype.Curves) + list(prototype.Datums) + list(prototype.CoordinateSystems)
            ):
                if uf_view.AskViewDependentStatus(value.Tag)[0]:
                    continue
                occurrence = component.FindOccurrence(value)
                if occurrence is not None:
                    values.append(occurrence)
        values = list({int(x.Tag): x for x in values}.values())
        if len(values) > 20000:
            raise NXToolError(
                "NX_OBJECT_LIMIT", "Drawing construction selection exceeds 20000 objects"
            )
        if values:
            if visible:
                view.DependentDisplay.RemoveErasureOnObjectAndSubobjects(values, False)
            else:
                view.DependentDisplay.Erase(values)
        view.SetAttribute("NX_MCP_DRAWING_CONSTRUCTION_V1", "show" if visible else "hide")
        return len(values)

    def _view_style(self, view, updates=None):
        import NXOpen.Preferences as P

        if updates and "centerlines" in updates:
            raise NXToolError(
                "NX_UNSUPPORTED_ARGUMENT",
                "NX v2606 does not persist the tested centerline preference setter; centerlines is read-only",
                details={"mutation_outcome": "not_started"},
            )
        obj = self._drawing_object(view, "drawing_view")
        b = self._work_part().SettingsManager.CreateDrawingEditViewSettingsBuilder([obj])
        try:
            b.InheritSettingsFromSelectedObjects(obj)
            if updates:
                for key, value in updates.items():
                    if key == "construction_geometry":
                        self._drawing_construction_visibility(obj, value)
                        continue
                    if key not in STYLE_PROPERTIES:
                        raise NXToolError(
                            "NX_INVALID_ARGUMENT", f"Unsupported view-style property: {key}"
                        )
                    group, prop = STYLE_PROPERTIES[key]
                    if key.endswith("font"):
                        if type(value) is not int or not 0 <= value <= 7:
                            raise NXToolError(
                                "NX_INVALID_ARGUMENT", "Line font must be 0..7 (0 invisible)"
                            )
                        value = P.Font.ValueOf(value)
                    elif key.endswith("width"):
                        if value not in WIDTHS:
                            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported line width")
                        value = getattr(P.Width, WIDTHS[value])
                    elif key == "rendering":
                        if value not in RENDERING:
                            raise NXToolError("NX_INVALID_ARGUMENT", "Unsupported rendering style")
                        value = getattr(P.ShadingRenderingStyleOption, RENDERING[value])
                    setattr(getattr(b.ViewStyle, group), prop, value)
                b.Commit()
            result = {
                key: getattr(getattr(b.ViewStyle, group), prop)
                for key, (group, prop) in STYLE_PROPERTIES.items()
            }
            for key, value in result.items():
                if key.endswith("font"):
                    result[key] = getattr(value, "value", value)
                elif key.endswith("width"):
                    native = enum_name(value, P.Width)
                    result[key] = next((k for k, v in WIDTHS.items() if v == native), native)
                elif key == "rendering":
                    native = enum_name(value, P.ShadingRenderingStyleOption)
                    result[key] = next((k for k, v in RENDERING.items() if v == native), native)
        finally:
            b.Destroy()
        attribute = "NX_MCP_DRAWING_CONSTRUCTION_V1"
        result["construction_geometry"] = (
            obj.GetStringAttribute(attribute) == "show"
            if obj.HasUserAttribute(attribute, self.nxopen.NXObject.AttributeType.String, -1)
            else None
        )
        if updates:
            self._update_model()
            self._work_part().DraftingViews.UpdateViews([obj])
            result = self._view_style(view)
            if any(result[k] != v for k, v in updates.items()):
                raise NXToolError(
                    "NX_VERIFICATION_FAILED",
                    "Native drafting style did not match requested settings",
                    details={"requested": updates, "actual": result},
                )
        return result
