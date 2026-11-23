"""E2 boundary for effects represented by committed etcd key changes.

The JSON gRPC gateway is accessed with Python's standard-library HTTP client.
No external machine-side effect is claimed atomic with this transaction.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
import uuid


def _b64(value):
    return base64.b64encode(value.encode()).decode()


def _unb64(value):
    return base64.b64decode(value).decode()


class EtcdUnavailable(RuntimeError):
    pass


class EtcdBoundary:
    def __init__(self, endpoint, prefix=None, timeout=1.5, attempts=8):
        self.endpoint = endpoint.rstrip("/")
        self.prefix = prefix or "/pieq-eval/v1/" + uuid.uuid4().hex + "/"
        if not self.prefix.startswith("/pieq-eval/v1/") or not self.prefix.endswith("/"):
            raise ValueError("an isolated versioned evaluation prefix is required")
        self.timeout = timeout
        self.attempts = attempts
        self.transactions = []

    def request(self, route, data):
        request = urllib.request.Request(
            self.endpoint + route, json.dumps(data).encode(),
            {"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
            if "error" in result:
                raise EtcdUnavailable(str(result))
            return result
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise EtcdUnavailable(str(exc)) from exc

    def snapshot(self):
        # All keys are read in one linearizable range at one revision. The
        # frontier serializes additions to the closed receipt domain.
        end = self.prefix[:-1] + chr(ord(self.prefix[-1]) + 1)
        result = self.request("/v3/kv/range", {
            "key": _b64(self.prefix), "range_end": _b64(end),
        })
        values, revisions = {}, {}
        for kv in result.get("kvs", []):
            key = _unb64(kv["key"])[len(self.prefix):]
            values[key] = json.loads(_unb64(kv["value"]))
            revisions[key] = int(kv["mod_revision"])
        return {"revision": int(result["header"]["revision"]),
                "values": values, "revisions": revisions}

    def _txn(self, snapshot, puts, absent=()):
        compares = [
            {"key": _b64(self.prefix + key), "target": "MOD", "result": "EQUAL",
             "mod_revision": str(revision)}
            for key, revision in snapshot["revisions"].items()
        ]
        compares += [
            {"key": _b64(self.prefix + key), "target": "VERSION",
             "result": "EQUAL", "version": "0"} for key in absent
        ]
        request = {"compare": compares, "success": [
            {"request_put": {"key": _b64(self.prefix + key),
                             "value": _b64(json.dumps(value, sort_keys=True))}}
            for key, value in puts.items()
        ], "failure": []}
        response = self.request("/v3/kv/txn", request)
        self.transactions.append({"read_revision": snapshot["revision"],
                                  "read_revisions": snapshot["revisions"],
                                  "request": request, "response": response})
        return response

    def initialize(self):
        response = self._txn({"revision": 0, "revisions": {}}, {
            "epoch": 10, "cap": 1, "frontier": [],
            "nodes/node_1": "ACTIVE", "nodes/node_2": "ACTIVE",
        }, absent=("epoch",))
        if not response.get("succeeded", False):
            raise ValueError("namespace already initialized")
        return response

    def revoke(self, epoch):
        try:
            for _ in range(self.attempts):
                snapshot = self.snapshot()
                if epoch <= snapshot["values"]["epoch"]:
                    return {"status": "ALREADY_REVOKED", "revision": snapshot["revision"]}
                response = self._txn(snapshot, {"epoch": epoch})
                if response.get("succeeded", False):
                    return {"status": "REVOKED", "revision": int(response["header"]["revision"])}
        except EtcdUnavailable:
            pass
        return {"status": "UNKNOWN"}

    def execute(self, op_id, epoch, node=None, predecessors=(), before_commit=None):
        if not op_id or "/" in op_id:
            raise ValueError("op_id must be a nonempty key component")
        identity = {"epoch": epoch, "node": node, "predecessors": sorted(set(predecessors))}
        receipt_key = "receipts/" + op_id
        try:
            for attempt in range(self.attempts):
                snapshot = self.snapshot()
                values = snapshot["values"]
                if receipt_key in values:
                    status = ("DUPLICATE_IDEMPOTENT_COMMITTED"
                              if values[receipt_key]["identity"] == identity
                              else "REJECTED_OP_ID_CONFLICT")
                    return {"status": status, "committed": False,
                            "revision": snapshot["revisions"][receipt_key]}
                if epoch < values["epoch"]:
                    return {"status": "REJECTED_STALE_EPOCH", "committed": False,
                            "revision": snapshot["revision"]}
                if not set(predecessors).issubset(values["frontier"]):
                    return {"status": "REJECTED_MISSING_PREDECESSOR_FRONTIER",
                            "committed": False, "revision": snapshot["revision"]}
                if node is not None and "nodes/" + node not in values:
                    return {"status": "REJECTED_OUTSIDE_DOMAIN", "committed": False}
                if node is not None and values["nodes/" + node] == "DRAINED":
                    return {"status": "REJECTED_ALREADY_DRAINED", "committed": False,
                            "revision": snapshot["revision"]}
                drained = {key for key, value in values.items()
                           if key.startswith("nodes/") and value == "DRAINED"}
                if node is not None:
                    drained.add("nodes/" + node)
                if len(drained) > values["cap"]:
                    return {"status": "REJECTED_EXPOSURE_CAP", "committed": False,
                            "revision": snapshot["revision"]}
                puts = {
                    receipt_key: {"identity": identity, "status": "COMMITTED"},
                    "frontier": sorted(set(values["frontier"]) | {op_id}),
                }
                if node is not None:
                    puts["nodes/" + node] = "DRAINED"
                if before_commit is not None and attempt == 0:
                    before_commit(snapshot)
                response = self._txn(snapshot, puts, absent=(receipt_key,))
                if response.get("succeeded", False):
                    return {"status": "COMMITTED", "committed": True,
                            "revision": int(response["header"]["revision"]),
                            "attempts": attempt + 1}
        except EtcdUnavailable as exc:
            # A lost response can hide a committed effect: the caller must
            # reconcile the same op_id. UNKNOWN never grants a fresh mutation.
            return {"status": "UNKNOWN", "committed": False, "error": str(exc)}
        return {"status": "UNKNOWN", "committed": False, "reason": "CAS retry bound"}


class EtcdQuiescenceBoundary(EtcdBoundary):
    """
    Revision 12 — Tier E1+-quiescence two-phase barrier backed by etcd Raft quorum.

    Operationalizes Corollary 1(c-E1+):
      - `prepare(op_id, auth_epoch)` checks durable `min_auth_epoch` and acquires
        exclusive `lock_r` + `prepared_op` via atomic Raft CAS.
      - `initiate_revocation(new_auth_epoch)` records `pending_auth_epoch`; if `lock_r`
        is free, flushes `min_auth_epoch` and `fence_ack_epoch` immediately; otherwise
        reports `BLOCKED_ON_PREPARED_LOCK` until `commit_prepared` drains `lock_r`.
      - `commit_prepared(op_id)` executes the already-prepared mutation (pre-FenceAck
        window) and atomically flushes `min_auth_epoch = max(min_auth_epoch, pending)`
        and `fence_ack_epoch`, returning `FenceAck`. Subsequent calls with revoked
        `auth_epoch` deterministically reject (`REJECTED_AUTHORIZATION_REVOKED`).
      - If the worker crashes while holding `lock_r`, `prepared_op` stays durable and
        blocks conflicting admissions (`BLOCKED_UNKNOWN_PREPARED`) until explicit
        `reconcile_prepared(op_id, outcome)` clears the barrier.
    """

    def initialize_quiescence(self, initial_auth_epoch: int = 5):
        response = self._txn({"revision": 0, "revisions": {}}, {
            "q/min_auth_epoch": initial_auth_epoch,
            "q/pending_auth_epoch": initial_auth_epoch,
            "q/fence_ack_epoch": initial_auth_epoch,
            "q/lock_holder": None,
            "q/prepared_op": None,
        }, absent=("q/min_auth_epoch",))
        if not response.get("succeeded", False):
            raise ValueError("quiescence namespace already initialized")
        return response

    def prepare(self, op_id: str, auth_epoch: int):
        try:
            for _ in range(self.attempts):
                snap = self.snapshot()
                vals = snap["values"]
                if vals["q/lock_holder"] is not None:
                    return {"status": "BLOCKED_UNKNOWN_PREPARED", "prepared": False, "holder": vals["q/lock_holder"]}
                if auth_epoch < vals["q/min_auth_epoch"]:
                    return {"status": "REJECTED_AUTHORIZATION_REVOKED", "prepared": False, "min_auth_epoch": vals["q/min_auth_epoch"]}
                resp = self._txn(snap, {"q/lock_holder": op_id, "q/prepared_op": {"op_id": op_id, "auth_epoch": auth_epoch}})
                if resp.get("succeeded", False):
                    return {"status": "PREPARED_LOCKED", "prepared": True, "revision": int(resp["header"]["revision"])}
        except EtcdUnavailable as exc:
            return {"status": "UNKNOWN", "prepared": False, "error": str(exc)}
        return {"status": "UNKNOWN", "prepared": False}

    def initiate_revocation(self, new_auth_epoch: int):
        try:
            for _ in range(self.attempts):
                snap = self.snapshot()
                vals = snap["values"]
                pending = max(vals["q/pending_auth_epoch"], new_auth_epoch)
                if vals["q/lock_holder"] is None:
                    new_min = max(vals["q/min_auth_epoch"], pending)
                    resp = self._txn(snap, {
                        "q/pending_auth_epoch": pending,
                        "q/min_auth_epoch": new_min,
                        "q/fence_ack_epoch": new_min,
                    })
                    if resp.get("succeeded", False):
                        return {"status": "FENCE_ACK_IMMEDIATE", "fence_ack_epoch": new_min}
                else:
                    resp = self._txn(snap, {"q/pending_auth_epoch": pending})
                    if resp.get("succeeded", False):
                        return {"status": "BLOCKED_ON_PREPARED_LOCK", "holder": vals["q/lock_holder"], "pending_auth_epoch": pending}
        except EtcdUnavailable as exc:
            return {"status": "UNKNOWN", "error": str(exc)}
        return {"status": "UNKNOWN"}

    def commit_prepared(self, op_id: str):
        try:
            for _ in range(self.attempts):
                snap = self.snapshot()
                vals = snap["values"]
                if vals["q/lock_holder"] != op_id:
                    return {"status": "REJECTED_LOCK_NOT_HELD", "committed": False}
                new_min = max(vals["q/min_auth_epoch"], vals["q/pending_auth_epoch"])
                pre_fence_race = vals["q/pending_auth_epoch"] > vals["q/prepared_op"]["auth_epoch"]
                resp = self._txn(snap, {
                    f"q/commits/{op_id}": {"op_id": op_id, "pre_fence_ack_rev_pending": pre_fence_race},
                    "q/min_auth_epoch": new_min,
                    "q/fence_ack_epoch": new_min,
                    "q/lock_holder": None,
                    "q/prepared_op": None,
                })
                if resp.get("succeeded", False):
                    return {
                        "status": "COMMITTED_PRE_FENCE_ACK" if pre_fence_race else "COMMITTED",
                        "committed": True,
                        "fence_ack_epoch": new_min,
                        "revision": int(resp["header"]["revision"]),
                    }
        except EtcdUnavailable as exc:
            return {"status": "UNKNOWN", "committed": False, "error": str(exc)}
        return {"status": "UNKNOWN", "committed": False}

    def reconcile_prepared(self, op_id: str, outcome: str = "FENCED_NO_EFFECT"):
        if outcome not in ("COMMITTED", "FENCED_NO_EFFECT"):
            raise ValueError("outcome must be COMMITTED or FENCED_NO_EFFECT")
        try:
            for _ in range(self.attempts):
                snap = self.snapshot()
                vals = snap["values"]
                if vals["q/lock_holder"] != op_id:
                    return {"status": "NO_MATCHING_PREPARED_OP"}
                new_min = max(vals["q/min_auth_epoch"], vals["q/pending_auth_epoch"])
                resp = self._txn(snap, {
                    f"q/reconciled/{op_id}": outcome,
                    "q/min_auth_epoch": new_min,
                    "q/fence_ack_epoch": new_min,
                    "q/lock_holder": None,
                    "q/prepared_op": None,
                })
                if resp.get("succeeded", False):
                    return {"status": f"RECONCILED_{outcome}", "fence_ack_epoch": new_min}
        except EtcdUnavailable as exc:
            return {"status": "UNKNOWN", "error": str(exc)}
        return {"status": "UNKNOWN"}
