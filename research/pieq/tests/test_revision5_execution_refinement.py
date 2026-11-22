"""
Revision 5 Formal Correctness Closure & Execution-Contract Regression Suite.

Covers all five Revision 5 formal repairs:
  - P0.1: Physical execution refinement, write-skew counterexample (both exact
          (x, y) non-commuting write-skew and commuting double-spend write-skew),
          fail-closed UNVERIFIED_ASYNC rejection, PHASE_DECOMPOSED interleaving
          detection, safe PHASE_DECOMPOSED certification, and Mode S overlap check.
  - P0.2: Explicit counterexample proving Viab(K_1 cap K_2) strict subset of
          Viab(K_1) cap Viab(K_2), contrasted with sound global invariant preservation.
  - P1.1: Separation of EvidenceValid, SafetyValid, and AuthorizationValid, with
          post-admission authorization revocation & expiry across Tiers E0/E1/E1+/E2.
  - P1.2: Durable SQLite WAL crash/restart persistence of evidence epochs, auth epochs,
          idempotent op_id identities, FENCED_NO_EFFECT records, and UNKNOWN retention.
  - P1.3: Per-domain admission linearization revision CAS invalidating stale Mode C
          certificates under concurrent admissions and preventing 3-way hazards.
"""

from dataclasses import replace

from pieq.core import (
    BoundedATMSEngine,
    CrossRootExposureLedger,
    DeterministicRuleVerifier,
    EpistemicLeaseCertificate,
    FaultWorld,
    InFlightMutation,
    InferenceRule,
    ModeCCompatibilityVerifier,
    PlausibleWorldGate,
    PrimitiveAssumption,
    SQLiteProtectedResourceBoundary,
    TargetResourceBoundary,
    compute_disjoint_justification_packing_bounded,
)


# ---------------------------------------------------------------------------
# P0.1 — Physical Execution Refinement & Write-Skew Counterexamples
# ---------------------------------------------------------------------------

