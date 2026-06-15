import pytest
from pathlib import Path
from oem_knowledge.concept_id import allocate_concept_id

def test_allocate_concept_id_uses_max_existing_registry_id():
    """Verify that allocation starts based on the max existing registry ID, not len(registry)."""
    registry = {
        "concept_001": {},
        "concept_004": {}
    }
    # Should allocate concept_005 since 4 is the maximum
    result = allocate_concept_id(registry=registry)
    assert result == "concept_005"

def test_allocate_concept_id_considers_orphan_wiki_files(tmp_path):
    """Verify that orphan wiki files are scanned and block their IDs from being allocated."""
    registry = {
        "concept_001": {}
    }
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    
    # Create an orphan wiki file concept_002.md
    (wiki_dir / "concept_002.md").write_text("content", encoding="utf-8")
    
    # Should allocate concept_003 because concept_002 exists on disk
    result = allocate_concept_id(registry=registry, wiki_dir=wiki_dir)
    assert result == "concept_003"

def test_allocate_concept_id_considers_reserved_ids():
    """Verify that reserved/in-flight IDs from the same run are not reused."""
    registry = {
        "concept_001": {}
    }
    reserved = {"concept_002"}
    
    # Should allocate concept_003 because concept_002 is reserved
    result = allocate_concept_id(registry=registry, reserved_ids=reserved)
    assert result == "concept_003"

def test_allocate_concept_id_ignores_non_numeric_for_sequence():
    """Verify that non-numeric/custom IDs are ignored for numeric sequence calculation but occupied."""
    registry = {
        "concept_001": {},
        "concept_custom": {}
    }
    # Should allocate concept_002 (ignoring concept_custom for sequencing)
    result = allocate_concept_id(registry=registry)
    assert result == "concept_002"

def test_allocate_concept_id_never_returns_existing_exact_id():
    """Verify that the allocator loops to avoid any exact collisions, even with custom IDs."""
    registry = {
        "concept_001": {},
        "concept_002": {}
    }
    reserved = {"concept_003"}
    
    # If custom ID concept_004 exists, allocator should skip it and return concept_005
    registry["concept_004"] = {}
    result = allocate_concept_id(registry=registry, reserved_ids=reserved)
    assert result == "concept_005"

import json
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.concept_id import ConceptIdCollisionError

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    return project_dir, engine

