# PIEQ Revision 7 — Execution Semantics Closure and Practical Liveness Validation Report

**Paper:** *Provenance-Isolated Epistemic Quorums: Execution-Bound Evidence Certification and Plausible-World Safety Gating in Agentic Control Planes*
**Date:** November 20, 2026
**Runtime Environment:** macOS (Apple M-series), Python `3.13.9`, `pytest 9.1.1`, SQLite WAL (`PRAGMA synchronous=FULL`)
**Unavailable Infrastructure (Explicitly Marked Unexecuted):** Docker Engine, PostgreSQL (`psql`), Patroni, `etcd` binary, and live paid multi-provider LLM API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).

---

## Deliverable 1 — Correctness Closure (P0)

### P0.1: Tier E2 vs. Tier E1+-Quiescence Authorization Freshness (Corollary 1, Table 2, Proposition 6)

- **Defect Identified:** Revision 6 Corollary 1(c) asserted that both Tier E2 and Tier E1+-quiescence guarantee execution-time authorization freshness at the physical linearization point. In reality, Tier E1+-quiescence only enforces **fence-relative exclusion after durable `FenceAck` completion** ($t \ge t_{\mathrm{fenced}}$); an operation that passed the entry check and entered `prepared_slot` under $\mathsf{lock}_r$ can still commit physically during $(t_{\mathrm{rev\_init}}, t_{\mathrm{fenced}})$ while revocation blocks on $\mathsf{lock}_r$.
- **Minimal 6-Step Execution Trace (`test_p0_1_tier_e1_plus_quiescence_vs_tier_e2_authorization_freshness`):**
  1. $t=1$: Certificate $C$ minted with $\tau_{\mathrm{adm}}=10, \tau_{\mathrm{auth}}=5$.
  2. $t=2$: Target $r$ checks $\tau_{\mathrm{auth}} = 5 \ge 5$, acquires $\mathsf{lock}_r$, enters `prepared_slot`.
  3. $t=3$ ($t_{\mathrm{rev\_init}}$): Control plane initiates revocation $\tau_{\mathrm{auth}}^{\mathrm{cp}} \leftarrow 6$; barrier blocks on $\mathsf{lock}_r$.
  4. $t=4$ ($t_{\mathrm{eff}}$): Under Tier E1+-quiescence, the already-prepared operation commits physically (`COMMITTED_PRE_FENCE_ACK_AUTH_REVOKED`) *after* revocation initiation.
  5. $t=5$ ($t_{\mathrm{fenced}}$): $\mathsf{lock}_r$ drains, target flushes $\tau_{\mathrm{auth}}^{\min}[r] \leftarrow 6$, returns signed $\mathsf{FenceAck}$.
  6. $t=6$: Post-`FenceAck` attempts with $\tau_{\mathrm{auth}}=5$ deterministically reject (`REJECTED_AUTHORIZATION_REVOKED`).
  - **Tier E2 contrast (`SQLiteProtectedResourceBoundary`):** Because `revoke_authorization_atomically(6)` and `execute_fenced_mutation` serialize inside `BEGIN IMMEDIATE`, $\delta_{\mathrm{fresh}}^{\mathrm{E2}} = 0$ (no pre-barrier race window).
- **Repair:** Split Corollary 1(c) into **(c-E2) Execution-Time Authorization Freshness** (atomic at $t_{\mathrm{tx}}$ under Tier E2 + complete mediation) vs. **(c-E1+) Fence-Relative Authorization Exclusion** (zero commits post-$\mathsf{FenceAck}$ under Tier E1+-quiescence). Propagated through Table 2 (`tab:tiers`), Proposition 6, and `TargetResourceBoundary.execute_quiescence_handshake(..., concurrent_auth_revocation_epoch=...)` in `pieq/core.py`.

### P0.2: `SnapshotPreconditionValid(a, v_snap)` vs. `CommitPreconditionValid(a, t_eff)`

