import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.models import KnowledgeEvent
from oem_knowledge.runtime import SessionState

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    return project_dir, engine

def test_session_end_reports_partial_when_events_written_but_materialization_failed(temp_project):
    """Verify that session_end returns partial status and closes the session on materialization error."""
    project_dir, engine = temp_project
    
    # Pre-stage active session file
    active_session_file = engine._resolve_harness(str(project_dir)) / "state" / "active_session.json"
    active_session_file.parent.mkdir(parents=True, exist_ok=True)
    
    sess = SessionState(
        session_id="session_test",
        agent="test-agent",
        status="running",
        started_at=12345.0,
        project=str(project_dir),
        transcript_path=str(project_dir / "transcript.md"),
        context_path=str(project_dir / "context.json"),
        temp_instructions=str(project_dir / "temp_instructions.txt")
    )
    sess.save(active_session_file)
    
    # Pre-populate registry with concept_001
    initial_reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "alpha",
            "aliases": ["alpha"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        }
    }
    engine.state._save_registry(initial_reg, str(project_dir))
    
    # Create concept_002.md
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    target_file = wiki_dir / "concept_002.md"
    target_file.write_text("SENTINEL", encoding="utf-8")
    
    # Mock allocator to return concept_002 (so it collides and fails materialization)
    with patch("oem_knowledge.concept_id.allocate_concept_id", return_value="concept_002"):
        # We pass 3 observation events to hit the validation threshold for the write path
        events = [
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e1", "summary": "s1", "event_id": "evt1"},
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e2", "summary": "s2", "event_id": "evt2"},
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e3", "summary": "s3", "event_id": "evt3"}
        ]
        
        res = engine.session_end(
            project=str(project_dir),
            conversation_text="some chat",
            session_id="session_test",
            events=events
        )
        
        assert res["status"] == "partial"
        assert res["session_closed"] is True
        assert res["events_written"] == 3
        assert res["materialization"]["status"] == "failed"
        assert res["index"]["status"] == "skipped"
        
        # Verify active session is unlinked (session is closed)
        assert not active_session_file.exists()

def test_session_end_closes_session_even_when_materialization_partial(temp_project):
    """Verify that session is closed and active session is cleaned up when materialization is partial/fails."""
    project_dir, engine = temp_project
    active_session_file = engine._resolve_harness(str(project_dir)) / "state" / "active_session.json"
    active_session_file.parent.mkdir(parents=True, exist_ok=True)
    
    sess = SessionState(
        session_id="session_test",
        agent="test-agent",
        status="running",
        started_at=12345.0,
        project=str(project_dir),
        transcript_path=str(project_dir / "transcript.md"),
        context_path=str(project_dir / "context.json"),
        temp_instructions=str(project_dir / "temp_instructions.txt")
    )
    sess.save(active_session_file)
    
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_001.md").write_text("SENTINEL", encoding="utf-8")
    
    with patch("oem_knowledge.concept_id.allocate_concept_id", return_value="concept_001"):
        events = [
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e1", "summary": "s1", "event_id": "evt1"},
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e2", "summary": "s2", "event_id": "evt2"},
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e3", "summary": "s3", "event_id": "evt3"}
        ]
        
        res = engine.session_end(
            project=str(project_dir),
            conversation_text="some chat",
            session_id="session_test",
            events=events
        )
        assert res["status"] == "partial"
        assert not active_session_file.exists()

def test_session_end_does_not_duplicate_events_on_retry(temp_project):
    """Verify that retrying session_end with same events does not write duplicate events to events.jsonl."""
    project_dir, engine = temp_project
    
    events = [
        {"type": "observation", "concept_candidates": ["beta"], "evidence": "e1", "summary": "s1", "event_id": "evt_abc"},
    ]
    
    # Run first session_end (events written, materialization success because it doesn't write since status is candidate)
    res1 = engine.session_end(
        project=str(project_dir),
        conversation_text="some chat",
        session_id="session_test_1",
        events=events
    )
    
    # Run second session_end with same event
    res2 = engine.session_end(
        project=str(project_dir),
        conversation_text="some chat",
        session_id="session_test_2",
        events=events
    )
    
    # Load and check the event log
    events_path = engine.layout(str(project_dir)).events_path
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    event_ids = []
    for line in lines:
        if line.strip():
            event_ids.append(json.loads(line)["event_id"])
            
    # Check that evt_abc is only present once in the log
    assert event_ids.count("evt_abc") == 1

