import pytest
import json
import threading
import queue
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.fs import FileLock

# Helpers
def write_registry(engine, project: Path, registry: dict) -> None:
    engine.state._save_registry(registry, str(project))

def read_registry(engine, project: Path) -> dict:
    return engine.state._load_registry(str(project))

def wiki_path(project: Path, concept_id: str) -> Path:
    return project / ".oem" / "wiki" / f"{concept_id}.md"

def materialize_validated_concept(engine, project: Path, concept_name: str, session_id: str = "sess_1"):
    """Creates a session report with 3 observations to ensure the concept status is promoted to 'validated' and materialized."""
    sessions_dir = engine._sessions_dir(str(project))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / f"{session_id}.md"
    
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {
                "type": "observation",
                "concept": concept_name,
                "evidence": "evidence 1",
                "session_id": session_id,
                "event_id": f"event-{concept_name}-1"
            },
            {
                "type": "observation",
                "concept": concept_name,
                "evidence": "evidence 2",
                "session_id": session_id,
                "event_id": f"event-{concept_name}-2"
            },
            {
                "type": "observation",
                "concept": concept_name,
                "evidence": "evidence 3",
                "session_id": session_id,
                "event_id": f"event-{concept_name}-3"
            }
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    return engine.materialization.materialize_concepts(str(project))

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    return project_dir, engine

def test_deleted_registry_entry_does_not_reuse_id(temp_project):
    """1. Deleted registry entry must not allow ID reuse."""
    project_dir, engine = temp_project
    
    # Setup registry with concept_001 and concept_003 (concept_002 is deleted/missing)
    initial_reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "alpha",
            "aliases": ["alpha"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        },
        "concept_003": {
            "concept_id": "concept_003",
            "canonical_name": "gamma",
            "aliases": ["gamma"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        }
    }
    write_registry(engine, project_dir, initial_reg)
    
    # Materialize a new concept (not alpha, not gamma)
    materialize_validated_concept(engine, project_dir, "beta", session_id="sess_1")
    
    # Load registry and verify beta concept ID is safe
    reg = read_registry(engine, project_dir)
    
    # Find the new concept's ID
    new_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            new_cid = cid
            break
            
    assert new_cid is not None, "Beta concept was not materialized"
    
    # Current behavior will allocate concept_003 (len(registry)+1 = 3) which collides/overwrites concept_003
    # Expected safe behavior: new ID must not be concept_003 or concept_001
    assert new_cid != "concept_003"
    assert new_cid != "concept_001"

def test_orphan_wiki_file_blocks_id_reuse(temp_project):
    """2. Orphan wiki file must block ID reuse and must not be overwritten."""
    project_dir, engine = temp_project
    
    # Registry only contains concept_001
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
    write_registry(engine, project_dir, initial_reg)
    
    # Create an orphan wiki file concept_002.md with sentinel content
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = wiki_path(project_dir, "concept_002")
    orphan_file.write_text("SENTINEL: DO NOT OVERWRITE", encoding="utf-8")
    
    # Materialize new concept (beta)
    # len(registry) is 1, so current logic allocates concept_002, overwriting the orphan file
    materialize_validated_concept(engine, project_dir, "beta", session_id="sess_1")
    
    # Check registry state
    reg = read_registry(engine, project_dir)
    beta_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
            break
            
    assert beta_cid is not None, "Beta concept was not materialized"
    
    # Expected safe behavior: beta must not get concept_002 ID, and the orphan file must remain intact
    assert beta_cid != "concept_002"
    assert orphan_file.read_text(encoding="utf-8") == "SENTINEL: DO NOT OVERWRITE"

def test_registry_gap_does_not_cause_collision(temp_project):
    """3. Registry gap must not cause collision."""
    project_dir, engine = temp_project
    
    # Setup registry with concept_001 and concept_004 (gap at concept_002 and concept_003)
    initial_reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "alpha",
            "aliases": ["alpha"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        },
        "concept_004": {
            "concept_id": "concept_004",
            "canonical_name": "delta",
            "aliases": ["delta"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        }
    }
    write_registry(engine, project_dir, initial_reg)
    
    # Materialize two new concepts sequentially: beta and epsilon
    # Under current logic:
    # 1. len(registry) = 2 -> beta becomes concept_003 (registry len is now 3)
    # 2. len(registry) = 3 -> epsilon becomes concept_004 (collision with delta!)
    materialize_validated_concept(engine, project_dir, "beta", session_id="sess_1")
    materialize_validated_concept(engine, project_dir, "epsilon", session_id="sess_2")
    
    reg = read_registry(engine, project_dir)
    
    delta_data = reg.get("concept_004")
    assert delta_data is not None, "delta concept was completely overwritten or removed"
    assert delta_data.get("canonical_name") == "delta", "concept_004 was hijacked by epsilon!"
    
    epsilon_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "epsilon":
            epsilon_cid = cid
            break
            
    assert epsilon_cid is not None, "Epsilon concept was not materialized"
    assert epsilon_cid != "concept_004", "Epsilon collided with delta (concept_004)"

