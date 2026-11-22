"""
PIEQ Revision 9 P1.1 — Proposer-Context Independence Resolution Tests.

The deterministic f = k_a - 1 fault-threshold theorem (Theorem 3) is sound over the
PHYSICAL EVIDENCE INDEPENDENCE footprint Phi_phys (failure domains, collectors,
assumption IDs), not over proposer/model reasoning identity or proof-construction
context IDs. These tests pin the five required Revision 9 properties:

  1. One agent using three independent telemetry domains can satisfy a
     three-domain evidence requirement.
  2. Five agents sharing one physical domain cannot.
  3. Splitting one agent's output into multiple context IDs does not increase
     physical evidence independence.
  4. Shared upstream evidence remains visible through multi-hop proof DAGs.
  5. An explicit proposer-context-isolation policy can impose additional
     restrictions without redefining physical fault tolerance.

Plus: a witness with no authenticated primitive leaf can never inflate nu_pack.
"""
from __future__ import annotations

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    DeterministicRuleVerifier,
    InferenceRule,
    SignedJustification,
    compute_disjoint_justification_packing_bounded,
    justification_dependency_footprint,
    physical_evidence_footprint,
)

EPOCH = 10
OBS_VER = 50
RESOURCE = "pg_cluster"
MANIFEST = "mh_rev9"


def _mk_bound(
    verifier: DeterministicRuleVerifier,
    bit: int,
    aid: str,
    ch: str,
    dom: str,
    prop: str,
) -> BoundPrimitiveAssumption:
    sig = verifier.sign_telemetry(
        assumption_id=aid,
        channel_id=ch,
        failure_domain=dom,
        epoch_revision=EPOCH,
        proposition=prop,
        observation_version=OBS_VER,
        bit_index=bit,
        resource_id=RESOURCE,
        manifest_hash=MANIFEST,
    )
    return BoundPrimitiveAssumption(
        bit_index=bit,
        assumption_id=aid,
        channel_id=ch,
        failure_domain=dom,
        epoch_revision=EPOCH,
        proposition=prop,
        signature=sig,
        observation_version=OBS_VER,
        resource_id=RESOURCE,
        manifest_hash=MANIFEST,
    )


def _engine_with_three_domains():
    """Three single-antecedent act:mitigate rules over three independent domains."""
    rules = [
        InferenceRule("r_d1", frozenset({"p_d1"}), "act:mitigate"),
        InferenceRule("r_d2", frozenset({"p_d2"}), "act:mitigate"),
        InferenceRule("r_d3", frozenset({"p_d3"}), "act:mitigate"),
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier, max_assumptions=16)
    assumps = [
        _mk_bound(verifier, 0, "a_d1", "ch_d1", "D1", "p_d1"),
        _mk_bound(verifier, 1, "a_d2", "ch_d2", "D2", "p_d2"),
        _mk_bound(verifier, 2, "a_d3", "ch_d3", "D3", "p_d3"),
    ]
    for a in assumps:
        assert atms.register_assumption(a)
    return verifier, atms


def test_one_agent_three_independent_domains_satisfies_three_domain_requirement() -> None:
    """Required property 1: a SINGLE agent under a SINGLE shared context_id reaches
    nu_pack = 3 when its three verified derivations rest on three genuinely
    independent physical telemetry domains."""
    _, atms = _engine_with_three_domains()
    shared_ctx = "agent_0:round_0"  # one agent, one shared proof-construction context
    for rule_id, bit in (("r_d1", 0), ("r_d2", 1), ("r_d3", 2)):
        cert = atms.add_verified_justification("agent_0", shared_ctx, "act:mitigate", 1 << bit, rule_id)
        assert cert is not None
    wits = atms.active_witness_justifications("act:mitigate")
    count, selected, exhausted = compute_disjoint_justification_packing_bounded(
        wits, atms.assumptions_by_bit
    )
    assert not exhausted
    assert count == 3  # satisfies k_a = 3 (f = 2) from one agent, one context
    # The physical footprints are pairwise disjoint even though ctx tokens coincide:
    fps = [physical_evidence_footprint(w, atms.assumptions_by_bit) for w in selected]
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            assert fps[i].isdisjoint(fps[j])


