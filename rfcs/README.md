# OpenKedge RFCs

This directory contains Request for Comments (RFCs) for the OpenKedge project. RFCs define protocol-level decisions, architectural standards, system invariants, and data schemas.

---

## 📊 RFC Index

| Number | Title | Status |
|--------|------|--------|
| 0001 | [Intent Governance Protocol (IGP)](./0001-intent-governance-protocol.md) | Draft |
| 0002 | [Event Evidence Chain (EEC)](./0002-event-evidence-chain.md) | Draft |
| 0003 | [Execution Identity (EI)](./0003-execution-identity.md) | Draft |
| 0004 | [Replay & Simulation API (RSA)](./0004-replay-simulation-api.md) | Draft |

---

## 🔄 RFC Lifecycle

RFCs progress through the following standardized stages:

- **Draft** → Initial proposal undergoing community review.
- **Accepted** → Approved by project maintainers for implementation.
- **Implemented** → Fully realized in the OpenKedge codebase.
- **Deprecated** → Replaced by a newer RFC or no longer recommended.

---

## 🧠 Design Principles

- **Normative Focus:** RFCs define *what must be true*, not specific language implementations.
- **IETF-Style Language:** RFCs utilize RFC 2119 normative language (MUST, SHOULD, MAY).
- **Determinism:** Protocols and processes described must be deterministic, testable, and auditable.

---

## 📝 How to Propose an RFC

1. Copy `0000-template.md` to `XXXX-your-feature-name.md`.
2. Assign the next available RFC number.
3. Submit a Pull Request.
4. Engage in discussion and address community feedback.
5. Iterate until consensus is reached and the RFC is Accepted.
