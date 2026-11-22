"""
Revision 6 Independent Audit Repairs & Counterexamples Regression Suite.

Tests every mathematical and execution-contract finding from the Revision 6 audit:
  1. Priority 0.A: Separation of positive proof packing (precondition soundness c_a)
     vs. refutation packing + plausible-world shielding (physical invariant preservation K).
     - Removing ONLY positive packing preserves K under <= f faulty domains while admitting
       a false-precondition / unwarranted mutation (not c_a).
     - Removing/weakening REFUTATION packing (e.g., raw counts or Menger flow) breaks
       true-state retention and causes an actual invariant violation under <= f faulty domains.
  2. Priority 0.B: Theorem 1 bounded-probability variance inconsistency vs. unbounded latent
     score residuals z_i^{(t)} in R with nonlinear probability readout y_i^{(t)} = sigmoid(z_i^{(t)}).
  3. Priority 0.C (Theorem 5 Part 2): Commit-version gap counterexample (unacknowledged I_U op
     committed at v=1 before known commit c at v=2) caught by contiguous-version barrier.
  4. Priority 0.C (Assumption 3): Torn-snapshot refutation mixing observation versions
     conservatively retains the affected world.
  5. Priority 2: Concurrent Mode S admission-revision CAS, full ledger revision bumps on all
     state mutations, issuance-key conflict rejection, fail-closed auth_valid_until clock check,
     record_fenced_no_effect return safety, SQLite WAL PRAGMA synchronous=FULL, and
     target-side cryptographic certificate signature & manifest_hash verification.
"""

from __future__ import annotations

from dataclasses import replace
import math
import numpy as np

from pieq.core import (
    BoundedATMSEngine,
    CrossRootExposureLedger,
    DeterministicRuleVerifier,
    EpistemicLeaseCertificate,
    FaultWorld,
    InFlightMutation,
    InferenceRule,
    PlausibleWorldGate,
    PrimitiveAssumption,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
    TargetResourceBoundary,
    compute_disjoint_justification_packing_bounded,
    compute_provenance_min_cut,
    propagate_conformity_covariance,
)


