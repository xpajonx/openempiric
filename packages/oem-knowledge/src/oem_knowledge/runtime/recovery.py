from __future__ import annotations
import sys
import datetime
import json
import re
import uuid
import time
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oem_knowledge.ui import render_panel
from .config import _OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS
from .session import SessionState
from oem_knowledge.tools.metrics import update_metrics_file

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

def parse_markdown_report(content: str) -> dict[str, Any]:
    fm = {}
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
    
    events = []
    events_match = re.search(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
    if events_match:
        try:
            data = json.loads(events_match.group(1))
            if isinstance(data, dict) and "knowledge_events" in data:
                events = data["knowledge_events"]
        except Exception:
            pass
    return {"frontmatter": fm, "events": events}

def build_markdown_report(date_str: str, project_name: str, events: list[dict[str, Any]]) -> str:
    yaml_events = []
    for e in events:
        yaml_events.append({
            "type": e.get("event_type") or e.get("type", "observation"),
            "concept": e["concept_candidates"][0] if e.get("concept_candidates") else e.get("concept", "General Learning"),
            "evidence": e.get("evidence", ""),
            "confidence": e.get("confidence", 1),
        })
    yaml_content = json.dumps({"knowledge_events": yaml_events}, indent=2)
    return f"""---
date: {date_str}
project: {project_name}
generated_by: openempiric
---
# Session Learning Report — {date_str}

## Knowledge Events
```json
{yaml_content}
```
"""

def cmd_recover(
    eng: KnowledgeEngine,
    project: str | None = None,
    abort: bool = False,
    status: bool = False,
    scope: str | None = None,
    dry_run: bool = False,
    apply: bool = False,
    backup: bool | None = None,
):
    harness = eng._resolve_harness(project)

    if scope == "reflection":
        # Run reflection recovery
        # 1. Locate paths
        events_path = eng.layout(project).events_path
        sessions_dir = eng.layout(project).sessions_dir
        
        # We also support runtime_events.jsonl if it exists
        event_files = [harness / "events.jsonl", harness / "runtime_events.jsonl"]
        existing_event_files = [f for f in event_files if f.exists()]

        # 2. Scan empty orphan session files
        empty_orphan_files = []
        if sessions_dir.exists():
            for f in sessions_dir.glob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                    parsed = parse_markdown_report(content)
                    if parsed.get("events") is None or len(parsed["events"]) == 0:
                        empty_orphan_files.append(f)
                except Exception:
                    empty_orphan_files.append(f)

        # 3. Read events and find manually appended / invalid / duplicate events
        invalid_jsonl_count = 0
        missing_metadata_events = []
        unique_events = []
        seen_keys = set()
        duplicate_events_count = 0

        # We keep track of file contents to rewrite them
        file_to_events = {}

        for ef in existing_event_files:
            file_events = []
            try:
                lines = ef.read_text(encoding="utf-8").splitlines()
                for line_no, line in enumerate(lines, 1):
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                        if not isinstance(ev, dict):
                            invalid_jsonl_count += 1
                            continue
                        
                        # Check missing metadata
                        has_missing = False
                        if not ev.get("event_id") and not ev.get("id"):
                            has_missing = True
                        if not ev.get("timestamp"):
                            has_missing = True
                        if not ev.get("source_type"):
                            has_missing = True

                        if has_missing:
                            missing_metadata_events.append(ev)

                        # Deduplicate
                        # Normalized key for uniqueness
                        normalized = dict(ev)
                        normalized.pop("event_id", None)
                        normalized.pop("id", None)
                        normalized.pop("timestamp", None)
                        normalized_key = json.dumps(normalized, sort_keys=True)
                        if normalized_key in seen_keys:
                            duplicate_events_count += 1
                        else:
                            seen_keys.add(normalized_key)
                            # Normalize/repair fields inline
                            if not ev.get("event_id") and not ev.get("id"):
                                ev["event_id"] = str(uuid.uuid4())
                            elif ev.get("id") and not ev.get("event_id"):
                                ev["event_id"] = ev.get("id")
                            
                            if not ev.get("timestamp"):
                                ev["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            if not ev.get("source_type"):
                                ev["source_type"] = "agent_transcript"
                            
                            unique_events.append(ev)
                            file_events.append(ev)
                    except json.JSONDecodeError:
                        invalid_jsonl_count += 1
            except Exception:
                pass
            file_to_events[ef] = file_events

        # 4. Find session reports inconsistent with event files
        inconsistent_reports = []
        reports_to_rebuild = {} # report_file -> correct_events_list
        
        # Group unique events from event files by date of their session/timestamp
        # We can extract the date part "YYYY-MM-DD" from timestamp or session_id
        session_to_events = {}
        for ev in unique_events:
            s_id = ev.get("session_id") or "default_session"
            session_to_events.setdefault(s_id, []).append(ev)

        # Map date to sorted list of session IDs and their events
        date_to_sessions = {}
        for s_id, s_evs in session_to_events.items():
            # Find date from first event timestamp or session_id
            date_str = None
            if s_evs:
                ts = s_evs[0].get("timestamp")
                if ts and len(ts) >= 10:
                    date_str = ts[:10]
            if not date_str:
                # Try to extract date from session_id format session_YYYYMMDD_HHMMSS
                match = re.search(r"session_(\d{4})(\d{2})(\d{2})", s_id)
                if match:
                    date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            if not date_str:
                date_str = time.strftime("%Y-%m-%d")
            
            date_to_sessions.setdefault(date_str, []).append((s_id, s_evs))

        # Sort sessions on each date chronologically
        for date_str, sessions in date_to_sessions.items():
            # Sort by session_id or timestamp
            sessions.sort(key=lambda item: item[0])
            
        # Find markdown reports and compare them
        if sessions_dir.exists():
            for date_str, sessions in date_to_sessions.items():
                # Report files on this date
                report_files = sorted([f for f in sessions_dir.glob(f"{date_str}*.md") if f not in empty_orphan_files])
                # Pair them up
                for idx, (s_id, s_evs) in enumerate(sessions):
                    if idx < len(report_files):
                        rf = report_files[idx]
                        if rf in empty_orphan_files:
                            continue
                        try:
                            content = rf.read_text(encoding="utf-8")
                            parsed = parse_markdown_report(content)
                            report_evs = parsed.get("events") or []
                            
                            # Compare report_evs with s_evs
                            # We compare them by type, concept, evidence
                            # Helper to normalize report event format to compare
                            def norm(e):
                                return (
                                    str(e.get("type") or e.get("event_type", "")).strip().lower(),
                                    str(e.get("concept") or "").strip().lower()[:80],
                                    str(e.get("evidence") or "").strip().lower()
                                )
                            
                            s_evs_norm = sorted([norm(e) for e in s_evs])
                            report_evs_norm = sorted([norm(e) for e in report_evs])
                            
                            # Check frontmatter generated_by
                            has_generated_by = parsed.get("frontmatter", {}).get("generated_by") == "openempiric"
                            
                            if s_evs_norm != report_evs_norm or not has_generated_by:
                                inconsistent_reports.append(rf)
                                reports_to_rebuild[rf] = (date_str, s_evs)
                        except Exception:
                            inconsistent_reports.append(rf)
                            reports_to_rebuild[rf] = (date_str, s_evs)

        # 5. Output results
        print("Found:")
        print(f"- {len(empty_orphan_files)} empty orphan session files")
        print(f"- {len(missing_metadata_events)} manually appended events missing source metadata")
        print(f"- {invalid_jsonl_count} invalid JSONL lines")
        print(f"- {duplicate_events_count} duplicate events")
        print(f"- {len(inconsistent_reports)} session reports inconsistent with event store")
        print()
        
        if len(empty_orphan_files) == 0 and len(missing_metadata_events) == 0 and invalid_jsonl_count == 0 and duplicate_events_count == 0 and len(inconsistent_reports) == 0:
            print("No repairs needed.")
            return

        print("Suggested repairs:")
        if empty_orphan_files:
            print("- remove empty orphan session files")
        if missing_metadata_events or invalid_jsonl_count or duplicate_events_count:
            print("- normalize event metadata and clean event log")
        if inconsistent_reports:
            print("- rebuild session report from runtime events")
        if backup is not False:
            print("- create backup before apply")
        print()

        if apply:
            # 1. Create backup
            if backup is not False:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                backup_dir = harness / "backups" / f"recover-{timestamp}"
                backup_dir.mkdir(parents=True, exist_ok=True)
                
                # Backup event files
                for ef in existing_event_files:
                    shutil.copy2(ef, backup_dir / ef.name)
                
                # Backup session reports
                if sessions_dir.exists():
                    backup_sessions_dir = backup_dir / "sessions"
                    backup_sessions_dir.mkdir(parents=True, exist_ok=True)
                    for f in sessions_dir.glob("*.md"):
                        shutil.copy2(f, backup_sessions_dir / f.name)
                print(f"Backup created at: {backup_dir.relative_to(harness.parent)}")

            # 2. Apply repairs
            # Remove empty/orphan files
            for f in empty_orphan_files:
                try:
                    f.unlink()
                except Exception:
                    pass
            
            # Rewrite event files (repaired, normalized, deduplicated)
            for ef in existing_event_files:
                file_events = file_to_events.get(ef, [])
                try:
                    with open(ef, "w", encoding="utf-8") as out_f:
                        for ev in file_events:
                            out_f.write(json.dumps(ev) + "\n")
                except Exception as e:
                    print(f"Error repairing event file {ef.name}: {e}")

            # Rebuild inconsistent session reports
            for rf, (date_str, s_evs) in reports_to_rebuild.items():
                try:
                    project_name = parsed.get("frontmatter", {}).get("project", "default")
                    new_report = build_markdown_report(date_str, project_name, s_evs)
                    rf.write_text(new_report, encoding="utf-8")
                except Exception as e:
                    print(f"Error rebuilding report {rf.name}: {e}")

            print("Repairs applied successfully.")
        return

    # Normal recovery (Active session recovery)
    active_session_file = harness / "state" / "active_session.json"
    
    session_state = SessionState.load(active_session_file)
    if not session_state:
        print(render_panel("OEM Recovery", ["No unfinished sessions detected."], status="info"))
        return

    session_id = session_state.session_id
    agent_name = session_state.agent
    started_at = session_state.started_at
    current_status = session_state.status
    transcript_path = session_state.transcript_path

    if status:
        started_str = datetime.datetime.fromtimestamp(started_at).isoformat() if started_at else "unknown"
        lines = [
            f"Session ID:      {session_id}",
            f"Agent:           {agent_name}",
            f"Lifecycle State: {current_status}",
            f"Started At:      {started_str}",
            f"Project:         {session_state.project}",
            f"Transcript Path: {transcript_path}",
            f"Context Path:    {session_state.context_path}",
            f"Temp Inst Path:  {session_state.temp_instructions}"
        ]
        print(render_panel("Active Session Status", lines, status="stats"))
        return

    if abort:
        context_path = session_state.context_path
        temp_inst = session_state.temp_instructions
        for path_str in (context_path, temp_inst, str(_OEM_RUNTIME_CONTEXT_PATH), str(_OEM_TEMP_INSTRUCTIONS)):
            if path_str:
                p = Path(path_str)
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
        try:
            session_state.status = "failed"
            active_session_file.unlink()
        except Exception:
            pass
        print(render_panel("Session Aborted", [f"Session {session_id} has been discarded and cleaned up."], status="ok"))
        return

    print(render_panel("Recovering Session", [f"Attempting to recover session {session_id} (State: {current_status})"], status="info"))
    
    from oem_knowledge.adapters import get_adapter
    adapter = get_adapter(agent_name, eng, project)

    chat_text = ""
    if transcript_path:
        t_file = Path(transcript_path)
        if t_file.exists():
            if hasattr(adapter, "parse_transcript"):
                chat_text = adapter.parse_transcript(t_file)
            else:
                chat_text = t_file.read_text(encoding="utf-8")

    if not chat_text:
        if hasattr(adapter, "discover_latest_transcript") and hasattr(adapter, "parse_transcript"):
            latest_t = adapter.discover_latest_transcript()
            if latest_t:
                chat_text = adapter.parse_transcript(latest_t)
        if not chat_text:
            chat_path = harness / "state" / f"chat_{session_id}.md"
            if chat_path.exists():
                chat_text = chat_path.read_text(encoding="utf-8")
                try:
                    chat_path.unlink()
                except Exception:
                    pass

    if not chat_text:
        print(render_panel("Recovery Failed", ["Could not find any conversation transcript or log for the session."], status="error"))
        sys.exit(1)

    try:
        commit_start = time.time()
        commit_res = eng.session_commit(
            project,
            conversation_text=chat_text,
            session_id=session_id,
            session_started_at=started_at
        )
        commit_duration = time.time() - commit_start
        eng.state.record_outcome("success", session_id=session_id, project=project)

        # Emit sessions_recovered metric
        try:
            metrics_file = harness / "state" / "metrics.json"
            update_metrics_file(metrics_file, {"sessions_recovered": 1})
        except Exception:
            pass

        try:
            session_state.status = "completed"
            active_session_file.unlink()
        except Exception:
            pass

        context_path = session_state.context_path
        temp_inst = session_state.temp_instructions
        for path_str in (context_path, temp_inst, str(_OEM_RUNTIME_CONTEXT_PATH), str(_OEM_TEMP_INSTRUCTIONS)):
            if path_str:
                p = Path(path_str)
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

        from oem_knowledge.runtime.supervisor import render_commit_complete_panel
        report_name = Path(commit_res['report_path']).name
        concepts_count = len(commit_res.get('materialized_log', []))
        exp = commit_res.get("explainability", {})
        obs_count = exp.get("file_observations", 0)

        print(
            render_commit_complete_panel(
                report_name=report_name,
                concepts_count=concepts_count,
                observations_count=obs_count,
                duration=commit_duration,
                structured_events=exp.get("structured_events", 0),
                fallback_concepts=exp.get("fallback_extractions", 0),
                file_observations=exp.get("file_observations", 0),
                index_stats=commit_res.get("index_stats"),
                retrieval_mode=eng.search.resolve_retrieval_mode()
            )
        )
    except Exception as e:
        print(render_panel("Recovery Commit Failed", [f"Error committing recovered session: {e}"], status="error"))
        sys.exit(1)
