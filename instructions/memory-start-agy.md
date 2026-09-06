# OpenEmpiric Project Memory

When working in a project that contains `.oem`, use OpenEmpiric as the project memory runtime.

Lifecycle:

1. Before planning a non-trivial task, check `.oem/.runtime/preflight_context.md` if it exists. If it contains an OEM Preflight Context with decision `required`, follow it before planning. If stale or absent, call `knowledge_preflight` directly.
2. If `knowledge_preflight` returns `decision="required"`, read and follow the returned OEM context before planning.
3. If `knowledge_preflight` returns `decision="suggest"`, consider the returned context and optionally use `knowledge_search` or `knowledge_source_search`.
4. If `knowledge_preflight` returns `decision="noop"`, proceed normally.
5. Call `knowledge_session_start` when beginning work.
6. Use `knowledge_read` whenever you need orientation, project background, recent context, conventions, or approved skills.
7. Use `knowledge_search` when you have a specific memory query.
8. Use `knowledge_source_search` to locate implementation in indexed project files.
9. Use `knowledge_source_read` to inspect exact code or docs with bounded line ranges.
10. Use `knowledge_reflect` to record important decisions, failures, constraints, risks, and outcomes.
11. Call `knowledge_session_end` before finishing.

Rules:

- `knowledge_preflight` is a manual planning step in this batch; do not assume it runs automatically.
- `knowledge_read` teaches broad project context.
- `knowledge_search` retrieves specific memory.
- `knowledge_source_search` retrieves implementation paths from the separate source corpus.
- `knowledge_source_read` reads exact project files with hard line and character limits.
- Do not use `knowledge_index` as a fallback for failed reflection.
- If `dense_llm_unavailable` is returned, do not repeatedly retry dense reflection. Use structured or marker-based reflection instead, then call session_end, report the warning, and stop.
- Do not treat the source corpus as learned memory.
- Prefer structured events or explicit markers for reflection.
- Do not manually edit `.oem` files.
- If OEM health is degraded, report it and suggest `oem doctor` or `oem recover`.

Canonical workflow routing:

- `knowledge_preflight` decision `required`: follow the returned context before planning; use `knowledge_read` only for broad orientation if needed.
- `knowledge_preflight` decision `suggest`: use the context; call `knowledge_search` for learned memory OR `knowledge_source_search` for implementation evidence only when a specific gap remains.
- `knowledge_preflight` decision `noop`: proceed normally and do not retrieve memory just to confirm.
- Durable fact during work: call `knowledge_add_memory` with concise content and evidence.
- Batch/session learnings at the end: call `knowledge_session_end`; `knowledge_session_commit` is deprecated and is not an agent action.
- Source search/read returns code evidence, not learned memory; do not treat source corpus results as learned memory.