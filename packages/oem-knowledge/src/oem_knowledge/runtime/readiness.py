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

        return checks
