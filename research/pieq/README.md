# Provenance-Isolated Epistemic Quorums (PIEQ) — Executable Artifact

**Paper:** *Provenance-Isolated Epistemic Quorums: Execution-Bound Evidence Certification and Plausible-World Safety Gating in Agentic Control Planes* (Fourth Revision, November 2026)  
**Authors:** Jun He and Deying Yu (OpenKedge LLC)

This directory (`research/pieq/` in `openkedge/openkedge`) contains the shareable Python implementation, regression test suite, deterministic benchmark simulator, and audit documentation for PIEQ.

## Contents

- `pieq/core.py` — Bounded 64-bit ATMS (`BoundedATMSEngine`), deterministic Horn/nogood verifier (`DeterministicRuleVerifier`), bounded complete-justification set packing (`compute_disjoint_justification_packing_bounded`), $f$-fault-tolerant belief shield (`PlausibleWorldGate`), versioned snapshot advancement (`SnapshotContext`), multi-resource constraint closure (`ConstraintDependencyGraph`), cross-root exposure ledger (`CrossRootExposureLedger`), concrete Mode C compatibility verifier (`ModeCCompatibilityVerifier`), E0–E2 fencing model (`TargetResourceBoundary`), and on-disk SQLite WAL protected-resource boundary (`SQLiteProtectedResourceBoundary`).
- `pieq/benchmark.py` — 100-scenario synthetic protocol & fault-injection simulator (SVD-whitened Gaussian agent latents, 13 ablations, 2 negative controls) plus pure wall-clock 1,000-trace profiler on an on-disk SQLite WAL database.
- `tests/` — 36 automated tests:
  - `tests/test_theorems_and_counterexamples.py`
  - `tests/test_benchmark_and_ablations.py`
  - `tests/test_revision4_formal_repairs.py`
- `scripts/run_benchmark.py` — Regenerates `results/benchmark_summary.json` and prints policy & latency tables.
- `docs/` — Audit deliverables:
  - [`docs/TRACEABILITY_MATRIX.md`](./docs/TRACEABILITY_MATRIX.md)
  - [`docs/COUNTEREXAMPLE_REPORT.md`](./docs/COUNTEREXAMPLE_REPORT.md)
  - [`docs/REPRODUCIBILITY_REPORT.md`](./docs/REPRODUCIBILITY_REPORT.md)
  - [`docs/REVIEWER_ASSESSMENT.md`](./docs/REVIEWER_ASSESSMENT.md)

## Reproducing All Tests and Benchmark Numbers

```bash
cd research/pieq

# 1. Run all 36 theorem, counterexample, Revision 4 repair, and SQLite WAL tests
python3 -m pytest -v tests/

# 2. Run the 100-scenario synthetic simulator & 1,000-trace pure wall-clock WAL profiler
python3 scripts/run_benchmark.py
```
