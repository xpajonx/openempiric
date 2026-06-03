from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING
from oem_knowledge.models import ConceptData, KnowledgeEvent

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class StateService:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine

    def _load_registry(self, project: str | None = None) -> dict:
        return self.engine._load_registry_before_extraction(project)

    def _save_registry(self, registry: dict, project: str | None = None):
        self.engine._save_registry_before_extraction(registry, project)

    def _load_events(self, project: str | None = None) -> list[dict]:
        return self.engine._load_events_before_extraction(project)

    def _append_event(self, event: dict | KnowledgeEvent, project: str | None = None):
        self.engine._append_event_before_extraction(event, project)

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
        self, cdata: dict, e_type: str, session_id: str
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

        if e_type == "deprecation":
            new_status = "deprecated"
        elif cdata.get("session_count", 0) >= 5 and cdata["confidence"] >= 4:
            new_status = "canonical"
        elif cdata.get("evidence_count", 0) >= 3 or current_status == "validated":
            new_status = "validated"
        elif cdata.get("session_count", 0) >= 2:
            new_status = "emerging"
        else:
            new_status = "candidate"

        if new_status != current_status:
            cdata.setdefault("promotion_history", []).append({
                "from_status": current_status,
                "to_status": new_status,
                "trigger_event": e_type,
                "session_id": session_id,
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

        contents = {}
        for f in md_files:
            try:
                contents[f] = sfs.read_text(f)
            except Exception:
                pass

        merged = []
        already_merged = set()
        for i in range(len(md_files)):
            f1 = md_files[i]
            if f1 in already_merged or f1 not in contents:
                continue
            for j in range(i + 1, len(md_files)):
                f2 = md_files[j]
                if f2 in already_merged or f2 not in contents:
                    continue

                w1 = set(re.findall(r"\w+", f1.stem.lower()))
                w2 = set(re.findall(r"\w+", f2.stem.lower()))
                common = w1 & w2
                if common and len(common) >= min(len(w1), len(w2)):
                    if len(f1.name) > len(f2.name):
                        f1, f2 = f2, f1
                    merged_content = f"{contents[f1].strip()}\n\n## Consolidated: {f2.stem.replace('_', ' ').title()}\n{contents[f2].strip()}"
                    sfs.write_text(f1, merged_content, force_allow_truncation=True)
                    sfs.unlink(f2)
                    already_merged.add(f2)
                    contents[f1] = merged_content
                    merged.append(f"Merged {f2.name} -> {f1.name}")

        if merged:
            self.engine.index_all(force=True)

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
            )
            temp_registry[cid] = cdata

        self._save_registry(temp_registry, project)

        materialized_log = []
        concepts_dir.mkdir(parents=True, exist_ok=True)
        for cid, cdata in temp_registry.items():
            if cdata["status"] in ("validated", "canonical"):
                concept_file = concepts_dir / f"{cid}.md"
                body = f"# {cdata['canonical_name'].replace('-', ' ').title()}\n\nThis concept is a validated organizational knowledge node.\n\n## Learnings\n"

                concept_content = f"---\nconcept_id: {cid}\ncanonical_name: {cdata['canonical_name']}\nstatus: {cdata['status']}\nconfidence: {cdata['confidence']}\nevidence_count: {cdata['evidence_count']}\nsession_count: {cdata.get('session_count', 0)}\naliases: {json.dumps(cdata.get('aliases', []))}\n---\n{body}"
                self.engine._safe_write_concept_file(concept_file, concept_content, project)
                self.engine._sync_index(cdata["canonical_name"], cid, project)
                materialized_log.append(cid)

        self.engine.index_all(force=True)

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
        events = self.engine.get_events(project, concept=cdata["canonical_name"])

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

        self.engine._log_action(
            f"Merge | Merged secondary concept {secondary_id} into primary {primary_id} ({pdata['canonical_name']})",
            project,
        )
        return {
            "status": "success",
            "message": f"Merged {secondary_id} into {primary_id}",
            "concept": pdata,
        }
