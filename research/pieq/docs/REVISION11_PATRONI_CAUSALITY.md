# REVISION 11 — EXP-07: Physical Causality in PostgreSQL/Patroni

**Status: PASS — causal attribution established for the supervised promotions; claims downgraded where the evidence requires.** Prereg: `results/rev11/exp07/prereg.json`.

## EXP-07A — Audit of the Revision 10 physical traces
Analyzer: `experiments/pg_patroni_failover/analyze_rev11_exp07a.py` → `results/rev11/exp07/exp07a_causality_audit.json`. Clock sources: one host wall clock for harness + observer (cross-process skew < ~1 s); observer sampling 1 Hz (every transition bounded ±1 s; sub-second states cannot be excluded — stated wherever exclusion is claimed).

All three Rev10 `f1_p4` promotions: fault bounded at [8.2–9.6 s] (or [0–1.2 s]) into the trial; earliest possible PIEQ dispatch = fault + 35 s lease-lapse wait + measured LLM inference (6.1–12.1 s) = 42.2–56.7 s; **no writable primary existed anywhere in [fault, earliest-dispatch]** (cluster paused: auto-promotion disabled by configuration, leader lock intact); first new writable primary observed at 57.6–66.9 s, i.e., 9–15 s AFTER earliest possible dispatch. Verdict per trial: **PIEQ-CAUSED** (within the declared supervised operating mode). The ~57–67 s recovery is a property of the supervised mode (lease lapse + inference + manual failover) and is NOT comparable to P0's 9.9–10.2 s auto-failover as if the availability policies were identical.

## EXP-07B — Controlled fresh comparisons (event-timestamped; logs in `rev10_logs/`)
| Condition | Outcome levels (proposal / authorization / physical effect) |
|---|---|
| P0 native | — / — / auto-promotion in 9.9 s, timeline bump, 0 dual-primary |
| P1 deterministic supervisor (prespecified: promote iff SQL∧OOB∧etcd all down; paused; same safeguards) | rule fired / n/a (no PIEQ) / promotion dispatched and observer-confirmed, 0 dual-primary |
| P2 PIEQ supervised (fresh, event-stamped) | live Sonnet proposed promote (verified typed derivations) / ADMIT (ν_pack=3, sole world) / dispatch → observer-confirmed promotion, 0 dual-primary |
| P3 auth refusal (Rev10 F5) | certified ν=3 / **REJECTED_AUTHORIZATION_REVOKED_PRE_DISPATCH** / zero effect |
| P4 commit-time policy on E0 (Rev10 F7) | certified / **fails closed** (CTS unenforceable on E0; missing policy fails closed) / zero effect |
| P5 asymmetric partition (fresh) | live model deferred / DEFER_PLAUSIBLE_WORLD_AMBIGUITY / leader kept serving, 0 dual-primary |

## Required attributions (manuscript language)
- What the LLM proposed: promotion (P2, 5/5 verified derivations) and deferral (P5).
- What PIEQ certified: P2's promotion over three container/network-layer evidence domains.
- What PIEQ blocked: P3 (revocation), P4 (temporal policy), P5 (ambiguity) — all pre-dispatch.
- What the target executed: all promotions ran through Patroni's native safe path; **split-brain prevention in every trial is attributable to Patroni/etcd, not PIEQ** — PIEQ is never described as preventing split-brain.
- Which transitions were autonomous Patroni: P0 (and the F9/OOA external switchover).
- **Causally attributable to PIEQ-authorized dispatch: the P2/f1_p4 promotions (4 total: 3 audited Rev10 + 1 fresh), under paused supervised mode.**
- P1 shows a conventional deterministic supervisor makes the SAME decision on clean leader death; PIEQ's measured increment over P1 is the certified-provenance/fault-budget machinery plus the P3/P4 enforcement gates and generality beyond a hand-coded rule — stated as such, with no claim that P1 is unsafe on these faults.
