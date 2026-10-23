#!/usr/bin/env python3
"""Verify opt-in product CONNECT-only recovery in two disposable QEMU guests.

The existing journal runner supplies transport, real I/O and collection only.
This runner uses its own positive verdict; the original 184 journal assertions
remain unchanged and must be run separately with the default policy.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
HERE = REPO / "scripts/qemu"
BASE_HASHES = {
    "run-journal-qemu.py": "1cefbc14bc45c9e9b5c9f6f0d9784be834fe3ee5856f8bc2937b7baabbbc01aa",
    "guest-journal-init.sh": "2236c3080d17e447c4d9cdec495609ab6c7d3976d6c92306e3b1286e9d94d8d4",
}
OWN_SOURCES = ["run-autorestore-qemu.py", "guest-autorestore-fragments.sh"]
SCENARIOS = ["owner-death", "helper-restart"]
SCOPE = ("Product opt-in CONNECT-only recovery in disposable HID/FTDI guests: ordinary real I/O, "
         "one SIGKILL/actual wait137, background helper restores the exact original baseline and "
         "retires the old grants before any pending API or new grant, then third real I/O. "
         "No test USBFS recovery actor, administrator bind/reconcile, FD revocation or hardware claim.")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def generate_init(original, scenario):
    """Compose a new positive flow without changing any original source file."""
    if scenario not in SCENARIOS:
        raise ValueError("unknown automatic recovery scenario")
    if sha(original) != BASE_HASHES["guest-journal-init.sh"]:
        raise ValueError("journal guest source drifted; review composition")
    text = original.decode()
    ledger = []

    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError("one exact composition anchor required: " + before)
        text = text.replace(before, after, 1)
        ledger.append({"before": before, "after": after, "matches": 1})

    replace("ROLE=unknown\n", f"AUTORESTORE_SCENARIO={scenario}\nROLE=unknown\n")
    replace("/tmp/client-outcomes.jsonl; do\n",
            "/tmp/client-outcomes.jsonl /tmp/helper-before-restart.log /tmp/autorestore-metadata.json /tmp/autorestore-helper-logs.jsonl /tmp/autorestore-readonly.jsonl /tmp/autorestore-primary.json /tmp/autorestore-recovery-window.json /tmp/autorestore-live-window.json /tmp/autorestore-live-disk.json /tmp/autorestore-killed-disk.json /tmp/autorestore-interfaces.json /tmp/autorestore-disk.json; do\n")
    replace("    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &\n",
            "    (exec 3<&- 9<&-; exec /usr/bin/rustusb-helper > /tmp/helper.log 2>&1) &\n")
    replace("    for attempt in $(seq 1 150); do\n        sysfs_observation\n",
            "    . /usr/lib/guest-autorestore-fragments.sh\n    for attempt in $(seq 1 150); do\n        sysfs_observation\n")
    replace("physical_device_ids:[$hid,$ftdi],peers:[],trusted_network:true}",
            "physical_device_ids:[$hid,$ftdi],physical_connect_restore_device_ids:[$hid,$ftdi],peers:[],trusted_network:true}")
    replace("    : > /tmp/admin-responses.jsonl\n    record initial\n",
            "    : > /tmp/admin-responses.jsonl\n    autorestore_begin\n    record initial\n")
    begin, end = "    STAGE=kill-service\n", "    STAGE=authorized-after-reconcile\n"
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) >= text.index(end):
        raise ValueError("one ordered original crash/manual-recovery section required")
    original_flow = text[text.index(begin):text.index(end) + len(end)]
    replace(original_flow, "    autorestore_killed_epoch\n    STAGE=authorized-after-automatic-recovery\n")
    replace("    start_ready reconciled\n", "    start_ready recovered\n")
    replace("    record reconciled-closed\n", "    record recovered-closed\n")
    replace("    helper_alive\n    echo 'RUSTUSB_JOURNAL_OBSERVATIONS_BEGIN'",
            "    helper_alive\n    autorestore_helper_log final\n    echo 'RUSTUSB_JOURNAL_OBSERVATIONS_BEGIN'")
    replace('        timeout 8 dd bs=1 count=35 <&4 > /tmp/rx.bin 2> /tmp/dd.log & SERIAL_READER=$!\n',
            '        if [ "$AUTORESTORE_SCENARIO" = helper-restart ] && [ "$cycle" = 2 ]; then\n'
            '            send_control 192.0.2.1 cycle-2-attached\n'
            '            receive_control cycle-2-io-allowed\n'
            '        fi\n'
            '        timeout 8 dd bs=1 count=35 <&4 > /tmp/rx.bin 2> /tmp/dd.log & SERIAL_READER=$!\n')
    # The unused old refusal helper is retained as source context, but the
    # generated executable flow has no restoration write or reconcile call.
    for forbidden in [" > /sys/bus/usb/drivers/", "--json physical-reconcile", "guardian_", "usbfs-guardian-probe"]:
        if forbidden in text:
            raise ValueError("forbidden administrator recovery actor in positive guest: " + forbidden)
    return text.encode(), ledger


def rows(log, prefix):
    return [json.loads(line[len(prefix):]) for line in log.splitlines() if line.startswith(prefix)]


def helper_operations(log):
    """Parse bounded tracing fields; do not infer success from a free-text word."""
    clean = re.sub(r"\x1b\[[0-9;]*m", "", log)
    result = []
    for line in clean.splitlines():
        if "physical_auto_restore" not in line:
            continue
        operation = re.search(r'(?:^|\s)operation="physical_auto_restore"(?:\s|$)', line)
        phase = re.search(r'(?:^|\s)phase="(attempt|retired|failed)"(?:\s|$)', line)
        lease = re.search(r'(?:^|\s)lease_id="?([a-zA-Z0-9_-]+)"?(?:\s|$)', line)
        if operation is None or phase is None or lease is None:
            raise ValueError("unrecognized product automatic recovery event: " + line)
        result.append({"lease_id": lease.group(1), "phase": phase.group(1), "raw": line})
    return result


def verify_positive(base, scenario, program, kernel):
    """Independent positive assertions, not a filtered original journal verdict."""
    checks = []

    def check(name, condition):
        checks.append({"name": name, "result": "PASS" if condition else "FAIL"})

    for role in base.ROLES:
        lines = program[role].splitlines()
        check(f"{role}_evidence_channel", [line for line in lines if line.startswith("RUSTUSB_JOURNAL_EVIDENCE_CHANNEL:")] == [f"RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0"])
        check(f"{role}_collection_complete", lines.count(f"RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}") == 1)
        check(f"{role}_guest_pass", lines.count(f"RUSTUSB_JOURNAL_RESULT: PASS role={role}") == 1 and not any("RUSTUSB_JOURNAL_RESULT: FAIL" in log for log in [program[role], kernel[role]]))
        check(f"{role}_integrity", not any(message in log for log in [program[role], kernel[role]] for message in base.physical.INTEGRITY_FAILURES))
        check(f"{role}_private_network", lines.count(f"RUSTUSB_JOURNAL_ISOLATION: PASS role={role} carrier=1 ip_forward=0") == 1)
        check(f"{role}_no_program_markers_in_kernel", not any(line.startswith(("RUSTUSB_JOURNAL_", "RUSTUSB_AUTORESTORE_")) for line in kernel[role].splitlines()))
    log = program["server"]
    check("automatic_evidence_only_on_server_program_channel", "RUSTUSB_AUTORESTORE_" not in program["client"] and not any("RUSTUSB_AUTORESTORE_" in value for value in kernel.values()))
    records = base.json_block(log, "RECORDS")
    waits = base.json_block(log, "WAITS")
    admin = base.json_block(log, "ADMIN")
    observations = base.json_block(log, "OBSERVATIONS")
    clients = base.json_block(program["client"], "CLIENT")
    metadata = rows(log, "RUSTUSB_AUTORESTORE_METADATA ")
    pure = rows(log, "RUSTUSB_AUTORESTORE_READONLY ")
    primary = rows(log, "RUSTUSB_AUTORESTORE_PRIMARY ")
    recovery_window = rows(log, "RUSTUSB_AUTORESTORE_RECOVERY_WINDOW ")
    live_window = rows(log, "RUSTUSB_AUTORESTORE_LIVE_WINDOW ")
    helper_logs = rows(log, "RUSTUSB_AUTORESTORE_HELPER ")
    expected_labels = ["initial", "normal-ready", "normal-pending", "normal-closed", "reopened-ready", "reopened-closed", "killed-ready"]
    if scenario == "helper-restart":
        expected_labels += ["killed-attached", "helper-reloaded", "live-held"]
    expected_labels += ["killed-pending", "automatically-restored", "recovered-ready", "recovered-pending", "recovered-closed"]
    check("exact_ordered_journal_observations", [row["label"] for row in records] == expected_labels)
    if [row["label"] for row in records] != expected_labels:
        raise ValueError("positive lifecycle observations incomplete; original streams retained")
    by_label = {row["label"]: row for row in records}
    observed = {row["label"]: row for row in observations}
    expected_observed = [label for label in expected_labels if label.endswith(("-ready", "-pending")) or label in ["killed-attached", "helper-reloaded", "live-held"]]
    check("exact_actual_snapshot_observations", [row["label"] for row in observations] == expected_observed and len(observed) == len(observations))
    check("immediate_records_match_complete_block", rows(log, "RUSTUSB_JOURNAL_RECORD ") == records)
    check("immediate_waits_match_complete_block", rows(log, "RUSTUSB_JOURNAL_WAIT ") == waits)
    check("no_administrator_reconcile_attempts", admin == [])
    original = by_label["initial"]["sysfs"]
    original_by_id = {device["device_id"]: device for device in original}
    check("original_two_complete_protected_devices", len(original) == len(original_by_id) == 2 and [d["bus_id"] for d in original] == ["1-1", "1-2"]
          and [d["interfaces"] for d in original] == [[{"number": 0, "alternate": 0, "driver": "usbhid"}], [{"number": 0, "alternate": 0, "driver": "ftdi_sio"}]]
          and all(d["configuration"] == 1 and d["node_owner_mode"] == "0:600" and d["generation"] and d["node_identity"] for d in original))
    check("one_explicit_scenario_metadata", len(metadata) == 1 and metadata[0].get("scenario") == scenario
          and metadata[0].get("usbfs_test_probe") is False and metadata[0].get("administrator_reconcile") is False
          and metadata[0].get("recovery_deadline_centiseconds") == 2000 and metadata[0].get("live_window_centiseconds") == 500 and metadata[0].get("product_tick_milliseconds") == 250)
    if len(metadata) != 1:
        raise ValueError("one real guest policy/boot metadata required")
    policy = metadata[0]["policy"]
    check("root_policy_explicitly_authorizes_only_fixture_restore", policy == {"allowed_uids": [1000], "export_device_ids": [], "physical_device_ids": [d["device_id"] for d in original], "physical_connect_restore_device_ids": [d["device_id"] for d in original], "peers": [], "trusted_network": True})
    for row in records:
        label, pending, disk = row["label"], row["pending"], row["journal"]
        projected = [{k: v for k, v in entry.items() if k != "recovery"} for entry in disk["records"]]
        check(label + "_old_ipc_shape_matches_actual_disk", pending.get("schema_version") == 1 and pending.get("next_after") is None and pending.get("records") == projected
              and all("recovery" not in entry for entry in pending["records"]))
        if label == "initial":
            check(label + "_new_empty_manual_store_is_legacy", disk.get("schema_version") == 1 and disk["records"] == []
                  and "storage_schema_version" not in pending and "recovery_authorizations" not in pending)
        else:
            authorizations = [{"lease_id": entry["lease_id"], "recovery": entry.get("recovery")} for entry in disk["records"]]
            check(label + "_explicit_sticky_storage_v2_authorizations", disk.get("schema_version") == 2 and pending.get("storage_schema_version") == 2
                  and pending.get("recovery_authorizations") == authorizations
                  and all(entry.get("recovery") == "connect_original_drivers" for entry in disk["records"]))
        check(label + "_protected_journal", row["journal_directory_owner_mode"] == "0:700" and row["journal_file_owner_mode"] == "0:600" and re.fullmatch(r"[0-9a-f]{64}", row["journal_sha256"]) is not None)
        for kind, expected in [("PENDING", pending), ("DISK", disk)]:
            begin, end = f"RUSTUSB_JOURNAL_{kind}_BEGIN label={label}", f"RUSTUSB_JOURNAL_{kind}_END label={label}"
            lines = log.splitlines()
            raw = "\n".join(lines[lines.index(begin) + 1:lines.index(end)]) if lines.count(begin) == lines.count(end) == 1 and lines.index(begin) < lines.index(end) else None
            check(label + "_raw_" + kind.lower(), raw is not None and json.loads(raw) == expected)
    empty = ["initial", "normal-closed", "reopened-closed", "automatically-restored", "recovered-closed"]
    for label in empty:
        check(label + "_empty_and_independent_original", by_label[label]["pending"]["records"] == [] and by_label[label]["journal"]["records"] == [] and by_label[label]["sysfs"] == original)
    leases = {}
    for epoch in ["normal", "reopened", "killed", "recovered"]:
        entries = by_label[epoch + "-ready"]["pending"]["records"]
        identity = observed[epoch + "-ready"]["identity"]
        leases[epoch] = {entry["lease_id"] for entry in entries}
        check(epoch + "_two_distinct_original_grants", len(entries) == len(leases[epoch]) == 2 and {e["baseline"]["device_id"] for e in entries} == set(original_by_id))
        check(epoch + "_ordinary_live_identity", valid_owner(identity, original))
        for entry in entries:
            device = original_by_id[entry["baseline"]["device_id"]]
            baseline, receipt = entry["baseline"], entry["baseline"]["receipt"]
            sysdev, sysino = map(int, device["generation"].split(":"))
            node = device["node_identity"].split(":")
            check(epoch + "_exact_baseline_" + device["bus_id"], baseline["bus_id"] == device["bus_id"] and baseline["configuration"] == device["configuration"] and baseline["interfaces"] == device["interfaces"]
                  and receipt["sysfs_device"] == sysdev and receipt["sysfs_inode"] == sysino and receipt["node_inode"] == int(node[1])
                  and receipt["node_device"] == os.makedev(int(node[2], 16), int(node[3], 16)) and receipt["usb_device_number"] == device["devnum"]
                  and receipt["bus_number"] == int(device["bus_id"].split("-", 1)[0]))
            check(epoch + "_owner_matches_actual_process_" + device["bus_id"], entry["owner"] == {"uid": 1000, "pid": identity["pid"], "start_time": identity["starttime"], "boot_id": metadata[0]["boot_id"]})
        ready = observed[epoch + "-ready"]["snapshot"]
        check(epoch + "_ready_uses_original_nonnull_drivers", base.zero_resources(ready) and all(device["configuration"] == original_by_id[device["device_id"]]["configuration"]
              and device["original_drivers"] == [{"interface": intf["number"], "driver": intf["driver"]} for intf in original_by_id[device["device_id"]]["interfaces"]] for device in ready["devices"]))
    check("eight_fresh_lease_ids_in_four_epochs", len(set.union(*leases.values())) == 8)
    claimed = json.loads(json.dumps(original))
    for device in claimed:
        for interface in device["interfaces"]:
            interface["driver"] = "usbfs"
    for epoch in ["normal", "killed", "recovered"]:
        row = observed[epoch + "-pending"]
        snap = row["snapshot"]
        check(epoch + "_actual_pending_and_positive_budget", snap["buffer_bytes_reserved"] > 0 and len(snap["devices"]) == 2 and all(d["leased"] is True and d["state"] == "leased" and d["pending_urbs"] >= 1 and d["usbfs_inflight"] >= 1 for d in snap["devices"]))
        check(epoch + "_claims_fds_and_receipt", row["sysfs"] == claimed and valid_owner(row["identity"], original)
              and row["identity"] == observed[epoch + "-ready"]["identity"]
              and by_label[epoch + "-pending"]["pending"]["records"] == by_label[epoch + "-ready"]["pending"]["records"])
    events = {epoch: base.epoch_events(log, epoch) for epoch in ["normal", "reopened", "killed", "recovered"]}
    server_waits = [w for w in waits if w.get("event") == "server_wait"]
    wait_by_label = {w["label"]: w for w in server_waits}
    check("four_actual_server_waits_once", len(server_waits) == len(wait_by_label) == 4 and [w["label"] for w in server_waits] == ["normal", "reopened", "killed", "recovered"])
    for epoch in ["normal", "reopened", "recovered"]:
        stopped = [event for event in events[epoch] if event.get("event") == "stopped"]
        check(epoch + "_actual_shutdown_wait0", wait_by_label[epoch]["wait_code"] == 0 and sum(e.get("event") == "ready" for e in events[epoch]) == 1 and len(stopped) == 1 and events[epoch][-1].get("event") == "stopped" and base.zero_resources(stopped[0]["snapshot"]))
        if epoch == "reopened":
            check("reopened_no_import", all(all(value == 0 for value in d["counters"].values()) and d["usbfs_reaped"] == 0 for d in stopped[0]["snapshot"]["devices"]))
    killed = wait_by_label["killed"]
    killed_owner = observed["killed-pending"]["identity"]
    check("one_owned_kill_actual137_without_fake_cleanup", killed["wait_code"] == 137 and killed["signal"] == 9 and killed["signal_count"] == 1 and killed["sender_uid"] == 1000
          and killed["pid"] == killed_owner["pid"] and killed["starttime"] == killed_owner["starttime"]
          and killed["final_backend_snapshot"] is None and killed["final_backend_snapshot_observable"] is False
          and sum(e.get("event") == "ready" for e in events["killed"]) == 1 and not any(e.get("event") == "stopped" for e in events["killed"]))
    check("one_signal_marker", [line for line in log.splitlines() if line.startswith("RUSTUSB_JOURNAL_SIGNAL:")] == [f"RUSTUSB_JOURNAL_SIGNAL: PASS signal=9 count=1 sender_uid=1000 pid={killed['pid']} starttime={killed['starttime']}"])
    check("readonly_sequence_complete", bool(pure) and [row["sequence"] for row in pure] == list(range(1, len(pure) + 1)) and all(a["tick"] <= b["tick"] for a, b in zip(pure, pure[1:])))
    for row in pure:
        check(f"readonly_{row['sequence']}_real_protected_observation", row["pending_api_called"] is False and row["journal_directory_owner_mode"] == "0:700" and row["journal_file_owner_mode"] == "0:600"
              and row["journal"].get("schema_version") == 2 and valid_helper(row["helper"]) and sha(row["journal_raw"].encode()) == row["journal_sha256"] and json.loads(row["journal_raw"]) == row["journal"])
    after = [row for row in pure if row["phase"] == "after-owner-reaped"]
    check("one_primary_from_final_readonly_recovery_observation", len(primary) == 1 and bool(after) and primary[0] == after[-1]
          and primary[0]["sysfs"] == original and primary[0]["journal"] == {"schema_version": 2, "records": []}
          and primary[0]["owner"] is None)
    check("recovery_window_bounded_and_no_api_trigger", len(recovery_window) == 1 and 0 <= recovery_window[0]["end_tick"] - recovery_window[0]["start_tick"] < 2000
          and recovery_window[0]["pending_api_before_primary"] is False and all(r["owner"] is None and recovery_window[0]["start_tick"] <= r["tick"] <= recovery_window[0]["end_tick"] for r in after))
    primary_position = log.index("RUSTUSB_AUTORESTORE_PRIMARY ")
    check("primary_saved_before_pending_api_and_new_service", log.index('RUSTUSB_JOURNAL_PENDING_BEGIN label=automatically-restored') > primary_position
          and log.index('RUSTUSB_JOURNAL_PENDING_BEGIN label=recovered-ready') > primary_position
          and log.index('RUSTUSB_JOURNAL_WAIT {"label":"killed"') < log.index("RUSTUSB_AUTORESTORE_READONLY ", log.index('RUSTUSB_JOURNAL_WAIT {"label":"killed"')))
    killed_disk = by_label["killed-pending"]["journal"]
    for row in after:
        check(f"recovery_{row['sequence']}_same_generation_no_foreign_baseline", len(row["sysfs"]) == 2 and all({k: v for k, v in d.items() if k != "interfaces"} == {k: v for k, v in original[i].items() if k != "interfaces"}
              and d["interfaces"] in [original[i]["interfaces"], [{"number": 0, "alternate": 0, "driver": None}]] for i, d in enumerate(row["sysfs"]))
              and all(entry in killed_disk["records"] for entry in row["journal"]["records"]))
    expected_helpers = ["initial"]
    if scenario == "helper-restart":
        expected_helpers += ["live-before-restart", "live-after-restart", "live-before-io"]
    expected_helpers += ["before-kill", "restored", "final"]
    check("exact_helper_log_checkpoints", [row["label"] for row in helper_logs] == expected_helpers and all(valid_helper(row["helper"]) for row in helper_logs))
    helpers = {row["label"]: row for row in helper_logs}
    check("no_recovery_attempt_with_live_owner", all(helper_operations(row["log"]) == [] for row in helper_logs if row["label"] not in ["restored", "final"]))
    operations = helper_operations(helpers["final"]["log"])
    check("exact_product_attempt_and_retirement_for_old_leases", len(operations) == 4 and all([{k: op[k] for k in ["lease_id", "phase"]} for op in operations if op["lease_id"] == lease] == [{"lease_id": lease, "phase": "attempt"}, {"lease_id": lease, "phase": "retired"}] for lease in leases["killed"]))
    check("actual_recovering_helper_preserved_through_final", helpers["before-kill"]["helper"] == helpers["restored"]["helper"] == helpers["final"]["helper"]
          and all(row["helper"] == helpers["final"]["helper"] for row in after))
    live = [row for row in pure if row["phase"] == "live-owner"]
    helper_waits = [row for row in waits if row.get("event") != "server_wait"]
    if scenario == "helper-restart":
        check("one_real_helper_stop_and_restart", [w["event"] for w in helper_waits] == ["helper_stop", "helper_restart"] and all(w["old_wait_code"] == 0 for w in helper_waits)
              and helper_waits[0]["old_pid"] == helper_waits[1]["old_pid"] == helpers["initial"]["helper"]["pid"]
              and helper_waits[0]["old_starttime"] == helper_waits[1]["old_starttime"] == helpers["initial"]["helper"]["starttime"]
              and helper_waits[1]["new_pid"] == helpers["final"]["helper"]["pid"] != helper_waits[1]["old_pid"]
              and helper_waits[1]["new_starttime"] == helpers["final"]["helper"]["starttime"] and helper_waits[1]["state_directory_unchanged"] is True)
        check("five_second_live_owner_window", len(live_window) == 1 and len(live) >= 2 and live_window[0]["end_tick"] - live_window[0]["start_tick"] >= 500
              and live[0]["tick"] >= live_window[0]["start_tick"] and live[-1]["tick"] >= live_window[0]["start_tick"] + 500
              and live_window[0]["owner_pid"] == killed_owner["pid"] and live_window[0]["owner_starttime"] == killed_owner["starttime"])
        for label in ["killed-attached", "helper-reloaded", "live-held"]:
            check(label + "_same_claimed_owner_and_immutable_authorization", by_label[label]["journal"] == killed_disk and by_label[label]["sysfs"] == claimed
                  and observed[label]["identity"] == killed_owner)
        check("all_live_observations_hold_original_grants_and_owner", all(row["journal"] == killed_disk and row["sysfs"] == claimed and row["owner"] == killed_owner
              and row["helper"] == helpers["final"]["helper"] for row in live))
        check("live_window_then_actual_io_then_kill", log.index("RUSTUSB_AUTORESTORE_LIVE_WINDOW ") < log.index('RUSTUSB_JOURNAL_RECORD {"label":"killed-pending"') < log.index("RUSTUSB_JOURNAL_SIGNAL:"))
    else:
        check("no_helper_restart_in_owner_death_case", helper_waits == [] and live_window == [] and live == [] and all(row["helper"] == helpers["initial"]["helper"] for row in helper_logs))
    check("only_expected_readonly_phases", len(pure) == len(live) + len(after))
    verify_clients(base, program, clients, check)
    check("authorization_gates", log.splitlines().count("RUSTUSB_JOURNAL_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600") == 1
          and log.splitlines().count("RUSTUSB_JOURNAL_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false") == 1)
    check("ordinary_cannot_read_journal", log.splitlines().count("RUSTUSB_JOURNAL_DISK_GATE: PASS ordinary_uid1000=denied directory=root0700 file=root0600") == 1)
    return {"checks": checks, "records": records, "waits": waits, "admin": admin, "observations": observations, "client_outcomes": clients,
            "epoch_events": events, "scenario": scenario, "metadata": metadata, "readonly_observations": pure, "primary": primary,
            "recovery_window": recovery_window, "live_window": live_window, "helper_checkpoints": helper_logs, "product_operations": operations,
            "evidence_collection_complete": all(f"RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}" in program[role].splitlines() for role in base.ROLES),
            "killed_epoch_terminal_resources": None, "killed_epoch_terminal_resources_observed": False,
            "automatic_driver_restoration_tested": True, "original_journal_verifier_used": False,
            "live_owner_no_ioctl_evidence": "product operation logs plus source control-flow audit; independent state preserved, no syscall tracer"}


def valid_owner(identity, original):
    return (identity["uids"] == [1000] * 4 and identity["exe"] == "/usr/bin/rustusb" and identity["pid"] > 1 and identity["starttime"] > 0
            and all(identity[key] == 0 for key in ["cap_effective", "cap_permitted", "cap_ambient"])
            and all(any(fd["node"] == d["node"] and fd["identity"] == d["node_identity"] for fd in identity["fds"]) for d in original))


def valid_helper(identity):
    return identity["uids"] == [0] * 4 and identity["exe"] == "/usr/bin/rustusb-helper" and identity["pid"] > 1 and identity["starttime"] > 0


def verify_clients(base, program, clients, check):
    check("three_actual_client_io_and_disconnect_records", [row["cycle"] for row in clients] == [1, 2, 3])
    for row in clients:
        cycle = row["cycle"]
        check(f"cycle{cycle}_serial_exact", row["rx_hex"] == base.rx(cycle).hex() and row["tx_hex"] == base.tx(cycle).hex())
        data = bytes.fromhex(row["input_event_hex"])
        frames = list(struct.iter_unpack("<qqHHi", data)) if data and len(data) % 24 == 0 else []
        check(f"cycle{cycle}_actual_key_down_up", any(frame[2:] == (1, cycle + 1, 1) for frame in frames) and any(frame[2:] == (1, cycle + 1, 0) for frame in frames))
        check(f"cycle{cycle}_spontaneous_before_detach", row["spontaneous_disconnect"] is True and row["detach_before_observation"] is False and row["sessions"] == [])
        receipts = row["receipts"]
        check(f"cycle{cycle}_complete_receipts", len(receipts) == 2 and receipts[0]["port"] != receipts[1]["port"] and all(r["status"] == 6 and r["local_device_address"] > 0 and r["local_device_generation"]["inode"] > 0 and r["local_device_generation"]["filesystem_device"] > 0 for r in receipts))
        values = [line.split() for line in row["vhci_status"].splitlines() if line.split() and line.split()[0] in ["hs", "ss"]]
        check(f"cycle{cycle}_independent_vhci_null", bool(values) and len({v[1] for v in values}) == len(values) and all(len(v) == 7 and int(v[2]) == 4 and v[6] == "0-0" for v in values)
              and all(str(r["port"]).zfill(4) in {v[1].zfill(4) for v in values} for r in receipts))
    busy = [json.loads(line) for line in program["client"].splitlines() if line.startswith('{"event":"physical_probe_case"')]
    check("all_three_cycles_both_devices_busy", busy == [{"event": "physical_probe_case", "case": "both_real_devices_exclusive_busy", "result": "PASS"}] * 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--kernel", default=None)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    for name, digest in BASE_HASHES.items():
        if sha((HERE / name).read_bytes()) != digest:
            parser.error("fixed base source changed; review composition before running")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO):
        parser.error("a clean source commit is required before QEMU/build")
    spec = importlib.util.spec_from_file_location("autorestore_transport_journal", HERE / "run-journal-qemu.py")
    journal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(journal)
    base_fingerprint, base_initramfs = journal.source_fingerprint, journal.initramfs
    journal.SCRIPTS = list(dict.fromkeys(journal.SCRIPTS + OWN_SOURCES))

    def fingerprint():
        digest = hashlib.sha256(bytes.fromhex(base_fingerprint()))
        for name in OWN_SOURCES:
            data = (HERE / name).read_bytes()
            digest.update(name.encode())
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()

    def initramfs(work, kernel, wire_probe):
        archive, binaries, modules = base_initramfs(work, kernel, wire_probe)
        root = work / "root"
        generated, replacements = generate_init((root / "init").read_bytes(), args.scenario)
        (root / "usr/lib").mkdir(exist_ok=True)
        shutil.copyfile(HERE / "guest-autorestore-fragments.sh", root / "usr/lib/guest-autorestore-fragments.sh")
        (root / "init").write_bytes(generated)
        (root / "init").chmod(0o755)
        subprocess.run(["sh", "-n", str(root / "init")], check=True)
        subprocess.run(["sh", "-n", str(root / "usr/lib/guest-autorestore-fragments.sh")], check=True)
        if (root / "usr/bin/usbfs-guardian-probe").exists():
            raise ValueError("positive payload must not contain a test USBFS recovery probe")
        (evidence / "source/generated-autorestore-init.sh").write_bytes(generated)
        metadata = {"scope": SCOPE, "scenario": args.scenario, "composition": replacements, "base_sha256": BASE_HASHES,
                    "generated_init_sha256": sha(generated), "fragment_sha256": sha((root / "usr/lib/guest-autorestore-fragments.sh").read_bytes()),
                    "recovery_policy_field": "physical_connect_restore_device_ids", "storage_schema": 2, "ipc_schema": 1,
                    "recovery_authorization": "connect_original_drivers", "product_tick_milliseconds": 250,
                    "live_window_seconds": 5, "recovery_deadline_seconds": 20,
                    "recovery_deadline_origin": "after actual wait137 and cycle-2-killed control delivery; not a KILL-to-restore SLA",
                    "usbfs_test_probe": False,
                    "base_journal_verifier_used": False}
        (evidence / "autorestore-composition.json").write_text(json.dumps(metadata, indent=2) + "\n")
        names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
        packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
        archive.write_bytes(gzip.compress(packed.stdout))
        return archive, binaries, modules

    journal.source_fingerprint = fingerprint
    journal.initramfs = initramfs
    journal.verify = lambda program, kernel: verify_positive(journal, args.scenario, program, kernel)
    sys.argv = [sys.argv[0], "--evidence", str(evidence), "--timeout", str(args.timeout)]
    if args.kernel is not None:
        sys.argv += ["--kernel", args.kernel]
    code = journal.main()
    summary_path = evidence / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        summary["scope"] = SCOPE
        summary["scenario"] = args.scenario
        summary["product_automatic_recovery_test"] = True
        summary["original_journal_verifier_used"] = False
        summary["autorestore_composition_artifact"] = "autorestore-composition.json"
        summary["working_tree_guard_passed"] = not summary["working_tree_dirty"] and not summary["working_tree_dirty_after"]
        if not summary["working_tree_guard_passed"]:
            summary["result"] = "FAIL"
            summary.setdefault("error", "working tree changed during automatic recovery run")
            code = 1
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
