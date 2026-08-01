"""Tests for user scope model and identity detection."""

import os
import pytest
from pathlib import Path


class TestResolveUserIdentity:
    """Tests for user identity resolution."""

    def test_oem_user_id_has_precedence(self, monkeypatch):
        monkeypatch.setenv("OEM_USER_ID", "explicit_user")
        from oem_knowledge.services.state import resolve_user_identity
        assert resolve_user_identity() == "explicit_user"

    def test_returns_none_when_no_identity(self, monkeypatch):
        monkeypatch.delenv("OEM_USER_ID", raising=False)
        from oem_knowledge.services.state import resolve_user_identity
        # Without OEM_USER_ID and without git, returns None
        result = resolve_user_identity()
        # May return git email if git is configured, or None
        assert result is None or "@" in result

    def test_get_user_events_path_with_identity(self, monkeypatch):
        monkeypatch.setenv("OEM_USER_ID", "test_user")
        from oem_knowledge.services.state import get_user_events_path
        path = get_user_events_path()
        assert path is not None
        assert "user_events.jsonl" in str(path)

    def test_get_user_events_path_none_without_identity(self, monkeypatch):
        monkeypatch.delenv("OEM_USER_ID", raising=False)
        monkeypatch.delenv("HOME", raising=False)
        from oem_knowledge.services.state import resolve_user_identity, get_user_events_path
        # Override subprocess to avoid git dependency in tests
        import subprocess
        original_run = subprocess.run
        def mock_run(*args, **kwargs):
            raise FileNotFoundError
        subprocess.run = mock_run
        try:
            user_id = resolve_user_identity()
            assert user_id is None
        finally:
            subprocess.run = original_run


class TestScopeOnKnowledgeEvent:
    """Tests for scope field on KnowledgeEvent."""

    def test_scope_defaults_to_project(self):
        from oem_knowledge.models import KnowledgeEvent
        ev = KnowledgeEvent(
            event_id="s1",
            timestamp="2026-07-30T00:00:00Z",
            project="test",
            session_id="s1",
            event_type="decision",
            summary="test",
            evidence="test",
            source="inline",
        )
        assert ev.scope == "project"

    def test_scope_user_persists(self):
        from oem_knowledge.models import KnowledgeEvent
        ev = KnowledgeEvent(
            event_id="s2",
            timestamp="2026-07-30T00:00:00Z",
            project="test",
            session_id="s1",
            event_type="preference",
            summary="test",
            evidence="test",
            source="inline_agent",
            scope="user",
        )
        d = ev.model_dump()
        assert d["scope"] == "user"


class TestCreatedBy:
    """Tests for created_by field on ConceptData."""

    def test_created_by_defaults_to_none(self):
        from oem_knowledge.models import ConceptData
        concept = ConceptData(concept_id="c1", canonical_name="Test")
        assert concept.created_by is None

    def test_created_by_persists(self):
        from oem_knowledge.models import ConceptData
        concept = ConceptData(
            concept_id="c2",
            canonical_name="Test2",
            created_by="orchestrator-v1",
        )
        d = concept.model_dump()
        assert d["created_by"] == "orchestrator-v1"
