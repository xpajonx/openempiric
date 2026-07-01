# P0 Directive Trigger Hardening Report

## Root cause
- `match_directives()` treated generic trigger overlap as enough to score critical directives highly.
- `router.py` converted critical directive matches to `required` unless the match was only a generic full-title match.
- Generic words such as `current`, `project`, `content`, and `work` could route unrelated directives into required preflight.
- Directive output did not expose enough diagnostics to explain whether a directive was semantic, generic, weak, or forcing.

## Generic token guardrail
- Added a full generic-token guardrail in `oem_knowledge/instructions/matcher.py`.
- Generic-only overlap is now diagnostic and non-forcing.
- Generic tokens are not globally banned: meaningful multi-token domain phrases can still be semantic.
- Added a small generic phrase denylist for unsafe continuation phrases such as `current project`, while preserving domain phrases such as `OEM health`.

## Directive match classes
- Directive matches now include:
  - `match_class`
  - `matched_tokens`
  - `semantic_tokens`
  - `generic_tokens`
  - `can_force_required`
- Supported classes:
  - `semantic_directive_match`
  - `generic_lexical_match`
  - `global_always_on_directive`
  - `weak_directive_match`
- `matched_directives` remains a diagnostic list and includes non-forcing matches.

## Critical directive relevance rules
- Critical directives now require two gates to force required:
  - priority/criticality gate
  - semantic relevance gate
- `priority=critical` alone no longer bypasses relevance.
- `always_on: true` and `scope: global` can force required without task-specific semantic overlap.
- Added idempotent `always_on` ledger migration with older rows defaulting to false.

## Preflight decision changes
- `router.py` now separates all diagnostic `matched_directives` from internal `forcing_directives`.
- Only directives with `can_force_required` can affect the decision cascade.
- Generic directive matches cannot override stronger primary reasons such as:
  - `active_work_resolved`
  - `target_file_decision_matched`
  - `active_work_conflict`
- Normalized preflight output preserves active-work reason codes for suggest/required results.

## Tests added
- `test_directive_generic_token_overlap_cannot_force_required`
- `test_directive_current_content_machine_contract_not_required_from_current_token_only`
- `test_directive_critical_requires_semantic_relevance`
- `test_directive_always_on_can_force_required`
- `test_directive_semantic_match_can_force_required`
- `test_directive_oem_health_phrase_can_match_oem_health_directive`
- `test_instruction_ledger_adds_always_on_column_idempotently`
- `test_existing_directives_default_always_on_false`
- `test_scope_global_implies_always_on`
- `test_critical_directive_without_semantic_overlap_cannot_force_required`
- `test_matched_directives_include_nonforcing_diagnostics`
- `test_preflight_review_current_health_not_required_from_current_directive`
- `test_preflight_langgraph_storm_prompt_can_match_langgraph_directive`
- `test_preflight_generic_current_project_reason_not_overridden_by_directive`
- `test_preflight_output_includes_directive_match_class`
- `test_preflight_review_current_oem_health_does_not_trigger_unrelated_required_directives`

## Test results
- Targeted directive/preflight: `60 passed, 11 warnings`.
- Instruction ledger: `23 passed, 22 warnings`.
- Broader relevant suite: `95 passed, 1 warning`.
- Broad suite: `768 passed, 1 skipped, 5 deselected, 723 warnings`.
- Requested `packages/oem-knowledge/tests/test_mcp_regression.py` does not exist in this checkout; used current MCP tests:
  - `test_mcp_tool_snapshot.py`
  - `test_mcp_session_tools.py`
  - `test_integrations_opencode_mcp.py`

## Manual verification
- `oem preflight --no-audit --json "review current OEM health"`
  - `decision`: `noop`
  - `reason`: `no_relevant_oem_context`
  - no unrelated required directive.
- `oem preflight --no-audit --json "continue working on the current project"`
  - `decision`: `suggest`
  - `reason`: `active_work_resolved`
  - generic directives did not override active-work reasoning.
- `oem preflight --no-audit --json "continue LangGraph STORM research pipeline implementation"`
  - local instruction ledger has no active LangGraph directive, so manual run cannot demonstrate a directive match in this checkout.
  - regression test `test_preflight_langgraph_storm_prompt_can_match_langgraph_directive` covers the directive-present case.

## Remaining risks
- Domain phrase matching is intentionally conservative and uses shared token phrases; future phrase false positives may need another small denylist entry.
- Existing directive ledgers require `ensure_schema()` to run once before `always_on` is available; the migration is idempotent and automatic through `get_db_connection()`.
- Warnings remain from existing datetime/pathspec deprecations unrelated to this change.
