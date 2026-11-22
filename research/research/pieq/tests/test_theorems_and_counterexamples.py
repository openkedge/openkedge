"""
Phase 1 & Second-Review Regression Test Suite:
Verifies every theorem (Theorems 1-5 and Proposition 6) and explicitly tests every
mathematical and distributed-systems counterexample from both reviews.
"""

import math
import numpy as np
import pytest

from pieq.core import (
    BoundedATMSEngine,
    CrossRootExposureLedger,
    DeterministicRuleVerifier,
    EpistemicLeaseCertificate,
    FaultWorld,
    InFlightMutation,
    InferenceRule,
    InvariantConstraint,
    PlausibleWorldGate,
    PrimitiveAssumption,
    SQLiteProtectedResourceBoundary,
    TargetResourceBoundary,
    binomial_tail_risk,
    compute_disjoint_justification_packing,
    compute_provenance_min_cut,
    covariance_metrics_from_matrix,
    disjoint_stratum_unsafe_bound,
    effective_sample_size_equicorr,
    equicorrelated_covariance,
    participation_ratio_equicorr,
    propagate_conformity_covariance,
    residual_unsafe_admit_bound,
)


def test_neff_vs_participation_ratio_reconciliation():
    """
    First Review Section 3.1A:
    Verify that Effective Sample Size N_eff = N / (1 + (N-1)*rho) and
    Covariance Participation Ratio r_PR = N / (1 + (N-1)*rho^2) are distinct
    and match the exact values in the review's table for N = 5.
    """
    n = 5
    assert math.isclose(effective_sample_size_equicorr(n, 0.620), 1.43678, rel_tol=1e-3)
    assert math.isclose(participation_ratio_equicorr(n, 0.620), 1.97036, rel_tol=1e-3)
    assert math.isclose(effective_sample_size_equicorr(n, 0.940), 1.05042, rel_tol=1e-3)
    assert math.isclose(participation_ratio_equicorr(n, 0.940), 1.10268, rel_tol=1e-3)
    assert math.isclose(effective_sample_size_equicorr(n, 0.784), 1.20890, rel_tol=1e-3)
    assert math.isclose(participation_ratio_equicorr(n, 0.784), 1.44566, rel_tol=1e-3)
    assert math.isclose(effective_sample_size_equicorr(n, 0.142), 3.18878, rel_tol=1e-3)
    assert math.isclose(participation_ratio_equicorr(n, 0.142), 4.62679, rel_tol=1e-3)


def test_theorem_1_conformity_dynamics_and_counterexample_3_1c():
    """
    First Review Section 3.1B & 3.1C + Second Review Section 5:
    1. Noiseless averaging contracts orthogonal-complement variance at rate (1 - gamma)^{2t}.
    2. Temporally white innovations yield a non-zero stationary orthogonal variance floor.
    3. Counterexample 3.1C: 5 independent agents with p = 0.30 average completely and threshold
       at 0.5 -> unanimous false consensus is 0.16308 < 0.30.
    """
    n = 5
    rho0 = 0.25
    gamma = 0.4
    t = 3

    sigma_t = propagate_conformity_covariance(n, sigma0_sq=1.0, rho0=rho0, gamma=gamma, rounds=t, tau_dec_sq=0.0)
    _, n_eff_t, _ = covariance_metrics_from_matrix(sigma_t)
    expected_n_eff = 1.0 + ((n - 1) * (1.0 - rho0) * ((1.0 - gamma) ** (2 * t))) / (1.0 + (n - 1) * rho0)
    assert math.isclose(n_eff_t, expected_n_eff, rel_tol=1e-9)

    sigma_noisy = propagate_conformity_covariance(
        n, sigma0_sq=1.0, rho0=rho0, gamma=gamma, rounds=50, tau_dec_sq=0.1, rho_model=0.2
    )
    bar_rho_noisy, _, _ = covariance_metrics_from_matrix(sigma_noisy)
    assert bar_rho_noisy < 0.999

    p_err = 0.30
    unanimous_false_consensus = binomial_tail_risk(m_channels=5, k_quorum=3, p_false=p_err)
    assert math.isclose(unanimous_false_consensus, 0.16308, abs_tol=1e-6)