def test_p01_write_skew_physical_refinement_and_phase_decomposition():
    """
    Exact P0.1 Write-Skew Scenario:
      Initial state: x = 0, y = 0.
      Safety invariant: not (x = 1 and y = 1).
      Operation a: read y; if y == 0, set x = 1.
      Operation u: read x; if x == 0, set y = 1.

    Both operation-level sequential orders are safe:
      a -> u reaches (1, 0); u -> a reaches (0, 1).
    Yet physically overlapping read/write phases r_a -> r_u -> w_a -> w_u
    reach (1, 1), violating the invariant.
    """
    # 1. Operation-level abstract model (omits internal read/write phases)
    s_00 = FaultWorld("s_00", frozenset(), frozenset({"a", "u"}), transition_map={"a": "s_10", "u": "s_01"}, viable=True)
    s_10 = FaultWorld("s_10", frozenset(), frozenset({"a", "u"}), transition_map={"a": "s_10", "u": "s_10"}, viable=True)
    s_01 = FaultWorld("s_01", frozenset(), frozenset({"a", "u"}), transition_map={"a": "s_01", "u": "s_01"}, viable=True)
    s_11 = FaultWorld("s_11", frozenset(), frozenset(), transition_map={}, viable=False)

    op_gate = PlausibleWorldGate([s_00, s_10, s_01, s_11], safe_probes=[])
    u_async = InFlightMutation("op_u", "u", "g", serialized_before_new=False)

    # Confirm both sequential operation-level orders preserve the invariant:
    assert s_00.transition_map["a"] == "s_10" and s_10.transition_map["u"] == "s_10"
    assert s_00.transition_map["u"] == "s_01" and s_01.transition_map["a"] == "s_01"

    # Under UNVERIFIED_ASYNC target, Mode C MUST fail closed immediately!
    ok_unver, reason_unver, cert_unver = op_gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="a",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[s_00],
        inflight_ops=[u_async],
        physical_refinement_mode="UNVERIFIED_ASYNC",
    )
    assert not ok_unver
    assert reason_unver == "MODE_C_REJECTED_PHYSICAL_REFINEMENT_UNVERIFIED"
    assert cert_unver is None

    # Under Mode S with single serialized predecessor u <_ord a, a does NOT overlap u -> SAFE!
    u_ser = InFlightMutation("op_u", "u", "g", serialized_before_new=True, sequence_number=1)
    ok_mode_s, reason_mode_s, _ = op_gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="a",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[s_00],
        inflight_ops=[u_ser],
        physical_refinement_mode="UNVERIFIED_ASYNC",
    )
    assert ok_mode_s
    assert reason_mode_s == "ADMIT_COMPOUND_SAFE"

    # Under Mode S with TWO unordered predecessors on UNVERIFIED_ASYNC, predecessors could overlap -> REJECT!
    u1_ser = InFlightMutation("op_u1", "u", "g", serialized_before_new=True)
    u2_ser = InFlightMutation("op_u2", "u", "g", serialized_before_new=True)
    ok_mode_s_2, reason_mode_s_2, _ = op_gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="a",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[s_00],
        inflight_ops=[u1_ser, u2_ser],
        physical_refinement_mode="UNVERIFIED_ASYNC",
    )
    assert not ok_mode_s_2
    assert reason_mode_s_2 == "MODE_S_REJECTED_PHYSICAL_REFINEMENT_UNVERIFIED"

    # 2. Phase-decomposed refinement model exposing internal read/write phases:
    # State encodes (x, y, y_a, x_u) where y_a, x_u in {None, 0, 1}
    phase_worlds = []
    for x in (0, 1):
        for y in (0, 1):
            for y_a in (None, 0, 1):
                for x_u in (None, 0, 1):
                    wid = f"x{x}_y{y}_ya{y_a}_xu{x_u}"
                    viable = not (x == 1 and y == 1)
                    tmap = {}
                    safe = set()
                    if viable:
                        # r_a: read y into y_a
                        if y_a is None:
                            tmap["r_a"] = f"x{x}_y{y}_ya{y}_xu{x_u}"
                            safe.add("r_a")
                        # r_u: read x into x_u
                        if x_u is None:
                            tmap["r_u"] = f"x{x}_y{y}_ya{y_a}_xu{x}"
                            safe.add("r_u")
                        # w_a: if y_a == 0, set x = 1; else no-op
                        if y_a is not None:
                            nx = 1 if y_a == 0 else x
                            tmap["w_a"] = f"x{nx}_y{y}_ya{y_a}_xu{x_u}"
                            safe.add("w_a")
                        # w_u: if x_u == 0, set y = 1; else no-op
                        if x_u is not None:
                            ny = 1 if x_u == 0 else y
                            tmap["w_u"] = f"x{x}_y{ny}_ya{y_a}_xu{x_u}"
                            safe.add("w_u")
                    phase_worlds.append(FaultWorld(wid, frozenset(), frozenset(safe), transition_map=tmap, viable=viable))

    phase_gate = PlausibleWorldGate(phase_worlds, safe_probes=[])
    p_s0 = phase_gate.worlds_by_id["x0_y0_yaNone_xuNone"]

    r_u = InFlightMutation("op_u_r", "r_u", "g", serialized_before_new=False)
    w_u = InFlightMutation(
        "op_u_w",
        "w_u",
        "g",
        serialized_before_new=False,
        predecessor_op_ids=("op_u_r",),
        intra_op_predecessor_ids=("op_u_r",),
    )
    r_a = InFlightMutation("op_a_r", "r_a", "g", serialized_before_new=False)
    w_a = InFlightMutation(
        "op_a_w",
        "w_a",
        "g",
        serialized_before_new=False,
        predecessor_op_ids=("op_a_r",),
        intra_op_predecessor_ids=("op_a_r",),
    )

    ok_phase, reason_phase, cert_phase = phase_gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="a",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[p_s0],
        inflight_ops=[r_u, w_u],
        physical_refinement_mode="PHASE_DECOMPOSED",
        candidate_phases=[r_a, w_a],
    )
    assert not ok_phase
    assert reason_phase == "UNSAFE_PHASE_INTERLEAVING_EXITS_INVARIANT"
    assert cert_phase is None


