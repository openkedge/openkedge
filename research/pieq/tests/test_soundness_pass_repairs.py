"""
Adversarial Regression Suite for PIEQ Revision 4 Formal Soundness Pass (P0-P2):
Covers every newly identified formal soundness gap and counterexample:
  1. P0-A (Mode S): Unsafe intermediate predecessor state followed by a safe recovery action.
  2. P0-A (Mode S): Unordered predecessors where reverse interleaving exits viability.
  3. P0-B (Mode C): Absent/canceled/fenced_no_effect candidate action exposing unsafe remaining schedule,
                    plus candidate partial-effect failure.
  4. P0-C (Snapshot Replay): Noncommutative unversioned assignment followed by versioned increment
                    triggering deterministic fail-closed reconciliation barrier.
  5. P1   (Multi-Step DAGs): Shared transitive provenance hidden behind distinct derived propositions.
  6. P0-D (Constraint Closure): Invariant affected by operations outside purported closure vs.
                    restored frame condition under complete ConstraintDependencyGraph coupling.
  7. P2   (Evidence Binding): Cryptographic binding of primitive assumptions to epoch, resource,
                    and topology manifest digest.
  8. P0   (Fail-Closed Transitions): Unmodeled commit or predecessor transitions fail closed.
"""

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    ConstraintDependencyGraph,
    DeterministicRuleVerifier,
    FaultWorld,
    InFlightMutation,
    InferenceRule,
    InvariantConstraint,
    ModeCCompatibilityVerifier,
    PlausibleWorldGate,
    PrimitiveAssumption,
    SnapshotContext,
    compute_disjoint_justification_packing_bounded,
    justification_dependency_footprint,
)


def test_p0a_mode_s_unsafe_intermediate_predecessor_followed_by_safe_recovery_rejected():
    """
    Required Test #1 (P0-A):
    Predecessor u1 transitions s0 (viable) -> s_bad (viable=False).
    Candidate a_recover is declared safe in both s0 and s_bad and recovers s_bad -> s_rec (viable=True).
    Even though candidate a_recover itself transitions to a viable terminal state,
    the complete schedule (u1, a_recover) visits non-viable intermediate state s_bad.
    Repaired Mode S MUST reject admission (`UNSAFE_PREDECESSOR_PREFIX_STATE`).
    """
    s0 = FaultWorld(
        "s0", frozenset(), frozenset({"u1", "a_recover"}),
        transition_map={"u1": "s_bad", "a_recover": "s_rec"}, viable=True,
    )
    s_bad = FaultWorld(
        "s_bad", frozenset(), frozenset({"a_recover"}),
        transition_map={"a_recover": "s_rec"}, viable=False,
    )
    s_rec = FaultWorld("s_rec", frozenset(), frozenset(), viable=True)

    gate = PlausibleWorldGate([s0, s_bad, s_rec], safe_probes=[], exposure_caps={"g": 3})
    u1 = InFlightMutation("op_u1", "u1", "g", exposure_cost=1, serialized_before_new=True)

    ok, reason, cert = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a_recover", "g", 1, [s0], [u1]
    )
    assert ok is False
    assert cert is None
    assert reason == "UNSAFE_PREDECESSOR_PREFIX_STATE"


