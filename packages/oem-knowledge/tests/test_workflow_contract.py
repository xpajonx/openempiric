from pathlib import Path

from oem_knowledge.engine import KnowledgeEngine, PhaseTimer
from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS
from oem_knowledge.tools.lifecycle import _decorate_session_result
from oem_knowledge.project import SESSION_TO_PROJECT


ROUTING = (
    "decision `required`",
    "decision `suggest`",
    "decision `noop`",
    "knowledge_add_memory` with concise content and evidence",
    "knowledge_session_end`; `knowledge_session_commit` is deprecated",
    "code evidence, not learned memory",
)


def test_instruction_copies_are_identical_and_route_workflow():
    root = Path(__file__).parents[3]
    copies = [
        (root / "instructions/memory-start.md").read_text().strip(),
        (root / "instructions/memory-start-agy.md").read_text().strip(),
        OEM_MEMORY_INSTRUCTIONS.strip(),
    ]
    assert copies[0] == copies[1] == copies[2]
    for body in copies:
        assert all(phrase in body for phrase in ROUTING)


def test_decorate_session_result_preserves_message_and_cleans_map(tmp_path):
    session_id = "workflow-contract"
    SESSION_TO_PROJECT[session_id] = tmp_path
    normal = {"status": "success", "message": "display", "custom": 1}
    result = _decorate_session_result(normal, tmp_path, session_id, "knowledge_session_end")
    assert result["message"] == "display"
    assert result["project_root"] == str(tmp_path)
    assert result["memory_root"] == str(tmp_path / ".oem")
    assert result["operation"] == "knowledge_session_end"
    assert session_id not in SESSION_TO_PROJECT

    deprecated = {"status": "partial", "message": "same", "warnings": []}
    result = _decorate_session_result(deprecated, tmp_path, session_id, "knowledge_session_commit", True)
    warning = "knowledge_session_commit is deprecated. Use knowledge_session_end instead."
    assert result["message"] == "same"
    assert result["deprecated"] is True
    assert result["warnings"].count(warning) == 1


def test_session_phase_timings_have_stable_keys_and_defaults():
    timer = PhaseTimer()
    timer.timings = {"reflection": 1.25, "total": 4.5}
    timings = KnowledgeEngine._build_session_phase_timings(timer)
    assert set(timings) == {
        "load_state", "reflection", "append_events", "materialization",
        "search_index", "write_report", "cleanup", "total",
    }
    assert timings["reflection"] == 1.25
    assert timings["total"] == 4.5
    assert all(timings[key] == 0.0 for key in set(timings) - {"reflection", "total"})