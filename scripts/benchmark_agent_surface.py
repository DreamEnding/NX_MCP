"""Tokenize paired NX response transcripts; never equate tokenizer counts with billed usage."""

import argparse
import hashlib
import json
from pathlib import Path

from nx_mcp.agent_surface import compact_payload


def benchmark(records, encoding):
    def tokens(value):
        return len(
            encoding.encode(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")), disallowed_special=()
            )
        )

    full = reduced = calls = failures = binary_calls = 0
    for row in records:
        if row.get("state") != "response":
            continue
        calls += 1
        failures += bool(row.get("error"))
        value = row["result"]
        full += tokens(value)
        result_id = "result_" + hashlib.sha256(str(calls).encode()).hexdigest()[:32]
        payload = compact_payload(value, result_id)
        reduced += tokens(payload)
        binary_calls += "data_base64" in value
    return {
        "scope": "paired recorded response projection; same completed native tasks, not an autonomous-agent A/B trial",
        "tokenizer": encoding.name,
        "serialization": "compact JSON structuredContent counted once; excludes protocol, duplicate text, image tokens and initial discovery",
        "full_response_tokens": full,
        "compact_response_tokens": reduced,
        "response_token_reduction_percent": round(100 * (1 - reduced / full), 2) if full else 0,
        "recorded_calls": calls,
        "recorded_errors": failures,
        "binary_transfer_calls": binary_calls,
        "model_calls": 0,
        "provider_input_tokens": "unavailable",
        "provider_cached_input_tokens": "unavailable",
        "provider_output_tokens": "unavailable",
        "autonomous_task_success": "not measured; use usage observations from actual agent runs",
        "extra_discovery_and_expansion_calls": "not measured by replay",
    }


def main():
    import tiktoken

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoding", default="o200k_base")
    parser.add_argument(
        "--usage",
        type=Path,
        help="Optional observed provider usage JSON; retained separately, never inferred",
    )
    args = parser.parse_args()
    result = benchmark(
        (json.loads(line) for line in args.transcript.read_text().splitlines()),
        tiktoken.get_encoding(args.encoding),
    )
    if args.usage:
        result["observed_agent_usage"] = json.loads(args.usage.read_text())
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
