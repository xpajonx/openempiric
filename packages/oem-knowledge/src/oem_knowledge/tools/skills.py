from __future__ import annotations

import json
from pathlib import Path

from ..engine import KnowledgeEngine
from ..project import (
    resolve_active_project,
    handle_resolution_error,
    ProjectResolutionError
)

def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    @mcp.tool()
    def knowledge_skill_candidates(project: str = "") -> str:
        """List all oem skill candidates.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                candidates = eng.skills.list_skill_candidates(str(project_root))
                if not candidates:
                    return json.dumps({
                        "status": "success",
                        "operation": "knowledge_skill_candidates",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": "No skill candidates found.",
                        "candidates": []
                    }, indent=2)
                
                lines = [
                    "| Slug | Title | Confidence | Status | Evidence Count |",
                    "| --- | --- | --- | --- | --- |"
                ]
                for c in candidates:
                    lines.append(f"| {c.slug} | {c.title} | {c.confidence} | {c.status} | {len(c.evidence)} |")
                panel = "\n".join(lines)
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidates",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel,
                    "candidates": [c.to_dict() if hasattr(c, "to_dict") else vars(c) for c in candidates]
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidates", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidates",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_skill_candidate_show(slug: str, project: str = "") -> str:
        """Show detailed candidate or approved skill.

        Args:
            slug: The slug of the skill candidate or approved skill.
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                candidate = eng.skills.load_skill_candidate(slug, str(project_root))
                if not candidate:
                    layout = eng.layout(str(project_root))
                    approved_path = layout.skills_dir / f"{slug}.md"
                    if approved_path.exists():
                        content = approved_path.read_text(encoding="utf-8")
                        return json.dumps({
                            "status": "success",
                            "operation": "knowledge_skill_candidate_show",
                            "project_root": str(project_root),
                            "memory_root": str(memory_root),
                            "message": content
                        }, indent=2)
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_skill_candidate_show",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": f"Candidate/Skill '{slug}' not found."
                    }, indent=2)
                
                lines = [
                    f"# Skill Candidate: {candidate.title}",
                    "",
                    f"- **Slug**: {candidate.slug}",
                    f"- **Status**: {candidate.status}",
                    f"- **Confidence**: {candidate.confidence}",
                    "",
                    "## Trigger",
                    candidate.trigger,
                    "",
                    "## Recommended behavior",
                    candidate.recommended_behavior,
                    "",
                    "## Rationale",
                    candidate.rationale,
                    "",
                    "## Evidence",
                ]
                for ev in candidate.evidence:
                    lines.append(f"- {ev}")
                panel = "\n".join(lines)
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidate_show",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel,
                    "candidate": candidate.to_dict() if hasattr(candidate, "to_dict") else vars(candidate)
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidate_show", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_show",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_skill_candidate_approve(slug: str = "", force: bool = False, project: str = "") -> str:
        """Approve a skill candidate and promote it to a project skill.

        Args:
            slug: The slug of the skill candidate.
            force: Force approval even if rejected previously.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_approve",
                "message": "Slug is required."
            }, indent=2)
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "approved", str(project_root), force=force)
                if not cand:
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_skill_candidate_approve",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": f"Candidate '{slug}' not found."
                    }, indent=2)
                panel = (
                    "Status: approved\n"
                    f"Skill: {slug}\n"
                    f"Approved skill written: .oem/skills/{slug}.md"
                )
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidate_approve",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidate_approve", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_approve",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_skill_candidate_reject(slug: str = "", project: str = "") -> str:
        """Reject a skill candidate.

        Args:
            slug: The slug of the skill candidate.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_reject",
                "message": "Slug is required."
            }, indent=2)
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "rejected", str(project_root))
                if not cand:
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_skill_candidate_reject",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": f"Candidate '{slug}' not found."
                    }, indent=2)
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidate_reject",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": f"Status: rejected\nSkill: {slug}"
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidate_reject", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_reject",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_skill_candidate_defer(slug: str = "", project: str = "") -> str:
        """Defer a oem skill candidate.

        Args:
            slug: The slug of the skill candidate.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_defer",
                "message": "Slug is required."
            }, indent=2)
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "deferred", str(project_root))
                if not cand:
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_skill_candidate_defer",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": f"Candidate '{slug}' not found."
                    }, indent=2)
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidate_defer",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": f"Status: deferred\nSkill: {slug}"
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidate_defer", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_defer",
                "message": str(e)
            }, indent=2)
