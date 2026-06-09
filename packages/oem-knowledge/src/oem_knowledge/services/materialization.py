from __future__ import annotations
import difflib
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from oem_knowledge.source_classifier import SourceType, classify_source

logger = logging.getLogger(__name__)

SYSTEM_GENERATED_SOURCE_TYPES = {
    SourceType.OEM_WIKI,
    SourceType.OEM_REGISTRY,
    SourceType.OEM_RUNTIME_LOG,
    SourceType.OEM_SESSION_REPORT,
    SourceType.OEM_HANDOFF,
    SourceType.OEM_CONFIG,
    SourceType.GENERATED_SUMMARY,
    "oem_generated",
    "openempiric_generated",
}

SUSPICIOUS_CONCEPT_SLUGS = {
    "index",
    "log",
    "inbox",
    "schema",
    "purpose",
    "triggers",
    "runtime-events",
    "outcomes",
    "session-report",
}


def _normalize_concept_slug(value: str | None) -> str:
    if value is None:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    return slug.strip("-")


def is_suspicious_concept_slug(value: str | None) -> bool:
    slug = _normalize_concept_slug(value)
    return slug in SUSPICIOUS_CONCEPT_SLUGS or bool(
        re.fullmatch(r"concept-\d+", slug)
    )


def _is_explicit_oem_source_type(source_type: object) -> bool:
    if source_type is None:
        return False
    normalized = str(source_type).strip().lower().replace("-", "_")
    return normalized in SYSTEM_GENERATED_SOURCE_TYPES or normalized.startswith("oem_")


