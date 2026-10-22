#!/usr/bin/env python3
"""Exercise RustUSB across two disposable Linux guests with one simulated USB keyboard.

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
           "xhci-pci", "evdev", "hid", "hid-generic", "usbhid", "usbip-core", "usbip-host", "vhci-hcd"]


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
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as an ordinary user. Only kernel-image copying may use sudo.")
    if os.uname().machine != "x86_64":
        raise RuntimeError("This runner currently verifies x86_64 host/guest only.")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be between 30 and 1800 seconds")
    if not args.skip_build:
        run(["cargo", "build", "--workspace", "--release", "--locked"], cwd=REPO)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = args.evidence or REPO / "docs/evidence" / f"qemu-native-{timestamp}"
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"started_at_utc": timestamp, "kernel": args.kernel, "scope": "two isolated guests; QEMU simulated USB keyboard; no physical USB",
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
            left, right = socket.socketpair()
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            qmp_path = work / "server-qmp.sock"
            for role, link in [("server", left), ("client", right)]:
                stderr = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(stderr)
                command = ["qemu-system-x86_64", "-m", "384", "-smp", "2", *acceleration,
                           "-kernel", str(image), "-initrd", str(archive),
                           "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.role={role}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none",
                           "-netdev", f"socket,id=private,fd={link.fileno()}",
                           "-device", f"e1000,netdev=private,mac=52:54:00:00:00:{'01' if role == 'server' else '02'}"]
                if role == "server":
                    command += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,bus=usb.0",
                                "-qmp", f"unix:{qmp_path},server=on,wait=off"]
                processes.append(subprocess.Popen(command, pass_fds=[link.fileno()], stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=stderr))
            left.close()
            right.close()
            deadline = time.monotonic() + args.timeout
            injected = set()
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
                while time.monotonic() < deadline:
                    client = (evidence / "client-serial.log").read_text(errors="replace")
                    server = (evidence / "server-serial.log").read_text(errors="replace")
                    for cycle in [1, 2]:
                        if f"RUSTUSB_WAIT_INPUT cycle={cycle}" in client and cycle not in injected:
                            qmp_command(qmp, reader, "send-key", {"keys": [{"type": "qcode", "data": "1"}], "hold-time": 100})
                            injected.add(cycle)
                    if "RUSTUSB_QEMU_RESULT: FAIL" in client + server:
                        raise RuntimeError("guest reported a test failure")
                    if "RUSTUSB_QEMU_RESULT: PASS role=client" in client and "RUSTUSB_QEMU_RESULT: PASS role=server" in server:
                        markers_seen = True
                        break
                    if all(process.poll() is not None for process in processes):
                        raise RuntimeError("guests exited before both PASS markers")
                    time.sleep(0.1)
                if not markers_seen:
                    raise RuntimeError("QEMU native integration deadline exceeded")
            summary["qmp_key_injections"] = sorted(injected)
            if sorted(injected) != [1, 2]:
                raise RuntimeError("Both QMP key injections were not observed")
            for role, process in zip(["server", "client"], processes):
                text = (evidence / f"{role}-serial.log").read_text(errors="replace")
                summary[role + "_checks"] = [line.strip() for line in text.splitlines() if line.startswith("RUSTUSB_")]
                required = (["RUSTUSB_SERVER_UNEXPORTED cycle=1 local_driver=usb interface_driver=usbhid",
                             "RUSTUSB_SERVER_UNEXPORTED cycle=2 local_driver=usb interface_driver=usbhid"] if role == "server" else
                            ["RUSTUSB_UID_GATE: PASS denied_uid=1001 allowed_uid=1000",
                             "RUSTUSB_HID_INPUT: PASS cycle=1 KEY_1_down", "RUSTUSB_HID_INPUT: PASS cycle=2 KEY_1_down",
                             "RUSTUSB_CLIENT_DETACHED cycle=1 kernel_sessions=0 device_removed=true",
                             "RUSTUSB_CLIENT_DETACHED cycle=2 kernel_sessions=0 device_removed=true"])
                if not all(marker in summary[role + "_checks"] for marker in required):
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
