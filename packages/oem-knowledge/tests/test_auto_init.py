import json
from pathlib import Path

import pytest
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.runner import _auto_recover_stale_session, _link_plugin
from oem_knowledge.runtime.session import SessionState


@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng


class TestAutoInitWizard:
    def test_is_initialized_returns_false_for_new_project(self, tmp_path):
        eng = KnowledgeEngine(project_path=tmp_path)
        assert not eng.is_initialized(str(tmp_path))

    def test_is_initialized_returns_true_after_init(self, engine, tmp_path):
        assert engine.is_initialized(str(tmp_path))

    def test_init_project_is_idempotent(self, engine, tmp_path):
        res1 = engine.init_project(str(tmp_path))
        res2 = engine.init_project(str(tmp_path))
        assert res1["status"] == "success"
        assert res2["status"] == "success"

    def test_is_initialized_false_in_empty_dir(self, tmp_path):
        empty = tmp_path / "empty_project"
        empty.mkdir()
        eng = KnowledgeEngine(project_path=empty)
        assert not eng.is_initialized(str(empty))

    def test_init_creates_state_dir(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        assert (harness / "state").is_dir()


class TestPluginHealthCheck:
    def test_health_returns_false_when_plugin_missing(self, tmp_path, monkeypatch):
        from oem_knowledge.adapters.opencode.adapter import OpenCodeAdapter
        eng = KnowledgeEngine(project_path=tmp_path)

        isolated_plugins = tmp_path / "isolated_plugins"
        isolated_plugins.mkdir()
        monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(isolated_plugins))

        adapter = OpenCodeAdapter(eng, str(tmp_path))
        healthy, msg = adapter.verify_health()
        assert not healthy
        assert "not found" in msg

    def test_health_returns_true_when_symlink_valid(self, tmp_path, monkeypatch):
        from oem_knowledge.adapters.opencode.adapter import OpenCodeAdapter
        eng = KnowledgeEngine(project_path=tmp_path)

        repo_root = tmp_path / "repo"
        plugin_src_dir = repo_root / "plugins"
        plugin_src_dir.mkdir(parents=True)
        plugin_src = plugin_src_dir / "openempiric.ts"
        plugin_src.write_text("export const plugin = {};")

        plugins_dir = tmp_path / "opencode_plugins"
        plugins_dir.mkdir()
        plugin_dest = plugins_dir / "openempiric.ts"
        plugin_dest.symlink_to(plugin_src)

        monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
        monkeypatch.setattr(
            "oem_knowledge.runtime.config._REPO_ROOT",
            repo_root,
        )

        adapter = OpenCodeAdapter(eng, str(tmp_path))
        healthy, msg = adapter.verify_health()
        assert healthy, msg

    def test_health_returns_false_when_symlink_broken(self, tmp_path, monkeypatch):
        from oem_knowledge.adapters.opencode.adapter import OpenCodeAdapter
        eng = KnowledgeEngine(project_path=tmp_path)

        repo_root = tmp_path / "repo"
        plugin_src_dir = repo_root / "plugins"
        plugin_src_dir.mkdir(parents=True)
        plugin_src = plugin_src_dir / "openempiric.ts"
        plugin_src.write_text("export const plugin = {};")

        plugins_dir = tmp_path / "opencode_plugins"
        plugins_dir.mkdir()
        plugin_dest = plugins_dir / "openempiric.ts"
        plugin_dest.symlink_to(tmp_path / "nonexistent.ts")

        monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
        monkeypatch.setattr(
            "oem_knowledge.runtime.config._REPO_ROOT",
            repo_root,
        )

        adapter = OpenCodeAdapter(eng, str(tmp_path))
        healthy, msg = adapter.verify_health()
        assert not healthy
        assert any(kw in msg for kw in ["broken", "not found", "wrong location"])

    def test_base_adapter_verify_health_delegates_to_mcp(self, tmp_path):
        from oem_knowledge.adapters.base import BaseAdapter
        eng = KnowledgeEngine(project_path=tmp_path)
        adapter = BaseAdapter(eng, str(tmp_path))
        healthy, msg = adapter.verify_health()
        assert not healthy


