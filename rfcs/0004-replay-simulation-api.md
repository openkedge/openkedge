# RFC-0004: Replay & Simulation API (RSA)

**OpenKedge Standard** **Intended status:** Standards Track  
**Created:** 2026-04-12  
**Authors:** OpenKedge Contributors  
**Updates:** [RFC-0002](./0002-event-evidence-chain.md)

---

## 1. Abstract

This document defines the Replay & Simulation API (RSA), a standardized interface for querying, deterministic replaying, and simulating intent-driven mutations recorded in the Event Evidence Chain (EEC, [RFC-0002]).

While the EEC provides a passive cryptographic ledger of autonomous actions, the RSA transforms this ledger into an active observability and reasoning interface. It enables time-travel state reconstruction, inspection of AI decision-making (explainability), and side-effect-free evaluation of hypothetical scenarios (simulation). Through RSA, OpenKedge serves as a deterministic "debugger" for AI-driven infrastructure decisions.

---

## 2. Conventions and Definitions

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [RFC2119] [RFC8174] when, and only when, they appear in all capitals, as shown here.

---

## 3. Problem Statement

Autonomous systems generate high-velocity decisions based on complex, transient context. Without a standardized interface to interrogate this history, systems suffer from:
* **Opaque Reasoning:** Inability to answer *why* an agent executed a specific mutation.
* **Testing Deficiencies:** Policy changes cannot be safely validated against historical edge cases.
* **State Ambiguity:** Difficulties in reconstructing the exact state of the world at the time a bad decision was made.
* **Lack of Introspection:** Developers lack the tooling to debug distributed, multi-agent workflows deterministically.

---

## 4. Terminology

* **Trace:** A structured, hierarchical representation linking an Intent to its Context, Decision, and Execution.
* **Replay:** The deterministic reconstruction of historical system state using the immutable events in the EEC.
* **Simulation:** The evaluation of a hypothetical mutation lifecycle (or re-evaluation of a historical one) using modified inputs (Policies, Contexts, or Intents) without executing state changes.
* **Time Cursor ($\tau$):** A strict temporal boundary used to isolate system state queries to a specific point in the past.
* **Diffing:** The programmatic comparison between an actual historical Trace and a Simulated Trace.

---

## 5. API Overview and Formalization

The RSA provides three core operational planes:
1. **Query:** Retrieving traces and topological event graphs.
2. **Replay:** Reconstructing absolute state.
3. **Simulation:** Sandboxed policy and context evaluation.

**5.1 State Reconstruction (Time-Travel)**
The absolute state $S$ at any timestamp $\tau$ MUST be strictly derived by folding the Event Evidence Chain up to that point:

$$S_{\tau} = \text{fold}(\{E_i \in \text{EEC} \mid E_i.\text{timestamp} \leq \tau\})$$

---

## 6. Specification: Query API

The Query API provides read-only access to historical traces.

### 6.1 Trace Retrieval
Clients MAY query the complete lifecycle of a mutation via its `intent_id`.

**Request:** `GET /v1/trace/{intent_id}`

**Response Contract:**
The response MUST return a hierarchical JSON object resolving the `IntentSubmitted`, `ContextEvaluated`, `PolicyEvaluated`, and `ExecutionCompleted`/`ExecutionFailed` events.

```json
{
  "trace_id": "uuid",
  "intent": { ... },
  "context_snapshot": { ... },
  "decision_record": { "status": "ALLOW", "reasoning": "..." },
  "execution_result": { ... },
  "causal_graph": { "parent_intents": [], "child_intents": [] }
}
```

---

## 7. Specification: Replay API

The Replay API reconstructs historical behavior deterministically.

### 7.1 Replay Execution
Replay MUST process events in strict causal order.
Replay MUST utilize the historically recorded Context Snapshots.
Replay MUST NOT interact with live external systems, APIs, or mutable state.

**Request:** `POST /v1/replay`
```json
{
  "intent_id": "uuid",
  "until_event": "event_id" // OPTIONAL: For partial replay
}
```

---

## 8. Specification: Simulation API

Simulation allows operators to inject overrides ($C_{override}, P_{override}$) into a historical or hypothetical Intent to observe the resulting decision boundary, without side effects.

### 8.1 Simulation Constraints
Simulation MUST NOT alter the production Evidence Chain.
Simulation MUST NOT provision an Execution Identity (RFC-0003) or interact with the execution plane.

**Request:** `POST /v1/simulate`
```json
{
  "intent_id": "uuid",
  "overrides": {
    "policy_version": "v2.1.0",
    "context_patches": [
      { "op": "replace", "path": "/resource/status", "value": "critical" }
    ]
  }
}
```

### 8.2 Diffing Response
The Simulation engine SHOULD return a delta comparing the actual historical execution against the simulated outcome.
$$ \Delta = \mathcal{S}(\text{Intent}, C_{override}, P_{override}) \oplus \text{Trace}_{actual} $$

```json
{
  "simulated_decision": "DENY",
  "differences": {
    "decision_changed": true,
    "actual": "ALLOW",
    "simulated": "DENY",
    "failing_constraint": "region_restriction_v2"
  }
}
```

---

## 9. Security Considerations

### 9.1 Simulation Isolation
The execution of the `simulate` endpoint MUST be strictly sandboxed. Under no circumstances may a simulated context or policy bypass the airgap to trigger a physical infrastructure mutation.

### 9.2 Data Exposure & Redaction
Because ContextEvaluated snapshots may contain sensitive environment variables or PII, the Query and Replay APIs MUST implement Role-Based Access Control (RBAC). Furthermore, the API SHOULD support selective redaction of fields marked as sensitive in the schema before returning Traces to the client.

### 9.3 Replay Integrity
The Replay API MUST mathematically validate the cryptographic hash chain of the retrieved events (as defined in RFC-0002, Section 8) prior to reconstructing state. If the chain is invalid, the API MUST return a 500 Internal Server Error indicating cryptographic tampering.

---

## 10. Rationale and Alternatives

Traditional infrastructure relies on disjointed logs and manual correlation to debug failures. When AI agents operate at machine speed, manual correlation becomes impossible.

**Alternatives Considered:**
* **Standard APM / Tracing (e.g., Jaeger, OpenTelemetry):** Rejected as the primary layer. APM tools trace network latency and spans, but they do not capture declarative intent, policy reasoning, or deterministic state snapshots necessary for "what-if" simulations.
* **Offline Local Simulation Tools:** Rejected. Decoupling simulation from the live governance plane leads to policy drift where local tests pass but live executions fail.

---

## 11. Future Work

1. **Visual Replay Interfaces:** Development of graphical dashboards to visualize state evolution over time.
2. **Adversarial Auto-Simulation:** Utilizing LLMs to automatically generate hypothetical "attack intents" and simulating them against the active policy engine to continuously discover governance vulnerabilities.
3. **Standardized Replay Query Language (RQL):** Implementing a GraphQL or SQL-like syntax for complex cross-intent dependency queries.
