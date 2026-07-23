from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from oem_knowledge.models import SkillCandidate, SkillPromotionEvent

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip().lower())


class SkillPromotionService:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine

    def _extract_trigger_and_behavior(self, texts: list[str]) -> tuple[str, str]:
        trigger = ""
        behavior = ""

        # Trigger keyword matches: when, if, during, whenever, upon
        trigger_pat = re.compile(
            r"\b(when|if|during|whenever|upon)\b\s+([^.,;!?\n]+)",
            re.IGNORECASE
        )
        # Behavior keyword matches: should, must, recommend, always, start with, ensure, avoid, prefer
        behavior_pat = re.compile(
            r"\b(should|must|recommend|always|start with|ensure|avoid|prefer)\b\s+([^.,;!?\n]+)",
            re.IGNORECASE
        )

        for text in texts:
            if not text:
                continue
            # Split into parts based on punctuation
            parts = re.split(r"[.!?]", text)
            for part in parts:
                part = part.strip()
                if not part:
                    continue

                if not trigger:
                    m_trig = trigger_pat.search(part)
                    if m_trig:
                        trigger = m_trig.group(0).strip()
                        trigger = trigger[0].upper() + trigger[1:]

                if not behavior:
                    m_beh = behavior_pat.search(part)
                    if m_beh:
                        behavior = m_beh.group(0).strip()
                        behavior = behavior[0].upper() + behavior[1:]
                        if not behavior.endswith("."):
                            behavior += "."

                if trigger and behavior:
                    return trigger, behavior

        return trigger, behavior

    def evaluate_skill_candidates(self, project: str, limit: int = 10, relaxed: bool = True) -> dict:
        try:
            return self._evaluate_skill_candidates_impl(project, limit, relaxed=relaxed)
        except Exception as e:
            logger.error("Failed to evaluate skill candidates: %s", e)
            return {
                "status": "error",
                "candidates_created": 0,
                "candidates_skipped": 0,
                "warnings": [str(e)],
                "candidates": [],
            }

    def _evaluate_skill_candidates_impl(self, project: str, limit: int = 10, relaxed: bool = True) -> dict:
        # Load registry
        registry = self.engine.state._load_registry(project)

        # Load outcomes
        outcomes_file = self.engine.layout(project).root / "state" / "outcomes.jsonl"
        outcomes = []
        if outcomes_file.exists():
            try:
                for line in outcomes_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        outcomes.append(json.loads(line))
            except Exception as e:
                logger.warning("Failed to load outcomes: %s", e)

        # Load events
        events = self.engine.state._load_events(project)

        # Read rejected slugs and fingerprints from promotions log
        promotions_file = self.engine.layout(project).skill_promotions_path
        rejected_slugs = set()
        rejected_fingerprints = set()
        if promotions_file.exists():
            try:
                for line in promotions_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        evt = json.loads(line)
                        if evt.get("new_status") == "rejected" or evt.get("event_type") == "rejected":
                            rejected_slugs.add(evt.get("slug"))
            except Exception as e:
                logger.warning("Failed to load promotions log: %s", e)

        # Load existing candidates
        existing_candidates = self.engine.skills.list_skill_candidates(project)
        approved_slugs = set()
        for ec in existing_candidates:
            if ec.status == "rejected":
                rejected_slugs.add(ec.slug)
                rejected_fingerprints.add(normalize_text(ec.trigger + ec.recommended_behavior))
            elif ec.status == "approved":
                approved_slugs.add(ec.slug)

        # Approved skills from the approved folder
        skills_dir = self.engine.layout(project).skills_dir
        if skills_dir.exists():
            for fp in skills_dir.glob("*.md"):
                approved_slugs.add(fp.stem)

        candidates_created = []
        candidates_skipped_count = 0

        # Group events by concept ID
        concept_events: dict[str, list[dict]] = {}
        for ev in events:
            # Skip if source is not user/agent chat fallback
            if ev.get("source") not in ("chat", "chat-fallback", "agent_structured"):
                continue
            
            cids = {self.engine.fitness._find_concept_id(c, registry) for c in ev.get("concept_candidates", [])}
            for cid in cids:
                if cid in registry:
                    concept_events.setdefault(cid, []).append(ev)

        for cid, evs in concept_events.items():
            if len(candidates_created) >= limit:
                break

            # Common: group by unique event_id (needed by both strict and relaxed paths)
            unique_events = {}
            for ev in evs:
                unique_events[ev.get("event_id")] = ev

            # Common: title and slug derivation
            concept_name = registry[cid].get("canonical_name", cid).replace("-", " ").title()
            title = f"{concept_name} Workflow"
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

            strict_ok = True

            # --- STRICT PATH ---

            # 1. Evidence count >= 2 (independent events by event_id)
            if len(unique_events) < 2:
                strict_ok = False

            # 2. Outcomes matching events
            success_signal_count = 0
            if strict_ok:
                matched_outcomes = []
                for ev in unique_events.values():
                    for out in outcomes:
                        # Match Rule 1: session_id matches
                        if out.get("session_id") and ev.get("session_id") and out.get("session_id") == ev.get("session_id"):
                            # Match Rule 2: references concept
                            ret_resolved = {self.engine.fitness._find_concept_id(c, registry) for c in out.get("retrieved_concepts", [])}
                            ref_resolved = {self.engine.fitness._find_concept_id(c, registry) for c in out.get("referenced_concepts", [])}
                            if cid in ret_resolved or cid in ref_resolved:
                                matched_outcomes.append(out)
                                continue
                        # Match Rule 3 fallback: same day
                        if not out.get("session_id") or not ev.get("session_id"):
                            out_date = out.get("timestamp", "")[:10]
                            ev_date = ev.get("timestamp", "")[:10]
                            if out_date and ev_date and out_date == ev_date:
                                matched_outcomes.append(out)

                success_signal_count = sum(1 for out in matched_outcomes if out.get("outcome") == "success")
                if success_signal_count < 1:
                    strict_ok = False

            # 3. Extract trigger and recommended behavior
            texts_to_parse = []

            # Read concept wiki body if exists
            wiki_file = self.engine.layout(project).concepts_dir / f"{cid}.md"
            if wiki_file.exists():
                try:
                    texts_to_parse.append(wiki_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            for ev in unique_events.values():
                texts_to_parse.append(ev.get("summary", ""))
                texts_to_parse.append(ev.get("evidence", ""))

            trigger, behavior = self._extract_trigger_and_behavior(texts_to_parse)

            if strict_ok:
                # Heuristics: trigger length >= 8, behavior length >= 12
                if not trigger or len(trigger) < 8 or not behavior or len(behavior) < 12:
                    strict_ok = False

            # Check rejection cooldowns
            fingerprint = normalize_text(trigger + behavior)
            if strict_ok:
                if slug in rejected_slugs or fingerprint in rejected_fingerprints:
                    strict_ok = False

                if slug in approved_slugs:
                    strict_ok = False

            # --- STRICT: create or update candidate ---
            if strict_ok:
                # Duplicate checking (slug, evidence overlap, trigger+behavior fingerprint)
                is_duplicate = False
                duplicate_candidate = None
                source_event_ids = list(unique_events.keys())

                for ec in existing_candidates:
                    # Same slug
                    if ec.slug == slug:
                        is_duplicate = True
                        duplicate_candidate = ec
                        break
                    # High evidence overlap (>= 50% overlap of source_event_ids)
                    overlap = len(set(source_event_ids) & set(ec.source_event_ids))
                    if max(len(source_event_ids), 1) > 0 and (overlap / len(source_event_ids)) >= 0.5:
                        is_duplicate = True
                        duplicate_candidate = ec
                        break
                    # Same trigger/behavior fingerprint
                    ec_fp = normalize_text(ec.trigger + ec.recommended_behavior)
                    if ec_fp == fingerprint:
                        is_duplicate = True
                        duplicate_candidate = ec
                        break

                if is_duplicate:
                    if duplicate_candidate:
                        if duplicate_candidate.status in ("proposed", "deferred"):
                            # Update evidence lists and save back
                            combined_evidence = list(set(duplicate_candidate.evidence + [ev.get("summary", "") for ev in unique_events.values()]))
                            combined_event_ids = list(set(duplicate_candidate.source_event_ids + source_event_ids))
                            duplicate_candidate.evidence = combined_evidence
                            duplicate_candidate.source_event_ids = combined_event_ids
                            # Direct update status/write candidate
                            self.engine.skills.create_skill_candidate(
                                candidate_id=duplicate_candidate.candidate_id,
                                slug=duplicate_candidate.slug,
                                title=duplicate_candidate.title,
                                trigger=duplicate_candidate.trigger,
                                recommended_behavior=duplicate_candidate.recommended_behavior,
                                evidence=combined_evidence,
                                rationale=duplicate_candidate.rationale,
                                confidence=duplicate_candidate.confidence,
                                status=duplicate_candidate.status,
                                source_event_ids=combined_event_ids,
                                source_concept_ids=duplicate_candidate.source_concept_ids,
                                created_at=duplicate_candidate.created_at,
                                project=project
                            )
                        elif duplicate_candidate.status in ("approved", "rejected"):
                            # Do not mutate approved/rejected candidates
                            candidates_skipped_count += 1
                            continue
                    candidates_skipped_count += 1
                    continue

                # Save proposed candidate
                evidence_list = [ev.get("summary", "") for ev in unique_events.values()]
                confidence_val = "high" if len(source_event_ids) >= 3 else "medium"
                rationale_text = f"This repeated pattern was verified in {success_signal_count} successful session(s)."

                import uuid
                candidate_id = f"skill_candidate_{uuid.uuid4().hex[:8]}"

                created_candidate = self.engine.skills.create_skill_candidate(
                    candidate_id=candidate_id,
                    slug=slug,
                    title=title,
                    trigger=trigger,
                    recommended_behavior=behavior,
                    evidence=evidence_list,
                    rationale=rationale_text,
                    confidence=confidence_val,
                    status="proposed",
                    source_event_ids=source_event_ids,
                    source_concept_ids=[cid],
                    project=project,
                )

                candidates_created.append({
                    "candidate_id": created_candidate.candidate_id,
                    "slug": created_candidate.slug,
                    "title": created_candidate.title,
                    "confidence": created_candidate.confidence,
                    "evidence_count": len(created_candidate.evidence),
                    "status": created_candidate.status,
                })
                continue

            # --- RELAXED FALLBACK PATH (short-term fix) ---
            # Activated when strict checks failed but relaxed is enabled.
            # Catches candidate/emerging concepts with at least one meaningful
            # event and extracts trigger/behavior at lower thresholds.
            if relaxed:
                concept_status = registry[cid].get("status", "")
                if concept_status in ("candidate", "emerging"):
                    # At least 1 evidence event with meaningful summary text
                    meaningful_evs = [ev for ev in unique_events.values()
                                      if ev.get("summary") and len(ev.get("summary", "")) > 20]
                    if len(meaningful_evs) >= 1:
                        # Extract trigger/behavior from meaningful events (lower thresholds)
                        relaxed_texts = []
                        wiki_file = self.engine.layout(project).concepts_dir / f"{cid}.md"
                        if wiki_file.exists():
                            try:
                                relaxed_texts.append(wiki_file.read_text(encoding="utf-8"))
                            except Exception:
                                pass
                        for ev in meaningful_evs:
                            relaxed_texts.append(ev.get("summary", ""))
                            relaxed_texts.append(ev.get("evidence", ""))

                        relaxed_trigger, relaxed_behavior = self._extract_trigger_and_behavior(relaxed_texts)

                        # Relaxed thresholds: trigger >= 4, behavior >= 6
                        if relaxed_trigger and len(relaxed_trigger) >= 4 and relaxed_behavior and len(relaxed_behavior) >= 6:
                            # Check rejection cooldowns for this relaxed version
                            relaxed_fingerprint = normalize_text(relaxed_trigger + relaxed_behavior)
                            if slug not in rejected_slugs and relaxed_fingerprint not in rejected_fingerprints and slug not in approved_slugs:
                                # Duplicate check
                                relaxed_event_ids = [ev.get("event_id") for ev in meaningful_evs if ev.get("event_id")]
                                is_relaxed_dup = False
                                for ec in existing_candidates:
                                    if ec.slug == slug:
                                        is_relaxed_dup = True
                                        break
                                    overlap = len(set(relaxed_event_ids) & set(ec.source_event_ids))
                                    if max(len(relaxed_event_ids), 1) > 0 and (overlap / len(relaxed_event_ids)) >= 0.5:
                                        is_relaxed_dup = True
                                        break
                                    ec_fp = normalize_text(ec.trigger + ec.recommended_behavior)
                                    if ec_fp == relaxed_fingerprint:
                                        is_relaxed_dup = True
                                        break

                                if not is_relaxed_dup:
                                    source_event_ids = list(unique_events.keys())
                                    evidence_list = [ev.get("summary", "") for ev in meaningful_evs]
                                    confidence_val = "low"
                                    rationale_text = (
                                        f"Suggested under relaxed thresholds. "
                                        f"Concept status: {concept_status}. "
                                        f"Based on {len(meaningful_evs)} event(s) with meaningful descriptions."
                                    )

                                    import uuid
                                    candidate_id = f"skill_candidate_{uuid.uuid4().hex[:8]}"

                                    created_candidate = self.engine.skills.create_skill_candidate(
                                        candidate_id=candidate_id,
                                        slug=slug,
                                        title=title,
                                        trigger=relaxed_trigger,
                                        recommended_behavior=relaxed_behavior,
                                        evidence=evidence_list,
                                        rationale=rationale_text,
                                        confidence=confidence_val,
                                        status="proposed",
                                        source_event_ids=source_event_ids,
                                        source_concept_ids=[cid],
                                        project=project,
                                    )

                                    candidates_created.append({
                                        "candidate_id": created_candidate.candidate_id,
                                        "slug": created_candidate.slug,
                                        "title": created_candidate.title,
                                        "confidence": created_candidate.confidence,
                                        "evidence_count": len(created_candidate.evidence),
                                        "status": created_candidate.status,
                                    })
                                    continue

            candidates_skipped_count += 1

        return {
            "status": "success",
            "candidates_created": len(candidates_created),
            "candidates_skipped": candidates_skipped_count,
            "warnings": [],
            "candidates": candidates_created,
        }
