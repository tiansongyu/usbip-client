#!/usr/bin/env python3
"""Verify persistent physical grants and administrator reconciliation in isolated guests.

Normal finish, crash refusal, helper restart and explicit reconciliation are
separate observations. Killed-epoch terminal snapshots remain unobserved.
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
OWN_SCRIPTS = ["run-journal-qemu.py", "guest-journal-init.sh"]
spec = importlib.util.spec_from_file_location("physical_qemu", REPO / "scripts/qemu/run-physical-qemu.py")
physical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(physical)
native = physical.native
SCRIPTS = OWN_SCRIPTS + physical.OWN_SCRIPTS + ["run-native-qemu.py", "guest-init.sh"]
def rx(cycle):
    return f"RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n".encode()


def tx(cycle):
    return f"RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n".encode()


TX = tx(1) + tx(2)
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
    shutil.copy2(REPO / "scripts/qemu/guest-journal-init.sh", root / "init")
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
    lines = log.splitlines()
    begin, end = f"RUSTUSB_JOURNAL_{tag}_BEGIN", f"RUSTUSB_JOURNAL_{tag}_END"
    if lines.count(begin) != 1 or lines.count(end) != 1 or lines.index(begin) >= lines.index(end):
        raise ValueError(f"one exact {tag} block required")
    return [json.loads(line) for line in lines[lines.index(begin) + 1:lines.index(end)]]


def epoch_events(log, epoch):
    lines = log.splitlines()
    begin, end = f"RUSTUSB_JOURNAL_EVENTS_BEGIN epoch={epoch}", f"RUSTUSB_JOURNAL_EVENTS_END epoch={epoch}"
    if lines.count(begin) != 1 or lines.count(end) != 1 or lines.index(begin) >= lines.index(end):
        raise ValueError(f"one exact epoch {epoch} required")
    return [json.loads(line) for line in lines[lines.index(begin) + 1:lines.index(end)]]


def zero_resources(snapshot):
    return (snapshot.get("schema_version") == 1 and snapshot.get("buffer_bytes_reserved") == 0
            and len(snapshot.get("devices", [])) == 2
            and all(device.get("state") == "available" and device.get("leased") is False
                    and device.get("cleanup_verified") is True
                    and all(device.get(key) == 0 for key in ["pending_urbs", "queued_urbs", "usbfs_inflight"])
                    and device.get("current_drivers") == device.get("original_drivers")
                    for device in snapshot["devices"]))


def verify(program, kernel):
    checks = []
    def check(name, condition):
        checks.append({"name": name, "result": "PASS" if condition else "FAIL"})
    for role in ROLES:
        lines = program[role].splitlines()
        check(f"{role}_evidence_channel", [line for line in lines if line.startswith("RUSTUSB_JOURNAL_EVIDENCE_CHANNEL:")] == [f"RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0"])
        check(f"{role}_collection_complete", lines.count(f"RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}") == 1)
        check(f"{role}_guest_pass", lines.count(f"RUSTUSB_JOURNAL_RESULT: PASS role={role}") == 1 and not any("RUSTUSB_JOURNAL_RESULT: FAIL" in log for log in [program[role], kernel[role]]))
        check(f"{role}_integrity", not any(message in log for log in [program[role], kernel[role]] for message in physical.INTEGRITY_FAILURES))
        check(f"{role}_private_network", f"RUSTUSB_JOURNAL_ISOLATION: PASS role={role} carrier=1 ip_forward=0" in lines)
    records = json_block(program["server"], "RECORDS")
    waits = json_block(program["server"], "WAITS")
    admin = json_block(program["server"], "ADMIN")
    observations = json_block(program["server"], "OBSERVATIONS")
    actual_observations = {row["label"]: row for row in observations}
    clients = json_block(program["client"], "CLIENT")
    by_label = {row["label"]: row for row in records}
    expected_labels = {"initial", "normal-ready", "normal-pending", "normal-closed", "reopened-ready", "reopened-closed", "killed-ready", "killed-pending", "killed-post", "denied-before-helper-restart-refused", "helper-reloaded", "denied-after-helper-restart-refused", "unrestored-refused", "guest-admin-restored", "admin-retired", "reconciled-ready", "reconciled-closed"}
    check("all_journal_observations_exact_once", len(by_label) == len(records) == len(expected_labels) and set(by_label) == expected_labels)
    original = by_label["initial"]["sysfs"]
    original_by_id = {device["device_id"]: device for device in original}
    check("original_two_protected_nodes_drivers_cfg_alt", len(original) == 2 and original[0]["interfaces"] == [{"number": 0, "alternate": 0, "driver": "usbhid"}]
          and original[1]["interfaces"] == [{"number": 0, "alternate": 0, "driver": "ftdi_sio"}]
          and all(device["configuration"] == 1 and device["generation"] and device["node_identity"] and device["node_owner_mode"] == "0:600" for device in original))
    for row in records:
        pending = row["pending"]
        check(row["label"] + "_actual_api_and_disk_agree", pending.get("schema_version") == 1 and pending.get("next_after") is None and isinstance(pending.get("records"), list)
              and row["journal"].get("records") == pending["records"])
        check(row["label"] + "_protected_journal", row["journal_directory_owner_mode"] == "0:700" and row["journal_file_owner_mode"] == "0:600")
        # Raw API and on-disk JSON are retained in their own exact blocks too.
        for kind, expected in [("PENDING", pending), ("DISK", row["journal"])]:
            tag = f"RUSTUSB_JOURNAL_{kind}_BEGIN label={row['label']}"
            end = f"RUSTUSB_JOURNAL_{kind}_END label={row['label']}"
            lines = program["server"].splitlines()
            actual = json.loads("\n".join(lines[lines.index(tag) + 1:lines.index(end)])) if lines.count(tag) == lines.count(end) == 1 else None
            check(row["label"] + "_raw_" + kind.lower(), actual == expected)
    empty_labels = ["initial", "normal-closed", "reopened-closed", "admin-retired", "reconciled-closed"]
    for label in empty_labels:
        check(label + "_no_pending_records", by_label[label]["pending"]["records"] == [])
        check(label + "_independent_original_restored", by_label[label]["sysfs"] == original)
    leases = {}
    for epoch in ["normal", "reopened", "killed", "reconciled"]:
        current = by_label[epoch + "-ready"]["pending"]["records"]
        leases[epoch] = {entry["lease_id"] for entry in current}
        check(epoch + "_two_distinct_grants", len(current) == len(leases[epoch]) == 2 and {entry["baseline"]["device_id"] for entry in current} == set(original_by_id))
        identity = actual_observations[epoch + "-ready"]["identity"]
        check(epoch + "_ready_owned_identity", identity["uids"] == [1000] * 4 and identity["exe"] == "/usr/bin/rustusb"
              and all(identity[key] == 0 for key in ["cap_effective", "cap_permitted", "cap_ambient"])
              and all(any(fd["node"] == device["node"] and fd["identity"] == device["node_identity"] for fd in identity["fds"]) for device in original))
        for entry in current:
            baseline = entry["baseline"]
            device = original_by_id[baseline["device_id"]]
            receipt = baseline["receipt"]
            sysdev, sysino = map(int, device["generation"].split(":"))
            nodeparts = device["node_identity"].split(":")
            check(epoch + "_baseline_" + device["bus_id"], baseline["bus_id"] == device["bus_id"] and baseline["configuration"] == device["configuration"] and baseline["interfaces"] == device["interfaces"]
                  and receipt["sysfs_device"] == sysdev and receipt["sysfs_inode"] == sysino and receipt["node_inode"] == int(nodeparts[1]) and receipt["usb_device_number"] == device["devnum"])
            check(epoch + "_ordinary_owner_" + device["bus_id"], entry["owner"]["uid"] == 1000 and entry["owner"]["pid"] == identity["pid"]
                  and entry["owner"]["start_time"] == identity["starttime"] and identity["pid"] > 1 and identity["starttime"] > 0 and bool(entry["owner"]["boot_id"]))
    check("fresh_lease_id_each_normal_registration", len(set.union(*leases.values())) == 8)
    killed_records = by_label["killed-ready"]["pending"]["records"]
    for label in ["killed-pending", "killed-post", "denied-before-helper-restart-refused", "helper-reloaded", "denied-after-helper-restart-refused", "unrestored-refused", "guest-admin-restored"]:
        check(label + "_original_pending_unchanged", by_label[label]["pending"]["records"] == killed_records)
    post = by_label["killed-post"]["sysfs"]
    check("kill_does_not_claim_automatic_driver_restore", len(post) == 2 and all(device == {**original[index], "interfaces": [{"number": 0, "alternate": 0, "driver": None}]} for index, device in enumerate(post)))
    check("admin_explicitly_restored_actual_original_before_retirement", by_label["guest-admin-restored"]["sysfs"] == original)
    for label in ["denied-before-helper-restart-refused", "helper-reloaded", "denied-after-helper-restart-refused", "unrestored-refused"]:
        check(label + "_does_not_mutate_devices", by_label[label]["sysfs"] == post)
    epochs = ["normal", "reopened", "killed", "denied-before-helper-restart", "denied-after-helper-restart", "reconciled"]
    events = {epoch: epoch_events(program["server"], epoch) for epoch in epochs}
    wait_by_label = {row["label"]: row for row in waits if row["event"] == "server_wait"}
    check("six_actual_server_waits", len(wait_by_label) == 6)
    for epoch in ["normal", "reopened", "reconciled"]:
        value = events[epoch]
        stopped = [row for row in value if row.get("event") == "stopped"]
        check(epoch + "_actual_shutdown_wait0", wait_by_label[epoch]["wait_code"] == 0 and sum(row.get("event") == "ready" for row in value) == 1 and len(stopped) == 1 and value[-1].get("event") == "stopped" and zero_resources(stopped[0]["snapshot"]))
        if epoch != "normal":
            check(epoch + "_no_import", all(all(value == 0 for value in device["counters"].values()) and device["usbfs_reaped"] == 0 for device in stopped[0]["snapshot"]["devices"]))
    killed = wait_by_label["killed"]
    check("one_owned_kill_actual137_no_fake_final_snapshot", killed["wait_code"] == 137 and killed["signal"] == 9 and killed["signal_count"] == 1 and killed["sender_uid"] == 1000
          and killed["final_backend_snapshot"] is None and killed["final_backend_snapshot_observable"] is False and not any(row.get("event") == "stopped" for row in events["killed"]))
    for epoch in ["denied-before-helper-restart", "denied-after-helper-restart"]:
        check(epoch + "_nonzero_no_ready", wait_by_label[epoch]["wait_code"] != 0 and not any(row.get("event") == "ready" for row in events[epoch]))
        begin = f"RUSTUSB_JOURNAL_STDERR_BEGIN epoch={epoch}"
        end = f"RUSTUSB_JOURNAL_STDERR_END epoch={epoch}"
        lines = program["server"].splitlines()
        error = "\n".join(lines[lines.index(begin) + 1:lines.index(end)]) if lines.count(begin) == lines.count(end) == 1 else ""
        check(epoch + "_explicit_pending_refusal", "unresolved physical grant blocks new authorization; inspect physical-pending and reconcile the original baseline" in error)
    helper = [row for row in waits if row["event"] == "helper_restart"]
    check("real_helper_restart_same_persistent_directory", len(helper) == 1 and helper[0]["old_wait_code"] == 0 and helper[0]["old_pid"] != helper[0]["new_pid"] and helper[0]["old_starttime"] > 0 and helper[0]["new_starttime"] > 0 and helper[0]["state_directory_unchanged"] is True)
    for epoch in ["normal", "killed"]:
        observed = actual_observations[epoch + "-pending"]
        snap = observed["snapshot"]
        identity = observed["identity"]
        current_records = by_label[epoch + "-pending"]["pending"]["records"]
        check(epoch + "_actual_pending_usbfs_budget", snap["buffer_bytes_reserved"] > 0 and len(snap["devices"]) == 2 and all(device["leased"] and device["state"] == "leased" and device["pending_urbs"] >= 1 and device["usbfs_inflight"] >= 1 for device in snap["devices"]))
        check(epoch + "_pending_owner_matches_server", all(entry["owner"]["pid"] == identity["pid"] and entry["owner"]["start_time"] == identity["starttime"] for entry in current_records)
              and identity["uids"] == [1000] * 4 and identity["exe"] == "/usr/bin/rustusb" and all(identity[key] == 0 for key in ["cap_effective", "cap_permitted", "cap_ambient"]))
        check(epoch + "_actual_claims_fds_same_generation", observed["sysfs"] == [{**device, "interfaces": [{"number": 0, "alternate": 0, "driver": "usbfs"}]} for device in original]
              and all(any(fd["node"] == device["node"] and fd["identity"] == device["node_identity"] for fd in identity["fds"]) for device in original))
    check("admin_api_attempts_exact", len(admin) == 12 and sum(row["phase"] == "pending-denied" for row in admin) == 4 and sum(row["phase"] == "reconcile-denied" for row in admin) == 4 and sum(row["phase"] == "unrestored-refused" for row in admin) == 2 and sum(row["phase"] == "restored-retired" for row in admin) == 2)
    for row in admin:
        if row["phase"] == "restored-retired":
            response = json.loads(row["stdout"])
            check("root_restored_reconcile_" + row["lease_id"], row["uid"] == 0 and row["exit_code"] == 0 and response.get("status") == "retired" and response.get("observation") == "restored")
        else:
            expected_error = {
                ("pending-denied", 1000): "physical pending records require root",
                ("reconcile-denied", 1000): "physical reconciliation requires root",
                ("pending-denied", 1001): "caller UID is not approved by the administrator",
                ("reconcile-denied", 1001): "caller UID is not approved by the administrator",
                ("unrestored-refused", 0): "physical original configuration, full interface/alternate/driver set, or generation is not restored; unresolved grant retained",
            }.get((row["phase"], row["uid"]))
            try:
                response = json.loads(row["stdout"])
            except (ValueError, TypeError):
                response = None
            check("actual_refusal_" + row["phase"] + "_" + str(row["uid"]), row["exit_code"] != 0
                  and isinstance(response, dict) and response.get("ok") is False
                  and isinstance(response.get("error"), str) and expected_error is not None
                  and expected_error in response["error"])
    check("client_two_actual_io_and_disconnect_records", [row["cycle"] for row in clients] == [1, 2])
    for row in clients:
        cycle = row["cycle"]
        check(f"cycle{cycle}_serial_exact", row["rx_hex"] == rx(cycle).hex() and row["tx_hex"] == tx(cycle).hex())
        data = bytes.fromhex(row["input_event_hex"])
        frames = list(struct.iter_unpack("<qqHHi", data)) if data and len(data) % 24 == 0 else []
        check(f"cycle{cycle}_actual_key_down_up", any(frame[2:] == (1, cycle + 1, 1) for frame in frames) and any(frame[2:] == (1, cycle + 1, 0) for frame in frames))
        check(f"cycle{cycle}_spontaneous_before_detach", row["spontaneous_disconnect"] is True and row["detach_before_observation"] is False and row["sessions"] == [])
        receipts = row["receipts"]
        check(f"cycle{cycle}_complete_receipts", len(receipts) == 2 and receipts[0]["port"] != receipts[1]["port"] and all(receipt["status"] == 6 and receipt["local_device_address"] > 0 and receipt["local_device_generation"]["inode"] > 0 and receipt["local_device_generation"]["filesystem_device"] > 0 for receipt in receipts))
        rows = [line.split() for line in row["vhci_status"].splitlines() if line.split() and line.split()[0] in ["hs", "ss"]]
        check(f"cycle{cycle}_independent_vhci_null", bool(rows) and len({value[1] for value in rows}) == len(rows) and all(len(value) == 7 and int(value[2]) == 4 and value[6] == "0-0" for value in rows)
              and all(str(receipt["port"]).zfill(4) in {value[1].zfill(4) for value in rows} for receipt in receipts))
    busy = [json.loads(line) for line in program["client"].splitlines() if line.startswith('{"event":"physical_probe_case"')]
    check("two_cycles_both_devices_busy", busy == [{"event": "physical_probe_case", "case": "both_real_devices_exclusive_busy", "result": "PASS"}] * 2)
    check("authorization_gates", "RUSTUSB_JOURNAL_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600" in program["server"].splitlines()
          and "RUSTUSB_JOURNAL_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false" in program["server"].splitlines())
    check("ordinary_cannot_read_journal", "RUSTUSB_JOURNAL_DISK_GATE: PASS ordinary_uid1000=denied directory=root0700 file=root0600" in program["server"].splitlines())
    return {"checks": checks, "records": records, "waits": waits, "admin": admin, "observations": observations, "client_outcomes": clients,
            "epoch_events": events, "evidence_collection_complete": all(f"RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}" in program[role].splitlines() for role in ROLES),
            "killed_epoch_terminal_resources": None, "killed_epoch_terminal_resources_observed": False, "automatic_driver_restoration_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=300)
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
               "scope": "persistent grants: normal real I/O finish, crash real I/O refusal across helper restart, explicit guest administrator restoration/reconciliation; not automatic recovery or hardware",
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
        with tempfile.TemporaryDirectory(prefix="rustusb-journal-qemu-") as temporary:
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
                           "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.journal_test=1 rustusb.role={role}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}", "-serial", f"file:{evidence / (role + '-evidence.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=private,fd={endpoint.fileno()}",
                           "-device", f"e1000,netdev=private,mac=52:54:00:00:00:{mac}"]
                if role == "server":
                    command += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,id=journal-kbd,bus=usb.0,port=1",
                                "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off", "-device", "usb-serial,id=journal-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
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
                failed = any("RUSTUSB_JOURNAL_RESULT: FAIL" in log or any(message in log for message in physical.INTEGRITY_FAILURES)
                             for role in ROLES for log in [program[role], kernel[role]])
                if failed and failure_started is None:
                    failure_started = time.monotonic()
                for cycle in [1, 2]:
                    if f"RUSTUSB_JOURNAL_WAIT_INPUT key={cycle}" in program["client"] and f"key-{cycle}" not in injections:
                        qmp.call("send-key", {"keys": [{"type": "qcode", "data": str(cycle)}], "hold-time": 100})
                        injections.add(f"key-{cycle}")
                    if f"RUSTUSB_JOURNAL_WAIT_SERIAL cycle={cycle} bytes=35" in program["client"] and f"serial-{cycle}" not in injections:
                        serial.settimeout(2)
                        serial.sendall(rx(cycle))
                        serial.setblocking(False)
                        injections.add(f"serial-{cycle}")
                while True:
                    try:
                        chunk = serial.recv(4096)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    received.extend(chunk)
                complete = all(f"RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}" in program[role].splitlines() for role in ROLES)
                finished = all(process.poll() is not None for process in processes.values())
                if finished or (complete and all("RUSTUSB_JOURNAL_RESULT:" in program[role] for role in ROLES)):
                    break
                if failure_started is not None and time.monotonic() - failure_started >= 30:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("journal integration deadline exceeded")
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
            if failure_started is not None or any(check["result"] != "PASS" for check in summary["verification"]["checks"]) or received != TX or injections != {"key-1", "serial-1", "key-2", "serial-2"} or any(process.returncode != 0 for process in processes.values()):
                raise RuntimeError("journal lifecycle contract failed; original records and per-epoch observations retained")
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
