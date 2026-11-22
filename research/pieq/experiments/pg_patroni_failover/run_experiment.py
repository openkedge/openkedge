#!/usr/bin/env python3
"""
PIEQ Revision 7 P2 — Operationalized PostgreSQL/Patroni + 3-Member etcd
Experimental Protocol & 4-Category Falsification Evaluator.

STATUS: UNEXECUTED (Docker/PostgreSQL/Patroni/etcd runtime unavailable on authoring host).

Implements:
  - 5 matched comparisons holding Patroni/PostgreSQL fencing identical:
      1. patroni_native_ha_baseline (no LLM mutation proposals)
      2. llm_proposal_matched_controls (LLM proposals + matched Patroni/etcd fencing)
      3. positive_packing_no_shield (Theorem 3 packing without Lemma 4 plausible-world shield)
      4. conservative_shield_no_packing (Lemma 4 shield without Theorem 3 positive packing)
      5. full_pieq_with_probing (Complete PIEQ + active read-only OOB probes)
  - Fault injection matrix explicitly separating:
      Within-assumption faults (F1-F6): true primary crash, asymmetric partition,
        single-domain false telemetry, slow WAL catch-up, etcd follower pause, transient load spike.
      Outside-assumption faults (F7-F9): shared-domain telemetry collapse (f+1 correlated domains),
        out-of-band direct bypass violating complete writer mediation, unmodeled world omission.
  - Corrected 4-category falsification framework (never conflating categories):
      1. formal_soundness_failure
      2. empirical_benefit_failure
      3. practical_viability_failure (engineering acceptance criteria, NOT mathematical falsification)
      4. trusted_assumption_violation
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Tuple


COMPARISONS: Tuple[str, ...] = (
    "patroni_native_ha_baseline",
    "llm_proposal_matched_controls",
    "positive_packing_no_shield",
    "conservative_shield_no_packing",
    "full_pieq_with_probing",
)

FAULT_MATRIX: Tuple[Dict[str, Any], ...] = (
    {"id": "F1", "name": "clean_primary_sigkill", "within_assumptions": True, "expected_actionable": True,
     "description": "SIGKILL pg1 + power-off BMC witness; replica WAL caught up; failover safely actionable."},
    {"id": "F2", "name": "asymmetric_partition_primary_alive", "within_assumptions": True, "expected_actionable": False,
     "description": "iptables drops pg1<->monitor traffic while pg1 holds etcd lease & serves client writes; promote unsafe."},
    {"id": "F3", "name": "single_faulty_collector_spoof", "within_assumptions": True, "expected_actionable": False,
     "description": "One collector domain falsely reports primary dead (<= f); must defer."},
    {"id": "F4", "name": "ambiguous_resolved_by_oob_probe", "within_assumptions": True, "expected_actionable": True,
     "description": "Passive heartbeat absent; read-only OOB BMC + storage-lease probes distinguish crash from partition."},
    {"id": "F5", "name": "concurrent_reconfig_and_auth_revoke", "within_assumptions": True, "expected_actionable": False,
     "description": "Operator revokes auth_epoch in etcd during failover admission; Tier E2 must reject atomically."},
    {"id": "F6", "name": "transient_load_spike_clears_before_commit", "within_assumptions": True, "expected_actionable": False,
     "description": "SnapshotPreconditionValid=True at v_snap, clears before t_eff; tests CommitPreconditionValid E2 check."},
    {"id": "F7", "name": "exceed_fault_budget_correlated_collapse", "within_assumptions": False, "expected_actionable": False,
     "description": "Outside assumption: >f physical domains compromised simultaneously (trusted-assumption violation)."},
    {"id": "F8", "name": "out_of_band_direct_bypass", "within_assumptions": False, "expected_actionable": False,
     "description": "Outside assumption: direct pg_ctl promote bypassing kf-proxy & etcd CAS (incomplete mediation)."},
    {"id": "F9", "name": "unmodeled_world_omission", "within_assumptions": False, "expected_actionable": False,
     "description": "Outside assumption: disk silent corruption outside W_cand (world-completeness violation)."},
)

# Engineering acceptance criteria (Category 3: Practical Viability — NOT mathematical falsification):
PRACTICAL_VIABILITY_THRESHOLDS: Dict[str, float] = {
    "min_actionable_safe_mitigation_rate": 0.80,
    "max_actionable_false_block_rate": 0.20,
    "max_p95_admission_overhead_ms": 250.0,
    "min_valid_proof_submission_rate": 0.85,
}

# Revision 8 P2 Required Experimental Controls & Observed Metric Schema:
REQUIRED_P2_CONTROLS: Tuple[str, ...] = (
    "constant_patroni_native_ha_config",
    "independent_sql_observation_of_physical_effects",
    "crash_and_asymmetric_network_partition_scenarios",
    "actual_primary_role_and_client_write_witness_recording",
    "snapshot_consistency_audit_during_probing_and_diagnosis",
    "concurrent_admission_race_and_stale_certificate_injection",
    "target_fencing_and_authorization_revocation_exercise",
    "explicit_separation_sqlite_admission_commit_vs_pg_promotion_effect",
    "physically_shared_collector_dependency_accounting",
    "actionable_and_mandatory_defer_scenarios_both_included",
)

REQUIRED_P2_METRICS: Tuple[str, ...] = (
    "real_unsafe_physical_effects",
    "false_precondition_authorizations",
    "useful_autonomous_mitigations",
    "mandatory_safe_deferrals",
    "false_blocks",
    "time_to_recovery_ms",
    "end_to_end_operational_latency_ms",
    "unknown_outcome_retention_and_recovery_count",
    "cpu_memory_model_tokens_and_cost_usd",
)


def evaluate_four_category_falsification(trial_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Evaluate executed trial outcomes strictly separating the four falsification categories.
    Never conflates formal soundness, empirical benefit, practical viability, and assumption violations.
    """
    formal_soundness_failures: List[Dict[str, Any]] = []
    trusted_assumption_violations: List[Dict[str, Any]] = []

    within_actionable = [r for r in trial_results if r["within_assumptions"] and r["expected_actionable"]]
    within_defer = [r for r in trial_results if r["within_assumptions"] and not r["expected_actionable"]]

    for r in trial_results:
        if r["within_assumptions"]:
            if r.get("unsafe_authorization") or r.get("physical_dual_primary_detected"):
                formal_soundness_failures.append(r)
        else:
            if r.get("unsafe_authorization") or r.get("physical_dual_primary_detected"):
                trusted_assumption_violations.append(r)

    actionable_mitigated = sum(1 for r in within_actionable if r.get("safe_correct_mitigation"))
    actionable_rate = (actionable_mitigated / len(within_actionable)) if within_actionable else 0.0
    false_block_rate = 1.0 - actionable_rate if within_actionable else 0.0
    defer_correct = sum(1 for r in within_defer if r.get("safe_deferral"))
    defer_rate = (defer_correct / len(within_defer)) if within_defer else 1.0

    viability_flags = []
    if within_actionable and actionable_rate < PRACTICAL_VIABILITY_THRESHOLDS["min_actionable_safe_mitigation_rate"]:
        viability_flags.append(
            f"actionable_mitigation_rate={actionable_rate:.3f} < {PRACTICAL_VIABILITY_THRESHOLDS['min_actionable_safe_mitigation_rate']}"
        )
    if within_actionable and false_block_rate > PRACTICAL_VIABILITY_THRESHOLDS["max_actionable_false_block_rate"]:
        viability_flags.append(
            f"actionable_false_block_rate={false_block_rate:.3f} > {PRACTICAL_VIABILITY_THRESHOLDS['max_actionable_false_block_rate']}"
        )

    return {
        "category_1_formal_soundness_failure": {
            "count": len(formal_soundness_failures),
            "triggered": len(formal_soundness_failures) > 0,
            "note": "Zero failures in finite trials is necessary evidence, NOT a universal statistical guarantee.",
        },
        "category_2_empirical_benefit_failure": {
            "requires_matched_baseline_comparison": True,
            "note": "Triggered iff Full PIEQ shows no measurable safety/diagnostic advantage over matched LLM+Patroni baseline.",
        },
        "category_3_practical_viability_engineering_criteria": {
            "actionable_mitigation_rate": round(actionable_rate, 4),
            "actionable_false_block_rate": round(false_block_rate, 4),
            "ambiguous_safe_deferral_rate": round(defer_rate, 4),
            "viability_alerts": viability_flags,
            "note": "Engineering acceptance thresholds only; never conflated with mathematical theorem falsification.",
        },
        "category_4_trusted_assumption_violation": {
            "count": len(trusted_assumption_violations),
            "triggered": len(trusted_assumption_violations) > 0,
            "note": "Expected boundary tests (F7-F9) probing behavior outside theorem hypotheses.",
        },
    }


