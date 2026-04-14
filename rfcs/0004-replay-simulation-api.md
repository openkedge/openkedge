# RFC-0004: Replay & Simulation API (RSA)

**Status:** Draft  
**Author:** Jun He  
**Project:** OpenKedge  
**Date:** April 2026  
**Scope:** Validation Framework for Intent Replay and Simulation

---

## 1. Abstract

This document defines the Replay & Simulation API (RSA), a standardized interface for querying, deterministic replaying, and simulating intent-driven mutations recorded in the Event Evidence Chain (EEC, [RFC-0002]).

As autonomous agents transition from advisory to operational roles in mission-critical infrastructure, the ability to interrogate historical decisions is no longer an optional observability feature—it is a requirement for **National Security** and **Algorithmic Accountability**. While the EEC provides a passive cryptographic ledger, the RSA transforms this ledger into an active reasoning interface. It enables time-travel state reconstruction, inspection of AI logic (explainability), and the evaluation of hypothetical "what-if" scenarios (simulation) without side effects.

---

## 2. Conventions and Definitions

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [RFC2119] [RFC8174] when, and only when, they appear in all capitals, as shown here.

---

## 3. Problem Statement

Autonomous systems generate high-velocity decisions based on transient, high-dimensional context. In a sovereign cloud or smart city environment (e.g., NEOM/Tonomus), the inability to interrogate this history leads to:
*   **Opaque Reasoners:** Black-box AI decisions that lack a verifiable "Why" trail, creating unacceptable liability and security risk.
*   **Governance Drift:** Inability to validate policy updates against historical edge cases before wide-scale deployment.
*   **Forensic Vacuum:** Difficulties in reconstructing the exact system state during a security incident or autonomous failure.
*   **Contextual Ambiguity:** testing agent behavior in non-deterministic environments that do not mirror the ground-truth state of the production environment.

---

## 4. Terminology

*   **Trace:** A structured, hierarchical representation linking an Intent to its Context, Decision, and Execution.
*   **Replay:** The deterministic reconstruction of historical system state using the immutable events in the EEC.
*   **Simulation:** The evaluation of a hypothetical mutation lifecycle using modified inputs (Policies, Contexts, or Intents) without executing state changes.
*   **Verifiable Action Record (VAR):** The formal data structure (Evidence Link) representing a single node in the chain.
*   **Time Cursor ($\tau$):** A strict temporal boundary used to isolate system state queries to a specific point in the past.

---

## 5. API Overview and Formalization

The RSA provides three core operational planes:
1.  **Query:** Retrieving traces and topological event graphs.
2.  **Replay:** Reconstructing absolute state.
3.  **Simulation:** Sandboxed policy and context evaluation.

### 5.1 State Reconstruction (Time-Travel)
The absolute state $S$ at any timestamp $\tau$ MUST be strictly derived by folding the Event Evidence Chain up to that point:

$$S_{\tau} = \text{fold}(\{E_i \in \text{EEC} \mid E_i.\text{timestamp} \leq \tau\})$$

---

## 6. Specification: Query API

The Query API provides read-only access to historical traces.

### 6.1 Trace Retrieval
Clients MAY query the complete lifecycle of a mutation via its `intent_id`.

**Request:** `GET /v1/trace/{intent_id}`

**Response Contract:**
The response MUST return a hierarchical JSON object resolving the `IntentSubmitted`, `ContextEvaluated`, `PolicyEvaluated`, and `ExecutionCompleted` events.

---

## 7. Specification: Replay API

The Replay API reconstructs historical behavior deterministically.

### 7.1 Replay Execution
Replay MUST process events in strict causal order.
Replay MUST utilize the historically recorded Context Snapshots.
Replay MUST NOT interact with live external systems, APIs, or mutable state.

---

## 8. Specification: Simulation API

Simulation allows operators to inject overrides into a historical or hypothetical Intent to observe the resulting decision boundary, without side effects.

### 8.1 Simulation Constraints
Simulation MUST NOT alter the production Evidence Chain.
Simulation MUST NOT provision an Execution Identity (RFC-0003) or interact with the execution plane.

---

## 9. Security Considerations

### 9.1 Simulation Isolation
Simulation environments MUST be strictly air-gapped from the execution plane. Under no circumstances may a simulated decision or context bypass the sandbox to trigger a physical infrastructure mutation.

### 9.2 Data Residency & Redaction
In compliance with **SDAIA/NDMO** standards, Replay APIs SHOULD support selective redaction of fields marked as sensitive in the schema (PII, Topology secrets) before returning traces to the client.

---

## 10. The Verifiable Action Record (VAR) Specification

To move beyond simple JSON logs, OpenKedge standardizes on the **Verifiable Action Record (VAR)**. Each "link" in the chain must bind the intent to the outcome, ensuring cryptographic lineage and auditability.

$$ChainLink = \{Identity, Intent, Evaluation, Execution, Proof\}$$

### 10.1 Data Schema (TypeScript Definition)
```typescript
/**
 * Verifiable Action Record (VAR)
 * Standardized data structure for a single link in the Evidence Chain.
 */
interface EvidenceLink {
  // 1. EXECUTION IDENTITY (WHO)
  // Refers to the RFC-0003 identity model.
  agentId: string;
  traceId: string;

  // 2. SOVEREIGN MASKED INTENT (WHAT the model thought)
  // Included directly for explainability and reasoning audits.
  intent: {
    foreignModel: string;
    rawJustification: string; // The natural language "Reasoning" from the AI
    abstractedPayload: string; // The masked command sent to the reasoner
  };

  // 3. EVALUATION METADATA (WHY we allowed it)
  // Captures the deterministic decision boundary.
  evaluation: {
    evaluatorId: string;
    policyVersion: string;
    telemetrySnapshotHash: string; // Hash of the context at decision time
    decision: "APPROVED" | "REJECTED";
  };

  // 4. BINDING & EXECUTION (HOW it happened)
  execution: {
    realResource: string;      // The unmasked resource identifier
    actualCommand: string;     // The final physical command executed
    timestamp: "ISO-8601";
  };

  // 5. CRYPTOGRAPHIC PROOF
  prevLinkHash: string;        // Hash linkage to maintenance chain integrity
  signature: string;           // Signed by the local Sovereign Kedge
}
```

---

## 11. Rationale and Alternatives

Traditional infrastructure relies on disjointed logs and manual correlation to debug failures. When AI agents operate at machine speed, manual correlation becomes impossible.

**Alternatives Considered:**
*   **Standard Tracing (OpenTelemetry):** Valuable for performance latency, but insufficient for full state-reconstruction and deterministic reasoning capture required for sovereign governance.
*   **Snapshot-only Backups:** Capture the "What" (state) but completely lose the "Why" (intent and policy reasoning).

---

## 12. Future Work

1.  **Adversarial Auto-Simulation:** Utilizing LLMs to automatically generate "attack intents" and simulating them against current policy engines to discover governance vulnerabilities.
2.  **Standardized Replay Query Language (RQL):** Implementing a GraphQL or SQL-like syntax for complex dependency queries across the Evidence Chain.
