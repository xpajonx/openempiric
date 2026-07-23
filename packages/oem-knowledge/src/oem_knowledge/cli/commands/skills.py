from __future__ import annotations

import sys


def run_skills_command(args):
    # Setup deferred logging Configuration
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    project = getattr(args, "project", None)
    if project == ".":
        project = None

    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project)
    import atexit; atexit.register(eng.close)

    action = args.skills_action
    
    if action == "list":
        candidates = eng.skills.list_skill_candidates(project)
        if not candidates:
            print("No skill candidates found.")
            return
        
        from oem_knowledge.ui import render_panel
        lines = [f"{'Slug':<40} | {'Confidence':<10} | {'Status':<10} | {'Evidence':<5}"]
        lines.append("-" * 75)
        for c in candidates:
            lines.append(f"{c.slug:<40} | {c.confidence:<10} | {c.status:<10} | {len(c.evidence):<5}")
        print(render_panel("Skill Candidates", lines, status="ok"))

    elif action == "show":
        candidate = eng.skills.load_skill_candidate(args.slug, project)
        if not candidate:
            # Maybe it is an approved skill
            layout = eng.layout(project)
            approved_path = layout.skills_dir / f"{args.slug}.md"
            if approved_path.exists():
                print(approved_path.read_text(encoding="utf-8"))
                return
            print(f"Error: Candidate/Skill '{args.slug}' not found.")
            sys.exit(1)
        
        # Display the candidate
        from oem_knowledge.ui import render_panel
        lines = [
            f"Candidate: {candidate.title}",
            f"Slug:      {candidate.slug}",
            f"Status:    {candidate.status}",
            f"Confidence:{candidate.confidence}",
            "",
            "## Trigger",
            candidate.trigger,
            "",
            "## Behavior",
            candidate.recommended_behavior,
            "",
            "## Rationale",
            candidate.rationale,
            "",
            "## Evidence",
        ]
        for ev in candidate.evidence:
            lines.append(f"- {ev}")
        print(render_panel(f"Skill Candidate: {candidate.slug}", lines, status="ok"))

    elif action == "suggest":
        # Always default to relaxed mode for best UX.
        # The --relaxed flag exists in the parser for explicit documentation.
        res = eng.skill_promotion.evaluate_skill_candidates(project, relaxed=True)
        from oem_knowledge.ui import render_panel
        if res.get("status") == "error":
            print(render_panel("Suggestion Error", res.get("warnings", []), status="error"))
            return
        
        lines = [
            f"Candidates created: {res.get('candidates_created', 0)}",
            f"Candidates skipped: {res.get('candidates_skipped', 0)}",
            "",
            "Created Suggestions:",
        ]
        for c in res.get("candidates", []):
            lines.append(f"- {c['title']} ({c['slug']}) - confidence: {c['confidence']}")
        print(render_panel("Skill Suggestions", lines, status="ok"))

    elif action == "approve":
        try:
            cand = eng.skills.update_skill_candidate_status(args.slug, "approved", project, force=args.force)
            if cand:
                print(f"Approved candidate '{args.slug}'. Approved skill written to .oem/skills/{args.slug}.md")
            else:
                print(f"Error: Candidate '{args.slug}' not found.")
                sys.exit(1)
        except Exception as e:
            print(f"Error during approval: {e}")
            sys.exit(1)

    elif action == "reject":
        try:
            cand = eng.skills.update_skill_candidate_status(args.slug, "rejected", project)
            if cand:
                print(f"Rejected candidate '{args.slug}'. Status updated to rejected.")
            else:
                print(f"Error: Candidate '{args.slug}' not found.")
                sys.exit(1)
        except Exception as e:
            print(f"Error during rejection: {e}")
            sys.exit(1)

    elif action == "defer":
        try:
            cand = eng.skills.update_skill_candidate_status(args.slug, "deferred", project)
            if cand:
                print(f"Deferred candidate '{args.slug}'. Status updated to deferred.")
            else:
                print(f"Error: Candidate '{args.slug}' not found.")
                sys.exit(1)
        except Exception as e:
            print(f"Error during deferral: {e}")
            sys.exit(1)

    elif action == "create":
        if not hasattr(args, 'name') or not args.name:
            print("Error: skill name required")
        else:
            description = getattr(args, 'description', '') or ''
            concepts_str = getattr(args, 'concepts', '')
            source_concept_ids = [c.strip() for c in concepts_str.split(",") if c.strip()] if concepts_str else None
            candidate = eng.skills.create_skill_from_template(args.name, description, project, source_concept_ids=source_concept_ids)
            print(f"Skill candidate created: {candidate.slug}")
            print(f"  Candidate ID: {candidate.candidate_id}")
            print(f"  Status: {candidate.status}")
            print(f"  File: .oem/skill_candidates/{candidate.slug}.md")
            print(f"  Review: oem skills show {candidate.slug}")
            print(f"  Edit:   oem skills edit {candidate.slug}")
            print(f"  Approve: oem skills approve {candidate.slug}")

    elif action == "preview":
        if not hasattr(args, 'slug') or not args.slug:
            print("Error: skill slug required")
        else:
            candidate = eng.skills.load_skill_candidate(args.slug, project)
            if not candidate:
                print(f"Skill candidate '{args.slug}' not found")
            else:
                lines = [
                    f"Title: {candidate.title}",
                    f"Slug: {candidate.slug}",
                    f"Status: {candidate.status}",
                    f"Confidence: {candidate.confidence}",
                    f"",
                    f"Trigger: {candidate.trigger}",
                    f"Behavior: {candidate.recommended_behavior}",
                    f"",
                    f"When this skill is approved and active, the agent will:",
                    f"  1. See the title '{candidate.title}' in preflight context",
                    f"  2. Match on trigger '{candidate.trigger}' during task analysis",
                    f"  3. Receive the recommended behavior as injected instructions",
                ]
                for line in lines:
                    print(line)

    elif action == "edit":
        # Edit candidate in-place
        candidate = eng.skills.load_skill_candidate(args.slug, project)
        if not candidate:
            print(f"Error: Candidate '{args.slug}' not found.")
            sys.exit(1)

        modified = False
        if args.title is not None:
            candidate.title = args.title
            modified = True
        if args.trigger is not None:
            candidate.trigger = args.trigger
            modified = True
        if args.behavior is not None:
            candidate.recommended_behavior = args.behavior
            modified = True

        if not modified:
            print("No fields specified to edit. Use --title, --trigger, or --behavior.")
            return

        # Re-save candidate using create_skill_candidate (which acts as update/upsert)
        eng.skills.create_skill_candidate(
            candidate_id=candidate.candidate_id,
            slug=candidate.slug,
            title=candidate.title,
            trigger=candidate.trigger,
            recommended_behavior=candidate.recommended_behavior,
            evidence=candidate.evidence,
            rationale=candidate.rationale,
            confidence=candidate.confidence,
            status=candidate.status,
            source_event_ids=candidate.source_event_ids,
            source_concept_ids=candidate.source_concept_ids,
            created_at=candidate.created_at,
            project=project,
        )
        print(f"Candidate '{args.slug}' updated successfully.")
