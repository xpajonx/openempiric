from __future__ import annotations
import time
import pytest
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine

@pytest.fixture
def temp_project(tmp_path):
    eng = KnowledgeEngine(tmp_path)
    eng.init_project(str(tmp_path))
    return eng, tmp_path

def test_noop_session_generates_minimal_observations(temp_project):
    engine, tmp_path = temp_project

    # Create an initial concept file in the wiki/concepts directory
    concepts_dir = tmp_path / ".oem" / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    concept_file = concepts_dir / "test-concept.md"
    
    initial_content = """---
canonical_name: Test Concept
aliases: []
---
This is some initial content."""
    concept_file.write_text(initial_content, encoding="utf-8")
    
    # Run a session reflection. The file exists but has mtime before the session started.
    session_start = time.time() + 10  # Future start to guarantee file is older
    
    res = engine.reflect_session(
        project=str(tmp_path),
        conversation_text="Hypothesis: This is a test chat conversation.",
        session_id="session_test_noise",
        session_started_at=session_start
    )
    
    explainability = res.get("explainability", {})
    observations = explainability.get("file_observations", 0)
    changed_files = 0  # No files should be detected as changed
    
    assert observations <= changed_files + 2, (
        f"Expected minimal observations (<= {changed_files + 2}), got {observations}"
    )

def test_reflection_explainability_reports_sources(temp_project):
    engine, tmp_path = temp_project

    res = engine.reflect_session(
        project=str(tmp_path),
        conversation_text="Fixed doctor global install detection.\nRefactored reflection pipeline.",
        session_id="session_test_explainability",
    )
    
    assert "explainability" in res
    exp = res["explainability"]
    assert "top_sources" in exp
    assert "fallback_extractions" in exp
    assert "file_observations" in exp
    assert "structured_events" in exp