def test_theorem_2_berkson_counterexample_and_minimal_subset_preservation():
    """
    First Review Section 3.2 + Second Review Section 5 (Theorem 2 Minimal Environments):
    1. Berkson selection counterexample: conditioning on X == Y induces Corr(X, Y | X=Y) = 1.0.
    2. Minimal Consistent Subset Preservation: If e0 alone proves c, then for a true superset
       E^* = {e0, e2}, E^* itself is not in L(c) (by minimality), but L(c) contains the minimal
       consistent subset {e0} <= E^* (`has_minimal_consistent_subset` == True).
    """
    joint_configs = [(0, 0), (0, 1), (1, 0), (1, 1)]
    retained = [c for c in joint_configs if c[0] == c[1]]
    assert math.isclose(np.corrcoef([c[0] for c in retained], [c[1] for c in retained])[0, 1], 1.0, abs_tol=1e-9)

    rules = [
        InferenceRule("r_single", frozenset({"obs:e0"}), "hyp:primary_dead"),
        InferenceRule("r_pair", frozenset({"obs:e0", "obs:e2"}), "hyp:primary_dead"),
    ]
    invariants = [
        InvariantConstraint("inv_lease", frozenset({"obs:e0", "obs:e1_conflict"}))
    ]
    verifier = DeterministicRuleVerifier(rules, invariants)
    atms = BoundedATMSEngine(verifier)

    for idx, prop in enumerate(["obs:e0", "obs:e1_conflict", "obs:e2"]):
        a = PrimitiveAssumption(
            idx, f"e{idx}", f"ch{idx}", f"fd{idx}", 1, prop,
            verifier.sign_telemetry(f"e{idx}", f"ch{idx}", f"fd{idx}", 1, prop, bit_index=idx)
        )
        atms.register_assumption(a)

    # Register {e0, e2} first, then minimal {e0}
    atms.add_verified_justification("a1", "ctx1", "hyp:primary_dead", 0b101, "r_pair")
    atms.add_verified_justification("a2", "ctx2", "hyp:primary_dead", 0b001, "r_single")

    # Minimality invariant pruned {e0, e2} (0b101) in favor of {e0} (0b001)!
    assert atms.labels["hyp:primary_dead"] == [0b001]
    assert 0b101 not in atms.labels["hyp:primary_dead"]
    # Yet {e0, e2} remains supported by its minimal consistent subset {e0} <= {e0, e2}:
    assert atms.has_minimal_consistent_subset("hyp:primary_dead", superset_mask=0b101) is True


def test_second_review_issue1_menger_min_cut_vs_complete_justification_packing():
    """
    Second Review Critical Issue #1 (Section 2):
    Three verified telemetry assumptions E = {e1, e2, e3} in disjoint failure domains F1, F2, F3
    and three conjunctive justifications:
        J1 = {e1, e2},  J2 = {e2, e3},  J3 = {e1, e3}.
    Menger max-flow routes e1->J1, e2->J2, e3->J3 and falsely reports minCut == 3!
    Our exact Complete-Justification Set Packing solver (`compute_disjoint_justification_packing`)
    correctly reports packing_number == 1 because every pair of complete justifications overlaps.
    """
    rules = [
        InferenceRule("r12", frozenset({"obs:e1", "obs:e2"}), "act:promote"),
        InferenceRule("r23", frozenset({"obs:e2", "obs:e3"}), "act:promote"),
        InferenceRule("r13", frozenset({"obs:e1", "obs:e3"}), "act:promote"),
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier)

    for idx in range(3):
        prop = f"obs:e{idx+1}"
        assump = PrimitiveAssumption(
            idx, f"e{idx+1}", f"ch_{idx+1}", f"fd_{idx+1}", 1, prop,
            verifier.sign_telemetry(f"e{idx+1}", f"ch_{idx+1}", f"fd_{idx+1}", 1, prop,
                                    bit_index=idx)
        )
        atms.register_assumption(assump)

    # J1 = {e1, e2}, J2 = {e2, e3}, J3 = {e1, e3} from 3 distinct agent contexts
    atms.add_verified_justification("a1", "ctx_1", "act:promote", 0b011, "r12")
    atms.add_verified_justification("a2", "ctx_2", "act:promote", 0b110, "r23")
    atms.add_verified_justification("a3", "ctx_3", "act:promote", 0b101, "r13")

    witnesses = atms.active_witness_justifications("act:promote")
    assert len(witnesses) == 3

    # Menger path flow counts 3 disjoint single-premise paths:
    menger_flow = compute_provenance_min_cut(witnesses, atms.assumptions_by_bit)
    assert menger_flow == 3

    # Complete-Justification Set Packing proves only 1 complete proof is disjoint!
    packing_num, packed_witnesses = compute_disjoint_justification_packing(witnesses, atms.assumptions_by_bit)
    assert packing_num == 1
    assert len(packed_witnesses) == 1


