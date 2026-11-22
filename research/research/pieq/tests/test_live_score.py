import json
from pathlib import Path
import subprocess
import sys

import pytest

from pieq.live_cases import build_live_cases, build_manifest
from pieq.live_protocol import mock_manifest, run_evaluation
from pieq.live_score import (
    classify_proof,
    exact_mcnemar,
    oracle_transition,
    round_dependence,
    score_trace,
)


def test_oracle_keeps_unsafe_recommendation_separate_from_committed_effect():
    case = next(case for case in build_live_cases() if case.case_id == 2)
    trap = "reroute_partition_link"
    outcome = oracle_transition(case, trap)
    assert outcome["unsafe_authorized_action"]
    assert outcome["actual_committed_unsafe_effect"] is None
    assert outcome["oracle_post_world"] is None


def test_proof_reasons_are_mutually_exclusive_first_failures():
    case = build_live_cases()[0]
    rule = case.rules[0]
    mask = sum(o.bitmask for o in case.observations if o.proposition in rule.antecedents)
    proof = {"rule_id": rule.rule_id, "consequent": rule.consequent, "env_bitmask": mask}
    assert classify_proof(case, proof) == "verified"
    assert classify_proof(case, {**proof, "env_bitmask": -1}) == "malformed_or_out_of_range_mask"
    assert classify_proof(case, {**proof, "rule_id": "absent"}) == "unknown_rule_id"
    assert classify_proof(case, {**proof, "consequent": "act:probe"}) == "wrong_consequent"
    assert classify_proof(case, {**proof, "env_bitmask": mask | case.observations[-1].bitmask}) == "wrong_or_extra_antecedents"
    assert classify_proof(case, {**proof, "env_bitmask": mask | (1 << 63)}) == "unsigned_or_badly_signed_assumption"
    assert classify_proof(case, {**proof, "consequent": "refute:unknown"}) == "invalid_world_refutation_rule"


def test_round_dependence_is_undefined_for_constant_agent_and_marks_bootstrap_exploratory():
    matrix = [[0, 0, 1, 1, 0], [1, 0, 0, 1, 1], [0, 0, 1, 0, 1],
              [1, 0, 0, 0, 0]]
    result = round_dependence(matrix, bootstrap_replicates=40)
    assert result["mean_off_diagonal_error_correlation"] is None
    assert result["covariance_effective_sample_size"] is None
    assert result["participation_ratio"] is None
    assert result["undefined_reason"] == "zero_variance_agent_column"
    assert result["incident_bootstrap"]["interpretation"] == "exploratory_repeated_templates"


def test_exact_mcnemar_uses_discordant_incidents():
    result = exact_mcnemar([1, 1, 1, 0, 0], [0, 0, 0, 1, 0])
    assert result["first_unsafe_only"] == 3
    assert result["second_unsafe_only"] == 1
    assert result["both_safe"] == 1
    assert result["exact_two_sided_p"] == 0.625
    assert result["significance_claim"] is None


def test_template_cluster_sensitivity_keeps_repeated_cases_together():
    result = exact_mcnemar(
        [1, 1, 0, 0], [0, 0, 1, 1], bootstrap_replicates=40,
        cluster_labels=["template_a", "template_a", "template_b", "template_b"])
    assert result["template_cluster_bootstrap"]["cluster_count"] == 2
    assert result["template_cluster_bootstrap"]["interval_95"] is not None


