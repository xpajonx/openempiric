"""Tests for the knowledge_add_memory lifecycle tool."""

import json
import pytest


class TestKnowledgeAddMemory:
    """Tests for the knowledge_add_memory MCP tool."""

    def test_inline_memory_creation_succeeds(self, tmp_path):
        """Inline memory creation returns success and an event ID."""
        from oem_knowledge.services.reflection import ReflectionService

        svc = ReflectionService()
        result = svc.add_inline_memory(
            memory_type="decision",
            content="Decided to use uv over pip for package management",
            scope="project",
            confidence=4,
            evidence="uv is faster than pip by 10-100x per benchmarks from astral.sh",
        )
        assert result["status"] == "success"
        assert result["event_id"] is not None
        assert result["auto_accepted"] is True

    def test_inline_memory_deduplication(self):
        """Duplicate content produces duplicate status not duplicate event."""
        from oem_knowledge.services.reflection import ReflectionService

        svc = ReflectionService()
        content = "Use SQLite for local caching"
        evidence = "SQLite is embedded, serverless, and transactional"

        r1 = svc.add_inline_memory(
            memory_type="decision", content=content,
            scope="project", confidence=4, evidence=evidence,
        )
        r2 = svc.add_inline_memory(
            memory_type="decision", content=content,
            scope="project", confidence=4, evidence=evidence,
        )

        assert r1["status"] == "success"
        assert r2["status"] in ("duplicate", "already_added") or r2.get("status") == "duplicate"

    def test_low_confidence_flagged(self):
        """Entries with confidence < 3 are stored but flagged."""
        from oem_knowledge.services.reflection import ReflectionService

        svc = ReflectionService()
        result = svc.add_inline_memory(
            memory_type="observation",
            content="Might want to try async here",
            scope="project",
            confidence=2,
            evidence="Not sure if this will help but worth noting",
        )
        assert result["status"] == "success"
        assert result.get("flagged") is True or result.get("auto_accepted") is False

    def test_evidence_required_for_auto_accept(self):
        """Auto-accept (confidence >= 3) requires non-empty evidence."""
        from oem_knowledge.services.reflection import ReflectionService

        svc = ReflectionService()
        result = svc.add_inline_memory(
            memory_type="decision",
            content="Switch to PostgreSQL",
            scope="project",
            confidence=5,
            evidence="",
        )
        assert result["status"] == "rejected"
        assert result.get("reason") == "evidence_required"

    def test_command_log_rejected_by_quality_gate(self):
        """Command log patterns are rejected by the quality gate."""
        from oem_knowledge.services.reflection import ReflectionService

        svc = ReflectionService()
        result = svc.add_inline_memory(
            memory_type="observation",
            content="Command `npm install` executed with exit code 0",
            scope="project",
            confidence=3,
            evidence="npm install output was successful",
        )
        assert result["status"] == "rejected"
        assert "quality_gate" in result.get("reason", "")

    def test_scope_field_persists(self):
        """Scope field round-trips through KnowledgeEvent."""
        from oem_knowledge.models import KnowledgeEvent
        ev = KnowledgeEvent(
            event_id="test-001",
            timestamp="2026-07-30T00:00:00Z",
            project="test",
            session_id="s1",
            event_type="decision",
            summary="test",
            evidence="test evidence",
            source="inline_agent",
            scope="user",
        )
        d = ev.model_dump()
        assert d["scope"] == "user"

    def test_malformed_input_rejected(self):
        """Route through validation rejects malformed input."""
        from oem_knowledge.services.reflection import ReflectionService

        svc = ReflectionService()
        result = svc.add_inline_memory(
            memory_type="decision",
            content="",
            scope="project",
            confidence=3,
            evidence="Some evidence here that is long enough for auto accept",
        )
        assert result["status"] == "rejected"
        assert result.get("reason") == "validation"

    def test_event_accepted_with_concept_instead_of_summary(self):
        """Event with 'concept' but no 'summary' is accepted."""
        from oem_knowledge.services.reflection import ReflectionService
        svc = ReflectionService()
        ev = {
            "event_type": "observation",
            "concept": "fix-auth-bug",
            "evidence": "Fixed the auth bug by adding token refresh",
            "confidence": 4,
            "source": "agent_structured",
        }
        warnings = []
        normalized = svc._validate_and_normalize_event(ev, warnings)
        assert normalized is not None
        assert normalized["summary"] == "fix-auth-bug"

    def test_event_accepted_with_concept_candidates_instead_of_summary(self):
        """Event with 'concept_candidates' but no 'summary' is accepted."""
        from oem_knowledge.services.reflection import ReflectionService
        svc = ReflectionService()
        ev = {
            "event_type": "decision",
            "concept_candidates": ["use-uv-over-pip"],
            "evidence": "uv is faster",
            "confidence": 4,
            "source": "agent_structured",
        }
        warnings = []
        normalized = svc._validate_and_normalize_event(ev, warnings)
        assert normalized is not None
        assert normalized["summary"] == "use-uv-over-pip"

    def test_event_rejected_when_no_summary_concept_or_candidates(self):
        """Event with no summary, concept, or concept_candidates is still rejected."""
        from oem_knowledge.services.reflection import ReflectionService
        svc = ReflectionService()
        ev = {
            "event_type": "observation",
            "evidence": "no summary or concept",
            "confidence": 4,
            "source": "agent_structured",
        }
        warnings = []
        normalized = svc._validate_and_normalize_event(ev, warnings)
        assert normalized is None
        assert any("missing summary" in w for w in warnings)


