#!/usr/bin/env python3
"""
Reproducible evaluation runner for Provenance-Isolated Epistemic Quorums (PIEQ).
Generates `results/benchmark_summary.json` containing:
  - Main policy comparisons with both N_eff and r_PR, 95% Clopper-Pearson CIs,
    exact 2x2 paired contingency tables (n11, n10, n01, n00), paired McNemar p-values,
    explicit scenario IDs of unsafe outcomes, and liveness/operational metrics.
  - All 7 component ablations (A1-A7) + 6 formal boundary ablations (B0-B4):
      B0: Menger Path Flow Only vs. Complete-Justification Set Packing (Theorem 3)
      B1: Pairwise Disjointness Gate vs. Global Plausible-World Intersection (Theorem 4)
      B2a: Proxy-Only Check (Tier E0) vs. Shared-State Atomic Fencing (Tier E2)
      B2b: Asynchronous Push Fence (Tier E1) under Delayed Fence Messages
      B3: Single-Sensor Belief Pruning (f=0) vs. Fault-Tolerant Belief Shield hat{B}_tau^{(2)} (f=2)
      B4: Single-Action Shield vs. Compound Concurrent Exposure Gate (Theorem 5)
  - Both Negative Controls (NC1: common-mode model error; NC2: unidentifiable without probe).
  - Empirical 1,000-trace stage and end-to-end latency quantiles (including SQLite WAL atomic fencing).
"""

from __future__ import annotations

import json
import platform
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pieq.benchmark import (
    build_benchmark_scenarios,
    evaluate_policy_on_benchmark,
    paired_contingency_and_mcnemar,
    profile_proxy_admission_traces,
)


def main() -> None:
    scenarios = build_benchmark_scenarios()

    policy_ids = [
        "free_debate_overlap",
        "blind_vote_overlap",
        "free_debate_ortho",
        "atms_nogood_only",
        "atms_pce_pillar1",
        "full_pieq",
        # Component Ablations A1-A7
        "single_strong_agent",
        "ortho_no_debate",
        "atms_no_cut",
        "cut_no_atms",
        "probing_no_atms",
        "full_correlated_faults",
        # Formal Boundary Ablations B0-B4
        "menger_flow_only_no_packing",
        "pairwise_gate_only",
        "proxy_only_no_target_fence",
        "async_push_fence_tier_e1",
        "naive_belief_pruning_f0",
        "no_concurrent_exposure_gate",
        # Negative Controls NC1-NC2
        "nc1_common_mode_model_error",
        "nc2_unidentifiable_no_probe",
    ]

    results = {}
    for pid in policy_ids:
        results[pid] = evaluate_policy_on_benchmark(pid, scenarios)

    full_flags = results["full_pieq"].scenario_unsafe_flags
    paired_contingency = {}
    for pid, res in results.items():
        paired_contingency[pid] = paired_contingency_and_mcnemar(res.scenario_unsafe_flags, full_flags)

    latency_traces = profile_proxy_admission_traces(n_traces=1000, seed=42)

    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_payload = {
        "benchmark_scenarios": len(scenarios),
        "simulator_profile": (
            "Synthetic 100-scenario protocol simulation (SVD-whitened Gaussian agent latents, "
            "seed=202611) + executable pieq/core.py verification + on-disk SQLite WAL target boundary"
        ),
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "sqlite_version": sqlite3.sqlite_version,
        },
        "policies": {
            pid: {
                **{k: v for k, v in asdict(res).items() if k != "scenario_unsafe_flags"},
                "unsafe_scenario_ids": [i for i, flag in enumerate(res.scenario_unsafe_flags) if flag == 1],
                "paired_vs_full_pieq": paired_contingency[pid],
            }
            for pid, res in results.items()
        },
        "latency_trace_quantiles_ms": latency_traces,
    }
    out_path = out_dir / "benchmark_summary.json"
    out_path.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")

    print("=== Main Policies & Ablations (100 Cloud Incident Scenarios) ===")
    header = (
        f"{'Policy':<30} {'rho3%':>5} {'N_eff':>5} {'r_PR':>5} "
        f"{'Unsafe% [95% CI]':>19} {'(n11,n10,n01,n00)':>18} {'Corr%':>6} {'Def%':>5} {'McNemar p':>10}"
    )
    print(header)
    print("-" * len(header))
    for pid in policy_ids:
        r = results[pid]
        ct = paired_contingency[pid]
        ci_str = f"{r.unsafe_mutation_rate:4.1f} [{r.unsafe_ci_95[0]:4.2f},{r.unsafe_ci_95[1]:5.2f}]"
        ct_str = f"({ct['n11_both_unsafe']},{ct['n10_base_only_unsafe']},{ct['n01_pieq_only_unsafe']},{ct['n00_both_safe']})"
        p_str = "ref" if pid == "full_pieq" else f"{ct['mcnemar_p']:.2e}"
        print(
            f"{pid:<30} {r.rho_round3:5.1f} {r.n_eff_round3:5.2f} {r.r_pr_round3:5.2f} "
            f"{ci_str:>19} {ct_str:>18} {r.correct_mitigation_rate:6.1f} {r.safe_deferral_rate:5.1f} {p_str:>10}"
        )

    print("\n=== Pure Wall-Clock Proxy + On-Disk SQLite WAL Latency Quantiles (1,000 Traces) ===")
    for stage, q in latency_traces.items():
        print(f"  {stage:<42} p50={q['p50']:6.3f} ms | p95={q['p95']:6.3f} ms | p99={q['p99']:6.3f} ms | mean={q['mean']:6.3f} ms")


if __name__ == "__main__":
    main()
