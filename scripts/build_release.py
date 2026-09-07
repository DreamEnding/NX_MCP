"""Build an offline Windows/Python 3.12 release from a clean committed checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if command("git", "status", "--porcelain", cwd=repo):
        raise SystemExit("Commit or isolate changes before building a release")
    commit = command("git", "rev-parse", "HEAD", cwd=repo)
    epoch = command("git", "show", "-s", "--format=%ct", "HEAD", cwd=repo)
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nx-release-") as temporary:
        root = Path(temporary)
        archive = root / "source.zip"
        subprocess.run(
            ["git", "archive", "--format=zip", "-o", str(archive), commit], cwd=repo, check=True
        )
        source = root / "source"
        with zipfile.ZipFile(archive) as z:
            z.extractall(source)
        # The package version is a simple literal; avoids a tomllib dependency on Python 3.10.
        namespace = {}
        exec((source / "src/nx_mcp/__init__.py").read_text(), namespace)
        version = namespace["__version__"]
        bundle = root / f"nx-mcp-{version}-windows-py312"
        bundle.mkdir()
        shutil.copytree(source, bundle / "source")
        wheels = bundle / "wheels"
        wheels.mkdir()
        env = dict(os.environ, SOURCE_DATE_EPOCH=epoch)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(wheels),
                str(source),
            ],
            env=env,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--require-hashes",
                "--only-binary=:all:",
                "--platform",
                "win_amd64",
                "--python-version",
                "312",
                "--implementation",
                "cp",
                "--dest",
                str(wheels),
                "-r",
                str(source / "requirements-windows.lock"),
            ],
            check=True,
        )
        shutil.copy2(source / "requirements-windows.lock", bundle)
        for name in ("install_release.ps1", "restore_release.ps1"):
            shutil.copy2(source / "scripts" / name, bundle)
        metadata = {
            "version": version,
            "commit": commit,
            "source_date_epoch": int(epoch),
            "python": "3.12",
            "platform": "win_amd64",
            "upstream": "https://github.com/DreamEnding/NX_MCP",
            "fork": "https://github.com/xuio/NX_MCP",
        }
        (bundle / "release.json").write_text(json.dumps(metadata, indent=2) + "\n")
        files = {
            p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(bundle.rglob("*"))
            if p.is_file()
        }
        (bundle / "manifest.json").write_text(json.dumps(files, indent=2) + "\n")
        output = destination / (bundle.name + ".zip")
        # Fixed ZIP timestamps and sorted entries make archive metadata reproducible.
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(bundle.rglob("*")):
                if p.is_file():
                    info = zipfile.ZipInfo(p.relative_to(bundle).as_posix(), (2020, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    z.writestr(info, p.read_bytes())
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n")
        print(
            json.dumps({**metadata, "archive": str(output), "sha256": digest, "files": len(files)})
        )


if __name__ == "__main__":
    main()
