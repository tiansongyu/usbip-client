#!/usr/bin/env python3
"""Verify an ordinary-user virtual HID/CDC server against real Linux VHCI.

Two disposable guests communicate through one inherited Unix socketpair. No
QEMU USB device, host USB, host module mutation, tap or public listener is used.
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

REPO = Path(__file__).resolve().parents[2]
ROLES = ["server", "client"]
OWN_SCRIPTS = ["run-userspace-qemu.py", "guest-userspace-init.sh", "userspace-wire-probe.rs"]
spec = importlib.util.spec_from_file_location("native_qemu", REPO / "scripts/qemu/run-native-qemu.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def source_fingerprint():
    digest = hashlib.sha256(bytes.fromhex(native.source_fingerprint()))
    for name in OWN_SCRIPTS:
        data = (REPO / "scripts/qemu" / name).read_bytes()
        digest.update(name.encode())
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def userspace_initramfs(work, kernel, probe):
    """Reuse the immutable native payload builder, adding CDC and this lab init."""
    archive = native.initramfs(work, kernel)
    root = work / "root"
    shutil.copy2(REPO / "scripts/qemu/guest-userspace-init.sh", root / "init")
    (root / "init").chmod(0o755)
    native.copy_binary(probe, "/usr/bin/userspace-wire-probe", root)
    tree = Path(f"/lib/modules/{kernel}")
    matches = list(tree.rglob("cdc-acm.ko*"))
    if matches:
        source = matches[0]
        if source.suffix == ".xz":
            data = lzma.decompress(source.read_bytes())
        elif source.suffix == ".gz":
            data = gzip.decompress(source.read_bytes())
        elif source.suffix == ".zst":
            data = subprocess.check_output(["zstd", "-d", "-c", str(source)])
        else:
            data = source.read_bytes()
        (root / "modules/cdc-acm.ko").write_bytes(data)
        cdc_module = {"source": str(source), "uncompressed_sha256": hashlib.sha256(data).hexdigest(), "builtin": False}
    else:
        builtin = tree / "modules.builtin"
        if not builtin.is_file() or not any(line.endswith("/cdc-acm.ko") for line in builtin.read_text().splitlines()):
            raise RuntimeError(f"CDC ACM module or explicit builtin missing for kernel {kernel}")
        cdc_module = {"builtin": True, "modules_builtin_sha256": native.sha256(builtin)}
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    result = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"],
                            input=names, cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(result.stdout))
    return archive, cdc_module


def parse_events(log):
    start = "RUSTUSB_USERSPACE_EVENTS_BEGIN\n"
    stop = "RUSTUSB_USERSPACE_EVENTS_END"
    if start not in log or stop not in log:
        raise RuntimeError("server JSONL evidence block missing")
    block = log.split(start, 1)[1].split(stop, 1)[0]
    events = [json.loads(line) for line in block.splitlines() if line.strip()]
    if not events or any(event.get("schema_version") != 1 or event.get("event") == "error" for event in events):
        raise RuntimeError("invalid server event schema or explicit server error")
    return events


def verify_server_events(events):
    snapshots = {event["id"]: event["snapshot"] for event in events if event.get("event") == "snapshot"}
    expected_ids = {"baseline", "held-1", "io-1", "released-1", "held-2", "io-2", "released-2"}
    if set(snapshots) != expected_ids:
        raise RuntimeError("missing exact two-cycle snapshot evidence")
    if not any(event.get("event") == "ready" and event.get("address") == "192.0.2.1:3240" for event in events):
        raise RuntimeError("server did not report its isolated address")
    stopped = [event for event in events if event.get("event") == "stopped"]
    if len(stopped) != 1 or events[-1].get("event") != "stopped":
        raise RuntimeError("server did not complete one final shutdown")
    final = stopped[0]["snapshot"]
    if any(device["leased"] or device["pending_urbs"] or device["hid_queued_reports"] or device["cdc_queued_bytes"] for device in final["devices"]):
        raise RuntimeError("server final lease, pending request or input queue leaked")
    cdc = next(device for device in final["devices"] if device["profile"] == "cdc_acm")
    hid = next(device for device in final["devices"] if device["profile"] == "hid_keyboard")
    outbound = b"RUSTUSB-PROBE-01"
    inbound = b"RUSTUSB-PROBE-01"
    for cycle in [1, 2]:
        emitted = f"RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n".encode()
        echoed = f"RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n".encode()
        if len(emitted) != 35 or len(echoed) != 35:
            raise RuntimeError("internal test frame length mismatch")
        outbound += echoed
        inbound += emitted + echoed
    expected_hashes = {"cdc_out_sha256": hashlib.sha256(outbound).hexdigest(),
                       "cdc_in_sha256": hashlib.sha256(inbound).hexdigest()}
    if any(cdc[field] != value for field, value in expected_hashes.items()):
        raise RuntimeError("server cumulative exact CDC byte digest differs")
    counters = cdc["counters"]
    if (counters["cdc_out_bytes"], counters["cdc_in_bytes"], counters["cdc_injected_bytes"]) != (86, 156, 70):
        raise RuntimeError("server cumulative CDC byte totals differ")
    if hid["counters"]["hid_taps"] != 2 or hid["counters"]["unlink_cancelled"] < 1:
        raise RuntimeError("HID two-tap or actual UNLINK cancellation evidence missing")
    return {"cdc_cumulative_bytes": {"out": 86, "in": 156, "injected": 70},
            "cdc_kernel_cycles_bytes": {"out": 70, "in": 140, "injected": 70},
            "probe_cdc_bytes_each_direction": 16, **expected_hashes,
            "final_snapshot": final}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence requires the default locked build")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as an ordinary user; guest root owns test administration.")
    if os.uname().machine != "x86_64":
        raise RuntimeError("This runner currently verifies x86_64 guests only.")
    if re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel) is None:
        parser.error("--kernel must be one safe kernel-release token")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be 30–1800 seconds")
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = (args.evidence or REPO / "docs/evidence" / f"qemu-userspace-{timestamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    scripts = OWN_SCRIPTS + ["run-native-qemu.py", "guest-init.sh"]
    summary = {
        "started_at_utc": timestamp,
        "scope": "ordinary UID1000 userspace virtual HID/standard CDC ACM server to real Linux VHCI; two isolated guests; no physical USB",
        "kernel": args.kernel, "host_uid": os.geteuid(), "host_architecture": os.uname().machine,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "qemu_version": subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip(),
        "rustc_version": subprocess.check_output(["rustc", "--version"], text=True).strip(),
        "build_performed": not args.skip_build,
        "build_command": ["cargo", "build", "--workspace", "--release", "--locked", "--features", "rustusb-cli/rust-backend"],
        "cargo_lock_sha256": native.sha256(REPO / "Cargo.lock"),
        "source_fingerprint_sha256": source_fingerprint(),
        "script_sha256": {name: native.sha256(REPO / "scripts/qemu" / name) for name in scripts},
        "topology": {"link": ["server:192.0.2.1", "client:192.0.2.2"], "ip_forward": False,
                     "qemu_usb_devices": 0, "host_network_listener": False},
        "result": "FAIL",
    }
    processes = {}
    handles = []
    links = []
    try:
        with (evidence / "build.log").open("wb") as build_log:
            if not args.skip_build:
                subprocess.run(summary["build_command"], cwd=REPO, stdout=build_log, stderr=subprocess.STDOUT, check=True)
        summary["binary_sha256"] = {name: native.sha256(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        with tempfile.TemporaryDirectory(prefix="rustusb-userspace-qemu-") as temporary:
            work = Path(temporary)
            probe = work / "userspace-wire-probe"
            with (evidence / "build.log").open("ab") as build_log:
                subprocess.run(["rustc", "--edition=2024", "-O", "-D", "warnings",
                                str(REPO / "scripts/qemu/userspace-wire-probe.rs"), "-o", str(probe)],
                               cwd=REPO, stdout=build_log, stderr=subprocess.STDOUT, check=True)
            summary["probe_binary_sha256"] = native.sha256(probe)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            archive, summary["cdc_acm_module"] = userspace_initramfs(work, args.kernel, probe)
            summary["kernel_image_sha256"] = native.sha256(image)
            summary["initramfs_sha256"] = native.sha256(archive)
            summary["native_tool_sha256"] = {name: native.sha256(native.real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            server, client = socket.socketpair()
            links = [server, client]
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            for role, endpoint, mac in [("server", server, "31"), ("client", client, "32")]:
                # QEMU creates its file asynchronously after Popen returns.
                # Establish it before the first poll, including startup failures.
                (evidence / f"{role}-serial.log").touch(exist_ok=False)
                stderr = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(stderr)
                command = ["qemu-system-x86_64", "-m", "512", "-smp", "2", *acceleration,
                           "-kernel", str(image), "-initrd", str(archive),
                           "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.role={role}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=private,fd={endpoint.fileno()}",
                           "-device", f"e1000,id=private-nic,netdev=private,mac=52:54:00:00:00:{mac}"]
                processes[role] = subprocess.Popen(command, pass_fds=[endpoint.fileno()], stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.DEVNULL, stderr=stderr)
            for endpoint in links:
                endpoint.close()
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                logs = {role: (evidence / f"{role}-serial.log").read_text(errors="replace") for role in ROLES}
                if any("RUSTUSB_USERSPACE_RESULT: FAIL" in log for log in logs.values()):
                    raise RuntimeError("a userspace guest reported failure")
                if all(f"RUSTUSB_USERSPACE_RESULT: PASS role={role}" in logs[role] for role in ROLES):
                    break
                if all(process.poll() is not None for process in processes.values()):
                    raise RuntimeError("guests exited before complete userspace evidence")
                time.sleep(0.1)
            else:
                raise RuntimeError("userspace integration deadline exceeded")
            required = {
                "server": ["RUSTUSB_USERSPACE_SERVER_MODULES: PASS usbip_host=false vhci_hcd=false",
                           "RUSTUSB_USERSPACE_SERVER_UID: PASS uid=1000 app_root=false",
                           "RUSTUSB_USERSPACE_MALFORMED_SURVIVAL: PASS same_pid=true uid=1000 lease=0 pending=0",
                           "RUSTUSB_USERSPACE_STOPPED: PASS exit=0 lease=0 pending=0 same_pid=true"],
                "client": ["RUSTUSB_USERSPACE_UID_GATE: PASS denied_uid=1001 allowed_uid=1000"],
            }
            for cycle in [1, 2]:
                required["server"] += [f"RUSTUSB_USERSPACE_COUNTERS: PASS cycle={cycle} hid_taps={cycle} cdc_out={35 * cycle} cdc_in={70 * cycle} cdc_injected={35 * cycle} probe_bytes_excluded=16",
                                       f"RUSTUSB_USERSPACE_SERVER_RELEASED: PASS cycle={cycle} lease=0 pending=0 queues=0"]
                required["client"] += [f"RUSTUSB_USERSPACE_ATTACHED: PASS cycle={cycle} sessions=2 hid_driver=usbhid cdc_driver=cdc_acm cdc_interfaces=2",
                                       f"RUSTUSB_USERSPACE_HID: PASS cycle={cycle} key_code={cycle + 1} down=true up=true same_receipt=true",
                                       f"RUSTUSB_USERSPACE_CDC: PASS cycle={cycle} injected_in=35 out=35 echoed_in=35 exact_bytes=true tty_echo=false same_receipt=true",
                                       f"RUSTUSB_USERSPACE_CLIENT_RELEASED: PASS cycle={cycle} kernel_sessions=0 both_devices_removed=true",
                                       f"RUSTUSB_USERSPACE_REDISCOVERED: PASS cycle={cycle} available_profiles=2"]
            for role in ROLES:
                required[role].append(f"RUSTUSB_USERSPACE_ISOLATION role={role} ip_forward=0")
                for marker in required[role]:
                    if marker not in logs[role].splitlines():
                        raise RuntimeError(f"missing required {role} assertion: {marker}")
                if any(message in logs[role] for message in ["Kernel panic", "BUG:", "was already given back", "seqnum max"]):
                    raise RuntimeError(f"{role} kernel reported a USB/IP or kernel consistency failure")
                if processes[role].wait(timeout=10) != 0:
                    raise RuntimeError(f"{role} exited unsuccessfully")
            probes = [json.loads(line) for line in logs["client"].splitlines() if line.startswith('{"event":"probe_case"')]
            cases = {entry["case"] for entry in probes if entry.get("result") == "PASS"}
            required_cases = {"control_bound_and_invalid_interface_stall", "pending_unlink_suppresses_old_completion",
                              "cdc_bulk_out_and_bounded_in_fifo", "unknown_version", "submit_before_import", "truncated_handshake",
                              "duplicate_import", "oversized_out", "negative_length", "invalid_direction", "invalid_endpoint", "wrong_device", "iso_rejected",
                              "truncated_data_header", "all_protocol_checks", "both_profiles_exclusive_busy", "health_two_profiles"}
            if cases != required_cases or sum(entry["case"] == "both_profiles_exclusive_busy" for entry in probes) != 2:
                raise RuntimeError("independent protocol, malformed, busy or restored-profile checks incomplete")
            summary["protocol_probe_cases"] = probes
            events = parse_events(logs["server"])
            summary["server_observations"] = verify_server_events(events)
            summary["server_events"] = events
            if source_fingerprint() != summary["source_fingerprint_sha256"]:
                raise RuntimeError("source or scripts changed during userspace test")
            if any(native.sha256(REPO / "target/release" / name) != value for name, value in summary["binary_sha256"].items()):
                raise RuntimeError("release binaries changed during userspace test")
            summary["pass_markers_verified"] = True
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = str(error)
        raise
    finally:
        for endpoint in links:
            endpoint.close()
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
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        for role in ROLES:
            path = evidence / f"{role}-serial.log"
            if path.exists():
                summary[role + "_checks"] = [line.strip() for line in path.read_text(errors="replace").splitlines() if line.startswith("RUSTUSB_")]
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"result": summary["result"], "evidence": str(evidence)}, indent=2))


if __name__ == "__main__":
    main()
