# RFC-0003: Execution Identity (EI)

**OpenKedge Standard** **Intended status:** Standards Track  
**Created:** 2026-04-12  
**Authors:** OpenKedge Contributors  
**Updates:** [RFC-0001](./0001-intent-governance-protocol.md)

---

## 1. Abstract

This document defines Execution Identity (EI), a time-bound, intent-scoped credential model for executing state mutations within the Intent Governance Protocol (IGP, [RFC-0001]).

Execution Identity replaces traditional long-lived credentials (e.g., IAM roles, service accounts) with ephemeral, cryptographically verifiable identities. These identities are bound exclusively to a specific evaluated intent, strictly constrained by policy, and automatically revoked post-execution. EI ensures that every autonomous mutation is executed with absolute least-privilege, a bounded time-to-live (TTL), and undeniable cryptographic accountability.

---

## 2. Conventions and Definitions

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [RFC2119] [RFC8174] when, and only when, they appear in all capitals, as shown here.

---

## 3. Problem Statement

Current cloud identity systems are designed for human operators or static application workloads. They suffer from the following architectural flaws when applied to autonomous agents:

* **Static Scope:** Roles are broadly scoped and detached from the specific context of individual actions.
* **Temporal Exposure:** Long-lived credentials create an unbounded window for credential theft and replay attacks.
* **Audit Disconnect:** There is no systemic linkage between the "reason" an action was taken (the decision) and the credential used to perform it (the execution).
* **Blast Radius:** A compromised agent with a static IAM role can execute arbitrary infrastructure mutations up to the limit of that role.

In agentic systems, where decisions are dynamic, high-frequency, and autonomous, pre-provisioned static identity models are fundamentally unsafe.

---

## 4. Terminology

* **Execution Identity (EI):** A temporary, single-use credential minted exclusively to execute one approved Intent.
* **Time-To-Live (TTL):** The strict maximum duration an EI remains cryptographically valid.
* **Scope:** The precise, immutable set of allowed actions and target resources granted to an EI.
* **Binding:** The cryptographic association linking an EI to its originating Intent and Evidence Chain event.
* **Revocation:** The explicit invalidation of an EI prior to its natural TTL expiration.

---

## 5. Model Overview

Execution Identity represents a fundamental shift in access control paradigms.

**Traditional IAM Mapping:**
Static identity determines permissions, which allow actions.
$Identity \mapsto Permissions \mapsto Actions$

**OpenKedge EI Derivation:**
Intent and Context are evaluated by Policy to dynamically derive a scoped Identity.
$Intent + Context \xrightarrow{Policy} EI \mapsto Action$

Formally, the Execution Identity is a mathematical derivation of the approved intent:
$$EI = \mathcal{M}(\text{Intent}_{appr}, \Delta t, \mathcal{K})$$

Where $\Delta t$ is the TTL and $\mathcal{K}$ is the system's signing key.

The lifecycle transition is:
`[Intent] → [Evaluated] → [Approved] → [EI Issued] → [Executed] → [EI Destroyed/Expired]`

---

## 6. Specification: Identity Structure

An Execution Identity payload MUST be formatted as a JSON Web Token (JWT) or an equivalent cryptographically signed object containing the following claims:

```json
{
  "identity_id": "uuid",
  "intent_id": "uuid",
  "issued_at": "ISO-8601",
  "expires_at": "ISO-8601",
  "scope": {
    "actions": ["ec2:TerminateInstances"],
    "resources": ["arn:aws:ec2:us-east-1:123456789012:instance/i-123"]
  },
  "constraints": {
    "source_ip": "10.0.0.0/8"
  },
  "signature": "string"
}
```

---

## 7. Specification: Issuing and Binding

### 7.1 Issuance Rules
An EI MUST ONLY be issued immediately following a deterministic ALLOW decision from the Policy Engine (RFC-0001).

The EI MUST be strictly derived from the evaluated intent; it cannot contain permissions broader than those explicitly requested and approved.

### 7.2 Cryptographic Binding
The EI MUST include the intent_id correlating to the Event Evidence Chain (RFC-0002).

Execution systems MUST verify the signature integrity and ensure the intent_id matches the active execution request before initiating the mutation.

---

## 8. Specification: Scope Enforcement

Execution systems (e.g., infrastructure provisioners, API proxies) MUST enforce the EI scope at runtime:

1. **Action-level constraints:** The system MUST reject any operation not explicitly listed in `scope.actions`.
2. **Resource-level constraints:** The system MUST reject mutations targeting resources not explicitly listed in `scope.resources`.

Example: If an agent requests termination of `[i-123, i-456]` but policy only permits `[i-123]`, the derived EI scope MUST ONLY contain `i-123`. The execution system MUST fail the termination of `i-456`.

---

## 9. Specification: Time Constraints and Revocation

### 9.1 Time-to-Live (TTL)
The EI MUST expire strictly at the `expires_at` timestamp.

The TTL SHOULD be as short as practically possible, reflecting the calculated expected execution duration (e.g., 5 seconds for an API call).

Execution systems MUST reject expired identities.

### 9.2 Revocation
The EI MUST support active revocation.

Revocation MUST immediately invalidate the identity across the execution plane.

Revocation MAY be triggered by context invalidation (e.g., the underlying target resource changes state before execution).

---

## 10. Integration with Existing Systems

To function within existing cloud provider constraints, OpenKedge SHOULD act as an Identity Broker:

Implementations MAY generate temporary native credentials (e.g., AWS STS AssumeRole with inline session policies) derived from the EI.

The inline session policy MUST precisely mirror the scope defined in the EI payload.

---

## 11. Security Considerations

### 11.1 Privilege Escalation
Strict scope derivation ensures that even if an execution node is fully compromised during the TTL window, the attacker can only execute the exact mutation approved by the policy engine.

### 11.2 Replay Attacks
Because the EI is bound to a specific `intent_id` (which is recorded in the immutable Evidence Chain per RFC-0002), the system MUST reject subsequent attempts to execute an EI if the corresponding intent has already been marked `ExecutionCompleted`.

### 11.3 Credential Leakage
The ultra-short TTL (often measured in seconds) drastically reduces the exposure window, rendering leaked EI tokens practically useless by the time they are discovered.

---

## 12. Rationale and Alternatives

Traditional identity systems force developers to guess the maximum required permissions for an agent upfront. OpenKedge inverses this: identity is no longer a pre-assigned static property, but a dynamic, ephemeral artifact of a verified governance decision.

**Alternatives Considered:**
* **Temporary Native Credentials without Binding:** Rejected. AWS STS provides temporary credentials, but they lack cryptographic binding to the causal intent, breaking the audit trail.
* **Just-In-Time (JIT) Role Assumption:** Rejected. JIT access typically grants broad roles for a specified time (e.g., "Admin for 1 hour"), violating the principle of least privilege per discrete action.

---

## 13. Future Work

1. **Hardware-Backed Execution Enclaves:** Tying EI generation to Trusted Execution Environments (TEEs) to prevent token extraction from memory.
2. **Identity Federation Protocols:** Standardizing EI translation into Kubernetes RBAC and generic OAuth 2.0 flows.