class TestAutoRecovery:
    def test_no_stale_session_does_nothing(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"
        if active_file.exists():
            active_file.unlink()
        _auto_recover_stale_session(engine, str(tmp_path))
        assert not active_file.exists()

    def test_recovery_with_chat_transcript(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"

        ss = SessionState.create(
            session_id="stale_001",
            agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "chat_stale.md"),
            context_path=str(tmp_path / "ctx.json"),
            temp_instructions=str(tmp_path / "inst.md"),
        )
        ss.status = "running"
        ss.save(active_file)

        chat_file = tmp_path / "chat_stale.md"
        chat_file.write_text("Fixed the parser module.")

        _auto_recover_stale_session(engine, str(tmp_path))

        assert not active_file.exists()
        outcomes_file = harness / "state" / "outcomes.jsonl"
        assert outcomes_file.exists()

    def test_recovery_with_no_transcript_records_abandoned(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"

        ss = SessionState.create(
            session_id="stale_002",
            agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "nonexistent.md"),
            context_path=str(tmp_path / "ctx.json"),
            temp_instructions=str(tmp_path / "inst.md"),
        )
        ss.status = "running"
        ss.save(active_file)

        _auto_recover_stale_session(engine, str(tmp_path))

        assert not active_file.exists()
        outcomes_file = harness / "state" / "outcomes.jsonl"
        assert outcomes_file.exists()
        last_line = outcomes_file.read_text().strip().splitlines()[-1]
        assert json.loads(last_line)["outcome"] == "abandoned"

    def test_completed_session_is_cleaned_up(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"

        ss = SessionState.create(
            session_id="done_001",
            agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "chat.md"),
            context_path=str(tmp_path / "ctx.json"),
            temp_instructions=str(tmp_path / "inst.md"),
        )
        ss.status = "completed"
        ss.save(active_file)

        _auto_recover_stale_session(engine, str(tmp_path))
        assert not active_file.exists()

    def test_recovery_with_fallback_transcript(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"

        ss = SessionState.create(
            session_id="stale_003",
            agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "nonexistent.md"),
            context_path=str(tmp_path / "ctx.json"),
            temp_instructions=str(tmp_path / "inst.md"),
        )
        ss.status = "running"
        ss.save(active_file)

        chat_path = harness / "state" / "chat_stale_003.md"
        chat_path.parent.mkdir(parents=True, exist_ok=True)
        chat_path.write_text("Refactored the entire module.")

        _auto_recover_stale_session(engine, str(tmp_path))

        assert not active_file.exists()
        outcomes_file = harness / "state" / "outcomes.jsonl"
        assert outcomes_file.exists()
        last_line = outcomes_file.read_text().strip().splitlines()[-1]
        assert json.loads(last_line)["outcome"] == "success"


class TestMemoryContext:
    def test_context_contains_memory_key(self, engine, tmp_path):
        from oem_knowledge.runtime.context import _compile_oem_context
        ctx = _compile_oem_context(engine)
        assert "memory_context" in ctx
        assert "project memory is already active" in ctx["memory_context"].lower()

    def test_context_mentions_retrieval_when_relevant(self, engine, tmp_path):
        from oem_knowledge.runtime.context import _compile_oem_context
        ctx = _compile_oem_context(engine)
        mc = ctx["memory_context"].lower()
        assert "do not assume work should proceed" in mc


class TestCorruptedDatabaseRecovery:
    def test_corrupted_database_auto_recovery(self, tmp_path):
        eng = KnowledgeEngine(project_path=tmp_path)
        harness = eng._resolve_harness(str(tmp_path))
        db_dir = harness / ".local_vector_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        
        # Write binary garbage to simulate a corrupted database
        garbage_file = db_dir / "vectors.db"
        garbage_file.write_bytes(b"\x00\xff\xee\xddgarbage_data" * 100)
        
        # Access vector_store, which should self-heal and return a healthy store
        store = eng.search.vector_store
        assert store is not None
        
        # The store should be accessible and count should be 0
        assert store.count() == 0

    def test_database_deletion_recovery_scenario(self, tmp_path):
        import shutil
        eng = KnowledgeEngine(project_path=tmp_path)
        harness = eng._resolve_harness(str(tmp_path))
        
        # 1. Create a dummy concept file
        wiki_dir = harness / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        concept_file = wiki_dir / "concept_test_rec.md"
        concept_file.write_text(
            "---\n"
            "concept_id: concept_test_rec\n"
            "canonical_name: database-recovery-concept\n"
            "status: validated\n"
            "---\n"
            "# Database Recovery Concept\n\n"
            "This is a specific queryable concept for testing database deletion recovery."
        )
        
        # Index it initially
        eng.search.index_all()
        
        # 2. Verify search works
        results_before = eng.search.search("database deletion recovery", k=1)
        assert len(results_before) == 1
        assert results_before[0]["metadata"]["title"] == "Database Recovery Concept"
        
        # 3. Delete .local_vector_db
        db_dir = harness / ".local_vector_db"
        shutil.rmtree(db_dir)
        
        # Reset the cached collections on eng
        eng.search._vector_store = None
        
        # 4 & 5. Search same concepts (should trigger auto-indexing and return the same result)
        results_after = eng.search.search("database deletion recovery", k=1)
        assert len(results_after) == 1
        assert results_after[0]["metadata"]["title"] == "Database Recovery Concept"
