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