def test_non_numeric_concept_ids_do_not_break_allocator(temp_project):
    """4. Existing non-numeric concept IDs must not break allocator."""
    project_dir, engine = temp_project
    
    initial_reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "alpha",
            "aliases": ["alpha"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        },
        "concept_custom": {
            "concept_id": "concept_custom",
            "canonical_name": "custom",
            "aliases": ["custom"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        }
    }
    write_registry(engine, project_dir, initial_reg)
    
    # Materialize a new concept (beta)
    # current logic: len(registry) + 1 = 3 -> concept_003
    materialize_validated_concept(engine, project_dir, "beta", session_id="sess_1")
    
    reg = read_registry(engine, project_dir)
    beta_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
            break
            
    assert beta_cid is not None
    assert beta_cid.startswith("concept_")
    assert beta_cid != "concept_custom"
    
    # Ensure custom concept is still in registry
    assert "concept_custom" in reg
    assert reg["concept_custom"]["canonical_name"] == "custom"

def test_concurrent_materialization_does_not_duplicate_ids(temp_project):
    """5. Concurrent materialization must not duplicate IDs."""
    project_dir, engine = temp_project
    
    # Setup initial registry with concept_001
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
    write_registry(engine, project_dir, initial_reg)
    
    # Create two sessions with new concepts to materialize
    sessions_dir = project_dir / ".oem" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    
    # Session 1: beta
    (sessions_dir / "sess_1.md").write_text("```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "beta", "evidence": "e1"},
            {"type": "observation", "concept": "beta", "evidence": "e2"},
            {"type": "observation", "concept": "beta", "evidence": "e3"}
        ]
    }) + "\n```", encoding="utf-8")
    
    # Session 2: gamma
    (sessions_dir / "sess_2.md").write_text("```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "gamma", "evidence": "e1"},
            {"type": "observation", "concept": "gamma", "evidence": "e2"},
            {"type": "observation", "concept": "gamma", "evidence": "e3"}
        ]
    }) + "\n```", encoding="utf-8")
    
    results = queue.Queue()
    
    def run_materialize(engine_inst):
        try:
            # We call the public materialize_concepts API under concurrency
            res = engine_inst.materialization.materialize_concepts(str(project_dir))
            results.put((res, None))
        except Exception as e:
            results.put((None, e))
            
    # Create separate engines to simulate concurrent instances targeting same project
    engine1 = KnowledgeEngine(project_dir)
    engine2 = KnowledgeEngine(project_dir)
    
    t1 = threading.Thread(target=run_materialize, args=(engine1,))
    t2 = threading.Thread(target=run_materialize, args=(engine2,))
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # Check results
    while not results.empty():
        res, exc = results.get()
        assert exc is None, f"Concurrent materialize failed: {exc}"
        assert res.get("status") in ("success", "error")
        
    # Load registry and verify that beta and gamma have distinct IDs
    reg = read_registry(engine, project_dir)
    
    beta_cid = None
    gamma_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
        elif data.get("canonical_name") == "gamma":
            gamma_cid = cid
            
    # If both got materialized (either sequentially or one after another), they must have distinct IDs.
    if beta_cid and gamma_cid:
        assert beta_cid != gamma_cid, f"Duplicate IDs generated concurrently: {beta_cid}"

def test_existing_wiki_file_content_not_overwritten(temp_project):
    """6. Existing wiki file content must not be overwritten."""
    project_dir, engine = temp_project
    
    # Setup initial registry with concept_001
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
    write_registry(engine, project_dir, initial_reg)
    
    # Create concept_002.md with sentinel content
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    target_file = wiki_path(project_dir, "concept_002")
    target_file.write_text("DO NOT OVERWRITE SENTINEL", encoding="utf-8")
    
    # Materialize new concept (beta). Current logic allocates concept_002.md
    materialize_validated_concept(engine, project_dir, "beta", session_id="sess_1")
    
    # Verify the file was not overwritten
    assert target_file.read_text(encoding="utf-8") == "DO NOT OVERWRITE SENTINEL"
