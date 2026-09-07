"""Checks that the bridge source imports without third-party site packages."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_nx_bridge_import_succeeds_without_site_packages() -> None:
    """The bridge module must import using only the standard library and source tree."""
    source_root = Path(__file__).parents[1] / "src"
    environment = os.environ | {"PYTHONPATH": str(source_root)}

    result = subprocess.run(
        [sys.executable, "-S", "-c", "import nx_mcp.nx_bridge"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_hardened_runtime_import_has_no_sidecar_dependencies():
    source_root = Path(__file__).parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-S", "-c", "import nx_mcp.hardened"],
        capture_output=True,
        text=True,
        env=os.environ | {"PYTHONPATH": str(source_root)},
    )
    assert result.returncode == 0, result.stderr