- **Defect Identified:** Theorem 3(1) certifies $\mathsf{SnapshotPreconditionValid}(a, v_{\mathrm{snap}})$ — that diagnostic precondition $c_a$ physically held at consistent observation snapshot $S_{v_{\mathrm{snap}}}$. It does **not** certify $\mathsf{CommitPreconditionValid}(a, t_{\mathrm{eff}})$ if a transient condition subsides before $t_{\mathrm{eff}}$ without violating invariant $\mathcal{K}$ or advancing $\tau_{\mathrm{adm}}$.
- **Minimal Counterexample (`test_p0_2_snapshot_precondition_vs_commit_precondition_truth`):**
  - At $v_{\mathrm{snap}}=10$, 3 failure-domain-disjoint collectors attest `obs:queue_high` ($\nu_{\mathrm{pack}}=3 \ge k_a=3$, isolating `s_overloaded`), so $\mathsf{SnapshotPreconditionValid}(\texttt{scale\_out\_cluster}, 10) = \mathsf{true}$ and $\mathcal{K}$ holds in all states.
  - Before $t_{\mathrm{eff}}$, transient load clears (`obs:queue_high = False`) without violating $\mathcal{K}$.
- **Repair:**
  - Explicitly define both predicates and state that PIEQ certifies $\mathsf{SnapshotPreconditionValid}(a, v_{\mathrm{snap}})$ by default — the intended semantics for historical-snapshot triggers (e.g., `revoke_iam_role` upon verified compromise, where commit-time revalidation is **not** forced).
  - For transient-state actions requiring $\mathsf{CommitPreconditionValid}(a, t_{\mathrm{eff}})$, require either an explicit temporal-persistence assumption $\mathsf{Persist}(c_a, [v_{\mathrm{snap}}, t_{\mathrm{eff}}])$ or opt-in Tier E2 atomic revalidation (`required_commit_predicates` / `commit_precondition_check` inside `SQLiteProtectedResourceBoundary.execute_fenced_mutation`, returning `REJECTED_COMMIT_PRECONDITION_FALSE` atomically).
  - Audited Proposition 6's $\mathrm{BadCommit}_T(a)$ definition so it indexes target tier $T$ and never conflates snapshot truth with commit-time truth or E1+-quiescence pre-`FenceAck` commits with E2 atomic freshness.

### P0.3: Remaining Formal Limitations
- Theorems 1–5 were not expanded (no defects found beyond P0.1/P0.2).
- Physical-to-abstract refinement ($\mathsf{Refines}_{\mathcal{D}}$), world completeness ($S_{v_{\mathrm{snap}}} \in \mathcal{S}_{\mathrm{cand}}$), physical failure-domain classification ($h_{\mathrm{man}}$), complete writer mediation, and target-native idempotency on remote nontransactional actuators remain explicit trusted external contracts tracked in Proposition 6.

---

## Deliverable 2 — Archived `r4` Live Five-LLM Pilot Failure Analysis

- **Artifact Audited:** `results/live_llm_traces_r4.jsonl` (SHA-256 `7e9a50ec6cb1a9e29f40a7dc880c0c3ee3994f76860216118b13d8a5921b0bfe`), manifest `results/live_primary_manifest_r4.json` (SHA-256 `10ffac48ea0838af5beb866cbc86b7fe34bf0d7552a42ca9e3261124951d987a`).
- **Provenance & Unpinned Identity Disclosure:** 800/800 requests completed (`200` Round-0). `agent_0` (`claude_cli` `claude-sonnet-5-5`, \$4.7919, 10.50 s median), `agent_1` (`claude_cli` `claude-haiku-5-5`, \$0.3395, 10.25 s median), `agent_2` & `agent_3` (`codex_cli`, declared `openai.gpt-5.6-sol`, **recorded `model_version = []` — unpinned CLI identity**, 21.08 s & 21.20 s median), `agent_4` (`local_openai` `Qwen/Qwen3-32B`, 8/160 malformed replies including 2 in Round-0, 18.98 s median). Preserved strictly as **exploratory evidence**, not a controlled pinned heterogeneous comparison.
- **Exact 8-Category Mutually Exclusive Primary Failure Breakdown (140/284 Round-0 Rejected Proofs):**

