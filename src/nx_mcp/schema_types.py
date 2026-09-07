"""Schema metadata is optional in NX's standard-library-only embedded Python.

The sidecar requires Pydantic and builds/validates actual models. Native handlers
only import contract names and effect sets; they never instantiate these models.
"""

try:
    from pydantic import BaseModel, ConfigDict, Field
except ModuleNotFoundError as error:
    if error.name != "pydantic":
        raise

    class BaseModel:  # type: ignore[no-redef]
        pass

    ConfigDict = dict  # type: ignore[misc,assignment]

    def Field(*args, **kwargs):  # type: ignore[no-redef]
        return None
