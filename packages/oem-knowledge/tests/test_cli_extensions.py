import json
import subprocess
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine


def test_schema_sync_guard():
    # Verify generate_ts_types.py produces the exact content on disk (avoiding drift)
    import sys
    from pathlib import Path
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from generate_ts_types import generate_ts
        expected_ts = generate_ts()
    finally:
        if sys.path[0] == str(scripts_dir):
            sys.path.pop(0)

    schema_path = Path(__file__).resolve().parent.parent / "src" / "oem_knowledge" / "plugins" / "generated" / "schemas.ts"
    assert schema_path.exists(), "TypeScript schema file does not exist!"

    actual_ts = schema_path.read_text(encoding="utf-8")
    assert actual_ts == expected_ts, (
        "TypeScript schemas are out of sync with Pydantic models!\n"
        "Run: uv run python scripts/generate_ts_types.py to regenerate them."
    )


def test_todo_cli_commands(tmp_path):
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test_todo_cli_proj")

    from oem_knowledge.tools.todos import oem_todo_read, oem_todo_write, oem_todo_advance

    # 1. Write todos
    items_json = json.dumps([
        {"content": "First Task", "status": "pending"},
        {"content": "Second Task", "status": "in_progress"}
    ])
    write_res = oem_todo_write(items_json, str(tmp_path))
    assert "Todo list updated (2 items)" in write_res

    # 2. Read todos
    read_res = oem_todo_read(str(tmp_path))
    assert "First Task" in read_res
    assert "Second Task" in read_res

    # Find the ID of the first task to advance it
    todos_data = json.loads((tmp_path / ".oem" / "state" / "todos.json").read_text())
    first_id = todos_data[0]["id"]

    # 3. Advance todo
    adv_res = oem_todo_advance(first_id, "completed", str(tmp_path))
    assert "First Task" in adv_res
    assert "completed" in adv_res


def test_metrics_report_cli(tmp_path):
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test_metrics_cli_proj")

    from oem_knowledge.tools.metrics import report_usage

    # Write a dummy session state first to simulate injection
    session_state_path = tmp_path / ".oem" / "state" / "session_state.json"
    session_state_path.parent.mkdir(parents=True, exist_ok=True)
    session_state_path.write_text(json.dumps({
        "last_injected_concepts": ["concept_001", "concept_002"]
    }))

    # Report usage
    report_res = report_usage(
        concepts_used=["concept_001"],
        concepts_ignored=None,
        decisions=["Aligned layout decisions"],
        project=str(tmp_path)
    )

    assert "Report Timestamp" in report_res
    assert "concept_001" in report_res

    # Verify metrics file on disk
    metrics_file = tmp_path / ".oem" / "state" / "metrics.json"
    assert metrics_file.exists()
    metrics_data = json.loads(metrics_file.read_text())
    assert metrics_data["knowledge_usage"]["concepts_referenced"] == 1
    assert metrics_data["knowledge_usage"]["concepts_ignored"] == 1
    assert metrics_data["knowledge_usage"]["agent_decisions_aligned"] == 1


def test_cli_engine_lifecycle_repetition(tmp_path):
    """CLI-style engine creation/usage/close repeated 10 times should not leak."""
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test-cli-lifecycle")
    wiki_dir = engine._concepts_dir(str(tmp_path))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "cli_test.md").write_text("# CLI Test\n\nBody")
    engine.search.index_all(force=True)
    engine.close()

    for i in range(10):
        eng = KnowledgeEngine(tmp_path)
        try:
            results = eng.search.search("test", k=3)
            assert isinstance(results, list)
        finally:
            eng.close()

    # DB remains usable after repeated CLI-style open/close
    eng = KnowledgeEngine(tmp_path)
    try:
        chunks = eng.search.vector_store.all_chunks()
        assert len(chunks) > 0
        eng.search.vector_store.upsert(
            "cli-after-repeat",
            "CLI repetition test",
            {"source": "cli"},
            [0.5],
        )
    finally:
        eng.close()
