"""Deterministic POSIX process schedules against the real SQLite WAL adapter.

The control ledger is deliberately separate from target receipts: expiry is not
evidence that an external effect stopped. Pipes establish ordering, never sleeps.
"""
from __future__ import annotations

from dataclasses import asdict
import multiprocessing as mp
import os
import random
import signal
import sqlite3
import tempfile
import time

from pieq.core import EpistemicLeaseCertificate, SQLiteProtectedResourceBoundary

SCHEDULES = (
    "e1_unknown", "e2_after_prepare", "e2_after_target_read",
    "e2_before_effect", "e2_after_effect_before_ack", "mode_s",
    "simultaneous_drains", "duplicate_op_id", "worker_crash_restart",
    "worker_crash_unknown",
)
TIMEOUT = 10.0


def _event(pipe, name, **fields):
    pipe.send({"event": name, "timestamp_ns": time.monotonic_ns(),
               "pid": os.getpid(), **fields})


def _receive(pipe):
    if not pipe.poll(TIMEOUT):
        raise TimeoutError("process barrier timed out")
    message = pipe.recv()
    if message.get("event") == "error":
        raise RuntimeError(message["error"])
    return message


def _control(db, pipe, op, action, epoch, block_unknown=False):
    boundary = SQLiteProtectedResourceBoundary(db)
    try:
        boundary.conn.execute("BEGIN IMMEDIATE")
        blocked = block_unknown and boundary.conn.execute(
            "SELECT 1 FROM fault_ledger WHERE state='UNKNOWN' AND op_id != ?", (op,)
        ).fetchone()
        if blocked:
            boundary.conn.execute("COMMIT")
            _event(pipe, "blocked", status="BLOCKED_UNKNOWN", op_id=op)
            return
        boundary.conn.execute(
            "INSERT OR IGNORE INTO fault_ledger VALUES (?, 'PREPARED', ?, ?)",
            (op, epoch, time.monotonic_ns() + 500_000_000),
        )
        boundary.conn.execute("COMMIT")
        _event(pipe, "after_prepare", op_id=op)
        _receive(pipe)
        boundary.conn.execute(
            "UPDATE fault_ledger SET state='UNKNOWN' WHERE op_id=? AND state='PREPARED'", (op,)
        )
        _event(pipe, "dispatched", op_id=op)
        receipt = _receive(pipe)["receipt"]
        _event(pipe, "after_effect_before_ack", receipt=receipt, op_id=op)
        _receive(pipe)
        state = "COMMITTED" if receipt["committed"] else "FENCED_NO_EFFECT"
        boundary.conn.execute("UPDATE fault_ledger SET state=? WHERE op_id=?", (state, op))
        _event(pipe, "acknowledged", receipt=receipt, op_id=op)
    except Exception as exc:
        _event(pipe, "error", error=repr(exc))
    finally:
        boundary.conn.close()
        pipe.close()


def _target(db, pipe, op, action, epoch, node, tier, predecessors):
    boundary = SQLiteProtectedResourceBoundary(db)
    try:
        read_epoch = boundary.conn.execute(
            "SELECT min_valid_epoch FROM control_epochs"
        ).fetchone()[0]
        _event(pipe, "after_target_read", observed_epoch=read_epoch, op_id=op)
        _receive(pipe)
        _event(pipe, "before_effect", op_id=op)
        _receive(pipe)
        # E1+ models a target without epoch/lease enforcement. It retains the
        # target's atomic cap and idempotence checks but uses its current epoch.
        if tier == "E1+":
            epoch = boundary.conn.execute(
                "SELECT min_valid_epoch FROM control_epochs"
            ).fetchone()[0]
        cert = EpistemicLeaseCertificate(action, 1, epoch, 1, (), "fault-eval")
        receipt = boundary.execute_fenced_mutation(
            cert, target_node=node, op_id=op,
            required_predecessor_op_ids=predecessors,
        )
        if not receipt.committed and receipt.status == "REJECTED_STALE_EPOCH":
            boundary.record_fenced_no_effect(op, action)
        payload = asdict(receipt)
        payload["enforcement_tier"] = tier
        _event(pipe, "effect_completed", receipt=payload, op_id=op)
    except Exception as exc:
        _event(pipe, "error", error=repr(exc))
    finally:
        boundary.conn.close()
        pipe.close()


