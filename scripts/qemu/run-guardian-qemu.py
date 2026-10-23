#!/usr/bin/env python3
"""Test independent USBFS OFDs in the disposable journal QEMU fixture.

This is a guest-administrator mechanism experiment, not product automatic
recovery. The existing journal assertions, I/O and observation window remain.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
HERE = REPO / "scripts/qemu"
BASE_HASHES = {
    "run-journal-qemu.py": "1cefbc14bc45c9e9b5c9f6f0d9784be834fe3ee5856f8bc2937b7baabbbc01aa",
    "guest-journal-init.sh": "2236c3080d17e447c4d9cdec495609ab6c7d3976d6c92306e3b1286e9d94d8d4",
}
OWN_SOURCES = ["run-guardian-qemu.py", "guest-guardian-fragments.sh", "usbfs-guardian-probe.c"]
SCOPE = ("Disposable guest-root USBFS mechanism experiment: independent B, retained A duplicate, "
         "external C OFD refusal, exact original-driver restoration via B, read-only root "
         "reconciliation and third real I/O. No product automatic recovery or FD revocation.")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def generate_init(original):
    """Fail on source drift; retain the original journal success assertions."""
    if sha(original) != BASE_HASHES["guest-journal-init.sh"]:
        raise ValueError("journal guest source drifted; review composition anchors")
    text = original.decode()
    replacements = [
        ("/tmp/client-outcomes.jsonl; do\n",
         "/tmp/client-outcomes.jsonl /tmp/guardian-probe.log /tmp/guardian-probe.jsonl /tmp/guardian-record.json /tmp/guardian-reply.json /tmp/guardian-pending.json /tmp/guardian-kill-wait.json /tmp/guardian-final-wait.json /tmp/guardian-full-interfaces.json /tmp/guardian-fds.json; do\n"),
        ("    STAGE=kill-service\n    start_ready killed\n",
         "    STAGE=kill-service\n    . /usr/lib/guest-guardian-fragments.sh\n    guardian_start\n    start_ready killed\n"),
        ("    KILL_PID=$SERVER_PID; KILL_START=$SERVER_START\n",
         "    guardian_arm\n    KILL_PID=$SERVER_PID; KILL_START=$SERVER_START\n"),
        ("    send_control 192.0.2.2 cycle-2-killed\n",
         "    send_control 192.0.2.2 cycle-2-killed\n    guardian_after_kill\n"),
        ('    if wait "$KILL_PID"; then code=0; else code=$?; fi\n',
         '    code=$GUARDIAN_KILL_WAIT # Actual child wait already recorded exactly once.\n'),
        ('    printf \'%s\' "$HID_BUS:1.0" > /sys/bus/usb/drivers/usbhid/bind\n'
         '    printf \'%s\' "$FTDI_BUS:1.0" > /sys/bus/usb/drivers/ftdi_sio/bind\n',
         '    guardian_restore\n'),
    ]
    ledger = []
    for before, after in replacements:
        if text.count(before) != 1:
            raise ValueError("guardian composition requires one exact anchor: " + before)
        text = text.replace(before, after, 1)
        ledger.append({"before": before, "after": after, "matches": 1})
    return text.encode(), ledger


def records(log, prefix):
    return [json.loads(line[len(prefix):]) for line in log.splitlines() if line.startswith(prefix)]


def verify_guardian(base, program, kernel):
    result = base(program, kernel)
    base_checks = list(result["checks"])
    checks = []

    def check(name, condition):
        checks.append({"name": "guardian_" + name, "result": "PASS" if condition else "FAIL"})

    log = program["server"]
    events = records(log, "RUSTUSB_GUARDIAN_PROBE ")
    observed = records(log, "RUSTUSB_GUARDIAN_RECORD ")
    waits = records(log, "RUSTUSB_GUARDIAN_WAIT ")
    labels = ["b-opened-before-import", "dup-armed-live", "dup-retained-after-wait137", "dup-refusal-preserved",
              "duplicate-closed-b-retained", "independent-b-claimed", "independent-b-released",
              "external-c-claimed", "external-c-preserved", "external-c-closed", "before-b-connect", "b-restored-original"]
    check("original_184_checks_preserved", len(base_checks) == 184)
    check("server_only_complete_evidence", log.splitlines().count("RUSTUSB_GUARDIAN_COLLECTION_COMPLETE role=server") == 1
          and not any("RUSTUSB_GUARDIAN_" in text for text in [program["client"], *kernel.values()]))
    check("exact_labels", [r.get("label") for r in observed] == labels)
    check("no_probe_fatal_or_unknown_event", all(e.get("event") in {"state", "ready", "ofd_compare", "external_ofd", "ioctl", "close", "done"} for e in events))
    if [r.get("label") for r in observed] != labels:
        raise ValueError("guardian primary observations missing; original streams retained")
    by_label = {r["label"]: r for r in observed}
    ready = [e for e in events if e.get("event") == "ready"]
    check("one_root_probe", len(ready) == 1 and ready[0].get("uid") == 0)
    if len(ready) != 1:
        raise ValueError("guardian identity missing")
    pid, start = ready[0]["pid"], ready[0]["starttime"]
    originals = result["records"][0]["sysfs"]
    journal = next(r for r in result["records"] if r["label"] == "killed-pending")["journal"]["records"]
    expected_counts = [2, 4, 4, 4, 2, 2, 2, 4, 4, 2, 2, 2]
    expected_drivers = [["usbhid", "ftdi_sio"], ["usbfs"] * 2, ["usbfs"] * 2, ["usbfs"] * 2,
                        [None] * 2, ["usbfs"] * 2, [None] * 2, ["usbfs"] * 2, ["usbfs"] * 2,
                        [None] * 2, [None] * 2, ["usbhid", "ftdi_sio"]]
    for observation, count, drivers in zip(observed, expected_counts, expected_drivers):
        label = observation["label"]
        check(label + "_same_root_probe_fd_count", observation["guardian"]["pid"] == pid and observation["guardian"]["starttime"] == start
              and observation["guardian"]["uids"] == [0] * 4 and len(observation["guardian"]["fds"]) == count)
        expected = json.loads(json.dumps(originals))
        for device, driver in zip(expected, drivers):
            device["interfaces"] = [{"number": 0, "alternate": 0, "driver": driver}]
        check(label + "_complete_same_generation_state", observation["original"] == originals and observation["sysfs"] == expected
              and observation["full_interfaces"] == [{"bus_id": d["bus_id"], "interfaces": d["interfaces"]} for d in expected])
        records_expected = [] if label == labels[0] else journal
        check(label + "_unmodified_pending_and_disk", observation["journal"]["records"] == records_expected
              and observation["pending"]["records"] == records_expected
              and (not records_expected or all(r["owner"]["boot_id"] == observation["boot_id"] for r in records_expected)))
    check("observation_order", all(a["tick"] <= b["tick"] for a, b in zip(observed, observed[1:])))
    killed = next(w for w in result["waits"] if w.get("label") == "killed")
    check("actual_single_child_wait137_and_probe_wait0", len(waits) == 2 and waits[0] == {"pid": killed["pid"], "starttime": killed["starttime"], "wait_code": 137, "signal": 9, "signal_count": 1, "terminal_resources": None}
          and waits[1] == {"pid": pid, "starttime": start, "wait_code": 0, "role": "guardian"})
    check("wait_persisted_before_duplicate_release", log.index("RUSTUSB_GUARDIAN_WAIT ") < log.index('"label":"dup-retained-after-wait137"'))
    ofds = [e for e in events if e["event"] == "ofd_compare"]
    check("actual_a_dup_same_b_different", [e.get("device") for e in ofds] == [0, 1] and all(e["seq"] == 1 and e["owner_pid"] == killed["pid"] and e["owner_starttime"] == killed["starttime"] and e["a_d"] == 0 and e["a_b"] in [1, 2, 3] and e["b_d"] in [1, 2, 3] for e in ofds))
    external = [e for e in events if e["event"] == "external_ofd"]
    check("actual_external_c_different_ofd_same_probe", [e.get("device") for e in external] == [0, 1] and all(e["seq"] == 11 and e["pid"] == pid and e["b_c"] in [1, 2, 3] for e in external))
    operations = [(2, "claim", "B", 0, False), (3, "connect", "B", 0, False), (4, "claim", "B", 1, False), (5, "connect", "B", 1, False),
                  (7, "claim", "B", 0, True), (8, "claim", "B", 1, True), (9, "release", "B", 0, True), (10, "release", "B", 1, True),
                  (12, "claim", "C", 0, True), (13, "claim", "C", 1, True), (14, "claim", "B", 0, False), (15, "connect", "B", 0, False),
                  (16, "claim", "B", 1, False), (17, "connect", "B", 1, False), (18, "release", "C", 0, True), (19, "release", "C", 1, True),
                  (21, "connect", "B", 0, True), (22, "connect", "B", 1, True)]
    ioctls = [e for e in events if e["event"] == "ioctl"]
    check("exact_nonstealing_ioctl_sequence", len(ioctls) == len(operations) and all((e["seq"], e["op"], e["handle"], e["device"]) == expected[:4]
          and e["interface"] == 0 and (((e["rc"] >= 0 if expected[1] == "connect" else e["rc"] == 0) and e["errno"] == 0)
          if expected[4] else (e["rc"] == -1 and e["errno"] == 16)) for e, expected in zip(ioctls, operations)))
    closed = [e for e in events if e["event"] == "close"]
    check("dup_c_b_explicit_close_and_ebadf", [(e["seq"], e["handle"], e["device"]) for e in closed] == [(s, h, i) for s, h in [(6, "D"), (20, "C"), (23, "B")] for i in [0, 1]]
          and all(e["close_rc"] == 0 and e["getfd_rc"] == -1 and e["getfd_errno"] == 9 for e in closed))
    done = [e for e in events if e["event"] == "done"]
    check("all_23_bounded_commands_completed_once", [e["seq"] for e in done] == list(range(1, 24)))
    states = [e for e in events if e["event"] == "state"]
    check("all_24_full_probe_snapshots", [(e["seq"], e["device"]) for e in states] == [(s, i) for s in range(24) for i in [0, 1]])
    receipts = {r["baseline"]["bus_id"]: r["baseline"]["receipt"] for r in journal}
    for i, bus in enumerate(["1-1", "1-2"]):
        receipt = receipts[bus]
        selected = [e for e in states if e["device"] == i]
        check(f"device_{i}_b_identity_and_receipt_retained", len(selected) == 24 and len({e["b_fd"] for e in selected}) == 1
              and all(e["pid"] == pid and e["starttime"] == start and e["node_rdev"] == receipt["node_device"] and e["node_inode"] == receipt["node_inode"]
                      and e["sysfs_dev"] == receipt["sysfs_device"] and e["sysfs_inode"] == receipt["sysfs_inode"] and e["configuration"] == 1 for e in selected))
    check("probe_closed_before_root_reconcile_and_third_io", log.index('"role":"guardian"') < log.index('"label":"guest-admin-restored"')
          and [o["cycle"] for o in result["client_outcomes"]] == [1, 2, 3])
    result["checks"] = base_checks + checks
    result["guardian"] = {"scope": SCOPE, "base_journal_check_count": len(base_checks), "checks": checks,
                          "observations": observed, "events": events, "waits": waits, "product_automatic_recovery": False,
                          "external_c_is_another_process": False, "killed_terminal_resources_observed": False}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=None)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    for name, digest in BASE_HASHES.items():
        if sha((HERE / name).read_bytes()) != digest:
            parser.error("base harness changed; review its exact source before composing")
    spec = importlib.util.spec_from_file_location("guardian_base_journal", HERE / "run-journal-qemu.py")
    journal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(journal)
    base_fingerprint, base_initramfs, base_verify = journal.source_fingerprint, journal.initramfs, journal.verify
    journal.SCRIPTS = list(dict.fromkeys(journal.SCRIPTS + OWN_SOURCES))

    def fingerprint():
        digest = hashlib.sha256(bytes.fromhex(base_fingerprint()))
        for name in OWN_SOURCES:
            data = (HERE / name).read_bytes()
            digest.update(name.encode()); digest.update(len(data).to_bytes(8, "big")); digest.update(data)
        return digest.hexdigest()

    def initramfs(work, kernel, wire_probe):
        archive, binaries, modules = base_initramfs(work, kernel, wire_probe)
        root = work / "root"
        generated, replacements = generate_init((root / "init").read_bytes())
        probe = work / "usbfs-guardian-probe"
        command = ["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic", str(HERE / "usbfs-guardian-probe.c"), "-o", str(probe)]
        with (evidence / "guardian-build.log").open("wb") as output:
            subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
        journal.native.copy_binary(probe, "/usr/bin/usbfs-guardian-probe", root)
        (root / "usr/lib").mkdir(exist_ok=True)
        shutil.copyfile(HERE / "guest-guardian-fragments.sh", root / "usr/lib/guest-guardian-fragments.sh")
        (root / "init").write_bytes(generated)
        (root / "init").chmod(0o755)
        subprocess.run(["sh", "-n", str(root / "init")], check=True)
        subprocess.run(["sh", "-n", str(root / "usr/lib/guest-guardian-fragments.sh")], check=True)
        (evidence / "source/generated-guardian-init.sh").write_bytes(generated)
        metadata = {"scope": SCOPE, "composition": replacements, "base_sha256": BASE_HASHES,
                    "generated_init_sha256": sha(generated), "probe_build_command": command,
                    "cc_version": subprocess.check_output(["cc", "--version"], text=True),
                    "probe_sha256": sha(probe.read_bytes()), "copied_probe_sha256": sha((root / "usr/bin/usbfs-guardian-probe").read_bytes()),
                    "uapi_header_sha256": {name: sha(Path(name).read_bytes()) for name in ["/usr/include/linux/usbdevice_fs.h", "/usr/include/linux/kcmp.h"]}}
        (evidence / "guardian-composition.json").write_text(json.dumps(metadata, indent=2) + "\n")
        names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
        packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
        archive.write_bytes(gzip.compress(packed.stdout))
        return archive, binaries, modules

    journal.source_fingerprint = fingerprint
    journal.initramfs = initramfs
    journal.verify = lambda program, kernel: verify_guardian(base_verify, program, kernel)
    sys.argv = [sys.argv[0], "--evidence", str(evidence), "--timeout", str(args.timeout)]
    if args.kernel is not None:
        sys.argv += ["--kernel", args.kernel]
    code = journal.main()
    summary_path = evidence / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        summary["base_journal_scope"] = summary["scope"]
        summary["scope"] = SCOPE
        summary["product_automatic_recovery"] = False
        summary["guardian_composition_artifact"] = "guardian-composition.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
