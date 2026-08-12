"""Wave 5: dream re-index is budget-bounded and skippable."""


def _seed_registry(eng, project):
    registry = {}
    for i in (1, 2, 3):
        cid = f"concept_00{i}"
        registry[cid] = {
            "canonical_name": f"concept number {i}",
            "status": "validated",
            "confidence": 3,
            "evidence_count": 2,
            "session_count": 2,
        }
    eng.state._save_registry(registry, project)
    return registry


def test_dream_zero_budget_skips_index(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    _seed_registry(eng, str(tmp_path / "proj"))
    res = eng.dream(project=str(tmp_path / "proj"), index_budget_seconds=0)
    assert res.get("status") == "success"
    assert res["index"]["status"] == "skipped"


def test_dream_with_budget_runs_index(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    _seed_registry(eng, str(tmp_path / "proj"))
    eng.search.set_retrieval_mode("bm25")
    res = eng.dream(project=str(tmp_path / "proj"), index_budget_seconds=30)
    assert res.get("status") == "success"
    assert res["index"]["status"] in ("success", "partial")


def test_dream_default_keeps_unbounded_index(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    _seed_registry(eng, str(tmp_path / "proj"))
    eng.search.set_retrieval_mode("bm25")
    res = eng.dream(project=str(tmp_path / "proj"))
    assert res["index"]["status"] in ("success", "partial")


def test_session_end_auto_dream_skips_index_on_zero_budget(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    _seed_registry(eng, str(tmp_path / "proj"))
    harness = eng._resolve_harness(str(tmp_path / "proj"))
    cfg = harness / "config" / "reflection.yml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("reflection:\n  auto_dream:\n    enabled: true\n", encoding="utf-8")
    event = {
        "event_id": "evt-dream-1",
        "timestamp": "2026-07-01T10:00:00Z",
        "project": "proj",
        "session_id": "sess-dream",
        "event_type": "decision",
        "summary": "Decision: bounded dream indexing",
        "evidence": "Decision: bounded dream indexing",
        "source": "test",
    }
    res = eng.session_end(
        project=str(tmp_path / "proj"),
        conversation_text="",
        events=[event],
        update_index=False,
        index_budget_seconds=0,
    )
    dream = res.get("dream")
    assert dream is not None, "auto-dream must run when enabled"
    assert dream.get("index", {}).get("status") == "skipped"
