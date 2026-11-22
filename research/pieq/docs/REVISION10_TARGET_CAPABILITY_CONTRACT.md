# REVISION 10 — Physical Target Capability Contract (PostgreSQL/Patroni + etcd)

**Status:** Authored BEFORE physical execution (EXP-03 Step 1), per protocol. Rows marked
*MEASURED* are updated from executed trials; rows marked *DECLARED* reflect the target's
documented contract and are not upgraded without observation.

**Environment:** 3× PostgreSQL 16 / Patroni (spilo-16:3.3-p1) + 3× etcd v3.7.3, isolated
Docker bridge networks `172.28.0.0/24` (data) and `172.29.0.0/24` (OOB emulation), running
inside ONE colima VM (macOS Virtualization.framework, 4 vCPU / 8 GB) on one physical laptop.

## Host-level common-cause disclosure (mandatory)

All six "nodes" share one VM kernel, one storage device, one power source, and one
network stack. Therefore:
- `D_host` (VM kernel/storage/power) is a **single common-cause failure domain** coupling
  every other domain. No claim of physical host independence is made anywhere.
- Failure-domain independence claims are scoped to the *injected* fault surfaces only:
  per-container process death, per-container iptables partitions, per-network-bridge
  isolation. These are genuinely independent **at the container/network layer**, not the
  hardware layer.
- Any `ν_pack` computed over these domains certifies container/network-layer
  independence under the manifest `h_man` that declares exactly this.

## Step-1 Contract Table

| Question | Answer | Basis |
|---|---|---|
| **What mutation is PIEQ authorizing?** | A Patroni **failover/switchover** (promote a named replica to primary) on cluster `pieq_pg_cluster`, requested via Patroni's REST API (`POST /failover` or `POST /switchover` on a member's `:8008`), plus the implied etcd leader-key transition Patroni performs internally. Secondary mutation: none (no drains/restarts authorized in Rev10 trials). | Testbed design |
| **Where does the authorization check occur?** | In the PIEQ admission controller **on the host, before the REST call**: typed-proof verification, Φ_phys packing, plausible-world shield, temporal-policy gate, admission-revision CAS and certificate minting all occur in the control plane. **No PIEQ check executes inside PostgreSQL's or Patroni's own commit path.** Patroni/etcd independently enforce their native leader-lock CAS at the target. | Code path (`HardenedAdmissionController` → REST adapter) |
| **When does the physical effect occur?** | At PostgreSQL promotion on the target node: timeline increment observed via `pg_control_checkpoint().timeline_id` and `pg_is_in_recovery()` flipping false, strictly **after** Patroni acquires/updates the etcd leader key. The REST response is *not* the effect point; the SQL-observable timeline change is. There is **no PIEQ-visible linearization point**: the effect is asynchronous relative to PIEQ's admission record. | DECLARED → *MEASURED in F1 trial (see execution report)* |
| **Can a revoked request still execute?** | DECLARED yes: once the REST call is dispatched, PIEQ revocation (auth-epoch bump in the control plane) does not reach Patroni; the promotion proceeds. PIEQ can only refuse *before* dispatch. | DECLARED → *MEASURED in F5 trial* |
| **Can a prepared request be cancelled?** | Patroni failover requests can be superseded while pending, but an in-flight `pg_promote()` is **non-cancellable**. PIEQ must treat dispatched-unconfirmed operations as UNKNOWN. | DECLARED → *MEASURED in F6 trial* |
| **What survives coordinator failure?** | Durable: the etcd cluster state (leader key, member list), PostgreSQL timelines/WAL, and PIEQ's durable ledger records (SQLite WAL on host). NOT durable: in-flight REST call state. Recovery = reconcile UNKNOWN ops against observed timeline history + Patroni history endpoint. | DECLARED → *MEASURED in F6 trial* |
| **Can an operation execute twice?** | Patroni's DCS leader lock gives target-native serialization of *concurrent* promotions (one leader key), but a **re-sent failover request after a lost ACK is a new operation**, not an idempotent retry: there is no client-supplied idempotency key on the REST API. PIEQ must not auto-retry lost-ACK promotions. | DECLARED (Patroni REST semantics) |
| **What enforcement tier is justified?** | **E0 (proxy-only) with respect to PIEQ's own certificates.** The Patroni REST endpoint does not verify PIEQ certificates, epochs, auth TTLs, or temporal policies at the effect point; nothing PIEQ signs is re-checked target-side, and no fence-drain-and-acknowledge protocol is implemented for this target. Patroni/etcd *natively* provide their own leader-lock CAS and watchdog fencing — powerful, but they enforce *Patroni's* contract, not PIEQ's certificate contract. We therefore classify the promotion adapter **Tier E0 + target-native HA safeguards**, and explicitly do NOT claim E1+-quiescence (no FenceAck drain) or E2 (no shared atomic boundary). An atomic etcd/SQLite record protecting the *admission* does not raise this tier. | Code + Patroni docs; confirmed by absence of any certificate check target-side |

## Consequences for claims

1. All PIEQ protection demonstrated on this target is **pre-dispatch refusal** (epistemic +
   policy + concurrency gating) plus **post-hoc reconciliation**; the window between
   dispatch and SQL-observable effect is protected only by Patroni-native mechanisms.
2. "PIEQ prevented an unsafe promotion" therefore means: *PIEQ refused to dispatch* a
   promotion that a matched less-conservative policy would have dispatched — and the
   independent SQL observer confirms what Patroni then did or did not allow.
3. Within-assumption safety theorems that require Tier E2 freshness are **out of scope**
   on this target and are not claimed; the F5 revocation-race trial exists to *measure*
   the E0 window, not to pass it.
4. Split-brain absence is asserted ONLY from the independent SQL observer
   (`pg_is_in_recovery()`, timelines, write-witness rows on every node), never from PIEQ
   or Patroni logs.
