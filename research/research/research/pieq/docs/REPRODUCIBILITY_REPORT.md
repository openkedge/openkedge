# PIEQ Reproducibility & Evidence Classification Report (2026-11-17)

## 1. Execution Environment & Source Revision

- **Current source base:** OpenKedge commit `e5c104b1d76ffb8e8504e4e241a2c0d6bdcdbae6`, plus the uncommitted evaluation additions in `research/pieq/`. The archived fault receipts correspond to those additions; the base commit alone cannot regenerate them.
- **Current fault-run host:** macOS 26.7.3 (Build `25G342`), arm64, Python 3.13.5, pytest 9.0.3, official etcd 3.7.3 local binary. The etcd client uses Python standard-library `urllib`, with no external etcd client dependency. `requirements-eval.txt` pins `numpy==2.5.1` and `pytest==9.0.3` for evaluation, but is not hash-locked.
- **Earlier Revision 4 synthetic run:** macOS 26.5.2 (Build `25F84`), arm64, Python 3.13.9, SQLite 3.50.4, `numpy==2.5.1`, `pytest==9.0.3`. The 100-scenario and CPU+WAL numbers below belong to this earlier run.
- **Container pin:** `gcr.io/etcd-development/etcd:v3.7.3` at manifest digest `sha256:0d27b9478551f431f82d1e0ddb6c87ad07ebae8b904c637f3a99213349523910`. Docker is unavailable on the current host, so the container-backed acceptance run is open.

## 2. Commands & Measured Outcomes

```bash
cd research/pieq
python3 -m pip install -r requirements-eval.txt

# Earlier Revision 4 run: mathematical, counterexample, repair, and WAL suite
python3 -m pytest -v tests/
# Earlier observed outcome: 36 passed in 0.43s; this is not the expanded suite.

# Regenerate the synthetic 100-scenario comparison and 1,000-trace latency profile
python3 scripts/run_benchmark.py

# Once a five-instance manifest and local credentials are supplied, validate
# without API calls or trace writes; this was not run with real credentials here.
python3 scripts/run_live_llm_eval.py --manifest run-manifest.json --preflight

# Expanded Python suite on this host; etcd tests skip without PIEQ_ETCD_BIN
/private/tmp/pieq-eval-venv/bin/python -m pytest -q tests/
PIEQ_ETCD_BIN=/private/tmp/etcd-v3.7.3-darwin-arm64/etcd \
  PIEQ_ETCD_REPETITIONS=1 \
  /private/tmp/pieq-eval-venv/bin/python -m pytest -q tests/

# Reproduce the POSIX multi-process SQLite run with the 3.13.5 environment
/private/tmp/pieq-eval-venv/bin/python -m pytest -q tests/test_multiprocess_faults.py
/private/tmp/pieq-eval-venv/bin/python scripts/run_multiprocess_fault_eval.py \
  --repetitions 20 --output /private/tmp/pieq-multiprocess-fault-replay.jsonl

# Reproduce the local three-member etcd run using the official 3.7.3 binary
PIEQ_ETCD_BIN=/private/tmp/etcd-v3.7.3-darwin-arm64/etcd \
  /private/tmp/pieq-eval-venv/bin/python -m pytest -q tests/test_etcd_faults.py
PIEQ_ETCD_BIN=/private/tmp/etcd-v3.7.3-darwin-arm64/etcd \
  /private/tmp/pieq-eval-venv/bin/python scripts/run_etcd_fault_eval.py \
  --repetitions 20 --output /private/tmp/pieq-etcd-local-replay.json

# Inspect the archived receipt identity; new runs have nondeterministic timestamps,
# allocated ports, and namespaces and therefore will have different hashes.
shasum -a 256 results/multiprocess_fault_evidence.jsonl results/etcd_local_fault_evidence.json

# From the OpenKedge repository root; the HTTP test needs loopback binding
cd ../..
npm run build
npm test -- --silent
```

The expanded Python suite passed `150 passed, 13 skipped` in `32.65 s` without `PIEQ_ETCD_BIN` after credential-redirect, resume-retry, and declared-reserve checks. An earlier protocol-v2 run passed `159` tests in `44.82 s` with the local three-member etcd binary enabled and `PIEQ_ETCD_REPETITIONS=1`; that run preceded the four new provider/retry/cap tests and emitted 883 macOS warnings about `fork()` from a multithreaded pytest process. No test failed. The independent fault artifacts recorded `200/200` SQLite and `140/140` local etcd passing invariant checks. The focused local-etcd module also passed `15/15` tests in `109.43 s` using its full repeated schedules, without Docker; this is not a container-backed acceptance run. OpenKedge `npm run build` passed; `npm test -- --silent` passed 16/16 suites, with 104 tests passed and 1 skipped when loopback binding was allowed. `containers/ETCD_SETUP.md` contains the separate pinned Compose commands. The container run has not been recorded.

