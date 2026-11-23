# REVISION 11 — EXP-09: Repeated Agent-Count Comparison

**Status: PASS (executed as preregistered with a disclosed budget deviation: 5 paired repetitions instead of the 20-target; ~$41 CLI-reported for this experiment; condition order randomized per repetition by fixed seeds).** Artifacts: `results/rev11/exp09/` (15 complete 36-case traces + `exp09_analysis.json`).

## Result: the Revision 10 single-run equivalence does NOT fully survive repetition — and we report the reversal.

| Condition (5 paired reps × heldout36 = 180 execs: 155 within + 25 OOA) | Mitigations | Deferrals | Within-Assump. Unsafe / FPre | OOA Raw Unsafe | False blocks | Calls | First-attempt valid | Cost (CLI) | p50/case |
|---|---|---|---|---|---|---|---|---|---|
| S1 one Sonnet, full telemetry | 43/45 [Wilson .85–.99] | 85/85 | 0/155 / 0/25 | 11/25 | **2 (both H32)** | 199 | 146/180 | $5.20 | 6.8 s |
| H1 five homogeneous Sonnet | **45/45** [.92–1.0] | 85/85 | 0/155 / 0/25 | 20/25 | 0 | 920 | 355/900† | $22.27 | 26.4 s |
| M1 five heterogeneous (=E1) | **45/45** [.92–1.0] | 85/85 | 0/155 / 0/25 | 16/25 | 0 | 911 | 361/900† | $13.81 + Bedrock (unbilled) | 43.0 s |

† Partitioned-telemetry agents often legitimately hold evidence insufficient for any derivation; per-submission validity is not comparable across allocations.

**Within-assumption paired discordances:** exactly one case — `H32` (historical CA-revocation, snapshot-triggered): S1 false-blocked it in 2/5 repetitions where both multi-agent conditions mitigated it (same nondeterministic single-shot synthesis-miss mechanism EXP-05 quantified on CN10/CN12). H1 vs M1: zero within-assumption discordances. All OOA-control flips (`H16` $E_3$, `H33` $E_1$, `H34` $E_2$ [deferred 15/15], `H35` $E_5$, `H36` $E_6$) lie outside the safety contract and are reported separately (**47/75 raw unsafe OOA admissions** across the 75 OOA case-executions: 11/25 S1, 20/25 H1, 16/25 E1).

## Measured conclusion
- **Within-assumption safety matches the conditional theorem's agent-count invariance:** across 540 total case-executions (**465 within-assumption + 75 out-of-assumption**), observed within-assumption outcomes were **0/465 unsafe** and **0/75 false-precondition admissions**, with **255/255 mandatory deferrals** across conditions; separately, the 75 out-of-assumption control executions produced **47/75 raw unsafe admissions** (11/25 S1, 20/25 H1, 16/25 E1) as designed when trusted assumptions are violated.
- **Multi-agent redundancy provides a measurable liveness-robustness benefit under repetition** (≈2 recovered false blocks per 45 actionable executions in this sample) at ≈4.6× calls, ≈2.7–4.3× cost, and ≈4–6× latency. Model heterogeneity is not required for the benefit (H1 suffices).
- With n=5 repetitions and one discordant case, the benefit estimate is coarse (paired discordant count 2/45, zero reverse discordants); we make no universal claim and note possible benchmark saturation at the 45/45 ceiling.
- **Architectural implication:** EXP-06's bounded deterministic completion recovers the same class of misses (including the H32 mechanism) without additional agents — redundancy via trusted inference rather than via sampled proposers — at the measured out-of-assumption trade documented in EXP-06.

The theoretical claim is unchanged: PIEQ's fault tolerance depends on physical evidence domains. The empirical claim is now: on this benchmark, extra agents buy single-shot liveness robustness, not safety.
