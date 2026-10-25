#!/usr/bin/env python3
"""Schema-expectation negatives and exact generated-guest syntax, without USB."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("automatic_rust_preflight_target", Path(__file__).with_name("run.py"))
target = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = target
spec.loader.exec_module(target)


def main():
    check = target.old_lifecycle("virtual").application_disk
    old = {"schema_version": 2, "sequence": 0, "jobs": [], "sessions": [], "requests": [], "audit": [], "link_history": []}
    current = {**copy.deepcopy(old), "schema_version": 3, "automation": {"sequence": 0, "rules": [], "mutations": [], "origins": [], "events": []}}
    cases = []
    def accept(state, role, schemas, name):
        check(state, role, schemas)
        cases.append({"name": name, "expected": "accepted", "observed": "accepted"})
    def reject(state, role, schemas, name):
        try:
            check(state, role, schemas)
        except RuntimeError:
            cases.append({"name": name, "expected": "rejected", "observed": "rejected"})
            return
        raise RuntimeError("invalid schema fixture accepted: " + name)
    for role in ["client", "server"]:
        other = "server" if role == "client" else "client"
        accept(old, role, None, role + " default disk2")
        accept(current, role, {role: 3}, role + " explicit disk3")
        accept(old, other, {role: 3}, other + " retains disk2")
        reject(current, role, None, role + " no implicit upgrade")
        reject(old, role, {role: 3}, role + " no silent downgrade")
        reject(old, role, {role: 4}, role + " unknown version")
        reject(old, role, {"unknown": 3}, role + " unknown role")
    for name in old:
        bad = copy.deepcopy(old); del bad[name]
        reject(bad, "client", None, "disk2 missing " + name)
    for value in [None, {}, [], False]:
        bad = copy.deepcopy(old); bad["automation"] = value
        reject(bad, "client", None, "disk2 mixed automation " + repr(value))
    for name in current:
        bad = copy.deepcopy(current); del bad[name]
        reject(bad, "client", {"client": 3}, "disk3 missing " + name)
    for value in [None, {}, [], False]:
        bad = copy.deepcopy(current); bad["automation"] = value
        reject(bad, "client", {"client": 3}, "disk3 invalid automation " + repr(value))
    for name in current["automation"]:
        bad = copy.deepcopy(current); del bad["automation"][name]
        reject(bad, "client", {"client": 3}, "journal missing " + name)
    for name in ["rules", "mutations", "origins", "events"]:
        bad = copy.deepcopy(current); bad["automation"][name] = None
        reject(bad, "client", {"client": 3}, "journal null " + name)
    for value in [True, -1, "0"]:
        bad = copy.deepcopy(current); bad["automation"]["sequence"] = value
        reject(bad, "client", {"client": 3}, "journal sequence " + repr(value))
    bad = copy.deepcopy(current); bad["automation"]["unknown"] = []
    reject(bad, "client", {"client": 3}, "journal unknown field")
    generated = []
    with tempfile.TemporaryDirectory(prefix="rustusb-automatic-syntax-") as directory:
        for backend in ["virtual", "usbfs"]:
            path = Path(directory) / (backend + ".sh")
            path.write_text(target.guest(backend))
            result = subprocess.run(["dash", "-n", str(path)], capture_output=True, check=True)
            generated.append({"backend": backend, "bytes": path.stat().st_size, "sha256": target.link.sha(path), "dash_exit": result.returncode})
    print(json.dumps({"result": "PASS", "schema_cases": cases, "schema_case_count": len(cases), "generated": generated, "source_inputs": target.inputs(), "scope": "Synthetic schema and generated shell syntax only; no real service, packet, USB or hardware acceptance."}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
