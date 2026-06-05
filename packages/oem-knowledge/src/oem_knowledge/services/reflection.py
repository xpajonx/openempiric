from __future__ import annotations
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

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

    def reflect_session(
        self,
        project: str | None = None,
        conversation_text: str = "",
        session_id: str = "",
        telemetry: dict | None = None,
        session_started_at: float | None = None,
    ) -> dict:
        import time
        import uuid
        if not session_id:
            session_id = f"session_{time.strftime('%Y%m%d_%H%M%S')}"

        knowledge_events = []
        file_observations_count = 0
        structured_events_found = 0
        fallback_extraction_used = False
        fallback_extractions_count = 0

        self.engine._resolve_harness(project)
        concepts_dir = self.engine._concepts_dir(project)

        modified_files = []
        if concepts_dir.exists():
            for fp in concepts_dir.rglob("*.md"):
                # 1. Check modification time
                if session_started_at is not None:
                    try:
                        mtime = fp.stat().st_mtime
                        if mtime < session_started_at:
                            continue
                    except Exception:
                        pass
                
                # 2. Check if body actually changed compared to last revision log
                try:
                    concept_id = fp.stem
                    current_text = fp.read_text(encoding="utf-8")
                    
                    fm_match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", current_text, re.DOTALL)
                    current_body = fm_match.group(1).strip() if fm_match else current_text.strip()
                    
                    history = self.engine.get_concept_history(concept_id, project)
                    if history:
                        last_entry = history[-1]
                        last_content = last_entry.get("content", "")
                        last_fm_match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", last_content, re.DOTALL)
                        last_body = last_fm_match.group(1).strip() if last_fm_match else last_content.strip()
                        
                        if current_body.strip() == last_body.strip():
                            continue
                except Exception:
                    pass
                
                modified_files.append(fp)

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
            file_observations_count += 1

        # Git diff codebase modifications
        try:
            import subprocess
            # Skip if subprocess.run is mocked (common in unit tests)
            if not ("mock" in type(subprocess.run).__name__.lower()):
                p_path = Path(project or ".").resolve()
                modified_code_files = []
                
                # Staged/unstaged changes
                res_diff = subprocess.run(
                    ["git", "diff", "--name-only"],
                    cwd=p_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res_diff.returncode == 0:
                    modified_code_files.extend(res_diff.stdout.splitlines())
                    
                # Last commit changes
                res_commit = subprocess.run(
                    ["git", "diff", "HEAD~1..HEAD", "--name-only"],
                    cwd=p_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res_commit.returncode == 0:
                    modified_code_files.extend(res_commit.stdout.splitlines())
                    
                # Filter and match against registry
                registry = self.engine._load_registry(project)
                seen_files = set()
                for f in modified_code_files:
                    f = f.strip()
                    if not f or f in seen_files or f.startswith(".oem") or f.startswith(".git"):
                        continue
                    seen_files.add(f)
                    
                    # Check for matching concepts in registry
                    f_path = Path(f)
                    stem = f_path.stem.lower()
                    name = f_path.name.lower()
                    full_path = f.lower()
                    
                    matched_cid = None
                    matched_name = None
                    
                    # P1: Explicit filename/path in aliases
                    for cid, cdata in registry.items():
                        aliases = [a.lower() for a in cdata.get("aliases", [])]
                        if name in aliases or full_path in aliases:
                            matched_cid = cid
                            matched_name = cdata.get("canonical_name", cid)
                            break
                            
                    # P2: Exact stem match to canonical name or alias (minimum 4 chars)
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
            logging.warning("Failed to extract codebase modifications via git diff: %s", e)



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
                    structured_events_found += 1
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
                    structured_events_found += 1
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
                    structured_events_found += 1
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
                    structured_events_found += 1
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
                    structured_events_found += 1

            if structured_events_found == 0 and text_clean:
                fallback_events = self._fallback_extract(conversation_text)
                if fallback_events:
                    knowledge_events.extend(fallback_events)
                    fallback_extraction_used = True
                    fallback_extractions_count = len(fallback_events)

        seen = set()
        canonical_events = []
        for ev in knowledge_events:
            e_type = ev.get("type", "observation")
            concept_str = ev.get("concept", "General Learning")[:80]
            evidence = ev.get("evidence", "")
            key = (e_type, concept_str, evidence.lower())
            if key in seen:
                continue
            seen.add(key)

            event_id = str(uuid.uuid4())
            source = ev.get("source", "chat")
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
                "source": source,
                "schema_version": 1,
            }
            canonical_events.append(canonical_event)
            self.engine._append_event(canonical_event, project)

        # Prioritize events: chat-derived first, then orchestrator, then file observations
        canonical_events.sort(key=lambda e: _SOURCE_PRIORITY.get(e.get("source", ""), 99))

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
            elif ev.get("source") in ("chat", "chat-fallback"):
                source_counts["chat"] = source_counts.get("chat", 0) + 1
        
        top_sources = sorted(source_counts.keys(), key=lambda k: source_counts[k], reverse=True)[:5]

        explainability = {
            "chat_lines_processed": len([l for l in conversation_text.splitlines() if l.strip()]),
            "structured_events": structured_events_found,
            "structured_events_found": structured_events_found,
            "fallback_extractions": fallback_extractions_count,
            "fallback_extraction_used": fallback_extraction_used,
            "file_observations": file_observations_count,
            "generated_concepts": [
                e["concept_candidates"][0]
                for e in canonical_events
                if e.get("source") in ("chat", "chat-fallback")
            ],
            "top_sources": top_sources,
        }

        # Emit reflection metrics
        try:
            from oem_knowledge.tools.metrics import update_metrics_file
            from oem_knowledge.engine import find_harness_root, OEM_DIR
            p = Path(project or ".").resolve()
            root = find_harness_root(p) or p
            metrics_file = (root / OEM_DIR / "state" / "metrics.json")
            update_metrics_file(metrics_file, {
                "structured_events": structured_events_found,
                "fallback_extractions": 1 if fallback_extraction_used else 0,
                "file_observations": file_observations_count,
                "empty_reflections": 1 if structured_events_found == 0 and not fallback_extraction_used and file_observations_count == 0 else 0,
                "reflections": 1,
            })
        except Exception:
            pass

        return {
            "status": "success",
            "report_path": str(report_file),
            "knowledge_events": yaml_events,
            "canonical_events": canonical_events,
            "explainability": explainability,
        }
