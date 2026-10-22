#!/usr/bin/env python3
"""Verify explicit TCP abort, owned cleanup and reconnect in disposable guests.

The only fault injection is a guest-root ss SOCK_DESTROY request for one exact
established USB/IP tuple. This is not a WAN blackhole or heartbeat benchmark.
No host USB, kernel module, network interface, or USB/IP service is modified.
"""

import argparse
import datetime
import gzip
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE_PATH = HERE / "run-native-qemu.py"
SPEC = importlib.util.spec_from_file_location("rustusb_native_qemu", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load the fixed Native QEMU helper module")
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
SCRIPT_NAMES = ["guest-init.sh", "run-native-qemu.py", "guest-fault-init.sh", "run-fault-qemu.py"]


def fingerprint():
    """Include all Rust inputs and both reused and new harness files."""
    digest = hashlib.sha256(BASE.source_fingerprint().encode())
    for name in SCRIPT_NAMES:
        data = (HERE / name).read_bytes()
        digest.update(name.encode())
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def fault_initramfs(work, kernel):
    """Reuse existing binary/module copying, replacing only temporary guest files."""
    archive = BASE.initramfs(work, kernel)
    root = work / "root"
    ss = shutil.which("ss")
    if ss is None:
        raise RuntimeError("iproute2 ss is required for the guest-only SOCK_DESTROY test")
    BASE.copy_binary(ss, "/usr/bin/ss", root)
    module_hashes = {}
    for name in ["inet_diag", "tcp_diag"]:
        matches = sorted(Path(f"/lib/modules/{kernel}").rglob(f"{name}.ko*"))
        if not matches:
            raise RuntimeError(f"Required diagnostic module file missing: {name} for {kernel}")
        source = matches[0]
        data = source.read_bytes()
        if source.suffix == ".xz":
            data = lzma.decompress(data)
        elif source.suffix == ".gz":
            data = gzip.decompress(data)
        elif source.suffix == ".zst":
            data = subprocess.check_output(["zstd", "-d", "-c", str(source)])
        (root / "modules" / f"{name}.ko").write_bytes(data)
        module_hashes[name] = hashlib.sha256(data).hexdigest()
    shutil.copy2(HERE / "guest-fault-init.sh", root / "init")
    (root / "init").chmod(0o755)
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    result = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"],
                            input=names, cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(result.stdout))
    return archive, module_hashes


def read_log(evidence, role):
    path = evidence / f"{role}-serial.log"
    return path.read_text(errors="replace") if path.exists() else ""


def checks(text):
    return [line.strip() for line in text.splitlines() if line.startswith("RUSTUSB_")]