def test_five_agents_sharing_one_physical_domain_cannot_pack_above_one() -> None:
    """Required property 2: five distinct agents (five distinct context IDs) whose
    evidence all originates in ONE physical failure domain can never exceed
    nu_pack = 1. A single false assumption from one physical domain is not
    counted repeatedly through separate derivations."""
    rules = [
        InferenceRule(f"r_{i}", frozenset({f"p_{i}"}), "act:mitigate") for i in range(5)
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier, max_assumptions=16)
    for i in range(5):
        # Five distinct channels/assumption IDs, but ALL in failure domain D_shared.
        a = _mk_bound(verifier, i, f"a_{i}", f"ch_{i}", "D_shared", f"p_{i}")
        assert atms.register_assumption(a)
        cert = atms.add_verified_justification(
            f"agent_{i}", f"agent_{i}:round_0", "act:mitigate", 1 << i, f"r_{i}"
        )
        assert cert is not None
    wits = atms.active_witness_justifications("act:mitigate")
    count, _, exhausted = compute_disjoint_justification_packing_bounded(
        wits, atms.assumptions_by_bit
    )
    assert not exhausted
    assert count == 1


def test_context_id_splitting_does_not_increase_physical_independence() -> None:
    """Required property 3: re-labeling one agent's derivations with distinct
    context IDs leaves nu_pack unchanged when the physical evidence is shared."""
    rules = [
        InferenceRule("r_x", frozenset({"p_x"}), "act:mitigate"),
        InferenceRule("r_y", frozenset({"p_x", "p_y"}), "act:mitigate"),
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier, max_assumptions=16)
    ax = _mk_bound(verifier, 0, "a_x", "ch_x", "D_shared", "p_x")
    ay = _mk_bound(verifier, 1, "a_y", "ch_y", "D_shared", "p_y")
    assert atms.register_assumption(ax)
    assert atms.register_assumption(ay)
    # Same agent, three artificially distinct context IDs, overlapping physical evidence:
    assert atms.add_verified_justification("agent_0", "ctx_A", "act:mitigate", 0b01, "r_x")
    assert atms.add_verified_justification("agent_0", "ctx_B", "act:mitigate", 0b01, "r_x")
    assert atms.add_verified_justification("agent_0", "ctx_C", "act:mitigate", 0b11, "r_y")
    wits = atms.active_witness_justifications("act:mitigate")
    count, _, exhausted = compute_disjoint_justification_packing_bounded(
        wits, atms.assumptions_by_bit
    )
    assert not exhausted
    assert count == 1  # ctx splitting bought nothing: both leaves live in D_shared


def test_shared_upstream_evidence_visible_through_multihop_dags() -> None:
    """Required property 4: two multi-hop DAG proofs that share an upstream lemma
    (and therefore its primitive leaf) overlap in Phi_phys and pack to 1."""
    rules = [
        InferenceRule("r_lemma", frozenset({"p_shared"}), "lemma:unhealthy"),
        InferenceRule("r_top1", frozenset({"lemma:unhealthy", "p_1"}), "act:mitigate"),
        InferenceRule("r_top2", frozenset({"lemma:unhealthy", "p_2"}), "act:mitigate"),
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier, max_assumptions=16)
    a_shared = _mk_bound(verifier, 0, "a_shared", "ch_s", "D_shared", "p_shared")
    a_1 = _mk_bound(verifier, 1, "a_1", "ch_1", "D1", "p_1")
    a_2 = _mk_bound(verifier, 2, "a_2", "ch_2", "D2", "p_2")
    for a in (a_shared, a_1, a_2):
        assert atms.register_assumption(a)

    lemma = verifier.verify_justification(
        "agent_0", "ctx_lemma", "lemma:unhealthy", 0b001, "r_lemma", atms.assumptions_by_bit
    )
    assert lemma is not None
    top1 = atms.add_verified_derivation("agent_1", "ctx_1", "act:mitigate", 0b010, [lemma], "r_top1")
    top2 = atms.add_verified_derivation("agent_2", "ctx_2", "act:mitigate", 0b100, [lemma], "r_top2")
    assert top1 is not None and top2 is not None

    fp1 = physical_evidence_footprint(top1, atms.assumptions_by_bit)
    fp2 = physical_evidence_footprint(top2, atms.assumptions_by_bit)
    # Shared upstream leaf tokens visible in both physical footprints:
    assert {"fd:D_shared", "as:a_shared"}.issubset(fp1 & fp2)

    count, _, exhausted = compute_disjoint_justification_packing_bounded(
        [top1, top2], atms.assumptions_by_bit
    )
    assert not exhausted
    assert count == 1