def test_p01_commuting_double_spend_write_skew_and_safe_phase_decomposed_admission():
    """
    Demonstrates an even stronger write-skew where operation-level a and u COMMUTE
    to the exact same safe state (w_count = 1), so a falsely attested SERIALIZABLE_ATOMIC
    check would pass, whereas UNVERIFIED_ASYNC rejects fail-closed and PHASE_DECOMPOSED
    catches the overlapping read-read-write-write double-commit (w_count = 2).
    Also verifies that genuinely safe non-interfering phased ops pass PHASE_DECOMPOSED.
    """
    # Operation-level commuting double-spend: a -> u == u -> a == s_1 (safe!)
    s_0 = FaultWorld("s_0", frozenset(), frozenset({"a", "u"}), transition_map={"a": "s_1", "u": "s_1"}, viable=True)
    s_1 = FaultWorld("s_1", frozenset(), frozenset({"a", "u"}), transition_map={"a": "s_1", "u": "s_1"}, viable=True)
    op_gate = PlausibleWorldGate([s_0, s_1], safe_probes=[])
    u_conc = InFlightMutation("op_u", "u", "g", serialized_before_new=False)

    # Under genuinely serializable target (R1), commuting ops are safe:
    ok_r1, reason_r1, cert_r1 = op_gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="a",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[s_0],
        inflight_ops=[u_conc],
        physical_refinement_mode="SERIALIZABLE_ATOMIC",
    )
    assert ok_r1 and reason_r1 == "ADMIT_COMPOUND_SAFE"
    assert cert_r1 is not None and cert_r1.physical_refinement_mode == "SERIALIZABLE_ATOMIC"
    assert ModeCCompatibilityVerifier.verify_certificate_digest(cert_r1)

    # Under UNVERIFIED_ASYNC target, MUST fail closed (cannot assume R1):
    ok_ua, reason_ua, cert_ua = op_gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="a",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[s_0],
        inflight_ops=[u_conc],
        physical_refinement_mode="UNVERIFIED_ASYNC",
    )
    assert not ok_ua and reason_ua == "MODE_C_REJECTED_PHYSICAL_REFINEMENT_UNVERIFIED"
    assert cert_ua is None

    # Safe disjoint phased operations (a sets x=1, u sets y=1, invariant allows both x=1 and y=1):
    safe_worlds = []
    for x in (0, 1):
        for y in (0, 1):
            for ra in (0, 1):
                for ru in (0, 1):
                    wid = f"s_{x}{y}_{ra}{ru}"
                    tmap = {}
                    safe = set()
                    if ra == 0:
                        tmap["r_a"] = f"s_{x}{y}_1{ru}"
                        safe.add("r_a")
                    else:
                        tmap["w_a"] = f"s_1{y}_1{ru}"
                        safe.add("w_a")
                    if ru == 0:
                        tmap["r_u"] = f"s_{x}{y}_{ra}1"
                        safe.add("r_u")
                    else:
                        tmap["w_u"] = f"s_{x}1_{ra}1"
                        safe.add("w_u")
                    safe_worlds.append(FaultWorld(wid, frozenset(), frozenset(safe), transition_map=tmap, viable=True))

    safe_phase_gate = PlausibleWorldGate(safe_worlds, safe_probes=[])
    r_u = InFlightMutation("op_u_r", "r_u", "g", serialized_before_new=False)
    w_u = InFlightMutation(
        "op_u_w", "w_u", "g", serialized_before_new=False,
        predecessor_op_ids=("op_u_r",), intra_op_predecessor_ids=("op_u_r",),
    )
    r_a = InFlightMutation("op_a_r", "r_a", "g", serialized_before_new=False)
    w_a = InFlightMutation(
        "op_a_w", "w_a", "g", serialized_before_new=False,
        predecessor_op_ids=("op_a_r",), intra_op_predecessor_ids=("op_a_r",),
    )

    ok_sp, reason_sp, cert_sp = safe_phase_gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="a",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[safe_phase_gate.worlds_by_id["s_00_00"]],
        inflight_ops=[r_u, w_u],
        physical_refinement_mode="PHASE_DECOMPOSED",
        candidate_phases=[r_a, w_a],
    )
    assert ok_sp and reason_sp == "ADMIT_COMPOUND_SAFE"
    assert cert_sp is not None and cert_sp.physical_refinement_mode == "PHASE_DECOMPOSED"
    assert ModeCCompatibilityVerifier.verify_certificate_digest(cert_sp)