def verify_markers(server, client):
    required_client = [f"RUSTUSB_FAULT_HID_INPUT: PASS cycle={cycle} KEY_1_down" for cycle in [1, 2, 3]]
    required_client += [f"RUSTUSB_FAULT_KERNEL_RELEASE: PASS cycle={cycle} " for cycle in [1, 2, 3]]
    required_client += ["RUSTUSB_FAULT_OWNED_CLEANUP: PASS cycle=1 already_kernel_released=true cli_uid=1000",
                        "RUSTUSB_FAULT_DIRECT_RECONNECT: PASS cycle=3 managed=true new_generation=true explicit_detach=false",
                        "RUSTUSB_FAULT_RESULT: PASS role=client"]
    for marker in required_client:
        if not any(line == marker or (marker.endswith(" ") and line.startswith(marker)) for line in checks(client)):
            raise RuntimeError(f"Required client gate absent: {marker}")
    if "RUSTUSB_FAULT_SERVER_RESTORED: PASS local_driver=usb interface_driver=usbhid" not in server:
        raise RuntimeError("Server local USB/class-driver restoration was not verified")
    targets = re.findall(r"RUSTUSB_TCP_ABORT_TARGET fault=([12]) local=192\.0\.2\.1:3240 remote=192\.0\.2\.2:(\d+) unique_established=1 mechanism=SOCK_DESTROY", server)
    outcomes = re.findall(r"RUSTUSB_TCP_ABORT: PASS fault=([12]) remote_port=(\d+) before=1 after=0 usbip_status=1 usbipd_alive=true helper_alive=true", server)
    if [item[0] for item in targets] != ["1", "2"] or targets != outcomes:
        raise RuntimeError("Two unique TCP tuple targets and their exact abort outcomes were not verified")
    return [{"fault": int(fault), "local": "192.0.2.1:3240", "remote": f"192.0.2.2:{port}",
             "mechanism": "guest ss SOCK_DESTROY", "unique_established_before": 1,
             "established_after": 0, "server_usbip_status_after": 1}
            for fault, port in targets]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", default=180, type=int)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as an ordinary user; only copying the test kernel may use sudo")
    if os.uname().machine != "x86_64":
        raise RuntimeError("This fault runner currently verifies x86_64 host/guest only")
    if re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel) is None:
        parser.error("--kernel must be a safe running-kernel release token")
    if not 30 <= args.timeout <= 180:
        parser.error("--timeout must be between 30 and 180 seconds")
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = (args.evidence or REPO / "docs/evidence" / f"qemu-transport-abort-{timestamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {
        "started_at_utc": timestamp, "kernel": args.kernel, "scenario": "explicit-tcp-transport-abort", "device": "hid",
        "scope": "two isolated QEMU guests; synthetic HID; guest-root SOCK_DESTROY; no physical USB or WAN blackhole test",
        "host_uid": os.geteuid(), "host_architecture": os.uname().machine,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "build_performed": not args.skip_build, "cargo_lock_sha256": BASE.sha256(REPO / "Cargo.lock"),
        "source_fingerprint_sha256": fingerprint(),
        "script_sha256": {name: BASE.sha256(HERE / name) for name in SCRIPT_NAMES},
        "guest_release_deadline_seconds": 15, "runner_deadline_seconds": args.timeout,
        "keepalive_or_tcp_sysctl_modified": False, "result": "FAIL",
    }
    processes = []
    handles = []
    injected = set()
    try:
        if not args.skip_build:
            with (evidence / "build.log").open("wb") as log:
                BASE.run(["cargo", "build", "--workspace", "--release", "--locked"], cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
        summary["binary_sha256"] = {name: BASE.sha256(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        summary["qemu_version"] = subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip()
        summary["ss_version"] = subprocess.check_output(["ss", "--version"], text=True).strip()
        summary["ss_binary_sha256"] = BASE.sha256(shutil.which("ss"))
        config_path = Path("/boot") / f"config-{args.kernel}"
        config = config_path.read_text()
        summary["kernel_diagnostic_config"] = [line for line in config.splitlines()
                                                if line.startswith(("CONFIG_INET_DIAG=", "CONFIG_INET_TCP_DIAG=", "CONFIG_INET_DIAG_DESTROY="))]
        if "CONFIG_INET_DIAG_DESTROY=y" not in config.splitlines():
            raise RuntimeError("Test kernel must explicitly provide CONFIG_INET_DIAG_DESTROY=y")
        with tempfile.TemporaryDirectory(prefix="rustusb-fault-qemu-") as temporary:
            work = Path(temporary)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                BASE.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)])
                BASE.run(["sudo", "-n", "chmod", "644", str(image)])
            archive, module_hashes = fault_initramfs(work, args.kernel)
            summary["kernel_image_sha256"] = BASE.sha256(image)
            summary["initramfs_sha256"] = BASE.sha256(archive)
            summary["diagnostic_module_sha256"] = module_hashes
            summary["native_tool_sha256"] = {name: BASE.sha256(BASE.real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            qmp_path = work / "server-qmp.sock"
            left, right = socket.socketpair()
            try:
                for role, link in [("server", left), ("client", right)]:
                    stderr = (evidence / f"{role}-qemu.stderr").open("wb")
                    handles.append(stderr)
                    command = ["qemu-system-x86_64", "-m", "384", "-smp", "2", *acceleration,
                               "-kernel", str(image), "-initrd", str(archive),
                               "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.role={role} rustusb.fault=transport-abort",
                               "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}",
                               "-monitor", "none", "-no-reboot", "-nic", "none",
                               "-netdev", f"socket,id=private,fd={link.fileno()}",
                               "-device", f"e1000,netdev=private,mac=52:54:00:00:00:{'11' if role == 'server' else '12'}"]
                    if role == "server":
                        command += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,bus=usb.0",
                                    "-qmp", f"unix:{qmp_path},server=on,wait=off"]
                    processes.append(subprocess.Popen(command, pass_fds=[link.fileno()], stdin=subprocess.DEVNULL,
                                                      stdout=subprocess.DEVNULL, stderr=stderr))
            finally:
                left.close()
                right.close()
            deadline = time.monotonic() + args.timeout
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as qmp:
                qmp.settimeout(5)
                while not qmp_path.exists() and time.monotonic() < deadline:
                    if processes[0].poll() is not None:
                        raise RuntimeError("Server QEMU exited before QMP became available")
                    time.sleep(0.1)
                qmp.connect(str(qmp_path))
                reader = qmp.makefile("rb")
                json.loads(reader.readline())
                BASE.qmp_command(qmp, reader, "qmp_capabilities")
                while time.monotonic() < deadline:
                    server, client = read_log(evidence, "server"), read_log(evidence, "client")
                    for cycle in [1, 2, 3]:
                        if f"RUSTUSB_FAULT_WAIT_INPUT cycle={cycle} " in client and cycle not in injected:
                            BASE.qmp_command(qmp, reader, "send-key", {"keys": [{"type": "qcode", "data": "1"}], "hold-time": 100})
                            injected.add(cycle)
                    if "RUSTUSB_FAULT_RESULT: FAIL" in server + client:
                        raise RuntimeError("Guest reported a fault-harness failure")
                    if "RUSTUSB_FAULT_RESULT: PASS role=server" in server and "RUSTUSB_FAULT_RESULT: PASS role=client" in client:
                        break
                    if all(process.poll() is not None for process in processes):
                        raise RuntimeError("Both guests exited before complete PASS evidence")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Fault-harness deadline exceeded")
            if sorted(injected) != [1, 2, 3]:
                raise RuntimeError("Three required HID injections were not observed")
            summary["transport_aborts"] = verify_markers(server, client)
            for role, process in zip(["server", "client"], processes):
                if process.wait(timeout=10) != 0:
                    raise RuntimeError(f"{role} guest exited unsuccessfully after PASS marker")
            if fingerprint() != summary["source_fingerprint_sha256"]:
                raise RuntimeError("Product or harness source changed while the guests were running")
            summary["pass_markers_verified"] = True
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = str(error)
        raise
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for handle in handles:
            handle.close()
        summary["qmp_key_injections"] = sorted(injected)
        summary["guest_exit_codes"] = {role: process.returncode for role, process in zip(["server", "client"], processes)}
        for role in ["server", "client"]:
            summary[role + "_checks"] = checks(read_log(evidence, role))
        summary["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"result": summary["result"], "evidence": str(evidence)}, indent=2))


if __name__ == "__main__":
    main()
