# PIEQ Revision 4 Reproducibility & Evidence Classification Report

## 1. Execution Environment & Source Revision

- **Git Repository:** `/Users/joehe/openkedge/openkedge` (`git@aaronjhe:openkedge/openkedge.git`)
- **Base Commit Audited:** `b5b498fc6c14dde0cd6f84f5cb30f938ebe6b316` (plus Revision 4 PIEQ additions in `research/pieq/`)
- **Operating System:** macOS 26.5.2 (Build `25F84`, arm64 / Apple M-series)
- **Python Runtime:** Python 3.13.9
- **SQLite Engine:** SQLite 3.50.4 (WAL journal mode)
- **Key Dependencies:** `numpy==2.5.1`, `pytest==9.0.3`
- **Node/TypeScript OpenKedge Suite:** Verified independently via `npm test -- --silent` (16 suites: 104 passed, 1 pre-existing skipped)

## 2. Exact Commands & Measured Outcomes

```bash
cd research/pieq

# 1. Run full mathematical, counterexample, Revision 4 repair, and SQLite WAL suite
python3 -m pytest -v tests/
# Observed outcome: 36 passed in 0.43s

# 2. Regenerate 100-scenario simulation, 13 ablations, 2 negative controls, and 1,000-trace latency profile
python3 scripts/run_benchmark.py
# Observed outcome: Exit 0; writes results/benchmark_summary.json
```

## 3. Summary of Measured Benchmark & Pure Latency Results

### 3.1 100-Scenario Synthetic Protocol Simulation (`seed = 202611`)
- **Free-Form Debate (Overlapping):** Unsafe = `38.0%` `[28.5, 48.3]`, Correct = `54.0%`, Contingency vs. Full PIEQ = `(1, 37, 0, 62)`, McNemar `p = 1.46e-11`.
- **Blind Voting (Overlapping):** Unsafe = `29.0%` `[20.4, 38.9]`, Correct = `61.0%`, Contingency = `(1, 28, 0, 71)`, `p = 7.45e-09`.
- **Free-Form Debate (Orthogonal):** Unsafe = `24.0%` `[16.0, 33.6]`, Correct = `68.0%`, Contingency = `(1, 23, 0, 76)`, `p = 2.38e-07`.
- **ATMS Nogood-Only (Pillar I):** Unsafe = `8.0%`, Correct = `78.0%`, Defer = `14.0%`.
- **ATMS PCE + Nogood (Pillar I):** Unsafe = `8.0%`, Correct = `90.0%`, Defer = `2.0%`.
- **Full PIEQ Control Plane (I–III):** Unsafe = `1.0%` `[0.03, 5.45]` (Scenario 99 only: 3-of-5 independent domain fault exceeding $f=2$), Correct = `96.0%`, Defer = `3.0%`, Stale In-Flight Leaked = `0/12`, Stale Rejected = `12/12`.
- **Boundary Ablations:**
  - `B0 (menger_flow_only_no_packing)`: `7.0%` unsafe (all 6 overlapping conjunctive traps + Scenario 99).
  - `B1 (pairwise_gate_only)`: `9.0%` unsafe (all 8 Helly cases + Scenario 99).
  - `B2a (proxy_only_no_target_fence)`: `8.0%` unsafe, `7/12` stale leaked.
  - `B2b (async_push_fence_tier_e1)`: `5.0%` unsafe, `4/12` delayed-fence races leaked.
  - `B3 (naive_belief_pruning_f0)`: `7.0%` unsafe (all 6 single-sensor refutation traps + Scenario 99).
  - `B4 (no_concurrent_exposure_gate)`: `7.0%` unsafe (all 6 concurrent dual drains + Scenario 99).

### 3.2 Pure Wall-Clock Latency Profile (1,000 Traces, On-Disk SQLite WAL, Zero Synthetic Jitter)
Measured directly via `time.perf_counter_ns()` in `profile_proxy_admission_traces` against a real temporary on-disk SQLite WAL database (`results/benchmark_summary.json`):

| Stage | p50 (ms) | p95 (ms) | p99 (ms) | Mean (ms) |
| :--- | ---: | ---: | ---: | ---: |
| 1. `V_rule` & 64-Bit ATMS Label Update | 0.0273 | 0.0323 | 0.0361 | 0.0282 |
| 2. `hat{B}_tau^{(2)}` Shield & Concurrent Gate | 0.0028 | 0.0038 | 0.0058 | 0.0033 |
| 3. Complete-Proof Set Packing (`nu_pack`) | 0.0089 | 0.0108 | 0.0138 | 0.0103 |
| 4. On-Disk SQLite WAL Tier-E2 Check | 0.0357 | 0.0773 | 0.1778 | 0.0476 |
| **End-to-End Trace Total (Measured Jointly)** | **0.0762** | **0.1264** | **0.2708** | **0.0893** |

