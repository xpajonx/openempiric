from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import importlib.resources as pkg_resources

ASSET_VERSION = "1.0.0"
ASSET_MANIFEST_NAME = "openempiric-manifest.json"

ASSET_DEFINITIONS = [
    {
        "package_path": "skills/remember/SKILL.md",
        "dest_rel": "skills/remember/SKILL.md",
        "marker": "source_type: oem_opencode_skill",
        "version": ASSET_VERSION,
    },
    {
        "package_path": "agent/dream.md",
        "dest_rel": "agent/dream.md",
        "marker": "source_type: oem_opencode_agent",
        "version": ASSET_VERSION,
    },
]


def load_package_asset(package_path: str) -> str:
    """Read a package asset, handling importlib Traversable mocks under tests."""
    source = pkg_resources.files("oem_knowledge").joinpath(package_path)
    is_mock = "mock" in type(source).__name__.lower() or hasattr(source, "mock_calls")
    if is_mock:
        return source.read_text(encoding="utf-8")
    return Path(str(source)).read_text(encoding="utf-8")


def is_managed_asset(content: str) -> bool:
    """True when content carries an OEM managed-asset marker."""
    return "generated_by: openempiric" in content or "source_type: oem_opencode_" in content


def sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _atomic_write_text(dest: Path, content: str) -> None:
    """Write via temp file + os.replace so a crash never leaves a truncated asset."""
    tmp_path = dest.with_name(dest.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, dest)


def validate_opencode_dir(opencode_dir: Path) -> None:
    """Reject traversal, symlink config roots, and symlinked ancestors.

    Raises ValueError on unsafe paths.
    """
    raw = Path(opencode_dir).expanduser()
    if ".." in raw.parts:
        raise ValueError(f"opencode config path must not contain '..': {opencode_dir}")
    if raw.is_symlink():
        raise ValueError(f"opencode config path must not be a symlink: {opencode_dir}")
    resolved = raw.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"opencode config path is not a directory: {opencode_dir}")
    for ancestor in resolved.parents:
        if ancestor.is_symlink():
            raise ValueError(f"opencode config path traverses symlink: {ancestor}")
        if ancestor == ancestor.parent:
            break


def write_managed_asset(
    dest: Path,
    content: str,
    *,
    manifest_key: str,
    repair: bool,
    force_assets: bool,
    manifest: dict,
) -> dict:
    """Install one OEM-managed asset with ownership verification.

    Ownership rules:
    - A destination is OEM-managed only when the manifest records the asset
      AND the recorded sha256 equals the existing file's sha256.
    - Verified-managed files are upgraded on any setup run (normal or repair).
    - Marker-only files with no matching manifest record are preserved
      (migration/adoption path); only --force-assets replaces them.
    - Symlinks are preserved unless --force-assets (which replaces them
      without a backup).

    Returns {"status", "message"} with status in
    installed | skipped | upgraded | preserved | force_replaced | failed.
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"status": "failed", "message": f"cannot create {dest.parent}: {e}"}
    if dest.is_symlink():
        if not force_assets:
            return {"status": "preserved", "message": f"preserved symlink {dest.name}"}
        try:
            dest.unlink()
            _atomic_write_text(dest, content)
            return {"status": "force_replaced", "message": f"replaced symlink {dest.name}"}
        except OSError as e:
            return {"status": "failed", "message": f"force replace failed for {dest}: {e}"}
    if not dest.exists():
        try:
            _atomic_write_text(dest, content)
            return {"status": "installed", "message": f"installed {dest.name}"}
        except OSError as e:
            return {"status": "failed", "message": f"cannot write {dest}: {e}"}
    try:
        existing = dest.read_text(encoding="utf-8")
    except OSError as e:
        return {"status": "failed", "message": f"cannot read {dest}: {e}"}
    if existing == content:
        return {"status": "skipped", "message": f"{dest.name} already current"}
    existing_sha = sha256_hex(existing)
    assets = (manifest or {}).get("assets", {})
    record = assets.get(manifest_key) if isinstance(assets, dict) else None
    verified_managed = bool(record) and record.get("sha256") == existing_sha
    if verified_managed:
        try:
            _atomic_write_text(dest, content)
            return {"status": "upgraded", "message": f"updated {dest.name}"}
        except OSError as e:
            return {"status": "failed", "message": f"cannot write {dest}: {e}"}
    if force_assets:
        try:
            backup = dest.with_name(dest.name + ".oem.bak")
            import shutil
            shutil.copy2(dest, backup)
            _atomic_write_text(dest, content)
            return {"status": "force_replaced", "message": f"replaced user file {dest.name} (backup: {backup.name})"}
        except OSError as e:
            return {"status": "failed", "message": f"force replace failed for {dest}: {e}"}
    return {"status": "preserved", "message": f"preserved {dest.name} (not verified as OEM-managed; use --force-assets to replace)"}


def write_asset_manifest(opencode_dir: Path, installed_assets: list[dict]) -> None:
    """Record installed assets (dest_rel -> version + sha256) in the OEM manifest."""
    manifest_path = opencode_dir / ASSET_MANIFEST_NAME
    data = {"schema_version": 1, "assets": {}}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("assets"), dict):
                data = existing
        except Exception:
            pass
    for entry in installed_assets:
        data["assets"][entry["dest_rel"]] = {
            "version": entry.get("version", ASSET_VERSION),
            "sha256": sha256_hex(entry["content"]),
        }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