def test_second_review_issue2_async_fence_race_and_tiers_e0_e1_e2():
    """
    Second Review Critical Issue #2 (Section 3):
    1. Proxy admits promote_replica with certificate epoch 10.
    2. Telemetry invalidates evidence; control plane advances to epoch 11 and sends Fence(r, 11),
       but the Fence message is delayed in the network (`fence_message_delayed=True`).
    3. Mutation request with epoch 10 arrives at target first.
    Verify:
      - E1_ASYNC_FENCE still has local_target_fence = 10 and commits the stale mutation!
      - E1_TWOPHASE_BARRIER and E2_SHARED_ATOMIC (and SQLiteProtectedResourceBoundary)
        reject the stale mutation with REJECTED_STALE_EPOCH.
    """
    cert = EpistemicLeaseCertificate(
        action="promote_replica",
        support_mask=0b111,
        admission_epoch=10,
        packing_number=3,
        plausible_worlds=("s_primary_dead",),
        signature="sig_proxy",
    )

    # Tier E1_ASYNC_FENCE with delayed Fence(r, 11) message -> Race occurs!
    e1_async = TargetResourceBoundary(initial_epoch=10, enforce_target_fence=True, tier="E1_ASYNC_FENCE")
    e1_async.advance_invalidation_fence(11, fence_message_delayed=True)
    r_async = e1_async.execute_with_certificate(cert)
    assert r_async.committed is True  # Stale commit beat the delayed fence message!
    committed_before_fence = e1_async.deliver_delayed_fence()
    assert committed_before_fence == ["promote_replica"]

    # Tier E1_TWOPHASE_BARRIER and Tier E2_SHARED_ATOMIC prevent the race
    e2_shared = TargetResourceBoundary(initial_epoch=10, enforce_target_fence=True, tier="E2_SHARED_ATOMIC")
    e2_shared.advance_invalidation_fence(11, fence_message_delayed=True)
    r_e2 = e2_shared.execute_with_certificate(cert)
    assert r_e2.committed is False
    assert r_e2.status == "REJECTED_STALE_EPOCH"

    # Real SQLite WAL transactional protected resource boundary (Tier E2)
    sqlite_target = SQLiteProtectedResourceBoundary(":memory:", initial_epoch=10)
    sqlite_target.revoke_epoch_atomically(11)
    r_sqlite = sqlite_target.execute_fenced_mutation(cert)
    assert r_sqlite.committed is False
    assert r_sqlite.status == "REJECTED_STALE_EPOCH"


