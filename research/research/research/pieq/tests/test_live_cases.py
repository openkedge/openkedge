import importlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from pieq.core import (
    BoundedATMSEngine, DeterministicRuleVerifier, InferenceRule,
    PlausibleWorldGate, PrimitiveAssumption,
)
from pieq.benchmark import build_benchmark_scenarios


def live_module():
    assert importlib.util.find_spec("pieq.live_cases") is not None, "live fixture module is missing"
    return importlib.import_module("pieq.live_cases")


def test_fixture_audit_and_frozen_manifest():
    live = live_module()
    cases = live.build_live_cases()
    audit = live.audit_live_cases(cases)
    assert audit["case_count"] == 40
    assert audit["family_counts"] == {
        "helly": 8, "conjunctive_overlap": 6, "false_refutation": 6,
        "shared_domain": 10, "positive_synthesis": 6, "comparison": 7,
    }
    manifest = json.loads((Path(__file__).parents[1] / "results/live_cases_manifest.json").read_text())
    assert manifest == live.build_manifest(cases)
    assert audit["errors"] == []
    assert len(set(case.template_id for case in cases)) == 18
    assert set(manifest["template_membership"]) == {str(case.case_id) for case in cases}
    assert len(set(manifest["template_membership"].values())) == 18


def test_manifest_binds_private_worlds_and_each_agent_payload():
    live = live_module()
    cases = live.build_live_cases()
    manifest = live.build_manifest(cases)
    case = cases[0]
    changed_world = replace(case.worlds[0], transition_map={"probe": "tampered"})
    changed_case = replace(case, worlds=(changed_world, *case.worlds[1:]))
    changed_cases = [changed_case, *cases[1:]]
    assert manifest["private_scoring_fixture_hash"] != live.build_manifest(changed_cases)[
        "private_scoring_fixture_hash"
    ]
    assert manifest["model_visible_agent_hashes"]["0"]["agent_0"] == live.fixture_hash(
        case.model_visible("agent_0")
    )
    assert set(manifest["model_visible_agent_hashes"]["0"]) == set(case.agent_allocation)


def test_private_truth_and_allocations_are_absent_from_model_payload():
    live = live_module()
    for case in live.build_live_cases():
        public = case.model_visible()
        assert not ({"true_world", "correct_mutation", "answer_key", "faulty_domains", "agent_allocation"} & public.keys())
        assert len(case.agent_allocation) == 5
        assert all(case.agent_allocation.values())
        assert public["f"] == public["k_a"] - 1
        assert {"defer", "probe"} <= set(public["candidate_actions"])
        assert all("collected_at" in obs and "observation_version" in obs for obs in public["observations"])
        assert "S_cand" not in public
        assert {world["world_id"] for world in public["candidate_worlds"]} == {
            world.world_id for world in case.worlds
        }
        assert "safe_mutations" not in json.dumps(public)
        assert "transition_map" not in json.dumps(public)
        assert "case_id" not in public
        assert f"Incident {case.case_id}:" not in public["incident_brief"]
        assert f"case_{case.case_id}_" not in json.dumps(public)
        assert f"collector_{case.case_id}_" not in json.dumps(public)
        assert public["snapshot_version"] == 1000
        assert public["epoch_revision"] == 2000


def test_oracle_rejects_traps_and_requires_complete_synthesis():
    live = live_module()
    cases = {case.case_id: case for case in live.build_live_cases()}
    assert cases[10].answer_key()["packing"]["act:reroute_partition_link"] == 1
    assert cases[2].answer_key()["packing"]["act:reroute_partition_link"] == 1
    assert cases[7].answer_key()["packing"]["refute:s_asymmetric_partition"] == 1
    assert cases[0].answer_key()["safe_actions"] == ["promote_replica"]
    assert cases[3].answer_key()["safe_actions"] == []
    assert cases[89].answer_key()["stratum"] == "stress"
    for case_id in (3, 11, 19, 27, 37, 45, 53, 75):
        sets = [set(world.safe_mutations) - {"defer", "probe"} for world in cases[case_id].worlds]
        assert all(sets[left] & sets[right] for left, right in ((0, 1), (0, 2), (1, 2)))
        assert not set.intersection(*sets)
        trap = build_benchmark_scenarios()[case_id].unsafe_trap_mutation
        true = next(world for world in cases[case_id].worlds
                    if world.world_id == cases[case_id].true_world)
        assert trap not in true.safe_mutations


def test_private_allocations_allow_complete_positive_and_overlap_proposals():
    live = live_module()
    for case in live.build_live_cases():
        if not ({"positive_synthesis", "conjunctive_overlap"} & set(case.families)):
            continue
        visible_by_agent = {
            agent: {item["proposition"] for item in case.model_visible(agent)["observations"]}
            for agent in case.agent_allocation
        }
        if "positive_synthesis" in case.families:
            positive_rules = [rule for rule in case.rules
                              if rule.consequent == f"act:{case.intended_action}"]
            assert len(positive_rules) >= case.k_a
            assert all(any(rule.antecedents <= visible for visible in visible_by_agent.values())
                       for rule in positive_rules)
        if "conjunctive_overlap" in case.families:
            trap_rules = [rule for rule in case.rules
                          if rule.consequent.startswith("act:") and len(rule.antecedents) == 2]
            assert len(trap_rules) == 3
            assert all(any(rule.antecedents <= visible for visible in visible_by_agent.values())
                       for rule in trap_rules)


