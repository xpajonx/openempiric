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
    status: Literal["candidate", "emerging", "validated", "canonical", "needs_review", "deprecated"] = (
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
    source_type: str | None = Field(default=None)
    ingestion_eligible: bool | None = Field(default=None)

    # Orchestrator Telemetry Integration
    tokens: dict[str, int] | None = Field(
        default=None, description="Prompt token count details (input/output/total)"
    )
    cost: float | None = Field(default=None, description="API invocation cost in USD")
    duration_s: float | None = Field(
        default=None, description="Subprocess run duration in seconds"
    )


class TodoItem(BaseModel):
    id: str = Field(description="UUID string for todo identification")
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"
    created_at: str


class RetrievalMetrics(BaseModel):
    search_count: int = 0
    search_latency_total: float = 0.0
    search_latency_min: float | None = None
    search_latency_max: float | None = None
    last_search_latency: float | None = None
    last_search_at: str | None = None
    cache_hits: int = 0
    cache_misses: int = 0
    concepts_retrieved: int = 0


class ContextMetrics(BaseModel):
    context_count: int = 0
    context_latency_total: float = 0.0
    context_latency_min: float | None = None
    context_latency_max: float | None = None
    last_context_latency: float | None = None
    last_context_at: str | None = None


class KnowledgeUsageMetrics(BaseModel):
    concepts_injected: int = 0
    concepts_referenced: int = 0
    concepts_ignored: int = 0
    agent_decisions_aligned: int = 0
    last_report_at: str | None = None


class ReflectionMetrics(BaseModel):
    structured_events: int = 0
    fallback_extractions: int = 0
    empty_reflections: int = 0
    file_observations: int = 0


class RuntimeMetrics(BaseModel):
    sessions_started: int = 0
    sessions_completed: int = 0
    sessions_failed: int = 0
    sessions_recovered: int = 0
    reflections: int = 0
    materializations: int = 0


class MetricsSchema(BaseModel):
    retrieval: RetrievalMetrics = Field(default_factory=RetrievalMetrics)
    context: ContextMetrics = Field(default_factory=ContextMetrics)
    knowledge_usage: KnowledgeUsageMetrics = Field(default_factory=KnowledgeUsageMetrics)
    reflection: ReflectionMetrics = Field(default_factory=ReflectionMetrics)
    runtime: RuntimeMetrics = Field(default_factory=RuntimeMetrics)


class OutcomeMetrics(BaseModel):
    concepts_injected: int = 0
    concepts_referenced: int = 0
    search_count: int = 0


class OutcomeRecord(BaseModel):
    schema_version: int = 1
    session_id: str
    outcome: Literal["success", "failure", "abandoned"]
    referenced_concepts: list[str] = Field(default_factory=list)
    retrieved_concepts: list[str] = Field(default_factory=list)
    reason: str | None = None
    goal_satisfaction: float | None = Field(default=None, ge=0.0, le=1.0)
    metrics: OutcomeMetrics = Field(default_factory=OutcomeMetrics)
    timestamp: str


class ConceptFitness(BaseModel):
    concept_id: str
    canonical_name: str
    retrieved: int = 0
    referenced: int = 0
    ignored: int = 0
    successful_sessions: int = 0
    failed_sessions: int = 0
    evidence_count: int = 0
    fitness_score: float = 0.0


class SkillCandidate(BaseModel):
    candidate_id: str = Field(description="Unique candidate identifier")
    slug: str = Field(description="Slug identifier of the candidate skill")
    title: str = Field(description="Title of the candidate skill")
    status: Literal["proposed", "approved", "rejected", "deferred", "superseded"] = "proposed"
    confidence: Literal["low", "medium", "high"] = "medium"
    trigger: str = Field(description="Under what conditions the skill should trigger")
    recommended_behavior: str = Field(description="The behavior guideline recommendation")
    evidence: list[str] = Field(default_factory=list, description="Associated evidence items")
    rationale: str = Field(description="Why this candidate should become a skill")
    created_at: str = Field(description="ISO 8601 UTC timestamp format")
    updated_at: str = Field(description="ISO 8601 UTC timestamp format")
    source_event_ids: list[str] = Field(default_factory=list, description="IDs of source events")
    source_concept_ids: list[str] = Field(default_factory=list, description="IDs of source concepts")


class SkillPromotionEvent(BaseModel):
    timestamp: str = Field(description="ISO 8601 UTC timestamp format")
    candidate_id: str = Field(description="Unique candidate identifier")
    slug: str = Field(description="Slug identifier of the candidate skill")
    event_type: Literal["proposed", "approved", "rejected", "deferred", "edited"]
    previous_status: str | None = None
    new_status: str
    notes: str | None = None


