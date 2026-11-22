#!/usr/bin/env python3
"""Emit JSONL evidence for repeated POSIX SQLite fault schedules."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pieq.fault_worker import SCHEDULES, run_schedule


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261117)
    parser.add_argument("--schedule", choices=SCHEDULES, action="append")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    output = args.output.open("w") if args.output else sys.stdout
    try:
        for schedule in args.schedule or SCHEDULES:
            for repetition in range(args.repetitions):
                result = run_schedule(schedule, args.seed + repetition)
                result["repetition"] = repetition
                output.write(json.dumps(result, sort_keys=True) + "\n")
                output.flush()
                if not result["checks"]["all_passed"]:
                    raise SystemExit(f"invariant failure in {schedule}, repetition {repetition}")
    finally:
        if args.output:
            output.close()


if __name__ == "__main__":
    main()
