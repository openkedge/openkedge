# RFC-0007: Controller distribution for MCP gateways

**Status:** Local multi-process protocol pilot, September 2026. Wire protocol version 1.

This RFC extends [RFC-0006](./0006-mcp-execution-gateway.md). It keeps the existing runtime `Intent`, `GatewayPolicy`, signed `ExecutionContract`, IEEC events, and MCP proposal/redemption tools. Jev may supply typed judgment that requests more assurance or denies a proposal; its response cannot sign a grant, activate policy, issue a dispatch permit, or change the policy epoch.

## Roles and wire interface

An administrator publishes a `GatewayPolicy` to one controller. The controller stores it in SQLite, increments a positive integer epoch, assigns a UUID bundle ID, sets `issuedAt` and `leaseUntil`, and signs the canonical JSON of every field except `signature` with Ed25519. The gateway pins the controller public key. Its signed bundle revision is `epoch:version@sha256(canonical policy)`. A new bundle at the same epoch is a conflict, even if the policy text matches. A lower epoch is a rollback. Gateways persist the highest verified bundle to their own local state before acknowledging activation. Missing signatures, malformed bundles or policies, invalid signatures, wrong epochs, and unauthorized HTTP responses are rejected.

Protocol endpoints use JSON and the [versioned schemas](../schemas/control-policy-bundle.v1.schema.json). A gateway authenticates to the controller with a per-gateway bearer token and `x-okg-gateway-id`; only the matching token is accepted. An administrator uses a separate token. Non-loopback gateway clients require HTTPS. The local controller server is plain HTTP on loopback; remote deployment needs a TLS terminator that preserves and protects the authenticated headers.

| Method and path | Caller | Meaning |
| --- | --- | --- |
| `GET /v1/policy` | Gateway | Obtain the current signed bundle. |
| `POST /v1/ack` | Gateway | Acknowledge bundle ID, epoch, revision and local activation time after persisting. |
| `POST /v1/permit` | Gateway | Request a short dispatch permit bound to proposal ID, contract ID, actor, exact operation and current policy revision. |
| `POST /v1/outcome` | Gateway | Report permit ID, proposal, grant, outcome and completed IEEC event hash. |
| `POST /v1/policy` | Administrator | Validate a policy and advance the epoch. The response gives activation time. |
| `GET /v1/status` | Administrator | Read current epoch, per-gateway acknowledgements, active permits and outcomes. |

Controller activation is not the same as gateway activation. A policy update becomes current at the controller when `POST /v1/policy` returns. Each gateway acknowledges only after it has fetched, verified and locally persisted that bundle. A disconnected gateway can lag indefinitely; its status remains at the last acknowledgement. The controller does not push to gateways. Consequential operations fetch on use, so an offline gateway cannot start a new one. A bundle acknowledgement does not itself revoke an in-flight API call.

Revocation in v1 is an **epoch advance**, not a per-grant list: every unredeemed grant from an earlier epoch is invalid once the controller activates the new epoch, including when policy content is otherwise unchanged. Removing a target from the new policy also blocks new admissions and permits for that target. A gateway that cannot fetch the current epoch fails closed for consequential operations. Already-issued dispatch permits are handled by the pending-update rule below. The controller exposes no endpoint that can recall an external API request already sent.

## Action classes and outage behavior

Action class is a trusted gateway mapping from operation name, never an MCP argument, policy free text, memory, or Jev response. `ec2:TerminateInstances` is consequential. A bounded action may use a previously verified local bundle only while `Date.now() < leaseUntil`; after that it must obtain a newly signed epoch. The sample client exposes `currentFor('bounded')` for implementations of low consequence actions, but this pilot does not advertise a bounded mutation tool. The controller gives each bundle a 60-second lease; the administrator must republish even an unchanged policy to renew it. During controller outage, a bounded action may use its remaining lease, while a consequential admission or redemption fails closed.

For consequential redemption, the gateway refetches policy, verifies the existing grant and exact parameters, consumes the grant once in its local IEEC database, refetches and compares policy and context, then requests a controller permit before the adapter call. The controller checks the current revision and target guard again. An update stops issuing new permits, waits for active permits to finish or expire, and then activates. A redemption arriving during that pending interval fails with `POLICY_UPDATE_PENDING`; the consumed grant cannot be retried. The gateway reports the completed IEEC event hash before the permit is released. This gives an ordering boundary for a healthy, responsive mock dispatch: activation occurs after an already-permitted call completes, and old unredeemed grants fail on the next fresh check.

The controller's permit check covers its allowlist, protected targets and parameters. It trusts the authenticated gateway to verify the signed grant, IEEC one-use transition, temporal rules and live resource state; the controller does not fetch EC2 state itself. A compromised gateway remains a bypass of those checks.

**Revocation is not instantaneous.** A permit expires no later than its grant and five seconds after issuance. If the gateway stalls or crashes, the controller can activate a new policy after permit expiry while an external API request may still arrive. EC2 has no atomic OpenKedge epoch condition on `TerminateInstances`. A policy update between the gateway's final local check and underlying API call can therefore leave the old call valid while its permit is active; the multi-process test deliberately exercises that interval. Stronger revocation needs a downstream execution service or API that enforces an epoch fence at the mutation itself. During controller outage, an already-issued permit may still reach the API, while outcome reconciliation can remain pending.

## Evidence and limits

The gateway records policy revision, bundle ID and epoch in decision evidence, and its completed execution event carries the permit ID. The controller stores gateway acknowledgements and outcome hashes. Operators can join by proposal ID, contract ID, permit ID and IEEC event hash, then verify the full local IEEC chain. The controller accepts a hash from an authenticated gateway; it does not independently verify that chain. If outcome delivery fails after an API attempt, the gateway returns an uncertain result and the controller may have no outcome row. This pilot has no durable outcome retry queue or global evidence replication.

One stdio gateway still serves one launcher-attested caller. The controller authenticates a gateway process, not an MCP caller or delegating human. Compromised gateway tokens, signing or private keys, controller SQLite, gateway cache/evidence storage, local clock, launcher, or direct cloud credentials bypass parts of this boundary. Gateway caches must have separate protected paths; deleting a cache removes its rollback memory. The local test proves behavior on loopback processes with mock API calls. It does not prove cross-region delivery, TLS termination, network partition tolerance, AWS API fencing or remote caller authentication.
