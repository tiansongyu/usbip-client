#!/usr/bin/env python3
"""Export Cargo's locked dependency/license metadata without executing dependencies."""
import json
import pathlib
import subprocess

root = pathlib.Path(__file__).resolve().parent.parent
metadata = json.loads(subprocess.check_output(
    ["cargo", "metadata", "--format-version", "1", "--all-features", "--locked"], cwd=root
))
packages = sorted(({
    "name": p["name"], "version": p["version"], "license": p["license"],
    "license_file": p["license_file"], "source": p["source"],
    "repository": p["repository"], "rust_version": p["rust_version"],
} for p in metadata["packages"]), key=lambda p: (p["name"], p["version"]))
target = root / "docs" / "dependency-inventory.json"
target.write_text(json.dumps(packages, indent=2, ensure_ascii=False) + "\n")
missing = [p["name"] for p in packages if not p["license"] and not p["license_file"]]
print(f"Exported {len(packages)} packages to {target.relative_to(root)}")
if missing:
    raise SystemExit(f"License metadata missing: {', '.join(missing)}")
