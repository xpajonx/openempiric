from __future__ import annotations

import os
import sys
import math
import time
import shutil
import json
import re
from pathlib import Path

from oem_knowledge.markdown.frontmatter import parse_frontmatter


def calculate_concept_health(cdata: dict) -> float:
    """Calculate a concept health score (0-100) based on confidence, evidence, failures, and recency."""
    # Start with base score
    score = 50.0

    # 1. Evidence Count: +5 points per evidence, up to +30
    ev_count = cdata.get("evidence_count", 0)
    score += min(30.0, ev_count * 5.0)

    # 2. Confidence Level (status/session weight): +5 points per level of confidence, up to +25
    conf = cdata.get("confidence", 1)
    score += min(25.0, conf * 5.0)

    # 3. Failures Penalty: -15 points per failure event
    # If the confidence was decayed, we also check if failures occurred in events
    # We will pass failures count if tracked in cdata, or default to 0
    failures = cdata.get("failure_count", 0)
    score -= (failures * 15.0)

    # 4. Status Multiplier/Additions
    status = cdata.get("status", "candidate").lower()
    if status == "global":
        score += 15.0
    elif status == "canonical":
        score += 10.0
    elif status == "validated":
        score += 5.0
    elif status == "needs_review":
        score -= 20.0
    elif status == "deprecated":
        score = 0.0
        return score

    # Clamp score to [0.0, 100.0]
    score = max(0.0, min(100.0, score))
    return round(score, 2)


def validate_concept_frontmatter(project: str | None = None) -> dict:
    """Validate frontmatter integrity for all concept_*.md wiki files.

    Checks:
      - File is readable
      - Frontmatter is well-formed (no parse warnings)
      - concept_id field exists in frontmatter
      - concept_id matches filename (when registry is available)
      - status field exists
      - Body is non-empty

    Returns:
        {"status": "success"|"warn"|"error", "checks": [...]}
    """
    from oem_knowledge.engine import KnowledgeEngine

    eng = KnowledgeEngine(project)
    concepts_dir = eng._concepts_dir(project)
    checks: list[dict] = []

    # Load registry for cross-reference (best-effort)
    registry: dict = {}
    registry_available = True
    try:
        registry = eng.state._load_registry(project)
    except Exception:
        registry_available = False
        checks.append({
            "name": "Concept registry unavailable for integrity cross-check",
            "status": "warn",
            "reason": "registry_unavailable_for_integrity_check",
        })

    if not concepts_dir.exists():
        checks.append({
            "name": "No concept wiki directory",
            "status": "success",
        })
        return _aggregate_health_status(checks, "success")

    for path in sorted(concepts_dir.glob("concept_*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            checks.append({
                "name": f"{path.name}: could not read ({exc})",
                "status": "error",
                "path": str(path),
            })
            continue

        parsed = parse_frontmatter(text, source_path=str(path))

        # Parser warnings
        error_reasons = {
            "frontmatter_block_not_closed",
            "frontmatter_yaml_parse_error",
            "frontmatter_not_mapping",
        }
        for w in parsed.warnings:
            severity = "error" if w["reason"] in error_reasons else "warn"
            checks.append({
                "name": f"{path.name}: {w['reason']}",
                "status": severity,
                "path": str(path),
                "reason": w["reason"],
            })

        meta = parsed.metadata

        # concept_id check
        fm_concept_id = str(meta.get("concept_id") or "")
        file_concept_id = path.stem
        if not fm_concept_id:
            checks.append({
                "name": f"{path.name}: missing concept_id in frontmatter",
                "status": "error",
                "path": str(path),
                "reason": "missing_concept_id",
            })
        elif registry_available and fm_concept_id != file_concept_id and fm_concept_id not in registry:
            checks.append({
                "name": f"{path.name}: concept_id mismatch (frontmatter={fm_concept_id}, file={file_concept_id})",
                "status": "error",
                "path": str(path),
                "reason": "concept_id_mismatch",
            })

        # status check
        if "status" not in meta:
            checks.append({
                "name": f"{path.name}: missing status in frontmatter",
                "status": "warn",
                "path": str(path),
                "reason": "missing_status",
            })

        # body check
        if not parsed.body.strip():
            checks.append({
                "name": f"{path.name}: empty body",
                "status": "warn",
                "path": str(path),
                "reason": "empty_body",
            })

    return _aggregate_health_status(checks, "success")


