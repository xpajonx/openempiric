from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from oem_knowledge.adapters.codex_app.adapter import CODEX_SKILL_CONTENT
from oem_knowledge.adapters.grok.adapter import GrokAdapter
from oem_knowledge.cli import main
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS
from oem_knowledge.server import mount_tools
from oem_knowledge.source_classifier import SourceType, classify_source


def _write_skill(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


@pytest.fixture
def preflight_project(tmp_path: Path) -> Path:
    project = tmp_path / "preflight-project"
    engine = KnowledgeEngine(project)
    engine.init_project(str(project))

    _write_skill(
        project / ".oem" / "skills" / "ccb-calendar-copy.md",
        """
        ---
        id: skill_ccb_calendar_copy
        title: CCB Calendar Copy Style
        status: approved
        triggers:
          - CCB
          - calendar copy
          - SME loans
        ---

        # CCB Calendar Copy Style

        Use warm partner tone. Avoid product dumping.
        """,
    )
    (project / ".oem" / "source_manifest.json").write_text(
        json.dumps({"version": 1, "files": [{"rel_path": "src/copy.py", "status": "indexed"}]}, indent=2),
        encoding="utf-8",
    )
    return project


def _payload_contract(payload: dict) -> dict:
    return {
        "operation": payload["operation"],
        "project_root": payload["project_root"],
        "memory_root": payload["memory_root"],
        "decision": payload["decision"],
        "reason": payload["reason"],
        "matched_skill_titles": [item["title"] for item in payload["matched_skills"]],
        "matched_concept_titles": [item["title"] for item in payload["matched_concepts"]],
        "matched_memory_titles": [item["title"] for item in payload["matched_memory"]],
        "source_suggestion_titles": [item["title"] for item in payload["source_suggestions"]],
        "warnings_type": type(payload["warnings"]).__name__,
    }


def test_mcp_knowledge_preflight_registered():
    mcp = FastMCP("oem")
    mount_tools(mcp)

    tools = asyncio.run(mcp.list_tools())
    assert any(tool.name == "knowledge_preflight" for tool in tools)


def test_mcp_knowledge_preflight_returns_project_root_and_memory_root(preflight_project: Path):
    mcp = FastMCP("oem")
    mount_tools(mcp)

    res = asyncio.run(
        mcp.call_tool(
            "knowledge_preflight",
            {
                "task": "buat copy calendar CCB untuk SME loans",
                "project": str(preflight_project),
            },
        )
    )
    payload = json.loads(res.content[0].text)

    assert payload["status"] == "success"
    assert payload["project_root"] == str(preflight_project.resolve())
    assert payload["memory_root"] == str((preflight_project / ".oem").resolve())


def test_mcp_knowledge_preflight_required_for_skill_trigger(preflight_project: Path):
    mcp = FastMCP("oem")
    mount_tools(mcp)

    res = asyncio.run(
        mcp.call_tool(
            "knowledge_preflight",
            {
                "task": "buat copy calendar CCB untuk SME loans",
                "project": str(preflight_project),
            },
        )
    )
    payload = json.loads(res.content[0].text)

    assert payload["decision"] == "required"
    assert payload["reason"] == "approved_skill_match"
    assert payload["matched_skills"][0]["title"] == "CCB Calendar Copy Style"


def test_mcp_knowledge_preflight_error_when_project_unresolved():
    mcp = FastMCP("oem")
    mount_tools(mcp)

    with patch.dict("os.environ", {"PWD": ""}, clear=True):
        with patch("os.getcwd", return_value="/tmp/nonexistent_project_dir_random_456"):
            res = asyncio.run(
                mcp.call_tool(
                    "knowledge_preflight",
                    {
                        "task": "buat copy calendar CCB untuk SME loans",
                        "project": "",
                    },
                )
            )

    payload = json.loads(res.content[0].text)
    assert payload["status"] == "error"
    assert payload["decision"] == "blocked"
    assert payload["reason"] == "project_unresolved"


def test_cli_preflight_text_output(preflight_project: Path, capsys):
    with patch.object(
        sys,
        "argv",
        ["oem", "preflight", "buat copy calendar CCB untuk SME loans", "--project", str(preflight_project)],
    ):
        main()

    captured = capsys.readouterr()
    assert "OEM Preflight" in captured.out
    assert "Decision: required" in captured.out
    assert "Matched skills:" in captured.out
    assert "CCB Calendar Copy Style" in captured.out


def test_cli_preflight_json_output(preflight_project: Path, capsys):
    with patch.object(
        sys,
        "argv",
        ["oem", "preflight", "buat copy calendar CCB untuk SME loans", "--project", str(preflight_project), "--json"],
    ):
        main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["decision"] == "required"
    assert payload["reason"] == "approved_skill_match"


def test_cli_preflight_respects_no_audit(preflight_project: Path, capsys):
    audit_path = preflight_project / ".oem" / "preflight" / "preflight_events.jsonl"
    assert not audit_path.exists()

    with patch.object(
        sys,
        "argv",
        [
            "oem",
            "preflight",
            "buat copy calendar CCB untuk SME loans",
            "--project",
            str(preflight_project),
            "--json",
            "--no-audit",
        ],
    ):
        main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert not audit_path.exists()


def test_preflight_limit_is_clamped_with_warning(preflight_project: Path):
    payload = KnowledgeEngine(str(preflight_project)).preflight(
        task="buat copy calendar CCB untuk SME loans",
        project=str(preflight_project),
        limit=99,
        write_audit=False,
    )

    assert payload["status"] == "success"
    assert any("clamped to 20" in warning for warning in payload["warnings"])


def test_agent_instructions_include_preflight_rule(tmp_path: Path):
    assert "Before planning non-trivial tasks, call `knowledge_preflight`" in OEM_MEMORY_INSTRUCTIONS
    assert "Do not use `knowledge_index` as a fallback for failed reflection." in OEM_MEMORY_INSTRUCTIONS
    assert "Do not treat the source corpus as learned memory." in OEM_MEMORY_INSTRUCTIONS
    assert "knowledge_preflight" in CODEX_SKILL_CONTENT

    project = tmp_path / "grok-project"
    engine = KnowledgeEngine(project)
    engine.init_project(str(project))
    adapter = GrokAdapter(engine, str(project))
    assert adapter.install_skill() is True
    installed = (project / ".grok" / "skills" / "openempiric" / "SKILL.md").read_text(encoding="utf-8")
    assert "knowledge_preflight" in installed
    assert "Do not treat the source corpus as learned memory." in installed


def test_adapter_contract_documented():
    path = Path(__file__).resolve().parents[3] / "docs" / "adapters" / "preflight.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "knowledge_preflight" in content
    assert "They should not say that preflight is automatic." in content


def test_preflight_runtime_context_non_ingestion_eligible_if_created(tmp_path: Path):
    runtime_file = tmp_path / ".oem" / ".runtime" / "preflight_context.md"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# Preflight Context\n", encoding="utf-8")

    classification = classify_source(runtime_file)
    assert classification.ingestion_eligible is False
    assert classification.source_type == SourceType.OEM_RUNTIME_LOG


def test_engine_cli_mcp_preflight_payloads_share_normalized_contract(preflight_project: Path, capsys):
    task = "buat copy calendar CCB untuk SME loans"
    audit_path = preflight_project / ".oem" / "preflight" / "preflight_events.jsonl"

    engine_payload = KnowledgeEngine(str(preflight_project)).preflight(
        task=task,
        project=str(preflight_project),
        limit=8,
        write_audit=False,
    )

    with patch.object(
        sys,
        "argv",
        ["oem", "preflight", task, "--project", str(preflight_project), "--json", "--no-audit"],
    ):
        main()
    cli_payload = json.loads(capsys.readouterr().out)

    mcp = FastMCP("oem")
    mount_tools(mcp)
    mcp_res = asyncio.run(
        mcp.call_tool(
            "knowledge_preflight",
            {
                "task": task,
                "project": str(preflight_project),
                "limit": 8,
                "write_audit": False,
            },
        )
    )
    mcp_payload = json.loads(mcp_res.content[0].text)

    assert _payload_contract(engine_payload) == _payload_contract(cli_payload)
    assert _payload_contract(engine_payload) == _payload_contract(mcp_payload)
    assert not audit_path.exists()
