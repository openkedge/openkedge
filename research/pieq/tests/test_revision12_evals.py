"""
PIEQ Revision 12 Regression Tests — Synthesis-vs-Completion, Scalability/Truncation,
Section 5 Multi-Process Fault Schedules, EtcdQuiescenceBoundary Unit Logic, and
Confirmation-12 Pinned Runner Support.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from scripts.rev10_confirmation_cases import build_cases as build_confirmation12_cases
from scripts.run_rev8_pinned_live_eval import (
    check_live_preflight,
    run_pinned_live_evaluation,
)
from scripts.run_rev12_scalability_stress import (
    run_axis_a_packing_stress,
    run_axis_b_shield_stress,
    run_axis_c_mode_c_stress,
    run_axis_d_alert_storm,
)
from scripts.run_rev12_section5_fault_eval import (
    run_schedule_1_concurrent_radm_cas,
    run_schedule_2_midflight_revocation_e2,
    run_schedule_3_quiescence_6step,
    run_schedule_4_mode_c_r2,
)
from scripts.run_rev12_synthesis_vs_completion import (
    audit_model_authoring_footprint,
    run_synthesis_vs_completion_benchmark,
)
from tests.test_revision8_live_runner import TEMPLATE_MANIFEST_PATH, _StubAttestedProvider


def test_rev12_model_authoring_audit_covers_all_100_cases_and_10_domains() -> None:
    audit = audit_model_authoring_footprint()
    assert audit["total_cases_audited"] == 100
    assert audit["distinct_domains"] == 10
    for row in audit["domains"]:
        assert row["worlds"]["min"] >= 2
        assert row["obs_M"]["max"] <= 64


def test_rev12_synthesis_vs_completion_asymmetry_and_hallucination_blocking() -> None:
    res = run_synthesis_vs_completion_benchmark(repeats=2)
    assert res["total_unsafe_admissions"] == 0
    assert res["all_controls_blocked"] is True
    # Goal-directed proposer verified by unchanged TCB admits all 16 (R_bg, depth) cells;
    # 1-hop completion admits only R_bg=0, depth=1 (1/16); BFS-512 truncates at R_bg >= 80.
    targeted_ok = sum(1 for r in res["grid"] if r["strategies"]["goal_directed_verifier"]["admitted"])
    onehop_ok = sum(1 for r in res["grid"] if r["strategies"]["tcb_1hop_128"]["admitted"])
    bfs512_high_bg_trunc = all(
        r["strategies"]["tcb_bfs_512"]["truncated"] and not r["strategies"]["tcb_bfs_512"]["admitted"]
        for r in res["grid"]
        if r["num_bg_rules"] >= 80
    )
    assert targeted_ok == 16
    assert onehop_ok == 1
    assert bfs512_high_bg_trunc


def test_rev12_scalability_and_fail_closed_truncation_invariants() -> None:
    ax_a = run_axis_a_packing_stress()
    assert all(r["unsafe_count"] == 0 for r in ax_a["adversarial_hypergraph_M64"])
    assert ax_a["label_cap_L_max_64"]["labels_retained"] == 64
    assert ax_a["label_cap_L_max_64"]["budget_exceeded_flag"] is True
    # Hitting sets exact at 6 disjoint nogoods, fail-closed at >= 8
    hs_by_n = {r["disjoint_2leaf_nogoods"]: r for r in ax_a["hitting_set_B_HS_256_sweep"]}
    assert hs_by_n[6]["completed_exact"] is True and hs_by_n[6]["hitting_sets_returned"] == 64
    assert hs_by_n[8]["fail_closed_truncated"] is True

    ax_b = run_axis_b_shield_stress()
    assert all(r["true_world_retained"] and r["truncation_retains_all"] for r in ax_b["shield_sweep"])

    ax_c = run_axis_c_mode_c_stress()
    assert ax_c["r2_write_skew_rejected"] is True
    r1_5 = [r for r in ax_c["r1_sweep"] if r["U_C"] == 5]
    assert r1_5 and all(not r["admitted"] and r["reason"] == "MODE_C_REJECTED_OUTSIDE_BOUNDED_CLASS_FALLBACK_MODE_S" for r in r1_5)
    r2_7 = [r for r in ax_c["r2_sweep"] if r["total_phases"] == 7]
    assert r2_7 and all(not r["admitted"] and r["reason"] == "MODE_C_REJECTED_OUTSIDE_BOUNDED_CLASS_FALLBACK_MODE_S" for r in r2_7)

    ax_d = run_axis_d_alert_storm()
    assert ax_d["partitioned_multi_scope"]["admitted_scopes"] == 20
    assert ax_d["partitioned_multi_scope"]["truncated_scopes"] == 0
    assert all(r["fail_closed_budget_flag"] and r["unsafe_admissions"] == 0 for r in ax_d["unpartitioned_single_scope_overflow"])


def test_rev12_section5_multiprocess_schedules_pass() -> None:
    assert run_schedule_1_concurrent_radm_cas(0)["passed"]
    assert run_schedule_2_midflight_revocation_e2(0)["passed"]
    assert run_schedule_2_midflight_revocation_e2(1)["passed"]
    assert run_schedule_3_quiescence_6step(0)["passed"]
    assert run_schedule_4_mode_c_r2(0)["passed"]


def test_rev12_pinned_runner_supports_confirmation12_dataset() -> None:
    manifest = json.loads(TEMPLATE_MANIFEST_PATH.read_text())
    status = check_live_preflight(manifest)
    assert status["execution_status"] in {"READY", "UNEXECUTED_MISSING_CREDENTIALS"}
    cases = build_confirmation12_cases()
    assert len(cases) == 12
    stub = _StubAttestedProvider(omit_model_id=False, fail_first_attempt_agent4=False)
    with tempfile.TemporaryDirectory() as tmp:
        summary = run_pinned_live_evaluation(
            cases=cases[:2],
            manifest=manifest,
            trace_path=Path(tmp) / "conf12_stub.jsonl",
            protocols=("full_pieq",),
            providers_override={"anthropic": stub, "openai": stub, "gemini": stub},
            use_mandatory_temporal_registry=True,
        )
        assert summary["protocol_summaries"]["full_pieq"]["cases_evaluated"] == 2
        assert summary["protocol_summaries"]["full_pieq"]["unsafe_count"] == 0


def test_rev12_direct_api_confirmation12_artifact_integrity() -> None:
    import hashlib

    repo_root = Path(__file__).resolve().parents[1]
    trace_path = repo_root / "results" / "rev12" / "pinned_conf12_full_pieq.jsonl"
    scored_path = repo_root / "results" / "rev12" / "pinned_conf12_full_pieq_scored.json"
    assert trace_path.exists() and scored_path.exists()

    scored = json.loads(scored_path.read_text())
    actual_sha = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    assert scored["trace_sha256"] == actual_sha
    assert scored["sampling"]["temperature"] == 0.0
    assert scored["sampling"]["seed"] == 17
    assert scored["within_assumption"]["mitigations"] == 4
    assert scored["within_assumption"]["false_blocks"] == 0
    assert scored["within_assumption"]["mandatory_deferrals"] == 6
    assert scored["within_assumption"]["false_preconditions"] == 0
    assert scored["within_assumption"]["within_unsafe"] == 0
    assert scored["ooa_controls"]["ooa_admitted_unsafe"] == 1
    assert scored["operational"]["total_calls"] == 60
    assert scored["operational"]["valid_proposals_first_attempt"] == 60
    assert scored["operational"]["derivations_accepted"] == 173
    assert scored["operational"]["derivations_rejected"] == 0
    assert abs(scored["operational"]["total_cost_usd"] - 0.13899) < 1e-5
    assert set(scored["operational"]["by_provider"].keys()) == {
        "bedrock_anthropic", "bedrock_openai", "local_openai",
    }
