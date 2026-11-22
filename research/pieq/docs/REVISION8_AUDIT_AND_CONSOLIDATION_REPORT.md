# PIEQ Revision 8 — Independent Validation, Counterfactual Integrity, and Final Scientific Consolidation Report

**Paper:** *Provenance-Isolated Epistemic Quorums: Execution-Bound Evidence Certification and Plausible-World Safety Gating in Agentic Control Planes*
**Date:** November 20, 2026
**Runtime Environment:** macOS (Apple M-series), Python `3.13.9`, `pytest 9.1.1`, SQLite WAL (`PRAGMA synchronous=FULL`)
**Unavailable Infrastructure (Explicitly Marked UNEXECUTED):**
1. Live pinned multi-provider LLM API credentials (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` all unset; `codex` CLI is unpinned and disallowed for controlled pinned runs).
2. Isolated container/cluster runtime (`docker`, `psql`, `patroni`, `etcd` binaries unavailable on authoring host).

---

## Deliverable 1 — Revision 7 Independent Audit Report

| Category | Finding in Revision 7 | Severity | Revision 8 Disposition |
|---|---|---|---|
| **Resolved in Rev 7** | Tier E2 atomic auth freshness ($\delta_{\mathrm{fresh}}^{\mathrm{E2}}{=}0$) vs. Tier E1+-quiescence post-$\mathsf{FenceAck}$ exclusion; snapshot vs. commit-time precondition split; `r4` 8-category rejection forensics; typed proof interface (`typed_pipeline.py`); hardened profile (`hardened.py`) | Resolved | Preserved intact |
| **Remaining Formal Problem (P1.2)** | Corollary 1(a) asserted an **invalid biconditional** (`iff`) claiming $\mathsf{CommitPreconditionValid}(a, t_{\mathrm{eff}})$ holds *iff* persistence or Tier E2 atomic revalidation holds. Disproved by dip-and-return (`True -> False -> True`) and coincidental persistence counterexamples. | High (Formal) | **Repaired** to sufficient-condition theorem distinguishing actual commit-time truth from a sound system guarantee; stated authoritative atomic boundary contract; added executable counterexample tests (`test_p1_2_*`) |
| **Remaining Formal / Runtime Bug (P1.3)** | `probe_loop.py` and `PlausibleWorldGate.compute_fault_tolerant_belief_set` did not anchor or check cross-proof snapshot consistency across probing rounds, allowed cross-version conjunctive rule firing, did not detect concurrent commits/manifest rotation during probing, and **never re-checked `P_sys` invariants** after probe arrival (exposing a **real unsafe invariant violation on held-out `H31`** and unregistered `observed_conflict` nogoods on `r4` Cases `1, 39, 71`). | High (Soundness) | **Repaired** via `proof_snapshot_signature`, `filter_snapshot_consistent_witnesses`, multi-round `TemporalAuditRecord`, concurrent-commit/manifest observers, and automatic post-probe `P_sys` nogood registration; verified by 8 regression tests (`test_p1_3_*`) and zero-retuning `H31` flip (`UNSAFE -> CORRECT_MANDATORY_DEFERRAL`) |
| **Evaluation Confound #1 (P0.1)** | `R4-E` was presented as bounded probing reaching `19/19`, yet all `43` probes copied already-present passive `case.observations`, leaked the realized observation support set into the probe menu, and used TCB forward-chaining to auto-complete omitted LLM rules. Track 2's 21-case OOB evaluation constructed probe rules, `target_actions`, and `world_outcome_map` **directly from oracle `case.true_world` and `case.intended_action`**. | Critical (Methodology) | **Audited & Disclosed** in full 40-case ledger (`results/rev8_r4_replay_ledger.json`, `docs/REV8_COUNTERFACTUAL_PROVENANCE_AUDIT.md`); preserved `R4-A/B/D/E` verbatim with corrected labels + added leakage-free `R4-E-strict` (`108` probes vs. `43`, confirming $2.5\times$ menu-leakage bias) |
| **Evaluation Confound #2 (P1.1)** | `Single-Agent + Shield` (`0/19`) was doubly disadvantaged: restricted to `agent_0`'s 1/5 local telemetry AND artificially capped at $\nu_{\mathrm{pack}} \le 1$ by `TypedCertificateBuilder`'s single shared `context_id` per agent. Track 2 ablations varied multiple factors simultaneously. | High (Causal Fairness) | **Resolved** via controlled agent-count isolation (`1A_LocalTel`, `1A_FullTel_SharedCtx`, `1A_FullTel_DomainIsolated`, `5A_PartTel`, `5A_FullTel`) and complete $2^4 = 16$-cell + `No-Shield` mechanism-isolation factorial across both `heldout36` and `frozen40` |
| **Evaluation Confound #3 (P1.4)** | Revision 7 reported post-retry `306/306` (`100%`) verified proofs in the headline table, obscuring first-attempt failure rates, retry costs, and the 4–5 order-of-magnitude gap between local TCB latency and live LLM inference. | Medium (Reporting) | **Resolved** via full 10-stage proof-generation funnel and multi-scale latency/cost breakdown (`results/rev8_workflow_cost_accounting.json`) |
| **Deployment Limitations** | No live pinned multi-provider LLM re-evaluation executed (credentials unset); no physical PostgreSQL/Patroni cluster failover executed (Docker/`psql` unavailable); `CrossRootExposureLedger` in `core.py` is RAM-only; physical refinement $\mathsf{Refines}_{\mathcal{D}}$ and world-completeness remain trusted external contracts. | Explicit Scope Boundary | **Preserved & Explicitly Labeled**; executable runners & tests retained; zero escalation from software/SQLite results to live-LLM or physical split-brain claims |

---

## Deliverable 2 — Counterfactual Provenance Audit (P0.1)

See full standalone audit in [`REV8_COUNTERFACTUAL_PROVENANCE_AUDIT.md`](./REV8_COUNTERFACTUAL_PROVENANCE_AUDIT.md) and complete 40-case machine-readable ledger in `results/rev8_r4_replay_ledger.json` (SHA-256 `22f63dbf...`).

Key verified findings:
- `R4-A` (`144/284`, `0/19` mitigations, `0/40` unsafe), `R4-B` (`239/284`, `5/19`, `0/40` unsafe), `R4-D` (`239/284`, `7/19`, `2` uncertified-positive admissions on Cases `72, 74`), and `R4-E` (`19/19`, `0/40` unsafe) are preserved verbatim.
- **`R4-E` re-classified** as **fixture-backed offline counterfactual replay with deterministic `M_sys` rule completion**: 43/43 probes copied passive observations already sent to `r4` agents, and 4/14 rescued cases (`6, 14, 22, 80`) already had sufficient positive packing (`act_pack >= k_a`) and missed only refutation rules.
- **Leakage-free `R4-E-strict`** (public incident-class schema menu, zero support-set filtering) executes **`108` probes** (`28` effective post-P1.3; `31` pre-P1.3) vs. `43` in `R4-E`, retaining `19/19` mitigations and `0/40` unsafe while exposing the $2.5\times$ probe-count understatement caused by fixture menu filtering.
- **Track 2 21-case interactive OOB evaluation** is explicitly re-labeled **oracle-backed offline counterfactual replay** (`run_rev7_liveness_eval.py:1103-1170` read `case.true_world` and `case.intended_action` directly).

---

## Deliverable 3 — Independent Held-Out Evaluation (P0.2) & Causal Fairness (P1.1)

### Freeze Integrity
Before creating or examining any held-out results, Revision 7 core hashes were frozen in `results/rev7_frozen_hashes.json`:
- `pieq/core.py`: `c43c9de06f975024...`
- `pieq/typed_pipeline.py`: `e7e5df62fd357fd1...`
- `pieq/probe_loop.py`: `bc79da948c2c159a...`
- `pieq/hardened.py`: `9148234192d10b4f...`

### Disjoint 36-Case Held-Out Benchmark (`pieq/heldout_cases.py`, `dataset_public_hash=f31fcba3...`, `dataset_full_hash=0e1ef0e2...`)
- **0% overlap** with `pieq/live_cases.py` across 4 brand-new domains (`bgp_route_leak`, `storage_quorum_fence`, `k8s_control_plane_drain`, `tls_ca_rotation`), world IDs, mutation names, and rule IDs (verified in `test_p0_2_heldout_disjoint_from_frozen40_and_covers_all_archetypes`).
- **Independent physical-spec ground truth** across all 4 required classes and 9 archetypes (4 cases each):
  - `oracle_actionable_safe_mitigation`: **9** (`H01–H04` passive, `H05–H08` probe-recoverable, `H32` historical-snapshot trigger)
  - `safe_but_diagnostically_unjustified`: **5** (`H17–H20` false-precondition passive controls, `H30` un-revalidated transient drop)
  - `correct_mandatory_deferral`: **17** (`H09–H12` unresolvable uncertainty, `H13–H15` correlated/shared-domain within assumptions, `H21–H24` unauthorized execution, `H25–H28` concurrent/stale certs, `H29` E2-revalidated transient drop, `H31` mid-probe plant version jump)
  - `out_of_assumption_failure`: **5** (`H16` $E_3$ misclassified $h_{\mathrm{man}}$, `H33` $E_1$ omitted world, `H34` $E_2$ unsound rule, `H35` $E_5$ $>f$ faulty domains, `H36` $E_6$ unmediated external writer bypass)

### Unretuned Frozen Rev 7 vs. Post-P1.3 Held-Out Results (`5A_PartTel` + Leak-Free Probes + Tier E2 SQLite WAL)
- **Frozen Revision 7 (`results/rev8_heldout_frozen_rev7.json`):**
  - Actionable mitigations: `9/9` (`0` false blocks; `4` rescued by leak-free domain probes `H05–H08`).
  - Within-assumption unsafe invariant violations: **`1/31` (`H31`)** — caught a real cross-snapshot probing bug in frozen Revision 7 combining passive `v=50` with probe `v=51`!
  - Within-assumption false-precondition / stale-precondition admissions: **`1/5` (`H30`)** — `0/4` on passive false-precondition controls (`H17–H20`), `1/1` on `H30` (omitted Tier E2 revalidation on transient drop, confirming P1.2), vs. `0` on `H29` (revalidated).
  - Out-of-assumption negative controls (`H16, H33–H36`): **`5/5` admitted unsafe** (confirming Proposition 6 boundary contracts honestly!).
- **Post-P1.3 Repair (`results/rev8_heldout_post_p13.json`, zero retuning of dataset, proposer, or probe ranking):**
  - **`H31` flips `UNSAFE_INVARIANT_VIOLATION` $\to$ `CORRECT_MANDATORY_DEFERRAL`** (`DEFER_SNAPSHOT_INCONSISTENT:TEMPORAL_VIOLATION_OBSERVATION_VERSION_DRIFT`).
  - Within-assumption unsafe invariant violations: **`0/31`**, mandatory deferrals: **`17/17`**, actionable mitigations: **`9/9`**, unjustified false-precondition admissions: **`1/5` (`H30` only; `0/4` on `H17–H20`)**, out-of-assumption controls: **`5/5`**.

### P1.1 Agent-Count Isolation (Holding Evidence, Verifier, Probes, and Tier E2 Constant)

| Configuration | Held-Out 36 (No Probes): Mitig (9) / FB / Unsafe(in) / Calls | Held-Out 36 (With Probes, Post-P1.3): Mitig (9) / FB / Unsafe(in) / Probes(Eff) | Frozen 40 (No Probes, No Omission): Mitig (19) / FB / Unsafe / Calls |
|---|---:|---:|---:|
| `1A_LocalTel` (Rev 7 disadvantaged) | 0/9 / 9 / 0 / 36 | 8/9 / 1 / 0 / 31 (16) | 0/19 / 19 / 0 / 40 |
| `1A_FullTel_SharedCtx` (Rev 7 single-ctx bug) | **0/9 / 9 / 0 / 36** | **8/9 / 1 / 0 / 27 (12)** | **0/19 / 19 / 0 / 40** |
| **`1A_FullTel_DomainIsolated`** (Fair 1-agent) | **5/9 / 4 / 0 / 36** | **9/9 / 0 / 0 / 17 (6)** | **19/19 / 0 / 0 / 40** |
| `5A_PartTel` (5 agents, partitioned) | 5/9 / 4 / 0 / 216 | 9/9 / 0 / 0 / 17 (6) | 19/19 / 0 / 0 / 240 |
| `5A_FullTel` (5 agents, full telemetry) | 5/9 / 4 / 0 / 216 | 9/9 / 0 / 0 / 17 (6) | 19/19 / 0 / 0 / 240 |
| `1A_FullTel_Unshielded` (Matched E2) | -- | 9/9 / 0 / **6** (+5/5 FP) / 0 | -- |
| `5A_FullTel_Debate` (3/5 Majority + E2) | -- | 9/9 / 0 / **6** (+5/5 FP) / 0 | -- |

**Key Causal Finding (P1.1):** Multiple LLM agents are **NOT** required for PIEQ's epistemic safety or liveness once physical telemetry handles are held constant: `1A_FullTel_DomainIsolated` matches `5A_PartTel` and `5A_FullTel` across every safety and liveness metric on both `heldout36` and `frozen40` with **$6\times$ fewer proposer calls**. Multi-agent partitioning is warranted only when organizational/privacy boundaries partition telemetry across agents or when diverse LLM models mitigate single-model synthesis recall variance.

### P1.1 Complete $2^4 = 16$-Cell Mechanism-Isolation Factorial Highlights (Post-P1.3, `5A_PartTel`, Tier E2 SQLite WAL)

| Factorial Cell (`T`yped, `P`osPack, `R`efutePack, Pro`B`es) | Held-Out 36: Mitig(9) / FB / Unsafe(in) / FP(in) / MDefer(17) / Prb(Eff) | Frozen 40: Mitig(19) / FB / Unsafe(40) / Defer(21) / Prb(Eff) | Isolated Mechanism Demonstrated |
|---|---:|---:|---|
| `T0_P1_R1_B0` (Legacy strict coupling) | 0/9 / 9 / 0 / 0 / 17/17 / 0 (0) | 0/19 / 19 / 0 / 21/21 / 0 (0) | Action coupling causes 100% passive paralysis |
| `T1_P1_R1_B0` (+ Typed decoupled) | 5/9 / 4 / 0 / 1 / 17/17 / 0 (0) | 14/19 / 5 / 0 / 21/21 / 0 (0) | Typed interface unlocks passive actionable cases |
| `T1_P0_R1_B0` (- Positive packing) | 5/9 / 4 / 0 / **5** / 17/17 / 0 (0) | 14/19 / 5 / 0 / 21/21 / 0 (0) | Dropping positive packing keeps `unsafe=0` yet admits **4/4** unjustified passive mutations (`H17–H20`) + `H30` |
| `T1_P1_R0_B0` (- Refutation packing, $f{=}0$) | 1/9 / **8** / 0 / 1 / 17/17 / 0 (0) | 0/19 / **19** / **6** / 15/21 / 0 (0) | Naive $f{=}0$ pruning lets 1 faulty domain eject $S_{\mathrm{true}}$: `6/40` unsafe on `frozen40`, `8/9` false blocks on `heldout36` |
| `T1_P0_R0_B0` (- Both packings) | 5/9 / 4 / **4** / **5** / 13/17 / 0 (0) | 0/19 / 19 / **6** / 15/21 / 0 (0) | Losing both causes both unsafe invariant violations & false-precondition admissions |
| `T1_P1_NO_SHIELD_B0` (No plausible shield) | 5/9 / 4 / **1** / 1 / 16/17 / 0 (0) | 14/19 / 5 / **7** / 14/21 / 0 (0) | Dropping shield admits `H09` Helly trap (`1` unsafe) and `7/40` unsafe on `frozen40` |
| **`T1_P1_R1_B1` (Full PIEQ, Post-P1.3)** | **9/9 / 0 / 0 / 1\*/ 17/17 / 17 (6)** | **19/19 / 0 / 0 / 21/21 / 17 (5)** | Complete mechanism: `0` within-assumption invariant violations, `9/9` & `19/19` mitigations (\*sole FP = `H30` omitted E2 reval) |

*(Full 17-row matrices for both datasets are archived in `results/rev8_heldout_post_p13.json` and the Supplementary Material.)*

---

## Deliverable 4 — Pinned Live-LLM Evaluation Runner (P0.3 — UNEXECUTED)

- Delivered executable runner `scripts/run_rev8_pinned_live_eval.py` + 3-family pinned template manifest `experiments/pinned_live_manifest_template.json` (`anthropic`, `openai`, `gemini`; `manifest_sha256=2629c9ff...`, `schema_sha256=ec595230...`).
- Records all 12 required P0.3 fields across all 4 compared protocols (`orig_freeform`, `typed_interface`, `typed_plus_probes`, `full_pieq`) on the exact same cases and fails closed on missing server-reported model IDs (`UNATTESTED_SERVER_MODEL_ID`) or unpinned CLI wrappers.
- Verified via 4 unit tests in `tests/test_revision8_live_runner.py` (`4 passed in 0.17s`).
- Live execution status via `--preflight`: **`UNEXECUTED_MISSING_CREDENTIALS`** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` unset). No deterministic software proposer result is substituted as live LLM evidence.

