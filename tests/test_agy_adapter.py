import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from oem_knowledge.adapters.antigravity.adapter import AntigravityAdapter
from oem_knowledge.engine import KnowledgeEngine

def test_parse_transcript(tmp_path):
    transcript_file = tmp_path / "transcript.jsonl"
    transcript_content = (
        '{"source":"USER","type":"USER_INPUT","content":"Hello agent"}\n'
        '{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Hello user"}\n'
    )
    transcript_file.write_text(transcript_content, encoding="utf-8")

    adapter = AntigravityAdapter()
    parsed = adapter.parse_transcript(transcript_file)
    assert "User: Hello agent" in parsed
    assert "Agent: Hello user" in parsed

def test_context_injection(tmp_path):
    project_dir = tmp_path / "my_project"
    project_dir.mkdir()
    oem_dir = project_dir / ".oem"
    oem_dir.mkdir()
    
    handoff_file = oem_dir / "session-handoff.md"
    handoff_file.write_text("## Next Action\nImplement stuff", encoding="utf-8")

    engine = MagicMock(spec=KnowledgeEngine)
    engine._load_registry.return_value = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "ai-safety",
            "status": "validated",
            "aliases": []
        }
    }

    adapter = AntigravityAdapter(engine)
    
    # Mock registry wiki file
    wiki_dir = oem_dir / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "concept_001.md").write_text("AI Safety measures description.", encoding="utf-8")

    context = adapter.context_injection(str(project_dir))
    assert "Implement stuff" in context
    assert "ai-safety" in context

def test_session_start(tmp_path):
    engine = MagicMock(spec=KnowledgeEngine)
    engine.restore_session_state.return_value = {
        "active_goals": ["Goal 1"],
        "active_decisions": ["Decision 1"]
    }
    
    adapter = AntigravityAdapter(engine)
    res = adapter.session_start(str(tmp_path))
    assert "session_id" in res
    assert res["active_goals"] == ["Goal 1"]