The live harness now uses protocol v3, which binds the case ID into request and decision IDs so repeated visible fixtures remain distinct, tightens the proposal-contract instruction text (own-evidence citations; `world_refutations` as a flat list of world-ID strings; integer proof masks), and syntactically unwraps at most one leading `<think>` block and one surrounding Markdown fence before strict JSON validation, identically for every provider, with verbatim replies archived. v3 added three transports: `claude_cli` (`claude -p`, MCP disabled, tools disallowed, one turn), `codex_cli` (`codex exec`, read-only sandbox, ephemeral, MCP disabled), and `local_openai` (local OpenAI-compatible server; SGLang). The regenerated v3 three-case mock trace has SHA-256 `0b5f6ac03e17a14239592956c1e6faa4c094a4a8c866a2af5eb99e0e5abcd94f`; its replayed summary has SHA-256 `f5126c351892056e360f97dd968a7d7e42a15c4172ab083c6e8e2e195463d00d`. These are software-pilot artifacts, not real-model results.

The complete primary live run is `primary-claude-codex-qwen-r4-2026-11-18` (executed 2026-11-19, max_attempts 3): 800/800 logical requests, zero transport errors, zero missing replies; only non-successes were 8 schema-malformed Qwen3 replies. Manifest: `results/live_primary_manifest_r4.json` (SHA-256 `10ffac48ea0838af5beb866cbc86b7fe34bf0d7552a42ca9e3261124951d987a`). Trace: `results/live_llm_traces_r4.jsonl` (SHA-256 `7e9a50ec6cb1a9e29f40a7dc880c0c3ee3994f76860216118b13d8a5921b0bfe`). Scored summary: `results/live_llm_summary_r4.json` (SHA-256 `057fb57bcdec94488af382263ff748920758df3902ac40e8c3019e1048976b5b`). Its strict `primary_live_claim_allowed` gate is `false` solely because the Codex CLI returns no server-attested model ID. Two intermediate attempts (r2, r3) were aborted after an unplanned host reboot caused clustered CLI transport failures; their traces are archived unscored and no rate is reported from them.

