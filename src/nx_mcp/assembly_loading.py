"""Read assembly load state without assuming an occurrence has a loaded Part."""

from nx_mcp.runtime import NXToolError


def component_info(component):
    prototype = component.Prototype
    path = getattr(prototype, "FullPath", None)
    loaded = bool(path)
    result = {
        "part_path": path or None,
        "load_state": "fully_loaded"
        if loaded and getattr(prototype, "IsFullyLoaded", True)
        else "partially_loaded"
        if loaded
        else "unloaded",
        "prototype_type": type(prototype).__name__ if prototype is not None else None,
    }
    if not loaded:
        import NXOpen.UF as U

        try:
            path = U.UFSession.GetUFSession().Assem.AskComponentData(component.Tag)[0]
            result["part_path"] = path or None
        except Exception as error:
            result["load_diagnostic"] = str(error)
    return result


def require_loaded(executor, part):
    incomplete = []
    for component, path in executor._walk_components(part):
        if component.IsSuppressed:
            continue
        info = component_info(component)
        if info["load_state"] != "fully_loaded":
            incomplete.append({"occurrence_path": path, **info})
    if incomplete:
        raise NXToolError(
            "NX_ASSEMBLY_NOT_LOADED",
            "Load assembly components before updating or exporting drawings",
            details={"components": incomplete, "mutation_outcome": "not_started"},
        )


def dependent_assemblies(executor, target):
    parents = []
    for part in executor.session.Parts:
        if part == target:
            continue
        for component, _ in executor._walk_components(part):
            prototype = component.Prototype
            if prototype is not None and int(prototype.Tag) == int(target.Tag):
                parents.append(part.FullPath)
                break
    return parents


def load_components(executor, part):
    """Explicitly load occurrence prototypes; restore the session loading preference."""
    options = executor.session.Parts.LoadOptions
    partial = options.UsePartialLoading
    opened = set()
    try:
        options.UsePartialLoading = False
        while True:
            pending = [
                c
                for c, _ in executor._walk_components(part)
                if not c.IsSuppressed and int(c.Tag) not in opened
            ]
            if not pending:
                break
            if len(opened) + len(pending) > 10000:
                raise NXToolError("NX_OBJECT_LIMIT", "Component loading exceeds 10000 occurrences")
            status, _ = part.ComponentAssembly.OpenComponents(
                part.ComponentAssembly.OpenOption.ComponentOnly, pending
            )
            try:
                if status and status.NumberUnloadedParts:
                    raise NXToolError(
                        "NX_COMPONENT_LOAD_FAILED",
                        "Some component prototypes could not be loaded",
                        details={
                            "parts": [
                                {"path": status.GetPartName(i), "nx_code": status.GetStatus(i)}
                                for i in range(status.NumberUnloadedParts)
                            ]
                        },
                    )
            finally:
                if status:
                    status.Dispose()
            opened.update(int(c.Tag) for c in pending)
        require_loaded(executor, part)
        return {"requested": True, "checked_occurrences": len(opened), "complete": True}
    except NXToolError as error:
        error.details["mutation_outcome"] = "partial"
        raise
    finally:
        options.UsePartialLoading = partial
