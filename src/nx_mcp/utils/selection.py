"""Selection utilities for NX Open operations."""

from __future__ import annotations

import contextlib
from typing import Any


def create_collector_from_names(
    work_part: Any,
    names: list[str],
    object_type: int | None = None,
) -> Any:
    """Create an ScCollector from a list of named object references.

    Resolves each name against the work part's features, bodies, and curves,
    then adds matching objects to a new ScCollector.

    Parameters
    ----------
    work_part : Any
        The NX work part.
    names : list[str]
        Object names to resolve (case-insensitive).
    object_type : int | None
        Optional NXOpen.UF.UFConstants filter (e.g. UF_solid_type).
        If None, all object types are accepted.

    Returns
    -------
    Any
        An NXOpen.ScCollector containing the resolved objects.
    """
    import NXOpen

    collector = work_part.ScCollectors.CreateCollector()

    # Build a mask triple array if object_type is specified
    if object_type is not None:
        mask = NXOpen.Selection.MaskTriple(
            NXOpen.UF.UFConstants.UF_solid_type,
            0,
            object_type,
        )
        collector.SetSelectionMask([mask])

    # Resolve names and add to collector
    all_objects: list[Any] = []
    for collection_name in ("Features", "Bodies", "Curves"):
        collection = getattr(work_part, collection_name, None)
        if collection is not None:
            with contextlib.suppress(Exception):
                all_objects.extend(list(collection))

    resolved: list[Any] = []
    for name in names:
        target = name.lower()
        for obj in all_objects:
            if obj.Name.lower() == target:
                resolved.append(obj)
                break

    if resolved:
        collector.AddObjects(resolved)

    return collector