An earlier completed run (`primary-claude-codex-qwen-2026-11-17`, executed 2026-11-18) finished all 800/800 logical requests (914 attempts): 679 ok, 114 `api_error` during one 62-minute local connectivity outage (08:33–09:35 UTC) that affected the four Bedrock-mediated CLI agents on case IDs {3,4,6,7,9,10,11,14}, and 7 malformed replies (all Qwen3-32B). Manifest: `results/live_primary_manifest.json` (SHA-256 `655682f13a9ce37c80085d14ea3e6796d3e82c06a334afe6670ce3a8f208d53d`). Trace: `results/live_llm_traces.jsonl` (SHA-256 `d554a68899df1492be24c700fd0cdcef211176404c2434708d083ebd2e436198`). Scored summary: `results/live_llm_summary.json` (SHA-256 `2614ef7c549675b57d33ac5b2ca04022daa36bf6a60eea092a0e3f26a8ba97a9`). Instances: `claude-sonnet-5-5` and `claude-haiku-5-5` (Claude Code CLI 2.1.326.1741, Bedrock, returned IDs matched exactly), `openai.gpt-5.6-sol` ×2 (Codex CLI 0.169.0.962, Bedrock; no server-attested model ID, identity unpinned), and local SGLang `Qwen/Qwen3-32B` (MLX 4-bit, thinking disabled, seed 17). The scorer's strict `primary_live_claim_allowed` gate is `false` (missing-call window + unpinned Codex identity); results are measured outcomes of one completed run, not a confirmatory claim. Four machine-sleep stalls (~997/976/933/927 s) inflate whole-run wall-clock only; per-attempt elapsed times are unaffected.

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
| Theorem 5 (Versioned snapshots, Mode C verifier, Mode S frontier, closure $D^\star(a)$) | **Mathematically proved** + **Executably tested** + **SQLite process and local etcd transaction trials** | SQLite runs Mode S frontiers, epoch checks, a durable shared cap, full-identity `op_id` receipts, and restart reconciliation in separate processes. Local etcd runs atomic multi-key CAS over represented target state. Closure and Mode C remain Python unit-test evidence. |
| Tier E0–E2 revocation & non-cancellable E1+ lease gap | **Mathematically proved** + **Executably tested** + **SQLite process and local etcd transaction trials** | A separate SQLite target process completed an E1+ operation after control revocation while conflicts were blocked. E2 rejects stale effects in WAL or etcd transactions; this does not establish fencing for arbitrary external RPCs. |
| Proposition 6 (Conditional 8-risk stratum accounting) | **Mathematically proved** + **Executably tested** | Conditional accounting identity; empirical priors ($\delta_{\mathrm{univ}}, \delta_{\mathrm{common}}, \bar{p}_{\mathrm{dom}}$) are environment inputs. |
| 100-scenario policy comparison (38.0% $\to$ 1.0%) | **Demonstrated in a simulator** | Synthetic fault-injection scenarios with SVD-whitened Gaussian latents (`_simulate_agent_errors`) and deterministic scenario outcomes; **NOT** live LLM API executions. |
| Real local three-member etcd quorum | **Observed in local multi-process trial** | Three independent etcd 3.7.3 members and two clients on one host, seven schedules × 20. Container-backed acceptance and remote hosts remain unverified. |
| Constructed 40-case proposer protocol | **Fixture, mock pilot, and completed live run** | Forty selected IDs use 18 causal templates, private answer key and per-agent telemetry subsets. The three-case mock pilot verifies software plumbing; the completed 800-request primary run supplies the live-model outcomes below. |
| Live multi-LLM comparison | **Measured in a complete primary run, replicated by an outage-affected run (strict claim gate false)** | Complete r4 run, 40 paired incidents, zero missing calls: unsafe actions 0/40 independent vote, 0/40 full PIEQ (all-deferral; no mutation admitted), 3/40 three-round debate, 5/40 raw-channel count. Debate vs PIEQ McNemar 3:0 discordant, p=0.25; raw vs PIEQ 5:0, p=0.0625; template-cluster bootstrap intervals include 0. 140/284 submitted proofs failed exact verification; 0 eligible minority-flip incidents (earlier run: 2/2 flipped). Earlier run consistent (0/40, 0/40, 4/40, 5/40) with 114/800 requests lost to a 62-minute outage. Codex identity unpinned, so `primary_live_claim_allowed` stays false (r4: that is the only reason). |
| OpenKedge gateway binding | **Open** | No bound gateway permit carried a PIEQ decision. |

## 5. Known Limitations & Trusted Assumptions
1. **Live LLM evidence scope:** `pieq/benchmark.py` models LLM ensemble error correlations synthetically; its simulator rates must not be transferred to live LLM claims. The frozen fixture/scoring manifest is `results/live_cases_manifest.json` (file SHA-256 `2dfee8f4c41566680406b2e51c1423077b781e478e0319edaa378262e03538c3`, unchanged). Two primary live runs are complete (hashes in Section 2). The complete r4 run has zero missing calls and 8 malformed Qwen3 replies; the earlier run carries explicit missing-call outcomes (114/800 `api_error` in one outage window) and 7 malformed replies. Both carry an unpinned Codex model identity, CLI-default decoding for the four CLI agents (temperature/seed not controllable), and repeated visible fixtures/templates that limit incident independence — so the strict `primary_live_claim_allowed` gate is false for both (for r4, solely the identity pin) and no confirmatory unsafe-rate claim is made. Scalar error-correlation summaries are undefined (two instances had zero error variance); per-round 5×5 error covariance matrices and 2/2 minority flips are archived. r4 token totals were 20.04M input / 0.64M output with zero unknown-usage attempts; only the Claude instances are dollar-priced (self-reported $5.13 in r4, $4.38 in the earlier run). The scorer records `request_attempt_elapsed_seconds` (r4 median 15.7 s, mean 20.0 s) and `serial_request_plus_tcb_path_seconds` (r4 median 82.7 s single-round conditions, 375.2 s debate); the latter is a serial sum/lower-bound proxy, not observed end-to-end whole-call wall time, and both omit some orchestration overhead.
2. **Complete Writer Mediation:** Unmediated external scripts or human operators directly mutating target state (`test_task4_overlapping_domain_sets_and_external_writer_bypass`) bypass `CrossRootExposureLedger` and induce $E_{\mathrm{drift}}$.
3. **Model & Manifest Soundness:** TCB rules $\mathcal{M}_{\mathrm{sys}}$, invariants $P_{\mathrm{sys}}$, world universe $\mathcal{S}_{\mathrm{cand}}$, and failure-domain topology $\mathcal{F}$ must be authored accurately outside the online runtime.
4. **Liveness vs. Safety Trade-off in E1+-quiescence:** Without target-side lease enforcement, PIEQ retains UNKNOWN and blocks conflicting admission until acknowledgment or reconciliation. In 40/200 SQLite schedules, a local 0.5-second lease deadline elapsed while the control worker was actually stopped; those schedules recorded blocked liveness. This tests the specified pause ordering, not remote clock skew or a production lease. The flag does not mean permanent nontermination; UNKNOWN durations were not summarized.
5. **Bounded Mode C Class:** Operations exceeding `MAX_CONCURRENT_MODE_C_OPS = 4` or lacking deterministic finite-state models are rejected from Mode C and serialized via Mode S or denied.
6. **Fault-boundary scope:** Barriers specify ten SQLite and seven etcd interleavings, not arbitrary schedules. All etcd members ran on one host. Its E2 effect is the node-state change inside the atomic transaction, not an out-of-band drain RPC. No OpenKedge controller/gateway permit is bound to PIEQ in these runs.