| # | Primary Failure Category | Count | Share of Rejections | Exact Root Cause in `r4` |
|---|---|---:|---:|---|
| 1 | Invalid proof structure or schema | 12 | 8.6% | All 12 from `agent_4` (Qwen3-32B) passing invariant ID `"conflict"` as a Horn `rule_id` (+ 2 top-level malformed Round-0 replies) |
| 2 | Incorrect action or goal binding | **95** | **67.9%** | 91 `action_not_requested` (`agent_0..3` prudently picked `probe`/`defer` on partial local views while submitting valid `act:*` proofs discarded by `live_protocol.py:251`) + 4 `refutation_not_requested` (`agent_0`) |
| 3 | Invalid Horn antecedent mapping | 33 | 23.6% | 29 `agent_4` (Qwen3 passing single bitmask `16` for unsatisfied rules) + 4 `agent_2` (Codex Case 88 OR-ing both local bits into 1-antecedent rules) |
| 4 | Incorrect primitive evidence identifiers | 0 | 0.0% | All parsed handles belonged to authorized local observations |
| 5 | Incorrect failure-domain / provenance refs | 0 | 0.0% | Server-owned collector signatures verified intact |
| 6 | Stale resource / epoch / version / manifest | 0 | 0.0% | Uniform fixture snapshot/epoch in `r4` |
| 7 | Semantically invalid despite schema-valid | 0 | 0.0% | Caught at Categories 1–3 |
| 8 | Insufficient independent evidence (per-proof) | 0 | 0.0% | Manifested at case aggregation: 14/19 actionable cases remained short of $k_a$ after decoupling due to partial rule omission & 0/549 executed probes |

- **Exact Counterfactual Replay on Frozen `r4` Round-0 LLM Replies (ZERO new LLM calls, ZERO TCB weakening):**
  - `R4-A` (Archived strict coupling, no probes): `144/284` (`50.7%`) verified, **`0/19`** actionable mitigations (`19/19` false blocks), `21/21` safe deferrals, **`0/40` unsafe**.
  - `R4-B` (Decoupled evidence submission, no probes): `239/284` (`84.2%`) verified, **`5/19` (`26.3%`)** actionable mitigations (`14/19` false blocks), `21/21` safe deferrals, **`0/40` unsafe**.
  - `R4-D` (Decoupled shield-only without positive packing): `239/284` verified, `7/19` admitted, of which **`2` (`Cases 72, 74`) lacked $k_a$ positive proof packing** (`uncertified_positive_admissions = 2`).
  - `R4-E` (Decoupled + bounded read-only probe rescue, $\le 4$ probes, authentic physical failure domains): **`19/19` (`100.0%`)** actionable mitigations (`0/19` false blocks), `21/21` safe deferrals, **`0/40` unsafe**, `43` probes executed (`33` effective).

---

## Deliverable 3 — Working Typed Proposal Pipeline & Bounded Diagnostic Probe Loop

- **Typed Proof-Submission Interface (`pieq/typed_pipeline.py`):**
  - Exposes finite verifier-known `TypedVocabulary` (permissible actions, candidate worlds, `M_sys` rule catalog, authorized evidence handle IDs $\to$ proposition text only).
  - Decouples local `proposed_action` (`probe`/`defer`/mutation) from `derivations: [{rule_id, evidence_handles}]`.
  - Deterministically rejects any untrusted injection of `FORBIDDEN_UNTRUSTED_FIELDS` (`signature`, `failure_domain`, `env_bitmask`, `auth_epoch`, `manifest_hash`, etc.).
  - `TypedCertificateBuilder` computes `consequent` and `env_bitmask` deterministically from server-side collector-signed `BoundPrimitiveAssumption` records and hands off to the **unchanged** `DeterministicRuleVerifier` and `BoundedATMSEngine`.
  - Bounded structured feedback & retry (`run_typed_proposal_with_feedback`, `max_retries=2`) repairs malformed proposals without leaking other agents' observations or weakening TCB acceptance criteria.
