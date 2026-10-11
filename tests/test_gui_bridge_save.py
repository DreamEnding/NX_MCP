"""Run production C# save/close handlers against injectable NX save statuses."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.fake_nx,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows C# compiler"),
]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def save_harness(tmp_path_factory):
    compiler = (
        Path(os.environ.get("WINDIR", "C:/Windows"))
        / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    if not compiler.exists():
        pytest.skip("in-box .NET Framework compiler unavailable")
    source = (ROOT / "nx_gui_bridge/NxMcpGuiBridge.cs").read_text(encoding="utf-8")
    error = source[
        source.index("    internal sealed class NxToolError") : source.index(
            "    internal sealed class WorkspaceViolation"
        )
    ]
    handlers = source[
        source.index("        private Dictionary<string, object> SavePart(") : source.index(
            "        private Dictionary<string, object> ExportStep("
        )
    ]
    directory = tmp_path_factory.mktemp("gui-save-build")
    extracted = directory / "Production.cs"
    extracted.write_text(
        "using System;\nusing System.Collections.Generic;\n"
        "namespace NxMcp.GuiBridge {\n"
        + error
        + "internal sealed partial class Executor {\n"
        + handlers
        + "}\n}\n",
        encoding="utf-8",
    )
    exe = directory / "SaveTests.exe"
    result = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/utf8output",
            "/langversion:5",
            "/target:exe",
            f"/out:{exe}",
            str(extracted),
            str(ROOT / "tests/gui_bridge_save_harness.cs"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return exe


@pytest.mark.parametrize("operation", ["save", "close", "discard"])
@pytest.mark.parametrize("unsaved_parts,unsaved_objects", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_save_status_controls_close_and_preserves_undo(
    save_harness, operation, unsaved_parts, unsaved_objects
):
    result = subprocess.run(
        [str(save_harness), operation, str(unsaved_parts), str(unsaved_objects)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
