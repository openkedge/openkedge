import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pieq.live_cases import build_live_cases


def protocol():
    assert importlib.util.find_spec("pieq.live_protocol"), "live protocol missing"
    return importlib.import_module("pieq.live_protocol")


def test_mock_pilot_replays_and_resume_does_not_call_provider(tmp_path):
    p = protocol()
    from pieq.live_providers import MockProvider
    path = tmp_path / "traces.jsonl"
    manifest = p.mock_manifest()
    cases = build_live_cases()[:3]
    first = p.run_evaluation(cases, manifest, path, providers={"mock": MockProvider()})
    class MustNotCall:
        def complete(self, *args):
            raise AssertionError("completed request billed twice")
    second = p.run_evaluation(cases, manifest, path, providers={"mock": MustNotCall()})
    assert p.canonical(first) == p.canonical(second)
    assert p.canonical(first) == p.canonical(p.replay_decisions(cases, path))
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len([r for r in records if r["record_type"] == "request"]) == 60
    assert len([r for r in records if r["record_type"] == "decision"]) == 12
    assert {r["condition"] for r in first} == {"independent_vote", "debate", "raw_channel_count", "full_pieq"}


def test_full_case_trace_preserves_distinct_requests_for_shared_fixtures(tmp_path):
    p = protocol()
    path = tmp_path / "full_mock_trace.jsonl"
    cases = build_live_cases()
    assert len(cases) == 40
    p.run_evaluation(cases, p.mock_manifest(), path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    requests = [record for record in records if record["record_type"] == "request"]
    decisions = [record for record in records if record["record_type"] == "decision"]
    assert len(requests) == 40 * 5 * 4
    assert len({record["request_id"] for record in requests}) == len(requests)
    assert len(decisions) == 40 * 4
    assert len({record["decision_id"] for record in decisions}) == len(decisions)
    assert len(p.replay_decisions(cases, path)) == 40 * 4
    from pieq.live_score import score_trace
    summary = score_trace(Path("results/live_cases_manifest.json"), path, cases=cases,
                          bootstrap_replicates=0)
    assert summary["case_count"] == 40
    assert summary["primary_live_claim_allowed"] is False


def test_trace_binds_private_scoring_fixture_before_replay(tmp_path):
    p = protocol()
    from pieq.live_cases import build_manifest, fixture_hash
    case = build_live_cases()[0]
    path = tmp_path / "trace.jsonl"
    p.run_evaluation([case], p.mock_manifest(), path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[0]["scoring_fixture_hash"] == fixture_hash(build_manifest())
    records[0]["scoring_fixture_hash"] = "0" * 64
    path.write_text("\n".join(p.canonical(record) for record in records) + "\n")
    with pytest.raises(ValueError, match="scoring fixture"):
        p.replay_decisions([case], path)
    with pytest.raises(ValueError, match="scoring fixture"):
        p.run_evaluation([case], p.mock_manifest(), path)


@pytest.mark.parametrize("mask", [-1, 1 << 64, True, "1", 1.2])
def test_invalid_mask_is_counted_as_rejected_proof(mask):
    p = protocol()
    case = build_live_cases()[0]
    output = {"action": "defer", "confidence": 0.5, "rationale": "uncertain",
              "citations": [], "proofs": [{"consequent": "act:promote_replica",
              "rule_id": "case_0_r0", "env_bitmask": mask}], "world_refutations": []}
    proposal = p.parse_proposal(json.dumps(output), case)
    result = p.aggregate(case, [proposal, None, None, None, None], "full_pieq")
    assert result["verifier_results"][0]["reason"] == "malformed_or_out_of_range_mask"
    assert result["action"] == "defer"


def test_missing_proofs_are_not_reconstructed_and_refutations_not_trusted():
    p = protocol()
    case = build_live_cases()[0]
    proposal = {"action": "promote_replica", "confidence": 1.0, "rationale": "looks safe",
                "citations": [o.assumption_id for o in case.observations],
                "proofs": [], "world_refutations": [w.world_id for w in case.worlds]}
    decision = p.aggregate(case, [proposal] * 5, "full_pieq")
    assert decision["action"] == "defer"
    assert len(decision["surviving_worlds"]) == 3
    assert p.aggregate(case, [proposal] * 5, "raw_channel_count")["action"] == "promote_replica"


def test_manifest_requires_five_and_labels_single_model_control():
    p = protocol()
    m = p.mock_manifest()
    p.validate_manifest(m)
    m["instances"].pop()
    with pytest.raises(ValueError):
        p.validate_manifest(m)
    m = p.mock_manifest()
    m["run_kind"] = "primary"
    with pytest.raises(ValueError):
        p.validate_manifest(m)
    m = p.mock_manifest()
    m["api_key"] = "never-write-this"
    with pytest.raises(ValueError):
        p.validate_manifest(m)


def test_primary_manifest_rejects_fake_family_diversity_for_one_model():
    p = protocol()
    manifest = p.mock_manifest()
    manifest["run_kind"] = "primary"
    manifest["spending_cap_usd"] = 10
    manifest["max_cost_per_call_usd"] = 0.01
    for index, instance in enumerate(manifest["instances"]):
        instance.update(provider="openai", model="one-returned-model",
                        family="label_a" if index < 3 else "label_b")
    with pytest.raises(ValueError, match="distinct"):
        p.validate_manifest(manifest)


def test_manifest_rejects_pre_case_id_protocol_version():
    p = protocol()
    manifest = p.mock_manifest()
    manifest["protocol_version"] = "pieq-live-protocol-v1"
    with pytest.raises(ValueError, match="protocol version"):
        p.validate_manifest(manifest)


def test_missing_live_credentials_fail_before_trace_is_created(tmp_path, monkeypatch):
    p = protocol()
    manifest = p.mock_manifest()
    manifest["run_kind"] = "primary"
    manifest["spending_cap_usd"] = 10
    manifest["max_cost_per_call_usd"] = 0.01
    for index, instance in enumerate(manifest["instances"]):
        instance.update(provider="openai" if index < 4 else "anthropic",
                        family="openai" if index < 4 else "anthropic",
                        model="model-a" if index < 4 else "model-b")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = tmp_path / "unstarted.jsonl"
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        p.run_evaluation(build_live_cases()[:1], manifest, path)
    assert not path.exists()


def test_live_trace_freezes_endpoint_identity_across_resume(tmp_path, monkeypatch):
    p = protocol()
    from pieq.live_providers import MockProvider
    manifest = p.mock_manifest()
    manifest["run_kind"] = "primary"
    manifest["spending_cap_usd"] = 10
    manifest["max_cost_per_call_usd"] = 0.01
    for index, instance in enumerate(manifest["instances"]):
        instance.update(provider="openai" if index < 4 else "anthropic",
                        family="openai" if index < 4 else "anthropic",
                        model="model-a" if index < 4 else "model-b")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://first.example/v1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://other.example")
    path = tmp_path / "primary.jsonl"
    providers = {"openai": MockProvider(), "anthropic": MockProvider()}
    p.run_evaluation(build_live_cases()[:1], manifest, path, providers=providers)
    header = json.loads(path.read_text().splitlines()[0])
    assert header["provider_endpoints"] == {
        "openai": "https://first.example/v1",
        "anthropic": "https://other.example",
    }
    monkeypatch.setenv("OPENAI_BASE_URL", "https://changed.example/v1")
    with pytest.raises(ValueError, match="endpoint"):
        p.run_evaluation(build_live_cases()[:1], manifest, path, providers=providers)


def test_live_cli_preflight_checks_configuration_without_calls_or_trace(tmp_path, monkeypatch):
    p = protocol()
    manifest = p.mock_manifest()
    manifest["run_kind"] = "primary"
    manifest["spending_cap_usd"] = 10
    manifest["max_cost_per_call_usd"] = 0.01
    for index, instance in enumerate(manifest["instances"]):
        instance.update(provider="openai" if index < 4 else "anthropic",
                        family="openai" if index < 4 else "anthropic",
                        model="model-a" if index < 4 else "model-b")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-preflight-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-preflight-anthropic")
    manifest_path = tmp_path / "run.json"
    manifest_path.write_text(json.dumps(manifest))
    trace_path = tmp_path / "must-not-exist.jsonl"
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_live_llm_eval.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--manifest", str(manifest_path),
         "--preflight", "--trace", str(trace_path)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["logical_requests"] == 800
    assert report["provider_endpoints"]["openai"] == "https://api.openai.com/v1"
    assert "secret-preflight" not in completed.stdout + completed.stderr
    assert not trace_path.exists()


def test_manifest_accepts_paired_finite_instance_prices():
    p = protocol()
    manifest = p.mock_manifest()
    for instance in manifest["instances"]:
        instance["input_usd_per_million"] = 0.25
        instance["output_usd_per_million"] = 1
    p.validate_manifest(manifest)


@pytest.mark.parametrize("field,value", [
    ("input_usd_per_million", -0.01),
    ("input_usd_per_million", float("nan")),
    ("output_usd_per_million", float("inf")),
    ("output_usd_per_million", True),
    ("input_usd_per_million", "0.25"),
])
def test_manifest_rejects_invalid_instance_price(field, value):
    p = protocol()
    manifest = p.mock_manifest()
    manifest["instances"][0].update(input_usd_per_million=0.25,
                                    output_usd_per_million=1.0)
    manifest["instances"][0][field] = value
    with pytest.raises(ValueError):
        p.validate_manifest(manifest)


def test_manifest_rejects_single_unpaired_instance_price():
    p = protocol()
    manifest = p.mock_manifest()
    manifest["instances"][0]["input_usd_per_million"] = 0.25
    with pytest.raises(ValueError):
        p.validate_manifest(manifest)


@pytest.mark.parametrize("field,value", [
    ("model", 12),
    ("family", 12),
    ("supports_seed", "true"),
])
def test_manifest_rejects_wrong_instance_field_types(field, value):
    p = protocol()
    manifest = p.mock_manifest()
    for instance in manifest["instances"]:
        instance[field] = value
    with pytest.raises(ValueError):
        p.validate_manifest(manifest)


def test_paid_manifest_rejects_reserve_below_declared_output_cost():
    p = protocol()
    manifest = p.mock_manifest()
    manifest["run_kind"] = "single_model_control"
    for instance in manifest["instances"]:
        instance.update(
            provider="openai", family="openai", model="pinned-model",
            input_usd_per_million=1,
            output_usd_per_million=10,
        )
    manifest["spending_cap_usd"] = 1
    manifest["max_cost_per_call_usd"] = 0.001
    with pytest.raises(ValueError, match="output cost"):
        p.validate_manifest(manifest)


def test_failures_and_budget_are_explicit_and_not_retried_on_resume(tmp_path):
    p = protocol()
    class Broken:
        def complete(self, *args):
            raise TimeoutError("not a secret")
    manifest = p.mock_manifest()
    manifest["max_attempts"] = 1
    manifest["max_calls"] = 2
    path = tmp_path / "traces.jsonl"
    decisions = p.run_evaluation(build_live_cases()[:1], manifest, path,
                                 providers={"mock": Broken()})
    records = [json.loads(line) for line in path.read_text().splitlines()]
    requests = [r for r in records if r["record_type"] == "request"]
    assert sum(r["status"] == "timeout" for r in requests) == 2
    assert sum(r["status"] == "budget_exhaustion" for r in requests) == 18
    assert all(d["action"] == "defer" for d in decisions)


def test_unhashable_world_refutation_is_malformed_not_api_error():
    p = protocol()
    case = build_live_cases()[0]
    payload = {"action": "defer", "confidence": 0.5, "rationale": "uncertain",
               "citations": [], "proofs": [], "world_refutations": [{}]}
    with pytest.raises(ValueError):
        p.parse_proposal(json.dumps(payload), case)


def test_prompt_contains_only_permitted_public_data_and_previous_round():
    p = protocol()
    case = build_live_cases()[0]
    prompt = p.build_prompt(case, "agent_0", 0, [])
    assert '"true_world"' not in prompt
    assert '"faulty_domains"' not in prompt
    assert '"agent_allocation"' not in prompt
    assert json.loads(prompt)["evidence"] == case.model_visible("agent_0")
    assert "case_id" not in json.loads(prompt)["evidence"]
    assert {item["assumption_id"] for item in json.loads(prompt)["evidence"]["observations"]} == set(
        case.agent_allocation["agent_0"]
    )


def test_mock_complete_proofs_admit_positive_case_and_reject_shared_domain():
    p = protocol()
    from pieq.live_providers import MockProvider
    provider = MockProvider()
    manifest = p.mock_manifest()
    cases = {case.case_id: case for case in build_live_cases()}
    for case_id, expected in [(0, "promote_replica"), (2, "promote_replica")]:
        case = cases[case_id]
        proposals = [p.parse_proposal(provider.complete(instance,
            p.build_prompt(case, instance["agent_id"], 0, []), manifest).text, case)
            for instance in manifest["instances"]]
        decision = p.aggregate(case, proposals, "full_pieq")
        assert decision["action"] == expected
    assert decision["packing"]["reroute_partition_link"] == 1


def test_full_gate_requires_action_request_and_authorized_premises():
    p = protocol()
    from pieq.live_providers import MockProvider
    case = build_live_cases()[0]
    manifest = p.mock_manifest()
    proposals = [p.parse_proposal(MockProvider().complete(
        instance, p.build_prompt(case, instance["agent_id"], 0, []), manifest
    ).text, case) for instance in manifest["instances"]]
    assert p.aggregate(case, proposals, "full_pieq")["action"] == "promote_replica"
    for proposal in proposals:
        proposal["action"] = "defer"
    assert p.aggregate(case, proposals, "full_pieq")["action"] == "defer"


def test_interrupted_call_is_not_automatically_billed_again(tmp_path):
    p = protocol()
    from pieq.live_cases import build_manifest, fixture_hash
    manifest = p.mock_manifest()
    case = build_live_cases()[0]
    prompt = p.build_prompt(case, "agent_0", 0, [])
    request_id = fixture_hash([fixture_hash(manifest), case.case_id, fixture_hash(case.model_visible()),
                              "agent_0", 0, prompt])
    path = tmp_path / "trace.jsonl"
    path.write_text(p.canonical({"record_type": "manifest", "manifest": manifest,
        "manifest_hash": fixture_hash(manifest),
        "scoring_fixture_hash": fixture_hash(build_manifest())}) + "\n" +
        p.canonical({"record_type": "attempt_started", "request_id": request_id, "attempt": 1}) + "\n")
    p.run_evaluation([case], manifest, path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    result = next(r for r in records if r.get("request_id") == request_id and r["record_type"] == "request")
    assert result["status"] == "interrupted_unknown_outcome"
    assert result["attempts"] == 1


@pytest.mark.parametrize("name,response,expected_path", [
    ("openai", {"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 3}}, "/chat/completions"),
    ("anthropic", {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 2}}, "/v1/messages"),
    ("gemini", {"candidates": [{"content": {"parts": [{"text": "{}"}]}}], "usageMetadata": {"totalTokenCount": 3}}, ":generateContent"),
    ("ollama", {"message": {"content": "{}"}, "eval_count": 2}, "/api/chat"),
])
def test_provider_transport_contract(monkeypatch, name, response, expected_path):
    p = protocol()
    import io
    from pieq.live_providers import RESTProvider, _NoCredentialRedirect
    for variable in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(variable, "secret-test-key")
    requests = []
    def transport(request, timeout):
        requests.append(request)
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr("urllib.request.urlopen", transport)
    if name != "ollama":
        class FakeOpener:
            open = staticmethod(transport)

        def fake_build_opener(*handlers):
            assert any(isinstance(handler, _NoCredentialRedirect) for handler in handlers)
            return FakeOpener()

        monkeypatch.setattr("urllib.request.build_opener", fake_build_opener)
    reply = RESTProvider(name).complete({"model": "pinned-v1", "supports_seed": True},
                                        "exact prompt", p.mock_manifest())
    assert reply.text == "{}"
    assert requests[0].full_url.endswith(expected_path)
    body = requests[0].data.decode()
    assert "exact prompt" in body
    assert "secret-test-key" not in body


def test_refutation_packing_exhaustion_is_explicit():
    p = protocol()
    from pieq.live_providers import MockProvider
    case = build_live_cases()[0]
    manifest = p.mock_manifest()
    proposal = p.parse_proposal(MockProvider().complete(manifest["instances"][0],
        p.build_prompt(case, "agent_0", 0, []), manifest).text, case)
    proposal["proofs"] = [proof for proof in proposal["proofs"] if proof["consequent"].startswith("refute:")]
    decision = p.aggregate(case, [proposal] * 5, "full_pieq", packing_expansions=0)
    assert decision["budget_exhausted"]
    assert len(decision["surviving_worlds"]) == 3


@pytest.mark.parametrize("record_type,field,replacement", [
    ("manifest", "manifest_hash", "0" * 64),
    ("request", "request_id", "0" * 64),
    ("request", "prompt", "altered prompt"),
    ("request", "parsed_proposal", {"action": "defer"}),
    ("decision", "action", "probe"),
])
def test_replay_and_resume_reject_changed_trace_fields(
    tmp_path, record_type, field, replacement,
):
    p = protocol()
    from pieq.live_providers import MockProvider
    case = build_live_cases()[0]
    manifest = p.mock_manifest()
    path = tmp_path / "trace.jsonl"
    p.run_evaluation([case], manifest, path, providers={"mock": MockProvider()})
    records = [json.loads(line) for line in path.read_text().splitlines()]
    next(record for record in records if record["record_type"] == record_type)[field] = replacement
    path.write_text("\n".join(p.canonical(record) for record in records) + "\n")
    with pytest.raises(ValueError, match="trace|Trace|manifest|Manifest"):
        p.replay_decisions([case], path)
    with pytest.raises(ValueError, match="trace|Trace|manifest|Manifest"):
        p.run_evaluation([case], manifest, path, providers={"mock": MockProvider()})


def test_resume_recovers_fsynced_success_without_a_second_call(tmp_path):
    p = protocol()
    from pieq.live_providers import MockProvider
    case = build_live_cases()[0]
    manifest = p.mock_manifest()
    path = tmp_path / "trace.jsonl"
    p.run_evaluation([case], manifest, path, providers={"mock": MockProvider()})
    records = [json.loads(line) for line in path.read_text().splitlines()]
    first_result = next(i for i, record in enumerate(records)
                        if record["record_type"] == "attempt_result")
    first_prompt = next(record for record in records
                        if record["record_type"] == "request")["prompt"]
    path.write_text("\n".join(p.canonical(record) for record in records[:first_result + 1]) + "\n")

    class FailIfRepeated:
        def complete(self, instance, prompt, configuration):
            if prompt == first_prompt:
                raise AssertionError("successful API attempt was repeated")
            return MockProvider().complete(instance, prompt, configuration)

    p.run_evaluation([case], manifest, path, providers={"mock": FailIfRepeated()})
    resumed = [json.loads(line) for line in path.read_text().splitlines()]
    recovered = next(record for record in resumed if record["record_type"] == "request")
    assert recovered["status"] == "ok"
    assert recovered["attempts"] == 1
    assert recovered["parsed_proposal"]["action"] == "promote_replica"


def test_resume_uses_remaining_retry_after_fsynced_timeout(tmp_path):
    p = protocol()
    from pieq.live_cases import build_manifest, fixture_hash
    from pieq.live_providers import MockProvider

    case = build_live_cases()[0]
    manifest = p.mock_manifest()
    path = tmp_path / "trace.jsonl"
    prompt = p.build_prompt(case, "agent_0", 0, [])
    request_id = fixture_hash([
        fixture_hash(manifest), case.case_id, fixture_hash(case.model_visible()),
        "agent_0", 0, prompt,
    ])
    records = [
        {"record_type": "manifest", "manifest": manifest,
         "manifest_hash": fixture_hash(manifest),
         "scoring_fixture_hash": fixture_hash(build_manifest())},
        {"record_type": "attempt_started", "request_id": request_id, "attempt": 1},
        {"record_type": "attempt_result", "request_id": request_id, "attempt": 1,
         "status": "timeout", "raw_reply": None, "usage": {}, "elapsed_seconds": 0.1},
    ]
    path.write_text("\n".join(p.canonical(record) for record in records) + "\n")

    p.run_evaluation([case], manifest, path, providers={"mock": MockProvider()})
    written = [json.loads(line) for line in path.read_text().splitlines()]
    first = next(record for record in written
                 if record["record_type"] == "request"
                 and record["request_id"] == request_id)
    assert first["status"] == "ok"
    assert first["attempts"] == 2
    assert any(record["record_type"] == "attempt_started"
               and record["request_id"] == request_id and record["attempt"] == 2
               for record in written)
    assert p.canonical(p.replay_decisions([case], path)) == p.canonical(
        p.run_evaluation([case], manifest, path, providers={"mock": MockProvider()}),
    )


def test_provider_value_error_is_api_error_but_invalid_reply_is_malformed(tmp_path):
    p = protocol()
    manifest = p.mock_manifest()
    manifest["max_attempts"] = 1
    manifest["max_calls"] = 1
    case = build_live_cases()[0]

    class ProviderFailure:
        def complete(self, *args):
            raise ValueError("provider failed")

    class InvalidReply:
        def complete(self, *args):
            from pieq.live_providers import Reply
            return Reply("{", {}, "mock")

    statuses = []
    for provider in (ProviderFailure(), InvalidReply()):
        path = tmp_path / f"{type(provider).__name__}.jsonl"
        p.run_evaluation([case], manifest, path, providers={"mock": provider})
        statuses.append(next(json.loads(line)["status"] for line in path.read_text().splitlines()
                             if json.loads(line)["record_type"] == "request"))
    assert statuses == ["api_error", "malformed_output"]


def test_trace_records_tcb_time_and_retry_timestamps_without_changing_replay(tmp_path):
    p = protocol()
    from pieq.live_providers import MockProvider
    from pieq.live_providers import Reply
    case = build_live_cases()[0]
    manifest = p.mock_manifest()
    attempts = 0

    class RetryThenSucceed:
        def complete(self, instance, prompt, configuration):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError()
            return MockProvider().complete(instance, prompt, configuration)

    path = tmp_path / "trace.jsonl"
    decisions = p.run_evaluation([case], manifest, path,
                                 providers={"mock": RetryThenSucceed()})
    records = [json.loads(line) for line in path.read_text().splitlines()]
    attempt_records = [record for record in records
                       if record["record_type"] in {"attempt_started", "attempt_result"}]
    assert all(record.get("timestamp") for record in attempt_records)
    assert all(record.get("elapsed_seconds", 0) >= 0 for record in attempt_records
               if record["record_type"] == "attempt_result")
    stored_decisions = [record for record in records if record["record_type"] == "decision"]
    assert all(record["tcb_elapsed_seconds"] >= 0 and record["timestamp"]
               for record in stored_decisions)
    assert p.canonical(decisions) == p.canonical(p.replay_decisions([case], path))


def test_each_proof_has_exclusive_rejection_and_each_action_has_gate_reason():
    p = protocol()
    from pieq.live_providers import MockProvider
    case = build_live_cases()[2]
    manifest = p.mock_manifest()
    proposals = [p.parse_proposal(MockProvider().complete(
        instance, p.build_prompt(case, instance["agent_id"], 0, []), manifest
    ).text, case, instance["agent_id"]) for instance in manifest["instances"]]
    valid = next(proof for proposal in proposals for proof in proposal["proofs"]
                 if proof["consequent"].startswith("act:"))
    bad = dict(valid, rule_id="missing-rule")
    proposals[0]["proofs"].extend([bad, dict(valid, env_bitmask=-1)])
    decision = p.aggregate(case, proposals, "full_pieq")
    rejected = [result for result in decision["verifier_results"] if not result["accepted"]]
    assert {result["reason"] for result in rejected} >= {
        "unknown_rule_id", "malformed_or_out_of_range_mask",
    }
    assert len(decision["verifier_results"]) == sum(len(proposal["proofs"])
                                                    for proposal in proposals)
    assert all(result["reason"] for result in decision["gate_results"].values())
    assert any(result["shield_reason"] != "ADMIT_COMPOUND_SAFE"
               for result in decision["gate_results"].values())


def test_agent_specific_citations_and_proof_masks_are_authorized():
    p = protocol()
    from pieq.live_providers import MockProvider
    case = build_live_cases()[0]
    manifest = p.mock_manifest()
    raw = MockProvider().complete(manifest["instances"][0],
                                  p.build_prompt(case, "agent_0", 0, []), manifest).text
    proposal = json.loads(raw)
    other = next(obs for obs in case.observations
                 if obs.assumption_id not in case.agent_allocation["agent_0"])
    proposal["citations"] = [other.assumption_id]
    with pytest.raises(ValueError):
        p.parse_proposal(json.dumps(proposal), case, "agent_0")
    proposal["citations"] = []
    proposal["proofs"] = [dict(proposal["proofs"][0], env_bitmask=other.bitmask)]
    parsed = p.parse_proposal(json.dumps(proposal), case, "agent_0")
    decision = p.aggregate(case, [parsed, None, None, None, None], "full_pieq")
    assert decision["verifier_results"][0]["reason"] == "unauthorized_assumption"


def test_mock_cli_uses_a_separate_default_trace(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_live_llm_eval.py"
    subprocess.run([sys.executable, str(script), "--mock-pilot"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    assert (tmp_path / "results" / "live_mock_traces.jsonl").exists()
    assert not (tmp_path / "results" / "live_llm_traces.jsonl").exists()


def test_mock_cli_rejects_explicit_manifest_before_any_trace_or_call(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_live_llm_eval.py"
    manifest_path = tmp_path / "real-manifest.json"
    manifest_path.write_text(json.dumps(protocol().mock_manifest()))
    result = subprocess.run(
        [sys.executable, str(script), "--mock-pilot", "--manifest", str(manifest_path)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "--mock-pilot" in result.stderr
    assert not (tmp_path / "results" / "live_mock_traces.jsonl").exists()


def test_normal_cli_rejects_mock_only_manifest_before_any_trace_or_call(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_live_llm_eval.py"
    manifest_path = tmp_path / "mock-manifest.json"
    manifest_path.write_text(json.dumps(protocol().mock_manifest()))
    result = subprocess.run(
        [sys.executable, str(script), "--manifest", str(manifest_path)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "mock-only" in result.stderr
    assert not (tmp_path / "results" / "live_llm_traces.jsonl").exists()


def test_replay_rejects_request_model_version_inconsistent_with_attempt(tmp_path):
    p = protocol()
    case = build_live_cases()[0]
    path = tmp_path / "trace.jsonl"
    p.run_evaluation([case], p.mock_manifest(), path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    first_request = next(record for record in records if record["record_type"] == "request")
    first_request["model_version"] = "unexpected-model"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    with pytest.raises(ValueError, match="fsynced attempt result"):
        p.replay_decisions([case], path)


def test_mock_replay_rejects_nonmock_saved_trace(tmp_path):
    p = protocol()
    from pieq.live_providers import MockProvider
    manifest = p.mock_manifest()
    manifest["instances"] = [
        {**instance, "provider": "openai", "family": "offline-control"}
        for instance in manifest["instances"]
    ]
    manifest["spending_cap_usd"] = 1
    manifest["max_cost_per_call_usd"] = 0.001
    trace = tmp_path / "control.jsonl"
    p.run_evaluation(build_live_cases()[:3], manifest, trace,
                     providers={"openai": MockProvider()})
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_live_llm_eval.py"
    result = subprocess.run(
        [sys.executable, str(script), "--mock-pilot", "--replay", "--trace", str(trace)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "mock" in result.stderr.lower()


def test_budget_after_failed_attempt_remains_replayable(tmp_path):
    p = protocol()
    manifest = p.mock_manifest()
    manifest["max_calls"] = 1
    class Timeout:
        def complete(self, *args):
            raise TimeoutError()
    case = build_live_cases()[0]
    path = tmp_path / "trace.jsonl"
    first = p.run_evaluation([case], manifest, path, providers={"mock": Timeout()})
    assert p.canonical(first) == p.canonical(p.replay_decisions([case], path))
    assert p.canonical(first) == p.canonical(
        p.run_evaluation([case], manifest, path, providers={"mock": Timeout()})
    )


def test_resume_rejects_budget_label_after_fsynced_success(tmp_path):
    p = protocol()
    case = build_live_cases()[0]
    manifest = p.mock_manifest()
    path = tmp_path / "trace.jsonl"
    p.run_evaluation([case], manifest, path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    first_request = next(i for i, record in enumerate(records)
                         if record["record_type"] == "request")
    prefix = records[:first_request + 1]
    prefix[-1]["status"] = "budget_exhaustion"
    prefix[-1]["parsed_proposal"] = None
    path.write_text("\n".join(p.canonical(record) for record in prefix) + "\n")
    with pytest.raises(ValueError, match="fsynced attempt result"):
        p.run_evaluation([case], manifest, path)


def test_resume_does_not_hide_unknown_later_retry_behind_earlier_timeout(tmp_path):
    p = protocol()
    from pieq.live_cases import build_manifest, fixture_hash
    manifest = p.mock_manifest()
    case = build_live_cases()[0]
    prompt = p.build_prompt(case, "agent_0", 0, [])
    request_id = fixture_hash([fixture_hash(manifest), case.case_id, fixture_hash(case.model_visible()),
                               "agent_0", 0, prompt])
    path = tmp_path / "trace.jsonl"
    records = [
        {"record_type": "manifest", "manifest": manifest, "manifest_hash": fixture_hash(manifest),
         "scoring_fixture_hash": fixture_hash(build_manifest())},
        {"record_type": "attempt_started", "request_id": request_id, "attempt": 1},
        {"record_type": "attempt_result", "request_id": request_id, "attempt": 1,
         "status": "timeout", "raw_reply": None, "usage": {}, "elapsed_seconds": 0.1},
        {"record_type": "attempt_started", "request_id": request_id, "attempt": 2},
    ]
    path.write_text("\n".join(p.canonical(record) for record in records) + "\n")
    p.run_evaluation([case], manifest, path)
    written = [json.loads(line) for line in path.read_text().splitlines()]
    request = next(record for record in written if record["record_type"] == "request")
    assert request["status"] == "interrupted_unknown_outcome"
    assert request["attempts"] == 2


def test_non_object_trace_record_is_rejected_as_damaged_trace(tmp_path):
    p = protocol()
    manifest = p.mock_manifest()
    from pieq.live_cases import fixture_hash
    path = tmp_path / "trace.jsonl"
    path.write_text(p.canonical({"record_type": "manifest", "manifest": manifest,
                                 "manifest_hash": fixture_hash(manifest)}) + "\n[]\n")
    with pytest.raises(ValueError, match="trace|Trace"):
        p.run_evaluation(build_live_cases()[:1], manifest, path)

def test_parse_proposal_unwraps_single_fence_and_think_block():
    p = protocol()
    case = build_live_cases()[0]
    agent = "agent_0"
    inner = p.canonical({
        "action": "defer", "confidence": 0.5, "rationale": "r",
        "citations": [], "proofs": [], "world_refutations": []})
    for raw in (inner,
                f"```json\n{inner}\n```",
                f"```\n{inner}\n```",
                f"<think>reasoning prose</think>\n{inner}",
                f"<think>x</think>\n```json\n{inner}\n```"):
        assert p.parse_proposal(raw, case, agent)["action"] == "defer"
    for raw in (f"preamble {inner}",
                f"```json\n{inner}\n``` trailing",
                f"<think>never closed {inner}",
                f"```json\n{inner}\n```\n```json\n{inner}\n```"):
        with pytest.raises(p.MalformedOutputError):
            p.parse_proposal(raw, case, agent)


def test_primary_manifest_accepts_cli_and_local_openai_identities():
    p = protocol()
    manifest = p.mock_manifest()
    manifest["run_kind"] = "primary"
    manifest["instances"] = [
        {"agent_id": "agent_0", "provider": "claude_cli", "family": "anthropic-claude",
         "model": "claude-sonnet-5-5", "supports_seed": False,
         "input_usd_per_million": 2.0, "output_usd_per_million": 10.0},
        {"agent_id": "agent_1", "provider": "claude_cli", "family": "anthropic-claude",
         "model": "claude-haiku-5-5", "supports_seed": False,
         "input_usd_per_million": 0.1, "output_usd_per_million": 0.5},
        {"agent_id": "agent_2", "provider": "codex_cli", "family": "openai-gpt",
         "model": "openai.gpt-5.6-sol", "supports_seed": False},
        {"agent_id": "agent_3", "provider": "codex_cli", "family": "openai-gpt",
         "model": "openai.gpt-5.6-sol", "supports_seed": False},
        {"agent_id": "agent_4", "provider": "local_openai", "family": "qwen3",
         "model": "Qwen/Qwen3-32B", "supports_seed": True},
    ]
    manifest["max_cost_per_call_usd"] = 0.25
    manifest["spending_cap_usd"] = 400.0
    p.validate_manifest(manifest)
    # CLI agents bill through their own authenticated accounts: caps stay mandatory.
    manifest["spending_cap_usd"] = 0
    with pytest.raises(ValueError, match="spending cap"):
        p.validate_manifest(manifest)
