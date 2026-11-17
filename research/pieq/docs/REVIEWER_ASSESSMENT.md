# Final Independent Reviewer Assessment (PIEQ Revision 4)

**Manuscript:** *Provenance-Isolated Epistemic Quorums: Execution-Bound Evidence Certification and Plausible-World Safety Gating in Agentic Control Planes* (Fourth Revision, November 2026)  
**Target Venue Standard:** Rigorous systems / distributed-systems research conference (e.g., SOSP / OSDI / NSDI / EuroSys / CCS)

---

## Overall Verdict: **Weak Accept** (Strong Formal Core; Simulator-Only Agent Evaluation)

### Summary of Strengths
1. **Genuine Correctness & Formal Soundness:** All four P0/P1 formal gaps from Revision 3 are cleanly closed:
   - *Theorem 5 Snapshot Semantics (Task 1):* Versioned snapshot advancement (`SnapshotContext`) cleanly eliminates double-counting of already-observed commits in $\mathcal{I}_C$ while preserving persistent exposure accounting, handling mixed-version observation intervals $[v_{\min}, v_{\max}]$, keeping `UNKNOWN` disjoint from `COMMITTED`, and supporting durable SQLite WAL restart reconciliation.
   - *E1+ Lease-Expiration Gap (Task 2):* Explicitly separates control-plane lease expiry from target-enforced post-expiry exclusion; non-lease-fenced targets retain stalled non-cancellable operations in $\mathcal{I}_U$ and trade liveness for safety via indefinite blocking rather than unsafely declaring fence completion.
   - *Concrete Mode C Verifier & Mode S Frontier (Task 3):* Replaces the abstract/circular $\mathsf{Compat}$ predicate with a deterministic finite-state verifier (`ModeCCompatibilityVerifier`, `K_conc = 4`) that checks all subset schedules, intermediate states, partial effects, and 3-way interactions, and fixes Mode S to use predecessor frontiers (`Pred(a) <= Settled`) rather than impossible multi-predecessor `seq(a) = seq(u) + 1`.
   - *Multi-Resource Constraint Closure (Task 4):* Defines fixed-point closure $D^\star(a)$, atomic cross-domain reservation with pre-dispatch rollback, crash-safe $\mathcal{I}_U$ retention, and explicit external-writer drift boundaries.
2. **Honest Empirical & Artifact Hygiene:** Synthetic lognormal latency overlays were removed from `profile_proxy_admission_traces` in favor of pure measured wall-clock timings (`0.076 ms` median, `0.271 ms` p99 on an on-disk SQLite WAL boundary), and the manuscript explicitly labels the 100-scenario evaluation as a synthetic protocol/fault-injection simulator rather than live LLM runs.
3. **Tighter Scope & Presentation:** Moving audit tables and counterexample catalogs to supplementary material leaves a crisp 10-page main manuscript focused on complete-proof certification ($\nu_{\mathrm{pack}}$), fault-tolerant belief shielding ($\widehat{\mathcal{B}}_\tau^{(f)}$), and explicit KedgeFlow composition contracts.

---

## Remaining Issues by Severity

### Blocker
- **None** for the narrowed formal and single-node/simulator claims (all Stop Conditions are satisfied).

### Major
1. **No Live Multi-LLM Evaluation or Multi-Node Distributed Fault Injection:** Although honestly disclosed, the 100-scenario benchmark (`pieq/benchmark.py`) uses SVD-whitened Gaussian latents and deterministic scenario-encoded outcomes rather than real LLM agents producing Horn justifications over real telemetry, and Tier E1+-quiescence / multi-domain coordination are tested in a single-process Python state machine + on-disk SQLite WAL rather than a multi-process distributed cluster (`etcd` + networked adapters). Adding a small live-LLM-to-$\mathcal{V}_{\mathrm{rule}}$ trace case study and multi-process `etcd` integration test would elevate the paper from **Weak Accept** to **Accept**.

### Minor
1. **Mode C Scalability Beyond $K_{\mathrm{conc}} = 4$:** Exhaustive subset/permutation checking restricts Mode C to $|\mathcal{U}_C| \le 4$ unserialized finite-state operations, falling back to Mode S beyond that threshold. An explicit structural/monotone-resource fast path (with formal soundness proof) could widen concurrent throughput in future engineering.
2. **OpenKedge TypeScript Gateway Integration:** The PIEQ prototype (`research/pieq/`) is implemented in Python + SQLite WAL, whereas OpenKedge's production MCP gateway (`src/gateway/`, `src/control-plane/`) is TypeScript/Node.js. Porting `DeterministicRuleVerifier`, `BoundedATMSEngine`, and `compute_disjoint_justification_packing_bounded` into `src/control-plane/` is straightforward future engineering.

### Editorial
1. One negligible `1.73 pt` overfull `\hbox` at line 388 of `epistemic_quorums_atms_paper.tex` and an unimportant Times small-caps-italic font substitution warning (`OT1/ptm/m/scit`) in the LaTeX log; no layout overlaps or undefined references exist.
