# REVISION 11 — EXP-08: Real PostgreSQL Tier E2 Transactional Enforcement

**Status: PASS** — the declared 10-point E2 contract held with no violation across the executed 12-scenario fault set, on a REAL Patroni-managed PostgreSQL 16 primary (testbed cluster, schema `pieq_e2`). Prereg: `results/rev11/exp08/prereg.json`. Adapter: `experiments/pg_patroni_failover/rev11_exp08_e2_adapter.py`. First run (two harness scenario-construction defects: re-minted certificates with fresh TTLs breaking identity-equality tests; a cap-arithmetic error) preserved verbatim in `exp08_results_firstrun.json`; the adapter semantics were NOT changed; the rerun is `exp08_results.json`.

## Contract matrix (Theorem 5 / Corollary 1 → PostgreSQL)
| Contract element | PG primitive | Boundary | Fault test → observed |
|---|---|---|---|
| Signed op identity + policy binding | pgcrypto HMAC recomputed in-transaction over canonical payload, key in `pieq_e2.secret` (executor-readable only) | same txn | tampered/mismatched → reject; policy string bound (point 9) |
| Resource scope + invariant serialization | `control` row `SELECT…FOR UPDATE` (requires UPDATE grant — discovered at first execution) | row lock to commit | concurrent +4/+4 over headroom: one COMMITTED, one SSI abort (retryable); final SUM=10 ≤ cap verified by independent superuser read |
| Authorization freshness | `min_auth_epoch` + DB-clock TTL check in-txn | same txn | expired → REJECTED_AUTHORIZATION_EXPIRED; revoked-pre-effect → REJECTED_AUTHORIZATION_REVOKED |
| Admission revision | `control.admission_rev` equality in-txn | same txn | stale → REJECTED_STALE_ADMISSION_REVISION; abort-then-retry with fresh admission → COMMITTED |
| Predecessors | `ops.status='COMMITTED'` lookups in-txn | same txn | covered by identity tests |
| Idempotency/dedup | `ops.op_id` PK + identity_json equality | same txn | identical re-presentation → DUPLICATE_IDEMPOTENT_COMMITTED; different identity → REJECTED_OP_ID_CONFLICT |
| Commit-time precondition (H30-analog) | `predicates.holds` read in-txn | same txn | true-at-admission/false-at-effect → **REJECTED_COMMIT_PRECONDITION_FALSE, no mutation** |
| Crash/recovery | WAL atomicity | txn commit | crash-before-commit → no row; crash-after-commit-before-ack → reconciled COMMITTED from authoritative `ops`; **no auto-redispatch**, idempotent re-presentation only |
| Mediation | role grants: `pieq_executor` only; `untrusted_agent` → `42501` denied | — | unmediated agent write DENIED; `postgres` superuser bypass SUCCEEDS and is reported as an administrator OUTSIDE the mediation assumptions |

## Explicit non-claims
A successful SERIALIZABLE transaction does not establish complete physical mediation, host fault-domain independence, or the whole of Refines_D; the Patroni REST **promotion** adapter remains Tier E0 + native safeguards (unchanged by this experiment). The E2 claim is scoped to this bounded reservation action vocabulary on this target.
