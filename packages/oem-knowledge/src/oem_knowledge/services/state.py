from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING
from oem_knowledge.fs import FileLock, SecureFileSystem
from oem_knowledge.models import ConceptData, KnowledgeEvent

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class StateService:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine

    def _sfs(self, project: str | None = None) -> SecureFileSystem:
        return SecureFileSystem(self.engine._resolve_harness(project))

    def _load_registry(self, project: str | None = None) -> dict:
        p = self.engine._registry_path(project)
        sfs = self._sfs(project)
        with FileLock(p.with_suffix(".lock")):
            if sfs.exists(p):
                try:
                    return json.loads(sfs.read_text(p))
                except Exception:
                    return {}
            return {}

    def _save_registry(self, registry: dict, project: str | None = None):
        p = self.engine._registry_path(project)
        sfs = self._sfs(project)
        with FileLock(p.with_suffix(".lock")):
            sfs.write_text(p, json.dumps(registry, indent=2))

    def _load_events(self, project: str | None = None) -> list[dict]:
        p = self.engine._events_path(project)
        sfs = self._sfs(project)
        with FileLock(p.with_suffix(".lock")):
            if not sfs.exists(p):
                return []
            events = []
            try:
                for line in sfs.read_text(p).splitlines():
                    line = line.strip()
                    if line:
                        ev_dict = json.loads(line)
                        events.append(self.engine.event_migrator.upcast(ev_dict))
            except Exception:
                return []
            return events

    def _append_event(self, event: dict | KnowledgeEvent, project: str | None = None):
        if isinstance(event, dict):
            event = KnowledgeEvent(**event)
        p = self.engine._events_path(project)
        sfs = self._sfs(project)
        with FileLock(p.with_suffix(".lock")):
            sfs.append_text(p, event.model_dump_json() + "\n")

    def _resolve_concept(self, term: str, registry: dict) -> tuple[str, dict]:
        import difflib
        term_clean = re.sub(r"[^\w\s-]", "", term).strip().lower()
        if not term_clean:
            term_clean = term.strip().lower()

        for cid, data in registry.items():
            canon = data.get("canonical_name", "").lower()
            aliases = [a.lower() for a in data.get("aliases", [])]
            if term_clean == canon or term_clean in aliases:
                return cid, data

        for cid, data in registry.items():
            canon = data.get("canonical_name", "").lower()
            aliases = [a.lower() for a in data.get("aliases", [])]
            candidates = [canon] + aliases
            for cand in candidates:
                if difflib.SequenceMatcher(None, term_clean, cand).ratio() >= 0.85:
                    if term not in data.get("aliases", []):
                        data.setdefault("aliases", []).append(term)
                    return cid, data

        next_num = len(registry) + 1
        new_id = f"concept_{next_num:03d}"
        canon_name = (
            re.sub(r"[^a-zA-Z0-9\s-]", "", term).strip().replace(" ", "-").lower()
            or f"concept-{next_num}"
        )
        new_data = ConceptData(
            concept_id=new_id, canonical_name=canon_name, aliases=[term]
        ).model_dump()
        registry[new_id] = new_data
        return new_id, new_data

    def evaluate_concept_status(
        self,
        cdata: dict,
        e_type: str,
        session_id: str,
        fitness_data: dict | None = None,
    ) -> dict:
        confidence = cdata.get("confidence", 1)

        if session_id not in cdata.setdefault("sessions", []):
            cdata["sessions"].append(session_id)
            cdata["session_count"] = len(cdata["sessions"])

        if e_type == "validation":
            confidence = min(5, confidence + 1)
        elif e_type == "failure":
            confidence = max(1, confidence - 1)
            cdata["failure_count"] = cdata.get("failure_count", 0) + 1
        cdata["confidence"] = confidence

        current_status = cdata.get("status", "candidate")
        evidence_count = cdata.get("evidence_count", 0)

        # Retrieve fitness telemetry (treated as correlation, not proof of correctness)
        cid = cdata.get("concept_id")
        fit_score = 0.0
        succ_sessions = 0
        fail_sessions = 0
        has_fitness = False

        if fitness_data and cid in fitness_data:
            fit = fitness_data[cid]
            fit_score = fit.fitness_score
            succ_sessions = fit.successful_sessions
            fail_sessions = fit.failed_sessions
            has_fitness = (fit.successful_sessions + fit.failed_sessions) > 0

        # Confidence/Evidence awareness for demotion thresholds
        if confidence >= 4 or evidence_count >= 10:
            min_failures = 5
        else:
            min_failures = 3

        # Status transitions
        history_reason = ""
        if e_type == "deprecation":
            new_status = "deprecated"
            history_reason = "Manual/event-triggered deprecation"
        elif has_fitness and fail_sessions >= min_failures and fit_score < 0.60:
            new_status = "needs_review"
            history_reason = f"Telemetry Correlation: Repeated failures (fitness: {fit_score * 100:.1f}%, failures: {fail_sessions}/{fail_sessions + succ_sessions}, confidence: {confidence}, evidence: {evidence_count})"
        elif current_status == "needs_review":
            # If it is already needs_review, it can only exit via deprecation or High Fitness promotion
            if has_fitness and fit_score >= 0.80 and succ_sessions >= 2 and evidence_count >= 2:
                new_status = "validated"
                history_reason = f"Telemetry Correlation: High fitness promotion from review (fitness: {fit_score * 100:.1f}%, successes: {succ_sessions}, evidence: {evidence_count})"
            else:
                new_status = "needs_review"
                history_reason = "Status retained: remains in needs_review"
        elif cdata.get("session_count", 0) >= 5 and cdata["confidence"] >= 4:
            new_status = "canonical"
            history_reason = f"Standard Promotion: High session usage ({cdata.get('session_count', 0)}) and confidence ({confidence})"
        elif (
            evidence_count >= 3
            or current_status == "validated"
            or (has_fitness and fit_score >= 0.80 and succ_sessions >= 2 and evidence_count >= 2)
        ):
            new_status = "validated"
            if has_fitness and fit_score >= 0.80 and succ_sessions >= 2 and evidence_count >= 2 and current_status not in ("validated", "canonical"):
                history_reason = f"Telemetry Correlation: High fitness promotion (fitness: {fit_score * 100:.1f}%, successes: {succ_sessions}, evidence: {evidence_count})"
            else:
                history_reason = f"Standard Validation: Evidence count ({evidence_count}) or status retention"
        elif cdata.get("session_count", 0) >= 2:
            new_status = "emerging"
            history_reason = f"Standard Promotion: Emerging concept based on session count ({cdata.get('session_count', 0)})"
        else:
            new_status = "candidate"
            history_reason = "Standard initialization as Candidate"

        if new_status != current_status:
            cdata.setdefault("promotion_history", []).append({
                "from_status": current_status,
                "to_status": new_status,
                "trigger_event": e_type,
                "session_id": session_id,
                "reason": history_reason,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            })

        cdata["status"] = new_status
        return cdata

    def consolidate(self, project: str | None = None) -> dict:
        concepts_dir = self.engine._concepts_dir(project)
        sfs = self.engine._sfs(project)
        if not sfs.exists(concepts_dir):
            return {"status": "error", "message": "No concepts directory found."}

        md_files = list(concepts_dir.rglob("*.md"))
        if len(md_files) < 2:
            return {
                "status": "success",
                "message": "Fewer than 2 files. No consolidation needed.",
                "merged": [],
            }

        registry = self._load_registry(project)
        if not registry:
            return {
                "status": "success",
                "message": "Empty registry. No consolidation needed.",
                "merged": [],
            }

        from oem_knowledge.identity_resolver import SemanticIdentityResolver
        resolver = SemanticIdentityResolver(self.engine)
        duplicates = resolver.scan_duplicates(project, threshold=0.82)

        if not duplicates:
            return {
                "status": "success",
                "message": "No duplicates found.",
                "merged": [],
            }

        import difflib
        status_ranks = {"canonical": 5, "validated": 4, "emerging": 3, "candidate": 2, "deprecated": 1}

        def get_quality_score(cid: str, data: dict) -> tuple[int, int, float]:
            status_val = status_ranks.get(data.get("status", "candidate"), 2)
            ev_count = data.get("evidence_count", 0)
            from oem_knowledge.health import calculate_concept_health
            try:
                h_score = calculate_concept_health(data)
            except Exception:
                h_score = 0.0
            return (status_val, ev_count, h_score)

        merged = []
        already_merged = set()

        for d in duplicates:
            cid_a = d["concept_a"]
            cid_b = d["concept_b"]

            if cid_a in already_merged or cid_b in already_merged:
                continue

            f1_path = concepts_dir / f"{cid_a}.md"
            f2_path = concepts_dir / f"{cid_b}.md"
            if not f1_path.exists() or not f2_path.exists():
                continue

            # Second validation step to reduce false positives
            name_a = registry[cid_a].get("canonical_name", "").lower()
            name_b = registry[cid_b].get("canonical_name", "").lower()
            name_similarity = difflib.SequenceMatcher(None, name_a, name_b).ratio()

            words_a = set(re.findall(r"\w+", name_a))
            words_b = set(re.findall(r"\w+", name_b))
            has_overlap = bool(words_a & words_b)

            if name_similarity < 0.4 and not has_overlap:
                continue

            # Determine primary vs secondary based on concept quality
            score_a = get_quality_score(cid_a, registry[cid_a])
            score_b = get_quality_score(cid_b, registry[cid_b])

            if score_a >= score_b:
                cid_primary, cid_secondary = cid_a, cid_b
                f_primary, f_secondary = f1_path, f2_path
            else:
                cid_primary, cid_secondary = cid_b, cid_a
                f_primary, f_secondary = f2_path, f1_path

            try:
                content_primary = sfs.read_text(f_primary)
                content_secondary = sfs.read_text(f_secondary)
            except Exception:
                continue

            # Merge markdown contents
            secondary_name = registry[cid_secondary].get("canonical_name", cid_secondary).replace("-", " ").title()
            merged_content = f"{content_primary.strip()}\n\n## Consolidated: {secondary_name}\n{content_secondary.strip()}"
            sfs.write_text(f_primary, merged_content, force_allow_truncation=True)

            # Delegate registry merge and secondary deletion
            res = self.merge_concepts(project, cid_primary, cid_secondary)
            if res.get("status") == "success":
                already_merged.add(cid_secondary)
                merged.append(f"Merged {cid_secondary} -> {cid_primary}")

        if merged:
            self.engine.search.index_all(force=True)

        return {
            "status": "success",
            "message": f"Consolidated {len(merged)} files.",
            "merged": merged,
        }

    def rebuild_registry(self, project: str | None = None) -> dict:
        self.engine._resolve_harness(project)
        self.engine._registry_path(project)
        self.engine._events_path(project)
        concepts_dir = self.engine._concepts_dir(project)
        sfs = self.engine._sfs(project)

        temp_registry = {}
        if sfs.exists(concepts_dir):
            for f in concepts_dir.glob("concept_*.md"):
                try:
                    sfs.unlink(f)
                except Exception:
                    pass

        fitness_data = self.engine.calculate_fitness(project)
        events = self._load_events(project)
        for event in events:
            concept_candidates = event.get("concept_candidates", [])
            primary_term = (
                concept_candidates[0]
                if concept_candidates
                else event.get("concept", "General")
            )

            cid, cdata = self._resolve_concept(primary_term, temp_registry)

            if event.get("evidence"):
                cdata["evidence_count"] = cdata.get("evidence_count", 0) + 1

            cdata = self.evaluate_concept_status(
                cdata=cdata,
                e_type=event.get("event_type", "observation"),
                session_id=event.get("session_id", "historical"),
                fitness_data=fitness_data,
            )
            temp_registry[cid] = cdata

        self._save_registry(temp_registry, project)

        materialized_log = []
        concepts_dir.mkdir(parents=True, exist_ok=True)
        for cid, cdata in temp_registry.items():
            if cdata["status"] in ("validated", "canonical", "needs_review"):
                concept_file = concepts_dir / f"{cid}.md"
                if cdata["status"] == "needs_review":
                    body = f"# {cdata['canonical_name'].replace('-', ' ').title()}\n\nThis concept requires review due to repeated session failures.\n\n## Learnings\n"
                else:
                    body = f"# {cdata['canonical_name'].replace('-', ' ').title()}\n\nThis concept is a validated organizational knowledge node.\n\n## Learnings\n"

                concept_content = f"---\nconcept_id: {cid}\ncanonical_name: {cdata['canonical_name']}\nstatus: {cdata['status']}\nconfidence: {cdata['confidence']}\nevidence_count: {cdata['evidence_count']}\nsession_count: {cdata.get('session_count', 0)}\naliases: {json.dumps(cdata.get('aliases', []))}\n---\n{body}"
                self.engine.materialization._safe_write_concept_file(concept_file, concept_content, project)
                self.engine.materialization._sync_index(cdata["canonical_name"], cid, project)
                materialized_log.append(cid)

        self.engine.search.index_all(force=True)

        return {
            "status": "success",
            "message": "Registry rebuilt from events log.",
            "materialized": len(materialized_log),
        }

    def explain_concept(self, project: str | None = None, concept_id: str = "") -> dict:
        registry = self._load_registry(project)
        if concept_id not in registry:
            return {"status": "error", "message": f"Concept {concept_id} not found."}

        cdata = registry[concept_id]
        events = self.engine.state.get_events(project, concept=cdata["canonical_name"])

        from oem_knowledge.health import calculate_concept_health
        health_score = calculate_concept_health(cdata)

        summary = {
            "concept": cdata,
            "total_events": len(events),
            "supporting_events": events,
            "promotion_history": cdata.get("promotion_history", []),
            "health_score": health_score,
            "recent_evidence": [
                e.get("evidence") for e in events[-5:] if e.get("evidence")
            ],
        }
        return {"status": "success", "explanation": summary}

    def merge_concepts(
        self, project: str | None = None, primary_id: str = "", secondary_id: str = ""
    ) -> dict:
        registry = self._load_registry(project)
        if primary_id not in registry or secondary_id not in registry:
            return {"status": "error", "message": "One or both concepts not found."}

        pdata = registry[primary_id]
        sdata = registry[secondary_id]

        new_aliases = set(
            pdata.get("aliases", [])
            + sdata.get("aliases", [])
            + [sdata.get("canonical_name")]
        )
        pdata["aliases"] = list(new_aliases)

        pdata["evidence_count"] = pdata.get("evidence_count", 0) + sdata.get(
            "evidence_count", 0
        )
        pdata["sessions"] = list(
            set(pdata.get("sessions", []) + sdata.get("sessions", []))
        )
        pdata["session_count"] = len(pdata["sessions"])

        del registry[secondary_id]
        self._save_registry(registry, project)

        pdata = self.evaluate_concept_status(pdata, "merge", "system")
        registry[primary_id] = pdata
        self._save_registry(registry, project)

        concepts_dir = self.engine._concepts_dir(project)
        sf = concepts_dir / f"{secondary_id}.md"
        if sf.exists():
            sf.unlink()

        self.engine.materialization._log_action(
            f"Merge | Merged secondary concept {secondary_id} into primary {primary_id} ({pdata['canonical_name']})",
            project,
        )
        return {
            "status": "success",
            "message": f"Merged {secondary_id} into {primary_id}",
            "concept": pdata,
        }

    def record_outcome(
        self,
        outcome: str,
        referenced_concepts: list[str] | None = None,
        reason: str | None = None,
        session_id: str | None = None,
        project: str | None = None,
        goal_satisfaction: float | None = None,
    ) -> dict:
        harness = self.engine._resolve_harness(project)
        state_dir = harness / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        session_state_path = state_dir / "session_state.json"
        injected_concepts = []
        resolved_session_id = session_id

        if session_state_path.exists():
            try:
                state_data = json.loads(session_state_path.read_text(encoding="utf-8"))
                if not resolved_session_id:
                    resolved_session_id = state_data.get("session_id")
                injected_concepts = state_data.get("last_injected_concepts", [])
            except Exception:
                pass

        if not resolved_session_id:
            resolved_session_id = f"session_{int(time.time() * 1000)}"

        if referenced_concepts is None:
            referenced_concepts = injected_concepts

        # Read metrics if available
        metrics_file = state_dir / "metrics.json"
        concepts_injected = 0
        concepts_referenced = 0
        search_count = 0
        if metrics_file.exists():
            try:
                metrics_data = json.loads(metrics_file.read_text(encoding="utf-8"))
                concepts_injected = metrics_data.get("knowledge_usage", {}).get("concepts_injected", 0)
                concepts_referenced = metrics_data.get("knowledge_usage", {}).get("concepts_referenced", 0)
                search_count = metrics_data.get("retrieval", {}).get("search_count", 0)
            except Exception:
                pass

        # Handle default goal satisfaction based on binary outcome
        resolved_satisfaction = goal_satisfaction
        if resolved_satisfaction is None:
            resolved_satisfaction = 1.0 if outcome == "success" else 0.0

        outcomes_file = state_dir / "outcomes.jsonl"
        log_entry = {
            "schema_version": 1,
            "session_id": resolved_session_id,
            "outcome": outcome,
            "referenced_concepts": referenced_concepts,
            "retrieved_concepts": injected_concepts,
            "reason": reason,
            "goal_satisfaction": resolved_satisfaction,
            "metrics": {
                "concepts_injected": concepts_injected,
                "concepts_referenced": concepts_referenced,
                "search_count": search_count,
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        with open(outcomes_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

        self.engine.materialization._log_action(
            f"Outcome | Logged session outcome '{outcome}' for {resolved_session_id} (satisfaction: {resolved_satisfaction})",
            project,
        )

        return {
            "status": "success",
            "session_id": resolved_session_id,
            "outcome": outcome,
            "referenced_concepts": referenced_concepts,
            "retrieved_concepts": injected_concepts,
            "reason": reason,
            "goal_satisfaction": resolved_satisfaction,
            "metrics": log_entry["metrics"],
        }

    def detect_stale_concepts(self, n_sessions: int = 5, project: str | None = None) -> list[dict]:
        """Identify concepts that have not been referenced in the last N sessions."""
        registry = self._load_registry(project)
        harness = self.engine._resolve_harness(project)
        outcomes_file = harness / "state" / "outcomes.jsonl"
        
        all_sessions = []
        if outcomes_file.exists():
            try:
                for line in outcomes_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        record = json.loads(line)
                        sid = record.get("session_id")
                        if sid:
                            all_sessions.append(sid)
            except Exception:
                pass

        if len(all_sessions) < n_sessions:
            return []

        last_n_sessions = set(all_sessions[-n_sessions:])
        
        stale_concepts = []
        for cid, cdata in registry.items():
            concept_sessions = set(cdata.get("sessions", []))
            if not concept_sessions.intersection(last_n_sessions):
                last_ref = None
                sessions_since = len(all_sessions)
                if cdata.get("sessions"):
                    last_ref = cdata.get("sessions")[-1]
                    if last_ref in all_sessions:
                        sessions_since = len(all_sessions) - all_sessions.index(last_ref) - 1
                
                stale_concepts.append({
                    "concept_id": cid,
                    "canonical_name": cdata.get("canonical_name", cid),
                    "last_referenced_session": last_ref,
                    "sessions_since_reference": sessions_since
                })

        return stale_concepts

    def get_events(
        self,
        project: str | None = None,
        concept: str = "",
        event_type: str = "",
        session_id: str = "",
    ) -> list[dict]:
        """Return events filtered by optional concept, event_type and session_id."""
        events = self._load_events(project)
        filtered = []
        for ev in events:
            if concept:
                c_clean = concept.strip().replace(" ", "-").lower()
                if c_clean not in [c.lower() for c in ev.get("concept_candidates", [])]:
                    continue
            if event_type and ev.get("event_type", "").lower() != event_type.lower():
                continue
            if session_id and ev.get("session_id", "") != session_id:
                continue
            filtered.append(ev)
        return filtered

    def get_event(self, project: str | None = None, event_id: str = "") -> dict:
        """Return a single event by ID, raising KeyError if not found."""
        for ev in self._load_events(project):
            if ev.get("event_id") == event_id:
                return ev
        raise KeyError(f"Event {event_id} not found")
