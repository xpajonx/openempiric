from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from oem_knowledge.source_classifier import classify_source

logger = logging.getLogger(__name__)


def llm_extraction_available() -> bool:
    import os
    if os.environ.get("OEM_MOCK_LLM") == "true":
        return True
    if any(os.environ.get(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")):
        return True
    if os.environ.get("OEM_LOCAL_LLM_PROVIDER") or os.environ.get("LOCAL_LLM_URL"):
        return True
    return False

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

# Software-development heuristics for fallback extraction.
# Matches action verbs commonly used in dev session summaries.
_SD_ACTION_PATTERNS = [
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Fixed|Fix)\s+(.+)$", re.IGNORECASE), "fix"),
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Added|Add)\s+(.+)$", re.IGNORECASE), "add"),
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Removed|Remove)\s+(.+)$", re.IGNORECASE), "remove"),
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Implemented|Implement)\s+(.+)$", re.IGNORECASE), "implement"),
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Refactored|Refactor)\s+(.+)$", re.IGNORECASE), "refactor"),
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Migrated|Migrate)\s+(.+)$", re.IGNORECASE), "migrate"),
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Decided|Decide)\s+(.+)$", re.IGNORECASE), "decision"),
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Validated|Validate)\s+(.+)$", re.IGNORECASE), "validation"),
    (re.compile(r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(?:Failed|Fail)\s+(.+)$", re.IGNORECASE), "failure"),
]



_SD_EVENT_TYPES = {
    "fix": "fix",
    "add": "addition",
    "remove": "removal",
    "implement": "implementation",
    "refactor": "refactoring",
    "migrate": "migration",
    "decision": "decision",
    "validation": "validation",
    "failure": "failure",
}

_SOURCE_PRIORITY = {"chat": 0, "chat-fallback": 1, "orchestrator": 2, "diff": 3}


class ReflectionService:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine

    def _detect_markers(self, text: str) -> bool:
        if not text:
            return False
        markers = {"observation", "obs", "decision", "dec", "outcome", "out", "hypothesis", "hyp", "experiment", "exp", "failure", "fail", "risk", "validation", "val"}
        pattern = re.compile(
            r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?(" + 
            "|".join(markers) + 
            r")\s*:", 
            re.IGNORECASE
        )
        for line in text.splitlines():
            if pattern.match(line):
                return True
        return False

    def _parse_markers(self, conversation_text: str) -> list[dict]:
        extracted = []
        if not conversation_text:
            return extracted
        pattern = re.compile(
            r"^\s*(?:-\s*(?:\[[ xX/]\]\s*)?|[*]\s*(?:\[[ xX/]\]\s*)?|\d+\.\s*|\[[ xX/]\]\s*)?("
            r"observation|obs|decision|dec|outcome|out|hypothesis|hyp|experiment|exp|failure|fail|risk|validation|val"
            r")\s*:\s*(.+)$",
            re.IGNORECASE
        )
        type_map = {
            "observation": "observation",
            "obs": "observation",
            "decision": "decision",
            "dec": "decision",
            "hypothesis": "hypothesis",
            "hyp": "hypothesis",
            "experiment": "experiment",
            "exp": "experiment",
            "outcome": "outcome",
            "out": "outcome",
            "failure": "failure",
            "fail": "failure",
            "risk": "risk",
            "validation": "validation",
            "val": "validation"
        }
        for line in conversation_text.splitlines():
            m = pattern.match(line)
            if m:
                original_type = m.group(1).lower()
                e_type = type_map.get(original_type, "observation")
                summary = m.group(2).strip()
                if summary:
                    import uuid
                    import time
                    extracted.append({
                        "event_type": e_type,
                        "summary": summary,
                        "evidence": summary,
                        "concept_candidates": [],
                        "confidence": 4, # 0.8 default
                        "source": "agent_marker",
                        "source_type": "agent_transcript",
                        "ingestion_eligible": True,
                        "event_id": str(uuid.uuid4()),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    })
        return extracted

    def _run_llm_extraction(self, conversation_text: str, timeout_seconds: float | None = None) -> list[dict]:
        import threading
        if timeout_seconds is not None:
            res_box = []
            exc_box = []
            def worker():
                try:
                    if "slow_extraction_mock" in conversation_text or (hasattr(self, "_mock_slow") and self._mock_slow):
                        import time
                        time.sleep(2.0)
                    res = self._fallback_extract(conversation_text)
                    res_box.append(res)
                except Exception as e:
                    exc_box.append(e)
            t = threading.Thread(target=worker)
            t.daemon = True
            t.start()
            t.join(timeout=timeout_seconds)
            if t.is_alive():
                raise TimeoutError("LLM extraction timed out.")
            if exc_box:
                raise exc_box[0]
            return res_box[0] if res_box else []
        else:
            return self._fallback_extract(conversation_text)

    def _validate_and_normalize_event(self, ev: dict, warnings_list: list[str]) -> dict | None:
        if not isinstance(ev, dict):
            warnings_list.append("Event is not a dictionary. Skipped.")
            return None
        event_type = ev.get("event_type") or ev.get("type")
        summary = ev.get("summary")
        if not summary or not isinstance(summary, str):
            warnings_list.append("Event rejected: missing summary")
            return None
        if not event_type or not isinstance(event_type, str):
            warnings_list.append("Event type missing or invalid; mapped to 'observation'.")
            event_type = "observation"
        else:
            event_type = event_type.strip().lower()
            allowed_types = {"observation", "decision", "hypothesis", "experiment", "outcome", "failure", "risk", "validation", "deprecation"}
            if event_type not in allowed_types:
                warnings_list.append(f"Mapped unknown event type '{event_type}' to 'observation'.")
                event_type = "observation"
        evidence = ev.get("evidence")
        if not evidence or not isinstance(evidence, str):
            evidence = summary
        concept_candidates = ev.get("concept_candidates") or ev.get("concepts")
        if not concept_candidates:
            concept_candidates = []
        elif isinstance(concept_candidates, str):
            concept_candidates = [concept_candidates]
        elif isinstance(concept_candidates, list):
            concept_candidates = [str(c) for c in concept_candidates if c]
        confidence = ev.get("confidence")
        if confidence is None:
            confidence = 4
        else:
            try:
                confidence_float = float(confidence)
                if 0.0 <= confidence_float <= 1.0:
                    confidence = int(confidence_float * 5)
                else:
                    confidence = int(confidence_float)
                if not (1 <= confidence <= 5):
                    confidence = 1
            except (ValueError, TypeError):
                confidence = 1
        source = ev.get("source") or "agent_structured"
        import uuid
        event_id = ev.get("event_id") or ev.get("id") or str(uuid.uuid4())
        import time
        timestamp = ev.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "concept_candidates": concept_candidates,
            "summary": summary,
            "evidence": evidence,
            "confidence": confidence,
            "source": source,
            "source_type": "agent_transcript",
            "ingestion_eligible": True,
        }

    def _fallback_extract(self, conversation_text: str) -> list[dict]:
        extracted = []
        for line in conversation_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            for pattern, action in _SD_ACTION_PATTERNS:
                m = pattern.match(stripped)
                if m:
                    concept_text = m.group(1).strip().rstrip(".!").lower()
                    concept_slug = re.sub(r"[^a-z0-9]+", "-", concept_text).strip("-")[:80]
                    if concept_slug:
                        extracted.append({
                            "type": _SD_EVENT_TYPES.get(action, "observation"),
                            "concept": concept_slug,
                            "evidence": stripped[:200],
                            "confidence": 1,
                            "source": "chat-fallback",
                        })
                    break
        return extracted

    def load_reflection_config(self, project: str | None = None) -> dict:
        import yaml
        default_config = {
            "reflection": {
                "mode": "auto",
                "structured": {
                    "enabled": True
                },
                "marker": {
                    "enabled": True
                },
                "dense": {
                    "enabled": False,
                    "on_unavailable": "skip",
                    "max_retry_count": 0,
                    "queue_pending": False,
                    "max_pending_items": 20,
                    "max_pending_bytes": 500 * 1024,
                    "max_age_days": 7
                }
            }
        }
        try:
            layout = self.engine.layout(project)
            config_path = layout.reflection_config_path
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                if isinstance(loaded, dict) and "reflection" in loaded:
                    ref_config = loaded["reflection"]
                    if not isinstance(ref_config, dict):
                        return default_config
                    
                    merged = {
                        "mode": str(ref_config.get("mode", "auto")),
                        "structured": {
                            "enabled": bool(ref_config.get("structured", {}).get("enabled", True))
                            if isinstance(ref_config.get("structured"), dict) else True
                        },
                        "marker": {
                            "enabled": bool(ref_config.get("marker", {}).get("enabled", True))
                            if isinstance(ref_config.get("marker"), dict) else True
                        },
                        "dense": {
                            "enabled": bool(ref_config.get("dense", {}).get("enabled", False))
                            if isinstance(ref_config.get("dense"), dict) else False,
                            "on_unavailable": str(ref_config.get("dense", {}).get("on_unavailable", "skip"))
                            if isinstance(ref_config.get("dense"), dict) else "skip",
                            "max_retry_count": int(ref_config.get("dense", {}).get("max_retry_count", 0))
                            if isinstance(ref_config.get("dense"), dict) else 0,
                            "queue_pending": bool(ref_config.get("dense", {}).get("queue_pending", False))
                            if isinstance(ref_config.get("dense"), dict) else False,
                            "max_pending_items": int(ref_config.get("dense", {}).get("max_pending_items", 20))
                            if isinstance(ref_config.get("dense"), dict) else 20,
                            "max_pending_bytes": int(ref_config.get("dense", {}).get("max_pending_bytes", 500 * 1024))
                            if isinstance(ref_config.get("dense"), dict) else 500 * 1024,
                            "max_age_days": int(ref_config.get("dense", {}).get("max_age_days", 7))
                            if isinstance(ref_config.get("dense"), dict) else 7
                        }
                    }
                    return {"reflection": merged}
        except Exception as e:
            logger.warning("Failed to load reflection config: %s", e)
        return default_config

    def prune_pending_reflections(self, project: str | None = None) -> list[dict]:
        import time
        from datetime import datetime, timedelta
        layout = self.engine.layout(project)
        queue_file = layout.pending_dense_reflections_path
        if not queue_file.exists():
            return []
        
        config = self.load_reflection_config(project)["reflection"]
        dense_cfg = config.get("dense", {})
        max_items = int(dense_cfg.get("max_pending_items", 20))
        max_bytes = int(dense_cfg.get("max_pending_bytes", 500 * 1024))
        max_age_days = int(dense_cfg.get("max_age_days", 7))
        max_retry = int(dense_cfg.get("max_retry_count", 2))
        
        items = []
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            items.append(json.loads(line))
                        except Exception:
                            pass
        except Exception as e:
            logger.warning("Failed to read pending reflections: %s", e)
            return []
            
        now = datetime.utcnow()
        valid_items = []
        for item in items:
            if int(item.get("retry_count", 0)) > max_retry:
                continue
            ts_str = item.get("timestamp")
            if ts_str:
                try:
                    dt = datetime.strptime(ts_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                    if now - dt > timedelta(days=max_age_days):
                        continue
                except Exception:
                    pass
            valid_items.append(item)
            
        final_items = []
        current_bytes = 0
        for item in reversed(valid_items):
            item_bytes = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if len(final_items) >= max_items:
                break
            if current_bytes + item_bytes > max_bytes:
                break
            final_items.append(item)
            current_bytes += item_bytes
            
        final_items.reverse()
        
        try:
            queue_file.parent.mkdir(parents=True, exist_ok=True)
            if final_items:
                with open(queue_file, "w", encoding="utf-8") as f:
                    for item in final_items:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
            else:
                queue_file.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Failed to save pruned pending reflections: %s", e)
            
        return final_items

    def queue_dense_reflection(self, project: str | None, session_id: str, conversation_text: str) -> None:
        import time
        layout = self.engine.layout(project)
        queue_file = layout.pending_dense_reflections_path
        item = {
            "session_id": session_id,
            "project": project or "default",
            "conversation_text": conversation_text,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "retry_count": 0
        }
        try:
            queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(queue_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
            self.prune_pending_reflections(project)
        except Exception as e:
            logger.warning("Failed to queue pending reflection: %s", e)

    def extract_session_events(
        self,
        project: str | None = None,
        conversation_text: str = "",
        session_id: str = "",
        telemetry: dict | None = None,
        session_started_at: float | None = None,
        progress_callback = None,
        events: list[dict] | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
    ) -> dict:
        import time
        import uuid
        start_t = time.perf_counter()
        if not session_id:
            session_id = f"session_{time.strftime('%Y%m%d_%H%M%S')}"

        knowledge_events = []
        warnings_list = []
        events_rejected = 0
        file_observations_count = 0
        structured_events_found = 0
        fallback_extraction_used = False
        fallback_extractions_count = 0

        excluded_oem_generated_paths: set[str] = set()

        def _track_excluded_file(path: Path | str) -> None:
            try:
                path_key = str(Path(path).resolve(strict=False))
            except Exception:
                path_key = str(path)
            excluded_oem_generated_paths.add(path_key)

        self.engine._resolve_harness(project)
        concepts_dir = self.engine._concepts_dir(project)

        # Load pending events staged by the hook runtime
        pending_events = []
        try:
            harness = self.engine._resolve_harness(project)
            pending_file = harness / ".runtime" / "pending_events.jsonl"
            if pending_file.exists():
                with open(pending_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                pending_events.append(json.loads(line))
                            except Exception as e:
                                warnings_list.append(f"Failed to parse pending event: {e}")
                try:
                    pending_file.unlink()
                except Exception as e:
                    logger.warning("Failed to unlink pending events file: %s", e)
        except Exception as e:
            logger.warning("Failed to load pending events: %s", e)

        all_events = []
        if events:
            all_events.extend(events)
        all_events.extend(pending_events)

        modified_files = []
        if concepts_dir.exists():
            for fp in concepts_dir.rglob("*.md"):
                if session_started_at is not None:
                    try:
                        mtime = fp.stat().st_mtime
                        if mtime < session_started_at:
                            continue
                    except Exception as e:
                        logger.warning("Failed to check modification time for concept file %s: %s", fp.name, e)
                    try:
                        concept_id = fp.stem
                        current_text = fp.read_text(encoding="utf-8")
                        fm_match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", current_text, re.DOTALL)
                        current_body = fm_match.group(1).strip() if fm_match else current_text.strip()
                        history = self.engine.materialization.get_concept_history(concept_id, project)
                        if history:
                            last_entry = history[-1]
                            last_content = last_entry.get("content", "")
                            last_fm_match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", last_content, re.DOTALL)
                            last_body = last_fm_match.group(1).strip() if last_fm_match else last_content.strip()
                            if current_body.strip() == last_body.strip():
                                continue
                    except Exception as e:
                        logger.warning("Failed to read or diff history for concept file %s: %s", fp.name, e)
                modified_files.append(fp)

        for fp in modified_files:
            try:
                source_text = fp.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to read concept file %s for source classification: %s", fp.name, e)
                source_text = None
            source_classification = classify_source(fp, source_text)
            if not source_classification.ingestion_eligible:
                _track_excluded_file(fp)
                continue
            concept = fp.stem.replace("_", " ").replace("-", " ").title()
            knowledge_events.append({
                "type": "observation",
                "concept": concept,
                "evidence": f"Modified: {fp.name}",
                "confidence": 1,
                "source": "diff",
            })
            file_observations_count += 1

        try:
            import subprocess
            if not ("mock" in type(subprocess.run).__name__.lower()):
                p_path = Path(project or ".").resolve()
                modified_code_files = []
                res_diff = subprocess.run(
                    ["git", "diff", "--name-only"],
                    cwd=p_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res_diff.returncode == 0:
                    modified_code_files.extend(res_diff.stdout.splitlines())
                res_commit = subprocess.run(
                    ["git", "diff", "HEAD~1..HEAD", "--name-only"],
                    cwd=p_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res_commit.returncode == 0:
                    modified_code_files.extend(res_commit.stdout.splitlines())
                registry = self.engine.state._load_registry(project)
                seen_files = set()
                for f in modified_code_files:
                    f = f.strip()
                    if not f or f in seen_files:
                        continue
                    seen_files.add(f)
                    source_path = p_path / f
                    try:
                        source_text = source_path.read_text(encoding="utf-8") if source_path.is_file() else None
                    except Exception as e:
                        logger.warning("Failed to read modified file %s for source classification: %s", f, e)
                        source_text = None
                    source_classification = classify_source(f, source_text)
                    if not source_classification.ingestion_eligible:
                        _track_excluded_file(source_path)
                        continue
                    if f.startswith(".git"):
                        continue
                    f_path = Path(f)
                    stem = f_path.stem.lower()
                    name = f_path.name.lower()
                    full_path = f.lower()
                    matched_cid = None
                    matched_name = None
                    for cid, cdata in registry.items():
                        aliases = [a.lower() for a in cdata.get("aliases", [])]
                        if name in aliases or full_path in aliases:
                            matched_cid = cid
                            matched_name = cdata.get("canonical_name", cid)
                            break
                    if not matched_cid and len(stem) >= 4:
                        for cid, cdata in registry.items():
                            canon = cdata.get("canonical_name", "").lower()
                            aliases = [a.lower() for a in cdata.get("aliases", [])]
                            if stem == canon or stem in aliases:
                                matched_cid = cid
                                matched_name = cdata.get("canonical_name", cid)
                                break
                    if matched_cid:
                        knowledge_events.append({
                            "type": "observation",
                            "concept": matched_name.replace("-", " ").title(),
                            "evidence": f"Code modified in workspace: {f}",
                            "confidence": 1,
                            "source": "diff",
                        })
                        file_observations_count += 1
        except Exception as e:
            logger.warning("Failed to extract codebase modifications via git diff: %s", e)
            warnings_list.append("Git diff extraction failed, so code modification evidence may be incomplete.")

        # Load config and resolve extraction mode
        config = self.load_reflection_config(project)["reflection"]
        structured_enabled = config.get("structured", {}).get("enabled", True)
        marker_enabled = config.get("marker", {}).get("enabled", True)
        dense_enabled = config.get("dense", {}).get("enabled", False)
        queue_pending = config.get("dense", {}).get("queue_pending", False)
        on_unavailable = config.get("dense", {}).get("on_unavailable", "skip")

        resolved_mode = extraction_mode
        if resolved_mode == "auto":
            has_struct = bool(events or pending_events)
            if has_struct and structured_enabled:
                resolved_mode = "structured"
            elif self._detect_markers(conversation_text) and marker_enabled:
                resolved_mode = "markers"
            else:
                resolved_mode = "llm"

        text_clean = conversation_text.strip()
        session_markers_detected = any(
            marker in conversation_text.lower()
            for marker in ["session start", "session end"]
        )

        def make_explainability() -> dict:
            return {
                "chat_lines_processed": len([l for l in conversation_text.splitlines() if l.strip()]),
                "structured_events": structured_events_found,
                "structured_events_found": structured_events_found,
                "fallback_extractions": fallback_extractions_count,
                "fallback_extraction_used": fallback_extraction_used,
                "file_observations": file_observations_count,
                "file_observations_count": file_observations_count,
                "excluded_oem_generated_files": len(excluded_oem_generated_paths),
                "generated_concepts": [],
                "top_sources": [],
                "session_markers_detected": session_markers_detected,
            }

        reflection_status = {
            "structured": "used" if resolved_mode == "structured" else "skipped",
            "marker": "used" if resolved_mode == "markers" else "skipped",
            "dense": {
                "status": "skipped",
                "reason": "dense_disabled"
            }
        }
        if dense_enabled:
            reflection_status["dense"] = {
                "status": "skipped",
                "reason": "none"
            }

        if telemetry:
            duration = telemetry.get("duration_sec", 0)
            tool_calls = telemetry.get("total_tool_calls", 0)
            knowledge_events.append({
                "type": "telemetry",
                "concept": "Session Metrics",
                "evidence": f"Session duration: {duration}s, Tool calls: {tool_calls}",
                "confidence": 1,
                "source": "orchestrator",
            })

        # Process any structured/pending events
        if structured_enabled:
            for ev in all_events:
                norm_ev = self._validate_and_normalize_event(ev, warnings_list)
                if norm_ev:
                    knowledge_events.append({
                        "type": norm_ev["event_type"],
                        "concept": norm_ev["concept_candidates"][0] if norm_ev["concept_candidates"] else "General Learning",
                        "evidence": norm_ev["evidence"],
                        "confidence": norm_ev["confidence"],
                        "source": norm_ev["source"] if norm_ev["source"] != "chat" else "opencode_hook",
                        "event_id": norm_ev["event_id"],
                        "timestamp": norm_ev["timestamp"],
                        "concept_candidates": norm_ev["concept_candidates"],
                        "summary": norm_ev["summary"],
                        "source_type": "agent_runtime_signal",
                        "ingestion_eligible": True,
                    })
                    structured_events_found += 1
                else:
                    events_rejected += 1

        if resolved_mode == "structured":
            # Already processed all_events in the shared block above
            if not isinstance(events, list) and not pending_events:
                return {
                    "status": "error",
                    "mode": resolved_mode,
                    "events_written": 0,
                    "events_rejected": 0,
                    "warnings": ["Structured mode requires 'events' parameter to be a list."],
                    "suggestion": "Pass a valid list of event dictionaries.",
                    "report_path": None,
                    "knowledge_events": [],
                    "canonical_events": [],
                    "explainability": make_explainability(),
                    "phase_timings": {},
                    "reflection": reflection_status
                }

        elif resolved_mode == "markers":
            extracted_markers = self._parse_markers(conversation_text) if marker_enabled else []
            if extracted_markers:
                for ev in extracted_markers:
                    knowledge_events.append({
                        "type": ev["event_type"],
                        "concept": ev["concept_candidates"][0] if ev.get("concept_candidates") else (ev.get("summary") or "General Learning")[:80].title(),
                        "evidence": ev["evidence"],
                        "confidence": ev["confidence"],
                        "source": ev["source"],
                        "event_id": ev["event_id"],
                        "timestamp": ev["timestamp"],
                        "concept_candidates": ev["concept_candidates"],
                        "summary": ev["summary"],
                        "source_type": ev.get("source_type"),
                        "ingestion_eligible": ev.get("ingestion_eligible"),
                    })
                structured_events_found = len(extracted_markers)
            else:
                if extraction_mode == "auto":
                    resolved_mode = "llm"
                else:
                    return {
                        "status": "empty",
                        "mode": resolved_mode,
                        "events_written": 0,
                        "events_rejected": 0,
                        "warnings": warnings_list,
                        "suggestion": "Use explicit markers or pass structured events.",
                        "report_path": None,
                        "knowledge_events": [],
                        "canonical_events": [],
                        "explainability": make_explainability(),
                        "phase_timings": {},
                        "reflection": reflection_status
                    }

        llm_skipped = False
        dense_reason = "none"
        if resolved_mode == "llm":
            import os
            is_explicit_llm = (extraction_mode == "llm")
            is_mock_llm = (os.environ.get("OEM_MOCK_LLM") == "true")
            if not is_explicit_llm and not dense_enabled and not is_mock_llm:
                llm_skipped = True
                dense_reason = "dense_disabled"
                reflection_status["dense"] = {
                    "status": "skipped",
                    "reason": "dense_disabled",
                    "severity": "info"
                }
            elif not llm_extraction_available():
                llm_skipped = True
                dense_reason = "dense_llm_unavailable"
                reflection_status["dense"] = {
                    "status": "skipped",
                    "reason": "dense_llm_unavailable",
                    "severity": "warning"
                }
                warnings_list.append("LLM extraction unavailable.")
                warnings_list.append("No reflection events were produced.")
                warnings_list.append("Use structured events or Observation:/Decision:/Outcome: markers to record memory without LLM.")
                if queue_pending:
                    self.queue_dense_reflection(project, session_id, conversation_text)
            else:
                try:
                    extracted_llm = self._run_llm_extraction(conversation_text, timeout_seconds)
                    if extracted_llm:
                        knowledge_events.extend(extracted_llm)
                        fallback_extraction_used = True
                        fallback_extractions_count = len(extracted_llm)
                    reflection_status["dense"] = {
                        "status": "completed",
                        "reason": "none"
                    }
                except TimeoutError:
                    reflection_status["dense"] = {
                        "status": "failed",
                        "reason": "timeout",
                        "severity": "warning"
                    }
                    if queue_pending:
                        self.queue_dense_reflection(project, session_id, conversation_text)
                    return {
                        "status": "partial",
                        "failed_step": "llm_extraction",
                        "mode": resolved_mode,
                        "events_written": 0,
                        "events_rejected": 0,
                        "message": "Session closed with partial reflection; LLM extraction timed out.",
                        "suggestion": "Retry with structured events or explicit markers.",
                        "warnings": ["LLM extraction timed out before producing validated events."],
                        "report_path": None,
                        "knowledge_events": [],
                        "canonical_events": [],
                        "explainability": make_explainability(),
                        "phase_timings": {},
                        "reflection": reflection_status
                    }

        seen = set()
        canonical_events = []
        for ev in knowledge_events:
            e_type = ev.get("type") or ev.get("event_type", "observation")
            concept_str = ev.get("concept", "General Learning")[:80]
            evidence = ev.get("evidence", "")
            key = (e_type, concept_str, evidence.lower())
            if key in seen:
                continue
            seen.add(key)

            event_id = ev.get("event_id") or str(uuid.uuid4())
            timestamp = ev.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            concept_candidates = ev.get("concept_candidates") or [concept_str.lower()]
            source = ev.get("source", "chat")
            
            canonical_event = {
                "event_id": event_id,
                "timestamp": timestamp,
                "project": project or "default",
                "session_id": session_id,
                "event_type": e_type,
                "concept_candidates": concept_candidates,
                "summary": ev.get("summary") or f"{e_type.title()}: {concept_str}",
                "evidence": evidence,
                "confidence": ev.get("confidence", 1),
                "source": source,
                "schema_version": 1,
            }
            if ev.get("source_type"):
                canonical_event["source_type"] = ev["source_type"]
            if ev.get("ingestion_eligible") is not None:
                canonical_event["ingestion_eligible"] = ev["ingestion_eligible"]

            canonical_events.append(canonical_event)

        canonical_events.sort(key=lambda e: _SOURCE_PRIORITY.get(e.get("source", ""), 99))

        yaml_events = [
            {
                "type": e["event_type"],
                "concept": e["concept_candidates"][0] if e["concept_candidates"] else "General Learning",
                "evidence": e["evidence"],
                "confidence": e["confidence"],
                "event_id": e["event_id"],
            }
            for e in canonical_events
        ]

        source_counts = {}
        for ev in canonical_events:
            if ev.get("source") == "diff":
                evidence = ev.get("evidence", "")
                if "Modified:" in evidence:
                    fname = evidence.replace("Modified:", "").strip()
                    source_counts[fname] = source_counts.get(fname, 0) + 1
                elif "Code modified in workspace:" in evidence:
                    fpath = evidence.replace("Code modified in workspace:", "").strip()
                    fname = Path(fpath).name
                    source_counts[fname] = source_counts.get(fname, 0) + 1
            elif ev.get("source") in ("chat", "chat-fallback", "agent_structured"):
                source_counts["chat"] = source_counts.get("chat", 0) + 1
        
        top_sources = sorted(source_counts.keys(), key=lambda k: source_counts[k], reverse=True)[:5]

        explainability = {
            "chat_lines_processed": len([l for l in conversation_text.splitlines() if l.strip()]),
            "structured_events": structured_events_found,
            "structured_events_found": structured_events_found,
            "fallback_extractions": fallback_extractions_count,
            "fallback_extraction_used": fallback_extraction_used,
            "file_observations": file_observations_count,
            "file_observations_count": file_observations_count,
            "excluded_oem_generated_files": len(excluded_oem_generated_paths),
            "generated_concepts": [
                e["concept_candidates"][0]
                for e in canonical_events
                if e.get("source") in ("chat", "chat-fallback", "agent_structured")
            ],
            "top_sources": top_sources,
            "session_markers_detected": any(
                marker in conversation_text.lower()
                for marker in ["session start", "session end"]
            ),
        }

        if llm_skipped:
            if dense_reason == "dense_llm_unavailable":
                status_val = "warn"
            else:
                status_val = "success" if len(canonical_events) > 0 else "empty"
        else:
            status_val = "partial" if (resolved_mode == "structured" and events_rejected > 0) else "success"
            if not canonical_events:
                status_val = "empty"

        suggestion_val = None
        if not canonical_events:
            suggestion_val = "Use explicit markers or pass structured events."

        return {
            "status": status_val,
            "mode": resolved_mode,
            "events_written": len(canonical_events),
            "events_rejected": events_rejected,
            "warnings": warnings_list,
            "suggestion": suggestion_val,
            "report_path": None,
            "knowledge_events": yaml_events,
            "canonical_events": canonical_events,
            "explainability": explainability,
            "reflection_time": time.perf_counter() - start_t,
            "reflection": reflection_status
        }

    def reflect_session(
        self,
        project: str | None = None,
        conversation_text: str = "",
        session_id: str = "",
        telemetry: dict | None = None,
        session_started_at: float | None = None,
        progress_callback = None,
        events: list[dict] | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
    ) -> dict:
        import time
        from pathlib import Path
        
        # Call the new extract_session_events method
        res = self.extract_session_events(
            project=project,
            conversation_text=conversation_text,
            session_id=session_id,
            telemetry=telemetry,
            session_started_at=session_started_at,
            progress_callback=progress_callback,
            events=events,
            extraction_mode=extraction_mode,
            timeout_seconds=timeout_seconds,
        )

        status_val = res.get("status")
        if status_val in ("error", "empty", "warn") or (status_val == "partial" and res.get("failed_step") == "llm_extraction"):
            return res

        canonical_events = res.get("canonical_events", [])
        
        # Persist events using public append_events
        if progress_callback is not None:
            try:
                progress_callback("append_events")
            except Exception:
                pass
        t_append_start = time.perf_counter()
        
        self.engine.state.append_events(canonical_events, project)
        append_events_time = time.perf_counter() - t_append_start

        # Write session report
        if progress_callback is not None:
            try:
                progress_callback("write_report")
            except Exception:
                pass
        t_write_start = time.perf_counter()

        sessions_dir = self.engine._sessions_dir(project)
        sfs = self.engine._sfs(project)
        date_str = time.strftime("%Y-%m-%d")
        report_file = sessions_dir / f"{date_str}.md"
        counter = 1
        while sfs.exists(report_file) and counter < 1000:
            report_file = sessions_dir / f"{date_str}_{counter}.md"
            counter += 1

        yaml_events = res.get("knowledge_events", [])
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
        try:
            sfs.write_text(report_file, report, force_allow_truncation=True)
        except OSError as e:
            logger.error("Failed to write session learning report to %s: %s", report_file, e)
            return {"status": "error", "failed_step": "reflection", "message": f"Failed to write session learning report: {e}"}

        write_report_time = time.perf_counter() - t_write_start

        # Emit reflection metrics
        try:
            from oem_knowledge.tools.metrics import update_metrics_file
            from oem_knowledge.engine import find_harness_root, OEM_DIR
            p = Path(project or ".").resolve()
            root = find_harness_root(p) or p
            metrics_file = (root / OEM_DIR / "state" / "metrics.json")
            
            structured_events_found = res.get("explainability", {}).get("structured_events_found", 0)
            fallback_extraction_used = res.get("explainability", {}).get("fallback_extraction_used", False)
            file_observations_count = res.get("explainability", {}).get("file_observations_count", 0)
            
            update_metrics_file(metrics_file, {
                "structured_events": structured_events_found,
                "fallback_extractions": 1 if fallback_extraction_used else 0,
                "file_observations": file_observations_count,
                "empty_reflections": 1 if structured_events_found == 0 and not fallback_extraction_used and file_observations_count == 0 else 0,
                "reflections": 1,
            })
        except Exception as e:
            logger.warning("Failed to emit reflection metrics: %s", e)

        # Update timings
        phase_timings = {
            "reflection": res.get("reflection_time", 0.0),
            "append_events": append_events_time,
            "write_report": write_report_time,
        }

        return {
            "status": status_val,
            "mode": res.get("mode"),
            "events_written": len(canonical_events),
            "events_rejected": res.get("events_rejected", 0),
            "warnings": res.get("warnings", []),
            "suggestion": None,
            "report_path": str(report_file),
            "knowledge_events": yaml_events,
            "canonical_events": canonical_events,
            "explainability": res.get("explainability"),
            "phase_timings": phase_timings,
            "reflection": res.get("reflection"),
        }

    _original_reflect_session_func = reflect_session