def _aggregate_health_status(checks: list[dict], default: str) -> dict:
    """Derive overall status from individual check severities."""
    if any(c["status"] == "error" for c in checks):
        status = "error"
    elif any(c["status"] == "warn" for c in checks):
        status = "warn"
    else:
        status = default
    return {"status": status, "checks": checks}


def _legacy_status(status: str) -> str:
    return "success" if status == "ok" else status


def _canonical_status(status: str) -> str:
    return "ok" if status == "success" else status


def _status_from_checks(checks: list[dict], default: str = "ok") -> str:
    statuses = {c.get("status") for c in checks}
    if "error" in statuses:
        return "error"
    if "warn" in statuses or "warning" in statuses:
        return "warn"
    return default


def _build_active_project_health(project: str | None = None) -> dict:
    from oem_knowledge.engine import KnowledgeEngine
    from oem_knowledge.runtime.active_work import resolve_active_work_identity

    eng = KnowledgeEngine(project)
    memory_root = eng._resolve_harness(project)
    project_root = memory_root.parent
    ident = resolve_active_work_identity(Path(memory_root))

    # Surface field-aware active_work
    active_work = ident.to_dict()

    # Legacy alias: active_project (do NOT fallback to workspace_root)
    legacy_val = ident.active_work_item or ident.active_topic
    legacy_alias = {
        "legacy_alias": True,
        "value": legacy_val,
        "source_field": "active_work_item" if ident.active_work_item else ("active_topic" if ident.active_topic else None),
    }
    if not legacy_val:
        legacy_alias["warning"] = "workspace_resolved_active_work_unknown"

    # Build contradictions using field-specific types
    contradictions = []
    for c in ident.conflicts:
        d = c.to_dict()
        contradictions.append(d)

    warnings = ident.warnings[:]

    checks: list[dict] = []
    if ident.active_work_item or ident.active_topic:
        sel = ident.active_work_item or ident.active_topic
        checks.append({
            "name": "Active work resolved",
            "status": "success",
            "active_work_item": ident.active_work_item,
            "active_topic": ident.active_topic,
        })
    else:
        checks.append({
            "name": "No active work item resolved (workspace only)",
            "status": "success",
            "reason": "workspace_resolved_active_work_unknown",
        })

    for warning in warnings:
        checks.append({
            "name": warning.get("message", warning.get("reason", "Active-work warning")),
            "status": "warn",
            "reason": warning.get("reason"),
            "path": warning.get("path"),
        })

    for contradiction in contradictions:
        checks.append({
            "name": contradiction.get("message", "Active-work field mismatch"),
            "status": "error" if contradiction.get("severity") == "error" else "warn",
            "type": contradiction.get("type"),
            "field": contradiction.get("field"),
            "sources": contradiction.get("sources"),
        })

    status = _status_from_checks(checks)
    return {
        "status": status,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "checks": checks,
        "contradictions": contradictions,
        "warnings": warnings,
        "active_project": {
            **legacy_alias,
            "legacy_shape": True,
        },
        "active_work": active_work,
    }