---

## Deliverable 5 — Formal Consistency Repair (P1.2 & P1.3)

- **P1.2 (Corollary 1 & Proposition 6):** Replaced the invalid `iff` in Corollary 1(a) with a sufficient-condition implication separating actual commit-time truth $\mathsf{CommitPreconditionValid}(a, t_{\mathrm{eff}})$ from a sound system guarantee ($\mathsf{SnapshotPreconditionValid} \land \mathsf{Persist}$, OR $T{=}\mathrm{E2}$ authoritative atomic revalidation inside the shared mutation boundary $t_{\mathrm{tx}}$). Added executable Counterexamples 1 & 2 (`test_p1_2_*`). Preserved Tier E2 vs. Tier E1+-quiescence authorization freshness cleanly.
- **P1.3 (Adaptive Probe Temporal & Snapshot Consistency):** Enforced existing Assumptions 1–3 across multi-round probing without adding a new theorem (`filter_snapshot_consistent_witnesses` in `pieq/core.py`, `TemporalAuditRecord` + concurrent-commit/manifest observers + post-probe `P_sys` nogood registration in `pieq/probe_loop.py`, strict propagation in `pieq/hardened.py`). Verified all 7 adversarial boundaries + repeated same-domain non-inflation in `tests/test_revision8_formal_and_probes.py` (`11 passed in 0.17s`).

