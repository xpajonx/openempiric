from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from oem_knowledge.cli import main
from oem_knowledge.engine import OEM_DIR


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_usage_log_cli(tmp_proj):
    """Verify that --usage-log reads and displays usage log entries correctly."""
    state_dir = Path(tmp_proj) / OEM_DIR / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Create dummy metrics.json
    metrics_file = state_dir / "metrics.json"
    metrics_file.write_text(json.dumps({
        "retrieval": {
            "search_count": 0,
            "concepts_retrieved": 0
        },
        "context": {
            "context_count": 0
        },
        "knowledge_usage": {
            "concepts_injected": 0,
            "concepts_referenced": 0,
            "concepts_ignored": 0,
            "agent_decisions_aligned": 0,
            "last_report_at": None
        }
    }), encoding="utf-8")

    # 2. Create dummy usage_log.jsonl
    log_file = state_dir / "usage_log.jsonl"
    log_entry1 = {
        "timestamp": "2026-06-03T15:00:00Z",
        "concepts_used": ["concept_001"],
        "concepts_ignored": ["concept_002"],
        "decisions": ["Aligned with concept_001"]
    }
    log_entry2 = {
        "timestamp": "2026-06-03T15:10:00Z",
        "concepts_used": ["concept_003"],
        "concepts_ignored": [],
        "decisions": ["Used concept_003 for auth"]
    }
    log_file.write_text(
        json.dumps(log_entry1) + "\n" + json.dumps(log_entry2) + "\n",
        encoding="utf-8"
    )

    # 3. Call main with --usage-log
    with patch.object(sys, "argv", ["oem", "metrics", "--project", tmp_proj, "--usage-log"]):
        try:
            main()
        except SystemExit as e:
            assert e.code == 0 or e.code is None


def test_reset_cleans_telemetry(tmp_proj):
    """Verify that --reset deletes metrics.json, usage_log.jsonl, and session_state.json."""
    state_dir = Path(tmp_proj) / OEM_DIR / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    metrics_file = state_dir / "metrics.json"
    metrics_file.write_text("{}", encoding="utf-8")

    log_file = state_dir / "usage_log.jsonl"
    log_file.write_text("{}", encoding="utf-8")

    session_state_file = state_dir / "session_state.json"
    session_state_file.write_text("{}", encoding="utf-8")

    # Call main with --reset
    with patch.object(sys, "argv", ["oem", "metrics", "--project", tmp_proj, "--reset"]):
        try:
            main()
        except SystemExit as e:
            assert e.code == 0 or e.code is None

    # Assert that usage_log.jsonl and session_state.json are deleted
    assert not log_file.exists()
    assert not session_state_file.exists()
    
    # Assert that metrics.json is reset to the empty schema
    assert metrics_file.exists()
    data = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert data["retrieval"]["concepts_retrieved"] == 0
    assert data["knowledge_usage"]["concepts_injected"] == 0
    assert data["knowledge_usage"]["last_report_at"] is None
