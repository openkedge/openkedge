# PIEQ Revision 9 — Final Evidence Closure and Submission Readiness Report

**Paper:** *Provenance-Isolated Epistemic Quorums: Execution-Bound Evidence Certification and Plausible-World Safety Gating in Agentic Control Planes*
**Date:** November 21, 2026
**Runtime Environment:** macOS (Apple M-series), Python 3.15.2, pytest 9.2.0, SQLite WAL (`PRAGMA synchronous=FULL`)
**Headline status:** Both targeted correctness repairs (P1.1, P1.2) are CLOSED with regression tests; the P1.3 frozen confirmation set is CLOSED (12/12 first-run). A genuine live-LLM evaluation was EXECUTED at operator request via locally authenticated agent CLIs (unpinned evidence tier — see Deliverable B); the pinned-API live protocol and the physical PostgreSQL/Patroni experiment remain **BLOCKED**. **Revision 9 is still NOT declared empirically complete at the pinned/physical tier.**

---

## Deliverable A — Missing-Evidence Closure Report

| Gap | Required evidence | Exact experiment/proof | Status | Source artifacts | Remaining limitations |
|---|---|---|---|---|---|
| **P0.1 Pinned live-LLM evaluation** | ≥2 server-attested model families executing configs A–E on identical cases with full trace/cost capture | `scripts/run_rev8_pinned_live_eval.py --manifest experiments/pinned_live_manifest_template.json` | **PARTIALLY CLOSED** — unpinned CLI-wrapper live run EXECUTED (all 5 configs, 36 cases, 771 live calls; Deliverable B); pinned-API protocol itself still **BLOCKED — NOT EXECUTED** | Executed: `results/rev9_cli_live_traces_*.jsonl`, `results/rev9_cli_live_scored.json`, runner `scripts/run_rev9_cli_live_eval.py`. Blocked: preflight artifact `results/rev9_live_preflight_blocked.json` (`UNEXECUTED_MISSING_CREDENTIALS`); pinned runner preserved unmodified | CLI tier caveats: model identities unpinned (claude CLI-reported IDs, codex config-declared/unattested), sampling unenforced, CLI harness token overhead. Pinned tier still requires `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`. |
| **P0.2 Physical PostgreSQL/Patroni failover** | Real cluster faults with independent SQL ground-truth observer, 5 matched baselines, actual useful physical mutations | `experiments/pg_patroni_failover/run_experiment.py` (after `docker compose up -d`) | **BLOCKED — PHYSICAL EXPERIMENT NOT EXECUTED** | `run_experiment.py --check` → `UNEXECUTED_MISSING_RUNTIME`; testbed bundle preserved runnable | `docker`, `psql`, `patroni`, `etcd`, `podman`, `colima` all absent. Enforcement-boundary documentation completed below (Section P0.2-A); all physical split-brain claims remain conditional. |
| **P1.1 Proposer-context independence** | Footprint semantics separating physical evidence from context labels; 5 required property tests | `pieq/core.py` (`physical_evidence_footprint`, Φ_phys packing, physical Menger graph), `pieq/hardened.py`/`pieq/probe_loop.py` policy plumbing | **CLOSED** | `tests/test_revision9_context_independence.py` (6/6), manuscript Section 3.2 rewritten | Model-reasoning independence (notion iii) remains formally unmodeled by design; multi-agent benefit is an empirical claim only. |
| **P1.2 Mandatory commit-time policy** | Trusted per-mutation temporal policy, fail-closed, certificate-bound, immune to LLM omission | `MutationTemporalPolicy` + registry enforcement in `pieq/hardened.py`; `temporal_policy` cert field + `REJECTED_STALE_TEMPORAL_POLICY` in `pieq/core.py` | **CLOSED** | `tests/test_revision9_temporal_policy.py` (10/10), manuscript Section 5 addition | `temporal_policies=None` legacy mode is retained solely so archived Rev 7/8 runners reproduce; new deployments must configure the registry (documented). |
| **P1.3 Independent H31-repair confirmation** | Previously unseen adversarial set, frozen implementation, sealed oracle, verbatim reporting | `scripts/run_rev9_confirmation_eval.py` (C01–C12) after `results/rev9_frozen_hashes.json` freeze | **CLOSED** (with scope caveats) | First run preserved: `results/rev9_confirmation_results_frozen_firstrun.json` (9/9 deferrals, 3/3 mitigations, 0 unsafe, 0 false blocks); regression rerun: `results/rev9_confirmation_results.json` | Authored by the same party as the repair (not an external red team); deterministic software scenarios only; confirms the software temporal-audit layer, nothing live or physical. |
| **P2 Reporting separation** | Seven evidence categories, exact denominators, no pooled headline rates | Manuscript Section 6 opening rewritten | **CLOSED** | `paper/epistemic_quorums_atms_paper.tex` | — |
| **P3 Manuscript consolidation** | Three contributions centered, Rev 9 claims-discipline, PC audit | Paper + supplementary recompiled (0 LaTeX errors) | **CLOSED** | PDFs regenerated Nov 21, 2026 | — |

