# TODO: migrate to canonical frontmatter parser
# (oem_knowledge.markdown.frontmatter) after wiki/concept/search/recovery
# paths are stabilized.

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from oem_knowledge.models import SkillCandidate, SkillPromotionEvent

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

logger = logging.getLogger(__name__)


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_frontmatter(text: str) -> dict:
    metadata = {}
    lines = text.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        
        # strip quotes
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        
        # parse simple lists like [ev_1, ev_2]
        if val.startswith("[") and val.endswith("]"):
            items_str = val[1:-1].strip()
            if items_str:
                val = [item.strip().strip('"').strip("'") for item in items_str.split(",")]
            else:
                val = []
        metadata[key] = val
    return metadata


def dump_frontmatter(metadata: dict) -> str:
    lines = ["---"]
    for k, v in metadata.items():
        if isinstance(v, list):
            list_str = ", ".join(f'"{item}"' for item in v)
            lines.append(f"{k}: [{list_str}]")
        else:
            if ":" in str(v) or "-" in str(v) or " " in str(v) or "/" in str(v):
                lines.append(f'{k}: "{v}"')
            else:
                lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def parse_markdown_body(body_text: str) -> dict:
    result = {
        "title": "",
        "trigger": "",
        "recommended_behavior": "",
        "evidence": [],
        "rationale": "",
    }
    
    # Extract Title: `# Title`
    title_match = re.search(r"^\s*#\s+(.+)$", body_text, re.MULTILINE)
    if title_match:
        result["title"] = title_match.group(1).strip()
        
    # Split by `## Header`
    sections = re.split(r"^\s*##\s+(.+)$", body_text, flags=re.MULTILINE)
    for i in range(1, len(sections), 2):
        header = sections[i].strip().lower()
        content = sections[i+1].strip() if i+1 < len(sections) else ""
        
        if header == "trigger":
            result["trigger"] = content
        elif header in ("recommended behavior", "skill"):
            result["recommended_behavior"] = content
        elif header == "evidence":
            evidence_items = []
            for line in content.splitlines():
                line_stripped = line.strip()
                if line_stripped.startswith("-") or line_stripped.startswith("*"):
                    evidence_items.append(line_stripped[1:].strip())
                elif line_stripped:
                    evidence_items.append(line_stripped)
            result["evidence"] = evidence_items
        elif header in ("why this should become a skill", "rationale"):
            result["rationale"] = content
            
    return result


def dump_markdown_body(candidate: SkillCandidate) -> str:
    body = []
    body.append(f"# {candidate.title}\n")
    body.append("## Trigger")
    body.append(candidate.trigger + "\n")
    body.append("## Recommended behavior")
    body.append(candidate.recommended_behavior + "\n")
    body.append("## Evidence")
    if candidate.evidence:
        body.append("\n".join(f"- {item}" for item in candidate.evidence) + "\n")
    else:
        body.append("\n")
    body.append("## Why this should become a skill")
    body.append(candidate.rationale + "\n")
    body.append("## Status")
    body.append(candidate.status.title())
    return "\n".join(body)


def dump_approved_skill_markdown(candidate: SkillCandidate, approved_at: str) -> str:
    frontmatter_data = {
        "generated_by": "openempiric",
        "source_type": "oem_project_skill",
        "status": "approved",
        "slug": candidate.slug,
        "approved_at": approved_at,
    }
    frontmatter = dump_frontmatter(frontmatter_data)
    
    body = []
    body.append(f"# {candidate.title}\n")
    body.append("## Trigger")
    body.append(candidate.trigger + "\n")
    body.append("## Skill")
    body.append(candidate.recommended_behavior + "\n")
    body.append("## Evidence")
    if candidate.evidence:
        body.append("\n".join(f"- {item}" for item in candidate.evidence) + "\n")
    else:
        body.append("\n")
    
    return f"{frontmatter}\n\n" + "\n".join(body)


def dump_superseded_skill_markdown(candidate: SkillCandidate, approved_at: str, superseded_at: str, reason: str = "") -> str:
    frontmatter_data = {
        "generated_by": "openempiric",
        "source_type": "oem_project_skill",
        "status": "superseded",
        "slug": candidate.slug,
        "approved_at": approved_at,
        "superseded_at": superseded_at,
    }
    if reason:
        frontmatter_data["superseded_reason"] = reason
    frontmatter = dump_frontmatter(frontmatter_data)
    
    body = []
    body.append(f"# {candidate.title}\n")
    body.append("## Trigger")
    body.append(candidate.trigger + "\n")
    body.append("## Skill")
    body.append(candidate.recommended_behavior + "\n")
    body.append("## Evidence")
    if candidate.evidence:
        body.append("\n".join(f"- {item}" for item in candidate.evidence) + "\n")
    else:
        body.append("\n")
    
    return f"{frontmatter}\n\n" + "\n".join(body)


