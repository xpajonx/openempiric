# Agent-First Experience Validation Checklist

This document details the manual scenarios to validate the agent-first design of the OpenEmpiric runtime.

---

## Scenario 1: Fresh Installation Initialization

### Action
Run a fresh install and bootstrap an agent session:

```bash
oem run opencode
```

### Verification
- The agent should start without asking to initialize OEM manually.
- The prompt context does not mention initializing or activating OEM.
- The agent treats OEM as active by default.

---

## Scenario 2: Previous Session Context Restoration

### Action
Run a new session following a previous session with documented targets (e.g., in `.oem/session-handoff.md`).

### Verification
- Verify that the agent understands prior work and target topics.
- Verify that the agent does not automatically execute or continue previous work without explicit instructions.

---

## Scenario 3: Memory Context Semantics

### Action
Inspect the agent prompt context.

### Verification
- The context should be framed clearly as historical context.
- The agent should remember decisions, tradeoffs, and failures.
- The agent must not resume work or follow steering directives under the assumption of queued tasks.

---

## Scenario 4: Tool Capability Availability

### Action
During an active session, trigger the developer tooling.

### Verification
- The agent is capable of invoking `knowledge_search` when additional historical context is needed.
- The agent is capable of invoking `knowledge_health_check` to detect stale or duplicate knowledge.
- The agent does not call lifecycle start or end tools.
