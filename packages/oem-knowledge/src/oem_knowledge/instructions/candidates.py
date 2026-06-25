from __future__ import annotations
import hashlib
from pathlib import Path
from oem_knowledge.project_layout import ProjectLayout

def create_instruction_update_candidate(
    layout: ProjectLayout,
    session_id: str,
    target_path: str,
    proposed_patch: str,
    reason: str
) -> dict:
    # 1. Compute stable ID
    hash_input = f"{target_path}:{proposed_patch}"
    cand_id = "candidate_" + hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:12]
    
    # 2. Render markdown content
    content = f"""---
type: instruction_update_candidate
id: {cand_id}
target_path: {target_path}
status: proposed
created_by: openempiric
---

# Proposed Instruction Update

## Reason

{reason}

## Proposed addition

{proposed_patch}
"""
    # 3. Write candidate file
    cand_dir = layout.instruction_candidates_dir
    cand_dir.mkdir(parents=True, exist_ok=True)
    
    cand_file = cand_dir / f"{cand_id}.md"
    cand_file.write_text(content, encoding="utf-8")
    
    return {
        "id": cand_id,
        "session_id": session_id,
        "target_path": target_path,
        "proposed_patch": proposed_patch,
        "reason": reason,
        "status": "proposed",
        "path": str(cand_file)
    }
