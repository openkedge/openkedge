# OpenKedge Architecture

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