class TestStructuredEvents:
    """Regression tests for structured events processing via knowledge_reflect / knowledge_session_end."""

    def test_extract_session_events_structured_mode(self, tmp_path):
        """extract_session_events processes events with extraction_mode='structured'."""
        from oem_knowledge.engine import KnowledgeEngine
        from oem_knowledge.services.reflection import ReflectionService

        proj = tmp_path / "test_proj"
        proj.mkdir()
        eng = KnowledgeEngine(proj)
        eng.init_project(str(proj))

        svc = eng.reflection
        events = [
            {
                "event_id": "ev-001",
                "event_type": "decision",
                "summary": "Decided to use PostgreSQL for storage",
                "evidence": "PostgreSQL handles concurrent writes better",
                "confidence": 4,
                "source": "agent_structured",
            },
            {
                "event_id": "ev-002",
                "event_type": "failure",
                "summary": "SQLite WAL mode caused lock contention",
                "evidence": "Multiple writers locked up under WAL",
                "confidence": 3,
                "source": "agent_structured",
            },
        ]

        res = svc.extract_session_events(
            project=str(proj),
            events=events,
            extraction_mode="structured",
        )

        assert res["status"] in ("success", "partial")
        assert res["events_written"] == 2
        assert res["mode"] == "structured"

    def test_reflect_session_structured_events_written(self, tmp_path):
        """reflect_session persists structured events to events.jsonl."""
        import json
        from oem_knowledge.engine import KnowledgeEngine

        proj = tmp_path / "test_proj"
        proj.mkdir()
        eng = KnowledgeEngine(proj)
        eng.init_project(str(proj))

        events = [
            {
                "event_id": "ev-003",
                "event_type": "decision",
                "summary": "Chose FastAPI over Flask",
                "evidence": "FastAPI has better async support",
                "confidence": 4,
                "source": "agent_structured",
            },
        ]

        res = eng.reflection.reflect_session(
            project=str(proj),
            events=events,
            extraction_mode="structured",
        )

        assert res["status"] in ("success", "partial")
        assert res["events_written"] == 1

        # Verify event was persisted to events.jsonl
        events_path = eng._events_path(str(proj))
        assert events_path.exists()
        with open(events_path) as f:
            stored = [json.loads(line) for line in f if line.strip()]
        assert len(stored) == 1
        assert stored[0]["summary"] == "Chose FastAPI over Flask"
