# RFC-0005: Sovereign Proxy Pattern for Cross-Border Agentic Governance

**Status:** Draft  
**Author:** Jun He  
**Project:** OpenKedge  
**Date:** April 2026  
**Scope:** Architecture for Decoupling Reasoning from Execution-Bound Governance

---

## 1. Abstract
As autonomous agents move from advisory roles (Copilots) to operational roles (Operators), a fundamental conflict arises between **Frontier Model Intelligence** and **Data Sovereignty**. Current agentic architectures require full environment context to be transmitted to the model provider, posing unacceptable risks to national security, infrastructure topology secrecy, and regulatory compliance.

This document (RFC-0005) defines the **Sovereign Proxy** pattern: a control plane architecture that decouples high-parameter "Foreign Reasoners" from sensitive "Sovereign Execution Zones." This pattern enables the utilization of top-tier global AI (e.g., GPT-5, Claude 4) while ensuring that infrastructure metadata remains resident and final execution authority remains strictly within a local, jurisdictionally-bound boundary.

## 2. Problem Statement
In a national infrastructure context (e.g., KSA Smart Cities, Energy, or Sovereign Finance), the "Context Leakage" risk is twofold:
1.  **Metadata Exposure:** Transmitting system topology, internal IP schemes, or privileged resource labels to external AI APIs violates national data residency and security standards.
2.  **Unverifiable Intent:** Executing commands where the "Reasoning Process" was formulated in a black-box environment outside local jurisdiction creates a governance vacuum, leaving no verifiable link between action and intent.

## 3. The Sovereign Proxy Architecture
The Sovereign Proxy acts as a **Cellular Membrane** between the Unrestricted Reasoning Zone (External) and the Sovereign Execution Zone (Internal).

### 3.1 Components
*   **The Reasoner (Foreign):** A high-performance LLM located outside the sovereign boundary. It receives only masked, anonymized context.
*   **The Obfuscator:** A local service that replaces sensitive resource identifiers with transient, synthetic tokens (e.g., `prod-db-riyadh-01` becomes `resource-alpha`).
*   **The Sovereign Evaluator (Local):** A localized, air-gapped LLM (e.g., Falcon-180B or Llama-3-SDAIA) that validates the Foreign Reasoner’s natural language logic against local **Policy-as-Code (PaC)** and real-time telemetry.
*   **The Kedge:** The final gatekeeper that maps synthetic tokens back to real assets and executes the sanctioned mutation with a one-time Execution Identity (RFC-0003).

---

## 4. The Handshake Protocol (The "Justification" Flow)

To maintain system integrity, the protocol follows four mandatory steps:

1.  **Context Masking:** The Proxy strips PII and infrastructure-specific metadata from the prompt, replacing them with abstracted handles before transmission to the Reasoner.
2.  **Intent Generation:** The Reasoner returns a proposed action with a **Justification String** (e.g., *"I am scaling this service because latency exceeded 200ms"*), referencing only the abstracted handles.
3.  **Sovereign Validation:** The Sovereign Evaluator checks the Justification against the **National Security Policy (NSP)**. 
    *   *Constraint:* Does the justification match the internal telemetry? (e.g., Is latency actually >200ms?)
    *   *Constraint:* Is the proposed mutation within the permitted "Blast Radius" for South-West Riyadh Sector?
4.  **Evidence Binding:** The original foreign justification and the local evaluation are cryptographically linked in the **Verifiable Action Record (VAR)**.

---

## 5. Security Properties
### 5.1 Zero-Knowledge Mutations
The Foreign Reasoner never learns the true identity or location of the resources it is manipulating. It operates on a "Logical Shadow" of the infrastructure, preventing the aggregation of intelligence by external providers.

### 5.2 Mandatory Justification
No command is executed unless accompanied by a high-fidelity natural language justification that the Sovereign Evaluator can parse and verify against ground-truth telemetry.

---

## 6. KSA-Specific Alignment (Vision 2030)
This pattern is specifically designed to satisfy the **SDAIA National Data Management Office (NDMO)** and **National Cybersecurity Authority (NCA)** requirements for:
*   **Data Residency:** Zero sensitive system context leaves the Kingdom.
*   **Autonomous Oversight:** Final "Kill-Switch" authority and reasoning validation reside on local hardware (e.g., within Saudi-based Cloud Regions).
*   **Algorithmic Accountability:** Every decision is backed by a local "Sovereign" audit trail.

---

## 7. Implementation Reference: Verifiable Action Record (VAR)
To ensure absolute accountability, the system utilizes the **Verifiable Action Record (VAR)** data structure as formally specified in [RFC-0004](./0004-replay-simulation-api.md). This structure binds foreign intent to local execution through the Evidence Chain (RFC-0002).

The VAR provides the cryptographic anchor required to maintain sovereignty while leveraging external reasoning capabilities.

---

## 8. Governance Reference
```yaml
# Example Sovereign Proxy Policy (OpenKedge-style)
sovereign_boundary:
  region: "ksa-west-1" # NEOM Data Center
  evaluator: "falcon-180b-ksa"
  masking_level: "strict" # No PII, No IP, No Resource Labels
  policy_rules:
    - allow_mutation: true
      condition: "justification.matches_telemetry(latency_threshold)"
      max_blast_radius: "subnet-level"
      geographic_restriction: "inside-ksa"
```
