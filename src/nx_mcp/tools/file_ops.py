"""File operation tools — create, open, save, close, import/export NX parts."""

from __future__ import annotations

import os

from nx_mcp.nx_session import NXSession
from nx_mcp.response import ToolError, ToolResult
from nx_mcp.tools.registry import mcp_tool


# ---------------------------------------------------------------------------
# 1. nx_create_part
# ---------------------------------------------------------------------------
@mcp_tool(
    name="nx_create_part",
    description="Create a new NX part file at the specified path.",
    params={
        "path": {
            "type": "string",
            "description": "Full file path for the new part (e.g. C:\\parts\\new.prt).",
            "required": True,
        },
        "units": {
            "type": "string",
            "description": "Unit system: 'mm' for millimeters, 'in' for inches. Default is 'mm'.",
            "required": False,
        },
    },
)
async def nx_create_part(path: str, units: str = "mm") -> ToolResult | ToolError:
    """Create a new NX part file."""
    try:
        import NXOpen

        session = NXSession.get_instance().require()

        unit_enum = (
            NXOpen.BasePart.Units.Millimeters
            if units.lower() in ("mm", "millimeters")
            else NXOpen.BasePart.Units.Inches
        )

        file_new = session.Parts.FileNew()
        file_new.TemplateFileName = "model-simple.prt"
        file_new.Units = unit_enum
        file_new.NewFileName = path
        file_new.MakeDisplayedPart = True
        file_new.ApplicationName = "ModelTemplate"
        file_new.MasterFileName = ""
        file_new.UseBlankTemplate = True
        file_new.Apply()

        part = session.Parts.Display
        return ToolResult.success(
            data={"path": part.FullPath, "name": part.Name},
            message=f"Created new part: {part.Name}",
        )

    except Exception as exc:
        return ToolResult.from_exception(exc)


# ---------------------------------------------------------------------------
# 2. nx_open_part
# ---------------------------------------------------------------------------
@mcp_tool(
    name="nx_open_part",
    description="Open an existing NX part file.",
    params={
        "path": {
            "type": "string",
            "description": "Full file path of the part to open (.prt).",
            "required": True,
        },
    },
)
async def nx_open_part(path: str) -> ToolResult | ToolError:
    """Open an existing NX part file."""
    try:
        session = NXSession.get_instance().require()

        _base_part, _load_status = session.Parts.OpenBaseDisplay(path)
        part = session.Parts.Display

        return ToolResult.success(
            data={"path": part.FullPath, "name": part.Name},
            message=f"Opened part: {part.Name}",
        )

    except Exception as exc:
        return ToolResult.from_exception(exc)


# ---------------------------------------------------------------------------
# 3. nx_save_part
# ---------------------------------------------------------------------------
@mcp_tool(
    name="nx_save_part",
    description="Save only the active work part; component files are not saved. Activate and save each component explicitly. The integration profile reports saved paths, disk fingerprints, native save errors and preservation checks for unrelated loaded parts.",
    params={},
)
async def nx_save_part() -> ToolResult | ToolError:
    """Save the current work part."""
    try:
        import NXOpen

        part = NXSession.get_instance().require_work_part()

        part.Save(
            NXOpen.BasePart.SaveComponents.FalseValue, NXOpen.BasePart.CloseAfterSave.FalseValue
        )

        return ToolResult.success(
            data={"path": part.FullPath, "name": part.Name},
            message=f"Saved part: {part.Name}",
        )

    except Exception as exc:
        return ToolResult.from_exception(exc)


# ---------------------------------------------------------------------------
# 4. nx_save_as
# ---------------------------------------------------------------------------
@mcp_tool(
    name="nx_save_as",
    description="Save the current work part to a new file path.",
    params={
        "path": {
            "type": "string",
            "description": "Full file path for the new part file.",
            "required": True,
        },
    },
)
async def nx_save_as(path: str) -> ToolResult | ToolError:
    """Save the current work part to a new file."""
    try:
        part = NXSession.get_instance().require_work_part()

        part.SaveAs(path)

        return ToolResult.success(
            data={"path": part.FullPath, "name": part.Name},
            message=f"Saved part as: {path}",
        )

    except Exception as exc:
        return ToolResult.from_exception(exc)


# ---------------------------------------------------------------------------
# 5. nx_close_part
# ---------------------------------------------------------------------------
@mcp_tool(
    name="nx_close_part",
    description="Close the currently active (work) part.",
    params={
        "save": {
            "type": "boolean",
            "description": "Whether to save before closing. Default is true.",
            "required": False,
        },
    },
)
async def nx_close_part(save: bool = True) -> ToolResult | ToolError:
    """Close the current work part."""
    try:
        import NXOpen

        session = NXSession.get_instance().require()
        part = NXSession.get_instance().require_work_part()

        part_name = part.Name
        part_path = part.FullPath

        if save:
            part.Save(
                NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.FalseValue
            )

        session.Parts.CloseDisplay(part, NXOpen.BasePart.CloseModified.CloseModified, None)

        return ToolResult.success(
            data={"path": part_path, "name": part_name},
            message=f"Closed part: {part_name} (saved={save})",
        )

    except Exception as exc:
        return ToolResult.from_exception(exc)


