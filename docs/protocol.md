# OpenKedge Protocol Specification

This is the engineering translation of the OpenKedge paper.

## Core Schemas

### IntentProposal
An intention submitted by an actor to mutate state.

### Fact
A discrete piece of information about an entity's state.

### TruthEvent
An immutable record of approved facts appended to the event store.

## Policy Function
Evaluates an `IntentProposal` against the current `Context` and rules to produce a `PolicyDecision` (approve, reject, or escalate).

## Reducer
Computes the current `DerivedState` from a sequence of `TruthEvent` records.