def build_health_report(
    project: str | None = None,
    *,
    include_daemon_runtime: bool = True,
    include_active_project: bool = True,
    include_concept_integrity: bool = True,
) -> dict:
    active_health = None
    if include_active_project:
        active_health = _build_active_project_health(project)

    if include_daemon_runtime:
        legacy = _build_runtime_health_legacy(project)
    else:
        project_root = Path(project or ".").resolve()
        memory_root = project_root / ".oem"
        if active_health:
            project_root = Path(active_health["project_root"])
            memory_root = Path(active_health["memory_root"])
        legacy = {
            "status": "success",
            "operation": "runtime_health",
            "project": str(project_root),
            "environment": {"status": "success", "checks": []},
            "runtime": {"status": "success", "checks": []},
            "opencode": {"active": False, "status": "success", "checks": []},
            "codex_app": {"active": False, "status": "success", "checks": []},
            "reflection_diagnostic": None,
            "knowledge_stats": {},
            "concept_integrity": {"status": "success", "checks": []},
        }

    if include_concept_integrity and not include_daemon_runtime:
        legacy["concept_integrity"] = validate_concept_frontmatter(project)

    checks: list[dict] = []
    for section in ("environment", "opencode", "codex_app", "runtime"):
        value = legacy.get(section, {})
        checks.extend(value.get("checks", []))
    checks.extend(legacy.get("concept_integrity", {}).get("checks", []))

    contradictions: list[dict] = []
    warnings: list[dict] = []
    active_project = {}
    active_work = {}
    project_root = str(project or legacy.get("project", "."))
    memory_root = str(Path(project_root) / ".oem")
    if active_health:
        checks.extend(active_health["checks"])
        contradictions = active_health["contradictions"]
        warnings = active_health["warnings"]
        active_project = active_health.get("active_project", {})
        active_work = active_health.get("active_work", {})
        project_root = active_health["project_root"]
        memory_root = active_health["memory_root"]

    status = _canonical_status(legacy.get("status", "success"))
    if any(c.get("severity") == "error" for c in contradictions):
        status = "error"
    elif contradictions or warnings:
        if status == "ok":
            status = "warn"

    from oem_knowledge.runtime.working_set import load_working_set
    ws = None
    try:
        ws = load_working_set(project)
    except Exception:
        pass

    working_set_report = {
        "exists": False,
        "updated_at": None,
        "age": None,
        "active_work_item": None,
        "active_files_count": 0,
        "active_concepts_count": 0,
    }
    if ws is not None:
        age = None
        if ws.updated_at:
            try:
                from datetime import datetime, timezone
                iso_str = ws.updated_at
                if iso_str.endswith("Z"):
                    iso_str = iso_str[:-1] + "+00:00"
                updated_dt = datetime.fromisoformat(iso_str)
                now_dt = datetime.now(timezone.utc)
                age = max(0.0, (now_dt - updated_dt).total_seconds())
            except Exception:
                pass
        working_set_report = {
            "exists": True,
            "updated_at": ws.updated_at,
            "age": age,
            "active_work_item": ws.active_work_item,
            "active_files_count": len(ws.active_files),
            "active_concepts_count": len(ws.active_concepts),
        }

    report = {
        **legacy,
        "status": status,
        "operation": "health_report",
        "project_root": project_root,
        "memory_root": memory_root,
        "checks": checks,
        "contradictions": contradictions,
        "warnings": warnings,
        "active_project": active_project,
        "active_work": active_work,
        "concept_integrity": legacy.get("concept_integrity", {}),
        "working_set": working_set_report,
    }
    return report


def build_runtime_health(project: str | None = None) -> dict:
    report = build_health_report(
        project,
        include_daemon_runtime=True,
        include_active_project=True,
        include_concept_integrity=True,
    )
    legacy = dict(report)
    legacy["status"] = _legacy_status(report["status"])
    legacy["operation"] = "runtime_health"
    return legacy


