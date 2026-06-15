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

def is_empty_orphan_session(content: str, file_date: str, dates_with_events: set[str]) -> bool:
    parsed = parse_markdown_report(content)
    events = parsed.get("events")
    if events:
        return False
    
    if "knowledge_events" in content and file_date in dates_with_events:
        return False

    text = content
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"^\s*#.*$", "", text, flags=re.MULTILINE)
    return len(text.strip()) == 0

def recover_reflection(
    eng: KnowledgeEngine,
    project: str | None = None,
    dry_run: bool = False,
    apply: bool = False,
    backup: bool | None = None,
    rebuild_reports: bool = False,
) -> dict:
    harness = eng._resolve_harness(project)

    if not dry_run and not apply:
        dry_run = True

    if dry_run and apply:
        raise ValueError("dry_run and apply are mutually exclusive.")

    if backup is False and not apply:
        raise ValueError("no-backup is only valid in apply mode.")

    events_path = eng.layout(project).events_path
    sessions_dir = eng.layout(project).sessions_dir
    
    event_files = [harness / "events.jsonl", harness / "runtime_events.jsonl"]
    existing_event_files = [f for f in event_files if f.exists()]

    invalid_jsonl_records = []
    missing_metadata_events = []
    unique_events = []
    
    seen_event_ids = set()
    seen_bodies = set()
    seen_summary_ts_source = set()
    
    duplicate_events_count = 0
    file_to_events = {}

    for ef in existing_event_files:
        file_events = []
        try:
            try:
                rel_file = str(ef.relative_to(harness))
            except Exception:
                rel_file = str(ef.name)
                
            lines = ef.read_text(encoding="utf-8").splitlines()
            for line_no, line in enumerate(lines, 1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                    if not isinstance(ev, dict):
                        invalid_jsonl_records.append({
                            "file": rel_file,
                            "line_number": line_no,
                            "raw_line": raw,
                            "error": "Not a JSON object"
                        })
                        continue
                    
                    has_missing = False
                    reasons = []
                    if not ev.get("event_id") and not ev.get("id"):
                        has_missing = True
                        reasons.append("event_id")
                    if not ev.get("timestamp"):
                        has_missing = True
                        reasons.append("timestamp")
                    if not ev.get("source_type"):
                        has_missing = True
                        reasons.append("source_type")

                    if has_missing:
                        missing_metadata_events.append((ev, reasons))

                    repaired_ev = dict(ev)
                    if has_missing:
                        repaired_ev["source"] = "recovered_reflection"
                        repaired_ev["source_type"] = "recovered_event"
                        repaired_ev["recovered_by"] = "oem recover --scope reflection"
                        repaired_ev["recovery_reason"] = "missing event_id/timestamp/source_type"
                        
                        if not repaired_ev.get("event_id") and not repaired_ev.get("id"):
                            repaired_ev["event_id"] = str(uuid.uuid4())
                        elif repaired_ev.get("id") and not repaired_ev.get("event_id"):
                            repaired_ev["event_id"] = repaired_ev.get("id")
                        
                        if not repaired_ev.get("timestamp"):
                            repaired_ev["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        
                        if not repaired_ev.get("event_type"):
                            repaired_ev["event_type"] = "observation"
                        
                        if not repaired_ev.get("summary"):
                            repaired_ev["summary"] = repaired_ev.get("evidence") or "Recovered event"

                    ev_id = repaired_ev.get("event_id") or repaired_ev.get("id")
                    body_parts = {k: v for k, v in repaired_ev.items() if k not in ("event_id", "id", "timestamp")}
                    normalized_body = json.dumps(body_parts, sort_keys=True)
                    triple = (repaired_ev.get("summary"), repaired_ev.get("timestamp"), repaired_ev.get("source"))
                    
                    is_duplicate = False
                    if ev_id and ev_id in seen_event_ids:
                        is_duplicate = True
                    elif normalized_body in seen_bodies:
                        is_duplicate = True
                    elif triple[0] and triple[1] and triple[2] and triple in seen_summary_ts_source:
                        is_duplicate = True
                        
                    if is_duplicate:
                        duplicate_events_count += 1
                    else:
                        if ev_id:
                            seen_event_ids.add(ev_id)
                        seen_bodies.add(normalized_body)
                        if triple[0] and triple[1] and triple[2]:
                            seen_summary_ts_source.add(triple)
                        unique_events.append(repaired_ev)
                        file_events.append(repaired_ev)
                        
                except json.JSONDecodeError as err:
                    invalid_jsonl_records.append({
                        "file": rel_file,
                        "line_number": line_no,
                        "raw_line": raw,
                        "error": str(err)
                    })
        except Exception:
            pass
        file_to_events[ef] = file_events

    # Collect dates with events
    dates_with_events = set()
    for ev in unique_events:
        s_id = ev.get("session_id") or "default_session"
        date_str = None
        ts = ev.get("timestamp")
        if ts and len(ts) >= 10:
            date_str = ts[:10]
        if not date_str:
            match = re.search(r"session_(\d{4})(\d{2})(\d{2})", s_id)
            if match:
                date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")
        dates_with_events.add(date_str)

    empty_orphan_files = []
    if sessions_dir.exists():
        for f in sessions_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                file_date = f.name[:10]
                if is_empty_orphan_session(content, file_date, dates_with_events):
                    empty_orphan_files.append(f)
            except Exception:
                pass

    inconsistent_reports = []
    reports_to_rebuild = {}
    reports_to_append_notes = {}
    
    session_to_events = {}
    for ev in unique_events:
        s_id = ev.get("session_id") or "default_session"
        session_to_events.setdefault(s_id, []).append(ev)

    date_to_sessions = {}
    for s_id, s_evs in session_to_events.items():
        date_str = None
        if s_evs:
            ts = s_evs[0].get("timestamp")
            if ts and len(ts) >= 10:
                date_str = ts[:10]
        if not date_str:
            match = re.search(r"session_(\d{4})(\d{2})(\d{2})", s_id)
            if match:
                date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")
        
        date_to_sessions.setdefault(date_str, []).append((s_id, s_evs))

    for date_str, sessions in date_to_sessions.items():
        sessions.sort(key=lambda item: item[0])
        
    if sessions_dir.exists():
        for date_str, sessions in date_to_sessions.items():
            report_files = sorted([f for f in sessions_dir.glob(f"{date_str}*.md") if f not in empty_orphan_files])
            for idx, (s_id, s_evs) in enumerate(sessions):
                if idx < len(report_files):
                    rf = report_files[idx]
                    if rf in empty_orphan_files:
                        continue
                    try:
                        content = rf.read_text(encoding="utf-8")
                        parsed = parse_markdown_report(content)
                        report_evs = parsed.get("events") or []
                        
                        def norm(e):
                            return (
                                str(e.get("type") or e.get("event_type", "")).strip().lower(),
                                str(e.get("concept") or "").strip().lower()[:80],
                                str(e.get("evidence") or "").strip().lower()
                            )
                        
                        s_evs_norm = sorted([norm(e) for e in s_evs])
                        report_evs_norm = sorted([norm(e) for e in report_evs])
                        
                        has_generated_by = parsed.get("frontmatter", {}).get("generated_by") == "openempiric"
                        
                        if s_evs_norm != report_evs_norm or not has_generated_by:
                            inconsistent_reports.append(rf)
                            if rebuild_reports:
                                reports_to_rebuild[rf] = (date_str, s_evs)
                            else:
                                if has_generated_by:
                                    mismatch_details = []
                                    mismatch_details.append(f"Event store has {len(s_evs)} events, while session report has {len(report_evs)} events.")
                                    store_diff = [e for e in s_evs if norm(e) not in report_evs_norm]
                                    report_diff = [e for e in report_evs if norm(e) not in s_evs_norm]
                                    if store_diff:
                                        mismatch_details.append("Events in store but missing/different in report:")
                                        for e in store_diff:
                                            mismatch_details.append(f"  - [{e.get('event_type', 'observation')}] Concept: {e.get('concept_candidates', ['unknown'])[0] if e.get('concept_candidates') else 'unknown'}, Summary: {e.get('summary', 'N/A')}")
                                    if report_diff:
                                        mismatch_details.append("Events in report but missing/different in store:")
                                        for e in report_diff:
                                            mismatch_details.append(f"  - [{e.get('type') or e.get('event_type', 'observation')}] Concept: {e.get('concept', 'unknown')}, Evidence: {e.get('evidence', 'N/A')}")
                                    reports_to_append_notes[rf] = (content, mismatch_details)
                    except Exception:
                        inconsistent_reports.append(rf)
                        if rebuild_reports:
                            reports_to_rebuild[rf] = (date_str, s_evs)

    issues = []
    if empty_orphan_files:
        issues.append(f"{len(empty_orphan_files)} empty orphan session files")
    if missing_metadata_events:
        issues.append(f"{len(missing_metadata_events)} manually appended events missing source metadata")
    if invalid_jsonl_records:
        issues.append(f"{len(invalid_jsonl_records)} invalid JSONL lines")
    if duplicate_events_count:
        issues.append(f"{duplicate_events_count} duplicate events")
    if inconsistent_reports:
        issues.append(f"{len(inconsistent_reports)} session reports inconsistent with event store")

    repairs = []
    changed_files = []
    warnings = []
    errors = []
    backup_dir = None
    quarantine_file = None
    report_file = None

    status = "success" if not issues else "warn"

    if apply:
        if backup is not False:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            backup_dir = harness / "backups" / f"recover-reflection-{timestamp}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            paths_to_backup = [
                harness / "events.jsonl",
                harness / "runtime_events.jsonl",
                harness / "sessions",
                harness / "reports",
                harness / "state"
            ]
            for src_path in paths_to_backup:
                if not src_path.exists():
                    continue
                if src_path.is_file():
                    rel = src_path.relative_to(harness)
                    dest = backup_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_path, dest)
                elif src_path.is_dir():
                    for f in src_path.rglob("*"):
                        if f.is_file():
                            rel = f.relative_to(harness)
                            dest = backup_dir / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(f, dest)

        if empty_orphan_files:
            repairs.append("remove empty orphan session files")
            for f in empty_orphan_files:
                try:
                    f.unlink()
                    changed_files.append(str(f))
                except Exception as e:
                    errors.append(f"Failed to remove orphan file {f.name}: {e}")
        
        for ef in existing_event_files:
            file_events = file_to_events.get(ef, [])
            try:
                with open(ef, "w", encoding="utf-8") as out_f:
                    for ev in file_events:
                        out_f.write(json.dumps(ev) + "\n")
                changed_files.append(str(ef))
            except Exception as e:
                errors.append(f"Error repairing event file {ef.name}: {e}")

        if invalid_jsonl_records:
            repairs.append("normalize event metadata and clean event log")
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            quarantine_file = harness / "reports" / f"recover-reflection-{timestamp}-invalid-jsonl.md"
            quarantine_file.parent.mkdir(parents=True, exist_ok=True)
            
            quarantine_content = [
                "---",
                "generated_by: openempiric",
                "source_type: oem_recovery_report",
                "scope: reflection",
                "---",
                "# Invalid JSONL Quarantine Report",
                "",
                "## Invalid JSONL",
                ""
            ]
            for rec in invalid_jsonl_records:
                quarantine_content.extend([
                    f"### {rec['file']}:{rec['line_number']}",
                    "",
                    f"Error: {rec['error']}",
                    "",
                    "```json",
                    rec['raw_line'],
                    "```",
                    ""
                ])
            
            try:
                quarantine_file.write_text("\n".join(quarantine_content), encoding="utf-8")
                changed_files.append(str(quarantine_file))
            except Exception as e:
                errors.append(f"Failed to write quarantine file: {e}")

        if reports_to_rebuild:
            repairs.append("rebuild session report from runtime events")
            for rf, (date_str, s_evs) in reports_to_rebuild.items():
                try:
                    parsed = parse_markdown_report(rf.read_text(encoding="utf-8"))
                    project_name = parsed.get("frontmatter", {}).get("project", "default")
                    new_report = build_markdown_report(date_str, project_name, s_evs)
                    rf.write_text(new_report, encoding="utf-8")
                    changed_files.append(str(rf))
                except Exception as e:
                    errors.append(f"Error rebuilding report {rf.name}: {e}")

        if reports_to_append_notes:
            repairs.append("append recovery notes to session reports")
            for rf, (content, mismatch_details) in reports_to_append_notes.items():
                try:
                    if "## Recovery Notes" in content:
                        content_base = content.split("## Recovery Notes")[0].rstrip()
                    else:
                        content_base = content.rstrip()
                    notes_text = "\n\n## Recovery Notes\n" + "\n".join(f"- {detail}" for detail in mismatch_details) + "\n"
                    rf.write_text(content_base + notes_text, encoding="utf-8")
                    changed_files.append(str(rf))
                except Exception as e:
                    errors.append(f"Error appending recovery notes to {rf.name}: {e}")

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        report_file = harness / "reports" / f"recover-reflection-{timestamp}.md"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        
        report_lines = [
            "---",
            "generated_by: openempiric",
            "source_type: oem_recovery_report",
            "scope: reflection",
            "---",
            "# Reflection Recovery Report",
            "",
            "## Summary",
            f"- Empty orphan files removed: {len(empty_orphan_files)}",
            f"- Invalid JSONL lines quarantined: {len(invalid_jsonl_records)}",
            f"- Events normalized (missing metadata): {len(missing_metadata_events)}",
            f"- Duplicate events removed: {duplicate_events_count}",
            f"- Inconsistent session reports: {len(inconsistent_reports)}",
            ""
        ]
        
        if empty_orphan_files:
            report_lines.extend([
                "### Removed Empty Orphan Files",
                *[f"- `{f.name}`" for f in empty_orphan_files],
                ""
            ])
            
        if missing_metadata_events:
            report_lines.extend([
                "### Normalized Events",
                "The following events had missing metadata normalized and provenance added:",
                ""
            ])
            for idx, (ev, reasons) in enumerate(missing_metadata_events, 1):
                report_lines.append(f"{idx}. Event concept: `{ev.get('concept_candidates', [''])[0] if ev.get('concept_candidates') else ev.get('concept', 'unknown')}` - missing: {', '.join(reasons)}")
            report_lines.append("")

        try:
            report_file.write_text("\n".join(report_lines), encoding="utf-8")
            changed_files.append(str(report_file))
        except Exception as e:
            errors.append(f"Failed to write recovery report: {e}")

        status = "success"

    from oem_knowledge.runtime.result import make_result
    return make_result(
        status=status,
        operation="recover_reflection",
        project=str(project or "."),
        mode="apply" if apply else "dry_run",
        scope="reflection",
        backup_dir=str(backup_dir) if backup_dir else None,
        report_path=str(report_file) if report_file else None,
        quarantine_file=str(quarantine_file) if quarantine_file else None,
        changed_files=changed_files,
        issues=issues,
        repairs=repairs if apply else [
            r for r in [
                "remove empty orphan session files" if empty_orphan_files else None,
                "normalize event metadata and clean event log" if (missing_metadata_events or invalid_jsonl_records or duplicate_events_count) else None,
                "rebuild session report from runtime events" if (inconsistent_reports and rebuild_reports) else ("append recovery notes to session reports" if inconsistent_reports else None)
            ] if r is not None
        ],
        warnings=warnings,
        errors=errors,
        empty_orphan_files=[str(f) for f in empty_orphan_files],
        invalid_jsonl_count=len(invalid_jsonl_records),
        missing_metadata_count=len(missing_metadata_events),
        duplicate_events_count=duplicate_events_count,
        inconsistent_reports=[str(f) for f in inconsistent_reports],
    )


