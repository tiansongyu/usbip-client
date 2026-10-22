#!/usr/bin/env python3
"""Verify real USBFS multi-interface ECM configuration/alternates in two guests.

The USB-net backend and server peer NIC use a second inherited Unix socketpair.
No host USB, module, TAP, IP configuration or network listener is used.
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
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
OWN = ["run-composite-qemu.py", "guest-composite-init.sh", "composite-wire-probe.rs"]
NET_MODULES = ["mii", "usbnet", "cdc_ether", "uhci-hcd"]
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


def initramfs(work, kernel, probe):
    native.MODULES = list(dict.fromkeys(native.MODULES + NET_MODULES))
    archive = native.initramfs(work, kernel)
    root = work / "root"
    modules = {}
    for name in NET_MODULES:
        module = root / "modules" / f"{name}.ko"
        if module.is_file():
            modules[name] = {"kind": "module", "guest_sha256": native.sha256(module)}
        else:
            builtin = (Path(f"/lib/modules/{kernel}") / "modules.builtin").read_text()
            if not any(Path(line).name == f"{name}.ko" for line in builtin.splitlines()):
                raise RuntimeError(f"required CDC ECM module is neither installed nor built in: {name}")
            modules[name] = {"kind": "builtin"}
    shutil.copy2(REPO / "scripts/qemu/guest-composite-init.sh", root / "init")
    (root / "init").chmod(0o755)
    native.copy_binary(probe, "/usr/bin/composite-wire-probe", root)
    binaries = {name: native.sha256(root / "usr/bin" / name) for name in ["rustusb", "rustusb-helper"]}
    if any(digest != native.sha256(REPO / "target/release" / name) for name, digest in binaries.items()):
        raise RuntimeError("release binary changed while copying the guest payload")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names,
                            cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(packed.stdout))
    return archive, binaries, modules


def event_block(log, label):
    start = f"RUSTUSB_COMPOSITE_{label}_BEGIN\n"
    stop = f"RUSTUSB_COMPOSITE_{label}_END"
    if log.count(start) != 1 or log.count(stop) != 1:
        raise RuntimeError(f"one complete {label} evidence block required")
    return log.split(start, 1)[1].split(stop, 1)[0]


def verify_events(log):
    events = [json.loads(line) for line in event_block(log, "EVENTS").splitlines() if line.strip()]
    if any(event.get("schema_version") != 1 or event.get("event") == "error" for event in events):
        raise RuntimeError("invalid physical server event schema or explicit error")
    snapshots = {event["id"]: event["snapshot"] for event in events if event.get("event") == "snapshot"}
    expected = {"baseline", "held-1", "io-1", "released-1", "held-2", "io-2", "released-2"}
    if set(snapshots) != expected or sum(event.get("event") == "snapshot" for event in events) != 7:
        raise RuntimeError("seven exact composite snapshots required")
    stopped = [event for event in events if event.get("event") == "stopped"]
    if len(stopped) != 1 or events[-1].get("event") != "stopped":
        raise RuntimeError("physical server did not stop cleanly once")

    def device(snapshot):
        if snapshot.get("schema_version") != 1 or len(snapshot["devices"]) != 1:
            raise RuntimeError("one physical composite device required")
        return snapshot["devices"][0]

    def released(snapshot):
        dev = device(snapshot)
        if (snapshot["buffer_bytes_reserved"] != 0 or dev["state"] != "available" or dev["leased"]
                or dev["pending_urbs"] or dev["queued_urbs"] or dev["usbfs_inflight"]
                or not dev["cleanup_verified"] or dev["configuration"] != 1 or dev["speed"] != 2
                or dev["ep0_max_packet_size"] != 64 or dev["current_drivers"] != dev["original_drivers"]
                or len(dev["current_drivers"]) != 2
                or any(driver["driver"] != "cdc_ether" for driver in dev["current_drivers"])):
            raise RuntimeError("composite lease, buffer or original-driver cleanup incomplete")

    for name in ["baseline", "released-1", "released-2"]:
        released(snapshots[name])
    released(stopped[0]["snapshot"])
    baseline = device(snapshots["baseline"])
    if baseline["usbfs_reaped"] < 10 or baseline["cancelled"] < 2 or baseline["counters"]["controls"] <= 10:
        raise RuntimeError("real control/cancellation/reap evidence missing")
    for cycle in [1, 2]:
        held = device(snapshots[f"held-{cycle}"])
        io = device(snapshots[f"io-{cycle}"])
        for dev in [held, io]:
            if (dev["state"] != "leased" or not dev["leased"] or dev["configuration"] != 1
                    or len(dev["current_drivers"]) != 2
                    or any(driver["driver"] != "usbfs" for driver in dev["current_drivers"])):
                raise RuntimeError("two interfaces were not actually held by usbfs")
        if any(io["counters"][field] <= held["counters"][field] for field in ["bulk_in", "bulk_out"]):
            raise RuntimeError("both real bulk counters must advance during each UDP exchange")
    return events, {"snapshots": snapshots, "final_snapshot": stopped[0]["snapshot"]}


def verify_probes(logs):
    cases = [json.loads(line) for line in logs["client"].splitlines()
             if line.startswith('{"event":"composite_probe_case"')]
    required = {"descriptor_index_is_not_configuration_value", "configuration_two_data_alt_zero_is_active",
                "configuration_barrier_uses_new_empty_endpoint_map", "alternate_one_enables_real_pending_bulk_endpoint",
                "alternate_barrier_retires_old_queued_and_active_requests", "nonexistent_alternate_stalls_without_state_change",
                "real_control_stall_preserves_connection", "real_interrupt_stall_preserves_connection",
                "model_clear_halt_failure_is_not_faked_success", "configuration_zero_and_reconfiguration_are_observed",
                "host_state_unlink_has_real_winner", "pending_real_endpoint_eof_recovers_original_device",
                "submitted_host_state_eof_cleanup_converges", "unsupported_iso_frame_is_explicitly_rejected",
                "all_lifecycle_checks", "ecm_exclusive_busy", "original_ecm_available"}
    if ({case["case"] for case in cases if case.get("result") == "PASS"} != required or len(cases) != 19
            or sum(case["case"] == "ecm_exclusive_busy" for case in cases) != 2
            or sum(case["case"] == "original_ecm_available" for case in cases) != 2):
        raise RuntimeError("exact lifecycle, busy and rediscovery probe results missing")
    peers = [json.loads(line) for line in event_block(logs["server"], "PEER_EVENTS").splitlines()
             if line.startswith('{"event":"composite_udp_peer"')]
    clients = [json.loads(line) for line in logs["client"].splitlines()
               if line.startswith('{"event":"composite_udp_client"')]
    expected_payloads = []
    for entries in [peers, clients]:
        if [entry["cycle"] for entry in entries] != [1, 2]:
            raise RuntimeError("two exact UDP peer/client cycles required")
        for entry in entries:
            cycle = entry["cycle"]
            request = f"RUSTUSB-ECM-REQUEST-{cycle}:0123456789abcdef".encode()
            response = f"RUSTUSB-ECM-RESPONSE-{cycle}:fedcba9876543210".encode()
            if not entry["exact"] or entry["request_bytes"] != len(request) or entry["response_bytes"] != len(response):
                raise RuntimeError("UDP endpoint payload evidence differs")
            if entries is clients:
                expected_payloads.append({"cycle": cycle, "request_bytes": len(request), "response_bytes": len(response),
                                          "request_sha256": hashlib.sha256(request).hexdigest(),
                                          "response_sha256": hashlib.sha256(response).hexdigest()})
    return cases, {"peer": peers, "client": clients, "compared_expected_payloads": expected_payloads}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence uses the default locked build")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0 or os.uname().machine != "x86_64":
        raise RuntimeError("Run as an ordinary x86_64 user; root actions are confined to disposable guests.")
    if re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel) is None:
        parser.error("--kernel must be one safe kernel-release token")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be 30–1800 seconds")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = (args.evidence or REPO / "docs/evidence" / f"qemu-composite-{stamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    scripts = OWN + ["run-native-qemu.py", "guest-init.sh"]
    summary = {"result": "FAIL", "started_at_utc": stamp,
               "scope": "one QEMU CDC ECM multi-interface USBFS device/configuration/alternate to Linux VHCI; no host USB",
               "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
               "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
               "kernel": args.kernel, "host_uid": os.geteuid(), "host_architecture": os.uname().machine,
               "qemu_version": subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip(),
               "rustc_version": subprocess.check_output(["rustc", "--version"], text=True).strip(),
               "build_performed": not args.skip_build,
               "build_command": ["cargo", "build", "--workspace", "--release", "--locked", "--all-features"],
               "source_fingerprint_sha256": source_fingerprint(), "cargo_lock_sha256": native.sha256(REPO / "Cargo.lock"),
               "script_sha256": {name: native.sha256(REPO / "scripts/qemu" / name) for name in scripts},
               "topology": {"management": "192.0.2.0/24", "data": "198.18.0.0/30", "guests": 2,
                            "usb_controller": "piix3-usb-uhci",
                            "private_socketpairs": 2, "data_endpoints_same_server_qemu_process": True,
                            "host_network_listener": False, "ip_forward": False}}
    processes = {}
    handles = []
    links = []
    try:
        with (evidence / "build.log").open("wb") as log:
            if not args.skip_build:
                subprocess.run(summary["build_command"], cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True)
        summary["binary_sha256"] = {name: native.sha256(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        with tempfile.TemporaryDirectory(prefix="rustusb-composite-qemu-") as directory:
            work = Path(directory)
            probe = work / "composite-wire-probe"
            with (evidence / "build.log").open("ab") as log:
                subprocess.run(["rustc", "--edition=2024", "-O", "-D", "warnings", str(REPO / "scripts/qemu/composite-wire-probe.rs"), "-o", str(probe)],
                               cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True)
            summary["probe_binary_sha256"] = native.sha256(probe)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            archive, summary["guest_binary_sha256"], summary["network_modules"] = initramfs(work, args.kernel, probe)
            if summary["guest_binary_sha256"] != summary["binary_sha256"]:
                raise RuntimeError("release binaries changed before guest copy")
            summary["kernel_image_sha256"] = native.sha256(image)
            summary["initramfs_sha256"] = native.sha256(archive)
            summary["native_tool_sha256"] = {name: native.sha256(native.real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            control_server, control_client = socket.socketpair()
            usb_backend, peer_backend = socket.socketpair()
            links = [control_server, control_client, usb_backend, peer_backend]
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            for role, endpoint, mac in [("server", control_server, "51"), ("client", control_client, "52")]:
                (evidence / f"{role}-serial.log").touch(exist_ok=False)
                error = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(error)
                command = ["qemu-system-x86_64", "-m", "512", "-smp", "2", *acceleration,
                           "-kernel", str(image), "-initrd", str(archive), "-append",
                           f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.role={role}",
                           "-display", "none", "-serial", f"file:{evidence / (role + '-serial.log')}",
                           "-monitor", "none", "-no-reboot", "-nic", "none",
                           "-netdev", f"socket,id=control,fd={endpoint.fileno()}",
                           "-device", f"e1000,id=control-nic,netdev=control,mac=52:54:00:00:00:{mac}"]
                descriptors = [endpoint.fileno()]
                if role == "server":
                    command += ["-device", "piix3-usb-uhci,id=usb", "-netdev", f"socket,id=usbdata,fd={usb_backend.fileno()}",
                                "-device", "usb-net,id=ecm,bus=usb.0,port=1,netdev=usbdata,mac=52:54:00:cc:00:02",
                                "-netdev", f"socket,id=peerdata,fd={peer_backend.fileno()}",
                                "-device", "e1000,id=peer-nic,netdev=peerdata,mac=52:54:00:cc:00:01",
                                "-object", f"filter-dump,id=usbcap,netdev=usbdata,queue=all,file={evidence / 'data-usb.pcap'}",
                                "-object", f"filter-dump,id=peercap,netdev=peerdata,queue=all,file={evidence / 'data-peer.pcap'}"]
                    descriptors += [usb_backend.fileno(), peer_backend.fileno()]
                summary.setdefault("qemu_commands", {})[role] = command
                processes[role] = subprocess.Popen(command, pass_fds=descriptors, stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.DEVNULL, stderr=error)
            for link in links:
                link.close()
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                logs = {role: (evidence / f"{role}-serial.log").read_text(errors="replace") for role in processes}
                if any("RUSTUSB_COMPOSITE_RESULT: FAIL" in log for log in logs.values()):
                    # Give both disposable guests a bounded chance to flush
                    # failure-only network counters and server snapshots.
                    time.sleep(1)
                    raise RuntimeError("a composite guest reported failure")
                if all(process.poll() is not None for process in processes.values()):
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("composite guests exceeded the overall deadline")
            for role, process in processes.items():
                if process.wait(timeout=10) != 0:
                    raise RuntimeError(f"{role} QEMU exited nonzero")
            logs = {role: (evidence / f"{role}-serial.log").read_text(errors="replace") for role in processes}
            required = {"server": ["RUSTUSB_COMPOSITE_ORIGINAL: PASS", "RUSTUSB_COMPOSITE_OPEN_DENIED: PASS",
                                   "RUSTUSB_COMPOSITE_SERVER_UID: PASS", "RUSTUSB_COMPOSITE_PEER_UID: PASS",
                                   "RUSTUSB_COMPOSITE_BASELINE: PASS", "RUSTUSB_COMPOSITE_STOPPED: PASS",
                                   "RUSTUSB_COMPOSITE_ROUTE_ISOLATION: PASS", "RUSTUSB_COMPOSITE_PRODUCTION_LOGS: PASS"],
                        "client": ["RUSTUSB_COMPOSITE_OFFICIAL_OWNERSHIP: PASS managed=false helper_detach=denied"]}
            for role in required:
                required[role] += [f"RUSTUSB_COMPOSITE_RESULT: PASS role={role}", f"RUSTUSB_COMPOSITE_UID_GATE: PASS role={role}"]
                for marker in required[role]:
                    if marker not in logs[role]:
                        raise RuntimeError(f"missing {role} marker: {marker}")
                if re.search(r"Kernel panic|BUG:|urb has been unlinked|inconsistent.*usbip", logs[role], re.I):
                    raise RuntimeError(f"kernel consistency failure in {role}")
            for cycle, managed in [(1, "true"), (2, "false")]:
                if f"RUSTUSB_COMPOSITE_ATTACHED: PASS cycle={cycle} managed={managed}" not in logs["client"]:
                    raise RuntimeError("RustUSB and official ownership proof missing")
                for role, marker in [("server", f"RUSTUSB_COMPOSITE_RELEASED: PASS cycle={cycle}"),
                                     ("client", f"RUSTUSB_COMPOSITE_IO: PASS cycle={cycle} exact_udp=true same_receipt=true"),
                                     ("client", f"RUSTUSB_COMPOSITE_CLIENT_RELEASED: PASS cycle={cycle}")]:
                    if marker not in logs[role]:
                        raise RuntimeError("exact two-cycle I/O or cleanup proof missing")
            summary["protocol_probe_cases"], summary["udp_observations"] = verify_probes(logs)
            summary["server_events"], summary["server_observations"] = verify_events(logs["server"])
            if source_fingerprint() != summary["source_fingerprint_sha256"]:
                raise RuntimeError("runtime source fingerprint changed")
            if any(native.sha256(REPO / "target/release" / name) != digest for name, digest in summary["binary_sha256"].items()):
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
        summary["private_data_captures"] = {path.name: {"bytes": path.stat().st_size, "sha256": native.sha256(path)}
                                             for path in evidence.glob("data-*.pcap")}
        for role in processes:
            log = (evidence / f"{role}-serial.log").read_text(errors="replace")
            summary[f"{role}_checks"] = [line for line in log.splitlines() if line.startswith("RUSTUSB_COMPOSITE_")]
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"result": summary["result"], "evidence": str(evidence)}, indent=2))


if __name__ == "__main__":
    main()