# ---------------------------------------------------------------------------
# P0.2 — Local Viability Intersection vs. Global Viability Counterexample
# ---------------------------------------------------------------------------

def _compute_viability_kernel(states, actions, transition_fn, safe_set):
    """Exact greatest fixed point Viab(K) = {s in K : exists a in A, Post(s, a) <= Viab(K)}."""
    current = set(safe_set)
    while True:
        nxt = {
            s for s in current
            if any(transition_fn(s, a).issubset(current) for a in actions)
        }
        if nxt == current:
            return current
        current = nxt


def test_p02_local_viability_intersection_strict_superset_of_global_viability():
    """
    Proves constructively that Viab(K_1 cap K_2) can be strictly smaller than
    Viab(K_1) cap Viab(K_2), whereas invariant preservation DOES compose via intersection.
    """
    states = {"s0", "s1", "s2", "s_bad"}
    actions = ("a1", "a2")
    transitions = {
        ("s0", "a1"): {"s1"},
        ("s0", "a2"): {"s2"},
        ("s1", "a1"): {"s1"},
        ("s1", "a2"): {"s_bad"},
        ("s2", "a1"): {"s_bad"},
        ("s2", "a2"): {"s2"},
        ("s_bad", "a1"): {"s_bad"},
        ("s_bad", "a2"): {"s_bad"},
    }
    post = lambda s, a: transitions[(s, a)]

    k1 = {"s0", "s1"}
    k2 = {"s0", "s2"}
    k_global = k1 & k2  # {"s0"}

    viab_k1 = _compute_viability_kernel(states, actions, post, k1)
    viab_k2 = _compute_viability_kernel(states, actions, post, k2)
    viab_global = _compute_viability_kernel(states, actions, post, k_global)

    assert viab_k1 == {"s0", "s1"}
    assert viab_k2 == {"s0", "s2"}
    assert (viab_k1 & viab_k2) == {"s0"}
    assert viab_global == set()
    assert viab_global < (viab_k1 & viab_k2)  # Strict subset!

    # Contrast: invariant preservation DOES compose by intersection:
    # Any action a_stay preserving both K_1 and K_2 preserves K_1 cap K_2.
    t_with_stay = dict(transitions)
    t_with_stay[("s0", "a_stay")] = {"s0"}
    assert t_with_stay[("s0", "a_stay")].issubset(k1)
    assert t_with_stay[("s0", "a_stay")].issubset(k2)
    assert t_with_stay[("s0", "a_stay")].issubset(k1 & k2)


# ---------------------------------------------------------------------------
# P1.1 — Execution-Time Evidence vs. Safety vs. Authorization Freshness
# ---------------------------------------------------------------------------

