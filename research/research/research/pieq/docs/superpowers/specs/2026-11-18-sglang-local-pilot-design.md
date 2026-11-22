# Local SGLang Qwen3 proposer pilot

Date: 2026-11-18

## Purpose and evidence boundary

Connect the existing PIEQ live-proposer protocol to the user's local SGLang
server and collect an auditable Qwen3 pilot. This is a single-model software
and proposer-behavior control, not the five-model primary comparison, a
distributed target experiment, or evidence of real incident prevalence.
Repeated calls to one model are not independent model-family replications.

The server endpoint is `http://127.0.0.1:30000/v1`. A read of `/v1/models`
returned the exact requested model ID `Qwen/Qwen3-32B` on 2026-11-18. The
workspace sandbox cannot connect to this listener; the actual local run needs
the approved outside-sandbox loopback path. The artifact report records the
SGLang version and the user-supplied `mlx_q4` launch configuration alongside
the returned model ID.

## Integration

Add a distinct `sglang` provider to the existing REST adapter and manifest
validation. It calls `/v1/chat/completions`, sends the frozen user prompt as one
message with `temperature` and `max_tokens`, and records returned content,
usage, and returned model ID. Seed is omitted unless explicitly supported and
tested. No API credential is required. Allow plain HTTP only for loopback
addresses and reject URL userinfo, query strings, fragments, and redirects.
The credential-free endpoint base remains frozen in the trace header. Keep the
paid `openai` provider's HTTPS and credential requirements unchanged.

Keep the existing protocol-v2 prompts, verifier, ATMS, packing, world gate,
oracle, and four conditions unchanged. Add an optional `case_ids` field to
the run manifest for bounded single-model pilots; existing protocol-v2 mock
manifests without that field remain valid. The runner selects exactly those
IDs from the frozen 40-case set, and replay and scoring verify the same list
from the manifest embedded in the trace header. A `primary` manifest cannot
select a subset, so a pilot cannot be relabeled as a complete five-model
evaluation.

## Pilot sequence

1. Offline transport, endpoint-validation, selection, replay, and budget tests
   use fake HTTP responses; they send no model calls.
2. Preflight confirms the endpoint, exact model ID, and the frozen manifest
   without writing a trace.
3. Run case 0 as a 20-logical-request smoke test: five named instances of
   `Qwen/Qwen3-32B` at rounds 0 through 3, one attempt per request, a
   20-call ceiling, temperature 0, a fixed output-token ceiling, and no paid
   API budget. Save all request, response, failure, decision, and timing
   records in a separate JSONL trace. Record the actual output ceiling and
   timeout in the manifest before dispatch.
4. Only after the case-0 trace is complete and replayable, run a separate
   60-logical-request exploratory control on cases 0, 2, and 3: positive
   synthesis, shared-domain trap, and Helly ambiguity. It has a new run ID
   and trace, never resumes into the 20-call trace.
5. Score traces offline with the existing oracle and label all rates as
   descriptive. Publish trace and manifest hashes, exact completed/missing
   call counts, malformed replies, verifier rejections, unsafe recommendations,
   safe deferrals, and latency. If a run fails, preserve its partial trace and
   report the failure without claiming completion.

## Acceptance and limits

The smoke test succeeds only if 20 distinct logical requests and four
condition decisions are recorded, the returned model ID matches the frozen
model, and replay regenerates the same decisions and score without HTTP.
Transport or parsing failures remain explicit trace statuses. The larger
control is optional and cannot make `primary_live_claim_allowed` true.
Do not alter the paper's synthetic 38%-to-1% result or insert pilot rates as
primary empirical findings.
