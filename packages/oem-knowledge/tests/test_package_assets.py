from oem_knowledge.opencode_assets import ASSET_DEFINITIONS, load_package_asset


def test_remember_skill_packaged_and_marked():
    content = load_package_asset("skills/remember/SKILL.md")
    assert "source_type: oem_opencode_skill" in content
    assert "generated_by: openempiric" in content
    assert "knowledge_preflight" in content
    assert "dream_end" in content
    assert "github.com/" not in content
    assert "/home/" not in content


def test_dream_agent_packaged_and_marked():
    content = load_package_asset("agent/dream.md")
    assert "source_type: oem_opencode_agent" in content
    assert "hidden: true" in content
    assert "dream_start" in content
    assert "dream_end" in content
    assert "MCP" in content
    assert "/home/" not in content


def test_asset_definitions_cover_both():
    assert len(ASSET_DEFINITIONS) == 2
    dest_rels = {entry["dest_rel"] for entry in ASSET_DEFINITIONS}
    assert dest_rels == {"skills/remember/SKILL.md", "agent/dream.md"}
    for entry in ASSET_DEFINITIONS:
        assert entry["version"]
        assert entry["package_path"]