def test_p0a_mode_s_unordered_predecessors_reverse_interleaving_caught():
    """
    P0-A (Unordered Mode S Predecessors):
    Predecessors u1, u2 are both serialized before candidate a, with NO order between u1 and u2:
      - u1 -> u2: s0 -> s1 (viable) -> s12 (viable)
      - u2 -> u1: s0 -> s2 (viable) -> s_bad (viable=False!)
    A single-pass list-order traversal `[u1, u2]` misses `u2 -> u1`.
    Repaired Mode S checks all linear extensions of U_S and rejects.
    Once `u1 <_ord u2` is enforced via `predecessor_op_ids=("op_u1",)`, all linear
    extensions are viable and Mode S admits safely.
    """
    s0 = FaultWorld("s0", frozenset(), frozenset({"u1", "u2", "a"}), transition_map={"u1": "s1", "u2": "s2"})
    s1 = FaultWorld("s1", frozenset(), frozenset({"u2", "a"}), transition_map={"u2": "s12", "a": "s1"})
    s2 = FaultWorld("s2", frozenset(), frozenset({"u1", "a"}), transition_map={"u1": "s_bad", "a": "s2"})
    s12 = FaultWorld("s12", frozenset(), frozenset({"a"}), transition_map={"a": "s12"})
    s_bad = FaultWorld("s_bad", frozenset(), frozenset(), viable=False)

    gate = PlausibleWorldGate([s0, s1, s2, s12, s_bad], safe_probes=[], exposure_caps={"g": 5})

    u1 = InFlightMutation("op_u1", "u1", "g", serialized_before_new=True)
    u2_unordered = InFlightMutation("op_u2", "u2", "g", serialized_before_new=True)
    ok_unord, reason_unord, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s0], [u1, u2_unordered]
    )
    assert ok_unord is False
    assert reason_unord == "UNSAFE_PREDECESSOR_PREFIX_STATE"

    # Adding target-enforced predecessor edge u1 <_ord u2 excludes u2 -> u1:
    u2_ordered = InFlightMutation(
        "op_u2", "u2", "g", serialized_before_new=True, predecessor_op_ids=("op_u1",)
    )
    ok_ord, reason_ord, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s0], [u1, u2_ordered]
    )
    assert ok_ord is True
    assert reason_ord == "ADMIT_COMPOUND_SAFE"


def test_p0b_mode_c_absent_candidate_action_unsafe_schedule_rejected():
    """
    Required Test #2 (P0-B):
    Mode C execution where candidate action `a` is absent (canceled / timed out / fenced_no_effect):
      - `u2` declares `op_a` in `predecessor_op_ids` (`a <_ord u2`).
      - Whenever `a` executes, `a(s0) = s_guarded`, and in `s_guarded` BOTH `u1` and `u2`
        stay safely viable in every order (`a -> u1 -> u2`, `a -> u2 -> u1`, `u1 -> a -> u2`).
        Thus EVERY subset O' CONTAINING `a` is safe!
      - HOWEVER, if `a` settles as `fenced_no_effect` (or is canceled before dispatch),
        `u2`'s predecessor frontier is satisfied without `a`'s effect, permitting schedule
        `(u1, u2)` from `s0`: `u1(s0) = s1`, `u2(s1) = s_bad` (viable=False)!
    Repaired Mode C enumerates ALL subsets O' <= U_C union {a} INCLUDING subsets excluding `a`,
    catching the hazard (`UNSAFE_ABSENT_CANDIDATE_SCHEDULE`).
    Also verifies candidate partial-effect checking (`UNSAFE_PARTIAL_EFFECT_STATE`).
    """
    s0 = FaultWorld(
        "s0", frozenset(), frozenset({"a", "u1", "u2"}),
        transition_map={"a": "s_guarded", "u1": "s1", "u2": "s2"},
    )
    s_guarded = FaultWorld(
        "s_guarded", frozenset(), frozenset({"u1", "u2"}),
        transition_map={"u1": "s_guarded", "u2": "s_guarded"},
    )
    s1 = FaultWorld(
        "s1", frozenset(), frozenset({"a", "u2"}),
        transition_map={"a": "s_guarded", "u2": "s_bad"},
    )
    s2 = FaultWorld(
        "s2", frozenset(), frozenset({"a", "u1"}),
        transition_map={"a": "s_guarded", "u1": "s_guarded"},
    )
    s_bad = FaultWorld("s_bad", frozenset(), frozenset(), viable=False)

    gate = PlausibleWorldGate([s0, s_guarded, s1, s2, s_bad], safe_probes=[], exposure_caps={"g": 4})
    u1 = InFlightMutation("op_u1", "u1", "g", serialized_before_new=False)
    u2 = InFlightMutation("op_u2", "u2", "g", serialized_before_new=False, predecessor_op_ids=("op_a",))

    ok, reason, cert = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s0], [u1, u2], candidate_op_id="op_a"
    )
    assert ok is False
    assert cert is None
    assert reason == "UNSAFE_ABSENT_CANDIDATE_SCHEDULE"

    # Also verify unsafe candidate partial effect is caught in Mode C:
    s_safe = FaultWorld("s_safe", frozenset(), frozenset({"a", "u"}), transition_map={"a": "s_safe", "u": "s_safe"})
    s_part_bad = FaultWorld("s_part_bad", frozenset(), frozenset(), viable=False)
    gate_part = PlausibleWorldGate([s_safe, s_part_bad], safe_probes=[], exposure_caps={"g": 2})
    u_safe = InFlightMutation("op_u", "u", "g", serialized_before_new=False)
    ok_p, reason_p, cert_p = gate_part.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s_safe], [u_safe], candidate_partial_effect_worlds=("s_part_bad",)
    )
    assert ok_p is False and cert_p is None and reason_p == "UNSAFE_PARTIAL_EFFECT_STATE"


