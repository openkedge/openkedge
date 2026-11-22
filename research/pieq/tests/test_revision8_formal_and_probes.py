"""
PIEQ Revision 8 Regression Tests — P1.2 Corollary 1 Repair, P1.3 Adaptive Probe
Temporal & Snapshot Consistency (All 7 Adversarial Boundaries), and P0.2 Held-Out
Benchmark Disjointness Audit.
"""
from __future__ import annotations

import os
import tempfile

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    DeterministicRuleVerifier,
    EpistemicLeaseCertificate,
    FaultWorld,
    InferenceRule,
    InvariantConstraint,
    PlausibleWorldGate,
    SQLiteProtectedResourceBoundary,
    TargetResourceBoundary,
    filter_snapshot_consistent_witnesses,
)
from pieq.heldout_cases import (
    GROUND_TRUTH_CLASSES,
    HELDOUT_DOMAINS,
    build_heldout_cases,
    build_heldout_manifest,
)
from pieq.live_cases import INCIDENTS, build_live_cases
from pieq.probe_loop import (
    DiagnosticProbeSpec,
    ProbeBudget,
    run_active_diagnostic_probe_loop,
)


def _mk_bound(
    verifier: DeterministicRuleVerifier,
    bit: int,
    aid: str,
    ch: str,
    dom: str,
    prop: str,
    *,
    epoch: int = 10,
    obs_ver: int = 50,
    resource_id: str = "pg_cluster",
    manifest_hash: str = "mh_v8",
) -> BoundPrimitiveAssumption:
    sig = verifier.sign_telemetry(
        assumption_id=aid,
        channel_id=ch,
        failure_domain=dom,
        epoch_revision=epoch,
        proposition=prop,
        observation_version=obs_ver,
        bit_index=bit,
        resource_id=resource_id,
        manifest_hash=manifest_hash,
    )
    return BoundPrimitiveAssumption(
        bit_index=bit,
        assumption_id=aid,
        channel_id=ch,
        failure_domain=dom,
        epoch_revision=epoch,
        proposition=prop,
        signature=sig,
        observation_version=obs_ver,
        resource_id=resource_id,
        manifest_hash=manifest_hash,
    )


def _signed_cert(
    action: str,
    *,
    admission_epoch: int = 10,
    auth_epoch: int = 5,
    auth_valid_until: int | None = 1000,
    resource_id: str = "db_cluster",
    manifest_hash: str = "mh_v8",
    snapshot_version: int = 50,
    signing_key: bytes = b"rev8-p12-key",
) -> EpistemicLeaseCertificate:
    raw = EpistemicLeaseCertificate(
        action=action,
        support_mask=0b11,
        admission_epoch=admission_epoch,
        packing_number=2,
        plausible_worlds=("s_overloaded",),
        signature="",
        auth_epoch=auth_epoch,
        auth_valid_until=auth_valid_until,
        resource_id=resource_id,
        manifest_hash=manifest_hash,
        snapshot_version=snapshot_version,
    )
    return EpistemicLeaseCertificate(
        action=raw.action,
        support_mask=raw.support_mask,
        admission_epoch=raw.admission_epoch,
        packing_number=raw.packing_number,
        plausible_worlds=raw.plausible_worlds,
        signature=raw.compute_signature(signing_key),
        auth_epoch=raw.auth_epoch,
        auth_valid_until=raw.auth_valid_until,
        resource_id=raw.resource_id,
        manifest_hash=raw.manifest_hash,
        snapshot_version=raw.snapshot_version,
    )


# =============================================================================
# P1.2 — Corollary 1 Non-Biconditional Counterexamples & Atomic Boundary Tests
# =============================================================================

