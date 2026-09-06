# OpenEmpiric Preflight Adapter Contract

OpenEmpiric preflight is the deterministic planning gate for project memory.

The boundary is simple:

- OEM decides what memory is relevant.
- The adapter decides how to surface it.

## Batch 2 scope

Batch 2 exposes preflight through MCP, CLI, and the in-process engine API.

It does not add automatic prompt hooks or automatic agent interception yet.

## Adapter responsibilities

Adapters should:

- capture the user task text
- call `knowledge_preflight` before non-trivial planning when possible
- inject or display the returned context without changing its meaning
- preserve the normalized result contract
- respect project binding and never bypass it
- avoid any LLM call for preflight

Adapters must not:

- modify preflight decision semantics
- auto-index sources as part of preflight
- mutate OEM memory during preflight
- treat the source corpus as learned memory

## Normalized contract

Adapters should expect the same normalized payload shape from:

- MCP `knowledge_preflight`
- CLI `oem preflight --json`
- `KnowledgeEngine.preflight(...)`

Key fields:

- `status` (`success` or `error`) is the transport-level status
- `decision` (`noop`, `suggest`, `required`, `blocked`, `error`) is the routing-level decision
- `project_root`
- `memory_root`
- `reason`
- `context`
- `warnings`

For expected prerequisite failures, transport status may be `error` while decision remains `blocked`.

## Adapter types

### MCP-only adapter

The agent should call `knowledge_preflight` directly before planning non-trivial tasks.

### Hook-capable adapter

The adapter may call preflight automatically in a future batch, but Batch 2 only standardizes the contract and instructions.

### Runner adapter

A runner may eventually materialize preflight context into `.oem/.runtime/preflight_context.md`, but that behavior is deferred to Batch 3.

### SDK adapter

SDK consumers may call `KnowledgeEngine.preflight(task, ...)` and use the same normalized payload.

## Runtime context file

`.oem/.runtime/preflight_context.md` is reserved for optional future adapter support.

If created in a future batch, it must be:

- generated
- bounded in size
- overwritten as needed
- non-ingestion-eligible
- excluded from reflection evidence and source corpus inputs

## Current instruction rule

### Read-only audit report

`oem preflight --audit-report [--json]` reads the existing
`.oem/preflight/preflight_events.jsonl` stream without running preflight,
writing, or indexing. The report is primary: it ignores a supplied task and
`--no-audit`. JSON output has `status: "success"`,
`operation: "preflight_audit_report"`, `project_root`, `memory_root`, and an
`audit` summary containing `exists`, event and malformed/empty line counts,
sorted decision and rejection-reason maps, rejected-memory totals, timestamp
bounds, and `truncated`.

The ranking summary is a test/evaluation helper, not a replacement for the
normalized preflight contract.

Batch 2 instructions should say:

- before planning non-trivial tasks, call `knowledge_preflight`
- if decision is `required`, follow the returned OEM context before planning
- if decision is `suggest`, consider the returned context and optionally continue with `knowledge_search` or `knowledge_source_search`
- if decision is `noop`, proceed normally

They should not say that preflight is automatic.