def test_p0a_positive_packing_not_necessary_for_invariant_safety_but_refutation_packing_is():
    """
    Priority 0.A Constructive Verification:
      - World s_healthy: primary is actually alive; `promote_replica` violates invariant (split-brain!),
        while `throttle_reads` preserves invariant K even though precondition c_throttle is FALSE.
      - World s_dead: primary is dead; both `promote_replica` and `throttle_reads` preserve K.
      - True state is S_true = s_healthy. Fault budget f = 2 (k_a = 3).
      - Part 1: Remove ONLY positive proof packing (nu_pack(W(throttle_reads)) == 0).
        With refutation packing intact and <= 2 faulty domains, s_healthy stays in hat{B}^{(2)}.
        Therefore `promote_replica` is BLOCKED (invariant K preserved!) while `throttle_reads`
        preserves K even with 0 positive proofs — yet is an UNWARRANTED mutation because its
        precondition c_throttle is false (only positive packing prevents false-precondition triggers!).
      - Part 2: Weakening REFUTATION packing (using raw counts on 1 faulty shared failure domain,
        or Menger flow on overlapping conjunctive refutations from 2 faulty domains <= f)
        falsely ejects s_healthy from the belief set and admits `promote_replica`, violating K!
    """
    rules = [
        InferenceRule("r_ref0", frozenset({"obs:e0"}), "refute:s_healthy"),
        InferenceRule("r_ref1", frozenset({"obs:e1"}), "refute:s_healthy"),
        InferenceRule("r_ref2", frozenset({"obs:e2"}), "refute:s_healthy"),
        InferenceRule("r_conj01", frozenset({"obs:c0", "obs:c1"}), "refute:s_healthy"),
        InferenceRule("r_conj12", frozenset({"obs:c1", "obs:c2"}), "refute:s_healthy"),
        InferenceRule("r_conj02", frozenset({"obs:c0", "obs:c2"}), "refute:s_healthy"),
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier)

    # 3 channels sharing ONE faulty failure domain fd_shared_tor (1 <= f = 2 faulty domains!)
    shared_refs = []
    for i in range(3):
        prop = f"obs:e{i}"
        sig = verifier.sign_telemetry(f"e{i}", f"ch{i}", "fd_shared_tor", 10, prop, 1, i)
        a = PrimitiveAssumption(i, f"e{i}", f"ch{i}", "fd_shared_tor", 10, prop, sig, 1)
        assert atms.register_assumption(a)
        j = atms.add_verified_justification(f"ag{i}", f"ctx{i}", "refute:s_healthy", a.bitmask, f"r_ref{i}")
        assert j is not None
        shared_refs.append(j)

    s_healthy = FaultWorld(
        "s_healthy", frozenset(), frozenset({"throttle_reads"}),
        transition_map={"throttle_reads": "s_healthy"}, viable=True,
    )
    s_dead = FaultWorld(
        "s_dead", frozenset(), frozenset({"promote_replica", "throttle_reads"}),
        transition_map={"throttle_reads": "s_dead", "promote_replica": "s_dead"}, viable=True,
    )

    gate = PlausibleWorldGate([s_healthy, s_dead], safe_probes=[])

    # Part 1: Refutation packing keeps s_healthy in hat{B}^{(2)} despite 3 shared-domain refutations!
    plausible = gate.compute_fault_tolerant_belief_set(
        {"s_healthy": shared_refs}, atms.assumptions_by_bit, fault_budget_f=2
    )
    assert {w.world_id for w in plausible} == {"s_healthy", "s_dead"}
    safe_actions = gate.global_safe_mutations(plausible)
    assert "promote_replica" not in safe_actions  # Invariant K preserved without positive packing!
    assert "throttle_reads" in safe_actions       # Preserves K even with nu_pack(W(throttle_reads)) == 0!

    # Yet positive packing blocks unwarranted `throttle_reads` (nu_pack == 0 < k_a = 3):
    nu_pos, _, _ = compute_disjoint_justification_packing_bounded(
        atms.active_witness_justifications("act:throttle_reads"), atms.assumptions_by_bit
    )
    assert nu_pos == 0

    # Part 2A: Removing refutation packing (raw refutation count >= 3) ejects s_healthy -> UNSAFE!
    assert len(shared_refs) == 3  # >= k_a = 3 raw proofs from 1 faulty domain
    naive_belief_raw = [w for w in [s_healthy, s_dead] if (len(shared_refs) if w.world_id == "s_healthy" else 0) < 3]
    assert {w.world_id for w in naive_belief_raw} == {"s_dead"}
    assert "promote_replica" in gate.global_safe_mutations(naive_belief_raw)  # Split-brain admitted!

    # Part 2B: Replacing refutation packing with Menger path flow on overlapping conjunctive refutations
    # (only 2 faulty domains c0, c1 <= f=2 make all 3 refutations {c0,c1}, {c1,c2}, {c0,c2} fire) -> UNSAFE!
    atms2 = BoundedATMSEngine(verifier)
    for i in range(3):
        prop = f"obs:c{i}"
        sig = verifier.sign_telemetry(f"c{i}", f"chc{i}", f"fdc{i}", 10, prop, 1, i)
        assert atms2.register_assumption(PrimitiveAssumption(i, f"c{i}", f"chc{i}", f"fdc{i}", 10, prop, sig, 1))
    j01 = atms2.add_verified_justification("a0", "ctx0", "refute:s_healthy", 0b011, "r_conj01")
    j12 = atms2.add_verified_justification("a1", "ctx1", "refute:s_healthy", 0b110, "r_conj12")
    j02 = atms2.add_verified_justification("a2", "ctx2", "refute:s_healthy", 0b101, "r_conj02")
    conj_refs = [j01, j12, j02]
    assert compute_provenance_min_cut(conj_refs, atms2.assumptions_by_bit) == 3  # Menger falsely says >= 3!
    nu_ref, _, _ = compute_disjoint_justification_packing_bounded(conj_refs, atms2.assumptions_by_bit)
    assert nu_ref == 1  # Exact refutation packing preserves s_healthy!
    plausible_conj = gate.compute_fault_tolerant_belief_set(
        {"s_healthy": conj_refs}, atms2.assumptions_by_bit, fault_budget_f=2
    )
    assert "s_healthy" in {w.world_id for w in plausible_conj}


