import os
import json
import re
from pathlib import Path

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_ACTION_ITEMS = re.compile(r"\d+\.\s+|[-*]\s+")
_COORDINATORS = re.compile(r"(?<=\w)\s+(and|then|also|plus)\s+(?=\w)", re.IGNORECASE)
_FILE_REFS = re.compile(r"\b[\w./-]+\.\w{1,5}\b")


def decompose(prompt: str) -> list[str]:
    """Split a prompt into sub-tasks.

    This method delegates to a small LLM when the HARNESS_PLANNER_LLM env var is set,
    otherwise falls back to deterministic regex-based decomposition.
    """
    if os.environ.get("HARNESS_PLANNER_LLM"):
        try:
            from .client import run

            prompt_instructions = (
                "Decompose the following prompt/task into a list of independent sub-tasks. "
                "You MUST return ONLY a raw JSON array of strings, where each string is a task description, and no extra conversational text or markdown blocks.\n"
                f"Task: {prompt}"
            )
            result = run(prompt_instructions)
            text = result.text.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if len(lines) >= 3:
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines[-1].startswith("```"):
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()

            sub_tasks = json.loads(text)
            if isinstance(sub_tasks, list) and all(
                isinstance(t, str) for t in sub_tasks
            ):
                return sub_tasks
        except Exception:
            pass

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
        lines = [line.strip() for line in prompt.split("\n") if line.strip()]
        long_lines = [line for line in lines if len(line) > 30]
        if len(long_lines) >= 2:
            return long_lines[:4]

    coord_sections = _COORDINATORS.split(prompt)
    coord_sections = [
        s.strip() for s in coord_sections if s.strip() and len(s.strip()) > 8
    ]
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