def test_p1_2_counterexample_commit_truth_without_persistence_or_revalidation() -> None:
    """
    P1.2 Counterexample 1 (Disproving Revision 7's invalid `iff` in Corollary 1(a)):
      - At v_snap=50 (t=0), obs:queue_high = True (SnapshotPreconditionValid = True).
      - At t=1 in (v_snap, t_eff), load briefly clears: obs:queue_high = False
        -> Interval persistence Persist(c_a, [v_snap, t_eff]) is FALSE!
      - At t=2 = t_eff, a new burst restores obs:queue_high = True.
      - Execution proceeds WITHOUT Tier E2 atomic revalidation (both on Tier E1+-quiescence
        and on un-revalidated Tier E2).
      - Result: CommitPreconditionValid(a, t_eff) IS TRUE at execution time, even though
        NEITHER interval persistence NOR Tier E2 atomic revalidation held!
      - Therefore persistence-or-revalidation is SUFFICIENT for a sound system guarantee
        of CommitPreconditionValid, NOT necessary (`iff` is mathematically false).
    """
    key = b"rev8-p12-ce1"
    cert = _signed_cert("scale_out_cluster", signing_key=key)

    with tempfile.TemporaryDirectory() as tmp:
        e2 = SQLiteProtectedResourceBoundary(
            os.path.join(tmp, "ce1.db"),
            initial_epoch=10,
            signing_key=key,
            expected_manifest_hash="mh_v8",
        )
        e2.revoke_authorization_atomically(5)

        # Trajectory of physical predicate c_a over [v_snap=0, t_mid=1, t_eff=2]:
        predicate_history = []
        e2.set_live_predicate("obs:queue_high", True)   # t=0 (v_snap)
        predicate_history.append(e2.get_live_predicate("obs:queue_high"))
        e2.set_live_predicate("obs:queue_high", False)  # t=1 (transient dip -> breaks Persist!)
        predicate_history.append(e2.get_live_predicate("obs:queue_high"))
        e2.set_live_predicate("obs:queue_high", True)   # t=2 (t_eff: returns True coincidentally!)
        predicate_history.append(e2.get_live_predicate("obs:queue_high"))

        interval_persist_holds = all(predicate_history)
        assert interval_persist_holds is False

        # Execute WITHOUT Tier E2 atomic revalidation (required_commit_predicates=()):
        rcpt_e2_unreval = e2.execute_fenced_mutation(
            cert, op_id="op_unreval", now_time=500, required_commit_predicates=()
        )
        actual_commit_truth = e2.get_live_predicate("obs:queue_high")
        assert rcpt_e2_unreval.committed is True
        assert actual_commit_truth is True  # CommitPreconditionValid holds WITHOUT Persist OR Reval!

        # Same counterexample under Tier E1+-quiescence (T != E2, no atomic revalidation):
        e1q = TargetResourceBoundary(initial_epoch=10, tier="E1_PLUS_QUIESCENCE_BARRIER")
        e1q.revoke_authorization_epoch(5, fence_message_delayed=False)
        rcpt_e1q, _ = e1q.execute_quiescence_handshake(cert, op_id="op_e1q", now_time=500)
        assert rcpt_e1q.committed is True
        assert actual_commit_truth is True  # True despite T=E1+q and Persist=False!
        e2.conn.close()


