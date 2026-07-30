from __future__ import annotations
import json
import logging
import re
from typing import TYPE_CHECKING
from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

def _compile_oem_context(eng: KnowledgeEngine) -> dict:
    """Build the OEMRuntimeContext dict from the engine's registry and events."""
    active_concepts = []
    try:
        registry = eng.state._load_registry()
        
        # Determine recommended concept IDs from session state (pre-search)
        rec_ids = set()
        try:
            session_state = eng.restore_session_state()
            rec_files = session_state.get("recommended_files", [])
            for f in rec_files:
                from pathlib import Path
                stem = Path(f).stem
                if stem.startswith("concept_") and stem in registry:
                    rec_ids.add(stem)
            
            # Boost active concepts directly
            for cid in session_state.get("active_concepts", []):
                if cid in registry:
                    rec_ids.add(cid)
        except Exception:
            pass

        from oem_knowledge.health import calculate_concept_health
        
        for cid, cdata in registry.items():
            if cdata.get("status") in ("validated", "canonical", "global"):
                desc = ""
                wiki_file = eng._concepts_dir() / f"{cid}.md"
                if wiki_file.exists():
                    try:
                        text = wiki_file.read_text(encoding="utf-8")
                        body_match = re.search(
                            r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.DOTALL
                        )
                        body = body_match.group(1).strip() if body_match else text.strip()
                        body = re.sub(r"^#.*?\n", "", body).strip()
                        desc = body.split("\n")[0][:150].strip()
                    except Exception:
                        pass
                
                health = calculate_concept_health(cdata)
                boost = 50.0 if cid in rec_ids else 0.0
                priority_score = health + boost

                active_concepts.append({
                    "id": cid,
                    "name": cdata.get("canonical_name", cid),
                    "description": desc,
                    "priority_score": priority_score,
                })
        
        # Sort concepts by priority_score descending
        active_concepts.sort(key=lambda c: c["priority_score"], reverse=True)
        # Clean up the priority_score key
        for c in active_concepts:
            c.pop("priority_score", None)

    except Exception as e:
        logging.warning("Failed to compile active concepts: %s", e)


    active_decisions = []
    relevant_failures = []
    try:
        events = eng.state._load_events(include_user=True)
        seen_decisions = set()
        for ev in reversed(events):
            if ev.get("event_type") == "decision":
                d = ev.get("evidence", "")
                if d and d not in seen_decisions:
                    scope = ev.get("scope", "project")
                    if scope == "user":
                        d = f"[User preference] {d}"
                    seen_decisions.add(d)
                    active_decisions.append(d)
                    if len(active_decisions) >= 5:
                        break
        active_decisions.reverse()

        seen_failures = set()
        for ev in reversed(events):
            if ev.get("event_type") == "failure":
                f = ev.get("evidence", "")
                if f and f not in seen_failures:
                    scope = ev.get("scope", "project")
                    if scope == "user":
                        f = f"[User preference] {f}"
                    seen_failures.add(f)
                    relevant_failures.append(f)
                    if len(relevant_failures) >= 5:
                        break
        relevant_failures.reverse()
    except Exception as e:
        logging.warning("Failed to compile events context: %s", e)

    open_questions = []
    try:
        session_state = eng.restore_session_state()
        open_questions = session_state.get("active_goals", [])
    except Exception as e:
        logging.warning("Failed to compile active goals: %s", e)

    last_topic = "General development"
    if open_questions:
        last_topic = open_questions[0]
        open_questions = open_questions[1:]

    return {
        "active_concepts": active_concepts,
        "recent_decisions": active_decisions,
        "relevant_failures": relevant_failures,
        "open_questions": open_questions,
        "last_topic": last_topic,
        "memory_context": (
            "# OEM Runtime Notice\n"
            "Project memory is already active. Relevant project memory has been restored automatically. "
            "OEM memory serves as a persistent knowledge layer.\n\n"
            f"{OEM_MEMORY_INSTRUCTIONS}"
        ),
    }
