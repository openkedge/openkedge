"""
PIEQ Revision 9 P1.2 — Mandatory Trusted Temporal-Precondition Policy Tests.

Every mutation type must carry a trusted temporal-precondition policy
(SNAPSHOT_TRIGGERED vs COMMIT_TIME_SENSITIVE) owned by trusted action/policy
metadata — never by the untrusted LLM proposal. Commit-time-sensitive operations
must be guaranteed at the physical effect point via target-native atomic
revalidation inside the Tier E2 boundary OR a trusted persistence attestation;
otherwise the operation fails closed and is never silently downgraded to
snapshot-only validation.

Note: interval persistence and atomic revalidation are SUFFICIENT mechanisms for a
sound commit-time guarantee. They are not claimed necessary for actual commit-time
truth (a precondition can dip and return — see the dip-and-return test).
"""
from __future__ import annotations

import os
import tempfile

import pytest

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    CrossRootExposureLedger,
    DeterministicRuleVerifier,
    EpistemicLeaseCertificate,
    FaultWorld,
    InferenceRule,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
)
from pieq.hardened import (
    HardenedAdmissionController,
    HardenedPIEQConfig,
    MutationTemporalPolicy,
    TargetRefinementAttestation,
)

KEY = b"rev9-temporal-policy-key"


def _make_stack(
    tmp_dir: str,
    *,
    temporal_policies,
    tier: str = "E2_SHARED_ATOMIC",
    max_drained_nodes: int = 4,
    exposure_cap: int = 4,
):
    rules = [
        InferenceRule("r_a0", frozenset({"obs:sig0"}), "act:drain_node"),
        InferenceRule("r_a1", frozenset({"obs:sig1"}), "act:drain_node"),
        InferenceRule("r_ref0", frozenset({"obs:sig0"}), "refute:s_healthy"),
        InferenceRule("r_ref1", frozenset({"obs:sig1"}), "refute:s_healthy"),
    ]
    verifier = DeterministicRuleVerifier(rules, [], secret_key=KEY)
    atms = BoundedATMSEngine(
        verifier,
        expected_epoch=10,
        expected_resource_id="db_cluster",
        expected_manifest_hash="mh_rev9",
    )
    for i in range(2):
        sig = verifier.sign_telemetry(
            assumption_id=f"obs_{i}",
            channel_id=f"ch_{i}",
            failure_domain=f"fd_{i}",
            epoch_revision=10,
            proposition=f"obs:sig{i}",
            observation_version=5,
            bit_index=i,
            resource_id="db_cluster",
            manifest_hash="mh_rev9",
        )
        obs = BoundPrimitiveAssumption(
            bit_index=i,
            assumption_id=f"obs_{i}",
            channel_id=f"ch_{i}",
            failure_domain=f"fd_{i}",
            epoch_revision=10,
            proposition=f"obs:sig{i}",
            signature=sig,
            observation_version=5,
            resource_id="db_cluster",
            manifest_hash="mh_rev9",
        )
        assert atms.register_assumption(obs) is True
        assert atms.add_verified_justification(f"ag_{i}", f"ag_{i}:0", "act:drain_node", 1 << i, f"r_a{i}")
        assert atms.add_verified_justification(f"ag_{i}", f"ag_{i}:0", "refute:s_healthy", 1 << i, f"r_ref{i}")

    worlds = [
        FaultWorld("s_degraded", frozenset(), frozenset({"drain_node", "defer", "probe"})),
        FaultWorld("s_healthy", frozenset(), frozenset({"defer", "probe"})),
    ]
    config = HardenedPIEQConfig(
        resource_id="db_cluster",
        epoch=10,
        manifest_hash="mh_rev9",
        auth_epoch=3,
        signing_key=KEY,
        f_fault_budget=1,
        k_a=2,
        temporal_policies=temporal_policies,
        legacy_temporal_mode_opt_in=temporal_policies is None,
    )
    att = TargetRefinementAttestation(
        resource_id="db_cluster",
        tier=tier,
        refinement_regime="R1_ATOMIC",
        manifest_hash="mh_rev9",
        complete_writer_mediation=True,
    ).with_signature(KEY)
    ledger = CrossRootExposureLedger({"db_cluster": exposure_cap})
    target = SQLiteProtectedResourceBoundary(
        db_path=os.path.join(tmp_dir, "rev9_temporal.db"),
        initial_epoch=10,
        max_drained_nodes=max_drained_nodes,
        signing_key=KEY,
        expected_manifest_hash="mh_rev9",
    )
    target.revoke_authorization_atomically(3)
    ctrl = HardenedAdmissionController(config, att, ledger, target)
    return ctrl, atms, worlds, config, target


SNAP = SnapshotContext(
    snapshot_version=5, min_obs_version=5, max_obs_version=5,
    require_versioned_commits=True, require_contiguous_versions=True,
)

