#!/usr/bin/env python3
"""Independent legacy, revoked-policy and external-claim recovery boundaries.

Reuses the frozen positive fixture's transport and three real I/O cycles, not
its verdict. Only external-claim payloads contain the claim-only USBFS actor.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
HERE = REPO / "scripts/qemu"
LEGACY_COMMIT = "933e720531fd96b7a23616ed687d59b816e0ce09"
SCENARIOS = ["legacy-schema1", "policy-revoked", "external-claim"]
OWN = ["run-autorestore-negative-qemu.py", "guest-autorestore-negative-fragments.sh", "usbfs-external-claim-probe.c"]
FROZEN = {"run-autorestore-qemu.py": "c7148dc070d71a570183a5a99060a70a38ba2449c0942710f8565a4d1a869ef1",
          "guest-autorestore-fragments.sh": "c6c1f0f52dda2033dc20b047fe6357ac0281707a83ec32b51aeb646aef8df830"}
SCOPE = ("Three independent simulated-USB negative contracts: real legacy helper records must remain manual, "
         "current recovery permission must still allow persisted opt-in, and external C claims must not be taken. "
         "Legacy tail uses explicit administrator restoration/read-only reconcile; other tails require a new helper "
         "before product recovery and third real I/O. No hardware or FD revocation claim.")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_legacy(helper, manifest_path):
    """Read only: never execute the old helper on the host."""
    if not helper.is_absolute() or not manifest_path.is_absolute():
        raise ValueError("absolute legacy helper and manifest paths required")
    manifest = json.loads(manifest_path.read_bytes())
    if (manifest.get("git_commit") != LEGACY_COMMIT or manifest.get("result") != "PASS"
            or manifest.get("exit_code") != 0 or manifest.get("working_tree_dirty_before") is not False
            or manifest.get("working_tree_dirty_after") is not False or manifest.get("source_guard_passed") is not True
            or manifest.get("head_guard_passed") is not True):
        raise ValueError("legacy build provenance/clean guards failed")
    binary = manifest["binary"]
    content = helper.read_bytes()
    if helper.resolve() != Path(binary["path"]).resolve() or len(content) != binary["bytes"] or sha(content) != binary["sha256"]:
        raise ValueError("legacy helper does not match its actual build manifest")
    if not content.startswith(b"\x7fELF"):
        raise ValueError("legacy producer must be an ELF")
    command = manifest["command"]
    if command[:5] != ["cargo", "build", "-p", "rustusb-daemon", "--all-features"] or "--release" not in command or "--locked" not in command or "--target-dir" not in command:
        raise ValueError("isolated locked legacy build command missing")
    target = Path(command[command.index("--target-dir") + 1]).resolve()
    if target == (REPO / "target").resolve() or not helper.resolve().is_relative_to(target):
        raise ValueError("legacy target must be isolated from current products")
    worktree = Path(manifest["worktree"])
    if not worktree.is_absolute() or worktree.resolve() == REPO:
        raise ValueError("legacy worktree must be separate")
    if subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True).strip() != LEGACY_COMMIT or subprocess.check_output(["git", "-C", str(worktree), "status", "--porcelain"]):
        raise ValueError("legacy worktree no longer at clean fixed commit")
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", LEGACY_COMMIT], cwd=REPO, text=True).splitlines()
    expected = {p for p in tracked if p in ["Cargo.toml", "Cargo.lock"] or p.startswith("vendor/") or (p.startswith("crates/") and (p.endswith(".rs") or Path(p).name == "Cargo.toml"))}
    if set(manifest["files"]) != expected:
        raise ValueError("legacy manifest must cover every fixed product input")
    for path, item in manifest["files"].items():
        frozen = subprocess.check_output(["git", "show", f"{LEGACY_COMMIT}:{path}"], cwd=REPO)
        if len(frozen) != item["bytes"] or sha(frozen) != item["sha256"] or (worktree / path).read_bytes() != frozen:
            raise ValueError("legacy input does not match fixed Git blob: " + path)
    return manifest


def generate_init(positive, original, scenario):
    text, inherited = positive.generate_init(original, "owner-death")
    text = text.decode()
    ledger = []

    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError("one exact negative composition anchor required: " + before)
        text = text.replace(before, after, 1)
        ledger.append({"before": before, "after": after, "matches": 1})

    replace("AUTORESTORE_SCENARIO=owner-death\n", f"AUTORESTORE_SCENARIO=owner-death\nNEGATIVE_SCENARIO={scenario}\n")
    replace("    . /usr/lib/guest-autorestore-fragments.sh\n", "    . /usr/lib/guest-autorestore-fragments.sh\n    . /usr/lib/guest-autorestore-negative-fragments.sh\n")
    replace("    autorestore_begin\n", "    negative_begin\n")
    replace("    autorestore_killed_epoch\n", "    negative_killed_epoch\n")
    replace("/tmp/autorestore-disk.json; do\n", "/tmp/autorestore-disk.json /tmp/negative-metadata.json /tmp/negative-helper-starts.jsonl /tmp/negative-helper-logs.jsonl /tmp/negative-readonly.jsonl /tmp/negative-windows.jsonl /tmp/negative-policies.jsonl /tmp/negative-killed-disk.json /tmp/negative-operations.json /tmp/negative-c.log /tmp/negative-c-events.jsonl /tmp/negative-c-identity.json /tmp/negative-helper-1.log /tmp/negative-helper-2.log /tmp/negative-helper-3.log; do\n")
    if scenario == "legacy-schema1":
        replace("physical_connect_restore_device_ids:[$hid,$ftdi],", "")
    return text.encode(), {"positive": inherited, "negative": ledger}


def verify_negative(base, positive, scenario, program, kernel, expected_binaries=None):
    """New negative verdict from original bytes; no inherited verdict filtering."""
    checks = []

    def check(name, condition):
        checks.append({"name": name, "result": "PASS" if condition else "FAIL"})

    log = program["server"]
    for role in base.ROLES:
        lines = program[role].splitlines()
        check(role + "_channel_and_completion", lines.count(f"RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0") == 1
              and lines.count(f"RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}") == 1 and lines.count(f"RUSTUSB_JOURNAL_RESULT: PASS role={role}") == 1)
        check(role + "_integrity", not any(term in value for value in [program[role], kernel[role]] for term in ["RUSTUSB_JOURNAL_RESULT: FAIL", *base.physical.INTEGRITY_FAILURES]))
        check(role + "_S0_not_program_channel", not any(line.startswith(("RUSTUSB_JOURNAL_", "RUSTUSB_AUTONEG_", "RUSTUSB_EXTERNAL_C ")) for line in kernel[role].splitlines()))
        check(role + "_private_network", lines.count(f"RUSTUSB_JOURNAL_ISOLATION: PASS role={role} carrier=1 ip_forward=0") == 1)
    check("server_only_negative_evidence", "RUSTUSB_AUTONEG_" not in program["client"] and "RUSTUSB_EXTERNAL_C " not in program["client"])
    metadata = positive.rows(log, "RUSTUSB_AUTONEG_METADATA ")
    check("one_mode_metadata", len(metadata) == 1 and metadata[0]["scenario"] == scenario and metadata[0]["tail_automatic_recovery"] == (scenario != "legacy-schema1"))
    if len(metadata) != 1:
        raise ValueError("negative metadata missing")
    check("guest_binary_metadata_matches_copied_payload", expected_binaries is not None and metadata[0]["binaries"] == expected_binaries)
    records = base.json_block(log, "RECORDS")
    observed = base.json_block(log, "OBSERVATIONS")
    waits = base.json_block(log, "WAITS")
    admin = base.json_block(log, "ADMIN")
    clients = base.json_block(program["client"], "CLIENT")
    pure = positive.rows(log, "RUSTUSB_AUTONEG_READONLY ")
    windows = positive.rows(log, "RUSTUSB_AUTONEG_WINDOW ")
    helpers = positive.rows(log, "RUSTUSB_AUTONEG_HELPER_START ")
    logs = positive.rows(log, "RUSTUSB_AUTONEG_HELPER_LOG ")
    policies = positive.rows(log, "RUSTUSB_AUTONEG_POLICY ")
    labels = ["initial", "normal-ready", "normal-pending", "normal-closed", "reopened-ready", "reopened-closed", "killed-ready", "killed-pending", "negative-observed", "negative-new-grant-refused"]
    if scenario == "legacy-schema1":
        labels += ["manual-restored"]
    labels += ["tail-retired", "recovered-ready", "recovered-pending", "recovered-closed"]
    check("exact_record_lifecycle", [r["label"] for r in records] == labels)
    if [r["label"] for r in records] != labels:
        raise ValueError("negative lifecycle incomplete")
    by = {r["label"]: r for r in records}
    obs = {r["label"]: r for r in observed}
    original = by["initial"]["sysfs"]
    orig_ids = {d["device_id"]: d for d in original}
    check("original_complete_fixture", len(original) == len(orig_ids) == 2 and [d["bus_id"] for d in original] == ["1-1", "1-2"]
          and [d["interfaces"] for d in original] == [[{"number": 0, "alternate": 0, "driver": "usbhid"}], [{"number": 0, "alternate": 0, "driver": "ftdi_sio"}]]
          and all(d["configuration"] == 1 and d["node_owner_mode"] == "0:600" for d in original))
    claimed = json.loads(json.dumps(original)); null = json.loads(json.dumps(original))
    for d in claimed:
        d["interfaces"][0]["driver"] = "usbfs"
    for d in null:
        d["interfaces"][0]["driver"] = None
    check("immediate_record_wait_preservation", positive.rows(log, "RUSTUSB_JOURNAL_RECORD ") == records and positive.rows(log, "RUSTUSB_JOURNAL_WAIT ") == waits)
    killed = by["killed-pending"]["journal"]
    old_leases = {r["lease_id"] for r in killed["records"]}
    for row in records:
        label, disk, pending = row["label"], row["journal"], row["pending"]
        expected_version = 1 if label == "initial" or (scenario == "legacy-schema1" and labels.index(label) < labels.index("recovered-ready")) else 2
        check(label + "_storage_and_IPC_projection", disk["schema_version"] == expected_version and pending["schema_version"] == 1 and pending["next_after"] is None
              and pending["records"] == [{k: v for k, v in r.items() if k != "recovery"} for r in disk["records"]])
        if expected_version == 1:
            check(label + "_no_legacy_promotion", all("recovery" not in r for r in disk["records"]) and "storage_schema_version" not in pending and "recovery_authorizations" not in pending)
        else:
            check(label + "_explicit_immutable_authorization", pending["storage_schema_version"] == 2 and all(r["recovery"] == "connect_original_drivers" for r in disk["records"])
                  and pending["recovery_authorizations"] == [{"lease_id": r["lease_id"], "recovery": r["recovery"]} for r in disk["records"]])
        check(label + "_journal_permissions", row["journal_directory_owner_mode"] == "0:700" and row["journal_file_owner_mode"] == "0:600")
        for kind, value in [("DISK", disk), ("PENDING", pending)]:
            begin, end = f"RUSTUSB_JOURNAL_{kind}_BEGIN label={label}", f"RUSTUSB_JOURNAL_{kind}_END label={label}"
            lines = log.splitlines()
            raw = "\n".join(lines[lines.index(begin) + 1:lines.index(end)]) + "\n" if lines.count(begin) == lines.count(end) == 1 else ""
            check(label + "_raw_" + kind, bool(raw) and json.loads(raw) == value and (kind != "DISK" or sha(raw.encode()) == row["journal_sha256"]))
    for label in ["initial", "normal-closed", "reopened-closed", "tail-retired", "recovered-closed"]:
        check(label + "_empty_and_original", by[label]["journal"]["records"] == [] and by[label]["sysfs"] == original)
    for label in ["negative-observed", "negative-new-grant-refused"] + (["manual-restored"] if scenario == "legacy-schema1" else []):
        check(label + "_old_records_preserved", by[label]["journal"] == killed)
    all_leases = []
    for epoch in ["normal", "reopened", "killed", "recovered"]:
        entries = by[epoch + "-ready"]["pending"]["records"]; identity = obs[epoch + "-ready"]["identity"]
        all_leases += [r["lease_id"] for r in entries]
        check(epoch + "_two_ordinary_grants", len(entries) == 2 and {r["baseline"]["device_id"] for r in entries} == set(orig_ids) and positive.valid_owner(identity, original))
        for entry in entries:
            baseline = entry["baseline"]; receipt = baseline["receipt"]; device = orig_ids[baseline["device_id"]]; nd = device["node_identity"].split(":")
            check(epoch + "_baseline_" + device["bus_id"], baseline["bus_id"] == device["bus_id"] and baseline["configuration"] == device["configuration"] and baseline["interfaces"] == device["interfaces"]
                  and [receipt["sysfs_device"], receipt["sysfs_inode"]] == list(map(int, device["generation"].split(":")))
                  and receipt["node_inode"] == int(nd[1]) and receipt["node_device"] == os.makedev(int(nd[2], 16), int(nd[3], 16))
                  and receipt["usb_device_number"] == device["devnum"] and receipt["bus_number"] == int(device["bus_id"].split("-")[0])
                  and entry["owner"] == {"uid": 1000, "pid": identity["pid"], "start_time": identity["starttime"], "boot_id": metadata[0]["boot_id"]})
        check(epoch + "_ready_uses_original_drivers", base.zero_resources(obs[epoch + "-ready"]["snapshot"]) and obs[epoch + "-ready"]["sysfs"] == original)
    check("eight_fresh_lease_ids", len(all_leases) == len(set(all_leases)) == 8)
    for epoch in ["normal", "killed", "recovered"]:
        row = obs[epoch + "-pending"]; snap = row["snapshot"]
        check(epoch + "_actual_pending_claims", row["sysfs"] == claimed and positive.valid_owner(row["identity"], original) and row["identity"] == obs[epoch + "-ready"]["identity"]
              and by[epoch + "-pending"]["journal"] == by[epoch + "-ready"]["journal"] and snap["buffer_bytes_reserved"] > 0
              and len(snap["devices"]) == 2 and all(d["leased"] and d["pending_urbs"] >= 1 and d["usbfs_inflight"] >= 1 for d in snap["devices"]))
    server_waits = [w for w in waits if w["event"] == "server_wait"]
    check("five_actual_server_waits", [w["label"] for w in server_waits] == ["normal", "reopened", "killed", "negative-new-grant", "recovered"])
    wait_by = {w["label"]: w for w in server_waits}
    events = {e: base.epoch_events(log, e) for e in ["normal", "reopened", "killed", "negative-new-grant", "recovered"]}
    for epoch in ["normal", "reopened", "recovered"]:
        stopped = [e for e in events[epoch] if e["event"] == "stopped"]
        check(epoch + "_wait0_cleanup0", wait_by[epoch]["wait_code"] == 0 and len(stopped) == 1 and events[epoch][-1] == stopped[0] and base.zero_resources(stopped[0]["snapshot"]))
    kw = wait_by["killed"]; ki = obs["killed-pending"]["identity"]
    check("one_owned_actual_KILL137", kw["wait_code"] == 137 and kw["signal"] == 9 and kw["signal_count"] == 1 and kw["sender_uid"] == 1000 and kw["pid"] == ki["pid"] and kw["starttime"] == ki["starttime"]
          and kw["final_backend_snapshot"] is None and kw["final_backend_snapshot_observable"] is False and not any(e["event"] == "stopped" for e in events["killed"]))
    check("one_actual_signal_marker", [line for line in log.splitlines() if line.startswith("RUSTUSB_JOURNAL_SIGNAL:")] == [f"RUSTUSB_JOURNAL_SIGNAL: PASS signal=9 count=1 sender_uid=1000 pid={kw['pid']} starttime={kw['starttime']}"])
    error_begin = "RUSTUSB_JOURNAL_STDERR_BEGIN epoch=negative-new-grant"
    error_end = "RUSTUSB_JOURNAL_STDERR_END epoch=negative-new-grant"
    lines = log.splitlines()
    refusal_error = "\n".join(lines[lines.index(error_begin) + 1:lines.index(error_end)]) if lines.count(error_begin) == lines.count(error_end) == 1 else ""
    check("new_grant_explicit_pending_refusal", wait_by["negative-new-grant"]["wait_code"] != 0 and not any(e["event"] == "ready" for e in events["negative-new-grant"])
          and "unresolved physical grant blocks new authorization; inspect physical-pending and reconcile the original baseline" in refusal_error)
    check("three_actual_helper_epochs", [h["epoch"] for h in helpers] == [1, 2, 3] and len({h["pid"] for h in helpers}) == 3
          and all(h["uids"] == [0] * 4 and h["starttime"] > 0 and any(h["exe"] == b["path"] and h["sha256"] == b["sha256"] for b in metadata[0]["binaries"]["helpers"]) for h in helpers))
    check("correct_helper_versions", [h["exe"] for h in helpers] == (["/usr/libexec/rustusb-helper-legacy"] if scenario == "legacy-schema1" else ["/usr/bin/rustusb-helper"]) + ["/usr/bin/rustusb-helper"] * 2)
    hw = [w for w in waits if w["event"] == "negative_helper_wait"]
    check("two_helper_stops_actually_waited", len(hw) == 2 and all(w["epoch"] == i + 1 and w["pid"] == helpers[i]["pid"] and w["starttime"] == helpers[i]["starttime"] and w["wait_code"] == 0 and w["signal"] == 15 for i, w in enumerate(hw)))
    check("helper_stopped_before_KILL", log.index('RUSTUSB_JOURNAL_WAIT {"event":"negative_helper_wait"') < log.index("RUSTUSB_JOURNAL_SIGNAL:"))
    check("three_policy_snapshots", [p["helper_epoch"] for p in policies] == [1, 2, 3] and all(sha(p["raw"].encode()) == p["sha256"] and json.loads(p["raw"]) == p["policy"] for p in policies))
    for index, p in enumerate(policies):
        policy = p["policy"]; ids = [d["device_id"] for d in original]
        expected = {"allowed_uids": [1000], "export_device_ids": [], "physical_device_ids": ids, "peers": [], "trusted_network": True}
        if not (scenario == "legacy-schema1" and index == 0):
            expected["physical_connect_restore_device_ids"] = [] if scenario == "policy-revoked" and index == 1 else ids
        check(f"policy_{index + 1}_exact_scope", policy == expected)
    for row in logs:
        check("helper_log_" + row["label"] + "_epoch" + str(row["helper"]["epoch"]), row["helper"] == helpers[row["helper"]["epoch"] - 1]
              and [{k: op[k] for k in ["lease_id", "phase"]} for op in positive.helper_operations(row["log"])] == [{k: op[k] for k in ["lease_id", "phase"]} for op in row["operations"]])
    before_stop = [row for row in logs if row["label"] == "before-stop"]
    check("initial_live_owner_no_auto_operation", len(before_stop) == 2 and before_stop[0]["operations"] == [])
    neg_logs = [row for row in logs if row["helper"]["epoch"] == 2]
    for row in neg_logs:
        ops = row["operations"]
        if scenario == "external-claim":
            check(row["label"] + "_two_once_only_external_failures", len(ops) == 4 and all([o["phase"] for o in ops if o["lease_id"] == lease] == ["attempt", "failed"] for lease in old_leases)
                  and all("physical device is unsafe, already exported, claimed, or incompletely described" in o["raw"] or "automatic physical recovery found a changed interface, alternate or external driver" in o["raw"] for o in ops if o["phase"] == "failed"))
        else:
            check(row["label"] + "_no_auto_operation_without_authority", ops == [])
    final = [row for row in logs if row["label"] == "final"]
    check("one_final_current_helper_log", len(final) == 1 and final[0]["helper"] == helpers[2])
    final_ops = final[0]["operations"]
    check("tail_correct_recovery_contract", final_ops == [] if scenario == "legacy-schema1" else len(final_ops) == 4 and all([o["phase"] for o in final_ops if o["lease_id"] == lease] == ["attempt", "retired"] for lease in old_leases))
    check("readonly_sequence_order", [r["sequence"] for r in pure] == list(range(1, len(pure) + 1)) and all(a["tick"] <= b["tick"] for a, b in zip(pure, pure[1:])))
    for row in pure:
        check("readonly_" + str(row["sequence"]), sha(row["journal_raw"].encode()) == row["journal_sha256"] and json.loads(row["journal_raw"]) == row["journal"] and row["pending_api_called"] is False
              and row["journal_directory_owner_mode"] == "0:700" and row["journal_file_owner_mode"] == "0:600")
    stopped_primary = [r for r in pure if r["label"] == "killed-with-helper-stopped"]
    check("KILL_primary_after_real_helper_stop", len(stopped_primary) == 1 and stopped_primary[0]["helper"] is None and stopped_primary[0]["external"] is None and stopped_primary[0]["journal"] == killed and stopped_primary[0]["sysfs"] == null)
    rejected = [r for r in pure if r["label"] == "rejected"]
    rejection = positive.rows(log, "RUSTUSB_AUTONEG_REJECTION_PRIMARY ")
    check("negative_primary_is_actual_final_pure_record", len(rejection) == 1 and rejected and rejection[0] == rejected[-1])
    for row in rejected:
        check("rejected_" + str(row["sequence"]) + "_immutable_same_generation", row["journal"] == killed and row["sysfs"] == (claimed if scenario == "external-claim" else null) and row["helper"] == helpers[1])
    expected_windows = ["rejected", "manual-restored-still-pending"] if scenario == "legacy-schema1" else ["rejected", "released-no-retry", "tail-automatic-recovery"] if scenario == "external-claim" else ["rejected", "tail-automatic-recovery"]
    check("exact_observation_windows", [w["label"] for w in windows] == expected_windows)
    for window in windows:
        values = [r for r in pure if r["label"] == window["label"]]
        if window["label"] != "tail-automatic-recovery":
            minimum = 2000 if window["label"] == "rejected" else 500
            check(window["label"] + "_full_readonly_window", len(values) >= 2 and window["end_tick"] - window["start_tick"] >= minimum and values[-1]["tick"] - window["start_tick"] >= minimum and values[0]["tick"] >= window["start_tick"])
        else:
            check("tail_bounded_after_new_helper", values and 0 <= window["end_tick"] - window["start_tick"] < 2000 and all(r["helper"] == helpers[2] and r["external"] is None for r in values))
    if scenario == "legacy-schema1":
        manual = [r for r in pure if r["label"] == "manual-restored-still-pending"]
        check("old_manual_stays_pending_even_restored_and_restarted", manual and all(r["journal"] == killed and r["sysfs"] == original and r["helper"] == helpers[2] for r in manual))
        check("two_explicit_root_readonly_reconciliations", len(admin) == 2 and {r["lease_id"] for r in admin} == old_leases
              and all(r["phase"] == "legacy-manual-retired" and r["uid"] == 0 and r["exit_code"] == 0 and json.loads(r["stdout"]) == {"lease_id": r["lease_id"], "observation": "restored", "status": "retired"} for r in admin))
        check("no_legacy_auto_recovery_primary", positive.rows(log, "RUSTUSB_AUTONEG_RECOVERY_PRIMARY ") == [])
    else:
        primary = positive.rows(log, "RUSTUSB_AUTONEG_RECOVERY_PRIMARY ")
        tail = [r for r in pure if r["label"] == "tail-automatic-recovery"]
        check("product_tail_pure_primary_before_API", admin == [] and len(primary) == 1 and primary[0] == tail[-1] and primary[0]["sysfs"] == original and primary[0]["journal"] == {"schema_version": 2, "records": []}
              and log.index("RUSTUSB_AUTONEG_RECOVERY_PRIMARY ") < log.index("RUSTUSB_JOURNAL_PENDING_BEGIN label=tail-retired"))
    c_events = positive.rows(log, "RUSTUSB_EXTERNAL_C ")
    c_waits = [w for w in waits if w["event"] == "external_c_wait"]
    check("only_actual_expected_wait_roles", len(waits) == len(server_waits) + len(hw) + len(c_waits))
    if scenario == "external-claim":
        control = positive.rows(log, "RUSTUSB_AUTONEG_C_CONTROL ")
        check("C_root_only_control_FIFO", control == [{"directory_owner_mode": "0:700", "fifo_owner_mode": "0:600", "is_fifo": True, "shell_holds_control_fd": False}])
        c_before = [r for r in pure if r["label"] == "c-claimed-before-helper"]
        check("C_claimed_while_helper_stopped", len(c_before) == 1 and c_before[0]["helper"] is None and c_before[0]["external"] is not None and c_before[0]["journal"] == killed and c_before[0]["sysfs"] == claimed)
        ready = [e for e in c_events if e["event"] == "ready"]
        check("one_external_C_and_actual_wait0", len(ready) == 1 and ready[0]["uid"] == 0 and len(c_waits) == 1 and c_waits[0]["wait_code"] == 0 and c_waits[0]["pid"] == ready[0]["pid"] and c_waits[0]["starttime"] == ready[0]["starttime"])
        ioctls = [e for e in c_events if e["event"] == "ioctl"]
        check("C_only_two_plain_claim_release_pairs", [(e["op"], e["device"]) for e in ioctls] == [("claim", 0), ("claim", 1), ("release", 0), ("release", 1)] and all(e["interface"] == 0 and e["rc"] == e["errno"] == 0 for e in ioctls))
        close = [e for e in c_events if e["event"] == "close"]
        check("C_actual_close_EBADF", [e["device"] for e in close] == [0, 1] and all(e["close_rc"] == 0 and e["getfd_rc"] == -1 and e["getfd_errno"] == 9 for e in close))
        states = [e for e in c_events if e["event"] == "state"]
        expected_states = [(i, phase) for i in [0, 1] for phase in ["before-open", "independently-opened", "claimed"]] + [(i, phase) for i in [0, 1] for phase in ["before-release", "released", "closed"]]
        check("C_exact_states_no_unknown_or_fatal", [(s["device"], s["phase"]) for s in states] == expected_states and len(c_events) == 20 and sum(e["event"] == "done" for e in c_events) == 1)
        for i, device in enumerate(original):
            receipt = next(r["baseline"]["receipt"] for r in killed["records"] if r["baseline"]["bus_id"] == device["bus_id"])
            check("C_full_original_receipt_" + str(i), all(s["pid"] == ready[0]["pid"] and s["starttime"] == ready[0]["starttime"] and s["node_dev"] == int(device["node_identity"].split(":")[0]) and s["node_inode"] == receipt["node_inode"] and s["node_rdev"] == receipt["node_device"]
                  and s["sysfs_dev"] == receipt["sysfs_device"] and s["sysfs_inode"] == receipt["sysfs_inode"] and s["bus_number"] == receipt["bus_number"] and s["device_number"] == receipt["usb_device_number"] and s["configuration"] == device["configuration"]
                  and s["interfaces"] == [{"number": 0, "alternate": 0, "driver": "usbfs" if s["phase"] in ["claimed", "before-release"] else None}] for s in states if s["device"] == i))
        check("C_claims_precede_helper_start_and_primary", log.index('RUSTUSB_EXTERNAL_C {"event":"ready"') < log.index('RUSTUSB_AUTONEG_HELPER_START {"epoch":2') < log.index("RUSTUSB_AUTONEG_REJECTION_PRIMARY "))
        check("C_held_through_rejection", all(r["external"] and r["external"]["pid"] == ready[0]["pid"] and r["external"]["starttime"] == ready[0]["starttime"] and r["external"]["uids"] == [0] * 4 and r["external"]["sha256"] == metadata[0]["binaries"]["external_claim_probe"]["sha256"] and len(r["external"]["fds"]) == 2 for r in rejected))
        check("C_actual_close_wait_before_recovering_helper", log.index('RUSTUSB_JOURNAL_WAIT {"event":"external_c_wait"') < log.index('RUSTUSB_AUTONEG_HELPER_START {"epoch":3'))
        released = [r for r in pure if r["label"] == "released-no-retry"]
        check("C_closed_same_helper_does_not_retry", released and all(r["external"] is None and r["helper"] == helpers[1] and r["journal"] == killed and r["sysfs"] == null for r in released))
    else:
        check("no_external_actor_in_other_negative_modes", c_events == [] and c_waits == [] and all(r["external"] is None for r in pure) and metadata[0]["binaries"]["external_claim_probe"] is None)
    positive.verify_clients(base, program, clients, check)
    check("ordinary_FD_authorization_gates", "RUSTUSB_JOURNAL_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600" in log.splitlines()
          and "RUSTUSB_JOURNAL_DISK_GATE: PASS ordinary_uid1000=denied directory=root0700 file=root0600" in log.splitlines())
    return {"checks": checks, "scenario": scenario, "metadata": metadata, "records": records, "observations": observed, "waits": waits, "admin": admin,
            "client_outcomes": clients, "readonly_observations": pure, "windows": windows, "helpers": helpers, "helper_logs": logs, "policies": policies, "external_events": c_events,
            "evidence_collection_complete": all(f"RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={r}" in program[r].splitlines() for r in base.ROLES),
            "tail_automatic_recovery": scenario != "legacy-schema1", "killed_epoch_terminal_resources": None, "killed_epoch_terminal_resources_observed": False,
            "positive_verifier_used": False, "original_journal_verifier_used": False,
            "readonly_primary_scope": "disk/sysfs before pending API and new grant; start_helper doctor/Status calls precede the window"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--legacy-helper", type=Path)
    parser.add_argument("--legacy-manifest", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--kernel")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    legacy = None
    if args.scenario == "legacy-schema1":
        if args.legacy_helper is None or args.legacy_manifest is None:
            parser.error("legacy-schema1 requires --legacy-helper and --legacy-manifest")
        legacy = validate_legacy(args.legacy_helper, args.legacy_manifest)
    elif args.legacy_helper is not None or args.legacy_manifest is not None:
        parser.error("legacy artifacts are allowed only for legacy-schema1")
    for name, expected in FROZEN.items():
        if sha((HERE / name).read_bytes()) != expected:
            parser.error("review frozen positive source drift before composition")
    positive = load_module("negative_positive_utilities", HERE / "run-autorestore-qemu.py")
    for name, expected in positive.BASE_HASHES.items():
        if sha((HERE / name).read_bytes()) != expected:
            parser.error("review frozen journal source drift before composition")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO):
        parser.error("clean source commit required before build/QEMU")
    base = load_module("negative_journal_transport", HERE / "run-journal-qemu.py")
    evidence = args.evidence.resolve()
    base_fingerprint, base_initramfs = base.source_fingerprint, base.initramfs
    guest_info = {}
    base.SCRIPTS = list(dict.fromkeys(base.SCRIPTS + list(FROZEN) + OWN))

    def fingerprint():
        digest = hashlib.sha256(bytes.fromhex(base_fingerprint()))
        for name in list(FROZEN) + OWN:
            data = (HERE / name).read_bytes(); digest.update(name.encode()); digest.update(len(data).to_bytes(8, "big")); digest.update(data)
        if legacy is not None:
            digest.update(args.legacy_manifest.read_bytes()); digest.update(args.legacy_helper.read_bytes())
            for path in sorted(legacy["files"]):
                digest.update((Path(legacy["worktree"]) / path).read_bytes())
        return digest.hexdigest()

    def initramfs(work, kernel, wire_probe):
        archive, binaries, modules = base_initramfs(work, kernel, wire_probe)
        root = work / "root"
        generated, ledger = generate_init(positive, (root / "init").read_bytes(), args.scenario)
        (root / "usr/lib").mkdir(exist_ok=True)
        for fragment in ["guest-autorestore-fragments.sh", "guest-autorestore-negative-fragments.sh"]:
            shutil.copyfile(HERE / fragment, root / "usr/lib" / fragment)
            subprocess.run(["sh", "-n", str(root / "usr/lib" / fragment)], check=True)
        info = {"helpers": [{"path": "/usr/bin/rustusb-helper", "sha256": binaries["rustusb-helper"]}], "external_claim_probe": None,
                "legacy_commit": LEGACY_COMMIT if legacy is not None else None}
        if legacy is not None:
            existing_libraries = {str(path.relative_to(root)): sha(path.read_bytes()) for path in root.rglob("*") if path.is_file() and any(part in ["lib", "lib64"] for part in path.relative_to(root).parts)}
            base.native.copy_binary(args.legacy_helper, "/usr/libexec/rustusb-helper-legacy", root)
            if any(sha((root / name).read_bytes()) != value for name, value in existing_libraries.items()):
                raise ValueError("legacy runtime dependencies changed an existing current payload library")
            if sha((root / "usr/libexec/rustusb-helper-legacy").read_bytes()) != legacy["binary"]["sha256"]:
                raise ValueError("guest legacy ELF copy mismatch")
            info["helpers"].append({"path": "/usr/libexec/rustusb-helper-legacy", "sha256": legacy["binary"]["sha256"]})
            (evidence / "legacy").mkdir()
            shutil.copyfile(args.legacy_manifest, evidence / "legacy/manifest.json")
            for path in legacy["files"]:
                destination = evidence / "legacy/source" / path; destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(legacy["worktree"]) / path, destination)
        if args.scenario == "external-claim":
            probe = work / "usbfs-external-claim-probe"
            command = ["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic", str(HERE / "usbfs-external-claim-probe.c"), "-o", str(probe)]
            with (evidence / "external-claim-build.log").open("wb") as out:
                subprocess.run(command, stdout=out, stderr=subprocess.STDOUT, check=True)
            base.native.copy_binary(probe, "/usr/bin/usbfs-external-claim-probe", root)
            info["external_claim_probe"] = {"sha256": sha(probe.read_bytes()), "command": command, "cc_version": subprocess.check_output(["cc", "--version"], text=True),
                                            "uapi_sha256": sha(Path("/usr/include/linux/usbdevice_fs.h").read_bytes())}
        elif (root / "usr/bin/usbfs-external-claim-probe").exists():
            raise ValueError("external actor in a non-external payload")
        guest_info.update(info)
        (root / "usr/lib/negative-binaries.json").write_text(json.dumps(info) + "\n")
        (root / "init").write_bytes(generated); (root / "init").chmod(0o755)
        subprocess.run(["sh", "-n", str(root / "init")], check=True)
        (evidence / "source/generated-negative-init.sh").write_bytes(generated)
        (evidence / "negative-composition.json").write_text(json.dumps({"scenario": args.scenario, "scope": SCOPE, "base_hashes": {**positive.BASE_HASHES, **FROZEN}, "composition": ledger,
            "generated_init_sha256": sha(generated), "binaries": info, "legacy_manifest_sha256": sha(args.legacy_manifest.read_bytes()) if legacy else None}, indent=2) + "\n")
        names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
        packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
        archive.write_bytes(gzip.compress(packed.stdout))
        return archive, binaries, modules

    base.source_fingerprint = fingerprint; base.initramfs = initramfs
    base.verify = lambda program, kernel: verify_negative(base, positive, args.scenario, program, kernel, guest_info)
    sys.argv = [sys.argv[0], "--evidence", str(evidence), "--timeout", str(args.timeout)]
    if args.kernel is not None:
        sys.argv += ["--kernel", args.kernel]
    code = base.main()
    path = evidence / "summary.json"
    if path.exists():
        summary = json.loads(path.read_text()); summary["scope"] = SCOPE; summary["scenario"] = args.scenario
        summary["positive_verifier_used"] = False; summary["original_journal_verifier_used"] = False
        summary["legacy_producer"] = {"commit": LEGACY_COMMIT, "binary": legacy["binary"], "only_old_helper": True} if legacy else None
        summary["tail_automatic_recovery"] = args.scenario != "legacy-schema1"
        summary["working_tree_guard_passed"] = not summary["working_tree_dirty"] and not summary["working_tree_dirty_after"]
        if not summary["working_tree_guard_passed"]:
            summary["result"] = "FAIL"; summary.setdefault("error", "working tree changed during negative run"); code = 1
        path.write_text(json.dumps(summary, indent=2) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