---

## Deliverable 6 — Physical PostgreSQL/Patroni Failover Testbed (P2 — UNEXECUTED)

- Retained and hardened `experiments/pg_patroni_failover/` (`docker-compose.yml`, `patroni_node.yml`, `split_brain_verifier.py`, `run_experiment.py`, `README.md`) encoding all 10 P2 required controls and 9 metric families, explicitly distinguishing SQLite WAL admission commits from physical PostgreSQL promotion effects.
- Verified via `python3 experiments/pg_patroni_failover/run_experiment.py --check`: **`UNEXECUTED_MISSING_RUNTIME`** (`docker` and `psql` unavailable on host). Zero physical split-brain exclusion claims are made from software/SQLite runs.

---

## Deliverable 7 — Complete Proof-Generation Funnel & Multi-Scale Cost Accounting (P1.4)

Artifact: `results/rev8_workflow_cost_accounting.json` (generated by `scripts/profile_rev8_workflow_costs.py`).

### Proof-Generation Funnel (`heldout36`, `5A_PartTel` vs. `1A_FullTel_DomainIsolated`)
- **`5A_PartTel`:** `216` raw proposals (`180` first-attempt + `36` retries, `0` retry exhaustions); first-attempt proposal validity: `144/180` (`80.0%`) schema/Horn/provenance-valid; first-attempt derivations: `277` submitted $\to$ `241` schema-valid (`87.0%`) $\to$ `205` Horn-valid (`74.0%`) $\to$ `205` provenance-valid & accepted (`74.0%`); post-retry accepted certificates: `205/205`; cases reaching $k_a$: `21` before probes $\to$ `25` after probes (`4` admitted after active probing, `12` rejected for insufficient evidence/snapshot drift, `21` correctly deferred).
- **`1A_FullTel_DomainIsolated`:** `36` raw proposals (`0` retries), `36/36` first-attempt valid, `204/204` first-attempt accepted derivations, `21 -> 25` reaching $k_a$, `4` admitted via probing, `21` correctly deferred.

