---
name: remember
description: Routes user requests containing "remember", "recall", "checkpoint", "session", "todo", or "where were we" to the correct OpenEmpiric (OEM) MCP tool. Use when the user wants to persist, retrieve, or contextualize knowledge from project memory.
metadata:
  trigger: remember recall checkpoint session todo "where were we" "what were we doing"
---

<!-- generated_by: openempiric; source_type: oem_opencode_skill; version: 1.0.0 -->

# Remember

This skill maps natural-language "remember" requests to the correct OpenEmpiric OEM MCP tool call.

OEM tools: `knowledge_preflight`, `knowledge_session_start`, `knowledge_read`, `knowledge_search`, `knowledge_source_search`, `knowledge_source_read`, `knowledge_reflect`, `knowledge_session_end`, `knowledge_health_check`, `oem_checkpoint_create`, `oem_checkpoint_list`, `oem_checkpoint_restore`, `oem_todo_read`, `oem_todo_write`, `oem_todo_advance`, `knowledge_get_events`.

## Routing Table

Match the user's phrasing to the action below.

| When user says... | Do this |
|---|---|
| "remember" (start of session / standalone) | `knowledge_session_start` + `knowledge_read(scope="recent")` + `knowledge_read(scope="project")` + `oem_checkpoint_list` — surface the latest checkpoint, active work item, recent successes and failures. |
| "remember where we left off" | `oem_checkpoint_list` (latest checkpoint + active_work_item) + `knowledge_read(scope="recent")` + `knowledge_read(scope="project")` |
| "remember what we tried" / "remember failures" | `knowledge_search("failure")` + `knowledge_read(scope="recent")` — filter to failures. |
| "remember decisions about X" | `knowledge_search("<X>")` + `oem_todo_read` — search both concept memory and open tasks. |
| "remember the workflow for X" | `knowledge_search("<X>")` scoped to workflow/convention/decision memory. Also check `directives/` if it exists. |
| "remember this" + fact | `knowledge_reflect` with structured events `[{event_type:"decision"|"failure"|"observation"|"workaround", summary:"..."}]` to persist the fact. |
| "remember this for next time" + observation | `knowledge_reflect` with events + `oem_checkpoint_create` to snapshot the current working set. |
| "restore checkpoint <id>" | `oem_checkpoint_restore(target="<id>")` where `<id>` is the name_id from checkpoint list. |
| "list checkpoints" | `oem_checkpoint_list` |
| "what's my todo" / "what's next" | `oem_todo_read` |
| "add todo" + item | `oem_todo_write` to append the item. |
| "mark todo done" / "advance" | `oem_todo_advance(item_id="<uuid>")` |
| "remember" (end of session / finishing) | `knowledge_reflect` (summarize decisions+outcomes) + `oem_checkpoint_create` + `knowledge_session_end` |

## When the OEM MCP is unreachable

Fall back to the OEM CLI:
```bash
oem search "<query>" --project .
oem session start --project .
oem session end --project .
oem checkpoint --project .
```

## Do NOT

- Call `knowledge_index` as a fallback for failed reflection — use `knowledge_reflect` with structured events or explicit markers instead.
- Manually edit `.oem/` files.
- Treat the source corpus as learned memory — source_search finds implementation paths, not concepts.
- Call OEM tools when the user is not talking about memory/context/checkpoints.

## dream operator routines

The `dream` subagent activates this skill and operates in ONE mode per delegation. Never run both modes in one delegation.

### dream_start (used only when delegated by `plan`)

Activate this skill, then:

1. Run `knowledge_preflight` for the current task/project.
2. If preflight returns `decision="required"`, read and follow the returned OEM context before planning.
3. Call `knowledge_session_start`.
4. Call `knowledge_read(scope="recent")` and `knowledge_read(scope="project")`.
5. Call `oem_checkpoint_list` and `oem_todo_read` for active context.
6. Report a concise context packet to plan: active task, recent decisions/failures, constraints, prior conventions, open todos.

Do NOT run `knowledge_reflect`, `oem_checkpoint_create`, or `knowledge_session_end` in this mode. Do NOT duplicate a session start if plan states one already completed.

### dream_end (used only when delegated by `orchestrator`)

Activate this skill, then:

1. Receive structured decisions, observations, failures, constraints, and outcomes from orchestrator.
2. Call `knowledge_reflect` with structured events built from the orchestrator's payload.
3. Call `oem_checkpoint_create` to snapshot the working set.
4. Call `knowledge_session_end`.
5. Report completion status or degradation to orchestrator.

Do NOT run `knowledge_preflight`, `knowledge_session_start`, `knowledge_read`, or `oem_checkpoint_list` in this mode.

### Fallback

MCP is the only normal write path. Do NOT manually edit `.oem/` files. If MCP is unreachable, use the CLI fallback documented in the routing table above and report the degradation.