- **Active Diagnostic Probe Loop (`pieq/probe_loop.py`):**
  - Implements the full 10-step bounded read-only workflow (`DiagnosticProbeSpec`, `ProbeBudget`, `run_active_diagnostic_probe_loop`).
  - Enforces `read_only=True`, `safety_contract_verified=True`, load weight cap, cost cap, invocation cap, and wall-clock timeout fail-closed.
  - Ranks probes via deterministic worst-case world reduction / partition discrimination + per-action/global failure-domain novelty (plus opt-in `calibrated_evsi` when calibrated priors & utilities are supplied).
  - Preserves recursive failure-domain provenance: verified in `test_active_probe_loop_preserves_provenance_and_blocks_unsafe_probes` that executing a separate read-only probe sharing `failure_domain="fd_0"` with passive sensors **does not inflate $\nu_{\mathrm{pack}}$** (`1`, never `2`), whereas a genuinely disjoint OOB probe (`fd_oob_bmc`) advances $\nu_{\mathrm{pack}}$ from `1 -> 2` and unlocks safe mitigation.

---

## Deliverable 4 — Hardened Reference Configuration & Regression Suite Outcomes

- **Module:** `pieq/hardened.py` (`HardenedPIEQConfig`, `TargetRefinementAttestation`, `HardenedAdmissionController`, `CONTRACT_IMPLEMENTATION_MATRIX`).
- **Contract-to-Implementation Matrix Highlights:**
  - **Core Python (`pieq/core.py`):** enforces HMAC rule/nogood verification, exact 64-bit disjoint packing with fail-closed budget caps, conservative $f$-fault-tolerant belief retention, Mode S/C schedule verification, and signed `TargetRefinementAttestation` checking (`UNVERIFIED_ASYNC` rejected fail-closed). **`CrossRootExposureLedger` is IN-MEMORY ONLY and does NOT provide crash durability by itself.**
  - **SQLite WAL Adapter (`SQLiteProtectedResourceBoundary`):** single-host crash durability (`PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL`), atomic Tier E2 epoch + `auth_epoch` + `auth_valid_until` (fail-closed clock) + `manifest_hash` + HMAC cert signature + `op_id`/identity deduplication + Mode S predecessor frontier + opt-in atomic `required_commit_predicates` + `record_fenced_no_effect` collision safety.
  - **`etcd` Adapter (`pieq/etcd_boundary.py`):** Raft-replicated linearizable KV Tier E2 epoch, exposure cap, durable `op_id`/identity, predecessor frontier, and `UNKNOWN` timeout retention (manifest/auth checks remain control-plane-side unless extended into key compares).
  - **External Physical Target:** trusted contract for complete writer mediation, physical `Refines_D(R1/R2)`, and target-native idempotency on remote nontransactional actuators.
- **Exact Regression Commands & Outcomes (Python `3.13.9`, `pytest 9.1.1`):**
  1. Core formal + Revision 6 + Revision 7 hardened suite:
     ```bash
     python3 -m pytest -q tests/test_theorems_and_counterexamples.py \
       tests/test_revision4_formal_repairs.py tests/test_soundness_pass_repairs.py \
       tests/test_sqlite_boundary_identity.py tests/test_revision5_execution_refinement.py \
       tests/test_revision6_audit_repairs.py tests/test_revision7_execution_closure.py \
       tests/test_revision7_pipeline_and_probes.py tests/test_revision7_hardened_profile.py
     ```
     **Outcome:** `64 passed in 0.24s` (0 failed, 0 skipped).
  2. Full repository regression suite:
     ```bash
     python3 -m pytest -q tests/
     ```
     **Outcome:** `185 passed, 13 skipped in 29.52s` (0 failed; the 13 skips are optional local `etcd` binary tests).

