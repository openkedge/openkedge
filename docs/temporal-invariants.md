# Temporal Invariants & Capability Attenuation

OpenKedge's execution runtime supports signed read capabilities, preceding-event
requirements, sliding quotas, and rate limits without an additional policy DSL.
Configure these controls on `OpenKedgeEngine`; agents cannot disable them by
omitting fields from a request.

## Runtime API

The execution runtime is in `src/core` and is exported from the root `openkedge`
entry point. `packages/core` re-exports its temporal types and reducers.
`packages/sdk-js` exposes this runtime client as `ExecutionClient` (and its
factory as `createExecutionClient`). The older fact-only `submitProposal` API
does not execute tools or issue identities; it rejects temporal requests instead
of silently ignoring their constraints.

```ts
import {
  OpenKedgeEngine, OpenKedgeClient, InMemoryIEECStore, IdentityManager,
  type TemporalGovernanceOptions
} from 'openkedge'

const store = new InMemoryIEECStore()
const governance: TemporalGovernanceOptions = {
  secretKey: process.env.OPENKEDGE_CAPABILITY_SECRET!,
  actions: {
    lookup_instance_cost: {
      kind: 'READ',
      capabilityBindings: {
        resourceId: 'resourceId',
        targetAccountId: 'targetAccountId'
      },
      capabilityTtlMs: 15 * 60_000
    },
    terminate_instance: {
      kind: 'MUTATION',
      requiredCapabilities: ['lookup_instance_cost']
    }
  },
  rules: [{
    type: 'PRECEDING_EVENT_REQUIRED',
    targetAction: 'terminate_instance',
    requiredPrecedingAction: 'lookup_instance_cost',
    windowMs: 15 * 60_000,
    matches: [{
      currentPath: 'payload.resourceId',
      historicalPath: 'result.resourceId'
    }]
  }],
  contractTtlMs: 30_000,
  maxDurationMs: 10_000
}

// Supply trusted contextProvider, policyEvaluator, executor, identityProvider.
const engine = new OpenKedgeEngine(
  contextProvider, policyEvaluator, executor,
  new IdentityManager(identityProvider, store), store,
  undefined, undefined, governance
)
const client = new OpenKedgeClient(engine, store)
```

Use a randomly generated secret with at least 32 bytes, stored outside source
control. Workers sharing a database must share the signing key and governance
configuration. Rotating the key invalidates outstanding tokens and contracts.
The current implementation uses HMAC-SHA256; it does not implement Ed25519 or a
multi-key rotation protocol.

## Capabilities

Only actions classified as `READ` or `PROBE` in **server configuration** mint
capabilities. Each must define a nonempty `capabilityBindings` mapping from a
target payload path to a **trusted executor result path**. Tokens are issued
only after successful execution has been appended to local evidence. Do not
bind identifiers extracted directly from untrusted tool prose; the adapter must
resolve and validate them against its authoritative result.

```ts
const read = await client.submitIntent({
  id: 'lookup-1', type: 'lookup_instance_cost',
  payload: { resourceId: 'i-dev' },
  metadata: { actor: 'agent-1', timestamp: Date.now() }
})
const mutation = await client.submitIntent({
  id: 'terminate-1', type: 'terminate_instance',
  payload: { resourceId: 'i-dev', targetAccountId: 'acc-123' },
  capabilities: read.capabilities,
  requiredCapabilities: read.capabilities?.map(token => token.tokenId),
  metadata: { actor: 'agent-1', timestamp: Date.now() }
})
```

`Intent.requiredCapabilities` names token IDs requested by the caller.
`actions[action].requiredCapabilities` names mandatory successful **source
actions** chosen by the server; the two requirements are cumulative. Configure
every mutation action and alias that needs attenuation. Requirements do not
automatically propagate between differently named tool actions.

Tokens are opaque authorization artifacts to callers, not encrypted objects.
The HMAC authenticates every field, including token ID, actor, source proposal
and exact source event hash, issue/expiry times, and canonical JSON bindings.
Keys are sorted recursively; arrays remain ordered. Comparisons are type
sensitive, use own properties only, and reject missing fields, non-JSON values,
and prototype traversal. A downstream request can add unrelated parameters but
cannot change any bound value. Bind **all** security-relevant parameters.

