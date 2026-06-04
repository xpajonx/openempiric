# OpenEmpiric Adapter Specification

This specification defines the integration boundaries and API contracts for building OpenEmpiric (OEM) adapters for various AI agents, IDEs, and runtimes.

## Ownership Boundaries

A successful integration divides responsibilities cleanly between the shared OpenEmpiric runtime core and the agent-specific adapter wrapper:

| What OpenEmpiric (OEM) Runtime Owns | What Adapters Own |
|---|---|
| Core SQLite, Vector DB, and File layout in `.oem/` | Orchestration wrappers (e.g. `oem run`, process interceptors) |
| Semantic & BM25 hybrid search execution | Passing conversation history and transcripts to the compiler |
| State transition logic (Promotions / Demotions) | Exposing MCP tool routes to the LLM agent |
| Markdown materialization (`.oem/wiki/*.md`) | Parsing/injecting handoff summaries into agent system prompts |
| File indexing & Graph-based reciprocal link building | Capturing telemetry (tokens, execution costs, duration) |
| Path-traversal guards & Truncation safety limits | Custom agent-specific tool execution and event triggers |

---

## Capabilities Specification

An adapter implements capabilities to interface with the OEM runtime. The following YAML specification defines required and optional capabilities:

```yaml
version: "0.9.5"
capabilities:
  required:
    context_injection:
      description: "Injects relevant validated wiki knowledge and active session context into the agent's context window."
      trigger: "Before session start"
      mechanism:
        - Read `.oem/session-handoff.md` and prepended concept content
        - Include system prompt context wrapper
    session_start:
      description: "Registers session states, tracks active goals, open issues, and initializes telemetry."
      command: "oem session-start"
      outputs:
        session_id: "Unique session identifier"
        active_goals: "List of targets loaded from handoff"
        recent_discoveries: "Contextual concepts pre-loaded from search"
    session_commit:
      description: "Closes the current session, triggers reflection, materializes markdown concepts, updates graph, and logs outcomes."
      command: "oem session-end --chat <transcript>"
      inputs:
        conversation_text: "Raw string of session conversation"
        outcome: "Success | Failure | Abandoned"
        reason: "Optional context/reasons for failure"

  optional:
    todo_access:
      description: "Provides tools for reading, writing, and advancing task todo items during execution."
      tools:
        - oem_todo_read
        - oem_todo_write
        - oem_todo_advance
    conversation_history:
      description: "Captures full prompt histories and logs incremental updates during runtime."
      file_path: ".oem/state/usage_log.jsonl"
    tool_events:
      description: "Hooks fired upon agent tool invocations to feed active telemetry (cost, tokens, duration) back into the runtime."
      metrics:
        - input_tokens
        - output_tokens
        - cost_usd
        - run_duration_s
```
