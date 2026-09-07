"""Verify an installed offline release and run serial native acceptance on its NX host.

Never installs, stops, restarts, saves or restores user parts itself. Native suites
create isolated test parts and must restore the original saved session.
"""

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
import re
import runpy
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def absolute_path(path):
    """Normalize host paths without querying unsupported shared-drive reparse APIs.

    These are operator-selected paths, not a containment/security boundary. File
    existence and trusted-package byte checks happen separately. In particular,
    Path.resolve() can fail with WinError 1005 on VirtIO shares, even for a valid
    path or a new output directory whose parents exist.
    """
    return Path(os.path.abspath(os.fspath(path)))


def write_receipt(path, report):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise RuntimeError(f"Unsafe package path: {name}")
    return path


def verify_package(archive, expected_hash, expected_commit, install_root, runtime_root):
    """Compare trusted ZIP bytes with installed source and importable module files."""
    raw = archive.read_bytes()
    if digest(raw) != expected_hash.lower():
        raise RuntimeError("Release ZIP SHA-256 differs from the expected value")
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        names = [i.filename for i in bundle.infolist() if not i.is_dir()]
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate ZIP members")
        for name in names:
            safe_name(name)
        manifest = json.loads(bundle.read("manifest.json"))
        if set(names) != set(manifest) | {"manifest.json"}:
            raise RuntimeError("Package manifest does not cover exactly the ZIP files")
        for name, expected in manifest.items():
            safe_name(name)
            if digest(bundle.read(name)) != expected:
                raise RuntimeError(f"Package checksum mismatch: {name}")
        release = json.loads(bundle.read("release.json"))
        if release["commit"] != expected_commit:
            raise RuntimeError("Package commit differs from the expected full commit")
        installed = json.loads((install_root / "release.json").read_text(encoding="utf-8-sig"))
        if installed != release:
            raise RuntimeError("Installed release metadata differs from the package")
        if (install_root / "validation-release.json").exists():
            raise RuntimeError("Unexpected validation overlay; use the consolidated package")
        checked_source = 0
        for name, expected in manifest.items():
            if name.startswith("source/"):
                actual = install_root.joinpath(*safe_name(name).parts)
                if not actual.is_file() or digest(actual.read_bytes()) != expected:
                    raise RuntimeError(f"Installed source mismatch: {name}")
                checked_source += 1
        wheel_name = f"wheels/nx_mcp-{release['version']}-py3-none-any.whl"
        with zipfile.ZipFile(io.BytesIO(bundle.read(wheel_name))) as wheel:
            runtime_files = {
                name.removeprefix("nx_mcp/"): wheel.read(name)
                for name in wheel.namelist()
                if name.startswith("nx_mcp/") and not name.endswith("/")
            }
        if not checked_source or not runtime_files:
            raise RuntimeError("Package lacks source or runtime module files")
        for name, expected in runtime_files.items():
            actual = runtime_root.joinpath(*safe_name(name).parts)
            if not actual.is_file() or actual.read_bytes() != expected:
                raise RuntimeError(f"Importable runtime differs from wheel: {name}")
        # Extra executable/configuration files can shadow the verified release.
        for root, expected_names in (
            (
                install_root / "source",
                {n.removeprefix("source/") for n in manifest if n.startswith("source/")},
            ),
            (runtime_root, set(runtime_files)),
        ):
            extras = [
                p.relative_to(root).as_posix()
                for p in root.rglob("*")
                if p.is_file()
                and p.suffix in {".py", ".json", ".pyd"}
                and "__pycache__" not in p.parts
                and p.relative_to(root).as_posix() not in expected_names
            ]
            if extras:
                raise RuntimeError(f"Unpackaged source/runtime files: {extras}")
        return {
            "release": release,
            "source_files_verified": checked_source,
            "runtime_files_verified": len(runtime_files),
            "runtime_path": str(runtime_root),
            "loaded_process_bytes_attested": False,
        }


def prepare_report(path, identity, resume):
    if path.exists():
        if not resume:
            raise RuntimeError("Acceptance receipt exists; use --resume or a new output directory")
        report = json.loads(path.read_text())
        if report["identity"] != identity:
            raise RuntimeError("Resume inputs differ from the original acceptance run")
        # A child could have mutated NX before it died. Never infer failure from
        # a missing receipt, or automatically repeat failed native operations.
        native = report["phases"].get("native", {})
        if native.get("state") in {"running", "failed"}:
            raise RuntimeError(
                "Native phase incomplete/failed: inspect receipts and NX session; no automatic replay"
            )
        if native.get("state") == "passed" and report.get("state") != "passed":
            raise RuntimeError(
                "Native phase finished but acceptance incomplete; reconcile session evidence before a new run"
            )
        return report
    if resume:
        raise RuntimeError("No acceptance receipt to resume")
    return {"identity": identity, "started": now(), "state": "pending", "phases": {}}


def phase(report, receipt, name, action):
    report["phases"][name] = {"state": "running", "started": now()}
    write_receipt(receipt, report)
    try:
        result = action()
    except BaseException as error:
        report["phases"][name].update(state="failed", error=str(error), finished=now())
        report.update(state="failed", finished=now())
        write_receipt(receipt, report)
        raise
    report["phases"][name].update(state="passed", result=result, finished=now())
    write_receipt(receipt, report)
    return result


