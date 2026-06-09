import pytest
import shutil
import json
from pathlib import Path
from oem_knowledge.fs import FileLock
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.models import KnowledgeEvent

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir
    if project_dir.exists():
        shutil.rmtree(project_dir)

def test_lock_acquire_release(tmp_path):
    lock_path = tmp_path / "test.lock"
    assert not lock_path.exists()
    
    with FileLock(lock_path) as lock:
        assert lock.acquired
        assert lock_path.exists()
        
    assert not lock_path.exists()

def test_lock_reacquisition_times_out(tmp_path):
    lock_path = tmp_path / "test.lock"
    
    with FileLock(lock_path) as lock1:
        assert lock1.acquired
        
        with pytest.raises(TimeoutError):
            with FileLock(lock_path, timeout=0.1) as lock2:
                # Should not be reached
                pass

def test_lock_timeout_not_silent(tmp_path):
    lock_path = tmp_path / "test.lock"
    entered = False
    
    with FileLock(lock_path) as lock1:
        assert lock1.acquired
        
        with pytest.raises(TimeoutError):
            with FileLock(lock_path, timeout=0.1):
                entered = True
                
    assert not entered

def test_registry_save_respects_lock(temp_project):
    engine = KnowledgeEngine(temp_project)
    reg_path = engine._registry_path(str(temp_project))
    lock_path = reg_path.with_suffix(".lock")
    
    # 1. Hold lock
    with FileLock(lock_path, timeout=1.0) as lock:
        assert lock.acquired
        
        # 2. Trying to read registry nested inside lock context should raise TimeoutError
        with pytest.raises(TimeoutError):
            engine.state._load_registry(str(temp_project))
            
        # 3. Trying to save registry nested inside lock context should raise TimeoutError
        with pytest.raises(TimeoutError):
            engine.state._save_registry({}, str(temp_project))

def test_event_append_respects_lock(temp_project):
    engine = KnowledgeEngine(temp_project)
    events_path = engine._events_path(str(temp_project))
    lock_path = events_path.with_suffix(".lock")
    
    # 1. Hold lock
    with FileLock(lock_path, timeout=1.0) as lock:
        assert lock.acquired
        
        # 2. Trying to load events nested inside lock context should raise TimeoutError
        with pytest.raises(TimeoutError):
            engine.state._load_events(str(temp_project))
            
        # 3. Trying to append event nested inside lock context should raise TimeoutError
        ev = KnowledgeEvent(
            event_id="evt_001",
            timestamp="2026-06-09T00:00:00Z",
            project="test_project",
            session_id="session_1",
            event_type="observation",
            summary="test observation",
            evidence="Some evidence",
            source="test"
        )
        with pytest.raises(TimeoutError):
            engine.state._append_event(ev, str(temp_project))

@pytest.mark.xfail(reason="Current FileLock has no stale lock recovery; fixed in CRIT-02B")
def test_stale_lock_recovery(tmp_path):
    lock_path = tmp_path / "stale.lock"
    # Create the stale lock file manually (simulating process crash/cleanup failure)
    lock_path.write_text("dummy stale lock metadata")
    
    # Attempting to acquire should fail/time out under current implementation
    with FileLock(lock_path, timeout=0.1) as lock:
        # We expect this to fail and raise TimeoutError, so if it does succeed, it's a failure under current impl
        assert lock.acquired