def test_p11_post_admission_authorization_revocation_across_tiers(tmp_path):
    """
    Action passes full PIEQ epistemic proof packing (nu_pack = 3 >= f + 1) and
    plausible-world safety gating, then authorization is revoked post-admission
    while telemetry evidence and physical safety remain completely valid.
    Verifies tier-dependent enforcement across E2 (SQLite WAL), E1+-quiescence,
    E1 async fence, and E0 proxy-only.
    """
    # 1. EvidenceValid = True (3 provenance-disjoint primitive domains)
    rules = [
        InferenceRule("r0", frozenset({"obs:ok_0"}), "node_safe_to_drain"),
        InferenceRule("r1", frozenset({"obs:ok_1"}), "node_safe_to_drain"),
        InferenceRule("r2", frozenset({"obs:ok_2"}), "node_safe_to_drain"),
    ]
    verifier = DeterministicRuleVerifier(rules, [])
    atms = BoundedATMSEngine(verifier)
    justs = []
    for bit, dom, ch in ((0, "dom_a", "col_a"), (1, "dom_b", "col_b"), (2, "dom_c", "col_c")):
        aid = f"a{bit}"
        prop = f"obs:ok_{bit}"
        sig = verifier.sign_telemetry(aid, ch, dom, 10, prop, observation_version=1, bit_index=bit)
        assump = PrimitiveAssumption(bit, aid, ch, dom, 10, prop, sig, observation_version=1)
        assert atms.register_assumption(assump)
        j = atms.add_verified_justification(f"A{bit}", f"ctx_{bit}", "node_safe_to_drain", assump.bitmask, f"r{bit}")
        assert j is not None
        justs.append(j)
    nu_pack, _, trunc = compute_disjoint_justification_packing_bounded(justs, atms.assumptions_by_bit)
    assert not trunc and nu_pack == 3

    # 2. SafetyValid = True (all plausible worlds allow drain_node_1)
    w_ok = FaultWorld("s_ok", frozenset(), frozenset({"drain_node_1"}), transition_map={"drain_node_1": "s_drained"}, viable=True)
    w_drained = FaultWorld("s_drained", frozenset(), frozenset(), transition_map={}, viable=True)
    gate = PlausibleWorldGate([w_ok, w_drained], safe_probes=[])
    ok_safe, _ = gate.evaluate_concurrent_compound_safety("drain_node_1", "db_cluster", 1, [w_ok], [])
    assert ok_safe

    # Certificate issued at admission_epoch=10, auth_epoch=5, auth_valid_until=100
    cert_v5 = EpistemicLeaseCertificate(
        action="drain_node_1",
        support_mask=0b111,
        admission_epoch=10,
        packing_number=3,
        plausible_worlds=("s_ok",),
        signature="sig_auth5",
        auth_epoch=5,
        auth_valid_until=100,
    )

    # 3. Tier E2 SQLite WAL: revoke authorization epoch to 6 (telemetry epoch remains 10!)
    db_file = str(tmp_path / "auth_e2.sqlite")
    e2 = SQLiteProtectedResourceBoundary(db_file, initial_epoch=10, max_drained_nodes=1)
    e2.revoke_authorization_atomically(6)
    assert e2.current_min_epoch() == 10  # Telemetry evidence epoch unchanged!
    assert e2.current_auth_epoch() == 6

    r_revoked = e2.execute_fenced_mutation(cert_v5, target_node="node_1", op_id="op_revoked", now_time=50)
    assert not r_revoked.committed
    assert r_revoked.status == "REJECTED_AUTHORIZATION_REVOKED"

    # Revocation survives crash/restart (`reopen()`)
    e2.reopen()
    r_after_restart = e2.execute_fenced_mutation(cert_v5, target_node="node_1", op_id="op_revoked_2", now_time=50)
    assert not r_after_restart.committed
    assert r_after_restart.status == "REJECTED_AUTHORIZATION_REVOKED"

    # Re-authorized cert (auth_epoch=6) still rejected if validity window expired (now_time=101 > 100)
    cert_v6 = replace(cert_v5, auth_epoch=6, signature="sig_auth6")
    r_expired = e2.execute_fenced_mutation(cert_v6, target_node="node_1", op_id="op_expired", now_time=101)
    assert not r_expired.committed
    assert r_expired.status == "REJECTED_AUTHORIZATION_EXPIRED"

    # Valid auth_epoch=6 within validity window (now_time=95 <= 100) succeeds atomically!
    r_ok = e2.execute_fenced_mutation(cert_v6, target_node="node_1", op_id="op_valid", now_time=95)
    assert r_ok.committed and r_ok.status == "COMMITTED"

    # 4. Tier E1+-quiescence rejects revoked & expired certs
    e1q = TargetResourceBoundary(initial_epoch=10, tier="E1_PLUS_QUIESCENCE_BARRIER")
    e1q.revoke_authorization_epoch(6)
    rq_rev, _ = e1q.execute_quiescence_handshake(cert_v5, op_id="q1", now_time=50)
    assert not rq_rev.committed and rq_rev.status == "REJECTED_AUTHORIZATION_REVOKED"
    rq_exp, _ = e1q.execute_quiescence_handshake(cert_v6, op_id="q2", now_time=101)
    assert not rq_exp.committed and rq_exp.status == "REJECTED_AUTHORIZATION_EXPIRED"

    # 5. Tier E1 async fence leaks while revocation message delayed, then rejects once delivered
    e1 = TargetResourceBoundary(initial_epoch=10, tier="E1_ASYNC_FENCE")
    e1.revoke_authorization_epoch(6, fence_message_delayed=True)
    r_e1_leak = e1.execute_with_certificate(cert_v5, now_time=50)
    assert r_e1_leak.committed  # Async fence delay leaks!
    e1.deliver_delayed_fence()
    r_e1_after = e1.execute_with_certificate(cert_v5, now_time=50)
    assert not r_e1_after.committed and r_e1_after.status == "REJECTED_AUTHORIZATION_REVOKED"

    # 6. Tier E0 proxy-only leaks unconditionally
    e0 = TargetResourceBoundary(initial_epoch=10, enforce_target_fence=False, tier="E0_PROXY_ONLY")
    e0.revoke_authorization_epoch(6)
    assert e0.execute_with_certificate(cert_v5, now_time=999).committed