def test_p0b_theorem1_bounded_probability_inconsistency_vs_unbounded_latent_scores():
    """
    Priority 0.B Verification:
      1. For any [0, 1]-valued random vector y in [0, 1]^N, Popoviciu's inequality forces
         Var(y_i) <= 1/4 and hence consensus-mode eigenvalue lambda_parallel <= N / 4.
      2. Under persistent white innovations (sigma_eta^2 > 0), the linear recursion
         grows lambda_parallel^{(t)} = Theta(t) strictly above N / 4 in finite rounds —
         confirming it is mathematically valid ONLY on unbounded latent scores z^{(t)} in R^N.
      3. Applying nonlinear probability readout y_i^{(t)} = sigmoid(z_i^{(t)}) keeps
         probability variance strictly bounded by 1/4 across long horizons.
    """
    n = 5
    sigma_t = propagate_conformity_covariance(
        n_agents=n, sigma0_sq=0.2, rho0=0.1, gamma=0.4, rounds=20, tau_dec_sq=0.2, rho_model=0.3
    )
    eigvals = np.linalg.eigvalsh(sigma_t)
    lambda_par = float(np.max(eigvals))
    popoviciu_cap = n / 4.0  # 1.25 for N = 5
    assert lambda_par > popoviciu_cap  # Impossible for [0, 1]-valued probabilities!

    # Monte Carlo check of nonlinear logistic readout y = 1 / (1 + exp(-z)) staying bounded in [0, 1]
    rng = np.random.default_rng(2026)
    z = rng.normal(0.0, math.sqrt(0.2), size=(2000, n))
    w = 0.6 * np.eye(n) + (0.4 / n) * np.ones((n, n))
    q_chol = np.linalg.cholesky(0.2 * (0.7 * np.eye(n) + 0.3 * np.ones((n, n))))
    for _ in range(20):
        z = z @ w.T + rng.standard_normal((2000, n)) @ q_chol.T
    y = 1.0 / (1.0 + np.exp(-z))
    assert np.all((y >= 0.0) & (y <= 1.0))
    cov_y = np.cov(y, rowvar=False)
    assert float(np.max(np.linalg.eigvalsh(cov_y))) <= popoviciu_cap + 1e-6


def test_p0c_commit_version_gap_counterexample_and_contiguous_barrier():
    """
    Revision 6 Theorem 5 Part 2 Counterexample & Repair:
      - Snapshot v_snap = 0 observes x = 0 (`s_0`).
      - Operation u in I_U (lost ack) actually committed at v = 1: x := 10 (`assign_10`).
      - Operation c in I_C (confirmed) committed at v = 2: x := x + 1 (`inc_1`).
      - True physical state is x = 11 (`s_11`), where `a` is UNSAFE!
      - Without contiguous-version checking, advancing only known c (v=2) reaches {s_1} and
        applying u afterwards reaches {s_1, s_10}, MISSING s_11 and unsafely admitting `a`!
      - With `require_contiguous_versions=True`, seeing v=2 after v_snap=0 with missing v=1
        blocks fail-closed (`BLOCKED_COMMIT_VERSION_GAP_RECONCILIATION_REQUIRED`).
    """
    s_0 = FaultWorld("s_0", frozenset(), frozenset({"a", "assign_10", "inc_1"}), transition_map={"assign_10": "s_10", "inc_1": "s_1"})
    s_1 = FaultWorld("s_1", frozenset(), frozenset({"a", "assign_10"}), transition_map={"assign_10": "s_10", "a": "s_1"})
    s_10 = FaultWorld("s_10", frozenset(), frozenset({"a", "inc_1"}), transition_map={"inc_1": "s_11", "a": "s_10"})
    s_11 = FaultWorld("s_11", frozenset(), frozenset(), viable=True)  # `a` NOT safe at x=11!

    gate = PlausibleWorldGate([s_0, s_1, s_10, s_11], safe_probes=[], exposure_caps={"g": 5})
    u_unknown = InFlightMutation("op_u", "assign_10", "g", lifecycle_state="UNKNOWN", serialized_before_new=True)
    c_v2 = InFlightMutation("op_c", "inc_1", "g", lifecycle_state="COMMITTED", serialized_before_new=True, commit_version=2)

    # 1. Legacy non-contiguous mode misses true state s_11 and unsafely admits `a`:
    ok_legacy, reason_legacy, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s_0], [c_v2, u_unknown],
        snapshot_context=SnapshotContext(snapshot_version=0, require_versioned_commits=True, require_contiguous_versions=False),
    )
    assert ok_legacy is True and reason_legacy == "ADMIT_COMPOUND_SAFE"

    # 2. Contiguous-version barrier catches missing version 1 and fails closed!
    ok_contig, reason_contig, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s_0], [c_v2, u_unknown],
        snapshot_context=SnapshotContext(snapshot_version=0, require_versioned_commits=True, require_contiguous_versions=True),
    )
    assert ok_contig is False
    assert reason_contig == "BLOCKED_COMMIT_VERSION_GAP_RECONCILIATION_REQUIRED"

    # 3. Once reconciliation recovers u's commit at v=1, contiguous history [v=1, v=2] reaches s_11 and blocks `a`:
    u_reconciled_v1 = InFlightMutation("op_u", "assign_10", "g", lifecycle_state="COMMITTED", serialized_before_new=True, commit_version=1)
    ok_rec, reason_rec, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s_0], [c_v2, u_reconciled_v1],
        snapshot_context=SnapshotContext(snapshot_version=0, require_versioned_commits=True, require_contiguous_versions=True),
    )
    assert ok_rec is False and reason_rec == "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE"


