from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

def execute_knowledge_read(eng: KnowledgeEngine, project: str | None = None, scope: str = "project") -> dict:
    from oem_knowledge.runtime.manifest import ensure_manifest
    from oem_knowledge.health import build_runtime_health
    
    # 1. Project identity
    proj_path = Path(project or eng.project_path or ".").resolve()
    manifest = ensure_manifest(proj_path)
    project_id = manifest.get("project_id", proj_path.name)
    
    # 2. Runtime status
    health = build_runtime_health(project)
    runtime_checks = health.get("runtime", {}).get("checks", [])
    runtime_status_list = [f"{c['name']}: {c['status']}" for c in runtime_checks]
    
    # 3. Active project memory / recent sessions / warnings
    warnings_list = []
    for check in health.get("environment", {}).get("checks", []) + runtime_checks:
        if check.get("status") == "warn":
            warnings_list.append(f"{check['name']} (Warning)")
        elif check.get("status") == "error":
            warnings_list.append(f"{check['name']} (Error)")
            
    # 4. Top/recent concepts
    important_concepts = []
    try:
        registry = eng.state._load_registry(project)
        # Sort concepts by confidence * evidence_count descending
        sorted_concepts = sorted(
            registry.values(),
            key=lambda c: (c.get("confidence", 1) * c.get("evidence_count", 0)),
            reverse=True
        )
        for c in sorted_concepts[:5]:
            cname = c.get("canonical_name", c.get("concept_id"))
            important_concepts.append(
                f"{cname} (Confidence: {c.get('confidence')}/5, Evidence: {c.get('evidence_count')})"
            )
    except Exception:
        pass
        
    # 5. Approved/active skills
    approved_skills = []
    try:
        candidates = eng.skills.list_skill_candidates(project)
        approved = [c for c in candidates if c.status == "approved"]
        for s in approved[:5]:
            approved_skills.append(f"{s.title}: {s.recommended_behavior}")
    except Exception:
        pass
        
    # Fallback to standard guidelines if no skills
    if not approved_skills:
        approved_skills = [
            "Use structured events for reflection when capturing learnings.",
            "Do not edit .oem files manually."
        ]
        
    # 6. Suggested next searches
    suggested_searches = [
        f"knowledge_search(\"current tasks\")",
        f"knowledge_search(\"conventions\")"
    ]
    if important_concepts:
        # Suggest searching for the top concept
        top_name = important_concepts[0].split(" (")[0]
        suggested_searches.append(f"knowledge_search(\"{top_name}\")")

    sections = {
        "runtime_status": runtime_status_list,
        "active_project_memory": important_concepts,
        "recent_sessions": [f"Last outcome: success"] if eng.is_initialized(project) else ["No session history"],
        "important_concepts": important_concepts,
        "approved_skills": approved_skills,
        "warnings": warnings_list,
        "suggested_next_searches": suggested_searches
    }
    
    summary = (
        f"OEM project memory is active.\n\n"
        f"Project:\n"
        f"- {project_id}\n\n"
        f"Runtime:\n"
        f"{chr(10).join('- ' + s for s in runtime_status_list[:3])}\n\n"
        f"Approved/active skills:\n"
        f"{chr(10).join('- ' + s for s in approved_skills[:3])}\n\n"
        f"Suggested next memory calls:\n"
        f"{chr(10).join('- ' + s for s in suggested_searches[:3])}"
    )

    return {
        "status": "success",
        "operation": "knowledge_read",
        "scope": scope,
        "project": str(proj_path),
        "summary": summary,
        "sections": sections
    }
