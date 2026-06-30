from __future__ import annotations

import pytest
from oem_knowledge.health import calculate_concept_health, validate_concept_frontmatter
from oem_knowledge.engine import KnowledgeEngine


def test_calculate_concept_health():
    # Test typical/normal state
    cdata = {
        "concept_id": "concept_001",
        "canonical_name": "test-concept",
        "status": "validated",
        "confidence": 3,
        "evidence_count": 4,
        "failure_count": 0
    }
    score = calculate_concept_health(cdata)
    assert 0.0 <= score <= 100.0

    # Test deprecated state (should be 0)
    cdata_dep = {**cdata, "status": "deprecated"}
    assert calculate_concept_health(cdata_dep) == 0.0

    # Test global state (should have high score)
    cdata_global = {**cdata, "status": "global", "confidence": 5, "evidence_count": 10}
    score_global = calculate_concept_health(cdata_global)
    assert score_global == 100.0

    # Test heavy failure state (should penalize heavily)
    cdata_fail = {**cdata, "failure_count": 5}
    score_fail = calculate_concept_health(cdata_fail)
    assert score_fail < score


# ---------------------------------------------------------------------------
# Concept Integrity validation
# ---------------------------------------------------------------------------


@pytest.fixture
def health_project(tmp_path):
    project_dir = tmp_path / "health_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def test_health_reports_frontmatter_block_not_closed_as_error(health_project):
    engine = KnowledgeEngine(health_project)
    wiki_dir = engine._concepts_dir(str(health_project))

    (wiki_dir / "concept_001.md").write_text(
        """---
concept_id: concept_001
status: validated

# No closing frontmatter delimiter.
""",
        encoding="utf-8",
    )

    result = validate_concept_frontmatter(str(health_project))
    assert result["status"] == "error"
    reasons = [c.get("reason") for c in result["checks"]]
    assert "frontmatter_block_not_closed" in reasons


def test_health_clean_for_valid_concept_with_body_separators(health_project):
    engine = KnowledgeEngine(health_project)
    wiki_dir = engine._concepts_dir(str(health_project))

    (wiki_dir / "concept_001.md").write_text(
        """---
concept_id: concept_001
status: validated
---

# Learnings

- echo "---"

---

Body horizontal rule.
""",
        encoding="utf-8",
    )

    result = validate_concept_frontmatter(str(health_project))
    assert result["status"] == "success"
    assert len(result["checks"]) == 0


def test_health_concept_id_mismatch_is_error(health_project):
    engine = KnowledgeEngine(health_project)
    wiki_dir = engine._concepts_dir(str(health_project))

    # File is concept_001.md but frontmatter says concept_002
    (wiki_dir / "concept_001.md").write_text(
        """---
concept_id: concept_002
status: validated
---

Body content.
""",
        encoding="utf-8",
    )

    result = validate_concept_frontmatter(str(health_project))
    assert result["status"] == "error"
    reasons = [c.get("reason") for c in result["checks"]]
    assert "concept_id_mismatch" in reasons


def test_health_missing_status_is_warning(health_project):
    engine = KnowledgeEngine(health_project)
    wiki_dir = engine._concepts_dir(str(health_project))

    (wiki_dir / "concept_001.md").write_text(
        """---
concept_id: concept_001
---

Body content without a status.
""",
        encoding="utf-8",
    )

    result = validate_concept_frontmatter(str(health_project))
    assert result["status"] in ("warn", "error")
    reasons = [c.get("reason") for c in result["checks"]]
    assert "missing_status" in reasons


def test_health_registry_unavailable_does_not_block_file_integrity(health_project):
    engine = KnowledgeEngine(health_project)
    wiki_dir = engine._concepts_dir(str(health_project))

    (wiki_dir / "concept_001.md").write_text(
        """---
concept_id: concept_001
status: validated
---

Body.
""",
        encoding="utf-8",
    )

    # Corrupt registry to simulate unavailability
    reg_path = engine.state.engine._registry_path(str(health_project))
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text("not json", encoding="utf-8")

    result = validate_concept_frontmatter(str(health_project))
    # File-level integrity still checked even if registry unavailable
    statuses = [c["status"] for c in result["checks"]]
    assert "warn" in statuses or "success" in statuses
    # Should NOT be overall error from missing registry alone


def test_health_empty_body_is_warning(health_project):
    engine = KnowledgeEngine(health_project)
    wiki_dir = engine._concepts_dir(str(health_project))

    (wiki_dir / "concept_001.md").write_text(
        """---
concept_id: concept_001
status: validated
---
""",
        encoding="utf-8",
    )

    result = validate_concept_frontmatter(str(health_project))
    assert result["status"] in ("warn", "error")
    reasons = [c.get("reason") for c in result["checks"]]
    assert "empty_body" in reasons
