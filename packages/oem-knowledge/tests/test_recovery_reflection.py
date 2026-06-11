from __future__ import annotations

import json
import time
import uuid
import pytest
from pathlib import Path
from typing import Any

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.recovery import cmd_recover, parse_markdown_report, is_empty_orphan_session

def _init_oem(project: Path) -> Path:
    oem = project / ".oem"
    (oem / "sessions").mkdir(parents=True, exist_ok=True)
    (oem / "reports").mkdir(parents=True, exist_ok=True)
    (oem / "state").mkdir(parents=True, exist_ok=True)
    return oem

def _write_event(path: Path, **overrides) -> dict[str, Any]:
    event = {
        "event_id": overrides.get("event_id", str(uuid.uuid4())),
        "timestamp": overrides.get("timestamp", "2026-06-12T03:50:58Z"),
        "project": overrides.get("project", "test"),
        "session_id": overrides.get("session_id", "session_20260612_035058"),
        "event_type": overrides.get("event_type", "observation"),
        "concept_candidates": overrides.get("concept_candidates", ["General Learning"]),
        "summary": overrides.get("summary", "An observation summary"),
        "evidence": overrides.get("evidence", "log lines"),
        "confidence": overrides.get("confidence", 3),
        "source": overrides.get("source", "recovered_reflection"),
        "source_type": overrides.get("source_type", "recovered_event")
    }
    for k, v in overrides.items():
        if v is None and k not in ("project", "confidence"):
            event.pop(k, None)
        else:
            event[k] = v
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return event

def test_recover_reflection_dry_run_detects_empty_orphan_sessions(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    
    orphan_file = oem / "sessions" / "2026-06-12-orphan.md"
    orphan_file.write_text("""---
generated_by: openempiric
project: test
---
# Session Learning Report — 2026-06-12
""")
    
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", dry_run=True)
    
    assert str(orphan_file) in res["empty_orphan_files"]
    assert orphan_file.exists()

def test_recover_reflection_apply_removes_empty_orphan_sessions_with_backup(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    
    orphan_file = oem / "sessions" / "2026-06-12-orphan.md"
    orphan_file.write_text("""---
generated_by: openempiric
project: test
---
# Session Learning Report — 2026-06-12
""")
    
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=True)
    
    assert str(orphan_file) in res["empty_orphan_files"]
    assert not orphan_file.exists()
    assert res["backup_dir"] is not None
    backup_path = Path(res["backup_dir"])
    assert (backup_path / "sessions" / "2026-06-12-orphan.md").exists()

def test_recover_reflection_detects_invalid_jsonl_lines(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    
    _write_event(events_file, summary="valid")
    with events_file.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "abc"\n')
    
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", dry_run=True)
    
    assert res["invalid_jsonl_count"] == 1
    assert events_file.read_text(encoding="utf-8").count("\n") == 2

def test_recover_reflection_apply_quarantines_invalid_jsonl_lines(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    
    _write_event(events_file, summary="valid")
    with events_file.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "abc"\n')
        
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=True)
    
    assert res["invalid_jsonl_count"] == 1
    assert res["quarantine_file"] is not None
    
    quar_path = Path(res["quarantine_file"])
    assert quar_path.exists()
    quar_content = quar_path.read_text(encoding="utf-8")
    assert "events.jsonl:2" in quar_content
    assert '{"event_id": "abc"' in quar_content
    
    lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "valid" in lines[0]

def test_recover_reflection_normalizes_missing_event_metadata(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    
    with events_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "project": "test",
            "session_id": "session-1",
            "event_type": "observation",
            "concept_candidates": ["c1"],
            "summary": "incomplete",
            "evidence": "some evidence",
            "confidence": 1,
            "source": "manual"
        }) + "\n")
        
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=False)
    
    assert res["missing_metadata_count"] == 1
    
    repaired_lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(repaired_lines) == 1
    repaired = json.loads(repaired_lines[0])
    
    assert "event_id" in repaired
    assert "timestamp" in repaired
    assert repaired["source_type"] == "recovered_event"
    assert repaired["source"] == "recovered_reflection"
    assert repaired["recovered_by"] == "oem recover --scope reflection"
    assert repaired["recovery_reason"] == "missing event_id/timestamp/source_type"

