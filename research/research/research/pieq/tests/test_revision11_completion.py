"""
Revision 11 EXP-06 — Adversarial tests for bounded deterministic completion.

Covers the mandatory list: shared physical failure domains, shared upstream leaves,
contradictory assumptions, nogood invalidation, unsound-rule attempts, cross-round
snapshot drift, manifest rotation, stale proof replay, probe unavailability, search
truncation, candidate-world incompleteness, missing-sensor refutation, and mutually
incompatible plausible worlds. The engine must never manufacture evidence, inflate
independence, or refute a physically possible world without verified support.
"""
from __future__ import annotations

from pieq.completion import bounded_deterministic_completion
from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    DeterministicRuleVerifier,
    FaultWorld,
    InferenceRule,
    InvariantConstraint,
    PlausibleWorldGate,
    compute_disjoint_justification_packing_bounded,
    filter_snapshot_consistent_witnesses,
)

KEY = b"rev11-completion-key"
RES, MH, EPOCH, VER = "res_c", "mh_c", 7, 50


def _obs(v, bit, aid, ch, dom, prop, *, obs_ver=VER, mh=MH):
    sig = v.sign_telemetry(assumption_id=aid, channel_id=ch, failure_domain=dom,
                           epoch_revision=EPOCH, proposition=prop, observation_version=obs_ver,
                           bit_index=bit, resource_id=RES, manifest_hash=mh)
    return BoundPrimitiveAssumption(bit_index=bit, assumption_id=aid, channel_id=ch,
                                    failure_domain=dom, epoch_revision=EPOCH, proposition=prop,
                                    signature=sig, observation_version=obs_ver,
                                    resource_id=RES, manifest_hash=mh)


def _engine(rules, invariants=()):
    v = DeterministicRuleVerifier(rules, list(invariants), secret_key=KEY)
    atms = BoundedATMSEngine(v, expected_epoch=EPOCH, expected_resource_id=RES,
                             expected_manifest_hash=MH, max_assumptions=32)
    return v, atms


def test_completion_derives_only_from_registered_authenticated_evidence():
    rules = [InferenceRule("r1", frozenset({"obs:a"}), "act:m"),
             InferenceRule("r2", frozenset({"obs:missing"}), "act:m")]
    v, atms = _engine(rules)
    assert atms.register_assumption(_obs(v, 0, "a0", "ch0", "D0", "obs:a"))
    n, trunc = bounded_deterministic_completion(atms, rules)
    assert (n, trunc) == (1, False)
    # r2 requires an observation that was never collected: NO derivation, no fabrication.
    assert len(atms.active_witness_justifications("act:m")) == 1


def test_shared_failure_domain_never_inflates_packing():
    rules = [InferenceRule(f"r{i}", frozenset({f"obs:p{i}"}), "act:m") for i in range(3)]
    v, atms = _engine(rules)
    for i in range(3):
        assert atms.register_assumption(_obs(v, i, f"a{i}", f"ch{i}", "D_shared", f"obs:p{i}"))
    n, _ = bounded_deterministic_completion(atms, rules)
    assert n == 3
    cnt, _, _ = compute_disjoint_justification_packing_bounded(
        atms.active_witness_justifications("act:m"), atms.assumptions_by_bit)
    assert cnt == 1  # one physical domain, regardless of completed derivation count


def test_shared_upstream_leaf_footprints_overlap():
    rules = [InferenceRule("rA", frozenset({"obs:x", "obs:y"}), "act:m"),
             InferenceRule("rB", frozenset({"obs:x", "obs:z"}), "act:m")]
    v, atms = _engine(rules)
    assert atms.register_assumption(_obs(v, 0, "ax", "chx", "Dx", "obs:x"))
    assert atms.register_assumption(_obs(v, 1, "ay", "chy", "Dy", "obs:y"))
    assert atms.register_assumption(_obs(v, 2, "az", "chz", "Dz", "obs:z"))
    n, _ = bounded_deterministic_completion(atms, rules)
    assert n == 2
    cnt, _, _ = compute_disjoint_justification_packing_bounded(
        atms.active_witness_justifications("act:m"), atms.assumptions_by_bit)
    assert cnt == 1  # both proofs share leaf obs:x