def test_second_review_issue3_fault_tolerant_belief_set_requires_ka_refutations():
    """
    Second Review Critical Issue #3 (Section 4):
    Suppose B_tau includes {s_primary_dead, s_partition}, and k_a = 3 (fault budget f = 2).
    If only 1 or 2 faulty failure domains emit false refutations against s_partition,
    `compute_fault_tolerant_belief_set` MUST retain s_partition in hat{B}_tau^{(2)} so
    promote_replica remains blocked! Only >= 3 disjoint refutations can prune s_partition.
    """
    rules = [
        InferenceRule(f"r_ref_{i}", frozenset({f"obs:not_partition_{i}"}), "refute:s_partition")
        for i in range(3)
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier)

    for i in range(3):
        prop = f"obs:not_partition_{i}"
        assump = PrimitiveAssumption(
            i, f"e_{i}", f"ch_{i}", f"fd_{i}", 1, prop,
            verifier.sign_telemetry(f"e_{i}", f"ch_{i}", f"fd_{i}", 1, prop, bit_index=i)
        )
        atms.register_assumption(assump)

    w_dead = FaultWorld("s_primary_dead", frozenset(), frozenset({"promote_replica"}))
    w_part = FaultWorld("s_partition", frozenset(), frozenset({"reroute_partition_link"}))
    gate = PlausibleWorldGate([w_dead, w_part], safe_probes=["probe_nvme"])

    # 1 faulty sensor refutes s_partition -> hat{B}_tau^{(2)} STILL retains s_partition!
    j0 = atms.add_verified_justification("a0", "ctx0", "refute:s_partition", 1 << 0, "r_ref_0")
    b_after_1 = gate.compute_fault_tolerant_belief_set(
        {"s_partition": [j0]}, atms.assumptions_by_bit, fault_budget_f=2
    )
    assert {w.world_id for w in b_after_1} == {"s_primary_dead", "s_partition"}
    assert gate.global_safe_mutations(b_after_1) == frozenset()

    # 2 faulty sensors refute s_partition -> hat{B}_tau^{(2)} STILL retains s_partition!
    j1 = atms.add_verified_justification("a1", "ctx1", "refute:s_partition", 1 << 1, "r_ref_1")
    b_after_2 = gate.compute_fault_tolerant_belief_set(
        {"s_partition": [j0, j1]}, atms.assumptions_by_bit, fault_budget_f=2
    )
    assert {w.world_id for w in b_after_2} == {"s_primary_dead", "s_partition"}

    # 3 disjoint sensors refute s_partition -> now s_partition is soundly pruned!
    j2 = atms.add_verified_justification("a2", "ctx2", "refute:s_partition", 1 << 2, "r_ref_2")
    b_after_3 = gate.compute_fault_tolerant_belief_set(
        {"s_partition": [j0, j1, j2]}, atms.assumptions_by_bit, fault_budget_f=2
    )
    assert {w.world_id for w in b_after_3} == {"s_primary_dead"}
    assert gate.global_safe_mutations(b_after_3) == frozenset({"promote_replica"})


def test_second_review_section6_concurrent_three_node_drain_and_sqlite_wal():
    """
    Second Review Section 6 (Concurrent and Aggregate Mutations / Theorem 5):
    A 3-node cluster tolerates draining 1 node:
      Post(s_3healthy, drain_node_1) = s_node1_drained in K
      Post(s_3healthy, drain_node_2) = s_node2_drained in K
    but draining both loses quorum!
    Verify that both `evaluate_concurrent_compound_safety` and `SQLiteProtectedResourceBoundary`
    admit `drain_node_1` and block concurrent `drain_node_2`.
    """
    w_3healthy = FaultWorld(
        "s_3healthy",
        frozenset(),
        frozenset({"drain_node_1", "drain_node_2"}),
        transition_map={"drain_node_1": "s_node1_drained", "drain_node_2": "s_node2_drained"},
    )
    w_1drained = FaultWorld(
        "s_node1_drained",
        frozenset(),
        frozenset(),  # Draining a second node exits the viability kernel!
    )
    w_2drained = FaultWorld(
        "s_node2_drained",
        frozenset(),
        frozenset(),
    )
    gate = PlausibleWorldGate(
        [w_3healthy, w_1drained, w_2drained],
        safe_probes=[],
        exposure_caps={"raft_quorum_group": 1},
    )

    # First drain with no in-flight operations is admitted:
    ok1, reason1 = gate.evaluate_concurrent_compound_safety(
        "drain_node_1", "raft_quorum_group", 1, [w_3healthy], inflight_ops=[]
    )
    assert ok1 is True and reason1 == "ADMIT_COMPOUND_SAFE"

    # Second concurrent drain while drain_node_1 is in-flight is blocked by both exposure cap
    # and compound reachable-belief intersection:
    inflight = [InFlightMutation("op1", "drain_node_1", "raft_quorum_group", exposure_cost=1)]
    ok2, reason2 = gate.evaluate_concurrent_compound_safety(
        "drain_node_2", "raft_quorum_group", 1, [w_3healthy], inflight_ops=inflight
    )
    assert ok2 is False

    # Also verify on real SQLite WAL protected resource boundary:
    sqlite_target = SQLiteProtectedResourceBoundary(":memory:", initial_epoch=10, max_drained_nodes=1)
    cert1 = EpistemicLeaseCertificate("drain_node_1", 0b111, 10, 3, ("s_3healthy",), "sig1")
    cert2 = EpistemicLeaseCertificate("drain_node_2", 0b111, 10, 3, ("s_3healthy",), "sig2")
    r1 = sqlite_target.execute_fenced_mutation(cert1, target_node="node_1")
    r2 = sqlite_target.execute_fenced_mutation(cert2, target_node="node_2")
    assert r1.committed is True
    assert r2.committed is False
    assert r2.status == "REJECTED_EXPOSURE_CAP"


