import pytest
import shutil
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from oem_knowledge.cli.parser import _setup_parser
from oem_knowledge.cli.commands.knowledge import run_knowledge_command
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.project import resolve_active_project, handle_resolution_error

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    
    # Create a couple of sample files to index inside valid directories
    src_dir = project_dir / "src"
    src_dir.mkdir()
    docs_dir = project_dir / "docs"
    docs_dir.mkdir()
    
    file1 = src_dir / "src_test_file.py"
    file1.write_text("def test_function():\n    return 'hello world'\n", encoding="utf-8")
    
    file2 = docs_dir / "docs_test_file.md"
    file2.write_text("# Documentation\nThis is a sample document for testing.\n", encoding="utf-8")
    
    yield project_dir
    shutil.rmtree(project_dir)

def test_cli_source_index_dry_run(temp_project, capsys):
    parser = _setup_parser()
    args = parser.parse_args(["source", "index", "--project", str(temp_project), "--dry-run"])
    run_knowledge_command(args)
    captured = capsys.readouterr()
    assert "Source Indexing (Dry Run)" in captured.out
    assert "Operation:         DRY_RUN" in captured.out
    assert "Scanned Files:     2" in captured.out

def test_cli_source_index_and_stats_and_search_and_read(temp_project, capsys):
    parser = _setup_parser()
    
    # 1. Run index
    args_index = parser.parse_args(["source", "index", "--project", str(temp_project)])
    run_knowledge_command(args_index)
    captured = capsys.readouterr()
    assert "Source Indexing Complete" in captured.out
    assert "Operation:         WRITE" in captured.out
    assert "Scanned Files:     2" in captured.out

    # 2. Run stats
    args_stats = parser.parse_args(["source", "stats", "--project", str(temp_project)])
    run_knowledge_command(args_stats)
    captured = capsys.readouterr()
    assert "Source Stats" in captured.out
    assert "Total Chunks:" in captured.out
    assert "Database Size:" in captured.out

    # 3. Run search
    args_search = parser.parse_args(["source", "search", "test_function", "--project", str(temp_project)])
    run_knowledge_command(args_search)
    captured = capsys.readouterr()
    assert "Source Search Results" in captured.out
    assert "src/src_test_file.py" in captured.out

    # 4. Run read
    args_read = parser.parse_args(["source", "read", "src/src_test_file.py", "--start-line", "1", "--end-line", "2", "--project", str(temp_project)])
    run_knowledge_command(args_read)
    captured = capsys.readouterr()
    assert "Source Content" in captured.out
    assert "def test_function():" in captured.out

def test_mcp_source_tools(temp_project):
    import asyncio
    from fastmcp import FastMCP
    from oem_knowledge.server import mount_tools
    
    mcp = FastMCP("test_openempiric")
    mount_tools(mcp)
    
    # Make sure tools are registered
    tools = asyncio.run(mcp.list_tools())
    search_tool = next((t for t in tools if t.name == "knowledge_source_search"), None)
    read_tool = next((t for t in tools if t.name == "knowledge_source_read"), None)
    
    assert search_tool is not None
    assert read_tool is not None
    
    # 1. Index the project first
    with KnowledgeEngine(str(temp_project)) as eng:
        eng.source.index()
    
    # 2. Test knowledge_source_search via the registered tool function
    search_fn = search_tool.fn
    search_res_json = search_fn(query="test_function", project=str(temp_project))
    search_res = json.loads(search_res_json)
    
    assert search_res["status"] == "success"
    assert search_res["operation"] == "knowledge_source_search"
    assert len(search_res["results"]) > 0
    assert search_res["results"][0]["metadata"]["rel_path"] == "src/src_test_file.py"
    
    # 3. Test knowledge_source_read via the registered tool function
    read_fn = read_tool.fn
    read_res_json = read_fn(path="src/src_test_file.py", start_line=1, end_line=2, project=str(temp_project))
    read_res = json.loads(read_res_json)
    
    assert read_res["status"] == "success"
    assert read_res["operation"] == "knowledge_source_read"
    assert "def test_function():" in read_res["content"]


class _SourceTestModel:
    def __init__(self, dimension=4, fail=False):
        self.dimension = dimension
        self.fail = fail
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("fake_embedding_failure")
        return [[float(index + 1)] * self.dimension for index, _ in enumerate(texts)]


