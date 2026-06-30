from __future__ import annotations
import re
import json
import fnmatch
from typing import Any

GENERIC_WORDS: frozenset[str] = frozenset({
    "current", "project", "content", "work", "continue",
    "session", "context", "file", "task", "next", "now",
    "state", "start", "working", "pick", "left", "resume",
    "machine", "contract",
})


def _tokenize_lower(text: str) -> set[str]:
    return set(re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", text.lower()))


def _title_overlap_is_generic(title: str, task: str) -> bool:
    """Return True if the only tokens overlapping between title and task
    are generic words."""
    title_tokens = _tokenize_lower(title)
    task_tokens = _tokenize_lower(task)
    overlap = title_tokens & task_tokens
    if not overlap:
        return False
    non_generic = overlap - GENERIC_WORDS
    return len(non_generic) == 0

def match_directives(
    task: str,
    active_directives: list[dict],
    matched_skills: list[Any] = [],
    matched_concepts: list[Any] = []
) -> list[dict]:
    matched = []
    
    task_lower = task.lower()
    # Simple tokenization
    task_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", task_lower))
    
    skill_ids = {getattr(s, "id", None) or s.get("id") for s in matched_skills if s}
    concept_ids = {getattr(c, "id", None) or c.get("id") for c in matched_concepts if c}
    
    # Simple extraction of file paths/names in the task text
    task_paths = re.findall(r"\b[a-zA-Z0-9_\-\.\*\/]+\.[a-zA-Z0-9_]+\b", task_lower)
    
    for d in active_directives:
        if d.get("status") != "active":
            continue
            
        score = 0.0
        reasons = []
        
        title_lower = (d.get("title") or "").lower()
        rule_lower = (d.get("rule") or "").lower()
        scope_lower = (d.get("scope") or "").lower()
        priority = d.get("priority") or "normal"
        
        triggers = set(json.loads(d.get("triggers_json") or "[]"))
        forbidden_actions = set(json.loads(d.get("forbidden_actions_json") or "[]"))
        related_concepts = set(json.loads(d.get("related_concepts_json") or "[]"))
        related_skills = set(json.loads(d.get("related_skills_json") or "[]"))
        
        # 1. Critical directive exact trigger match: +8
        trigger_matches = task_tokens.intersection(triggers)
        if priority == "critical" and trigger_matches:
            score += 8.0
            reasons.append(f"critical directive trigger match: {', '.join(sorted(trigger_matches))}")
        # 2. Trigger/token overlap: +3 (if not already counted under critical exact match)
        elif trigger_matches:
            score += 3.0
            reasons.append(f"trigger overlap: {', '.join(sorted(trigger_matches))}")
            
        # 3. Workflow directive match: +6
        # Matches if rule/title is workflow-related and task also matches workflow terms
        is_wf_directive = "workflow" in title_lower or "workflow" in rule_lower or "workflow" in scope_lower
        is_wf_task = "workflow" in task_lower or "wf" in task_lower
        if is_wf_directive and is_wf_task:
            score += 6.0
            reasons.append("workflow directive match")
            
        # 4. Directive title match: +5 (halved if generic-token-only overlap)
        title_match_is_generic = False
        if title_lower and title_lower in task_lower:
            title_match_is_generic = _title_overlap_is_generic(title_lower, task_lower)
            if title_match_is_generic:
                score += 2.5
                reasons.append(f"title match (generic overlap): '{title_lower}'")
            else:
                score += 5.0
                reasons.append(f"title match: '{title_lower}'")
            
        # 5. Directive scope match: +4
        if scope_lower and (scope_lower in task_lower or scope_lower.replace("_", " ") in task_lower):
            score += 4.0
            reasons.append(f"scope match: '{scope_lower}'")
            
        # 6. Related skill/concept match: +2
        matched_rel_concepts = concept_ids.intersection(related_concepts)
        matched_rel_skills = skill_ids.intersection(related_skills)
        if matched_rel_concepts or matched_rel_skills:
            score += 2.0
            matched_items = list(matched_rel_concepts) + list(matched_rel_skills)
            reasons.append(f"related skill/concept match: {', '.join(matched_items)}")
            
        # 7. File glob/path match: +2
        glob_matched = False
        matched_globs = []
        for glob in forbidden_actions:
            # Check if this glob is a file pattern
            if "." in glob or "*" in glob:
                for path in task_paths:
                    if fnmatch.fnmatch(path, glob) or glob in path or path in glob:
                        glob_matched = True
                        matched_globs.append(glob)
                        break
        if glob_matched:
            score += 2.0
            reasons.append(f"file pattern match: {', '.join(matched_globs)}")
            
        if score >= 4.0:
            matched.append({
                "id": d["id"],
                "title": d["title"],
                "rule": d["rule"],
                "source_path": d["source_path"],
                "line_start": d["line_start"],
                "line_end": d["line_end"],
                "score": score,
                "reason": "; ".join(reasons) if reasons else "score criteria met",
                "related_concepts": list(related_concepts),
                "related_skills": list(related_skills),
                "priority": priority,
                "scope": d["scope"],
                "title_match_is_generic": title_match_is_generic,
                "always_on": d.get("always_on", False) or d.get("scope") == "global",
            })
            
    # Sort matched descending by score
    matched.sort(key=lambda x: x["score"], reverse=True)
    return matched

def resolve_selected_workflow(task: str, matched_directives: list[dict]) -> dict | None:
    # If a workflow is deterministically matched
    # We check if any matched directive references workflows, or if matched directives
    # have specific scopes that imply a workflow
    for md in matched_directives:
        scope = md.get("scope", "")
        # If the matched directive has a workflow scope
        if scope.startswith("workflow_") or "workflow" in scope:
            return {
                "id": scope,
                "reason": f"matched directive scope '{scope}' and task keywords"
            }
            
    # Fallback to keyword matching in task
    task_lower = task.lower()
    if "opencode" in task_lower and ("config" in task_lower or "adapter" in task_lower):
        return {
            "id": "workflow_opencode_adapter_change",
            "reason": "matched task keywords for opencode adapter workflow"
        }
        
    return None