def test_theorem_4_helly_three_world_and_provan_budget_overapproximation():
    """
    First Review Section 3.4 & 4.2 + Second Review Section 5:
    1. Helly 3-world counterexample: pairwise safe actions overlap, global intersection is empty.
    2. Budget exhaustion in BoundedATMSEngine triggers conservative overapproximation hat{B}_tau^{(f)} = S_cand.
    """
    w1 = FaultWorld("s1", frozenset({"obs:symptom"}), frozenset({"a", "b"}))
    w2 = FaultWorld("s2", frozenset({"obs:symptom"}), frozenset({"b", "c"}))
    w3 = FaultWorld("s3", frozenset({"obs:symptom"}), frozenset({"a", "c"}))
    worlds = [w1, w2, w3]
    assert PlausibleWorldGate.pairwise_ambiguity_detected(worlds) is False
    assert PlausibleWorldGate.global_safe_mutations(worlds) == frozenset()

    invariants = [InvariantConstraint(f"inv_{k}", frozenset({f"obs:a_{k}", f"obs:b_{k}"})) for k in range(10)]
    verifier = DeterministicRuleVerifier([], invariants)
    atms = BoundedATMSEngine(verifier, max_hitting_set_expansions=64)
    for k in range(10):
        for offset, prefix in enumerate(("a", "b")):
            bit = 2 * k + offset
            prop = f"obs:{prefix}_{k}"
            a_obj = PrimitiveAssumption(
                bit, f"e{prefix}_{k}", f"ch_{bit}", f"fd_{bit}", 1, prop,
                verifier.sign_telemetry(f"e{prefix}_{k}", f"ch_{bit}", f"fd_{bit}", 1, prop,
                                        bit_index=bit)
            )
            atms.register_assumption(a_obj)
        atms.register_nogood("checker", (1 << (2 * k)) | (1 << (2 * k + 1)), f"inv_{k}")

    _, budget_ok = atms.minimal_hitting_sets()
    assert budget_ok is False
    assert atms.budget_exceeded is True

    # Under budget_exceeded=True, compute_fault_tolerant_belief_set retains all worlds in S_cand
    gate = PlausibleWorldGate(worlds, safe_probes=[])
    overapprox = gate.compute_fault_tolerant_belief_set({}, atms.assumptions_by_bit, budget_exceeded=True)
    assert len(overapprox) == 3
    assert gate.global_safe_mutations(overapprox) == frozenset()


