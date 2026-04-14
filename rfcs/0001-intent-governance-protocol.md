# RFC-0001: Intent Governance Protocol (IGP)

**Status:** Draft  
**Author:** Jun He  
**Project:** OpenKedge  
**Date:** April 2026  
**Scope:** Core Protocol for Governed State Mutations

---

## 1. Abstract

This document defines the Intent Governance Protocol (IGP), a generalized protocol for governing state mutations in agentic and autonomous systems. IGP replaces direct API execution with a structured lifecycle consisting of intent submission, context evaluation, policy enforcement, execution under bounded identity, and cryptographic evidence recording. The protocol ensures that all system mutations are deterministic, auditable, and strictly constrained by explicit governance rules.

---

## 2. Conventions and Definitions

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [RFC2119] [RFC8174] when, and only when, they appear in all capitals, as shown here.

---

## 3. Problem Statement

Modern agentic systems interact with infrastructure through direct, imperative API calls. This model suffers from several critical limitations:
- **Semantic Gap:** API permissions (e.g., IAM roles) do not encode real-world safety constraints or business logic.
- **Context Blindness:** Mutations are executed without verifiable awareness of the broader system state.
- **Auditability:** There is no verifiable reasoning trail or cryptographic evidence behind autonomous actions.
- **Blast Radius:** The use of long-lived credentials for autonomous agents creates an unbounded risk of catastrophic failure.

As autonomous agents increasingly govern infrastructure, these limitations introduce unacceptable systemic risk.

---

## 4. Terminology

* **Intent:** A structured, declarative payload describing a desired state mutation.
* **Evaluation:** The deterministic process of gathering context and assessing an intent against active policies.
* **Execution:** The act of performing the mutation using a temporary, constrained identity.
* **Context:** The aggregate of relevant information required to evaluate an intent (e.g., current system state, historical data).
* **Policy Engine:** An isolated component responsible for rendering ALLOW/DENY decisions based on Context and Intent.
* **Execution Identity:** A short-lived, strictly scoped credential generated solely for an approved Intent.
* **Evidence Chain:** An append-only, verifiable ledger logging all intents, contexts, decisions, and execution results.

---

## 5. Protocol Overview

IGP defines a strict state machine for state mutations. All operations MUST follow this lifecycle:

```text
 [SUBMITTED] ---> [EVALUATED] ---> [APPROVED] ---> [EXECUTED] ---> [RECORDED]
                       |                |               |
                       v                v               v
                   [DENIED]        [EXPIRED]        [FAILED]
```

**SUBMITTED**: The agent generates and signs an Intent.

**EVALUATED**: The system gathers Context and passes the Intent to the Policy Engine.

**APPROVED / DENIED**: The Policy Engine renders a deterministic decision.

**EXECUTED**: If APPROVED, an Execution Identity is minted, and the mutation occurs.

**RECORDED**: The entire lifecycle is committed to the Evidence Chain.

---

## 6. Specification

### 6.1 Intent Structure
An Intent payload MUST be formatted as a JSON object containing the following fields:

- **actor** (String, REQUIRED): A unique identifier for the agent initiating the intent.
- **action** (String, REQUIRED): The standardized URI or string representing the operation.
- **target** (Array of Strings, REQUIRED): The unique identifiers of the resources to be mutated.
- **constraints** (Object, OPTIONAL): Key-value pairs defining execution boundaries (e.g., time-to-live, geographic regions).
- **metadata** (Object, OPTIONAL): Contextual data regarding the "why" of the mutation (e.g., natural language reasoning, issue tracker links).

### 6.2 Context Gathering and Evaluation
The Evaluation phase MUST retrieve all relevant context necessary to satisfy the requirements of the Policy Engine.

The context gathering process MUST NOT perform mutations.

The combination of Intent + Context MUST produce a deterministic ALLOW or DENY result from the Policy Engine.

### 6.3 Policy Enforcement
All intents MUST be evaluated by a defined Policy Engine (e.g., Open Policy Agent (OPA), AWS Cedar).

If the Policy Engine cannot be reached, the system MUST default to DENY.

A decision of DENY MUST immediately terminate the lifecycle and proceed to the RECORDED state.

### 6.4 Bounded Execution
Execution MUST occur under a Bounded Execution Identity.

The system MUST NOT use static or long-lived credentials to execute the mutation.

The generated credential MUST be scoped strictly to the action and target defined in the Intent.

The credential MUST expire immediately after execution or upon a predefined timeout.

### 6.5 Evidence Chain
All terminal states (RECORDED, DENIED, FAILED, EXPIRED) MUST generate an immutable entry in the Evidence Chain.
The resulting payload MUST include the original intent, the gathered context, the decision, the execution_result (if applicable), and a valid ISO-8601 timestamp.

---

## 7. Security Considerations

### 7.1 Blast Radius and Credential Leakage
By mandating Bounded Execution Identities (Section 6.4), IGP ensures that even if an execution context is compromised, the attacker only possesses permissions to execute the strictly approved Intent.

### 7.2 Replay Attacks
To prevent replay attacks, implementations SHOULD include a unique nonce or execution ID within the Intent metadata. The Evidence Chain MUST reject duplicate executions of the same identifier.

### 7.3 Policy Bypass
The architecture MUST ensure that the Execution layer cannot be reached without a valid cryptographic signature or token from the Policy Engine verifying an ALLOW decision.

### 7.4 Evidence Chain Integrity
The Evidence Chain SHOULD utilize an append-only data structure (e.g., a Merkle tree, WORM storage, or distributed ledger) to prevent historical tampering of autonomous decisions.

---

## 8. Registry Considerations (IANA equivalent)
While OpenKedge does not currently register with IANA, implementations of IGP MUST maintain an internal registry of supported action URIs to ensure interoperability between Agents and Policy Engines.

---

## 9. Rationale and Alternatives

### 9.1 Multi-Agent Intent Negotiation
Protocols for agents to co-sign or collaboratively refine Intents before submission.

### 9.2 Standardized Policy Schemas
A common vocabulary for translating agent reasoning directly into Cedar/Rego policies.
