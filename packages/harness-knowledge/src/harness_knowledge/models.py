from __future__ import annotations

import time
from typing import Literal
from pydantic import BaseModel, Field


class ConceptRelation(BaseModel):
    type: str
    target: str


class ConceptData(BaseModel):
    concept_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    status: Literal["candidate", "emerging", "validated", "canonical", "deprecated"] = (
        "candidate"
    )
    confidence: int = Field(default=1, ge=1, le=5)
    evidence_count: int = 0
    sessions: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    relationships: list[ConceptRelation] = Field(default_factory=list)


class KnowledgeEvent(BaseModel):
    event_id: str = Field(description="UUID string for event identification")
    timestamp: str = Field(description="ISO 8601 UTC timestamp format")
    project: str = Field(description="Project identifier/directory name")
    session_id: str = Field(description="Source session ID")
    event_type: str = Field(
        description="Type: hypothesis, experiment, validation, failure, decision, deprecation, observation"
    )
    concept_candidates: list[str] = Field(
        default_factory=list, description="Associated concepts"
    )
    summary: str = Field(description="Short human-readable summary")
    evidence: str = Field(description="Log snippet or conversation line")
    confidence: int = Field(
        default=1, ge=1, le=5, description="Confidence rating (1-5)"
    )
    source: str = Field(description="Source of capture: chat, diff, test")
    schema_version: int = Field(default=1)

    # Orchestrator Telemetry Integration
    tokens: dict[str, int] | None = Field(
        default=None, description="Prompt token count details (input/output/total)"
    )
    cost: float | None = Field(default=None, description="API invocation cost in USD")
    duration_s: float | None = Field(
        default=None, description="Subprocess run duration in seconds"
    )
