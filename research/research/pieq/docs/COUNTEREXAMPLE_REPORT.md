# Adversarial Counterexample Report (PIEQ Revisions 3 & 4)

This report catalogs every invalid execution and naive theorem formulation exposed across Reviews 1–4, together with the concrete mechanism and regression test that now rejects or handles it safely.

## 1. Revision 4 New Adversarial Counterexamples (Tasks 1–5)

### CE-R4-1: Double-Counting Already-Committed Persistent Effects (Task 1)
- **Previous Vulnerability:** `S_tau^{base} = Post(hat{B}_tau^{(f)}, I_C)` unconditionally re-applied committed operations in `I_C` onto the plausible belief set. If telemetry at `v_snap = 5` already observed `drain_1` (committed at `v_commit = 5`), re-applying `drain_1` falsely advanced the base state to 2 drained nodes (`s_2`).
- **Resolution:** `SnapshotContext` anchors `hat{B}` at `[v_min, v_max]`. Commits with `v_commit <= v_min` remain in `I_C` for persistent exposure `C(d)` but are **never** re-applied to `S_tau^{base}`; only `v_commit > v_max` commits advance `S_tau^{base}` in ascending commit order, and `(v_min, v_max]` / unversioned commits conservatively retain both skip and apply branches.
- **Verified By:** `test_task1_already_committed_with_persistent_exposure_not_double_counted`, `test_task1_delayed_telemetry_snapshot_advances_ordered_commits`, `test_task1_commit_ack_after_newer_observation_and_mixed_versions`.

### CE-R4-2: Contradictory or Unreconciled `UNKNOWN` State After Restart (Task 1)
- **Previous Vulnerability:** No explicit check prevented an operation from simultaneously sitting in `I_C` and `I_U` or losing its commit-version ordering across gateway/target restarts.
- **Resolution:** `CrossRootExposureLedger._assert_disjoint_invariants` and `evaluate_concurrent_compound_safety_with_certificate` reject overlapping `I_C` / `I_U` IDs (`INVALID_DUPLICATE_COMMITTED_AND_UNKNOWN_OP`); `UNKNOWN` ops induce mutually exclusive no-effect / partial-effect / full-effect branches; `reconcile_after_restart` reconciles against durable SQLite WAL `committed_ops`.
- **Verified By:** `test_task1_unknown_partial_effect_disjoint_from_committed`, `test_task1_reconciliation_after_gateway_and_target_restart`.

### CE-R4-3: E1+ Non-Cancellable Operation Outliving Control-Plane Lease Expiry (Task 2)
- **Counterexample:** Target registers prepared intent with `lease_expiry = 10`, starts a non-cancellable external operation at `t = 9`, then stalls/partitions. Control-plane lease expires at `t = 10`, revocation declares fencing complete, and the external operation finishes at `t = 11`—violating post-fence exclusion.
- **Resolution:** `TargetResourceBoundary.initiate_revocation_at_time` refuses to declare fence completion on control-plane lease expiry unless `target_enforces_lease_at_commit` is `True` (and clock skew `clock_skew_bound` has elapsed). On non-lease-fenced targets, the operation remains in `unknown_prepared_ops` (`I_U`), blocking conflicting mutations indefinitely (`BLOCKED_BY_UNKNOWN_NONCANCELLABLE_OR_UNFENCED_OP`) until `FenceAck`, completion, or an out-of-band definitive barrier.
- **Verified By:** `test_task2_noncancellable_op_outlives_cp_lease_expiry_gap_closed`, `test_task2_lease_fenced_target_and_clock_skew_rejects_late_completion`.

### CE-R4-4: Mode C Unsafe Intermediate State with Matching Final States (Task 3)
- **Previous Vulnerability:** Checking only commutativity of final states (`u(a(s0)) == a(u(s0))`) or trusting unverified `commutes_with` tags admits concurrent execution when an intermediate state (`a(s0)`) exits `Viab(K)`.
- **Resolution:** `ModeCCompatibilityVerifier` ignores `commutes_with` tags, inspects every prefix state of every linear extension across all subsets in the bounded class (`|U_C| <= 4`), and rejects with `UNSAFE_POST_STATE_EXITS_VIABILITY` / `UNSAFE_INTERMEDIATE_STATE`.
- **Verified By:** `test_task3_mode_c_matching_final_states_unsafe_intermediate_rejected`, `test_task3_mode_c_partial_side_effects_and_cancel_commit_checked`.