## P0.1 Blocker Detail and Unblock Procedure

`BLOCKED — LIVE EVALUATION NOT EXECUTED.`

To execute (estimated ≤ $50 spending cap enforced by the manifest):
```bash
cd openkedge/research/research/pieq
export ANTHROPIC_API_KEY=... OPENAI_API_KEY=... GEMINI_API_KEY=...
python3 scripts/run_rev8_pinned_live_eval.py \
  --manifest experiments/pinned_live_manifest_template.json --preflight   # verify endpoints_ready
python3 scripts/run_rev8_pinned_live_eval.py \
  --manifest experiments/pinned_live_manifest_template.json --trace results/rev9_live_traces.jsonl
```
Note before running: the manifest pins 2025-era snapshots (`claude-sonnet-4-5-20250929`, `gpt-4.1-2025-04-14`, `gemini-2.5-pro`); verify each is still served and update pins (and prices) in the manifest if deprecated — the runner rejects any response lacking a server-reported model ID. The runner enforces: ≥2 provider families, no CLI wrappers, no oracle access, bounded retries, all 12 trace fields, per-case outcome records (P0.1 measurement list), and the frozen Rev 8 vocabulary/verifier/probe policy. Configurations A–E map to `orig_freeform`, `typed_interface`, `typed_plus_probes`, single-model-full-telemetry, and `full_pieq`.

## P0.2 Blocker Detail, Enforcement-Boundary Documentation, and Unblock Procedure

`BLOCKED — PHYSICAL EXPERIMENT NOT EXECUTED.` Missing: Docker Engine (or compatible runtime) and `psql` client. Runnable sequence is in `experiments/pg_patroni_failover/README.md` (compose up → witness table → `split_brain_verifier.py` → `run_experiment.py` → compose down).

### P0.2-A — The True Enforcement Boundary (documented now, testable once unblocked)

1. **What exact physical operation PIEQ authorizes:** a Patroni leader promotion (`POST /switchover` / failover) or node drain on one PostgreSQL node, plus the etcd leader-key transition that Patroni serializes against.
2. **Where authorization is checked:** at the PIEQ admission controller (certificate + CAS) *before* the Patroni REST call; and atomically inside etcd transactions only for control-plane keys PIEQ owns.
3. **What constitutes physical commit:** PostgreSQL timeline increment on the promoted node (`pg_control_checkpoint().timeline_id`) and first accepted write — observed only by the independent SQL verifier, never inferred from the REST response.
4. **Target-native atomic check at the effect point:** PostgreSQL/Patroni provide their own (etcd leader-key CAS + Patroni lease); **PIEQ's certificate is NOT re-validated inside PostgreSQL's promotion path.**
5. **Actual tier of the promotion adapter:** the REST promotion is a **nontransactional external RPC → Tier E1+-quiescence at best** (post-FenceAck exclusion, pre-FenceAck race window), **never Tier E2**, regardless of any atomic etcd/SQLite transaction protecting the control-plane request. All E2 claims in the paper are scoped to targets where the store *is* the effect boundary.
6. **Stale authorization/retries/incomplete outcomes:** lost-ack promotions must remain `UNKNOWN` in the exposure ledger (no automatic retry without target-native idempotency); reconciliation is via the independent observer's timeline history.
7. **Writers that can bypass PIEQ:** any operator or Patroni auto-failover acting directly — complete writer mediation is a trusted external contract (E6/H36 class), held constant across baselines by design.
8. **Observable proof a fence is effective:** the old primary thereafter rejects writes (`pg_is_in_recovery() = true` or connection refusal) in the verifier's log, with zero overlapping writable-primary intervals in `pieq_write_witness`.

