from pathlib import Path
import re

class ConceptIdCollisionError(RuntimeError):
    """Raised when allocating a concept ID would result in overwriting an existing wiki file."""
    pass

def allocate_concept_id(
    registry: dict,
    wiki_dir: Path | None = None,
    reserved_ids: set[str] | None = None,
) -> str:
    """Allocates a safe, new concept ID in the format concept_###.
    
    Ensures that the new ID is strictly greater than the maximum existing numeric concept ID,
    and is not already present in the registry, wiki directory, or in-flight reserved IDs.
    """
    existing_ids = set()
    
    if registry:
        existing_ids.update(registry.keys())
        
    if wiki_dir:
        wiki_path = Path(wiki_dir)
        if wiki_path.is_dir():
            for f in wiki_path.glob("concept_*.md"):
                existing_ids.add(f.stem)
            
    if reserved_ids:
        existing_ids.update(reserved_ids)
        
    numeric_values = set()
    pattern = re.compile(r"^concept_(\d+)$")
    
    for cid in existing_ids:
        match = pattern.match(cid)
        if match:
            numeric_values.add(int(match.group(1)))
            
    next_num = max(numeric_values) + 1 if numeric_values else 1
    
    while True:
        candidate_id = f"concept_{next_num:03d}"
        if candidate_id not in existing_ids:
            return candidate_id
        next_num += 1