# ---------------------------------------------------------------------------
# 6. nx_export_step
# ---------------------------------------------------------------------------
_EXPORT_FORMATS = {
    "step": {"description": "STEP AP214", "ext": ".stp"},
    "iges": {"description": "IGES", "ext": ".igs"},
    "stl": {"description": "STL", "ext": ".stl"},
    "parasolid": {"description": "Parasolid", "ext": ".x_t"},
}


@mcp_tool(
    name="nx_export_step",
    description="Export the current work part to STEP, IGES, STL, or Parasolid format.",
    params={
        "path": {
            "type": "string",
            "description": "Full file path for the exported file.",
            "required": True,
        },
        "format": {
            "type": "string",
            "description": "Export format: step, iges, stl, or parasolid. Default is 'step'.",
            "required": False,
        },
    },
)
async def nx_export_step(path: str, format: str = "step") -> ToolResult | ToolError:  # noqa: A002
    """Export the current work part to a CAD interchange format."""
    try:
        fmt_key = format.lower().strip()
        if fmt_key not in _EXPORT_FORMATS:
            valid = ", ".join(_EXPORT_FORMATS.keys())
            return ToolError(
                error_code="NX_INVALID_PARAMS",
                message=f"Unsupported export format: '{format}'.",
                suggestion=f"Use one of: {valid}.",
            )

        session = NXSession.get_instance().require()
        NXSession.get_instance().require_work_part()

        # Use the appropriate NX Open exporter based on format
        if fmt_key == "step":
            step_creator = session.DexManager.CreateStepCreator()
            step_creator.OutputFile = path
            step_creator.Apply()
        elif fmt_key == "iges":
            iges_creator = session.DexManager.CreateIgesCreator()
            iges_creator.OutputFile = path
            iges_creator.Apply()
        elif fmt_key == "stl":
            stl_creator = session.DexManager.CreateStlCreator()
            stl_creator.OutputFile = path
            stl_creator.Apply()
        elif fmt_key == "parasolid":
            ps_creator = session.DexManager.CreateParasolidTranslator()
            ps_creator.OutputFile = path
            ps_creator.Apply()

        info = _EXPORT_FORMATS[fmt_key]
        return ToolResult.success(
            data={"path": path, "format": fmt_key, "description": info["description"]},
            message=f"Exported part to {info['description']} at: {path}",
        )

    except Exception as exc:
        return ToolResult.from_exception(exc)


# ---------------------------------------------------------------------------
# 7. nx_import_geometry
# ---------------------------------------------------------------------------
_IMPORT_EXTENSIONS = {".stp", ".step", ".igs", ".iges", ".x_t", ".x_b"}


@mcp_tool(
    name="nx_import_geometry",
    description="Import geometry from STEP, IGES, or Parasolid files into the current work part.",
    params={
        "path": {
            "type": "string",
            "description": "Full file path to import (.stp/.step, .igs/.iges, .x_t/.x_b).",
            "required": True,
        },
    },
)
async def nx_import_geometry(path: str) -> ToolResult | ToolError:
    """Import geometry from an external CAD file into the current work part."""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext not in _IMPORT_EXTENSIONS:
            valid = ", ".join(sorted(_IMPORT_EXTENSIONS))
            return ToolError(
                error_code="NX_INVALID_PARAMS",
                message=f"Unsupported import file extension: '{ext}'.",
                suggestion=f"Use one of: {valid}.",
            )

        session = NXSession.get_instance().require()
        NXSession.get_instance().require_work_part()

        if ext in (".stp", ".step"):
            importer = session.DexManager.CreateStepImporter()
            importer.InputFile = path
            importer.Apply()
        elif ext in (".igs", ".iges"):
            importer = session.DexManager.CreateIgesImporter()
            importer.InputFile = path
            importer.Apply()
        elif ext in (".x_t", ".x_b"):
            importer = session.DexManager.CreateParasolidImporter()
            importer.InputFile = path
            importer.Apply()

        return ToolResult.success(
            data={"path": path, "extension": ext},
            message=f"Imported geometry from: {path}",
        )

    except Exception as exc:
        return ToolResult.from_exception(exc)


# ---------------------------------------------------------------------------
# 8. nx_list_open_parts
# ---------------------------------------------------------------------------
@mcp_tool(
    name="nx_list_open_parts",
    description="List all currently open parts in the NX session.",
    params={},
)
async def nx_list_open_parts() -> ToolResult | ToolError:
    """List all currently open parts."""
    try:
        session = NXSession.get_instance().require()

        parts_array = session.Parts.ToArray()
        work_name = session.Parts.Work.Name if session.Parts.Work is not None else None
        parts_list = []
        for p in parts_array:
            parts_list.append(
                {
                    "name": p.Name,
                    "path": p.FullPath,
                    "is_work": p.Name == work_name if work_name is not None else False,
                }
            )

        return ToolResult.success(
            data={"parts": parts_list, "count": len(parts_list)},
            message=f"Open parts: {len(parts_list)}",
        )

    except Exception as exc:
        return ToolResult.from_exception(exc)