def test_p1_2_counterexample_non_atomic_revalidation_toctou_vs_atomic_boundary() -> None:
    """
    P1.2 Counterexample 2 (Why target-side revalidation MUST share the authoritative
    atomic mutation boundary):
      - Checking a stale/separate predicate read BEFORE entering the atomic mutation
        transaction (check-then-act TOCTOU) passes even when the authoritative predicate
        flips False before commit.
      - Inspecting the authoritative predicate state INSIDE SQLite's `BEGIN IMMEDIATE`
        mutation transaction (`required_commit_predicates`) rejects atomically!
    """
    key = b"rev8-p12-ce2"
    cert = _signed_cert("scale_out_cluster", signing_key=key)

    with tempfile.TemporaryDirectory() as tmp:
        e2 = SQLiteProtectedResourceBoundary(
            os.path.join(tmp, "ce2.db"),
            initial_epoch=10,
            signing_key=key,
            expected_manifest_hash="mh_v8",
        )
        e2.revoke_authorization_atomically(5)
        e2.set_live_predicate("obs:queue_high", True)

        # Non-atomic check-then-act (reads outside mutation boundary):
        cached_check_passed = e2.get_live_predicate("obs:queue_high")
        assert cached_check_passed is True
        # Concurrent plant update clears predicate BEFORE mutation commits:
        e2.set_live_predicate("obs:queue_high", False)

        # (a) Non-atomic revalidation wrongly allows commit when predicate is now False:
        r_non_atomic = e2.execute_fenced_mutation(cert, op_id="op_toctou", now_time=500)
        assert r_non_atomic.committed is True
        assert e2.get_live_predicate("obs:queue_high") is False

        # (b) Authoritative atomic revalidation sharing `BEGIN IMMEDIATE` rejects deterministically:
        r_atomic = e2.execute_fenced_mutation(
            cert,
            op_id="op_atomic",
            now_time=501,
            required_commit_predicates=("obs:queue_high",),
        )
        assert r_atomic.committed is False
        assert r_atomic.status == "REJECTED_COMMIT_PRECONDITION_FALSE"
        e2.conn.close()


# =============================================================================
# P1.3 — Adaptive Probe Snapshot Consistency (All 7 Adversarial Boundaries)
# =============================================================================

def _setup_probe_scenario(invariants=()):
    rules = [
        InferenceRule("r_a0", frozenset({"obs:hb_absent"}), "act:promote"),
        InferenceRule("r_a1", frozenset({"obs:bmc_off"}), "act:promote"),
        InferenceRule("r_a_conj", frozenset({"obs:hb_absent", "obs:bmc_off"}), "act:promote"),
        InferenceRule("r_ref0", frozenset({"obs:hb_absent"}), "refute:s_part"),
        InferenceRule("r_ref1", frozenset({"obs:bmc_off"}), "refute:s_part"),
        InferenceRule("r_ref_conj", frozenset({"obs:hb_absent", "obs:bmc_off"}), "refute:s_part"),
        InferenceRule("r_same_a", frozenset({"obs:same_dom_1"}), "act:promote"),
        InferenceRule("r_same_b", frozenset({"obs:same_dom_2"}), "act:promote"),
        InferenceRule("r_same_r1", frozenset({"obs:same_dom_1"}), "refute:s_part"),
        InferenceRule("r_same_r2", frozenset({"obs:same_dom_2"}), "refute:s_part"),
    ]
    verifier = DeterministicRuleVerifier(rules, list(invariants), secret_key=b"rev8-p13-key")
    atms = BoundedATMSEngine(
        verifier,
        expected_epoch=10,
        expected_resource_id="pg_cluster",
        expected_manifest_hash="mh_v8",
    )
    e0 = _mk_bound(verifier, 0, "e0", "ch_0", "fd_0", "obs:hb_absent", epoch=10, obs_ver=50)
    assert atms.register_assumption(e0) is True
    assert atms.add_verified_justification("ag0", "ag0:0", "act:promote", 1 << 0, "r_a0") is not None
    assert atms.add_verified_justification("ag0", "ag0:0", "refute:s_part", 1 << 0, "r_ref0") is not None

    worlds = [
        FaultWorld("s_dead", frozenset(), frozenset({"promote", "defer", "probe"})),
        FaultWorld("s_part", frozenset(), frozenset({"reroute", "defer", "probe"})),
    ]
    oob_probe = DiagnosticProbeSpec(
        probe_id="probe_oob_bmc",
        collector_id="col_bmc",
        failure_domain="fd_oob",
        read_only=True,
        safety_contract_verified=True,
        world_outcome_map={"s_dead": "obs:bmc_off", "s_part": "obs:bmc_on"},
        target_actions=("promote",),
    )
    return verifier, atms, worlds, oob_probe


