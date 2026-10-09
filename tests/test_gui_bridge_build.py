"""Exercise the build/sign boundary with the real compiler and a fake NX SDK/signer."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = (
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
)
PWSH = shutil.which("pwsh")
pytestmark = [
    pytest.mark.fake_nx,
    pytest.mark.skipif(
        os.name != "nt" or not COMPILER.is_file() or not PWSH,
        reason="requires Windows, PowerShell 7 and the in-box C# compiler",
    ),
]


@pytest.fixture(scope="session")
def fake_signing_sdk(tmp_path_factory):
    sdk = tmp_path_factory.mktemp("signing-sdk")
    managed = sdk / "NXBIN/managed"
    managed.mkdir(parents=True)
    stub = sdk / "stub.cs"
    stub.write_text("// Empty test assembly.\n", encoding="utf-8")
    for assembly in ("NXOpen.dll", "NXOpen.UF.dll", "NXOpenUI.dll", "NXOpen.Utilities.dll"):
        subprocess.run(
            [str(COMPILER), "/nologo", "/target:library", f"/out:{managed / assembly}", str(stub)],
            check=True,
            capture_output=True,
            timeout=30,
        )
    resource = sdk / "UGOPEN/NXSigningResource.res"
    resource.parent.mkdir()
    resource.write_bytes(b"Test fixture only: not a Siemens signing resource.")
    signer = sdk / "signer.cs"
    signer.write_text(
        """using System;
using System.IO;
using System.Reflection;
public static class FakeSigner {
    public static int Main(string[] args) {
        string stage = args[0] == "-verify" ? "verify" : "sign";
        File.AppendAllText(Environment.GetEnvironmentVariable("NX_MCP_TEST_SIGN_LOG"), stage + "\\n");
        if (Environment.GetEnvironmentVariable("NX_MCP_TEST_SIGN_FAIL_STAGE") == stage) return 7;
        Assembly assembly = Assembly.ReflectionOnlyLoadFrom(args[args.Length - 1]);
        return Array.IndexOf(assembly.GetManifestResourceNames(), "NXSigningResource.res") < 0 ? 8 : 0;
    }
}
""",
        encoding="utf-8",
    )
    subprocess.run(
        [str(COMPILER), "/nologo", f"/out:{sdk / 'NXBIN/SignDotNet.exe'}", str(signer)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return sdk


@pytest.fixture
def build_project(tmp_path, fake_signing_sdk, monkeypatch):
    project = tmp_path / "build project"
    project.mkdir()
    shutil.copy2(ROOT / "nx_gui_bridge/build.ps1", project / "build.ps1")
    (project / "NxMcpGuiBridge.cs").write_text(
        "public static class NxMcpGuiBridge {}\n", encoding="utf-8"
    )
    shutil.copytree(fake_signing_sdk, project / "NX installation")
    monkeypatch.setenv("NX_MCP_TEST_SIGN_LOG", str(project / "sign.log"))
    monkeypatch.delenv("NX_MCP_TEST_SIGN_FAIL_STAGE", raising=False)
    return project


def run_build(project, *, sign=False):
    return subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(project / "build.ps1"),
            "-NxRoot",
            str(project / "NX installation"),
            "-OutputDirectory",
            str(project / "output"),
            *(["-Sign"] if sign else []),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_signed_build_embeds_resource_and_verifies_signature(build_project):
    result = run_build(build_project, sign=True)
    assert result.returncode == 0, result.stdout + result.stderr
    dll = build_project / "output/NxMcpGuiBridge.dll"
    assert dll.is_file()
    assert result.stdout.strip() == str(dll)
    assert (build_project / "sign.log").read_text().splitlines() == ["sign", "verify"]


@pytest.mark.parametrize("missing", ["UGOPEN/NXSigningResource.res", "NXBIN/SignDotNet.exe"])
def test_missing_signing_tools_fail_before_build(build_project, missing):
    (build_project / "NX installation" / missing).unlink()
    result = run_build(build_project, sign=True)
    assert result.returncode != 0
    assert Path(missing).name in result.stderr
    assert not (build_project / "output").exists()
    assert not (build_project / "sign.log").exists()


@pytest.mark.parametrize("stage", ["sign", "verify"])
def test_signing_or_verification_failure_is_not_reported_as_success(
    build_project, monkeypatch, stage
):
    monkeypatch.setenv("NX_MCP_TEST_SIGN_FAIL_STAGE", stage)
    result = run_build(build_project, sign=True)
    assert result.returncode != 0
    assert "exit code 7" in result.stderr
    assert not result.stdout.strip()
    expected = ["sign"] if stage == "sign" else ["sign", "verify"]
    assert (build_project / "sign.log").read_text().splitlines() == expected


def test_compilation_failure_does_not_invoke_signer(build_project):
    (build_project / "NxMcpGuiBridge.cs").write_text("invalid C#;\n", encoding="utf-8")
    result = run_build(build_project, sign=True)
    assert result.returncode != 0
    assert "csc.exe failed" in result.stderr
    assert not (build_project / "sign.log").exists()


def test_unsigned_build_needs_no_signing_tools(build_project):
    (build_project / "NX installation/UGOPEN/NXSigningResource.res").unlink()
    (build_project / "NX installation/NXBIN/SignDotNet.exe").unlink()
    result = run_build(build_project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (build_project / "output/NxMcpGuiBridge.dll").is_file()
    assert not (build_project / "sign.log").exists()
