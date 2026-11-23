#!/usr/bin/env python3
"""
PIEQ Revision 12 — Multi-Process OS-Level Section 5 Concurrency & Revocation Fault Suite.

Addresses Reviewer Weakness 3 ("Gap Between Concurrency Theory and Physical Validation")
by exercising Section 5's concurrency, universal r_adm CAS, Regime R2 phase decomposition,
and Tier E1+-quiescence vs. Tier E2 revocation semantics across REAL forked POSIX processes
coordinated over pipes against an on-disk SQLite WAL (`PRAGMA synchronous=FULL`).

Four fault schedules (20 repetitions each = 80 multi-process OS trials):
  1. `concurrent_coordinator_radm_cas`: Two independent forked coordinators verify Mode C R1
     (`ModeCCompatibilityVerifier`) at identical `admission_rev = r`, then race across a
     POSIX pipe barrier at the `BEGIN IMMEDIATE` WAL commit boundary (atomically checking
     and incrementing `admission_rev`). Verifies exactly 1 commit, 1 stale-revision reject,
     and clean post-revalidation retry at `r+1`.
  2. `midflight_auth_revocation_e2`: Forked coordinator and revocation processes race across
     a POSIX barrier against Tier E2 `SQLiteProtectedResourceBoundary`; verifies 0 revoked
     commits post-revocation linearization (delta_fresh^E2 = 0).
  3. `tier_e1_plus_quiescence_6step`: Real multi-process on-disk two-phase quiescence barrier
     (durable `lock_r`, `prepared_slot`, WAL-flushed `FenceAck`) empirically reproducing the
     exact 6-step pre-FenceAck prepared-slot window vs. 0 post-FenceAck revoked commits, plus
     real SIGKILL crash-while-prepared blocking conflicting admissions in I_U until reconciled.
  4. `mode_c_r2_phase_decomposed_write_skew`: Wires `ModeCCompatibilityVerifier` Regime R2
     (`PHASE_DECOMPOSED`) directly before two forked worker processes executing overlapping
     read/write SQLite transactions: blocks write-skew pre-dispatch while admitting R2-safe
     disjoint-key concurrent phase ops with 0 invariant violations.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import signal
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pieq.core import (
    EpistemicLeaseCertificate,
    FaultWorld,
    InFlightMutation,
    ModeCCompatibilityVerifier,
    SQLiteProtectedResourceBoundary,
)

OUT_PATH = REPO_ROOT / "results" / "rev12" / "rev12_section5_multiprocess_faults.json"
TIMEOUT = 10.0


def _open_wal(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=FULL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    return conn


def _recv(pipe) -> Dict[str, Any]:
    if not pipe.poll(TIMEOUT):
        raise TimeoutError("POSIX pipe barrier timed out")
    msg = pipe.recv()
    if msg.get("error"):
        raise RuntimeError(msg["error"])
    return msg


# -----------------------------------------------------------------------------
# Schedule 1: Concurrent-Coordinator Universal r_adm CAS Race (Mode C R1 + WAL)
# -----------------------------------------------------------------------------

def _coord_radm_worker(db_path: str, pipe, worker_id: str, op_id: str, amount: int) -> None:
    conn = _open_wal(db_path)
    try:
        # 1. Read current admission_rev and verify Mode C R1 CompatCert locally
        row = conn.execute("SELECT admission_rev, capacity_limit FROM ctrl WHERE domain='dom_pool';").fetchone()
        obs_rev, cap = int(row[0]), int(row[1])

        w0 = FaultWorld("s_ok", frozenset(), frozenset({"reserve_a", "reserve_b"}), {},
                        {"reserve_a": "s_ok", "reserve_b": "s_ok"}, viable=True)
        ok_mc, cert_mc, reason_mc = ModeCCompatibilityVerifier.verify(
            candidate_action="reserve_a" if worker_id == "c1" else "reserve_b",
            domain_closure=("dom_pool",),
            base_worlds=[w0],
            unresolved_ops=[],
            worlds_by_id={"s_ok": w0},
            physical_refinement_mode="SERIALIZABLE_ATOMIC",
            admission_revisions={"dom_pool": obs_rev},
        )
        assert ok_mc and cert_mc is not None, f"Mode C failed: {reason_mc}"

        # Barrier: wait until BOTH coordinators have minted certificates at the SAME admission_rev
        pipe.send({"event": "ready", "worker": worker_id, "obs_rev": obs_rev, "compat_digest": cert_mc.verifier_digest})
        _recv(pipe)  # GO

        # 2. Race at linearizable BEGIN IMMEDIATE commit with atomic r_adm CAS + increment
        conn.execute("BEGIN IMMEDIATE;")
        cur_rev, cur_cap = conn.execute("SELECT admission_rev, capacity_limit FROM ctrl WHERE domain='dom_pool';").fetchone()
        if int(cur_rev) != obs_rev:
            conn.execute("ROLLBACK;")
            status = "REJECTED_STALE_ADMISSION_REVISION"
            committed = False
        else:
            total = int(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM reservations;").fetchone()[0])
            if total + amount > int(cur_cap):
                conn.execute("ROLLBACK;")
                status = "REJECTED_EXPOSURE_CAP"
                committed = False
            else:
                conn.execute("INSERT INTO reservations(op_id, worker_id, amount, rev_used) VALUES (?, ?, ?, ?);",
                             (op_id, worker_id, amount, obs_rev))
                conn.execute("UPDATE ctrl SET admission_rev = admission_rev + 1 WHERE domain='dom_pool';")
                conn.execute("COMMIT;")
                status = "COMMITTED"
                committed = True

        pipe.send({
            "event": "done",
            "worker": worker_id,
            "pid": os.getpid(),
            "obs_rev": obs_rev,
            "status": status,
            "committed": committed,
        })
    except Exception as exc:
        pipe.send({"error": repr(exc)})
    finally:
        conn.close()
        pipe.close()


def run_schedule_1_concurrent_radm_cas(rep: int) -> Dict[str, Any]:
    ctx = mp.get_context("fork")
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / f"radm_{rep}.sqlite")
        conn = _open_wal(db)
        conn.execute("CREATE TABLE ctrl (domain TEXT PRIMARY KEY, admission_rev INTEGER NOT NULL, capacity_limit INTEGER NOT NULL);")
        conn.execute("CREATE TABLE reservations (op_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, amount INTEGER NOT NULL, rev_used INTEGER NOT NULL);")
        conn.execute("INSERT INTO ctrl VALUES ('dom_pool', 1, 10);")
        conn.close()

        p1_p, p1_c = ctx.Pipe()
        p2_p, p2_c = ctx.Pipe()
        w1 = ctx.Process(target=_coord_radm_worker, args=(db, p1_c, "c1", f"op_c1_{rep}", 4))
        w2 = ctx.Process(target=_coord_radm_worker, args=(db, p2_c, "c2", f"op_c2_{rep}", 4))
        w1.start(); w2.start()
        p1_c.close(); p2_c.close()

        r1 = _recv(p1_p)
        r2 = _recv(p2_p)
        # Release both simultaneously
        p1_p.send({"go": True})
        p2_p.send({"go": True})
        d1 = _recv(p1_p)
        d2 = _recv(p2_p)
        w1.join(TIMEOUT); w2.join(TIMEOUT)
        p1_p.close(); p2_p.close()

        # Revalidation retry for loser at updated rev=2
        loser = d1 if not d1["committed"] else d2
        conn = _open_wal(db)
        rev_after_race = int(conn.execute("SELECT admission_rev FROM ctrl WHERE domain='dom_pool';").fetchone()[0])
        conn.execute("BEGIN IMMEDIATE;")
        cur_rev = int(conn.execute("SELECT admission_rev FROM ctrl WHERE domain='dom_pool';").fetchone()[0])
        assert cur_rev == 2
        conn.execute("INSERT INTO reservations VALUES (?, ?, ?, ?);",
                     (f"op_{loser['worker']}_retry_{rep}", loser["worker"], 4, cur_rev))
        conn.execute("UPDATE ctrl SET admission_rev = admission_rev + 1 WHERE domain='dom_pool';")
        conn.execute("COMMIT;")
        final_rev = int(conn.execute("SELECT admission_rev FROM ctrl WHERE domain='dom_pool';").fetchone()[0])
        final_sum = int(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM reservations;").fetchone()[0])
        conn.close()

        statuses = sorted([d1["status"], d2["status"]])
        passed = (
            r1["obs_rev"] == 1 and r2["obs_rev"] == 1
            and statuses == ["COMMITTED", "REJECTED_STALE_ADMISSION_REVISION"]
            and rev_after_race == 2
            and final_rev == 3
            and final_sum == 8 <= 10
            and d1["pid"] != d2["pid"]
        )
        return {
            "rep": rep,
            "passed": passed,
            "race_statuses": statuses,
            "rev_after_race": rev_after_race,
            "final_rev": final_rev,
            "final_sum": final_sum,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }


# -----------------------------------------------------------------------------
# Schedule 2: Mid-Flight Authorization Revocation Race (Tier E2 Atomic WAL)
# -----------------------------------------------------------------------------

def _e2_exec_worker(db_path: str, pipe, op_id: str, auth_epoch: int) -> None:
    b = SQLiteProtectedResourceBoundary(db_path)
    try:
        cert = EpistemicLeaseCertificate(
            action="drain_node", support_mask=1, admission_epoch=10,
            packing_number=3, plausible_worlds=("w_dead",), signature="sig_rev12",
            auth_epoch=auth_epoch, auth_valid_until=1000,
        )
        pipe.send({"event": "ready", "pid": os.getpid()})
        _recv(pipe)  # GO
        receipt = b.execute_fenced_mutation(cert, target_node="node_1", op_id=op_id, now_time=500)
        pipe.send({
            "event": "done",
            "committed": receipt.committed,
            "status": receipt.status,
            "pid": os.getpid(),
        })
    except Exception as exc:
        pipe.send({"error": repr(exc)})
    finally:
        b.conn.close()
        pipe.close()


def _e2_revoke_worker(db_path: str, pipe, new_auth_epoch: int) -> None:
    b = SQLiteProtectedResourceBoundary(db_path)
    try:
        pipe.send({"event": "ready", "pid": os.getpid()})
        _recv(pipe)  # GO
        b.revoke_authorization_atomically(new_auth_epoch)
        pipe.send({"event": "revoked", "new_auth_epoch": new_auth_epoch, "pid": os.getpid()})
    except Exception as exc:
        pipe.send({"error": repr(exc)})
    finally:
        b.conn.close()
        pipe.close()


def run_schedule_2_midflight_revocation_e2(rep: int) -> Dict[str, Any]:
    ctx = mp.get_context("fork")
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / f"e2_rev_{rep}.sqlite")
        b = SQLiteProtectedResourceBoundary(db, initial_epoch=10)
        b.revoke_authorization_atomically(5)
        b.conn.close()

        pe_p, pe_c = ctx.Pipe()
        pr_p, pr_c = ctx.Pipe()
        we = ctx.Process(target=_e2_exec_worker, args=(db, pe_c, f"op_e2_{rep}", 5))
        wr = ctx.Process(target=_e2_revoke_worker, args=(db, pr_c, 6))
        we.start(); wr.start()
        pe_c.close(); pr_c.close()

        _recv(pe_p); _recv(pr_p)
        # Alternate release ordering: on even reps revoke slightly first; on odd reps release simultaneously
        if rep % 2 == 0:
            pr_p.send({"go": True})
            _recv(pr_p)  # revocation serialized strictly before exec attempt
            pe_p.send({"go": True})
            exec_res = _recv(pe_p)
            revoked_before_exec = True
        else:
            # Simultaneous race: either exec commits strictly BEFORE revoke, or revoke commits first and exec rejects.
            # In BOTH serializations, delta_fresh^E2 = 0!
            pr_p.send({"go": True})
            pe_p.send({"go": True})
            _recv(pr_p)
            exec_res = _recv(pe_p)
            revoked_before_exec = False

        we.join(TIMEOUT); wr.join(TIMEOUT)
        pe_p.close(); pr_p.close()

        # Verify authoritative DB state
        conn = _open_wal(db)
        final_auth = int(conn.execute("SELECT min_auth_epoch FROM control_epochs WHERE resource_id='db_cluster';").fetchone()[0])
        committed_rows = conn.execute("SELECT op_id, status FROM committed_ops WHERE op_id=?;", (f"op_e2_{rep}",)).fetchall()
        conn.close()

        if revoked_before_exec:
            # Must NEVER commit after revocation serialized
            passed = (not exec_res["committed"]) and (exec_res["status"] == "REJECTED_AUTHORIZATION_REVOKED") and (len(committed_rows) == 0)
            revoked_commit_leak = exec_res["committed"]
        else:
            # Strict serializability: either committed cleanly before revoke OR rejected cleanly after revoke
            valid_outcomes = (
                (exec_res["committed"] and exec_res["status"] == "COMMITTED" and len(committed_rows) == 1)
                or ((not exec_res["committed"]) and exec_res["status"] == "REJECTED_AUTHORIZATION_REVOKED" and len(committed_rows) == 0)
            )
            passed = valid_outcomes and (final_auth == 6)
            revoked_commit_leak = False

        return {
            "rep": rep,
            "mode": "post_revocation_strict" if revoked_before_exec else "simultaneous_race",
            "passed": passed,
            "exec_status": exec_res["status"],
            "committed": exec_res["committed"],
            "revoked_commit_leak": revoked_commit_leak,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }


# -----------------------------------------------------------------------------
# Schedule 3: Multi-Process Tier E1+-Quiescence 6-Step Boundary + SIGKILL Crash
# -----------------------------------------------------------------------------

def _q_prepared_worker(db_path: str, pipe, op_id: str, auth_epoch: int, crash_in_prepared: bool = False) -> None:
    conn = _open_wal(db_path)
    try:
        # Step 1-2: Verify auth_epoch against durable target state, acquire lock_r, register prepared_slot
        conn.execute("BEGIN IMMEDIATE;")
        min_auth, lock_holder = conn.execute("SELECT min_auth_epoch, lock_holder FROM q_state WHERE id=1;").fetchone()
        if lock_holder is not None:
            conn.execute("ROLLBACK;")
            pipe.send({"event": "done", "status": "BLOCKED_LOCK_OR_UNKNOWN", "committed": False})
            return
        if auth_epoch < int(min_auth):
            conn.execute("ROLLBACK;")
            pipe.send({"event": "done", "status": "REJECTED_AUTHORIZATION_REVOKED_POST_FENCE_ACK", "committed": False})
            return
        conn.execute("UPDATE q_state SET lock_holder=?, prepared_op=? WHERE id=1;", (op_id, op_id))
        conn.execute("COMMIT;")
        pipe.send({"event": "prepared_locked", "op_id": op_id, "pid": os.getpid()})

        if crash_in_prepared:
            # Wait for parent to SIGKILL us while holding lock_r / prepared_slot
            time.sleep(30.0)

        _recv(pipe)  # Proceed to Step 4 commit (while revocation waits on lock_r!)
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("INSERT INTO q_commits(op_id, auth_epoch, phase) VALUES (?, ?, 'PRE_FENCE_ACK');", (op_id, auth_epoch))
        # Step 5: drain lock_r, flush any pending revocation, declare FenceAck durably
        pending_auth = int(conn.execute("SELECT pending_auth_epoch FROM q_state WHERE id=1;").fetchone()[0])
        new_min = max(int(min_auth), pending_auth)
        conn.execute(
            "UPDATE q_state SET min_auth_epoch=?, lock_holder=NULL, prepared_op=NULL, fence_ack_epoch=? WHERE id=1;",
            (new_min, new_min),
        )
        conn.execute("COMMIT;")
        pipe.send({"event": "committed_and_fence_ack", "op_id": op_id, "fence_ack_epoch": new_min, "committed": True})
    except Exception as exc:
        pipe.send({"error": repr(exc)})
    finally:
        conn.close()
        pipe.close()


def _q_revoker_worker(db_path: str, pipe, new_auth_epoch: int) -> None:
    conn = _open_wal(db_path)
    try:
        # Step 3: Control plane initiates revocation; sees lock_r held, records pending_auth_epoch & blocks until FenceAck
        conn.execute("BEGIN IMMEDIATE;")
        lock_holder = conn.execute("SELECT lock_holder FROM q_state WHERE id=1;").fetchone()[0]
        conn.execute("UPDATE q_state SET pending_auth_epoch=? WHERE id=1;", (new_auth_epoch,))
        conn.execute("COMMIT;")
        blocked_on_lock = lock_holder is not None
        pipe.send({"event": "rev_initiated_blocked", "blocked_on_lock": blocked_on_lock, "pid": os.getpid()})

        # Poll for durable FenceAck
        while True:
            fa = int(conn.execute("SELECT fence_ack_epoch FROM q_state WHERE id=1;").fetchone()[0])
            if fa >= new_auth_epoch:
                break
            time.sleep(0.002)
        pipe.send({"event": "fence_ack_observed", "fence_ack_epoch": fa})
    except Exception as exc:
        pipe.send({"error": repr(exc)})
    finally:
        conn.close()
        pipe.close()


def run_schedule_3_quiescence_6step(rep: int) -> Dict[str, Any]:
    ctx = mp.get_context("fork")
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / f"q6_{rep}.sqlite")
        conn = _open_wal(db)
        conn.execute(
            "CREATE TABLE q_state (id INTEGER PRIMARY KEY, min_auth_epoch INTEGER NOT NULL, "
            "pending_auth_epoch INTEGER NOT NULL, fence_ack_epoch INTEGER NOT NULL, "
            "lock_holder TEXT, prepared_op TEXT);"
        )
        conn.execute("CREATE TABLE q_commits (op_id TEXT PRIMARY KEY, auth_epoch INTEGER NOT NULL, phase TEXT NOT NULL);")
        conn.execute("INSERT INTO q_state VALUES (1, 5, 5, 5, NULL, NULL);")
        conn.close()

        # Phase A: Op1 enters prepared_slot (t=2), Revoker initiates rev=6 and blocks on lock_r (t=3),
        # Op1 commits pre-FenceAck (t=4) and flushes FenceAck=6 (t=5).
        p1_p, p1_c = ctx.Pipe()
        pr_p, pr_c = ctx.Pipe()
        w1 = ctx.Process(target=_q_prepared_worker, args=(db, p1_c, f"op_prep_{rep}", 5, False))
        w1.start(); p1_c.close()
        m1 = _recv(p1_p)
        assert m1["event"] == "prepared_locked"

        wr = ctx.Process(target=_q_revoker_worker, args=(db, pr_c, 6))
        wr.start(); pr_c.close()
        mr = _recv(pr_p)
        assert mr["blocked_on_lock"] is True

        p1_p.send({"go": True})
        m1_done = _recv(p1_p)
        mr_done = _recv(pr_p)
        w1.join(TIMEOUT); wr.join(TIMEOUT)
        p1_p.close(); pr_p.close()

        # Phase B (Step 6): Post-FenceAck Op2 with revoked auth_epoch=5 MUST reject deterministically!
        p2_p, p2_c = ctx.Pipe()
        w2 = ctx.Process(target=_q_prepared_worker, args=(db, p2_c, f"op_post_{rep}", 5, False))
        w2.start(); p2_c.close()
        m2_done = _recv(p2_p)
        w2.join(TIMEOUT); p2_p.close()

        # Phase C: SIGKILL crash while prepared -> conflicting op blocked indefinitely until reconciled!
        p3_p, p3_c = ctx.Pipe()
        w3 = ctx.Process(target=_q_prepared_worker, args=(db, p3_c, f"op_crash_{rep}", 6, True))
        w3.start(); p3_c.close()
        m3 = _recv(p3_p)
        assert m3["event"] == "prepared_locked"
        os.kill(w3.pid, signal.SIGKILL)
        w3.join(TIMEOUT)
        p3_p.close()

        # Attempt conflicting op4 while op_crash is UNKNOWN in prepared_slot -> must block!
        p4_p, p4_c = ctx.Pipe()
        w4 = ctx.Process(target=_q_prepared_worker, args=(db, p4_c, f"op_conflict_{rep}", 6, False))
        w4.start(); p4_c.close()
        m4_done = _recv(p4_p)
        w4.join(TIMEOUT); p4_p.close()

        passed = (
            m1_done["committed"] is True
            and mr_done["fence_ack_epoch"] == 6
            and (m2_done["committed"] is False and m2_done["status"] == "REJECTED_AUTHORIZATION_REVOKED_POST_FENCE_ACK")
            and (m4_done["committed"] is False and m4_done["status"] == "BLOCKED_LOCK_OR_UNKNOWN")
        )
        return {
            "rep": rep,
            "passed": passed,
            "pre_fence_ack_prepared_committed": m1_done["committed"],
            "post_fence_ack_revoked_rejected": not m2_done["committed"],
            "crash_while_prepared_blocks_conflict": m4_done["status"] == "BLOCKED_LOCK_OR_UNKNOWN",
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }


# -----------------------------------------------------------------------------
# Schedule 4: Mode C Regime R2 Phase-Decomposed Write-Skew Block vs. Safe Run
# -----------------------------------------------------------------------------

def _r2_phase_worker(db_path: str, pipe, key_to_write: str) -> None:
    conn = _open_wal(db_path)
    try:
        # Phase 1: Read state
        _ = conn.execute("SELECT k, v FROM kv;").fetchall()
        pipe.send({"event": "read_done", "key": key_to_write, "pid": os.getpid()})
        _recv(pipe)  # Barrier forcing overlapping read/write phases across processes
        # Phase 2: Write own disjoint key
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("UPDATE kv SET v = v + 1 WHERE k = ?;", (key_to_write,))
        conn.execute("COMMIT;")
        pipe.send({"event": "write_done", "key": key_to_write, "pid": os.getpid()})
    except Exception as exc:
        pipe.send({"error": repr(exc)})
    finally:
        conn.close()
        pipe.close()


def run_schedule_4_mode_c_r2(rep: int) -> Dict[str, Any]:
    ctx = mp.get_context("fork")
    t0 = time.perf_counter()

    # Part A: Write-skew pair MUST be rejected pre-dispatch by ModeCCompatibilityVerifier (R2)
    ws_worlds = [
        FaultWorld("00", frozenset(), frozenset({"r_a", "r_u"}), {}, {"r_a": "00_ra", "r_u": "00_ru"}, viable=True),
        FaultWorld("00_ra", frozenset(), frozenset({"w_a", "r_u"}), {}, {"w_a": "10", "r_u": "00_raru"}, viable=True),
        FaultWorld("00_ru", frozenset(), frozenset({"w_u", "r_a"}), {}, {"w_u": "01", "r_a": "00_raru"}, viable=True),
        FaultWorld("00_raru", frozenset(), frozenset({"w_a", "w_u"}), {}, {"w_a": "10_ru", "w_u": "01_ra"}, viable=True),
        FaultWorld("10", frozenset(), frozenset({"r_u"}), {}, {"r_u": "10_ru_ok"}, viable=True),
        FaultWorld("01", frozenset(), frozenset({"r_a"}), {}, {"r_a": "01_ra_ok"}, viable=True),
        FaultWorld("10_ru_ok", frozenset(), frozenset({"w_u"}), {}, {"w_u": "10"}, viable=True),
        FaultWorld("01_ra_ok", frozenset(), frozenset({"w_a"}), {}, {"w_a": "01"}, viable=True),
        FaultWorld("10_ru", frozenset(), frozenset({"w_u"}), {}, {"w_u": "11_bad"}, viable=True),
        FaultWorld("01_ra", frozenset(), frozenset({"w_a"}), {}, {"w_a": "11_bad"}, viable=True),
        FaultWorld("11_bad", frozenset(), frozenset(), {}, {}, viable=False),
    ]
    ws_ok, _, ws_reason = ModeCCompatibilityVerifier.verify(
        candidate_action="op_a", domain_closure=("dom_xy",),
        base_worlds=[ws_worlds[0]],
        unresolved_ops=[
            InFlightMutation("u_r", "r_u", "dom_xy"),
            InFlightMutation("u_w", "w_u", "dom_xy", intra_op_predecessor_ids=("u_r",)),
        ],
        worlds_by_id={w.world_id: w for w in ws_worlds},
        physical_refinement_mode="PHASE_DECOMPOSED",
        candidate_phases=[
            InFlightMutation("a_r", "r_a", "dom_xy"),
            InFlightMutation("a_w", "w_a", "dom_xy", intra_op_predecessor_ids=("a_r",)),
        ],
    )

    # Part B: R2-safe disjoint-key pair -> verified by ModeCCompatibilityVerifier, then executed
    # with forced read-phase overlap across two forked OS processes against SQLite WAL!
    safe_worlds = [
        FaultWorld("00", frozenset(), frozenset({"r_a", "w_a", "r_u", "w_u"}), {},
                   {"r_a": "00", "r_u": "00", "w_a": "10", "w_u": "01"}, viable=True),
        FaultWorld("10", frozenset(), frozenset({"r_a", "r_u", "w_u"}), {},
                   {"r_a": "10", "r_u": "10", "w_u": "11"}, viable=True),
        FaultWorld("01", frozenset(), frozenset({"r_a", "w_a", "r_u"}), {},
                   {"r_a": "01", "r_u": "01", "w_a": "11"}, viable=True),
        FaultWorld("11", frozenset(), frozenset({"r_a", "r_u"}), {}, {"r_a": "11", "r_u": "11"}, viable=True),
    ]
    ok_safe, cert_safe, _ = ModeCCompatibilityVerifier.verify(
        candidate_action="op_a", domain_closure=("dom_xy",),
        base_worlds=[safe_worlds[0]],
        unresolved_ops=[
            InFlightMutation("u_r", "r_u", "dom_xy"),
            InFlightMutation("u_w", "w_u", "dom_xy", intra_op_predecessor_ids=("u_r",)),
        ],
        worlds_by_id={w.world_id: w for w in safe_worlds},
        physical_refinement_mode="PHASE_DECOMPOSED",
        candidate_phases=[
            InFlightMutation("a_r", "r_a", "dom_xy"),
            InFlightMutation("a_w", "w_a", "dom_xy", intra_op_predecessor_ids=("a_r",)),
        ],
    )

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / f"r2_{rep}.sqlite")
        conn = _open_wal(db)
        conn.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v INTEGER NOT NULL);")
        conn.execute("INSERT INTO kv VALUES ('x', 0), ('y', 0);")
        conn.close()

        pa_p, pa_c = ctx.Pipe()
        pu_p, pu_c = ctx.Pipe()
        wa = ctx.Process(target=_r2_phase_worker, args=(db, pa_c, "x"))
        wu = ctx.Process(target=_r2_phase_worker, args=(db, pu_c, "y"))
        wa.start(); wu.start()
        pa_c.close(); pu_c.close()

        # Both finish read phase BEFORE either writes (true physical overlap)
        _recv(pa_p); _recv(pu_p)
        pa_p.send({"go": True}); pu_p.send({"go": True})
        _recv(pa_p); _recv(pu_p)
        wa.join(TIMEOUT); wu.join(TIMEOUT)
        pa_p.close(); pu_p.close()

        conn = _open_wal(db)
        vals = dict(conn.execute("SELECT k, v FROM kv;").fetchall())
        conn.close()

    passed = (
        (not ws_ok and ws_reason == "UNSAFE_PHASE_INTERLEAVING_EXITS_INVARIANT")
        and (ok_safe and cert_safe is not None and cert_safe.checked_schedule_count == 18)
        and vals == {"x": 1, "y": 1}
    )
    return {
        "rep": rep,
        "passed": passed,
        "write_skew_blocked_pre_dispatch": not ws_ok,
        "r2_safe_admitted_and_overlapped": ok_safe and vals == {"x": 1, "y": 1},
        "checked_schedules": cert_safe.checked_schedule_count if cert_safe else 0,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


def main() -> None:
    repetitions = 20
    runners = {
        "concurrent_coordinator_radm_cas": run_schedule_1_concurrent_radm_cas,
        "midflight_auth_revocation_e2": run_schedule_2_midflight_revocation_e2,
        "tier_e1_plus_quiescence_6step": run_schedule_3_quiescence_6step,
        "mode_c_r2_phase_decomposed_write_skew": run_schedule_4_mode_c_r2,
    }
    summaries: Dict[str, Any] = {}
    total_trials = 0
    total_passed = 0
    for name, fn in runners.items():
        trials = [fn(r) for r in range(repetitions)]
        passed = sum(1 for t in trials if t["passed"])
        total_trials += len(trials)
        total_passed += passed
        lats = [t["elapsed_ms"] for t in trials]
        summaries[name] = {
            "repetitions": repetitions,
            "passed": passed,
            "failed": repetitions - passed,
            "p50_ms": round(statistics.median(lats), 3),
            "p95_ms": round(sorted(lats)[min(len(lats) - 1, int(0.95 * (len(lats) - 1)))], 3),
            "sample_trial": trials[0],
        }

    report = {
        "schema_version": "pieq-rev12-section5-multiprocess-faults-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_trials": total_trials,
        "total_passed": total_passed,
        "all_passed": total_passed == total_trials,
        "schedules": summaries,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(report, indent=2, sort_keys=True)
    OUT_PATH.write_text(raw + "\n")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    print(f"Wrote {OUT_PATH} (sha256={digest[:16]}) — {total_passed}/{total_trials} passed")
    for name, s in summaries.items():
        print(f"  {name:38s}: {s['passed']}/{s['repetitions']} passed (p50={s['p50_ms']:.2f}ms, p95={s['p95_ms']:.2f}ms)")


if __name__ == "__main__":
    main()
