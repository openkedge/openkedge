# PIEQ Revision 10 — Evidence Matrix

Every claim below links to the theorem, executed test, live trace, or independent physical
observation that supports it. Categories are never pooled. (Tiers: FORMAL = proof under
stated assumptions; SW = deterministic software execution; LIVE = genuine CLI-wrapper LLM
inference, unpinned identities; PHYS = real containerized PostgreSQL/Patroni execution with
independent SQL observation, Tier E0+native target.)

| # | Claim | Tier | Evidence |
|---|---|---|---|
| 1 | ν_pack ≥ k_a over Φ_phys certifies snapshot preconditions under ≤ f = k_a−1 faulty physical domains | FORMAL | Theorem 3 (paper §3.2); `tests/test_revision9_context_independence.py` |
| 2 | Context IDs / agent count never substitute for physical-domain independence | FORMAL+SW+LIVE | Eq. Φ_phys; 6 P1.1 tests; live: F3 fold-in (`rev10_logs/f2_p4_*.json`, duplicated-path ν=1); CN05 (live, deferred) |
| 3 | Mandatory temporal registry blocks commit-time-false mutations in the integrated LIVE pipeline | LIVE | H30 decomposition in `results/rev10_live_scored.json` (E1/S1/H1): proposal YES, packing 2=k_a, shield pass, `REJECTED_COMMIT_PRECONDITION_FALSE` inside `BEGIN IMMEDIATE`, commit=false, signed policy id; CN09 blocked 2/2 live runs |
| 4 | Legacy `temporal_policies=None` admits H30 (the closed defect) | LIVE | preserved E0 trace `results/rev9_cli_live_traces_full_pieq.jsonl` (H30 COMMITTED) |
| 5 | Snapshot-triggered/historical ops stay actionable under the registry | LIVE | E1/S1/H1 9/9 incl. H32; CN12 (mitigated in exploratory rep; r1 false-block was refutation under-submission, not policy) |
| 6 | Hardened full PIEQ live: 9/9 mitigations, 17/17 deferrals, 0/31 unsafe, 0/5 false-precondition | LIVE | `results/rev10_live_scored.json` E1, S1, H1 (3 independent conditions) |
| 7 | No measurable multi-agent benefit with evidence access + execution gate constant | LIVE | EXP-02 paired table: 0 within-assumption S1-vs-M1 disagreements; S1 at 39 calls/$1.00/6.3 s vs M1 185/$2.77+Bedrock/52.9 s; H1 rules out heterogeneity. Caveat: single run/condition; OOA flips H33/H35 both directions |
| 8 | Live single-shot refutation synthesis is nondeterministically incomplete (liveness, not safety) | LIVE | EXP-04: r1 CN10/CN12 false-blocked (1 refutation/world submitted), exploratory rep 4/4; preregistered liveness criterion FAILED, all safety criteria passed (`results/rev10_confirmation_live_*.jsonl`) |
| 9 | PIEQ+live LLM can execute a useful real infrastructure mutation | PHYS | F1-P4 ×3: Sonnet 5/5 accepted derivations on real collector evidence; ν_pack=3; REST dispatch; independent SQL observer: new primary, timeline bump, 0 dual-primary (`rev10_logs/f1_p4_*.json` + observer JSONL) |
| 10 | PIEQ refuses certified-but-unauthorized / temporally-unsound / unjustified physical mutations at verified pre-dispatch boundaries | PHYS | F5 (`REJECTED_AUTHORIZATION_REVOKED_PRE_DISPATCH`, ν=3 certified, zero effect); F7 (CTS fail-closed on E0; missing policy fail-closed); F2-P4 (partition: defer, leader kept serving 20/20 writes) |
| 11 | Patroni REST promotion is Tier E0 + native safeguards; no E2/E1+q claim | PHYS (contract) | `docs/REVISION10_TARGET_CAPABILITY_CONTRACT.md`; F9 measured: unmediated switchover executes (E6 contract); F5 post-dispatch window documented untested-by-design |
| 12 | Native Patroni alone prevented split-brain in every trial; PIEQ's physical increment is pre-dispatch gating + detection, not split-brain prevention | PHYS | all `rev10_logs/*observer.jsonl`: 0 dual-primary intervals in 100% of trials incl. F9; F1-P0 native 10.2 s auto-failover baseline |
| 13 | Zero provider failures after bounded retry; costs/tokens reconciled | LIVE | trace headers + `results/rev10_live_scored.json`; Bedrock unbilled (disclosed); pinned identities BLOCKED (`results/rev10_pinned_preflight.json`) |
| 14 | Single-VM host is a common-cause domain; no physical host-independence claim | PHYS (disclosure) | capability contract §host-level disclosure |

## Visual PDF inspection record (EXP-06)
- Rev9 Table 6 overlapped the adjacent column (defect confirmed on the archived PDF).
- Rev10 rebuild: live results split into a full-width outcomes table (`table*`) and a
  supplementary operations table; pages containing every table rendered to images and
  inspected after compilation (see execution log). Result recorded below the compile step.
