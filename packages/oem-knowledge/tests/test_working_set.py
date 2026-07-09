import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.working_set import (
    WorkingSet,
    load_working_set,
    save_working_set,
    update_working_set,
    merge_working_set,
)
from oem_knowledge.health import build_health_report
from oem_knowledge.cli import main

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng

def test_working_set_create(engine, tmp_path):
    ws = update_working_set(project=str(tmp_path), goal="My goal")
    assert ws.goal == "My goal"
    assert ws.schema_version == 1
    assert ws.workspace_root == str(tmp_path.resolve())
    
    # Verify file was written
    path = engine.layout().working_set_path
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["goal"] == "My goal"
    assert data["schema_version"] == 1
    assert data["workspace_root"] == str(tmp_path.resolve())

def test_working_set_roundtrip(engine, tmp_path):
    ws = WorkingSet(
        workspace_root=str(tmp_path),
        goal="Test goal",
        current_problem="Problem description",
        active_files=["file1.py", "file2.py"],
    )
    save_working_set(ws, project=str(tmp_path))
    
    loaded = load_working_set(project=str(tmp_path))
    assert loaded is not None
    assert loaded.goal == "Test goal"
    assert loaded.current_problem == "Problem description"
    assert loaded.active_files == ["file1.py", "file2.py"]

def test_working_set_atomic_write(engine, tmp_path):
    ws = WorkingSet(workspace_root=str(tmp_path), goal="atomic test")
    path = engine.layout().working_set_path
    
    orig_replace = Path.replace
    replace_called = []
    
    def mock_replace(self, target):
        replace_called.append((self, target))
        return orig_replace(self, target)
        
    with patch.object(Path, "replace", mock_replace):
        save_working_set(ws, project=str(tmp_path))
        
    assert len(replace_called) == 1
    src, target = replace_called[0]
    assert target.resolve() == path.resolve()
    assert src.name.startswith(".working_set.json.")
    assert src.name.endswith(".tmp")

def test_working_set_health_visible(engine, tmp_path):
    # No working set exists initially
    report_empty = build_health_report(str(tmp_path), include_daemon_runtime=False)
    assert "working_set" in report_empty
    assert report_empty["working_set"]["exists"] is False
    assert report_empty["working_set"]["active_files_count"] == 0

    # Write a working set
    update_working_set(
        project=str(tmp_path),
        active_work_item="work-1",
        active_files=["src/foo.py", "src/bar.py"],
        active_concepts=["concept_a"],
    )
    
    report_active = build_health_report(str(tmp_path), include_daemon_runtime=False)
    assert report_active["working_set"]["exists"] is True
    assert report_active["working_set"]["active_work_item"] == "work-1"
    assert report_active["working_set"]["active_files_count"] == 2
    assert report_active["working_set"]["active_concepts_count"] == 1

def test_working_set_show_cli(engine, tmp_path):
    # CLI show should fail if working set does not exist
    with patch.object(sys, "argv", ["oem", "working-set", "show", "--project", str(tmp_path)]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    # Now create one
    update_working_set(project=str(tmp_path), goal="CLI goal")
    
    # Run show in text mode
    with patch.object(sys, "argv", ["oem", "working-set", "show", "--project", str(tmp_path)]):
        with patch("builtins.print") as mock_print:
            main()
        
        output = "\n".join(" ".join(str(arg) for arg in call[0]) for call in mock_print.call_args_list)
        assert "CLI goal" in output
        assert "OEM Working Set" in output

    # Run show in JSON mode
    with patch.object(sys, "argv", ["oem", "working-set", "show", "--project", str(tmp_path), "--json"]):
        with patch("builtins.print") as mock_print:
            main()
        
        output = "\n".join(" ".join(str(arg) for arg in call[0]) for call in mock_print.call_args_list)
        data = json.loads(output)
        assert data["goal"] == "CLI goal"
        assert data["schema_version"] == 1

def test_working_set_corruption(engine, tmp_path):
    path = engine.layout().working_set_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupted_json", encoding="utf-8")
    
    loaded = load_working_set(project=str(tmp_path))
    assert loaded is None

def test_working_set_migration(engine, tmp_path):
    path = engine.layout().working_set_path
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_data = {
        "version": 1,
        "workspace_root": str(tmp_path),
        "goal": "legacy version test"
    }
    path.write_text(json.dumps(legacy_data), encoding="utf-8")
    
    loaded = load_working_set(project=str(tmp_path))
    assert loaded is not None
    assert loaded.schema_version == 1
    assert loaded.goal == "legacy version test"

def test_working_set_merge(engine, tmp_path):
    update_working_set(
        project=str(tmp_path),
        goal="original goal",
        active_files=["a.py"],
    )
    merged = merge_working_set(
        project=str(tmp_path),
        goal="merged goal",
        active_files=["b.py"],
    )
    assert merged.goal == "merged goal"
    assert merged.active_files == ["a.py", "b.py"]