### CE-R4-5: Pairwise Compatibility Failing on 3-Way Schedule & Multi-Predecessor `seq` Deadlock (Task 3)
- **Previous Vulnerability:** Pairwise compatibility (`u1 <-> u2`, `u1 <-> a`, `u2 <-> a`) does not guarantee safety of 3-way schedule `(u1, u2, a)`. Separately, requiring `seq(a) = seq(u) + 1` simultaneously for multiple Mode S predecessors with distinct sequence numbers is unsatisfiable.
- **Resolution:** `ModeCCompatibilityVerifier` exhaustively checks all $k$-way schedules up to `K_conc = 4` (and rejects `> 4` ops back to Mode S). Mode S in `SQLiteProtectedResourceBoundary` uses `required_predecessor_op_ids` (`Pred(a) <= Settled`) supporting multiple predecessors with distinct sequence numbers and `FENCED_NO_EFFECT` settlements.
- **Verified By:** `test_task3_mode_c_three_ops_pairwise_compatible_three_way_unsafe_rejected`, `test_task3_mode_s_multi_predecessor_frontier_and_duplicate_retries`.

### CE-R4-6: Cross-Group Global Quorum Bypass & Coordinator Crash Leak (Task 4)
- **Previous Vulnerability:** Single-domain reservations allow simultaneous mutations on `group_A` and `group_B` to exceed a shared `global_quorum_AB` cap; coordinator crash after reserving `d_1` could drop reservation.
- **Resolution:** `ConstraintDependencyGraph.domain_closure` closes `D(a)` onto `D*(a)`, `admit_and_reserve_multi_domain` checks all domains atomically, rolls back pre-dispatch on partial failure (`ROLLBACK_PARTIAL_RESERVATION`), retains reservations in `I_U` on coordinator crash (`COORDINATOR_CRASH_RETAINED_IN_IU`), and rejects non-atomic multi-domain unserialized writes.
- **Verified By:** `test_task4_simultaneous_cross_group_and_global_quorum_closure`, `test_task4_partial_reservation_rollback_and_coordinator_crash`, `test_task4_overlapping_domain_sets_and_external_writer_bypass`.

### CE-R4-7: Set-Packing Search Budget Exhaustion (Task 5)
- **Previous Vulnerability:** Unbounded branch-and-bound over `m <= 64` footprints could exceed latency budget or needed explicit fail-closed truncation semantics.
- **Resolution:** `compute_disjoint_justification_packing_bounded` enforces `max_expansions` (`B_pack`), returning a sound lower bound for positive support and forcing conservative world retention in `compute_fault_tolerant_belief_set` when truncated.
- **Verified By:** `test_task5_bounded_set_packing_truncation_fails_closed`.

---

## 2. Preserved Counterexamples from Revisions 1–3
- **CE-1 (Symmetric Averaging vs. Single-Agent Error):** `test_theorem_1_conformity_dynamics_and_counterexample_3_1c`
- **CE-2 (Berkson Conditioning in ATMS):** `test_theorem_2_berkson_counterexample_and_minimal_subset_preservation`
- **CE-3 (Menger Max-Flow = 3 vs. Complete-Proof Packing = 1 on Conjunctive Overlap):** `test_second_review_issue1_menger_min_cut_vs_complete_justification_packing`
- **CE-4 (Helly 3-World Pairwise Non-Disjointness):** `test_theorem_4_helly_three_world_and_provan_budget_overapproximation`
- **CE-5 ($f=0$ Single-Sensor Refutation Trap):** `test_second_review_issue3_fault_tolerant_belief_set_requires_ka_refutations`
- **CE-6 (One-Sided Concurrent Post-Subset Gate `a -> u -> s_bad`, CE-5A):** `test_revision3_task1_concurrent_reverse_interleaving_counterexample`
- **CE-7 (Premature Timeout/Cancel Release, CE-5B):** `test_revision3_task1_exposure_ledger_unknown_cancel_and_dedup`
- **CE-8 (Async Push Fence E1 & Lock+ReadIndex E1+-read Races):** `test_second_review_issue2_async_fence_race_and_tiers_e0_e1_e2`, `test_revision3_task2_e1_plus_read_index_race_and_quiescence_repair`
