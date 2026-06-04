# OpenEmpiric Adapter Architecture

This document describes the runtime architecture of the OpenEmpiric (OEM) knowledge engine, its internal file storage formats, and the sandboxing security limits.

## Architectural Layering

```text
┌────────────────────────────────────────────────────────┐
│                     Adapter Layer                      │
│     (Wrappers, TUI, OpenCode Plugin, MCP Servers)      │
├────────────────────────────────────────────────────────┤
│                  OpenEmpiric Engine                    │
│    (Knowledge Engine, State Service, Search Service)   │
├────────────────────────────────────────────────────────┤
│                 Secure File System (SFS)               │
│        (Path-traversal and Truncation Guard)           │
├────────────────────────────────────────────────────────┤
│                  Storage Directory                     │
│               (.oem/ State & Database)                 │
└────────────────────────────────────────────────────────┘
```

### OEM Core Ownership
- **Schema & Database integrity**: Validating json schemas and SQLite instances.
- **Safety checks**: Restricting file write boundaries and preventing accidental deletions/truncations.
- **Evaluation**: Deciding promotion to validated/canonical or demotion to needs_review.

### Adapter Ownership
- **User input formatting**: Collecting transcripts and passing them into the commit logic.
- **TUI & CLI rendering**: Printing outputs and warnings to the developer.

---

## Directory & File Layout

All local project knowledge is self-contained within the `.oem/` directory at the project root:

```text
.oem/
├── concept_registry.json     ← Active concept records, aliases, history and stats
├── events.jsonl              ← Immutable event-sourced learning logs
├── session-handoff.md        ← Current handoff context for context injection
├── wiki/                     ← Materialized concept wiki markdown files
│   ├── concept_001.md
│   ├── index.md              ← Automatically maintained wiki directory
│   └── log.md                ← Audit log of engine modifications
└── state/
    ├── outcomes.jsonl        ← Append-only session outcomes and correlation stats
    └── todos.json            ← Persistent session tasks
```

---

## Storage Schema Reference

### 1. Event Log (`events.jsonl`)
Immutable event source entries recorded upon session commitment:
```json
{
  "event_id": "evt_abc123",
  "timestamp": "2026-06-04T00:00:00Z",
  "project": "my-project",
  "session_id": "session_17000000",
  "event_type": "observation",
  "concept_candidates": ["ai-safety"],
  "summary": "Observed validation constraints in prompt template.",
  "evidence": "Log trace showing template overflow.",
  "confidence": 3,
  "source": "chat",
  "schema_version": 1
}
```

### 2. Outcomes Log (`state/outcomes.jsonl`)
Append-only telemetry outcome statistics used for calculating fitness:
```json
{
  "schema_version": 1,
  "session_id": "session_17000000",
  "outcome": "success",
  "referenced_concepts": ["concept_001"],
  "retrieved_concepts": ["concept_001", "concept_002"],
  "reason": "Test pipeline completed successfully.",
  "metrics": {
    "concepts_injected": 2,
    "concepts_referenced": 1,
    "search_count": 4
  },
  "timestamp": "2026-06-04T00:00:05Z"
}
```

### 3. Concept Registry (`concept_registry.json`)
Tracks the current status, statistics, and audit trail of each concept:
```json
{
  "concept_001": {
    "concept_id": "concept_001",
    "canonical_name": "ai-safety",
    "aliases": ["AI Safety"],
    "status": "validated",
    "confidence": 4,
    "evidence_count": 3,
    "sessions": ["session_17000000"],
    "created_at": 1780000000.0,
    "updated_at": 1780000005.0,
    "relationships": [],
    "promotion_history": [
      {
        "from_status": "candidate",
        "to_status": "validated",
        "trigger_event": "validation",
        "session_id": "session_17000000",
        "reason": "Standard Validation: Evidence count (3)",
        "timestamp": "2026-06-04T00:00:05Z"
      }
    ]
  }
}
```

---

## Safety & Security Sanity Guards

OpenEmpiric runs a **Secure File System (SFS)** wrapper to protect the host workspace during agent edits:

1. **Path-Traversal Protection**:
   - SFS validates that every target file path resolves strictly within the configured project/repository folder boundaries.
   - Any attempt to read or write files outside the workspace root raises a `PermissionError`.

2. **Truncation Prevention Guard**:
   - During file updates, the SFS compares the character length of the proposed content with the existing content.
   - If the new content is less than **50%** of the original size and the action is not explicitly forced (via `force_allow_truncation=True`), it raises a `ValueError` to prevent accidental file deletion or context loss.
