"""Render evidence-scoped capability documentation without importing NX or the server."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("Native-tested", "Contract/sidecar-tested", "Experimental", "Unavailable")


def classification(entry: dict) -> str:
    """Fail closed: a tested status alone never establishes native evidence."""
    status = entry.get("status")
    evidence = entry.get("evidence_type", "")
    if status == "unavailable":
        return "Unavailable"
    if status != "tested":
        return "Experimental"
    if isinstance(evidence, str) and evidence.startswith("real_NX_"):
        return "Native-tested"
    if evidence in {
        "local_contract_test",
        "local_contract_tests",
        "live_sidecar_and_contract_tests",
    }:
        return "Contract/sidecar-tested"
    return "Experimental"


def cell(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
    )


def render(manifest: dict) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    tools = manifest["tools"]
    counts = Counter(classification(entry) for entry in tools.values())
    lines = [
        "# Capability evidence matrix",
        "",
        "Generated from `src/nx_mcp/capability_manifest.json`; do not edit this table by hand.",
        "Run `python scripts/generate_capability_matrix.py` to regenerate, or add `--check` to detect drift.",
        "",
        f"Manifest revision: **{cell(manifest['revision'])}**. NX: **{cell(manifest['nx_version'])}**. "
        f"Bridge protocol: **{cell(manifest['bridge_protocol'])}**.",
        f"Canonical manifest SHA-256: `{hashlib.sha256(canonical).hexdigest()}`.",
        "",
        "These labels report manifest evidence, not certification or independent verification of its claims. "
        "Native-tested means status `tested` with an evidence type beginning `real_NX_`; "
        "only the stated scope and NX version are covered. Contract/sidecar-tested does not establish "
        "native CAD correctness. Experimental includes untested entries and tested entries without a "
        "recognized evidence type. Unavailable capabilities are explicitly recorded by the manifest; "
        "absence from this matrix is not proof of availability or unavailability.",
        "",
        "| Tool classification | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {group} | {counts[group]} |" for group in GROUPS)
    lines.extend(
        [
            "",
            "## Tools",
            "",
            "| Tool | Classification | Manifest status | Evidence type | Tested scope / caveat |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for name, entry in sorted(tools.items()):
        values = [
            name,
            classification(entry),
            entry.get("status", "missing"),
            entry.get("evidence_type", "not recorded"),
            entry.get("scope", "not recorded"),
        ]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines.extend(
        [
            "",
            "## Explicitly unavailable capabilities",
            "",
            "These are capability names, separate from the tool counts above.",
            "",
        ]
    )
    lines.extend(f"- {cell(name)}" for name in sorted(manifest.get("unavailable", [])))
    if not manifest.get("unavailable"):
        lines.append("None recorded.")
    lines.extend(["", "## Manifest limitations", ""])
    lines.extend(f"- {cell(value)}" for value in manifest.get("limitations", []))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "src/nx_mcp/capability_manifest.json"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "docs/capability-matrix.md")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = render(json.loads(args.manifest.read_text(encoding="utf-8")))
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != result:
            print(f"Capability matrix is stale: {args.output}; regenerate with this script.")
            return 1
        print("Capability matrix matches manifest.")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