def test_p1_3_boundary_1_plant_changes_between_initial_telemetry_and_probe() -> None:
    """Boundary 1: Plant advances from v=50 (passive) to v=51 before OOB probe executes."""
    verifier, atms, worlds, oob_probe = _setup_probe_scenario()

    def exec_drifted(spec: DiagnosticProbeSpec, next_bit: int) -> BoundPrimitiveAssumption:
        return _mk_bound(verifier, next_bit, "p1", spec.collector_id, spec.failure_domain, "obs:bmc_off", obs_ver=51)

    res = run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "promote", "reroute"),
        incident_class="db_failover",
        f_fault_budget=1,
        k_a=2,
        available_probes=[oob_probe],
        probe_executor=exec_drifted,
        require_consistent_observation_version=True,
    )
    assert res.outcome == "DEFER_ESCALATE"
    assert res.temporal_consistency_ok is False
    assert "TEMPORAL_VIOLATION_OBSERVATION_VERSION_DRIFT" in res.temporal_violation_reasons
    assert set(res.surviving_worlds) == {"s_dead", "s_part"}  # Conservative world retention!


def test_p1_3_boundary_2_concurrent_commit_during_probe_execution() -> None:
    """Boundary 2: Concurrent commit advances target commit_seq during probe execution."""
    verifier, atms, worlds, oob_probe = _setup_probe_scenario()
    commit_seq = {"val": 5}

    def exec_with_concurrent_commit(spec: DiagnosticProbeSpec, next_bit: int) -> BoundPrimitiveAssumption:
        # Simulate another coordinator committing on the target while the probe RPC is in flight:
        commit_seq["val"] = 6
        return _mk_bound(verifier, next_bit, "p1", spec.collector_id, spec.failure_domain, "obs:bmc_off", obs_ver=50)

    res = run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "promote", "reroute"),
        incident_class="db_failover",
        f_fault_budget=1,
        k_a=2,
        available_probes=[oob_probe],
        probe_executor=exec_with_concurrent_commit,
        version_observer=lambda: commit_seq["val"],
    )
    assert res.outcome == "DEFER_ESCALATE"
    assert res.temporal_consistency_ok is False
    assert "TEMPORAL_VIOLATION_CONCURRENT_COMMIT_DURING_PROBE" in res.temporal_violation_reasons
    assert set(res.surviving_worlds) == {"s_dead", "s_part"}


def test_p1_3_boundary_3_stale_observation_handle_not_reused_after_version_advance() -> None:
    """Boundary 3: Stale v=50 passive handle cannot be combined into a conjunctive rule with a v=51 probe."""
    rules = [
        InferenceRule("r_conj_only", frozenset({"obs:hb_absent", "obs:bmc_off"}), "act:promote"),
        InferenceRule("r_ref_only", frozenset({"obs:hb_absent", "obs:bmc_off"}), "refute:s_part"),
    ]
    verifier = DeterministicRuleVerifier(rules, [], secret_key=b"rev8-b3-key")
    atms = BoundedATMSEngine(
        verifier, expected_epoch=10, expected_resource_id="pg_cluster", expected_manifest_hash="mh_v8"
    )
    e0 = _mk_bound(verifier, 0, "e0", "ch_0", "fd_0", "obs:hb_absent", obs_ver=50)
    assert atms.register_assumption(e0) is True
    worlds = [
        FaultWorld("s_dead", frozenset(), frozenset({"promote", "defer", "probe"})),
        FaultWorld("s_part", frozenset(), frozenset({"reroute", "defer", "probe"})),
    ]
    oob_probe = DiagnosticProbeSpec(
        probe_id="probe_bmc", collector_id="col_bmc", failure_domain="fd_oob",
        read_only=True, safety_contract_verified=True,
        world_outcome_map={"s_dead": "obs:bmc_off", "s_part": "obs:bmc_on"},
        target_actions=("promote",),
    )

    res = run_active_diagnostic_probe_loop(
        atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "promote", "reroute"),
        incident_class="db_failover", f_fault_budget=0, k_a=1,
        available_probes=[oob_probe],
        probe_executor=lambda spec, b: _mk_bound(
            verifier, b, "p_v51", spec.collector_id, spec.failure_domain, "obs:bmc_off", obs_ver=51
        ),
        require_consistent_observation_version=True,
    )
    assert res.outcome == "DEFER_ESCALATE"
    assert res.action_packing["promote"] == 0
    assert len(atms.active_witness_justifications("act:promote")) == 0


