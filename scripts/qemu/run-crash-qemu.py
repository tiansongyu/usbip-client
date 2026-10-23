#!/usr/bin/env python3
"""Collect one owned SIGKILL and the first no-IMPORT restart in isolated guests.

Evidence collection and product recovery are separate verdicts. An expected
negative recovery result remains FAIL; absent killed-epoch snapshots stay absent.
Only disposable guest kernels access the simulated HID and FTDI devices.
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
OWN_SCRIPTS = ["run-crash-qemu.py", "guest-crash-init.sh"]
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
                 "usbip-core": "USBIP_CORE", "vhci-hcd": "USBIP_VHCI_HCD"}


def source_fingerprint():
    digest = hashlib.sha256(bytes.fromhex(physical.source_fingerprint()))
    for name in OWN_SCRIPTS:
        data = (REPO / "scripts/qemu" / name).read_bytes()
        digest.update(name.encode())
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def initramfs(work, kernel, probe):
    """Reuse the physical payload builder, preserving its helper/probe binaries."""
    archive, binaries = physical.physical_initramfs(work, kernel, probe)
    root = work / "root"
    shutil.copy2(REPO / "scripts/qemu/guest-crash-init.sh", root / "init")
    (root / "init").chmod(0o755)
    config_path = Path(f"/boot/config-{kernel}")
    config = dict(line.split("=", 1) for line in config_path.read_text().splitlines()
                  if line.startswith("CONFIG_") and "=" in line)
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


def json_block(log, tag):
    """Parse exact machine-channel delimiters without stripping ANSI or payload."""
    start, end = f"RUSTUSB_CRASH_{tag}_BEGIN", f"RUSTUSB_CRASH_{tag}_END"
    lines = log.splitlines()
    if lines.count(start) != 1 or lines.count(end) != 1 or lines.index(start) >= lines.index(end):
        raise ValueError(f"exact {tag} block missing")
    return [json.loads(line) for line in lines[lines.index(start) + 1:lines.index(end)]]


def one_record(log, prefix):
    records = [json.loads(line[len(prefix):]) for line in log.splitlines() if line.startswith(prefix)]
    if len(records) != 1:
        raise ValueError(f"one exact {prefix.strip()} record required")
    return records[0]


def zero_resources(snapshot):
    return (snapshot.get("schema_version") == 1 and snapshot.get("buffer_bytes_reserved") == 0
            and len(snapshot.get("devices", [])) == 2
            and all(device.get("state") == "available" and device.get("leased") is False
                    and device.get("cleanup_verified") is True
                    and all(device.get(key) == 0 for key in ["pending_urbs", "queued_urbs", "usbfs_inflight"])
                    and device.get("current_drivers") == device.get("original_drivers")
                    for device in snapshot["devices"]))


def verify(program, kernel):
    """Evaluate facts and recovery separately, retaining negative raw observations."""
    checks = []

    def check(category, name, condition):
        checks.append({"category": category, "name": name, "result": "PASS" if condition else "FAIL"})

    outcomes = {}
    complete = True
    for role in ROLES:
        lines = program[role].splitlines()
        expected = f"RUSTUSB_CRASH_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0"
        check("fixture", f"{role}_one_evidence_channel", [line for line in lines if line.startswith("RUSTUSB_CRASH_EVIDENCE_CHANNEL:")] == [expected])
        collected = lines.count(f"RUSTUSB_CRASH_COLLECTION_COMPLETE role={role}") == 1
        complete = complete and collected
        check("fixture", f"{role}_collection_complete", collected)
        check("fixture", f"{role}_primary_complete", lines.count(f"RUSTUSB_CRASH_PRIMARY_EVIDENCE_COMPLETE role={role}") == 1)
        check("recovery", f"{role}_guest_recovery_verdict", lines.count(f"RUSTUSB_CRASH_RESULT: PASS role={role}") == 1 and not any("RUSTUSB_CRASH_RESULT: FAIL" in log for log in [program[role], kernel[role]]))
        check("fixture", f"{role}_integrity", not any(text in log for log in [program[role], kernel[role]] for text in physical.INTEGRITY_FAILURES))
        check("fixture", f"{role}_isolation", f"RUSTUSB_CRASH_ISOLATION: PASS role={role} carrier=1 ip_forward=0" in lines)
        outcome = outcomes[role] = one_record(program[role], "RUSTUSB_CRASH_OUTCOME ")
        check("fact", f"{role}_schema", outcome.get("schema_version") == 1 and outcome.get("role") == role and outcome.get("event") == "crash_outcome")
        check("fact", f"{role}_helper_alive", outcome.get("helper_alive") is True)
        check("fact", f"{role}_observation_limit", outcome.get("observation_limit_seconds") == 20)
    server, client = outcomes["server"], outcomes["client"]
    restart = one_record(program["server"], "RUSTUSB_CRASH_RESTART_OUTCOME ")
    initial_events = json_block(program["server"], "INITIAL_EVENTS")
    restart_events = json_block(program["server"], "RESTART_EVENTS")
    observations = json_block(program["server"], "OBSERVATIONS")
    by_label = {entry["label"]: entry for entry in observations}
    labels = {"baseline", "held", "pre-crash", "post-crash", "post-restart"}
    restart_labels = {"restart-ready", "restart-rejected", "restart-timeout"}
    check("fixture", "exact_independent_observations", len(by_label) == len(observations) == 6 and labels <= set(by_label) and len(set(by_label) & restart_labels) == 1)
    original = by_label["baseline"]["sysfs"]
    original_by_id = {device["device_id"]: device for device in original}
    check("fact", "original_drivers_cfg_alt_and_protected_nodes", len(original) == 2
          and original[0]["interfaces"] == [{"number": 0, "alternate": 0, "driver": "usbhid"}]
          and original[1]["interfaces"] == [{"number": 0, "alternate": 0, "driver": "ftdi_sio"}]
          and all(device["configuration"] == 1 and device["node_owner_mode"] == "0:600" and device["generation"] and device["node_identity"] for device in original))
    check("fixture", "authorization_and_identity_gates", "RUSTUSB_CRASH_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600" in program["server"].splitlines()
          and "RUSTUSB_CRASH_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false" in program["server"].splitlines())
    check("fact", "initial_event_schemas", bool(initial_events) and all(event.get("schema_version") == 1 and event.get("event") != "error" for event in initial_events))
    check("fact", "one_initial_ready", sum(event.get("event") == "ready" for event in initial_events) == 1)
    check("fact", "killed_epoch_has_no_fabricated_stopped", not any(event.get("event") == "stopped" for event in initial_events)
          and server.get("final_backend_snapshot") is None and server.get("final_backend_snapshot_observable") is False
          and by_label["post-crash"].get("snapshot") is None and by_label["post-crash"].get("snapshot_observable") is False)
    snapshots = {event["id"]: event["snapshot"] for event in initial_events if event.get("event") == "snapshot"}
    check("fixture", "initial_snapshot_ids_unique", len(snapshots) == sum(event.get("event") == "snapshot" for event in initial_events))
    check("fact", "initial_baseline_resources", zero_resources(snapshots["baseline"]))
    before = snapshots[server["pre_snapshot_id"]]
    check("fact", "two_real_pending_usbfs_imports", before.get("buffer_bytes_reserved", 0) > 0 and len(before["devices"]) == 2
          and all(device["state"] == "leased" and device["leased"] and device["pending_urbs"] >= 1 and device["usbfs_inflight"] >= 1
                  and all(driver["driver"] == "usbfs" for driver in device["current_drivers"]) for device in before["devices"]))
    check("fact", "real_control_bulk_interrupt", all(device["counters"]["controls"] > 0 for device in before["devices"])
          and any(device["counters"]["interrupt_in"] >= 2 for device in before["devices"])
          and any(device["counters"]["bulk_in"] > 0 and device["counters"]["bulk_out"] > 0 for device in before["devices"]))
    for label in ["held", "pre-crash"]:
        observation = by_label[label]
        current = observation["sysfs"]
        check("fact", f"{label}_actual_claims_same_generation", len(current) == 2 and all(current[index] == {**old, "interfaces": [{"number": 0, "alternate": 0, "driver": "usbfs"}]} for index, old in enumerate(original)))
        identity = observation["identity"]
        check("fact", f"{label}_owned_uid_and_pid", identity.get("pid") == server["pid"] and identity.get("starttime") == server["starttime"]
              and identity.get("exe") == "/usr/bin/rustusb" and identity.get("uids") == [1000] * 4
              and all(identity.get(key) == 0 for key in ["cap_effective", "cap_permitted", "cap_ambient"]))
        check("fact", f"{label}_actual_usb_fds", all(any(fd["node"] == device["node"] and fd["identity"] == device["node_identity"] for fd in identity["fds"]) for device in original))
    check("fact", "one_owned_sigkill", server.get("signal") == 9 and server.get("signal_count") == 1 and server.get("sender_uid") == 1000 and server.get("pid", 0) > 1 and server.get("starttime", 0) > 0)
    check("fact", "actual_wait137_and_process_fds_gone", server.get("wait_code") == 137 and all(server.get(key) is True for key in ["process_exit_observed", "pid_absent", "fd_table_absent"]))
    check("fact", "stdin_not_shutdown_trigger", server.get("stdin_kept_open") is True and server.get("shutdown_before_crash") is False)
    check("recovery", "original_sysfs_restored_after_kill", server.get("restored_sysfs") is True and server.get("within_observation_window") is True and by_label["post-crash"]["sysfs"] == original)
    for label in ["post-crash", "post-restart"]:
        check("fact", f"{label}_unchanged_device_generation_and_node", len(by_label[label]["sysfs"]) == 2
              and all({key: value for key, value in device.items() if key not in ["interfaces", "configuration"]}
                      == {key: value for key, value in original[index].items() if key not in ["interfaces", "configuration"]}
                      for index, device in enumerate(by_label[label]["sysfs"])))
    check("fact", "client_spontaneous_cleanup_before_detach", client.get("spontaneous_disconnect") is True and client.get("detach_before_observation") is False
          and client.get("hid_generation_gone") is True and client.get("ftdi_generation_gone") is True and client.get("sessions") == [])
    receipts = client["receipts"]
    check("fact", "two_distinct_complete_receipts", len(receipts) == 2 and receipts[0]["port"] != receipts[1]["port"] and receipts[0]["local_bus_id"] != receipts[1]["local_bus_id"]
          and all(receipt["status"] == 6 and receipt["local_device_address"] and receipt["local_device_generation"]["inode"] > 0 and receipt["local_device_generation"]["filesystem_device"] > 0 for receipt in receipts))
    rows = [line.split() for line in client["vhci_status"].splitlines() if line.split() and line.split()[0] in ["hs", "ss"]]
    check("fact", "raw_vhci_null_including_receipts", bool(rows) and len({row[1] for row in rows}) == len(rows)
          and all(len(row) == 7 and int(row[2]) == 4 and row[6] == "0-0" for row in rows)
          and all(str(receipt["port"]).zfill(4) in {row[1].zfill(4) for row in rows} for receipt in receipts))
    check("fact", "exact_serial_rx_and_tx_fixture", client.get("rx_hex") == RX.hex() and client.get("serial_in_bytes") == len(RX) and client.get("serial_out_bytes") == len(TX)
          and client.get("rx_sha256") == hashlib.sha256(RX).hexdigest() and client.get("tx_sha256") == hashlib.sha256(TX).hexdigest())
    data = bytes.fromhex(client["input_event_hex"])
    input_events = list(struct.iter_unpack("<qqHHi", data)) if data and len(data) % 24 == 0 else []
    check("fact", "actual_key1_down_up", client.get("input_event_format") == "linux_x86_64_24" and any(event[2:] == (1, 2, 1) for event in input_events) and any(event[2:] == (1, 2, 0) for event in input_events))
    busy = [json.loads(line) for line in program["client"].splitlines() if line.startswith('{"event":"physical_probe_case"')]
    check("fact", "both_actual_devices_exclusive_busy", busy == [{"event": "physical_probe_case", "case": "both_real_devices_exclusive_busy", "result": "PASS"}])
    check("fact", "one_first_restart_no_import", restart.get("schema_version") == 1 and restart.get("event") == "restart_outcome" and restart.get("epoch") == "restart"
          and restart.get("attempted") is True and restart.get("attempt_count") == 1 and restart.get("pid", 0) > 1 and restart.get("pid") != server["pid"]
          and restart.get("new_imports_sent") is False and client.get("new_imports_after_crash") is False and restart.get("helper_alive") is True)
    check("fact", "restart_explicit_bounded_outcome", restart.get("startup_limit_seconds") == 10 and restart.get("shutdown_limit_seconds") == 20
          and isinstance(restart.get("ready_observed"), bool) and isinstance(restart.get("process_exit_observed"), bool))
    check("fact", "restart_event_schemas", all(event.get("schema_version") == 1 for event in restart_events))
    ready = [event for event in restart_events if event.get("event") == "ready"]
    stopped = [event for event in restart_events if event.get("event") == "stopped"]
    check("recovery", "first_restart_ready_owned_shutdown_zero", restart.get("ready_observed") is True and restart.get("identity_verified") is True
          and restart.get("shutdown_control_attempted") is True and restart.get("shutdown_ack_observed") is True
          and restart.get("process_exit_observed") is True and restart.get("wait_code") == 0 and len(ready) == 1)
    # A new epoch's zero counters are retained separately; never substitute them
    # for unavailable killed-epoch terminal, reaping or original-driver evidence.
    if len(ready) == 1:
        current = by_label["restart-ready"]
        identity = current["identity"]
        check("fact", "restart_actual_identity_and_fds", identity is not None and identity.get("pid") == restart["pid"] and identity.get("starttime") == restart["starttime"]
              and identity.get("exe") == "/usr/bin/rustusb" and identity.get("uids") == [1000] * 4
              and all(identity.get(key) == 0 for key in ["cap_effective", "cap_permitted", "cap_ambient"])
              and all(any(fd["node"] == device["node"] and fd["identity"] == device["node_identity"] for fd in identity["fds"]) for device in original))
        fresh = ready[0]["snapshot"]
        check("fact", "restart_only_fresh_zero_import_epoch", zero_resources(fresh) and all(device["usbfs_reaped"] == device["cancelled"] == 0
              and all(value == 0 for value in device["counters"].values()) for device in fresh["devices"]))
        check("recovery", "restart_original_still_matches_initial_baseline", len(fresh["devices"]) == 2
              and {device["device_id"] for device in fresh["devices"]} == set(original_by_id)
              and all(device["original_drivers"] == [{"interface": 0, "driver": original_by_id[device["device_id"]]["interfaces"][0]["driver"]}]
                      and device["current_drivers"] == device["original_drivers"] for device in fresh["devices"])
              and current["sysfs"] == original)
    else:
        check("fact", "restart_refusal_preserved_without_fake_ready", not ready and restart.get("ready_observed") is False and "restart-ready" not in by_label
              and restart.get("identity_verified") is False and restart.get("shutdown_control_attempted") is False)
        check("recovery", "restart_original_still_matches_initial_baseline", False)
    check("recovery", "restart_actual_final_stopped_new_epoch_only", len(stopped) == 1 and restart_events[-1].get("event") == "stopped" and zero_resources(stopped[0]["snapshot"]))
    check("recovery", "initial_original_sysfs_after_first_restart", restart.get("restored_initial_sysfs") is True and by_label["post-restart"]["sysfs"] == original)
    return {"checks": checks, "evidence_collection_complete": complete, "primary_outcomes": outcomes, "restart_outcome": restart,
            "initial_events": initial_events, "restart_events": restart_events, "observations": observations,
            "killed_epoch_terminal_resources": None, "killed_epoch_terminal_resources_observed": False}


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
    summary = {"schema_version": 1, "result": "FAIL", "evidence_collection_complete": False, "started_at_utc": started,
               "scope": "one owned ordinary-user SIGKILL with QEMU HID/FTDI live USBFS leases, then first no-IMPORT restart; not supervisor recovery or hardware",
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
        with tempfile.TemporaryDirectory(prefix="rustusb-crash-qemu-") as temporary:
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
            summary["guest_utility_sha256"] = {name: native.sha256(work / "root" / path) for name, path in {"busybox": "bin/busybox", "jq": "usr/bin/jq", "setpriv": "usr/bin/setpriv"}.items()}
            links = list(socket.socketpair())
            accel = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in accel else "tcg"
            for role, endpoint, mac in zip(ROLES, links, ["71", "72"]):
                for channel in ["serial", "evidence"]:
                    (evidence / f"{role}-{channel}.log").touch(exist_ok=False)
                handle = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(handle)
                command = ["qemu-system-x86_64", "-m", "512", "-smp", "2", *accel, "-kernel", str(image), "-initrd", str(archive),
                           "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.crash_test=1 rustusb.role={role}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}", "-serial", f"file:{evidence / (role + '-evidence.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=private,fd={endpoint.fileno()}",
                           "-device", f"e1000,netdev=private,mac=52:54:00:00:00:{mac}"]
                if role == "server":
                    command += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,id=crash-kbd,bus=usb.0,port=1",
                                "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off", "-device", "usb-serial,id=crash-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
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
                program, kernel = physical.read_channels(evidence)
                failed = any("RUSTUSB_CRASH_RESULT: FAIL" in log or any(message in log for message in physical.INTEGRITY_FAILURES)
                             for role in ROLES for log in [program[role], kernel[role]])
                if failed and failure_started is None:
                    failure_started = time.monotonic()
                if "RUSTUSB_CRASH_WAIT_INPUT key=1" in program["client"] and "key-1" not in injections:
                    qmp.call("send-key", {"keys": [{"type": "qcode", "data": "1"}], "hold-time": 100})
                    injections.add("key-1")
                if "RUSTUSB_CRASH_WAIT_SERIAL bytes=35" in program["client"] and "serial-1" not in injections:
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
                complete = all(f"RUSTUSB_CRASH_COLLECTION_COMPLETE role={role}" in program[role].splitlines() for role in ROLES)
                finished = all(process.poll() is not None for process in processes.values())
                if finished or (complete and all("RUSTUSB_CRASH_RESULT:" in program[role] for role in ROLES)):
                    break
                if failure_started is not None and time.monotonic() - failure_started >= 30:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("crash integration deadline exceeded")
            for process in processes.values():
                process.wait(timeout=10)
            program, kernel = physical.read_channels(evidence)
            summary["verification"] = verify(program, kernel)
            summary["evidence_collection_complete"] = summary["verification"]["evidence_collection_complete"]
            # Final drain verifies every byte through actual QEMU chardev EOF.
            serial.settimeout(2)
            while True:
                chunk = serial.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
            if failure_started is not None or any(check["result"] != "PASS" for check in summary["verification"]["checks"]) or received != TX or injections != {"key-1", "serial-1"} or any(process.returncode != 0 for process in processes.values()):
                raise RuntimeError("crash/restart recovery contract failed; observed facts and missing killed-epoch terminal retained")
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
        # Only fixed synthetic fixture bytes; raw files are not product logging.
        (evidence / "ftdi-host-tx.bin").write_bytes(received)
        summary["serial_output"] = {"artifact": "ftdi-host-tx.bin", "expected_bytes": len(TX), "received_bytes": len(received),
                                    "sha256": hashlib.sha256(received).hexdigest(), "exact_match": received == TX}
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        summary["injections"] = sorted(injections)
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
        print(json.dumps({"result": summary["result"], "evidence_collection_complete": summary["evidence_collection_complete"], "evidence": str(evidence)}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
