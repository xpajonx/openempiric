import pytest
import shutil
import yaml
from pathlib import Path
from oem_knowledge.services.source_corpus import SourceCorpusService, SourceIndexConfig

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    yield project_dir
    if project_dir.exists():
        shutil.rmtree(project_dir)

def test_source_config_uses_defaults_when_missing_without_writing_file(temp_project):
    service = SourceCorpusService(temp_project)
    config = service.load_config()
    
    assert isinstance(config, SourceIndexConfig)
    assert config.max_file_size_bytes == 524288
    assert config.max_read_lines == 400
    assert config.max_read_characters == 50000
    
    # Ensure config file was NOT created
    assert not (temp_project / ".oem" / "source_index_config.yml").exists()

def test_source_config_loads_yaml_config(temp_project):
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    config_file = oem_dir / "source_index_config.yml"
    
    config_data = {
        "include": ["src/**", "tests/**"],
        "exclude": ["node_modules/**", "dist/**"],
        "max_file_size_bytes": 100000,
        "max_read_lines": 200,
        "max_read_characters": 10000,
    }
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    
    service = SourceCorpusService(temp_project)
    config = service.load_config()
    
    assert config.include == ["src/**", "tests/**"]
    assert config.exclude == ["node_modules/**", "dist/**"]
    assert config.max_file_size_bytes == 100000
    assert config.max_read_lines == 200
    assert config.max_read_characters == 10000

def test_source_discovery_respects_gitignore(temp_project):
    service = SourceCorpusService(temp_project)
    
    # Create gitignore excluding target.py
    (temp_project / ".gitignore").write_text("target.py\n", encoding="utf-8")
    
    # Create target.py and clean.py in src
    src_dir = temp_project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "target.py").write_text("print('ignored')", encoding="utf-8")
    (src_dir / "clean.py").write_text("print('kept')", encoding="utf-8")
    
    res = service.discover_files()
    assert res.excluded_reasons["gitignored"] >= 1
    
    clean_cls = next((c for c in res.discovered_files if c.rel_path == "src/clean.py"), None)
    target_cls = next((c for c in res.discovered_files if c.rel_path == "src/target.py"), None)
    
    assert clean_cls is not None
    assert clean_cls.eligible is True
    assert target_cls is not None
    assert target_cls.eligible is False
    assert target_cls.reason == "gitignored"

def test_source_discovery_respects_oemignore(temp_project):
    service = SourceCorpusService(temp_project)
    
    # Create oemignore excluding docs
    (temp_project / ".oemignore").write_text("docs/**\n", encoding="utf-8")
    
    docs_dir = temp_project / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "ignored.md").write_text("no index", encoding="utf-8")
    
    res = service.discover_files()
    assert res.excluded_reasons["gitignored"] >= 1
    
    ignored_cls = next((c for c in res.discovered_files if c.rel_path == "docs/ignored.md"), None)
    assert ignored_cls is not None
    assert ignored_cls.eligible is False
    assert ignored_cls.reason == "gitignored"

def test_source_discovery_excludes_oem_directory(temp_project):
    service = SourceCorpusService(temp_project)
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "some_memory.md").write_text("memory", encoding="utf-8")
    
    res = service.discover_files()
    # .oem directory is pruned from walking and files are excluded
    oem_cls = [c for c in res.discovered_files if ".oem/" in c.rel_path or c.rel_path.startswith(".oem")]
    assert len(oem_cls) == 0

def test_source_discovery_excludes_git_directory(temp_project):
    service = SourceCorpusService(temp_project)
    git_dir = temp_project / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "config").write_text("config", encoding="utf-8")
    
    res = service.discover_files()
    git_cls = [c for c in res.discovered_files if ".git/" in c.rel_path or c.rel_path.startswith(".git")]
    assert len(git_cls) == 0

def test_source_discovery_excludes_env_files(temp_project):
    service = SourceCorpusService(temp_project)
    (temp_project / ".env").write_text("KEY=123", encoding="utf-8")
    (temp_project / ".env.local").write_text("KEY=456", encoding="utf-8")
    
    res = service.discover_files()
    env_cls = next((c for c in res.discovered_files if c.rel_path == ".env"), None)
    env_local_cls = next((c for c in res.discovered_files if c.rel_path == ".env.local"), None)
    
    assert env_cls is not None
    assert env_cls.eligible is False
    assert env_cls.reason == "secret_env_file"
    
    assert env_local_cls is not None
    assert env_local_cls.eligible is False
    assert env_local_cls.reason == "secret_env_file"

def test_source_discovery_excludes_binary_files(temp_project):
    service = SourceCorpusService(temp_project)
    src_dir = temp_project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    
    # Write a file containing null byte (binary indicator)
    binary_file = src_dir / "image.png"
    binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    
    res = service.discover_files()
    bin_cls = next((c for c in res.discovered_files if c.rel_path == "src/image.png"), None)
    
    assert bin_cls is not None
    assert bin_cls.eligible is False
    # Either binary file suffix rule or text check catches it
    assert bin_cls.reason in ("unsupported_binary_or_media", "binary")

