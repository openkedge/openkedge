#!/usr/bin/env python3
"""
PIEQ Revision 10 — EXP-01/EXP-02: Fully Hardened Live Evaluation
(mandatory trusted temporal-policy registry + full PIEQ + Tier E2 boundary).

Conditions (same prompts, typed schema, verifier, probe budget, and trace fields as the
Revision 8/9 pipeline — the ONLY change vs. Rev9 live Config E is the mandatory
temporal-policy registry and, for S1/H1, the declared agent roster/allocation):

  E1 / M1  five-agent heterogeneous CLI ensemble (3x Claude + 2x GPT), partitioned
           telemetry (aggregate identical to S1), hardened registry  [EXP-01 + EXP-02 M1]
  S1       one Claude Sonnet agent, ALL telemetry handles, hardened registry [EXP-02]
  H1       five homogeneous Claude Sonnet agents, partitioned telemetry,
           hardened registry                                            [EXP-02 optional]

Trusted temporal-policy derivation (per incident; NEVER from oracle labels):
  - If the case's trusted action metadata declares commit-time-truth semantics
    (`is_commit_time_truth_action`), EVERY mutation type of that incident is registered
    COMMIT_TIME_SENSITIVE with trusted predicates = declared required_commit_predicates
    UNION the target-maintained live-predicate keys. This injects the predicate that the
    legacy H30 configuration silently omitted.
  - Otherwise every mutation type is SNAPSHOT_TRIGGERED (no unnecessary revalidation;
    historical security-event remediation stays actionable).
  - The registry is exhaustive per incident: a mutation without a policy fails closed.
  - `temporal_policies=None` legacy mode is NOT used here; the Rev9 live Config E trace
    is preserved separately as the legacy-mode counterexample (E0).

Evidence tier: cli_wrapper_live_unpinned (same disclosure as run_rev9_cli_live_eval.py).
Ground-truth labels are never passed to agents, probes, or the decision pipeline.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_rev8_pinned_live_eval as rev8
from run_rev9_cli_live_eval import CLI_MANIFEST, ResilientCLI, validate_cli_manifest

from pieq.core import (
    CrossRootExposureLedger,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
)
from pieq.hardened import (
    HardenedAdmissionController,
    HardenedPIEQConfig,
    MutationTemporalPolicy,
    TargetRefinementAttestation,
)
from pieq.heldout_cases import HELDOUT_COLLECTOR_KEY, build_heldout_cases
from pieq.live_protocol import _extract_json_text
from pieq.probe_loop import ProbeBudget
from pieq.typed_pipeline import TypedCertificateBuilder, TypedVocabulary


def build_rev10_temporal_policies(case) -> tuple[MutationTemporalPolicy, ...]:
    """Exhaustive per-incident trusted registry (see module docstring)."""
    actions = sorted({a for w in case.worlds for a in w.safe_mutations} - {"defer", "probe"})
    if case.is_commit_time_truth_action:
        preds = tuple(sorted(set(case.required_commit_predicates)
                             | set(case.live_predicates_at_commit.keys())))
        return tuple(
            MutationTemporalPolicy(action=a, mode="COMMIT_TIME_SENSITIVE",
                                   trusted_commit_predicates=preds)
            for a in actions
        )
    return tuple(MutationTemporalPolicy(action=a, mode="SNAPSHOT_TRIGGERED") for a in actions)


CONDITIONS = {
    "E1": {"instances": "all", "allocation": "partitioned",
           "note": "EXP-01 hardened five-agent heterogeneous (doubles as EXP-02 M1)"},
    "S1": {"instances": ["agent_0"], "allocation": "full",
           "note": "EXP-02 single Sonnet, all telemetry handles"},
    "H1": {"instances": "sonnet_x5", "allocation": "partitioned",
           "note": "EXP-02 optional five homogeneous Sonnet agents"},
}


def build_manifest(condition: str) -> dict:
    m = json.loads(json.dumps(CLI_MANIFEST))
    m["run_id"] = f"pieq-rev10-hardened-live-{condition}-v1"
    m["hardened_temporal_policies"] = "mandatory_per_incident_registry"
    if condition == "S1":
        m["instances"] = [m["instances"][0]]
    elif condition == "H1":
        base = m["instances"][0]
        m["instances"] = [dict(base, agent_id=f"agent_{i}") for i in range(5)]
        # Single family is intentional for H1 (homogeneous-ensemble control):
        m["homogeneous_control"] = True
    return m


def validate_manifest(manifest):
    if manifest.get("homogeneous_control"):
        info = {
            "manifest_sha256": rev8._canonical_sha256(manifest),
            "provider_families": sorted({i["family"] for i in manifest["instances"]}),
            "providers": sorted({i["provider"] for i in manifest["instances"]}),
            "pinned_models": {i["agent_id"]: f"{i['provider']}:{i['model']}@{i['snapshot']}"
                              for i in manifest["instances"]},
        }
        return info
    return validate_cli_manifest(manifest)


def load_cases(cases_module: str | None):
    if not cases_module:
        return build_heldout_cases()
    import importlib
    mod = importlib.import_module(cases_module)
    return mod.build_cases()


def prepare_cases(condition: str, cases_module: str | None = None):
    cases = load_cases(cases_module)
    if CONDITIONS[condition]["allocation"] == "full":
        cases = [
            dataclasses.replace(
                c,
                agent_allocation={
                    "agent_0": tuple(sorted({h for hs in c.agent_allocation.values() for h in hs}))
                },
            )
            for c in cases
        ]
    return cases


def run_condition(condition: str, *, limit: int, trace_path: Path,
                  cases_module: str | None = None) -> dict:
    manifest = build_manifest(condition)
    m_info = validate_manifest(manifest)
    cases = prepare_cases(condition, cases_module)
    if limit > 0:
        cases = cases[:limit]

    providers = {name: ResilientCLI(name) for name in {i["provider"] for i in manifest["instances"]}}
    for p in providers.values():
        p.preflight()

    sampling = manifest["sampling"]
    compat_manifest = {
        "temperature": sampling["temperature"], "seed": sampling.get("seed"),
        "max_output_tokens": sampling["max_output_tokens"],
        "timeout_seconds": manifest.get("timeout_seconds", 300),
    }
    max_retries = int(manifest.get("max_retries_typed", 2))
    spending_cap = float(manifest.get("spending_cap_usd", 40.0))
    max_calls = int(manifest.get("max_calls", 4000))

    total_calls = 0
    total_cost = 0.0
    admitted_cnt = deferred_cnt = unsafe_cnt = 0
    t_run0 = time.perf_counter()

    trace_path.parent.mkdir(parents=True, exist_ok=True)

    def _emit(rec):
        with trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str) + "\n")

    _emit({
        "record_type": "run_header",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": manifest["run_id"],
        "condition": condition,
        "condition_note": CONDITIONS[condition]["note"],
        "manifest_sha256": m_info["manifest_sha256"],
        "schema_sha256": rev8.TYPED_SCHEMA_SHA256,
        "sampling": sampling,
        "pinned_models": m_info["pinned_models"],
        "case_ids": [c.case_id for c in cases],
        "protocol": "full_pieq_hardened_temporal_registry",
        "evidence_tier": "cli_wrapper_live_unpinned",
    })

    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            c_t0 = time.perf_counter()
            atms, verifier, by_id = case.build_atms()
            cand_actions = tuple(sorted({a for w in case.worlds for a in w.safe_mutations}))
            world_ids = tuple(sorted(w.world_id for w in case.worlds))
            alloc_map = {aid: frozenset(hs) for aid, hs in case.agent_allocation.items()}
            # S1/H1 rosters may not match the case's native agent IDs; remap partitioned
            # allocations onto the manifest roster deterministically when needed:
            roster = [i["agent_id"] for i in manifest["instances"]]
            if set(roster) != set(alloc_map.keys()):
                if len(roster) == 1:
                    union = frozenset(h for hs in alloc_map.values() for h in hs)
                    alloc_map = {roster[0]: union}
                else:
                    native = [alloc_map[k] for k in sorted(alloc_map.keys())]
                    alloc_map = {aid: native[idx % len(native)] for idx, aid in enumerate(roster)}
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

            agent_attempts_log = []
            requested_probes_all = []
            for inst in manifest["instances"]:
                aid = inst["agent_id"]
                prov = providers[inst["provider"]]
                prompt_payload = vocab.agent_prompt_payload(aid)
                feedback_history = []
                for attempt in range(max_retries + 1):
                    if total_calls >= max_calls or total_cost >= spending_cap:
                        raise RuntimeError("Call or spending cap reached")
                    prompt_str = rev8._build_typed_prompt(
                        prompt_payload, feedback_history, protocol="full_pieq"
                    )
                    call_t0 = time.perf_counter()
                    reply = prov.complete(inst, prompt_str, compat_manifest)
                    call_ms = round((time.perf_counter() - call_t0) * 1000.0, 3)
                    total_calls += 1
                    if not isinstance(reply.model, str) or not reply.model.strip():
                        raise RuntimeError("Empty model identity")
                    call_cost = rev8._estimate_cost_usd(inst, reply.usage or {})
                    total_cost = round(total_cost + call_cost, 6)
                    try:
                        parsed = json.loads(_extract_json_text(reply.text))
                    except Exception as exc:
                        parsed = f"MALFORMED_JSON:{exc}"
                    rep = builder.validate_and_register_proposal(
                        aid, parsed, round_tag=f"hardened:try_{attempt}"
                    )
                    rejected = [o for o in rep.outcomes if not o.accepted]
                    requested_probes_all.extend(rep.requested_probes)
                    agent_attempts_log.append({
                        "agent_id": aid,
                        "provider": inst["provider"],
                        "declared_model": inst["model"],
                        "server_reported_model_id": reply.model,
                        "prompt_sha256": hashlib.sha256(prompt_str.encode()).hexdigest(),
                        "schema_sha256": rev8.TYPED_SCHEMA_SHA256,
                        "assigned_observation_handles": sorted(alloc_map[aid]),
                        "attempt": attempt + 1,
                        "proposed_action": rep.proposed_action,
                        "raw_reply": reply.text,
                        "usage": reply.usage,
                        "cost_usd": call_cost,
                        "elapsed_ms": call_ms,
                        "schema_rejected_proposal": rep.schema_rejected_proposal,
                        "proposal_error_category": rep.proposal_error_category,
                        "derivation_outcomes": [asdict(o) for o in rep.outcomes],
                    })
                    if not rep.schema_rejected_proposal and not rejected:
                        break
                    feedback_history.append({
                        "attempt": attempt + 1,
                        "proposal_error": (
                            {"category": rep.proposal_error_category,
                             "detail": rep.proposal_error_detail}
                            if rep.schema_rejected_proposal else None
                        ),
                        "rejected_derivations": [
                            {"rule_id": o.rule_id, "evidence_handles": list(o.evidence_handles),
                             "category": o.primary_category, "detail": o.detail}
                            for o in rejected
                        ],
                    })

            # Hardened admission + Tier E2 execution with MANDATORY temporal registry:
            policies = build_rev10_temporal_policies(case)
            db_path = os.path.join(tmp, f"hardened_{condition}_{case.case_id}.db")
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
            ledger = CrossRootExposureLedger({case.exposure_domain: case.exposure_cap})
            if case.preexisting_unknown_exposure > 0:
                ledger.admit_and_reserve("ik_pre", "op_pre", "pre_op",
                                         case.exposure_domain, case.preexisting_unknown_exposure)
                ledger.mark_timeout_or_cancel_race(case.exposure_domain, "op_pre", state="UNKNOWN")
            cfg = HardenedPIEQConfig(
                resource_id=case.resource_id, epoch=case.epoch,
                manifest_hash=case.manifest_hash, auth_epoch=case.auth_epoch_cert,
                signing_key=HELDOUT_COLLECTOR_KEY, f_fault_budget=case.f_budget,
                k_a=case.k_a, require_positive_packing=True,
                temporal_policies=policies,
            )
            att = TargetRefinementAttestation(
                case.resource_id, "E2_SHARED_ATOMIC", case.refinement_regime,
                case.manifest_hash, case.complete_writer_mediation,
            ).with_signature(HELDOUT_COLLECTOR_KEY)
            ctrl = HardenedAdmissionController(cfg, att, ledger, target)
            snap = SnapshotContext(case.snapshot_version, case.snapshot_version,
                                   case.snapshot_version, True, True)
            dec = ctrl.evaluate_and_execute(
                atms=atms, worlds=case.worlds, candidate_actions=cand_actions,
                incident_class=case.incident_domain, exposure_domain=case.exposure_domain,
                exposure_cost=1, op_id=f"op_{case.case_id}", issuance_key=f"ik_{case.case_id}",
                snapshot_context=snap, now_time=case.now_time,
                auth_valid_until=case.auth_valid_until,
                required_predecessor_op_ids=case.required_predecessor_op_ids,
                required_commit_predicates=case.required_commit_predicates,
                available_probes=list(case.probe_catalog),
                probe_executor=case.make_leak_free_probe_executor(verifier),
                probe_budget=ProbeBudget(max_invocations=4, max_cost_units=6.0, max_load_units=6),
                override_expected_admission_revs=(
                    {case.exposure_domain: 999} if case.concurrent_cas_stale else None
                ),
            )
            physical_seq_after = target.current_seq()
            probe_responses_log = (
                [asdict(r) for r in dec.probe_result.executed_probes]
                if dec.probe_result is not None else []
            )
            admitted = dec.admitted
            chosen = dec.action if dec.admitted else "defer"
            if admitted:
                admitted_cnt += 1
                if chosen not in case.true_world_safe_actions:
                    unsafe_cnt += 1
            else:
                deferred_cnt += 1

            # EXP-01 decomposed outcome ledger (mechanism-attribution record):
            agents_proposing_mutation = sorted({
                a["proposed_action"] for a in agent_attempts_log
                if a.get("proposed_action") not in (None, "defer", "probe")
            })
            decomposition = {
                "agents_proposed_mutations": agents_proposing_mutation,
                "action_packing": dict(dec.action_packing),
                "positive_evidence_sufficient": any(
                    v >= case.k_a for a, v in dec.action_packing.items()
                ),
                "surviving_worlds": sorted(dec.surviving_worlds),
                "reached_target_boundary": dec.execution_receipt is not None,
                "target_receipt_status": (
                    dec.execution_receipt.status if dec.execution_receipt else None
                ),
                "temporal_policy_registry": {p.action: p.canonical_string() for p in policies},
                "certificate_temporal_policy": (
                    dec.certificate.temporal_policy if dec.certificate else None
                ),
                "physical_commit": bool(
                    dec.execution_receipt is not None and dec.execution_receipt.committed
                ),
                "target_seq_after": physical_seq_after,
            }
            target.conn.close()

            _emit({
                "record_type": "case_evaluation",
                "protocol": "full_pieq_hardened_temporal_registry",
                "condition": condition,
                "case_id": case.case_id,
                "incident_domain": case.incident_domain,
                "observation_assignment_by_agent": {k: sorted(v) for k, v in alloc_map.items()},
                "agent_attempts": agent_attempts_log,
                "probe_requests_from_agents": sorted(set(requested_probes_all)),
                "completed_probe_responses": probe_responses_log,
                "accepted_certificate": asdict(dec.certificate) if dec.certificate else None,
                "admission_outcome": {
                    "admitted": admitted,
                    "action": chosen,
                    "reason": dec.reason,
                    "safe_in_true_world": (chosen == "defer") or (chosen in case.true_world_safe_actions),
                },
                "exp01_decomposition": decomposition,
                "end_to_end_duration_ms": round((time.perf_counter() - c_t0) * 1000.0, 3),
            })

    summary = {
        "schema_version": "pieq-rev10-hardened-live-summary-v1",
        "run_kind": "cli_wrapper_live_unpinned",
        "condition": condition,
        "manifest_sha256": m_info["manifest_sha256"],
        "schema_sha256": rev8.TYPED_SCHEMA_SHA256,
        "models": m_info["pinned_models"],
        "cases_evaluated": len(cases),
        "admitted_count": admitted_cnt,
        "deferred_count": deferred_cnt,
        "raw_unsafe_count": unsafe_cnt,
        "api_calls": total_calls,
        "api_cost_usd_cli_reported": round(total_cost, 6),
        "wall_clock_seconds": round(time.perf_counter() - t_run0, 3),
        "provider_failures": {n: p.failures for n, p in providers.items()},
        "trace_path": str(trace_path),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    trace_path.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=sorted(CONDITIONS), required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--trace", type=Path, default=None)
    parser.add_argument("--cases-module", default=None,
                        help="Module exposing build_cases() (default: heldout36)")
    args = parser.parse_args()
    trace = args.trace or (REPO_ROOT / "results" / f"rev10_hardened_live_traces_{args.condition}.jsonl")
    print(json.dumps(run_condition(args.condition, limit=args.limit, trace_path=trace,
                                   cases_module=args.cases_module), indent=2))


if __name__ == "__main__":
    main()
