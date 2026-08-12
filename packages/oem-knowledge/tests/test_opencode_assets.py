from pathlib import Path

import pytest

from oem_knowledge.opencode_assets import (
    is_managed_asset,
    sha256_hex,
    validate_opencode_dir,
    write_asset_manifest,
    write_managed_asset,
)


def test_write_managed_asset_installs_when_missing(tmp_path):
    dest = tmp_path / "remember" / "SKILL.md"
    result = write_managed_asset(
        dest,
        "content",
        manifest_key="skills/remember/SKILL.md",
        repair=False,
        force_assets=False,
        manifest={},
    )
    assert result["status"] == "installed"
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "content"


def test_write_managed_asset_preserves_user_file(tmp_path):
    dest = tmp_path / "remember" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("user content", encoding="utf-8")
    result = write_managed_asset(
        dest,
        "new content",
        manifest_key="skills/remember/SKILL.md",
        repair=False,
        force_assets=False,
        manifest={},
    )
    assert result["status"] == "preserved"
    assert dest.read_text(encoding="utf-8") == "user content"


def test_write_managed_asset_force_replaces_user_file_with_backup(tmp_path):
    dest = tmp_path / "remember" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("user content", encoding="utf-8")
    result = write_managed_asset(
        dest,
        "new content",
        manifest_key="skills/remember/SKILL.md",
        repair=False,
        force_assets=True,
        manifest={},
    )
    assert result["status"] == "force_replaced"
    assert dest.read_text(encoding="utf-8") == "new content"
    backup = dest.with_name(dest.name + ".oem.bak")
    assert backup.exists()


def test_write_managed_asset_force_replaces_symlink_without_backup(tmp_path):
    target = tmp_path / "real_target.md"
    target.write_text("target content", encoding="utf-8")
    dest = tmp_path / "remember" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(target)
    result = write_managed_asset(
        dest,
        "new content",
        manifest_key="skills/remember/SKILL.md",
        repair=False,
        force_assets=True,
        manifest={},
    )
    assert result["status"] == "force_replaced"
    assert dest.is_file()
    assert not dest.is_symlink()
    assert dest.read_text(encoding="utf-8") == "new content"
    backup = dest.with_name(dest.name + ".oem.bak")
    assert not backup.exists()


def test_write_managed_asset_upgrades_verified_managed_file(tmp_path):
    from oem_knowledge.opencode_assets import write_managed_asset, sha256_hex
    dest = tmp_path / "SKILL.md"
    old_content = "old generated_by: openempiric content"
    dest.write_text(old_content, encoding="utf-8")
    manifest = {"schema_version": 1, "assets": {"k": {"sha256": sha256_hex(old_content)}}}
    result = write_managed_asset(dest, "new content", manifest_key="k", repair=False, force_assets=False, manifest=manifest)
    assert result["status"] == "upgraded"
    assert dest.read_text(encoding="utf-8") == "new content"


def test_write_managed_asset_repair_upgrades_recorded_but_unmarked(tmp_path):
    from oem_knowledge.opencode_assets import write_managed_asset, sha256_hex
    dest = tmp_path / "SKILL.md"
    old_content = "no marker but old"
    dest.write_text(old_content, encoding="utf-8")
    manifest = {"schema_version": 1, "assets": {"skills/remember/SKILL.md": {"sha256": sha256_hex(old_content)}}}
    result = write_managed_asset(dest, "new content", manifest_key="skills/remember/SKILL.md", repair=True, force_assets=False, manifest=manifest)
    assert result["status"] == "upgraded"


def test_write_managed_asset_skips_identical(tmp_path):
    from oem_knowledge.opencode_assets import write_managed_asset
    dest = tmp_path / "SKILL.md"
    dest.write_text("same content", encoding="utf-8")
    result = write_managed_asset(dest, "same content", manifest_key="k", repair=False, force_assets=False, manifest={})
    assert result["status"] == "skipped"


def test_validate_opencode_dir_rejects_traversal(tmp_path):
    path = Path(str(tmp_path)).joinpath("..", "opencode")
    with pytest.raises(ValueError):
        validate_opencode_dir(path)


def test_validate_opencode_dir_rejects_file_target(tmp_path):
    target = tmp_path / "f"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        validate_opencode_dir(target)


def test_validate_opencode_dir_accepts_missing_dir(tmp_path):
    path = tmp_path / "opencode"
    validate_opencode_dir(path)


def test_write_asset_manifest_roundtrip(tmp_path):
    write_asset_manifest(
        tmp_path,
        [{"dest_rel": "agent/dream.md", "content": "x", "version": "1.0.0"}],
    )
    manifest_path = tmp_path / "openempiric-manifest.json"
    assert manifest_path.exists()
    import json

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["assets"]["agent/dream.md"]["sha256"] == sha256_hex("x")


def test_is_managed_asset():
    assert is_managed_asset("generated_by: openempiric")
    assert is_managed_asset("source_type: oem_opencode_skill")
    assert not is_managed_asset("plain user text")


def test_write_managed_asset_preserves_symlink_without_force(tmp_path):
    from oem_knowledge.opencode_assets import write_managed_asset
    target = tmp_path / "real_target.md"
    target.write_text("target", encoding="utf-8")
    dest = tmp_path / "SKILL.md"
    dest.symlink_to(target)
    result = write_managed_asset(dest, "new content", manifest_key="k", repair=False, force_assets=False, manifest={})
    assert result["status"] == "preserved"
    assert dest.is_symlink()
    assert target.read_text(encoding="utf-8") == "target"


def test_write_managed_asset_preserves_marker_file_without_manifest(tmp_path):
    from oem_knowledge.opencode_assets import write_managed_asset
    dest = tmp_path / "SKILL.md"
    dest.write_text("<!-- generated_by: openempiric --> spoofed user content", encoding="utf-8")
    result = write_managed_asset(dest, "package content", manifest_key="k", repair=True, force_assets=False, manifest={})
    assert result["status"] == "preserved"
    assert "spoofed user content" in dest.read_text(encoding="utf-8")


def test_write_managed_asset_preserves_on_manifest_sha_mismatch(tmp_path):
    from oem_knowledge.opencode_assets import write_managed_asset, sha256_hex
    dest = tmp_path / "SKILL.md"
    dest.write_text("user content with marker", encoding="utf-8")
    manifest = {"schema_version": 1, "assets": {"k": {"sha256": sha256_hex("something else entirely")}}}
    result = write_managed_asset(dest, "package content", manifest_key="k", repair=True, force_assets=False, manifest=manifest)
    assert result["status"] == "preserved"


def test_validate_opencode_dir_rejects_symlink_dir(tmp_path):
    from oem_knowledge.opencode_assets import validate_opencode_dir
    import pytest
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "opencode-link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        validate_opencode_dir(link)