def test_source_discovery_excludes_large_files(temp_project):
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    config_file = oem_dir / "source_index_config.yml"
    config_file.write_text("max_file_size_bytes: 10\n", encoding="utf-8")
    
    service = SourceCorpusService(temp_project)
    src_dir = temp_project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    
    large_file = src_dir / "large.py"
    large_file.write_text("print('this file is larger than 10 bytes')", encoding="utf-8")
    
    res = service.discover_files()
    large_cls = next((c for c in res.discovered_files if c.rel_path == "src/large.py"), None)
    
    assert large_cls is not None
    assert large_cls.eligible is False
    assert large_cls.reason == "large_file"
    assert large_cls.metadata_only is True

def test_source_discovery_rejects_symlink_outside_project(temp_project, tmp_path):
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("print('outside')", encoding="utf-8")
    
    service = SourceCorpusService(temp_project)
    
    # Create symlink pointing outside project root
    symlink_path = temp_project / "outside_link.py"
    try:
        symlink_path.symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks are not supported on this platform/permissions")
        
    res = service.discover_files()
    link_cls = next((c for c in res.discovered_files if c.rel_path == "outside_link.py"), None)
    
    assert link_cls is not None
    assert link_cls.eligible is False
    assert link_cls.reason == "outside_project_symlink"

def test_source_discovery_records_lockfiles_metadata_only(temp_project):
    service = SourceCorpusService(temp_project)
    src_dir = temp_project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "uv.lock").write_text("uv lock content", encoding="utf-8")
    
    res = service.discover_files()
    lock_cls = next((c for c in res.discovered_files if c.rel_path == "src/uv.lock"), None)
    
    assert lock_cls is not None
    assert lock_cls.eligible is False
    assert lock_cls.reason == "lockfile_metadata_only"
    assert lock_cls.metadata_only is True

def test_source_discovery_dry_run_creates_no_files(temp_project):
    service = SourceCorpusService(temp_project)
    
    # Ensure no config exists initially
    config_path = temp_project / ".oem" / "source_index_config.yml"
    assert not config_path.exists()
    
    # Run discovery (which is discovery-only and dry-run safe)
    res = service.discover_files()
    
    # Ensure no config/index/manifest files are created
    assert not (temp_project / ".oem").exists()
    assert not (temp_project / ".oem" / "indexes").exists()
    assert not (temp_project / ".oem" / "source_manifest.json").exists()

def test_source_discovery_does_not_create_concepts(temp_project):
    service = SourceCorpusService(temp_project)
    res = service.discover_files()
    
    assert not (temp_project / ".oem" / "wiki").exists()

def test_source_discovery_does_not_write_runtime_events(temp_project):
    service = SourceCorpusService(temp_project)
    res = service.discover_files()
    
    assert not (temp_project / ".oem" / "events.jsonl").exists()
    assert not (temp_project / ".oem" / "runtime_events.jsonl").exists()

def test_source_discovery_does_not_materialize_wiki(temp_project):
    service = SourceCorpusService(temp_project)
    res = service.discover_files()
    
    assert not (temp_project / ".oem" / "wiki").exists()

def test_source_storage_is_separate_from_memory_storage(temp_project):
    service = SourceCorpusService(temp_project)
    
    # Check layout paths explicitly
    layout = service._layout()
    
    assert layout.source_index_db_path == temp_project / ".oem" / "indexes" / "source_index.sqlite"
    assert layout.registry_path == temp_project / ".oem" / "concept_registry.json"
    assert layout.source_index_db_path != layout.registry_path

def test_source_discovery_mandatory_exclusions_override_config_includes(temp_project):
    # Force inclusion of .oem/wiki and .env in configuration
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    config_file = oem_dir / "source_index_config.yml"
    config_data = {
        "include": [".oem/wiki/**", ".env", "src/**"]
    }
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    
    service = SourceCorpusService(temp_project)
    
    # Create files under forced includes
    (temp_project / ".env").write_text("KEY=val", encoding="utf-8")
    (temp_project / ".oem" / "wiki").mkdir(parents=True, exist_ok=True)
    (temp_project / ".oem" / "wiki" / "concept_001.md").write_text("some concept", encoding="utf-8")
    
    res = service.discover_files()
    
    # Both .env and .oem/wiki should STILL be excluded
    env_cls = next((c for c in res.discovered_files if c.rel_path == ".env"), None)
    assert env_cls is not None
    assert env_cls.eligible is False
    assert env_cls.reason == "secret_env_file"
    
    oem_cls = [c for c in res.discovered_files if ".oem" in c.rel_path]
    # Pruning keeps .oem/ from being walked
    assert len(oem_cls) == 0

def test_source_discovery_rejects_path_traversal_outside_project(temp_project):
    service = SourceCorpusService(temp_project)
    
    # Construct traversal path
    traversal_path = temp_project / ".." / "outside.py"
    
    classification = service.classify_file(traversal_path)
    assert classification.eligible is False
    assert classification.reason == "outside_project_symlink"