def check_runtime_prerequisites() -> Dict[str, Any]:
    docker_bin = shutil.which("docker")
    psql_bin = shutil.which("psql")
    docker_ok = False
    docker_err = None
    if docker_bin:
        try:
            proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=3, check=False)
            docker_ok = proc.returncode == 0
            if not docker_ok:
                docker_err = (proc.stderr or proc.stdout).strip()[:200]
        except OSError as exc:
            docker_err = str(exc)
    else:
        docker_err = "docker binary not found in PATH"
    return {
        "execution_status": "READY" if (docker_ok and psql_bin is not None) else "UNEXECUTED_MISSING_RUNTIME",
        "docker_available": docker_ok,
        "docker_detail": docker_err,
        "psql_available": psql_bin is not None,
        "comparisons": list(COMPARISONS),
        "fault_scenarios": [f["id"] + ":" + f["name"] for f in FAULT_MATRIX],
        "required_p2_controls": list(REQUIRED_P2_CONTROLS),
        "required_p2_metrics": list(REQUIRED_P2_METRICS),
        "sqlite_vs_pg_note": (
            "SQLite WAL protected-resource commits validate local Tier E2 admission fencing only; "
            "they NEVER prove physical PostgreSQL split-brain exclusion without executing this cluster testbed."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify runtime prerequisites without executing")
    args = parser.parse_args()
    status = check_runtime_prerequisites()
    print(json.dumps(status, indent=2))
    if not args.check and status["execution_status"] != "READY":
        print(
            "ERROR: Isolated PostgreSQL/Patroni + etcd runtime unavailable on this host. "
            "Experiment remains UNEXECUTED; see README.md for deployment instructions.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
