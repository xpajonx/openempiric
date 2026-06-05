from __future__ import annotations
import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

def _compile_oem_context(eng: KnowledgeEngine) -> dict:
    """Build the OEMRuntimeContext dict from the engine's registry and events."""
    active_concepts = []
    try:
        registry = eng._load_registry()
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
                active_concepts.append({
                    "id": cid,
                    "name": cdata.get("canonical_name", cid),
                    "description": desc,
                })
    except Exception as e:
        logging.warning("Failed to compile active concepts: %s", e)

    active_decisions = []
    relevant_failures = []
    try:
        events = eng._load_events()
        seen_decisions = set()
        for ev in reversed(events):
            if ev.get("event_type") == "decision":
                d = ev.get("evidence", "")
                if d and d not in seen_decisions:
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

    return {
        "active_concepts": active_concepts,
        "active_decisions": active_decisions,
        "relevant_failures": relevant_failures,
        "open_questions": open_questions,
    }
