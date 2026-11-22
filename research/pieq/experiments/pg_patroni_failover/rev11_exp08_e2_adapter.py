#!/usr/bin/env python3
"""
EXP-08 — Real PostgreSQL Tier E2 transactional mutation adapter (capacity reservation).

Runs INSIDE the testbed network (docker exec on a pg node) against the current Patroni
primary. The protected mutation is a bounded capacity reservation: SUM(reservations)
must never exceed the trusted limit.

Tier E2 contract, enforced inside ONE PostgreSQL transaction against authoritative
transactionally consistent state (never a cache/preflight):
  1. signed operation identity: HMAC-SHA256 recomputed by the database (pgcrypto) over
     the canonical payload using a server-stored key readable only by the executor role
  2. resource scope (control row, SELECT ... FOR UPDATE -> serializes the invariant,
     preventing write-skew on the aggregate)
  3. authorization freshness: cert.auth_epoch >= control.min_auth_epoch AND
     now() <= cert.valid_until (DB clock)
  4. admission revision: cert.admission_rev == control.admission_rev
  5. predecessor ordering: required ops COMMITTED in pieq_e2.ops
  6. idempotency/dedup: op_id PK; identical identity -> DUPLICATE_IDEMPOTENT;
     different identity -> OP_ID_CONFLICT
  7. exposure: SUM(amount)+req <= capacity_limit (under the control-row lock)
  8. commit-time precondition: pieq_e2.predicates.holds read in-transaction
  9. temporal-policy binding: cert.temporal_policy must equal control.expected_policy
 10. the mutation itself (ops + reservations inserts) in the same transaction

Mediation: role `pieq_executor` is the only grantee on the schema; role
`untrusted_agent` has no grants (denied write = mediation holds). The `postgres`
superuser can bypass -- that is an administrator outside the mediation assumptions and
is reported as such, never as a defect of the adapter. A successful SERIALIZABLE
transaction does NOT by itself establish complete physical mediation, host fault-domain
independence, or all of Refines_D; those remain contract disclosures.

Usage (inside container): python3 rev11_exp08_e2_adapter.py <leader_ip>
Prints one JSON document with per-scenario results.
"""
from __future__ import annotations

import json
import sys
import threading
import time

import psycopg2
import psycopg2.extensions

LEADER = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
SUPER_DSN = f"host={LEADER} port=5432 user=postgres password=zalando dbname=postgres"
EXEC_DSN = f"host={LEADER} port=5432 user=pieq_executor password=pieq_exec_pw dbname=postgres"
AGENT_DSN = f"host={LEADER} port=5432 user=untrusted_agent password=agent_pw dbname=postgres"
HMAC_KEY = "rev11-e2-server-key"
POLICY = "COMMIT_TIME_SENSITIVE|pred:capacity_headroom|persist=0"

SETUP = f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;
DROP SCHEMA IF EXISTS pieq_e2 CASCADE;
CREATE SCHEMA pieq_e2;
CREATE TABLE pieq_e2.secret (k text NOT NULL);
INSERT INTO pieq_e2.secret VALUES ('{HMAC_KEY}');
CREATE TABLE pieq_e2.control (
  resource_id text PRIMARY KEY, min_auth_epoch int NOT NULL,
  admission_rev int NOT NULL, capacity_limit int NOT NULL,
  expected_policy text NOT NULL);
INSERT INTO pieq_e2.control VALUES ('capacity_pool_01', 5, 1, 10, '{POLICY}');
CREATE TABLE pieq_e2.predicates (predicate_id text PRIMARY KEY, holds boolean NOT NULL);
INSERT INTO pieq_e2.predicates VALUES ('pred:capacity_headroom', true);
CREATE TABLE pieq_e2.ops (
  op_id text PRIMARY KEY, action text NOT NULL, identity_json text NOT NULL,
  status text NOT NULL, amount int NOT NULL, committed_at timestamptz DEFAULT now());