def test_p0c_torn_snapshot_refutation_conservatively_retains_world():
    """
    Assumption 3 Repair Verification:
    A refutation DAG combining leaves from differing observation_versions (v=1 and v=5)
    is rejected from pruning the world when `require_consistent_observation_version=True`.
    """
    rules = [
        InferenceRule("r_torn", frozenset({"obs:old_v1", "obs:new_v5"}), "refute:s_true"),
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier)
    sig0 = verifier.sign_telemetry("e0", "ch0", "fd0", 10, "obs:old_v1", 1, 0)
    sig1 = verifier.sign_telemetry("e1", "ch1", "fd1", 10, "obs:new_v5", 5, 1)
    assert atms.register_assumption(PrimitiveAssumption(0, "e0", "ch0", "fd0", 10, "obs:old_v1", sig0, 1))
    assert atms.register_assumption(PrimitiveAssumption(1, "e1", "ch1", "fd1", 10, "obs:new_v5", sig1, 5))
    j_torn = atms.add_verified_justification("ag0", "ctx0", "refute:s_true", 0b11, "r_torn")
    assert j_torn is not None

    s_true = FaultWorld("s_true", frozenset(), frozenset({"safe_act"}))
    gate = PlausibleWorldGate([s_true], safe_probes=[])

    # With fault_budget_f=0 (1 refutation would otherwise prune s_true), torn-snapshot check retains s_true!
    surviving = gate.compute_fault_tolerant_belief_set(
        {"s_true": [j_torn]},
        atms.assumptions_by_bit,
        fault_budget_f=0,
        require_consistent_observation_version=True,
    )
    assert [w.world_id for w in surviving] == ["s_true"]


