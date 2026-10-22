#!/usr/bin/env python3
"""Read-only verification of the vendored nusb archive, hashes and exact diff.

No USB devices, modules, network, subprocesses, or repository writes are used.
Usage: python3 /tmp/rustusb-verify-nusb-provenance.py --repo REPOSITORY
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", type=Path, default=Path.cwd())
parser.add_argument("--archive", type=Path)
options = parser.parse_args()
root = options.repo / "vendor/nusb"
manifest = json.loads((root / "RUSTUSB-PROVENANCE.json").read_text())
archive = options.archive
if archive is None:
    candidates = list((Path.home() / ".cargo/registry/cache").glob("*/nusb-0.2.8.crate"))
    if len(candidates) != 1:
        raise SystemExit("Pass --archive to select one exact nusb-0.2.8.crate cache file")
    archive = candidates[0]
failures = []
original = {}
with tarfile.open(archive, "r:gz") as package:
    for item in package.getmembers():
        if item.isfile():
            name = item.name.removeprefix("nusb-0.2.8/")
            original[name] = package.extractfile(item).read()
sha = lambda data: hashlib.sha256(data).hexdigest()
if sha(archive.read_bytes()) != manifest["archive_sha256"]:
    failures.append("archive checksum")
if set(original) != set(manifest["files"]):
    failures.append("original inventory coverage")
patch = ""
modified = []
unsafe_counts = [0, 0]
for name, data in sorted(original.items()):
    current = (root / name).read_bytes()
    record = manifest["files"].get(name, {})
    if sha(data) != record.get("original_sha256"):
        failures.append(name + ": original checksum")
    if sha(current) != record.get("vendored_sha256"):
        failures.append(name + ": current checksum")
    if (data != current) != record.get("modified"):
        failures.append(name + ": modified marker")
    if name.endswith(".rs"):
        unsafe_counts[0] += len(re.findall(rb"\bunsafe\b", data))
        unsafe_counts[1] += len(re.findall(rb"\bunsafe\b", current))
    if data != current:
        modified.append(name)
        patch += "".join(difflib.unified_diff(data.decode().splitlines(True), current.decode().splitlines(True), fromfile="a/" + name, tofile="b/" + name))
actual_patch = (root / "RUSTUSB.patch").read_bytes()
if patch.encode() != actual_patch:
    failures.append("exact patch reconstruction")
if sha(actual_patch) != manifest["patch_sha256"]:
    failures.append("patch checksum")
if modified != manifest["modified_files"]:
    failures.append("modified file list")
if len(original) != manifest["archive_file_count"]:
    failures.append("archive file count")
if unsafe_counts != [manifest["source_unsafe_tokens_before"], manifest["source_unsafe_tokens_after"]]:
    failures.append("unsafe token counts")
licenses = {name: {"unchanged": (root / name).read_bytes() == original[name], "sha256": sha((root / name).read_bytes())} for name in ["LICENSE-MIT", "LICENSE-APACHE"]}
if not all(record["unchanged"] for record in licenses.values()):
    failures.append("license changes")
print(json.dumps({"result": "PASS" if not failures else "FAIL", "original_file_count": len(original), "modified_files": modified, "archive_sha256": sha(archive.read_bytes()), "patch_sha256": sha(actual_patch), "patch_byte_count": len(actual_patch), "licenses": licenses, "unsafe_token_counts": unsafe_counts, "vendor_target_exists": (root / "target").exists(), "failures": failures}, indent=2))
raise SystemExit(bool(failures))
