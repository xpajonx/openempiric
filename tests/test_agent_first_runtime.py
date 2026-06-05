from __future__ import annotations
import json
import tempfile
import shutil
from pathlib import Path
import pytest

from oem_knowledge.engine import KnowledgeEngine, OEM_DIR
from oem_knowledge.runtime.context import _compile_oem_context

@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)

def test_runtime_already_active(tmp_proj):
    # Test 1: Validate that the compiled context implies the runtime is active
    # and does not prompt manual lifecycle commands/initialization.
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    
    context = _compile_oem_context(eng)
    memory_ctx = context["memory_context"]
    
    assert "OpenEmpiric is already active for this session" in memory_ctx
    
    # Must not contain lifecycle hooks or initialization/activation commands
    forbidden = ["knowledge_session_start", "knowledge_session_commit", "initialize oem", "activate oem"]
    for phrase in forbidden:
        assert phrase not in memory_ctx.lower()

def test_memory_is_context_not_instruction(tmp_proj):
    # Test 2: Validate that memory context behaves as historical context,
    # not task instructions.
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    
    # Seed session goals
    goals_file = Path(tmp_proj) / OEM_DIR / "state" / "current-goals.md"
    goals_file.parent.mkdir(parents=True, exist_ok=True)
    goals_file.write_text("- realistic-image-gen\n- Validate claims #6-9\n", encoding="utf-8")
    
    context = _compile_oem_context(eng)
    
    # Check that goals/handoff are parsed under topic/open questions (as historical context)
    assert context["last_topic"] == "realistic-image-gen"
    assert "Validate claims #6-9" in context["open_questions"]
    
    # Verify that the generated memory context contains "historical context"
    memory_ctx = context["memory_context"]
    assert "historical context" in memory_ctx.lower()
    
    # Verify that the generated memory context does not use task steering vocabulary
    # like "Next Actions", "Continue Work", "Resume Phase", or "Queued Tasks".
    steering_words = ["next actions", "continue work", "resume phase", "queued tasks"]
    for word in steering_words:
        assert word not in memory_ctx.lower()

def test_no_task_steering(tmp_proj):
    # Test 3: Validate that the generated memory context does not contain
    # steering keywords (continue, resume, execute, next step).
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    
    context = _compile_oem_context(eng)
    memory_ctx = context["memory_context"]
    
    steering_words = ["continue", "resume", "execute", "next step"]
    for word in steering_words:
        # Check they don't appear in the core instructions/memory_context
        assert word not in memory_ctx.lower()

def test_search_is_optional(tmp_proj):
    # Test 4: Verify search is presented as optional context retrieval
    # and does not direct the agent to call it immediately.
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    
    context = _compile_oem_context(eng)
    memory_ctx = context["memory_context"]
    
    # Expected: "Use knowledge_search when you need additional historical context."
    # which is optional, and not "Call knowledge_search immediately" or "Run knowledge_search"
    assert "use knowledge_search" in memory_ctx.lower()
    assert "when you need additional historical context" in memory_ctx.lower()
    assert "immediately" not in memory_ctx.lower()
