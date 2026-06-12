from __future__ import annotations

import os
import sys
import math
import time
import shutil
import json
import re
from pathlib import Path

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


def build_runtime_health(project: str | None = None) -> dict:
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
        _ = eng.search.stats()
        env_checks.append({"name": "Search Pipeline Available", "status": "success"})
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

    # LLM Reflection (Capability/Config check, NO slow LLM call)
    if os.environ.get("OEM_LLM_DEGRADED") == "true":
        runtime_checks.append({"name": "LLM Reflection Degraded", "status": "warn"})
        if runtime_status == "success":
            runtime_status = "warn"
    else:
        # Check environment/config keys for LLM
        from oem_knowledge.services.reflection import llm_extraction_available
        if llm_extraction_available():
            runtime_checks.append({"name": "LLM Reflection Ready", "status": "success"})
        else:
            runtime_checks.append({"name": "LLM Reflection Degraded (No API key or provider found)", "status": "warn"})
            if runtime_status == "success":
                runtime_status = "warn"

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
        outcomes_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_checks.append({"name": "Outcome Tracking Ready", "status": "success"})
    except Exception as e:
        runtime_checks.append({"name": f"Outcome Tracking not ready: {e}", "status": "error"})
        runtime_status = "error"

    # Overall Status Map
    overall_status = "success"
    if env_status == "error" or runtime_status == "error" or (opencode_active and opencode_status == "error") or (codex_active and codex_status == "error"):
        overall_status = "error"
    elif env_status == "warn" or runtime_status == "warn" or (opencode_active and opencode_status == "warn") or (codex_active and codex_status == "warn"):
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
        "knowledge_stats": knowledge_stats,
    }