def test_materialization_allocates_distinct_ids_in_same_run(temp_project):
    """Verify that multiple new concepts materialized in the same run receive distinct IDs."""
    project_dir, engine = temp_project
    
    # Create a session report with two new concepts
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "first-concept", "evidence": "e1"},
            {"type": "observation", "concept": "first-concept", "evidence": "e2"},
            {"type": "observation", "concept": "first-concept", "evidence": "e3"},
            {"type": "observation", "concept": "second-concept", "evidence": "e1"},
            {"type": "observation", "concept": "second-concept", "evidence": "e2"},
            {"type": "observation", "concept": "second-concept", "evidence": "e3"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    # Run materialization
    engine.materialization.materialize_concepts(str(project_dir))
    
    # Assert they got distinct concept IDs
    reg = engine.state._load_registry(str(project_dir))
    ids = {}
    for cid, data in reg.items():
        if data.get("canonical_name") in ("first-concept", "second-concept"):
            ids[data["canonical_name"]] = cid
            
    assert "first-concept" in ids
    assert "second-concept" in ids
    assert ids["first-concept"] != ids["second-concept"]

def test_materialization_does_not_overwrite_existing_wiki_file(temp_project):
    """Verify that materialization raises ConceptIdCollisionError if it tries to write to an existing wiki file."""
    project_dir, engine = temp_project
    
    # Set up registry with concept_001
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
    
    # Create concept_002.md with sentinel content
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    target_file = wiki_dir / "concept_002.md"
    target_file.write_text("SENTINEL CONTENT", encoding="utf-8")
    
    # Mock allocate_concept_id to return concept_002 to trigger overwrite error
    from unittest.mock import patch
    with patch("oem_knowledge.concept_id.allocate_concept_id", return_value="concept_002"):
        sessions_dir = engine._sessions_dir(str(project_dir))
        sessions_dir.mkdir(parents=True, exist_ok=True)
        report_file = sessions_dir / "sess_1.md"
        report_content = "```json\n" + json.dumps({
            "knowledge_events": [
                {"type": "observation", "concept": "beta", "evidence": "e1"},
                {"type": "observation", "concept": "beta", "evidence": "e2"},
                {"type": "observation", "concept": "beta", "evidence": "e3"}
            ]
        }) + "\n```"
        report_file.write_text(report_content, encoding="utf-8")
        
        res = engine.materialization.materialize_concepts(str(project_dir))
        assert res["status"] == "error"
        assert "Concept ID collision" in res["message"]
            
    # Verify the file was not overwritten
    assert target_file.read_text(encoding="utf-8") == "SENTINEL CONTENT"

def test_materialization_registry_and_wiki_stay_consistent(temp_project):
    """Verify registry and wiki file names remain fully consistent after materialization."""
    project_dir, engine = temp_project
    
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "beta", "evidence": "e1"},
            {"type": "observation", "concept": "beta", "evidence": "e2"},
            {"type": "observation", "concept": "beta", "evidence": "e3"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    engine.materialization.materialize_concepts(str(project_dir))
    
    reg = engine.state._load_registry(str(project_dir))
    beta_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
            break
            
    assert beta_cid is not None
    wiki_dir = engine._concepts_dir(str(project_dir))
    assert (wiki_dir / f"{beta_cid}.md").exists()

def test_materialization_no_len_registry_id_generation(temp_project):
    """Verify that len(registry) is not used for concept ID allocation."""
    project_dir, engine = temp_project
    
    # Set up registry with concept_001 and concept_004 (gap at concept_002 and concept_003)
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
    engine.state._save_registry(initial_reg, str(project_dir))
    
    # Materialize new concept (beta)
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "beta", "evidence": "e1"},
            {"type": "observation", "concept": "beta", "evidence": "e2"},
            {"type": "observation", "concept": "beta", "evidence": "e3"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    engine.materialization.materialize_concepts(str(project_dir))
    
    reg = engine.state._load_registry(str(project_dir))
    beta_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
            break
            
    # Max is 4, so next allocated ID must be concept_005 (not concept_003 which len(registry)+1 would generate)
    assert beta_cid == "concept_005"


def test_two_processes_concurrent_materialization(temp_project):
    """Verify that concurrent processes materializing concepts on the same project serialize correctly and do not duplicate IDs."""
    project_dir, engine = temp_project
    
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
    
    # Create two sessions with new concepts to materialize
    sessions_dir = engine._sessions_dir(str(project_dir))
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
    
    import sys
    import subprocess
    import threading
    import queue
    
    cmd = [
        sys.executable,
        "-c",
        "import sys; from oem_knowledge.engine import KnowledgeEngine; engine = KnowledgeEngine(sys.argv[1]); engine.materialization.materialize_concepts(sys.argv[1])",
        str(project_dir)
    ]
    
    results = queue.Queue()
    
    def run_proc():
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            results.put((res.stdout, res.stderr, None))
        except Exception as e:
            results.put((None, None, e))
            
    # Start two subprocesses concurrently
    t1 = threading.Thread(target=run_proc)
    t2 = threading.Thread(target=run_proc)
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # Verify no unexpected exceptions
    while not results.empty():
        out, err, exc = results.get()
        assert exc is None, f"Subprocess run failed: {exc}\nStderr: {err}"
        
    # Verify registry remains valid JSON and concepts have distinct IDs
    reg = engine.state._load_registry(str(project_dir))
    assert isinstance(reg, dict)
    
    beta_cid = None
    gamma_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
        elif data.get("canonical_name") == "gamma":
            gamma_cid = cid
            
    assert beta_cid is not None, "beta was not materialized"
    assert gamma_cid is not None, "gamma was not materialized"
    assert beta_cid != gamma_cid, f"Duplicate IDs generated concurrently across processes: {beta_cid}"


def test_existing_project_with_gaps_still_works(temp_project):
    """Verify that legacy projects with registry gaps allocate max + 1 safely."""
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
        "concept_003": {
            "concept_id": "concept_003",
            "canonical_name": "gamma",
            "aliases": ["gamma"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        },
        "concept_010": {
            "concept_id": "concept_010",
            "canonical_name": "omega",
            "aliases": ["omega"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        }
    }
    engine.state._save_registry(initial_reg, str(project_dir))
    
    # Materialize new concept (beta)
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "beta", "evidence": "e1"},
            {"type": "observation", "concept": "beta", "evidence": "e2"},
            {"type": "observation", "concept": "beta", "evidence": "e3"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    engine.materialization.materialize_concepts(str(project_dir))
    
    reg = engine.state._load_registry(str(project_dir))
    beta_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
            break
            
    assert beta_cid == "concept_011"


def test_existing_orphan_wiki_files_still_block_reuse(temp_project):
    """Verify that orphan wiki files are not overwritten and block their IDs from reuse."""
    project_dir, engine = temp_project
    
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
    
    # Create an orphan wiki file concept_002.md (missing from registry) with sentinel content
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = wiki_dir / "concept_002.md"
    orphan_file.write_text("SENTINEL: DO NOT OVERWRITE", encoding="utf-8")
    
    # Materialize new concept (beta)
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "beta", "evidence": "e1"},
            {"type": "observation", "concept": "beta", "evidence": "e2"},
            {"type": "observation", "concept": "beta", "evidence": "e3"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    engine.materialization.materialize_concepts(str(project_dir))
    
    reg = engine.state._load_registry(str(project_dir))
    beta_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
            break
            
    # concept_002 is blocked by the orphan file, so it must allocate concept_003
    assert beta_cid == "concept_003"
    assert orphan_file.read_text(encoding="utf-8") == "SENTINEL: DO NOT OVERWRITE"


def test_materialization_does_not_allocate_existing_wiki_concept_id(temp_project):
    """Verify that materialization does not allocate concept_009 when concept_009.md exists in the wiki."""
    project_dir, engine = temp_project
    
    # Setup registry with concept_001 to concept_005
    initial_reg = {}
    for i in range(1, 6):
        cid = f"concept_{i:03d}"
        initial_reg[cid] = {
            "concept_id": cid,
            "canonical_name": f"concept-{i}",
            "aliases": [f"concept-{i}"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        }
    engine.state._save_registry(initial_reg, str(project_dir))
    
    # Create concept_009.md in the wiki (which is missing from the registry)
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    concept_009_file = wiki_dir / "concept_009.md"
    concept_009_file.write_text("SENTINEL: DO NOT OVERWRITE", encoding="utf-8")
    
    # Create a session report with a new concept to materialize
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "new-concept", "evidence": "some evidence"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    # Run materialization
    res = engine.materialization.materialize_concepts(str(project_dir))
    
    # Check that concept_009.md remains untouched
    assert concept_009_file.read_text(encoding="utf-8") == "SENTINEL: DO NOT OVERWRITE"
    
    # Verify it succeeds and allocated a safe ID
    assert res["status"] == "success"
    reg = engine.state._load_registry(str(project_dir))
    new_concept_id = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "new-concept":
            new_concept_id = cid
            break
            
    assert new_concept_id is not None
    assert new_concept_id != "concept_009"


def test_allocate_concept_id_scans_registry_and_wiki_files(tmp_path):
    registry = {"concept_001": {}}
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "concept_002.md").write_text("content", encoding="utf-8")
    result = allocate_concept_id(registry=registry, wiki_dir=wiki_dir)
    assert result == "concept_003"

def test_allocate_concept_id_skips_orphan_wiki_file(tmp_path):
    registry = {"concept_001": {}}
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "concept_002.md").write_text("content", encoding="utf-8")
    result = allocate_concept_id(registry=registry, wiki_dir=wiki_dir)
    assert result == "concept_003"

def test_allocate_concept_id_skips_reserved_ids_in_batch():
    registry = {"concept_001": {}}
    reserved = {"concept_002"}
    result = allocate_concept_id(registry=registry, reserved_ids=reserved)
    assert result == "concept_003"

def test_allocate_concept_id_ignores_nonnumeric_for_sequence():
    registry = {"concept_001": {}, "concept_custom": {}}
    result = allocate_concept_id(registry=registry)
    assert result == "concept_002"

def test_allocate_concept_id_never_uses_len_registry_plus_one():
    registry = {
        "concept_001": {},
        "concept_004": {}
    }
    result = allocate_concept_id(registry=registry)
    assert result == "concept_005"

def test_allocate_concept_id_handles_gaps_safely(tmp_path):
    registry = {
        "concept_001": {},
        "concept_003": {},
        "concept_010": {}
    }
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "concept_002.md").write_text("content", encoding="utf-8")
    (wiki_dir / "concept_009.md").write_text("content", encoding="utf-8")
    reserved = {"concept_011"}
    
    result = allocate_concept_id(registry=registry, wiki_dir=wiki_dir, reserved_ids=reserved)
    assert result == "concept_012"

def test_allocate_concept_id_handles_existing_concept_009_file(tmp_path):
    registry = {
        "concept_001": {},
        "concept_002": {},
        "concept_003": {},
        "concept_004": {},
        "concept_005": {}
    }
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "concept_009.md").write_text("content", encoding="utf-8")
    
    result = allocate_concept_id(registry=registry, wiki_dir=wiki_dir)
    assert result == "concept_010"


