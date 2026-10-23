#!/usr/bin/env python3
"""Actual Native same-data TLS v2 blackhole experiment in two disposable guests.

The initial implementation supports Native over either IP family. USBFS and
virtual workload branches are not enabled. Existing raw-v1 runners are read only.
"""
import argparse
import base64
import datetime
import gzip
import ipaddress
import os
import re
import select
import tempfile
import time
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys

sys.dont_write_bytecode = True
from valve import Binding, Valve  # noqa: E402
from packets import Flow, digest as digest_bytes
import audit as packet_audit
from verify import phase_proof, verify as verify_lifecycle

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
EXPECTED_QEMU = "6.2.0"
DATA_LINK = {"protocol": "framed_v2", "interval_millis": 1000, "response_timeout_millis": 3000}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def support():
    path = REPO / "scripts/qemu/run-secure-qemu.py"
    spec = importlib.util.spec_from_file_location("data_link_secure_support", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("shared secure support cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_inputs():
    result = support().inputs()
    paths = list(HERE.glob("*.py")) + list(HERE.glob("*.sh")) + list(HERE.glob("*.md"))
    paths += [REPO / "scripts/qemu/run-agent-qemu.py", REPO / "scripts/qemu/guest-agent-init.sh"]
    for path in paths:
        result[str(path.relative_to(REPO))] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    return dict(sorted(result.items()))


@dataclass
class PrivateLink:
    """Runner owns guest ends until spawn; valve owns its two ends until close."""
    guests: dict
    valve: Valve

    def after_spawn(self):
        for endpoint in self.guests.values():
            endpoint.close()

    def close(self):
        self.after_spawn()
        self.valve.close()


def private_link(evidence, family):
    guests, bridge = {}, {}
    try:
        for role in ("server", "client"):
            guests[role], bridge[role] = socket.socketpair()
        valve = Valve(bridge, Path(evidence) / "valve.jsonl", 4 if family == "ipv4" else 6)
        return PrivateLink(guests, valve)
    except Exception:
        for endpoint in list(guests.values()) + list(bridge.values()):
            endpoint.close()
        raise


def plan(backend, family):
    before = source_inputs()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True)
    result = {
        "schema_version": 1, "result": "PLAN_ONLY", "implementation": "native_workload_written_not_yet_run",
        "source_head": head, "source_inputs": before, "git_status": status,
        "backend": backend, "family": family, "required_qemu_version": EXPECTED_QEMU,
        "actual_qemu_version_verified": False,
        "tls_config_schema_version": 2, "data_link": DATA_LINK, "service_disk_schema": 2,
        "health_rpc": "session_health", "health_cli": "agent health PROFILE",
        "phases": ["c2s", "s2c", "both"],
        "required_each_phase": [
            "fresh actual session/attempt, paired data TCP tuple/connection identities and SYN epoch",
            "real HID key and serial 35-byte RX/TX before fault; actual matched probe/RTT",
            "USBFS sampled pending+inflight, or Native idle scope explicitly limited",
            "target packet drops with both QEMU socket FDs retained",
            "other original device receipt remains and serial 35-byte RX/TX continue",
            "fresh independent Catalog succeeds while target data link fails",
            "actual timeout/EOF operation + separate cleanup and exact retained proof",
            "disarm only after actual terminal observations; explicit fresh attempt I/O",
        ],
        "dependencies_found": {name: shutil.which(name) for name in
                               ("qemu-system-x86_64", "busybox", "cpio", "openssl", "ip", "ss", "jq", "dash")},
        "not_implemented": ["USBFS and virtual actual guest workload branches", "Native pending-URB instrumentation"],
        "scope": "Preparatory source plan only. No guest, Cargo build, USB operation or runtime verdict.",
        "qemu_runs_executed": 0,
    }
    result["source_guard"] = before == source_inputs()
    if not result["source_guard"]:
        raise RuntimeError("source changed during plan capture")
    return result



def agent_support():
    path = REPO / "scripts/qemu/run-agent-qemu.py"
    spec = importlib.util.spec_from_file_location("data_link_agent_support", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("shared agent support cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def once(text, anchor):
    if text.count(anchor) != 1:
        raise RuntimeError("shared composition anchor changed: " + anchor)
    return text.split(anchor, 1)


def guest_source():
    agent = agent_support()
    original = (REPO / "scripts/qemu/guest-agent-init.sh").read_text()
    server_anchor = 'if [ "$ROLE" = server ]; then\n    STAGE=server-devices\n'
    prefix = once(agent.guest_source(), server_anchor)[0]
    prefix = prefix.replace("RUSTUSB_AGENT_RESULT", "RUSTUSB_LINK_RESULT")
    prefix = prefix.replace("rustusb.agent=1", "rustusb.link_health=1")
    server = '    STAGE=server-devices\n' + once(once(original, server_anchor)[1], "    manifest; agent_start 1; denied server\n")[0]
    client = '    STAGE=client-prepare\n' + once(once(original, 'else\n    STAGE=client-prepare\n')[1], "    manifest; agent_start 1; denied hid\n")[0]
    config_anchor = "{schema_version:1,broker_uid:2000,"
    config_v2 = "{schema_version:2,data_link:{protocol:\"framed_v2\",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,"
    for fragment in (server, client):
        if fragment.count(config_anchor) != 1:
            raise RuntimeError("shared trusted TLS config anchor changed")
    body = (HERE / "guest-init.sh").read_text()
    if body.count("@@SERVER_SETUP@@") != 1 or body.count("@@CLIENT_SETUP@@") != 1:
        raise RuntimeError("new guest setup anchor changed")
    body = body.replace("@@SERVER_SETUP@@", server.replace(config_anchor, config_v2))
    body = body.replace("@@CLIENT_SETUP@@", client.replace(config_anchor, config_v2))
    return prefix + "\nLINK_COMMON=1\n" + body


def payload(work, kernel, certificates, role):
    _, provenance = support().payload(work, kernel, certificates, role)
    root = work / "root"
    (root / "etc/rustusb/client-pin.txt").chmod(0o644)
    (root / "init").write_text(guest_source())
    (root / "init").chmod(0o755)
    shutil.copyfile(root / "init", work / "generated-link-init.sh")
    subprocess.run(["dash", "-n", str(root / "init")], check=True)
    provenance["init"] = sha(root / "init")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
    archive = work / "link-initramfs.cpio.gz"
    archive.write_bytes(gzip.compress(packed.stdout))
    return archive, provenance


def collect_live(evidence, role, log):
    directory = evidence / "live" / role
    directory.mkdir(parents=True, exist_ok=True)
    pattern = r"^LINK_RAW_BEGIN ([A-Za-z0-9_.-]{1,100}) (\d+)\n([A-Za-z0-9+/=\n]+)\nLINK_RAW_END \1\n"
    for match in re.finditer(pattern, log, re.M):
        name, size, encoded = match.groups()
        size = int(size)
        if size > 1048576:
            raise RuntimeError("live artifact exceeded bound")
        raw = base64.b64decode("".join(encoded.split()), validate=True)
        if len(raw) != size:
            raise RuntimeError("incomplete live artifact")
        path = directory / name
        if path.exists():
            if path.read_bytes() != raw:
                raise RuntimeError("previous live artifact changed")
        else:
            path.write_bytes(raw)


def bind_phase(evidence, phase):
    bindings = evidence / "bindings"
    bindings.mkdir(exist_ok=True)
    values, flows = {}, []
    for role in ("client", "server"):
        directory = evidence / "live" / role
        state = json.loads((directory / f"agent-pre-{phase}.json").read_text())
        key = json.loads((evidence / "keys.json").read_text())[0]
        matches = [s for s in state["sessions"] if s["device_key"] == key and s["role"] == ("import" if role == "client" else "incoming") and not s["terminal"]]
        if len(matches) != 1:
            raise RuntimeError("one actual HID data session required for arm")
        session = matches[0]
        raw = (directory / f"health-{phase}-{role}-pre.json").read_bytes()
        reply = json.loads(raw)
        entries = [h for h in reply["response"]["sessions"] if h["session_id"] == session["id"]]
        if len(entries) != 1:
            raise RuntimeError("actual health/session mapping missing")
        health = entries[0]
        link = health["link"]
        if (health["instance_id"] != session["instance_id"] or health["availability"] != "live"
                or link["protocol"] != "framed_v2" or link["phase"] != "open" or link["end"] is not None
                or link["last_reply_sequence"] is None or link["last_round_trip_micros"] is None):
            raise RuntimeError("arm requires matching real current v2 reply")
        local = packet_audit._socket_address(link["identity"]["tcp"]["local"])
        peer = packet_audit._socket_address(link["identity"]["tcp"]["peer"])
        version = ipaddress.ip_address(local[0]).version
        flow = Flow(version, local[0], local[1], peer[0], peer[1])
        flows.append(flow if role == "client" else flow.reverse())
        values.update({f"{role}_instance": session["instance_id"], f"{role}_session": session["id"],
                       f"{role}_connection": link["identity"]["connection"], f"{role}_health_sha256": digest_bytes(raw)})
        target = bindings / f"{phase}-{role}-health.json"
        if target.exists() and target.read_bytes() != raw:
            raise RuntimeError("bound health bytes changed")
        target.write_bytes(raw)
    if flows[0] != flows[1]:
        raise RuntimeError("two actual data connection tuples are not reverses")
    return flows[0], Binding(**values)


def drive(evidence, processes, connections, private, qmp, ftdi, deadline, summary):
    sent, tx, active, arm_clock = set(), bytearray(), None, None
    control_log = evidence / "host-control.jsonl"

    def send_once(mark, role, message):
        if mark in sent:
            return
        connections[role].sendall(message.encode() + b"\n")
        with control_log.open("a") as log:
            log.write(json.dumps({"monotonic_ns": time.monotonic_ns(), "mark": mark, "role": role, "command": message}) + "\n")
        sent.add(mark)

    while time.monotonic() < deadline:
        private.valve.poll(0.01)
        logs = {role: (evidence / f"{role}-evidence.log").read_text(errors="replace") for role in processes}
        markers = [{"role": role, "line": line} for role, log in logs.items() for line in log.splitlines() if "RUSTUSB_LINK_RESULT: FAIL" in line]
        if markers:
            summary.setdefault("observed_failure_markers", markers)
            (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            raise RuntimeError("guest reported actual failure")
        for role in processes:
            kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
            if "Kernel panic" in kernel or "Oops:" in kernel:
                raise RuntimeError(f"{role} kernel fault")
            collect_live(evidence, role, logs[role])
        keys = re.search(r"^AGENT_KEYS (\[.*\])$", logs["server"], re.M)
        if keys:
            send_once("keys", "client", keys[1])
            (evidence / "keys.json").write_text(keys[1] + "\n")
        if "LINK_SERVER_READY\n" in logs["server"] and "keys" in sent:
            send_once("start", "client", "START")
        while select.select([ftdi], [], [], 0)[0]:
            chunk = ftdi.recv(4096)
            if not chunk:
                break
            tx.extend(chunk)
            (evidence / "ftdi-host-rx.bin").write_bytes(tx)
            if len(tx) > 245:
                raise RuntimeError("unexpected extra FTDI output")
        for round_id in range(1, 8):
            if f"AGENT_CLIENT_WAIT_INPUT round={round_id} " not in logs["client"]:
                continue
            phase = {1: "c2s", 3: "s2c", 5: "both"}.get(round_id)
            if phase:
                send_once(f"pre-{phase}", "server", f"PRE_{phase}")
                ready = f"LINK_PRE_READY role=server phase={phase}\n" in logs["server"]
            elif round_id == 7:
                send_once("final-held", "server", "FINAL_HELD")
                ready = "LINK_FINAL_HELD\n" in logs["server"]
            else:
                ready = True
            if ready and f"input-{round_id}" not in sent:
                if round_id % 2:
                    arguments = {"keys": [{"type": "qcode", "data": str(round_id)}], "hold-time": 100}
                    response = qmp.call("send-key", arguments)
                    with (evidence / "qmp-input.jsonl").open("a") as log:
                        log.write(json.dumps({"round": round_id, "command": "send-key", "arguments": arguments, "return": response}) + "\n")
                fixture = f"RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n".encode()
                ftdi.settimeout(2)
                ftdi.sendall(fixture)
                ftdi.setblocking(False)
                (evidence / f"ftdi-host-injected-{round_id}.bin").write_bytes(fixture)
                sent.add(f"input-{round_id}")
        for index, phase in enumerate(("c2s", "s2c", "both")):
            if (phase not in private.valve.used_rules and active is None and private.valve.quiescent()
                    and all(f"LINK_PRE_READY role={role} phase={phase}\n" in logs[role] for role in processes)):
                flow, binding = bind_phase(evidence, phase)
                private.valve.arm(phase, flow, phase, binding)
                active, arm_clock = phase, time.monotonic()
                for role in processes:
                    send_once(f"arm-{phase}-{role}", role, f"ARM_{phase}")
            if (active == phase and private.valve.quiescent()
                    and all(f"LINK_FAULT_CLOSED role={role} phase={phase}\n" in logs[role] for role in processes)
                    and f"AGENT_CLIENT_IO round={index * 2 + 2} " in logs["client"] and len(tx) >= (index * 2 + 2) * 35):
                bundle = phase_proof(evidence, phase, live=True)
                raw = (json.dumps(bundle, sort_keys=True, indent=2) + "\n").encode()
                (evidence / "bindings" / f"{phase}-terminal.json").write_bytes(raw)
                private.valve.disarm(phase, digest_bytes(raw))
                for role in processes:
                    send_once(f"next-{phase}-{role}", role, f"NEXT_{phase}")
                active, arm_clock = None, None
        if active is not None and time.monotonic() - arm_clock > 15:
            raise RuntimeError("fault did not produce real terminal proof in the 15-second observation window")
        if "AGENT_CLIENT_IO round=7 " in logs["client"] and len(tx) == 245:
            send_once("close-client", "client", "CLOSE")
        if "LINK_CLIENT_CLOSED\n" in logs["client"]:
            send_once("client-closed", "server", "CLIENT_CLOSED")
        if (all(f"LINK_NETWORK_DOWN role={role}\n" in logs[role] for role in processes)
                and private.valve.quiescent() and not private.valve.exit_allowed):
            private.valve.allow_guest_exit()
            for role in processes:
                send_once(f"finish-{role}", role, "FINISH")
        if all(process.poll() is not None for process in processes.values()):
            # Read final socket EOF after both actual QEMU processes have exited.
            for _ in range(100):
                if all(endpoint.eof for endpoint in private.valve.endpoints.values()):
                    break
                private.valve.poll(0.01)
            private.valve.finish()
            private.valve.close()
            return tx
        if any(process.poll() is not None and f"RUSTUSB_LINK_RESULT: PASS role={role}" not in logs[role] for role, process in processes.items()):
            raise RuntimeError("guest exited without terminal PASS")
    raise RuntimeError("two-guest data-link run deadline exceeded")


secure = support()
git, command, credentials, connect_unix, artifacts = secure.git, secure.command, secure.credentials, secure.connect_unix, secure.artifacts
physical = secure.physical


def run_experiment(args):
    if os.geteuid() == 0 or os.uname().machine != "x86_64": raise RuntimeError("ordinary x86_64 host user required")
    if not 60 <= args.timeout <= 900: raise RuntimeError("timeout must be 60..900 seconds")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": 1, "result": "FAIL", "backend": args.backend, "family": args.family,
               "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "head": git("rev-parse", "HEAD"),
               "git_status_before": git("status", "--porcelain"), "source_inputs": source_inputs(),
               "scope": "Native first slice: same data TLS v2 directional Ethernet blackholes, actual agent HID/FTDI, proof and fresh attempts; no Native pending-URB/physical hardware/complete Phase3 claim",
               "build_features": "--workspace --all-features --release --locked", "build_skipped": args.skip_build}
    processes, handles, connections = {}, [], {}
    qmp = ftdi = private = None
    sent = set()
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    try:
        if summary["git_status_before"]: raise RuntimeError("clean committed source required")
        for name in summary["source_inputs"]:
            destination = evidence / "source" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, destination)
        if not args.skip_build:
            command(["cargo", "build", "--workspace", "--all-features", "--release", "--locked"], evidence / "build.log")
        if source_inputs() != summary["source_inputs"]: raise RuntimeError("build changed frozen inputs")
        summary["binary_sha256"] = {name: sha(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        command(["qemu-system-x86_64", "--version"], evidence / "tool-versions.log")
        version_text = (evidence / "tool-versions.log").read_text()
        version = re.search(r"QEMU emulator version ([0-9.]+)", version_text)
        if version is None or version[1] != EXPECTED_QEMU:
            raise RuntimeError("actual QEMU differs from the source-reviewed socket framing version")
        summary["qemu_version"] = version[1]
        command(["rustc", "--version", "--verbose"], evidence / "tool-versions.log")
        with tempfile.TemporaryDirectory(prefix="rustusb-data-link-") as temporary:
            work = Path(temporary)
            certs = credentials(work, evidence)
            archives = {}
            summary["payload_sha256"] = {}
            summary["initramfs_sha256"] = {}
            summary["credential_payloads"] = {}
            for role in ["server", "client"]:
                role_work = work / f"{role}-payload"
                role_work.mkdir()
                archives[role], summary["payload_sha256"][role] = payload(role_work, args.kernel, certs, role)
                shutil.copyfile(role_work / "generated-link-init.sh", evidence / f"{role}-generated-link-init.sh")
                shutil.copyfile(role_work / "payload-dependencies.json", evidence / f"{role}-payload-dependencies.json")
                summary["initramfs_sha256"][role] = sha(archives[role])
                summary["credential_payloads"][role] = sorted(path.name for path in (role_work / "root/etc/rustusb").iterdir())
                for name, binary_digest in summary["binary_sha256"].items():
                    if summary["payload_sha256"][role][f"usr/bin/{name}"] != binary_digest:
                        raise RuntimeError("ELF changed during role-specific payload copy")
            image = work / "vmlinuz"
            try: shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            summary["kernel_image_sha256"] = sha(image)
            kernel_config = Path("/boot") / f"config-{args.kernel}"
            shutil.copyfile(kernel_config, evidence / "kernel.config")
            summary["kernel_config_sha256"] = sha(kernel_config)
            summary["kernel"] = args.kernel
            private = private_link(evidence, args.family)
            links = private.guests
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            deadline = time.monotonic() + args.timeout
            for index, role in enumerate(["server", "client"]):
                for channel in ["serial", "evidence"]: (evidence / f"{role}-{channel}.log").touch()
                output = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(output)
                cmd = ["qemu-system-x86_64", "-m", "768", "-smp", "2", *acceleration, "-rtc", "base=utc,clock=host", "-kernel", str(image), "-initrd", str(archives[role]),
                       "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.secure=1 rustusb.agent=1 rustusb.link_health=1 rustusb.role={role} rustusb.backend={args.backend} rustusb.family={args.family}",
                       "-display", "none", "-serial", f"file:{evidence / (role+'-serial.log')}", "-serial", f"file:{evidence / (role+'-evidence.log')}",
                       "-chardev", f"socket,id=control,path={work / (role+'-control.sock')},server=on,wait=off", "-serial", "chardev:control",
                       "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=external,fd={links[role].fileno()}",
                       "-device", f"e1000,netdev=external,mac=52:54:00:00:00:{61+index}",
                       "-object", f"filter-dump,id=capture,netdev=external,file={evidence / (role+'-external.pcap')}"]
                if role == "server" and args.backend != "virtual":
                    cmd += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,id=secure-kbd,bus=usb.0,port=1",
                            "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off",
                            "-device", "usb-serial,id=secure-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
                            "-qmp", f"unix:{work / 'qmp.sock'},server=on,wait=off"]
                summary.setdefault("qemu_commands", {})[role] = cmd
                processes[role] = subprocess.Popen(cmd, pass_fds=[links[role].fileno()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=output)
            private.after_spawn()
            for role in processes: connections[role] = connect_unix(work / f"{role}-control.sock", processes[role], deadline)
            if args.backend != "virtual":
                qmp = physical.Qmp(work / "qmp.sock", deadline, processes["server"])
                ftdi = connect_unix(work / "ftdi.sock", processes["server"], deadline)
                ftdi.setblocking(False)
            tx = drive(evidence, processes, connections, private, qmp, ftdi, deadline, summary)
            if any(process.wait() != 0 for process in processes.values()): raise RuntimeError("nonzero actual QEMU exit")
            if ftdi:
                while select.select([ftdi], [], [], 0)[0]:
                    chunk = ftdi.recv(4096)
                    if not chunk: break
                    tx.extend(chunk)
                (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                (evidence / "qmp-events.json").write_text(json.dumps(qmp.events, indent=2) + "\n")
            summary["guest_artifacts"] = artifacts(evidence)
            execution_hashes = []
            helper_hashes = []
            for role in ["server", "client"]:
                for path in (evidence / f"{role}-artifacts").glob("executable-*.txt"):
                    actual = path.read_text().split()[0]
                    helper = path.name == "executable-helper.txt"
                    program = "rustusb-helper" if helper else "rustusb"
                    if actual != summary["binary_sha256"][program]:
                        raise RuntimeError("actual guest broker executable differs from frozen ELF")
                    (helper_hashes if helper else execution_hashes).append(str(path.relative_to(evidence)))
            if len(execution_hashes) != 2:
                raise RuntimeError("missing actual broker executable/identity observations")
            if len(helper_hashes) != 2:
                raise RuntimeError("missing actual privileged helper executable observations")
            summary["actual_broker_executable_receipts"] = execution_hashes
            summary["actual_helper_executable_receipts"] = helper_hashes
            summary["facts"] = verify_lifecycle(evidence, args)
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = f"{type(error).__name__}: {error}"
    finally:
        # Preserve the first failure and give PID1's failure trap time to finish
        # its raw artifact dump. ABORT is deliberately never an accepted command.
        # Killing QEMU at the first FAIL marker would discard the following dump.
        if summary["result"] != "PASS" and connections:
            for role, connection in connections.items():
                if processes[role].poll() is None:
                    try: connection.sendall(b"ABORT\n")
                    except OSError: pass
            capture_deadline = time.monotonic() + 25
            while any(process.poll() is None for process in processes.values()) and time.monotonic() < capture_deadline:
                if private and not private.valve.closed and private.valve.failure is None:
                    try: private.valve.poll(0.01)
                    except Exception as collector_error: summary.setdefault("valve_collector_error", str(collector_error))
                time.sleep(0.05)
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        if private:
            try: private.close()
            except Exception as valve_error: summary.setdefault("valve_close_error", str(valve_error)); summary["result"] = "FAIL"
        for connection in connections.values(): connection.close()
        if qmp: qmp.close()
        if ftdi: ftdi.close()
        for handle in handles: handle.close()
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        summary["head_after"] = git("rev-parse", "HEAD")
        summary["git_status_after"] = git("status", "--porcelain")
        summary["source_guard"] = source_inputs() == summary["source_inputs"]
        summary["binary_guard"] = all(sha(REPO / "target/release" / name) == binary_digest for name, binary_digest in summary.get("binary_sha256", {}).items())
        summary["head_guard"] = summary["head_after"] == summary["head"]
        summary["clean_guard"] = not summary["git_status_before"] and not summary["git_status_after"]
        if not all(summary[key] for key in ["source_guard", "binary_guard", "head_guard", "clean_guard"]): summary["result"] = "FAIL"
        if processes and "guest_artifacts" not in summary:
            try: summary["guest_artifacts"] = artifacts(evidence)
            except Exception as error: summary["artifact_error"] = str(error)
        summary["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        summary["artifact_manifest"] = {str(path.relative_to(evidence)): {"bytes": path.stat().st_size, "sha256": sha(path)}
                                        for path in evidence.rglob("*") if path.is_file() and path.name != "summary.json"}
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"result": summary["result"], "backend": args.backend, "family": args.family,
                      "evidence": str(evidence), "error": summary.get("error")}))
    return 0 if summary["result"] == "PASS" else 1

def main():
    parser = argparse.ArgumentParser(description="Actual same-data TLS heartbeat blackhole QEMU (Native first slice)")
    parser.add_argument("--backend", choices=("native",), default="native")
    parser.add_argument("--family", choices=("ipv4", "ipv6"), required=True)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--timeout", type=int, default=480)
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence uses locked all-features build")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if args.plan:
        if args.output is None: parser.error("--plan requires --output")
        value = plan(args.backend, args.family)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True); output.write("\n")
        print(json.dumps({"result": "PLAN_ONLY", "output": str(args.output)}))
        return 0
    if args.evidence is None: parser.error("actual run requires --evidence")
    return run_experiment(args)


if __name__ == "__main__":
    raise SystemExit(main())