def test_recover_reflection_deduplicates_exact_duplicate_events(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    
    e1 = _write_event(events_file, event_id="duplicate-id", summary="first")
    e2 = _write_event(events_file, event_id="duplicate-id", summary="first")
    
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=False)
    
    assert res["duplicate_events_count"] == 1
    lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

def test_recover_reflection_preserves_valid_jsonl(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    
    _write_event(events_file, event_id="id-1", summary="s1")
    _write_event(events_file, event_id="id-2", summary="s2")
    
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=False)
    
    assert res["invalid_jsonl_count"] == 0
    assert res["duplicate_events_count"] == 0
    lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

def test_recover_reflection_apply_creates_backup_before_mutation(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    _write_event(events_file, event_id="id-1", summary="s1")
    _write_event(events_file, event_id="id-1", summary="s1")
    
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=True)
    
    assert res["backup_dir"] is not None
    backup_path = Path(res["backup_dir"])
    assert backup_path.exists()
    assert (backup_path / "events.jsonl").exists()

def test_recover_reflection_no_apply_does_not_modify_files(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    _write_event(events_file, event_id="id-1", summary="s1")
    _write_event(events_file, event_id="id-1", summary="s1")
    
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", dry_run=True)
    
    assert res["duplicate_events_count"] == 1
    assert events_file.read_text(encoding="utf-8").count("\n") == 2

def test_recover_reflection_no_backup_without_apply_errors(tmp_path):
    project = tmp_path
    eng = KnowledgeEngine(project_path=str(project))
    
    with pytest.raises(SystemExit):
        cmd_recover(eng, project=str(project), scope="reflection", dry_run=True, backup=False)

def test_recover_reflection_does_not_touch_agents_md(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    agents_md = project / "AGENTS.md"
    agents_md.write_text("Agents list", encoding="utf-8")
    
    events_file = oem / "events.jsonl"
    _write_event(events_file, event_id="id-1", summary="s1")
    _write_event(events_file, event_id="id-1", summary="s1")
    
    eng = KnowledgeEngine(project_path=str(project))
    cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=False)
    
    assert agents_md.read_text(encoding="utf-8") == "Agents list"

def test_recover_reflection_does_not_touch_adapter_configs(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    config_file = project / "adapter_config.json"
    config_file.write_text("{}", encoding="utf-8")
    
    events_file = oem / "events.jsonl"
    _write_event(events_file, event_id="id-1", summary="s1")
    _write_event(events_file, event_id="id-1", summary="s1")
    
    eng = KnowledgeEngine(project_path=str(project))
    cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=False)
    
    assert config_file.read_text(encoding="utf-8") == "{}"

def test_recover_reflection_reports_session_discrepancies_without_rebuild(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    
    _write_event(events_file, session_id="session_20260612_035058", concept_candidates=["c1"], timestamp="2026-06-12T03:50:58Z")
    
    report_file = oem / "sessions" / "2026-06-12.md"
    report_file.write_text("""---
generated_by: openempiric
project: test
---
# Session Learning Report — 2026-06-12

## Knowledge Events
```json
{
  "knowledge_events": []
}
```
""", encoding="utf-8")
    
    eng = KnowledgeEngine(project_path=str(project))
    res = cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=False, rebuild_reports=False)
    
    assert str(report_file) in res["inconsistent_reports"]
    
    content = report_file.read_text(encoding="utf-8")
    assert "## Recovery Notes" in content
    assert "Event store has 1 events" in content

def test_recover_reflection_rebuild_reports_requires_explicit_flag(tmp_path):
    project = tmp_path
    oem = _init_oem(project)
    events_file = oem / "events.jsonl"
    _write_event(events_file, session_id="session_20260612_035058", concept_candidates=["c1"], timestamp="2026-06-12T03:50:58Z")
    
    report_file = oem / "sessions" / "2026-06-12.md"
    report_file.write_text("""---
generated_by: openempiric
project: test
---
# Session Learning Report — 2026-06-12

## Knowledge Events
```json
{
  "knowledge_events": []
}
```
""", encoding="utf-8")
    
    eng = KnowledgeEngine(project_path=str(project))
    
    cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=False, rebuild_reports=False)
    content = report_file.read_text(encoding="utf-8")
    assert '"knowledge_events": []' in content
    
    cmd_recover(eng, project=str(project), scope="reflection", apply=True, backup=False, rebuild_reports=True)
    content = report_file.read_text(encoding="utf-8")
    assert '"knowledge_events": [' in content
    assert '"type": "observation"' in content
