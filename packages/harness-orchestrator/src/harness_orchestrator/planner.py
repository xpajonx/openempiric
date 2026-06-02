from __future__ import annotations

import re
from pathlib import Path

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_ACTION_ITEMS = re.compile(r"\d+\.\s+|[-*]\s+")
_COORDINATORS = re.compile(r"(?<=\w)\s+(and|then|also|plus)\s+(?=\w)", re.IGNORECASE)
_FILE_REFS = re.compile(r"\b[\w./-]+\.\w{1,5}\b")


def decompose(prompt: str) -> list[str]:
    """Deterministically split a prompt into sub-tasks. No LLM call.
    
    This is the v0.1 deterministic planner. In v0.2, this method can optionally
    delegate to a small LLM when the HARNESS_PLANNER_LLM env var is set.
    
    Decomposition strategies (in priority order):
    1. Numbered action items (1., 2., 3.)
    2. Bullet items (-, *)
    3. Line breaks (paragraphs)
    4. Coordinators (and, then, also)
    5. File references (multiple files → per-file tasks)
    """
    candidates: list[str] = []

    numbered = _ACTION_ITEMS.findall(prompt)
    if numbered:
        raw_sections = _ACTION_ITEMS.split(prompt)
        for sec in raw_sections:
            sec = sec.strip()
            if len(sec) > 10:
                candidates.append(sec)
        if len(candidates) >= 2:
            return candidates[:4]

    if "\n" in prompt:
        lines = [l.strip() for l in prompt.split("\n") if l.strip()]
        long_lines = [l for l in lines if len(l) > 30]
        if len(long_lines) >= 2:
            return long_lines[:4]

    coord_sections = _COORDINATORS.split(prompt)
    coord_sections = [s.strip() for s in coord_sections if s.strip() and len(s.strip()) > 8]
    if len(coord_sections) >= 2:
        return coord_sections[:4]

    files = _FILE_REFS.findall(prompt)
    if len(files) >= 2:
        file_groups: dict[str, list[str]] = {}
        for f in files:
            ext = Path(f).suffix
            file_groups.setdefault(ext, []).append(f)
        for ext, group in file_groups.items():
            fnames = ", ".join(group[:3])
            candidates.append(f"Process {fnames}")
        if len(candidates) >= 2:
            return candidates[:4]

    return [prompt]