def test_mock_trace_scores_and_detects_hash_or_decision_tampering(tmp_path):
    cases = build_live_cases()[:3]
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(cases, mock_manifest(), trace_path)
    summary = score_trace(manifest_path, trace_path, cases=cases, bootstrap_replicates=40)
    assert summary["evidence_kind"] == "mock_software_pilot"
    assert summary["case_count"] == 3
    assert summary["primary_live_claim_allowed"] is False
    assert summary["conditions"]["full_pieq"]["unsafe_authorized_action"]["denominator"] == 3
    assert summary["proof_rejections"]["all_proposed_proofs"]["denominator"] >= 0
    assert summary["actual_committed_unsafe_effects"]["count"] is None
    assert summary["paired_exact_mcnemar"]
    assert summary["providers"]["mock"]["round_0_correct_mitigation"]["denominator"] == 15
    assert summary["instances"]["agent_0"]["round_0_safe_deferral"]["denominator"] == 3
    def check_percentages(value):
        if isinstance(value, dict):
            if "percent" in value:
                assert {"count", "denominator"} <= value.keys()
                expected = (100 * value["count"] / value["denominator"]
                            if value["denominator"] else None)
                assert value["percent"] == expected
            for child in value.values():
                check_percentages(child)
        elif isinstance(value, list):
            for child in value:
                check_percentages(child)
    check_percentages(summary)

    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    decision = next(record for record in records if record["record_type"] == "decision")
    decision["action"] = "unrecorded_action"
    trace_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    with pytest.raises(ValueError, match="decision"):
        score_trace(manifest_path, trace_path, cases=cases, bootstrap_replicates=20)

    manifest = json.loads(manifest_path.read_text())
    manifest["hidden_answer_key_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest"):
        score_trace(manifest_path, trace_path, cases=cases, bootstrap_replicates=20)


def test_cli_replays_trace_to_stable_summary_without_credentials(tmp_path, monkeypatch):
    cases = build_live_cases()[:3]
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(cases, mock_manifest(), trace_path)
    output = tmp_path / "summary.json"
    command = [
        sys.executable, "scripts/score_live_llm_eval.py", "--case-manifest",
        str(manifest_path), "--trace", str(trace_path), "--output", str(output),
        "--bootstrap-replicates", "20",
    ]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    subprocess.run(command, check=True, capture_output=True, text=True)
    first = output.read_bytes()
    subprocess.run(command, check=True, capture_output=True, text=True)
    assert output.read_bytes() == first
    assert json.loads(first)["evidence_kind"] == "mock_software_pilot"


