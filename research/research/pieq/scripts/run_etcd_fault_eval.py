#!/usr/bin/env python3
"""Isolated etcd quorum schedules with durable revision evidence."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pieq.etcd_boundary import EtcdBoundary, EtcdUnavailable

SCHEDULES = ("stale_resume", "simultaneous_drains", "duplicate_replay",
             "predecessor_frontier", "leader_failure", "quorum_loss", "client_isolation")
ISOLATED_COMPOSE = Path(__file__).resolve().parents[1] / "containers" / "etcd-compose.yml"
ISOLATED_ENDPOINTS = (
    "http://127.0.0.1:12379",
    "http://127.0.0.1:22379",
    "http://127.0.0.1:32379",
)


class _ClientRoute:
    """A per-client network relay whose link can be cut without moving clients."""
    def __init__(self, target):
        self.target = target
        self.connected = threading.Event()
        self.connected.set()

    def __enter__(self):
        route = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if not route.connected.is_set():
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                body = self.rfile.read(int(self.headers["Content-Length"]))
                response = EtcdBoundary(route.target).request(self.path, json.loads(body))
                payload = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def cut(self):
        self.connected.clear()

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


def _ports(count):
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


class EtcdCluster:
    """Owns only its temporary local cluster or explicitly selected compose."""
    def __enter__(self):
        self.processes = {}
        self.logs = []
        self.compose = os.environ.get("PIEQ_ETCD_COMPOSE")
        supplied = os.environ.get("PIEQ_ETCD_ENDPOINTS")
        if bool(self.compose) != bool(supplied):
            raise ValueError("isolated Compose path and endpoints must be provided together")
        if supplied:
            if Path(self.compose).resolve() != ISOLATED_COMPOSE:
                raise ValueError("isolated bundled Compose project is required for fault control")
            self.compose = str(ISOLATED_COMPOSE)
            self.endpoints = supplied.split(",")
            if tuple(self.endpoints) != ISOLATED_ENDPOINTS:
                raise ValueError("isolated Compose endpoints must match the bundled loopback ports")
            self.scope = "three-container etcd quorum"
        self.directory = tempfile.TemporaryDirectory(prefix="pieq-etcd-")
        if not supplied:
            self.binary = os.environ["PIEQ_ETCD_BIN"]
            ports = _ports(6)
            self.endpoints = [f"http://127.0.0.1:{port}" for port in ports[:3]]
            peers = [f"http://127.0.0.1:{port}" for port in ports[3:]]
            initial = ",".join(f"pieq{i}={url}" for i, url in enumerate(peers))
            self.commands = [[
                self.binary, "--name", f"pieq{i}", "--data-dir",
                str(Path(self.directory.name) / f"node{i}"),
                "--listen-client-urls", self.endpoints[i],
                "--advertise-client-urls", self.endpoints[i],
                "--listen-peer-urls", peers[i], "--initial-advertise-peer-urls", peers[i],
                "--initial-cluster", initial, "--initial-cluster-token", Path(self.directory.name).name,
                "--log-level", "error",
            ] for i in range(3)]
            self.scope = "three-process single-host etcd quorum; container acceptance unverified"
            for i in range(3):
                self.start(i)
        try:
            self.wait()
            self.members = EtcdBoundary(self.endpoints[0]).request("/v3/cluster/member/list", {})["members"]
            if len(self.members) != 3:
                raise ValueError("fault evaluation requires exactly three etcd members")
            by_id = {str(member["ID"]): member for member in self.members}
            if len(by_id) != 3:
                raise ValueError("etcd member IDs are not distinct")
            self.member_routes = []
            for index, endpoint in enumerate(self.endpoints):
                status = EtcdBoundary(endpoint).request("/v3/maintenance/status", {})
                member_id = str(status["header"]["member_id"])
                member = by_id.get(member_id)
                if member is None or member["name"] != f"pieq{index}":
                    raise ValueError("isolated endpoint does not match its dedicated etcd member")
                self.member_routes.append({
                    "endpoint": endpoint,
                    "member_id": member_id,
                    "name": member["name"],
                    "raft_term": status.get("raftTerm"),
                    "leader_member_id": str(status["leader"]),
                })
            if len({route["member_id"] for route in self.member_routes}) != 3:
                raise ValueError("isolated endpoints do not reach distinct etcd members")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _compose(self, *args):
        subprocess.run(["docker", "compose", "-f", self.compose, *args],
                       check=True, stdout=subprocess.DEVNULL, timeout=30)

    def start(self, index):
        if self.compose:
            self._compose("start", f"etcd{index}")
        else:
            log = open(Path(self.directory.name) / f"node{index}.log", "ab")
            self.logs.append(log)
            self.processes[index] = subprocess.Popen(self.commands[index], stdout=log, stderr=log)

    def stop(self, index):
        if self.compose:
            self._compose("kill", "-s", "SIGKILL", f"etcd{index}")
        else:
            process = self.processes.pop(index)
            process.kill()
            process.wait(timeout=10)

    def wait(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                for endpoint in self.endpoints:
                    boundary = EtcdBoundary(endpoint, timeout=.5)
                    boundary.request("/v3/maintenance/status", {})
                    boundary.snapshot()
                return
            except EtcdUnavailable:
                time.sleep(.05)
        raise TimeoutError("three etcd members did not become healthy")

    def leader_index(self):
        status = EtcdBoundary(self.endpoints[0]).request("/v3/maintenance/status", {})
        leader = str(status["leader"])
        for i, endpoint in enumerate(self.endpoints):
            member = EtcdBoundary(endpoint).request("/v3/maintenance/status", {})
            if str(member["header"]["member_id"]) == leader:
                return i
        raise RuntimeError("leader not found")

    def __exit__(self, *_):
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in self.logs:
            log.close()
        self.directory.cleanup()


def _worker(endpoint, prefix, pipe, op, epoch, node):
    boundary = EtcdBoundary(endpoint, prefix)
    try:
        def barrier(snapshot):
            pipe.send({"event": "before_commit", "pid": os.getpid(), "snapshot": snapshot})
            if not pipe.poll(15):
                raise TimeoutError("commit barrier")
            pipe.recv()
        receipt = boundary.execute(op, epoch, node, before_commit=barrier)
        pipe.send({"event": "completed", "receipt": receipt,
                   "transactions": boundary.transactions, "pid": os.getpid()})
    except BaseException as exc:
        pipe.send({"event": "error", "error": repr(exc)})
    finally:
        pipe.close()


def _recv(pipe, expected):
    if not pipe.poll(20):
        raise TimeoutError(expected)
    message = pipe.recv()
    if message["event"] != expected:
        raise RuntimeError(message)
    return message


def run_schedule(cluster, schedule, repetition=0):
    if schedule not in SCHEDULES:
        raise ValueError(schedule)
    started = time.monotonic()
    boundary = EtcdBoundary(cluster.endpoints[0])
    boundary.initialize()
    peer = EtcdBoundary(cluster.endpoints[1], boundary.prefix)
    receipts, events, children, stopped = [], [], [], []
    revocation_revision = None
    isolation_before = None
    leader_before_fault = None

    def stop_member(index):
        started_ns = time.monotonic_ns()
        cluster.stop(index)
        events.append({
            "event": "member_stop",
            "member_index": index,
            "member_id": cluster.member_routes[index]["member_id"],
            "signal": "SIGKILL",
            "requested_ns": started_ns,
            "timestamp_ns": time.monotonic_ns(),
        })
        stopped.append(index)

    def restart_member(index):
        started_ns = time.monotonic_ns()
        cluster.start(index)
        events.append({
            "event": "member_restart",
            "member_index": index,
            "member_id": cluster.member_routes[index]["member_id"],
            "requested_ns": started_ns,
            "timestamp_ns": time.monotonic_ns(),
        })

    def record_post_fault_health(index, write_status):
        event = {
            "event": "post_fault_health",
            "member_index": index,
            "member_id": cluster.member_routes[index]["member_id"],
            "endpoint": cluster.endpoints[index],
            "write_status": write_status,
            "timestamp_ns": time.monotonic_ns(),
        }
        try:
            status = EtcdBoundary(cluster.endpoints[index], timeout=.5).request(
                "/v3/maintenance/status", {})
            event.update(reachable=True, observed_member_id=str(status["header"]["member_id"]),
                         leader_member_id=(str(status["leader"]) if "leader" in status else None),
                         raft_term=status.get("raftTerm"),
                         maintenance_errors=status.get("errors", []))
        except EtcdUnavailable as error:
            event.update(reachable=False, error=str(error))
        events.append(event)

    def spawn(endpoint, op, node):
        parent, child = mp.get_context("spawn").Pipe()
        process = mp.get_context("spawn").Process(
            target=_worker, args=(endpoint, boundary.prefix, child, op, 10, node))
        process.start()
        child.close()
        children.append((process, parent))
        events.append(_recv(parent, "before_commit"))
        return process, parent

    def finish(worker):
        worker[1].send("commit")
        response = _recv(worker[1], "completed")
        events.append(response)
        receipts.append(response["receipt"])

    try:
        if schedule == "stale_resume":
            worker = spawn(cluster.endpoints[0], "stale", "node_1")
            os.kill(worker[0].pid, signal.SIGSTOP)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                pid, status = os.waitpid(worker[0].pid, os.WUNTRACED | os.WNOHANG)
                if pid and os.WIFSTOPPED(status):
                    break
            else:
                raise TimeoutError("SIGSTOP confirmation")
            events.append({"event": "SIGSTOP", "pid": worker[0].pid})
            revoked = peer.revoke(11)
            assert revoked["status"] == "REVOKED", revoked
            revocation_revision = revoked["revision"]
            events.append({"event": "epoch_revocation", **revoked})
            receipts.append(peer.execute("replacement", 11, "node_2"))
            os.kill(worker[0].pid, signal.SIGCONT)
            events.append({"event": "SIGCONT", "pid": worker[0].pid})
            finish(worker)
        elif schedule in ("simultaneous_drains", "duplicate_replay"):
            duplicate = schedule == "duplicate_replay"
            workers = [spawn(cluster.endpoints[0], "shared" if duplicate else "a", "node_1"),
                       spawn(cluster.endpoints[1], "shared" if duplicate else "b",
                             "node_1" if duplicate else "node_2")]
            # Both have read the same revision before either CAS is released.
            for _, pipe in workers:
                pipe.send("commit")
            for _, pipe in workers:
                response = _recv(pipe, "completed")
                events.append(response)
                receipts.append(response["receipt"])
            if duplicate:
                receipts.append(peer.execute("shared", 10, "node_1"))
        elif schedule == "predecessor_frontier":
            receipts.append(peer.execute("successor", 10, predecessors=("a", "b")))
            receipts.append(boundary.execute("a", 10))
            receipts.append(peer.execute("successor", 10, predecessors=("a", "b")))
            receipts.append(boundary.execute("b", 10))
            receipts.append(peer.execute("successor", 10, "node_1", predecessors=("a", "b")))
        elif schedule in ("leader_failure", "quorum_loss"):
            leader = cluster.leader_index()
            leader_status = EtcdBoundary(cluster.endpoints[leader]).request(
                "/v3/maintenance/status", {})
            leader_before_fault = {
                "member_index": leader,
                "member_id": cluster.member_routes[leader]["member_id"],
                "raft_term": leader_status.get("raftTerm"),
                "leader_member_id": str(leader_status["leader"]),
            }
            stop_member(leader)
            survivor = next(i for i in range(3) if i != leader)
            live = EtcdBoundary(cluster.endpoints[survivor], boundary.prefix, timeout=2)
            if schedule == "quorum_loss":
                second = next(i for i in range(3) if i not in (leader, survivor))
                stop_member(second)
                receipts.append(live.execute("no-quorum", 10, "node_1"))
                assert receipts[-1]["status"] == "UNKNOWN", receipts
            else:
                deadline = time.monotonic() + 10
                while True:
                    result = live.execute("survivor", 10, "node_1")
                    if result["status"] != "UNKNOWN" or time.monotonic() >= deadline:
                        break
                receipts.append(result)
                boundary.transactions.extend(live.transactions)
            record_post_fault_health(survivor, receipts[-1]["status"])
        elif schedule == "client_isolation":
            # First prove this client's fixed endpoint reaches a real member;
            # then sever only that route. The peer keeps its independent route.
            with _ClientRoute(cluster.endpoints[0]) as route:
                isolated = EtcdBoundary(route.endpoint, boundary.prefix, timeout=.3)
                isolation_before = isolated.snapshot()
                route.cut()
                receipts.append(isolated.execute("isolated", 10, "node_1"))
                receipts.append(peer.execute("healthy", 10, "node_2"))
                events.append({"event": "client_route_isolated", "endpoint": isolated.endpoint,
                               "member_target": route.target,
                               "mechanism": "previously healthy per-client relay drops connections"})
        for index in stopped:
            restart_member(index)
        stopped.clear()
        cluster.wait()
        final = boundary.snapshot()
        values = final["values"]
        committed = {key: value for key, value in values.items() if key.startswith("receipts/")}
        effect_receipts = [key for key, value in committed.items() if value["identity"]["node"]]
        effects = sum(value == "DRAINED" for key, value in values.items() if key.startswith("nodes/"))
        statuses = [receipt["status"] for receipt in receipts]
        checks = {
            "atomic_cap": effects <= 1,
            "one_drain_receipt": len(effect_receipts) <= 1,
            "receipt_frontier": set(values["frontier"]) == {key.split("/", 1)[1] for key in committed},
            "receipt_effect_same_revision": all(
                final["revisions"][key] ==
                final["revisions"]["nodes/" + committed[key]["identity"]["node"]]
                for key in effect_receipts),
            "no_stale_commit_after_revocation": all(
                value["identity"]["epoch"] >= 11 or final["revisions"][key] < revocation_revision
                for key, value in committed.items()) if revocation_revision else True,
        }
        expected = {
            "stale_resume": ["COMMITTED", "REJECTED_STALE_EPOCH"],
            "simultaneous_drains": ["COMMITTED", "REJECTED_EXPOSURE_CAP"],
            "duplicate_replay": ["COMMITTED", "DUPLICATE_IDEMPOTENT_COMMITTED",
                                 "DUPLICATE_IDEMPOTENT_COMMITTED"],
            "predecessor_frontier": ["REJECTED_MISSING_PREDECESSOR_FRONTIER", "COMMITTED",
                                     "REJECTED_MISSING_PREDECESSOR_FRONTIER", "COMMITTED", "COMMITTED"],
            "leader_failure": ["COMMITTED"],
            "quorum_loss": ["UNKNOWN"],
            "client_isolation": ["UNKNOWN", "COMMITTED"],
        }
        checks["schedule_outcomes"] = sorted(statuses) == sorted(expected[schedule])
        checks["expected_effect_count"] = effects == (0 if schedule == "quorum_loss" else 1)
        if schedule == "stale_resume":
            checks["stale_resource_unchanged"] = values["nodes/node_1"] == "ACTIVE"
            checks["stale_receipt_absent"] = "receipts/stale" not in committed
        if schedule == "client_isolation":
            checks["isolated_receipt_absent"] = "receipts/isolated" not in committed
        checks["all_passed"] = all(checks.values())
    finally:
        for process, pipe in children:
            if process.is_alive():
                os.kill(process.pid, signal.SIGCONT)
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            pipe.close()
        for index in stopped:
            restart_member(index)
        if stopped:
            cluster.wait()
    return {
        "schedule": schedule, "repetition": repetition, "evidence_scope": cluster.scope,
        "enforcement_tier": "E2: effects represented inside the atomic etcd state change",
        "cluster_member_ids": [member["ID"] for member in cluster.members],
        "member_routes": cluster.member_routes,
        "leader_before_fault": leader_before_fault,
        "client_targets": cluster.endpoints[:2], "namespace": boundary.prefix,
        "fault_schedule": events, "receipts": receipts,
        "isolation_before": isolation_before,
        "transactions": boundary.transactions + peer.transactions + [
            transaction for event in events for transaction in event.get("transactions", [])],
        "final_state": final, "effect_count": effects, "checks": checks,
        "wall_clock_seconds": time.monotonic() - started, "processes_reaped": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trials = []
    with EtcdCluster() as cluster:
        for schedule in SCHEDULES:
            for repetition in range(args.repetitions):
                trial = run_schedule(cluster, schedule, repetition)
                trials.append(trial)
                if not trial["checks"]["all_passed"]:
                    raise AssertionError(trial)
    distributions = {}
    for schedule in SCHEDULES:
        durations = sorted(t["wall_clock_seconds"] for t in trials if t["schedule"] == schedule)
        distributions[schedule] = {"count": len(durations), "min": min(durations),
                                   "median": statistics.median(durations),
                                   "p95": durations[max(0, int(.95 * len(durations)) - 1)],
                                   "max": max(durations)}
    args.output.write_text(json.dumps({"trials": trials, "wall_clock_distributions": distributions}, indent=2))
    print(f"{len(trials)} trials passed; evidence: {args.output}")


if __name__ == "__main__":
    main()
