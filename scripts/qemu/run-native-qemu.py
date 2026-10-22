#!/usr/bin/env python3
"""Exercise Native USB/IP across two disposable Linux guests and a simulated USB device.

Only guest kernels load modules and bind devices. Host access is read-only except
temporary artifacts, QEMU processes and saved evidence. No USB host passthrough,
tap device, host module loading, host USB/IP service, or public listener is used.
"""

import argparse
import datetime
import gzip
import hashlib
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


REPO = Path(__file__).resolve().parents[2]
MODULES = ["usb-common", "usbcore", "e1000", "xhci-hcd", "xhci-pci-renesas",
           "xhci-pci", "evdev", "hid", "hid-generic", "usbhid", "usbserial", "ftdi_sio",
           "usbip-core", "usbip-host", "vhci-hcd"]


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_fingerprint():
    """Hash Rust sources/manifests and the exact runner, excluding evidence output."""
    paths = [REPO / "Cargo.toml", REPO / "Cargo.lock"]
    paths += [path for path in (REPO / "crates").rglob("*")
              if path.is_file() and (path.suffix == ".rs" or path.name == "Cargo.toml")]
    paths += [REPO / "scripts/qemu/guest-init.sh", Path(__file__).resolve()]
    digest = hashlib.sha256()
    for path in sorted(paths):
        name = str(path.relative_to(REPO)).encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def real_tool(name, kernel):
    candidates = [Path(f"/usr/lib/linux-tools/{kernel}/{name}"),
                  Path(f"/usr/sbin/{name}"), Path(f"/usr/bin/{name}")]
    for path in candidates:
        if path.is_file() and path.read_bytes()[:4] == b"\x7fELF":
            return path.resolve()
    raise RuntimeError(f"ELF {name} tool missing for kernel {kernel}")


def copy_binary(source, target, root):
    source = Path(source)
    dest = root / target.lstrip("/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    result = subprocess.run(["ldd", str(source)], capture_output=True, text=True)
    for lib in re.findall(r"/[^\s()]+", result.stdout):
        lib = Path(lib)
        if lib.is_file():
            destination = root / str(lib).lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lib, destination)


def initramfs(work, kernel):
    root = work / "root"
    root.mkdir()
    for path in ["bin", "sbin", "usr/bin", "etc", "proc", "sys", "dev", "tmp", "run", "modules"]:
        (root / path).mkdir(parents=True, exist_ok=True)
    busybox = Path(shutil.which("busybox") or "/usr/bin/busybox")
    copy_binary(busybox, "/bin/busybox", root)
    applets = subprocess.check_output([str(busybox), "--list"], text=True).splitlines()
    for name in applets:
        (root / "bin" / name).symlink_to("busybox") if name != "busybox" else None
    for name in ["rustusb", "rustusb-helper"]:
        copy_binary(REPO / "target/release" / name, f"/usr/bin/{name}", root)
    for name in ["jq", "setpriv"]:
        path = shutil.which(name)
        if path is None:
            raise RuntimeError(f"required command missing: {name}")
        copy_binary(path, f"/usr/bin/{name}", root)
    for name in ["usbip", "usbipd"]:
        copy_binary(real_tool(name, kernel), f"/usr/bin/{name}", root)
    tree = Path(f"/lib/modules/{kernel}")
    for name in MODULES:
        matches = list(tree.rglob(f"{name}.ko*"))
        if not matches:
            continue  # built-in drivers have no module file
        source = matches[0]
        if source.suffix == ".xz":
            data = lzma.decompress(source.read_bytes())
        elif source.suffix == ".gz":
            data = gzip.decompress(source.read_bytes())
        elif source.suffix == ".zst":
            data = subprocess.check_output(["zstd", "-d", "-c", str(source)])
        else:
            data = source.read_bytes()
        (root / "modules" / f"{name}.ko").write_bytes(data)
    shutil.copy2(REPO / "scripts/qemu/guest-init.sh", root / "init")
    (root / "init").chmod(0o755)
    (root / "etc/passwd").write_text("root:x:0:0:root:/:/bin/sh\nrustusb:x:1000:1000:test:/:/bin/sh\n")
    root.chmod(0o755)
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o755)
    archive = work / "initramfs.cpio.gz"
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    result = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root,
                            capture_output=True, check=True)
    archive.write_bytes(gzip.compress(result.stdout))
    return archive


