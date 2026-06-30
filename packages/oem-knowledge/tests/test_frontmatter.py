"""
Tests for canonical frontmatter parser.
"""

import pytest
from oem_knowledge.markdown.frontmatter import (
    ParsedMarkdown,
    parse_frontmatter,
    safe_yaml_load,
)


FULL_CONCEPT_WITH_BODY_SEPARATORS = """\
---
concept_id: concept_test
status: validated
---

# Learnings

- Command output:
  echo "---"

---

Another markdown separator in body.

```text
---
Full extracted text here...
---
```"""


# ---------------------------------------------------------------------------
# Core parsing rules
# ---------------------------------------------------------------------------


def test_frontmatter_parser_only_uses_line_one_opening_delimiter():
    """Body content that looks like frontmatter must not trigger re-parsing."""
    non_first_line_dash = """\
# Some heading
---
fake_key: fake_value
---

Real body content.
"""
    result = parse_frontmatter(non_first_line_dash)
    assert result.metadata == {}
    assert "---" in result.body
    assert result.warnings == []


def test_frontmatter_parser_ignores_body_horizontal_rules():
    """`---` horizontal rules in body are preserved as content."""
    result = parse_frontmatter(FULL_CONCEPT_WITH_BODY_SEPARATORS)
    assert result.metadata["concept_id"] == "concept_test"
    assert result.metadata["status"] == "validated"
    assert result.warnings == []
    # Body must contain the horizontal-rule separator
    assert "Another markdown separator in body." in result.body
    # Body must contain the code-fence triple-dash
    assert "Full extracted text here..." in result.body


def test_frontmatter_parser_ignores_echo_triple_dash_in_body():
    """`echo "---"` inside a code block is not treated as frontmatter delimiter."""
    result = parse_frontmatter(FULL_CONCEPT_WITH_BODY_SEPARATORS)
    assert 'echo "---"' in result.body


def test_frontmatter_parser_returns_warning_for_true_unclosed_frontmatter():
    """No closing `---` after the opening line → frontmatter_block_not_closed."""
    text = """---
concept_id: concept_test
status: validated

# Body without closing delimiter.
"""
    result = parse_frontmatter(text, source_path="/fake/concept_001.md")
    assert result.metadata == {}
    assert len(result.warnings) == 1
    assert result.warnings[0]["reason"] == "frontmatter_block_not_closed"
    # Body is the full raw text since we can't isolate frontmatter
    assert "Body without closing delimiter" in result.body


def test_frontmatter_parser_closes_on_ellipsis_delimiter():
    """`...` is a valid YAML frontmatter closing delimiter."""
    text = """---
concept_id: concept_test
...

# Body
Actual content.
"""
    result = parse_frontmatter(text)
    assert result.metadata["concept_id"] == "concept_test"
    assert "Actual content." in result.body
    assert result.warnings == []


def test_frontmatter_parser_handles_empty_document():
    result = parse_frontmatter("")
    assert result.metadata == {}
    assert result.body == ""
    assert result.warnings == []


def test_frontmatter_parser_handles_no_frontmatter_document():
    text = "# Just a heading\n\nBody text."
    result = parse_frontmatter(text)
    assert result.metadata == {}
    assert result.body == text
    assert result.warnings == []


# ---------------------------------------------------------------------------
# YAML-specific
# ---------------------------------------------------------------------------


def test_frontmatter_invalid_yaml_returns_warning():
    """Malformed YAML produces frontmatter_yaml_parse_error + manual fallback metadata."""
    text = """---
concept_id: concept_test
\tbad_indent: yes
status: validated
---

Body.
"""
    result = parse_frontmatter(text, source_path="/fake/concept_bad.md")
    assert len(result.warnings) >= 1
    reasons = [w["reason"] for w in result.warnings]
    assert "frontmatter_yaml_parse_error" in reasons
    # Fallback still extracts key:value pairs
    assert result.metadata.get("concept_id") == "concept_test"
    assert result.metadata.get("status") == "validated"


def test_frontmatter_non_mapping_yaml_returns_warning():
    """YAML that parses to a list/string instead of dict → frontmatter_not_mapping."""
    text = """---
- item1
- item2
---

Body.
"""
    result = parse_frontmatter(text, source_path="/fake/concept_list.md")
    assert len(result.warnings) >= 1
    reasons = [w["reason"] for w in result.warnings]
    assert "frontmatter_not_mapping" in reasons


# ---------------------------------------------------------------------------
# Body preservation
# ---------------------------------------------------------------------------


def test_parser_body_output_retains_body_content():
    """Parser body preserves all content after frontmatter — used for reading, not rewriting."""
    result = parse_frontmatter(FULL_CONCEPT_WITH_BODY_SEPARATORS)
    assert "# Learnings" in result.body
    assert "Another markdown separator in body." in result.body
    assert "```text" in result.body
    assert "Full extracted text here..." in result.body
    # Frontmatter YAML must NOT appear in body
    assert "concept_id:" not in result.body
    assert "status: validated" not in result.body


def test_parser_body_output_not_used_to_rewrite_concept_file():
    """splitlines() + join() may drop a trailing newline.  The parser body is
    for reading and chunking only — concept files must NOT be rewritten with
    parser output directly."""
    text = "---\nkey: value\n---\n\nBody text.\n\n"
    result = parse_frontmatter(text)
    # Body is for reading/chunking — trailing newlines may differ from original
    assert "Body text." in result.body
    # This just documents the behavior; no rewrite occurs in this patch
    assert True
