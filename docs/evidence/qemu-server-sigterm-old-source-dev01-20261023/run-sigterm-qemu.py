#!/usr/bin/env python3
"""Verify cooperative SIGTERM with two live USBFS leases in disposable guests.

Only guest administrators signal the verified ordinary-user server. No host USB,
module, ptrace, tap, public listener or network configuration is used.
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
import struct
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
ROLES = ["server", "client"]
OWN_SCRIPTS = ["run-sigterm-qemu.py", "guest-sigterm-init.sh"]
spec = importlib.util.spec_from_file_location("physical_qemu", REPO / "scripts/qemu/run-physical-qemu.py")
physical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(physical)
native = physical.native
SCRIPTS = OWN_SCRIPTS + physical.OWN_SCRIPTS + ["run-native-qemu.py", "guest-init.sh"]
RX = b"RUSTUSB-RX-1-0001:0123456789abcdef\n"
TX = b"RUSTUSB-TX-1-0001:fedcba9876543210\n"
MODULE_CONFIG = {"usb-common": "USB_COMMON", "usbcore": "USB", "e1000": "E1000",
                 "evdev": "INPUT_EVDEV", "hid": "HID", "hid-generic": "HID_GENERIC",
                 "usbhid": "USB_HID", "usbserial": "USB_SERIAL", "ftdi_sio": "USB_SERIAL_FTDI_SIO",
                 "xhci-hcd": "USB_XHCI_HCD", "xhci-pci": "USB_XHCI_PCI",
                 "usbip-core": "USBIP_CORE", "vhci-hcd": "USBIP_VHCI_HCD", "unix_diag": "UNIX_DIAG"}


def source_fingerprint():
    digest = hashlib.sha256(bytes.fromhex(physical.source_fingerprint()))
    for name in OWN_SCRIPTS:
        data = (REPO / "scripts/qemu" / name).read_bytes()
        digest.update(name.encode())
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def initramfs(work, kernel, probe):
    archive, binaries = physical.physical_initramfs(work, kernel, probe)
    root = work / "root"
    shutil.copy2(REPO / "scripts/qemu/guest-sigterm-init.sh", root / "init")
    (root / "init").chmod(0o755)
    config_path = Path(f"/boot/config-{kernel}")
    config = dict(line.split("=", 1) for line in config_path.read_text().splitlines() if line.startswith("CONFIG_") and "=" in line)
    ss = shutil.which("ss")
    if ss is None:
        raise RuntimeError("ss is required for guest-only Unix listener backlog observation")
    native.copy_binary(ss, "/usr/bin/ss", root)
    if config.get("CONFIG_UNIX_DIAG") == "m":
        matches = list(Path(f"/lib/modules/{kernel}").rglob("unix_diag.ko*"))
        if len(matches) != 1:
            raise RuntimeError("one kernel-matching unix_diag module required")
        module = matches[0]
        if module.suffix == ".xz":
            data = native.lzma.decompress(module.read_bytes())
        elif module.suffix == ".gz":
            data = gzip.decompress(module.read_bytes())
        elif module.suffix == ".zst":
            data = subprocess.check_output(["zstd", "-d", "-c", str(module)])
        else:
            data = module.read_bytes()
        (root / "modules/unix_diag.ko").write_bytes(data)
    modules = {}
    for name, option in MODULE_CONFIG.items():
        mode = config.get("CONFIG_" + option)
        path = root / "modules" / f"{name}.ko"
        if mode not in ["y", "m"] or (mode == "m" and not path.is_file()):
            raise RuntimeError(f"required guest driver {name} unavailable: CONFIG_{option}={mode}")
        modules[name] = {"config": mode, "sha256": native.sha256(path) if path.is_file() else None}
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"],
                            input=names, cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(packed.stdout))
    return archive, binaries, {"config_sha256": native.sha256(config_path), "required": modules,
                               "copied_module_sha256": {path.name: native.sha256(path) for path in sorted((root / "modules").glob("*.ko"))}}


def read_channels(evidence):
    return physical.read_channels(evidence)


def json_block(log, tag):
    start, end = f"RUSTUSB_SIGTERM_{tag}_BEGIN", f"RUSTUSB_SIGTERM_{tag}_END"
    lines = log.splitlines()
    if lines.count(start) != 1 or lines.count(end) != 1 or lines.index(start) >= lines.index(end):
        raise ValueError(f"exact {tag} block missing")
    return [json.loads(line) for line in lines[lines.index(start) + 1:lines.index(end)]]


def backlog_contains(text, helper_pid, inode):
    """Match only the named helper's kernel listener backlog, not signal pipes."""
    for line in text.splitlines():
        fields = line.split()
        if (len(fields) >= 8 and fields[1] == "LISTEN" and fields[4] == "/run/rustusb/control.sock"
                and f"pid={helper_pid}," in line and "peers:" in fields):
            peers = fields[fields.index("peers:") + 1:]
            if str(inode) in peers:
                return True
    return False


