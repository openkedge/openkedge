# REVISION 11 — EXP-06: Bounded Deterministic Refutation Completion

**Status: PASS within assumptions, with one STRICT-LETTER preregistration violation that is itself the experiment's most important finding (reported, not reclassified).**
Preregistered in `results/rev11/rev11_phase0_freeze.json`. Artifacts: `results/rev11/exp06/`, implementation `pieq/completion.py` (+ optional `evidence_completion_fn` hook threaded through `probe_loop.py`/`hardened.py`, default-off), adversarial suite `tests/test_revision11_completion.py` (13 tests).

## Mechanism and TCB expansion
`bounded_deterministic_completion` forward-chains ONLY over (a) primitive assumptions already registered and signature-verified in the ATMS and (b) the pre-existing trusted rule vocabulary, submitting every candidate through the UNCHANGED verifier. No oracle labels, fixtures, synthetic observations, future probe outcomes, or privileged telemetry. Fail-closed bounds (`max_new_derivations`, combo cap) can only UNDER-populate: positive packing keeps a sound lower bound; refutation shortfall retains worlds (Lemma 4 conservative direction). TCB grows by ~70 deterministic lines whose soundness reduces to the verifier's. Footprints remain physical (one shared completion context is packing-inert; adversarial tests confirm shared-domain/shared-leaf evidence can never inflate ν_pack).

## Results (paired with the 20 EXP-05 runs; n=240 replays per condition)
| Condition | CN10 | CN12 | Within-assumption unsafe | FPre commits | True-world refutations (within-assumption) | OOA CN11 |
|---|---|---|---|---|---|---|
| C0 baseline (EXP-05) | 11/20 | 9/20 | 0 | 0 | 0 | deferred 20/20 (model prudence) |
| C1 completion, no new probes (paired replay) | **20/20** | **20/20** | 0 | 0 | 0 | **admitted-unsafe 20/20** |
| C2 completion inside probe loop (paired replay) | **20/20** | **20/20** | 0 | 0 | 0 | **admitted-unsafe 20/20** |
| C3 live + TYPE-level missing-proof feedback (n=3 runs) | 3/3 | 3/3 | 0 | 0 | 0 | admitted-unsafe 3/3 |

All other within-assumption cases retain identical outcome classes to C0 in every condition (all 6 deferral controls CN03–CN08 deferred 120/120; commit-time control CN09 blocked 20/20, with 9/20 shifting from earlier epistemic deferral to atomic Tier-E2 commit-time rejection). Paired C1/C2-vs-C0 transition accounting over 240 case-replays per condition:
- **Actionable false blocks recovered (`FB → MIT`): 20** (9 on `CN10` + 11 on `CN12`, lifting actionable mitigations from 60/80 to 80/80 by supplying 42 individual missing derivations across those 20 runs: 20 on `CN10`, 22 on `CN12`).
- **Out-of-assumption admissions (`OOA_DEF → OOA_ADM`): 20** (all on `CN11`, unsound-rule $E_2$ control).
- **Total outcome-class flips (within + OOA): 40** out of 240 paired replays per condition; zero flips in any unsafe direction within assumptions.
- *Erratum note (pre-submission audit):* An earlier draft of this line wrote `20+11=31 flips` by erroneously substituting `20` for `CN10`'s `9` (`20−11`) flips before adding `CN12`'s `11` flips. Direct case-by-case reconstruction against `results/rev11/exp05/run_*.jsonl` and `results/rev11/exp06/c1_results.jsonl` / `c2_results.jsonl` confirms the exact evidence-backed count is **20 newly recovered actionable mitigations** (`9 CN10 + 11 CN12`).

## The key finding (and the prereg-letter violation)
The preregistered safety criterion read "no NEW unsafe admissions ... beyond C0" without scoping to within-assumption cases. CN11 — the out-of-assumption E2-class control whose TRUSTED rule set is deliberately unsound — flips from 0/20 admitted (C0) to 20/20 admitted-unsafe under C1/C2 (and 3/3 under C3). By the strict letter, that violates the criterion; we report it as such rather than rewording post hoc. The correct interpretation, consistent with every prior revision's taxonomy: **completion (and equally, feedback) removes the accidental protection of LLM reticence against trusted-model defects.** C0's CN11 safety was model luck — exactly the kind of non-guarantee PIEQ's formalism refuses to count — and no PIEQ mechanism claims protection when the trusted rule model itself is unsound (Proposition 6 boundary). Lemma 4 is unaffected (verified: zero within-assumption true-world refutations across 480 replays; adversarial truncation/missing-sensor/incompatible-worlds tests).

## Integration decision
The hook ships **default-off**. Enabling it is an explicit operator decision that (1) eliminates the measured liveness failure mode entirely, (2) makes outcome quality exactly as good as the trusted rule model — deterministically, with no prudence buffer. The manuscript states this trade plainly. Deterministic completion is positioned as an implementation mechanism (not a fourth contribution).