class _Harness:
    def __init__(self, db):
        # POSIX fork is intentional: no inherited SQLite connection is used.
        self.ctx = mp.get_context("fork")
        self.db = db
        self.children = []
        self.events = []
        self.receipts = []

    def spawn(self, function, *args):
        parent, child = self.ctx.Pipe()
        process = self.ctx.Process(target=function, args=(self.db, child, *args))
        process.start()
        child.close()
        self.children.append((process, parent))
        return process, parent

    def recv(self, pipe, expected):
        event = _receive(pipe)
        self.events.append(event)
        assert event["event"] == expected, event
        if event["event"] == "effect_completed":
            self.receipts.append(event["receipt"])
        return event

    def control(self, op, action="drain_original", epoch=10, block=False):
        worker = self.spawn(_control, op, action, epoch, block)
        self.recv(worker[1], "after_prepare")
        return worker

    def dispatch(self, worker, op, action="drain_original", epoch=10,
                 node="node_1", tier="E2", predecessors=()):
        worker[1].send({"event": "continue"})
        self.recv(worker[1], "dispatched")
        target = self.spawn(_target, op, action, epoch, node, tier, predecessors)
        self.recv(target[1], "after_target_read")
        return target

    def effect(self, target, read_released=False):
        if not read_released:
            target[1].send({"event": "continue"})
            self.recv(target[1], "before_effect")
        target[1].send({"event": "continue"})
        return self.recv(target[1], "effect_completed")["receipt"]

    def ack(self, worker, receipt):
        worker[1].send({"event": "receipt", "receipt": receipt})
        self.recv(worker[1], "after_effect_before_ack")
        worker[1].send({"event": "continue"})
        self.recv(worker[1], "acknowledged")

    def finish(self, worker, op, **kwargs):
        target = self.dispatch(worker, op, **kwargs)
        receipt = self.effect(target)
        self.ack(worker, receipt)
        return receipt

    def stop(self, worker):
        os.kill(worker[0].pid, signal.SIGSTOP)
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            pid, status = os.waitpid(worker[0].pid, os.WUNTRACED | os.WNOHANG)
            if pid and os.WIFSTOPPED(status):
                self.events.append({"event": "SIGSTOP", "pid": pid,
                                    "timestamp_ns": time.monotonic_ns()})
                return
        raise TimeoutError("SIGSTOP was not observed")

    def resume(self, worker):
        os.kill(worker[0].pid, signal.SIGCONT)
        self.events.append({"event": "SIGCONT", "pid": worker[0].pid,
                            "timestamp_ns": time.monotonic_ns()})

    def cleanup(self):
        for process, pipe in self.children:
            if process.is_alive():
                os.kill(process.pid, signal.SIGCONT)
            process.join(TIMEOUT)
            if process.is_alive():
                process.kill()
                process.join(TIMEOUT)
            pipe.close()
            assert not process.is_alive(), "child failed to terminate"


def _ledger(connection):
    return dict(connection.execute("SELECT op_id,state FROM fault_ledger ORDER BY op_id"))


