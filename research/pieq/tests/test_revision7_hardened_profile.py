"""
Revision 7 P1 Tests — Hardened Reference Configuration & Contract-to-Implementation Matrix.
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
    FaultWorld,
    InferenceRule,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
)
from pieq.hardened import (
    CONTRACT_IMPLEMENTATION_MATRIX,
    HardenedAdmissionController,
    HardenedPIEQConfig,
    TargetRefinementAttestation,
)


def _make_hardened_stack(tmp_dir: str, *, key: bytes = b"rev7-hardened-secret-key"):
    rules = [
        InferenceRule("r_a0", frozenset({"obs:sig0"}), "act:drain_node"),
        InferenceRule("r_a1", frozenset({"obs:sig1"}), "act:drain_node"),
        InferenceRule("r_ref0", frozenset({"obs:sig0"}), "refute:s_healthy"),
        InferenceRule("r_ref1", frozenset({"obs:sig1"}), "refute:s_healthy"),
    ]
    verifier = DeterministicRuleVerifier(rules, [], secret_key=key)
    atms = BoundedATMSEngine(
        verifier,
        expected_epoch=10,
        expected_resource_id="db_cluster",
        expected_manifest_hash="mh_hardened_v7",
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
            manifest_hash="mh_hardened_v7",
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
            manifest_hash="mh_hardened_v7",
        )
        assert atms.register_assumption(obs) is True
        assert atms.add_verified_justification(f"ag_{i}", f"ag_{i}:0", "act:drain_node", 1 << i, f"r_a{i}") is not None
        assert atms.add_verified_justification(f"ag_{i}", f"ag_{i}:0", "refute:s_healthy", 1 << i, f"r_ref{i}") is not None

    worlds = [
        FaultWorld("s_degraded", frozenset(), frozenset({"drain_node", "defer", "probe"})),
        FaultWorld("s_healthy", frozenset(), frozenset({"defer", "probe"})),
    ]
    config = HardenedPIEQConfig(
        resource_id="db_cluster",
        epoch=10,
        manifest_hash="mh_hardened_v7",
        auth_epoch=3,
        signing_key=key,
        f_fault_budget=1,
        k_a=2,
    )
    att = TargetRefinementAttestation(
        resource_id="db_cluster",
        tier="E2_SHARED_ATOMIC",
        refinement_regime="R1_ATOMIC",
        manifest_hash="mh_hardened_v7",
        complete_writer_mediation=True,
    ).with_signature(key)
    ledger = CrossRootExposureLedger({"db_cluster": 1})
    target = SQLiteProtectedResourceBoundary(
        db_path=os.path.join(tmp_dir, "hardened.db"),
        initial_epoch=10,
        max_drained_nodes=1,
        signing_key=key,
        expected_manifest_hash="mh_hardened_v7",
    )
    target.revoke_authorization_atomically(3)
    ctrl = HardenedAdmissionController(config, att, ledger, target)
    return ctrl, atms, worlds, config, att, ledger, target


def test_hardened_config_rejects_relaxed_parameters() -> None:
    with pytest.raises(ValueError):
        HardenedPIEQConfig(
            resource_id="db_cluster", epoch=10, manifest_hash="mh", auth_epoch=1,
            signing_key=b"12345678", f_fault_budget=2, k_a=2,  # k_a < f + 1
        )
    with pytest.raises(ValueError):
        HardenedPIEQConfig(
            resource_id="db_cluster", epoch=10, manifest_hash="mh", auth_epoch=1,
            signing_key=b"12345678", f_fault_budget=1, k_a=2,
            require_contiguous_versions=False,
        )
    with pytest.raises(ValueError):
        HardenedPIEQConfig(
            resource_id="db_cluster", epoch=10, manifest_hash="mh", auth_epoch=1,
            signing_key=b"12345678", f_fault_budget=1, k_a=2,
            allowed_refinement_regimes=frozenset({"R1_ATOMIC", "UNVERIFIED_ASYNC"}),
        )


def test_hardened_controller_blocks_all_negative_controls_and_commits_valid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, atms, worlds, config, att, ledger, target = _make_hardened_stack(tmp)
        snap = SnapshotContext(
            snapshot_version=5, min_obs_version=5, max_obs_version=5,
            require_versioned_commits=True, require_contiguous_versions=True,
        )

        # Negative 1: UNVERIFIED_ASYNC attestation rejected fail-closed
        bad_att = TargetRefinementAttestation(
            resource_id="db_cluster", tier="E2_SHARED_ATOMIC", refinement_regime="UNVERIFIED_ASYNC",
            manifest_hash="mh_hardened_v7", complete_writer_mediation=True,
        ).with_signature(config.signing_key)
        ctrl_bad = HardenedAdmissionController(config, bad_att, ledger, target)
        d1 = ctrl_bad.evaluate_and_execute(
            atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "drain_node"),
            incident_class="db_failover", exposure_domain="db_cluster", exposure_cost=1,
            op_id="op_neg1", issuance_key="ik_neg1", snapshot_context=snap,
            now_time=100, auth_valid_until=500,
        )
        assert not d1.admitted and d1.reason == "REJECTED_UNVERIFIED_PHYSICAL_REFINEMENT"

        # Negative 2: Missing auth clock rejected fail-closed
        d2 = ctrl.evaluate_and_execute(
            atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "drain_node"),
            incident_class="db_failover", exposure_domain="db_cluster", exposure_cost=1,
            op_id="op_neg2", issuance_key="ik_neg2", snapshot_context=snap,
            now_time=None, auth_valid_until=500,
        )
        assert not d2.admitted and d2.reason == "REJECTED_MISSING_AUTH_CLOCK_OR_TTL"

        # Negative 3: Torn snapshot rejected fail-closed
        torn_snap = SnapshotContext(
            snapshot_version=5, min_obs_version=4, max_obs_version=5,
            require_versioned_commits=True, require_contiguous_versions=True,
        )
        d3 = ctrl.evaluate_and_execute(
            atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "drain_node"),
            incident_class="db_failover", exposure_domain="db_cluster", exposure_cost=1,
            op_id="op_neg3", issuance_key="ik_neg3", snapshot_context=torn_snap,
            now_time=100, auth_valid_until=500,
        )
        assert not d3.admitted and d3.reason == "REJECTED_TORN_SNAPSHOT_CONTEXT"

        # Negative 4: Stale per-domain admission revision CAS rejected fail-closed
        d4 = ctrl.evaluate_and_execute(
            atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "drain_node"),
            incident_class="db_failover", exposure_domain="db_cluster", exposure_cost=1,
            op_id="op_neg4", issuance_key="ik_neg4", snapshot_context=snap,
            now_time=100, auth_valid_until=500,
            override_expected_admission_revs={"db_cluster": 999},
        )
        assert not d4.admitted and "REJECTED_LEDGER_ADMISSION" in d4.reason

        # Positive: Full hardened execution commits atomically on Tier E2 SQLite WAL!
        d_ok = ctrl.evaluate_and_execute(
            atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "drain_node"),
            incident_class="db_failover", exposure_domain="db_cluster", exposure_cost=1,
            op_id="op_valid_1", issuance_key="ik_valid_1", snapshot_context=snap,
            now_time=100, auth_valid_until=500, target_node="node_1",
        )
        assert d_ok.admitted is True
        assert d_ok.action == "drain_node"
        assert d_ok.execution_receipt is not None and d_ok.execution_receipt.committed is True
        assert target.current_seq() == 1

        # Negative 5: Second concurrent drain violates cross-root exposure cap (1) -> rejected!
        d5 = ctrl.evaluate_and_execute(
            atms=atms, worlds=worlds, candidate_actions=("defer", "probe", "drain_node"),
            incident_class="db_failover", exposure_domain="db_cluster", exposure_cost=1,
            op_id="op_cap_exceed", issuance_key="ik_cap_exceed", snapshot_context=snap,
            now_time=101, auth_valid_until=500, target_node="node_2",
        )
        assert d5.admitted is False
        assert "REJECTED_LEDGER_ADMISSION" in d5.reason
        assert target.current_seq() == 1

        # Verify contract matrix explicitly clarifies in-memory ledger non-durability:
        ledger_rows = [r for r in CONTRACT_IMPLEMENTATION_MATRIX if "IN-MEMORY ONLY" in r["core_python"]]
        assert len(ledger_rows) >= 3
        target.conn.close()


def test_four_category_falsification_evaluator_separates_categories() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "pg_exp",
        Path(__file__).resolve().parents[1] / "experiments" / "pg_patroni_failover" / "run_experiment.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    trials = [
        # Within-assumption actionable: 1 mitigated, 1 false block -> viability alert, NOT soundness failure!
        {"id": "F1", "within_assumptions": True, "expected_actionable": True,
         "safe_correct_mitigation": True, "unsafe_authorization": False, "physical_dual_primary_detected": False},
        {"id": "F4", "within_assumptions": True, "expected_actionable": True,
         "safe_correct_mitigation": False, "unsafe_authorization": False, "physical_dual_primary_detected": False},
        # Within-assumption defer: safely deferred
        {"id": "F2", "within_assumptions": True, "expected_actionable": False,
         "safe_deferral": True, "unsafe_authorization": False, "physical_dual_primary_detected": False},
        # Outside-assumption fault (F7 exceeding f): triggers Category 4, NOT Category 1!
        {"id": "F7", "within_assumptions": False, "expected_actionable": False,
         "safe_deferral": False, "unsafe_authorization": True, "physical_dual_primary_detected": False},
    ]
    ev = mod.evaluate_four_category_falsification(trials)
    assert ev["category_1_formal_soundness_failure"]["triggered"] is False
    assert ev["category_4_trusted_assumption_violation"]["triggered"] is True
    assert ev["category_4_trusted_assumption_violation"]["count"] == 1
    assert ev["category_3_practical_viability_engineering_criteria"]["actionable_mitigation_rate"] == 0.5
    assert len(ev["category_3_practical_viability_engineering_criteria"]["viability_alerts"]) == 2