def recover_registry(
    eng: KnowledgeEngine,
    project: str | None = None,
    dry_run: bool = False,
    apply: bool = False,
    backup: bool | None = None,
) -> dict:
    harness = eng._resolve_harness(project)
    registry_path = eng.layout(project).registry_path
    wiki_dir = eng.layout(project).concepts_dir
    events_path = eng.layout(project).events_path

    if not dry_run and not apply:
        dry_run = True

    if dry_run and apply:
        raise ValueError("dry_run and apply are mutually exclusive.")

    registry = {}
    if registry_path.exists():
        try:
            registry = eng.state._load_registry(project, lock=False)
        except Exception:
            pass

    wiki_files = list(wiki_dir.glob("concept_*.md")) if wiki_dir.exists() else []
    wiki_stems = {f.stem for f in wiki_files}
    registry_keys = set(registry.keys())
    concept_registry_keys = {k for k in registry_keys if k.startswith("concept_")}

    # 1. Orphan wiki concept files
    orphans = wiki_stems - registry_keys

    # 2. Registry entries missing wiki files
    missing_wiki = concept_registry_keys - wiki_stems

    # 3. Registry ID gaps
    gaps = []
    numeric_values = set()
    pattern = re.compile(r"^concept_(\d+)$")
    for cid in concept_registry_keys:
        m = pattern.match(cid)
        if m:
            numeric_values.add(int(m.group(1)))
    if numeric_values:
        max_val = max(numeric_values)
        for i in range(1, max_val + 1):
            if i not in numeric_values:
                gaps.append(f"concept_{i:03d}")

    # 4. Duplicate concept titles
    titles = {}
    duplicates = {}
    for cid, cdata in registry.items():
        if not isinstance(cdata, dict) or not cid.startswith("concept_"):
            continue
        title = cdata.get("canonical_name", "").lower()
        if title:
            if title in titles:
                if title not in duplicates:
                    duplicates[title] = [titles[title]]
                duplicates[title].append(cid)
            else:
                titles[title] = cid

    # 5. Partially materialized events
    partially_materialized = []
    all_event_ids = set()
    if events_path.exists():
        try:
            for line in events_path.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    ev = json.loads(line)
                    ev_id = ev.get("event_id") or ev.get("id")
                    ev_type = ev.get("event_type") or ev.get("type", "observation")
                    if ev_id and ev_type in ("observation", "validation", "failure", "needs_review", "canonical"):
                        all_event_ids.add(ev_id)
        except Exception:
            pass

    materialized_event_ids = set()
    for cid, cdata in registry.items():
        if isinstance(cdata, dict):
            materialized_event_ids.update(cdata.get("source_event_ids", []))

    partially_materialized = sorted(list(all_event_ids - materialized_event_ids))

    issues = []
    if orphans:
        issues.append(f"{len(orphans)} orphan wiki concept files")
    if missing_wiki:
        issues.append(f"{len(missing_wiki)} registry entries missing wiki files")
    if gaps:
        issues.append(f"{len(gaps)} registry ID gaps")
    if duplicates:
        issues.append(f"{len(duplicates)} duplicate concept titles")
    if partially_materialized:
        issues.append(f"{len(partially_materialized)} partially materialized events")

    repairs = []
    if orphans:
        repairs.append("reattach orphan wiki files to registry or mark as unmanaged")
    if missing_wiki:
        repairs.append("remove registry entries with missing wiki files")

    backup_file = None
    if apply:
        from oem_knowledge.fs import FileLock
        lock_path = registry_path.with_suffix(".lock")
        with FileLock(lock_path):
            if backup is not False and registry_path.exists():
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                backup_file = registry_path.with_name(f"concept_registry.json.bak-{timestamp}")
                shutil.copy2(registry_path, backup_file)

            # Re-load under lock
            if registry_path.exists():
                try:
                    registry = eng.state._load_registry(project, lock=False)
                except Exception:
                    pass

            for cid in orphans:
                fp = wiki_dir / f"{cid}.md"
                frontmatter = {}
                try:
                    text = fp.read_text(encoding="utf-8")
                    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
                    if fm_match:
                        for line in fm_match.group(1).splitlines():
                            if ":" in line:
                                k, v = line.split(":", 1)
                                frontmatter[k.strip()] = v.strip()
                except Exception:
                    pass

                c_id = frontmatter.get("concept_id")
                c_name = frontmatter.get("canonical_name")
                c_status = frontmatter.get("status")

                if c_id and c_name and c_status:
                    # Clean aliases
                    c_aliases = []
                    raw_aliases = frontmatter.get("aliases", "[]")
                    try:
                        c_aliases = json.loads(raw_aliases)
                    except Exception:
                        pass
                    # Clean source_event_ids
                    c_sevents = []
                    raw_sevents = frontmatter.get("source_event_ids", "[]")
                    try:
                        c_sevents = json.loads(raw_sevents)
                    except Exception:
                        pass

                    registry[cid] = {
                        "concept_id": cid,
                        "canonical_name": c_name,
                        "status": c_status,
                        "confidence": int(frontmatter.get("confidence", 3)),
                        "evidence_count": int(frontmatter.get("evidence_count", 0)),
                        "sessions": frontmatter.get("sessions", []),
                        "aliases": c_aliases,
                        "source_event_ids": c_sevents,
                    }
                else:
                    registry[cid] = {
                        "concept_id": cid,
                        "canonical_name": f"unmanaged-{cid}",
                        "status": "unmanaged",
                        "aliases": [],
                        "evidence_count": 0,
                    }

            # Remove entries that are missing wiki files
            for cid in missing_wiki:
                if cid in registry:
                    del registry[cid]

            eng.state._save_registry(registry, project, lock=False)

    from oem_knowledge.runtime.result import make_result
    return make_result(
        status="success" if not issues else "warn",
        operation="recover_registry",
        project=str(project or "."),
        mode="apply" if apply else "dry_run",
        orphans=sorted(list(orphans)),
        missing_wiki=sorted(list(missing_wiki)),
        gaps=sorted(gaps),
        duplicates=duplicates,
        partially_materialized=partially_materialized,
        issues=issues,
        repairs=repairs,
        backup_file=str(backup_file) if backup_file else None,
    )