class _SemanticSourceTestModel:
    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "semantic target" in lowered or "get_target" in lowered or "semantic query" in lowered:
                vectors.append([1.0, 0.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 1.0, 0.0, 0.0])
        return vectors


class _WrongDimensionModel:
    dimension = 4

    def embed(self, texts):
        return [[0.0] * (self.dimension + 1 if index == 0 else self.dimension) for index, _ in enumerate(texts)]


class _NonFiniteModel:
    dimension = 4

    def embed(self, texts):
        return [[float("nan")] * self.dimension for _ in texts]


class _EmptyVectorModel:
    dimension = 4

    def embed(self, texts):
        return [[] for _ in texts]


def _hybrid_engine(project, model):
    engine = KnowledgeEngine(project)
    engine.init_project(str(project))
    engine.search.set_retrieval_mode("hybrid")
    engine._model = model
    engine._local_load_failed = False
    return engine


def _write_source_file(project, name, content):
    path = project / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_source_embeddings_use_separate_store(tmp_path):
    _write_source_file(tmp_path, "target.py", "def target():\n    return 1\n")
    model = _SourceTestModel()
    with _hybrid_engine(tmp_path, model) as engine:
        stats = engine.source.index()
        assert stats["status"] == "success"
        assert stats["embedding_status"] == "ready"
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert "source_embeddings" in tables
            assert connection.execute("SELECT COUNT(*) FROM source_embeddings").fetchone()[0] > 0
        assert engine.search.vector_store.all_chunks() == []


def test_source_embedding_generation_and_content_invalidation(tmp_path):
    source = _write_source_file(tmp_path, "target.py", "def target():\n    return 1\n")
    model_a = _SourceTestModel(dimension=4)
    with _hybrid_engine(tmp_path, model_a) as engine:
        engine.source.index()
        first_call_count = len(model_a.calls)
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            first_rows = connection.execute(
                "SELECT embedding_model, embedding_dimension, content_hash "
                "FROM source_embeddings"
            ).fetchall()
        engine.source.index()
        assert len(model_a.calls) == first_call_count + 1
        source.write_text("def target():\n    return 2\n", encoding="utf-8")
        engine.source.index()
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            current_hash = connection.execute(
                "SELECT content_hash FROM source_chunks WHERE rel_path = 'src/target.py'"
            ).fetchone()[0]
            assert connection.execute(
                "SELECT COUNT(*) FROM source_embeddings WHERE content_hash = ?",
                (current_hash,),
            ).fetchone()[0] == 1
        engine.config_embedding_set_model("test/model-b")
        model_b = _SourceTestModel(dimension=3)
        engine._model = model_b
        engine._local_load_failed = False
        engine.source.index()
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            generations = set(
                connection.execute(
                    "SELECT embedding_model, embedding_dimension FROM source_embeddings"
                ).fetchall()
            )
        assert (first_rows[0][0], first_rows[0][1]) in generations
        assert ("test/model-b", 3) in generations
        manifest = json.loads(engine.layout().source_manifest_path.read_text(encoding="utf-8"))
        assert manifest["embedding"]["model"] == "test/model-b"
        assert manifest["embedding"]["dimension"] == 3


def test_source_embedding_failure_keeps_bm25_and_writes_no_partial_rows(tmp_path):
    _write_source_file(tmp_path, "target.py", "def target():\n    return 1\n")
    with _hybrid_engine(tmp_path, _SourceTestModel(fail=True)) as engine:
        stats = engine.source.index()
        assert stats["status"] == "success"
        assert stats["embedding_status"] == "bm25_fallback"
        assert any(item.startswith("source_embedding_fallback:") for item in stats["warnings"])
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM source_embeddings").fetchone()[0] == 0


def _assert_malformed_embedding_batch_falls_back_to_bm25(tmp_path, model):
    _write_source_file(tmp_path, "target.py", "def target():\n    return 1\n")
    _write_source_file(tmp_path, "second.py", "def second():\n    return 2\n")
    with _hybrid_engine(tmp_path, model) as engine:
        stats = engine.source.index()
        assert stats["embedding_status"] == "bm25_fallback"
        assert any(item.startswith("source_embedding_fallback:") for item in stats["warnings"])
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM source_embeddings").fetchone()[0] == 0