### Multi-Scale Latency & Cost Separation (Apple M-Series, Python 3.13.9, SQLite WAL `synchronous=FULL`)
| Scale / Stage | Source | p50 | p95 | Notes |
|---|---|---:|---:|---|
| First-attempt typed build + verify CPU | `heldout36` profiler | 0.087 ms | 0.168 ms | Local TCB CPU |
| Retry verification CPU overhead | `heldout36` profiler | 0.002 ms | 0.013 ms | Excludes live LLM retry RTT (~15.7 s!) |
| ATMS packing + plausible-world shield CPU | `heldout36` profiler | 0.031 ms | 0.057 ms | Exact 64-bit B&B |
| Active probe loop TCB temporal-audit CPU | `heldout36` profiler | 0.205 ms | 0.310 ms | Multi-round audit CPU |
| Pre-initialized Tier E2 SQLite WAL commit | `heldout36` profiler | 0.092 ms | 0.113 ms | `BEGIN IMMEDIATE`, `synchronous=FULL` |
| End-to-end local TCB to useful action | `heldout36` (9 actionable) | **0.492 ms** | **0.579 ms** | Local TCB + SQLite WAL only |
| Simulated OOB probe I/O (when invoked) | `heldout36` (8 cases) | 20.01 ms | 38.02 ms | Configured probe RPC latencies |
| Distributed Raft coordination (`etcd` 3.7.3) | `etcd_local_fault_evidence.json` | 45--2,467 ms | -- | 140 local 3-node trials (normal to quorum-loss) |
| Archived `r4` live LLM single-call inference | `live_llm_traces_r4.jsonl` (800 calls) | **15,719 ms** | **40,746 ms** | 19.78M in / 607k out tok; \$5.13 Claude CLI |
| Archived `r4` Round-0 parallel 5-agent wall clock | `live_llm_traces_r4.jsonl` (40 cases) | **24,249 ms** | **97,615 ms** | Sequential sum: 82.7 s p50 / 178.7 s p95 |
| Physical PostgreSQL/Patroni promotion | `pg_patroni_failover/` | **UNEXECUTED** | **UNEXECUTED** | Explicitly unmeasured |

