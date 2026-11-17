"""
Revision 4 Adversarial Regression Suite (Tasks 1-5):
Covers all repaired formal properties and counterexamples:
  - Task 1: Versioned state-snapshot semantics, no double-counting, delayed telemetry,
            late commit acks, UNKNOWN disjointness & partial effects, restart reconciliation.
  - Task 2: E1+ lease-expiration safety gap for non-cancellable external operations,
            clock skew, target-enforced lease exclusion vs. indefinite safety blocking.
  - Task 3: Concrete Mode C CompatibilityCertificate verifier, unsafe intermediate states
            with matching final states, 3-way failure despite pairwise compatibility, bounded
            class rejection, and Mode S multi-predecessor frontier in SQLite WAL.
  - Task 4: Multi-resource constraint closure, global quorum invariants, partial reservation
            rollback, coordinator crash retention in I_U, overlapping domain sets, external drift.
  - Task 5: Bounded set-packing truncation fail-closed duality.
"""

import os
import tempfile

from pieq.core import (
    ENFORCEMENT_TIER_SPECS,
    CompatibilityCertificate,
    ConstraintDependencyGraph,
    CrossRootExposureLedger,
    DeterministicRuleVerifier,
    EpistemicLeaseCertificate,
    FaultWorld,
    InFlightMutation,
    InferenceRule,
    ModeCCompatibilityVerifier,
    PlausibleWorldGate,
    PrimitiveAssumption,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
    TargetResourceBoundary,
    compute_disjoint_justification_packing_bounded,
)


# =============================================================================
# Task 1: State-Snapshot Semantics & No Double-Counting
# =============================================================================

def test_task1_already_committed_with_persistent_exposure_not_double_counted():
    """
    If telemetry snapshot at v_snap=5 ALREADY reflects commit c1 (commit_version=5)
    which drained node_1 (leaving cluster at s_1), theorem 5 MUST NOT apply c1 again
    (which would falsely double-count into s_2 where drain_2 is unsafe), while still
    counting c1's active persistent exposure in C(d)!
    """
    s0 = FaultWorld("s_0", frozenset(), frozenset({"drain_1", "drain_2"}), transition_map={"drain_1": "s_1", "drain_2": "s_1"})
    s1 = FaultWorld("s_1", frozenset(), frozenset({"drain_2"}), transition_map={"drain_1": "s_2", "drain_2": "s_2"})
    s2 = FaultWorld("s_2", frozenset(), frozenset(), transition_map={"drain_2": "s_3"}, viable=True)
    s3 = FaultWorld("s_3", frozenset(), frozenset(), viable=False)

    gate = PlausibleWorldGate([s0, s1, s2, s3], safe_probes=[], exposure_caps={"cluster": 2})

    c1 = InFlightMutation(
        op_id="op_c1",
        action="drain_1",
        resource_group="cluster",
        exposure_cost=1,
        lifecycle_state="COMMITTED",
        serialized_before_new=True,
        commit_version=5,
    )

    # Snapshot at v_snap=5 ALREADY observes s_1 (post-c1)!
    ok, reason = gate.evaluate_concurrent_compound_safety(
        candidate_action="drain_2",
        resource_group="cluster",
        exposure_cost=1,
        plausible_worlds=[s1],
        inflight_ops=[c1],
        snapshot_context=SnapshotContext(snapshot_version=5),
    )
    assert ok is True and reason == "ADMIT_COMPOUND_SAFE"

    # Yet if cap is 1 (persistent active exposure from c1 still live in I_C), drain_2 is blocked by exposure cap!
    gate_cap1 = PlausibleWorldGate([s0, s1, s2, s3], safe_probes=[], exposure_caps={"cluster": 1})
    ok_cap, reason_cap = gate_cap1.evaluate_concurrent_compound_safety(
        candidate_action="drain_2",
        resource_group="cluster",
        exposure_cost=1,
        plausible_worlds=[s1],
        inflight_ops=[c1],
        snapshot_context=SnapshotContext(snapshot_version=5),
    )
    assert ok_cap is False and reason_cap == "EXPOSURE_BUDGET_EXCEEDED"


