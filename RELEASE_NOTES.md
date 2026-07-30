# Release Notes

## v1.1.0 - Layered Architecture Rebuild

**Release Date:** 2026-07-30

### Summary

v1.1.0 is a ground-up architectural rebuild of OpenEmpiric's core memory module. The monolithic `engine.py` (1,640+ lines) and tangled service layer have been restructured into a clean three-layer architecture with strict unidirectional dependencies and protocol contracts between layers.

The external API is fully preserved — all MCP tool names, parameters, and return shapes are unchanged. No action is needed for existing users.

---

### Breaking Changes

**None.** All existing MCP tools, CLI commands, and `.oem/` data formats are fully compatible.

---

### New Features

- **Three-layer architecture** — Storage, Computation, and API layers with protocol contracts
- **Event-sourced snapshots** — `events.jsonl` is the sole source of truth; the concept registry is a derived materialized view rebuilt on every `session_end` via `rebuild_registry()`
- **Export/Import** — Transfer memory between machines via `.tar.gz` archives
  - `knowledge_export` MCP tool + `engine.export_memory()`
  - `knowledge_import` MCP tool + `engine.import_memory()`
  - Event_id dedup on import, alias-merge for concept conflicts
- **Scope filtering on search** — `knowledge_search` supports `scope` parameter (`project`, `user`, `session`)
- **Inline memory** — `knowledge_add_memory` now correctly auto-populates `project` and `session_id` server-side

---

### Bug Fixes

- **`knowledge_add_memory` schema mismatch** — Fixed Pydantic validation error where `project` and `session_id` fields were stripped during event normalization. These are now preserved through the pipeline and auto-populated from the active session.
- **`knowledge_reflect` structured events ignored** — Fixed `knowledge_reflect(events=[...], extraction_mode="structured")` returning `events_written: 0`. The validator now falls back from `summary` to `concept` to `concept_candidates[0]` when the summary field is missing.

---

### Internal Changes

- **Engine.py shrunk** from ~1,640 lines to ~200 lines of thin orchestration
- **Storage layer** (`oem_knowledge/storage/`) — `EventStore`, `RegistryStore`, `ConceptFiles`, `SessionFiles`, `UserStore`
- **Computation layer** (`oem_knowledge/computation/`) — `SnapshotComputation`, `ReflectionComputation`, `IndexingComputation`, `SearchComputation`, `FitnessComputation`, `EvolutionComputation`, `MaterializationComputation`, `PreflightComputation`, `SkillsComputation`
- **Protocol contracts** — `EventStoreProtocol`, `RegistryStoreProtocol`, `ConceptFilesProtocol`, `SnapshotProtocol`, `ReflectionProtocol`, `IndexingProtocol`, `SearchProtocol`, `PreflightProtocol`
- **Public method aliases** — `StateService.load_registry/save_registry/load_events`, `MaterializationService.safe_write_concept_file`
- **TypeScript schemas regenerated** to include new tool Pydantic models

---

### Migration Notes

No action is needed for existing users. All `.oem/` data (events, registry, wiki, vector store) is fully compatible. The new layered architecture is an internal restructuring that does not affect the external API.

To use the new export/import feature:

```bash
# Export memory from project A
oem knowledge_export --project /path/to/project_a --output /tmp/memory.tar.gz

# Import into project B
oem knowledge_import --project /path/to/project_b --input /tmp/memory.tar.gz
```

---

### Test Coverage

- 1,164+ tests pass
- 6 new protocol compliance tests verify implementations satisfy their contracts
- 6 new rebuild_registry tests verify idempotency, event preservation, command log filtering, user scope isolation, empty events, and corrupt line handling
- 4 new export/import tests verify round-trip, dedup, and error handling
- 3 new validation tests cover summary fallback edge cases