COMMIT_TIME_POLICY = (
    MutationTemporalPolicy(
        action="drain_node",
        mode="COMMIT_TIME_SENSITIVE",
        trusted_commit_predicates=("load_above_threshold",),
    ),
)
SNAPSHOT_POLICY = (
    MutationTemporalPolicy(action="drain_node", mode="SNAPSHOT_TRIGGERED"),
)


def _run(ctrl, atms, worlds, *, op_id: str, caller_predicates=()):
    return ctrl.evaluate_and_execute(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "drain_node"),
        incident_class="db_failover",
        exposure_domain="db_cluster",
        exposure_cost=1,
        op_id=op_id,
        issuance_key=f"ik_{op_id}",
        snapshot_context=SNAP,
        now_time=100,
        auth_valid_until=500,
        target_node="node_1",
        required_commit_predicates=caller_predicates,
    )


def test_policy_dataclass_validation() -> None:
    with pytest.raises(ValueError):
        MutationTemporalPolicy(action="x", mode="SOMETIMES")
    with pytest.raises(ValueError):
        # Commit-time-sensitive with no sound mechanism at all:
        MutationTemporalPolicy(action="x", mode="COMMIT_TIME_SENSITIVE")
    with pytest.raises(ValueError):
        # Snapshot-triggered actions never require commit-time revalidation:
        MutationTemporalPolicy(
            action="x", mode="SNAPSHOT_TRIGGERED", trusted_commit_predicates=("p",)
        )
    with pytest.raises(ValueError):
        HardenedPIEQConfig(
            resource_id="db_cluster", epoch=10, manifest_hash="mh", auth_epoch=1,
            signing_key=b"12345678", f_fault_budget=1, k_a=2,
            temporal_policies=SNAPSHOT_POLICY + SNAPSHOT_POLICY,  # duplicate action
        )


def test_transient_load_drop_before_scaling_is_rejected_at_commit() -> None:
    """A transient precondition that becomes false before execution must block a
    COMMIT_TIME_SENSITIVE mutation at the atomic effect boundary."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, _, target = _make_stack(tmp, temporal_policies=COMMIT_TIME_POLICY)
        target.set_live_predicate("load_above_threshold", False)  # load dipped
        d = _run(ctrl, atms, worlds, op_id="op_dip")
        assert d.admitted is False
        assert d.execution_receipt is not None
        assert d.execution_receipt.status == "TARGET_REJECTED:REJECTED_COMMIT_PRECONDITION_FALSE".split(":", 1)[1]
        assert target.current_seq() == 0  # zero physical effect
        target.conn.close()


def test_dip_and_return_commits_once_predicate_true_again() -> None:
    """Dip-and-return: the predicate goes True -> False -> True. Execution during the
    dip is rejected; execution after the return commits. This also demonstrates that
    interval persistence is NOT necessary for actual commit-time truth — atomic
    revalidation at t_eff is the sufficient mechanism used."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, _, target = _make_stack(tmp, temporal_policies=COMMIT_TIME_POLICY)
        target.set_live_predicate("load_above_threshold", True)
        target.set_live_predicate("load_above_threshold", False)  # dip
        d_dip = _run(ctrl, atms, worlds, op_id="op_dip_phase")
        assert d_dip.admitted is False
        target.set_live_predicate("load_above_threshold", True)  # return
        d_ok = _run(ctrl, atms, worlds, op_id="op_return_phase")
        assert d_ok.admitted is True
        assert d_ok.execution_receipt is not None and d_ok.execution_receipt.committed
        assert d_ok.certificate is not None
        assert d_ok.certificate.temporal_policy == COMMIT_TIME_POLICY[0].canonical_string()
        target.conn.close()


