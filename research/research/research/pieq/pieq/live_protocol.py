"""Frozen five-instance collection, conservative aggregation, and offline replay."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.error

from pieq.core import PlausibleWorldGate, compute_disjoint_justification_packing_bounded
from pieq.live_cases import build_manifest, fixture_hash
from pieq.live_providers import CLI_PROVIDERS, make_provider

PROTOCOL_VERSION = "pieq-live-protocol-v3"
SUPPORTED_PROVIDERS = frozenset({"mock", "openai", "anthropic", "gemini", "ollama",
                                 "local_openai"} | set(CLI_PROVIDERS))
# Providers with no per-token billing boundary in this process: the mock, a local
# Ollama server, and a local OpenAI-compatible server. CLI agents bill through
# their own authenticated provider accounts, so they stay under the paid caps.
_UNBILLED_PROVIDERS = frozenset({"mock", "ollama", "local_openai"})
CONDITIONS = ("independent_vote", "debate", "raw_channel_count", "full_pieq")


class MalformedOutputError(ValueError):
    """A model reply violates the frozen proposal contract."""


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def mock_manifest():
    return {"run_id": "mock-pilot-v2", "run_kind": "single_model_control",
            "protocol_version": PROTOCOL_VERSION,
            "instances": [{"agent_id": f"agent_{i}", "provider": "mock",
                           "family": "mock", "model": "deterministic-public-v1",
                           "supports_seed": True} for i in range(5)],
            "temperature": 0, "seed": 17, "max_output_tokens": 2048,
            "timeout_seconds": 60, "max_attempts": 2, "max_calls": 800,
            "spending_cap_usd": 0, "max_cost_per_call_usd": 0,
            "packing_expansions": 10000}


def validate_manifest(manifest):
    allowed_manifest = {
        "run_id", "run_kind", "protocol_version", "instances", "temperature",
        "seed", "max_output_tokens", "timeout_seconds", "max_attempts",
        "max_calls", "spending_cap_usd", "max_cost_per_call_usd",
        "packing_expansions",
    }
    if not isinstance(manifest, dict) or set(manifest) != allowed_manifest:
        raise ValueError("Manifest fields must match the credential-free schema")
    if type(manifest["run_id"]) is not str or not manifest["run_id"]:
        raise ValueError("Missing run ID")
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Manifest protocol version mismatch")
    instances = manifest["instances"]
    base_instance_fields = {"agent_id", "provider", "family", "model", "supports_seed"}
    price_fields = {"input_usd_per_million", "output_usd_per_million"}
    if type(instances) is not list or any(
        type(item) is not dict or
        set(item) not in (base_instance_fields, base_instance_fields | price_fields)
        for item in instances
    ):
        raise ValueError("Invalid instance fields")
    if len(instances) != 5 or {item["agent_id"] for item in instances} != {f"agent_{i}" for i in range(5)}:
        raise ValueError("Exactly five named agent_0..agent_4 instances required")
    for instance in instances:
        if type(instance["provider"]) is not str or instance["provider"] not in SUPPORTED_PROVIDERS:
            raise ValueError("Unsupported provider")
        if (type(instance["model"]) is not str or not instance["model"].strip()
                or type(instance["family"]) is not str or not instance["family"].strip()):
            raise ValueError("Pin model identifiers and family")
        if type(instance["supports_seed"]) is not bool:
            raise ValueError("supports_seed must be boolean")
        for field in price_fields & instance.keys():
            price = instance[field]
            if type(price) not in (int, float) or not math.isfinite(price) or price < 0:
                raise ValueError(f"Invalid {field}")
    families = {(item["provider"], item["family"]) for item in instances}
    identities = {(item["provider"], item["model"]) for item in instances}
    if manifest["run_kind"] == "primary":
        if (len(families) < 2 or len(identities) < 2
                or any(item["provider"] == "mock" for item in instances)):
            raise ValueError("Primary comparison requires at least two distinct real provider/model identities and families")
    elif manifest["run_kind"] != "single_model_control":
        raise ValueError("Unknown run kind")
    elif len({(item["provider"], item["family"], item["model"]) for item in instances}) != 1:
        raise ValueError("Single-model control requires one provider/family/model")
    for field in ("max_output_tokens", "max_attempts", "max_calls", "packing_expansions"):
        if type(manifest[field]) is not int or manifest[field] < 1:
            raise ValueError(f"Invalid {field}")
    for field in ("temperature", "timeout_seconds", "spending_cap_usd", "max_cost_per_call_usd"):
        if not math.isfinite(manifest[field]) or manifest[field] < 0:
            raise ValueError(f"Invalid {field}")
    if any(item["provider"] not in _UNBILLED_PROVIDERS for item in instances):
        if manifest["max_cost_per_call_usd"] <= 0 or manifest["spending_cap_usd"] <= 0:
            raise ValueError("Paid APIs require a positive conservative per-call bound and spending cap")
        for instance in instances:
            if "output_usd_per_million" in instance:
                declared_output_ceiling = (
                    instance["output_usd_per_million"] * manifest["max_output_tokens"] / 1_000_000
                )
                if manifest["max_cost_per_call_usd"] < declared_output_ceiling:
                    raise ValueError("Per-call reserve is below the declared maximum output cost")


def build_prompt(case, agent_id, round_index, previous):
    return canonical({
        "protocol_version": PROTOCOL_VERSION, "agent_id": agent_id, "round": round_index,
        "evidence": case.model_visible(agent_id),
        "previous_round_messages": previous if round_index else [],
        "instructions": "Return one JSON object, no markdown: action (candidate or defer), "
            "confidence (0..1), rationale (prose), citations, proofs, world_refutations. "
            "citations: a list of assumption ID strings drawn only from the observations in "
            "your own evidence payload; never cite an ID you saw only in "
            "previous_round_messages. proofs: a list of {consequent, rule_id, env_bitmask} "
            "objects whose env_bitmask is the integer OR of your own observations' bit "
            "positions. world_refutations: a flat list of world ID strings only, no objects. "
            "Supply complete Horn proofs if you can; leave unavailable proofs empty. "
            "Refutations require refute:<world_id> proofs. Previous round messages may inform "
            "your reasoning, but citations and proof masks must stay within your own evidence. "
            "A mutation requires k_a independent complete proofs and safety in every retained world.",
    })


def _extract_json_text(raw):
    """Unwrap at most one leading <think>...</think> block and one markdown fence.

    Several chat models wrap the requested JSON object in a reasoning preamble or a
    ```json fence despite the no-markdown instruction. The unwrapping is purely
    syntactic, identical for every provider and condition, and the verbatim reply is
    archived in the trace. Anything else still fails parsing as malformed output.
    """
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if text.startswith("<think>"):
        _, separator, text = text.partition("</think>")
        if not separator:
            return raw
        text = text.strip()
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        first_line, separator, body = text[3:-3].partition("\n")
        if separator and (not first_line.strip() or first_line.strip().isalnum()):
            text = body.strip()
    return text


def parse_proposal(raw, case, agent_id=None):
    try:
        output = json.loads(_extract_json_text(raw),
                            parse_constant=lambda val: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    except (ValueError, TypeError) as error:
        raise MalformedOutputError("Invalid proposal JSON") from error
    required = {"action", "confidence", "rationale", "citations", "proofs", "world_refutations"}
    if not isinstance(output, dict) or not required <= output.keys():
        raise MalformedOutputError("Missing proposal fields")
    if type(output["action"]) is not str or output["action"] not in case.model_visible()["candidate_actions"]:
        raise MalformedOutputError("Unknown action")
    confidence = output["confidence"]
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        raise MalformedOutputError("Invalid confidence")
    if not isinstance(output["rationale"], str):
        raise MalformedOutputError("Rationale must be prose")
    known = (set(case.agent_allocation[agent_id]) if agent_id is not None
             else {obs.assumption_id for obs in case.observations})
    if not isinstance(output["citations"], list) or any(type(c) is not str or c not in known for c in output["citations"]):
        raise MalformedOutputError("Invalid citations")
    if not isinstance(output["world_refutations"], list) or any(
        type(w) is not str or w not in {s.world_id for s in case.worlds}
        for w in output["world_refutations"]
    ):
        raise MalformedOutputError("Invalid world refutations")
    if not isinstance(output["proofs"], list) or len(output["proofs"]) > 256:
        raise MalformedOutputError("Proof proposal bound exceeded")
    return output


def classify_proof(case, proof, agent_id, atms):
    """First mutually exclusive failure in the submitted proof's verification path."""
    if not isinstance(proof, dict) or set(proof) != {"consequent", "rule_id", "env_bitmask"}:
        return "malformed_or_out_of_range_mask"
    mask = proof["env_bitmask"]
    if type(mask) is not int or not 0 <= mask < 1 << 64:
        return "malformed_or_out_of_range_mask"
    rule = atms.verifier.rules.get(proof["rule_id"]) if type(proof["rule_id"]) is str else None
    if rule is None:
        return "unknown_rule_id"
    consequent = proof["consequent"]
    worlds = {world.world_id for world in case.worlds}
    if (type(consequent) is str and consequent.startswith("refute:")
            and consequent[7:] not in worlds) or (
            rule.consequent.startswith("refute:") and rule.consequent[7:] not in worlds):
        return "invalid_world_refutation_rule"
    if consequent != rule.consequent:
        return "wrong_consequent"
    visible = set(case.agent_allocation[agent_id])
    authorized_mask = sum(obs.bitmask for obs in case.observations
                          if obs.assumption_id in visible)
    if mask & ~authorized_mask:
        return "unauthorized_assumption"
    propositions = []
    for bit in range(mask.bit_length()):
        if mask & (1 << bit):
            assumption = atms.assumptions_by_bit.get(bit)
            if assumption is None or not atms.verifier.verify_assumption_signature(assumption):
                return "unsigned_or_badly_signed_assumption"
            propositions.append(assumption.proposition)
    if len(propositions) != len(rule.antecedents) or set(propositions) != set(rule.antecedents):
        return "wrong_or_extra_antecedents"
    return "verified"


def aggregate(case, proposals, condition, packing_expansions=10000):
    votes = Counter(p["action"] for p in proposals if p)
    majority = next((action for action, count in sorted(votes.items()) if count >= 3), "defer")
    base = {"case_id": case.case_id, "condition": condition, "action": "defer",
            "gate_reason": "NO_MAJORITY", "verifier_results": [], "surviving_worlds": []}
    if condition in {"independent_vote", "debate"}:
        base.update(action=majority, gate_reason="FIXED_THREE_OF_FIVE" if majority != "defer" else "TIE_OR_DEFER")
        return base
    if condition == "raw_channel_count":
        channels = {obs.assumption_id: obs.channel_id for obs in case.observations}
        counts = {}
        for action in sorted(votes):
            cited = {channels[c] for p in proposals if p and p["action"] == action for c in p["citations"]}
            counts[action] = len(cited)
        candidates = sorted(a for a, n in counts.items() if n >= case.k_a and a not in {"defer", "probe"})
        base.update(action=candidates[0] if len(candidates) == 1 else "defer",
                    gate_reason="RAW_CHANNEL_QUORUM" if len(candidates) == 1 else "RAW_TIE_OR_INSUFFICIENT",
                    channel_counts=counts)
        return base
    if condition != "full_pieq":
        raise ValueError("Unknown aggregation condition")
    atms = case.registered_atms()
    for index, proposal in enumerate(proposals):
        if not proposal:
            continue
        for proof in proposal["proofs"]:
            agent_id = f"agent_{index}"
            reason = classify_proof(case, proof, agent_id, atms)
            if reason == "verified":
                consequent = proof["consequent"]
                if consequent.startswith("act:") and proposal["action"] != consequent[4:]:
                    reason = "action_not_requested"
                elif consequent.startswith("refute:") and consequent[7:] not in proposal["world_refutations"]:
                    reason = "refutation_not_requested"
                elif not consequent.startswith(("act:", "refute:")):
                    reason = "wrong_consequent"
            if reason == "verified":
                cert = atms.add_verified_justification(agent_id, f"{agent_id}:round_0",
                    proof["consequent"], proof["env_bitmask"], proof["rule_id"])
                if cert is None:
                    reason = "verification_budget_exhausted" if atms.budget_exceeded else "wrong_or_extra_antecedents"
            base["verifier_results"].append({"agent_id": agent_id, "proposal": proof,
                                            "accepted": reason == "verified", "reason": reason})
    # Invariants are checked deterministically over registered primitive evidence.
    base["nogood_results"] = []
    for invariant in case.invariants:
        matching = [o for o in case.observations if o.proposition in invariant.conflict_predicates]
        if {o.proposition for o in matching} == set(invariant.conflict_predicates):
            cert = atms.register_nogood("collector", sum(o.bitmask for o in matching), invariant.invariant_id)
            base["nogood_results"].append({"invariant_id": invariant.invariant_id,
                                            "accepted": cert is not None})
    gate = PlausibleWorldGate(case.worlds, ["defer", "probe"])
    refs = {world.world_id: atms.active_witness_justifications(f"refute:{world.world_id}")
            for world in case.worlds}
    survivors = gate.compute_fault_tolerant_belief_set(refs, atms.assumptions_by_bit, case.f,
            atms.budget_exceeded, max_packing_expansions=packing_expansions)
    base["surviving_worlds"] = sorted(w.world_id for w in survivors)
    candidates = []
    packing = {}
    gate_results = {}
    truncated = atms.budget_exceeded
    refutation_packing = {}
    for world_id, witnesses in refs.items():
        count, _, exhausted = compute_disjoint_justification_packing_bounded(
            witnesses, atms.assumptions_by_bit, packing_expansions)
        truncated |= exhausted
        refutation_packing[world_id] = {"count": count, "budget_exhausted": exhausted}
    base["refutation_packing"] = refutation_packing
    for action in sorted(case.model_visible()["candidate_actions"]):
        if action in {"defer", "probe"}:
            continue
        count, _, exhausted = compute_disjoint_justification_packing_bounded(
            atms.active_witness_justifications(f"act:{action}"), atms.assumptions_by_bit, packing_expansions)
        packing[action] = count
        truncated |= exhausted
        ok, reason = gate.evaluate_concurrent_compound_safety(action, case.incident_class, 0, survivors, [])
        if exhausted:
            action_reason = "packing_budget_exhausted"
        elif count < case.k_a:
            action_reason = "insufficient_disjoint_proofs"
        elif not ok:
            action_reason = "shield_rejected"
        else:
            action_reason = "eligible"
        gate_results[action] = {"packing_count": count, "packing_budget_exhausted": exhausted,
                                "shield_reason": reason, "reason": action_reason}
        if action_reason == "eligible":
            candidates.append(action)
    base.update(packing=packing, budget_exhausted=truncated,
                gate_results=gate_results,
                action=candidates[0] if len(candidates) == 1 else "defer",
                gate_reason="ADMIT_COMPOUND_SAFE" if len(candidates) == 1 else "NO_UNIQUE_DISJOINT_SAFE_QUORUM",
                concurrent_context="no_inflight_operations_fixture")
    return base


def _append(path, record):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(canonical(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _records(path):
    if not Path(path).exists():
        return []
    result = []
    for line in Path(path).read_text().splitlines():
        try:
            result.append(json.loads(line))
        except ValueError as error:
            raise ValueError("Damaged trace: refuse resume to avoid duplicate billing") from error
    return result


def _decision(case, proposals, condition, manifest, manifest_hash, fixture):
    result = aggregate(case, proposals, condition, manifest["packing_expansions"])
    result.update(record_type="decision", run_id=manifest["run_id"],
                  collector_fixture_hash=fixture,
                  decision_id=fixture_hash([manifest_hash, case.case_id, fixture, condition]))
    return result


def _audit_trace(cases, records, manifest=None):
    """Check the complete saved prefix before trusting it for replay or resume."""
    if not records:
        if manifest is None:
            raise ValueError("Empty trace")
        return {}, {}, set(), 0, manifest, {}
    header = records[0]
    if not isinstance(header, dict) or header.get("record_type") != "manifest" or not isinstance(header.get("manifest"), dict):
        raise ValueError("Trace must start with a manifest")
    saved_manifest = header["manifest"]
    validate_manifest(saved_manifest)
    if saved_manifest["run_kind"] == "primary":
        expected_providers = {item["provider"] for item in saved_manifest["instances"]}
        endpoints = header.get("provider_endpoints")
        if (not isinstance(endpoints, dict) or set(endpoints) != expected_providers
                or any(not isinstance(value, str) or not value
                       for value in endpoints.values())):
            raise ValueError("Trace lacks frozen provider endpoint identities")
    manifest_hash = fixture_hash(saved_manifest)
    if header.get("manifest_hash") != manifest_hash:
        raise ValueError("Trace manifest hash mismatch")
    if header.get("scoring_fixture_hash") != fixture_hash(build_manifest()):
        raise ValueError("Trace scoring fixture hash mismatch")
    if manifest is not None and manifest != saved_manifest:
        raise ValueError("Frozen manifest differs from existing trace")

    requests, starts, results, decisions = {}, {}, {}, {}
    for record in records[1:]:
        if not isinstance(record, dict):
            raise ValueError("Damaged trace record")
        kind = record.get("record_type")
        if kind == "manifest":
            raise ValueError("Duplicate trace manifest")
        if kind == "request":
            key = record.get("request_id")
            if key in requests:
                raise ValueError("Duplicate trace request")
            requests[key] = record
        elif kind == "attempt_started":
            key = (record.get("request_id"), record.get("attempt"))
            if key in starts:
                raise ValueError("Duplicate trace attempt")
            starts[key] = record
        elif kind == "attempt_result":
            key = (record.get("request_id"), record.get("attempt"))
            if key in results:
                raise ValueError("Duplicate trace attempt result")
            results[key] = record
        elif kind == "decision":
            key = record.get("decision_id")
            if key in decisions:
                raise ValueError("Duplicate trace decision")
            decisions[key] = record
        else:
            raise ValueError("Unknown trace record type")

    expected = {}
    generated_decisions = {}
    for case in cases:
        fixture = fixture_hash(case.model_visible())
        previous = []
        rounds = []
        for round_index in range(4):
            proposals, messages = [], []
            complete = True
            for instance in saved_manifest["instances"]:
                agent = instance["agent_id"]
                prompt = build_prompt(case, agent, round_index, previous)
                request_id = fixture_hash([manifest_hash, case.case_id, fixture, agent, round_index, prompt])
                expected[request_id] = (case, agent, round_index, prompt, fixture)
                record = requests.get(request_id)
                if record is None:
                    complete = False
                    continue
                if any(record.get(field) != value for field, value in (
                    ("run_id", saved_manifest["run_id"]), ("case_id", case.case_id),
                    ("agent_id", agent), ("round", round_index), ("prompt", prompt),
                    ("collector_fixture_hash", fixture),
                    ("condition", "round_0_shared" if round_index == 0 else "debate"),
                )):
                    raise ValueError("Trace request metadata or exact prompt mismatch")
                status = record.get("status")
                if status == "ok":
                    try:
                        parsed = parse_proposal(record.get("raw_reply"), case, agent)
                    except MalformedOutputError as error:
                        raise ValueError("Trace successful reply is malformed") from error
                    if parsed != record.get("parsed_proposal"):
                        raise ValueError("Trace parsed proposal mismatch")
                elif status == "malformed_output":
                    try:
                        parse_proposal(record.get("raw_reply"), case, agent)
                    except MalformedOutputError:
                        pass
                    else:
                        raise ValueError("Trace malformed status has a valid reply")
                    if record.get("parsed_proposal") is not None:
                        raise ValueError("Trace failed request has a parsed proposal")
                elif status in {"timeout", "rate_limit", "api_error", "budget_exhaustion",
                                "interrupted_unknown_outcome"}:
                    if record.get("parsed_proposal") is not None:
                        raise ValueError("Trace failed request has a parsed proposal")
                else:
                    raise ValueError("Trace request has unknown status")
                proposals.append(record["parsed_proposal"])
                messages.append({"agent_id": agent, "raw_reply": record.get("raw_reply"),
                                 "status": status})
            if not complete:
                break
            rounds.append(proposals)
            previous = messages
        if len(rounds) == 4:
            for condition in CONDITIONS:
                generated = _decision(case, rounds[3] if condition == "debate" else rounds[0],
                                      condition, saved_manifest, manifest_hash, fixture)
                generated_decisions[generated["decision_id"]] = generated
    if set(requests) - set(expected):
        raise ValueError("Trace has unknown or changed request ID")
    if {key[0] for key in starts} - set(expected):
        raise ValueError("Trace has unknown attempt request ID")
    if {key[0] for key in results} - set(expected):
        raise ValueError("Trace has unknown result request ID")
    if set(decisions) - set(generated_decisions):
        raise ValueError("Trace has unknown or premature decision")
    for key, result in results.items():
        if key not in starts:
            raise ValueError("Trace attempt result has no start")
        if result.get("status") not in {"ok", "timeout", "rate_limit", "api_error",
                                        "malformed_output"}:
            raise ValueError("Trace attempt result has invalid status")
        if result["status"] == "ok":
            case, agent, _, _, _ = expected[key[0]]
            try:
                parse_proposal(result.get("raw_reply"), case, agent)
            except MalformedOutputError as error:
                raise ValueError("Trace successful attempt has malformed reply") from error
    for request_id, record in requests.items():
        numbered = sorted((attempt for rid, attempt in starts if rid == request_id))
        if record.get("attempts") != len(numbered):
            raise ValueError("Trace request attempt count mismatch")
        last_result = results.get((request_id, numbered[-1])) if numbered else None
        if last_result is not None:
            saved_status = record.get("status")
            attempt_status = last_result.get("status")
            if (saved_status != attempt_status
                    and not (saved_status == "budget_exhaustion"
                             and attempt_status in {"timeout", "rate_limit", "api_error"})):
                raise ValueError("Trace request differs from fsynced attempt result")
            for field in ("raw_reply", "usage", "model_version", "http_status", "error_type"):
                if record.get(field) != last_result.get(field):
                    raise ValueError("Trace request differs from fsynced attempt result")
        elif numbered and record.get("status") != "interrupted_unknown_outcome":
            raise ValueError("Trace dispatched request lacks a matching result")
    for decision_id, record in decisions.items():
        deterministic = {key: val for key, val in record.items()
                         if key not in {"timestamp", "tcb_elapsed_seconds"}}
        if deterministic != generated_decisions[decision_id]:
            raise ValueError("Trace stored decision mismatch")
    pending = {request_id for request_id, _ in starts
               if (request_id, _) not in results and request_id not in requests}
    return requests, results, pending, len(starts), saved_manifest, decisions


def run_evaluation(cases, manifest, path, providers=None):
    validate_manifest(manifest)
    endpoints = {
        name: make_provider(name).endpoint_base()
        for name in dict.fromkeys(instance["provider"] for instance in manifest["instances"])
        if name != "mock"
    }
    if providers is None:
        for name in endpoints:
            make_provider(name).preflight()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = _records(path)
    completed, attempt_results, pending, calls, _, decision_records = _audit_trace(
        cases, records, manifest)
    if records and records[0].get("provider_endpoints", {}) != endpoints:
        raise ValueError("Provider endpoint identity changed since trace creation")
    manifest_hash = fixture_hash(manifest)
    if not records:
        _append(path, {"record_type": "manifest", "manifest": manifest,
                       "manifest_hash": manifest_hash,
                       "provider_endpoints": endpoints,
                       "scoring_fixture_hash": fixture_hash(build_manifest())})
    providers = providers or {name: make_provider(name)
                             for name in {i["provider"] for i in manifest["instances"]}}
    decisions = []
    decision_ids = set(decision_records)
    for case in cases:
        rounds = []
        fixture = fixture_hash(case.model_visible())
        previous = []
        for round_index in range(4):
            proposals, messages = [], []
            for instance in manifest["instances"]:
                agent = instance["agent_id"]
                prompt = build_prompt(case, agent, round_index, previous)
                request_id = fixture_hash([manifest_hash, case.case_id, fixture, agent, round_index, prompt])
                if request_id in completed:
                    record = completed[request_id]
                else:
                    record = {"record_type": "request", "run_id": manifest["run_id"],
                        "request_id": request_id, "case_id": case.case_id, "agent_id": agent,
                        "condition": "round_0_shared" if round_index == 0 else "debate",
                        "round": round_index, "prompt": prompt, "raw_reply": None,
                        "parsed_proposal": None, "collector_fixture_hash": fixture,
                        "usage": {}, "status": "budget_exhaustion", "attempts": 0,
                        "timestamp": _timestamp(), "elapsed_seconds": 0}
                    prior_results = [(number, item) for (rid, number), item in attempt_results.items()
                                     if rid == request_id]
                    prior_results.sort()
                    if request_id in pending:
                        record["status"] = "interrupted_unknown_outcome"
                        record["attempts"] = len(prior_results) + 1
                        record["elapsed_seconds"] = sum(item.get("elapsed_seconds", 0)
                                                        for _, item in prior_results)
                    else:
                        if prior_results:
                            _, result = prior_results[-1]
                            record.update(status=result["status"], raw_reply=result.get("raw_reply"),
                                          usage=result.get("usage", {}), attempts=len(prior_results),
                                          elapsed_seconds=sum(item.get("elapsed_seconds", 0)
                                                              for _, item in prior_results),
                                          model_version=result.get("model_version"),
                                          http_status=result.get("http_status"),
                                          error_type=result.get("error_type"))
                            if result["status"] == "ok":
                                record["parsed_proposal"] = parse_proposal(result["raw_reply"], case, agent)
                        if record["status"] not in {"ok", "malformed_output"}:
                            for attempt in range(len(prior_results), manifest["max_attempts"]):
                                reserve = manifest["max_cost_per_call_usd"]
                                if calls >= manifest["max_calls"] or (calls + 1) * reserve > manifest["spending_cap_usd"]:
                                    record["status"] = "budget_exhaustion"
                                    break
                                _append(path, {"record_type": "attempt_started", "request_id": request_id,
                                              "case_id": case.case_id, "agent_id": agent,
                                              "attempt": attempt + 1, "timestamp": _timestamp()})
                                calls += 1
                                started = time.monotonic()
                                record["attempts"] += 1
                                try:
                                    reply = providers[instance["provider"]].complete(instance, prompt, manifest)
                                    record.update(raw_reply=reply.text, usage=reply.usage, model_version=reply.model)
                                    record["parsed_proposal"] = parse_proposal(reply.text, case, agent)
                                    record["status"] = "ok"
                                except MalformedOutputError:
                                    record["status"] = "malformed_output"
                                except TimeoutError:
                                    record["status"] = "timeout"
                                except urllib.error.HTTPError as error:
                                    record["status"] = "rate_limit" if error.code == 429 else "api_error"
                                    record["http_status"] = error.code
                                except Exception as error:
                                    record["status"] = "api_error"
                                    record["error_type"] = type(error).__name__
                                elapsed = time.monotonic() - started
                                record["elapsed_seconds"] += elapsed
                                _append(path, {"record_type": "attempt_result", "request_id": request_id,
                                              "attempt": attempt + 1, "status": record["status"],
                                              "raw_reply": record["raw_reply"], "usage": record["usage"],
                                              "model_version": record.get("model_version"),
                                              "http_status": record.get("http_status"),
                                              "error_type": record.get("error_type"),
                                              "timestamp": _timestamp(), "elapsed_seconds": elapsed})
                                if record["status"] in {"ok", "malformed_output"}:
                                    break
                    _append(path, record)
                    completed[request_id] = record
                proposals.append(record["parsed_proposal"])
                messages.append({"agent_id": agent, "raw_reply": record["raw_reply"], "status": record["status"]})
            rounds.append(proposals)
            previous = messages
        for condition in CONDITIONS:
            started = time.monotonic()
            decision = _decision(case, rounds[3] if condition == "debate" else rounds[0],
                                 condition, manifest, manifest_hash, fixture)
            elapsed = time.monotonic() - started
            decisions.append(decision)
            if decision["decision_id"] not in decision_ids:
                _append(path, {**decision, "timestamp": _timestamp(),
                               "tcb_elapsed_seconds": elapsed})
    return decisions


def replay_decisions(cases, path):
    records = _records(path)
    requests, _, _, _, manifest, decisions = _audit_trace(cases, records)
    manifest_hash = fixture_hash(manifest)
    results = []
    for case in cases:
        fixture = fixture_hash(case.model_visible())
        previous = []
        rounds = []
        for round_index in range(4):
            proposals, messages = [], []
            for instance in manifest["instances"]:
                agent = instance["agent_id"]
                prompt = build_prompt(case, agent, round_index, previous)
                request_id = fixture_hash([manifest_hash, case.case_id, fixture, agent, round_index, prompt])
                record = requests.get(request_id)
                if record is None:
                    raise ValueError("Incomplete trace request sequence")
                proposals.append(record["parsed_proposal"])
                messages.append({"agent_id": agent, "raw_reply": record["raw_reply"],
                                 "status": record["status"]})
            rounds.append(proposals)
            previous = messages
        for condition in CONDITIONS:
            decision = _decision(case, rounds[3] if condition == "debate" else rounds[0],
                                 condition, manifest, manifest_hash, fixture)
            if decision["decision_id"] not in decisions:
                raise ValueError("Incomplete trace decisions")
            results.append(decision)
    return results