Verification checks signature, `issuedAt <= now < expiresAt`, matching actor,
matching parameters, and a local successful READ/PROBE with the exact source
hash and matching trusted output. Source action requirements cannot be satisfied
by a failed read, a fabricated historical entry, or an unregistered read action.
A capability may authorize multiple fresh proposals until expiry; it is not a
single-use nonce. Quota/rate rules can limit reuse. Proposal IDs, however, are
unique and cannot be executed twice.

## Temporal rules

Rules are ordinary JSON objects described by
[`temporal-rule.schema.json`](../schemas/temporal-rule.schema.json).
`matches` compares declared paths with exact canonical JSON equality. A trusted
in-process `matchPredicate` function is also supported, but cannot appear in a
JSON rule. Predicates must be deterministic and have no side effects.

```json
{
  "id": "shared-transfer-budget",
  "type": "SLIDING_WINDOW_QUOTA",
  "targetAction": "transfer",
  "scope": "RESOURCE",
  "resourcePath": "accountId",
  "windowMs": 86400000,
  "metricPath": "intent.payload.amount",
  "maxCumulativeValue": 500
}
```

| Rule | Required fields | Admission condition |
| --- | --- | --- |
| `SLIDING_WINDOW_QUOTA` | `metricPath`, `maxCumulativeValue` | historical usage + reserved usage + proposed value <= limit |
| `RATE_LIMIT` | `maxCount` | completed count + reserved count + 1 <= limit |
| `PRECEDING_EVENT_REQUIRED` | `requiredPrecedingAction` | a successful matching prior proposal exists in the window |

Windows use `(now - windowMs, now]` at trusted server time, never the agent's
timestamp. `ACTOR` is the default scope; `GLOBAL` spans all actors, and
`RESOURCE` spans actors sharing `intent.payload[resourcePath]`. Successful
execution is counted once at completion time. Failed and future completions do
not satisfy prerequisites. Missing, negative, nonnumeric, or nonfinite metrics
reject admission. Use integer minor units for money to avoid floating-point
rounding; the demo uses whole-dollar values for display.

`evaluateTemporalRules(intent, history, rules, now)` and
`aggregateHistoricalMetric(history, actor, action, metricPath, windowMs, now)`
are side-effect-free query/reduction APIs. The latter sums successful completed
records, not reservations. A standalone evaluation is advisory: admission must
use the engine's transactional check and reservation.

## Atomic admission and durable traces

```text
Intent received -> Capability verification -> Context lookup
 -> Temporal evaluation -> Policy evaluation
 -> [Atomic capability/temporal recheck + signed contract + quota reservation]
 -> Contract/precondition check -> Identity issuance -> Recheck -> Execution
 -> Completion evidence -> Identity revocation -> Read capability issuance
```

`IEECRecord` is an indexed projection of the existing immutable hashed evidence
events, not an independently mutable quota counter. `ExecutionReserved`,
`ExecutionCompleted`, and `ExecutionCancelled` reduce to `RESERVED`, `SUCCESS`,
and `FAILED`. Finalized executions cannot be refunded by a subsequent failure
event, and completion must retain the reserved intent snapshot. Reads and writes
return immutable snapshots. Quotas count both
successful records and unresolved reservations, so agents racing across engines
cannot both spend the same remaining budget. Winner ordering across database
processes is not guaranteed; the aggregate bound is.

Reservation and check commit **before** external execution. Database locks are
never held while invoking a tool. A confirmed failure (`success: false`) releases
the reservation; executors must return that result only when they know the
operation had no effect. Exceptions/timeouts after invocation are uncertain and
keep reservations, even beyond the sliding window. A failure before invocation
records `ExecutionCancelled` and releases budget. An operator must reconcile
uncertain outcomes with the external system and append a verified completion
or cancellation. Never automatically refund timed-out work. There is no public
unauthenticated reconciliation endpoint.

Storage adapters:

| Store | Coordination | Persistence |
| --- | --- | --- |
| `InMemoryIEECStore` / `InMemoryEventStore` | FIFO mutex per shared instance | process lifetime |
| `SQLiteIEECStore` | `BEGIN IMMEDIATE`, async retry on busy locks | supplied SQLite database |
| `PostgresIEECStore` | transaction advisory lock shared by all writers | supplied Postgres pool |

SQLite/Postgres index action, actor, status, timestamp, proposal, and hash. Memory
queries use sorted indexes and binary searches. All quota participants must use
the **same authoritative store** and these transaction APIs. Separate memory
instances and direct database writes are not coordinated. FileEventStore does
not support temporal admission and is rejected when governance is enabled.