def test_proposer_context_isolation_policy_restricts_but_never_relaxes() -> None:
    """Required property 5: the optional proposer-context-isolation policy can
    only REDUCE nu_pack (conservative policy restriction); it never raises it
    and never substitutes for physical-domain overlap detection."""
    _, atms = _engine_with_three_domains()
    shared_ctx = "agent_0:round_0"
    for rule_id, bit in (("r_d1", 0), ("r_d2", 1), ("r_d3", 2)):
        assert atms.add_verified_justification("agent_0", shared_ctx, "act:mitigate", 1 << bit, rule_id)
    wits = atms.active_witness_justifications("act:mitigate")

    base_count, _, _ = compute_disjoint_justification_packing_bounded(
        wits, atms.assumptions_by_bit
    )
    policy_count, _, _ = compute_disjoint_justification_packing_bounded(
        wits, atms.assumptions_by_bit, proposer_context_isolation=True
    )
    assert base_count == 3
    assert policy_count == 1  # policy excludes same-context proofs from counting separately
    assert policy_count <= base_count

    # Converse direction: distinct contexts over ONE shared physical domain stay at 1
    # under BOTH semantics — the policy never redefines physical fault tolerance.
    rules = [InferenceRule(f"r_{i}", frozenset({f"p_{i}"}), "act:mitigate") for i in range(3)]
    verifier2 = DeterministicRuleVerifier(rules, [])
    atms2 = BoundedATMSEngine(verifier2, max_assumptions=16)
    for i in range(3):
        a = _mk_bound(verifier2, i, f"a_{i}", f"ch_{i}", "D_shared", f"p_{i}")
        assert atms2.register_assumption(a)
        assert atms2.add_verified_justification(
            "agent_0", f"ctx_{i}", "act:mitigate", 1 << i, f"r_{i}"
        )
    wits2 = atms2.active_witness_justifications("act:mitigate")
    for policy in (False, True):
        cnt, _, _ = compute_disjoint_justification_packing_bounded(
            wits2, atms2.assumptions_by_bit, proposer_context_isolation=policy
        )
        assert cnt == 1


def test_empty_physical_footprint_witness_cannot_inflate_packing() -> None:
    """A hand-constructed witness carrying NO authenticated primitive leaf has an
    empty Phi_phys and must be excluded from packing entirely (it would otherwise
    be vacuously disjoint from everything)."""
    _, atms = _engine_with_three_domains()
    assert atms.add_verified_justification("agent_0", "ctx_0", "act:mitigate", 0b001, "r_d1")
    real = atms.active_witness_justifications("act:mitigate")
    ghost_a = SignedJustification("agent_9", "ctx_ghost_a", "act:mitigate", 0, "r_d1", "h_a")
    ghost_b = SignedJustification("agent_9", "ctx_ghost_b", "act:mitigate", 0, "r_d1", "h_b")
    assert physical_evidence_footprint(ghost_a, atms.assumptions_by_bit) == frozenset()
    # Historical full footprint still carries only a ctx token for the ghost:
    assert justification_dependency_footprint(ghost_a, atms.assumptions_by_bit) == frozenset(
        {"ctx:ctx_ghost_a"}
    )
    count, selected, exhausted = compute_disjoint_justification_packing_bounded(
        list(real) + [ghost_a, ghost_b], atms.assumptions_by_bit
    )
    assert not exhausted
    assert count == 1
    assert all(w.env_mask != 0 for w in selected)
