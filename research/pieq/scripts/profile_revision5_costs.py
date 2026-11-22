"""
Revision 5 (P2.2) Operational Cost & Fail-Closed Microbenchmark.

Measures REAL local Python/CPU + on-disk SQLite WAL latencies for:
  1. Multi-step Proof-DAG verification & recursive dependency footprint (depths 1, 2, 4).
  2. Set-packing under overlapping proofs (W = 8, 16, 32, 64) + bounded-cap truncation.
  3. Mode S predecessor prefix verification (|U_S| = 1..4 unordered, 4 totally ordered,
     and fail-closed rejection at |U_S| = 5 unordered).
  4. Mode C exhaustive schedule verification including absent-a subsets (|U_C| = 1..4,
     fail-closed rejection at |U_C| = 5, and 2-op/4-phase PHASE_DECOMPOSED verification).
  5. Unknown-order commit reconciliation blocking (`advance_snapshot_states`).
  6. On-disk SQLite WAL crash/restart recovery (`reopen` + `reconcile_after_restart`
     + post-restart fenced execution).

Explicitly local single-host measurements only (does NOT measure distributed consensus
RTT, remote target RPCs, or LLM inference latency).
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    SignedJustification,
    SnapshotContext,
    compute_disjoint_justification_packing_bounded,
    justification_dependency_footprint,
)


def _quantiles_ms(samples_ns: List[int]) -> Dict[str, float]:
    s = sorted(x / 1_000_000.0 for x in samples_ns)
    n = len(s)
    def pct(p: float) -> float:
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return round(s[idx], 4)
    return {
        "n": n,
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "max_ms": round(s[-1], 4),
    }


def bench_proof_dag_verification() -> Dict[str, Dict[str, float]]:
    results = {}
    for depth in (1, 2, 4):
        # Build binary tree DAG of given depth: 2^depth primitive leaves -> depth layers of Horn rules
        num_leaves = 2 ** depth
        rules = []
        for lvl in range(1, depth + 1):
            count = 2 ** (depth - lvl)
            for idx in range(count):
                if lvl == 1:
                    ant = frozenset({f"obs:l_{2*idx}", f"obs:l_{2*idx+1}"})
                else:
                    ant = frozenset({f"d_{lvl-1}_{2*idx}", f"d_{lvl-1}_{2*idx+1}"})
                cons = "act:target" if lvl == depth else f"d_{lvl}_{idx}"
                rules.append(InferenceRule(f"r_{lvl}_{idx}", ant, cons))

        verifier = DeterministicRuleVerifier(rules, [])
        # Pre-sign leaves once (measures admission-time verification + DAG derivation + recursive footprint)
        leaves = []
        for i in range(num_leaves):
            sig = verifier.sign_telemetry(f"a{i}", f"ch{i}", f"fd{i}", 10, f"obs:l_{i}", 1, i)
            leaves.append(PrimitiveAssumption(i, f"a{i}", f"ch{i}", f"fd{i}", 10, f"obs:l_{i}", sig, 1))

        samples = []
        for _ in range(100):
            t0 = time.perf_counter_ns()
            atms = BoundedATMSEngine(verifier, max_env_cardinality=32)
            for leaf in leaves:
                assert atms.register_assumption(leaf)
            cur_layer = []
            for idx in range(num_leaves // 2):
                mask = leaves[2 * idx].bitmask | leaves[2 * idx + 1].bitmask
                j = atms.add_verified_justification(
                    f"ag_{idx}", f"ctx_1_{idx}",
                    "act:target" if depth == 1 else f"d_1_{idx}",
                    mask, f"r_1_{idx}",
                )
                assert j is not None
                cur_layer.append(j)
            for lvl in range(2, depth + 1):
                nxt_layer = []
                for idx in range(len(cur_layer) // 2):
                    cons = "act:target" if lvl == depth else f"d_{lvl}_{idx}"
                    j = atms.add_verified_derivation(
                        f"ag_{lvl}_{idx}", f"ctx_{lvl}_{idx}", cons, 0,
                        [cur_layer[2 * idx], cur_layer[2 * idx + 1]], f"r_{lvl}_{idx}",
                    )
                    assert j is not None
                    nxt_layer.append(j)
                cur_layer = nxt_layer
            fp = justification_dependency_footprint(cur_layer[0], atms.assumptions_by_bit)
            assert len(fp) >= num_leaves
            samples.append(time.perf_counter_ns() - t0)
        results[f"depth_{depth}_leaves_{num_leaves}"] = _quantiles_ms(samples)
    return results


def bench_set_packing() -> Dict[str, Dict[str, object]]:
    results: Dict[str, Dict[str, object]] = {}
    # Structured overlapping proofs: each proof uses 2 failure domains from a pool of W//2 domains
    # creating cyclic/cluster overlaps so branch-and-bound does real work
    for w_count in (8, 16, 32, 64):
        pool = max(4, w_count // 2)
        assumps: Dict[int, PrimitiveAssumption] = {}
        for i in range(pool):
            assumps[i] = PrimitiveAssumption(i, f"a{i}", f"ch{i}", f"fd{i}", 10, f"obs:{i}", "sig", 1)
        justs = []
        for idx in range(w_count):
            b1 = idx % pool
            b2 = (idx + 1 + (idx // pool)) % pool
            mask = (1 << b1) | (1 << b2)
            justs.append(
                SignedJustification(
                    agent_id=f"ag_{idx}",
                    context_id=f"ctx_{idx}",
                    consequent="act:promote",
                    env_mask=mask,
                    rule_id="r",
                    merkle_hash=f"h_{idx}",
                )
            )
        samples = []
        last_nu = 0
        last_trunc = False
        iters = 50 if w_count <= 32 else 20
        for _ in range(iters):
            t0 = time.perf_counter_ns()
            nu, _, trunc = compute_disjoint_justification_packing_bounded(
                justs, assumps, max_expansions=10000
            )
            samples.append(time.perf_counter_ns() - t0)
            last_nu = nu
            last_trunc = trunc
        entry = dict(_quantiles_ms(samples))
        entry["nu_pack"] = last_nu
        entry["truncated"] = last_trunc
        results[f"W_{w_count}_cap_10000"] = entry

    # Explicit bounded-cap fail-closed truncation measurement (dense overlap with tight cap=128)
    assumps_dense = {
        i: PrimitiveAssumption(i, f"a{i}", f"ch{i}", f"fd{i}", 10, f"obs:{i}", "sig", 1)
        for i in range(18)
    }
    justs_dense = [
        SignedJustification(
            agent_id=f"ag_{i}",
            context_id=f"ctx_{i}",
            consequent="act:promote",
            env_mask=(1 << (i % 18)) | (1 << ((i * 5 + 3) % 18)),
            rule_id="r",
            merkle_hash=f"hd_{i}",
        )
        for i in range(36)
    ]
    samples_trunc = []
    trunc_nu = 0
    trunc_flag = False
    for _ in range(50):
        t0 = time.perf_counter_ns()
        nu, _, trunc = compute_disjoint_justification_packing_bounded(
            justs_dense, assumps_dense, max_expansions=128
        )
        samples_trunc.append(time.perf_counter_ns() - t0)
        trunc_nu = nu
        trunc_flag = trunc
    trunc_entry = dict(_quantiles_ms(samples_trunc))
    trunc_entry["nu_lower_bound"] = trunc_nu
    trunc_entry["truncated"] = trunc_flag
    results["W_36_dense_cap_128_fail_closed"] = trunc_entry
    return results


def bench_mode_s_and_mode_c() -> Dict[str, Dict[str, object]]:
    # Commuting safe counter worlds s0..s6
    actions = ("u0", "u1", "u2", "u3", "u4", "a")
    worlds = []
    for k in range(7):
        tmap = {act: f"s{min(6, k + 1)}" for act in actions}
        worlds.append(FaultWorld(f"s{k}", frozenset(), frozenset(actions), transition_map=tmap, viable=True))
    gate = PlausibleWorldGate(worlds, safe_probes=[])
    s0 = worlds[0]

    res: Dict[str, Dict[str, object]] = {}

    # Mode S unordered |U_S| = 1..4
    for n_ops in (1, 2, 3, 4):
        ops = [InFlightMutation(f"op_{i}", f"u{i}", "g", serialized_before_new=True) for i in range(n_ops)]
        samples = []
        iters = 100 if n_ops <= 3 else 30
        for _ in range(iters):
            t0 = time.perf_counter_ns()
            ok, reason, _ = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], ops)
            assert ok and reason == "ADMIT_COMPOUND_SAFE"
            samples.append(time.perf_counter_ns() - t0)
        res[f"mode_s_unordered_{n_ops}"] = _quantiles_ms(samples)

    # Mode S totally ordered chain |U_S| = 4
    chain_ops = [
        InFlightMutation(
            f"op_{i}", f"u{i}", "g", serialized_before_new=True,
            predecessor_op_ids=() if i == 0 else (f"op_{i-1}",),
        )
        for i in range(4)
    ]
    samples = []
    for _ in range(100):
        t0 = time.perf_counter_ns()
        ok, reason, _ = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], chain_ops)
        assert ok and reason == "ADMIT_COMPOUND_SAFE"
        samples.append(time.perf_counter_ns() - t0)
    res["mode_s_chain_4"] = _quantiles_ms(samples)

    # Mode S fail-closed at |U_S| = 5 unordered
    ops5 = [InFlightMutation(f"op_{i}", f"u{i}", "g", serialized_before_new=True) for i in range(5)]
    samples = []
    for _ in range(100):
        t0 = time.perf_counter_ns()
        ok, reason, _ = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], ops5)
        assert not ok and reason == "MODE_S_PREDECESSOR_CLASS_UNBOUNDED_FAIL_CLOSED"
        samples.append(time.perf_counter_ns() - t0)
    entry = dict(_quantiles_ms(samples))
    entry["reason"] = "MODE_S_PREDECESSOR_CLASS_UNBOUNDED_FAIL_CLOSED"
    res["mode_s_unordered_5_fail_closed"] = entry

    # Mode C |U_C| = 1..4 (exhaustive subsets including absent-a)
    for n_ops in (1, 2, 3, 4):
        ops = [InFlightMutation(f"op_{i}", f"u{i}", "g", serialized_before_new=False) for i in range(n_ops)]
        samples = []
        scheds = 0
        iters = 100 if n_ops <= 3 else 15
        for _ in range(iters):
            t0 = time.perf_counter_ns()
            ok, reason, cert = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], ops)
            assert ok and reason == "ADMIT_COMPOUND_SAFE" and cert is not None
            scheds = cert.checked_schedule_count
            samples.append(time.perf_counter_ns() - t0)
        entry = dict(_quantiles_ms(samples))
        entry["checked_schedules"] = scheds
        res[f"mode_c_serializable_{n_ops}"] = entry

    # Mode C fail-closed at |U_C| = 5
    ops5_c = [InFlightMutation(f"op_{i}", f"u{i}", "g", serialized_before_new=False) for i in range(5)]
    samples = []
    for _ in range(100):
        t0 = time.perf_counter_ns()
        ok, reason, cert = gate.evaluate_concurrent_compound_safety_with_certificate("a", "g", 1, [s0], ops5_c)
        assert not ok and cert is None and reason == "MODE_C_REJECTED_OUTSIDE_BOUNDED_CLASS_FALLBACK_MODE_S"
        samples.append(time.perf_counter_ns() - t0)
    entry = dict(_quantiles_ms(samples))
    entry["reason"] = "MODE_C_REJECTED_OUTSIDE_BOUNDED_CLASS_FALLBACK_MODE_S"
    res["mode_c_5_fail_closed"] = entry

    # Mode C PHASE_DECOMPOSED (2 ops = 4 phases, safe disjoint)
    safe_worlds = []
    for x in (0, 1):
        for y in (0, 1):
            for ra in (0, 1):
                for ru in (0, 1):
                    wid = f"s_{x}{y}_{ra}{ru}"
                    tmap = {}
                    safe = set()
                    if ra == 0:
                        tmap["r_a"] = f"s_{x}{y}_1{ru}"; safe.add("r_a")
                    else:
                        tmap["w_a"] = f"s_1{y}_1{ru}"; safe.add("w_a")
                    if ru == 0:
                        tmap["r_u"] = f"s_{x}{y}_{ra}1"; safe.add("r_u")
                    else:
                        tmap["w_u"] = f"s_{x}1_{ra}1"; safe.add("w_u")
                    safe_worlds.append(FaultWorld(wid, frozenset(), frozenset(safe), transition_map=tmap, viable=True))
    pgate = PlausibleWorldGate(safe_worlds, safe_probes=[])
    p0 = pgate.worlds_by_id["s_00_00"]
    r_u = InFlightMutation("u_r", "r_u", "g", serialized_before_new=False)
    w_u = InFlightMutation("u_w", "w_u", "g", serialized_before_new=False, predecessor_op_ids=("u_r",), intra_op_predecessor_ids=("u_r",))
    r_a = InFlightMutation("a_r", "r_a", "g", serialized_before_new=False)
    w_a = InFlightMutation("a_w", "w_a", "g", serialized_before_new=False, predecessor_op_ids=("a_r",), intra_op_predecessor_ids=("a_r",))
    samples = []
    scheds = 0
    for _ in range(100):
        t0 = time.perf_counter_ns()
        ok, reason, cert = pgate.evaluate_concurrent_compound_safety_with_certificate(
            "a", "g", 1, [p0], [r_u, w_u],
            physical_refinement_mode="PHASE_DECOMPOSED", candidate_phases=[r_a, w_a],
        )
        assert ok and cert is not None
        scheds = cert.checked_schedule_count
        samples.append(time.perf_counter_ns() - t0)
    entry = dict(_quantiles_ms(samples))
    entry["checked_schedules"] = scheds
    res["mode_c_phase_decomposed_4phases"] = entry

    return res


def bench_unknown_order_and_crash_recovery() -> Dict[str, Dict[str, object]]:
    res: Dict[str, Dict[str, object]] = {}

    # 1. Unknown-order commit blocking
    s0 = FaultWorld("s0", frozenset(), frozenset({"assign", "inc", "a"}), transition_map={"assign": "s1", "inc": "s1", "a": "s0"}, viable=True)
    s1 = FaultWorld("s1", frozenset(), frozenset({"assign", "inc", "a"}), transition_map={"assign": "s1", "inc": "s2", "a": "s1"}, viable=True)
    s2 = FaultWorld("s2", frozenset(), frozenset(), transition_map={}, viable=False)
    gate = PlausibleWorldGate([s0, s1, s2], safe_probes=[])
    c_unver = InFlightMutation("c1", "assign", "g", lifecycle_state="COMMITTED", commit_version=None)
    c_ver = InFlightMutation("c2", "inc", "g", lifecycle_state="COMMITTED", commit_version=2)
    snap = SnapshotContext(snapshot_version=0, require_versioned_commits=True)

    samples = []
    for _ in range(200):
        t0 = time.perf_counter_ns()
        ok, _, reason = gate.advance_snapshot_states([s0], [c_unver, c_ver], snapshot_context=snap)
        assert not ok and reason == "BLOCKED_UNVERSIONED_COMMIT_RECONCILIATION_REQUIRED"
        samples.append(time.perf_counter_ns() - t0)
    res["unknown_order_commit_barrier"] = _quantiles_ms(samples)

    # 2. On-disk SQLite WAL crash/restart recovery + reconciliation + fenced auth/epoch check
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "recovery_bench.sqlite")
        target = SQLiteProtectedResourceBoundary(db_path, initial_epoch=10, max_drained_nodes=2)
        cert1 = EpistemicLeaseCertificate("drain_node_1", 0b111, 10, 3, ("s0",), "sig1", auth_epoch=3)
        assert target.execute_fenced_mutation(cert1, target_node="node_1", op_id="op_1").committed
        target.record_fenced_no_effect("op_fenced", action="drain_node_3")
        target.revoke_epoch_atomically(11)
        target.revoke_authorization_atomically(4)

        reopen_reconcile_samples = []
        fenced_reject_samples = []
        cert_stale = EpistemicLeaseCertificate("drain_node_2", 0b111, 11, 3, ("s0",), "sig_s", auth_epoch=3)

        for _ in range(100):
            ledger = CrossRootExposureLedger({"db_cluster": 2})
            ledger.admit_and_reserve("k1", "op_1", "drain_node_1", "db_cluster", 1)
            ledger.admit_and_reserve("k2", "op_2", "drain_node_2", "db_cluster", 1)
            t0 = time.perf_counter_ns()
            target.reopen()
            rep = ledger.reconcile_after_restart(
                "db_cluster",
                target_committed_versions=target.durable_committed_versions(),
                fenced_no_effect_ops=target.durable_fenced_no_effect_ops(),
            )
            reopen_reconcile_samples.append(time.perf_counter_ns() - t0)
            assert rep["op_1"] == "RECONCILED_COMMITTED" and rep["op_2"] == "RETAINED_IN_IU_UNKNOWN"

            t1 = time.perf_counter_ns()
            rcpt = target.execute_fenced_mutation(cert_stale, target_node="node_2", op_id="op_stale")
            fenced_reject_samples.append(time.perf_counter_ns() - t1)
            assert not rcpt.committed and rcpt.status == "REJECTED_AUTHORIZATION_REVOKED"

        res["sqlite_wal_reopen_and_reconcile"] = _quantiles_ms(reopen_reconcile_samples)
        res["sqlite_wal_fenced_auth_rejection"] = _quantiles_ms(fenced_reject_samples)

    return res


def main() -> None:
    report = {
        "proof_dag_verification": bench_proof_dag_verification(),
        "set_packing": bench_set_packing(),
        "mode_s_and_mode_c": bench_mode_s_and_mode_c(),
        "unknown_order_and_crash_recovery": bench_unknown_order_and_crash_recovery(),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