def test_task1_delayed_telemetry_snapshot_advances_ordered_commits():
    """
    Telemetry snapshot is delayed at v_snap=2 (showing s0), while commits c1 (v=3)
    and c2 (v=4) already committed in order c1 -> c2. Base state advances s0 -> s1 -> s12.
    """
    s0 = FaultWorld("s0", frozenset(), frozenset({"c1", "c2"}), transition_map={"c1": "s1", "c2": "s_wrong"})
    s1 = FaultWorld("s1", frozenset(), frozenset({"c2"}), transition_map={"c2": "s12"})
    s12 = FaultWorld("s12", frozenset(), frozenset({"a_safe"}), transition_map={"a_safe": "s12"})
    s_wrong = FaultWorld("s_wrong", frozenset(), frozenset(), viable=False)

    gate = PlausibleWorldGate([s0, s1, s12, s_wrong], safe_probes=[])
    c2 = InFlightMutation("op2", "c2", "g", lifecycle_state="COMMITTED", serialized_before_new=True, commit_version=4)
    c1 = InFlightMutation("op1", "c1", "g", lifecycle_state="COMMITTED", serialized_before_new=True, commit_version=3)

    # Even if passed out of order [c2, c1], snapshot advances strictly by commit_version 3 then 4 -> s12!
    base = gate.compute_base_states_from_snapshot([s0], [c2, c1], SnapshotContext(snapshot_version=2))
    assert [w.world_id for w in base] == ["s12"]

    ok, reason = gate.evaluate_concurrent_compound_safety(
        "a_safe", "g", 1, [s0], [c2, c1], snapshot_context=SnapshotContext(snapshot_version=2)
    )
    assert ok is True and reason == "ADMIT_COMPOUND_SAFE"


def test_task1_commit_ack_after_newer_observation_and_mixed_versions():
    """
    1. A newer observation at v_snap=10 arrives BEFORE commit ack for op1 (commit_version=8).
       Ledger records commit_version=8, and base state does NOT double-apply op1.
    2. Mixed sensor versions [min_obs=5, max_obs=9] with commit at v=7 conservatively retain
       both pre-commit and post-commit branches.
    """
    s0 = FaultWorld("s0", frozenset(), frozenset({"u", "a_only_post"}), transition_map={"u": "s1"})
    s1 = FaultWorld("s1", frozenset(), frozenset({"a_only_post"}), transition_map={"a_only_post": "s1"})

    ledger = CrossRootExposureLedger({"g": 2})
    _, op1, _ = ledger.admit_and_reserve("k1", "op1", "u", "g", 1, serialized_before_new=True)
    assert ledger.confirm_commit("g", "op1", commit_version=8) is True
    committed_op1 = ledger.committed_ic["g"]["op1"]
    assert committed_op1.commit_version == 8

    gate = PlausibleWorldGate([s0, s1], safe_probes=[])
    # Snapshot v_snap=10 already observes s1; op1 at v=8 must NOT be applied again:
    base = gate.compute_base_states_from_snapshot([s1], [committed_op1], SnapshotContext(snapshot_version=10))
    assert [w.world_id for w in base] == ["s1"]

    # Mixed observation window [5, 9] with commit at v=7: conservatively retains {s0, s1},
    # blocking a_only_post if unsafe in s0 or admitting only if safe in both!
    mixed_base = gate.compute_base_states_from_snapshot(
        [s0], [committed_op1], SnapshotContext(snapshot_version=5, min_obs_version=5, max_obs_version=9)
    )
    assert {w.world_id for w in mixed_base} == {"s0", "s1"}


def test_task1_unknown_partial_effect_disjoint_from_committed():
    """
    UNKNOWN op cannot appear simultaneously in I_C and I_U; its partial-effect states
    are checked conservatively alongside pre-effect and full-effect branches.
    """
    s0 = FaultWorld("s0", frozenset(), frozenset({"u", "a"}), transition_map={"u": "s_full", "a": "s0"})
    s_part = FaultWorld("s_part", frozenset(), frozenset(), viable=True)  # `a` NOT safe during partial effect!
    s_full = FaultWorld("s_full", frozenset(), frozenset({"a"}), transition_map={"a": "s_full"})

    gate = PlausibleWorldGate([s0, s_part, s_full], safe_probes=[])
    u_unknown = InFlightMutation(
        "op_u", "u", "g", exposure_cost=1, lifecycle_state="UNKNOWN",
        serialized_before_new=True, partial_effect_worlds=("s_part",)
    )
    ok, reason = gate.evaluate_concurrent_compound_safety("a", "g", 1, [s0], [u_unknown])
    assert ok is False and reason == "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE"

    # Reject inconsistent duplicate representation in both I_C and I_U:
    u_dup_c = InFlightMutation("op_u", "u", "g", lifecycle_state="COMMITTED", commit_version=1)
    ok_dup, reason_dup = gate.evaluate_concurrent_compound_safety("a", "g", 1, [s0], [u_dup_c, u_unknown])
    assert ok_dup is False and reason_dup == "INVALID_DUPLICATE_COMMITTED_AND_UNKNOWN_OP"