---

## Deliverable 5 — Reproducible Five-Condition Evaluation (`results/rev7_liveness_summary.json`)

- **Command:** `python3 scripts/run_rev7_liveness_eval.py`
- **Explicit Scope Label:** Track 2 uses deterministic `r4`-calibrated software proposers (`0.00 USD`, 0 LLM tokens) running through the real `TypedCertificateBuilder`, `ActiveDiagnosticProbeLoop`, `HardenedAdmissionController`, and on-disk Tier E2 SQLite WAL (`PRAGMA synchronous=FULL`). Frozen 40-case labels in `pieq/live_cases.py` (`19` passive oracle-actionable, `20` bounded passive-ambiguous, `1` fault-exceeded stress Case 89) were **never modified**. Live multi-provider API runs require `--manifest` with server-attested API providers across $\ge 2$ families (unpinned CLI providers are refused by `--preflight` unless `--allow-exploratory-cli` is passed).
- **Summary (40 Frozen Cases + 10-Case False-Precondition Slice + 21-Case Interactive OOB Probe Evaluation):**
  - `A` (Orig. strict, no probes): `180/361` (`49.9%`) verified, **`0/19`** actionable mitigations (`19/19` false blocks), `21/21` safe deferrals, **`0/40` unsafe**, median `1.62 ms`.
  - `B` (Typed, no probes): `55` initial rejections repaired (`240` calls), `306/306` post-retry verified, **`14/19` (`73.7%`)** actionable mitigations (`5/19` false blocks on 5 partial-omission cases), `21/21` safe deferrals, **`0/40` unsafe**, median `1.71 ms`.
  - `C` (Typed + Probes) & `E` (Full PIEQ Hardened): **`19/19` (`100.0%`)** actionable mitigations (`0/19` false blocks), `21/21` safe deferrals, **`0/40` unsafe**, `22` read-only probes executed (`8` effective), `40/40` unsafe non-read-only control probes blocked, median `1.89 ms` (`p95 2.40 ms`).
  - `D` (Shield, No Positive Packing): `14/19` on 40 frozen cases, **`10/10` false-precondition admissions** (`0` invariant violations) on the 10-case False-Precondition Companion Slice vs. **`0/10`** under `E` (`Full PIEQ`).
  - Matched Tier E2 Baselines: `Single-Agent (Unshielded)` = `16/19` actionable mitigations, **`12/40` unsafe**; `Single-Agent + Shield` = **`0/19` mitigations (`19/19` false blocks)**, `0/40` unsafe; `Debate (3/5 Majority)` = `7/19` mitigations, **`16/40` unsafe**.
  - Interactive OOB Probe Resolution on 21 Frozen Passive-Defer Cases (frozen labels kept intact): **`14/14`** OOB-distinguishable bounded cases safely resolved (`0` unsafe), **`6/6`** shared-domain trap cases safely deferred (`DEFER_ESCALATE`), **`1/1`** Case 89 stress safely deferred, **`21/21`** non-read-only probes blocked.

---

## Deliverable 6 — Physical Database Failover Testbed (UNEXECUTED)

- Delivered complete runnable bundle in `experiments/pg_patroni_failover/`:
  - `docker-compose.yml` (3 PostgreSQL 16/Patroni nodes + 3-member `etcd` v3.7.3 quorum)
  - `patroni_node.yml` (identical native Patroni HA/fencing baseline across all 5 comparisons)
  - `split_brain_verifier.py` (independent SQL ground-truth dual-primary & `pieq_write_witness` verifier)
  - `run_experiment.py` (5 matched comparisons, F1–F6 within-assumption & F7–F9 outside-assumption faults, 4-category falsification evaluator)
  - `README.md` (physical failure-domain separation matrix + single-host Docker caveat + exact commands)
- **Status:** **UNEXECUTED** (`python3 experiments/pg_patroni_failover/run_experiment.py --check` reports `UNEXECUTED_MISSING_RUNTIME: docker binary not found in PATH, psql unavailable`).

