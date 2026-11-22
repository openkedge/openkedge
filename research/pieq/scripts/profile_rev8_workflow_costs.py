#!/usr/bin/env python3
"""
PIEQ Revision 8 P1.4 — Complete Proof-Generation Workflow Funnel & Multi-Scale Cost Accounting.

Measures every stage cleanly, separating:
  - Local TCB microsecond/millisecond CPU stages (typed build/verify, retry overhead,
    ATMS + branch-and-bound packing + shield, probe audit loop, pre-initialized Tier E2
    SQLite WAL `PRAGMA synchronous=FULL` atomic commit),
  - Simulated OOB diagnostic probe I/O latency,
  - Real local 3-member `etcd` v3.7.3 Raft coordination latency (parsed verbatim from
    `results/etcd_local_fault_evidence.json`),
  - Real archived `r4` live five-LLM inference latency, token volume, and API cost
    (parsed verbatim from `results/live_llm_traces_r4.jsonl`),
  - Preserved workload-specific local microbenchmarks (`results/rev7_liveness_summary.json` /
    `profile_revision5_costs.py`).

Writes `results/rev8_workflow_cost_accounting.json`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pieq.core import (
    CrossRootExposureLedger,
    EpistemicLeaseCertificate,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
    compute_disjoint_justification_packing_bounded,
    filter_snapshot_consistent_witnesses,
    PlausibleWorldGate,
)
from pieq.heldout_cases import HELDOUT_COLLECTOR_KEY, build_heldout_cases
from pieq.probe_loop import ProbeBudget, run_active_diagnostic_probe_loop
from pieq.typed_pipeline import TypedCertificateBuilder, TypedVocabulary
from scripts.run_rev8_independent_eval import (
    _make_software_proposer,
    _run_typed_with_full_attempt_tracking,
)

R4_TRACE_PATH = REPO_ROOT / "results" / "live_llm_traces_r4.jsonl"
ETCD_EVIDENCE_PATH = REPO_ROOT / "results" / "etcd_local_fault_evidence.json"
POST_P13_PATH = REPO_ROOT / "results" / "rev8_heldout_post_p13.json"
OUTPUT_PATH = REPO_ROOT / "results" / "rev8_workflow_cost_accounting.json"


def _stats(vals: List[float]) -> Dict[str, float]:
    if not vals:
        return {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    s = sorted(vals)
    return {
        "n": len(s),
        "p50_ms": round(statistics.median(s), 4),
        "p95_ms": round(s[int(0.95 * (len(s) - 1))], 4),
        "mean_ms": round(statistics.mean(s), 4),
    }


def _profile_heldout_stages() -> Dict[str, Any]:
    cases = build_heldout_cases()
    t_first_attempt_ms: List[float] = []
    t_retry_overhead_ms: List[float] = []
    t_packing_and_shield_ms: List[float] = []
    t_probe_cpu_ms: List[float] = []
    t_probe_sim_io_ms: List[float] = []
    t_sqlite_commit_ms: List[float] = []
    t_sqlite_reject_ms: List[float] = []
    t_e2e_useful_action_local_ms: List[float] = []

    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            # Pre-initialize SQLite WAL DB OUTSIDE per-incident timing (measuring real
            # steady-state `BEGIN IMMEDIATE` commit/reject cost, not DDL setup!)
            db_path = os.path.join(tmp, f"prof_{case.case_id}.db")
            target = SQLiteProtectedResourceBoundary(
                db_path=db_path,
                initial_epoch=case.target_min_valid_epoch,
                max_drained_nodes=case.exposure_cap,
                signing_key=HELDOUT_COLLECTOR_KEY,
                expected_manifest_hash=case.target_manifest_hash or case.manifest_hash,
            )
            target.revoke_authorization_atomically(case.auth_epoch_target_min)
            for pid, holds in case.live_predicates_at_commit.items():
                target.set_live_predicate(pid, holds)

            t_inc0 = time.perf_counter()
            atms, verifier, by_id = case.build_atms()
            cand_actions = tuple(sorted({a for w in case.worlds for a in w.safe_mutations}))
            world_ids = tuple(sorted(w.world_id for w in case.worlds))
            alloc_map = {aid: frozenset(hs) for aid, hs in case.agent_allocation.items()}
            vocab = TypedVocabulary(
                permissible_actions=cand_actions,
                world_ids=world_ids,
                rules={r.rule_id: r for r in case.rules},
                agent_authorized_handles=alloc_map,
                assumptions_by_id=by_id,
                expected_resource_id=case.resource_id,
                expected_epoch=case.epoch,
                expected_observation_version=case.snapshot_version,
                expected_manifest_hash=case.manifest_hash,
            )
            builder = TypedCertificateBuilder(vocab, atms)

            # Stage 1 & 2: First-attempt build/verify vs retry overhead
            first_ms = 0.0
            retry_ms = 0.0
            for aid in [f"agent_{i}" for i in range(5)]:
                vis_obs = {h: by_id[h] for h in alloc_map[aid] if h in by_id}
                proposer = _make_software_proposer(
                    rules=case.rules,
                    visible_obs_by_id=vis_obs,
                    cand_actions=cand_actions,
                    world_ids=world_ids,
                    agent_id=aid,
                    inject_r4_first_attempt_faults=(aid == "agent_4"),
                )
                # Measure attempt 1 separately from retry attempt 2:
                t_a1 = time.perf_counter()
                prompt_payload = vocab.agent_prompt_payload(aid)
                raw1 = proposer(prompt_payload, [])
                rep1 = builder.validate_and_register_proposal(aid, raw1, round_tag="round_0:try_0")
                first_ms += (time.perf_counter() - t_a1) * 1000.0
                rej1 = [o for o in rep1.outcomes if not o.accepted]
                if rej1:
                    t_r = time.perf_counter()
                    fb = [
                        {
                            "attempt": 1,
                            "proposal_error": None,
                            "rejected_derivations": [
                                {"rule_id": o.rule_id, "evidence_handles": list(o.evidence_handles),
                                 "category": o.primary_category, "detail": o.detail}
                                for o in rej1
                            ],
                        }
                    ]
                    raw2 = proposer(prompt_payload, fb)
                    builder.validate_and_register_proposal(aid, raw2, round_tag="round_0:try_1")
                    retry_ms += (time.perf_counter() - t_r) * 1000.0
            t_first_attempt_ms.append(first_ms)
            t_retry_overhead_ms.append(retry_ms)

            # Stage 3: Passive packing + plausible-world shield
            t_pk0 = time.perf_counter()
            gate = PlausibleWorldGate(case.worlds, ["defer", "probe"])
            refs = {w.world_id: atms.active_witness_justifications(f"refute:{w.world_id}") for w in case.worlds}
            survs = gate.compute_fault_tolerant_belief_set(
                refs, atms.assumptions_by_bit, fault_budget_f=case.f_budget,
                expected_snapshot_version=case.snapshot_version,
                require_consistent_observation_version=True,
                expected_epoch=case.epoch, expected_resource_id=case.resource_id,
                expected_manifest_hash=case.manifest_hash,
            )
            elig = []
            for a in cand_actions:
                if a in {"defer", "probe"}:
                    continue
                pw, inc, _, _ = filter_snapshot_consistent_witnesses(
                    atms.active_witness_justifications(f"act:{a}"), atms.assumptions_by_bit,
                    expected_snapshot_version=case.snapshot_version, expected_epoch=case.epoch,
                    expected_resource_id=case.resource_id, expected_manifest_hash=case.manifest_hash,
                )
                cnt, _, ex = compute_disjoint_justification_packing_bounded(pw, atms.assumptions_by_bit, 10000)
                ok, _ = gate.evaluate_concurrent_compound_safety(a, case.incident_domain, 0, survs, [])
                if not ex and not inc and ok and cnt >= case.k_a:
                    elig.append(a)
            t_packing_and_shield_ms.append((time.perf_counter() - t_pk0) * 1000.0)

            chosen = elig[0] if len(elig) == 1 else None
            # Stage 4: Active probing loop (when passive doesn't admit & probes available)
            if chosen is None and case.probe_catalog:
                t_pr0 = time.perf_counter()
                pres = run_active_diagnostic_probe_loop(
                    atms=atms, worlds=case.worlds, candidate_actions=cand_actions,
                    incident_class=case.incident_domain, f_fault_budget=case.f_budget,
                    k_a=case.k_a, available_probes=case.probe_catalog,
                    probe_executor=case.make_leak_free_probe_executor(verifier),
                    budget=ProbeBudget(max_invocations=4, max_cost_units=6.0, max_load_units=6),
                    expected_snapshot_version=case.snapshot_version,
                    version_observer=target.current_seq,
                )
                t_probe_cpu_ms.append((time.perf_counter() - t_pr0) * 1000.0)
                if pres.probes_executed > 0:
                    t_probe_sim_io_ms.append(sum(r.latency_ms for r in pres.executed_probes))
                chosen = pres.admitted_action

            # Stage 5: Tier E2 SQLite WAL atomic target admission (pre-initialized WAL)
            committed = False
            if chosen is not None and case.refinement_regime != "UNVERIFIED_ASYNC" and not case.concurrent_cas_stale and case.preexisting_unknown_exposure == 0:
                raw_c = EpistemicLeaseCertificate(
                    action=chosen, support_mask=1, admission_epoch=case.epoch, packing_number=case.k_a,
                    plausible_worlds=tuple(sorted(w.world_id for w in survs)), signature="",
                    auth_epoch=case.auth_epoch_cert, auth_valid_until=case.auth_valid_until,
                    resource_id=case.resource_id, manifest_hash=case.manifest_hash,
                    snapshot_version=case.snapshot_version,
                )
                sig_c = EpistemicLeaseCertificate(
                    action=raw_c.action, support_mask=1, admission_epoch=raw_c.admission_epoch,
                    packing_number=raw_c.packing_number, plausible_worlds=raw_c.plausible_worlds,
                    signature=raw_c.compute_signature(HELDOUT_COLLECTOR_KEY),
                    auth_epoch=raw_c.auth_epoch, auth_valid_until=raw_c.auth_valid_until,
                    resource_id=raw_c.resource_id, manifest_hash=raw_c.manifest_hash,
                    snapshot_version=raw_c.snapshot_version,
                )
                t_sq0 = time.perf_counter()
                rcpt = target.execute_fenced_mutation(
                    sig_c, op_id=f"op_{case.case_id}",
                    required_predecessor_op_ids=case.required_predecessor_op_ids,
                    now_time=case.now_time,
                    required_commit_predicates=case.required_commit_predicates,
                )
                sq_ms = (time.perf_counter() - t_sq0) * 1000.0
                if rcpt.committed:
                    committed = True
                    t_sqlite_commit_ms.append(sq_ms)
                else:
                    t_sqlite_reject_ms.append(sq_ms)

            if committed and case.ground_truth_class == "oracle_actionable_safe_mitigation":
                t_e2e_useful_action_local_ms.append((time.perf_counter() - t_inc0) * 1000.0)

            target.conn.close()

    return {
        "first_attempt_build_and_verify_cpu": _stats(t_first_attempt_ms),
        "retry_verification_overhead_cpu": _stats(t_retry_overhead_ms),
        "atms_packing_and_shield_cpu": _stats(t_packing_and_shield_ms),
        "probe_loop_tcb_audit_cpu": _stats(t_probe_cpu_ms),
        "simulated_oob_probe_io_when_invoked": _stats(t_probe_sim_io_ms),
        "sqlite_wal_synchronous_full_commit": _stats(t_sqlite_commit_ms),
        "sqlite_wal_synchronous_full_reject": _stats(t_sqlite_reject_ms),
        "end_to_end_local_tcb_to_useful_action": _stats(t_e2e_useful_action_local_ms),
    }


def _extract_r4_live_llm_costs() -> Dict[str, Any]:
    records = [json.loads(line) for line in R4_TRACE_PATH.read_text().splitlines() if line.strip()]
    reqs = [r for r in records if r.get("record_type") == "request"]
    r0 = [r for r in reqs if r.get("round") == 0]
    elapsed_all_ms = [float(r.get("elapsed_seconds") or 0.0) * 1000.0 for r in reqs]
    # Per-case Round-0 critical-path (parallel 5 agents = max latency across 5 agents) vs sequential sum:
    by_case_r0: Dict[int, List[float]] = {}
    for r in r0:
        by_case_r0.setdefault(r["case_id"], []).append(float(r.get("elapsed_seconds") or 0.0) * 1000.0)
    r0_parallel_ms = [max(v) for v in by_case_r0.values() if v]
    r0_serial_ms = [sum(v) for v in by_case_r0.values() if v]

    total_in = sum(int((r.get("usage") or {}).get("input_tokens") or 0) for r in reqs)
    total_out = sum(int((r.get("usage") or {}).get("output_tokens") or 0) for r in reqs)
    total_usd = round(sum(float((r.get("usage") or {}).get("reported_cost_usd") or 0.0) for r in reqs), 4)
    r0_in = sum(int((r.get("usage") or {}).get("input_tokens") or 0) for r in r0)
    r0_out = sum(int((r.get("usage") or {}).get("output_tokens") or 0) for r in r0)
    r0_usd = round(sum(float((r.get("usage") or {}).get("reported_cost_usd") or 0.0) for r in r0), 4)

    return {
        "source": "results/live_llm_traces_r4.jsonl (exploratory archived pilot; unpinned codex_cli)",
        "total_calls_all_rounds": len(reqs),
        "round0_calls": len(r0),
        "single_call_latency": _stats(elapsed_all_ms),
        "round0_parallel_wall_clock_per_case": _stats(r0_parallel_ms),
        "round0_sequential_wall_clock_per_case": _stats(r0_serial_ms),
        "total_input_tokens_recorded": total_in,
        "total_output_tokens_recorded": total_out,
        "round0_input_tokens_recorded": r0_in,
        "round0_output_tokens_recorded": r0_out,
        "reported_claude_cli_cost_usd_all_rounds": total_usd,
        "reported_claude_cli_cost_usd_round0": r0_usd,
        "note": "codex_cli & local_openai reported $0.00 in trace; Qwen3-32B local server reported 0 token counts.",
    }


def _extract_etcd_coordination_costs() -> Dict[str, Any]:
    data = json.loads(ETCD_EVIDENCE_PATH.read_text())
    trials = data.get("trials", [])
    by_sched: Dict[str, List[float]] = {}
    for t in trials:
        sched = t.get("schedule", "unknown")
        # Extract elapsed_ms if present
        el = t.get("elapsed_ms") or t.get("duration_ms")
        if el is None and t.get("elapsed_seconds") is not None:
            el = float(t["elapsed_seconds"]) * 1000.0
        if el is not None:
            by_sched.setdefault(sched, []).append(float(el))
    return {
        "source": "results/etcd_local_fault_evidence.json (140 single-host 3-member etcd v3.7.3 trials)",
        "total_trials": len(trials),
        "schedule_summary": {k: _stats(v) for k, v in sorted(by_sched.items())},
        "documented_range_ms": "45-2467 ms schedule medians across normal CAS, faulted leader, and quorum-loss timeout",
    }


def main() -> None:
    post_p13 = json.loads(POST_P13_PATH.read_text())
    report = {
        "schema_version": "pieq-rev8-workflow-cost-accounting-v1",
        "local_tcb_stage_profiling_heldout36": _profile_heldout_stages(),
        "heldout36_proof_generation_funnel_5A_PartTel": post_p13["heldout_agent_count_isolation_with_probes"]["5A_PartTel"]["proof_generation_funnel"],
        "heldout36_proof_generation_funnel_1A_FullTel_DomainIsolated": post_p13["heldout_agent_count_isolation_with_probes"]["1A_FullTel_DomainIsolated"]["proof_generation_funnel"],
        "archived_r4_live_llm_inference_costs": _extract_r4_live_llm_costs(),
        "distributed_coordination_etcd_costs": _extract_etcd_coordination_costs(),
        "physical_db_failover_recovery_time": {
            "status": "UNEXECUTED",
            "reason": "PostgreSQL/Patroni/Docker runtime unavailable on authoring host; SQLite WAL commit (~0.1-0.4 ms) is explicitly NOT substituted for PostgreSQL physical promotion time.",
        },
        "scale_separation_summary": (
            "Local TCB verification, packing, probing audit, and pre-initialized SQLite WAL commit "
            "complete in ~0.1-0.6 ms total CPU+WAL (sub-millisecond); real incident-level time to "
            "authorized useful action is dominated by LLM inference (p50 ~22-27 s parallel Round-0 "
            "wall-clock in archived r4, +retry RTT when retries fire), OOB probe RPCs (~10-50 ms "
            "simulated / real BMC RTT), and distributed Raft coordination (45-2467 ms in local 3-node etcd)."
        ),
    }
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    print(json.dumps(report["local_tcb_stage_profiling_heldout36"], indent=2))
    print("\n=== ARCHIVED R4 LIVE LLM INFERENCE TIMING & COST ===")
    print(json.dumps(report["archived_r4_live_llm_inference_costs"], indent=2))


if __name__ == "__main__":
    main()