def run_schedule(schedule, seed=0):
    """Run one fixed-barrier schedule, collecting receipts and durable final rows."""
    if os.name != "posix":
        raise RuntimeError("POSIX signals required")
    if schedule not in SCHEDULES:
        raise ValueError(schedule)
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="pieq-fault-") as directory:
        db = os.path.join(directory, "target.sqlite")
        boundary = SQLiteProtectedResourceBoundary(db)
        boundary.conn.execute(
            "CREATE TABLE fault_ledger (op_id TEXT PRIMARY KEY, state TEXT NOT NULL "
            "CHECK(state IN ('PREPARED','UNKNOWN','COMMITTED','FENCED_NO_EFFECT')), "
            "epoch INTEGER NOT NULL, lease_deadline_ns INTEGER NOT NULL)"
        )
        boundary.conn.close()
        harness = _Harness(db)
        connection = sqlite3.connect(db, isolation_level=None)
        pause = {}
        checks = {}
        liveness = False

        def revoke():
            fence = SQLiteProtectedResourceBoundary(db)
            fence.revoke_epoch_atomically(11)
            fence.conn.close()
            harness.events.append({"event": "revoke_epoch", "epoch": 11,
                                   "timestamp_ns": time.monotonic_ns(), "pid": os.getpid()})

        def reconcile():
            # Missing receipt remains UNKNOWN. Only durable target evidence settles.
            connection.execute(
                "UPDATE fault_ledger SET state=(SELECT status FROM committed_ops "
                "WHERE committed_ops.op_id=fault_ledger.op_id) "
                "WHERE op_id IN (SELECT op_id FROM committed_ops)"
            )
            harness.events.append({"event": "restart_reconciliation",
                                   "timestamp_ns": time.monotonic_ns(), "pid": os.getpid()})

        try:
            if schedule.startswith("e2_") or schedule in (
                "e1_unknown", "worker_crash_restart", "worker_crash_unknown"
            ):
                original = harness.control("original")
                target = None
                if schedule != "e2_after_prepare":
                    target = harness.dispatch(
                        original, "original", tier="E1+" if schedule == "e1_unknown" else "E2"
                    )
                if schedule == "e2_before_effect":
                    target[1].send({"event": "continue"})
                    harness.recv(target[1], "before_effect")
                receipt = None
                if schedule in ("e2_after_effect_before_ack", "worker_crash_restart"):
                    receipt = harness.effect(target)
                    original[1].send({"event": "receipt", "receipt": receipt})
                    harness.recv(original[1], "after_effect_before_ack")
                harness.stop(original)
                revoke()
                deadline = connection.execute(
                    "SELECT lease_deadline_ns FROM fault_ledger WHERE op_id='original'"
                ).fetchone()[0]
                stop_timestamp = next(
                    event["timestamp_ns"] for event in reversed(harness.events)
                    if event["event"] == "SIGSTOP"
                )
                pause["stop_timestamp_ns"] = stop_timestamp
                pause["lease_deadline_ns"] = deadline
                pause["stop_preceded_lease_expiry"] = stop_timestamp < deadline
                if schedule in ("e1_unknown", "worker_crash_unknown"):
                    if not pause["stop_preceded_lease_expiry"]:
                        raise AssertionError("lease expired before the SIGSTOP barrier")
                    while time.monotonic_ns() <= deadline:
                        time.sleep(min(0.01, max(0, (deadline - time.monotonic_ns()) / 1e9)))
                    pause["lease_expiry_mode"] = "elapsed_while_stopped"
                else:
                    pause["lease_expiry_mode"] = "not_waited"
                pause["observed_expiry_ns"] = time.monotonic_ns()
                pause["ledger"] = _ledger(connection)
                pause["lease_expired"] = deadline < pause["observed_expiry_ns"]
                if schedule == "e1_unknown":
                    conflict = harness.spawn(_control, "conflict", "drain_conflict", 11, True)
                    pause["conflict_status"] = harness.recv(conflict[1], "blocked")["status"]
                    receipt = harness.effect(target)
                    pause["target_completed_while_control_stopped"] = receipt["committed"]
                    liveness = True
                    checks["unknown_blocks_after_expiry"] = (
                        pause["ledger"]["original"] == "UNKNOWN"
                        and pause["conflict_status"] == "BLOCKED_UNKNOWN"
                        and pause["target_completed_while_control_stopped"]
                    )
                    harness.resume(original)
                    harness.ack(original, receipt)
                elif schedule in ("worker_crash_restart", "worker_crash_unknown"):
                    original[0].kill()
                    original[0].join(TIMEOUT)
                    reconcile()
                    if schedule == "worker_crash_unknown":
                        pause["restart_state"] = _ledger(connection)["original"]
                        conflict = harness.spawn(_control, "conflict", "drain_conflict", 11, True)
                        pause["conflict_status"] = harness.recv(conflict[1], "blocked")["status"]
                        checks["crash_retains_unknown"] = (
                            pause["restart_state"] == "UNKNOWN"
                            and pause["conflict_status"] == "BLOCKED_UNKNOWN"
                        )
                        receipt = harness.effect(target)
                        reconcile()
                        checks["restart_recovers_fenced"] = (
                            _ledger(connection)["original"] == "FENCED_NO_EFFECT"
                            and receipt["status"] == "REJECTED_STALE_EPOCH"
                        )
                        liveness = True
                    else:
                        restart = harness.control("original")
                        replay = harness.finish(restart, "original", epoch=10)
                        checks["restart_recovers_committed"] = (
                            _ledger(connection)["original"] == "COMMITTED"
                            and replay["status"] == "DUPLICATE_IDEMPOTENT_COMMITTED"
                        )
                else:
                    conflict = harness.control("conflict", "drain_conflict", 11)
                    harness.finish(conflict, "conflict", action="drain_conflict", epoch=11, node="node_2")
                    harness.resume(original)
                    if schedule == "e2_after_effect_before_ack":
                        original[1].send({"event": "continue"})
                        harness.recv(original[1], "acknowledged")
                        checks["effect_precedes_revoke"] = receipt["committed"]
                    else:
                        if target is None:
                            target = harness.dispatch(original, "original")
                        receipt = harness.effect(target, read_released=schedule == "e2_before_effect")
                        harness.ack(original, receipt)
                        checks["stale_zero_effect"] = (
                            receipt["status"] == "REJECTED_STALE_EPOCH"
                            and not connection.execute(
                                "SELECT 1 FROM cluster_nodes WHERE node_id='node_1' AND status='DRAINED'"
                            ).fetchone()
                        )
            elif schedule == "mode_s":
                second = harness.control("successor", "noop_successor")
                rejected = harness.finish(second, "successor", action="noop_successor",
                                          predecessors=("predecessor_a", "predecessor_b"))
                for op in ("predecessor_a", "predecessor_b"):
                    worker = harness.control(op, "noop")
                    harness.finish(worker, op, action="noop")
                    if op == "predecessor_a":
                        partial = harness.control("successor", "noop_successor")
                        partial_receipt = harness.finish(
                            partial, "successor", action="noop_successor",
                            predecessors=("predecessor_a", "predecessor_b"),
                        )
                successor = harness.control("successor", "noop_successor")
                accepted = harness.finish(successor, "successor", action="noop_successor",
                                          predecessors=("predecessor_a", "predecessor_b"))
                checks["all_predecessors_required"] = (
                    rejected["status"] == "REJECTED_MISSING_PREDECESSOR_FRONTIER"
                    and partial_receipt["status"] == "REJECTED_MISSING_PREDECESSOR_FRONTIER"
                    and accepted["committed"]
                )
            else:
                duplicate = schedule == "duplicate_op_id"
                first = harness.control("shared" if duplicate else "a")
                second = harness.control("shared" if duplicate else "b")
                targets = [
                    harness.dispatch(first, "shared" if duplicate else "a"),
                    harness.dispatch(second, "shared" if duplicate else "b",
                                     node="node_1" if duplicate else "node_2"),
                ]
                for target in targets:
                    target[1].send({"event": "continue"})
                    harness.recv(target[1], "before_effect")
                order = [0, 1]
                rng.shuffle(order)
                for index in order:
                    targets[index][1].send({"event": "continue"})
                receipts = [harness.recv(target[1], "effect_completed")["receipt"] for target in targets]
                for worker, receipt in zip((first, second), receipts):
                    harness.ack(worker, receipt)
                if duplicate:
                    checks["duplicate_has_one_effect"] = sorted(r["status"] for r in receipts) == [
                        "COMMITTED", "DUPLICATE_IDEMPOTENT_COMMITTED"]
                else:
                    checks["atomic_cap_serializes_race"] = sorted(r["status"] for r in receipts) == [
                        "COMMITTED", "REJECTED_EXPOSURE_CAP"]
            reconcile()
            tables = ("cluster_nodes", "control_epochs", "committed_ops", "audit_journal", "fault_ledger")
            rows = {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in tables}
            checks["drain_cap"] = sum(row[1] == "DRAINED" for row in rows["cluster_nodes"]) <= 1
            checks["unique_committed_ids"] = len(rows["committed_ops"]) == len(
                {row[0] for row in rows["committed_ops"]})
            checks["wal_integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            checks["all_passed"] = all(checks.values())
        finally:
            connection.close()
            harness.cleanup()
        return {
            "schedule": schedule, "seed": seed,
            "evidence_scope": "multi-process single-host SQLite",
            "events": harness.events, "receipts": harness.receipts,
            "rows": rows, "checks": checks, "pause_snapshot": pause,
            "liveness_lost_to_unknown": liveness, "processes_reaped": True,
        }