def cmd_recover(
    eng: KnowledgeEngine,
    project: str | None = None,
    abort: bool = False,
    status: bool = False,
    scope: str | None = None,
    dry_run: bool = False,
    apply: bool = False,
    backup: bool | None = None,
    rebuild_reports: bool = False,
):
    harness = eng._resolve_harness(project)

    if scope == "registry":
        try:
            res = recover_registry(
                eng, project, dry_run=dry_run, apply=apply, backup=backup
            )
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        print("Registry / Wiki Drift Status:")
        print(f"- {len(res['orphans'])} orphan wiki concept files")
        print(f"- {len(res['missing_wiki'])} registry entries missing wiki files")
        print(f"- {len(res['gaps'])} registry ID gaps")
        print(f"- {len(res['duplicates'])} duplicate concept titles")
        print(f"- {len(res['partially_materialized'])} partially materialized events")
        print()

        if not res.get("issues"):
            print("No repairs needed.")
            return res

        print("Suggested repairs:")
        for r in res.get("repairs", []):
            if r:
                print(f"- {r}")
        if apply and backup is not False:
            print("- create backup before apply")
        print()

        if apply:
            if res.get("backup_file"):
                p = Path(res["backup_file"])
                try:
                    rel_print = p.relative_to(harness.parent)
                except Exception:
                    rel_print = p
                print(f"Backup created at: {rel_print}")
            print("Repairs applied successfully.")

        return res

    if scope == "reflection":
        try:
            res = recover_reflection(
                eng, project, dry_run=dry_run, apply=apply, backup=backup, rebuild_reports=rebuild_reports
            )
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        empty_orphan_count = len(res.get("empty_orphan_files", []))
        invalid_jsonl_count = res.get("invalid_jsonl_count", 0)
        missing_metadata_count = res.get("missing_metadata_count", 0)
        duplicate_events_count = res.get("duplicate_events_count", 0)
        inconsistent_reports_count = len(res.get("inconsistent_reports", []))

        print("Found:")
        print(f"- {empty_orphan_count} empty orphan session files")
        print(f"- {missing_metadata_count} manually appended events missing source metadata")
        print(f"- {invalid_jsonl_count} invalid JSONL lines")
        print(f"- {duplicate_events_count} duplicate events")
        print(f"- {inconsistent_reports_count} session reports inconsistent with event store")
        print()

        if not res.get("issues"):
            print("No repairs needed.")
            return res

        print("Suggested repairs:")
        for r in res.get("repairs", []):
            if r:
                print(f"- {r}")
        if apply and backup is not False:
            print("- create backup before apply")
        print()

        if apply:
            if res.get("backup_dir"):
                p = Path(res["backup_dir"])
                try:
                    rel_print = p.relative_to(harness.parent)
                except Exception:
                    rel_print = p
                print(f"Backup created at: {rel_print}")

            if res.get("quarantine_file"):
                p = Path(res["quarantine_file"])
                try:
                    rel_quar = p.relative_to(harness.parent)
                except Exception:
                    rel_quar = p
                print(f"Quarantined {invalid_jsonl_count} lines to: {rel_quar}")

            if res.get("report_path"):
                p = Path(res["report_path"])
                try:
                    rel_rep = p.relative_to(harness.parent)
                except Exception:
                    rel_rep = p
                print(f"Repairs applied successfully. Report written to: {rel_rep}")

        return res

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