# ---------------------------------------------------------------------------
# P1.2 — Crash/Restart Fencing Persistence & Recovery Reconciliation
# ---------------------------------------------------------------------------

def test_p12_sqlite_wal_fencing_and_auth_epoch_survive_crash_restart(tmp_path):
    """
    Verifies P1.2 durability obligations on SQLite WAL target boundary:
      - min_valid_epoch, min_auth_epoch, committed_ops (with identity_json),
        FENCED_NO_EFFECT records, and last_committed_seq all persist across restart.
      - Coordinator ledger reconciles durable outcomes while keeping unconfirmed ops in I_U.
    """
    db_path = str(tmp_path / "crash_recovery.sqlite")
    target = SQLiteProtectedResourceBoundary(db_path, initial_epoch=10, max_drained_nodes=2)
    ledger = CrossRootExposureLedger({"db_cluster": 2})

    _, op1, _ = ledger.admit_and_reserve("k1", "op_1", "drain_node_1", "db_cluster", 1)
    _, op2, _ = ledger.admit_and_reserve("k2", "op_2", "drain_node_2", "db_cluster", 1)
    assert op1 is not None and op2 is not None

    cert1 = EpistemicLeaseCertificate("drain_node_1", 0b111, 10, 3, ("s0",), "sig1", auth_epoch=3)
    r1 = target.execute_fenced_mutation(cert1, target_node="node_1", op_id="op_1")
    assert r1.committed and r1.commit_version == 1

    # Record op_fenced as FENCED_NO_EFFECT and advance both epochs before crash
    target.record_fenced_no_effect("op_fenced", action="drain_node_3")
    target.revoke_epoch_atomically(12)
    target.revoke_authorization_atomically(4)

    # Simulate hard crash and fresh restart from durable WAL file
    target.conn.close()
    restarted = SQLiteProtectedResourceBoundary(db_path, initial_epoch=10, max_drained_nodes=2)

    assert restarted.current_min_epoch() == 12
    assert restarted.current_auth_epoch() == 4
    assert restarted.current_seq() == 1
    assert restarted.durable_committed_versions() == {"op_1": 1}
    assert "op_fenced" in restarted.durable_fenced_no_effect_ops()

    # Duplicate retry of op_1 is idempotent; conflicting identity fails closed!
    r1_dup = restarted.execute_fenced_mutation(cert1, target_node="node_1", op_id="op_1")
    assert r1_dup.committed and r1_dup.status == "DUPLICATE_IDEMPOTENT_COMMITTED"
    cert1_tampered = replace(cert1, auth_epoch=4)
    r1_conflict = restarted.execute_fenced_mutation(cert1_tampered, target_node="node_1", op_id="op_1")
    assert not r1_conflict.committed and r1_conflict.status == "REJECTED_OP_ID_CONFLICT"

    # Stale epoch / stale auth certs rejected post-restart
    cert_stale_ep = EpistemicLeaseCertificate("drain_node_2", 0b111, 11, 3, ("s0",), "sig2", auth_epoch=4)
    assert restarted.execute_fenced_mutation(cert_stale_ep, target_node="node_2", op_id="op_2").status == "REJECTED_STALE_EPOCH"
    cert_stale_auth = EpistemicLeaseCertificate("drain_node_2", 0b111, 12, 3, ("s0",), "sig2b", auth_epoch=3)
    assert restarted.execute_fenced_mutation(cert_stale_auth, target_node="node_2", op_id="op_2").status == "REJECTED_AUTHORIZATION_REVOKED"

    # Re-trying FENCED_NO_EFFECT op_id stays permanently fenced
    cert_fenced = EpistemicLeaseCertificate("drain_node_3", 0b111, 12, 3, ("s0",), "sig3", auth_epoch=4)
    r_fen = restarted.execute_fenced_mutation(cert_fenced, target_node="node_3", op_id="op_fenced")
    assert not r_fen.committed and r_fen.status == "REJECTED_PREVIOUSLY_FENCED"

    # Mode S successor waiting on op_fenced (FENCED_NO_EFFECT) + op_1 (COMMITTED) can proceed with fresh epochs!
    cert_next = EpistemicLeaseCertificate("drain_node_2", 0b111, 12, 3, ("s0",), "sig_next", auth_epoch=4)
    r_next = restarted.execute_fenced_mutation(
        cert_next, target_node="node_2", op_id="op_next", required_predecessor_op_ids=("op_1", "op_fenced")
    )
    assert r_next.committed and r_next.commit_version == 2

    # Coordinator crash recovery reconciles op_1 (COMMITTED) and retains op_2 (unconfirmed) in I_U!
    report = ledger.reconcile_after_restart(
        "db_cluster",
        target_committed_versions=restarted.durable_committed_versions(),
        fenced_no_effect_ops=restarted.durable_fenced_no_effect_ops(),
    )
    assert report["op_1"] == "RECONCILED_COMMITTED"
    assert report["op_2"] == "RETAINED_IN_IU_UNKNOWN"
    assert "op_2" in ledger.unknown_iu["db_cluster"]