def test_session_end_does_not_duplicate_concepts_on_retry(temp_project):
    """Verify that retrying session_end does not create duplicate concepts or files."""
    project_dir, engine = temp_project
    
    events = [
        {"type": "observation", "concept_candidates": ["beta"], "evidence": "e1", "summary": "s1", "event_id": "evt_abc_1"},
        {"type": "observation", "concept_candidates": ["beta"], "evidence": "e2", "summary": "s2", "event_id": "evt_abc_2"},
        {"type": "observation", "concept_candidates": ["beta"], "evidence": "e3", "summary": "s3", "event_id": "evt_abc_3"}
    ]
    
    # Run first session_end
    res1 = engine.session_end(
        project=str(project_dir),
        conversation_text="some chat",
        session_id="session_test_1",
        events=events
    )
    
    reg1 = engine.state._load_registry(str(project_dir))
    beta_cid_1 = None
    for cid, data in reg1.items():
        if data.get("canonical_name") == "beta":
            beta_cid_1 = cid
            break
            
    assert beta_cid_1 is not None
    
    # Run second session_end with same events
    res2 = engine.session_end(
        project=str(project_dir),
        conversation_text="some chat",
        session_id="session_test_2",
        events=events
    )
    
    reg2 = engine.state._load_registry(str(project_dir))
    beta_cid_2 = None
    for cid, data in reg2.items():
        if data.get("canonical_name") == "beta":
            beta_cid_2 = cid
            break
            
    assert beta_cid_2 == beta_cid_1

def test_session_end_does_not_run_index_when_materialization_failed_without_changes(temp_project):
    """Verify that indexing is skipped if materialization fails."""
    project_dir, engine = temp_project
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_001.md").write_text("SENTINEL", encoding="utf-8")
    
    with patch("oem_knowledge.concept_id.allocate_concept_id", return_value="concept_001"):
        events = [
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e1", "summary": "s1", "event_id": "evt1"},
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e2", "summary": "s2", "event_id": "evt2"},
            {"type": "observation", "concept_candidates": ["beta"], "evidence": "e3", "summary": "s3", "event_id": "evt3"}
        ]
        
        res = engine.session_end(
            project=str(project_dir),
            conversation_text="some chat",
            session_id="session_test",
            events=events
        )
        assert res["index"]["status"] == "skipped"

def test_materialization_skips_already_materialized_events(temp_project):
    """Verify that materialization skips already materialized events by checking their event_ids."""
    project_dir, engine = temp_project
    
    # Pre-populate registry with a concept that has evt_123 in its source_event_ids
    initial_reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "beta",
            "aliases": ["beta"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"],
            "source_event_ids": ["evt_123"]
        }
    }
    engine.state._save_registry(initial_reg, str(project_dir))
    
    # Run materialize_concepts with event containing evt_123
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "beta", "evidence": "e4", "event_id": "evt_123"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    res = engine.materialization.materialize_concepts(str(project_dir))
    assert res["status"] == "success"
    
    # Check that it skipped the event
    assert "skipped_existing: evt_123" in res["materialized"]

def test_session_end_retry_after_materialization_failure_is_idempotent(temp_project):
    """Verify that retrying session_end after a partial failure is fully idempotent and succeeds if the conflict is resolved."""
    project_dir, engine = temp_project
    active_session_file = engine._resolve_harness(str(project_dir)) / "state" / "active_session.json"
    active_session_file.parent.mkdir(parents=True, exist_ok=True)
    
    sess = SessionState(
        session_id="session_test",
        agent="test-agent",
        status="running",
        started_at=12345.0,
        project=str(project_dir),
        transcript_path=str(project_dir / "transcript.md"),
        context_path=str(project_dir / "context.json"),
        temp_instructions=str(project_dir / "temp_instructions.txt")
    )
    sess.save(active_session_file)
    
    # Pre-create concept_001.md
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    concept_001_file = wiki_dir / "concept_001.md"
    concept_001_file.write_text("SENTINEL", encoding="utf-8")
    
    events = [
        {"type": "observation", "concept_candidates": ["beta"], "evidence": "e1", "summary": "s1", "event_id": "evt_abc_1"},
        {"type": "observation", "concept_candidates": ["beta"], "evidence": "e2", "summary": "s2", "event_id": "evt_abc_2"},
        {"type": "observation", "concept_candidates": ["beta"], "evidence": "e3", "summary": "s3", "event_id": "evt_abc_3"}
    ]
    
    # 1. Run session_end with forced collision -> returns partial
    with patch("oem_knowledge.concept_id.allocate_concept_id", return_value="concept_001"):
        res1 = engine.session_end(
            project=str(project_dir),
            conversation_text="some chat",
            session_id="session_test",
            events=events
        )
        assert res1["status"] == "partial"
        
    # 2. Run session_end again (retry) without mock -> should allocate concept_002 and succeed
    # Re-save active session since first one was closed
    sess.save(active_session_file)
    res2 = engine.session_end(
        project=str(project_dir),
        conversation_text="some chat",
        session_id="session_test",
        events=events
    )
    assert res2["status"] == "success"
    
    # Verify concept_001.md was preserved and concept_002.md was created
    assert concept_001_file.read_text(encoding="utf-8") == "SENTINEL"
    assert (wiki_dir / "concept_002.md").exists()