def test_p2_mode_s_cas_ledger_revs_and_sqlite_durability_and_cert_checks(tmp_path):
    """
    Priority 2 Certificate, Revision CAS, and Durability Verification:
      1. Concurrent Mode S coordinators racing at the same r_adm(g): CAS accepts first and rejects second.
      2. All ledger mutations (confirm_commit, mark_timeout, release_on_verified_no_effect) increment r_adm(g).
      3. Reusing issuance_key with a different action is rejected (`REJECTED_ISSUANCE_KEY_CONFLICT`).
      4. `auth_valid_until` with `now_time=None` fails closed (`REJECTED_AUTHORIZATION_CLOCK_UNAVAILABLE`).
      5. `record_fenced_no_effect` returns False if op_id was already COMMITTED.
      6. SQLite WAL enforces `PRAGMA synchronous=FULL` (value 2) and verifies cryptographic cert signatures
         and `manifest_hash` when configured.
    """
    ledger = CrossRootExposureLedger({"g": 5})
    ok_u, _, _ = ledger.admit_and_reserve("k_u", "op_u", "u", "g", 1, serialized_before_new=True)
    assert ok_u
    rev_before = ledger.admission_revisions(["g"])
    assert rev_before == {"g": 1}

    # Concurrent Mode S coordinators both read rev_before == {"g": 1}
    ok_c1, _, _ = ledger.admit_and_reserve(
        "k_a1", "op_a1", "a1", "g", 1, serialized_before_new=True,
        predecessor_op_ids=("op_u",), expected_admission_revs=rev_before,
    )
    assert ok_c1
    ok_c2, _, reason_c2 = ledger.admit_and_reserve(
        "k_a2", "op_a2", "a2", "g", 1, serialized_before_new=True,
        predecessor_op_ids=("op_u",), expected_admission_revs=rev_before,
    )
    assert not ok_c2 and reason_c2 == "REJECTED_STALE_CONCURRENCY_CERTIFICATE_REVALIDATION_REQUIRED"

    # Issuance-key conflict rejected
    ok_conf, _, reason_conf = ledger.admit_and_reserve("k_a1", "op_diff", "different_action", "g", 1)
    assert not ok_conf and reason_conf == "REJECTED_ISSUANCE_KEY_CONFLICT"

    # Every reservation state transition advances domain_admission_rev
    r_prev = ledger.domain_admission_rev["g"]
    assert ledger.mark_timeout_or_cancel_race("g", "op_a1") is True
    assert ledger.domain_admission_rev["g"] == r_prev + 1
    assert ledger.release_on_verified_no_effect("g", "op_a1", has_fenced_no_effect_proof=True) is True
    assert ledger.domain_admission_rev["g"] == r_prev + 2
    assert ledger.confirm_commit("g", "op_u", commit_version=1) is True
    assert ledger.domain_admission_rev["g"] == r_prev + 3

    # SQLite WAL durability + cert signature + manifest_hash + fail-closed clock + record_fenced_no_effect
    signing_key = b"pieq-control-plane-key-v6"
    db_path = str(tmp_path / "p2_audit.sqlite")
    target = SQLiteProtectedResourceBoundary(
        db_path, initial_epoch=10, max_drained_nodes=2,
        signing_key=signing_key, expected_manifest_hash="sha256:manifest-v6",
    )
    sync_mode = target.conn.execute("PRAGMA synchronous;").fetchone()[0]
    assert sync_mode == 2  # FULL

    base_cert = EpistemicLeaseCertificate(
        action="drain_node_1",
        support_mask=0b111,
        admission_epoch=10,
        packing_number=3,
        plausible_worlds=("s0",),
        signature="forged_sig",
        auth_epoch=1,
        auth_valid_until=100,
        resource_id="db_cluster",
        manifest_hash="sha256:manifest-v6",
        snapshot_version=4,
        proof_manifest_digest="sha256:proofs-v6",
        admission_revisions=(("db_cluster", 1),),
    )

    # Forged signature rejected
    r_bad_sig = target.execute_fenced_mutation(base_cert, target_node="node_1", op_id="op_1", now_time=50)
    assert not r_bad_sig.committed and r_bad_sig.status == "REJECTED_INVALID_CERTIFICATE_SIGNATURE"

    # Stale manifest hash rejected
    stale_man_cert = replace(base_cert, manifest_hash="sha256:manifest-old")
    stale_man_cert = replace(stale_man_cert, signature=stale_man_cert.compute_signature(signing_key))
    r_bad_man = target.execute_fenced_mutation(stale_man_cert, target_node="node_1", op_id="op_1", now_time=50)
    assert not r_bad_man.committed and r_bad_man.status == "REJECTED_STALE_MANIFEST_HASH"

    # Valid signed cert with auth_valid_until but missing clock (now_time=None) fails closed!
    valid_cert = replace(base_cert, signature=base_cert.compute_signature(signing_key))
    r_no_clock = target.execute_fenced_mutation(valid_cert, target_node="node_1", op_id="op_1", now_time=None)
    assert not r_no_clock.committed and r_no_clock.status == "REJECTED_AUTHORIZATION_CLOCK_UNAVAILABLE"

    # Same fail-closed clock behavior on TargetResourceBoundary (E1+-quiescence & E2)
    t_e1q = TargetResourceBoundary(initial_epoch=10, tier="E1_PLUS_QUIESCENCE_BARRIER")
    r_q_nc, _ = t_e1q.execute_quiescence_handshake(valid_cert, op_id="q_nc", now_time=None)
    assert not r_q_nc.committed and r_q_nc.status == "REJECTED_AUTHORIZATION_CLOCK_UNAVAILABLE"

    # Valid execution commits, and record_fenced_no_effect returns False for already-COMMITTED op!
    r_ok = target.execute_fenced_mutation(valid_cert, target_node="node_1", op_id="op_1", now_time=50)
    assert r_ok.committed and r_ok.status == "COMMITTED"
    assert target.record_fenced_no_effect("op_1", "drain_node_1") is False
    assert target.record_fenced_no_effect("op_never_ran", "drain_node_2") is True
    assert target.record_fenced_no_effect("op_never_ran", "drain_node_2") is True

    # PRAGMA synchronous=FULL preserved across reopen()
    target.reopen()
    assert target.conn.execute("PRAGMA synchronous;").fetchone()[0] == 2
