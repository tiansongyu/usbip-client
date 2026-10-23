#!/usr/bin/env python3
"""One disposable guest: usbip_host -> anonymous AF_UNIX pair -> VHCI.

This is a socket-family mechanism experiment. It does not implement TLS,
remote authentication, a product attach API, or real-hardware acceptance.
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
OWN = ["run-kernel-socketpair-qemu.py", "guest-kernel-socketpair-init.sh", "kernel-socketpair-probe.rs"]
SCRIPTS = OWN + ["run-physical-qemu.py", "run-native-qemu.py", "guest-init.sh"]
SPEC = importlib.util.spec_from_file_location("pair_physical", REPO / "scripts/qemu/run-physical-qemu.py")
physical = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(physical)
native = physical.native
RX = b"RUSTUSB-RX-1-0001:0123456789abcdef\n"
TX = b"RUSTUSB-TX-1-0001:fedcba9876543210\n"
PASS = "RUSTUSB_PAIR_RESULT: PASS single_guest=true tls=false remote_security=false"
BAD = ["RUSTUSB_PAIR_RESULT: FAIL", "Kernel panic", "BUG:", "was already given back", "seqnum max"]
MODULES = {"usb-common": "USB_COMMON", "usbcore": "USB", "xhci-hcd": "USB_XHCI_HCD",
           "xhci-pci": "USB_XHCI_PCI", "evdev": "INPUT_EVDEV", "hid": "HID", "hid-generic": "HID_GENERIC",
           "usbhid": "USB_HID", "usbserial": "USB_SERIAL", "ftdi_sio": "USB_SERIAL_FTDI_SIO",
           "usbip-core": "USBIP_CORE", "usbip-host": "USBIP_HOST", "vhci-hcd": "USBIP_VHCI_HCD"}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def source_state():
    paths = {REPO / "Cargo.toml", REPO / "Cargo.lock"}
    for directory in [REPO / "crates", REPO / "vendor"]:
        paths.update(path for path in directory.rglob("*") if path.is_file()
                     and (path.suffix == ".rs" or path.name in ["Cargo.toml", "Cargo.lock"]))
    paths.update(REPO / "scripts/qemu" / name for name in SCRIPTS)
    files = [{"path": str(path.relative_to(REPO)), "length": path.stat().st_size, "sha256": native.sha256(path)}
             for path in sorted(paths)]
    return {"head": git("rev-parse", "HEAD"), "git_status": git("status", "--porcelain=v1"), "files": files,
            "source_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def payload(work, kernel, probe):
    archive = native.initramfs(work, kernel)
    root = work / "root"
    shutil.copy2(REPO / "scripts/qemu/guest-kernel-socketpair-init.sh", root / "init")
    (root / "init").chmod(0o755)
    native.copy_binary(probe, "/usr/bin/kernel-socketpair-probe", root)
    binaries = {name: {"length": (root / "usr/bin" / name).stat().st_size, "sha256": native.sha256(root / "usr/bin" / name)}
                for name in ["rustusb", "rustusb-helper", "kernel-socketpair-probe"]}
    (root / "etc/pair-test-binaries.json").write_text(json.dumps(binaries) + "\n")
    config_path = Path(f"/boot/config-{kernel}")
    config = dict(line.split("=", 1) for line in config_path.read_text().splitlines()
                  if line.startswith("CONFIG_") and "=" in line)
    modules = {}
    for name, option in MODULES.items():
        mode = config.get("CONFIG_" + option)
        path = root / "modules" / (name + ".ko")
        if mode not in ["y", "m"] or (mode == "m" and not path.is_file()):
            raise RuntimeError(f"required CONFIG_{option}={mode} lacks matching module")
        modules[name] = {"mode": mode, "sha256": native.sha256(path) if path.is_file() else None}
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    result = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"],
                            input=names, cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(result.stdout))
    return archive, binaries, {"config_sha256": native.sha256(config_path), "required": modules,
                               "copied": {p.name: native.sha256(p) for p in sorted((root / "modules").glob("*.ko"))}}


def records(log, prefix):
    return [json.loads(line.removeprefix(prefix)) for line in log.splitlines() if line.startswith(prefix)]


def status_rows(text):
    lines = text.splitlines()
    if not lines or " ".join(lines[0].split()) != "hub port sta spd dev sockfd local_busid":
        raise ValueError("unexpected VHCI header")
    rows = []
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 7 or fields[0] not in ["hs", "ss"]:
            raise ValueError("bad VHCI row")
        if re.fullmatch(r"[0-9a-fA-F]{1,16}", fields[5]) is None:
            raise ValueError("bad opaque VHCI socket display")
        rows.append({"hub": fields[0], "port": int(fields[1]), "status": int(fields[2]), "speed": int(fields[3]),
                     "remote": int(fields[4], 16), "socket_display": fields[5], "local": fields[6]})
    if not rows or len({row["port"] for row in rows}) != len(rows):
        raise ValueError("missing/duplicate VHCI ports")
    return rows


def verify(log, summary, received):
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise RuntimeError(name)

    summary["checks"] = checks
    check("one exact guest PASS and channel beacon", log.splitlines().count(PASS) == 1 and
          log.splitlines().count("RUSTUSB_PAIR_EVIDENCE_CHANNEL: PASS device=ttyS1 kernel_console=ttyS0") == 1)
    events = records(log, "RUSTUSB_PAIR_PROBE ")
    expected = {"guard": 1, "initial_status": 1, "prepared": 2, "write_begin": 4, "write_complete": 4,
                "attached": 2, "ready": 1, "stop_received": 1, "shutdown": 4, "disconnected": 1, "complete": 1}
    check("exact complete probe event set", {name: sum(e["event"] == name for e in events) for name in expected} == expected
          and all(e["event"] in expected and e["schema_version"] == 1 for e in events))
    by = {name: [e for e in events if e["event"] == name] for name in expected}
    check("both original attributes preopened before the first handoff", max(events.index(e) for e in by["prepared"]) < min(events.index(e) for e in by["write_begin"]))
    check("probe operations ordered before explicit stop and retained-FD cleanup", events[0]["event"] == "guard" and
          events[-1]["event"] == "complete" and events.index(by["ready"][0]) < events.index(by["stop_received"][0]) <
          events.index(by["shutdown"][0]) < events.index(by["disconnected"][0]) < events.index(by["complete"][0]))
    identity = records(log, "RUSTUSB_PAIR_IDENTITY ")
    waits = records(log, "RUSTUSB_PAIR_WAIT ")
    check("actual root probe identity and one real wait0", len(identity) == len(waits) == 1 and
          identity[0]["pid"] == waits[0]["pid"] == by["guard"][0]["pid"] and identity[0]["starttime"] == waits[0]["starttime"] == by["guard"][0]["starttime"] and
          waits[0]["wait_code"] == 0 and identity[0]["uids"] == [0] * 4 and identity[0]["control_fifo"] == "0:600:fifo" and
          identity[0]["executed_sha256"] == summary["probe_binary_sha256"])
    refusals = records(log, "RUSTUSB_PAIR_GUARD_REFUSAL ")
    check("ordinary UID refused before USB operation", len(refusals) == 1 and refusals[0]["event"] == "error" and refusals[0]["error"] == "guest root required")
    bins = records(log, "RUSTUSB_PAIR_BINARIES ")
    check("guest installed and actually executing helper/probe hashes", len(bins) == 1 and bins[0]["installed"] == summary["guest_binaries"] and
          bins[0]["helper_executed_sha256"] == summary["binary_sha256"]["rustusb-helper"])
    snapshots = records(log, "RUSTUSB_PAIR_SNAPSHOT ")
    stages = ["original", "exported", "attached", "before-stop", "disconnected", "restored"]
    check("six original primary snapshots in order", [s["stage"] for s in snapshots] == stages)
    snap = dict(zip(stages, snapshots))
    for snapshot in snapshots:
        check(snapshot["stage"] + " no TCP sockets or nonloopback network", snapshot["network_interfaces"] == ["lo"] and
              len(snapshot["tcp"].splitlines()) == 1 and len(snapshot["tcp6"].splitlines()) <= 1)
    baseline = snap["original"]["physical"]
    check("exact original HID/FTDI drivers and final restoration", len(baseline) == 2 and
          [d["bus_id"] for d in baseline] == ["1-1", "1-2"] and baseline == snap["restored"]["physical"] and
          [d["interfaces"][0]["driver"] for d in baseline] == ["usbhid", "ftdi_sio"] and
          all(d["configuration"] == 1 and d["device_driver"] == "usb" and len(d["interfaces"]) == 1 and d["interfaces"][0]["alternate"] == 0 for d in baseline))
    for snapshot in snapshots:
        check(snapshot["stage"] + " original generations/configuration/fullIF unchanged", len(snapshot["physical"]) == 2 and
              all((d["bus_id"], d["generation"], d["address"], d["configuration"],
                   [(i["name"], i["number_hex"], i["alternate"]) for i in d["interfaces"]]) ==
                  (b["bus_id"], b["generation"], b["address"], b["configuration"],
                   [(i["name"], i["number_hex"], i["alternate"]) for i in b["interfaces"]])
                  for d, b in zip(snapshot["physical"], baseline)))
    prepared = {e["original"]["bus_id"]: e for e in by["prepared"]}
    attached = {e["original"]["bus_id"]: e for e in by["attached"]}
    check("exact two original devices and distinct selected ports", set(prepared) == set(attached) == {"1-1", "1-2"} and len({e["port"] for e in prepared.values()}) == 2)
    all_sockets = [e[side] for e in prepared.values() for side in ["stub", "vhci"]]
    check("four distinct anonymous AF_UNIX stream endpoints", len({s["fd"] for s in all_sockets}) == len({s["inode"] for s in all_sockets}) == 4 and
          all(s["anonymous"] and s["family"] == "AF_UNIX" and s["type"] == "SOCK_STREAM" and s["target"] == f"socket:[{s['inode']}]" for s in all_sockets))
    check("four actual proc FD links match prepared endpoints", all(f"{s['fd']} {s['target']}" in identity[0]["fds"].splitlines() for s in all_sockets))
    for bus, vendor, product, address, cls in [("1-1", "0627", "0001", 2, 3), ("1-2", "0403", "6001", 3, 255)]:
        p, a = prepared[bus], attached[bus]
        original = p["original"]
        original_shell = next(d for d in baseline if d["bus_id"] == bus)
        check(bus + " immutable original device/config/fullIF/receipt", original == a["original"] and
              original["vendor"] == vendor and original["product"] == product and original["bus_number"] == 1 and
              original["device_address"] == address and original["configuration"] == 1 and original["speed"] == "12" and
              len(original["interfaces"]) == 1 and original["interfaces"][0]["class"] == cls and
              original["interfaces"][0]["number"] == original["interfaces"][0]["alternate"] == 0 and
              original_shell["generation"] == f"{original['filesystem_device']}:{original['inode']}")
        check(bus + " actual new VHCI generation and full-speed identity", a["local"]["bus_id"] not in prepared and
              a["local"]["sysfs_path"].startswith("/sys/devices/platform/vhci_hcd.0/") and
              a["local"]["vendor"] == vendor and a["local"]["product"] == product and a["local"]["configuration"] == 1 and
              a["local"]["speed"] == "12" and a["remote_devid"] == 65536 + address and p["port"] == a["port"] and
              a["local"]["inode"] != original["inode"] and
              [{k: v for k, v in i.items() if k != "name"} for i in a["local"]["interfaces"]] ==
              [{k: v for k, v in i.items() if k != "name"} for i in original["interfaces"]])
        for stage in ["attached", "before-stop"]:
            row = next(r for r in status_rows(snap[stage]["vhci_status"]) if r["port"] == p["port"])
            check(bus + " " + stage + " actual kernel port/devid/local", {k: v for k, v in row.items() if k != "socket_display"} ==
                  {"hub": "hs", "port": p["port"], "status": 6, "speed": 2, "remote": 65536 + address, "local": a["local"]["bus_id"]})
        for side in ["stub", "vhci"]:
            begin = [e for e in by["write_begin"] if e["bus_id"] == bus and e["side"] == side]
            end = [e for e in by["write_complete"] if e["bus_id"] == bus and e["side"] == side]
            value = f"{p['stub']['fd']}\n" if side == "stub" else f"{p['port']} {p['vhci']['fd']} {65536 + address} 2\n"
            check(bus + " one complete " + side + " sysfs write", len(begin) == len(end) == 1 and begin[0]["value"] == value and
                  end[0]["bytes"] == len(value) and events.index(begin[0]) < events.index(end[0]) and
                  (side != "stub" or begin[0]["inode"] == p["attribute_inode"]))
    for stage in ["original", "exported", "disconnected", "restored"]:
        check(stage + " all VHCI rows NULL", all(r["status"] == 4 and r["remote"] == 0 and not r["socket_display"].strip("0") and r["local"] == "0-0"
              for r in status_rows(snap[stage]["vhci_status"])))
    for stage in ["exported", "disconnected"]:
        check(stage + " both stub exports AVAILABLE", all(d["usbip_status"] == 1 and d["device_driver"] == "usbip-host" for d in snap[stage]["physical"]))
    check("four explicit successful shutdowns, no Drop-only claim", all(e["how"] == "SHUT_RDWR" and e["ok"] and e["error"] is None for e in by["shutdown"]) and
          {(e["bus_id"], e["side"]) for e in by["shutdown"]} == {(bus, side) for bus in prepared for side in ["stub", "vhci"]} and
          all(e["fd"] == prepared[e["bus_id"]][e["side"]]["fd"] for e in by["shutdown"]))
    disconnected = by["disconnected"][0]
    check("NULL/down observed while all four endpoint FDs retained", disconnected["ports_null"] and disconnected["stub_available"] and
          disconnected["old_local_generations_absent"] and disconnected["fds_still_open"] and
          {(e["fd"], e["inode"], e["target"]) for e in disconnected["retained_sockets"]} == {(s["fd"], s["inode"], s["target"]) for s in all_sockets})
    sessions = records(log, "RUSTUSB_PAIR_SESSIONS ")
    check("kernel imports explicitly unmanaged, no fabricated official record", len(sessions) == 1 and len(sessions[0]) == 2 and
          all(s["managed"] is False and s["session"]["status"] == 6 for s in sessions[0]))
    io = records(log, "RUSTUSB_PAIR_IO ")
    check("one actual I/O result", len(io) == 1)
    evidence = io[0]
    raw_input = bytes.fromhex(evidence["input_event_hex"])
    check("complete actual evdev event frames", bool(raw_input) and len(raw_input) % 24 == 0)
    key = {(kind, code, value) for _, _, kind, code, value in struct.iter_unpack("<qqHHi", raw_input)}
    check("real KEY1 down and up through imported HID", (1, 2, 1) in key and (1, 2, 0) in key and evidence["hid_local"] == attached["1-1"]["local"]["bus_id"])
    check("exact real FTDI35B in both directions and host capture", bytes.fromhex(evidence["rx_hex"]) == RX and bytes.fromhex(evidence["tx_hex"]) == received == TX and
          evidence["ftdi_local"] == attached["1-2"]["local"]["bus_id"])
    check("ordinary helper export/unexport markers", "RUSTUSB_PAIR_EXPORT: PASS uid=1000 devices=2 via=existing_helper" in log.splitlines() and
          "RUSTUSB_PAIR_UNEXPORT: PASS uid=1000 devices=2 original_drivers=true" in log.splitlines())
    summary["probe_events"] = events
    summary["snapshots"] = snapshots
    summary["actual_io"] = evidence
    summary["check_count"] = len(checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skip-build", action="store_true", help="use existing product binaries; the standalone probe is still compiled")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0 or os.uname().machine != "x86_64":
        parser.error("ordinary host UID and x86_64 are required; only the disposable guest is root")
    if re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel) is None or not 60 <= args.timeout <= 1800:
        parser.error("safe kernel token and timeout60..1800 required")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    before = source_state()
    summary = {"result": "FAIL", "scope": "single disposable guest; kernel usbip_host to anonymous AF_UNIX stream pair to VHCI; no TCP proxy, TLS, remote authentication, or real hardware",
               "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "host_uid": os.geteuid(),
               "kernel": args.kernel, "source_before": before, "build_performed": not args.skip_build,
               "build_command": ["cargo", "build", "--workspace", "--release", "--locked"],
               "build_features": "default" if not args.skip_build else "external prebuilt; not inferred", "topology": {"guests": 1, "qemu_nics": 0, "usb_models": ["usb-kbd", "usb-serial"], "tls": False},
               "qemu_version": subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).strip(),
               "rustc_version": subprocess.check_output(["rustc", "--version"], text=True).strip()}
    process = qmp = serial = stderr = None
    received = bytearray()
    injections = []
    try:
        if before["git_status"]:
            raise RuntimeError("freeze and commit all sources before a mechanism acceptance run")
        (evidence / "source").mkdir()
        for name in SCRIPTS:
            shutil.copyfile(REPO / "scripts/qemu" / name, evidence / "source" / name)
        with (evidence / "build.log").open("wb") as build:
            if not args.skip_build:
                subprocess.run(summary["build_command"], cwd=REPO, stdout=build, stderr=subprocess.STDOUT, check=True)
        summary["binary_sha256"] = {name: native.sha256(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        with tempfile.TemporaryDirectory(prefix="rustusb-kernel-pair-") as temporary:
            work = Path(temporary)
            probe = work / "kernel-socketpair-probe"
            command = ["rustc", "--edition=2024", "-O", "-D", "warnings", str(REPO / "scripts/qemu/kernel-socketpair-probe.rs"), "-o", str(probe)]
            summary["probe_build_command"] = command
            with (evidence / "build.log").open("ab") as build:
                subprocess.run(command, cwd=REPO, stdout=build, stderr=subprocess.STDOUT, check=True)
            summary["probe_binary_sha256"] = native.sha256(probe)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            archive, summary["guest_binaries"], summary["guest_modules"] = payload(work, args.kernel, probe)
            if any(summary["guest_binaries"][name]["sha256"] != value for name, value in summary["binary_sha256"].items()):
                raise RuntimeError("product binaries changed during payload creation")
            summary["kernel_image_sha256"], summary["initramfs_sha256"] = native.sha256(image), native.sha256(archive)
            summary["native_tool_sha256"] = {name: native.sha256(native.real_tool(name, args.kernel)) for name in ["usbip", "usbipd"]}
            summary["guest_utility_sha256"] = {name: native.sha256(work / "root" / path) for name, path in {"busybox": "bin/busybox", "jq": "usr/bin/jq", "setpriv": "usr/bin/setpriv"}.items()}
            for channel in ["serial", "evidence"]:
                (evidence / f"guest-{channel}.log").touch()
            stderr = (evidence / "guest-qemu.stderr").open("wb")
            accel = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in accel else "tcg"
            command = ["qemu-system-x86_64", "-m", "512", "-smp", "2", *accel, "-kernel", str(image), "-initrd", str(archive),
                       "-append", "console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.kernel_socketpair_test=1",
                       "-display", "none", "-serial", f"file:{evidence / 'guest-serial.log'}", "-serial", f"file:{evidence / 'guest-evidence.log'}",
                       "-monitor", "none", "-no-reboot", "-nic", "none", "-device", "qemu-xhci,id=usb",
                       "-device", "usb-kbd,id=pair-kbd,bus=usb.0,port=1", "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off",
                       "-device", "usb-serial,id=pair-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
                       "-qmp", f"unix:{work / 'qmp.sock'},server=on,wait=off"]
            summary["qemu_command"] = command
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
            deadline = time.monotonic() + args.timeout
            qmp = physical.Qmp(work / "qmp.sock", deadline, process)
            serial = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            serial.settimeout(5)
            serial.connect(str(work / "ftdi.sock"))
            serial.setblocking(False)
            failure_at = None
            while time.monotonic() < deadline:
                log = (evidence / "guest-evidence.log").read_text(errors="replace")
                kernel = (evidence / "guest-serial.log").read_text(errors="replace")
                failures = [line for text in [log, kernel] for line in text.splitlines() if any(marker in line for marker in BAD)]
                if failures and failure_at is None:
                    failure_at = time.monotonic()
                    summary["first_observed_failure"] = failures[0]
                    (evidence / "first-observed-failure.json").write_text(json.dumps({"line": failures[0]}) + "\n")
                if failure_at is None:
                    if "RUSTUSB_PAIR_WAIT_INPUT key=1" in log and not any(item["kind"] == "key" for item in injections):
                        qmp.call("send-key", {"keys": [{"type": "qcode", "data": "1"}], "hold-time": 100})
                        injections.append({"kind": "key", "key": "1", "monotonic": time.monotonic()})
                    if "RUSTUSB_PAIR_WAIT_SERIAL bytes=35" in log and not any(item["kind"] == "serial" for item in injections):
                        serial.settimeout(2)
                        serial.sendall(RX)
                        serial.setblocking(False)
                        (evidence / "serial-injected.bin").write_bytes(RX)
                        injections.append({"kind": "serial", "bytes": len(RX), "sha256": hashlib.sha256(RX).hexdigest(), "monotonic": time.monotonic()})
                while True:
                    try:
                        chunk = serial.recv(4096)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    received.extend(chunk)
                    if not TX.startswith(received):
                        raise RuntimeError("unexpected/extra FTDI output bytes")
                if process.poll() is not None:
                    break
                if failure_at is not None and time.monotonic() - failure_at >= 30:
                    raise RuntimeError("guest failure; diagnostic grace exhausted")
                time.sleep(0.05)
            else:
                raise RuntimeError("single-guest mechanism deadline exceeded")
            if process.wait(timeout=10) != 0:
                raise RuntimeError("QEMU exited nonzero")
            serial.settimeout(2)
            while True:
                chunk = serial.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
            log = (evidence / "guest-evidence.log").read_text(errors="replace")
            kernel = (evidence / "guest-serial.log").read_text(errors="replace")
            if any(marker in log + kernel for marker in BAD):
                raise RuntimeError("guest failure or kernel integrity marker")
            verify(log, summary, bytes(received))
            if {item["kind"] for item in injections} != {"key", "serial"}:
                raise RuntimeError("actual QMP/chardev injections incomplete")
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = str(error)
    finally:
        if qmp is not None:
            summary["qmp_events"] = qmp.events
            qmp.close()
        if serial is not None:
            serial.close()
        if process is not None and process.poll() is None:
            summary["emergency_qemu_cleanup"] = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if stderr is not None:
            stderr.close()
        summary["guest_exit_code"] = process.returncode if process is not None else None
        summary["injections"] = injections
        (evidence / "serial-received.bin").write_bytes(received)
        summary["serial_output"] = {"length": len(received), "sha256": hashlib.sha256(received).hexdigest(), "exact_match": received == TX}
        summary["source_after"] = source_state()
        summary["source_guard_passed"] = summary["source_after"] == before
        summary["binary_guard_passed"] = all(native.sha256(REPO / "target/release" / name) == value for name, value in summary.get("binary_sha256", {}).items()) and bool(summary.get("binary_sha256"))
        if not summary["source_guard_passed"] or not summary["binary_guard_passed"] or summary.get("emergency_qemu_cleanup"):
            summary["result"] = "FAIL"
        summary["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        artifacts = [{"path": str(path.relative_to(evidence)), "length": path.stat().st_size, "sha256": native.sha256(path)}
                     for path in sorted(evidence.rglob("*")) if path.is_file()]
        (evidence / "artifact-sha256.json").write_text(json.dumps(artifacts, indent=2) + "\n")
        print(json.dumps({"result": summary["result"], "evidence": str(evidence), "checks": summary.get("check_count", 0)}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
