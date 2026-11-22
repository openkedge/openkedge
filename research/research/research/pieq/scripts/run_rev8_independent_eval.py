#!/usr/bin/env python3
"""
PIEQ Revision 8 — Independent Held-Out Evaluation (P0.2), Causally Fair Baselines &
2^4 Mechanism-Isolation Factorial Ablation (P1.1), and Full Proof-Generation Funnel &
Multi-Scale Cost Accounting (P1.4).

Supports two explicit freeze-verified modes:
  - `--mode frozen-rev7`: Verifies SHA-256 hashes of the exact frozen Revision 7 code
    (`results/rev7_frozen_hashes.json`) BEFORE any Revision 8 runtime edits and writes
    `results/rev8_heldout_frozen_rev7.json`.
  - `--mode post-p13`: Runs the EXACT SAME unmodified held-out dataset, proposer,
    probe policy, and thresholds after applying the P1.3 adaptive probe temporal-
    consistency repair (zero retuning!) and writes `results/rev8_heldout_post_p13.json`.

Explicit provenance boundary:
  All automated proposer runs here use deterministic software proposers ($0.00, 0 LLM
  tokens) exercising the real PIEQ TCB + on-disk Tier E2 SQLite WAL (`synchronous=FULL`).
  They are NEVER presented as live LLM evaluations (live LLM re-evaluation requires
  pinned multi-provider credentials via `scripts/run_rev8_pinned_live_eval.py`).
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    CrossRootExposureLedger,
    DeterministicRuleVerifier,
    EpistemicLeaseCertificate,
    FaultWorld,
    PlausibleWorldGate,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
    compute_disjoint_justification_packing_bounded,
)
from pieq.hardened import (
    HardenedAdmissionController,
    HardenedPIEQConfig,
    TargetRefinementAttestation,
)
from pieq.heldout_cases import (
    GROUND_TRUTH_CLASSES,
    HELDOUT_COLLECTOR_KEY,
    HeldOutCase,
    build_heldout_cases,
    build_heldout_manifest,
)
from pieq.live_cases import COLLECTOR_KEY, LiveCase, build_live_cases, fixture_hash
from pieq.probe_loop import (
    DiagnosticProbeSpec,
    ProbeBudget,
    run_active_diagnostic_probe_loop,
)
from pieq.typed_pipeline import (
    FAILURE_CATEGORIES,
    AgentSubmissionReport,
    TypedCertificateBuilder,
    TypedVocabulary,
    run_typed_proposal_with_feedback,
)

REV7_HASHES_PATH = REPO_ROOT / "results" / "rev7_frozen_hashes.json"
FROZEN_CORE_FILES = (
    "pieq/core.py",
    "pieq/typed_pipeline.py",
    "pieq/probe_loop.py",
    "pieq/hardened.py",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_frozen_rev7_hashes() -> Dict[str, str]:
    saved = json.loads(REV7_HASHES_PATH.read_text())
    current = {}
    for rel in FROZEN_CORE_FILES:
        h = _sha256_file(REPO_ROOT / rel)
        current[rel] = h
        if saved.get(rel) != h:
            raise RuntimeError(
                f"Frozen Revision 7 integrity check FAILED for {rel}: expected {saved.get(rel)}, got {h}"
            )
    return current


def _current_code_hashes() -> Dict[str, str]:
    return {rel: _sha256_file(REPO_ROOT / rel) for rel in FROZEN_CORE_FILES}


# =============================================================================
# Generic Deterministic Software Proposer (r4-calibrated, zero oracle access!)
# =============================================================================

def _make_software_proposer(
    *,
    rules: Sequence[Any],
    visible_obs_by_id: Dict[str, BoundPrimitiveAssumption],
    cand_actions: Tuple[str, ...],
    world_ids: Tuple[str, ...],
    agent_id: str,
    inject_r4_first_attempt_faults: bool,
    omit_local_derivations: bool = False,
) -> Callable[[Dict[str, Any], List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Deterministic software proposer using ONLY `visible_obs_by_id` and public `rules`.
    Never reads ground-truth world, expected action, or hidden probe outcomes.
    Calibrated to archived r4 behavior:
      - Cautious agents with partial telemetry prefer `proposed_action="probe"`
        (agent_4 proposes local action if one is derivable).
      - When `inject_r4_first_attempt_faults=True`, attempt 1 on `agent_4` injects
        one unknown rule_id ('conflict') and one incomplete multi-antecedent claim
        (or wrong antecedent), repaired cleanly by structured feedback on attempt 2.
    """
    prop_to_ids: Dict[str, List[str]] = {}
    for oid in sorted(visible_obs_by_id):
        obs = visible_obs_by_id[oid]
        prop_to_ids.setdefault(obs.proposition, []).append(oid)

    provable: List[Dict[str, Any]] = []
    for r in sorted(rules, key=lambda x: x.rule_id):
        if all(ant in prop_to_ids for ant in r.antecedents):
            # If multiple handles exist for same proposition (e.g. full-telemetry single agent),
            # emit one derivation per distinct handle combo (for single-antecedent rules, all handles;
            # for multi-antecedent rules, deterministic disjoint assignment).
            if len(r.antecedents) == 1:
                ant = next(iter(r.antecedents))
                for h in prop_to_ids[ant]:
                    provable.append({"rule_id": r.rule_id, "evidence_handles": [h]})
            else:
                handles = sorted(prop_to_ids[ant][0] for ant in r.antecedents)
                provable.append({"rule_id": r.rule_id, "evidence_handles": handles})

    rules_map = {r.rule_id: r for r in rules}

    def _proposer(prompt_payload: Dict[str, Any], feedback_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        act_consequents = [
            rules_map[d["rule_id"]].consequent[4:]
            for d in provable
            if rules_map[d["rule_id"]].consequent.startswith("act:")
        ]
        if agent_id == "agent_4" and act_consequents:
            local_action = act_consequents[0]
        else:
            local_action = "probe"

        if omit_local_derivations:
            derivs: List[Dict[str, Any]] = []
        elif inject_r4_first_attempt_faults and agent_id == "agent_4" and not feedback_history:
            first_handle = sorted(visible_obs_by_id)[0] if visible_obs_by_id else ""
            bad: List[Dict[str, Any]] = []
            if first_handle:
                bad.append({"rule_id": "conflict", "evidence_handles": [first_handle]})
                # Pick a rule unsatisfied by [first_handle] alone:
                first_prop = visible_obs_by_id[first_handle].proposition
                unsat = next((r for r in rules if r.antecedents != frozenset({first_prop})), None)
                if unsat is not None:
                    bad.append({"rule_id": unsat.rule_id, "evidence_handles": [first_handle]})
            derivs = bad + provable
        else:
            derivs = list(provable)

        return {
            "proposed_action": local_action if local_action in cand_actions else "probe",
            "hypothesized_worlds": list(world_ids),
            "requested_probes": ["probe_disambiguate"] if local_action == "probe" else [],
            "derivations": derivs,
        }

    return _proposer


def _register_domain_isolated_single_agent(
    builder: TypedCertificateBuilder,
    agent_id: str,
    proposer_fn: Callable[[Dict[str, Any], List[Dict[str, Any]]], Dict[str, Any]],
    *,
    max_retries: int,
    enforce_action_coupling: bool,
) -> Tuple[AgentSubmissionReport, List[AgentSubmissionReport]]:
    """
    Fair 1A-FullTel (domain-isolated) submission:
    One agent with ALL authorized telemetry handles runs a single proposal (with
    bounded feedback retry), and each verified derivation is registered with a
    per-derivation context (`agent_0:deriv_<rule_id>_<handles>`) so complete-proof
    packing counts independent physical telemetry failure domains rather than
    collapsing all proofs from 1 agent into one shared `context_id`.
    Still enforces 100% of schema, forbidden-field, handle-auth, Horn-rule,
    binding, and collector-HMAC checks via `TypedCertificateBuilder`.
    """
    prompt_payload = builder.vocab.agent_prompt_payload(agent_id)
    feedback_history: List[Dict[str, Any]] = []
    attempt_reports: List[AgentSubmissionReport] = []
    first_rejections = []
    last_report: Optional[AgentSubmissionReport] = None

    for attempt in range(max_retries + 1):
        raw = proposer_fn(prompt_payload, feedback_history)
        # Validate top-level proposal structure using builder on a dry check of derivations one-by-one
        if not isinstance(raw, dict) or not {"proposed_action", "hypothesized_worlds", "requested_probes", "derivations"}.issubset(raw.keys()):
            rep = builder.validate_and_register_proposal(
                agent_id, raw, round_tag=f"try_{attempt}", enforce_action_coupling=enforce_action_coupling
            )
            rep.attempts_used = attempt + 1
            attempt_reports.append(rep)
            last_report = rep
            if attempt == 0:
                first_rejections = list(rep.outcomes)
            break

        # Validate each derivation with its own per-derivation round_tag so `ctx` isolates
        # by derivation while physical failure-domain footprints `fd:*` remain exact!
        rep = AgentSubmissionReport(
            agent_id=agent_id,
            attempts_used=attempt + 1,
            proposed_action=str(raw["proposed_action"]),
            hypothesized_worlds=tuple(raw["hypothesized_worlds"]),
            requested_probes=tuple(raw["requested_probes"]),
        )
        auth_handles = builder.vocab.agent_authorized_handles.get(agent_id, frozenset())
        seen_keys: set = set()
        for d_idx, item in enumerate(raw["derivations"]):
            o = builder._validate_single_derivation(
                agent_id=agent_id,
                item=item,
                authorized_handles=auth_handles,
                proposed_action=rep.proposed_action,
                round_tag=f"try_{attempt}:d{d_idx}",
                enforce_action_coupling=enforce_action_coupling,
                seen_keys=seen_keys,
            )
            rep.outcomes.append(o)

        rejected = [o for o in rep.outcomes if not o.accepted]
        if attempt == 0:
            first_rejections = list(rejected)
        rep.initial_attempt_rejections = first_rejections
        attempt_reports.append(rep)
        last_report = rep

        if not rep.schema_rejected_proposal and not rejected:
            return rep, attempt_reports

        feedback_history.append(
            {
                "attempt": attempt + 1,
                "proposal_error": None,
                "rejected_derivations": [
                    {
                        "rule_id": o.rule_id,
                        "evidence_handles": list(o.evidence_handles),
                        "category": o.primary_category,
                        "detail": o.detail,
                    }
                    for o in rejected
                ],
            }
        )

    assert last_report is not None
    return last_report, attempt_reports


def _run_typed_with_full_attempt_tracking(
    builder: TypedCertificateBuilder,
    agent_id: str,
    proposer_fn: Callable[[Dict[str, Any], List[Dict[str, Any]]], Dict[str, Any]],
    *,
    max_retries: int,
    enforce_action_coupling: bool,
) -> Tuple[AgentSubmissionReport, List[AgentSubmissionReport]]:
    """Run standard `TypedCertificateBuilder` while capturing EVERY attempt separately for P1.4 funnel."""
    prompt_payload = builder.vocab.agent_prompt_payload(agent_id)
    feedback_history: List[Dict[str, Any]] = []
    attempt_reports: List[AgentSubmissionReport] = []
    first_rejections = []
    last_report: Optional[AgentSubmissionReport] = None

    for attempt in range(max_retries + 1):
        raw = proposer_fn(prompt_payload, feedback_history)
        rep = builder.validate_and_register_proposal(
            agent_id,
            raw,
            round_tag=f"round_0:try_{attempt}",
            enforce_action_coupling=enforce_action_coupling,
        )
        rep.attempts_used = attempt + 1
        rejected = [o for o in rep.outcomes if not o.accepted]
        if attempt == 0:
            first_rejections = list(rejected)
        rep.initial_attempt_rejections = first_rejections
        attempt_reports.append(rep)
        last_report = rep
        if not rep.schema_rejected_proposal and not rejected:
            return rep, attempt_reports
        feedback_history.append(
            {
                "attempt": attempt + 1,
                "proposal_error": (
                    {"category": rep.proposal_error_category, "detail": rep.proposal_error_detail}
                    if rep.schema_rejected_proposal
                    else None
                ),
                "rejected_derivations": [
                    {
                        "rule_id": o.rule_id,
                        "evidence_handles": list(o.evidence_handles),
                        "category": o.primary_category,
                        "detail": o.detail,
                    }
                    for o in rejected
                ],
            }
        )
    assert last_report is not None
    return last_report, attempt_reports


# =============================================================================
# Core Unified Case Evaluator (works on BOTH HeldOutCase and Frozen LiveCase)
# =============================================================================

def _evaluate_heldout_configuration(
    cases: Sequence[HeldOutCase],
    *,
    agent_mode: str = "5A_PartTel",  # 1A_LocalTel | 1A_FullTel_SharedCtx | 1A_FullTel_DomainIsolated | 5A_PartTel | 5A_FullTel | 1A_FullTel_Unshielded | 5A_FullTel_Debate
    typed: bool = True,
    pos_pack: bool = True,
    refute_pack: bool = True,  # True -> f=case.f_budget; False -> naive f=0 single-refutation pruning
    shield_enabled: bool = True,  # False -> disables plausible-world shield completely
    probes: bool = True,
) -> Dict[str, Any]:
    """
    Evaluate one controlled configuration across all 36 held-out cases on real on-disk
    Tier E2 SQLite WAL (`PRAGMA synchronous=FULL`), collecting:
      - Disaggregated outcomes across the 4 independent ground-truth classes,
      - Explicit separation of unsafe invariant violations vs. false-precondition admissions
        vs. out-of-assumption failures,
      - Complete P1.4 proof-generation funnel and stage-by-stage timing.
    """
    funnel = {
        "raw_proposals_submitted_total": 0,
        "first_attempt_proposals_submitted": 0,
        "first_attempt_schema_valid_proposals": 0,
        "first_attempt_horn_valid_proposals": 0,
        "first_attempt_provenance_valid_proposals": 0,
        "first_attempt_derivations_submitted": 0,
        "first_attempt_derivations_schema_valid": 0,
        "first_attempt_derivations_horn_valid": 0,
        "first_attempt_derivations_provenance_valid": 0,
        "first_attempt_derivations_accepted": 0,
        "certificates_accepted_after_retry": 0,
        "retry_count": 0,
        "retry_exhaustion_count": 0,
        "cases_reaching_ka_before_probes": 0,
        "cases_reaching_ka_after_probes": 0,
        "cases_rejected_insufficient_evidence": 0,
        "cases_admitted_after_active_probing": 0,
        "cases_correctly_deferred": 0,
    }
    stage_timings_ms: Dict[str, List[float]] = {
        "typed_build_and_verify_ms": [],
        "retry_overhead_ms": [],
        "epistemic_gate_and_probe_ms": [],
        "sqlite_e2_wal_ms": [],
        "end_to_end_local_tcb_ms": [],
    }
    outcomes = {
        # Within-assumption (31 cases: 9 actionable, 5 unjustified, 17 mandatory-defer):
        "within_assumption_total": 0,
        "actionable_total": 0,
        "actionable_safe_mitigations": 0,
        "actionable_false_blocks": 0,
        "actionable_unsafe_violations": 0,
        "unjustified_total": 0,
        "unjustified_correct_deferrals": 0,
        "unjustified_false_precondition_admissions": 0,
        "mandatory_defer_total": 0,
        "mandatory_defer_correct": 0,
        "mandatory_defer_unsafe_invariant_violations": 0,
        "mandatory_defer_auth_or_conc_or_stale_breaches": 0,
        # Out-of-assumption negative controls (5 cases: H16, H33-H36):
        "out_of_assumption_total": 0,
        "out_of_assumption_admitted_unsafe": 0,
        "out_of_assumption_deferred": 0,
        # Aggregate counters:
        "total_unsafe_invariant_violations_within_assumptions": 0,
        "total_false_precondition_admissions_within_assumptions": 0,
        "probes_executed": 0,
        "probes_effective": 0,
        "unsafe_probes_blocked": 0,
        "simulated_probe_io_ms_total": 0.0,
    }
    per_case: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        for case in cases:
            t_case0 = time.perf_counter()
            atms, verifier, by_id = case.build_atms()
            cand_actions = tuple(sorted({a for w in case.worlds for a in w.safe_mutations}))
            world_ids = tuple(sorted(w.world_id for w in case.worlds))
            all_handles = frozenset(by_id.keys())

            # Configure agent allocation per agent_mode:
            if agent_mode == "1A_LocalTel":
                active_agents = ["agent_0"]
                alloc_map = {"agent_0": frozenset(case.agent_allocation["agent_0"])}
            elif agent_mode in {"1A_FullTel_SharedCtx", "1A_FullTel_DomainIsolated", "1A_FullTel_Unshielded"}:
                active_agents = ["agent_0"]
                alloc_map = {"agent_0": all_handles}
            elif agent_mode in {"5A_FullTel", "5A_FullTel_Debate"}:
                active_agents = [f"agent_{i}" for i in range(5)]
                alloc_map = {aid: all_handles for aid in active_agents}
            else:  # 5A_PartTel
                active_agents = [f"agent_{i}" for i in range(5)]
                alloc_map = {aid: frozenset(case.agent_allocation[aid]) for aid in active_agents}

            # Setup on-disk Tier E2 SQLite WAL target boundary (identical across all modes!)
            db_path = os.path.join(tmp_dir, f"{case.case_id}.db")
            target_mhash = case.target_manifest_hash or case.manifest_hash
            target = SQLiteProtectedResourceBoundary(
                db_path=db_path,
                initial_epoch=case.target_min_valid_epoch,
                max_drained_nodes=case.exposure_cap,
                signing_key=HELDOUT_COLLECTOR_KEY,
                expected_manifest_hash=target_mhash,
            )
            target.revoke_authorization_atomically(case.auth_epoch_target_min)
            for pred_id, holds in case.live_predicates_at_commit.items():
                target.set_live_predicate(pred_id, holds)

            # Setup CrossRootExposureLedger (including preexisting UNKNOWN exposure for H26):
            ledger = CrossRootExposureLedger({case.exposure_domain: case.exposure_cap})
            if case.preexisting_unknown_exposure > 0:
                ledger.admit_and_reserve(
                    issuance_key="ik_preexisting",
                    op_id="op_preexisting_unknown",
                    action="preexisting_op",
                    domain=case.exposure_domain,
                    exposure_cost=case.preexisting_unknown_exposure,
                )
                ledger.mark_timeout_or_cancel_race(case.exposure_domain, "op_preexisting_unknown", state="UNKNOWN")

            # --- Unshielded / Debate Matched Tier E2 Baselines ---
            if agent_mode in {"1A_FullTel_Unshielded", "5A_FullTel_Debate"}:
                t_b0 = time.perf_counter()
                votes: Counter[str] = Counter()
                for aid in active_agents:
                    vis_props = {by_id[h].proposition for h in alloc_map[aid] if h in by_id}
                    hits = sorted(
                        {
                            r.consequent[4:]
                            for r in case.rules
                            if r.consequent.startswith("act:") and r.antecedents.issubset(vis_props)
                        }
                    )
                    funnel["raw_proposals_submitted_total"] += 1
                    funnel["first_attempt_proposals_submitted"] += 1
                    votes[hits[0] if hits else "defer"] += 1
                if agent_mode == "1A_FullTel_Unshielded":
                    proposed = next((a for a, _ in votes.most_common() if a != "defer"), "defer")
                else:
                    proposed = next((a for a, cnt in sorted(votes.items()) if cnt >= 3 and a != "defer"), "defer")
                stage_timings_ms["typed_build_and_verify_ms"].append((time.perf_counter() - t_b0) * 1000.0)
                stage_timings_ms["retry_overhead_ms"].append(0.0)
                stage_timings_ms["epistemic_gate_and_probe_ms"].append(0.0)

                t_sq0 = time.perf_counter()
                admitted = False
                chosen = "defer"
                reason = "DEFER"
                if proposed != "defer":
                    # Still enforce matched Tier E2 SQLite WAL boundary (auth, TTL, clock, epoch, frontier, commit predicates)!
                    if case.now_time is None or (case.auth_valid_until is not None and case.now_time > case.auth_valid_until):
                        reason = "REJECTED_AUTH_CLOCK_OR_TTL"
                    elif case.refinement_regime == "UNVERIFIED_ASYNC":
                        reason = "REJECTED_UNVERIFIED_ASYNC"
                    elif case.concurrent_cas_stale or case.preexisting_unknown_exposure >= case.exposure_cap:
                        reason = "REJECTED_CONCURRENT_OR_EXPOSURE"
                    else:
                        raw_c = EpistemicLeaseCertificate(
                            action=proposed,
                            support_mask=0,
                            admission_epoch=case.epoch,
                            packing_number=0,
                            plausible_worlds=(),
                            signature="",
                            auth_epoch=case.auth_epoch_cert,
                            auth_valid_until=case.auth_valid_until,
                            resource_id=case.resource_id,
                            manifest_hash=case.manifest_hash,
                            snapshot_version=case.snapshot_version,
                        )
                        sig_c = EpistemicLeaseCertificate(
                            action=raw_c.action,
                            support_mask=0,
                            admission_epoch=raw_c.admission_epoch,
                            packing_number=0,
                            plausible_worlds=(),
                            signature=raw_c.compute_signature(HELDOUT_COLLECTOR_KEY),
                            auth_epoch=raw_c.auth_epoch,
                            auth_valid_until=raw_c.auth_valid_until,
                            resource_id=raw_c.resource_id,
                            manifest_hash=raw_c.manifest_hash,
                            snapshot_version=raw_c.snapshot_version,
                        )
                        rcpt = target.execute_fenced_mutation(
                            sig_c,
                            op_id=f"op_{case.case_id}",
                            required_predecessor_op_ids=case.required_predecessor_op_ids,
                            now_time=case.now_time,
                            required_commit_predicates=case.required_commit_predicates,
                        )
                        admitted = rcpt.committed
                        chosen = proposed if admitted else "defer"
                        reason = rcpt.status
                stage_timings_ms["sqlite_e2_wal_ms"].append((time.perf_counter() - t_sq0) * 1000.0)
                stage_timings_ms["end_to_end_local_tcb_ms"].append((time.perf_counter() - t_case0) * 1000.0)
                target.conn.close()
                _record_heldout_case_outcome(
                    case, admitted, chosen, reason, outcomes, funnel, per_case,
                    admitted_after_probe=False,
                )
                continue

            # --- PIEQ / Ablation Execution Path ---
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
            enforce_coupling = not typed
            max_retries = 2 if typed else 0

            t_build0 = time.perf_counter()
            retry_ms_case = 0.0
            for aid in active_agents:
                vis_obs = {h: by_id[h] for h in alloc_map[aid] if h in by_id}
                proposer = _make_software_proposer(
                    rules=case.rules,
                    visible_obs_by_id=vis_obs,
                    cand_actions=cand_actions,
                    world_ids=world_ids,
                    agent_id=aid,
                    inject_r4_first_attempt_faults=(aid == "agent_4"),
                    omit_local_derivations=False,
                )
                t_a0 = time.perf_counter()
                if agent_mode == "1A_FullTel_DomainIsolated":
                    rep, attempts = _register_domain_isolated_single_agent(
                        builder, aid, proposer, max_retries=max_retries, enforce_action_coupling=enforce_coupling
                    )
                else:
                    rep, attempts = _run_typed_with_full_attempt_tracking(
                        builder, aid, proposer, max_retries=max_retries, enforce_action_coupling=enforce_coupling
                    )
                t_a_elapsed = (time.perf_counter() - t_a0) * 1000.0
                if len(attempts) > 1:
                    retry_ms_case += t_a_elapsed * ((len(attempts) - 1) / len(attempts))

                # P1.4 Funnel Accounting:
                funnel["raw_proposals_submitted_total"] += len(attempts)
                funnel["first_attempt_proposals_submitted"] += 1
                a0 = attempts[0]
                if not a0.schema_rejected_proposal and all(o.schema_valid for o in a0.outcomes):
                    funnel["first_attempt_schema_valid_proposals"] += 1
                if not a0.schema_rejected_proposal and all(o.horn_rule_valid for o in a0.outcomes):
                    funnel["first_attempt_horn_valid_proposals"] += 1
                if not a0.schema_rejected_proposal and all(o.provenance_valid for o in a0.outcomes):
                    funnel["first_attempt_provenance_valid_proposals"] += 1
                for o in a0.outcomes:
                    funnel["first_attempt_derivations_submitted"] += 1
                    funnel["first_attempt_derivations_schema_valid"] += int(o.schema_valid)
                    funnel["first_attempt_derivations_horn_valid"] += int(o.horn_rule_valid)
                    funnel["first_attempt_derivations_provenance_valid"] += int(o.provenance_valid)
                    funnel["first_attempt_derivations_accepted"] += int(o.accepted)

                retries_here = len(attempts) - 1
                funnel["retry_count"] += retries_here
                if retries_here == max_retries and max_retries > 0 and (
                    rep.schema_rejected_proposal or any(not o.accepted for o in rep.outcomes)
                ):
                    funnel["retry_exhaustion_count"] += 1
                funnel["certificates_accepted_after_retry"] += sum(1 for o in rep.outcomes if o.accepted)

            build_ms = (time.perf_counter() - t_build0) * 1000.0
            stage_timings_ms["typed_build_and_verify_ms"].append(build_ms)
            stage_timings_ms["retry_overhead_ms"].append(retry_ms_case)

            # Measure pre-probe k_a attainment:
            pre_max_pack = max(
                (
                    compute_disjoint_justification_packing_bounded(
                        atms.active_witness_justifications(f"act:{a}"), atms.assumptions_by_bit, 10000
                    )[0]
                    for a in cand_actions
                    if a not in {"defer", "probe"}
                ),
                default=0,
            )
            if pre_max_pack >= case.k_a:
                funnel["cases_reaching_ka_before_probes"] += 1

            # --- Epistemic Gate + Optional Probing + Hardened Tier E2 Execution ---
            t_gate0 = time.perf_counter()
            effective_f = case.f_budget if refute_pack else 0
            probe_specs = list(case.probe_catalog) if probes else []
            probe_exec = case.make_leak_free_probe_executor(verifier) if probes else None
            admitted_after_probe = False
            probes_exec_case = 0
            probes_eff_case = 0

            # If shield_enabled=False OR refute_pack=False (where f=0 < k_a-1), we cannot construct
            # HardenedPIEQConfig when k_a >= f+1 still holds (for f=0, k_a=2 >= 1 IS valid in HardenedPIEQConfig!).
            # Only when shield_enabled=False do we bypass PlausibleWorldGate to test no-shield ablation cleanly.
            if shield_enabled:
                config = HardenedPIEQConfig(  # legacy temporal mode: archival Rev8 software eval
                    legacy_temporal_mode_opt_in=True,
                    resource_id=case.resource_id,
                    epoch=case.epoch,
                    manifest_hash=case.manifest_hash,
                    auth_epoch=case.auth_epoch_cert,
                    signing_key=HELDOUT_COLLECTOR_KEY,
                    f_fault_budget=effective_f,
                    k_a=case.k_a,
                    require_positive_packing=pos_pack,
                )
                att = TargetRefinementAttestation(
                    resource_id=case.resource_id,
                    tier="E2_SHARED_ATOMIC",
                    refinement_regime=case.refinement_regime,
                    manifest_hash=case.manifest_hash,
                    complete_writer_mediation=case.complete_writer_mediation,
                ).with_signature(HELDOUT_COLLECTOR_KEY)
                ctrl = HardenedAdmissionController(config, att, ledger, target)
                snap = SnapshotContext(
                    snapshot_version=case.snapshot_version,
                    min_obs_version=case.snapshot_version,
                    max_obs_version=case.snapshot_version,
                    require_versioned_commits=True,
                    require_contiguous_versions=True,
                )
                override_revs = {case.exposure_domain: 999} if case.concurrent_cas_stale else None
                dec = ctrl.evaluate_and_execute(
                    atms=atms,
                    worlds=case.worlds,
                    candidate_actions=cand_actions,
                    incident_class=case.incident_domain,
                    exposure_domain=case.exposure_domain,
                    exposure_cost=1,
                    op_id=f"op_{case.case_id}",
                    issuance_key=f"ik_{case.case_id}",
                    snapshot_context=snap,
                    now_time=case.now_time,
                    auth_valid_until=case.auth_valid_until,
                    required_predecessor_op_ids=case.required_predecessor_op_ids,
                    required_commit_predicates=case.required_commit_predicates,
                    available_probes=probe_specs,
                    probe_executor=probe_exec,
                    probe_budget=ProbeBudget(max_invocations=4, max_cost_units=6.0, max_load_units=6),
                    override_expected_admission_revs=override_revs,
                )
                gate_and_e2_ms = (time.perf_counter() - t_gate0) * 1000.0
                if dec.probe_result is not None:
                    probes_exec_case = dec.probe_result.probes_executed
                    probes_eff_case = dec.probe_result.probes_effective
                    outcomes["probes_executed"] += probes_exec_case
                    outcomes["probes_effective"] += probes_eff_case
                    outcomes["unsafe_probes_blocked"] += len(dec.probe_result.rejected_unsafe_probes)
                    outcomes["simulated_probe_io_ms_total"] += sum(
                        r.latency_ms for r in dec.probe_result.executed_probes
                    )
                    if dec.admitted and probes_exec_case > 0:
                        admitted_after_probe = True
                post_max_pack = (
                    max(dec.action_packing.values(), default=0)
                    if dec.action_packing
                    else pre_max_pack
                )
                if post_max_pack >= case.k_a:
                    funnel["cases_reaching_ka_after_probes"] += 1
                if not dec.admitted and (
                    "INSUFFICIENT" in dec.reason
                    or "AMBIGUOUS" in dec.reason
                    or "NO_UNIQUE" in dec.reason
                    or "PROBE_BUDGET" in dec.reason
                    or "SNAPSHOT" in dec.reason
                ):
                    funnel["cases_rejected_insufficient_evidence"] += 1
                admitted = dec.admitted
                chosen = dec.action if dec.admitted else "defer"
                reason = dec.reason
                # Approximate split between epistemic/probe gate vs SQLite commit:
                stage_timings_ms["epistemic_gate_and_probe_ms"].append(gate_and_e2_ms)
                stage_timings_ms["sqlite_e2_wal_ms"].append(0.0)
            else:
                # Shield completely OFF (admits any action meeting positive packing, ignoring plausible worlds!):
                elig = []
                act_pack: Dict[str, int] = {}
                for a in cand_actions:
                    if a in {"defer", "probe"}:
                        continue
                    cnt, _, ex = compute_disjoint_justification_packing_bounded(
                        atms.active_witness_justifications(f"act:{a}"), atms.assumptions_by_bit, 10000
                    )
                    act_pack[a] = cnt
                    if not ex and (cnt >= case.k_a if pos_pack else cnt >= 1):
                        elig.append(a)
                if max(act_pack.values(), default=0) >= case.k_a:
                    funnel["cases_reaching_ka_after_probes"] += 1
                proposed = elig[0] if len(elig) == 1 else "defer"
                stage_timings_ms["epistemic_gate_and_probe_ms"].append((time.perf_counter() - t_gate0) * 1000.0)
                t_sq0 = time.perf_counter()
                admitted = False
                chosen = "defer"
                reason = "DEFER_NO_POSITIVE_ACTION"
                if proposed != "defer":
                    if case.now_time is None or (case.auth_valid_until is not None and case.now_time > case.auth_valid_until):
                        reason = "REJECTED_AUTH_CLOCK_OR_TTL"
                    elif case.refinement_regime == "UNVERIFIED_ASYNC":
                        reason = "REJECTED_UNVERIFIED_ASYNC"
                    elif case.concurrent_cas_stale or case.preexisting_unknown_exposure >= case.exposure_cap:
                        reason = "REJECTED_CONCURRENT_OR_EXPOSURE"
                    else:
                        raw_c = EpistemicLeaseCertificate(
                            action=proposed,
                            support_mask=0,
                            admission_epoch=case.epoch,
                            packing_number=act_pack.get(proposed, 0),
                            plausible_worlds=(),
                            signature="",
                            auth_epoch=case.auth_epoch_cert,
                            auth_valid_until=case.auth_valid_until,
                            resource_id=case.resource_id,
                            manifest_hash=case.manifest_hash,
                            snapshot_version=case.snapshot_version,
                        )
                        sig_c = EpistemicLeaseCertificate(
                            action=raw_c.action,
                            support_mask=0,
                            admission_epoch=raw_c.admission_epoch,
                            packing_number=raw_c.packing_number,
                            plausible_worlds=(),
                            signature=raw_c.compute_signature(HELDOUT_COLLECTOR_KEY),
                            auth_epoch=raw_c.auth_epoch,
                            auth_valid_until=raw_c.auth_valid_until,
                            resource_id=raw_c.resource_id,
                            manifest_hash=raw_c.manifest_hash,
                            snapshot_version=raw_c.snapshot_version,
                        )
                        rcpt = target.execute_fenced_mutation(
                            sig_c,
                            op_id=f"op_{case.case_id}",
                            required_predecessor_op_ids=case.required_predecessor_op_ids,
                            now_time=case.now_time,
                            required_commit_predicates=case.required_commit_predicates,
                        )
                        admitted = rcpt.committed
                        chosen = proposed if admitted else "defer"
                        reason = rcpt.status
                stage_timings_ms["sqlite_e2_wal_ms"].append((time.perf_counter() - t_sq0) * 1000.0)

            stage_timings_ms["end_to_end_local_tcb_ms"].append((time.perf_counter() - t_case0) * 1000.0)
            target.conn.close()
            _record_heldout_case_outcome(
                case, admitted, chosen, reason, outcomes, funnel, per_case,
                admitted_after_probe=admitted_after_probe,
                probes_executed=probes_exec_case,
                probes_effective=probes_eff_case,
            )

    def _med_p95(xs: List[float]) -> Dict[str, float]:
        if not xs:
            return {"p50_ms": 0.0, "p95_ms": 0.0}
        s = sorted(xs)
        return {
            "p50_ms": round(statistics.median(s), 3),
            "p95_ms": round(s[int(0.95 * (len(s) - 1))], 3),
        }

    return {
        "config": {
            "agent_mode": agent_mode,
            "typed": typed,
            "pos_pack": pos_pack,
            "refute_pack": refute_pack,
            "shield_enabled": shield_enabled,
            "probes": probes,
        },
        "outcomes": outcomes,
        "proof_generation_funnel": funnel,
        "local_tcb_timings": {k: _med_p95(v) for k, v in stage_timings_ms.items()},
        "per_case_results": per_case,
    }


def _record_heldout_case_outcome(
    case: HeldOutCase,
    admitted: bool,
    chosen: str,
    reason: str,
    outcomes: Dict[str, Any],
    funnel: Dict[str, Any],
    per_case: List[Dict[str, Any]],
    *,
    admitted_after_probe: bool,
    probes_executed: int = 0,
    probes_effective: int = 0,
) -> None:
    gt = case.ground_truth_class
    safe_in_true = (chosen == "defer") or (chosen in case.true_world_safe_actions)
    verdict = ""

    if gt == "out_of_assumption_failure":
        outcomes["out_of_assumption_total"] += 1
        if admitted:
            outcomes["out_of_assumption_admitted_unsafe"] += 1
            verdict = f"OUT_OF_ASSUMPTION_ADMITTED({case.violated_assumption_stratum})"
        else:
            outcomes["out_of_assumption_deferred"] += 1
            verdict = f"OUT_OF_ASSUMPTION_DEFERRED({case.violated_assumption_stratum})"
    else:
        outcomes["within_assumption_total"] += 1
        if gt == "oracle_actionable_safe_mitigation":
            outcomes["actionable_total"] += 1
            if admitted and chosen == case.expected_safe_action and safe_in_true:
                outcomes["actionable_safe_mitigations"] += 1
                if admitted_after_probe:
                    funnel["cases_admitted_after_active_probing"] += 1
                verdict = "SAFE_CORRECT_MITIGATION"
            elif not admitted:
                outcomes["actionable_false_blocks"] += 1
                verdict = "FALSE_BLOCK"
            elif not safe_in_true:
                outcomes["actionable_unsafe_violations"] += 1
                outcomes["total_unsafe_invariant_violations_within_assumptions"] += 1
                verdict = "UNSAFE_INVARIANT_VIOLATION"
            else:
                outcomes["total_false_precondition_admissions_within_assumptions"] += 1
                verdict = "SAFE_WRONG_ACTION"
        elif gt == "safe_but_diagnostically_unjustified":
            outcomes["unjustified_total"] += 1
            if not admitted:
                outcomes["unjustified_correct_deferrals"] += 1
                funnel["cases_correctly_deferred"] += 1
                verdict = "CORRECT_DEFERRAL_UNJUSTIFIED"
            elif not safe_in_true:
                outcomes["total_unsafe_invariant_violations_within_assumptions"] += 1
                verdict = "UNSAFE_INVARIANT_VIOLATION"
            else:
                outcomes["unjustified_false_precondition_admissions"] += 1
                outcomes["total_false_precondition_admissions_within_assumptions"] += 1
                verdict = "FALSE_PRECONDITION_ADMISSION"
        elif gt == "correct_mandatory_deferral":
            outcomes["mandatory_defer_total"] += 1
            if not admitted:
                outcomes["mandatory_defer_correct"] += 1
                funnel["cases_correctly_deferred"] += 1
                verdict = "CORRECT_MANDATORY_DEFERRAL"
            elif not safe_in_true:
                outcomes["mandatory_defer_unsafe_invariant_violations"] += 1
                outcomes["total_unsafe_invariant_violations_within_assumptions"] += 1
                verdict = "UNSAFE_INVARIANT_VIOLATION"
            else:
                outcomes["mandatory_defer_auth_or_conc_or_stale_breaches"] += 1
                outcomes["total_false_precondition_admissions_within_assumptions"] += 1
                verdict = "UNAUTHORIZED_OR_STALE_ADMISSION"

    per_case.append(
        {
            "case_id": case.case_id,
            "archetype": case.archetype,
            "incident_domain": case.incident_domain,
            "ground_truth_class": gt,
            "violated_assumption_stratum": case.violated_assumption_stratum,
            "admitted": admitted,
            "action": chosen,
            "reason": reason,
            "safe_in_true_world": safe_in_true,
            "probes_executed": probes_executed,
            "probes_effective": probes_effective,
            "verdict": verdict,
        }
    )


# =============================================================================
# Frozen 40-Case Fair Agent-Count & 2^4 Factorial Evaluation (P1.1 + P1.4)
# =============================================================================

def _lift_frozen_case(case: LiveCase, mhash: str):
    verifier = DeterministicRuleVerifier(list(case.rules), list(case.invariants), secret_key=COLLECTOR_KEY)
    atms = BoundedATMSEngine(
        verifier, expected_epoch=2000, expected_resource_id="db_cluster", expected_manifest_hash=mhash
    )
    by_id: Dict[str, BoundPrimitiveAssumption] = {}
    for obs in case.observations:
        sig = verifier.sign_telemetry(
            obs.assumption_id, obs.channel_id, obs.failure_domain, 2000, obs.proposition,
            observation_version=1000, bit_index=obs.bit_index, resource_id="db_cluster", manifest_hash=mhash,
        )
        b = BoundPrimitiveAssumption(
            obs.bit_index, obs.assumption_id, obs.channel_id, obs.failure_domain,
            2000, obs.proposition, sig, 1000, "db_cluster", mhash,
        )
        assert atms.register_assumption(b)
        by_id[b.assumption_id] = b
    for inv in case.invariants:
        matching = [a for a in by_id.values() if a.proposition in inv.conflict_predicates]
        if {a.proposition for a in matching} == set(inv.conflict_predicates):
            atms.register_nogood("collector", sum(1 << a.bit_index for a in matching), inv.invariant_id)
    return atms, verifier, by_id


def _make_leak_free_frozen40_probes(
    case: LiveCase, atms: BoundedATMSEngine, mhash: str
) -> Tuple[List[DiagnosticProbeSpec], Callable[[DiagnosticProbeSpec, int], BoundPrimitiveAssumption]]:
    """Schema-level read-only re-query probes for frozen40 (no agent_2 peeking, no oracle target_actions)."""
    obs_by_prop = {o.proposition: o for o in case.observations}
    rule_props = sorted({ant for r in case.rules for ant in r.antecedents})
    probes: List[DiagnosticProbeSpec] = []
    prop_lookup: Dict[str, str] = {}
    for idx, prop in enumerate(rule_props):
        orig = obs_by_prop.get(prop)
        dom = orig.failure_domain if orig is not None else f"fd_schema_{idx}"
        col = f"ro_col_{orig.channel_id}" if orig is not None else f"ro_col_{idx}"
        wmap = {}
        for w in case.worlds:
            refutes_w = any(r.consequent == f"refute:{w.world_id}" and prop in r.antecedents for r in case.rules)
            wmap[w.world_id] = f"refutes_{w.world_id}" if refutes_w else prop
        t_acts = tuple(sorted({r.consequent[4:] for r in case.rules if r.consequent.startswith("act:") and prop in r.antecedents}))
        pid = f"ro_schema_{idx:02d}"
        probes.append(
            DiagnosticProbeSpec(
                probe_id=pid, collector_id=col, failure_domain=dom,
                read_only=True, safety_contract_verified=True, cost_units=1.0,
                estimated_latency_ms=10.0, world_outcome_map=wmap, target_actions=t_acts,
            )
        )
        prop_lookup[pid] = prop

    def _exec(spec: DiagnosticProbeSpec, next_bit: int) -> BoundPrimitiveAssumption:
        prop = prop_lookup[spec.probe_id]
        orig = obs_by_prop.get(prop)
        ret_prop = orig.proposition if orig is not None else f"obs:absent_{spec.probe_id}"
        ret_dom = orig.failure_domain if orig is not None else spec.failure_domain
        sig = atms.verifier.sign_telemetry(
            f"p_{spec.probe_id}_{next_bit}", spec.collector_id, ret_dom, 2000, ret_prop,
            observation_version=1000, bit_index=next_bit, resource_id="db_cluster", manifest_hash=mhash,
        )
        return BoundPrimitiveAssumption(
            next_bit, f"p_{spec.probe_id}_{next_bit}", spec.collector_id, ret_dom,
            2000, ret_prop, sig, 1000, "db_cluster", mhash,
        )

    return probes, _exec


def _evaluate_frozen40_configuration(
    cases: Sequence[LiveCase],
    *,
    agent_mode: str = "5A_PartTel",
    typed: bool = True,
    pos_pack: bool = True,
    refute_pack: bool = True,
    shield_enabled: bool = True,
    probes: bool = True,
    simulate_agent2_omission_on_last5: bool = True,
) -> Dict[str, Any]:
    actionable_ids = [c.case_id for c in cases if c.answer_key()["oracle_outcome"] != "defer"]
    omitted_ids = set(actionable_ids[-5:]) if simulate_agent2_omission_on_last5 else set()

    summary = {
        "actionable_safe_mitigations_19": 0,
        "actionable_false_blocks_19": 0,
        "ambiguous_safe_deferrals_21": 0,
        "unsafe_invariant_violations_40": 0,
        "false_precondition_admissions_40": 0,
        "probes_executed": 0,
        "probes_effective": 0,
        "first_attempt_derivations_submitted": 0,
        "first_attempt_derivations_accepted": 0,
        "post_retry_derivations_accepted": 0,
        "retry_count": 0,
        "proposer_calls": 0,
    }
    latencies: List[float] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        for case in cases:
            t0 = time.perf_counter()
            key = case.answer_key()
            is_act = key["oracle_outcome"] != "defer"
            true_w = next(w for w in case.worlds if w.world_id == case.true_world)
            mhash = fixture_hash(["rev8_f40", case.case_id])
            atms, _, by_id = _lift_frozen_case(case, mhash)
            cand_actions = tuple(sorted({a for w in case.worlds for a in w.safe_mutations}))
            world_ids = tuple(sorted(w.world_id for w in case.worlds))
            all_handles = frozenset(by_id.keys())

            if agent_mode == "1A_LocalTel":
                active_agents = ["agent_0"]
                alloc_map = {"agent_0": frozenset(case.agent_allocation["agent_0"])}
            elif agent_mode in {"1A_FullTel_SharedCtx", "1A_FullTel_DomainIsolated"}:
                active_agents = ["agent_0"]
                alloc_map = {"agent_0": all_handles}
            elif agent_mode == "5A_FullTel":
                active_agents = [f"agent_{i}" for i in range(5)]
                alloc_map = {aid: all_handles for aid in active_agents}
            else:
                active_agents = [f"agent_{i}" for i in range(5)]
                alloc_map = {aid: frozenset(case.agent_allocation[aid]) for aid in active_agents}

            vocab = TypedVocabulary(
                permissible_actions=cand_actions, world_ids=world_ids,
                rules={r.rule_id: r for r in case.rules},
                agent_authorized_handles=alloc_map, assumptions_by_id=by_id,
                expected_resource_id="db_cluster", expected_epoch=2000,
                expected_observation_version=1000, expected_manifest_hash=mhash,
            )
            builder = TypedCertificateBuilder(vocab, atms)
            enforce_coupling = not typed
            max_retries = 2 if typed else 0

            for aid in active_agents:
                vis_obs = {h: by_id[h] for h in alloc_map[aid] if h in by_id}
                omit_here = (case.case_id in omitted_ids) and (aid == "agent_2")
                proposer = _make_software_proposer(
                    rules=case.rules, visible_obs_by_id=vis_obs,
                    cand_actions=cand_actions, world_ids=world_ids,
                    agent_id=aid, inject_r4_first_attempt_faults=(aid == "agent_4"),
                    omit_local_derivations=omit_here,
                )
                if agent_mode == "1A_FullTel_DomainIsolated":
                    rep, attempts = _register_domain_isolated_single_agent(
                        builder, aid, proposer, max_retries=max_retries, enforce_action_coupling=enforce_coupling
                    )
                else:
                    rep, attempts = _run_typed_with_full_attempt_tracking(
                        builder, aid, proposer, max_retries=max_retries, enforce_action_coupling=enforce_coupling
                    )
                summary["proposer_calls"] += len(attempts)
                summary["retry_count"] += len(attempts) - 1
                summary["first_attempt_derivations_submitted"] += len(attempts[0].outcomes)
                summary["first_attempt_derivations_accepted"] += sum(1 for o in attempts[0].outcomes if o.accepted)
                summary["post_retry_derivations_accepted"] += sum(1 for o in rep.outcomes if o.accepted)

            target = SQLiteProtectedResourceBoundary(
                os.path.join(tmp_dir, f"f40_{case.case_id}.db"),
                initial_epoch=2000, signing_key=COLLECTOR_KEY, expected_manifest_hash=mhash,
            )
            target.revoke_authorization_atomically(3)
            ledger = CrossRootExposureLedger({"db_cluster": 1})

            if shield_enabled:
                effective_f = case.f if refute_pack else 0
                config = HardenedPIEQConfig(  # legacy temporal mode: archival Rev8 software eval
                    legacy_temporal_mode_opt_in=True,
                    resource_id="db_cluster", epoch=2000, manifest_hash=mhash, auth_epoch=3,
                    signing_key=COLLECTOR_KEY, f_fault_budget=effective_f, k_a=case.k_a,
                    require_positive_packing=pos_pack,
                )
                att = TargetRefinementAttestation(
                    "db_cluster", "E2_SHARED_ATOMIC", "R1_ATOMIC", mhash, True
                ).with_signature(COLLECTOR_KEY)
                ctrl = HardenedAdmissionController(config, att, ledger, target)
                snap = SnapshotContext(1000, 1000, 1000, True, True)
                p_specs, p_exec = _make_leak_free_frozen40_probes(case, atms, mhash) if probes else ([], None)
                dec = ctrl.evaluate_and_execute(
                    atms=atms, worlds=case.worlds, candidate_actions=cand_actions,
                    incident_class=case.incident_class, exposure_domain="db_cluster", exposure_cost=1,
                    op_id=f"op_{case.case_id}", issuance_key=f"ik_{case.case_id}", snapshot_context=snap,
                    now_time=100, auth_valid_until=500,
                    available_probes=p_specs, probe_executor=p_exec,
                    probe_budget=ProbeBudget(max_invocations=4, max_cost_units=6.0, max_load_units=6),
                )
                if dec.probe_result is not None:
                    summary["probes_executed"] += dec.probe_result.probes_executed
                    summary["probes_effective"] += dec.probe_result.probes_effective
                chosen = dec.action if dec.admitted else "defer"
                act_pack = dec.action_packing
            else:
                act_pack = {}
                elig = []
                for a in cand_actions:
                    if a in {"defer", "probe"}:
                        continue
                    cnt, _, ex = compute_disjoint_justification_packing_bounded(
                        atms.active_witness_justifications(f"act:{a}"), atms.assumptions_by_bit, 10000
                    )
                    act_pack[a] = cnt
                    if not ex and (cnt >= case.k_a if pos_pack else cnt >= 1):
                        elig.append(a)
                chosen = elig[0] if len(elig) == 1 else "defer"

            safe_in_true = (chosen == "defer") or (chosen in true_w.safe_mutations)
            if is_act:
                if chosen == key["oracle_outcome"]:
                    summary["actionable_safe_mitigations_19"] += 1
                    if act_pack.get(chosen, 0) < case.k_a:
                        summary["false_precondition_admissions_40"] += 1
                elif chosen == "defer":
                    summary["actionable_false_blocks_19"] += 1
                elif not safe_in_true:
                    summary["unsafe_invariant_violations_40"] += 1
                else:
                    summary["false_precondition_admissions_40"] += 1
            else:
                if chosen == "defer":
                    summary["ambiguous_safe_deferrals_21"] += 1
                elif not safe_in_true:
                    summary["unsafe_invariant_violations_40"] += 1
                else:
                    summary["false_precondition_admissions_40"] += 1

            latencies.append((time.perf_counter() - t0) * 1000.0)
            target.conn.close()

    summary["median_latency_ms"] = round(statistics.median(latencies), 3)
    return summary


def run_all(mode: str) -> Dict[str, Any]:
    if mode == "frozen-rev7":
        code_hashes = _verify_frozen_rev7_hashes()
    else:
        code_hashes = _current_code_hashes()

    heldout = build_heldout_cases()
    heldout_manifest = build_heldout_manifest(heldout)
    frozen40 = build_live_cases()

    # 1. Track A (P0.2): Primary Full PIEQ + Agent-Count Isolation on Held-Out 36
    agent_modes_heldout = (
        "1A_LocalTel",
        "1A_FullTel_SharedCtx",
        "1A_FullTel_DomainIsolated",
        "5A_PartTel",
        "5A_FullTel",
        "1A_FullTel_Unshielded",
        "5A_FullTel_Debate",
    )
    heldout_agent_isolation: Dict[str, Any] = {}
    for am in agent_modes_heldout:
        heldout_agent_isolation[am] = _evaluate_heldout_configuration(
            heldout, agent_mode=am, typed=True, pos_pack=True, refute_pack=True, shield_enabled=True, probes=True
        )

    # Also test agent-count isolation WITHOUT probes on heldout36 to isolate passive telemetry access!
    heldout_agent_isolation_no_probes: Dict[str, Any] = {}
    for am in ("1A_LocalTel", "1A_FullTel_SharedCtx", "1A_FullTel_DomainIsolated", "5A_PartTel", "5A_FullTel"):
        heldout_agent_isolation_no_probes[am] = _evaluate_heldout_configuration(
            heldout, agent_mode=am, typed=True, pos_pack=True, refute_pack=True, shield_enabled=True, probes=False
        )

    # 2. Track B (P1.1): Agent-Count Isolation on Frozen 40 (no omission + with omission)
    frozen40_agent_isolation: Dict[str, Any] = {}
    for am in ("1A_LocalTel", "1A_FullTel_SharedCtx", "1A_FullTel_DomainIsolated", "5A_PartTel", "5A_FullTel"):
        frozen40_agent_isolation[am] = _evaluate_frozen40_configuration(
            frozen40, agent_mode=am, typed=True, pos_pack=True, refute_pack=True, shield_enabled=True, probes=False,
            simulate_agent2_omission_on_last5=False,
        )

    # 3. Track C (P1.1): Complete 2^4 = 16-cell Mechanism-Isolation Factorial Ablation on Held-Out 36
    factorial_heldout: Dict[str, Any] = {}
    for typed_v in (0, 1):
        for pos_v in (0, 1):
            for ref_v in (0, 1):
                for prb_v in (0, 1):
                    cell_id = f"T{typed_v}_P{pos_v}_R{ref_v}_B{prb_v}"
                    res = _evaluate_heldout_configuration(
                        heldout,
                        agent_mode="5A_PartTel",
                        typed=bool(typed_v),
                        pos_pack=bool(pos_v),
                        refute_pack=bool(ref_v),
                        shield_enabled=True,
                        probes=bool(prb_v),
                    )
                    factorial_heldout[cell_id] = {
                        "config": res["config"],
                        "outcomes": res["outcomes"],
                        "proof_generation_funnel": res["proof_generation_funnel"],
                        "local_tcb_timings": res["local_tcb_timings"],
                    }
    # Explicit No-Shield control (shield_enabled=False) with typed=1, pos_pack=1, probes=0:
    no_shield_heldout = _evaluate_heldout_configuration(
        heldout, agent_mode="5A_PartTel", typed=True, pos_pack=True, refute_pack=False, shield_enabled=False, probes=False
    )
    factorial_heldout["T1_P1_NO_SHIELD_B0"] = {
        "config": no_shield_heldout["config"],
        "outcomes": no_shield_heldout["outcomes"],
        "proof_generation_funnel": no_shield_heldout["proof_generation_funnel"],
        "local_tcb_timings": no_shield_heldout["local_tcb_timings"],
    }

    # Complete 2^4 = 16-cell factorial on Frozen 40 (with leak-free schema probes):
    factorial_frozen40: Dict[str, Any] = {}
    for typed_v in (0, 1):
        for pos_v in (0, 1):
            for ref_v in (0, 1):
                for prb_v in (0, 1):
                    cell_id = f"T{typed_v}_P{pos_v}_R{ref_v}_B{prb_v}"
                    factorial_frozen40[cell_id] = _evaluate_frozen40_configuration(
                        frozen40,
                        agent_mode="5A_PartTel",
                        typed=bool(typed_v),
                        pos_pack=bool(pos_v),
                        refute_pack=bool(ref_v),
                        shield_enabled=True,
                        probes=bool(prb_v),
                        simulate_agent2_omission_on_last5=True,
                    )
    factorial_frozen40["T1_P1_NO_SHIELD_B0"] = _evaluate_frozen40_configuration(
        frozen40,
        agent_mode="5A_PartTel",
        typed=True,
        pos_pack=True,
        refute_pack=False,
        shield_enabled=False,
        probes=False,
        simulate_agent2_omission_on_last5=True,
    )

    return {
        "schema_version": "pieq-rev8-independent-eval-v1",
        "evaluation_mode": mode,
        "proposer_provenance_disclosure": (
            "Deterministic r4-calibrated software proposers ($0.00, 0 LLM tokens) running through "
            "the real PIEQ TCB and on-disk Tier E2 SQLite WAL (PRAGMA synchronous=FULL). "
            "NOT live LLM runs."
        ),
        "code_sha256": code_hashes,
        "heldout_manifest": heldout_manifest,
        "heldout_agent_count_isolation_with_probes": {
            k: {
                "config": v["config"],
                "outcomes": v["outcomes"],
                "proof_generation_funnel": v["proof_generation_funnel"],
                "local_tcb_timings": v["local_tcb_timings"],
                "per_case_results": v["per_case_results"],
            }
            for k, v in heldout_agent_isolation.items()
        },
        "heldout_agent_count_isolation_no_probes": {
            k: {
                "config": v["config"],
                "outcomes": v["outcomes"],
                "proof_generation_funnel": v["proof_generation_funnel"],
                "local_tcb_timings": v["local_tcb_timings"],
            }
            for k, v in heldout_agent_isolation_no_probes.items()
        },
        "frozen40_agent_count_isolation_no_probes": frozen40_agent_isolation,
        "heldout_factorial_16_plus_noshield": factorial_heldout,
        "frozen40_factorial_16_plus_noshield": factorial_frozen40,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("frozen-rev7", "post-p13"), required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    out_path = args.output or (
        REPO_ROOT / "results" / (
            "rev8_heldout_frozen_rev7.json" if args.mode == "frozen-rev7" else "rev8_heldout_post_p13.json"
        )
    )
    report = run_all(args.mode)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} (mode={args.mode})")

    print("\n=== HELD-OUT 36: AGENT-COUNT ISOLATION (WITH PROBES) ===")
    for am, data in report["heldout_agent_count_isolation_with_probes"].items():
        o = data["outcomes"]
        f = data["proof_generation_funnel"]
        print(
            f"  {am:28s}: mitig={o['actionable_safe_mitigations']}/{o['actionable_total']} "
            f"fb={o['actionable_false_blocks']} "
            f"unjust_fp={o['unjustified_false_precondition_admissions']}/{o['unjustified_total']} "
            f"mdefer={o['mandatory_defer_correct']}/{o['mandatory_defer_total']} "
            f"unsafe_in={o['total_unsafe_invariant_violations_within_assumptions']} "
            f"ooa_unsafe={o['out_of_assumption_admitted_unsafe']}/{o['out_of_assumption_total']} "
            f"prb={o['probes_executed']}({o['probes_effective']}) "
            f"p50={data['local_tcb_timings']['end_to_end_local_tcb_ms']['p50_ms']}ms"
        )

    print("\n=== HELD-OUT 36: NON-PASSING CASES UNDER 5A_PartTel (WITH PROBES) ===")
    for c in report["heldout_agent_count_isolation_with_probes"]["5A_PartTel"]["per_case_results"]:
        if c["verdict"] not in {"SAFE_CORRECT_MITIGATION", "CORRECT_DEFERRAL_UNJUSTIFIED", "CORRECT_MANDATORY_DEFERRAL"}:
            print(f"  {c['case_id']} ({c['archetype']}, gt={c['ground_truth_class']}): admitted={c['admitted']} action={c['action']} reason={c['reason']} -> {c['verdict']}")

    print("\n=== HELD-OUT 36: 2^4 MECHANISM-ISOLATION FACTORIAL ===")
    for cid, data in report["heldout_factorial_16_plus_noshield"].items():
        o = data["outcomes"]
        print(
            f"  {cid:18s}: mitig={o['actionable_safe_mitigations']}/{o['actionable_total']} "
            f"fb={o['actionable_false_blocks']} "
            f"unsafe_in={o['total_unsafe_invariant_violations_within_assumptions']} "
            f"fp_in={o['total_false_precondition_admissions_within_assumptions']} "
            f"mdefer={o['mandatory_defer_correct']}/{o['mandatory_defer_total']} "
            f"ooa={o['out_of_assumption_admitted_unsafe']}/{o['out_of_assumption_total']} "
            f"prb={o['probes_executed']}({o['probes_effective']})"
        )


if __name__ == "__main__":
    main()
