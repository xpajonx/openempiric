from __future__ import annotations

import json
from difflib import SequenceMatcher

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.ui import render_panel


def _concept_text(concept: dict) -> str:
    """Build a matchable text string from a registry concept entry."""
    parts = [concept.get("canonical_name", "")]
    parts.extend(concept.get("aliases", []))
    parts.append(concept.get("concept_id", ""))
    return " ".join(parts)


def _compute_matches(
    event: dict, registry: dict, top_n: int = 3
) -> list[tuple[str, str, float]]:
    """Return the top N (concept_id, canonical_name, score) matches for an event.

    Scores are based on SequenceMatcher similarity between the event's
    evidence/summary text and each concept's canonical name and aliases.
    """
    evidence_text = f"{event.get('summary', '')} {event.get('evidence', '')}"
    scored: list[tuple[str, str, float]] = []
    for cid, concept in registry.items():
        name = concept.get("canonical_name", cid)
        ct = _concept_text(concept)
        score = SequenceMatcher(None, evidence_text.lower(), ct.lower()).ratio()
        if score > 0.0:
            scored.append((cid, name, score))

    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:top_n]


def _render_event_row(
    idx: int, event: dict, matches: list[tuple[str, str, float]]
) -> list[str]:
    """Format a single event as panel lines."""
    lines = [
        f"Event #{idx}",
        f"  ID:        {event.get('event_id', '?')}",
        f"  Type:      {event.get('event_type', '?')}",
        f"  Session:   {event.get('session_id', '?')}",
        f"  Summary:   {event.get('summary', '')[:200]}",
        f"  Evidence:  {event.get('evidence', '')[:200]}",
        "",
        "  Top concept matches:",
    ]
    if not matches:
        lines.append("    (no matches found)")
    else:
        for cid, name, score in matches:
            pct = round(score * 100, 1)
            lines.append(f"    [{cid}] {name}  (confidence: {pct}%)")
    return lines


def _prompt_action(event: dict, matches: list[tuple[str, str, float]]) -> str:
    """Prompt the user for an action on a single event.

    Returns one of:
      - A concept ID string to assign the event to
      - "__create__" to signal the caller should create a new concept
      - "__skip__" to skip this event
      - "__quit__" to abort the review
      - None on empty input (treated as skip)
    """
    while True:
        try:
            inp = input("  Action ([1-{n}]/c[n]/s/q, default=s): ".format(
                n=len(matches) if matches else 0
            )).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "__quit__"

        if not inp or inp == "s":
            return "__skip__"
        if inp == "q":
            return "__quit__"
        if inp.startswith("c"):
            return "__create__"
        if inp.isdigit():
            n = int(inp)
            if 1 <= n <= len(matches):
                return matches[n - 1][0]
        print("  Invalid choice. Enter a number, c (create), s (skip), or q (quit).")


def _save_assignment(
    eng: KnowledgeEngine,
    event: dict,
    concept_id: str,
    project: str | None,
) -> None:
    """Persist the event with its new concept assignment back to events.jsonl."""
    concept_candidates = event.get("concept_candidates", [])
    if concept_id not in concept_candidates:
        concept_candidates.append(concept_id)
    event["concept_candidates"] = concept_candidates

    # Rewrite the full events file with this event updated in-place
    events_path = eng._events_path(project)
    all_events = eng.state._load_events(project)
    sfs = eng._sfs(project)

    for i, ev in enumerate(all_events):
        if ev.get("event_id") == event.get("event_id"):
            all_events[i] = event
            break

    sfs.write_text(
        events_path,
        "\n".join(json.dumps(ev, default=str) for ev in all_events) + "\n",
    )
    print(f"  -> Assigned event to concept '{concept_id}'")


def _create_new_concept(
    eng: KnowledgeEngine,
    registry: dict,
    event: dict,
    project: str | None,
) -> str | None:
    """Prompt for a new concept name and create it in the registry.

    Returns the new concept_id, or None if cancelled.
    """
    try:
        name = input("  New concept name: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not name:
        print("  Cancelled.")
        return None

    import time
    import uuid

    cid = f"concept_{uuid.uuid4().hex[:8]}"
    registry[cid] = {
        "concept_id": cid,
        "canonical_name": name,
        "aliases": [],
        "status": "candidate",
        "confidence": 1,
        "evidence_count": 0,
        "sessions": [],
        "created_at": time.time(),
        "updated_at": time.time(),
        "relationships": [],
        "source_event_ids": [],
    }

    # Save registry
    eng.state._save_registry(registry, project)
    print(f"  Created new concept '{name}' with ID {cid}")
    return cid


def run_review_command(args) -> None:
    """Interactive (or batch) review queue for unassigned events."""
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    project = getattr(args, "project", None)
    if project == ".":
        project = None

    batch_mode = getattr(args, "batch", False)

    eng = KnowledgeEngine(project)
    import atexit

    atexit.register(eng.close)

    events = eng.state._load_events(project)
    registry = eng.state._load_registry(project)

    # Filter to unassigned events
    unassigned = [
        ev
        for ev in events
        if not ev.get("concept_candidates")
    ]

    if not unassigned:
        print(render_panel("Review Queue", ["No unassigned events found."], status="ok"))
        return

    total = len(unassigned)
    print(render_panel(
        "Review Queue",
        [f"Total events: {len(events)}", f"Unassigned:   {total}"],
        status="info",
    ))

    idx = 0
    for idx, event in enumerate(unassigned, start=1):
        matches = _compute_matches(event, registry)
        lines = _render_event_row(idx, event, matches)

        if batch_mode:
            print(render_panel(
                f"Unassigned Event {idx}/{total}",
                lines,
                status="info",
            ))
            continue

        print(render_panel(
            f"Event {idx}/{total}",
            lines,
            status="info",
        ))

        action = _prompt_action(event, matches)

        if action == "__quit__":
            print(f"\nReview stopped at event {idx}/{total}.")
            break
        elif action == "__skip__":
            print("  Skipped.")
            continue
        elif action == "__create__":
            cid = _create_new_concept(eng, registry, event, project)
            if cid is not None:
                _save_assignment(eng, event, cid, project)
            continue
        else:
            # action is a concept ID
            _save_assignment(eng, event, action, project)

    print(render_panel(
        "Review Complete",
        [f"Reviewed {idx}/{total} unassigned events."],
        status="ok",
    ))