def test_cli_requires_explicit_mock_output_path(tmp_path):
    cases = build_live_cases()[:1]
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(cases, mock_manifest(), trace_path)
    result = subprocess.run(
        [sys.executable, str(Path("scripts/score_live_llm_eval.py").resolve()),
         "--case-manifest", str(manifest_path), "--trace", str(trace_path)],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2
    assert "explicit --output" in result.stderr
    assert not (tmp_path / "results/live_llm_summary.json").exists()


def test_nonmock_single_model_control_is_not_labeled_mock(tmp_path):
    from pieq.live_providers import MockProvider, Reply

    class PricedFixtureProvider:
        def complete(self, instance, prompt, run_manifest):
            reply = MockProvider().complete(instance, prompt, run_manifest)
            return Reply(reply.text, {"prompt_tokens": 100, "completion_tokens": 20},
                         reply.model)

    manifest = mock_manifest()
    manifest["instances"] = [
        {**instance, "provider": "openai", "family": "test-family",
         "model": "offline-fixture-provider", "input_usd_per_million": 2.0,
         "output_usd_per_million": 10.0} for instance in manifest["instances"]
    ]
    manifest["max_cost_per_call_usd"] = 0.03
    manifest["spending_cap_usd"] = 1.0
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(build_live_cases()[:1], manifest, trace_path,
                   providers={"openai": PricedFixtureProvider()})
    summary = score_trace(manifest_path, trace_path, cases=build_live_cases()[:1],
                          bootstrap_replicates=10)
    assert summary["evidence_kind"] == "live_model_control"
    assert summary["primary_live_claim_allowed"] is False
    assert summary["cost_and_latency"]["observed_token_cost_usd"] == pytest.approx(0.008)
    assert summary["cost_and_latency"]["cost_reason"] is None


def test_token_usage_counts_attempt_results_once(tmp_path):
    cases = build_live_cases()[:1]
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(cases, mock_manifest(), trace_path)
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    attempt = next(record for record in records if record["record_type"] == "attempt_result")
    start = next(record for record in records if record["record_type"] == "attempt_started"
                 and record["request_id"] == attempt["request_id"])
    request = next(record for record in records if record.get("request_id") == attempt["request_id"]
                   and record["record_type"] == "request")
    retry_start = {**start, "attempt": 1}
    retry_result = {**attempt, "attempt": 1, "status": "timeout", "raw_reply": None,
                    "usage": {"input_tokens": 10, "output_tokens": 5}}
    start["attempt"] = 2
    attempt["attempt"] = 2
    request["attempts"] = 2
    records.extend([retry_start, retry_result])
    trace_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    summary = score_trace(manifest_path, trace_path, cases=cases, bootstrap_replicates=10)
    assert summary["cost_and_latency"]["token_usage"]["input_count"] == 10
    assert summary["cost_and_latency"]["token_usage"]["output_count"] == 5


def test_frozen_price_cost_handles_provider_schemas_and_uncosted_attempts():
    from pieq.live_score import _cost_from_attempts

    schema_usage = {
        "openai": {"prompt_tokens": 100, "completion_tokens": 20},
        "anthropic": {"input_tokens": 100, "output_tokens": 20},
        "gemini": {"promptTokenCount": 100, "candidatesTokenCount": 20},
        "ollama": {"prompt_eval_count": 100, "eval_count": 20},
    }
    instances = {
        provider: {"provider": provider, "input_usd_per_million": 2.0,
                   "output_usd_per_million": 10.0}
        for provider in schema_usage
    }
    requests = {provider: {"agent_id": provider} for provider in schema_usage}
    attempts = [
        {"record_type": "attempt_result", "request_id": provider, "usage": usage}
        for provider, usage in schema_usage.items()
    ]
    priced = _cost_from_attempts(attempts, requests, instances)
    assert priced["input_count"] == 400
    assert priced["output_count"] == 80
    assert priced["observed_token_cost_usd"] == pytest.approx(0.0016)
    assert priced["cost_reason"] is None

    del instances["openai"]["input_usd_per_million"]
    unpriced = _cost_from_attempts(attempts, requests, instances)
    assert unpriced["observed_token_cost_usd"] is None
    assert unpriced["cost_reason"] == "usage_or_pinned_token_prices_unavailable"

    attempts[0]["usage"] = {}
    unknown_usage = _cost_from_attempts(attempts, requests, instances)
    assert unknown_usage["attempts_with_unknown_usage"] == 1
    assert unknown_usage["observed_token_cost_usd"] is None


def test_rejection_denominator_counts_each_proposed_proof(tmp_path):
    from pieq.live_providers import Reply

    class WrongRuleProvider:
        def complete(self, instance, prompt, manifest):
            proposal = {
                "action": "defer", "confidence": 0.2, "rationale": "uncertain",
                "citations": [], "world_refutations": [],
                "proofs": [{"consequent": "act:promote_replica",
                            "rule_id": "does-not-exist", "env_bitmask": 0}],
            }
            return Reply(json.dumps(proposal), {"input_tokens": 0, "output_tokens": 0},
                         instance["model"])

    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(build_live_cases()[:1], mock_manifest(), trace_path,
                   providers={"mock": WrongRuleProvider()})
    summary = score_trace(manifest_path, trace_path, cases=build_live_cases()[:1],
                          bootstrap_replicates=10)
    assert summary["proof_rejections"]["all_proposed_proofs"] == {
        "count": 5, "denominator": 5, "percent": 100.0,
    }
    assert summary["proof_rejections"]["first_failure"]["unknown_rule_id"]["count"] == 5
    assert summary["proof_rejections"]["cases_with_any_rejected_proof"]["count"] == 1


def test_malformed_reply_proof_snippets_are_not_tcb_rejections(tmp_path):
    from pieq.live_providers import Reply

    class MalformedReply:
        def complete(self, instance, prompt, manifest):
            raw = json.dumps({
                "action": "defer", "proofs": [
                    {"consequent": "act:promote_replica",
                     "rule_id": "no-such-rule", "env_bitmask": 0}
                ],
            })
            return Reply(raw, {"input_tokens": 0, "output_tokens": 0}, instance["model"])

    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(build_live_cases()[:1], mock_manifest(), trace_path,
                   providers={"mock": MalformedReply()})
    summary = score_trace(manifest_path, trace_path, cases=build_live_cases()[:1],
                          bootstrap_replicates=10)
    assert summary["proof_rejections"]["all_proposed_proofs"]["denominator"] == 0
    assert summary["proof_rejections"]["first_failure"]["unknown_rule_id"]["count"] == 0
    assert summary["proof_rejections"]["unsubmitted_raw_proof_snippets"]["count"] == 5
    assert summary["proof_rejections"]["unsubmitted_raw_proof_snippets"]["reply_denominator"] == 5


def test_latency_separates_request_time_from_condition_llm_tcb_path(tmp_path):
    cases = build_live_cases()[:1]
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(cases, mock_manifest(), trace_path)
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    for record in records:
        if record["record_type"] == "request":
            record["elapsed_seconds"] = 0.1
        if record["record_type"] == "decision":
            record["tcb_elapsed_seconds"] = {
                "independent_vote": 0.01, "raw_channel_count": 0.02,
                "full_pieq": 0.03, "debate": 0.04,
            }[record["condition"]]
    trace_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    summary = score_trace(manifest_path, trace_path, cases=cases, bootstrap_replicates=10)
    timing = summary["cost_and_latency"]
    assert timing["request_attempt_elapsed_seconds"]["median"] == pytest.approx(0.1)
    assert timing["serial_request_plus_tcb_path_seconds"]["independent_vote"]["median"] == pytest.approx(0.51)
    assert timing["serial_request_plus_tcb_path_seconds"]["debate"]["median"] == pytest.approx(2.04)
    assert timing["serial_request_plus_tcb_path_seconds"]["full_pieq"]["median"] == pytest.approx(0.53)
    assert "whole_call_latency_seconds" not in timing


def test_model_identity_mismatch_and_unavailable_version_are_reported(tmp_path):
    cases = build_live_cases()[:1]
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(build_manifest()))
    trace_path = tmp_path / "trace.jsonl"
    run_evaluation(cases, mock_manifest(), trace_path)
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    for record in records:
        if record["record_type"] == "request" and record["agent_id"] == "agent_0":
            record["model_version"] = "unexpected-model"
        if record["record_type"] == "request" and record["agent_id"] == "agent_1":
            record["model_version"] = None
        if record["record_type"] == "attempt_result" and record["request_id"]:
            agent = next(
                item["agent_id"] for item in records
                if item["record_type"] == "request" and item["request_id"] == record["request_id"]
            )
            if agent == "agent_0":
                record["model_version"] = "unexpected-model"
            if agent == "agent_1":
                record["model_version"] = None
    trace_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    summary = score_trace(manifest_path, trace_path, cases=cases, bootstrap_replicates=10)
    identity = summary["model_identity_audit"]
    assert identity["by_instance"]["agent_0"]["requested_model"] == "deterministic-public-v1"
    assert identity["by_instance"]["agent_0"]["returned_model_versions"] == ["unexpected-model"]
    assert identity["by_instance"]["agent_0"]["mismatched_request_count"] == 4
    assert identity["by_instance"]["agent_1"]["missing_version_request_count"] == 4
    assert identity["identity_pinned"] is False
    assert summary["primary_live_claim_allowed"] is False
    assert summary["minority_flip"]["scope"] == "round_0_correct_positive_proof_minority"


def test_offline_mechanism_replay_matches_full_gate_with_nogood():
    from pieq.live_providers import MockProvider
    from pieq.live_protocol import aggregate, build_prompt, parse_proposal
    from pieq.live_score import _offline_ablation

    manifest = mock_manifest()
    provider = MockProvider()
    for case in build_live_cases():
        proposals = [
            parse_proposal(provider.complete(instance, build_prompt(case, instance["agent_id"], 0, []),
                                             manifest).text, case, instance["agent_id"])
            for instance in manifest["instances"]
        ]
        assert _offline_ablation(case, proposals, manifest["packing_expansions"]) == aggregate(
            case, proposals, "full_pieq")["action"], case.case_id


def test_offline_ablations_skip_structurally_malformed_proofs():
    from pieq.live_score import _offline_ablation

    case = build_live_cases()[0]
    proposal = {
        "action": "promote_replica", "world_refutations": [],
        "proofs": [
            None,
            {"rule_id": [], "consequent": "act:promote_replica", "env_bitmask": 0},
            {"rule_id": "unknown", "consequent": [], "env_bitmask": 0},
        ],
    }
    for disabled in (None, "rule_verifier", "packing", "shield"):
        assert _offline_ablation(case, [proposal], 10000, disabled) == "defer"