---

## Deliverable 8 — Revision 7 Change Log

| Area | Revision 6 Defect / Limitation | Revision 7 Resolution | Status |
|---|---|---|---|
| **P0.1 Formal** | Corollary 1(c) overclaimed Tier E2 atomic auth freshness for Tier E1+-quiescence | Split into (c-E2) atomic execution-time freshness ($\delta_{\mathrm{fresh}}^{\mathrm{E2}}=0$) vs. (c-E1+) post-`FenceAck` exclusion; added minimal 6-step trace + `concurrent_auth_revocation_epoch` in `core.py` + `test_p0_1_*`; propagated to Table 2 & Prop. 6 | **Closed & Verified** |
| **P0.2 Formal** | Conflated snapshot truth of $c_a$ at $v_{\mathrm{snap}}$ with commit-time truth at $t_{\mathrm{eff}}$ | Introduced `SnapshotPreconditionValid(a, v_snap)` vs. `CommitPreconditionValid(a, t_eff)`; documented historical-snapshot default (no forced revalidation) vs. opt-in Tier E2 `required_commit_predicates` / `commit_precondition_check` in SQLite WAL; audited Prop. 6 `BadCommit_T`; added `test_p0_2_*` | **Closed & Verified** |
| **P1 Forensics** | Archived `r4` pilot had `0/40` mitigations and `140/284` rejected proofs with no root-cause classification | Audited `live_llm_traces_r4.jsonl` into 8 mutually exclusive categories (95 action/goal coupling, 33 antecedent mapping, 12 schema); proved decoupling alone yields `239/284` verified & `5/19` mitigations (`0` unsafe), + bounded probe rescue yields `19/19` (`0` unsafe) on frozen `r4` trace | **Closed & Verified** |
| **P1 Pipeline** | LLMs had to compute raw bitmasks & couple proofs to local action vote; 0/549 probe requests executed | Built `pieq/typed_pipeline.py` (finite vocabulary, forbidden server-owned fields, bounded retry) and `pieq/probe_loop.py` (10-step read-only probe loop preserving recursive failure-domain provenance) with zero TCB weakening | **Closed & Verified** |
| **P1 Hardening** | Strict guards defaulted off in `core.py` for legacy tests; in-memory vs. durable roles blurred | Built `pieq/hardened.py` (`HardenedPIEQConfig`, signed `TargetRefinementAttestation`, `HardenedAdmissionController`, `CONTRACT_IMPLEMENTATION_MATRIX`) clearly separating core Python RAM vs. SQLite WAL vs. `etcd` vs. external targets | **Closed & Verified** |
| **P1 Live Re-eval** | Unpinned Codex CLI identity in `r4`; no 5-condition controlled evaluation | Preserved `r4` as exploratory; executed 5-condition + baseline hardened software evaluation (`results/rev7_liveness_summary.json`) + 10-case false-precondition slice + 21-case OOB probe evaluation; added `--preflight` rejecting unpinned CLI models. *New pinned multi-provider live API run awaits live provider credentials.* | **Software Eval Executed; Live API Run Pending Keys** |
| **P2 DB Testbed & Falsification** | Section 6.3 DB testbed was prose-only; FC1–FC4 conflated engineering viability with formal falsification | Delivered runnable `experiments/pg_patroni_failover/` bundle explicitly marked **UNEXECUTED**; replaced FC1–FC4 with strict 4-category falsification framework separating soundness, empirical benefit, practical viability, and assumption violations | **Runnable Spec Delivered (Unexecuted); Framework Closed** |
| **P3 Manuscript** | 13 pages dominated by scripted 100-scenario fixture; dense tables | Consolidated to crisp print-readable layout centering live forensics, typed pipeline, probing, and safety–liveness results; demoted 100-scenario fixture to compact supporting regression subsection; moved extended tables/audit details to Supplement | **Completed & Compiled** |