def qmp_command(connection, reader, command, arguments=None):
    connection.sendall(json.dumps({"execute": command, "arguments": arguments or {}}).encode() + b"\n")
    while True:
        message = json.loads(reader.readline())
        if "error" in message:
            raise RuntimeError(f"QMP error: {message['error']}")
        if "return" in message:
            return message["return"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", default=180, type=int)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--scenario", choices=["managed", "linux-client", "linux-server", "reconnect"], default="managed")
    parser.add_argument("--device", choices=["hid", "ftdi-serial"], default="hid")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as an ordinary user. Only kernel-image copying may use sudo.")
    if os.uname().machine != "x86_64":
        raise RuntimeError("This runner currently verifies x86_64 host/guest only.")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be between 30 and 1800 seconds")
    if args.scenario == "reconnect" and args.device != "hid":
        parser.error("the reconnect ownership scenario currently requires --device hid")
    if not args.skip_build:
        run(["cargo", "build", "--workspace", "--release", "--locked"], cwd=REPO)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "" if args.scenario == "managed" and args.device == "hid" else f"-{args.scenario}-{args.device}"
    evidence = (args.evidence or REPO / "docs/evidence" / f"qemu-native{suffix}-{timestamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"started_at_utc": timestamp, "kernel": args.kernel,
               "scope": "two isolated guests; QEMU simulated USB device; Native Linux data path; no physical USB",
               "scenario": args.scenario, "device": args.device,
               "qemu_version": subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip(),
               "host_uid": os.geteuid(), "host_architecture": os.uname().machine,
               "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
               "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
               "build_performed": not args.skip_build,
               "cargo_lock_sha256": sha256(REPO / "Cargo.lock"),
               "source_fingerprint_sha256": source_fingerprint(),
               "script_sha256": {name: sha256(REPO / "scripts/qemu" / name)
                                 for name in ["guest-init.sh", "run-native-qemu.py"]},
               "binary_sha256": {name: sha256(REPO / "target/release" / name)
                                 for name in ["rustusb", "rustusb-helper"]}, "result": "FAIL"}
    processes = []
    handles = []
    serial_socket = None
    injected = set()
    serial_received = bytearray()
    try:
        with tempfile.TemporaryDirectory(prefix="rustusb-native-qemu-") as temporary:
            work = Path(temporary)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)])
                run(["sudo", "-n", "chmod", "644", str(image)])
            archive = initramfs(work, args.kernel)
            summary["kernel_image_sha256"] = sha256(image)
            summary["initramfs_sha256"] = sha256(archive)
            summary["native_tool_sha256"] = {name: sha256(real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            left, right = socket.socketpair()
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            qmp_path = work / "server-qmp.sock"
            for role, link in [("server", left), ("client", right)]:
                stderr = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(stderr)
                command = ["qemu-system-x86_64", "-m", "384", "-smp", "2", *acceleration,
                           "-kernel", str(image), "-initrd", str(archive),
                           "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.role={role} rustusb.scenario={args.scenario} rustusb.device={args.device}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none",
                           "-netdev", f"socket,id=private,fd={link.fileno()}",
                           "-device", f"e1000,netdev=private,mac=52:54:00:00:00:{'01' if role == 'server' else '02'}"]
                if role == "server":
                    command += ["-device", "qemu-xhci,id=usb", "-qmp", f"unix:{qmp_path},server=on,wait=off"]
                    if args.device == "hid":
                        command += ["-device", "usb-kbd,bus=usb.0"]
                    else:
                        command += ["-chardev", f"socket,id=serial,path={work / 'ftdi.sock'},server=on,wait=off",
                                    "-device", "usb-serial,bus=usb.0,chardev=serial,always-plugged=on"]
                processes.append(subprocess.Popen(command, pass_fds=[link.fileno()], stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=stderr))
            left.close()
            right.close()
            deadline = time.monotonic() + args.timeout
            cycles = [1, 2, 3] if args.scenario == "reconnect" else [1, 2]
            expected_serial = b"".join(f"RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n".encode() for cycle in cycles)
            markers_seen = False
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as qmp:
                qmp.settimeout(5)
                while not qmp_path.exists() and time.monotonic() < deadline:
                    if processes[0].poll() is not None:
                        raise RuntimeError("server QEMU exited before QMP became available")
                    time.sleep(0.1)
                qmp.connect(str(qmp_path))
                reader = qmp.makefile("rb")
                json.loads(reader.readline())
                qmp_command(qmp, reader, "qmp_capabilities")
                if args.device == "ftdi-serial":
                    serial_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    serial_socket.settimeout(5)
                    serial_socket.connect(str(work / "ftdi.sock"))
                    serial_socket.setblocking(False)
                while time.monotonic() < deadline:
                    client = (evidence / "client-serial.log").read_text(errors="replace")
                    server = (evidence / "server-serial.log").read_text(errors="replace")
                    for cycle in cycles:
                        marker = "RUSTUSB_WAIT_INPUT" if args.device == "hid" else "RUSTUSB_WAIT_SERIAL"
                        if f"{marker} cycle={cycle} " in client and cycle not in injected:
                            if args.device == "hid":
                                qmp_command(qmp, reader, "send-key", {"keys": [{"type": "qcode", "data": "1"}], "hold-time": 100})
                            else:
                                serial_socket.settimeout(2)
                                serial_socket.sendall(f"RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n".encode())
                                serial_socket.setblocking(False)
                            injected.add(cycle)
                    if serial_socket is not None:
                        while True:
                            try:
                                chunk = serial_socket.recv(4096)
                            except BlockingIOError:
                                break
                            if not chunk:
                                break
                            serial_received.extend(chunk)
                            if not expected_serial.startswith(serial_received):
                                raise RuntimeError("FTDI output differs from the exact sequenced test frames")
                    if "RUSTUSB_QEMU_RESULT: FAIL" in client + server:
                        raise RuntimeError("guest reported a test failure")
                    if ("RUSTUSB_QEMU_RESULT: PASS role=client" in client
                            and "RUSTUSB_QEMU_RESULT: PASS role=server" in server
                            and (serial_socket is None or serial_received == expected_serial)):
                        markers_seen = True
                        break
                    if all(process.poll() is not None for process in processes):
                        raise RuntimeError("guests exited before both PASS markers")
                    time.sleep(0.1)
                if not markers_seen:
                    raise RuntimeError("QEMU native integration deadline exceeded")
            summary["qmp_key_injections" if args.device == "hid" else "serial_frame_injections"] = sorted(injected)
            if sorted(injected) != cycles:
                raise RuntimeError("All required synthetic input injections were not observed")
            if args.device == "ftdi-serial":
                summary["serial_output"] = {"expected_bytes": len(expected_serial), "received_bytes": len(serial_received),
                                            "sha256": hashlib.sha256(serial_received).hexdigest(),
                                            "exact_match": serial_received == expected_serial}
                if serial_received != expected_serial:
                    raise RuntimeError("FTDI output was incomplete")
            for role, process in zip(["server", "client"], processes):
                text = (evidence / f"{role}-serial.log").read_text(errors="replace")
                summary[role + "_checks"] = [line.strip() for line in text.splitlines() if line.startswith("RUSTUSB_")]
                if role == "server":
                    driver = "usbhid" if args.device == "hid" else "ftdi_sio"
                    unexport_cycles = [3] if args.scenario == "reconnect" else cycles
                    required = [f"RUSTUSB_SERVER_UNEXPORTED cycle={cycle} local_driver=usb interface_driver={driver}"
                                for cycle in unexport_cycles]
                    if args.scenario == "linux-server":
                        required += ["RUSTUSB_EXTERNAL_EXPORT_GATE: PASS ownership=unmanaged unchanged=true"]
                else:
                    required = ["RUSTUSB_UID_GATE: PASS denied_uid=1001 allowed_uid=1000"]
                    required += [f"RUSTUSB_CLIENT_DETACHED cycle={cycle} kernel_sessions=0 device_removed=true" for cycle in cycles]
                    if args.device == "hid":
                        required += [f"RUSTUSB_HID_INPUT: PASS cycle={cycle} KEY_1_down" for cycle in cycles]
                    else:
                        required += [f"RUSTUSB_SERIAL_INPUT: PASS cycle={cycle} driver=ftdi_sio " for cycle in cycles]
                    if args.scenario == "linux-client":
                        required += [f"RUSTUSB_LINUX_CLIENT: PASS cycle={cycle} list=true port=true" for cycle in cycles]
                        required += [f"RUSTUSB_EXTERNAL_SESSION_GATE: PASS cycle={cycle} ownership=unmanaged unchanged=true" for cycle in cycles]
                    if args.scenario == "reconnect":
                        required += ["RUSTUSB_DIRECT_RECONNECT: PASS ", "RUSTUSB_REUSED_PORT_GATE: PASS "]
                required += [f"RUSTUSB_DOCTOR_GATE: PASS role={role} missing_before=true root_peer_verified=true denied_uid=1001 network_verified=false hardware_verified=false"]
                if not all(any(line == marker or (marker.endswith(" ") and line.startswith(marker))
                               for line in summary[role + "_checks"]) for marker in required):
                    raise RuntimeError(f"Required {role} evidence marker missing")
                if process.wait(timeout=10) != 0:
                    raise RuntimeError(f"{role} QEMU exited unsuccessfully despite PASS markers")
            if source_fingerprint() != summary["source_fingerprint_sha256"]:
                raise RuntimeError("Source files changed while the tested guests were running")
            summary["pass_markers_verified"] = True
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = str(error)
        raise
    finally:
        if serial_socket is not None:
            serial_socket.close()
        summary["qmp_key_injections" if args.device == "hid" else "serial_frame_injections"] = sorted(injected)
        if args.device == "ftdi-serial":
            summary["serial_received_bytes"] = len(serial_received)
            summary["serial_received_sha256"] = hashlib.sha256(serial_received).hexdigest()
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
        summary["guest_exit_codes"] = {role: process.returncode for role, process in zip(["server", "client"], processes)}
        for role in ["server", "client"]:
            log = evidence / f"{role}-serial.log"
            if log.exists():
                lines = log.read_text(errors="replace").splitlines()
                summary[role + "_checks"] = [line.strip() for line in lines if line.startswith("RUSTUSB_")]
                if summary["result"] != "PASS":
                    summary[role + "_diagnostics"] = [line.strip() for line in lines
                                                       if line.startswith(("rustusb-helper:", "DIAGNOSTIC", "helper did", "setpriv:"))]
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"result": summary["result"], "evidence": str(evidence)}, indent=2))


if __name__ == "__main__":
    main()
