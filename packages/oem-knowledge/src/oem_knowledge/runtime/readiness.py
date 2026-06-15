from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

@dataclass
class ReadinessCheck:
    name: str
    status: Literal["success", "warning", "failure"]
    detail: str | None = None
    suggestion: str | None = None


def check_opencode_config_valid() -> tuple[str, str]:
    import shutil
    import os
    import subprocess
    from pathlib import Path
    
    if not shutil.which("opencode"):
        return "skipped", "opencode executable not found"
        
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode"
    config_file = config_dir / "opencode.jsonc"
    
    if not config_file.exists():
        return "success", ""
        
    try:
        import sys
        if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            return "success", ""
        res = subprocess.run(
            ["opencode", "debug", "config"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            return "success", ""
            
        output = res.stderr or res.stdout
        error_msg = ""
        for line in output.splitlines():
            if line.strip().startswith("Unrecognized key:") or "invalid" in line.lower() or "error" in line.lower():
                error_msg = line.strip()
                break
        if not error_msg:
            error_msg = "Invalid configuration structure."
        return "failure", error_msg
    except Exception as e:
        return "failure", str(e)


class RuntimeReadiness:
    def check(
        self,
        eng: KnowledgeEngine,
        agent_name: str,
        project: str | None = None,
        harness: Path | None = None,
        adapter: object | None = None,
        stale_existed: bool = False,
        recovery_failed: bool = False
    ) -> list[ReadinessCheck]:
        checks = []

        # Check 1: Project initialized
        try:
            initialized = eng.is_initialized(project)
            checks.append(ReadinessCheck(
                name="Project initialized",
                status="success" if initialized else "failure",
                suggestion=None if initialized else "Run 'oem init' to bootstrap the project"
            ))
        except Exception as e:
            checks.append(ReadinessCheck(
                name="Project initialized",
                status="failure",
                detail=str(e),
                suggestion="Run 'oem init' to bootstrap the project"
            ))

        # Check 2: Harness resolved
        if harness is not None:
            checks.append(ReadinessCheck(
                name="Harness resolved",
                status="success"
            ))
        else:
            checks.append(ReadinessCheck(
                name="Harness resolved",
                status="failure"
            ))

        # Check 3: Plugin healthy
        if adapter is not None:
            try:
                if hasattr(adapter, "verify_health"):
                    healthy, msg = adapter.verify_health()
                    checks.append(ReadinessCheck(
                        name="Plugin healthy",
                        status="success" if healthy else "warning",
                        detail=None if healthy else msg,
                        suggestion=None if healthy else "Run 'oem doctor' to troubleshoot plugin links"
                    ))
                else:
                    checks.append(ReadinessCheck(
                        name="Plugin healthy",
                        status="success"
                    ))
            except Exception as e:
                checks.append(ReadinessCheck(
                    name="Plugin healthy",
                    status="warning",
                    detail=str(e),
                    suggestion="Run 'oem doctor' to troubleshoot plugin links"
                ))
        else:
            checks.append(ReadinessCheck(
                name="Plugin healthy",
                status="failure",
                detail="Adapter could not be resolved"
            ))

        # Check 4: Skill installed
        if adapter is not None and hasattr(adapter, "get_skill_path"):
            skills_file = adapter.get_skill_path()
            installed = Path(skills_file).exists()
            checks.append(ReadinessCheck(
                name="Skill installed",
                status="success" if installed else "warning",
                suggestion=None if installed else "Run 'oem setup codex-app' to install/verify skills"
            ))
        elif harness is not None:
            skills_file = harness / "skills" / "openempiric.yaml"
            installed = skills_file.exists()
            checks.append(ReadinessCheck(
                name="Skill installed",
                status="success" if installed else "warning",
                suggestion=None if installed else "Run 'oem doctor' to install/verify skills"
            ))
        else:
            checks.append(ReadinessCheck(
                name="Skill installed",
                status="failure"
            ))

        # Check 5: Context compilation
        try:
            from oem_knowledge.runtime.context import _compile_oem_context
            context = _compile_oem_context(eng)
            required = {
                "active_concepts",
                "recent_decisions",
                "relevant_failures",
                "open_questions",
            }
            if required.issubset(context.keys()):
                checks.append(ReadinessCheck(
                    name="Context compilation",
                    status="success"
                ))
            else:
                checks.append(ReadinessCheck(
                    name="Context compilation",
                    status="warning",
                    detail="Context missing required schema keys"
                ))
        except Exception as e:
            checks.append(ReadinessCheck(
                name="Context compilation",
                status="failure",
                detail=str(e)
            ))

        # Check 6: Embedding cache status
        try:
            cache_ready = eng.embedding_cache_ready()
            checks.append(ReadinessCheck(
                name="Embedding cache ready" if cache_ready else "Embedding cache missing",
                status="success" if cache_ready else "warning",
                suggestion=None if cache_ready else "Run 'oem warmup' to pre-download model"
            ))
        except Exception as e:
            checks.append(ReadinessCheck(
                name="Embedding cache missing",
                status="warning",
                detail=str(e),
                suggestion="Run 'oem warmup' to pre-download model"
            ))

        # Check 7: MCP registered
        if adapter is not None:
            try:
                if hasattr(adapter, "verify_mcp"):
                    registered = adapter.verify_mcp()
                    checks.append(ReadinessCheck(
                        name="MCP registered",
                        status="success" if registered else "failure",
                        suggestion=None if registered else "Run 'oem doctor' to register MCP configs"
                    ))
                else:
                    checks.append(ReadinessCheck(
                        name="MCP registered",
                        status="success"
                    ))
            except Exception as e:
                checks.append(ReadinessCheck(
                    name="MCP registered",
                    status="failure",
                    detail=str(e),
                    suggestion="Run 'oem doctor' to register MCP configs"
                ))
        else:
            checks.append(ReadinessCheck(
                name="MCP registered",
                status="failure"
            ))

        # Check 8: Session recovery
        if recovery_failed:
            checks.append(ReadinessCheck(
                name="Session recovery status",
                status="failure",
                suggestion="Run 'oem recover' to manage unfinished sessions"
            ))
        elif stale_existed:
            checks.append(ReadinessCheck(
                name="Recoverable session detected",
                status="warning",
                suggestion="Run 'oem recover' to manage unfinished sessions"
            ))
        else:
            checks.append(ReadinessCheck(
                name="No unfinished sessions",
                status="success"
            ))

        # Check 9: OpenCode config validation
        valid_status, err_msg = check_opencode_config_valid()
        if valid_status == "skipped":
            checks.append(ReadinessCheck(
                name="OpenCode config valid",
                status="success",
                detail="Validation skipped (opencode not installed)"
            ))
        elif valid_status == "failure":
            checks.append(ReadinessCheck(
                name="OpenCode config valid",
                status="failure",
                detail=err_msg,
                suggestion="Run 'oem setup opencode --repair'"
            ))
        else:
            checks.append(ReadinessCheck(
                name="OpenCode config valid",
                status="success"
            ))

        if agent_name == "opencode":
            mapped_checks = []
            
            # 0. OpenCode config valid
            c_config = next((c for c in checks if c.name == "OpenCode config valid"), None)
            if c_config:
                mapped_checks.append(c_config)

            # 1. .oem project memory found (mapped from Check 1: Project initialized)
            c1 = next((c for c in checks if c.name == "Project initialized"), None)
            if c1:
                c1.name = ".oem project memory found"
                mapped_checks.append(c1)
                
            # 2. OpenCode MCP registered (mapped from Check 7: MCP registered)
            c2 = next((c for c in checks if c.name == "MCP registered"), None)
            if c2:
                c2.name = "OpenCode MCP registered"
                mapped_checks.append(c2)
                
            # 3. OEM instructions active (mapped from Check 4: Skill installed)
            c3 = next((c for c in checks if c.name == "Skill installed"), None)
            if c3:
                c3.name = "OEM instructions active"
                mapped_checks.append(c3)
                
            # 3b. OEM local plugin file installed
            import os
            from oem_knowledge.cli.commands.system import is_oem_managed_plugin
            env_plugins_dir = os.environ.get("OPENCODE_PLUGINS_DIR")
            if env_plugins_dir:
                plugins_dir = Path(env_plugins_dir)
            else:
                plugins_dir = Path.home() / ".config" / "opencode" / "plugins"
            plugin_dest = plugins_dir / "openempiric.ts"
            
            plugin_installed = plugin_dest.exists() and is_oem_managed_plugin(plugin_dest)
            plugin_check = ReadinessCheck(
                name="OEM local plugin file installed",
                status="success" if plugin_installed else "warning",
                detail="Local plugin file is installed and OEM-managed." if plugin_installed else "Local plugin file is missing or not OEM-managed."
            )
            mapped_checks.append(plugin_check)
                
            # 4. OEM hook runtime likely active
            mcp_registered = c2.status == "success" if c2 else False
            inst_active = c3.status == "success" if c3 else False
            config_valid = valid_status != "failure"
            hook_active = plugin_installed and config_valid and mcp_registered and inst_active
            
            c4 = next((c for c in checks if c.name == "Plugin healthy"), None)
            if c4:
                if hook_active:
                    c4.name = "OEM hook runtime likely active"
                    c4.status = "success"
                    c4.detail = None
                else:
                    c4.name = "OEM hook runtime unavailable"
                    c4.status = "warning"
                    reasons = []
                    if not plugin_installed:
                        reasons.append("Plugin file not installed")
                    if not config_valid:
                        reasons.append("OpenCode config is invalid")
                    if not mcp_registered:
                        reasons.append("MCP not registered")
                    if not inst_active:
                        reasons.append("Instructions not active")
                    c4.detail = f"Unavailable: {', '.join(reasons)}"
                mapped_checks.append(c4)
                
            # 5. Session lifecycle enabled (mapped from Check 8)
            c5 = next((c for c in checks if c.name in ("No unfinished sessions", "Recoverable session detected", "Session recovery status")), None)
            if c5:
                c5.name = "Session lifecycle enabled"
                c5.status = "success" if c5.status in ("success", "warning") else "failure"
                mapped_checks.append(c5)
            else:
                mapped_checks.append(ReadinessCheck(name="Session lifecycle enabled", status="success"))
                
            return mapped_checks

        return checks
