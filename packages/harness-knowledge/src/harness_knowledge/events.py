from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


class KnowledgeEventType:
    HYPOTHESIS = "hypothesis"
    EXPERIMENT = "experiment"
    VALIDATION = "validation"
    FAILURE = "failure"
    DECISION = "decision"
    DEPRECATION = "deprecation"


@dataclass
class KnowledgeEvent:
    event_type: str
    summary: str
    detail: str = ""
    confidence: float = 0.5
    concepts: list[str] = field(default_factory=list)
    session_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "summary": self.summary,
            "detail": self.detail,
            "confidence": self.confidence,
            "concepts": self.concepts,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> KnowledgeEvent:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def extract_events_from_conversation(
    conversation_text: str, session_id: str = ""
) -> list[KnowledgeEvent]:
    """Extract structured Knowledge Events from raw conversation text.

    Uses simple heuristics to identify events. Can be replaced with LLM-based
    extraction by the orchestrator's lifecycle module.
    """
    events: list[KnowledgeEvent] = []
    lines = conversation_text.splitlines()

    for i, line in enumerate(lines):
        lower = line.strip().lower()

        if lower.startswith("hypothesis:") or lower.startswith("hyp:"):
            summary = line.split(":", 1)[1].strip()
            events.append(
                KnowledgeEvent(
                    event_type=KnowledgeEventType.HYPOTHESIS,
                    summary=summary[:200],
                    detail=line,
                    session_id=session_id,
                )
            )

        elif lower.startswith("experiment:") or lower.startswith("exp:"):
            summary = line.split(":", 1)[1].strip()
            events.append(
                KnowledgeEvent(
                    event_type=KnowledgeEventType.EXPERIMENT,
                    summary=summary[:200],
                    detail=line,
                    session_id=session_id,
                )
            )

        elif lower.startswith("validation:") or lower.startswith("val:"):
            summary = line.split(":", 1)[1].strip()
            events.append(
                KnowledgeEvent(
                    event_type=KnowledgeEventType.VALIDATION,
                    summary=summary[:200],
                    detail=line,
                    confidence=0.7,
                    session_id=session_id,
                )
            )

        elif lower.startswith("failure:") or lower.startswith("fail:"):
            summary = line.split(":", 1)[1].strip()
            events.append(
                KnowledgeEvent(
                    event_type=KnowledgeEventType.FAILURE,
                    summary=summary[:200],
                    detail=line,
                    session_id=session_id,
                )
            )

        elif lower.startswith("decision:") or lower.startswith("dec:"):
            summary = line.split(":", 1)[1].strip()
            events.append(
                KnowledgeEvent(
                    event_type=KnowledgeEventType.DECISION,
                    summary=summary[:200],
                    detail=line,
                    confidence=0.8,
                    session_id=session_id,
                )
            )

    return events