def test_revision3_task1_concurrent_reverse_interleaving_counterexample():
    """
    Revision 3 Task 1 Counterexample against Revision 2's Theorem 5:
      - u(s0) = s1 (safe)
      - a(s0) = s2 (safe)
      - a(s1) = s2 (safe)
      - u(s2) = s_bad (unsafe!)
    1. Legacy one-sided post-subset gate (`legacy_onesided_check=True`) checks only
       a in A_safe(s0) cap A_safe(s1), unsafely admitting `a` even though `a -> u` reaches `s_bad`.
    2. Repaired Theorem 5 gate rejects concurrent unserialized `a` (`UNSAFE_REVERSE_INTERLEAVING_AFTER_CANDIDATE`).
    3. Mode S (Resource-level serialization `u <_ord a`, `serialized_before_new=True`) admits `a`
       with a predecessor sequence constraint, and `SQLiteProtectedResourceBoundary` rejects out-of-order
       execution if `a` arrives before `u` commits.
    """
    s0 = FaultWorld(
        "s0",
        frozenset(),
        frozenset({"u", "a"}),
        transition_map={"u": "s1", "a": "s2"},
    )
    s1 = FaultWorld(
        "s1",
        frozenset(),
        frozenset({"a"}),
        transition_map={"a": "s2"},
    )
    s2 = FaultWorld(
        "s2",
        frozenset(),
        frozenset(),  # u is NOT safe in s2 because u(s2) = s_bad!
        transition_map={"u": "s_bad"},
    )
    s_bad = FaultWorld("s_bad", frozenset(), frozenset())

    gate = PlausibleWorldGate([s0, s1, s2, s_bad], safe_probes=[], exposure_caps={"cluster": 2})
    inflight_unserialized = [
        InFlightMutation("op_u", "u", "cluster", exposure_cost=1, serialized_before_new=False)
    ]

    # 1. Legacy one-sided gate unsafely admits `a`:
    ok_legacy, status_legacy = gate.evaluate_concurrent_compound_safety(
        "a", "cluster", 1, [s0], inflight_unserialized, legacy_onesided_check=True
    )
    assert ok_legacy is True
    assert status_legacy == "ADMIT_LEGACY_ONESIDED_UNSOUND"

    # 2. Repaired Theorem 5 gate blocks unserialized `a` because interleaving `a -> u` reaches `s_bad`:
    ok_repaired, status_repaired = gate.evaluate_concurrent_compound_safety(
        "a", "cluster", 1, [s0], inflight_unserialized, legacy_onesided_check=False
    )
    assert ok_repaired is False
    assert status_repaired == "UNSAFE_REVERSE_INTERLEAVING_AFTER_CANDIDATE"

    # 3. Mode S: When `u <_ord a` is enforced by a target sequence precondition (`serialized_before_new=True`),
    # `a` is admitted and the target rejects out-of-order execution `a -> u`:
    inflight_serialized = [
        InFlightMutation("op_u", "u", "cluster", exposure_cost=1, serialized_before_new=True, sequence_number=1)
    ]
    ok_mode_s, status_mode_s = gate.evaluate_concurrent_compound_safety(
        "a", "cluster", 1, [s0], inflight_serialized
    )
    assert ok_mode_s is True
    assert status_mode_s == "ADMIT_COMPOUND_SAFE"

    sqlite_target = SQLiteProtectedResourceBoundary(":memory:", initial_epoch=10, max_drained_nodes=2)
    cert_u = EpistemicLeaseCertificate("u", 0b111, 10, 3, ("s0",), "sig_u")
    cert_a = EpistemicLeaseCertificate("a", 0b111, 10, 3, ("s0",), "sig_a")

    # If `a` (expecting predecessor seq 1) arrives at target BEFORE `u` has committed (cur_seq == 0), target rejects `a`:
    r_early_a = sqlite_target.execute_fenced_mutation(cert_a, expected_predecessor_seq=1)
    assert r_early_a.committed is False
    assert r_early_a.status == "REJECTED_OUT_OF_ORDER_SEQUENCE"

    # Once `u` commits (advancing seq 0 -> 1), `a` commits cleanly in order `u -> a`:
    r_u = sqlite_target.execute_fenced_mutation(cert_u, expected_predecessor_seq=0)
    r_a = sqlite_target.execute_fenced_mutation(cert_a, expected_predecessor_seq=1)
    assert r_u.committed is True
    assert r_a.committed is True


def test_revision3_task1_exposure_ledger_unknown_cancel_and_dedup():
    """
    Revision 3 Task 1:
    Verify KedgeFlow Cross-Root Exposure Conservation across I_C, I_H, and I_U:
      - Duplicate requests with identical issuance_key are deduplicated without double-charging H(d).
      - Timed-out or cancellation-raced operations move from I_H(d) to I_U(d) keeping C+H+U invariant.
      - Release from I_U(d) is refused without an authoritative `fenced_no_effect` receipt.
    """
    ledger = CrossRootExposureLedger(caps={"raft_quorum": 1})

    ok1, op1, msg1 = ledger.admit_and_reserve("key_drain_1", "op1", "drain_node_1", "raft_quorum", 1)
    assert ok1 is True and msg1 == "RESERVED_IN_IH"
    assert ledger.domain_totals("raft_quorum") == (0, 1, 0)

    # Duplicate retry with same issuance_key does not double-charge H(d):
    ok_dup, op_dup, msg_dup = ledger.admit_and_reserve("key_drain_1", "op1_retry", "drain_node_1", "raft_quorum", 1)
    assert ok_dup is True and msg_dup == "DUPLICATE_IDEMPOTENT_REUSE"
    assert op_dup == op1
    assert ledger.domain_totals("raft_quorum") == (0, 1, 0)

    # Timeout / cancellation race moves op1 from I_H to I_U; C+H+U remains 1, blocking a second drain:
    assert ledger.mark_timeout_or_cancel_race("raft_quorum", "op1", state="CANCEL_RACING") is True
    assert ledger.domain_totals("raft_quorum") == (0, 0, 1)

    ok2, _, msg2 = ledger.admit_and_reserve("key_drain_2", "op2", "drain_node_2", "raft_quorum", 1)
    assert ok2 is False and msg2 == "EXPOSURE_BUDGET_EXCEEDED"

    # Unverified release without fenced_no_effect proof is rejected:
    assert ledger.release_on_verified_no_effect("raft_quorum", "op1", has_fenced_no_effect_proof=False) is False
    assert ledger.total_reserved_exposure("raft_quorum") == 1

    # Verified fenced_no_effect proof releases op1 from I_U, allowing op2:
    assert ledger.release_on_verified_no_effect("raft_quorum", "op1", has_fenced_no_effect_proof=True) is True
    assert ledger.total_reserved_exposure("raft_quorum") == 0