def verify(program, kernel):
    """Keep independent observed checks even when the old server exits 143."""
    checks = []

    def check(name, condition):
        checks.append({"name": name, "result": "PASS" if condition else "FAIL"})

    outcomes = {}
    for role in ROLES:
        lines = program[role].splitlines()
        expected = f"RUSTUSB_SIGTERM_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0"
        check(f"{role}_one_evidence_channel", [line for line in lines if line.startswith("RUSTUSB_SIGTERM_EVIDENCE_CHANNEL:")] == [expected])
        check(f"{role}_collection_complete", lines.count(f"RUSTUSB_SIGTERM_COLLECTION_COMPLETE role={role}") == 1)
        check(f"{role}_guest_result", lines.count(f"RUSTUSB_SIGTERM_RESULT: PASS role={role}") == 1 and not any("RUSTUSB_SIGTERM_RESULT: FAIL" in log for log in [program[role], kernel[role]]))
        check(f"{role}_integrity", not any(text in log for log in [program[role], kernel[role]] for text in physical.INTEGRITY_FAILURES))
        check(f"{role}_isolation", f"RUSTUSB_SIGTERM_ISOLATION: PASS role={role} carrier=1 ip_forward=0" in lines)
        records = [json.loads(line.removeprefix("RUSTUSB_SIGTERM_OUTCOME ")) for line in lines if line.startswith("RUSTUSB_SIGTERM_OUTCOME ")]
        if len(records) != 1:
            raise ValueError(f"one exact {role} outcome required")
        outcome = outcomes[role] = records[0]
        check(f"{role}_schema", outcome.get("schema_version") == 1 and outcome.get("role") == role and outcome.get("event") == "sigterm_outcome")
        check(f"{role}_helper_still_alive", outcome.get("helper_alive") is True)
        check(f"{role}_observation_limit", outcome.get("observation_limit_seconds") == 20)
    server, client = outcomes["server"], outcomes["client"]
    startup_lines = [line.removeprefix("RUSTUSB_SIGTERM_STARTUP_OUTCOME ") for line in program["server"].splitlines() if line.startswith("RUSTUSB_SIGTERM_STARTUP_OUTCOME ")]
    if len(startup_lines) != 1:
        raise ValueError("one startup TERM outcome required")
    startup = json.loads(startup_lines[0])
    check("startup_schema", startup.get("schema_version") == 1 and startup.get("event") == "startup_term_outcome")
    check("startup_one_owned_child_term", startup.get("uid") == startup.get("sender_uid") == 1000 and startup.get("signal") == 15 and startup.get("signal_count") == 1 and startup.get("pid") != server.get("pid") and startup.get("pid", 0) > 1 and startup.get("starttime", 0) > 0)
    check("startup_real_helper_queue_not_signal_pipe", backlog_contains(startup["unix_ss_observation"], startup["helper_pid"], startup["connected_unix_inode"]) and any(len(fields) >= 7 and fields[4] == "0001" and fields[5] == "03" and fields[6] == startup["connected_unix_inode"] for fields in (line.split() for line in startup["unix_observation"].splitlines())))
    listeners = [line.split() for text in [startup["tcp_observation"], startup["tcp6_observation"]] for line in text.splitlines() if len(line.split()) >= 4 and line.split()[1].endswith(":0CA8") and line.split()[3] == "0A"]
    check("startup_stopped_helper_no_ready_or_listener", startup.get("helper_stopped_before_signal") is True and startup.get("ready_before_signal") is False and startup.get("listener_before_signal") is False and not listeners and startup.get("helper_acceptance_observed") is False)
    check("startup_wait_zero_within_three_seconds", startup.get("process_exit_observed") is True and startup.get("wait_code") == 0 and startup.get("observation_limit_seconds") == 3 and startup.get("observation_centiseconds", 10000) <= 300)
    check("startup_helper_resumed", startup.get("helper_continued") is True)
    check("exactly_one_term_owned_pid", server.get("signal") == 15 and server.get("signal_count") == 1 and server.get("sender_uid") == 1000 and server.get("pid", 0) > 1 and server.get("starttime", 0) > 0)
    check("stdin_not_shutdown_trigger", server.get("stdin_kept_open") is True and server.get("shutdown_control_sent") is False)
    check("actual_wait_zero", server.get("wait_code") == 0)
    check("cooperative_observation_within_twenty_seconds", server.get("within_observation_window") is True)
    check("process_and_fds_gone", all(server.get(key) is True for key in ["process_exit_observed", "pid_absent", "fd_table_absent"]))
    events = json_block(program["server"], "EVENTS")
    observations = json_block(program["server"], "OBSERVATIONS")
    check("all_event_schemas_no_error", bool(events) and all(event.get("schema_version") == 1 and event.get("event") != "error" for event in events))
    check("one_ready", sum(event.get("event") == "ready" for event in events) == 1)
    snapshots = {event["id"]: event["snapshot"] for event in events if event.get("event") == "snapshot"}
    check("snapshot_ids_unique", len(snapshots) == sum(event.get("event") == "snapshot" for event in events))
    before = snapshots[server["pre_snapshot_id"]]
    check("two_actual_pending_imports", before.get("buffer_bytes_reserved", 0) > 0 and len(before["devices"]) == 2 and all(device["leased"] and device["state"] == "leased" and device["pending_urbs"] >= 1 and device["usbfs_inflight"] >= 1 and all(driver["driver"] == "usbfs" for driver in device["current_drivers"]) for device in before["devices"]))
    check("real_control_bulk_interrupt", all(device["counters"]["controls"] > 0 for device in before["devices"]) and any(device["counters"]["interrupt_in"] >= 2 for device in before["devices"]) and any(device["counters"]["bulk_in"] > 0 and device["counters"]["bulk_out"] > 0 for device in before["devices"]))
    by_label = {observation["label"]: observation for observation in observations}
    check("exact_independent_observations", len(by_label) == len(observations) == 4 and set(by_label) == {"baseline", "held", "term-pending", "post-term"})
    original = by_label["baseline"]["sysfs"]
    check("startup_independent_original_sysfs_unchanged", startup.get("sysfs_unchanged") is True and startup.get("sysfs") == original)
    check("original_drivers_cfg_alt", len(original) == 2 and original[0]["interfaces"] == [{"number": 0, "alternate": 0, "driver": "usbhid"}] and original[1]["interfaces"] == [{"number": 0, "alternate": 0, "driver": "ftdi_sio"}] and all(device["configuration"] == 1 and device["node_owner_mode"] == "0:600" and device["generation"] and device["node_identity"] for device in original))
    for label in ["held", "term-pending"]:
        current = by_label[label]["sysfs"]
        check(f"{label}_independent_claims_and_generation", len(current) == 2 and all(current[index] == {**old, "interfaces": [{"number": 0, "alternate": 0, "driver": "usbfs"}]} for index, old in enumerate(original)))
        check(f"{label}_actual_usb_fds", all(any(fd["node"] == device["node"] and fd["identity"] == device["node_identity"] for fd in by_label[label]["fds"]) for device in original))
        identity = by_label[label]["identity"]
        check(f"{label}_owned_ordinary_identity", identity.get("pid") == server["pid"] and identity.get("starttime") == server["starttime"] and identity.get("exe") == "/usr/bin/rustusb" and identity.get("uids") == [1000] * 4 and all(identity.get(key) == 0 for key in ["cap_effective", "cap_permitted", "cap_ambient"]))
    check("independent_original_sysfs_restored", server.get("restored_sysfs") is True and by_label["post-term"]["sysfs"] == original)
    stopped = [event for event in events if event.get("event") == "stopped"]
    check("one_final_stopped", len(stopped) == 1 and events[-1].get("event") == "stopped")
    if len(stopped) == 1:
        after = stopped[0]["snapshot"]
        check("stopped_resources_and_drivers", after["buffer_bytes_reserved"] == 0 and len(after["devices"]) == 2 and all(device["state"] == "available" and not device["leased"] and device["cleanup_verified"] and device["pending_urbs"] == device["queued_urbs"] == device["usbfs_inflight"] == 0 and device["current_drivers"] == device["original_drivers"] for device in after["devices"]))
        old = {device["device_id"]: device for device in before["devices"]}
        check("real_pending_reaped_cancellation_counts_preserved", all(device["usbfs_reaped"] > old[device["device_id"]]["usbfs_reaped"] and device["cancelled"] >= old[device["device_id"]]["cancelled"] for device in after["devices"]))
    else:
        check("stopped_resources_and_drivers", False)
        check("real_pending_reaped_cancellation_counts_preserved", False)
    check("spontaneous_disconnect_before_detach", client.get("spontaneous_disconnect") is True and client.get("detach_before_observation") is False and client.get("hid_generation_gone") is True and client.get("ftdi_generation_gone") is True and client.get("sessions") == [])
    receipts = client["receipts"]
    check("two_distinct_complete_receipts", len(receipts) == 2 and receipts[0]["port"] != receipts[1]["port"] and receipts[0]["local_bus_id"] != receipts[1]["local_bus_id"] and all(receipt["status"] == 6 and receipt["local_device_address"] and receipt["local_device_generation"]["inode"] > 0 and receipt["local_device_generation"]["filesystem_device"] > 0 for receipt in receipts))
    rows = [line.split() for line in client["vhci_status"].splitlines() if line.split() and line.split()[0] in ["hs", "ss"]]
    check("raw_vhci_all_null_including_receipts", bool(rows) and len({row[1] for row in rows}) == len(rows) and all(len(row) == 7 and int(row[2]) == 4 and row[6] == "0-0" for row in rows) and all(str(receipt["port"]).zfill(4) in {row[1].zfill(4) for row in rows} for receipt in receipts))
    check("exact_serial_content_digests", client.get("serial_in_bytes") == len(RX) and client.get("serial_out_bytes") == len(TX) and client.get("rx_sha256") == hashlib.sha256(RX).hexdigest() and client.get("tx_sha256") == hashlib.sha256(TX).hexdigest())
    data = bytes.fromhex(client["input_event_hex"])
    input_events = list(struct.iter_unpack("<qqHHi", data)) if data and len(data) % 24 == 0 else []
    check("real_key_down_and_up", client.get("input_event_format") == "linux_x86_64_24" and any(event[2:] == (1, 2, 1) for event in input_events) and any(event[2:] == (1, 2, 0) for event in input_events))
    busy = [json.loads(line) for line in program["client"].splitlines() if line.startswith('{"event":"physical_probe_case"')]
    check("both_actual_devices_busy", busy == [{"event": "physical_probe_case", "case": "both_real_devices_exclusive_busy", "result": "PASS"}])
    check("authorization_and_identity_gates", "RUSTUSB_SIGTERM_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600" in program["server"].splitlines() and "RUSTUSB_SIGTERM_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false" in program["server"].splitlines())
    return {"checks": checks, "outcomes": outcomes, "startup_outcome": startup, "server_events": events, "server_observations": observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0 or os.uname().machine != "x86_64":
        parser.error("ordinary host UID and x86_64 required")
    if re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel) is None or not 60 <= args.timeout <= 1800:
        parser.error("safe kernel-release token and timeout 60..1800 required")
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO))
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": 1, "result": "FAIL", "started_at_utc": started,
               "scope": "separate startup helper-wait and two-live-lease SIGTERM observations of ordinary USBFS servers with QEMU HID/FTDI; not SIGKILL/hardware",
               "kernel": args.kernel, "git_commit": head, "working_tree_dirty": dirty,
               "host_uid": os.geteuid(), "host_architecture": os.uname().machine,
               "build_performed": True, "build_command": ["cargo", "build", "--workspace", "--release", "--locked", "--all-features"],
               "source_fingerprint_sha256": source_fingerprint(), "cargo_lock_sha256": native.sha256(REPO / "Cargo.lock"),
               "script_sha256": {name: native.sha256(REPO / "scripts/qemu" / name) for name in SCRIPTS},
               "qemu_version": subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip(),
               "rustc_version": subprocess.check_output(["rustc", "--version"], text=True).strip(),
               "topology": {"private_socketpair": ["server:192.0.2.1", "client:192.0.2.2"], "host_network_listener": False, "ip_forward": False},
               "output_channels": {role: {"kernel": f"{role}-serial.log", "program": f"{role}-evidence.log", "kernel_device": "ttyS0", "program_device": "ttyS1"} for role in ROLES}}
    processes, handles, links, injections = {}, [], [], set()
    qmp = serial = None
    received = bytearray()
    try:
        source_dir = evidence / "source"
        source_dir.mkdir()
        for name in SCRIPTS:
            shutil.copyfile(REPO / "scripts/qemu" / name, source_dir / name)
        with (evidence / "build.log").open("wb") as log:
            subprocess.run(summary["build_command"], cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True)
        summary["binary_sha256"] = {name: native.sha256(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        with tempfile.TemporaryDirectory(prefix="rustusb-sigterm-qemu-") as temporary:
            work = Path(temporary)
            probe = work / "physical-wire-probe"
            with (evidence / "build.log").open("ab") as log:
                subprocess.run(["rustc", "--edition=2024", "-O", "-D", "warnings", str(REPO / "scripts/qemu/physical-wire-probe.rs"), "-o", str(probe)], cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True)
            summary["probe_binary_sha256"] = native.sha256(probe)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            archive, summary["guest_binary_sha256"], summary["guest_modules"] = initramfs(work, args.kernel, probe)
            if summary["guest_binary_sha256"] != summary["binary_sha256"]:
                raise RuntimeError("binaries changed while building payload")
            summary["kernel_image_sha256"], summary["initramfs_sha256"] = native.sha256(image), native.sha256(archive)
            summary["native_tool_sha256"] = {name: native.sha256(native.real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            summary["guest_utility_sha256"] = {name: native.sha256(work / "root" / path) for name, path in {"busybox": "bin/busybox", "jq": "usr/bin/jq", "setpriv": "usr/bin/setpriv", "ss": "usr/bin/ss"}.items()}
            links = list(socket.socketpair())
            accel = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in accel else "tcg"
            for role, endpoint, mac in zip(ROLES, links, ["61", "62"]):
                for channel in ["serial", "evidence"]:
                    (evidence / f"{role}-{channel}.log").touch(exist_ok=False)
                handle = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(handle)
                command = ["qemu-system-x86_64", "-m", "512", "-smp", "2", *accel, "-kernel", str(image), "-initrd", str(archive),
                           "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.sigterm_test=1 rustusb.role={role}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}", "-serial", f"file:{evidence / (role + '-evidence.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=private,fd={endpoint.fileno()}",
                           "-device", f"e1000,netdev=private,mac=52:54:00:00:00:{mac}"]
                if role == "server":
                    command += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,id=term-kbd,bus=usb.0,port=1",
                                "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off", "-device", "usb-serial,id=term-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
                                "-qmp", f"unix:{work / 'qmp.sock'},server=on,wait=off"]
                summary.setdefault("qemu_commands", {})[role] = command
                processes[role] = subprocess.Popen(command, pass_fds=[endpoint.fileno()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=handle)
            for endpoint in links:
                endpoint.close()
            deadline = time.monotonic() + args.timeout
            qmp = physical.Qmp(work / "qmp.sock", deadline, processes["server"])
            serial = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            serial.settimeout(5)
            serial.connect(str(work / "ftdi.sock"))
            serial.setblocking(False)
            failure_started = None
            while time.monotonic() < deadline:
                program, kernel = read_channels(evidence)
                failed = any("RUSTUSB_SIGTERM_RESULT: FAIL" in log or any(message in log for message in physical.INTEGRITY_FAILURES) for role in ROLES for log in [program[role], kernel[role]])
                if failed and failure_started is None:
                    failure_started = time.monotonic()
                if "RUSTUSB_SIGTERM_WAIT_INPUT key=1" in program["client"] and "key-1" not in injections:
                    qmp.call("send-key", {"keys": [{"type": "qcode", "data": "1"}], "hold-time": 100})
                    injections.add("key-1")
                if "RUSTUSB_SIGTERM_WAIT_SERIAL bytes=35" in program["client"] and "serial-1" not in injections:
                    serial.settimeout(2)
                    serial.sendall(RX)
                    serial.setblocking(False)
                    injections.add("serial-1")
                while True:
                    try:
                        chunk = serial.recv(4096)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    received.extend(chunk)
                complete = all(f"RUSTUSB_SIGTERM_COLLECTION_COMPLETE role={role}" in program[role].splitlines() for role in ROLES)
                finished = all(process.poll() is not None for process in processes.values())
                if finished or (complete and all(f"RUSTUSB_SIGTERM_RESULT:" in program[role] for role in ROLES)):
                    break
                # Sticky failure gets bounded collection time, never a PASS retry.
                if failure_started is not None and time.monotonic() - failure_started >= 30:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("SIGTERM integration deadline exceeded")
            for process in processes.values():
                process.wait(timeout=10)
            program, kernel = read_channels(evidence)
            summary["verification"] = verify(program, kernel)
            summary["serial_output"] = {"expected_bytes": len(TX), "received_bytes": len(received), "sha256": hashlib.sha256(received).hexdigest(), "exact_match": received == TX}
            serial.settimeout(2)
            while True:
                chunk = serial.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
            summary["serial_output"] = {"expected_bytes": len(TX), "received_bytes": len(received), "sha256": hashlib.sha256(received).hexdigest(), "exact_match": received == TX}
            if failure_started is not None or any(check["result"] != "PASS" for check in summary["verification"]["checks"]) or received != TX or injections != {"key-1", "serial-1"} or any(process.returncode != 0 for process in processes.values()):
                raise RuntimeError("cooperative SIGTERM checks failed; original wait/status and recovery observations retained")
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = str(error)
    finally:
        if qmp is not None:
            summary["qmp_events"] = qmp.events
            qmp.close()
        if serial is not None:
            serial.close()
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
        summary["injections"] = sorted(injections)
        summary["serial_received_bytes"] = len(received)
        summary["serial_received_sha256"] = hashlib.sha256(received).hexdigest()
        summary["source_fingerprint_after_sha256"] = source_fingerprint()
        summary["source_guard_passed"] = summary["source_fingerprint_after_sha256"] == summary["source_fingerprint_sha256"]
        summary["binary_after_sha256"] = {name: native.sha256(REPO / "target/release" / name) for name in summary.get("binary_sha256", {})}
        summary["binary_guard_passed"] = bool(summary.get("binary_sha256")) and summary["binary_after_sha256"] == summary.get("binary_sha256")
        summary["git_commit_after"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        summary["working_tree_dirty_after"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO))
        if not summary["source_guard_passed"] or not summary["binary_guard_passed"] or summary["git_commit_after"] != head:
            summary["result"] = "FAIL"
            summary.setdefault("error", "runtime source, binary or commit guard failed")
        for role in ROLES:
            path = evidence / f"{role}-evidence.log"
            if path.exists():
                summary[role + "_checks"] = [line for line in path.read_text(errors="replace").splitlines() if line.startswith("RUSTUSB_")]
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"result": summary["result"], "evidence": str(evidence)}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
