# PIEQ Revision 11 — Closing Report

**Date:** 2026-11-22/23 · All preregistrations hash-frozen before execution (`results/rev11/rev11_phase0_freeze.json`, `results/rev11/exp07|exp08/prereg.json`). Historical artifacts untouched. Verified GitHub Actions CI run (`https://github.com/openkedge/openkedge/actions/runs/45072074097`, commit `525ff84e`): **242 pytest passed, 0 failed, 0 skipped in 170.35 s** (superseding prior local 229 passed, 13 skipped without `etcd`) + **140 distributed `etcd` fault trials passed**.

## Executive summary
Revision 11 converts Revision 10's anecdotes into quantified science: the live liveness failure is measured and mechanistically explained (EXP-05); a bounded deterministic completion mechanism eliminates it with a sharp, honestly-reported out-of-assumption trade (EXP-06); physical promotion causality is demonstrated, with PIEQ's physical increment narrowed by a deterministic-supervisor control (EXP-07); the strongest execution theorem finally runs on a real distributed database (EXP-08); and repeated agent-count comparisons **reverse our own Rev 10 claim** — multi-agent redundancy measurably buys single-shot liveness robustness, never safety (EXP-09). Several headline numbers got worse where honesty required; the paper is scientifically stronger for it.

## Prespecified vs. actual
| Exp | Status | Prespecified criterion | Actual |
|---|---|---|---|
| EXP-05 live reliability (20 reps, 240 execs, $5.47) | **PASS** | reproducible explanation of the Rev10 failure | Safety 0/0; CN10 11/20, CN12 9/20; all failures = omitted verifier-acceptable derivations (mostly refutations); retry never repairs valid-but-incomplete submissions |
| EXP-06 deterministic completion | **PASS within assumptions; strict-letter prereg criterion VIOLATED and reported** | liveness gain AND "no new unsafe admissions beyond C0" (wording unscoped) | C1/C2: all 20 actionable false blocks recovered (9 CN10 + 11 CN12 → 20/20 each; 42 derivations supplied; 0/220 within-assumption unsafe, 0/20 CN09 FPre, 0 true-world refutations in 480 replays); C3 feedback matches (n=3). OOA unsound-rule control CN11 flips deferred 20/20 → admitted-unsafe 20/20 (40 total outcome flips/240 replays): completion removes prudence-masking of trusted-rule defects (E2 stratum). Ships default-off |
| EXP-07 Patroni causality | **PASS** | causal ordering demonstrated or claim downgraded | All 3 Rev10 promotions PIEQ-caused (no writable primary before earliest dispatch; paused mode) + 1 fresh event-stamped rep; P1 deterministic supervisor matches the F1 decision → PIEQ's physical increment re-scoped to certification + P3/P4/P5 gating; split-brain prevention attributed to Patroni; 1 Hz resolution disclosed |
| EXP-08 real-PG Tier E2 | **PASS** | 10-point contract, no violation in 12-scenario fault set | All checks enforced in one SERIALIZABLE transaction on the live Patroni primary; concurrency serialized; crash/reconciliation safe; unmediated writer denied; SUM=10≤cap independently verified; 2 harness scenario bugs disclosed (first run preserved); Patroni promotion stays E0 |
| EXP-09 agent count (5 paired reps, $41.28) | **PASS** (budget deviation from 20-rep target disclosed) | report whatever the data supports | 540 execs = 465 within-assumption + 75 OOA: **0/465 within-assumption unsafe**, **0/75 FPre**, **255/255 deferrals**, and **47/75 raw unsafe OOA admissions** (11/25 S1, 20/25 H1, 16/25 E1); S1 43/45 (H32 FB ×2) vs H1/M1 45/45; multi-agent = observed liveness redundancy at 4–6× latency / 2.7–4.3× CLI cost; heterogeneity unnecessary; coarse estimate (n=5), saturation possible |
| Pinned multi-provider API eval | **UNEXECUTED/BLOCKED** | — | credentials still absent; claims withheld |

## Negative / failed results preserved
Rev10 CN primary run (2/4); EXP-06 strict-letter violation; EXP-09 reversal of the Rev10 equivalence claim; CN11/OOA exposures under completion and feedback; Patroni-native sufficiency for split-brain in every physical trial; F4/F6/F8-class faults not physically executed.

## Theorem–implementation consistency
`docs/REVISION11_FORMAL_IMPLEMENTATION_AUDIT.md`: all five audit areas verified; no theorem defect found; legacy `temporal_policies=None` now fail-closed behind an explicit opt-in; residual trusted assumptions enumerated (world-completeness, rule soundness, h_man, common-cause, writer mediation, identity attestation).

## Physical causal attribution
Four supervised promotions causally attributable to PIEQ-authorized dispatch (paused supervised mode); recovery-latency comparisons across availability policies explicitly avoided; single-VM common cause and 1 Hz sampling limits stated everywhere exclusion is claimed.

## Remaining limitations / blockers
Author-created labels throughout (no external validation); unpinned CLI model identities, uncontrolled sampling, Bedrock costs unbilled; pinned API evaluation blocked; E2 demonstrated for a bounded reservation vocabulary (promotion target remains E0); EXP-09 power limited (5 reps, one discordant case); physical F4/F6/F8 unexecuted.

## Reviewer-style assessment & recommendation
The three contributions are each supported at their claimed tier, every headline number traces to a hashed artifact (`results/rev11/REVISION11_EVIDENCE_MANIFEST.json`), negative results are prominent, and the strongest previously-missing links (physical causality; real-database E2; repetition-based uncertainty) now exist. Remaining soft spots a committee will probe: author-only labels, unpinned identities, single-VM physicality, and the small-n agent study — all disclosed in the abstract/limitations. **Recommendation: READY for submission as a systems paper with an explicit evidence ladder** (formal framework + verified implementation + live software-tier evaluation + physical case studies), with claims exactly as scoped in the Revision 11 manuscript; the pinned-identity evaluation remains the single most valuable follow-up.
