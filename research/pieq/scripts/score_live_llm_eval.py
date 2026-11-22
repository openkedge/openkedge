#!/usr/bin/env python3
"""Verify frozen fixtures and trace, then score saved proposals without API calls."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pieq.live_cases import build_live_cases
from pieq.live_score import score_trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-manifest", type=Path,
                        default=Path("results/live_cases_manifest.json"))
    parser.add_argument("--trace", type=Path, default=Path("results/live_llm_traces.jsonl"))
    parser.add_argument("--output", type=Path, help="Required for mock traces")
    parser.add_argument("--expected-trace-sha256")
    parser.add_argument("--bootstrap-replicates", type=int, default=1000)
    args = parser.parse_args()
    if args.bootstrap_replicates < 0:
        parser.error("--bootstrap-replicates must be nonnegative")
    try:
        trace_records = [json.loads(line) for line in args.trace.read_text().splitlines()]
        trace_cases = {record["case_id"] for record in trace_records
                       if record.get("record_type") == "request"}
        cases = [case for case in build_live_cases() if case.case_id in trace_cases]
        result = score_trace(args.case_manifest, args.trace, cases=cases,
                             bootstrap_replicates=args.bootstrap_replicates,
                             expected_trace_sha256=args.expected_trace_sha256)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Replay verification failed: {error}\n")
    if result["evidence_kind"] == "mock_software_pilot" and args.output is None:
        parser.error("Mock traces require an explicit --output; the default is reserved for live traces")
    output = args.output or Path("results/live_llm_summary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                         prefix=".live-summary-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print(json.dumps({"output": str(output), "trace_sha256": result["hashes"]["trace_sha256"],
                      "evidence_kind": result["evidence_kind"], "case_count": result["case_count"]},
                     sort_keys=True))


if __name__ == "__main__":
    main()
