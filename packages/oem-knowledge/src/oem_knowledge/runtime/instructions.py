from __future__ import annotations

OEM_MEMORY_INSTRUCTIONS = """# OpenEmpiric Project Memory

When working in a project that contains `.oem`, use OpenEmpiric as the project memory runtime.

Lifecycle:

1. Call `knowledge_session_start` when beginning work.
2. Use `knowledge_read` whenever you need orientation, project background, recent context, conventions, or approved skills.
3. Use `knowledge_search` when you have a specific memory query.
4. Use `knowledge_reflect` to record important decisions, failures, constraints, risks, and outcomes.
5. Call `knowledge_session_end` before finishing.

Rules:

- `knowledge_read` teaches broad project context.
- `knowledge_search` retrieves specific memory.
- Prefer structured events or explicit markers for reflection.
- Do not manually edit `.oem` files.
- If OEM health is degraded, report it and suggest `oem doctor` or `oem recover`."""