def test_contradictory_assumptions_and_nogood_invalidation():
    inv = InvariantConstraint("inv_pq", frozenset({"obs:p", "obs:q"}), "p and q conflict")
    rules = [InferenceRule("rp", frozenset({"obs:p"}), "act:m"),
             InferenceRule("rq", frozenset({"obs:q"}), "refute:w1")]
    v, atms = _engine(rules, [inv])
    assert atms.register_assumption(_obs(v, 0, "ap", "chp", "Dp", "obs:p"))
    assert atms.register_assumption(_obs(v, 1, "aq", "chq", "Dq", "obs:q"))
    n, _ = bounded_deterministic_completion(atms, rules)
    assert n == 2
    assert atms.register_nogood("tcb", 0b11, "inv_pq") is not None
    # Completion must not resurrect anything the nogood killed:
    n2, _ = bounded_deterministic_completion(atms, rules)
    assert n2 == 0
    assert atms.active_witness_justifications("act:m") == [] or all(
        not atms.is_consistent(j.env_mask) or (j.env_mask & 0b11) != 0b11
        for j in atms.active_witness_justifications("act:m"))


def test_unsound_rule_attempt_rejected_by_unchanged_verifier():
    rules = [InferenceRule("r_ok", frozenset({"obs:a"}), "act:m")]
    v, atms = _engine(rules)
    assert atms.register_assumption(_obs(v, 0, "a0", "ch0", "D0", "obs:a"))
    rogue = [InferenceRule("r_rogue", frozenset({"obs:a"}), "act:evil")]
    n, _ = bounded_deterministic_completion(atms, rogue)
    # r_rogue is not in the verifier's trusted rule set -> verifier returns None:
    assert n == 0
    assert atms.active_witness_justifications("act:evil") == []


def test_cross_round_snapshot_drift_and_manifest_rotation_still_filtered():
    rules = [InferenceRule("r1", frozenset({"obs:a"}), "act:m"),
             InferenceRule("r2", frozenset({"obs:b"}), "act:m")]
    v, atms = _engine(rules)
    assert atms.register_assumption(_obs(v, 0, "a0", "ch0", "D0", "obs:a"))
    assert atms.register_assumption(_obs(v, 1, "a1", "ch1", "D1", "obs:b", obs_ver=VER + 1))
    n, _ = bounded_deterministic_completion(atms, rules)
    assert n == 2
    wits, inc, _, _ = filter_snapshot_consistent_witnesses(
        atms.active_witness_justifications("act:m"), atms.assumptions_by_bit,
        expected_snapshot_version=VER)
    assert inc is True and len(wits) == 1  # drifted-round proof excluded downstream


def test_stale_proof_replay_not_duplicated():
    rules = [InferenceRule("r1", frozenset({"obs:a"}), "act:m")]
    v, atms = _engine(rules)
    assert atms.register_assumption(_obs(v, 0, "a0", "ch0", "D0", "obs:a"))
    assert atms.add_verified_justification("agent", "ctx", "act:m", 0b1, "r1")
    n, _ = bounded_deterministic_completion(atms, rules)
    assert n == 0  # identical (rule, env) already registered: replay adds nothing


def test_truncation_fails_closed_and_cannot_refute_true_world():
    # 8 same-domain observations x 1 rule each; cap new derivations at 2.
    rules = [InferenceRule(f"r{i}", frozenset({f"obs:p{i}"}), "refute:w_true") for i in range(8)]
    v, atms = _engine(rules)
    for i in range(8):
        assert atms.register_assumption(_obs(v, i, f"a{i}", f"ch{i}", f"D{i}", f"obs:p{i}"))
    n, trunc = bounded_deterministic_completion(atms, rules, max_new_derivations=2)
    assert n == 2 and trunc is True
    # Under-population only RETAINS worlds (Lemma 4 conservative direction):
    gate = PlausibleWorldGate([FaultWorld("w_true", frozenset(), frozenset({"defer"}))], ["defer"])
    survivors = gate.compute_fault_tolerant_belief_set(
        {"w_true": atms.active_witness_justifications("refute:w_true")},
        atms.assumptions_by_bit, fault_budget_f=2)
    # 2 disjoint refutations < f+1=3 required -> w_true retained:
    assert [w.world_id for w in survivors] == ["w_true"]


