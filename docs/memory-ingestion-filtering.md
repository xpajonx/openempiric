# Memory ingestion filtering

R1 keeps operational runtime output out of durable OEM memory without changing the event schema or low-level event-store protocol.

## Measurable outcome

Each reflection reports `noise_events_filtered` and `telemetry_events_skipped` in its result and explainability data. Session-end metrics accumulate both counters under `metrics.json` -> `reflection`. The deterministic gate is:

```text
uv run pytest -q packages/oem-knowledge/tests/test_ingestion_filtering.py
```

## Retained events

- Agent-authored failures, including failures whose evidence quotes a failed command.
- Agent-authored decisions, outcomes, validations, hypotheses, experiments, risks, and deprecations.
- Ordinary project observations that are not operational output.
- Non-command genuine failures from runtime sources.

A raw command failure from the runtime hook is operational evidence, not a durable failure memory. It is dropped unless an agent records the durable failure separately.

## Dropped events

- OpenCode runtime-hook observations and tool results.
- Raw command and tool output, including successful commands and command failures.
- Explicit `telemetry`, `command_log`, and `search_log` events.
- Events marked `ingestion_eligible: false`.
- Generated OEM source types and paths under `.oem/sessions`, `.oem/session_reports`, `.oem/reports`, `.oem/.runtime`, and `.oem/state`.
- Recursive session-output markers such as `Session End / Commit Complete`, `Extracted Knowledge Events:`, and `Graph & Index Updates:` when emitted by runtime sources.

## Filtering stages

1. Reflection filters structured and pending events before they become canonical durable events. Telemetry is counted and skipped before event creation. A final canonical guard covers marker, file, and local extraction output.
2. Legacy session-report materialization applies the same policy before creating concepts.
3. Registry rebuild applies the policy to older raw events still present in `events.jsonl`.
4. User-event indexing applies the policy before adding user-scoped records to search.

`append_event` and `append_events` remain unchanged public protocol methods. This preserves direct callers and crash-recovery behavior.

## Legacy state

R1 does not delete old event or session files. Existing raw events are suppressed during rebuild, and old session reports are suppressed during materialization. Derived vector/index state may require an explicit rebuild or re-index to remove chunks created before R1.
