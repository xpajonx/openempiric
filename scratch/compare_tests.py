import os
from pathlib import Path

root_tests = Path("tests")
pkg_tests = Path("packages/oem-knowledge/tests")

root_files = {f.name for f in root_tests.glob("*.py")}
pkg_files = {f.name for f in pkg_tests.glob("*.py")}

print("Only in root tests/ (candidates for migration):")
for f in sorted(root_files - pkg_files):
    print(f"  - {f} (size: {os.path.getsize(root_tests / f)})")

print("\nOnly in packages/oem-knowledge/tests/:")
for f in sorted(pkg_files - root_files):
    print(f"  - {f}")

print("\nOverlapping names (different sizes/content):")
for f in sorted(root_files & pkg_files):
    r_size = os.path.getsize(root_tests / f)
    p_size = os.path.getsize(pkg_tests / f)
    if r_size != p_size:
        print(f"  - {f} (root: {r_size} vs pkg: {p_size})")
    else:
        print(f"  - {f} (identical size: {r_size})")