Acceptance evidence required when executed: at least some successful useful physical promotions (not only safe rejections), plus cases where PIEQ refuses mutations a less conservative baseline attempts, with all failures and false blocks reported and negative controls (F7–F9) separately identified.

---

## Deliverable B — Executed Live-LLM Results (Unpinned CLI-Wrapper Tier, Nov 21–22, 2026)

Operator-requested run through locally authenticated agent CLIs. **Genuine live inference on every proposal** — 771 calls, zero replay, zero oracle access, zero deterministic completion — but a **weaker evidence tier than the pinned-API protocol** (which remains blocked): `claude -p` reports CLI-surfaced model IDs without immutable snapshots (`claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-opus-5-5`); `codex exec` (Bedrock `openai.gpt-5.6-sol`) reports no model ID at all (config-declared, unattested); sampling parameters are not enforceable through either CLI. The rev8 pinned runner's evaluation core was reused verbatim (identical prompts, schema, certificate builder, verifier, probe policy, retry budget, trace fields); only the manifest validator differs (`scripts/run_rev9_cli_live_eval.py`).

| Config (36 held-out cases each) | Mitigations /9 | False blocks | Unsafe (within) /31 | Mand. deferrals /17 | False-precond /5 | Unjust-but-safe adm. | OOA unsafe /5 | Valid first→retry | Probes (eff) | p50/case | CLI cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A orig_freeform | 4/9 | 5 | 0 | 13/17 | 1/5 | 4 | 2/5 | 62→62/180 | 0 | 43.2 s | $2.80 |
| B typed_interface | 4/9 | 5 | 0 | 9/17 | 1/5 | 8 | 4/5 | 73→74/180 | 0 | 38.7 s | $2.68 |
| C typed_plus_probes | **9/9** | **0** | 0 | 9/17 | 1/5 | 8 | 4/5 | 69→74/180 | 17 (6) | 46.3 s | $2.85 |
| D single-model full-telemetry | **9/9** | **0** | 0 | 12/17 | **0/5** | 5 | 3/5 | 30→33/36 | 19 (8) | 6.4 s | $1.00 |
| E full_pieq (Tier E2) | **9/9** | **0** | **0** | **17/17** | 1/5 | **0** | 2/5 | 73→74/180 | 17 (6) | 44.4 s | $2.81 |

Required comparisons answered: (1) the corrected typed/probing pipeline improves live liveness without weakening safety (4/9 + 5 false blocks → 9/9 + 0, with 0/31 within-assumption unsafe everywhere); (2) multiple live agents offer no measurable benefit once evidence access is held constant (Config D matches at 5× fewer calls, ~$1 vs ~$2.85, 6.4 s vs ~44 s p50). Configs A–D admit 4–8 physically-safe-but-unjustified mutations on enforcement-deferral cases (H21–H29) — expected, since those deferrals are execution-boundary decisions present only in E. The single within-assumption crossing (`H30`, 1/5 false-precondition) reproduces live the legacy `temporal_policies=None` boundary; the run used legacy mode deliberately for comparability with Revision-8 software numbers, and the Revision-9 mandatory registry blocks exactly this admission (regression-tested). Zero provider failures after bounded retry; retries rescued 1–5 proposals per config. Artifacts: traces + summaries in `results/`, post-hoc scorer `scripts/score_rev9_cli_live.py` (sealed ground truth consulted only there).

## Deliverable C — Executed Physical Failover Results

**None.** See P0.2 blocker above. Local SQLite WAL / etcd results are reported only as implementation regression evidence.

## Deliverable D — Formal and Implementation Closure (P1.1, P1.2)

