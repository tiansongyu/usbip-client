#!/usr/bin/env python3
"""Gate real USBFS host-state syscall entry in two disposable x86_64 guests.

Only the guest-root tracer pauses the ordinary server's matching blocking TID.
No host USB, ptrace, module, TAP, listener or network configuration is used.
This proves syscall-entry ordering, not an in-kernel active USB ioctl window.
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
import secrets
import shutil
import socket
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
OWN = ["run-host-state-qemu.py", "guest-host-state-init.sh", "host-state-wire-probe.rs", "usbfs-syscall-gate.c"]
MODULES = ["usb-common", "usbcore", "e1000", "mii", "usbnet", "cdc_ether", "uhci-hcd"]
REQUESTS = {"configuration": 0x80045505, "alternate": 0x80085504, "clear": 0x80045515}
MUTATIONS = {"claim", "release", "configuration", "alternate", "clear", "driver"}
spec = importlib.util.spec_from_file_location("native_qemu", REPO / "scripts/qemu/run-native-qemu.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def source_fingerprint():
    digest = hashlib.sha256(bytes.fromhex(native.source_fingerprint()))
    paths = [REPO / "scripts/qemu" / name for name in OWN]
    paths += [path for path in (REPO / "vendor").rglob("*")
              if path.is_file() and (path.suffix == ".rs" or path.name in ["Cargo.toml", "Cargo.lock"])]
    for path in sorted(paths):
        data = path.read_bytes()
        digest.update(str(path.relative_to(REPO)).encode())
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def initramfs(work, kernel, probe, tracer):
    native.MODULES = list(dict.fromkeys(native.MODULES + MODULES))
    archive = native.initramfs(work, kernel)
    root = work / "root"
    modules = {}
    for name in MODULES:
        module = root / "modules" / f"{name}.ko"
        if module.is_file():
            modules[name] = {"kind": "module", "guest_sha256": native.sha256(module)}
        else:
            builtin = (Path(f"/lib/modules/{kernel}") / "modules.builtin").read_text()
            if not any(Path(line).name == f"{name}.ko" for line in builtin.splitlines()):
                raise RuntimeError(f"required host-state test module is neither installed nor built in: {name}")
            modules[name] = {"kind": "builtin"}
    shutil.copy2(REPO / "scripts/qemu/guest-host-state-init.sh", root / "init")
    (root / "init").chmod(0o755)
    native.copy_binary(probe, "/usr/bin/host-state-wire-probe", root)
    native.copy_binary(tracer, "/usr/bin/usbfs-syscall-gate", root)
    binaries = {name: native.sha256(root / "usr/bin" / name) for name in ["rustusb", "rustusb-helper"]}
    tools = {name: native.sha256(root / "usr/bin" / name) for name in ["host-state-wire-probe", "usbfs-syscall-gate"]}
    if any(value != native.sha256(REPO / "target/release" / name) for name, value in binaries.items()):
        raise RuntimeError("release binary changed while copying guest payload")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names,
                            cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(packed.stdout))
    return archive, binaries, tools, modules


def block(log, label):
    start = f"RUSTUSB_HOST_STATE_{label}_BEGIN\n"
    stop = f"RUSTUSB_HOST_STATE_{label}_END"
    if log.count(start) != 1 or log.count(stop) != 1:
        raise RuntimeError(f"one complete {label} evidence block required")
    return log.split(start, 1)[1].split(stop, 1)[0]


def json_lines(value):
    return [json.loads(line) for line in value.splitlines() if line.strip()]


def verify_probe(log, operation, mode, nonce):
    entries = []
    for line in log.splitlines():
        if line.startswith("{"):
            entry = json.loads(line)
            if entry.get("event") in ["host_state_probe_case", "host_state_probe_result"]:
                entries.append(entry)
    cases = [entry for entry in entries if entry["event"] == "host_state_probe_case"]
    expected = ["import_identity", "syscall_entry_held"]
    if mode == "unlink":
        expected += ["queued_cancel_worker_witness", "dependent_request_waits", "active_completion_wins",
                     "clear_error_preserves_pending_endpoint" if operation == "clear" else "new_empty_endpoint_map",
                     "same_connection_health"]
    else:
        expected += ["queued_request_accepted", "eof_holds_lease", "closing_hidden_and_busy"]
    expected += ["original_state_restored", "bounded_server_shutdown"]
    if [entry.get("case") for entry in cases] != expected:
        raise RuntimeError("exact ordered host-state probe cases missing")
    if any(entry.get("operation") != operation or entry.get("mode") != mode or entry.get("nonce") != nonce
           or entry.get("result") != "PASS" for entry in entries):
        raise RuntimeError("probe result identity or assertion failure")
    results = [entry for entry in entries if entry["event"] == "host_state_probe_result"]
    if len(results) != 1 or results[0].get("cases") != len(expected) or entries[-1] != results[0]:
        raise RuntimeError("one final probe result with exact case count required")
    by_name = {entry["case"]: entry for entry in cases}
    if mode == "unlink":
        terminal = by_name["active_completion_wins"]
        if (by_name["queued_cancel_worker_witness"].get("unlink_status") != -104
                or terminal.get("submit_status") != (-32 if operation == "clear" else 0)
                or terminal.get("actual_length") != 0 or terminal.get("unlink_status") != 0):
            raise RuntimeError("wire queued-cancel or active-completion winner differs")
        if operation == "clear":
            endpoint = by_name["clear_error_preserves_pending_endpoint"]
            if endpoint.get("pending") is not True or endpoint.get("cancel_status") != -104:
                raise RuntimeError("failed clear did not preserve a cancellable real endpoint")
        elif by_name["new_empty_endpoint_map"].get("status") != -32 or by_name["new_empty_endpoint_map"].get("actual_length") != 0:
            raise RuntimeError("dependent request did not use the new empty endpoint map")
        health = by_name["same_connection_health"]
        if health.get("configuration") != 1 or health.get("alternate") != (1 if operation == "clear" else 0):
            raise RuntimeError("same-connection configuration/alternate readback differs")
    elif (by_name["closing_hidden_and_busy"].get("import_status") != 2
          or by_name["closing_hidden_and_busy"].get("devlist_count") != 0):
        raise RuntimeError("held closing generation was visible or importable")
    return cases, results[0]


def verify_server(log, operation, mode, nonce):
    events = json_lines(block(log, "EVENTS"))
    if any(event.get("schema_version") != 1 or event.get("event") == "error" for event in events):
        raise RuntimeError("invalid physical server event schema or explicit error")
    if sum(event.get("event") == "ready" for event in events) != 1:
        raise RuntimeError("one physical server ready event required")
    stopped = [event for event in events if event.get("event") == "stopped"]
    if len(stopped) != 1 or events[-1] != stopped[0]:
        raise RuntimeError("one final clean physical server stopped event required")
    observations = json_lines(block(log, "OBSERVATIONS"))
    expected = ["baseline", "held", "witness"] if mode == "unlink" else ["baseline", "held", "accepted", "closed"]
    if operation == "clear" and mode == "unlink":
        expected += ["pending"]
    expected += ["restored", "stopped"]
    if [observation.get("label") for observation in observations] != expected:
        raise RuntimeError("exact independent snapshot/sysfs observations missing")
    observed = {entry["label"]: entry for entry in observations}
    original = observed["baseline"]["sysfs"]
    drivers = [{"interface": 0, "driver": "cdc_ether"}, {"interface": 1, "driver": "cdc_ether"}]
    if original["configuration"] != 1 or original["interfaces"] != [
            {"number": 0, "driver": "cdc_ether", "alternate": 0},
            {"number": 1, "driver": "cdc_ether", "alternate": 1}]:
        raise RuntimeError("original composite sysfs state differs")
    for name in expected:
        snapshot = observed[name]["snapshot"]
        if snapshot.get("schema_version") != 1 or len(snapshot["devices"]) != 1:
            raise RuntimeError("one versioned physical device snapshot required")
        dev = snapshot["devices"][0]
        if dev["configuration"] != 1 or dev["speed"] != 2 or dev["ep0_max_packet_size"] != 64:
            raise RuntimeError("actual physical device/configuration identity differs")
        if name in ["baseline", "restored", "stopped"]:
            if (snapshot["buffer_bytes_reserved"] or dev["state"] != "available" or dev["leased"]
                    or not dev["cleanup_verified"] or any(dev[key] for key in ["pending_urbs", "queued_urbs", "usbfs_inflight"])
                    or dev["current_drivers"] != drivers or dev["original_drivers"] != drivers
                    or observed[name]["sysfs"] != original):
                raise RuntimeError("resource retirement or independent original-state restoration incomplete")
        else:
            count = 1 if name in ["held", "pending"] else 2
            charge = 256 if name == "held" else (384 if name == "pending" else (640 if mode == "unlink" else 512))
            state = "closing" if name == "closed" else "leased"
            if (snapshot["buffer_bytes_reserved"] != charge or dev["state"] != state or not dev["leased"]
                    or dev["cleanup_verified"] or dev["pending_urbs"] != count or dev["usbfs_inflight"] != (1 if name == "pending" else 0)):
                raise RuntimeError("held syscall lost its lease, request ownership or real budget")
            if (name in ["held", "closed", "pending"] and dev["queued_urbs"] != 0
                    or name in ["witness", "accepted"] and dev["queued_urbs"] < 1):
                raise RuntimeError("queued/deferred or EOF cancellation state differs")
            if observed[name]["sysfs"] != observed["held"]["sysfs"]:
                raise RuntimeError("independent sysfs ownership changed before release")
            if operation != "configuration" and observed[name]["sysfs"]["interfaces"] != [
                    {"number": 0, "driver": "usbfs", "alternate": 0},
                    {"number": 1, "driver": "usbfs", "alternate": 1}]:
                raise RuntimeError("actual alternate/clear interface claims were not retained")
    if stopped[0]["snapshot"] != observed["stopped"]["snapshot"]:
        raise RuntimeError("stopped physical event differs from independent final snapshot")
    return events, observations


def verify_gate(log, operation, nonce):
    trace = json_lines(block(log, "TRACE"))
    beacons = json_lines(block(log, "BEACONS"))
    if len(beacons) != 1 or set(beacons[0]) != {"ready", "held", "exit"}:
        raise RuntimeError("exact ready/held/exit tracer beacons required")
    ready, held, exited = [beacons[0][key] for key in ["ready", "held", "exit"]]
    if (ready.get("schema_version") != 1 or ready.get("event") != "ready" or ready.get("uid") != 1000
            or ready.get("gid") != 1000 or any(ready.get(key) != 0 for key in
                                             ["cap_effective", "cap_permitted", "cap_ambient", "cap_inheritable"])):
        raise RuntimeError("actual traced server identity/capabilities differ")
    fields = ["nonce", "operation", "tid", "fd", "request", "node_device", "node_inode", "node_rdev",
              "tracee_pid", "tracee_starttime", "mutation_counts"]
    if (held.get("schema_version") != 1 or exited.get("schema_version") != 1
            or held.get("event") != "held" or exited.get("event") != "exit"
            or any(held.get(key) != exited.get(key) for key in fields)
            or held.get("nonce") != nonce or held.get("operation") != operation
            or held.get("request") != REQUESTS[operation] or set(held["mutation_counts"]) != MUTATIONS
            or held["tracee_pid"] != ready["tracee_pid"] or held["tracee_starttime"] != ready["tracee_starttime"]
            or exited["monotonic_ns"] <= held["monotonic_ns"]):
        raise RuntimeError("tracer operation, node, syscall or generation correlation differs")
    retval = -32 if operation == "clear" else 0
    if exited.get("return_value") != retval or exited.get("is_error") != (retval < 0):
        raise RuntimeError("real syscall errno/success was not the expected model result")
    if any(entry.get("schema_version") != 1 or entry.get("event") == "failure" for entry in trace):
        raise RuntimeError("tracer failed or emitted invalid events")
    for kind in ["armed", "held", "released", "exit", "finished"]:
        entries = [entry for entry in trace if entry.get("event") == kind]
        if len(entries) != 1:
            raise RuntimeError(f"one tracer {kind} event required")
    release = next(entry for entry in trace if entry["event"] == "released")
    armed = next(entry for entry in trace if entry["event"] == "armed")
    if armed.get("nonce") != nonce or armed.get("operation") != operation or armed["monotonic_ns"] >= held["monotonic_ns"]:
        raise RuntimeError("matching armed event did not precede target entry")
    if not held["monotonic_ns"] < release["monotonic_ns"] < exited["monotonic_ns"]:
        raise RuntimeError("syscall entry/release/exit ordering differs")
    held_entries = [entry for entry in trace if entry.get("event") == "ioctl_entry"
                    and held["monotonic_ns"] <= entry["monotonic_ns"] <= release["monotonic_ns"]]
    if held_entries:
        raise RuntimeError("a mutation ioctl entered while the target remained stopped")
    targets = [entry for entry in trace if entry.get("event") == "ioctl_entry"
               and entry.get("request") == REQUESTS[operation]
               and armed["monotonic_ns"] <= entry["monotonic_ns"] < exited["monotonic_ns"]]
    if len(targets) != 1 or targets[0].get("tid") != held["tid"] or targets[0].get("fd") != held["fd"]:
        raise RuntimeError("queued operation unexpectedly reached another mutation syscall")
    finished = [entry for entry in trace if entry.get("event") == "finished"][0]
    if finished.get("tracee_exit_code") != 0 or trace[-1] != finished:
        raise RuntimeError("traced ordinary server did not exit cleanly")
    return trace, beacons[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=list(REQUESTS), required=True)
    parser.add_argument("--mode", choices=["unlink", "eof"], required=True)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--nonce", default="hs-" + secrets.token_hex(12))
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence requires the default locked build")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0 or os.uname().machine != "x86_64":
        raise RuntimeError("Run as an ordinary x86_64 user; ptrace/root actions are confined to disposable guests.")
    if re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel) is None:
        parser.error("--kernel must be one safe kernel-release token")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.nonce) is None:
        parser.error("--nonce must contain 1–48 safe ASCII letters, digits, hyphens or underscores")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be between 30 and 1800 seconds")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = (args.evidence or REPO / "docs/evidence" / f"qemu-host-state-{args.operation}-{args.mode}-{stamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    scripts = OWN + ["run-native-qemu.py", "guest-init.sh"]
    summary = {"result": "FAIL", "started_at_utc": stamp,
               "scope": "two isolated x86_64 QEMU guests; real USBFS Special syscall-entry gate; not in-kernel execution or physical hardware",
               "operation": args.operation, "mode": args.mode, "nonce": args.nonce,
               "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
               "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
               "kernel": args.kernel, "host_uid": os.geteuid(), "host_architecture": os.uname().machine,
               "qemu_version": subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip(),
               "rustc_version": subprocess.check_output(["rustc", "--version"], text=True).strip(),
               "c_compiler_version": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
               "build_performed": not args.skip_build,
               "build_command": ["cargo", "build", "--workspace", "--release", "--locked", "--all-features"],
               "source_fingerprint_sha256": source_fingerprint(), "cargo_lock_sha256": native.sha256(REPO / "Cargo.lock"),
               "script_sha256": {name: native.sha256(REPO / "scripts/qemu" / name) for name in scripts},
               "topology": {"guests": 2, "management": "192.0.2.0/24", "usb_controller": "piix3-usb-uhci",
                            "private_socketpairs": 2, "server_peer_nic_down": True, "host_network_listener": False,
                            "host_ptrace": False, "host_usb": False, "ip_forward": False},
               "startup_arm_idle_deadline_seconds": 25,
               "gate_deadline_seconds": 8, "rpc_deadline_seconds": 3, "tracer_deadline_seconds": 120}
    processes, handles, links = {}, [], []
    try:
        with (evidence / "build.log").open("wb") as log:
            if not args.skip_build:
                subprocess.run(summary["build_command"], cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True)
        summary["binary_sha256"] = {name: native.sha256(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        with tempfile.TemporaryDirectory(prefix="rustusb-host-state-qemu-") as directory:
            work = Path(directory)
            probe, tracer = work / "host-state-wire-probe", work / "usbfs-syscall-gate"
            summary["probe_build_command"] = ["rustc", "--edition=2024", "-O", "-D", "warnings", str(REPO / "scripts/qemu/host-state-wire-probe.rs"), "-o", str(probe)]
            summary["tracer_build_command"] = ["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", str(REPO / "scripts/qemu/usbfs-syscall-gate.c"), "-o", str(tracer)]
            with (evidence / "build.log").open("ab") as log:
                for command in [summary["probe_build_command"], summary["tracer_build_command"]]:
                    subprocess.run(command, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True)
            summary["probe_binary_sha256"], summary["tracer_binary_sha256"] = native.sha256(probe), native.sha256(tracer)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            archive, summary["guest_binary_sha256"], summary["guest_test_tool_sha256"], summary["kernel_modules"] = initramfs(work, args.kernel, probe, tracer)
            if (summary["guest_binary_sha256"] != summary["binary_sha256"]
                    or summary["guest_test_tool_sha256"] != {"host-state-wire-probe": native.sha256(probe), "usbfs-syscall-gate": native.sha256(tracer)}):
                raise RuntimeError("release or test tool changed before guest copy")
            summary["kernel_image_sha256"], summary["initramfs_sha256"] = native.sha256(image), native.sha256(archive)
            summary["native_tool_sha256"] = {name: native.sha256(native.real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            server_link, client_link = socket.socketpair()
            usb_link, quiet_peer_link = socket.socketpair()
            links = [server_link, client_link, usb_link, quiet_peer_link]
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            for role, endpoint, mac in [("server", server_link, "61"), ("client", client_link, "62")]:
                (evidence / f"{role}-serial.log").touch(exist_ok=False)
                error = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(error)
                command = ["qemu-system-x86_64", "-m", "512", "-smp", "2", *acceleration,
                           "-kernel", str(image), "-initrd", str(archive), "-append",
                           f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.host_state_test=1 rustusb.role={role} rustusb.operation={args.operation} rustusb.mode={args.mode} rustusb.nonce={args.nonce}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=control,fd={endpoint.fileno()}",
                           "-device", f"e1000,id=control-nic,netdev=control,mac=52:54:00:00:00:{mac}"]
                descriptors = [endpoint.fileno()]
                if role == "server":
                    command += ["-device", "piix3-usb-uhci,id=usb", "-netdev", f"socket,id=usbdata,fd={usb_link.fileno()}",
                                "-device", "usb-net,id=ecm,bus=usb.0,port=1,netdev=usbdata,mac=52:54:00:cc:00:02",
                                "-netdev", f"socket,id=peerdata,fd={quiet_peer_link.fileno()}",
                                "-device", "e1000,id=peer-nic,netdev=peerdata,mac=52:54:00:cc:00:01"]
                    descriptors += [usb_link.fileno(), quiet_peer_link.fileno()]
                summary.setdefault("qemu_commands", {})[role] = command
                processes[role] = subprocess.Popen(command, pass_fds=descriptors, stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.DEVNULL, stderr=error)
            for link in links:
                link.close()
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                logs = {role: (evidence / f"{role}-serial.log").read_text(errors="replace") for role in processes}
                if any("RUSTUSB_HOST_STATE_RESULT: FAIL" in log for log in logs.values()):
                    time.sleep(0.5)
                    raise RuntimeError("a host-state guest reported failure")
                if all(process.poll() is not None for process in processes.values()):
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("host-state guests exceeded the overall deadline")
            for role, process in processes.items():
                if process.wait(timeout=5) != 0:
                    raise RuntimeError(f"{role} QEMU exited nonzero")
            logs = {role: (evidence / f"{role}-serial.log").read_text(errors="replace") for role in processes}
            required = {"server": ["ORIGINAL", "UID_GATE", "SERVER_UID", "PEER_QUIET", "HELD", "EXIT", "RESTORED", "STOPPED"],
                        "client": ["PROBE_UID"]}
            required["server"] += ["WITNESS"] if args.mode == "unlink" else ["ACCEPTED", "CLOSED"]
            if args.operation == "clear" and args.mode == "unlink":
                required["server"] += ["PENDING"]
            for role, markers in required.items():
                for name in markers:
                    if f"RUSTUSB_HOST_STATE_{name}: PASS" not in logs[role]:
                        raise RuntimeError(f"missing {role} assertion: {name}")
                if (f"RUSTUSB_HOST_STATE_RESULT: PASS role={role}" not in logs[role]
                        or f"RUSTUSB_HOST_STATE_ISOLATION: PASS role={role}" not in logs[role]):
                    raise RuntimeError(f"missing {role} terminal result/isolation")
                if re.search(r"Kernel panic|BUG:|inconsistent.*usbip", logs[role], re.I):
                    raise RuntimeError(f"kernel consistency failure in {role}")
                actual = {}
                for line in block(logs[role], "EXECUTABLES").splitlines():
                    digest, filename = line.split()
                    actual[Path(filename).name] = digest
                expected = {**summary["binary_sha256"], **summary["guest_test_tool_sha256"]}
                if actual != expected:
                    raise RuntimeError(f"actual {role} executable hashes differ from copied/build artifacts")
                summary.setdefault("observed_guest_executable_sha256", {})[role] = actual
            summary["protocol_probe_cases"], summary["protocol_probe_result"] = verify_probe(logs["client"], args.operation, args.mode, args.nonce)
            summary["server_events"], summary["server_observations"] = verify_server(logs["server"], args.operation, args.mode, args.nonce)
            summary["tracer_events"], summary["tracer_beacons"] = verify_gate(logs["server"], args.operation, args.nonce)
            summary["source_fingerprint_after_sha256"] = source_fingerprint()
            summary["binary_after_sha256"] = {name: native.sha256(REPO / "target/release" / name) for name in summary["binary_sha256"]}
            if summary["source_fingerprint_after_sha256"] != summary["source_fingerprint_sha256"]:
                raise RuntimeError("runtime source fingerprint changed")
            if summary["binary_after_sha256"] != summary["binary_sha256"]:
                raise RuntimeError("runtime release binary changed")
            summary["pass_markers_verified"] = True
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = str(error)
        raise
    finally:
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
        for link in links:
            link.close()
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        for role in processes:
            log = (evidence / f"{role}-serial.log").read_text(errors="replace")
            summary[f"{role}_checks"] = [line for line in log.splitlines() if line.startswith("RUSTUSB_HOST_STATE_")]
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"result": summary["result"], "operation": args.operation, "mode": args.mode,
                          "evidence": str(evidence)}, indent=2))


if __name__ == "__main__":
    main()
