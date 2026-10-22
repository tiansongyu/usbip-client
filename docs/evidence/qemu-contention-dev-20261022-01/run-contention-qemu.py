#!/usr/bin/env python3
"""Verify a simulated HID remains exclusive across three isolated Linux guests.

Two private socketpairs connect server/client-a and server/client-b. No physical
USB, host module changes, tap, public listener, or external network is used.
"""

import argparse
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
ROLES = ["server", "client-a", "client-b"]
spec = importlib.util.spec_from_file_location("native_qemu", REPO / "scripts/qemu/run-native-qemu.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def source_fingerprint():
    digest = hashlib.sha256(bytes.fromhex(native.source_fingerprint()))
    for path in [Path(__file__).resolve(), REPO / "scripts/qemu/guest-contention-init.sh"]:
        data = path.read_bytes()
        digest.update(path.name.encode())
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def contention_initramfs(work, kernel):
    """Reuse the pinned native guest payload; replace only its guest init entry."""
    archive = native.initramfs(work, kernel)
    root = work / "root"
    shutil.copy2(REPO / "scripts/qemu/guest-contention-init.sh", root / "init")
    (root / "init").chmod(0o755)
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    result = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"],
                            input=names, cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(result.stdout))
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as an ordinary user; guest root owns all test mutations.")
    if os.uname().machine != "x86_64":
        raise RuntimeError("This runner currently verifies x86_64 guests only.")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be 30–1800 seconds")
    if not args.skip_build:
        subprocess.run(["cargo", "build", "--workspace", "--release", "--locked"], cwd=REPO, check=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = (args.evidence or REPO / "docs/evidence" / f"qemu-contention-{timestamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    scripts = ["run-contention-qemu.py", "guest-contention-init.sh", "run-native-qemu.py", "guest-init.sh"]
    summary = {
        "started_at_utc": timestamp, "scope": "three isolated guests; one QEMU USB keyboard; Native Linux USB/IP; no physical USB",
        "kernel": args.kernel, "host_uid": os.geteuid(), "host_architecture": os.uname().machine,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "qemu_version": subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip(),
        "build_performed": not args.skip_build, "cargo_lock_sha256": native.sha256(REPO / "Cargo.lock"),
        "source_fingerprint_sha256": source_fingerprint(),
        "script_sha256": {name: native.sha256(REPO / "scripts/qemu" / name) for name in scripts},
        "binary_sha256": {name: native.sha256(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]},
        "topology": {"link_a": ["server:192.0.2.1", "client-a:192.0.2.2"], "link_b": ["server:192.0.3.1", "client-b:192.0.3.2"], "ip_forward": False},
        "result": "FAIL",
    }
    processes = {}
    handles = []
    links = []
    injections = set()
    expected_inputs = {("client-a", "before", "1"), ("client-a", "after", "2"), ("client-b", "handoff", "3")}
    try:
        with tempfile.TemporaryDirectory(prefix="rustusb-contention-qemu-") as temporary:
            work = Path(temporary)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            archive = contention_initramfs(work, args.kernel)
            summary["kernel_image_sha256"] = native.sha256(image)
            summary["initramfs_sha256"] = native.sha256(archive)
            summary["native_tool_sha256"] = {name: native.sha256(native.real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            server_a, client_a = socket.socketpair()
            server_b, client_b = socket.socketpair()
            links = [server_a, client_a, server_b, client_b]
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            qmp_path = work / "server-qmp.sock"
            for role in ROLES:
                stderr = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(stderr)
                command = ["qemu-system-x86_64", "-m", "384", "-smp", "2", *acceleration,
                           "-kernel", str(image), "-initrd", str(archive),
                           "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.role={role}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none"]
                endpoints = [("link-a", server_a, "01"), ("link-b", server_b, "02")] if role == "server" else [
                    ("link-a", client_a, "03") if role == "client-a" else ("link-b", client_b, "04")]
                for identifier, link, mac in endpoints:
                    command += ["-netdev", f"socket,id={identifier},fd={link.fileno()}", "-device",
                                f"e1000,id={identifier}-nic,netdev={identifier},mac=52:54:00:00:00:{mac}"]
                if role == "server":
                    command += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,bus=usb.0",
                                "-qmp", f"unix:{qmp_path},server=on,wait=off"]
                processes[role] = subprocess.Popen(command, pass_fds=[link.fileno() for _, link, _ in endpoints],
                                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
            for link in links:
                link.close()
            deadline = time.monotonic() + args.timeout
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as qmp:
                qmp.settimeout(5)
                while not qmp_path.exists():
                    if processes["server"].poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("server QMP unavailable before deadline")
                    time.sleep(0.1)
                qmp.connect(str(qmp_path))
                reader = qmp.makefile("rb")
                json.loads(reader.readline())
                native.qmp_command(qmp, reader, "qmp_capabilities")
                completed = False
                while time.monotonic() < deadline:
                    logs = {role: (evidence / f"{role}-serial.log").read_text(errors="replace") for role in ROLES}
                    for role, phase, key in expected_inputs:
                        marker = f"RUSTUSB_CONTENTION_WAIT_INPUT role={role} phase={phase} key={key}"
                        if marker in logs[role] and (role, phase, key) not in injections:
                            native.qmp_command(qmp, reader, "send-key", {"keys": [{"type": "qcode", "data": key}], "hold-time": 100})
                            injections.add((role, phase, key))
                    if any("RUSTUSB_CONTENTION_RESULT: FAIL" in log for log in logs.values()):
                        raise RuntimeError("a contention guest reported failure")
                    if all(f"RUSTUSB_CONTENTION_RESULT: PASS role={role}" in logs[role] for role in ROLES):
                        completed = True
                        break
                    if all(process.poll() is not None for process in processes.values()):
                        raise RuntimeError("guests exited before complete contention evidence")
                    time.sleep(0.1)
                if not completed:
                    raise RuntimeError("contention integration deadline exceeded")
            if injections != expected_inputs:
                raise RuntimeError("three distinct required HID injections not observed")
            required = {
                "server": ["RUSTUSB_CONTENTION_SERVER_USED owner=client-a status=2", "RUSTUSB_CONTENTION_SERVER_STILL_USED owner=client-a status=2", "RUSTUSB_CONTENTION_SERVER_RESTORED local_driver=usb interface_driver=usbhid"],
                "client-a": ["RUSTUSB_CONTENTION_INPUT: PASS role=client-a phase=before key_code=2", "RUSTUSB_CONTENTION_INPUT: PASS role=client-a phase=after key_code=3", "RUSTUSB_CONTENTION_OWNER_UNCHANGED: PASS role=client-a receipt=true reader_continuous=true"],
                "client-b": ["RUSTUSB_CONTENTION_MANAGED_REJECT: PASS role=client-b kernel_sessions=0 reason=", "RUSTUSB_CONTENTION_WIRE_BUSY: PASS role=client-b reply_hex=0111000300000002 status=2", "RUSTUSB_CONTENTION_NATIVE_REJECT: PASS role=client-b kernel_sessions=0", "RUSTUSB_CONTENTION_INPUT: PASS role=client-b phase=handoff key_code=4"],
            }
            for role in ROLES:
                lines = [line.strip() for line in (evidence / f"{role}-serial.log").read_text(errors="replace").splitlines() if line.startswith("RUSTUSB_")]
                required[role] += [f"RUSTUSB_CONTENTION_UID_GATE: PASS role={role} denied_uid=1001 allowed_uid=1000", f"RUSTUSB_CONTENTION_ISOLATION role={role} ip_forward=0"]
                if role != "server":
                    required[role] += [f"RUSTUSB_CONTENTION_RELEASED role={role} kernel_sessions=0 device_removed=true", f"RUSTUSB_CONTENTION_EMPTY: PASS role={role} remote_devices=0"]
                for marker in required[role]:
                    if not any(line.startswith(marker) if marker.endswith("=") else line == marker for line in lines):
                        raise RuntimeError(f"missing required {role} assertion: {marker}")
                if processes[role].wait(timeout=10) != 0:
                    raise RuntimeError(f"{role} exited unsuccessfully")
            if source_fingerprint() != summary["source_fingerprint_sha256"]:
                raise RuntimeError("source or scripts changed during contention test")
            summary["pass_markers_verified"] = True
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = str(error)
        raise
    finally:
        for link in links:
            link.close()
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for handle in handles:
            handle.close()
        summary["qmp_key_injections"] = [dict(zip(["role", "phase", "key"], entry)) for entry in sorted(injections)]
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        for role in ROLES:
            path = evidence / f"{role}-serial.log"
            if path.exists():
                summary[role + "_checks"] = [line.strip() for line in path.read_text(errors="replace").splitlines() if line.startswith("RUSTUSB_")]
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"result": summary["result"], "evidence": str(evidence)}, indent=2))


if __name__ == "__main__":
    main()
