from oem_knowledge.memory_ranking import classify_memory_type
from oem_knowledge.preflight.scoring import _detect_memory_type


def test_detect_memory_type_first_line_decision_still_works():
    assert classify_memory_type("Decision: open project alpha") == "decision"


def test_detect_memory_type_first_line_failure_still_works():
    assert classify_memory_type("Failure: do not run tests") == "failure"


def test_detect_memory_type_first_line_outcome_still_works():
    assert classify_memory_type("Outcome: project complete") == "outcome"


def test_detect_memory_type_markdown_bold_decision_bullet():
    assert classify_memory_type("- **Decision**: Essay_ID.md is open") == "decision"


def test_detect_memory_type_markdown_bold_failure_bullet():
    assert classify_memory_type("* **Failure**: Do not modify Indonesian essays") == "failure"


def test_detect_memory_type_markdown_bold_outcome_bullet():
    assert classify_memory_type("- **Outcome**: Successful materialization") == "outcome"


def test_detect_memory_type_learnings_section_decision():
    content = "## Learnings\n\n- Decision: Essay_ID.md is open."
    assert classify_memory_type(content) == "decision"


def test_detect_memory_type_learnings_section_failure():
    content = "## Learnings\n\n- Failure: Do not write."
    assert classify_memory_type(content) == "failure"


def test_detect_memory_type_learnings_section_outcome():
    content = "## Learnings\n\n- Outcome: Clean code."
    assert classify_memory_type(content) == "outcome"


def test_detect_memory_type_multiple_labels_prioritizes_failure_over_decision():
    content = "- **Decision**: Use main branch.\n- **Failure**: Avoid using draft configs."
    assert classify_memory_type(content) == "failure"


def test_detect_memory_type_ignores_decision_word_in_plain_prose():
    assert classify_memory_type("The decision tree algorithm is implemented in this file.") == "observation"


def test_detect_memory_type_ignores_markers_inside_code_fence():
    content = "Some context\n```text\nDecision: this should be ignored\n```\nNormal text"
    assert classify_memory_type(content) == "observation"


def test_preflight_scoring_uses_materialized_wiki_memory_type_detection():
    # Verify that preflight's internal _detect_memory_type routes to our classifier
    snippet = "- **Decision**: 2_Essay/expertise-debt/Essay_ID.md is the open project."
    assert _detect_memory_type("Some Title", snippet) == "decision"


def test_detect_memory_type_supports_indented_bold_bullets():
    content = "  - **Decision**: indent works\n    - **Failure**: nested failure"
    assert classify_memory_type(content) == "failure"


def test_detect_memory_type_ignores_markers_inside_language_code_fence():
    content = "Outside\n```json\n{\"Failure\": \"example\"}\n```\nOutside"
    assert classify_memory_type(content) == "observation"


def test_detect_memory_type_handles_unclosed_code_fence_conservatively():
    content = "Outside\n```text\nDecision: unclosed\n"
    assert classify_memory_type(content) == "decision"


def test_real_concept008_style_learnings_chunk_prioritizes_failure():
    content = """## Learnings

- **Decision**: 2_Essay/expertise-debt/Essay_ID.md is the open project.
- **Failure**: For Indonesian essays, do not modify the file unless explicitly asked.
- **Observation**: The essay uses a reflective tone."""
    assert classify_memory_type(content) == "failure"


def test_technical_handoff_signals_preserved_when_decision_marker_present():
    content = """- **Decision**: Use local database.
Handoff: pass source_ids explicitly chat.ask workaround"""
    # The primary classification is decision
    assert classify_memory_type(content) == "decision"