def test_single_false_refutation_reaches_shield_but_not_f_fault_budget():
    live = live_module()
    for case in live.build_live_cases():
        if "false_refutation" not in case.families:
            continue
        key = case.answer_key()
        trap = next(action for action in {world_action for world in case.worlds
                                          for world_action in world.safe_mutations}
                    if action not in {"defer", "probe", case.intended_action}
                    and key["packing"].get(f"act:{action}", 0) >= case.k_a)
        assert key["packing"][f"act:{trap}"] >= case.k_a
        assert key["packing"][f"refute:{case.true_world}"] == 1
        assert case.true_world in key["surviving_worlds"]
        assert trap not in key["safe_actions"]
        gate = PlausibleWorldGate(case.worlds, ["probe"])
        atms = case.registered_atms()
        for index, rule in enumerate(case.rules):
            mask = sum(obs.bitmask for obs in case.observations
                       if obs.proposition in rule.antecedents)
            assert atms.add_verified_justification(
                f"agent_{index % 5}", f"ctx_{index}", rule.consequent, mask, rule.rule_id
            )
        refs = {world.world_id: atms.justifications.get(f"refute:{world.world_id}", [])
                for world in case.worlds}
        survivors_f0 = gate.compute_fault_tolerant_belief_set(
            refs, atms.assumptions_by_bit, fault_budget_f=0
        )
        assert case.true_world not in {world.world_id for world in survivors_f0}
        assert trap in gate.global_safe_mutations(survivors_f0)


def test_nonambiguous_source_cases_can_admit_correct_action_despite_traps():
    live = live_module()
    source = build_benchmark_scenarios()
    for case in live.build_live_cases():
        if source[case.case_id].passively_ambiguous:
            continue
        key = case.answer_key()
        assert case.intended_action in key["safe_actions"], case.case_id
        assert key["packing"].get(f"act:{case.intended_action}", 0) >= case.k_a
        if "shared_domain" in case.families or "conjunctive_overlap" in case.families:
            assert key["packing"].get(
                f"act:{source[case.case_id].unsafe_trap_mutation}", 0
            ) < case.k_a


def test_public_rule_table_does_not_identify_true_world_by_missing_consequent():
    live = live_module()
    for case in live.build_live_cases():
        if build_benchmark_scenarios()[case.case_id].passively_ambiguous:
            continue
        refuted = {rule.consequent for rule in case.rules if rule.consequent.startswith("refute:")}
        assert refuted == {f"refute:{world.world_id}" for world in case.worlds}
        assert case.answer_key()["packing"][f"refute:{case.true_world}"] < case.k_a


def test_targeted_comparison_cases_exercise_verified_nogoods():
    live = live_module()
    cases = {case.case_id: case for case in live.build_live_cases()}
    for case_id in (1, 39, 71):
        case = cases[case_id]
        atms = case.registered_atms()
        conflicting = [inv for inv in case.invariants if inv.invariant_id == "observed_conflict"]
        assert len(conflicting) == 1
        inv = conflicting[0]
        mask = sum(obs.bitmask for obs in case.observations
                   if obs.proposition in inv.conflict_predicates)
        assert mask.bit_count() == 2
        proof = next(rule for rule in case.rules if rule.antecedents == inv.conflict_predicates)
        assert atms.add_verified_justification("agent_0", "ctx", proof.consequent, mask,
                                               proof.rule_id)
        assert atms.register_nogood("collector", mask, inv.invariant_id)
        assert not atms.active_witness_justifications(proof.consequent)
        assert case.answer_key()["packing"][proof.consequent] == 0


@pytest.mark.parametrize("mask", [-1, 1 << 64, True, 1.5, "1", None])
def test_untrusted_masks_are_rejected_before_verification(mask):
    verifier = DeterministicRuleVerifier([InferenceRule("r", frozenset({"p"}), "q")], [])
    atms = BoundedATMSEngine(verifier)
    assert verifier.verify_justification("a", "c", "q", mask, "r", {}) is None
    assert verifier.verify_nogood("a", mask, "i", {}) is None
    assert atms.add_verified_justification("a", "c", "q", mask, "r") is None


def test_signature_binds_snapshot_version_and_all_signed_fields():
    verifier = DeterministicRuleVerifier([], [])
    signature = verifier.sign_telemetry("e", "ch", "fd", 10, "p",
                                        observation_version=4, bit_index=0)
    obs = PrimitiveAssumption(0, "e", "ch", "fd", 10, "p", signature, 4)
    atms = BoundedATMSEngine(verifier)
    assert atms.register_assumption(obs)
    for field, value in {
        "bit_index": 5,
        "assumption_id": "other", "channel_id": "other", "failure_domain": "other",
        "epoch_revision": 11, "proposition": "other", "observation_version": 5,
        "signature": "",
    }.items():
        assert not atms.register_assumption(replace(obs, **{field: value}))
    assert not atms.register_assumption(replace(obs, signature="0" * 16))


def test_rule_accepts_exact_antecedents_and_rejects_partial_or_unsigned():
    live = live_module()
    case = live.build_live_cases()[0]
    atms = case.registered_atms()
    rule = next(rule for rule in case.rules if len(rule.antecedents) == 2)
    mask = sum(obs.bitmask for obs in case.observations if obs.proposition in rule.antecedents)
    assert atms.add_verified_justification("a", "c", rule.consequent, mask, rule.rule_id)
    assert atms.add_verified_justification("a", "c", rule.consequent, mask & (mask - 1), rule.rule_id) is None
    bit = (mask & -mask).bit_length() - 1
    atms.assumptions_by_bit[bit] = replace(atms.assumptions_by_bit[bit], signature="")
    assert atms.add_verified_justification("a", "c", rule.consequent, mask, rule.rule_id) is None