if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class MaterializationService:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine

    def _sync_index(self, canonical_name: str, concept_id: str, project: str | None = None):
        harness = self.engine._resolve_harness(project)
        index_file = harness / "wiki" / "index.md"
        sfs = self.engine._sfs(project)

        if not sfs.exists(index_file):
            sfs.write_text(
                index_file,
                "# Wiki Index\n\n### Concepts\n\n",
                force_allow_truncation=True,
            )

        content = sfs.read_text(index_file)
        display_title = canonical_name.replace("-", " ").title()
        link = f"- [[{concept_id}|{display_title}]]"

        if f"[[{concept_id}|" in content:
            return

        lines = content.splitlines()
        updated_lines = []
        inserted = False
        for line in lines:
            updated_lines.append(line)
            if line.strip() == "### Concepts" and not inserted:
                updated_lines.append("")
                updated_lines.append(link)
                inserted = True

        if not inserted:
            updated_lines.append("\n### Concepts")
            updated_lines.append(link)

        sfs.write_text(
            index_file, "\n".join(updated_lines) + "\n", force_allow_truncation=True
        )

    def _write_revision_log(self, file_path: Path, new_content: str, project: str | None = None):
        """Append a revision entry to .oem/wiki/.history.jsonl."""
        sfs = self.engine._sfs(project)
        history_file = self.engine._concepts_dir(project) / ".history.jsonl"
        
        old_content = ""
        if sfs.exists(file_path):
            try:
                old_content = sfs.read_text(file_path)
            except Exception as e:
                logger.warning("Failed to read concept file %s for revision log: %s", file_path, e)
                
        diff_str = ""
        if old_content:
            diff_lines = list(difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile="old_" + file_path.name,
                tofile="new_" + file_path.name,
                lineterm=""
            ))
            diff_str = "\n".join(diff_lines)
            
        concept_id = file_path.stem
        m = re.search(r"concept_id:\s*([^\n\r]+)", new_content)
        if m:
            concept_id = m.group(1).strip().strip("\"'")
            
        entry = {
            "concept_id": concept_id,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "file_name": file_path.name,
            "diff": diff_str,
            "content": new_content
        }
        
        try:
            sfs.append_text(history_file, json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning("Failed to append entry to history file %s: %s", history_file, e)

    def get_concept_history(self, concept_id: str, project: str | None = None) -> list[dict]:
        """Read all revision log entries for a given concept_id."""
        sfs = self.engine._sfs(project)
        history_file = self.engine._concepts_dir(project) / ".history.jsonl"
        history = []
        if sfs.exists(history_file):
            try:
                for line in sfs.read_text(history_file).strip().splitlines():
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry.get("concept_id") == concept_id:
                        history.append(entry)
            except Exception as e:
                logger.warning("Failed to parse history entry in %s: %s", history_file, e)
        return history

    def _safe_write_concept_file(
        self, file_path: Path, content: str, project: str | None = None
    ) -> bool:
        concepts_dir = self.engine._concepts_dir(project).resolve()
        resolved_path = file_path.resolve()
        try:
            if not resolved_path.is_relative_to(concepts_dir):
                raise PermissionError(
                    f"Security Abort: Path traversal attempted -> {file_path}"
                )
        except ValueError:
            raise PermissionError(
                f"Security Abort: Path traversal attempted -> {file_path}"
            )

        self._write_revision_log(file_path, content, project)
        sfs = self.engine._sfs(project)
        return sfs.write_text(file_path, content)

    def _log_action(self, message: str, project: str | None = None):
        harness = self.engine._resolve_harness(project)
        log_file = harness / "wiki" / "log.md"
        date_str = time.strftime("%Y-%m-%d %H:%M")
        entry = f"- **[{date_str}]**: {message}\n"

        sfs = self.engine._sfs(project)
        if not sfs.exists(log_file):
            sfs.write_text(log_file, "# Wiki Log\n\n", force_allow_truncation=True)

        sfs.append_text(log_file, entry)

    def materialize_concepts(self, project: str | None = None) -> dict:
        from oem_knowledge.fs import LockTimeoutError
        try:
            return self._materialize_concepts_impl(project)
        except LockTimeoutError as e:
            logger.error("Lock acquisition failure during materialization: %s", e)
            return {
                "status": "error",
                "failed_step": "materialization",
                "message": f"Lock acquisition failure during materialization: {e}",
            }

    def _materialize_concepts_impl(self, project: str | None = None) -> dict:
        self.engine._resolve_harness(project)
        sessions_dir = self.engine._sessions_dir(project)
        if not sessions_dir.exists():
            return {
                "status": "success",
                "message": "No session reports found.",
                "materialized": [],
                "skipped_oem_generated_events": 0,
                "skipped_oem_generated_event_details": [],
                "suspicious_concepts": [],
            }

        concepts_dir = self.engine._concepts_dir(project)
        concepts_dir.mkdir(parents=True, exist_ok=True)

        session_files = sorted(sessions_dir.glob("*.md"))
        if not session_files:
            return {
                "status": "success",
                "message": "No session reports found.",
                "materialized": [],
                "skipped_oem_generated_events": 0,
                "skipped_oem_generated_event_details": [],
                "suspicious_concepts": [],
            }

        registry = self.engine.state._load_registry(project)
        fitness_data = self.engine.fitness.calculate_fitness(project)
        materialized_log = []
        skipped_oem_generated_event_details = []
        suspicious_concepts = []

        # Derive already processed session IDs from the registry
        processed_sessions = set()
        for cdata in registry.values():
            processed_sessions.update(cdata.get("sessions", []))

        registry_updated = False

        for session_file in session_files:
            session_id = session_file.stem
            if session_id in processed_sessions:
                continue

            content = session_file.read_text(encoding="utf-8")
            knowledge_events = []
            json_match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    if "knowledge_events" in data:
                        knowledge_events = data["knowledge_events"]
                except json.JSONDecodeError as e:
                    logger.warning("Corrupt JSON in session file %s: %s", session_file, e)
                except Exception as e:
                    logger.warning("Unexpected error parsing JSON in session file %s: %s", session_file, e)

            if not knowledge_events:
                continue

            registry_updated = True

            for event in knowledge_events:
                concept = event.get("concept", "General Learning")
                e_type = event.get("type", "observation").lower()
                evidence = event.get("evidence", "")
                source_path = event.get("source_path")
                source = event.get("source")
                source_type = event.get("source_type")
                suspicious_slug = is_suspicious_concept_slug(concept)

                skip_reason = None
                classification = None
                if _is_explicit_oem_source_type(source_type):
                    skip_reason = f"source_type {source_type!r} is OpenEmpiric-generated"
                elif source_path:
                    classification = classify_source(source_path)
                    if not classification.ingestion_eligible:
                        skip_reason = classification.reason

                if skip_reason:
                    detail = {
                        "session_id": session_id,
                        "concept": concept,
                        "source": source,
                        "source_path": (
                            str(source_path) if source_path is not None else None
                        ),
                        "source_type": source_type,
                        "classifier_source_type": (
                            classification.source_type if classification else source_type
                        ),
                        "reason": skip_reason,
                        "action": "skipped",
                    }
                    skipped_oem_generated_event_details.append(detail)
                    logger.warning(
                        "Skipping OpenEmpiric-generated materialization event: "
                        "session_id=%s concept=%r source=%r source_path=%r "
                        "source_type=%r classifier_source_type=%r reason=%s",
                        session_id,
                        concept,
                        source,
                        source_path,
                        source_type,
                        detail["classifier_source_type"],
                        skip_reason,
                    )
                    continue

                if suspicious_slug:
                    event["suspicious_concept"] = True
                    suspicious_concepts.append(
                        {
                            "session_id": session_id,
                            "concept": concept,
                            "reason": "system-like slug",
                            "source": source,
                            "source_path": (
                                str(source_path) if source_path is not None else None
                            ),
                            "source_type": source_type,
                            "action": "flagged",
                        }
                    )
                    logger.debug(
                        "Flagging suspicious materialization concept without skipping: "
                        "session_id=%s concept=%r source=%r source_path=%r source_type=%r",
                        session_id,
                        concept,
                        source,
                        source_path,
                        source_type,
                    )

                cid, cdata = self.engine.state._resolve_concept(concept, registry)
                if suspicious_slug:
                    cdata.setdefault("diagnostics", {})["suspicious_concept_slug"] = True

                if evidence:
                    cdata["evidence_count"] = cdata.get("evidence_count", 0) + 1

                cdata = self.engine.state.evaluate_concept_status(cdata, e_type, session_id=session_id, fitness_data=fitness_data)
                new_status = cdata["status"]
                registry[cid] = cdata

                concept_file = concepts_dir / f"{cid}.md"

                try:
                    if new_status in ("validated", "canonical", "needs_review"):
                        existing_body = ""
                        is_new = not concept_file.exists()
                        if not is_new:
                            try:
                                text = concept_file.read_text(encoding="utf-8")
                                fm = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.DOTALL)
                                existing_body = fm.group(1).strip() if fm else text.strip()
                            except Exception as e:
                                logger.warning("Failed to read existing concept file %s: %s", concept_file, e)

                        learning = f"- **{e_type.title()}**: {evidence}" if evidence else ""
                        if is_new:
                            if new_status == "needs_review":
                                body = f"# {cdata['canonical_name'].replace('-', ' ').title()}\n\nThis concept requires review due to repeated session failures.\n\n## Learnings\n{learning}\n"
                            else:
                                body = f"# {cdata['canonical_name'].replace('-', ' ').title()}\n\nThis concept is a validated organizational knowledge node.\n\n## Learnings\n{learning}\n"
                        else:
                            body = existing_body
                            if learning:
                                body += f"\n\n## Learnings ({time.strftime('%Y-%m-%d')})\n{learning}\n"

                        concept_content = f"""---
concept_id: {cid}
canonical_name: {cdata["canonical_name"]}
status: {new_status}
confidence: {cdata["confidence"]}
evidence_count: {cdata["evidence_count"]}
session_count: {cdata["session_count"]}
aliases: {json.dumps(cdata.get("aliases", []))}
---
{body}"""

                        self._safe_write_concept_file(concept_file, concept_content, project)
                        self._log_action(
                            f"Ingest | Materialized concept {cid} ({cdata['canonical_name']}) as {new_status}",
                            project,
                        )
                        self._sync_index(cdata["canonical_name"], cid, project)
                        materialized_log.append(
                            f"{cid} ({cdata['canonical_name']}) = {new_status}"
                        )

                    elif new_status == "deprecated":
                        if concept_file.exists():
                            concept_file.unlink()
                            self._log_action(
                                f"Delete | Deprecated concept {cid} ({cdata['canonical_name']})",
                                project,
                            )
                        materialized_log.append(f"Deprecated: {cid}")

                    else:
                        materialized_log.append(
                            f"{cid} ({cdata['canonical_name']}) = {new_status} (not materialized)"
                        )
                except Exception as e:
                    logger.error("Failed to materialize/write concept file %s: %s", concept_file, e)
                    return {"status": "error", "failed_step": "materialization", "message": f"Failed to write concept file {concept_file.name}: {e}"}

        if registry_updated:
            self.engine.state._save_registry(registry, project)

        # Emit materializations metric
        try:
            from oem_knowledge.tools.metrics import update_metrics_file
            from oem_knowledge.engine import find_harness_root, OEM_DIR
            p = Path(project or ".").resolve()
            root = find_harness_root(p) or p
            metrics_file = (root / OEM_DIR / "state" / "metrics.json")
            if materialized_log:
                update_metrics_file(metrics_file, {"materializations": len(materialized_log)})
        except Exception as e:
            logger.warning("Failed to emit materializations metrics: %s", e)

        return {
            "status": "success",
            "materialized": materialized_log,
            "skipped_oem_generated_events": len(skipped_oem_generated_event_details),
            "skipped_oem_generated_event_details": skipped_oem_generated_event_details,
            "suspicious_concepts": suspicious_concepts,
        }

    def update_graph(self, project: str | None = None) -> dict:
        from oem_knowledge.fs import LockTimeoutError
        try:
            return self._update_graph_impl(project)
        except LockTimeoutError as e:
            logger.error("Lock acquisition failure during graph update: %s", e)
            return {
                "status": "error",
                "failed_step": "materialization",
                "message": f"Lock acquisition failure during graph update: {e}",
            }

    def _update_graph_impl(self, project: str | None = None) -> dict:
        concepts_dir = self.engine._concepts_dir(project)
        if not concepts_dir.exists():
            return {
                "status": "success",
                "message": "No concepts directory.",
                "links_updated": 0,
            }

        concept_files = list(concepts_dir.glob("concept_*.md"))
        if not concept_files:
            return {
                "status": "success",
                "message": "No concept files found.",
                "links_updated": 0,
            }

        registry = self.engine.state._load_registry(project)

        for cid in registry:
            registry[cid]["relationships"] = []

        concepts = {}
        for f in concept_files:
            cid = f.stem
            if cid not in registry:
                continue
            text = f.read_text()
            matches = re.findall(
                r"\[\[(?:([a-zA-Z0-9_]+):)?(concept_\d{3})(?:\|([^\]]*))?\]\]", text
            )
            parsed_links = []
            for r_type, target_id, label in matches:
                parsed_links.append(
                    {
                        "type": r_type or "relates_to",
                        "target": target_id,
                        "label": label or target_id,
                    }
                )
            concepts[cid] = {
                "path": f,
                "name": registry[cid]["canonical_name"].replace("-", " ").title(),
                "text": text,
                "links": parsed_links,
            }

        rec_map = {
            "depends_on": "depended_on_by",
            "depended_on_by": "depends_on",
            "implements": "implemented_by",
            "implemented_by": "implements",
            "supersedes": "superseded_by",
            "superseded_by": "supersedes",
            "mitigates": "mitigated_by",
            "mitigated_by": "mitigates",
            "relates_to": "relates_to",
        }

        printable_links = {cid: {} for cid in concepts}

        for cid_a, data_a in concepts.items():
            for link in data_a["links"]:
                target = link["target"]
                rel_type = link["type"]

                if target in concepts:
                    rel_list_a = registry[cid_a].setdefault("relationships", [])
                    if not any(
                        r.get("target") == target and r.get("type") == rel_type
                        for r in rel_list_a
                    ):
                        rel_list_a.append({"type": rel_type, "target": target})
                    printable_links[cid_a][target] = rel_type

                    rec_type = rec_map.get(rel_type, "relates_to")
                    rel_list_b = registry[target].setdefault("relationships", [])
                    if not any(
                        r.get("target") == cid_a and r.get("type") == rec_type
                        for r in rel_list_b
                    ):
                        rel_list_b.append({"type": rec_type, "target": cid_a})
                    printable_links[target][cid_a] = rec_type

        links_added = 0
        for cid, data in concepts.items():
            fp = data["path"]
            text = data["text"]
            targets = printable_links[cid]
            if not targets:
                continue

            fm = re.match(r"^(---\s*\n.*?\n---\s*\n)(.*)$", text, re.DOTALL)
            if not fm:
                continue

            header = fm.group(1)
            body = re.split(r"\n##\s+Related", fm.group(2), flags=re.IGNORECASE)[
                0
            ].strip()

            related_lines = []
            for tc, r_type in sorted(targets.items()):
                tname = registry[tc]["canonical_name"].replace("-", " ").title()
                r_label = r_type.replace("_", " ").title()
                related_lines.append(
                    f"- [[{r_type}:{tc}|{tname}]] — {tname} ({r_label})"
                )

            new_text = (
                header
                + body
                + "\n\n## Related Knowledge\n"
                + "\n".join(related_lines)
                + "\n"
            )
            if new_text != text:
                self._safe_write_concept_file(fp, new_text, project)
                links_added += 1

        self.engine.state._save_registry(registry, project)
        return {
            "status": "success",
            "links_updated": links_added,
            "files_scanned": len(concept_files),
        }