def test_stale_certificate_after_temporal_policy_change_is_rejected() -> None:
    """A certificate minted under policy P1 and replayed after the trusted policy
    rotated to P2 must be rejected inside the atomic boundary."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, _, _, target = _make_stack(tmp, temporal_policies=COMMIT_TIME_POLICY)
        old_policy = "SNAPSHOT_TRIGGERED||persist=0"
        new_policy = COMMIT_TIME_POLICY[0].canonical_string()
        raw = EpistemicLeaseCertificate(
            action="drain_node", support_mask=0b11, admission_epoch=10, packing_number=2,
            plausible_worlds=("s_degraded",), signature="", auth_epoch=3,
            auth_valid_until=500, resource_id="db_cluster", manifest_hash="mh_rev9",
            snapshot_version=5, temporal_policy=old_policy,
        )
        stale = EpistemicLeaseCertificate(
            action=raw.action, support_mask=raw.support_mask, admission_epoch=raw.admission_epoch,
            packing_number=raw.packing_number, plausible_worlds=raw.plausible_worlds,
            signature=raw.compute_signature(KEY), auth_epoch=raw.auth_epoch,
            auth_valid_until=raw.auth_valid_until, resource_id=raw.resource_id,
            manifest_hash=raw.manifest_hash, snapshot_version=raw.snapshot_version,
            temporal_policy=raw.temporal_policy,
        )
        receipt = target.execute_fenced_mutation(
            stale, target_node="node_1", op_id="op_stale_policy", now_time=100,
            expected_temporal_policy=new_policy,
        )
        assert receipt.committed is False
        assert receipt.status == "REJECTED_STALE_TEMPORAL_POLICY"
        assert target.current_seq() == 0
        target.conn.close()


def test_llm_omitting_commit_time_validation_is_overridden_by_trusted_policy() -> None:
    """The caller (standing in for an LLM-influenced coordinator) passes NO commit
    predicates; the trusted registry injects them anyway and the false predicate
    blocks the mutation. Untrusted omission cannot downgrade the temporal policy."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, _, target = _make_stack(tmp, temporal_policies=COMMIT_TIME_POLICY)
        target.set_live_predicate("load_above_threshold", False)
        d = _run(ctrl, atms, worlds, op_id="op_omit", caller_predicates=())
        assert d.admitted is False
        assert target.current_seq() == 0
        target.conn.close()


def test_commit_time_policy_unenforceable_on_non_atomic_tier_fails_closed() -> None:
    """A COMMIT_TIME_SENSITIVE operation on a target whose attested tier provides no
    atomic revalidation at the effect point (E1+ quiescence) and no persistence
    attestation must fail closed — a pre-transaction precondition check is NOT an
    acceptable substitute for commit-boundary revalidation."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, _, target = _make_stack(
            tmp, temporal_policies=COMMIT_TIME_POLICY, tier="E1_PLUS_QUIESCENCE_BARRIER"
        )
        # Out-of-transaction "check" claims the predicate currently holds; irrelevant:
        target.set_live_predicate("load_above_threshold", True)
        d = _run(ctrl, atms, worlds, op_id="op_e1plus")
        assert d.admitted is False
        assert d.reason == "REJECTED_COMMIT_TIME_POLICY_UNENFORCEABLE"
        assert target.current_seq() == 0
        target.conn.close()


def test_persistence_attestation_is_alternative_sufficient_mechanism() -> None:
    """A commit-time-sensitive operation with a trusted persistence guarantee
    attestation admits even without target predicates (the alternative sufficient
    mechanism), including on a non-E2 revalidation path."""
    policies = (
        MutationTemporalPolicy(
            action="drain_node",
            mode="COMMIT_TIME_SENSITIVE",
            persistence_guarantee_attested=True,
        ),
    )
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, _, target = _make_stack(tmp, temporal_policies=policies)
        d = _run(ctrl, atms, worlds, op_id="op_persist")
        assert d.admitted is True
        assert d.certificate is not None
        assert "persist=1" in d.certificate.temporal_policy
        target.conn.close()


def test_historical_security_event_remains_actionable_snapshot_triggered() -> None:
    """A SNAPSHOT_TRIGGERED action (e.g. responding to a historical security event)
    remains actionable after its immediate symptom disappears: no commit-time
    predicates are injected and the mutation commits."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, _, target = _make_stack(tmp, temporal_policies=SNAPSHOT_POLICY)
        # The transient symptom is gone at commit time; must NOT block the action:
        target.set_live_predicate("load_above_threshold", False)
        d = _run(ctrl, atms, worlds, op_id="op_hist")
        assert d.admitted is True
        assert d.execution_receipt is not None and d.execution_receipt.committed
        assert d.certificate is not None
        assert d.certificate.temporal_policy == "SNAPSHOT_TRIGGERED||persist=0"
        target.conn.close()


def test_missing_temporal_policy_fails_closed_when_registry_configured() -> None:
    """With a configured (non-None) registry, an action lacking a policy entry is
    rejected before any ledger reservation or physical execution."""
    other_only = (
        MutationTemporalPolicy(action="restart_replica", mode="SNAPSHOT_TRIGGERED"),
    )
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, _, target = _make_stack(tmp, temporal_policies=other_only)
        d = _run(ctrl, atms, worlds, op_id="op_nopolicy")
        assert d.admitted is False
        assert d.reason == "REJECTED_MISSING_TEMPORAL_PRECONDITION_POLICY"
        assert target.current_seq() == 0
        target.conn.close()


def test_legacy_none_registry_preserves_rev8_semantics() -> None:
    """temporal_policies=None preserves archived Revision 7/8 runner semantics:
    snapshot-only certification with caller predicates passed through verbatim."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, _, target = _make_stack(tmp, temporal_policies=None)
        d = _run(ctrl, atms, worlds, op_id="op_legacy")
        assert d.admitted is True
        assert d.certificate is not None and d.certificate.temporal_policy == ""
        target.conn.close()
