# REVISION 11 — Formal / Implementation Consistency Audit

Scope: focused verification of the five audit areas (A–E); no theoretical redesign. Each
row cites the enforcing code path and the executed test(s). "Trusted" = assumption not
independently discharged by code.

## A. Complete-proof independence (Theorem 3 / Definition 3)

| Check | Code path | Verified by | Status |
|---|---|---|---|
| Footprint = physical leaves only (Φ_phys); ctx tokens excluded | `pieq/core.py` `justification_dependency_footprint(include_proposer_contexts=False)`, `physical_evidence_footprint` | `test_revision9_context_independence.py` (one agent × 3 domains = 3; 5 agents × 1 domain = 1; ctx-splitting inert) | OK |
| Nested derivations expose shared upstream leaves | DAG traversal via `direct_env_mask` + `sub_justifications` | `test_shared_upstream_evidence_visible_through_multihop_dags`; Rev11 `test_shared_upstream_leaf_footprints_overlap` | OK |
| Shared collectors/repeated leaves overlap | fd/ch/as tokens in footprint | Rev11 `test_shared_failure_domain_never_inflates_packing`; live F3 fold-in (`f2_p4` logs, ν=1) | OK |
| Dominated footprints removed; truncation fail-closed (lower bound) | `compute_disjoint_justification_packing_bounded` superset pruning + `budget_exhausted` | `test_theorems_and_counterexamples.py`; Rev11 truncation test | OK |
| Empty-footprint witnesses excluded | packing guard | `test_empty_physical_footprint_witness_cannot_inflate_packing` | OK |
| Deterministic completion cannot create false independence | completion registers via the UNCHANGED verifier; shares one ctx (inert); physical leaves unchanged | Rev11 `test_completion_context_cannot_create_false_independence`, shared-domain/leaf tests | OK |

## B. Plausible-world retention (Lemma 4)

| Check | Code path | Verified by | Status |
|---|---|---|---|
| World pruned only on f+1 disjoint verified refutations | `PlausibleWorldGate.compute_fault_tolerant_belief_set` | theorem tests; Rev11 `test_truncation_fails_closed_and_cannot_refute_true_world` | OK |
| Budget exhaustion retains all worlds | `budget_exceeded` branch | existing tests | OK |
| Completion cannot invalidate Lemma 4 | completion only ADDS verifier-accepted derivations from authenticated evidence; refutations of the true world would require f+1 *physically disjoint* false domains — exactly the Lemma 4 fault budget; truncation under-populates (conservative) | Rev11 completion suite (missing-sensor, incompatible-worlds, truncation, nogood tests); EXP-06 post-hoc true-world-retention check over all replays | OK |
| Snapshot-inconsistent refutations never packed | `filter_snapshot_consistent_witnesses` applied before packing | P1.3 suite; Rev11 drift test | OK |

## C. Concurrent execution (Theorem 5 / Cor. 1)

| Declared mechanism | Code path | Tested | Status |
|---|---|---|---|
| Contiguous commit-version barrier | `SnapshotContext` + gate checks | rev5/7 tests | OK (SW) |
| Per-domain admission-revision CAS | `CrossRootExposureLedger.admit_and_reserve` | rev7 tests; live C08-class; **EXP-08 scenario 4 on real PostgreSQL** | OK |
| Exposure accounting | ledger + SQLite boundary; **EXP-08 `SUM(amount)<=cap` under `FOR UPDATE`** | rev7 tests; EXP-08 scenarios 1/7/9 | OK |
| Predecessor ordering | SQLite boundary frontier; **EXP-08 `predecessors` check** | rev7 tests; EXP-08 | OK |
| UNKNOWN retention / no auto-redispatch | ledger `mark_timeout_or_cancel_race`; EXP-08 scenarios 10–11 (reconcile from authoritative `ops`, idempotent re-presentation only) | tests + EXP-08 | OK |
| Revocation/fencing tier distinctions | `TargetResourceBoundary` tiers; **control-plane vs physical atomicity never conflated**: Patroni REST = E0+native (contract doc); SQLite & real-PG adapter = E2 | tier tests; EXP-08; capability contract | OK |

## D. Temporal precondition policy

- Every mutation type requires trusted `MutationTemporalPolicy`; configured registry is exhaustive and fail-closed (`REJECTED_MISSING_TEMPORAL_PRECONDITION_POLICY`).
- **Rev11 change:** `temporal_policies=None` now requires `legacy_temporal_mode_opt_in=True` at construction (ValueError otherwise). All archival runners (`run_rev7_liveness_eval`, `run_rev8_independent_eval`, `run_rev8_pinned_live_eval`) carry the opt-in with a written justification; the hardened/release path cannot silently downgrade. Test: `test_hardened_config_requires_registry_unless_legacy_opt_in`.
- Live integrated enforcement: Rev10 E1/S1/H1 H30 decomposition; **EXP-08 scenario 5** (H30-analog at a real PostgreSQL effect boundary) and scenario-level stale-policy binding (point 9 of the E2 contract).

## E. Physical refinement — four evidence levels (never conflated)

1. **Mathematical conditional theorems** — Thm 3/5, Lem 4, Cor 1, Prop 6 (assumptions trusted: world-completeness, h_man domain mapping, rule soundness, complete writer mediation).
2. **Deterministic software validation** — 229-test suite; heldout36; Rev9/Rev10 software confirmation sets; SQLite E2 boundary.
3. **Real database transactional enforcement** — **EXP-08**: 10-point E2 contract on a Patroni-managed PostgreSQL 16 primary, 12 fault scenarios, invariant verified by independent superuser SQL; non-claims recorded (SERIALIZABLE success ≠ complete mediation/host independence/full Refines_D; superuser bypass outside mediation assumptions).
4. **Physical distributed behavior under fault injection** — EXP-03/EXP-07: supervised promotions (causally attributed via EXP-07A timelines), partition deferrals, pre-dispatch refusals — all under the Tier E0+native Patroni contract, single-VM common-cause disclosed, 1 Hz observer resolution disclosed. Level-4 claims are NEVER made from levels 1–3 evidence; the Patroni promotion adapter's classification is unchanged by EXP-08.

## Residual trusted assumptions (not independently discharged)
World-completeness (E1), rule soundness (E2), failure-domain manifest correctness (E3), absence of cross-domain common cause (E4), complete writer mediation (E6; physically violated on purpose in F9/12b controls), physical refinement beyond the tested adapters, and model-identity attestation at the CLI tier.
