#!/usr/bin/env python3
"""Verify ordinary-user usbfs forwarding of QEMU HID/FTDI devices to Linux VHCI.

Two disposable guests communicate through one inherited Unix socketpair. No
host USB, host module mutation, tap or public listener is used.
"""

import argparse
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import select
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
ROLES = ["server", "client"]
OWN_SCRIPTS = ["run-physical-qemu.py", "guest-physical-init.sh", "physical-wire-probe.rs"]
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
    vendor = REPO / "vendor"
    if vendor.is_dir():
        for path in sorted(vendor.rglob("*")):
            if path.is_file() and (path.suffix == ".rs" or path.name in ["Cargo.toml", "Cargo.lock"]):
                data = path.read_bytes()
                digest.update(str(path.relative_to(REPO)).encode())
                digest.update(len(data).to_bytes(8, "big"))
                digest.update(data)
    return digest.hexdigest()


def physical_initramfs(work, kernel, probe):
    """Reuse the native payload builder; add only this init and independent probe."""
    archive = native.initramfs(work, kernel)
    root = work / "root"
    shutil.copy2(REPO / "scripts/qemu/guest-physical-init.sh", root / "init")
    (root / "init").chmod(0o755)
    native.copy_binary(probe, "/usr/bin/physical-wire-probe", root)
    binaries = {name: native.sha256(root / "usr/bin" / name) for name in ["rustusb", "rustusb-helper"]}
    for name, digest in binaries.items():
        if digest != native.sha256(REPO / "target/release" / name):
            raise RuntimeError(f"{name} changed while copying the guest payload")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    result = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"],
                            input=names, cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(result.stdout))
    return archive, binaries


def parse_events(log):
    start = "RUSTUSB_PHYSICAL_EVENTS_BEGIN\n"
    stop = "RUSTUSB_PHYSICAL_EVENTS_END"
    if start not in log or stop not in log:
        raise RuntimeError("server JSONL evidence block missing")
    block = log.split(start, 1)[1].split(stop, 1)[0]
    events = [json.loads(line) for line in block.splitlines() if line.strip()]
    if not events or any(event.get("schema_version") != 1 or event.get("event") == "error" for event in events):
        raise RuntimeError("invalid server event schema or explicit server error")
    return events


def verify_server_events(events, hot_unplug):
    snapshots = {event["id"]: event["snapshot"] for event in events if event.get("event") == "snapshot"}
    expected = {"baseline", "held-1", "io-1", "released-1", "held-2", "io-2", "released-2"}
    if hot_unplug:
        expected |= {"unplug-held", "unplug-gone", "unplug-io", "unplug-released", "replug-stale"}
    if set(snapshots) != expected:
        raise RuntimeError("missing exact physical two-cycle snapshot evidence")
    stopped = [event for event in events if event.get("event") == "stopped"]
    if len(stopped) != 1 or events[-1].get("event") != "stopped":
        raise RuntimeError("physical server did not complete one final shutdown")
    def released(snapshot):
        if snapshot.get("schema_version") != 1 or snapshot["buffer_bytes_reserved"] != 0 or len(snapshot["devices"]) != 2:
            raise RuntimeError("physical snapshot schema, buffer budget or device count differs")
        for device in snapshot["devices"]:
            if (device["state"] != "available" or device["leased"] or device["pending_urbs"] or device["queued_urbs"]
                    or device["usbfs_inflight"] or not device["cleanup_verified"]
                    or device["current_drivers"] != device["original_drivers"]):
                raise RuntimeError("physical lease or original driver cleanup incomplete")
    for name in ["baseline", "released-1", "released-2"]:
        released(snapshots[name])
    if not hot_unplug:
        released(stopped[0]["snapshot"])
    else:
        for name in ["unplug-released", "replug-stale"]:
            snapshot = snapshots[name]
            if snapshot["buffer_bytes_reserved"] != 0:
                raise RuntimeError("removed physical device leaked buffer reservations")
            gone = [device for device in snapshot["devices"] if device["state"] == "gone"]
            available = [device for device in snapshot["devices"] if device["state"] == "available"]
            if len(gone) != 1 or len(available) != 1 or not available[0]["cleanup_verified"]:
                raise RuntimeError("hot unplug or stale-generation authorization state differs")
            if any(device["pending_urbs"] or device["queued_urbs"] or device["usbfs_inflight"] for device in snapshot["devices"]):
                raise RuntimeError("hot unplug leaked real or queued USB requests")
    baseline = snapshots["baseline"]["devices"]
    hid = next(device for device in baseline if any(driver["driver"] == "usbhid" for driver in device["original_drivers"]))
    ftdi = next(device for device in baseline if any(driver["driver"] == "ftdi_sio" for driver in device["original_drivers"]))
    if hid["cancelled"] < 2 or ftdi["cancelled"] < 1 or hid["usbfs_reaped"] < 4 or ftdi["usbfs_reaped"] < 4:
        raise RuntimeError("real UNLINK and EOF cancellation/reap evidence missing")
    for cycle in [1, 2]:
        for phase in ["held", "io"]:
            for device in snapshots[f"{phase}-{cycle}"]["devices"]:
                if device["state"] != "leased" or not device["leased"] or not all(driver["driver"] == "usbfs" for driver in device["current_drivers"]):
                    raise RuntimeError("physical imported devices were not really claimed by usbfs")
    return {"snapshots": snapshots, "final_snapshot": stopped[0]["snapshot"]}