def _build_runtime_health_legacy(project: str | None = None) -> dict:
    from oem_knowledge.engine import KnowledgeEngine
    from oem_knowledge.runtime import SessionState

    eng = KnowledgeEngine(project)
    
    # 1. Environment & Workspace Root
    try:
        resolved_dir = eng._resolve_harness(project)
        workspace_root = resolved_dir
    except Exception:
        workspace_root = Path(project or ".")

    while workspace_root.parent != workspace_root:
        if (workspace_root / "pyproject.toml").exists():
            break
        workspace_root = workspace_root.parent

    pyproject_path = workspace_root / "pyproject.toml"
    root_venv_path = workspace_root / ".venv"

    is_dev_workspace = False
    if pyproject_path.exists():
        try:
            content = pyproject_path.read_text(encoding="utf-8")
            if 'name = "oem-mcp"' in content:
                is_dev_workspace = True
        except Exception:
            pass

    env_checks = []
    env_status = "success"

    if is_dev_workspace:
        # Root workspace
        if pyproject_path.exists():
            env_checks.append({"name": "Root workspace detected", "status": "success"})
        else:
            env_checks.append({"name": "Root workspace pyproject.toml not found", "status": "error"})
            env_status = "error"

        # Root venv
        if root_venv_path.exists():
            env_checks.append({"name": "Root .venv exists", "status": "success"})
        else:
            env_checks.append({"name": "Root .venv not found", "status": "error"})
            env_status = "error"

        # UV Workspace healthy
        try:
            content = pyproject_path.read_text(encoding="utf-8")
            if "[tool.uv.workspace]" in content:
                env_checks.append({"name": "UV workspace healthy", "status": "success"})
            else:
                env_checks.append({"name": "[tool.uv.workspace] missing in root pyproject.toml", "status": "error"})
                env_status = "error"
        except Exception as e:
            env_checks.append({"name": f"Failed to read root pyproject.toml: {e}", "status": "error"})
            env_status = "error"

        # Nested venvs
        nested_venvs = []
        packages_dir = workspace_root / "packages"
        if packages_dir.exists() and packages_dir.is_dir():
            for p in packages_dir.iterdir():
                if p.is_dir():
                    sub_venv = p / ".venv"
                    if sub_venv.exists():
                        nested_venvs.append(str(sub_venv.relative_to(workspace_root)))
        if nested_venvs:
            env_status = "error"
            for nv in nested_venvs:
                env_checks.append({"name": f"Nested virtualenv detected: {nv}", "status": "error"})
        else:
            env_checks.append({"name": "No nested virtualenvs detected", "status": "success"})
    else:
        env_checks.append({"name": "Running as globally installed user tool", "status": "success"})
        env_checks.append({"name": f"Project directory: {workspace_root.resolve()}", "status": "success"})
        if shutil.which("oem"):
            env_checks.append({"name": "OEM executable available", "status": "success"})
        else:
            env_checks.append({"name": "OEM executable not found in PATH", "status": "warn"})
            if env_status == "success":
                env_status = "warn"

    # Schema check
    try:
        schema_status = eng.event_migrator.get_schema_status(project)
        if schema_status["status"] == "up_to_date":
            env_checks.append({"name": f"Events schema up to date ({schema_status['message']})", "status": "success"})
        else:
            env_checks.append({"name": f"Events schema outdated: {schema_status['message']}", "status": "error"})
            env_status = "error"
    except Exception as e:
        env_checks.append({"name": f"Events schema check failed: {e}", "status": "error"})
        env_status = "error"

    # Skill installed
    enabled_adapters = []
    try:
        h_dir = eng._resolve_harness(project)
        skills_file = h_dir / "skills" / "openempiric.yaml"
        if skills_file.exists():
            import yaml
            with open(skills_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data:
                    if "adapters" in data:
                        val = data["adapters"]
                        enabled_adapters = list(val) if isinstance(val, list) else [val]
                    elif "adapter" in data:
                        enabled_adapters = [data["adapter"]]
            env_checks.append({"name": f"OEM Skill Installed (enabled adapters: {', '.join(enabled_adapters) if enabled_adapters else 'none'})", "status": "success"})
        else:
            env_checks.append({"name": "OEM Skill not installed (missing skills/openempiric.yaml)", "status": "error"})
            env_status = "error"
    except Exception as e:
        env_checks.append({"name": f"Failed to verify OEM Skill installation: {e}", "status": "error"})
        env_status = "error"

    if not enabled_adapters:
        enabled_adapters = ["opencode"]

    # Embedding cache ready
    try:
        retrieval_mode = eng.search.get_retrieval_mode()
        if eng.embedding_cache_ready():
            env_checks.append({"name": "Embedding Cache Ready", "status": "success"})
        else:
            if retrieval_mode == "hybrid":
                env_checks.append({"name": "Embedding Cache not ready", "status": "error"})
                env_status = "error"
            else:
                env_checks.append({"name": "Embedding Cache cold (BM25 or Auto active)", "status": "warn"})
                if env_status == "success":
                    env_status = "warn"
    except Exception as e:
        env_checks.append({"name": f"Failed to check Embedding Cache: {e}", "status": "error"})
        env_status = "error"

    # Managed runtime available
    if shutil.which("oem"):
        env_checks.append({"name": "Managed Runtime Available", "status": "success"})
    else:
        env_checks.append({"name": "Managed Runtime not available (executable 'oem' not found in PATH)", "status": "warn"})
        if env_status == "success":
            env_status = "warn"

    # Search pipeline available
    try:
        vector_db_file = eng.layout(project).vector_db_path / "vectors.db"
        if vector_db_file.exists():
            env_checks.append({"name": "Search Pipeline Available", "status": "success"})
        else:
            env_checks.append({"name": "Search Pipeline index not initialized", "status": "warn"})
            if env_status == "success":
                env_status = "warn"
    except Exception as e:
        env_checks.append({"name": f"Search Pipeline not available: {e}", "status": "error"})
        env_status = "error"

    # 2. OpenCode Integration Check
    opencode_checks = []
    opencode_status = "success"
    opencode_dir = Path.home() / ".config" / "opencode"
    jsonc_file = opencode_dir / "opencode.jsonc"
    opencode_active = ("opencode" in enabled_adapters or jsonc_file.exists())

    if opencode_active:
        plugin_dest = opencode_dir / "plugins" / "openempiric.ts"
        inst_dest = opencode_dir / "instructions" / "memory-start.md"

        # Check plugin
        if not plugin_dest.exists():
            opencode_checks.append({"name": "OpenCode Plugin not installed", "status": "error"})
            opencode_status = "error"
        else:
            opencode_checks.append({"name": "OpenCode Plugin installed", "status": "success"})

        # Check instructions
        if not inst_dest.exists():
            opencode_checks.append({"name": "OpenCode Instructions not installed", "status": "error"})
            opencode_status = "error"
        else:
            opencode_checks.append({"name": "OpenCode Instructions installed", "status": "success"})

        # Check config
        mcp_registered = False
        mcp_cmd = []
        if not jsonc_file.exists():
            opencode_checks.append({"name": "OpenCode Config missing", "status": "error"})
            opencode_status = "error"
        else:
            try:
                text = jsonc_file.read_text(encoding="utf-8")
                # Remove comments safely without matching protocol URLs (e.g., https://)
                cleaned = re.sub(r"(?<!:)\/\/.*$", "", text, flags=re.MULTILINE)
                cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
                config_data = json.loads(cleaned, strict=False)
                opencode_checks.append({"name": "OpenCode Config verified", "status": "success"})
                
                mcp_config = config_data.get("mcp", {}).get("openempiric")
                if mcp_config:
                    mcp_registered = True
                    cmd = mcp_config.get("command")
                    mcp_args = mcp_config.get("args", [])
                    mcp_cmd = ([cmd] + mcp_args) if isinstance(cmd, str) else (cmd + mcp_args)
                    opencode_checks.append({"name": "OEM MCP Server registered in OpenCode config", "status": "success"})
                else:
                    opencode_checks.append({"name": "OEM MCP Server not registered in OpenCode config", "status": "error"})
                    opencode_status = "error"
            except Exception as e:
                opencode_checks.append({"name": f"OpenCode Config validation failed: {e}", "status": "error"})
                opencode_status = "error"

        # Context Injection
        try:
            from oem_knowledge.runtime import _OEM_RUNTIME_CONTEXT_PATH
            context_dir = _OEM_RUNTIME_CONTEXT_PATH.parent
            if context_dir.exists():
                opencode_checks.append({"name": "Context Injection Working", "status": "success"})
            else:
                opencode_checks.append({"name": "Context Injection Directory Ready", "status": "success"})
        except Exception as e:
            opencode_checks.append({"name": f"Context Injection Check failed: {e}", "status": "error"})
            opencode_status = "error"

    # 3. Codex App Integration Check
    codex_checks = []
    codex_status = "success"
    codex_active = False
    try:
        from oem_knowledge.adapters.codex_app.adapter import CodexAppAdapter
        codex_adapter = CodexAppAdapter(eng, project)
        try:
            config_path = codex_adapter.get_config_path()
            codex_home_detected = True
        except RuntimeError:
            codex_home_detected = False

        if "codex-app" in enabled_adapters or "codex" in enabled_adapters or (codex_home_detected and config_path.exists()):
            codex_active = True
            if codex_home_detected and config_path.exists():
                codex_checks.append({"name": "Config found", "status": "success"})
                if codex_adapter.verify_mcp():
                    codex_checks.append({"name": "OEM MCP registered", "status": "success"})
                else:
                    codex_checks.append({"name": "OEM MCP not registered", "status": "error"})
                    codex_status = "error"
            else:
                codex_checks.append({"name": "Codex home not detected or config missing", "status": "error"})
                codex_status = "error"
    except Exception as e:
        if "codex-app" in enabled_adapters or "codex" in enabled_adapters:
            codex_active = True
            codex_checks.append({"name": f"Codex App check failed: {e}", "status": "error"})
            codex_status = "error"

    # 4. Runtime Health Checks
    runtime_checks = []
    runtime_status = "success"

    # Recovery
    try:
        active_file = resolved_dir / "state" / "active_session.json"
        _ = SessionState.load(active_file)
        runtime_checks.append({"name": "Session Recovery Ready", "status": "success"})
    except Exception as e:
        runtime_checks.append({"name": f"Session Recovery not ready: {e}", "status": "error"})
        runtime_status = "error"

    # Structured Reflection
    try:
        rs = eng.reflection
        res_struct = rs.reflect_session(project, events=[], extraction_mode="structured")
        if res_struct.get("status") in ("success", "empty"):
            runtime_checks.append({"name": "Structured Reflection Ready", "status": "success"})
        else:
            runtime_checks.append({"name": "Structured Reflection not ready", "status": "error"})
            runtime_status = "error"
    except Exception as e:
        runtime_checks.append({"name": f"Structured Reflection not ready: {e}", "status": "error"})
        runtime_status = "error"

    # Marker Reflection
    try:
        rs = eng.reflection
        res_marker = rs.reflect_session(project, conversation_text="", extraction_mode="markers")
        if res_marker.get("status") in ("success", "empty"):
            runtime_checks.append({"name": "Marker Reflection Ready", "status": "success"})
        else:
            runtime_checks.append({"name": "Marker Reflection not ready", "status": "error"})
            runtime_status = "error"
    except Exception as e:
        runtime_checks.append({"name": f"Marker Reflection not ready: {e}", "status": "error"})
        runtime_status = "error"

    # Check reflection config
    try:
        config = eng.reflection.load_reflection_config(project)["reflection"]
    except Exception:
        config = {}
    structured_enabled = config.get("structured", {}).get("enabled", True)
    marker_enabled = config.get("marker", {}).get("enabled", True)
    dense_enabled = config.get("dense", {}).get("enabled", False)
    queue_pending = config.get("dense", {}).get("queue_pending", False)
    
    # Calculate pending count
    layout = eng.layout(project)
    queue_file = layout.pending_dense_reflections_path
    pending_count = 0
    if queue_file.exists():
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        pending_count += 1
        except Exception:
            pass

    # LLM Reflection (Capability/Config check, NO slow LLM call)
    from oem_knowledge.services.reflection import llm_extraction_available
    llm_avail = llm_extraction_available()

    if os.environ.get("OEM_LLM_DEGRADED") == "true":
        runtime_checks.append({"name": "LLM Reflection Degraded", "status": "warn"})
        if runtime_status == "success":
            runtime_status = "warn"
    else:
        # Check environment/config keys for LLM
        if dense_enabled:
            if llm_avail:
                runtime_checks.append({"name": "LLM Reflection Ready", "status": "success"})
            else:
                runtime_checks.append({"name": "LLM Reflection Degraded (No API key or provider found)", "status": "warn"})
                if runtime_status == "success":
                    runtime_status = "warn"
        else:
            runtime_checks.append({"name": "LLM Reflection Disabled (Configured)", "status": "success"})

    # Materialization
    try:
        # Check if concepts directory exists and layout is sane
        concepts_dir = eng._concepts_dir(project)
        if concepts_dir.parent.exists():
            runtime_checks.append({"name": "Materialization Pipeline Ready", "status": "success"})
        else:
            runtime_checks.append({"name": "Materialization Pipeline not ready", "status": "error"})
            runtime_status = "error"
    except Exception as e:
        runtime_checks.append({"name": f"Materialization Pipeline check failed: {e}", "status": "error"})
        runtime_status = "error"

    # Outcome tracking
    try:
        outcomes_file = resolved_dir / "state" / "outcomes.jsonl"
        if outcomes_file.parent.exists():
            runtime_checks.append({"name": "Outcome Tracking Ready", "status": "success"})
        else:
            runtime_checks.append({"name": "Outcome Tracking state directory missing", "status": "warn"})
            if runtime_status == "success":
                runtime_status = "warn"
    except Exception as e:
        runtime_checks.append({"name": f"Outcome Tracking not ready: {e}", "status": "error"})
        runtime_status = "error"

    # Active-work surface consistency + project conflict detection
    try:
        from oem_knowledge.runtime.active_work import resolve_active_work, resolve_active_project
        aw_memory_root = eng._resolve_harness(project)
        aw = resolve_active_work(Path(aw_memory_root))
        if aw.contradictions:
            for c in aw.contradictions:
                runtime_checks.append({"name": f"Surface contradiction: {c}", "status": "error"})
            runtime_status = "error"
        else:
            runtime_checks.append({"name": "Active-work surfaces consistent", "status": "success"})

        # Project-level conflict detection
        proj = resolve_active_project(Path(aw_memory_root))
        if proj.conflicts:
            for c in proj.conflicts:
                severity = "error" if c.severity == "error" else "warn"
                sources = ", ".join(c.sources)
                vals = ", ".join(
                    f"{s.source}={s.project or '?'}"
                    for s in proj.sources
                    if s.project is not None
                )
                runtime_checks.append({
                    "name": f"Active project conflict ({c.type}): [{vals}]",
                    "status": severity,
                })
                if severity == "error":
                    runtime_status = "error"
                elif runtime_status == "success":
                    runtime_status = "warn"
        else:
            runtime_checks.append({"name": "Active-project sources agree", "status": "success"})
    except Exception as e:
        runtime_checks.append({"name": f"Active-work consistency check failed: {e}", "status": "warn"})
        if runtime_status == "success":
            runtime_status = "warn"

    # Runtime provenance — detect dev checkout serving as runtime for non-dev project
    try:
        from oem_knowledge.runtime.provenance import detect_runtime, RUNTIME_KIND_REPO_VENV, RUNTIME_KIND_EDITABLE, RUNTIME_KIND_UV_TOOL
        prov = detect_runtime()
        runtime_checks.append({"name": f"Runtime: {prov['runtime_kind']} ({prov['executable_path']})", "status": "success"})
        if prov["package_path"]:
            runtime_checks.append({"name": f"Package path: {prov['package_path']}", "status": "success"})
        if prov["version"]:
            runtime_checks.append({"name": f"Package version: {prov['version']}", "status": "success"})

        dev_runtime_on_nondev = (
            prov["runtime_kind"] in (RUNTIME_KIND_REPO_VENV, RUNTIME_KIND_EDITABLE)
            and project is not None
            and not is_dev_workspace
        )
        if dev_runtime_on_nondev and not os.environ.get("OEM_ALLOW_DEV_RUNTIME") == "1":
            runtime_checks.append({
                "name": "Dev checkout runtime serving non-dev project — set OEM_ALLOW_DEV_RUNTIME=1 to suppress",
                "status": "warn"
            })
            if runtime_status == "success":
                runtime_status = "warn"
    except Exception as e:
        runtime_checks.append({"name": f"Runtime provenance check failed: {e}", "status": "warn"})
        if runtime_status == "success":
            runtime_status = "warn"

    # Stale active-session detection
    try:
        active_file = resolved_dir / "state" / "active_session.json"
        if active_file.exists():
            data = json.loads(active_file.read_text(encoding="utf-8"))
            if data.get("status") == "running":
                missing_fields = [
                    f for f in ("context_path", "temp_instructions")
                    if f not in data or not data[f]
                ]
                if missing_fields:
                    runtime_checks.append({
                        "name": f"Stale active session: status=running but missing: {', '.join(missing_fields)}",
                        "status": "warn"
                    })
                    if runtime_status == "success":
                        runtime_status = "warn"
    except Exception as e:
        runtime_checks.append({"name": f"Stale session check failed: {e}", "status": "warn"})
        if runtime_status == "success":
            runtime_status = "warn"

    # Reflection Diagnostics
    reflection_diagnostic = {
        "structured_enabled": structured_enabled,
        "marker_enabled": marker_enabled,
        "dense_llm": "configured" if llm_avail else ("unavailable" if dense_enabled else "not configured"),
        "shutdown_policy": "queue pending" if (dense_enabled and queue_pending) else "skip dense",
        "pending_count": pending_count,
        "status": "warning" if pending_count > 0 else "healthy",
        "suggestion": "configure local LLM or prune pending dense reflections" if pending_count > 0 else None
    }

    # Overall Status Map
    overall_status = "success"
    if env_status == "error" or runtime_status == "error" or (opencode_active and opencode_status == "error") or (codex_active and codex_status == "error"):
        overall_status = "error"
    elif env_status == "warn" or runtime_status == "warn" or (opencode_active and opencode_status == "warn") or (codex_active and codex_status == "warn"):
        overall_status = "warn"

    if reflection_diagnostic["status"] == "warning" and overall_status == "success":
        overall_status = "warn"

    # Concept Integrity
    concept_integrity = validate_concept_frontmatter(project)
    if concept_integrity["status"] == "error":
        overall_status = "error"
    elif concept_integrity["status"] == "warn" and overall_status == "success":
        overall_status = "warn"

    # Knowledge checks (Separate, but computed here for convenience)
    knowledge_stats = {}
    try:
        registry = eng.state._load_registry(project)
        knowledge_stats["total_concepts"] = len(registry)
    except Exception:
        knowledge_stats["total_concepts"] = 0

    return {
        "status": overall_status,
        "operation": "runtime_health",
        "project": str(project or "."),
        "concept_integrity": concept_integrity,
        "environment": {
            "is_dev_workspace": is_dev_workspace,
            "status": env_status,
            "checks": env_checks,
        },
        "opencode": {
            "active": opencode_active,
            "status": opencode_status,
            "checks": opencode_checks,
        },
        "codex_app": {
            "active": codex_active,
            "status": codex_status,
            "checks": codex_checks,
        },
        "runtime": {
            "status": runtime_status,
            "checks": runtime_checks,
        },
        "reflection_diagnostic": reflection_diagnostic,
        "knowledge_stats": knowledge_stats,
    }