### P1.1 — Proposer-Context Independence (minimal model change)
- `Φ(Π)` split: **physical evidence footprint** `Φ_phys` (failure domains, collectors, assumption IDs — the sole basis of the deterministic `f = k_a − 1` theorem) vs. **optional proposer-context isolation policy** (ctx tokens; strictly conservative, opt-in via `proposer_context_isolation` / `require_proposer_context_isolation`).
- Empty-`Φ_phys` witnesses are excluded from packing (cannot be vacuously disjoint).
- Diagnostic Menger min-cut graph reduced to physical layers so Theorem 3(2) remains a valid upper bound.
- Manuscript states explicitly: **PIEQ is evidence-domain-based and agent-count agnostic**; measured multi-agent advantage is empirical, not a premise of the fault-tolerance proof.
- Required tests (all passing, `tests/test_revision9_context_independence.py`): (1) one agent × three domains ⇒ ν_pack = 3; (2) five agents × one domain ⇒ ν_pack = 1; (3) context-ID splitting does not raise ν_pack; (4) shared upstream evidence visible through multi-hop DAGs; (5) context policy restricts without redefining fault tolerance; (+) empty-footprint non-inflation.

### P1.2 — Mandatory Trusted Temporal-Precondition Policy
- `MutationTemporalPolicy(action, mode ∈ {SNAPSHOT_TRIGGERED, COMMIT_TIME_SENSITIVE}, trusted_commit_predicates, persistence_guarantee_attested)` lives in trusted config metadata; a configured registry is exhaustive and fail-closed (`REJECTED_MISSING_TEMPORAL_PRECONDITION_POLICY`).
- COMMIT_TIME_SENSITIVE requires Tier E2 atomic revalidation of trusted predicates **or** an attested persistence guarantee; otherwise `REJECTED_COMMIT_TIME_POLICY_UNENFORCEABLE` — never a silent downgrade.
- Trusted predicates are injected regardless of caller/LLM input; the canonical policy string is HMAC-signed into the certificate and re-checked inside `BEGIN IMMEDIATE` (`REJECTED_STALE_TEMPORAL_POLICY`).
- Snapshot-triggered actions incur no unnecessary revalidation and stay actionable after transient symptoms clear.
- Sufficiency (not necessity) of persistence/revalidation is preserved per the Rev 8 dip-and-return counterexamples.
- Required regressions (all passing, `tests/test_revision9_temporal_policy.py`, 10 tests): transient drop, dip-and-return, stale policy certificate, LLM omission override, out-of-transaction check rejected via non-atomic tier fail-closed, persistence alternative, historical event actionable, missing policy, legacy `None` reproducibility, dataclass validation.

**Full suite: 216 passed, 0 failed, 13 skipped** (skips = optional etcd-binary and live-credential tests).

## Deliverable E — Frozen Confirmation Results (P1.3)

- Freeze first: `results/rev9_frozen_hashes.json` (SHA-256 of all `pieq/*.py`) written before the confirmation set existed.
- Set: 12 adversarial cases (C01–C12) in a new `dns_anycast_cluster` domain, disjoint from frozen40/heldout36 vocabulary; sealed `ORACLE` map consulted only post-hoc by the scorer; no tuning followed.
- First execution: **9/9 correct mandatory deferrals, 3/3 correct mitigations, 0 unsafe admissions, 0 false blocks, 0 runner errors** — preserved verbatim in `results/rev9_confirmation_results_frozen_firstrun.json`. Each deferral fired the intended distinct mechanism (version drift, mixed snapshots, concurrent commit, manifest rotation, same-domain packing cap, P_sys conflict, stale cache vs. version observer, admission-revision CAS, commit-predicate rejection).
- Post-freeze change (disclosed): `compute_provenance_min_cut` physical-graph fix, found by manuscript/theorem consistency audit — **not** by the confirmation set; recorded in the freeze file's addendum; identical 12/12 rerun on the same cases is classified as a **regression run** (`results/rev9_confirmation_results.json`).
- Honest scope: same-author case design (not independent third-party labeling); deterministic software only.

## Deliverable F — Revised Manuscript and Supplement

