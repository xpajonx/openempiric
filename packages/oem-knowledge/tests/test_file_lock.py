import pytest
import shutil
import json
import socket
import os
from unittest.mock import patch
from pathlib import Path
from oem_knowledge.fs import FileLock, LockTimeoutError
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
        
        with pytest.raises(LockTimeoutError):
            with FileLock(lock_path, timeout=0.1) as lock2:
                # Should not be reached
                pass

def test_lock_timeout_not_silent(tmp_path):
    lock_path = tmp_path / "test.lock"
    entered = False
    
    with FileLock(lock_path) as lock1:
        assert lock1.acquired
        
        with pytest.raises(LockTimeoutError):
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
        
        # 2. Trying to read registry nested inside lock context should raise LockTimeoutError
        with pytest.raises(LockTimeoutError):
            engine.state._load_registry(str(temp_project))
            
        # 3. Trying to save registry nested inside lock context should raise LockTimeoutError
        with pytest.raises(LockTimeoutError):
            engine.state._save_registry({}, str(temp_project))

def test_event_append_respects_lock(temp_project):
    engine = KnowledgeEngine(temp_project)
    events_path = engine._events_path(str(temp_project))
    lock_path = events_path.with_suffix(".lock")
    
    # 1. Hold lock
    with FileLock(lock_path, timeout=1.0) as lock:
        assert lock.acquired
        
        # 2. Trying to load events nested inside lock context should raise LockTimeoutError
        with pytest.raises(LockTimeoutError):
            engine.state._load_events(str(temp_project))
            
        # 3. Trying to append event nested inside lock context should raise LockTimeoutError
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
        with pytest.raises(LockTimeoutError):
            engine.state._append_event(ev, str(temp_project))

def test_stale_lock_recovery(tmp_path):
    lock_path = tmp_path / "stale.lock"
    
    # Create the stale lock file manually, simulating a dead process on same host
    metadata = {
        "pid": 999999,  # Non-existent process ID
        "hostname": socket.gethostname(),
        "created_at": 1710000000.0,
        "owner_id": "dead-owner-id"
    }
    lock_path.write_text(json.dumps(metadata), encoding="utf-8")
    
    # Attempting to acquire should succeed automatically by recovering the stale lock
    with FileLock(lock_path, timeout=0.2, stale_timeout=0.1) as lock:
        assert lock.acquired
        # Verify the lock file contains our metadata now
        content = json.loads(lock_path.read_text(encoding="utf-8"))
        assert content["owner_id"] == lock.owner_id
        assert content["pid"] == os.getpid()

def test_lock_exit_does_not_delete_other_owner_lock(tmp_path):
    lock_path = tmp_path / "race.lock"
    
    with FileLock(lock_path) as lock:
        assert lock.acquired
        
        # Mutate lock file metadata to a different owner_id
        metadata = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": 1710000000.0,
            "owner_id": "some-other-owner-id"
        }
        lock_path.write_text(json.dumps(metadata), encoding="utf-8")
        
    # Exit original lock should NOT delete the lock file because owner_id doesn't match
    assert lock_path.exists()

def test_permission_error_pid_check_does_not_remove_lock(tmp_path):
    lock_path = tmp_path / "perm.lock"
    
    # Write lock metadata representing a process we don't own/can't signal
    metadata = {
        "pid": 1,  # Usually init/system process, signaling might raise PermissionError
        "hostname": socket.gethostname(),
        "created_at": 1710000000.0,
        "owner_id": "system-owner-id"
    }
    lock_path.write_text(json.dumps(metadata), encoding="utf-8")
    
    # Mock os.kill to raise PermissionError
    def mock_kill(pid, sig):
        raise PermissionError("Operation not permitted")
        
    with patch("os.kill", side_effect=mock_kill):
        # Trying to acquire the lock should fail/timeout since the process is considered alive
        with pytest.raises(LockTimeoutError):
            with FileLock(lock_path, timeout=0.2, stale_timeout=0.1):
                pass
                
    assert lock_path.exists()
