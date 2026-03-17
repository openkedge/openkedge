# OpenKedge

**The open protocol that kedges passive cloud infrastructure into self-protected agentic systems.**

### What is "kedge"?
A **kedge** is a small anchor sailors use to carefully reposition a massive ship when the main engines can’t move it.  
OpenKedge does exactly the same for cloud infrastructure: it takes passive, API-only systems and safely repositions them into **self-protected, agentic systems** that actively care for your customer resources and collaborate safely with DevOps agents.

### Vision
Infrastructure is no longer passive.  
With AI agents and automation, a single mutation can now impact thousands of resources in seconds.  
Yet most cloud systems still rely on manual reviews, coarse IAM, and static policies.

OpenKedge changes that.

It turns every passive API-based system into a **self-protected agentic system** that:
- Pulls live documentation context for any mutation
- Understands how resources are actually used in your stack
- Intercepts changes before they reach the control plane
- Works safely with any read-only DevOps agent

### Core Protocol
```ascii
Mutation → OpenKedge Interceptor
           │
           ▼
   AI Agent Context Layer (docs + stack usage)
           │
           ▼
   Self-Protected Agentic Decision
   ┌─────────────┴─────────────┐
   ▼                           ▼
ALLOW + Care for resources    BLOCK

### Install

```
go install github.com/openkedge/openkedge/cmd/kedge@latest
```
# or

brew install openkedge   # (we'll add the formula later)