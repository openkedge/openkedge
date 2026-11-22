# PIEQ Realistic Database Failover Testbed (Section 6.3 / Revision 7 P2)

> **EXECUTION STATUS: UNEXECUTED**
> Docker, PostgreSQL (`psql`), Patroni, and `etcd` binaries were unavailable on the authoring host at Revision 7 release time. This directory provides the complete, runnable configuration, ground-truth verifier, fault-injection specification, and 4-category falsification evaluator. No simulated or manufactured PostgreSQL/Patroni results are reported anywhere in the manuscript.

## Topology & Controls

- **Cluster:** 3 PostgreSQL 16 / Patroni nodes (`pg1`, `pg2`, `pg3`) coordinated by a 3-member `etcd` v3.7.3 Raft quorum (`etcd1`, `etcd2`, `etcd3`).
- **Identical Native Safety Baseline:** All 5 compared configurations share the exact same [`patroni_node.yml`](./patroni_node.yml) (`synchronous_mode: true`, `ttl: 30`, `use_pg_rewind: true`, automatic watchdog), separating Patroni-native HA safety from PIEQ's incremental epistemic and admission protection.
- **Independent Ground-Truth Verifier:** [`split_brain_verifier.py`](./split_brain_verifier.py) queries every PostgreSQL node directly over SQL (`pg_is_in_recovery()`, `pg_control_checkpoint().timeline_id`, `pieq_write_witness`) rather than trusting PIEQ's ledger or Patroni's REST API.
- **Safety vs. Authorization Separation:** Records both *unsafe mutation authorization* (at the control plane) and *actual physical dual-primary writes* (observed independently in PostgreSQL).

## Physical Failure-Domain Separation Audit

| Channel | Emulated Docker Network | Required Physical Separation (Multi-Host) | Single-Host Docker Caveat |
|---|---|---|---|
| `D1_etcd_lease` | `pieq_db_net` (172.28.0.0/24) | Dedicated `etcd` Raft hosts + control VLAN | Shares Docker host kernel/storage; **merge domains if testing host-level faults** |
| `D2_pg_sql_health` | `pieq_db_net` (172.28.0.0/24) | Dedicated SQL probe host on data VLAN | Distinct from `D1` only under network-partition/process faults |
| `D3_bmc_power` | `pieq_oob_net` (172.29.0.0/24) | Out-of-band IPMI/Redfish BMC network | Separate container bridge emulates OOB network partition |
| `D4_storage_fence` | `pieq_oob_net` (172.29.0.0/24) | Storage fabric / SCSI PR reservation controller | Must not share authority or power with `D1`/`D2` in production |

Any channels sharing a common switch, host, storage array, or credential authority MUST be merged into a single failure domain `D(e)` before computing $\nu_{\mathrm{pack}}$.

## Five Required Matched Comparisons

1. `patroni_native_ha_baseline`: Native Patroni/etcd HA without LLM mutation proposals.
2. `llm_proposal_matched_controls`: LLM-based proposal admission with matched Patroni/etcd target controls.
3. `positive_packing_no_shield`: Theorem 3 disjoint positive proof packing without Lemma 4 plausible-world shield.
4. `conservative_shield_no_packing`: Lemma 4 plausible-world shield without Theorem 3 positive packing.
5. `full_pieq_with_probing`: Full PIEQ (typed proof submission + positive packing + plausible-world shield + active read-only OOB probes + Tier E2 fencing).

## Exact Execution Commands (Isolated Disposable Host Only)

```bash
# 1. Verify runtime prerequisites (Docker Engine + psql client)
python3 run_experiment.py --check

# 2. Launch isolated 3-node PostgreSQL/Patroni + 3-member etcd cluster
docker compose up -d

# 3. Initialize independent SQL write-witness table on current primary
psql "postgresql://postgres:pieq_eval_pg_only_disposable@172.28.0.21:5432/postgres" \
  -c "CREATE TABLE IF NOT EXISTS pieq_write_witness (seq BIGSERIAL PRIMARY KEY, writer_node TEXT NOT NULL, timeline INT NOT NULL, written_at TIMESTAMPTZ DEFAULT now());"

# 4. Run independent SQL ground-truth dual-primary check
python3 split_brain_verifier.py

# 5. Execute all 5 comparisons across within-assumption (F1-F6) and outside-assumption (F7-F9) faults
python3 run_experiment.py

# 6. Teardown disposable environment
docker compose down -v
```
