# Provenance-Isolated Epistemic Quorums (PIEQ) — Executable Artifact

**Paper:** *Provenance-Isolated Epistemic Quorums: Execution-Bound Evidence Certification and Plausible-World Safety Gating in Agentic Control Planes* (arXiv Preprint, November 2026)  
**Authors:** Jun He and Deying Yu (OpenKedge LLC)

This directory (`research/pieq/` in `openkedge/openkedge`, verified commit `525ff84eb0b786fa3479673a71915db3d5de84c1`) contains the shareable Python implementation, regression test suite, deterministic benchmark simulator, live/physical evaluation harnesses, and audit documentation for PIEQ.

## Verified GitHub Actions CI

Workflow `.github/workflows/pieq-evaluation.yml` ([run 45072074097](https://github.com/openkedge/openkedge/actions/runs/45072074097), Python 3.13.18, `pytest 9.0.3`, NumPy 2.5.1, containerized 3-member `etcd`):
- `python -m pytest tests/ -q`: **242 passed, 0 failed, 0 skipped in 170.35 s** (includes the 13 container-backed `etcd` integration tests; local runs without an `etcd` cluster pass 229 with those 13 skipped).
- `python scripts/run_etcd_fault_eval.py --repetitions 20 --output results/ci_etcd_evidence.json`: **140 distributed `etcd` fault trials passed** (artifact `pieq-etcd-evidence`; not double-counted with pytest's `etcd` integration cases).

## Contents

- `pieq/core.py`, `pieq/hardened.py`, `pieq/typed_pipeline.py`, `pieq/probe_loop.py`, `pieq/completion.py`, `pieq/etcd_boundary.py` — Bounded 64-bit ATMS (`BoundedATMSEngine`), deterministic Horn/nogood verifier (`DeterministicRuleVerifier`), bounded complete-justification set packing (`compute_disjoint_justification_packing_bounded`), $f$-fault-tolerant belief shield (`PlausibleWorldGate`), versioned snapshot advancement (`SnapshotContext`), multi-resource constraint closure (`ConstraintDependencyGraph`), cross-root exposure ledger (`CrossRootExposureLedger`), concrete Mode C compatibility verifier (`ModeCCompatibilityVerifier`), E0–E2 fencing model (`TargetResourceBoundary`), on-disk SQLite WAL protected-resource boundary (`SQLiteProtectedResourceBoundary`), mandatory temporal-precondition policy registry (`MutationTemporalPolicy`), typed proof-submission pipeline, seven-boundary probe loop, and default-off bounded deterministic completion.
- `pieq/benchmark.py` — 100-scenario synthetic protocol & fault-injection simulator (SVD-whitened Gaussian agent latents, 13 ablations, 2 negative controls) plus pure wall-clock 1,000-trace profiler on an on-disk SQLite WAL database.
- `tests/` — 242 automated test cases across 21 test modules covering formal theorems, counterexamples, temporal-policy enforcement, seven-boundary probing, deterministic completion, SQLite WAL boundaries, live protocols, and container-backed `etcd` faults.
- `scripts/run_benchmark.py` — Regenerates `results/benchmark_summary.json` and prints policy & latency tables.
- `docs/` — Audit deliverables:
  - [`docs/REVISION11_CLOSING_REPORT.md`](./docs/REVISION11_CLOSING_REPORT.md) — final closing report (EXP-05..09, negative results, remaining blockers)
  - [`docs/REVISION11_REPRODUCIBILITY.md`](./docs/REVISION11_REPRODUCIBILITY.md) — exact commands, CI verification, and frozen manifests
  - [`docs/REVISION11_FORMAL_IMPLEMENTATION_AUDIT.md`](./docs/REVISION11_FORMAL_IMPLEMENTATION_AUDIT.md) — theorem-to-implementation consistency audit
  - [`docs/REVISION11_REFUTATION_COMPLETION.md`](./docs/REVISION11_REFUTATION_COMPLETION.md) & [`docs/REVISION11_AGENT_COUNT_STUDY.md`](./docs/REVISION11_AGENT_COUNT_STUDY.md)
  - [`docs/REVISION10_TARGET_CAPABILITY_CONTRACT.md`](./docs/REVISION10_TARGET_CAPABILITY_CONTRACT.md), [`docs/REVISION11_PATRONI_CAUSALITY.md`](./docs/REVISION11_PATRONI_CAUSALITY.md), & [`docs/REVISION11_POSTGRES_E2_CONTRACT.md`](./docs/REVISION11_POSTGRES_E2_CONTRACT.md)
  - [`docs/REVISION9_CLOSING_REPORT.md`](./docs/REVISION9_CLOSING_REPORT.md), [`docs/REVISION8_AUDIT_AND_CONSOLIDATION_REPORT.md`](./docs/REVISION8_AUDIT_AND_CONSOLIDATION_REPORT.md), [`docs/TRACEABILITY_MATRIX.md`](./docs/TRACEABILITY_MATRIX.md), [`docs/COUNTEREXAMPLE_REPORT.md`](./docs/COUNTEREXAMPLE_REPORT.md), [`docs/REPRODUCIBILITY_REPORT.md`](./docs/REPRODUCIBILITY_REPORT.md)

## Reproducing All Tests and Benchmark Numbers

```bash
cd research/pieq
python3 -m pip install -r requirements-eval.txt

# 1. Run the regression suite (242 passed with containerized etcd; 229 passed, 13 skipped locally without etcd)
python3 -m pytest -v tests/

# 1b. Revision 9 frozen confirmation set (12/12; see docs/REVISION9_CLOSING_REPORT.md)
python3 scripts/run_rev9_confirmation_eval.py

# 1c. Revision 9 live CLI-wrapper LLM evaluation (executed Nov 2026; unpinned tier)
#     Requires locally authenticated `claude` and `codex` CLIs.
python3 scripts/run_rev9_cli_live_eval.py --protocols full_pieq
python3 scripts/score_rev9_cli_live.py

# 2. Run the 100-scenario synthetic simulator & 1,000-trace pure wall-clock WAL profiler
python3 scripts/run_benchmark.py
```

## Live model protocol and offline pilot

The five-instance harness uses one common public round-0 instruction and
output contract per case, with each agent receiving only its authorized
telemetry subset, followed by three debate rounds. Every debate prompt
contains only permitted evidence and the previous round's messages. Independent
vote and final debate use a fixed three-of-five majority; ties, missing replies,
and no majority defer. Raw-channel counting and full PIEQ consume the identical
round-0 replies without further model calls. Missing proofs stay missing.
Full PIEQ verifies Horn proposals, registers applicable primitive nogoods, packs
complete provenance footprints, gates fault-tolerant worlds, and checks concurrent
safety. These incident fixtures specify no in-flight operations; that context is
recorded explicitly and is separate from the multiprocess fault experiment.

```bash
python3 -m pytest -q tests/test_live_protocol.py
python3 scripts/run_live_llm_eval.py --mock-pilot
python3 scripts/run_live_llm_eval.py --mock-pilot --replay
python3 scripts/score_live_llm_eval.py --trace results/live_mock_traces.jsonl \
  --output results/live_mock_summary.json
```

`results/live_mock_traces.jsonl` from this command is a deterministic software pilot
on three authored cases, not a live model comparison. Requests carry wall-clock
timestamps and each retry attempt has start/result timestamps and elapsed time.
Decision records include measured TCB-only aggregation time; that measurement is
kept separate from the deterministic decision payload, which replays byte-for-byte.
The scorer reports request-attempt time separately from a per-condition sum of
relevant LLM request and TCB times. The latter is not observed end-to-end wall
time: prompt construction, trace writes, and scheduling overhead are excluded.
The CLI prints the complete trace's SHA-256 to stderr. The trace header also
binds the full frozen scoring manifest, including private oracle and answer-key
hashes, before the first call. Preserve the trace, manifest, and hashes when
publishing a run. Protocol v3 includes the case ID in request and decision IDs,
so cases with identical visible evidence still receive distinct calls and records,
tightens the proposal-contract instructions (own-evidence citations, flat
world-refutation ID lists, integer proof masks), and unwraps at most one leading
`<think>` block and one surrounding Markdown fence before strict JSON parsing,
identically for every provider; verbatim replies stay in the trace. Traces from
earlier protocol versions cannot be resumed or replayed as v3 traces.

For a real run, create a JSON manifest using `pieq.live_protocol.mock_manifest()`
as the field template, replace its five `instances`, and set `run_kind` to
`primary`. Use named `agent_0` through `agent_4`, pinned model/version identifiers,
explicit `family` identifiers and `supports_seed` flags. A primary manifest must
include at least two distinct real provider/model identities and declared families.
Family labels are supplied by the experimenter, not independently verified by
the harness. Label a five-instance run of
one model `single_model_control`. Unavailable providers need not be represented.
Mock-only manifests must use the explicit `--mock-pilot` path; they cannot be
captured or replayed as live runs.
Freeze temperature, seed, output limit, timeout, retry count, agent assignment,
packing budget, call ceiling and spending ceiling in the manifest before running.
For each paid instance, pin the observed price basis in optional paired
`input_usd_per_million` and `output_usd_per_million` fields before the run.
Both must be finite nonnegative numbers; omit both when no reliable price basis
is available. Offline billing estimates use recorded token usage and these
frozen rates. The live spending guard still reserves the conservative
`max_cost_per_call_usd` before each attempt, independently of the estimate.
The REST adapters use Python's standard library, so no optional provider SDK
dependencies or SDK version drift are introduced.

Set only the credentials needed by your manifest: `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, or `GEMINI_API_KEY`. Local Ollama needs no credential, and
`local_openai` (any local OpenAI-compatible server such as SGLang; base URL from
`LOCAL_OPENAI_BASE_URL`, default `http://127.0.0.1:30000`) needs none either.
The `claude_cli` and `codex_cli` providers shell out to locally authenticated
`claude -p` and `codex exec` binaries (argv subprocess, empty scratch directory,
MCP disabled, tools disallowed or read-only sandboxed); their credentials never
enter this process or the trace, and the Codex CLI returns no server-attested
model ID, so its identity is recorded as unpinned by the scorer.

The complete primary run `primary-claude-codex-qwen-r4-2026-11-18` is archived as
`results/live_primary_manifest_r4.json`, `results/live_llm_traces_r4.jsonl`, and
`results/live_llm_summary_r4.json`; the earlier outage-affected run
`primary-claude-codex-qwen-2026-11-17` is archived alongside it, and two aborted
attempts (r2, r3; host reboot) are kept unscored. Hashes and outcome summaries
are in `docs/REPRODUCIBILITY_REPORT.md` and the paper supplement.
Optional endpoint overrides are `OPENAI_BASE_URL` (including `/v1`),
`ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL` (including `/v1beta`), and
`OLLAMA_BASE_URL`. Credentialed endpoints must use HTTPS and cannot carry
userinfo, query parameters, or fragments; credentialed requests reject redirects
to avoid forwarding API keys to a different destination. The credential-free resolved endpoint
bases are saved in the trace header and may not change on resume. Do not put
credentials in the manifest or endpoint URL.

```bash
python3 scripts/run_live_llm_eval.py --manifest run-manifest.json \
  --preflight
python3 scripts/run_live_llm_eval.py --manifest run-manifest.json \
  --trace results/live_primary_traces.jsonl
python3 scripts/run_live_llm_eval.py --replay \
  --trace results/live_primary_traces.jsonl
```

Preflight checks the manifest, required environment credentials, endpoint
scheme, model IDs, and spending ceilings without making calls or writing a
trace. It does not establish that a model ID exists or that a provider will
accept every model-specific generation parameter. The runner makes the same
local checks before it creates a paid-run trace.

The live command covers all 40 frozen cases: 800 logical requests before
retries. Set positive `spending_cap_usd` and `max_cost_per_call_usd` for paid APIs.
The latter must conservatively bound the entire request using current provider
pricing, full prompt length and output limit. The harness reserves that amount
before each attempt and counts retries against both ceilings. It does not infer
prices or claim an actual dollar bill from token counts. When paired price fields
are present, preflight rejects a reserve below the declared maximum output-token
cost; the operator still must allow for input tokens and any provider-specific
charges. The declared spending ceiling is a reservation guard, not a provider
billing guarantee.

Each API attempt is durably marked before dispatch and has an attempt result;
each logical request and each aggregation decision has a JSONL record.
Malformed JSON, invalid masks, API errors, rate limits, timeouts, and exhausted
budgets are explicit failures. A 429 currently retries immediately without
`Retry-After` pacing, so run operators should use provider-appropriate rate
limits and inspect partial traces before continuing a capped run. Resume with
the identical manifest and trace reuses completed requests. A durably written
successful `attempt_result` is recovered if the process stopped before writing
its final request record. An
explicitly failed attempt can use any remaining retries after a restart. An
interrupted dispatched attempt with no result is recorded as
`interrupted_unknown_outcome` and is never automatically billed again. Resume
and replay verify the manifest hash, derived request IDs, exact per-agent prompts,
parsed replies, and stored decisions. Corrupt or inconsistent JSONL stops the
run. Proof records with bad masks remain countable as rejected proof proposals;
the proposal itself is malformed when its top-level contract fails. Full PIEQ
decisions record one exclusive reason per proposed proof, registered nogoods,
and each action's packing and shield result. Run one collector process per trace file.
Proof rejection rates use only parsed proposals actually submitted to the full
PIEQ verifier. Raw proof snippets in malformed replies are reported separately.
The reported minority-flip rate is limited to round-0 correct minorities with a
locally visible positive Horn proof; it excludes correct deferrals and does not
establish uniquely private disambiguating evidence. Returned model IDs are
reported beside requested IDs. Missing or mismatched returned IDs prevent the
score from allowing a primary live claim until model identity is resolved.
`results/live_llm_traces.schema.json` specifies individual records.
Only a trace with all five real instances completing all 40 primary cases can
support a full live comparison; the committed mock pilot does not meet that bar.
