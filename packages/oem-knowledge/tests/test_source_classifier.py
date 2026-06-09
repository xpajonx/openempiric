from oem_knowledge.source_classifier import SourceType, classify_source, is_ingestion_eligible


def test_source_classifier_marks_oem_wiki_as_not_ingestion_eligible():
    classification = classify_source(".oem/wiki/concepts/example.md")

    assert classification.source_type == SourceType.OEM_WIKI
    assert classification.ingestion_eligible is False
    assert is_ingestion_eligible(".oem/wiki/concepts/example.md") is False


def test_source_classifier_marks_runtime_events_as_oem_runtime_log():
    classification = classify_source(".oem/runtime_events.jsonl")

    assert classification.source_type == SourceType.OEM_RUNTIME_LOG
    assert classification.ingestion_eligible is False


def test_source_classifier_marks_project_file_as_ingestion_eligible():
    classification = classify_source("src/package/module.py", "def hello():\n    pass\n")

    assert classification.source_type == SourceType.PROJECT_FILE
    assert classification.ingestion_eligible is True
    assert is_ingestion_eligible("src/package/module.py") is True