## 4. Honest Evidence Classification (Strict Category Separation)

| Claim / Mechanism | Category | Exact Scope & Caveat |
| :--- | :--- | :--- |
| Theorem 1 (Conformity eigenvalue contraction, $N_{\mathrm{eff}}$ vs. $r_{\mathrm{PR}}$) | **Mathematically proved** + **Executably tested** | Exact under linear equicorrelated Gaussian/white-noise model; numerical assertions verified in `tests/`. |
| Theorem 2 (Minimal consistent subset retention under bounded ATMS) | **Mathematically proved** + **Executably tested** | Holds under Assumption 1 (sound $\mathcal{M}_{\mathrm{sys}}, P_{\mathrm{sys}}$) and $|E_0| \le d_{\max}$ registered before $L_{\max}$. |
| Theorem 3 (Complete-justification set packing & bounded fail-closed truncation) | **Mathematically proved** + **Executably tested** + **Simulator (B0)** | Proves structural provenance disjointness, not statistical fault independence. |
| Theorem 4 ($f$-fault-tolerant belief shield $\widehat{\mathcal{B}}_\tau^{(f)}$) | **Mathematically proved** + **Executably tested** + **Simulator (B1, B3)** | Requires $S_\tau \in \mathcal{S}_{\mathrm{cand}}$ and $\le f$ faulty domains. |
| Theorem 5 (Versioned snapshots, Mode C verifier, Mode S frontier, closure $D^\star(a)$) | **Mathematically proved** + **Executably tested** + **Transactional boundary (SQLite WAL)** | Mode S predecessor frontiers, atomic epoch checks, drain caps, and restart reconciliation run on on-disk SQLite WAL. Multi-domain closure & Mode C verifier tested in Python unit tests. |
| Tier E0–E2 revocation & non-cancellable E1+ lease gap | **Mathematically proved** + **Executably tested** + **Transactional boundary (SQLite WAL for E2)** | E1+-quiescence stall/partition non-cancellable behavior is tested in an in-memory protocol state machine (`TargetResourceBoundary`), not across separate OS processes/networks. |
| Proposition 6 (Conditional 8-risk stratum accounting) | **Mathematically proved** + **Executably tested** | Conditional accounting identity; empirical priors ($\delta_{\mathrm{univ}}, \delta_{\mathrm{common}}, \bar{p}_{\mathrm{dom}}$) are environment inputs. |
| 100-scenario policy comparison (38.0% $\to$ 1.0%) | **Demonstrated in a simulator** | Synthetic fault-injection scenarios with SVD-whitened Gaussian latents (`_simulate_agent_errors`) and deterministic scenario outcomes; **NOT** live LLM API executions. |
| Real multi-node distributed cluster / live LLM deployment | **Assumed / Not independently verified** | Out-of-scope for this artifact; documented explicitly as unvalidated distributed integration. |

## 5. Known Limitations & Trusted Assumptions
1. **Synthetic Agent Simulation:** `pieq/benchmark.py` models LLM ensemble error correlations synthetically rather than executing live multi-LLM tool-calling loops.
2. **Complete Writer Mediation:** Unmediated external scripts or human operators directly mutating target state (`test_task4_overlapping_domain_sets_and_external_writer_bypass`) bypass `CrossRootExposureLedger` and induce $E_{\mathrm{drift}}$.
3. **Model & Manifest Soundness:** TCB rules $\mathcal{M}_{\mathrm{sys}}$, invariants $P_{\mathrm{sys}}$, world universe $\mathcal{S}_{\mathrm{cand}}$, and failure-domain topology $\mathcal{F}$ must be authored accurately outside the online runtime.
4. **Liveness vs. Safety Trade-off in E1+-quiescence:** When a target lacks target-side lease enforcement and partitions mid non-cancellable operation, PIEQ blocks conflicting admissions indefinitely to preserve safety.
5. **Bounded Mode C Class:** Operations exceeding `MAX_CONCURRENT_MODE_C_OPS = 4` or lacking deterministic finite-state models are rejected from Mode C and serialized via Mode S or denied.