def test_p0c_noncommutative_unversioned_assign_and_versioned_increment_fails_closed():
    """
    Required Test #3 (P0-C):
    Noncommutative unversioned assignment followed by versioned increment:
      - Snapshot at v_snap = 0 observes x = 0 (`s_0`).
      - Unversioned post-restart commit `c_assign` (commit_version=None): x := 10.
      - Versioned commit `c_inc` (commit_version=1 > v_snap=0): x := x + 1.
      - True execution order `c_assign -> c_inc` yields x = 11 (`s_11`), where `a` is UNSAFE!
      - Old Eq. (15) applied versioned `c_inc` first (0 -> 1) then optional `c_assign` (1 -> {1, 10}),
        producing {s_1, s_10} and COMPLETELY MISSING true state s_11 (unsafely admitting `a`).
    Repaired semantics enforce a deterministic fail-closed reconciliation barrier whenever
    any relevant committed op lacks a commit_version (`BLOCKED_UNVERSIONED_COMMIT_RECONCILIATION_REQUIRED`).
    Once WAL reconciliation assigns versions (c_assign @ v=1, c_inc @ v=2), snapshot advancement
    reaches [s_11] deterministically and blocks unsafe `a`.
    """
    s_0 = FaultWorld("s_0", frozenset(), frozenset({"a"}), transition_map={"assign_10": "s_10", "inc_1": "s_1"})
    s_1 = FaultWorld("s_1", frozenset(), frozenset({"a"}), transition_map={"assign_10": "s_10", "a": "s_1"})
    s_10 = FaultWorld("s_10", frozenset(), frozenset({"a"}), transition_map={"inc_1": "s_11", "a": "s_10"})
    s_11 = FaultWorld("s_11", frozenset(), frozenset(), viable=True)  # `a` NOT safe at x=11!

    gate = PlausibleWorldGate([s_0, s_1, s_10, s_11], safe_probes=[], exposure_caps={"g": 5})

    c_assign_unver = InFlightMutation(
        "op_assign", "assign_10", "g", lifecycle_state="COMMITTED",
        serialized_before_new=True, commit_version=None,
    )
    c_inc_v1 = InFlightMutation(
        "op_inc", "inc_1", "g", lifecycle_state="COMMITTED",
        serialized_before_new=True, commit_version=1,
    )

    # 1. Fail-closed barrier blocks admission when unversioned commit order is unknown:
    ok_blocked, reason_blocked, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s_0], [c_assign_unver, c_inc_v1],
        snapshot_context=SnapshotContext(snapshot_version=0, require_versioned_commits=True),
    )
    assert ok_blocked is False
    assert reason_blocked == "BLOCKED_UNVERSIONED_COMMIT_RECONCILIATION_REQUIRED"

    # 2. After WAL reconciliation restores durable commit versions (assign @ v=1, inc @ v=2):
    c_assign_v1 = InFlightMutation(
        "op_assign", "assign_10", "g", lifecycle_state="COMMITTED",
        serialized_before_new=True, commit_version=1,
    )
    c_inc_v2 = InFlightMutation(
        "op_inc", "inc_1", "g", lifecycle_state="COMMITTED",
        serialized_before_new=True, commit_version=2,
    )
    ok_adv, base_worlds, _ = gate.advance_snapshot_states(
        [s_0], [c_inc_v2, c_assign_v1],
        snapshot_context=SnapshotContext(snapshot_version=0, require_versioned_commits=True),
    )
    assert ok_adv is True
    assert [w.world_id for w in base_worlds] == ["s_11"]

    ok_post, reason_post, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s_0], [c_inc_v2, c_assign_v1],
        snapshot_context=SnapshotContext(snapshot_version=0, require_versioned_commits=True),
    )
    assert ok_post is False
    assert reason_post == "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE"