def test_source_discovery_rejects_absolute_path_outside_project(temp_project):
    service = SourceCorpusService(temp_project)
    
    # Absolute path outside project
    abs_path = Path("/tmp/some_other_project_outside/outside.py")
    
    classification = service.classify_file(abs_path)
    assert classification.eligible is False
    assert classification.reason == "outside_project_symlink"

def test_source_config_old_defaults_upgrade_triggers_with_custom_max_read_lines(temp_project):
    """Old include patterns should be upgraded to new defaults when max_read_lines differs from sentinel."""
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "source_index_config.yml").write_text(yaml.safe_dump({
        "include": ["src/**", "tests/**"],
        "max_read_lines": 300,  # Different from sentinel 200, triggers upgrade
    }), encoding="utf-8")

    service = SourceCorpusService(temp_project)
    config = service.load_config()

    # Should include the new defaults (execution/**, agent/**)
    assert "execution/**" in config.include
    assert "agent/**" in config.include

def test_source_config_old_defaults_upgrade_skipped_with_sentinel_max_read_lines(temp_project):
    """Old include patterns should NOT be upgraded when max_read_lines matches sentinel 200."""
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "source_index_config.yml").write_text(yaml.safe_dump({
        "include": ["src/**", "tests/**"],
        "max_read_lines": 200,  # Sentinel value, skips upgrade
    }), encoding="utf-8")

    service = SourceCorpusService(temp_project)
    config = service.load_config()

    # Should NOT be upgraded - kept original include
    assert "execution/**" not in config.include
    assert "agent/**" not in config.include
    assert config.include == ["src/**", "tests/**"]

def test_source_config_old_defaults_upgrade_triggers_with_missing_max_read_lines(temp_project):
    """Old include patterns should be upgraded when max_read_lines key is absent (default used)."""
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "source_index_config.yml").write_text(yaml.safe_dump({
        "include": ["src/**", "tests/**"],
    }), encoding="utf-8")

    service = SourceCorpusService(temp_project)
    config = service.load_config()

    # Default max_read_lines is 400, so upgrade should trigger
    assert "execution/**" in config.include
    assert "agent/**" in config.include

def test_source_config_exclude_globs_is_loaded_and_applied(temp_project):
    """exclude_globs should be loaded from config and applied during file classification."""
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "source_index_config.yml").write_text(yaml.safe_dump({
        "include": ["src/**"],
        "exclude_globs": ["src/generated/**"],  # Should exclude generated files via ignore matcher
    }), encoding="utf-8")

    src_dir = temp_project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "real.py").write_text("print('real')")
    gen_dir = src_dir / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "file.py").write_text("print('generated')")

    service = SourceCorpusService(temp_project)
    config = service.load_config()

    # exclude_globs must be available in the config
    assert config.exclude_globs == ["src/generated/**"]

    real_cls = service.classify_file(src_dir / "real.py")
    gen_cls = service.classify_file(gen_dir / "file.py")

    assert real_cls.eligible is True, f"real.py should be eligible: {real_cls.reason}"
    assert gen_cls.eligible is False, f"generated/file.py should be excluded via exclude_globs: {gen_cls.reason}"

def test_source_config_exclude_prevents_lockfile_metadata_only_indexing(temp_project):
    """Explicit exclude should block lockfiles from metadata-only indexing."""
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "source_index_config.yml").write_text(yaml.safe_dump({
        "include": ["**/*"],
        "exclude": ["**/package-lock.json"],
    }), encoding="utf-8")

    (temp_project / "package-lock.json").write_text('{"lockfileVersion":3}')
    (temp_project / "main.py").write_text("print('hello')")

    service = SourceCorpusService(temp_project)

    lock_cls = service.classify_file(temp_project / "package-lock.json")
    main_cls = service.classify_file(temp_project / "main.py")

    assert lock_cls.eligible is False
    assert lock_cls.reason == "excluded"
    assert main_cls.eligible is True

def test_source_config_exclude_and_exclude_globs_both_applied(temp_project):
    """Both exclude and exclude_globs should be applied during classification, each in its own slot."""
    oem_dir = temp_project / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "source_index_config.yml").write_text(yaml.safe_dump({
        "include": ["src/**"],
        "exclude": ["src/tmp/**"],  # Explicit exclude section
        "exclude_globs": ["src/generated/**"],  # Gitignore-style ignore matcher
    }), encoding="utf-8")

    src_dir = temp_project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "real.py").write_text("print('real')")

    # File that should be excluded by include/exclude matching
    tmp_dir = src_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "file.py").write_text("print('tmp')")

    # File that should be excluded by exclude_globs (ignore matcher)
    gen_dir = src_dir / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "file.py").write_text("print('generated')")

    service = SourceCorpusService(temp_project)

    real_cls = service.classify_file(src_dir / "real.py")
    tmp_cls = service.classify_file(tmp_dir / "file.py")
    gen_cls = service.classify_file(gen_dir / "file.py")

    assert real_cls.eligible is True
    assert real_cls.reason == "eligible"
    assert tmp_cls.eligible is False
    assert tmp_cls.reason == "excluded"
    assert gen_cls.eligible is False
    assert gen_cls.reason == "gitignored"
