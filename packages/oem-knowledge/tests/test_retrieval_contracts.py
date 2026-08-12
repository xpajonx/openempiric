"""Retrieval contract tests (Wave 0): corpus shape, record validation, normalization, windows."""

import copy

RETRIEVAL_CORPUS = [
    {"id": "proj-dec-1", "scope": "project", "memory_type": "decision", "timestamp": "2026-07-01T10:00:00Z",
     "document": "Decision: use SQLite for event storage",
     "metadata": {"source": ".oem/wiki/concept_001.md", "title": "Storage Decision"}},
    {"id": "proj-fail-1", "scope": "project", "memory_type": "failure", "timestamp": "2026-06-15T08:30:00Z",
     "document": "Failure: session_end hung when embedding cache broken",
     "metadata": {"source": ".oem/wiki/concept_002.md"}},
    {"id": "user-pref-1", "scope": "user", "memory_type": "preference", "timestamp": "2026-07-10T12:00:00Z",
     "document": "Preference: prefer hybrid retrieval",
     "metadata": {"source": "user_events.jsonl"}},
    {"id": "user-dec-1", "scope": "user", "memory_type": "decision", "timestamp": "2026-05-01T09:00:00Z",
     "document": "Type: decision\nUse git over hg",
     "metadata": {"source": "user_events.jsonl"}},
    {"id": "sess-obs-1", "scope": "session", "memory_type": "observation", "timestamp": "2026-07-15T14:00:00Z",
     "document": "Observation: scope filter returns empty for user scope",
     "metadata": {}},
    {"id": "evt-type-1", "scope": "project", "timestamp": "2026-07-20T11:00:00Z",
     "document": "Adopted spawn-isolated indexing with 10s budget",
     "metadata": {"memory_type": "decision", "source": ".oem/state/events.jsonl"}},
    {"id": "old-obs-1", "scope": "project", "memory_type": "observation", "timestamp": "2026-01-01T00:00:00Z",
     "document": "Observation: old note",
     "metadata": {}},
    {"id": "malformed-1", "scope": "project", "memory_type": "observation", "timestamp": "not-a-date",
     "document": "Observation: bad timestamp",
     "metadata": {"scope": "user"}},
]


def test_corpus_has_expected_size_and_unique_ids():
    ids = [rec["id"] for rec in RETRIEVAL_CORPUS]
    assert len(RETRIEVAL_CORPUS) == 8
    assert len(set(ids)) == 8


def test_corpus_records_1_to_7_well_formed():
    from oem_knowledge.retrieval import is_well_formed_record
    for record in RETRIEVAL_CORPUS[:7]:
        ok, issues = is_well_formed_record(record)
        assert ok, f"record {record['id']} flagged issues: {issues}"


def test_malformed_record_flags_invalid_timestamp():
    from oem_knowledge.retrieval import is_well_formed_record
    ok, issues = is_well_formed_record(RETRIEVAL_CORPUS[7])
    assert ok is False
    assert any("invalid timestamp" in issue for issue in issues)


def test_malformed_record_flags_conflicting_metadata_scope():
    from oem_knowledge.retrieval import is_well_formed_record
    ok, issues = is_well_formed_record(RETRIEVAL_CORPUS[7])
    assert ok is False
    assert any("conflicting scope" in issue for issue in issues)


def test_normalize_promotes_missing_fields():
    from oem_knowledge.retrieval import normalize_record_fields
    normalized = normalize_record_fields(RETRIEVAL_CORPUS[5])
    assert normalized["memory_type"] == "decision"
    assert normalized["scope"] == "project"


def test_normalize_keeps_explicit_top_level():
    from oem_knowledge.retrieval import normalize_record_fields
    normalized = normalize_record_fields(RETRIEVAL_CORPUS[7])
    assert normalized["scope"] == "project"


def test_normalize_does_not_mutate_input():
    from oem_knowledge.retrieval import normalize_record_fields
    original = copy.deepcopy(RETRIEVAL_CORPUS[5])
    normalize_record_fields(RETRIEVAL_CORPUS[5])
    assert RETRIEVAL_CORPUS[5] == original


def test_parse_iso_window_handles_z_and_offsets():
    from oem_knowledge.retrieval import parse_iso_window
    since_dt, until_dt, error = parse_iso_window(
        "2026-07-01T10:00:00Z", "2026-07-01T10:00:00+02:00"
    )
    assert since_dt is not None
    assert until_dt is not None
    assert error is None


def test_parse_iso_window_rejects_garbage():
    from oem_knowledge.retrieval import parse_iso_window
    since_dt, until_dt, error = parse_iso_window("not-a-date", "not-a-date")
    assert since_dt is None
    assert until_dt is None
    assert error is not None
    since_dt, until_dt, error = parse_iso_window("not-a-date", "2026-07-01T10:00:00Z")
    assert since_dt is None
    assert until_dt is not None
    assert error is not None and "since" in error


def test_record_scope_matches():
    from oem_knowledge.retrieval import record_scope_matches
    assert record_scope_matches("user", "user") is True
    assert record_scope_matches("project", "user") is False
    assert record_scope_matches(None, "user") is True
    assert record_scope_matches("user", None) is False


def test_record_in_window_excludes_old_record():
    from oem_knowledge.retrieval import parse_iso_window, record_in_window
    since_dt = parse_iso_window("2026-06-01", None)[0]
    assert record_in_window(RETRIEVAL_CORPUS[6]["timestamp"], since_dt, None) is False
    assert record_in_window(RETRIEVAL_CORPUS[0]["timestamp"], since_dt, None) is True


def test_record_in_window_until_excludes_new():
    from oem_knowledge.retrieval import parse_iso_window, record_in_window
    until_dt = parse_iso_window(None, "2026-07-01T00:00:00Z")[1]
    assert record_in_window(RETRIEVAL_CORPUS[0]["timestamp"], None, until_dt) is False
    assert record_in_window(RETRIEVAL_CORPUS[6]["timestamp"], None, until_dt) is True


def test_record_in_window_missing_or_invalid_timestamp_is_true():
    from oem_knowledge.retrieval import record_in_window
    assert record_in_window(None, None, None) is True
    assert record_in_window("garbage", None, None) is True


def test_parse_iso_window_treats_whitespace_and_non_string_as_absent():
    from oem_knowledge.retrieval import parse_iso_window
    since_dt, until_dt, error = parse_iso_window("   ", "2026-07-01T10:00:00Z")
    assert since_dt is None
    assert until_dt is not None
    assert error is None
    since_dt, until_dt, error = parse_iso_window(123, None)
    assert (since_dt, until_dt, error) == (None, None, None)


def test_well_formed_record_with_only_conflicting_metadata_scope_is_ok():
    from oem_knowledge.retrieval import is_well_formed_record
    record = {"id": "conflict-ok-1", "scope": "project", "memory_type": "observation",
              "timestamp": "2026-07-01T10:00:00Z", "document": "Observation: ok",
              "metadata": {"scope": "user"}}
    ok, issues = is_well_formed_record(record)
    assert ok is True
    assert any("conflicting scope" in issue for issue in issues)


def test_record_in_window_non_string_timestamp_is_true():
    from oem_knowledge.retrieval import record_in_window
    assert record_in_window(12345, None, None) is True
