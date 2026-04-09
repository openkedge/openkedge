# OpenKedge

> Governing Agentic Mutation with Execution-Bound Safety and Evidence Chains

OpenKedge is a protocol for safely operating AI agents over real-world systems.

It replaces direct API execution with:
- **intent-governed mutation**
- **execution-bounded contracts**
- **verifiable decision lineage (IEEC)**

---

## 🚨 Why OpenKedge?

Modern infrastructure is built on assumptions that no longer hold:

- callers are deterministic  
- actions are correct  
- context is complete  

This breaks in the era of AI agents.

Today’s model:

```text
Agent → API → Immediate Mutation
````

Leads to:

* unsafe deletions (e.g., terminating live infrastructure)
* conflicting multi-agent updates
* context-blind automation
* cascading failures

👉 The issue is not just the model —
it’s the **mutation model**.

---

## 🔐 The OpenKedge Model

OpenKedge introduces a governed mutation pipeline:

```text
Intent → Context → Policy → Contract → Execution → Evidence Chain
```

---

### 1. Intent-Governed Mutation

Agents do not execute APIs directly.

They submit:

> what they want to achieve

The system evaluates:

* system-wide context
* dependencies
* policy constraints
* multi-agent conflicts

---

### 2. Execution-Bound Safety

Approved intents are compiled into **execution contracts**:

* allowed actions
* scoped resources
* strict time bounds

Execution is enforced via **ephemeral identities** (e.g., AWS STS).

Even if an agent hallucinates, execution is physically constrained.

---

### 3. Intent-to-Execution Evidence Chain (IEEC)

Every mutation produces a **verifiable lineage**:

```text
Intent → Context → Policy → Contract → Execution → Outcome
```

Properties:

* cryptographically linked
* temporally ordered
* fully reconstructable

This enables:

* auditability
* explainability
* forensic debugging

👉 Not just *what happened*, but *why it was allowed*

---

## 🧠 Core Insight

> Mutation should not be executed. Mutation should be governed.

---

## 📄 Paper

Read the full paper:

* [openkedge.io/paper](https://www.openkedge.io/paper)

---

## 🏗 Architecture

![OpenKedge Architecture](./docs/diagram.svg)

Key guarantees:

* no direct agent → API execution path
* all mutations pass governance
* execution is strictly bounded
* every step is recorded in IEEC

---

## 🧪 Example: Safe Instance Termination

### ❌ Traditional

```text
ec2:TerminateInstances
```

→ Instance deleted (even if still in use)

---

### ✅ OpenKedge

1. Agent submits:

```text
Intent: "remove unused compute resource"
```

2. System evaluates:

* dependency graph
* traffic signals
* policy constraints

3. If approved:

* generates execution contract
* issues scoped identity

4. Execution:

* limited to specific resource
* within time bounds

5. IEEC records full lineage

---

## 🚀 Getting Started

```bash
git clone https://github.com/openkedge/openkedge
cd openkedge
```

> ⚠️ Early-stage reference implementation

---

## 🧩 Use Cases

* AI-driven DevOps automation
* multi-agent systems
* cloud infrastructure safety
* workflow engines with AI integration
* autonomous system governance

---

## 🔬 Key Contributions

* intent-governed mutation protocol
* execution-bound safety via contracts
* IEEC: verifiable mutation lineage

---

## 🛣 Roadmap

* [ ] Core protocol (v0.1)
* [ ] AWS adapter (STS + policy integration)
* [ ] Policy engine plugins (Cedar / OPA)
* [ ] Multi-agent simulation framework
* [ ] IEEC visualization UI
* [ ] Production SDK

---

## 🤝 Contributing

We welcome contributions across:

* policy engines
* cloud adapters
* agent integrations
* visualization tools

---

## 📜 License

MIT (or TBD)

---

## 🌍 Vision

As AI agents become primary operators of infrastructure,
the correctness of individual agents becomes secondary to the correctness of the system governing them.

OpenKedge provides that foundation.

---

## ⭐ If this resonates

Star the repo and follow the project.

This is just the beginning.