def run_native(source, output, tool_count):
    log = output / "native.log"
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            [
                sys.executable,
                str(source / "scripts/validate_native_release.py"),
                "--output",
                str(output / "native"),
                "--expected-tool-count",
                str(tool_count),
            ],
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    path = output / "native/release-validation.json"
    if result.returncode:
        raise RuntimeError(f"Native runner exit {result.returncode}; inspect {log} and {path}")
    native = json.loads(path.read_text())
    if not native.get("passed") or not native.get("session_restored"):
        raise RuntimeError("Native runner lacks passed/session_restored evidence")
    return {
        "receipt": str(path),
        "sha256": digest(path.read_bytes()),
        "log": str(log),
        "suites": native["suites"],
        "session_restored": True,
    }


def verify_dependencies(lock, version=importlib.metadata.version):
    versions = {}
    for line in lock.read_text().splitlines():
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([\w.-]+)==([^\s;]+)\s*(?:\\)?", line)
        if not match:
            raise RuntimeError(f"Unsupported dependency lock entry: {line}")
        name, expected = match.groups()
        actual = version(name)
        if actual != expected:
            raise RuntimeError(
                f"Installed dependency mismatch: {name} expected {expected}, got {actual}"
            )
        versions[name] = actual
    if not versions:
        raise RuntimeError("Dependency lock contains no pinned versions")
    return versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-zip", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-tool-count", type=int, default=189)
    parser.add_argument("--expected-nx-version", default="v2606")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("NX_MCP_URL", "")
    parsed = urlparse(url)
    if (
        parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
    ):
        parser.error(
            "NX_MCP_URL must address this Windows NX host over loopback, without credentials"
        )
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
        parser.error(
            "Run on the NX Windows host with its installed Python 3.12 virtual environment"
        )
    args.output = absolute_path(args.output)
    args.install_root = absolute_path(args.install_root)
    args.release_zip = absolute_path(args.release_zip)
    fixture = absolute_path(os.environ.get("NX_VENDOR_STEP", ""))
    if not fixture.is_file():
        parser.error("NX_VENDOR_STEP must identify an authorized local STEP fixture")
    identity = {
        "release_zip": str(args.release_zip),
        "sha256": args.sha256.lower(),
        "expected_commit": args.expected_commit,
        "install_root": str(args.install_root),
        "endpoint": url,
        "python": sys.executable,
        "vendor_step": str(fixture),
        "vendor_step_sha256": digest(fixture.read_bytes()),
        "expected_tool_count": args.expected_tool_count,
        "expected_nx_version": args.expected_nx_version,
    }
    receipt = args.output / "acceptance.json"
    if args.output.exists() and not receipt.exists():
        parser.error("Use a new output directory; existing artifacts have no acceptance receipt")
    lock = args.install_root / "acceptance.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise RuntimeError(
            "Acceptance lock exists; inspect the prior process/receipt before removing it"
        ) from error
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"pid": os.getpid(), "receipt": str(receipt), "started": now()}, stream)
        report = prepare_report(receipt, identity, args.resume)
        if report["state"] == "passed":
            print(
                json.dumps({"state": "passed", "historical_receipt": str(receipt), "rerun": False})
            )
            return
        args.output.mkdir(parents=True, exist_ok=True)
        write_receipt(receipt, report)
        execute(args, receipt, report)
    finally:
        lock.unlink()


def execute(args, receipt, report):
    def package():
        spec = importlib.util.find_spec("nx_mcp")
        runtime = absolute_path(Path(spec.origin).parent)
        # A source checkout in PYTHONPATH is not the installed wheel environment.
        if not runtime.is_relative_to(args.install_root / "venv"):
            raise RuntimeError("nx_mcp must import from the selected installation's venv")
        return verify_package(
            args.release_zip, args.sha256, args.expected_commit, args.install_root, runtime
        )

    phase(report, receipt, "package_and_installed_files", package)

    def dependencies():
        versions = verify_dependencies(args.install_root / "source/requirements-windows.lock")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "check"], capture_output=True, text=True
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return {
            "python_version": sys.version,
            "pip_check": result.stdout.strip(),
            "locked_versions": versions,
        }

    phase(report, receipt, "dependencies", dependencies)
    source = args.install_root / "source"
    runner = runpy.run_path(str(source / "scripts/validate_native_release.py"))

    def live_preflight():
        before = asyncio.run(runner["snapshot"]())
        if (
            before["tool_count"] != args.expected_tool_count
            or before["nx_version"] != args.expected_nx_version
        ):
            raise RuntimeError("Live NX version/tool count differs from expected runtime")
        return before

    before = phase(report, receipt, "live_saved_session", live_preflight)
    if args.verify_only:
        report.update(state="verified", finished=now())
        write_receipt(receipt, report)
        print(json.dumps({"state": "verified", "native_run": False, "receipt": str(receipt)}))
        return
    try:
        phase(
            report,
            receipt,
            "native",
            lambda: run_native(source, args.output, args.expected_tool_count),
        )
    finally:

        def preservation():
            after = asyncio.run(runner["snapshot"]())
            runner["verify_session"](before, after)
            return {"session_restored": True, "after": after}

        phase(report, receipt, "session_preservation", preservation)
    report.update(state="passed", finished=now())
    write_receipt(receipt, report)
    print(json.dumps({"state": "passed", "receipt": str(receipt)}))


if __name__ == "__main__":
    main()