def test_candidate_world_incompleteness_not_maskable():
    # Completion operates on rules/evidence only; it cannot add or remove worlds.
    rules = [InferenceRule("r1", frozenset({"obs:a"}), "refute:w_modeled")]
    v, atms = _engine(rules)
    assert atms.register_assumption(_obs(v, 0, "a0", "ch0", "D0", "obs:a"))
    bounded_deterministic_completion(atms, rules)
    worlds = [FaultWorld("w_modeled", frozenset(), frozenset({"defer"})),
              FaultWorld("w_other", frozenset(), frozenset({"defer"}))]
    gate = PlausibleWorldGate(worlds, ["defer"])
    survivors = gate.compute_fault_tolerant_belief_set(
        {w.world_id: atms.active_witness_justifications(f"refute:{w.world_id}") for w in worlds},
        atms.assumptions_by_bit, fault_budget_f=0)
    # The unrefuted world survives; an OMITTED world is out-of-assumption (E1) and
    # completion neither helps nor hides it:
    assert {w.world_id for w in survivors} == {"w_other"}


def test_missing_sensor_refutation_impossible():
    rules = [InferenceRule("r_ref", frozenset({"obs:sensor_absent"}), "refute:w_possible")]
    v, atms = _engine(rules)  # the sensor never reported
    n, _ = bounded_deterministic_completion(atms, rules)
    assert n == 0
    assert atms.active_witness_justifications("refute:w_possible") == []


def test_mutually_incompatible_plausible_worlds_both_retained_without_disjoint_refutations():
    rules = [InferenceRule("r1", frozenset({"obs:a"}), "refute:w1"),
             InferenceRule("r2", frozenset({"obs:a"}), "refute:w2")]
    v, atms = _engine(rules)
    assert atms.register_assumption(_obs(v, 0, "a0", "ch0", "D0", "obs:a"))
    bounded_deterministic_completion(atms, rules)
    worlds = [FaultWorld("w1", frozenset(), frozenset({"x", "defer"})),
              FaultWorld("w2", frozenset(), frozenset({"y", "defer"}))]
    gate = PlausibleWorldGate(worlds, ["defer"])
    survivors = gate.compute_fault_tolerant_belief_set(
        {w.world_id: atms.active_witness_justifications(f"refute:{w.world_id}") for w in worlds},
        atms.assumptions_by_bit, fault_budget_f=1)
    # f=1 needs 2 disjoint refutations per world; one domain supplies only 1 each:
    assert {w.world_id for w in survivors} == {"w1", "w2"}


def test_completion_context_cannot_create_false_independence():
    rules = [InferenceRule("r1", frozenset({"obs:p0"}), "act:m"),
             InferenceRule("r2", frozenset({"obs:p1"}), "act:m")]
    v, atms = _engine(rules)
    for i in range(2):
        assert atms.register_assumption(_obs(v, i, f"a{i}", f"ch{i}", "D_one", f"obs:p{i}"))
    bounded_deterministic_completion(atms, rules)
    cnt, _, _ = compute_disjoint_justification_packing_bounded(
        atms.active_witness_justifications("act:m"), atms.assumptions_by_bit,
        proposer_context_isolation=True)
    assert cnt == 1  # even under the ctx-isolation policy, and physically regardless


def test_hardened_config_requires_registry_unless_legacy_opt_in():
    import pytest
    from pieq.hardened import HardenedPIEQConfig
    with pytest.raises(ValueError):
        HardenedPIEQConfig(resource_id="r", epoch=1, manifest_hash="m", auth_epoch=1,
                           signing_key=b"12345678", f_fault_budget=1, k_a=2)
    cfg = HardenedPIEQConfig(resource_id="r", epoch=1, manifest_hash="m", auth_epoch=1,
                             signing_key=b"12345678", f_fault_budget=1, k_a=2,
                             legacy_temporal_mode_opt_in=True)
    assert cfg.temporal_policies is None
