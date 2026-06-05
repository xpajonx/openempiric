import json
import logging
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.runner import run_agent, _compile_oem_context
from oem_knowledge.runtime.session import SessionState

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng

def test_context_compile_fallback(engine, tmp_path):
    # Mock _compile_oem_context to fail
    with patch("oem_knowledge.runtime.runner._compile_oem_context", side_effect=RuntimeError("Chroma/Compilation DB error")):
        mock_adapter = MagicMock()
        mock_adapter.verify_health.return_value = (True, "OK")
        mock_adapter.verify_mcp.return_value = True
        mock_adapter.get_expected_transcript_path.return_value = Path(tmp_path / "chat.md")

        with patch("subprocess.run") as mock_run:
            with patch("builtins.print") as mock_print:
                # Should not raise exception
                run_agent("opencode", engine, str(tmp_path))
                assert mock_run.called

                # Warnings should have been collected and printed
                printed_warning = False
                for call in mock_print.call_args_list:
                    args = call[0]
                    if args and any("degraded functionality" in str(arg) for arg in args):
                        printed_warning = True
                assert printed_warning

def test_search_fallback_registry_only(engine, tmp_path):
    # Register a concept
    harness = engine._resolve_harness(str(tmp_path))
    registry_file = harness / "concept_registry.json"
    
    registry = {
        "concept_001": {
            "canonical_name": "Resilient Fallback Design",
            "aliases": ["degradation", "resilience"],
            "status": "validated",
            "confidence": 4,
            "evidence_count": 2,
            "session_count": 1
        }
    }
    registry_file.write_text(json.dumps(registry), encoding="utf-8")
    
    # Write wiki doc
    wiki_dir = harness / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    wiki_doc = wiki_dir / "concept_001.md"
    wiki_doc.write_text("# Resilient Fallback Design\n\nThis is a fallback description for gracefully handling errors.\n", encoding="utf-8")

    # Mock collection to raise when queries are run
    mock_col = MagicMock()
    mock_col.count.side_effect = RuntimeError("ChromaDB connection refused")
    mock_col.query.side_effect = RuntimeError("ChromaDB connection refused")
    engine._collection = mock_col

    # 1. Verify search fallback returns matching results
    results = engine.search("degradation", k=1)
    assert len(results) == 1
    assert "concept_001" in results[0]["id"]
    assert "gracefully handling errors" in results[0]["document"]

    # 2. Verify stats fallback returns 0 chunks and doesn't crash
    stats = engine.search_service.stats()
    assert stats["total_chunks"] == 0
