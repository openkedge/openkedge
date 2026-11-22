#!/usr/bin/env python3
"""
PIEQ Revision 9 P1.3 — Independent Confirmation Evaluation of the H31 (Rev8 P1.3) Repair.

Context: `H31` was discovered on the heldout36 set and the implementation was repaired
afterward, so the repaired result on heldout36 is REGRESSION evidence, not untouched
confirmation evidence. This runner provides a NEW, previously unseen adversarial
confirmation set (C01-C12) authored AFTER the Revision 9 implementation freeze
(`results/rev9_frozen_hashes.json`), exercising:

  - Cross-round observation version changes (C01)
  - Mixed snapshots that appear internally valid (C02)
  - Target commits during probing, between rounds (C03)
  - Manifest rotation between probe rounds (C04)
  - Same-domain evidence reused under different handles (C05)
  - Newly discovered nogoods invalidating earlier PROBE-derived proofs (C06)
  - Stale evidence surviving retries (C07)
  - Concurrent state change between evidence collection and admission CAS (C08)
  - Positive controls: probe-recoverable and passive actionable cases (C09, C10)
  - Rev9 P1.2 integration: commit-time-sensitive dip blocked at the atomic
    boundary (C11), snapshot-triggered historical event still actionable (C12)

Integrity rules enforced by construction:
  - Oracle labels live ONLY in the sealed `ORACLE` map and are consulted exclusively by
    `score()` AFTER the pipeline produced its outcome. Nothing in any case constructor
    reads ORACLE.
  - No case was used to tune the implementation: the implementation hash freeze precedes
    this file's first execution, and this evaluator contains no per-case special-casing.
  - All failures are reported verbatim in `results/rev9_confirmation_results.json`.

Usage: python3 scripts/run_rev9_confirmation_eval.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    CrossRootExposureLedger,
    DeterministicRuleVerifier,
    FaultWorld,
    InferenceRule,
    InvariantConstraint,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
)
from pieq.hardened import (
    HardenedAdmissionController,
    HardenedPIEQConfig,
    MutationTemporalPolicy,
    TargetRefinementAttestation,
)
from pieq.probe_loop import (
    DiagnosticProbeSpec,
    ProbeBudget,
    run_active_diagnostic_probe_loop,
)

KEY = b"rev9-confirmation-key"
RESOURCE = "dns_anycast_cluster"
MANIFEST = "mh_rev9_conf"
EPOCH = 7
BASE_VER = 70

# =============================================================================
# Sealed oracle labels — consulted ONLY by score(), never by case constructors.
# =============================================================================
ORACLE: Dict[str, Dict[str, Any]] = {
    "C01": {"class": "mandatory_deferral", "expected_action": None},
    "C02": {"class": "mandatory_deferral", "expected_action": None},
    "C03": {"class": "mandatory_deferral", "expected_action": None},
    "C04": {"class": "mandatory_deferral", "expected_action": None},
    "C05": {"class": "mandatory_deferral", "expected_action": None},
    "C06": {"class": "mandatory_deferral", "expected_action": None},
    "C07": {"class": "mandatory_deferral", "expected_action": None},
    "C08": {"class": "mandatory_deferral", "expected_action": None},
    "C09": {"class": "actionable", "expected_action": "reroute_anycast"},
    "C10": {"class": "actionable", "expected_action": "drain_node"},
    "C11": {"class": "mandatory_deferral", "expected_action": None},
    "C12": {"class": "actionable", "expected_action": "drain_node"},
}


def _mk_bound(
    verifier: DeterministicRuleVerifier,
    bit: int,
    aid: str,
    ch: str,
    dom: str,
    prop: str,
    *,
    obs_ver: int = BASE_VER,
    manifest: str = MANIFEST,
) -> BoundPrimitiveAssumption:
    sig = verifier.sign_telemetry(
        assumption_id=aid,
        channel_id=ch,
        failure_domain=dom,
        epoch_revision=EPOCH,
        proposition=prop,
        observation_version=obs_ver,
        bit_index=bit,
        resource_id=RESOURCE,
        manifest_hash=manifest,
    )
    return BoundPrimitiveAssumption(
        bit_index=bit,
        assumption_id=aid,
        channel_id=ch,
        failure_domain=dom,
        epoch_revision=EPOCH,
        proposition=prop,
        signature=sig,
        observation_version=obs_ver,
        resource_id=RESOURCE,
        manifest_hash=manifest,
    )


def _dns_rules() -> List[InferenceRule]:
    return [
        InferenceRule("r_bgp", frozenset({"obs:bgp_withdrawn"}), "act:reroute_anycast"),
        InferenceRule("r_edge", frozenset({"obs:edge_unreachable"}), "act:reroute_anycast"),
        InferenceRule("r_resolver", frozenset({"obs:resolver_timeout"}), "act:reroute_anycast"),
        InferenceRule("r_ref_bgp", frozenset({"obs:bgp_withdrawn"}), "refute:s_flap"),
        InferenceRule("r_ref_edge", frozenset({"obs:edge_unreachable"}), "refute:s_flap"),
        InferenceRule("r_ref_resolver", frozenset({"obs:resolver_timeout"}), "refute:s_flap"),
    ]


def _dns_worlds() -> List[FaultWorld]:
    return [
        FaultWorld("s_pop_down", frozenset(), frozenset({"reroute_anycast", "defer", "probe"})),
        FaultWorld("s_flap", frozenset(), frozenset({"defer", "probe"})),
    ]


def _mk_engine(rules, invariants=()) -> Tuple[DeterministicRuleVerifier, BoundedATMSEngine]:
    verifier = DeterministicRuleVerifier(rules, list(invariants), secret_key=KEY)
    atms = BoundedATMSEngine(
        verifier,
        expected_epoch=EPOCH,
        expected_resource_id=RESOURCE,
        expected_manifest_hash=MANIFEST,
    )
    return verifier, atms


def _probe(pid: str, col: str, dom: str, outcome_down: str, outcome_flap: str) -> DiagnosticProbeSpec:
    return DiagnosticProbeSpec(
        probe_id=pid,
        collector_id=col,
        failure_domain=dom,
        read_only=True,
        safety_contract_verified=True,
        world_outcome_map={"s_pop_down": outcome_down, "s_flap": outcome_flap},
        target_actions=("reroute_anycast",),
    )


def _run_loop(verifier, atms, probes, executor, *, k_a=3, f=2, version_observer=None, manifest_observer=None):
    return run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=_dns_worlds(),
        candidate_actions=("defer", "probe", "reroute_anycast"),
        incident_class="dns_pop_failover",
        f_fault_budget=f,
        k_a=k_a,
        available_probes=probes,
        probe_executor=executor,
        budget=ProbeBudget(max_invocations=4, max_cost_units=8.0, max_time_ms=2000.0, max_load_units=8),
        require_consistent_observation_version=True,
        expected_snapshot_version=BASE_VER,
        version_observer=version_observer,
        manifest_observer=manifest_observer,
    )


def _seed_passive(verifier, atms) -> None:
    """One passive observation (domain D_bgp) supporting reroute, consistent v=70."""
    e0 = _mk_bound(verifier, 0, "a_bgp", "ch_bgp", "D_bgp", "obs:bgp_withdrawn")
    assert atms.register_assumption(e0)
    assert atms.add_verified_justification("ag0", "ag0:r0", "act:reroute_anycast", 1 << 0, "r_bgp")
    assert atms.add_verified_justification("ag0", "ag0:r0", "refute:s_flap", 1 << 0, "r_ref_bgp")


# =============================================================================
# Case constructors — each returns (outcome_record) with NO oracle access.
# =============================================================================

def case_C01_cross_round_version_drift() -> Dict[str, Any]:
    """Round-1 probe returns v=70 (consistent); round-2 probe returns v=71."""
    verifier, atms = _mk_engine(_dns_rules())
    _seed_passive(verifier, atms)
    probes = [
        _probe("p_edge", "col_edge", "D_edge", "obs:edge_unreachable", "obs:edge_ok"),
        _probe("p_resolver", "col_res", "D_resolver", "obs:resolver_timeout", "obs:resolver_ok"),
    ]
    calls = {"n": 0}

    def executor(spec: DiagnosticProbeSpec, bit: int) -> BoundPrimitiveAssumption:
        calls["n"] += 1
        ver = BASE_VER if calls["n"] == 1 else BASE_VER + 1
        prop = spec.world_outcome_map["s_pop_down"]
        return _mk_bound(verifier, bit, f"probe_{spec.probe_id}", spec.collector_id, spec.failure_domain, prop, obs_ver=ver)

    res = _run_loop(verifier, atms, probes, executor)
    return _loop_record(res)


def case_C02_mixed_internally_valid_snapshots() -> Dict[str, Any]:
    """Two passive complete proofs, each internally consistent, at v=70 and v=71."""
    verifier, atms = _mk_engine(_dns_rules())
    _seed_passive(verifier, atms)  # v=70, D_bgp
    e1 = _mk_bound(verifier, 1, "a_edge", "ch_edge", "D_edge", "obs:edge_unreachable", obs_ver=BASE_VER + 1)
    assert atms.register_assumption(e1)
    assert atms.add_verified_justification("ag1", "ag1:r0", "act:reroute_anycast", 1 << 1, "r_edge")
    assert atms.add_verified_justification("ag1", "ag1:r0", "refute:s_flap", 1 << 1, "r_ref_edge")
    res = _run_loop(verifier, atms, [], None, k_a=2, f=1)
    return _loop_record(res)


def case_C03_commit_between_probe_rounds() -> Dict[str, Any]:
    """An external coordinator commits on the target between probe round 1 and round 2."""
    verifier, atms = _mk_engine(_dns_rules())
    _seed_passive(verifier, atms)
    probes = [
        _probe("p_edge", "col_edge", "D_edge", "obs:edge_unreachable", "obs:edge_ok"),
        _probe("p_resolver", "col_res", "D_resolver", "obs:resolver_timeout", "obs:resolver_ok"),
    ]
    seq = {"val": 11}
    calls = {"n": 0}

    def executor(spec: DiagnosticProbeSpec, bit: int) -> BoundPrimitiveAssumption:
        calls["n"] += 1
        if calls["n"] == 2:
            seq["val"] += 1  # concurrent target commit lands while round-2 probe in flight
        prop = spec.world_outcome_map["s_pop_down"]
        return _mk_bound(verifier, bit, f"probe_{spec.probe_id}", spec.collector_id, spec.failure_domain, prop)

    res = _run_loop(verifier, atms, probes, executor, version_observer=lambda: seq["val"])
    return _loop_record(res)


def case_C04_manifest_rotation_between_rounds() -> Dict[str, Any]:
    """Topology manifest rotates after the first probe round."""
    verifier, atms = _mk_engine(_dns_rules())
    _seed_passive(verifier, atms)
    probes = [
        _probe("p_edge", "col_edge", "D_edge", "obs:edge_unreachable", "obs:edge_ok"),
        _probe("p_resolver", "col_res", "D_resolver", "obs:resolver_timeout", "obs:resolver_ok"),
    ]
    manifest = {"val": MANIFEST}
    calls = {"n": 0}

    def executor(spec: DiagnosticProbeSpec, bit: int) -> BoundPrimitiveAssumption:
        calls["n"] += 1
        if calls["n"] == 2:
            manifest["val"] = "mh_rev9_conf_ROTATED"
        prop = spec.world_outcome_map["s_pop_down"]
        return _mk_bound(verifier, bit, f"probe_{spec.probe_id}", spec.collector_id, spec.failure_domain, prop)

    res = _run_loop(verifier, atms, probes, executor, manifest_observer=lambda: manifest["val"])
    return _loop_record(res)


def case_C05_same_domain_reused_under_different_handles() -> Dict[str, Any]:
    """Two probes expose the SAME physical domain (D_edge) under distinct collectors
    and assumption IDs; with only two distinct physical domains total, k_a=3 must fail."""
    verifier, atms = _mk_engine(_dns_rules())
    _seed_passive(verifier, atms)
    probes = [
        _probe("p_edge_a", "col_edge_a", "D_edge", "obs:edge_unreachable", "obs:edge_ok"),
        _probe("p_edge_b", "col_edge_b", "D_edge", "obs:resolver_timeout", "obs:resolver_ok"),
    ]

    def executor(spec: DiagnosticProbeSpec, bit: int) -> BoundPrimitiveAssumption:
        prop = spec.world_outcome_map["s_pop_down"]
        return _mk_bound(verifier, bit, f"probe_{spec.probe_id}", spec.collector_id, spec.failure_domain, prop)

    res = _run_loop(verifier, atms, probes, executor)
    return _loop_record(res)


def case_C06_late_nogood_invalidates_earlier_probe_proof() -> Dict[str, Any]:
    """Round-2 probe observation conflicts (trusted P_sys invariant) with the ROUND-1
    probe observation, invalidating the earlier probe-derived proof."""
    inv = InvariantConstraint(
        "inv_edge_vs_edge_serving",
        frozenset({"obs:edge_unreachable", "obs:edge_actively_serving"}),
        "An edge PoP cannot be simultaneously unreachable and actively serving.",
    )
    verifier, atms = _mk_engine(_dns_rules(), invariants=[inv])
    _seed_passive(verifier, atms)
    probes = [
        _probe("p_edge", "col_edge", "D_edge", "obs:edge_unreachable", "obs:edge_ok"),
        _probe("p_edge_oob", "col_oob", "D_oob", "obs:resolver_timeout", "obs:edge_actively_serving"),
    ]
    calls = {"n": 0}

    def executor(spec: DiagnosticProbeSpec, bit: int) -> BoundPrimitiveAssumption:
        calls["n"] += 1
        if calls["n"] == 1:
            prop = "obs:edge_unreachable"
        else:
            prop = "obs:edge_actively_serving"  # contradicts round-1 probe via P_sys
        return _mk_bound(verifier, bit, f"probe_{spec.probe_id}", spec.collector_id, spec.failure_domain, prop)

    res = _run_loop(verifier, atms, probes, executor)
    return _loop_record(res)


def case_C07_stale_evidence_surviving_retry() -> Dict[str, Any]:
    """Probe retry returns the identical stale observation (same assumption id, v=70)
    while the independent version observer shows the plant advanced."""
    verifier, atms = _mk_engine(_dns_rules())
    _seed_passive(verifier, atms)
    probes = [
        _probe("p_edge", "col_edge", "D_edge", "obs:edge_unreachable", "obs:edge_ok"),
        _probe("p_resolver", "col_res", "D_resolver", "obs:resolver_timeout", "obs:resolver_ok"),
    ]
    seq = {"val": 11}

    def executor(spec: DiagnosticProbeSpec, bit: int) -> BoundPrimitiveAssumption:
        seq["val"] += 1  # the plant advances on EVERY probe round
        # ... yet the collector cache keeps serving the same stale v=70 snapshot:
        prop = spec.world_outcome_map["s_pop_down"]
        return _mk_bound(verifier, bit, "a_stale_cache", spec.collector_id, spec.failure_domain, prop, obs_ver=BASE_VER)

    res = _run_loop(verifier, atms, probes, executor, version_observer=lambda: seq["val"])
    return _loop_record(res)


def case_C08_concurrent_change_between_evidence_and_admission() -> Dict[str, Any]:
    """Admission-revision CAS: coordinator A captured admission revisions, then a
    concurrent operation commits through the ledger before A admits."""
    decision, target = _run_hardened_case(
        temporal_policies=(MutationTemporalPolicy(action="drain_node", mode="SNAPSHOT_TRIGGERED"),),
        stale_cas=True,
    )
    rec = _hardened_record(decision, target)
    target.conn.close()
    return rec


def case_C09_actionable_probe_recoverable() -> Dict[str, Any]:
    """Positive control: passive D_bgp + two genuinely independent probe domains,
    all consistent at v=70 -> should certify reroute_anycast with k_a=3."""
    verifier, atms = _mk_engine(_dns_rules())
    _seed_passive(verifier, atms)
    probes = [
        _probe("p_edge", "col_edge", "D_edge", "obs:edge_unreachable", "obs:edge_ok"),
        _probe("p_resolver", "col_res", "D_resolver", "obs:resolver_timeout", "obs:resolver_ok"),
    ]

    def executor(spec: DiagnosticProbeSpec, bit: int) -> BoundPrimitiveAssumption:
        prop = spec.world_outcome_map["s_pop_down"]
        return _mk_bound(verifier, bit, f"probe_{spec.probe_id}", spec.collector_id, spec.failure_domain, prop)

    res = _run_loop(verifier, atms, probes, executor)
    return _loop_record(res)


def case_C10_actionable_passive_full_pipeline() -> Dict[str, Any]:
    """Positive control through the FULL hardened controller (Tier E2 SQLite WAL)."""
    decision, target = _run_hardened_case(
        temporal_policies=(MutationTemporalPolicy(action="drain_node", mode="SNAPSHOT_TRIGGERED"),),
    )
    rec = _hardened_record(decision, target)
    target.conn.close()
    return rec


def case_C11_commit_time_sensitive_dip_blocked() -> Dict[str, Any]:
    """Rev9 P1.2 integration: COMMIT_TIME_SENSITIVE policy; the trusted predicate is
    false at the physical effect point -> must be blocked at the atomic boundary."""
    decision, target = _run_hardened_case(
        temporal_policies=(
            MutationTemporalPolicy(
                action="drain_node",
                mode="COMMIT_TIME_SENSITIVE",
                trusted_commit_predicates=("node_load_above_threshold",),
            ),
        ),
        predicate_value=False,
    )
    rec = _hardened_record(decision, target)
    target.conn.close()
    return rec


def case_C12_snapshot_triggered_historical_still_actionable() -> Dict[str, Any]:
    """Rev9 P1.2 integration: SNAPSHOT_TRIGGERED policy; the transient symptom
    predicate is false at commit time but the action must still commit."""
    decision, target = _run_hardened_case(
        temporal_policies=(MutationTemporalPolicy(action="drain_node", mode="SNAPSHOT_TRIGGERED"),),
        predicate_value=False,
    )
    rec = _hardened_record(decision, target)
    target.conn.close()
    return rec


# =============================================================================
# Hardened end-to-end harness (used by C08, C10, C11, C12)
# =============================================================================

def _run_hardened_case(*, temporal_policies, stale_cas: bool = False, predicate_value: Optional[bool] = None):
    rules = [
        InferenceRule("r_a0", frozenset({"obs:sig0"}), "act:drain_node"),
        InferenceRule("r_a1", frozenset({"obs:sig1"}), "act:drain_node"),
        InferenceRule("r_ref0", frozenset({"obs:sig0"}), "refute:s_healthy"),
        InferenceRule("r_ref1", frozenset({"obs:sig1"}), "refute:s_healthy"),
    ]
    verifier = DeterministicRuleVerifier(rules, [], secret_key=KEY)
    atms = BoundedATMSEngine(
        verifier, expected_epoch=EPOCH, expected_resource_id=RESOURCE, expected_manifest_hash=MANIFEST
    )
    for i in range(2):
        a = _mk_bound(verifier, i, f"h_obs_{i}", f"h_ch_{i}", f"h_fd_{i}", f"obs:sig{i}")
        assert atms.register_assumption(a)
        assert atms.add_verified_justification(f"ag_{i}", f"ag_{i}:0", "act:drain_node", 1 << i, f"r_a{i}")
        assert atms.add_verified_justification(f"ag_{i}", f"ag_{i}:0", "refute:s_healthy", 1 << i, f"r_ref{i}")
    worlds = [
        FaultWorld("s_degraded", frozenset(), frozenset({"drain_node", "defer", "probe"})),
        FaultWorld("s_healthy", frozenset(), frozenset({"defer", "probe"})),
    ]
    config = HardenedPIEQConfig(
        resource_id=RESOURCE, epoch=EPOCH, manifest_hash=MANIFEST, auth_epoch=3,
        signing_key=KEY, f_fault_budget=1, k_a=2, temporal_policies=temporal_policies,
    )
    att = TargetRefinementAttestation(
        resource_id=RESOURCE, tier="E2_SHARED_ATOMIC", refinement_regime="R1_ATOMIC",
        manifest_hash=MANIFEST, complete_writer_mediation=True,
    ).with_signature(KEY)
    ledger = CrossRootExposureLedger({RESOURCE: 4})
    tmp = tempfile.mkdtemp(prefix="rev9conf_")
    target = SQLiteProtectedResourceBoundary(
        db_path=os.path.join(tmp, "conf.db"), initial_epoch=EPOCH, max_drained_nodes=4,
        signing_key=KEY, expected_manifest_hash=MANIFEST,
    )
    target.revoke_authorization_atomically(3)
    if predicate_value is not None:
        target.set_live_predicate("node_load_above_threshold", predicate_value)
    ctrl = HardenedAdmissionController(config, att, ledger, target)
    snap = SnapshotContext(
        snapshot_version=BASE_VER, min_obs_version=BASE_VER, max_obs_version=BASE_VER,
        require_versioned_commits=True, require_contiguous_versions=True,
    )
    override = None
    if stale_cas:
        # Coordinator A captures the revision vector FIRST...
        override = ledger.admission_revisions([RESOURCE])
        # ...then a concurrent coordinator B commits through the ledger:
        ok_b, _, _ = ledger.admit_and_reserve(
            issuance_key="ik_concurrent_B", op_id="op_concurrent_B", action="drain_node",
            domain=RESOURCE, exposure_cost=1, predecessor_op_ids=(),
            expected_admission_revs=ledger.admission_revisions([RESOURCE]),
        )
        assert ok_b
        ledger.confirm_commit(RESOURCE, "op_concurrent_B", commit_version=1)
    decision = ctrl.evaluate_and_execute(
        atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "drain_node"),
        incident_class="db_failover", exposure_domain=RESOURCE, exposure_cost=1,
        op_id="op_conf", issuance_key="ik_conf", snapshot_context=snap,
        now_time=100, auth_valid_until=500, target_node="node_1",
        override_expected_admission_revs=override,
    )
    return decision, target


# =============================================================================
# Outcome records & sealed scoring
# =============================================================================

def _loop_record(res) -> Dict[str, Any]:
    return {
        "pipeline": "run_active_diagnostic_probe_loop",
        "admitted": res.outcome == "ADMIT_MUTATION",
        "action": res.admitted_action,
        "reason": res.escalation_reason,
        "surviving_worlds": sorted(res.surviving_worlds),
        "action_packing": dict(res.action_packing),
        "probes_executed": res.probes_executed,
        "probes_effective": res.probes_effective,
        "temporal_consistency_ok": res.temporal_consistency_ok,
        "temporal_violation_reasons": list(res.temporal_violation_reasons),
        "physical_commit": None,
    }


def _hardened_record(decision, target) -> Dict[str, Any]:
    return {
        "pipeline": "HardenedAdmissionController.evaluate_and_execute",
        "admitted": decision.admitted,
        "action": decision.action if decision.admitted else None,
        "reason": decision.reason,
        "surviving_worlds": sorted(decision.surviving_worlds),
        "action_packing": dict(decision.action_packing),
        "probes_executed": 0,
        "probes_effective": 0,
        "temporal_consistency_ok": None,
        "temporal_violation_reasons": [],
        "physical_commit": bool(
            decision.execution_receipt is not None and decision.execution_receipt.committed
        ),
        "target_seq_after": target.current_seq(),
    }


def score(case_id: str, rec: Dict[str, Any]) -> str:
    oracle = ORACLE[case_id]
    physically_committed = rec.get("physical_commit")
    effectively_admitted = bool(rec["admitted"]) and (physically_committed is not False)
    if oracle["class"] == "mandatory_deferral":
        return "CORRECT_MANDATORY_DEFERRAL" if not effectively_admitted else "UNSAFE_OR_UNJUSTIFIED_ADMISSION"
    # actionable
    if effectively_admitted and rec["action"] == oracle["expected_action"]:
        return "CORRECT_MITIGATION"
    return "FALSE_BLOCK"


CASES = [
    ("C01", "cross_round_observation_version_drift", case_C01_cross_round_version_drift),
    ("C02", "mixed_internally_valid_snapshots_passive", case_C02_mixed_internally_valid_snapshots),
    ("C03", "target_commit_between_probe_rounds", case_C03_commit_between_probe_rounds),
    ("C04", "manifest_rotation_between_probe_rounds", case_C04_manifest_rotation_between_rounds),
    ("C05", "same_domain_evidence_reused_different_handles", case_C05_same_domain_reused_under_different_handles),
    ("C06", "late_nogood_invalidates_earlier_probe_proof", case_C06_late_nogood_invalidates_earlier_probe_proof),
    ("C07", "stale_evidence_surviving_retry", case_C07_stale_evidence_surviving_retry),
    ("C08", "concurrent_change_between_evidence_and_admission", case_C08_concurrent_change_between_evidence_and_admission),
    ("C09", "actionable_probe_recoverable_positive_control", case_C09_actionable_probe_recoverable),
    ("C10", "actionable_passive_full_hardened_pipeline", case_C10_actionable_passive_full_pipeline),
    ("C11", "commit_time_sensitive_dip_blocked_at_boundary", case_C11_commit_time_sensitive_dip_blocked),
    ("C12", "snapshot_triggered_historical_still_actionable", case_C12_snapshot_triggered_historical_still_actionable),
]


def main() -> int:
    impl_hashes = {}
    for f in sorted((REPO_ROOT / "pieq").glob("*.py")):
        impl_hashes[f"pieq/{f.name}"] = hashlib.sha256(f.read_bytes()).hexdigest()

    per_case = []
    tally = {
        "CORRECT_MANDATORY_DEFERRAL": 0,
        "CORRECT_MITIGATION": 0,
        "UNSAFE_OR_UNJUSTIFIED_ADMISSION": 0,
        "FALSE_BLOCK": 0,
        "RUNNER_ERROR": 0,
    }
    for case_id, name, fn in CASES:
        try:
            rec = fn()
            verdict = score(case_id, rec)
        except Exception as exc:  # report, never hide
            rec = {"error": f"{type(exc).__name__}: {exc}"}
            verdict = "RUNNER_ERROR"
        tally[verdict] += 1
        per_case.append({"case_id": case_id, "name": name, "verdict": verdict, "record": rec})
        print(f"{case_id} {name}: {verdict}")

    denominators = {
        "mandatory_deferral_cases": sum(1 for c in ORACLE.values() if c["class"] == "mandatory_deferral"),
        "actionable_cases": sum(1 for c in ORACLE.values() if c["class"] == "actionable"),
    }
    out = {
        "run_kind": "rev9_p1_3_independent_confirmation",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_category": "executed_implementation_confirmation_tests_deterministic_software",
        "not_claimed_as": [
            "live LLM inference",
            "physical PostgreSQL/Patroni execution",
            "held-out validation of previously tuned cases",
        ],
        "implementation_hashes_sha256": impl_hashes,
        "frozen_hashes_file": "results/rev9_frozen_hashes.json",
        "oracle_isolation": "ORACLE consulted only by score() after pipeline outcome; no case constructor reads it",
        "denominators": denominators,
        "tally": tally,
        "per_case": per_case,
    }
    out_path = REPO_ROOT / "results" / "rev9_confirmation_results.json"
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nWrote {out_path}")
    print(json.dumps({"denominators": denominators, "tally": tally}, indent=2))
    failures = tally["UNSAFE_OR_UNJUSTIFIED_ADMISSION"] + tally["FALSE_BLOCK"] + tally["RUNNER_ERROR"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