def test_p1_3_boundary_4_different_collectors_observing_different_consistent_snapshots() -> None:
    """Boundary 4: Two individually consistent proofs from v=50 and v=51 MUST NOT pack together."""
    verifier = DeterministicRuleVerifier(
        [
            InferenceRule("r0", frozenset({"obs:sig_a"}), "refute:s_part"),
            InferenceRule("r1", frozenset({"obs:sig_b"}), "refute:s_part"),
            InferenceRule("ra0", frozenset({"obs:sig_a"}), "act:promote"),
            InferenceRule("ra1", frozenset({"obs:sig_b"}), "act:promote"),
        ],
        [],
        secret_key=b"rev8-b4-key",
    )
    atms = BoundedATMSEngine(
        verifier, expected_epoch=10, expected_resource_id="pg_cluster", expected_manifest_hash="mh_v8"
    )
    # Collector 0 observes consistent snapshot v=50; Collector 1 observes consistent snapshot v=51!
    e0 = _mk_bound(verifier, 0, "e0", "ch_0", "fd_0", "obs:sig_a", obs_ver=50)
    e1 = _mk_bound(verifier, 1, "e1", "ch_1", "fd_1", "obs:sig_b", obs_ver=51)
    assert atms.register_assumption(e0) and atms.register_assumption(e1)
    assert atms.add_verified_justification("ag0", "ag0:0", "refute:s_part", 1 << 0, "r0") is not None
    assert atms.add_verified_justification("ag1", "ag1:0", "refute:s_part", 1 << 1, "r1") is not None
    assert atms.add_verified_justification("ag0", "ag0:0", "act:promote", 1 << 0, "ra0") is not None
    assert atms.add_verified_justification("ag1", "ag1:0", "act:promote", 1 << 1, "ra1") is not None

    filtered, inc, _, reason = filter_snapshot_consistent_witnesses(
        atms.active_witness_justifications("act:promote"), atms.assumptions_by_bit
    )
    assert inc is True
    assert filtered == []
    assert reason == "CROSS_SNAPSHOT_PROOFS_INCOMPATIBLE"

    worlds = [
        FaultWorld("s_dead", frozenset(), frozenset({"promote", "defer"})),
        FaultWorld("s_part", frozenset(), frozenset({"reroute", "defer"})),
    ]
    gate = PlausibleWorldGate(worlds, ["defer"])
    survs = gate.compute_fault_tolerant_belief_set(
        {"s_part": atms.active_witness_justifications("refute:s_part"), "s_dead": []},
        atms.assumptions_by_bit,
        fault_budget_f=1,
        require_consistent_observation_version=True,
    )
    assert {w.world_id for w in survs} == {"s_dead", "s_part"}  # s_part NOT pruned across mixed snapshots!


def test_p1_3_boundary_5_probe_retry_returning_different_state_epoch() -> None:
    """Boundary 5: A probe retry returning an observation from state epoch 11 != anchor epoch 10 fails closed."""
    verifier, atms, worlds, oob_probe = _setup_probe_scenario()

    def exec_epoch_drift(spec: DiagnosticProbeSpec, next_bit: int) -> BoundPrimitiveAssumption:
        # Simulate probe retry succeeding only after state epoch rotated 10 -> 11:
        return _mk_bound(
            verifier, next_bit, "p_retry_e11", spec.collector_id, spec.failure_domain,
            "obs:bmc_off", epoch=11, obs_ver=50,
        )

    res = run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "promote", "reroute"),
        incident_class="db_failover",
        f_fault_budget=1,
        k_a=2,
        available_probes=[oob_probe],
        probe_executor=exec_epoch_drift,
    )
    assert res.outcome == "DEFER_ESCALATE"
    assert res.temporal_consistency_ok is False
    assert "TEMPORAL_VIOLATION_EPOCH_DRIFT" in res.temporal_violation_reasons
    assert set(res.surviving_worlds) == {"s_dead", "s_part"}