def test_p1_multistep_derivation_dag_shared_upstream_premise_not_independent():
    """
    Required Test #4 (P1):
    Multi-step Horn derivation DAG where two distinct derived propositions `derived:d1` and
    `derived:d2` share an upstream primitive telemetry premise `e1` (failure domain `fd_1`):
      - Step 1: {obs:e1, obs:e2} |- derived:d1  (agent A1, ctx_A1)
      - Step 1: {obs:e1, obs:e3} |- derived:d2  (agent A2, ctx_A2)
      - Step 2: {derived:d1}     |- act:promote (agent A3, ctx_A3) -> Pi_1
      - Step 2: {derived:d2}     |- act:promote (agent A4, ctx_A4) -> Pi_2
    Naive 1-hop footprints over immediate antecedents `{derived:d1}` vs `{derived:d2}` look disjoint,
    yet recursive derivation-DAG footprints Phi(Pi_1) and Phi(Pi_2) BOTH include `{fd:fd_1, ch:ch_1, as:e1}`.
    Set packing MUST return nu_pack = 1 (never 2). Adding a genuinely disjoint derivation Pi_3
    rooted in `e4` (`fd_4`) raises nu_pack to 2, and a nogood on `e1` purges both Pi_1 and Pi_2!
    """
    rules = [
        InferenceRule("r_d1", frozenset({"obs:e1", "obs:e2"}), "derived:d1"),
        InferenceRule("r_d2", frozenset({"obs:e1", "obs:e3"}), "derived:d2"),
        InferenceRule("r_d3", frozenset({"obs:e4"}), "derived:d3"),
        InferenceRule("r_act1", frozenset({"derived:d1"}), "act:promote"),
        InferenceRule("r_act2", frozenset({"derived:d2"}), "act:promote"),
        InferenceRule("r_act3", frozenset({"derived:d3"}), "act:promote"),
    ]
    invariants = [
        InvariantConstraint("inv_e1_invalid", frozenset({"obs:e1"}))
    ]
    verifier = DeterministicRuleVerifier(rules, invariants)
    atms = BoundedATMSEngine(verifier)

    def make_a(bit: int, aid: str, ch: str, fd: str, prop: str) -> PrimitiveAssumption:
        sig = verifier.sign_telemetry(aid, ch, fd, 10, prop, observation_version=1, bit_index=bit)
        a = PrimitiveAssumption(bit, aid, ch, fd, 10, prop, sig, observation_version=1)
        assert atms.register_assumption(a) is True
        return a

    e1 = make_a(0, "e1", "ch_1", "fd_1", "obs:e1")
    e2 = make_a(1, "e2", "ch_2", "fd_2", "obs:e2")
    e3 = make_a(2, "e3", "ch_3", "fd_3", "obs:e3")
    e4 = make_a(3, "e4", "ch_4", "fd_4", "obs:e4")

    # Step 1 derivations
    j_d1 = atms.add_verified_justification("A1", "ctx_A1", "derived:d1", e1.bitmask | e2.bitmask, "r_d1")
    j_d2 = atms.add_verified_justification("A2", "ctx_A2", "derived:d2", e1.bitmask | e3.bitmask, "r_d2")
    j_d3 = atms.add_verified_justification("A5", "ctx_A5", "derived:d3", e4.bitmask, "r_d3")
    assert j_d1 is not None and j_d2 is not None and j_d3 is not None

    # Step 2 multi-step derivations rooted at act:promote
    pi_1 = atms.add_verified_derivation("A3", "ctx_A3", "act:promote", 0, [j_d1], "r_act1")
    pi_2 = atms.add_verified_derivation("A4", "ctx_A4", "act:promote", 0, [j_d2], "r_act2")
    assert pi_1 is not None and pi_2 is not None
    assert pi_1.env_mask == (e1.bitmask | e2.bitmask)
    assert pi_2.env_mask == (e1.bitmask | e3.bitmask)

    # Recursive footprints expose shared upstream premise e1 AND ancestor contexts:
    fp1 = justification_dependency_footprint(pi_1, atms.assumptions_by_bit)
    fp2 = justification_dependency_footprint(pi_2, atms.assumptions_by_bit)
    assert "fd:fd_1" in fp1 and "fd:fd_1" in fp2
    assert "as:e1" in fp1 and "as:e1" in fp2
    assert "ctx:ctx_A1" in fp1 and "ctx:ctx_A3" in fp1
    assert not fp1.isdisjoint(fp2)

    # Set packing MUST NOT count Pi_1 and Pi_2 as 2 provenance-independent proofs:
    nu_shared, _, trunc = compute_disjoint_justification_packing_bounded(
        [pi_1, pi_2], atms.assumptions_by_bit
    )
    assert trunc is False
    assert nu_shared == 1

    # Adding a truly disjoint multi-step proof Pi_3 from e4 (fd_4) raises packing to 2:
    pi_3 = atms.add_verified_derivation("A6", "ctx_A6", "act:promote", 0, [j_d3], "r_act3")
    assert pi_3 is not None
    nu_with_3, _, _ = compute_disjoint_justification_packing_bounded(
        [pi_1, pi_2, pi_3], atms.assumptions_by_bit
    )
    assert nu_with_3 == 2

    # Registering a nogood on upstream leaf e1 automatically invalidates Pi_1 and Pi_2 (retaining Pi_3):
    assert atms.register_nogood("verifier", e1.bitmask, "inv_e1_invalid") is not None
    active = atms.active_witness_justifications("act:promote")
    assert len(active) == 1 and active[0] == pi_3