---

## Deliverable 9 — Exact Reproducibility Record

### Executed Commands & Verified Outcomes (Python `3.13.9`, `pytest 9.1.1`)
1. `python3 scripts/audit_rev8_counterfactual_provenance.py` $\to$ `results/rev8_r4_replay_ledger.json` (40-case ledger; verified `R4-A/B/D/E` + `R4-E-strict`).
2. `python3 scripts/run_rev8_independent_eval.py --mode frozen-rev7` $\to$ `results/rev8_heldout_frozen_rev7.json` (verified against `results/rev7_frozen_hashes.json` prior to P1.3 edits).
3. `python3 scripts/run_rev8_independent_eval.py --mode post-p13` $\to$ `results/rev8_heldout_post_p13.json` (post-P1.3 zero-retuning run).
4. `python3 scripts/profile_rev8_workflow_costs.py` $\to$ `results/rev8_workflow_cost_accounting.json`.
5. `python3 scripts/run_rev8_pinned_live_eval.py --manifest experiments/pinned_live_manifest_template.json --preflight` $\to$ `UNEXECUTED_MISSING_CREDENTIALS`.
6. `python3 experiments/pg_patroni_failover/run_experiment.py --check` $\to$ `UNEXECUTED_MISSING_RUNTIME`.
7. `python3 -m pytest -q tests/` $\to$ **`200 passed, 0 failed, 13 skipped`** (optional local `etcd` binary tests) in `30.02s`.