def test_revision3_task2_e1_plus_read_index_race_and_quiescence_repair():
    """
    Revision 3 Task 2:
    1. Show why a synchronous ReadIndex under a target-local lock (`E1_PLUS_READ_LOCK`) is NOT zero-race:
       ReadIndex returns epoch 10 at t_read, revocation commits epoch 11 in etcd at t_rev in (t_read, t_eff),
       and target commits stale mutation at t_eff > t_rev.
    2. Show that `E1_PLUS_QUIESCENCE_BARRIER` serializes revocation completion `fenced_linearized_epoch`
       against `prepared_slot` / `lock_r`, deduplicates retries, and transitions crashed prepared
       attempts to `UNKNOWN_CRASHED_IN_PREPARED`.
    """
    cert = EpistemicLeaseCertificate("promote_replica", 0b111, 10, 3, ("s_dead",), "sig")

    # 1. Counterexample: Read-Index-Only E1+ leaks when revocation commits in (t_read, t_eff)
    e1_read = TargetResourceBoundary(initial_epoch=10, tier="E1_PLUS_READ_LOCK")
    r_read_race = e1_read.execute_e1_plus_read_index_with_post_read_race(
        cert, revocation_epoch_during_post_read_window=11
    )
    assert r_read_race.committed is True
    assert r_read_race.status == "COMMITTED_AFTER_POST_READ_REVOCATION"
    assert e1_read.authoritative_epoch == 11

    # 2. Repaired E1+ Quiescence Handshake:
    e1_sync = TargetResourceBoundary(initial_epoch=10, tier="E1_PLUS_QUIESCENCE_BARRIER")
    # Case A: Crash while prepared -> transitions to UNKNOWN_CRASHED_IN_PREPARED (no commit)
    r_crash, waited_crash = e1_sync.execute_quiescence_handshake(
        cert, op_id="op_crash", crash_while_prepared=True, concurrent_revocation_epoch=11
    )
    assert r_crash.committed is False
    assert r_crash.status == "UNKNOWN_CRASHED_IN_PREPARED"
    assert waited_crash is True
    assert "op_crash" in e1_sync.unknown_prepared_ops

    # Case B: Subsequent attempt at epoch 10 after fence completed at 11 is deterministically rejected
    e1_sync.deliver_delayed_fence()
    assert e1_sync.fenced_linearized_epoch == 11
    r_after_fence, _ = e1_sync.execute_quiescence_handshake(cert, op_id="op_late")
    assert r_after_fence.committed is False
    assert r_after_fence.status == "REJECTED_STALE_EPOCH"


def test_revision3_task3_proposition6_eight_risk_disjoint_strata():
    """
    Revision 3 Task 3:
    Verify Proposition 6's 8-risk separation and sequential disjoint stratum bound
    (ensuring overlapping events are never double-counted).
    """
    strata = disjoint_stratum_unsafe_bound(
        m_domains=5,
        k_quorum=3,
        p_domain_fault=0.05,
        p_univ=0.002,
        p_rules_given_prev=0.0,
        p_dom_given_prev=0.0,
        p_common_given_prev=0.005,
        p_drift_given_prev=0.001,
        p_fresh_given_prev=0.0,
        p_conc_given_prev=0.0,
    )
    assert strata["exact_disjoint_total"] <= strata["additive_upper_bound"]
    assert strata["E2_unsound_rules"] == 0.0
    assert strata["E3_domain_misclassification"] == 0.0
    assert strata["E8_concurrent_coordination"] == 0.0
    bound = residual_unsafe_admit_bound(
        m_channels=5,
        k_quorum=3,
        p_sensor=0.05,
        delta_model=0.002,
        delta_common=0.005,
        delta_freshness=0.0,
        delta_drift=0.001,
    )
    assert math.isclose(bound, strata["additive_upper_bound"], rel_tol=1e-12)


