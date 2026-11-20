# PIEQ Evidence Assessment (2026-11-17)

**Manuscript:** *Provenance-Isolated Epistemic Quorums: Execution-Bound Evidence Certification and Plausible-World Safety Gating in Agentic Control Planes* (Fourth Revision).

This is an evidence audit, not a forecast of a venue decision. The manuscript's formal claims depend on stated soundness, candidate-world, fault-budget, writer-mediation, and target-enforcement assumptions. The 100-case `38% → 1%` result comes from a deterministic synthetic protocol simulator; its agent errors and scenario outcomes are not live LLM observations. The 1,000-trace latency profile measures pure CPU plus a local SQLite WAL check, not provider or production end-to-end latency.

## Acceptance status by evidence source

| Source | Observed result | Scope and status |
| :--- | :--- | :--- |
| Earlier Revision 4 formal/SQLite suite | 36/36 tests passed in the prior environment | Executable checks for theorem edge cases and boundary counterexamples; historical baseline. |
| Expanded Python and OpenKedge checks | An earlier expanded Python run passed 109 with 10 etcd tests skipped; a final post-hardening count is pending. Node build passed; 16/16 Jest suites passed, with 104 tests passed and 1 skipped. | The focused local etcd suite was separately run with the binary. Node checks do not establish a PIEQ-to-gateway permit binding. |
| POSIX SQLite fault suite and archived JSONL | 10 schedules × 20 = 200/200 invariant checks passed | Independent workers and a target process on one host. In 20/20 E1+ races, the local lease elapsed while the control worker was stopped; its target completed after revocation while a conflict was blocked. Forty of 200 schedules reported temporary liveness blocking by UNKNOWN. The SQLite boundary now persists its shared cap and full operation identity, rejects mismatched replay, and refuses absent/already-drained nodes. |
| Local etcd 3.7.3 fault suite and archived JSON | 7 schedules × 20 = 140/140 invariant checks passed; 60 CAS conflicts | Three separate members and two clients on one host; endpoint/member mappings and injected member stops/restarts are archived. In the stale-resume schedule, 0/20 stale node-state effects occurred. Quorum loss and client isolation each returned 20/20 UNKNOWN receipts. |
| Container-backed etcd acceptance | Not run | Docker is unavailable on this host. The local three-member run does not meet the plan's container-backed acceptance gate. |
| Constructed 40-case proposer fixture and mock pilot | Fixed selection from 18 causal templates; deterministic three-case software pilot | Prompt and replay plumbing only. The fixture uses a private oracle and per-agent observations. Current fixture/trace hashes are not frozen for a real-model run. |
| Live multi-LLM paired comparison | 0/800 planned primary real-model requests completed | No five-instance model IDs, provider traces, incident-level unsafe/correct/defer counts, uncertainty intervals, proof rejection rates, covariance/mimicry estimates, token costs, or whole-call latency. This is a major OPEN evaluation gap. |
| OpenKedge gateway integration | Not run | No binding of PIEQ decision hash, epoch, action, target, and snapshot to a redeemable gateway permit is shown. |

## Interpretation and limits

The SQLite fault receipts support E1+ safety through blocking at the cost of liveness during the specified UNKNOWN races. The 0.5-second local lease elapsed during a real OS process pause in the E1+ and unresolved-crash schedules; this does not test remote clock skew. E2's stale-effect observations apply to the SQLite transaction and to the node-state fields inside etcd's multi-key CAS. They do not establish atomic fencing of an out-of-band drain RPC or another physical effect. The barrier schedules establish exact tested orderings; they do not measure coverage over arbitrary interleavings, network conditions, or remote hosts.

The constructed incidents can test whether real proposers produce useful verifiable proofs only after live calls are captured and scored. Their 40 IDs share 18 authored causal templates, so case-level uncertainty estimates would still need to account for template clustering. Even a complete future live run would measure behavior on authored telemetry, not real incident frequency or ground-truth reliability. No current mock result should be counted as evidence of live-model safety or mitigation quality.

The strongest remaining evaluation need is the frozen five-instance, 40-case paired provider run with full failure and deferral accounting. Container-backed etcd acceptance and any claimed OpenKedge gateway path have separate unfinished gates. Future assessment should be updated only with passing test outputs and archived receipt or trace hashes; it should not infer a reviewer verdict from those gates.