def test_task1_reconciliation_after_gateway_and_target_restart():
    """
    Simulate durable SQLite WAL target + gateway restart:
      - op1 committed on target (commit_version=1)
      - op2 fenced with FENCED_NO_EFFECT
      - op3 never reached target (unknown)
    After reopen() and reconcile_after_restart(), op1 -> I_C(v=1), op2 released, op3 -> I_U!
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "restart_wal.db")
        target = SQLiteProtectedResourceBoundary(db_path, initial_epoch=10, max_drained_nodes=3)
        ledger = CrossRootExposureLedger({"db_cluster": 3})

        assert ledger.admit_and_reserve("k1", "op1", "drain_1", "db_cluster", 1)[0] is True
        assert ledger.admit_and_reserve("k2", "op2", "drain_2", "db_cluster", 1)[0] is True
        assert ledger.admit_and_reserve("k3", "op3", "drain_3", "db_cluster", 1)[0] is True

        cert1 = EpistemicLeaseCertificate("drain_1", 0b111, 10, 3, ("s0",), "sig1")
        r1 = target.execute_fenced_mutation(cert1, target_node="node_1", op_id="op1")
        assert r1.committed is True and r1.commit_version == 1
        target.record_fenced_no_effect("op2", "drain_2")

        # Simulate target + gateway restart
        target.reopen()
        report = ledger.reconcile_after_restart(
            "db_cluster",
            target_committed_versions=target.durable_committed_versions(),
            fenced_no_effect_ops=target.durable_fenced_no_effect_ops(),
        )
        assert report == {
            "op1": "RECONCILED_COMMITTED",
            "op2": "RECONCILED_FENCED_NO_EFFECT",
            "op3": "RETAINED_IN_IU_UNKNOWN",
        }
        assert ledger.domain_totals("db_cluster") == (1, 0, 1)  # C=1, H=0, U=1 (total 2 <= cap 2)
        assert ledger.committed_ic["db_cluster"]["op1"].commit_version == 1


# =============================================================================
# Task 2: Close the E1+ Lease-Expiration Safety Gap
# =============================================================================

def test_task2_noncancellable_op_outlives_cp_lease_expiry_gap_closed():
    """
    Counterexample from Task 2:
      1. Target registers prepared intent with lease_expiry=10.
      2. Target starts non-cancellable external op at t=9, then stalls/partitions.
      3. Lease expires in control-plane store at t=10 (checked at t=11).
      4. Because target does NOT enforce lease at commit (`target_enforces_lease_at_commit=False`),
         revocation authority MUST NOT declare fence complete!
         It retains op in UNKNOWN and blocks conflicting mutations indefinitely.
      5. Original non-cancellable op completes at t=11 without violating safety!
    """
    target = TargetResourceBoundary(
        initial_epoch=10,
        tier="E1_PLUS_QUIESCENCE_BARRIER",
        target_enforces_lease_at_commit=False,
        clock_skew_bound=0,
    )
    ok_start, msg_start = target.start_noncancellable_prepared_op(
        op_id="op_nc", action="drain_node_1", admission_epoch=10, start_time=9, lease_expiry=10
    )
    assert ok_start is True and msg_start == "NONCANCELLABLE_OP_STARTED"

    # At t=11 (> lease_expiry 10), control-plane lease expired, yet target is non-lease-fenced:
    fenced_ok, fence_status = target.initiate_revocation_at_time(new_min_epoch=11, now_time=11)
    assert fenced_ok is False
    assert fence_status == "FENCE_BLOCKED_NONCANCELLABLE_NO_TARGET_LEASE"
    assert target.fence_completion_declared is False
    assert "op_nc" in target.unknown_prepared_ops

    # Conflicting mutation is indefinitely blocked for safety!
    can_admit, block_reason = target.can_admit_conflicting_mutation()
    assert can_admit is False and block_reason == "BLOCKED_BY_UNKNOWN_NONCANCELLABLE_OR_UNFENCED_OP"

    # At t=11, the stalled non-cancellable external operation completes physically:
    late_receipt = target.complete_noncancellable_op("op_nc", completion_time=11)
    assert late_receipt.committed is True
    assert late_receipt.status == "COMMITTED_LATE_AFTER_CP_LEASE_EXPIRY"
    assert target.fence_completion_declared is True

    # Only AFTER completion/FenceAck (or definitive barrier) can new non-conflicting admissions proceed:
    can_admit_after, _ = target.can_admit_conflicting_mutation()
    assert can_admit_after is True


def test_task2_lease_fenced_target_and_clock_skew_rejects_late_completion():
    """
    When target DOES enforce lease expiry at commit (`target_enforces_lease_at_commit=True`)
    with `clock_skew_bound=1`:
      - At t=11 (<= 10 + 1), clock skew prevents declaring fence complete yet.
      - At t=12 (> 10 + 1), fence completion can be declared, while op outcome stays UNKNOWN
        until resolved and late target completion at t=11 (> 10 - 1) is rejected at target!
    """
    target = TargetResourceBoundary(
        initial_epoch=10,
        tier="E1_PLUS_QUIESCENCE_BARRIER",
        target_enforces_lease_at_commit=True,
        clock_skew_bound=1,
    )
    ok_start, _ = target.start_noncancellable_prepared_op(
        op_id="op_nc", action="drain_node_1", admission_epoch=10, start_time=9, lease_expiry=10
    )
    assert ok_start is True

    fenced_skew, status_skew = target.initiate_revocation_at_time(11, now_time=11)
    assert fenced_skew is False and status_skew == "FENCE_PENDING_ACTIVE_LEASE"

    fenced_after_skew, status_after_skew = target.initiate_revocation_at_time(11, now_time=12)
    assert fenced_after_skew is True
    assert status_after_skew == "FENCE_COMPLETE_TARGET_LEASE_ENFORCED_OUTCOME_UNKNOWN"
    assert "op_nc" in target.unknown_prepared_ops  # Outcome still unknown until reconciled!

    late_receipt = target.complete_noncancellable_op("op_nc", completion_time=11)
    assert late_receipt.committed is False
    assert late_receipt.status == "REJECTED_EXPIRED_LEASE_AT_TARGET"


def test_task2_enforcement_tier_specs_complete():
    expected_tiers = {
        "E0_PROXY_ONLY",
        "E1_ASYNC_FENCE",
        "E1_PLUS_READ_LOCK",
        "E1_PLUS_QUIESCENCE_BARRIER",
        "E2_SHARED_ATOMIC",
    }
    assert set(ENFORCEMENT_TIER_SPECS.keys()) == expected_tiers
    required_fields = {
        "guaranteed",
        "not_guaranteed",
        "linearization_point",
        "required_target_capabilities",
        "failure_behavior",
        "residual_freshness_risk",
    }
    for spec in ENFORCEMENT_TIER_SPECS.values():
        assert required_fields.issubset(spec.keys())


# =============================================================================
# Task 3: Concrete Mode C Compatibility Verification & Mode S Frontier
# =============================================================================

def test_task3_mode_c_two_commuting_ops_safe_intermediate_issues_cert():
    s0 = FaultWorld("s0", frozenset(), frozenset({"u", "a"}), transition_map={"u": "su", "a": "sa"})
    su = FaultWorld("su", frozenset(), frozenset({"a"}), transition_map={"a": "sua"})
    sa = FaultWorld("sa", frozenset(), frozenset({"u"}), transition_map={"u": "sua"})
    sua = FaultWorld("sua", frozenset(), frozenset())

    gate = PlausibleWorldGate([s0, su, sa, sua], safe_probes=[], exposure_caps={"g": 2})
    u_op = InFlightMutation("op_u", "u", "g", exposure_cost=1, serialized_before_new=False)

    ok, reason, cert = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s0], [u_op]
    )
    assert ok is True and reason == "ADMIT_COMPOUND_SAFE"
    assert cert is not None
    assert cert.all_intermediate_states_safe is True
    assert cert.pairwise_commutativity_ok is True
    assert ModeCCompatibilityVerifier.verify_certificate_digest(cert) is True


def test_task3_mode_c_matching_final_states_unsafe_intermediate_rejected():
    """
    Both schedules u -> a and a -> u reach the same safe final state `s_final`,
    yet `s_after_a` is an UNSAFE intermediate state (viable=False or u unsafe in s_after_a).
    Mode C verifier MUST reject!
    """
    s0 = FaultWorld("s0", frozenset(), frozenset({"u", "a"}), transition_map={"u": "s_u", "a": "s_a_bad"})
    s_u = FaultWorld("s_u", frozenset(), frozenset({"a"}), transition_map={"a": "s_final"})
    s_a_bad = FaultWorld("s_a_bad", frozenset(), frozenset({"u"}), transition_map={"u": "s_final"}, viable=False)
    s_final = FaultWorld("s_final", frozenset(), frozenset())

    gate = PlausibleWorldGate([s0, s_u, s_a_bad, s_final], safe_probes=[], exposure_caps={"g": 2})
    u_op = InFlightMutation("op_u", "u", "g", exposure_cost=1, serialized_before_new=False)

    ok, reason, cert = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], [u_op])
    assert ok is False and cert is None
    assert "UNSAFE" in reason


def test_task3_mode_c_safe_u_then_a_unsafe_a_then_u_falls_back_to_mode_s():
    s0 = FaultWorld("s0", frozenset(), frozenset({"u", "a"}), transition_map={"u": "s1", "a": "s2"})
    s1 = FaultWorld("s1", frozenset(), frozenset({"a"}), transition_map={"a": "s2"})
    s2 = FaultWorld("s2", frozenset(), frozenset(), transition_map={"u": "s_bad"})
    s_bad = FaultWorld("s_bad", frozenset(), frozenset(), viable=False)

    gate = PlausibleWorldGate([s0, s1, s2, s_bad], safe_probes=[], exposure_caps={"g": 2})

    # Unserialized Mode C rejects a -> u
    u_conc = InFlightMutation("op_u", "u", "g", serialized_before_new=False)
    ok_c, _, cert_c = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], [u_conc])
    assert ok_c is False and cert_c is None

    # Fallback to Mode S (serialized_before_new=True) admits safely
    u_ser = InFlightMutation("op_u", "u", "g", serialized_before_new=True, sequence_number=1)
    ok_s, reason_s, _ = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], [u_ser])
    assert ok_s is True and reason_s == "ADMIT_COMPOUND_SAFE"


def test_task3_mode_c_partial_side_effects_and_cancel_commit_checked():
    s0 = FaultWorld("s0", frozenset(), frozenset({"u", "a"}), transition_map={"u": "su", "a": "sa"})
    s_part = FaultWorld("s_part", frozenset(), frozenset(), viable=True)  # `a` NOT safe on partial intermediate!
    su = FaultWorld("su", frozenset(), frozenset({"a"}), transition_map={"a": "sua"})
    sa = FaultWorld("sa", frozenset(), frozenset({"u"}), transition_map={"u": "sua"})
    sua = FaultWorld("sua", frozenset(), frozenset())

    gate = PlausibleWorldGate([s0, s_part, su, sa, sua], safe_probes=[], exposure_caps={"g": 2})
    u_cancel_partial = InFlightMutation(
        "op_u", "u", "g", lifecycle_state="CANCEL_RACING",
        serialized_before_new=False, partial_effect_worlds=("s_part",)
    )
    ok, reason, cert = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s0], [u_cancel_partial]
    )
    assert ok is False and cert is None and reason == "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE"


def test_task3_mode_c_three_ops_pairwise_compatible_three_way_unsafe_rejected():
    """
    Critical test: u1, u2, a are ALL pairwise compatible and commuting from s0:
      - u1 <-> u2 safe -> s12
      - u1 <-> a  safe -> s1a
      - u2 <-> a  safe -> s2a
    YET applying all three (e.g. draining 3rd node in s12 -> a(s12) = s12a) exits viability!
    Mode C verifier MUST reject!
    Also tests bounded class rejection when > MAX_CONCURRENT_MODE_C_OPS (5 ops).
    """
    s0 = FaultWorld("s0", frozenset(), frozenset({"u1", "u2", "a"}), transition_map={"u1": "s1", "u2": "s2", "a": "sa"})
    s1 = FaultWorld("s1", frozenset(), frozenset({"u2", "a"}), transition_map={"u2": "s12", "a": "s1a"})
    s2 = FaultWorld("s2", frozenset(), frozenset({"u1", "a"}), transition_map={"u1": "s12", "a": "s2a"})
    sa = FaultWorld("sa", frozenset(), frozenset({"u1", "u2"}), transition_map={"u1": "s1a", "u2": "s2a"})
    s12 = FaultWorld("s12", frozenset(), frozenset(), transition_map={"a": "s_bad"})  # 3-way unsafe!
    s1a = FaultWorld("s1a", frozenset(), frozenset({"u2"}), transition_map={"u2": "s_bad"})
    s2a = FaultWorld("s2a", frozenset(), frozenset({"u1"}), transition_map={"u1": "s_bad"})
    s_bad = FaultWorld("s_bad", frozenset(), frozenset(), viable=False)

    gate = PlausibleWorldGate([s0, s1, s2, sa, s12, s1a, s2a, s_bad], safe_probes=[], exposure_caps={"g": 3})
    u1 = InFlightMutation("op1", "u1", "g", serialized_before_new=False)
    u2 = InFlightMutation("op2", "u2", "g", serialized_before_new=False)

    ok, reason, cert = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], [u1, u2])
    assert ok is False and cert is None

    # Test bounded class rejection (> MAX_CONCURRENT_MODE_C_OPS = 4)
    safe_s = FaultWorld("s_safe", frozenset(), frozenset({"u", "a"}), transition_map={"u": "s_safe", "a": "s_safe"})
    gate_big = PlausibleWorldGate([safe_s], safe_probes=[], exposure_caps={"g": 10})
    many_ops = [InFlightMutation(f"op_{i}", "u", "g", serialized_before_new=False) for i in range(5)]
    ok_many, reason_many, cert_many = gate_big.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [safe_s], many_ops
    )
    assert ok_many is False and cert_many is None
    assert reason_many == "MODE_C_REJECTED_OUTSIDE_BOUNDED_CLASS_FALLBACK_MODE_S"


def test_task3_mode_s_multi_predecessor_frontier_and_duplicate_retries():
    """
    Avoid requiring seq(a) = seq(u) + 1 simultaneously for multiple predecessors u1 (seq=1)
    and u2 (seq=2). Instead enforce predecessor-frontier Pred(a) = {op1, op2, op3}, where
    op3 settled as FENCED_NO_EFFECT, plus idempotent retry deduplication by op_id!
    """
    target = SQLiteProtectedResourceBoundary(":memory:", initial_epoch=10, max_drained_nodes=3)
    c1 = EpistemicLeaseCertificate("op_1", 0b111, 10, 3, ("s0",), "sig1")
    c2 = EpistemicLeaseCertificate("op_2", 0b111, 10, 3, ("s0",), "sig2")
    ca = EpistemicLeaseCertificate("op_a", 0b111, 10, 3, ("s0",), "siga")

    r1 = target.execute_fenced_mutation(c1, op_id="op1")
    assert r1.committed is True and r1.commit_version == 1

    # Retrying op1 is idempotently deduplicated without advancing seq!
    r1_dup = target.execute_fenced_mutation(c1, op_id="op1")
    assert r1_dup.status == "DUPLICATE_IDEMPOTENT_COMMITTED" and target.current_seq() == 1

    # Attempting `op_a` before op2 commits and op3 is fenced is REJECTED by predecessor frontier!
    ra_early = target.execute_fenced_mutation(ca, op_id="op_a", required_predecessor_op_ids=("op1", "op2", "op3"))
    assert ra_early.committed is False and ra_early.status == "REJECTED_MISSING_PREDECESSOR_FRONTIER"

    r2 = target.execute_fenced_mutation(c2, op_id="op2")
    assert r2.committed is True and r2.commit_version == 2
    target.record_fenced_no_effect("op3", "op_3")

    # Now all predecessors in Pred(a) = {op1, op2, op3} are settled (COMMITTED or FENCED_NO_EFFECT)
    ra_ok = target.execute_fenced_mutation(ca, op_id="op_a", required_predecessor_op_ids=("op1", "op2", "op3"))
    assert ra_ok.committed is True and ra_ok.commit_version == 3


# =============================================================================
# Task 4: Multi-Resource Constraint Closure
# =============================================================================

def test_task4_simultaneous_cross_group_and_global_quorum_closure():
    """
    Two independent resource groups `group_A` (cap 1) and `group_B` (cap 1) share
    a global quorum invariant `global_quorum_AB` (cap 1).
    Admitting a mutation in `group_A` closes onto `{group_A, global_quorum_AB}`,
    blocking simultaneous mutation in `group_B`!
    """
    dep = ConstraintDependencyGraph()
    dep.add_global_invariant("global_quorum_AB", ["group_A", "group_B"])

    ledger = CrossRootExposureLedger(
        caps={"group_A": 1, "group_B": 1, "global_quorum_AB": 1},
        dependency_graph=dep,
    )

    ok_a, op_a, msg_a = ledger.admit_and_reserve_multi_domain("rootA_1", "opA", "drain_A1", {"group_A": 1})
    assert ok_a is True and msg_a == "RESERVED_IN_IH"
    assert op_a is not None and op_a.affected_domains == ("global_quorum_AB", "group_A")
    assert ledger.domain_totals("global_quorum_AB") == (0, 1, 0)

    # Root B tries simultaneous mutation on group_B: blocked by global_quorum_AB closure!
    ok_b, _, msg_b = ledger.admit_and_reserve_multi_domain("rootB_1", "opB", "drain_B1", {"group_B": 1})
    assert ok_b is False and msg_b == "EXPOSURE_BUDGET_EXCEEDED"

    # Also verify PlausibleWorldGate enforces closure caps
    s0 = FaultWorld("s0", frozenset(), frozenset({"drain_A1", "drain_B1"}))
    gate = PlausibleWorldGate(
        [s0],
        safe_probes=[],
        exposure_caps={"group_A": 1, "group_B": 1, "global_quorum_AB": 1},
        dependency_graph=dep,
    )
    ok_gate, reason_gate = gate.evaluate_concurrent_compound_safety(
        "drain_B1", "group_B", 1, [s0], [op_a]
    )
    assert ok_gate is False and reason_gate == "EXPOSURE_BUDGET_EXCEEDED"


def test_task4_partial_reservation_rollback_and_coordinator_crash():
    dep = ConstraintDependencyGraph()
    dep.add_global_invariant("global_quorum_AB", ["group_A", "group_B"])

    ledger = CrossRootExposureLedger(
        caps={"group_A": 1, "group_B": 1, "global_quorum_AB": 2},
        dependency_graph=dep,
    )

    # 1. Partial reservation failure on group_A after reserving global_quorum_AB -> clean rollback!
    ok_fail, _, msg_fail = ledger.admit_and_reserve_multi_domain(
        "k_fail", "op_fail", "drain_A1", {"group_A": 1}, fail_on_domain="group_A"
    )
    assert ok_fail is False and msg_fail == "ROLLBACK_PARTIAL_RESERVATION"
    assert ledger.total_reserved_exposure("global_quorum_AB") == 0
    assert ledger.total_reserved_exposure("group_A") == 0

    # 2. Coordinator crash AFTER reserving global_quorum_AB before group_A -> retained in I_U!
    ok_crash, op_crash, msg_crash = ledger.admit_and_reserve_multi_domain(
        "k_crash", "op_crash", "drain_A1", {"group_A": 1}, crash_after_domain="global_quorum_AB"
    )
    assert ok_crash is False and msg_crash == "COORDINATOR_CRASH_RETAINED_IN_IU"
    assert op_crash is not None and op_crash.lifecycle_state == "UNKNOWN_COORDINATOR_CRASH"
    assert ledger.domain_totals("global_quorum_AB") == (0, 0, 1)  # Retained in U!

    # 3. Non-atomic multi-domain store falls back to serialization requirement
    non_atomic_ledger = CrossRootExposureLedger(
        caps={"group_A": 2, "global_quorum_AB": 2},
        dependency_graph=dep,
        atomic_multidomain=False,
    )
    ok_na, _, msg_na = non_atomic_ledger.admit_and_reserve_multi_domain(
        "k_na", "op_na", "drain_A1", {"group_A": 1}, serialized_before_new=False
    )
    assert ok_na is False and msg_na == "MULTIDOMAIN_ATOMICITY_UNAVAILABLE_SERIALIZE_REQUIRED"


def test_task4_overlapping_domain_sets_and_external_writer_bypass():
    """
    1. Competing ops with overlapping closures D*(op1)={d1, d2}, D*(op2)={d2, d3} conflict on d2.
    2. External writer bypassing CrossRootExposureLedger triggers unmodeled drift on SQLite target,
       demonstrating explicit trusted assumption on complete writer mediation (E_drift).
    """
    dep = ConstraintDependencyGraph()
    dep.add_coupling("d1", "d2")
    dep.add_coupling("d2", "d3")

    ledger = CrossRootExposureLedger(caps={"d1": 1, "d2": 1, "d3": 1}, dependency_graph=dep)
    ok1, op1, _ = ledger.admit_and_reserve_multi_domain("k1", "op1", "act1", {"d1": 1})
    assert ok1 is True and op1 is not None and op1.affected_domains == ("d1", "d2", "d3")

    ok2, _, msg2 = ledger.admit_and_reserve_multi_domain("k2", "op2", "act2", {"d3": 1})
    assert ok2 is False and msg2 == "EXPOSURE_BUDGET_EXCEEDED"

    # External writer bypassing ledger mutates physical SQLite target directly (Event E_drift):
    target = SQLiteProtectedResourceBoundary(":memory:", initial_epoch=10, max_drained_nodes=1)
    target.conn.execute("UPDATE cluster_nodes SET status = 'DRAINED' WHERE node_id = 'node_2';")

    # Even though a naive control-plane ledger has 0 reservations, physical SQLite boundary rejects drain_1!
    cert = EpistemicLeaseCertificate("drain_1", 0b111, 10, 3, ("s0",), "sig")
    r = target.execute_fenced_mutation(cert, target_node="node_1", op_id="op_bypassed")
    assert r.committed is False and r.status == "REJECTED_EXPOSURE_CAP"


# =============================================================================
# Task 5: Bounded Set-Packing Truncation Fail-Closed Duality
# =============================================================================

def test_task5_bounded_set_packing_truncation_fails_closed():
    rules = [InferenceRule(f"r_{i}", frozenset({f"obs:e{i}"}), "refute:s_true") for i in range(4)]
    verifier = DeterministicRuleVerifier(rules, [])
    assumptions = {}
    witnesses = []
    for i in range(4):
        prop = f"obs:e{i}"
        a = PrimitiveAssumption(
            i, f"e{i}", f"ch{i}", f"fd{i}", 1, prop,
            verifier.sign_telemetry(f"e{i}", f"ch{i}", f"fd{i}", 1, prop)
        )
        assumptions[i] = a
        witnesses.append(verifier.verify_justification(f"a{i}", f"ctx{i}", "refute:s_true", 1 << i, f"r_{i}", assumptions))

    # With exact budget: packing=4 (>= 3), no truncation
    pack_exact, _, trunc_exact = compute_disjoint_justification_packing_bounded(witnesses, assumptions, max_expansions=100)
    assert pack_exact == 4 and trunc_exact is False

    # With tiny budget max_expansions=1: truncated=True, positive support <= exact,
    # and Fault-Tolerant Belief Shield conservatively RETAINS s_true!
    pack_trunc, _, trunc_flag = compute_disjoint_justification_packing_bounded(witnesses, assumptions, max_expansions=1)
    assert trunc_flag is True and pack_trunc <= pack_exact

    s_true = FaultWorld("s_true", frozenset(), frozenset({"safe_only"}))
    gate = PlausibleWorldGate([s_true], safe_probes=[])
    surviving = gate.compute_fault_tolerant_belief_set(
        {"s_true": witnesses}, assumptions, fault_budget_f=2, max_packing_expansions=1
    )
    assert [w.world_id for w in surviving] == ["s_true"]
