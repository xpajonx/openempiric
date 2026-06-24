from __future__ import annotations

import os
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path

import pytest


def test_wheel_packaging_and_resource_resolution():
    """Verify that uv build succeeds, and the built wheel resolves package resources correctly in an isolated environment."""
    # 1. Resolve workspace root and package directory
    current_file = Path(__file__).resolve()
    workspace_root = current_file.parents[3]  # packages/oem-knowledge/tests/test_packaging_resource.py -> 4 levels up
    
    assert (workspace_root / "pyproject.toml").exists(), f"Could not find root pyproject.toml at {workspace_root}"
    
    # 2. Run uv build to generate the wheel
    dist_dir = workspace_root / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
        
    try:
        # Run uv build using the system uv
        subprocess.run(
            ["uv", "build", "--package", "oem-knowledge"],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        pytest.fail(f"uv build failed: {e.stderr}\nStdout: {e.stdout}")
        
    # Locate the built wheel file
    wheels = list(dist_dir.glob("oem_knowledge-*.whl"))
    assert len(wheels) >= 1, "No wheel was built under dist/"
    wheel_path = wheels[0].resolve()
    
    # 3. Create temporary virtual environment
    temp_dir = tempfile.mkdtemp(prefix="oem_packaging_test_")
    venv_dir = Path(temp_dir) / "venv"
    
    try:
        # Create virtual env
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True
        )
        
        # Resolve venv python and pip executables
        if sys.platform == "win32":
            venv_python = venv_dir / "Scripts" / "python.exe"
            venv_pip = venv_dir / "Scripts" / "pip.exe"
        else:
            venv_python = venv_dir / "bin" / "python"
            venv_pip = venv_dir / "bin" / "pip"
            
        # 4. Install the wheel into the virtual environment
        subprocess.run(
            [str(venv_pip), "install", str(wheel_path)],
            check=True,
            capture_output=True,
            text=True
        )
        
        # 5. Run a check inside the virtual environment to verify resource loading
        verify_script = (
            "import importlib.resources as pkg_resources\n"
            "try:\n"
            "    source = pkg_resources.files('oem_knowledge').joinpath('plugins/openempiric.ts')\n"
            "    exists = source.exists()\n"
            "    content = source.read_text(encoding='utf-8')\n"
            "    is_valid = 'export const OpenempiricPlugin' in content\n"
            "    print(f'exists={exists},is_valid={is_valid}')\n"
            "except Exception as e:\n"
            "    print(f'error={e}')\n"
        )
        
        res = subprocess.run(
            [str(venv_python), "-c", verify_script],
            capture_output=True,
            text=True,
            check=True
        )
        
        output = res.stdout.strip()
        assert "exists=True" in output, f"Resource openempiric.ts not found. Output: {output}"
        assert "is_valid=True" in output, f"Resource openempiric.ts invalid. Output: {output}"
        
    finally:
        # Clean up dist/ and temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
        if dist_dir.exists():
            shutil.rmtree(dist_dir, ignore_errors=True)
