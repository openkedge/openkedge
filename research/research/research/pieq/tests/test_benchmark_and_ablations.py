"""
Benchmark, Ablation, Contingency-Table, Negative Control, and Trace-Quantile Tests.
"""

import math
import pytest

from pieq.benchmark import (
    build_benchmark_scenarios,
    clopper_pearson_95,
    evaluate_policy_on_benchmark,
    mcnemar_exact_pvalue,
    paired_contingency_and_mcnemar,
    profile_proxy_admission_traces,
)


def test_benchmark_scenario_distribution():
    scenarios = build_benchmark_scenarios()
    assert len(scenarios) == 100
    assert sum(1 for s in scenarios if s.passively_ambiguous) == 50
    assert sum(1 for s in scenarios if s.helly_three_world) == 8
    assert sum(1 for s in scenarios if s.requires_positive_synthesis) == 15
    assert sum(1 for s in scenarios if s.has_shared_failure_domain_trap) == 10
    assert sum(1 for s in scenarios if s.has_overlapping_conjunctive_trap) == 6
    assert sum(1 for s in scenarios if s.has_single_sensor_false_refutation) == 6
    assert sum(1 for s in scenarios if s.has_concurrent_aggregate_drain) == 6
    assert sum(1 for s in scenarios if s.has_inflight_invalidation_race) == 12
    assert sum(1 for s in scenarios if s.has_delayed_fence_message) == 4


def test_clopper_pearson_exact_intervals():
    lo, hi = clopper_pearson_95(1, 100)
    assert math.isclose(lo, 0.0253, abs_tol=0.01)
    assert math.isclose(hi, 5.4459, abs_tol=0.01)


def test_main_policies_and_paired_contingency_tables():
    """
    Second Review Section 7:
    Verify that Scenario 99 (where >= 3 independent failure domains suffer simultaneous
    sensor corruption, Event E4 of Proposition 6) fails across both baselines and Full PIEQ,
    so the paired 2x2 contingency table has n11 == 1, n01 == 0, and n10 == baseline_unsafe - 1.
    """
    scenarios = build_benchmark_scenarios()

    res_debate = evaluate_policy_on_benchmark("free_debate_overlap", scenarios)
    res_blind = evaluate_policy_on_benchmark("blind_vote_overlap", scenarios)
    res_ortho = evaluate_policy_on_benchmark("free_debate_ortho", scenarios)
    res_nogood = evaluate_policy_on_benchmark("atms_nogood_only", scenarios)
    res_pce = evaluate_policy_on_benchmark("atms_pce_pillar1", scenarios)
    res_full = evaluate_policy_on_benchmark("full_pieq", scenarios)

    assert res_debate.unsafe_mutation_rate == 38.0
    assert res_blind.unsafe_mutation_rate == 29.0
    assert res_ortho.unsafe_mutation_rate == 24.0
    assert res_nogood.unsafe_mutation_rate == 8.0
    assert res_pce.unsafe_mutation_rate == 8.0
    assert res_full.unsafe_mutation_rate == 1.0
    assert res_full.scenario_unsafe_flags[99] == 1  # Root cause: Scenario 99 (Event E4)

    ct_debate = paired_contingency_and_mcnemar(res_debate.scenario_unsafe_flags, res_full.scenario_unsafe_flags)
    assert ct_debate["n11_both_unsafe"] == 1
    assert ct_debate["n10_base_only_unsafe"] == 37
    assert ct_debate["n01_pieq_only_unsafe"] == 0
    assert ct_debate["n00_both_safe"] == 62
    assert ct_debate["mcnemar_p"] < 1e-10


def test_all_component_and_second_review_boundary_ablations():
    scenarios = build_benchmark_scenarios()

    res_full = evaluate_policy_on_benchmark("full_pieq", scenarios)
    res_a1 = evaluate_policy_on_benchmark("single_strong_agent", scenarios)
    res_a2 = evaluate_policy_on_benchmark("ortho_no_debate", scenarios)
    res_a4 = evaluate_policy_on_benchmark("atms_no_cut", scenarios)
    res_a5 = evaluate_policy_on_benchmark("cut_no_atms", scenarios)
    res_a6 = evaluate_policy_on_benchmark("probing_no_atms", scenarios)
    res_a7 = evaluate_policy_on_benchmark("full_correlated_faults", scenarios)

    # Second Review Priority 1-3 + Concurrent Exposure Boundary Ablations
    res_b0_menger = evaluate_policy_on_benchmark("menger_flow_only_no_packing", scenarios)
    res_b1_pairwise = evaluate_policy_on_benchmark("pairwise_gate_only", scenarios)
    res_b2a_e0 = evaluate_policy_on_benchmark("proxy_only_no_target_fence", scenarios)
    res_b2b_e1 = evaluate_policy_on_benchmark("async_push_fence_tier_e1", scenarios)
    res_b3_f0 = evaluate_policy_on_benchmark("naive_belief_pruning_f0", scenarios)
    res_b4_conc = evaluate_policy_on_benchmark("no_concurrent_exposure_gate", scenarios)

    assert res_b0_menger.unsafe_mutation_rate == 7.0    # 6 overlapping conjunctive traps + idx=99
    assert res_b1_pairwise.unsafe_mutation_rate == 9.0  # 8 Helly cases + idx=99
    assert res_b2a_e0.stale_inflight_leaked == 7        # 7 leaked under E0_PROXY_ONLY
    assert res_b2b_e1.stale_inflight_leaked == 4        # 4 delayed-fence races leaked under E1_ASYNC_FENCE
    assert res_b3_f0.unsafe_mutation_rate == 7.0        # 6 single-sensor refutation traps + idx=99
    assert res_b4_conc.unsafe_mutation_rate == 7.0      # 6 concurrent dual drains + idx=99

    assert res_full.stale_inflight_leaked == 0
    assert res_full.stale_inflight_rejected == 12

    nc1 = evaluate_policy_on_benchmark("nc1_common_mode_model_error", scenarios)
    nc2 = evaluate_policy_on_benchmark("nc2_unidentifiable_no_probe", scenarios)
    assert nc1.unsafe_mutation_rate == 50.0
    assert nc2.unsafe_mutation_rate == 0.0
    assert nc2.safe_deferral_rate == 50.0


def test_trace_latency_quantiles_real_measurements():
    stats = profile_proxy_admission_traces(n_traces=1000, seed=42)
    stages = [
        "atms_verify_and_label_update",
        "fault_tolerant_belief_and_compound_gate",
        "disjoint_justification_set_packing",
        "sqlite_wal_atomic_epoch_fence_check",
    ]
    sum_means = sum(stats[s]["mean"] for s in stages)
    total_stats = stats["end_to_end_trace_total"]
    assert abs(total_stats["mean"] - sum_means) < 0.01
    assert 0.0 < total_stats["p50"] <= total_stats["p95"] <= total_stats["p99"] < 5.0
