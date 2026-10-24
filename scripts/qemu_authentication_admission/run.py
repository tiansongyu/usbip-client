#!/usr/bin/env python3
"""Actual admission exhaustion alongside original live USB imports in two VMs."""
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

auth = module("admission_authentication_base", ROOT / "scripts/qemu_authentication/run.py")
base = auth.base
checker = module("admission_raw_checker", HERE / "verify.py")
original_generated = auth.generated_init

def inputs():
    result = auth.inputs()
    for path in sorted(HERE.glob("*")):
        if path.is_file() and path.suffix in [".py", ".sh", ".md"]:
            result[str(path.relative_to(ROOT))] = {"sha256": base.digest(path), "bytes": path.stat().st_size}
    return dict(sorted(result.items()))

def generated_init():
    source = original_generated()
    def replace(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise RuntimeError(f"admission guest anchor absent/ambiguous: {old[:100]!r}")
        source = source.replace(old, new, 1)
    replace('if [ "$ROLE" = server ]; then\n    STAGE=server-prepare',
            (HERE / "extra.sh").read_text() + '\nif [ "$ROLE" = server ]; then\n    STAGE=server-prepare')
    replace('    auth_policy 1\n', '    jq \' .authentication_limit={burst:32,interval_millis:500}\' /etc/rustusb/server.json > /run/rate-server.json\n    cp /run/rate-server.json /etc/rustusb/server.json\n    auth_policy 1\n')
    replace('        auth_server_barrier "held-$cycle" held\n',
            '        auth_server_barrier "held-$cycle" held\n        rate_server_gate "$cycle"\n')
    replace('        echo "SECURE_CLIENT_WAIT_INPUT cycle=$cycle"',
            '        rate_client_begin "$cycle"\n        echo "SECURE_CLIENT_WAIT_INPUT cycle=$cycle"')
    replace('        echo "SECURE_CLIENT_IO cycle=$cycle key_code=$((cycle+1)) rx=35 tx=35"',
            '        echo "SECURE_CLIENT_IO cycle=$cycle key_code=$((cycle+1)) rx=35 tx=35"\n        rate_client_finish "$cycle"')
    replace('        echo "SECURE_SERVER_IO cycle=$cycle"',
            '        echo "SECURE_SERVER_IO cycle=$cycle"\n        rate_server_after_io "$cycle"')
    replace('    set +e\n', '    set +e\n    if command -v rate_join > /dev/null 2>&1; then\n        rate_join || echo "RUSTUSB_SECURE_RESULT: FAIL role=$ROLE stage=rate-worker-join"\n    fi\n')
    return source

def payload(work, kernel, certificates, role):
    auth.payload(work, kernel, certificates, role)
    root = work / "root"
    worker = root / "etc/rustusb/rate-worker.sh"
    worker.write_bytes((HERE / "worker.sh").read_bytes())
    worker.chmod(0o644)
    preflight = base.validate_payload(root)
    (work / "payload-dependencies.json").write_text(json.dumps(preflight, indent=2) + "\n")
    if auth.run_evidence is None:
        raise RuntimeError("admission credential/evidence owner missing")
    (auth.run_evidence / f"{role}-generated-admission-init.sh").write_bytes((root / "init").read_bytes())
    (auth.run_evidence / f"{role}-installed-rate-worker.sh").write_bytes(worker.read_bytes())
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
    archive = work / "admission-initramfs.cpio.gz"
    archive.write_bytes(gzip.compress(packed.stdout))
    provenance = {str(path.relative_to(root)): base.digest(path) for path in root.rglob("*") if path.is_file() and not path.is_symlink() and
                  (str(path.relative_to(root)).startswith(("usr/bin/", "modules/")) or path in [root / "init", worker, root / "bin/busybox"])}
    return archive, provenance

def phase_ready(logs, phase, cycle):
    if phase == "input":
        return f"RATE_SERVER_LIMITED cycle={cycle}\n" in logs["server"]
    if phase == "close":
        return f"RATE_SERVER_CHECKED cycle={cycle}\n" in logs["server"]
    raise RuntimeError("unknown admission coordinator phase")

def coordinate(logs, send, evidence, args):
    auth.coordinate(logs, send, evidence, args, input_ready=lambda view, cycle: phase_ready(view, "input", cycle))
    for cycle in [1, 2]:
        if f"RATE_CLIENT_WAIT cycle={cycle}\n" in logs["client"] and f"RATE_SERVER_WAIT cycle={cycle}\n" in logs["server"]:
            send(f"rate-begin-{cycle}", "server", f"RATE_BEGIN {cycle}")
        if f"RATE_SERVER_SAVED cycle={cycle}\n" in logs["server"]:
            send(f"rate-go-{cycle}", "client", f"RATE_GO {cycle}")
        if f"RATE_CLIENT_RUNNING cycle={cycle}\n" in logs["client"]:
            send(f"rate-running-{cycle}", "server", f"RATE_RUNNING {cycle}")
        if f"RATE_CLIENT_FLOOD_DONE cycle={cycle}\n" in logs["client"]:
            send(f"rate-done-{cycle}", "server", f"RATE_FLOOD_DONE {cycle}")
        if f"RATE_SERVER_DRAINED cycle={cycle}\n" in logs["server"]:
            send(f"rate-recover-{cycle}", "client", f"RATE_RECOVER {cycle}")
        if f"RATE_CLIENT_RECOVERED cycle={cycle}\n" in logs["client"]:
            send(f"rate-recovered-{cycle}", "server", f"RATE_RECOVERED {cycle}")
        if f"RATE_SERVER_CHECKED cycle={cycle}\n" in logs["server"]:
            send(f"rate-checked-{cycle}", "client", f"RATE_RECOVERY_CHECKED {cycle}")

def main():
    auth.generated_init = generated_init
    base.inputs = inputs
    base.credentials = auth.credentials
    base.payload = payload
    base.verify = lambda evidence, args: checker.verify(evidence, args, base, auth.checker)
    return base.main(coordinate=coordinate, phase_ready=phase_ready,
                     scope="one paired raw-v1 admission run: two original credential epochs, eight actual UID2000 repeated-client workers per epoch, observed rate refusal before original HID/serial I/O, real refill/authentication recovery and original full cleanup; no hardware/fullG3/fairness/performance claim")

if __name__ == "__main__":
    sys.exit(main())
