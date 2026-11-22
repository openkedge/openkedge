#!/usr/bin/env python3
"""Run a frozen live evaluation or replay saved replies without provider calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pieq.live_cases import build_live_cases
from pieq.live_protocol import canonical, mock_manifest, replay_decisions, run_evaluation, validate_manifest
from pieq.live_providers import make_provider


def _saved_manifest(path):
    try:
        with path.open() as stream:
            header = json.loads(stream.readline())
        if header["record_type"] != "manifest":
            raise ValueError("Trace lacks manifest header")
        return header["manifest"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("Trace lacks a valid manifest header") from error


def _mock_only(manifest):
    return all(item["provider"] == "mock" for item in manifest["instances"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--mock-pilot", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--preflight", action="store_true",
                        help="Check manifest, credentials, and endpoint safety without making calls or writing a trace")
    parser.add_argument("--trace", type=Path)
    args = parser.parse_args()
    if args.mock_pilot and args.manifest:
        parser.error("--mock-pilot cannot be combined with --manifest")
    if args.preflight:
        if args.mock_pilot or args.replay or args.manifest is None:
            parser.error("--preflight requires --manifest and cannot be combined with --mock-pilot or --replay")
        try:
            manifest = json.loads(args.manifest.read_text())
            validate_manifest(manifest)
            if _mock_only(manifest):
                raise ValueError("Mock-only manifest requires --mock-pilot")
            endpoints = {
                name: make_provider(name).preflight()
                for name in dict.fromkeys(item["provider"] for item in manifest["instances"])
                if name != "mock"
            }
        except (OSError, ValueError, KeyError) as error:
            parser.error(str(error))
        print(canonical({
            "run_id": manifest["run_id"],
            "run_kind": manifest["run_kind"],
            "logical_requests": len(build_live_cases()) * len(manifest["instances"]) * 4,
            "max_calls": manifest["max_calls"],
            "spending_cap_usd": manifest["spending_cap_usd"],
            "max_cost_per_call_usd": manifest["max_cost_per_call_usd"],
            "provider_endpoints": endpoints,
            "model_ids": {item["agent_id"]: item["model"] for item in manifest["instances"]},
        }))
        return
    if not args.mock_pilot and not args.manifest and not args.replay:
        parser.error("Supply --mock-pilot, --manifest, or --replay")
    if args.trace is None:
        args.trace = Path("results/live_mock_traces.jsonl" if args.mock_pilot
                          else "results/live_llm_traces.jsonl")
    cases = build_live_cases()[:3] if args.mock_pilot else build_live_cases()
    if args.replay:
        try:
            saved = _saved_manifest(args.trace)
            validate_manifest(saved)
        except ValueError as error:
            parser.error(str(error))
        is_mock = saved["run_kind"] == "single_model_control" and _mock_only(saved)
        if args.mock_pilot != is_mock:
            parser.error("mock-only replay requires --mock-pilot; --mock-pilot requires a mock-only saved trace")
        decisions = replay_decisions(cases, args.trace)
    else:
        manifest = json.loads(args.manifest.read_text()) if args.manifest else mock_manifest()
        validate_manifest(manifest)
        if not args.mock_pilot and _mock_only(manifest):
            parser.error("mock-only manifest requires --mock-pilot and the three-case pilot path")
        decisions = run_evaluation(cases, manifest, args.trace)
    for decision in decisions:
        print(canonical(decision))
    trace_hash = hashlib.sha256(args.trace.read_bytes()).hexdigest()
    print(canonical({"trace_sha256": trace_hash, "cases": len(cases),
                     "result_kind": "mock_software_pilot" if args.mock_pilot else "attempted_live_run"}),
          file=sys.stderr)


if __name__ == "__main__":
    main()