## 6. Archived Fault Receipts and Counts

| Artifact | Archived SHA-256 | Setup and exact observations |
| :--- | :--- | :--- |
| `results/multiprocess_fault_evidence.jsonl` | `27084967675f583d3410ad709261228c691a0be0d14870ec76f3c75b76646688` | Ten POSIX/SQLite schedules × 20 = 200; 200/200 checks pass; 40/200 UNKNOWN liveness flags with local lease expiry observed during SIGSTOP. E1+ target completion after revocation occurred in 20/20 while conflict was blocked. Three E2 pre-effect pause schedules rejected 60/60 stale attempts. The shared cap and exact `op_id` identity are now persisted. |
| `results/live_llm_traces_r4.jsonl` | `7e9a50ec6cb1a9e29f40a7dc880c0c3ee3994f76860216118b13d8a5921b0bfe` | Complete primary run (r4, max_attempts 3): 800/800 logical requests, 800 attempts, zero transport errors, zero missing replies, 8 malformed Qwen3 replies. Verbatim prompts/replies and all 160 decisions; replayable offline. |
| `results/live_llm_summary_r4.json` | `057fb57bcdec94488af382263ff748920758df3902ac40e8c3019e1048976b5b` | Scorer output over the r4 trace (primary result set). `primary_live_claim_allowed` = false solely due to the unpinned Codex identity. |
| `results/live_llm_traces.jsonl` | `d554a68899df1492be24c700fd0cdcef211176404c2434708d083ebd2e436198` | Completed primary live run, 40 cases × 5 agents × 4 rounds = 800 logical requests (914 attempts; 679 ok, 114 `api_error` in one 62-minute outage window, 7 malformed). Verbatim prompts and replies; decisions for all four conditions; replayable and auditable offline. |
| `results/live_llm_summary.json` | `2614ef7c549675b57d33ac5b2ca04022daa36bf6a60eea092a0e3f26a8ba97a9` | Offline scorer output over the primary trace: per-condition outcome rates, exact paired McNemar tests with template-cluster bootstrap, proof-rejection taxonomy, per-round error covariance matrices, minority flips, token totals, and latency accounting. `primary_live_claim_allowed` = false. |
| `results/etcd_local_fault_evidence.json` | `d5618c70b9610bb22125235013d6d289a44efd292eaf6b94cb6fcfc177ac7f7a` | Seven local three-member etcd schedules × 20 = 140; 140/140 checks pass; 60 CAS conflicts; 0/20 stale-resume node-state effects; 20/20 quorum-loss and 20/20 isolated-client UNKNOWN receipts. Each trial records three endpoint-to-member IDs; elected-leader and quorum-loss schedules record 20 and 40 member stops respectively, plus restart/health evidence. |

For etcd, whole-schedule medians/p95s in seconds were: stale resume 0.117/0.139, simultaneous drains 0.185/0.189, duplicate replay 0.195/0.213, predecessor frontier 0.045/0.057, leader failure 1.408/1.949, quorum loss 2.467/2.849, client isolation 0.521/0.524. These durations include fault orchestration and do not replace the pure CPU+WAL latency table.

The official macOS ARM etcd 3.7.3 archive used locally had SHA-256 `efd1fc11ea761c9c39c9011fda9c92a9eaecf91be3b7b3198afe4dc786435e28`. The Compose digest above is a pin, not evidence of a completed container run. The published benchmark JSON belongs to the historical synthetic experiment; its `38% → 1%` comparison is not a live-model outcome.
