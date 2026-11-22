"""
Revision 7 P0 Regression Tests — Execution Semantics Closure.

Covers:
  - P0.1: Minimal counterexample separating Tier E2 atomic execution-time authorization
    freshness from Tier E1+-quiescence fence-relative exclusion (pre-FenceAck race window).
  - P0.2: Minimal counterexample separating SnapshotPreconditionValid(a, v_snap) from
    CommitPreconditionValid(a, t_eff), contrasting historical-snapshot actions (no forced
    revalidation) against commit-time-truth actions (optional Tier E2 atomic revalidation).
  - P0.3: Complete composition audit of Corollary 1 and tier-indexed BadCommit_T accounting
    in Proposition 6.
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
    PlausibleWorldGate,
    SQLiteProtectedResourceBoundary,
    TargetResourceBoundary,
    compute_disjoint_justification_packing_bounded,
)


def _signed_cert(
    action: str,
    *,
    admission_epoch: int = 10,
    auth_epoch: int = 5,
    auth_valid_until: int | None = 1000,
    resource_id: str = "db_cluster",
    manifest_hash: str = "mhash_rev7",
    snapshot_version: int = 10,
    signing_key: bytes = b"rev7-test-key",
) -> EpistemicLeaseCertificate:
    cert = EpistemicLeaseCertificate(
        action=action,
        support_mask=0b111,
        admission_epoch=admission_epoch,
        packing_number=3,
        plausible_worlds=("s_overloaded",),
        signature="",
        auth_epoch=auth_epoch,
        auth_valid_until=auth_valid_until,
        resource_id=resource_id,
        manifest_hash=manifest_hash,
        snapshot_version=snapshot_version,
    )
    return EpistemicLeaseCertificate(
        action=cert.action,
        support_mask=cert.support_mask,
        admission_epoch=cert.admission_epoch,
        packing_number=cert.packing_number,
        plausible_worlds=cert.plausible_worlds,
        signature=cert.compute_signature(signing_key),
        auth_epoch=cert.auth_epoch,
        auth_valid_until=cert.auth_valid_until,
        resource_id=cert.resource_id,
        manifest_hash=cert.manifest_hash,
        snapshot_version=cert.snapshot_version,
    )


def test_p0_1_tier_e1_plus_quiescence_vs_tier_e2_authorization_freshness() -> None:
    """
    Minimal 6-step execution trace separating Tier E1+-quiescence from Tier E2:
      t=1: Certificate C minted with auth_epoch=5, admission_epoch=10.
      t=2: Target checks auth_epoch >= 5, acquires lock_r, enters prepared_slot.
      t=3 (t_rev_init): Control plane initiates auth revocation -> auth_epoch=6 (blocked on lock_r).
      t=4 (t_eff): Already-prepared operation commits physically under Tier E1+-quiescence
                   EVEN THOUGH revocation was initiated at t=3 < t=4.
      t=5 (t_fenced): lock_r drains, local_auth_epoch advances to 6, FenceAck completes.
      t=6: Post-FenceAck attempt with auth_epoch=5 is deterministically rejected.

    Contrast with Tier E2 (SQLite WAL): once revoke_authorization_atomically(6) linearizes,
    no execution with auth_epoch=5 can commit (zero pre-barrier window, delta_fresh^{E2} = 0).
    """
    key = b"rev7-p01-key"
    cert_e5 = _signed_cert("scale_out_cluster", admission_epoch=10, auth_epoch=5, signing_key=key)

    # --- Tier E1+-quiescence trace ---
    e1p = TargetResourceBoundary(initial_epoch=10, tier="E1_PLUS_QUIESCENCE_BARRIER")
    e1p.revoke_authorization_epoch(5, fence_message_delayed=False)

    receipt_prepared, blocked = e1p.execute_quiescence_handshake(
        cert_e5,
        op_id="op_pre_fence",
        concurrent_auth_revocation_epoch=6,
        now_time=500,
    )
    # Revocation arrived while lock_r held -> blocked until FenceAck, so prepared op committed!
    assert blocked is True
    assert receipt_prepared.committed is True
    assert receipt_prepared.status == "COMMITTED_PRE_FENCE_ACK_AUTH_REVOKED"
    assert e1p.fence_completion_declared is True
    assert e1p.local_auth_epoch == 6

    # After FenceAck (t >= t_fenced), stale auth_epoch=5 is deterministically excluded:
    receipt_post, blocked_post = e1p.execute_quiescence_handshake(
        cert_e5,
        op_id="op_post_fence",
        now_time=501,
    )
    assert blocked_post is False
    assert receipt_post.committed is False
    assert receipt_post.status == "REJECTED_AUTHORIZATION_REVOKED"

    # --- Tier E2 (SQLite WAL) atomic contrast ---
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "e2_auth.db")
        e2 = SQLiteProtectedResourceBoundary(
            db_path=db_path,
            initial_epoch=10,
            signing_key=key,
            expected_manifest_hash="mhash_rev7",
        )
        e2.revoke_authorization_atomically(5)
        e2.revoke_authorization_atomically(6)
        r_e2 = e2.execute_fenced_mutation(cert_e5, op_id="op_e2_after_rev", now_time=500)
        assert r_e2.committed is False
        assert r_e2.status == "REJECTED_AUTHORIZATION_REVOKED"
        assert e2.current_seq() == 0
        e2.conn.close()


def test_p0_2_snapshot_precondition_vs_commit_precondition_truth() -> None:
    """
    Minimal counterexample showing SnapshotPreconditionValid(a, v_snap) does NOT imply
    CommitPreconditionValid(a, t_eff):
      - At snapshot v_snap=10, 3 failure-domain-disjoint collectors attest obs:queue_high,
        certifying act:scale_out_cluster with nu_pack = 3 >= k_a = 3 (Theorem 3 holds) and
        isolating s_overloaded (Lemma 4 holds; scale_out_cluster is invariant-safe in all states).
      - Before t_eff, the transient traffic spike subsides so obs:queue_high is FALSE at t_eff
        while invariant K remains preserved and admission_epoch has not changed.
      - Historical-snapshot actions (e.g., revoke_iam_role upon verified compromise at v_snap)
        intentionally omit commit-time revalidation and commit validly based on SnapshotPreconditionValid.
      - Commit-time-truth actions explicitly specify required_commit_predicates or
        commit_precondition_check under Tier E2 and atomically reject when CommitPreconditionValid fails.
    """
    key = b"rev7-p02-key"
    rules = [
        InferenceRule("r_q0", frozenset({"obs:queue_high_0"}), "act:scale_out_cluster"),
        InferenceRule("r_q1", frozenset({"obs:queue_high_1"}), "act:scale_out_cluster"),
        InferenceRule("r_q2", frozenset({"obs:queue_high_2"}), "act:scale_out_cluster"),
        InferenceRule("r_ref0", frozenset({"obs:queue_high_0"}), "refute:s_idle"),
        InferenceRule("r_ref1", frozenset({"obs:queue_high_1"}), "refute:s_idle"),
        InferenceRule("r_ref2", frozenset({"obs:queue_high_2"}), "refute:s_idle"),
    ]
    verifier = DeterministicRuleVerifier(rules, [], secret_key=key)
    atms = BoundedATMSEngine(
        verifier,
        expected_epoch=10,
        expected_resource_id="db_cluster",
        expected_manifest_hash="mhash_rev7",
    )
    for i in range(3):
        sig = verifier.sign_telemetry(
            assumption_id=f"q_{i}",
            channel_id=f"collector_{i}",
            failure_domain=f"fd_{i}",
            epoch_revision=10,
            proposition=f"obs:queue_high_{i}",
            observation_version=10,
            bit_index=i,
            resource_id="db_cluster",
            manifest_hash="mhash_rev7",
        )
        a = BoundPrimitiveAssumption(
            bit_index=i,
            assumption_id=f"q_{i}",
            channel_id=f"collector_{i}",
            failure_domain=f"fd_{i}",
            epoch_revision=10,
            proposition=f"obs:queue_high_{i}",
            signature=sig,
            observation_version=10,
            resource_id="db_cluster",
            manifest_hash="mhash_rev7",
        )
        assert atms.register_assumption(a) is True
        assert atms.add_verified_justification(
            f"agent_{i}", f"agent_{i}:0", "act:scale_out_cluster", 1 << i, f"r_q{i}"
        ) is not None
        assert atms.add_verified_justification(
            f"agent_{i}", f"agent_{i}:0", "refute:s_idle", 1 << i, f"r_ref{i}"
        ) is not None

    # 1. Theorem 3 certifies SnapshotPreconditionValid(scale_out_cluster, v_snap=10) == True:
    act_pack, _, exhausted = compute_disjoint_justification_packing_bounded(
        atms.active_witness_justifications("act:scale_out_cluster"),
        atms.assumptions_by_bit,
        max_expansions=1000,
    )
    assert not exhausted and act_pack == 3  # >= k_a = 3

    # 2. Lemma 4 certifies snapshot invariant safety:
    worlds = [
        FaultWorld(
            "s_overloaded",
            frozenset({"obs:queue_high_0", "obs:queue_high_1", "obs:queue_high_2"}),
            frozenset({"scale_out_cluster", "defer"}),
        ),
        FaultWorld(
            "s_idle",
            frozenset(),
            frozenset({"scale_out_cluster", "defer"}),
        ),
    ]
    gate = PlausibleWorldGate(worlds, ["defer"])
    refs = {"s_idle": atms.active_witness_justifications("refute:s_idle"), "s_overloaded": []}
    survivors = gate.compute_fault_tolerant_belief_set(
        refs, atms.assumptions_by_bit, fault_budget_f=2, require_consistent_observation_version=True
    )
    assert [w.world_id for w in survivors] == ["s_overloaded"]

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "e2_precond.db")
        e2 = SQLiteProtectedResourceBoundary(
            db_path=db_path,
            initial_epoch=10,
            signing_key=key,
            expected_manifest_hash="mhash_rev7",
        )
        # Transient spike was True at v_snap=10, then drops to False before t_eff:
        e2.set_live_predicate("obs:queue_high", True)
        assert e2.get_live_predicate("obs:queue_high") is True
        e2.set_live_predicate("obs:queue_high", False)
        assert e2.get_live_predicate("obs:queue_high") is False

        # (a) Action requiring CommitPreconditionValid(a, t_eff) rejects atomically:
        cert_scale = _signed_cert("scale_out_cluster", snapshot_version=10, signing_key=key)
        r_reval_false = e2.execute_fenced_mutation(
            cert_scale,
            op_id="op_scale_stale_precond",
            now_time=500,
            required_commit_predicates=("obs:queue_high",),
        )
        assert r_reval_false.committed is False
        assert r_reval_false.status == "REJECTED_COMMIT_PRECONDITION_FALSE"
        assert e2.current_seq() == 0

        # (b) Historical snapshot-trigger action (e.g. revoke_iam_role) intentionally omits
        # commit-time revalidation: SnapshotPreconditionValid(a, v_snap) is the target semantics!
        cert_hist = _signed_cert("revoke_iam_role", snapshot_version=10, signing_key=key)
        r_hist = e2.execute_fenced_mutation(
            cert_hist,
            op_id="op_hist_snapshot_trigger",
            now_time=501,
        )
        assert r_hist.committed is True
        assert r_hist.status == "COMMITTED"
        assert e2.current_seq() == 1

        # (c) When obs:queue_high persists (True at t_eff), Tier E2 revalidated action commits:
        e2.set_live_predicate("obs:queue_high", True)
        r_reval_true = e2.execute_fenced_mutation(
            cert_scale,
            op_id="op_scale_live_precond",
            now_time=502,
            required_commit_predicates=("obs:queue_high",),
            commit_precondition_check=lambda conn: (
                conn.execute(
                    "SELECT holds FROM resource_predicates WHERE predicate_id = 'obs:queue_high';"
                ).fetchone()[0]
                == 1
            ),
        )
        assert r_reval_true.committed is True
        assert r_reval_true.status == "COMMITTED"
        assert e2.current_seq() == 2
        e2.conn.close()


def test_p0_3_proposition6_tier_indexed_bad_commit_accounting() -> None:
    """
    Verifies the corrected Proposition 6 BadCommit_T decomposition across enforcement tiers:
      - E2 enforces zero authorization-freshness gap (delta_fresh=0) and optional commit-time
        precondition revalidation, leaving residual probability governed only by bounded
        epistemic/model-coverage terms when all TCB & refinement contracts hold.
      - E1+-quiescence enforces zero post-FenceAck stale commits, but permits a non-zero
        pre-FenceAck window [t_rev_init, t_fenced) for already-prepared ops.
      - E1 async fence permits stale-cache execution over Delta_prop.
    """
    key = b"rev7-p03-key"
    cert = _signed_cert("promote_replica", admission_epoch=10, auth_epoch=5, signing_key=key)

    # E1 async fence: control plane revokes auth to 6, delayed push leaves local cache at 5 -> stale commit!
    e1 = TargetResourceBoundary(initial_epoch=10, tier="E1_ASYNC_FENCE")
    e1.revoke_authorization_epoch(5, fence_message_delayed=False)
    e1.revoke_authorization_epoch(6, fence_message_delayed=True)
    r_e1 = e1.execute_with_certificate(cert, now_time=500)
    assert r_e1.committed is True  # Stale auth commit during propagation window Delta_prop!

    # E2 atomic: control plane auth=6 checked atomically -> deterministic reject!
    e2 = TargetResourceBoundary(initial_epoch=10, tier="E2_SHARED_ATOMIC")
    e2.revoke_authorization_epoch(5, fence_message_delayed=False)
    e2.revoke_authorization_epoch(6, fence_message_delayed=True)
    r_e2 = e2.execute_with_certificate(cert, now_time=500)
    assert r_e2.committed is False
    assert r_e2.status == "REJECTED_AUTHORIZATION_REVOKED"