def test_p1_3_boundary_6_topology_manifest_or_failure_domain_changes() -> None:
    """Boundary 6: Topology manifest rotation OR failure-domain mismatch during probing fails closed."""
    # (a) Manifest rotated mid-probing detected by manifest_observer
    verifier, atms, worlds, oob_probe = _setup_probe_scenario()
    live_manifest = {"hash": "mh_v8"}

    def exec_with_manifest_rotation(spec: DiagnosticProbeSpec, next_bit: int) -> BoundPrimitiveAssumption:
        live_manifest["hash"] = "mh_v9_rotated"
        return _mk_bound(verifier, next_bit, "p_m", spec.collector_id, spec.failure_domain, "obs:bmc_off")

    res_a = run_active_diagnostic_probe_loop(
        atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "promote", "reroute"),
        incident_class="db_failover", f_fault_budget=1, k_a=2,
        available_probes=[oob_probe], probe_executor=exec_with_manifest_rotation,
        manifest_observer=lambda: live_manifest["hash"],
    )
    assert res_a.outcome == "DEFER_ESCALATE"
    assert "TEMPORAL_VIOLATION_MANIFEST_ROTATED_DURING_PROBE" in res_a.temporal_violation_reasons

    # (b) Probe returns observation stamped with mutated failure_domain != spec.failure_domain
    verifier2, atms2, worlds2, oob_probe2 = _setup_probe_scenario()
    res_b = run_active_diagnostic_probe_loop(
        atms=atms2, worlds=worlds2, candidate_actions=("defer", "probe", "promote", "reroute"),
        incident_class="db_failover", f_fault_budget=1, k_a=2,
        available_probes=[oob_probe2],
        probe_executor=lambda spec, b: _mk_bound(
            verifier2, b, "p_spoof", spec.collector_id, "fd_spoofed_other", "obs:bmc_off"
        ),
    )
    assert res_b.outcome == "DEFER_ESCALATE"
    assert "TEMPORAL_VIOLATION_FAILURE_DOMAIN_CHANGED" in res_b.temporal_violation_reasons


def test_p1_3_boundary_7_previously_verified_proof_invalidated_after_new_probe_observation() -> None:
    """
    Boundary 7: A previously verified passive proof (from e0: obs:hb_absent) becomes INVALID
    when an active OOB probe returns a contradicting observation (obs:bmc_primary_alive)
    matching a trusted P_sys conflict invariant!
    The probe loop MUST register the verified NogoodCertificate, deactivate the prior proofs,
    drop packing 1 -> 0, and conservatively retain both fault worlds.
    """
    conflict_inv = InvariantConstraint(
        "inv_hb_vs_bmc_alive",
        frozenset({"obs:hb_absent", "obs:bmc_primary_alive"}),
        "Primary cannot simultaneously be dead and actively serving OOB heartbeat.",
    )
    verifier, atms, worlds, _ = _setup_probe_scenario(invariants=[conflict_inv])
    assert len(atms.active_witness_justifications("act:promote")) == 1
    assert len(atms.active_witness_justifications("refute:s_part")) == 1

    oob_probe = DiagnosticProbeSpec(
        probe_id="probe_oob_bmc_alive_check",
        collector_id="col_bmc",
        failure_domain="fd_oob",
        read_only=True,
        safety_contract_verified=True,
        world_outcome_map={"s_dead": "obs:bmc_off", "s_part": "obs:bmc_primary_alive"},
        target_actions=("promote",),
    )

    res = run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "promote", "reroute"),
        incident_class="db_failover",
        f_fault_budget=1,
        k_a=2,
        available_probes=[oob_probe],
        probe_executor=lambda spec, b: _mk_bound(
            verifier, b, "p_alive", spec.collector_id, spec.failure_domain, "obs:bmc_primary_alive", obs_ver=50
        ),
    )
    assert res.outcome == "DEFER_ESCALATE"
    assert res.action_packing["promote"] == 0
    assert res.refutation_packing["s_part"] == 0
    assert set(res.surviving_worlds) == {"s_dead", "s_part"}
    assert len(res.temporal_audit_log) == 1
    assert "inv_hb_vs_bmc_alive" in res.temporal_audit_log[0].newly_registered_nogoods
    assert res.temporal_audit_log[0].invalidated_prior_proofs == 2  # both r_a0 & r_ref0 deactivated!