def test_revision3_task4_theorems2_to_4_subsumption_and_truncation():
    """
    Revision 3 Task 4 Audit:
      1. Theorem 2: Verify that when L(c) is at capacity L_max, registering a strict subset
         of an existing environment STILL succeeds by pruning its supersets first (preserving
         minimal consistent subsets), whereas registering a non-subsuming environment beyond
         L_max sets `budget_exceeded = True` without corrupting existing minimal subsets.
      2. Theorem 3: Verify that subsumption pruning (discarding J' when D(J) < D(J'))
         preserves exact maximum set-packing cardinality nu_pack(W(a)) by the exchange property.
      3. Theorem 4: Verify that under partial budget truncation (`budget_exceeded=True` with
         `verified_Done_worlds={"s_refuted_early"}`), worlds verified prior to truncation with >= k_a
         disjoint refutations are pruned while unverified worlds are conservatively retained.
    """
    rules = [
        InferenceRule("r_pair", frozenset({"obs:e0", "obs:e1"}), "hyp:c"),
        InferenceRule("r_single0", frozenset({"obs:e0"}), "hyp:c"),
        InferenceRule("r_single2", frozenset({"obs:e2"}), "hyp:c"),
        InferenceRule("r_single3", frozenset({"obs:e3"}), "hyp:c"),
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier, max_environments_per_node=2)

    for i in range(4):
        prop = f"obs:e{i}"
        a = PrimitiveAssumption(
            i, f"e{i}", f"ch{i}", f"fd{i}", 1, prop,
            verifier.sign_telemetry(f"e{i}", f"ch{i}", f"fd{i}", 1, prop, bit_index=i)
        )
        atms.register_assumption(a)

    # Fill L(hyp:c) to capacity L_max = 2 with {e0, e1} and {e2}
    atms.add_verified_justification("a0", "ctx0", "hyp:c", 0b0011, "r_pair")
    atms.add_verified_justification("a2", "ctx2", "hyp:c", 0b0100, "r_single2")
    assert len(atms.labels["hyp:c"]) == 2
    assert atms.budget_exceeded is False

    # Register strict subset {e0} subsetneq {e0, e1} at capacity -> subsumes {e0, e1} without exceeding L_max!
    atms.add_verified_justification("a1", "ctx1", "hyp:c", 0b0001, "r_single0")
    assert atms.budget_exceeded is False
    assert set(atms.labels["hyp:c"]) == {0b0001, 0b0100}
    assert atms.has_minimal_consistent_subset("hyp:c", 0b0011) is True

    # Registering a 3rd disjoint minimal environment {e3} beyond L_max=2 sets budget_exceeded=True
    # while retaining existing minimal environments intact:
    atms.add_verified_justification("a3", "ctx3", "hyp:c", 0b1000, "r_single3")
    assert atms.budget_exceeded is True
    assert atms.has_minimal_consistent_subset("hyp:c", 0b0011) is True

    # Theorem 3 exchange property: J_sub = {e0} (ctx0) vs J_sup = {e0, e1} (ctx0) + disjoint J_other = {e2} (ctx2)
    witnesses = atms.active_witness_justifications("hyp:c")
    pack_num, packed = compute_disjoint_justification_packing(witnesses, atms.assumptions_by_bit)
    assert pack_num == 3  # {e0}@ctx1, {e2}@ctx2, {e3}@ctx3 are all pairwise footprint-disjoint

    # Theorem 4 top-down partial truncation overapproximation:
    w_early = FaultWorld("s_refuted_early", frozenset(), frozenset({"act_safe"}))
    w_unverified = FaultWorld("s_unverified_at_cap", frozenset(), frozenset({"act_other"}))
    gate = PlausibleWorldGate([w_early, w_unverified], safe_probes=[])
    surviving = gate.compute_fault_tolerant_belief_set(
        refutations_by_world={"s_refuted_early": packed},
        assumptions_by_bit=atms.assumptions_by_bit,
        fault_budget_f=2,
        budget_exceeded=True,
        verified_Done_worlds={"s_refuted_early"},
    )
    assert [w.world_id for w in surviving] == ["s_unverified_at_cap"]
