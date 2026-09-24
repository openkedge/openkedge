# OpenKedge Architecture

## Agent control plane path

```text
Agent proposal generation (untrusted MCP arguments and retrieved text)
  → optional typed JudgmentProvider (routes to more assurance; cannot authorize)
  → deterministic ContextProvider, AWS safety policy, blast radius, CAC and temporal admission
  → signed existing ExecutionContract with policy revision
  → execution gateway checks actual operation, target, parameters, current policy and state
  → mock adapter
  → IEEC events and ReplayEngine verification
```

The gateway uses the runtime `Intent` and `TemporalGovernance` contract path. A future Jev integration can implement `JudgmentProvider` and request an additional assurance check; a judgment cannot supply missing authorization or replace the deterministic decision. See [the gateway RFC](../rfcs/0006-mcp-execution-gateway.md) and [implementation note](mcp-gateway-implementation.md).

Agent → Intent → Context → Policy → Event → State

1. **Agent** submits an `IntentProposal`.
2. Engine builds **Context** by loading recent events and current derived state.
3. **Policy Engine** evaluates the proposal against the context and rules.
4. If approved, a **TruthEvent** is appended to the store.
5. A deterministic **Reducer** recomputes the final **State**.

## Execution runtime: temporal invariants

The separate execution runtime under `src/core` adds capability verification and
temporal reductions before policy evaluation. Admission rechecks constraints and
appends a signed contract plus durable quota reservation in one IEEC transaction.
Credential issuance then requires the contract's time bounds and prerequisite
hashes to be satisfied. Successful READ/PROBE results mint capabilities from
trusted output bindings. See [Temporal Invariants & Capability Attenuation](temporal-invariants.md)
for API configuration, storage coordination, and timeout semantics.