- `paper/epistemic_quorums_atms_paper.tex` (+ PDF, 15 pp): Φ_phys formalism rewrite (Section 3.2, Theorem 3, proofs), mandatory temporal-policy paragraph (Section 5), seven-category evidence separation with explicit **NONE** rows for live and physical outcomes (Section 6), new Rev 9 confirmation subsection, updated UNEXECUTED section with re-verified preflights, limitations updated with the Patroni-REST tier classification, conclusion restated as "formally repaired framework with verified software prototype; physical autonomy unproven."
- `paper/epistemic_quorums_atms_supplementary.tex` (+ PDF, 5 pp): Revision 9 change log, reproducibility record (216 tests, confirmation run, both blockers), and formal blocker statement.
- Both compile with 0 errors; duplicate copies under `paper/pieq/` synced.

## Deliverable G — Final Submission Audit (PC-style)

1. **Are the principal theorems sound under stated assumptions?** Yes, as conditional results. Theorem 3's fault threshold now rests exactly on its true premises (trusted derivation rules + independent physical failure domains); the Rev 8 `iff` defect and the Rev 9 ctx-conflation are both repaired with executable counterexamples/tests. The proofs are careful but have not been machine-checked.
2. **Are the assumptions independently enforceable or externally trusted?** Mixed, and now honestly labeled: signature verification, snapshot binding, CAS, and Tier E2 SQLite semantics are enforced in code; world-completeness, failure-domain manifests, physical refinement, and complete writer mediation are trusted external contracts with negative controls (H16, H33–H36) showing what happens when they fail.
3. **Is the claimed system implemented coherently?** Yes for the software layer: one consistent codebase, 216 passing tests, hardened profile wiring every theorem contract, archived runners reproducible under a documented legacy mode.
4. **Does the evaluation isolate each contribution?** Yes in software: 2⁴ factorial + agent-count isolation separate packing, shielding, probing, and typed interface; the Rev 9 confirmation set isolates the temporal-audit repair.
5. **Is there genuine live-model evidence?** **Yes, at the unpinned CLI tier.** The corrected pipeline was driven end-to-end by live models (3 Claude + 1 GPT family via agent CLIs): 9/9 live mitigations, 17/17 mandatory deferrals, 0/31 within-assumption unsafe under full PIEQ, with the single H30 crossing at the known legacy temporal-policy boundary. A skeptical reviewer will still discount this tier for unpinned/unattested model identities and unenforced sampling; the pinned-API run remains the open item.
6. **Is there genuine physical execution evidence?** **No.** SQLite/etcd are real local boundaries but no external physical system (PostgreSQL promotion) has been controlled or fenced.
7. **What would a skeptical reviewer reject?** (a) Treating the unpinned CLI live run as equivalent to pinned, server-attested evaluation (identity/sampling caveats; one vendor account per family; single run, no repetition for variance); (b) any Tier-E2 reading of database failover — the paper now forecloses this explicitly; (c) multi-agent benefit claims — now explicitly empirical-only; (d) treating the 12/12 confirmation as independent validation — correctly scoped as same-author adversarial confirmation; (e) the trusted domain-model burden in real operations (rule/world authoring is unaddressed operationally).
8. **Ready for submission as what?** As a **formal framework plus verified systems prototype with explicitly scoped negative/blocked empirical sections** — suitable for a formal-methods-leaning systems venue or as a framework paper. **Not** submittable as a validated end-to-end autonomous-remediation system: that claim requires executing P0.1 and P0.2. Recommendation: either run the two blocked experiments first, or submit with the current explicit scoping and title the evaluation accordingly.

---

## Reproduction Commands (Revision 9)

```bash
cd openkedge/research/research/pieq
python3 -m venv .venv-rev9 && ./.venv-rev9/bin/pip install pytest numpy scipy requests
./.venv-rev9/bin/python -m pytest -q tests/            # 216 passed, 13 skipped
./.venv-rev9/bin/python scripts/run_rev9_confirmation_eval.py   # 12/12 (regression rerun)
./.venv-rev9/bin/python scripts/run_rev8_pinned_live_eval.py \
  --manifest experiments/pinned_live_manifest_template.json --preflight  # BLOCKED artifact
./.venv-rev9/bin/python experiments/pg_patroni_failover/run_experiment.py --check  # BLOCKED artifact
cd ../../../../paper && pdflatex epistemic_quorums_atms_paper.tex  # 15 pp, 0 errors
```