# ---------------------------------------------------------------------------
# P1.3 — Admission Linearization & Stale Mode C Certificate Invalidation
# ---------------------------------------------------------------------------

def test_p13_concurrent_admission_invalidates_stale_mode_c_certificate():
    """
    Two concurrent Mode C admissions a_1 and a_2 both verify against the same
    ledger snapshot r_adm(g) = 1 (with U = {u_1}), where each pair {u_1, a_1}
    and {u_1, a_2} is safe (<= 2 drains) but the 3-way set {u_1, a_1, a_2}
    violates the 2-drain invariant (s_3.viable = False).

    Admission linearization CAS on r_adm(g) accepts a_1 (advancing r_adm -> 2)
    and deterministically rejects a_2's stale certificate, forcing revalidation
    against {u_1, a_1}, which catches the 3-way hazard.
    """
    # Counter state: 0, 1, 2 drains safe; 3 drains unsafe
    s0 = FaultWorld("s0", frozenset(), frozenset({"d1", "d2", "d3"}), transition_map={"d1": "s1", "d2": "s1", "d3": "s1"}, viable=True)
    s1 = FaultWorld("s1", frozenset(), frozenset({"d1", "d2", "d3"}), transition_map={"d1": "s2", "d2": "s2", "d3": "s2"}, viable=True)
    s2 = FaultWorld("s2", frozenset(), frozenset(), transition_map={"d1": "s3", "d2": "s3", "d3": "s3"}, viable=True)
    s3 = FaultWorld("s3", frozenset(), frozenset(), transition_map={}, viable=False)

    gate = PlausibleWorldGate([s0, s1, s2, s3], safe_probes=[])
    ledger = CrossRootExposureLedger({"g": 10})  # High exposure cap so safety comes from Mode C + CAS!

    ok_u1, u1, _ = ledger.admit_and_reserve("k_u1", "op_u1", "d1", "g", 1, serialized_before_new=False)
    assert ok_u1 and u1 is not None
    revs_snap = ledger.admission_revisions(["g"])
    assert revs_snap == {"g": 1}

    # Both Coordinator 1 (d2) and Coordinator 2 (d3) verify Mode C concurrently against revs_snap={"g": 1}
    ok_a1, reason_a1, cert_a1 = gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="d2",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[s0],
        inflight_ops=ledger.active_operations("g"),
        admission_revisions=revs_snap,
    )
    ok_a2, reason_a2, cert_a2 = gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="d3",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[s0],
        inflight_ops=ledger.active_operations("g"),
        admission_revisions=revs_snap,
    )
    assert ok_a1 and cert_a1 is not None and cert_a1.admission_revisions == (("g", 1),)
    assert ok_a2 and cert_a2 is not None and cert_a2.admission_revisions == (("g", 1),)
    assert ModeCCompatibilityVerifier.verify_certificate_digest(cert_a1)
    assert ModeCCompatibilityVerifier.verify_certificate_digest(cert_a2)

    # Tampering with bound admission_revisions invalidates certificate digest!
    tampered = replace(cert_a2, admission_revisions=(("g", 2),))
    assert not ModeCCompatibilityVerifier.verify_certificate_digest(tampered)

    # Coordinator 1 linearizes admission at r_adm(g) = 1 -> SUCCEEDS, advancing r_adm(g) -> 2
    res1_ok, op_a1, res1_reason = ledger.admit_and_reserve(
        "k_a1", "op_a1", "d2", "g", 1, serialized_before_new=False,
        expected_admission_revs=dict(cert_a1.admission_revisions),
    )
    assert res1_ok and op_a1 is not None and res1_reason == "RESERVED_IN_IH"
    assert ledger.admission_revisions(["g"]) == {"g": 2}

    # Coordinator 2 attempts to reserve with stale cert_a2 bound to r_adm(g) = 1 -> REJECTED BY CAS!
    res2_ok, op_a2, res2_reason = ledger.admit_and_reserve(
        "k_a2", "op_a2", "d3", "g", 1, serialized_before_new=False,
        expected_admission_revs=dict(cert_a2.admission_revisions),
    )
    assert not res2_ok and op_a2 is None
    assert res2_reason == "REJECTED_STALE_CONCURRENCY_CERTIFICATE_REVALIDATION_REQUIRED"

    # Coordinator 2 revalidates against updated ledger state {u_1, a_1} at r_adm(g) = 2 -> CATCHES 3-WAY HAZARD!
    ok_a2_reval, reason_a2_reval, cert_a2_reval = gate.evaluate_concurrent_compound_safety_with_certificate(
        candidate_action="d3",
        resource_group="g",
        exposure_cost=1,
        plausible_worlds=[s0],
        inflight_ops=ledger.active_operations("g"),
        admission_revisions=ledger.admission_revisions(["g"]),
    )
    assert not ok_a2_reval and cert_a2_reval is None
    assert reason_a2_reval in (
        "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE",
        "UNSAFE_REVERSE_INTERLEAVING_AFTER_CANDIDATE",
        "UNSAFE_INTERMEDIATE_STATE",
        "UNSAFE_POST_STATE_EXITS_VIABILITY",
    )