def test_wrong_dimension_batch_falls_back_to_bm25(tmp_path):
    _assert_malformed_embedding_batch_falls_back_to_bm25(tmp_path, _WrongDimensionModel())


def test_non_finite_embedding_falls_back_to_bm25(tmp_path):
    _assert_malformed_embedding_batch_falls_back_to_bm25(tmp_path, _NonFiniteModel())


def test_empty_vector_embedding_falls_back_to_bm25(tmp_path):
    _assert_malformed_embedding_batch_falls_back_to_bm25(tmp_path, _EmptyVectorModel())


def test_corrupted_stored_embedding_falls_back_to_bm25(tmp_path):
    _write_source_file(tmp_path, "target.py", "def target():\n    return 1\n")
    with _hybrid_engine(tmp_path, _SourceTestModel()) as engine:
        assert engine.source.index()["embedding_status"] == "ready"
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            before_count = connection.execute(
                "SELECT COUNT(*) FROM source_embeddings"
            ).fetchone()[0]
            connection.execute("UPDATE source_embeddings SET embedding = 'NOT_VALID_JSON'")
            connection.commit()
        stats = engine.source.index()
        assert stats["embedding_status"] == "bm25_fallback"
        assert any(item.startswith("source_embedding_fallback:") for item in stats["warnings"])
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM source_embeddings").fetchone()[0] == before_count


def test_manifest_os_replace_failure_preserves_previous(tmp_path):
    _write_source_file(tmp_path, "target.py", "def target():\n    return 1\n")
    with _hybrid_engine(tmp_path, _SourceTestModel()) as engine:
        first = engine.source.index()
        manifest_path = engine.layout().source_manifest_path
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        with patch(
            "oem_knowledge.services.source_corpus.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with pytest.raises(OSError, match="replace failed"):
                engine.source.index()
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert first["embedding_status"] == "ready"
        assert current["embedding"]["status"] == previous["embedding"]["status"]


def test_hybrid_source_search_preserves_exact_and_adds_dense_diagnostics(tmp_path):
    _write_source_file(
        tmp_path,
        "target.py",
        "def get_target():\n    # semantic target\n    return 1\n",
    )
    _write_source_file(tmp_path, "decoy.py", "def unrelated():\n    # semantic decoy\n    return 0\n")
    with _hybrid_engine(tmp_path, _SemanticSourceTestModel()) as engine:
        stats = engine.source.index()
        assert stats["embedding_status"] == "ready"
        semantic = engine.source.search("semantic query", k=2)
        assert semantic["status"] == "success"
        assert semantic["results"][0]["metadata"]["rel_path"] == "src/target.py"
        diagnostics = semantic["results"][0]["metadata"]["source_diagnostics"]
        assert diagnostics["retrieval_mode"] == "hybrid"
        assert diagnostics["dense_available"] is True
        assert diagnostics["dense_score"] > 0.25
        exact = engine.source.search("get_target", k=2)
        assert exact["status"] == "success"
        assert exact["results"][0]["metadata"]["rel_path"] == "src/target.py"


def test_hybrid_source_search_falls_back_without_downloading(tmp_path):
    _write_source_file(tmp_path, "target.py", "def get_target():\n    return 1\n")
    with _hybrid_engine(tmp_path, _SourceTestModel()) as engine:
        engine.source.index()
        with patch.object(engine.search, "embed", side_effect=RuntimeError("query_embedding_failure")):
            with patch.object(engine, "_download_model", side_effect=AssertionError("download forbidden")):
                result = engine.source.search("get_target", k=1)
        assert result["status"] == "success"
        assert result["warnings"] == ["source_dense_fallback:embedding_unavailable"]
        diagnostics = result["results"][0]["metadata"]["source_diagnostics"]
        assert diagnostics["dense_available"] is False
        assert diagnostics["dense_fallback_reason"] == "embedding_unavailable"


def test_removed_source_files_remove_all_embedding_generations(tmp_path):
    source = _write_source_file(tmp_path, "target.py", "def target():\n    return 1\n")
    with _hybrid_engine(tmp_path, _SourceTestModel()) as engine:
        engine.source.index()
        source.unlink()
        engine.source.index()
        with sqlite3.connect(engine.layout().source_index_db_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM source_embeddings").fetchone()[0] == 0