class Qmp:
    """Retain asynchronous events instead of dropping them while awaiting replies."""
    def __init__(self, path, deadline, process):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        while not path.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("physical server QMP unavailable")
            time.sleep(0.05)
        self.socket.connect(str(path))
        self.buffer = b""
        self.messages = []
        self.events = []
        self.identifier = 0
        greeting_deadline = time.monotonic() + 5
        while not self.messages and time.monotonic() < greeting_deadline:
            self.read(max(0, greeting_deadline - time.monotonic()))
        if not any("QMP" in message for message in self.messages):
            raise RuntimeError("QMP greeting missing")
        self.messages.clear()
        self.call("qmp_capabilities")

    def read(self, timeout):
        if not select.select([self.socket], [], [], timeout)[0]:
            return
        chunk = self.socket.recv(65536)
        if not chunk:
            raise RuntimeError("QMP closed unexpectedly")
        self.buffer += chunk
        if len(self.buffer) > 1048576:
            raise RuntimeError("QMP framing exceeds bound")
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            message = json.loads(line)
            if "event" in message:
                self.events.append(message)
            else:
                self.messages.append(message)

    def call(self, command, arguments=None):
        self.identifier += 1
        identifier = self.identifier
        self.socket.sendall(json.dumps({"execute": command, "arguments": arguments or {}, "id": identifier}).encode() + b"\n")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for index, message in enumerate(self.messages):
                if message.get("id") == identifier:
                    self.messages.pop(index)
                    if "error" in message:
                        raise RuntimeError(f"QMP {command} failed: {message['error']}")
                    return message.get("return")
            self.read(max(0, deadline - time.monotonic()))
        raise RuntimeError(f"QMP {command} timed out")

    def close(self):
        self.socket.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence requires the default locked build")
    parser.add_argument("--hot-unplug", action="store_true", help="also remove HID during active I/O, continue FTDI and reject automatic reauthorization on reinsertion")
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
    evidence = (args.evidence or REPO / "docs/evidence" / f"qemu-physical-{timestamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    scripts = OWN_SCRIPTS + ["run-native-qemu.py", "guest-init.sh"]
    summary = {
        "started_at_utc": timestamp,
        "scope": "ordinary UID1000 helper-authorized nusb/usbfs server over QEMU HID/FTDI devices to Linux VHCI; no host USB",
        "kernel": args.kernel, "host_uid": os.geteuid(), "host_architecture": os.uname().machine,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "qemu_version": subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip(),
        "rustc_version": subprocess.check_output(["rustc", "--version"], text=True).strip(),
        "build_performed": not args.skip_build,
        "build_command": ["cargo", "build", "--workspace", "--release", "--locked", "--all-features"],
        "cargo_lock_sha256": native.sha256(REPO / "Cargo.lock"),
        "source_fingerprint_sha256": source_fingerprint(),
        "script_sha256": {name: native.sha256(REPO / "scripts/qemu" / name) for name in scripts},
        "topology": {"link": ["server:192.0.2.1", "client:192.0.2.2"], "ip_forward": False,
                     "qemu_usb_devices": 2, "host_network_listener": False},
        "result": "FAIL",
        "hot_unplug_requested": args.hot_unplug,
    }
    processes = {}
    qmp = None
    serial_socket = None
    serial_received = bytearray()
    injections = set()
    handles = []
    links = []
    try:
        with (evidence / "build.log").open("wb") as build_log:
            if not args.skip_build:
                subprocess.run(summary["build_command"], cwd=REPO, stdout=build_log, stderr=subprocess.STDOUT, check=True)
        summary["binary_sha256"] = {name: native.sha256(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        with tempfile.TemporaryDirectory(prefix="rustusb-physical-qemu-") as temporary:
            work = Path(temporary)
            probe = work / "physical-wire-probe"
            with (evidence / "build.log").open("ab") as build_log:
                subprocess.run(["rustc", "--edition=2024", "-O", "-D", "warnings",
                                str(REPO / "scripts/qemu/physical-wire-probe.rs"), "-o", str(probe)],
                               cwd=REPO, stdout=build_log, stderr=subprocess.STDOUT, check=True)
            summary["probe_binary_sha256"] = native.sha256(probe)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            archive, summary["guest_binary_sha256"] = physical_initramfs(work, args.kernel, probe)
            if summary["guest_binary_sha256"] != summary["binary_sha256"]:
                raise RuntimeError("release binaries changed between fingerprinting and guest copy")
            summary["kernel_image_sha256"] = native.sha256(image)
            summary["initramfs_sha256"] = native.sha256(archive)
            summary["native_tool_sha256"] = {name: native.sha256(native.real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            server, client = socket.socketpair()
            links = [server, client]
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            qmp_path = work / "server-qmp.sock"
            for role, endpoint, mac in [("server", server, "31"), ("client", client, "32")]:
                # QEMU creates its file asynchronously after Popen returns.
                # Establish it before the first poll, including startup failures.
                (evidence / f"{role}-serial.log").touch(exist_ok=False)
                stderr = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(stderr)
                command = ["qemu-system-x86_64", "-m", "512", "-smp", "2", *acceleration,
                           "-kernel", str(image), "-initrd", str(archive),
                           "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.role={role} rustusb.hot_unplug={int(args.hot_unplug)}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=private,fd={endpoint.fileno()}",
                           "-device", f"e1000,id=private-nic,netdev=private,mac=52:54:00:00:00:{mac}"]
                if role == "server":
                    command += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,id=physical-kbd,bus=usb.0,port=1",
                                "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off",
                                "-device", "usb-serial,id=physical-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
                                "-qmp", f"unix:{qmp_path},server=on,wait=off"]
                processes[role] = subprocess.Popen(command, pass_fds=[endpoint.fileno()], stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.DEVNULL, stderr=stderr)
            for endpoint in links:
                endpoint.close()
            deadline = time.monotonic() + args.timeout
            qmp = Qmp(qmp_path, deadline, processes["server"])
            serial_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            serial_socket.settimeout(5)
            serial_socket.connect(str(work / "ftdi.sock"))
            serial_socket.setblocking(False)
            serial_cycles = [1, 2, 3, 4] if args.hot_unplug else [1, 2]
            expected_serial = b"".join(f"RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n".encode() for cycle in serial_cycles)
            while time.monotonic() < deadline:
                logs = {role: (evidence / f"{role}-serial.log").read_text(errors="replace") for role in ROLES}
                if "RUSTUSB_PHYSICAL_PROBE_WAIT_INPUT key=3" in logs["client"] and "probe" not in injections:
                    qmp.call("send-key", {"keys": [{"type": "qcode", "data": "3"}], "hold-time": 100})
                    injections.add("probe")
                if "RUSTUSB_PHYSICAL_PROBE_WAIT_SERIAL bytes=13" in logs["client"] and "probe-serial" not in injections:
                    serial_socket.settimeout(2)
                    serial_socket.sendall(b"RUSTUSB-P15!!")
                    serial_socket.setblocking(False)
                    injections.add("probe-serial")
                for cycle in [1, 2]:
                    if f"RUSTUSB_PHYSICAL_WAIT_INPUT cycle={cycle}" in logs["client"] and f"key-{cycle}" not in injections:
                        qmp.call("send-key", {"keys": [{"type": "qcode", "data": str(cycle)}], "hold-time": 100})
                        injections.add(f"key-{cycle}")
                for cycle in serial_cycles:
                    if f"RUSTUSB_PHYSICAL_WAIT_SERIAL cycle={cycle}" in logs["client"] and f"serial-{cycle}" not in injections:
                        serial_socket.settimeout(2)
                        serial_socket.sendall(f"RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n".encode())
                        serial_socket.setblocking(False)
                        injections.add(f"serial-{cycle}")
                if args.hot_unplug and "RUSTUSB_PHYSICAL_WAIT_UNPLUG device=physical-kbd" in logs["client"] and "unplug" not in injections:
                    qmp.call("device_del", {"id": "physical-kbd"})
                    injections.add("unplug")
                deleted = any(event.get("event") == "DEVICE_DELETED" and event.get("data", {}).get("device") == "physical-kbd" for event in qmp.events)
                if "unplug" in injections and not deleted:
                    qmp.read(0)
                    deleted = any(event.get("event") == "DEVICE_DELETED" and event.get("data", {}).get("device") == "physical-kbd" for event in qmp.events)
                if args.hot_unplug and deleted and "RUSTUSB_PHYSICAL_WAIT_REINSERT device=physical-kbd" in logs["client"] and "reinsert" not in injections:
                    qmp.call("device_add", {"driver": "usb-kbd", "id": "physical-kbd", "bus": "usb.0", "port": "1"})
                    injections.add("reinsert")
                while True:
                    try:
                        chunk = serial_socket.recv(4096)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    serial_received.extend(chunk)
                    if not expected_serial.startswith(serial_received):
                        raise RuntimeError("unexpected or out-of-order bytes reached host FTDI chardev")
                if any("RUSTUSB_PHYSICAL_RESULT: FAIL" in log for log in logs.values()):
                    raise RuntimeError("a physical guest reported failure")
                if all(f"RUSTUSB_PHYSICAL_RESULT: PASS role={role}" in logs[role] for role in ROLES) and serial_received == expected_serial:
                    break
                if all(process.poll() is not None for process in processes.values()):
                    raise RuntimeError("guests exited before complete physical evidence")
                time.sleep(0.1)
            else:
                raise RuntimeError("physical integration deadline exceeded")
            required = {
                "server": ["RUSTUSB_PHYSICAL_SERVER_MODULES: PASS usbip_host=false vhci_hcd=false",
                           "RUSTUSB_PHYSICAL_SERVER_UID: PASS uid=1000 app_root=false capabilities=0",
                           "RUSTUSB_PHYSICAL_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied nodes=root0600",
                           "RUSTUSB_PHYSICAL_HELPER_UID_GATE: PASS denied_uid=1001 allowed_uid=1000",
                           "RUSTUSB_PHYSICAL_OPEN_FD_DENIED: PASS uid=1001 route=open_physical json_refusal=true eof=true",
                           "RUSTUSB_PHYSICAL_PROBE_CLEANUP: PASS original_drivers=true inflight=0"],
                "client": ["RUSTUSB_PHYSICAL_UID_GATE: PASS denied_uid=1001 allowed_uid=1000"],
            }
            for cycle in [1, 2]:
                required["server"] += [f"RUSTUSB_PHYSICAL_SERVER_HELD: PASS cycle={cycle} drivers=usbfs leases=2",
                                       f"RUSTUSB_PHYSICAL_SERVER_RELEASED: PASS cycle={cycle} inflight=0 drivers=usbhid,ftdi_sio"]
                required["client"] += [f"RUSTUSB_PHYSICAL_ATTACHED: PASS cycle={cycle} sessions=2 hid_driver=usbhid serial_driver=ftdi_sio",
                                       f"RUSTUSB_PHYSICAL_HID: PASS cycle={cycle} key_code={cycle+1} down=true up=true same_receipt=true",
                                       f"RUSTUSB_PHYSICAL_FTDI: PASS cycle={cycle} in=35 out=35 exact_bytes=true tty_echo=false same_receipt=true",
                                       f"RUSTUSB_PHYSICAL_CLIENT_RELEASED: PASS cycle={cycle} kernel_sessions=0 both_devices_removed=true",
                                       f"RUSTUSB_PHYSICAL_REDISCOVERED: PASS cycle={cycle} available_devices=2"]
            if args.hot_unplug:
                required["server"] += ["RUSTUSB_PHYSICAL_GONE: PASS old_hid_gone=true ftdi_leased=true inflight_hid=0",
                                       "RUSTUSB_PHYSICAL_REINSERT_STALE: PASS new_generation=true old_slot_gone=true nodes=root0600",
                                       "RUSTUSB_PHYSICAL_STOPPED: PASS exit=0 ftdi_driver=ftdi_sio gone_hid=true inflight=0"]
                required["client"] += ["RUSTUSB_PHYSICAL_UNPLUG: PASS hid_kernel_gone=true ftdi_same_receipt=true",
                                       "RUSTUSB_PHYSICAL_FTDI_CONTINUES: PASS before=35 after=35 exact_bytes=true same_receipt=true",
                                       "RUSTUSB_PHYSICAL_NO_AUTO_AUTH: PASS reinserted_hid_not_advertised=true available_devices=1"]
                if not deleted:
                    raise RuntimeError("QMP DEVICE_DELETED completion missing")
            else:
                required["server"].append("RUSTUSB_PHYSICAL_STOPPED: PASS exit=0 original_drivers=true inflight=0")
            for role in ROLES:
                required[role].append(f"RUSTUSB_PHYSICAL_ISOLATION role={role} ip_forward=0")
                for marker in required[role]:
                    if marker not in logs[role].splitlines():
                        raise RuntimeError(f"missing required {role} assertion: {marker}")
                if any(message in logs[role] for message in ["Kernel panic", "BUG:", "was already given back", "seqnum max"]):
                    raise RuntimeError(f"{role} kernel reported an integrity failure")
                if processes[role].wait(timeout=10) != 0:
                    raise RuntimeError(f"{role} exited unsuccessfully")
            # Both QEMUs have closed their descriptors. Drain through EOF so
            # late output after a guest PASS cannot evade the exact-byte check.
            serial_socket.settimeout(2)
            while True:
                chunk = serial_socket.recv(4096)
                if not chunk:
                    break
                serial_received.extend(chunk)
                if not expected_serial.startswith(serial_received):
                    raise RuntimeError("extra FTDI output arrived during guest shutdown")
            probes = [json.loads(line) for line in logs["client"].splitlines() if line.startswith('{"event":"physical_probe_case"')]
            cases = {entry["case"] for entry in probes if entry.get("result") == "PASS"}
            expected_cases = {"real_control_descriptor_and_speed", "real_unlink_reaped_no_old_completion_or_input_loss",
                              "real_unsupported_control_stall_and_survival",
                              "real_configuration_barrier_preserves_following_in",
                              "real_zero_bulk_out_actual_zero", "real_one_byte_bulk_in_pending_cancelled",
                              "real_zero_bulk_in_original_length", "real_non_mps_fifteen_byte_bulk_in",
                              "real_pending_eof_releases_lease", "real_ftdi_descriptor_and_speed", "all_lifecycle_checks",
                              "both_real_devices_exclusive_busy", "both_real_devices_available"}
            if cases != expected_cases or sum(entry["case"] == "both_real_devices_exclusive_busy" for entry in probes) != 2:
                raise RuntimeError("real usbfs lifecycle or exclusivity checks incomplete")
            summary["protocol_probe_cases"] = probes
            summary["server_events"] = parse_events(logs["server"])
            summary["server_observations"] = verify_server_events(summary["server_events"], args.hot_unplug)
            if serial_received != expected_serial:
                raise RuntimeError("host FTDI output differs from both exact frames")
            expected_injections = {"probe", "probe-serial", "key-1", "key-2", "serial-1", "serial-2"}
            if args.hot_unplug:
                expected_injections |= {"serial-3", "serial-4", "unplug", "reinsert"}
            if injections != expected_injections:
                raise RuntimeError("required real HID/FTDI injections incomplete")
            summary["serial_output"] = {"expected_bytes": len(expected_serial), "received_bytes": len(serial_received),
                                        "sha256": hashlib.sha256(serial_received).hexdigest(), "exact_match": True}
            summary["serial_input_ledger"] = {"raw_probe_payload_bytes": 13, "raw_probe_usb_status_bytes": 2,
                                               "kernel_serial_payload_bytes": 35 * len(serial_cycles)}
            if source_fingerprint() != summary["source_fingerprint_sha256"]:
                raise RuntimeError("source or scripts changed during physical test")
            if any(native.sha256(REPO / "target/release" / name) != value for name, value in summary["binary_sha256"].items()):
                raise RuntimeError("release binaries changed during physical test")
            summary["pass_markers_verified"] = True
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = str(error)
        raise
    finally:
        if qmp is not None:
            summary["qmp_events"] = qmp.events
            qmp.close()
        if serial_socket is not None:
            serial_socket.close()
        summary["injections"] = sorted(injections)
        summary["serial_received_bytes"] = len(serial_received)
        summary["serial_received_sha256"] = hashlib.sha256(serial_received).hexdigest()
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
