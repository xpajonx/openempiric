"""Wave 5: the compatibility reflect_session wrapper and canonical extraction agree."""


def test_reflect_session_matches_canonical_extraction(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    transcript = (
        "# OEM Session Transcript\n\n"
        "Decision: use SQLite for event storage.\n"
        "Failure: session_end hung when the embedding cache was broken.\n"
    )
    wrapped = eng.reflection.reflect_session(
        project=str(tmp_path / "proj"),
        conversation_text=transcript,
        session_id="sess-eq",
    )
    canonical = eng.reflection.extract_session_events(
        str(tmp_path / "proj"),
        transcript,
        session_id="sess-eq",
    )
    wrapped_events = wrapped.get("canonical_events", []) if isinstance(wrapped, dict) else []
    canon_events = canonical.get("canonical_events", []) if isinstance(canonical, dict) else []
    wrapped_set = {(e.get("event_type"), e.get("summary")) for e in wrapped_events if isinstance(e, dict)}
    canon_set = {(e.get("event_type"), e.get("summary")) for e in canon_events if isinstance(e, dict)}
    assert wrapped_set == canon_set, f"wrapped={wrapped_set} canonical={canon_set}"