def test_p1_3_repeated_probes_same_failure_domain_never_inflate_packing() -> None:
    """Multiple distinct read-only probes sharing physical failure_domain='fd_0' NEVER inflate nu_pack."""
    verifier, atms, worlds, _ = _setup_probe_scenario()
    same_domain_probes = [
        DiagnosticProbeSpec(
            probe_id="probe_same_1", collector_id="col_s1", failure_domain="fd_0",
            read_only=True, safety_contract_verified=True,
            world_outcome_map={"s_dead": "obs:same_dom_1", "s_part": "obs:same_alt_1"},
            target_actions=("promote",),
        ),
        DiagnosticProbeSpec(
            probe_id="probe_same_2", collector_id="col_s2", failure_domain="fd_0",
            read_only=True, safety_contract_verified=True,
            world_outcome_map={"s_dead": "obs:same_dom_2", "s_part": "obs:same_alt_2"},
            target_actions=("promote",),
        ),
    ]
    prop_map = {"probe_same_1": "obs:same_dom_1", "probe_same_2": "obs:same_dom_2"}

    res = run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "promote", "reroute"),
        incident_class="db_failover",
        f_fault_budget=1,
        k_a=2,
        available_probes=same_domain_probes,
        probe_executor=lambda spec, b: _mk_bound(
            verifier, b, f"obs_{spec.probe_id}", spec.collector_id, spec.failure_domain, prop_map[spec.probe_id], obs_ver=50
        ),
        budget=ProbeBudget(max_invocations=3, max_cost_units=5.0),
    )
    assert res.outcome == "DEFER_ESCALATE"
    assert res.action_packing["promote"] == 1  # Strictly 1 despite 3 observations in fd_0!
    assert res.refutation_packing["s_part"] == 1


# =============================================================================
# P0.2 — Held-Out Benchmark Disjointness & Independent Ground-Truth Audit
# =============================================================================

def test_p0_2_heldout_disjoint_from_frozen40_and_covers_all_archetypes() -> None:
    heldout = build_heldout_cases()
    manifest = build_heldout_manifest(heldout)
    frozen = build_live_cases()

    assert len(heldout) == 36
    assert set(manifest["ground_truth_class_counts"].keys()) == set(GROUND_TRUTH_CLASSES)
    assert all(cnt > 0 for cnt in manifest["ground_truth_class_counts"].values())
    assert len(manifest["archetype_ids"]) == 9
    assert all(len(ids) == 4 for ids in manifest["archetype_ids"].values())

    # Strictly 0% overlap in incident domains, worlds, actions, and rule IDs:
    frozen_domains = set(INCIDENTS.keys())
    heldout_domains = set(HELDOUT_DOMAINS.keys())
    assert frozen_domains.isdisjoint(heldout_domains)

    frozen_worlds = {w.world_id for c in frozen for w in c.worlds}
    heldout_worlds = {w.world_id for c in heldout for w in c.worlds}
    assert frozen_worlds.isdisjoint(heldout_worlds)

    frozen_actions = {a for c in frozen for w in c.worlds for a in w.safe_mutations} - {"defer", "probe"}
    heldout_actions = {a for c in heldout for w in c.worlds for a in w.safe_mutations} - {"defer", "probe"}
    assert frozen_actions.isdisjoint(heldout_actions)

    frozen_rules = {r.rule_id for c in frozen for r in c.rules}
    heldout_rules = {r.rule_id for c in heldout for r in c.rules}
    assert frozen_rules.isdisjoint(heldout_rules)