def test_p0d_invariant_affected_outside_purported_closure_and_frame_repair():
    """
    Required Test #5 (P0-D):
    Global invariant K_global forbids draining BOTH group_A and group_B simultaneously
    (total drains across A + B <= 1).
    1. When `ConstraintDependencyGraph` OMITS the coupling between `group_A` and `group_B`
       (violating the Theorem 5 closed-domain frame condition!), `op_A` on `group_A` lies
       outside `Dom*(drain_B) = {group_B}`. The closed-domain gate checks only `group_B`
       and admits `drain_B` — proving why Theorem 5 MUST be scoped to closed-domain
       invariants satisfying the frame condition rather than arbitrary global invariants!
    2. Once `add_global_invariant("global_quorum_AB", ["group_A", "group_B"])` closes
       the coupling in G_inv, `Dom*(drain_B) = {global_quorum_AB, group_B}` captures `op_A`
       and blocks `drain_B` cleanly.
    """
    s0 = FaultWorld(
        "s0", frozenset(), frozenset({"drain_A", "drain_B"}),
        transition_map={"drain_A": "s_A_drained", "drain_B": "s_B_drained"},
    )
    s_A = FaultWorld("s_A_drained", frozenset(), frozenset(), transition_map={"drain_B": "s_both_bad"})
    s_B = FaultWorld("s_B_drained", frozenset(), frozenset(), transition_map={"drain_A": "s_both_bad"})
    s_bad = FaultWorld("s_both_bad", frozenset(), frozenset(), viable=False)

    # 1. Incomplete closure (no edge linking group_A and group_B -> frame condition violated):
    incomplete_dep = ConstraintDependencyGraph()
    gate_unclosed = PlausibleWorldGate(
        [s0, s_A, s_B, s_bad],
        safe_probes=[],
        exposure_caps={"group_A": 1, "group_B": 1, "global_quorum_AB": 1},
        dependency_graph=incomplete_dep,
    )
    op_A_unclosed = InFlightMutation(
        "op_A", "drain_A", "group_A", exposure_cost=1,
        serialized_before_new=False, affected_domains=("group_A",),
    )
    ok_unclosed, reason_unclosed, _ = gate_unclosed.evaluate_concurrent_compound_safety_with_certificate(
        "drain_B", "group_B", 1, [s0], [op_A_unclosed]
    )
    # Without the coupling edge, op_A is outside Dom*(drain_B)={group_B} and NOT seen:
    assert ok_unclosed is True and reason_unclosed == "ADMIT_COMPOUND_SAFE"

    # 2. Repaired closure registering global_quorum_AB coupling (frame condition holds for K_global):
    complete_dep = ConstraintDependencyGraph()
    complete_dep.add_global_invariant("global_quorum_AB", ["group_A", "group_B"])
    gate_closed = PlausibleWorldGate(
        [s0, s_A, s_B, s_bad],
        safe_probes=[],
        exposure_caps={"group_A": 1, "group_B": 1, "global_quorum_AB": 1},
        dependency_graph=complete_dep,
    )
    op_A_closed = InFlightMutation(
        "op_A", "drain_A", "group_A", exposure_cost=1,
        serialized_before_new=False,
        affected_domains=complete_dep.domain_closure(["group_A"]),
        domain_costs=complete_dep.closure_costs({"group_A": 1}),
    )
    ok_closed, reason_closed, _ = gate_closed.evaluate_concurrent_compound_safety_with_certificate(
        "drain_B", "group_B", 1, [s0], [op_A_closed]
    )
    assert ok_closed is False and reason_closed == "EXPOSURE_BUDGET_EXCEEDED"

    # Even with exposure cap relaxed to 2, schedule safety over closed Dom*(drain_B) STILL blocks!
    gate_closed_cap2 = PlausibleWorldGate(
        [s0, s_A, s_B, s_bad],
        safe_probes=[],
        exposure_caps={"group_A": 2, "group_B": 2, "global_quorum_AB": 2},
        dependency_graph=complete_dep,
    )
    ok_sched, reason_sched, _ = gate_closed_cap2.evaluate_concurrent_compound_safety_with_certificate(
        "drain_B", "group_B", 1, [s0], [op_A_closed]
    )
    assert ok_sched is False and "UNSAFE" in reason_sched


