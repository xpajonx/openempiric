from __future__ import annotations
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class ReflectionService:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine

    def reflect_session(
        self,
        project: str | None = None,
        conversation_text: str = "",
        session_id: str = "",
        telemetry: dict | None = None,
    ) -> dict:
        # Avoid import loop by locally importing
        import time
        import uuid
        if not session_id:
            session_id = f"session_{time.strftime('%Y%m%d_%H%M%S')}"

        knowledge_events = []
        self.engine._resolve_harness(project)
        concepts_dir = self.engine._concepts_dir(project)

        modified_files = (
            list(concepts_dir.rglob("*.md")) if concepts_dir.exists() else []
        )
        for fp in modified_files:
            concept = fp.stem.replace("_", " ").replace("-", " ").title()
            knowledge_events.append(
                {
                    "type": "observation",
                    "concept": concept,
                    "evidence": f"Modified: {fp.name}",
                    "confidence": 1,
                    "source": "diff",
                }
            )

        text_clean = conversation_text.strip()
        if telemetry:
            duration = telemetry.get("duration_sec", 0)
            tool_calls = telemetry.get("total_tool_calls", 0)
            knowledge_events.append(
                {
                    "type": "telemetry",
                    "concept": "Session Metrics",
                    "evidence": f"Session duration: {duration}s, Tool calls: {tool_calls}",
                    "confidence": 1,
                    "source": "orchestrator",
                }
            )
        if text_clean.startswith("{"):
            try:
                data = json.loads(text_clean)
                if "knowledge_events" in data:
                    knowledge_events.extend(data["knowledge_events"])
            except Exception:
                pass
        else:
            for line in conversation_text.splitlines():
                lower = line.strip().lower()
                if lower.startswith("hypothesis:") or lower.startswith("hyp:"):
                    knowledge_events.append(
                        {
                            "type": "hypothesis",
                            "concept": lower.split(":", 1)[1].strip()[:80],
                            "evidence": line.strip(),
                            "confidence": 1,
                            "source": "chat",
                        }
                    )
                elif lower.startswith("experiment:") or lower.startswith("exp:"):
                    knowledge_events.append(
                        {
                            "type": "experiment",
                            "concept": lower.split(":", 1)[1].strip()[:80],
                            "evidence": line.strip(),
                            "confidence": 1,
                            "source": "chat",
                        }
                    )
                elif lower.startswith("validation:") or lower.startswith("val:"):
                    knowledge_events.append(
                        {
                            "type": "validation",
                            "concept": lower.split(":", 1)[1].strip()[:80],
                            "evidence": line.strip(),
                            "confidence": 1,
                            "source": "chat",
                        }
                    )
                elif lower.startswith("failure:") or lower.startswith("fail:"):
                    knowledge_events.append(
                        {
                            "type": "failure",
                            "concept": lower.split(":", 1)[1].strip()[:80],
                            "evidence": line.strip(),
                            "confidence": 1,
                            "source": "chat",
                        }
                    )
                elif lower.startswith("decision:") or lower.startswith("dec:"):
                    knowledge_events.append(
                        {
                            "type": "decision",
                            "concept": lower.split(":", 1)[1].strip()[:80],
                            "evidence": line.strip(),
                            "confidence": 1,
                            "source": "chat",
                        }
                    )

        seen = set()
        canonical_events = []
        for ev in knowledge_events:
            e_type = ev.get("type", "observation")
            concept_str = ev.get("concept", "General Learning")
            evidence = ev.get("evidence", "")
            key = (e_type, concept_str, evidence.lower())
            if key in seen:
                continue
            seen.add(key)

            event_id = str(uuid.uuid4())
            canonical_event = {
                "event_id": event_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "project": project or "default",
                "session_id": session_id,
                "event_type": e_type,
                "concept_candidates": [concept_str],
                "summary": f"{e_type.title()}: {concept_str}",
                "evidence": evidence,
                "confidence": ev.get("confidence", 1),
                "source": ev.get("source", "chat"),
                "schema_version": 1,
            }
            canonical_events.append(canonical_event)
            self.engine._append_event(canonical_event, project)

        sessions_dir = self.engine._sessions_dir(project)
        sfs = self.engine._sfs(project)
        date_str = time.strftime("%Y-%m-%d")
        report_file = sessions_dir / f"{date_str}.md"
        counter = 1
        while sfs.exists(report_file):
            report_file = sessions_dir / f"{date_str}_{counter}.md"
            counter += 1

        yaml_events = [
            {
                "type": e["event_type"],
                "concept": e["concept_candidates"][0],
                "evidence": e["evidence"],
                "confidence": e["confidence"],
            }
            for e in canonical_events
        ]
        yaml_content = json.dumps({"knowledge_events": yaml_events}, indent=2)

        report = f"""---
date: {date_str}
project: {project or "default"}
---
# Session Learning Report — {date_str}

## Knowledge Events
```json
{yaml_content}
```
"""
        sfs.write_text(report_file, report, force_allow_truncation=True)

        return {
            "status": "success",
            "report_path": str(report_file),
            "knowledge_events": yaml_events,
            "canonical_events": canonical_events,
        }
