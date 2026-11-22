#!/usr/bin/env python3
"""
EXP-07A — Causal-ordering audit of the Revision 10 physical promotion trials (f1_p4_*).

Question: did Patroni promote a standby BEFORE the PIEQ-authorized REST dispatch?
Method: reconstruct a per-trial event timeline from (a) the trial JSON (relative stage
durations) and (b) the independent observer JSONL (absolute wall-clock samples of
pg_is_in_recovery / writable primaries on every node at ~1 Hz).

Clock sources: ALL events share one host wall clock (the harness process and observer
thread run on the same macOS host; containers are only observed via docker exec from
that host), so cross-process skew is bounded by process scheduling (< ~1 s). The fault
instant is bounded between the last observer sample showing the old leader writable
and the first sample showing it unreachable (±1 observer interval). The earliest
possible dispatch instant is fault_time + 35 s (fixed lease-lapse wait) + LLM inference
duration (recorded per trial). CAUSAL ATTRIBUTION HOLDS if, in the window
[fault_detected, earliest_dispatch], the observer shows NO writable primary other than
the (dead) old leader — i.e., Patroni (paused) did not promote before PIEQ dispatched.

Precision disclosure: 1 Hz sampling bounds each transition to ±1 s and cannot exclude
sub-second intervals; the paused-mode design makes pre-dispatch auto-promotion
impossible by configuration, and this audit verifies the observable record is
consistent with that.
"""
from __future__ import annotations

import glob
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def parse_t(x: str) -> float:
    return datetime.fromisoformat(x).timestamp()


def audit(trial_json: Path) -> dict:
    d = json.loads(trial_json.read_text())
    obs_file = HERE / "rev10_logs" / d["observer_file"]
    samples = [json.loads(l) for l in obs_file.open()]
    old = d["initial_leader"]
    new = d.get("new_leader")
    t_first = parse_t(samples[0]["t_utc"])

    def leader_writable(s, node):
        v = s["nodes"].get(node, {})
        return v.get("reachable") and v.get("is_in_recovery") is False

    last_old_writable = next((parse_t(s["t_utc"]) for s in reversed(samples)
                              if leader_writable(s, old)), None)
    first_old_gone = next((parse_t(s["t_utc"]) for s in samples
                           if not s["nodes"].get(old, {}).get("reachable", False)), None)
    first_new_writable = next((parse_t(s["t_utc"]) for s in samples
                               if new and leader_writable(s, new)), None)
    llm_ms = (d.get("llm") or {}).get("elapsed_ms") or 0.0
    # fault occurs just after trial start (pause + docker stop); bound by observer:
    fault_lo = (last_old_writable - t_first) if last_old_writable else None
    fault_hi = (first_old_gone - t_first) if first_old_gone else None
    earliest_dispatch = (fault_hi if fault_hi is not None else 0) + 35.0 + llm_ms / 1000.0
    # any foreign writable primary BEFORE earliest dispatch?
    pre_dispatch_promotion = False
    for s in samples:
        t = parse_t(s["t_utc"]) - t_first
        if t >= earliest_dispatch:
            break
        prim = [p for p in s["writable_primaries"] if p != old]
        if prim:
            pre_dispatch_promotion = True
    return {
        "trial": trial_json.name,
        "old_leader": old,
        "new_leader": new,
        "clock_sources": "single host wall clock (harness + observer); sampling 1 Hz (+/-1 s)",
        "fault_window_s_rel": [round(fault_lo, 1) if fault_lo is not None else None,
                               round(fault_hi, 1) if fault_hi is not None else None],
        "llm_inference_s": round(llm_ms / 1000.0, 1),
        "earliest_possible_dispatch_s_rel": round(earliest_dispatch, 1),
        "first_new_primary_observed_s_rel": (round(first_new_writable - t_first, 1)
                                             if first_new_writable else None),
        "writable_primary_before_earliest_dispatch": pre_dispatch_promotion,
        "pieq_decision_admitted": (d.get("pieq_decision") or {}).get("admitted"),
        "dispatch_response": (d.get("dispatch") or {}).get("response"),
        "dual_primary_intervals": d.get("dual_primary_intervals_observed"),
        "causal_attribution": (
            "PIEQ-CAUSED (paused mode; no writable primary existed before earliest "
            "possible dispatch; new primary appears only after)"
            if (not pre_dispatch_promotion and first_new_writable
                and first_new_writable - t_first > earliest_dispatch - 1.0)
            else "CAUSALLY UNRESOLVED from observable record"
        ),
    }


def main() -> None:
    out = [audit(Path(p)) for p in sorted(glob.glob(str(HERE / "rev10_logs" / "f1_p4_*.json")))]
    dest = HERE.parents[1] / "results" / "rev11" / "exp07" / "exp07a_causality_audit.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    print("Wrote", dest)


if __name__ == "__main__":
    main()