```ts
// Node >=22.13; node:sqlite may emit an experimental warning on some Node versions.
import { DatabaseSync } from 'node:sqlite'
const sqlite = new SQLiteIEECStore(new DatabaseSync('ieec.sqlite'))

// Optional application dependency: npm install pg
import { Pool } from 'pg'
const postgres = new PostgresIEECStore(new Pool({ connectionString: process.env.DATABASE_URL }))
await postgres.initialize() // provision tables/indexes before serving requests
```

Injecting drivers avoids imposing native SQLite or Postgres dependencies on all
users. SQLite also accepts a compatible `better-sqlite3` database. Use dedicated
connections for these stores; do not run unrelated transactions on an injected
SQLite connection. Protect database write access; hashes do not defend against
an administrator who can replace the complete history and signing key.

SQLite lock behavior follows [SQLite transactions](https://www.sqlite.org/lang_transaction.html);
Postgres coordination uses [transaction advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS).

## Execution contract bounds

[`execution-contract.schema.json`](../schemas/execution-contract.schema.json)
defines the signed contract. It authenticates the full intent hash, actor,
action, embedded capabilities, linked token IDs, and prerequisite SHA-256 hashes.
`temporalBounds` has `notBefore`, exclusive `notAfter`, and `maxDurationMs`;
`temporalValidity.validAfter/validBefore` mirrors those timestamps.
Callers can request narrower bounds on an intent but cannot extend server limits.

The cutoff is the minimum of the configured TTL, requested cutoff, capability
expiry, and required preceding-event expiry. A future activation is rejected
for now rather than queued. IdentityManager verifies the signed contract and
precondition evidence before issuance, again after issuance, and immediately
before invoking the executor. It caps local identity expiry and supplies an
`AbortSignal`. On deadline it aborts, revokes the identity, reports a timeout,
and retains uncertain quota. Executors should check the signal and identity
before each remote operation. Cancellation cannot undo a remote side effect.

The AWS STS adapter additionally adds `DateGreaterThanEquals` and `DateLessThan`
conditions using [`aws:CurrentTime`](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-keys.html#condition-keys-currenttime)
to the scoped session policy, so the cutoff is not only local metadata. This
does not shorten AWS's minimum STS session duration; it restricts the requests
the session may authorize. Prerequisite hashes are checked by OpenKedge before
STS issuance, not evaluated by IAM itself. Other identity adapters must provide
equivalent remote enforcement if credentials can leave the trusted executor.

## Demo and validation

Start `npm --workspace apps/demo-server run dev` and `npm run dev:replay-ui`;
open `http://127.0.0.1:4173/`. Click **Run Capability Attack** to inspect a dev
instance and attempt to terminate a production DB instead. The mutation returns
`CAPABILITY_MISMATCH_ERROR` without receiving credentials. **Run Budget Scenario**
shows `$150 / $500` used, with a second agent's `$400` request rejected.

The replay displays clickable source read nodes, dotted causal links, token
badges, budget snapshots, and contract bounds. Offline temporal examples are
generated by the actual engine (`npx ts-node scripts/generate-temporal-replays.ts`),
including valid hashes. Source-evidence verification and token acceptance are
displayed separately; an existing source does not make a tampered token valid.

The demo is mock-only, binds to loopback, and trusts actor metadata for ease of
use. Production entry points must authenticate the caller and derive the actor
from that authenticated identity. Do not expose the demo or allow agents to
edit server rules, keys, trusted adapters, or IEEC writes.

```sh
npm test
npm run typecheck
npm run build
npm run build:replay-ui
npm run bench:temporal
```

The benchmark seeds 25,000 events and measures 200 warmed evaluations over a
100-record window. On the development host, memory p95 was 0.016ms and SQLite
p95 was 0.529ms. These are local query/reducer measurements, not a guarantee for
arbitrary history sizes, predicate costs, database load, or network latency.
Postgres latency must be measured in its deployment environment.

The test suite runs real SQLite conformance on Node versions with `node:sqlite`.
For actual Postgres conformance, install `pg` and set
`OPENKEDGE_TEST_POSTGRES_URL` to a disposable test database; tests create and
drop isolated schemas. Without it, that suite is explicitly skipped. No
Postgres server is required for the remaining tests.