CREATE TABLE pieq_e2.reservations (
  op_id text PRIMARY KEY REFERENCES pieq_e2.ops(op_id), amount int NOT NULL);
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='pieq_executor') THEN
    CREATE ROLE pieq_executor LOGIN PASSWORD 'pieq_exec_pw'; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='untrusted_agent') THEN
    CREATE ROLE untrusted_agent LOGIN PASSWORD 'agent_pw'; END IF;
END $$;
GRANT USAGE ON SCHEMA pieq_e2 TO pieq_executor;
GRANT SELECT ON pieq_e2.secret, pieq_e2.predicates TO pieq_executor;
GRANT SELECT, UPDATE ON pieq_e2.control TO pieq_executor;  -- UPDATE needed for FOR UPDATE row lock
GRANT SELECT, INSERT, UPDATE ON pieq_e2.ops, pieq_e2.reservations TO pieq_executor;
REVOKE ALL ON SCHEMA pieq_e2 FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO pieq_executor;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO pieq_executor;
"""

E2_TXN = """
BEGIN;
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
"""


def canonical(cert: dict) -> str:
    return json.dumps(cert, sort_keys=True, separators=(",", ":"))


def sign(cert: dict) -> str:
    import hashlib, hmac as h
    return h.new(HMAC_KEY.encode(), canonical(cert).encode(), hashlib.sha256).hexdigest()


def mk_cert(op_id, amount=3, auth_epoch=5, admission_rev=1, valid_s=600,
            policy=POLICY, predecessors=()):
    cert = {"op_id": op_id, "action": "reserve_capacity", "amount": amount,
            "auth_epoch": auth_epoch, "admission_rev": admission_rev,
            "valid_until_epoch_s": time.time() + valid_s,
            "temporal_policy": policy, "predecessors": sorted(predecessors),
            "resource_id": "capacity_pool_01"}
    return cert, sign(cert)


def execute_e2(conn, cert, sig, *, crash_before_commit=False, crash_after_commit=False):
    """The single Tier E2 transaction. Returns (status, detail)."""
    cur = conn.cursor()
    try:
        cur.execute("BEGIN ISOLATION LEVEL SERIALIZABLE;")
        # (1) in-database signature verification against server-stored key:
        cur.execute(
            "SELECT encode(hmac(%s::bytea, k::bytea, 'sha256'),'hex') = %s FROM pieq_e2.secret;",
            (canonical(cert), sig))
        if not cur.fetchone()[0]:
            conn.rollback(); return "REJECTED_INVALID_SIGNATURE", None
        # (2) control row lock = authoritative state + invariant serialization:
        cur.execute("SELECT min_auth_epoch, admission_rev, capacity_limit, expected_policy "
                    "FROM pieq_e2.control WHERE resource_id=%s FOR UPDATE;",
                    (cert["resource_id"],))
        row = cur.fetchone()
        if row is None:
            conn.rollback(); return "REJECTED_UNKNOWN_RESOURCE", None
        min_auth, adm_rev, cap, expected_policy = row
        # (3) authorization freshness (DB clock):
        cur.execute("SELECT extract(epoch from now());")
        db_now = float(cur.fetchone()[0])
        if cert["auth_epoch"] < min_auth:
            conn.rollback(); return "REJECTED_AUTHORIZATION_REVOKED", None
        if db_now > cert["valid_until_epoch_s"]:
            conn.rollback(); return "REJECTED_AUTHORIZATION_EXPIRED", None
        # (4) admission revision CAS:
        if cert["admission_rev"] != adm_rev:
            conn.rollback(); return "REJECTED_STALE_ADMISSION_REVISION", None
        # (9) temporal-policy binding:
        if cert["temporal_policy"] != expected_policy:
            conn.rollback(); return "REJECTED_STALE_TEMPORAL_POLICY", None
        # (6) idempotency / identity conflict:
        cur.execute("SELECT status, identity_json FROM pieq_e2.ops WHERE op_id=%s;",
                    (cert["op_id"],))
        prev = cur.fetchone()
        if prev is not None:
            conn.rollback()
            if prev[1] == canonical(cert):
                return ("DUPLICATE_IDEMPOTENT_" + prev[0]), None
            return "REJECTED_OP_ID_CONFLICT", None
        # (5) predecessor frontier:
        for p in cert["predecessors"]:
            cur.execute("SELECT 1 FROM pieq_e2.ops WHERE op_id=%s AND status='COMMITTED';", (p,))
            if cur.fetchone() is None:
                conn.rollback(); return "REJECTED_MISSING_PREDECESSOR", None
        # (8) commit-time precondition (authoritative, in-transaction):
        cur.execute("SELECT holds FROM pieq_e2.predicates WHERE predicate_id=%s;",
                    ("pred:capacity_headroom",))
        pr = cur.fetchone()
        if pr is None or not pr[0]:
            conn.rollback(); return "REJECTED_COMMIT_PRECONDITION_FALSE", None
        # (7) exposure under the control lock:
        cur.execute("SELECT COALESCE(SUM(amount),0) FROM pieq_e2.reservations;")
        total = cur.fetchone()[0]
        if total + cert["amount"] > cap:
            conn.rollback(); return "REJECTED_EXPOSURE_CAP", {"total": int(total), "cap": cap}
        # (10) the mutation:
        cur.execute("INSERT INTO pieq_e2.ops VALUES (%s,%s,%s,'COMMITTED',%s,now());",
                    (cert["op_id"], cert["action"], canonical(cert), cert["amount"]))
        cur.execute("INSERT INTO pieq_e2.reservations VALUES (%s,%s);",
                    (cert["op_id"], cert["amount"]))
        if crash_before_commit:
            conn.close(); return "CRASHED_BEFORE_COMMIT", None
        conn.commit()
        if crash_after_commit:
            conn.close(); return "CRASHED_AFTER_COMMIT_BEFORE_ACK", None
        return "COMMITTED", None
    except psycopg2.errors.SerializationFailure:
        conn.rollback(); return "SERIALIZATION_ABORT_RETRYABLE", None
    except psycopg2.Error as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return f"PG_ERROR:{e.pgcode}", str(e)[:160]


def observer(sql):
    c = psycopg2.connect(SUPER_DSN)
    cur = c.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    c.close()
    return rows


def main():
    results = {"experiment": "EXP-08", "leader": LEADER, "scenarios": {}}
    su = psycopg2.connect(SUPER_DSN)
    su.autocommit = True
    su.cursor().execute(SETUP)

    def fresh():
        return psycopg2.connect(EXEC_DSN)

    R = results["scenarios"]
    # 1. valid authorized reservation
    valid_cert, valid_sig = mk_cert("op_valid_1")
    R["1_valid"] = execute_e2(fresh(), valid_cert, valid_sig)[0]
    # 2. expired authorization
    cert, sig = mk_cert("op_expired", valid_s=-5)
    R["2_expired_auth"] = execute_e2(fresh(), cert, sig)[0]
    # 3. revoked before effect boundary
    cert, sig = mk_cert("op_revoked", auth_epoch=5)
    su.cursor().execute("UPDATE pieq_e2.control SET min_auth_epoch=6;")
    R["3_revoked_pre_effect"] = execute_e2(fresh(), cert, sig)[0]
    su.cursor().execute("UPDATE pieq_e2.control SET min_auth_epoch=5;")
    # 4. stale admission revision
    cert, sig = mk_cert("op_stale_rev", admission_rev=0)
    R["4_stale_revision"] = execute_e2(fresh(), cert, sig)[0]
    # 5. false commit-time precondition (H30-analog: true at admission, false at effect)
    cert, sig = mk_cert("op_h30")  # 'admission' happened with predicate true
    su.cursor().execute("UPDATE pieq_e2.predicates SET holds=false;")
    R["5_false_commit_precondition"] = execute_e2(fresh(), cert, sig)[0]
    su.cursor().execute("UPDATE pieq_e2.predicates SET holds=true;")
    # 6. duplicate op identity (idempotent) + conflicting identity
    R["6a_duplicate_idempotent"] = execute_e2(fresh(), valid_cert, valid_sig)[0]
    cert2, sig2 = mk_cert("op_valid_1", amount=4)
    R["6b_op_id_conflict"] = execute_e2(fresh(), cert2, sig2)[0]
    # 7. concurrent reservations exceeding aggregate capacity (two threads)
    out = {}
    def worker(name, op, amt):
        c, s = mk_cert(op, amount=amt)
        out[name] = execute_e2(fresh(), c, s)[0]
    t1 = threading.Thread(target=worker, args=("a", "op_conc_a", 4))
    t2 = threading.Thread(target=worker, args=("b", "op_conc_b", 4))
    t1.start(); t2.start(); t1.join(); t2.join()
    # cap=10, already 3 reserved: 3+4+4=11 > 10 -> exactly one must be rejected
    R["7_concurrent_capacity"] = out
    # 8. transaction abort and retry (serialization failure path): force via stale rev then retry fresh
    cert, sig = mk_cert("op_retry", admission_rev=0)
    first = execute_e2(fresh(), cert, sig)[0]
    cert, sig = mk_cert("op_retry", amount=2)  # new certified admission with correct rev
    R["8_abort_then_retry"] = {"first": first, "retry": execute_e2(fresh(), cert, sig)[0]}
    # 9. process crash before commit
    cert, sig = mk_cert("op_crash_pre", amount=1)
    R["9_crash_before_commit"] = {
        "status": execute_e2(fresh(), cert, sig, crash_before_commit=True)[0],
        "row_exists_after": bool(observer("SELECT 1 FROM pieq_e2.ops WHERE op_id='op_crash_pre';"))}
    # 10. crash after commit before ack + 11. lost-response reconciliation
    post_cert, post_sig = mk_cert("op_crash_post", amount=1)
    cert, sig = post_cert, post_sig
    st = execute_e2(fresh(), cert, sig, crash_after_commit=True)[0]
    recon = observer("SELECT status FROM pieq_e2.ops WHERE op_id='op_crash_post';")
    # reconciliation: do NOT redispatch; idempotent re-presentation is safe:
    R["10_11_crash_after_commit_and_reconciliation"] = {
        "status": st, "reconciled_status": recon[0][0] if recon else None,
        "idempotent_representation": execute_e2(fresh(), post_cert, post_sig)[0]}
    # 12. unmediated writer (untrusted agent role) vs admin bypass
    try:
        ac = psycopg2.connect(AGENT_DSN)
        cur = ac.cursor()
        cur.execute("INSERT INTO pieq_e2.reservations VALUES ('rogue', 99);")
        ac.commit()
        R["12a_unmediated_agent_write"] = "UNEXPECTED_SUCCESS"
    except psycopg2.Error as e:
        R["12a_unmediated_agent_write"] = f"DENIED:{e.pgcode}"
    sup = psycopg2.connect(SUPER_DSN)
    try:
        cur = sup.cursor()
        cur.execute("INSERT INTO pieq_e2.ops VALUES ('admin_bypass','x','{}','COMMITTED',0,now());")
        sup.commit()
        R["12b_admin_bypass_outside_mediation"] = "SUCCEEDS (superuser is outside the mediation assumption, as declared)"
    except psycopg2.Error as e:
        R["12b_admin_bypass_outside_mediation"] = f"DENIED:{e.pgcode}"
    # independent final state
    results["final_state"] = {
        "reservation_total": int(observer("SELECT COALESCE(SUM(amount),0) FROM pieq_e2.reservations;")[0][0]),
        "capacity_limit": 10,
        "committed_ops": sorted(r[0] for r in observer("SELECT op_id FROM pieq_e2.ops WHERE status='COMMITTED';")),
    }
    results["invariant_total_le_cap"] = results["final_state"]["reservation_total"] <= 10
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
