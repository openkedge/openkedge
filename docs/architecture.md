# OpenKedge Architecture

Agent → Intent → Context → Policy → Event → State

1. **Agent** submits an `IntentProposal`.
2. Engine builds **Context** by loading recent events and current derived state.
3. **Policy Engine** evaluates the proposal against the context and rules.
4. If approved, a **TruthEvent** is appended to the store.
5. A deterministic **Reducer** recomputes the final **State**.