def _read_approved_at_from_skill_file(filepath: Path, sfs) -> str | None:
    if sfs.exists(filepath):
        try:
            content = sfs.read_text(filepath)
            parts = content.split("---", 2)
            if len(parts) >= 3:
                metadata = parse_frontmatter(parts[1])
                return metadata.get("approved_at")
        except Exception:
            pass
    return None


class SkillService:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine

    def create_skill_from_template(
        self,
        name: str,
        description: str = "",
        project: str | None = None,
    ) -> SkillCandidate:
        import uuid
        import re as _re
        now = utc_iso()
        cid = uuid.uuid4().hex[:12]
        slug = name.lower().replace(" ", "-").replace("_", "-")
        slug = _re.sub(r"[^a-z0-9-]", "", slug).strip("-")[:80]
        trigger = name.lower().replace("-", " ")

        candidate = SkillCandidate(
            candidate_id=cid,
            slug=slug,
            title=name,
            trigger=trigger,
            recommended_behavior=description or f"Skill candidate for {name}",
            evidence=[],
            rationale=f"Manually created skill candidate: {name}",
            confidence="medium",
            status="proposed",
            source_event_ids=[],
            source_concept_ids=[],
            created_at=now,
            updated_at=now,
        )

        sfs = self.engine._sfs(project)
        layout = self.engine.layout(project)
        filepath = layout.skill_candidates_dir / f"{slug}.md"

        frontmatter_data = {
            "generated_by": "openempiric",
            "source_type": "oem_skill_candidate",
            "status": candidate.status,
            "confidence": candidate.confidence,
            "candidate_id": candidate.candidate_id,
            "slug": candidate.slug,
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
        }
        if candidate.source_event_ids:
            frontmatter_data["source_event_ids"] = candidate.source_event_ids
        if candidate.source_concept_ids:
            frontmatter_data["source_concept_ids"] = candidate.source_concept_ids

        frontmatter = dump_frontmatter(frontmatter_data)
        body = dump_markdown_body(candidate)
        content = f"{frontmatter}\n\n{body}"
        sfs.write_text(filepath, content, force_allow_truncation=True)

        return candidate

    def create_skill_candidate(
        self,
        candidate_id: str,
        slug: str,
        title: str,
        trigger: str,
        recommended_behavior: str,
        evidence: list[str],
        rationale: str,
        confidence: Literal["low", "medium", "high"] = "medium",
        status: Literal["proposed", "approved", "rejected", "deferred", "superseded"] = "proposed",
        source_event_ids: list[str] | None = None,
        source_concept_ids: list[str] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        project: str | None = None,
    ) -> SkillCandidate:
        now = utc_iso()
        candidate = SkillCandidate(
            candidate_id=candidate_id,
            slug=slug,
            title=title,
            trigger=trigger,
            recommended_behavior=recommended_behavior,
            evidence=evidence,
            rationale=rationale,
            confidence=confidence,
            status=status,
            source_event_ids=source_event_ids or [],
            source_concept_ids=source_concept_ids or [],
            created_at=created_at or now,
            updated_at=updated_at or now,
        )

        sfs = self.engine._sfs(project)
        layout = self.engine.layout(project)
        filepath = layout.skill_candidates_dir / f"{slug}.md"

        frontmatter_data = {
            "generated_by": "openempiric",
            "source_type": "oem_skill_candidate",
            "status": candidate.status,
            "confidence": candidate.confidence,
            "candidate_id": candidate.candidate_id,
            "slug": candidate.slug,
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
        }
        if candidate.source_event_ids:
            frontmatter_data["source_event_ids"] = candidate.source_event_ids
        if candidate.source_concept_ids:
            frontmatter_data["source_concept_ids"] = candidate.source_concept_ids

        frontmatter = dump_frontmatter(frontmatter_data)
        body = dump_markdown_body(candidate)
        content = f"{frontmatter}\n\n{body}"

        sfs.write_text(filepath, content, force_allow_truncation=True)

        # Record promotion event
        promo_event = SkillPromotionEvent(
            timestamp=now,
            candidate_id=candidate.candidate_id,
            slug=candidate.slug,
            event_type=candidate.status,
            previous_status=None,
            new_status=candidate.status,
            notes=f"Candidate proposed with status {candidate.status}",
        )
        self.record_skill_promotion_event(promo_event, project)

        return candidate

    def load_skill_candidate(self, slug: str, project: str | None = None) -> SkillCandidate | None:
        sfs = self.engine._sfs(project)
        layout = self.engine.layout(project)
        filepath = layout.skill_candidates_dir / f"{slug}.md"
        if not sfs.exists(filepath):
            return None

        content = sfs.read_text(filepath)
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        metadata = parse_frontmatter(parts[1])
        body_data = parse_markdown_body(parts[2])

        return SkillCandidate(
            candidate_id=metadata.get("candidate_id", ""),
            slug=metadata.get("slug", slug),
            title=body_data.get("title", ""),
            trigger=body_data.get("trigger", ""),
            recommended_behavior=body_data.get("recommended_behavior", ""),
            evidence=body_data.get("evidence") or [],
            rationale=body_data.get("rationale", ""),
            confidence=metadata.get("confidence", "medium"),
            status=metadata.get("status", "proposed"),
            source_event_ids=metadata.get("source_event_ids") or [],
            source_concept_ids=metadata.get("source_concept_ids") or [],
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
        )

    def list_skill_candidates(self, project: str | None = None) -> list[SkillCandidate]:
        layout = self.engine.layout(project)
        candidates_dir = layout.skill_candidates_dir
        if not candidates_dir.exists():
            return []

        candidates = []
        for fp in candidates_dir.glob("*.md"):
            slug = fp.stem
            candidate = self.load_skill_candidate(slug, project)
            if candidate:
                candidates.append(candidate)
        return candidates

    def update_skill_candidate_status(self, slug: str, status: str, project: str | None = None, force: bool = False) -> SkillCandidate | None:
        candidate = self.load_skill_candidate(slug, project)
        if not candidate:
            return None

        prev_status = candidate.status
        if prev_status == status:
            return candidate

        # Enforce status transition restrictions
        if prev_status == "rejected" and status == "approved" and not force:
            raise ValueError("Cannot transition from rejected to approved directly unless forced.")
        if prev_status == "approved" and status in ("deferred", "rejected") and not force:
            raise ValueError(f"Cannot demote approved skill to {status} unless forced.")

        candidate.status = status
        now = utc_iso()
        candidate.updated_at = now

        # Save updated candidate in skill_candidates/
        sfs = self.engine._sfs(project)
        layout = self.engine.layout(project)
        filepath = layout.skill_candidates_dir / f"{slug}.md"

        frontmatter_data = {
            "generated_by": "openempiric",
            "source_type": "oem_skill_candidate",
            "status": candidate.status,
            "confidence": candidate.confidence,
            "candidate_id": candidate.candidate_id,
            "slug": candidate.slug,
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
        }
        if candidate.source_event_ids:
            frontmatter_data["source_event_ids"] = candidate.source_event_ids
        if candidate.source_concept_ids:
            frontmatter_data["source_concept_ids"] = candidate.source_concept_ids

        frontmatter = dump_frontmatter(frontmatter_data)
        body = dump_markdown_body(candidate)
        content = f"{frontmatter}\n\n{body}"
        sfs.write_text(filepath, content, force_allow_truncation=True)

        # Handle approved state: save to .oem/skills/
        skills_filepath = layout.skills_dir / f"{slug}.md"
        if status == "approved":
            content_approved = dump_approved_skill_markdown(candidate, now)
            sfs.write_text(skills_filepath, content_approved, force_allow_truncation=True)
        elif prev_status == "approved" and status in ("deferred", "rejected") and force:
            # Mark approved skill as superseded, do not silently delete
            approved_at = _read_approved_at_from_skill_file(skills_filepath, sfs) or now
            content_superseded = dump_superseded_skill_markdown(
                candidate,
                approved_at=approved_at,
                superseded_at=now,
                reason=f"Demoted to {status} with force"
            )
            sfs.write_text(skills_filepath, content_superseded, force_allow_truncation=True)

        # Record promotion event
        promo_event = SkillPromotionEvent(
            timestamp=now,
            candidate_id=candidate.candidate_id,
            slug=candidate.slug,
            event_type=status,
            previous_status=prev_status,
            new_status=status,
            notes=f"Candidate status updated from {prev_status} to {status}",
        )
        self.record_skill_promotion_event(promo_event, project)

        return candidate

    def record_skill_promotion_event(self, event: SkillPromotionEvent | dict, project: str | None = None) -> None:
        if isinstance(event, dict):
            event_data = event
        else:
            event_data = event.model_dump()

        layout = self.engine.layout(project)
        promotions_path = layout.skill_promotions_path
        lock_path = promotions_path.with_suffix(".lock")

        from oem_knowledge.fs import FileLock
        with FileLock(lock_path):
            sfs = self.engine._sfs(project)
            line_str = json.dumps(event_data) + "\n"
            sfs.append_text(promotions_path, line_str)
