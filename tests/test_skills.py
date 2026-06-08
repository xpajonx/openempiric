from __future__ import annotations

import sys
import tempfile
import shutil
import yaml
from pathlib import Path
from unittest.mock import patch

from oem_knowledge.engine import KnowledgeEngine, OEM_DIR
from oem_knowledge.cli import main
from oem_knowledge.adapters import get_adapter
from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.adapters.opencode.adapter import OpenCodeAdapter
from oem_knowledge.adapters.antigravity.adapter import AntigravityAdapter


def test_adapter_resolution(tmp_path):
    eng = KnowledgeEngine(tmp_path)
    
    adapter = get_adapter("opencode", eng, str(tmp_path))
    assert isinstance(adapter, OpenCodeAdapter)
    
    adapter_agy = get_adapter("antigravity", eng, str(tmp_path))
    assert isinstance(adapter_agy, AntigravityAdapter)
    
    adapter_base = get_adapter("other_agent", eng, str(tmp_path))
    assert isinstance(adapter_base, BaseAdapter)


def test_base_adapter_defaults(tmp_path):
    eng = KnowledgeEngine(tmp_path)
    adapter = BaseAdapter(eng, str(tmp_path))
    assert adapter.install_skill() is False


def test_opencode_skill_installation(tmp_path):
    eng = KnowledgeEngine(tmp_path)
    eng.init_project(str(tmp_path))
    
    adapter = OpenCodeAdapter(eng, str(tmp_path))
    
    # Remove it first to verify install
    skills_file = tmp_path / OEM_DIR / "skills" / "openempiric.yaml"
    if skills_file.exists():
        skills_file.unlink()
        
    assert adapter.install_skill() is True
    assert skills_file.exists()
    
    # Parse yaml and check content
    with open(skills_file, "r") as f:
        data = yaml.safe_load(f)
        
    assert data["name"] == "openempiric"
    assert data["version"] == "0.97"
    assert data["schema_version"] == 1
    assert "opencode" in data["adapters"]
    assert "Agent knowledge runtime" in data["description"]
    assert "knowledge_search" in data["required"]
    assert "knowledge_session_start" not in data["required"]
    assert "knowledge_capture_after_work" in data["required"]
    assert "knowledge_search" in data["tools"]
    assert any("decisions" in bp for bp in data["best_practices"])
    assert any("failures" in bp for bp in data["best_practices"])



def test_init_installs_skill(tmp_path):
    with patch.object(sys, "argv", ["oem", "init", str(tmp_path)]):
        main()
        
    skills_file = tmp_path / ".oem" / "skills" / "openempiric.yaml"
    assert skills_file.exists()
    
    with open(skills_file, "r") as f:
        data = yaml.safe_load(f)
    assert data["name"] == "openempiric"


def test_doctor_skill_check(tmp_path):
    # Initialize project (should install skills)
    with patch.object(sys, "argv", ["oem", "init", str(tmp_path)]):
        main()
        
    # Doctor check with skill installed should succeed
    with patch.object(sys, "argv", ["oem", "doctor", "--project", str(tmp_path)]):
        with patch("sys.stdout") as mock_stdout:
            try:
                main()
            except SystemExit:
                pass
                
    skills_file = tmp_path / ".oem" / "skills" / "openempiric.yaml"
    assert skills_file.exists()
    
    # Delete skill file and verify doctor flags it
    skills_file.unlink()
    
    # Run doctor again
    with patch.object(sys, "argv", ["oem", "doctor", "--project", str(tmp_path)]):
        # Expect system exit with non-zero or doctor printing the fail line
        with patch("builtins.print") as mock_print:
            try:
                main()
            except SystemExit:
                pass
            
            # Find if any call contains '✗ OEM Skill not installed'
            flagged = False
            for call in mock_print.call_args_list:
                args = call[0]
                if args and any("✗ OEM Skill not installed" in str(arg) for arg in args):
                    flagged = True
                    break
            assert flagged, "Doctor did not flag missing skills file"
