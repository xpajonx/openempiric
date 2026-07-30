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