### Key Artifact SHA-256 Digests (`results/rev8_reproducibility_hashes.json`)
- `pieq/core.py` (post-P1.3): `db119ce0ba28284ef6f48c2e3eabf931b4789ca56bc6c6ceeb690659ae0bc617`
- `pieq/probe_loop.py` (post-P1.3): `bbefa66ad0d1f12090b25b5e6bd5995f0bd575aa716124a87718a0fe2661c927`
- `pieq/hardened.py` (post-P1.3): `3982f024fc9048f1b3f34f88cb8d6fbf39230d490fe4ea3f08d0071bab973b8d`
- `pieq/heldout_cases.py`: `5cddb0d22e71e5ca39281064d26b2ddcbce69aa7e64e5b790058dd6484dfb198`
- `results/rev7_frozen_hashes.json`: `5bbff93ac37af4f320c44c174443afa0875f6a48e79768807287503b589feaae`
- `results/rev8_r4_replay_ledger.json`: `22f63dbf08071881ab124b1818f7bdd7b26e010db70d5bedcacdd0d55d638731`
- `results/rev8_heldout_frozen_rev7.json`: `003b9de82f761e649c63d03ba77f2bd7ac1cca03505247c0c47d57fc72726e8f`
- `results/rev8_heldout_post_p13.json`: `93c4c4e4f7a2c73f93660c17e78f973aa5c3303de000111a180ffbb2b69bf255`
- `results/rev8_workflow_cost_accounting.json`: `d929e957d29eb5306425570cfc2da6c0f9838cfa540a6360a65907d0730ab842`