def test_p2_signed_evidence_binding_epoch_resource_manifest():
    """
    P2 (Cryptographic Evidence Binding):
    Valid collector signatures from a mismatched epoch, mismatched resource_id, or stale
    topology manifest_hash MUST be rejected by BoundedATMSEngine.
    """
    verifier = DeterministicRuleVerifier([], [])
    atms = BoundedATMSEngine(
        verifier,
        expected_epoch=42,
        expected_resource_id="cluster-prod-db-01",
        expected_manifest_hash="sha256:topo-v7",
    )

    def build(bit: int, aid: str, epoch: int, res: str, man: str) -> BoundPrimitiveAssumption:
        sig = verifier.sign_telemetry(
            aid, "ch_1", "fd_1", epoch, "obs:down",
            observation_version=5, bit_index=bit, resource_id=res, manifest_hash=man,
        )
        return BoundPrimitiveAssumption(
            bit, aid, "ch_1", "fd_1", epoch, "obs:down", sig,
            observation_version=5, resource_id=res, manifest_hash=man,
        )

    assert atms.register_assumption(build(0, "e_wrong_epoch", 41, "cluster-prod-db-01", "sha256:topo-v7")) is False
    assert atms.register_assumption(build(1, "e_wrong_res", 42, "cluster-staging-02", "sha256:topo-v7")) is False
    assert atms.register_assumption(build(2, "e_wrong_man", 42, "cluster-prod-db-01", "sha256:topo-v6")) is False
    # Unbound PrimitiveAssumption (missing resource_id & manifest_hash) also fails closed:
    unbound_sig = verifier.sign_telemetry("e_unbound", "ch_1", "fd_1", 42, "obs:down", observation_version=5, bit_index=4)
    assert atms.register_assumption(PrimitiveAssumption(4, "e_unbound", "ch_1", "fd_1", 42, "obs:down", unbound_sig, 5)) is False
    assert atms.register_assumption(build(3, "e_valid", 42, "cluster-prod-db-01", "sha256:topo-v7")) is True


def test_p0_unmodeled_commit_or_predecessor_transition_fails_closed():
    """
    Fail-closed verification: transitions targeting an unmodeled world ID outside S_cand
    MUST fail closed during both snapshot advancement and Mode S predecessor execution.
    """
    s0 = FaultWorld("s0", frozenset(), frozenset({"u", "a"}), transition_map={"u": "s_unmodeled", "a": "s0"})
    gate = PlausibleWorldGate([s0], safe_probes=[], exposure_caps={"g": 2})

    c_unmodeled = InFlightMutation("op_c", "u", "g", lifecycle_state="COMMITTED", commit_version=2)
    ok_c, reason_c, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s0], [c_unmodeled], snapshot_context=SnapshotContext(snapshot_version=1)
    )
    assert ok_c is False and reason_c == "UNMODELED_COMMIT_TRANSITION"

    u_unmodeled = InFlightMutation("op_u", "u", "g", serialized_before_new=True)
    ok_u, reason_u, _ = gate.evaluate_concurrent_compound_safety_with_certificate(
        "a", "g", 1, [s0], [u_unmodeled]
    )
    assert ok_u is False and reason_u == "MODE_C_REJECTED_UNMODELED_POST_STATE"
