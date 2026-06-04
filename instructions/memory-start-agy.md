## openempiric Memory Protocol for Antigravity (agy)

### 1. Antigravity Validation (Mandatory Session Start)
At the start of every session, you **MUST** ensure the `openempiric` context is loaded:
- Use your `run_command` tool to query/read `.oem/session-handoff.md` and active concepts.
- Alternatively, if context is injected by the runner/adapter, read the system prompt/instructions.

### 2. Session Start & Context Loading
- Run `oem session-start` via command line to restore state and check active goals, blockers, and discoveries.
- Use `oem search "<query>"` to locate specific concept definitions and evidence.
- These definitions serve as the ground truth of design rules, validation constraints, and architectural decisions.

### 3. Session End & Organization
- When the task is complete, or when finishing, you **MUST** trigger the reflection pipeline.
- Run `oem session-end --chat "<transcript_summary_or_raw>"` or simply exit so the orchestrator runs the commit pipeline.
