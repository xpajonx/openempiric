from __future__ import annotations
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def get_manifest_path(project_path: str | Path) -> Path:
    return Path(project_path).resolve() / ".oem" / "manifest.json"

def load_manifest(project_path: str | Path) -> dict | None:
    p = get_manifest_path(project_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse manifest at %s: %s", p, e)
        return None

def save_manifest(project_path: str | Path, manifest: dict) -> None:
    p = get_manifest_path(project_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save manifest to %s: %s", p, e)

def ensure_manifest(project_path: str | Path) -> dict:
    project_dir = Path(project_path).resolve()
    manifest = load_manifest(project_dir)
    if manifest is None:
        manifest = {
            "schema_version": 1,
            "project_id": project_dir.name,
            "memory_root": ".oem",
            "created_by": "openempiric",
            "agent_integrations": {}
        }
    
    # Ensure standard keys are present
    if "schema_version" not in manifest:
        manifest["schema_version"] = 1
    if "project_id" not in manifest:
        manifest["project_id"] = project_dir.name
    if "memory_root" not in manifest:
        manifest["memory_root"] = ".oem"
    if "created_by" not in manifest:
        manifest["created_by"] = "openempiric"
    if "agent_integrations" not in manifest:
        manifest["agent_integrations"] = {}
        
    save_manifest(project_dir, manifest)
    return manifest

def update_manifest_integration(project_path: str | Path, agent_name: str, enabled: bool = True) -> dict:
    project_dir = Path(project_path).resolve()
    manifest = ensure_manifest(project_dir)
    manifest["agent_integrations"][agent_name] = {
        "enabled": enabled,
        "mcp": True,
        "instructions": True,
        "session_hooks": True
    }
    save_manifest(project_dir, manifest)
    return manifest
