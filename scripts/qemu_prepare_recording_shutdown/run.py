#!/usr/bin/env python3
"""Guest-only prepare/recording stop gates; old-product failures remain failures."""
import argparse
import datetime
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
OWN = Path(__file__).resolve().parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

agent_support = load("prepare_agent_support", REPO / "scripts/qemu/run-agent-qemu.py")
secure = agent_support.secure
physical = secure.physical
digest, git, command = secure.digest, secure.git, secure.command
credentials, connect_unix, artifacts = secure.credentials, secure.connect_unix, secure.artifacts
verification = load("prepare_verifier", OWN / "verify.py")
local_mode = load("prepare_local_mode", OWN / "local.py")

def inputs():
    result = agent_support.inputs()
    for path in OWN.iterdir():
        if path.is_file() and path.suffix in {".py", ".sh", ".c", ".md"}:
            result[str(path.relative_to(REPO))] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    return dict(sorted(result.items()))

def guest_source(case, stop):
    if case not in {"native-resource", "native-available", "usbfs-resource", "usbfs-initializer"} or stop not in {"job", "term", "session"} or (stop == "session" and not case.startswith("native-")) or (case == "native-available" and stop != "session"):
        raise ValueError("fixed prepare case/stop required")
    prefix = agent_support.unique_before(agent_support.guest_source(), 'if [ "$ROLE" = server ]; then\n    STAGE=server-devices\n')
    anchor = '[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2'
    if prefix.count(anchor) != 1:
        raise RuntimeError("agent helper composition guard changed")
    prefix = prefix.replace(anchor, '[ "${AGENT_COMMON:-}" = 1 ] || exit 2')
    prefix = prefix.replace("RUSTUSB_AGENT_RESULT", "RUSTUSB_PREPARE_RESULT")
    body = (OWN / "guest-init.sh").read_text()
    if stop == "session": body = local_mode.compose(body)
    return prefix + f"\nCASE={case} STOP_MODE={stop} PREPARE_COMMON=1\n" + body

def payload(work, kernel, certificates, role, probe, case, stop):
    _, provenance = secure.payload(work, kernel, certificates, role)
    root = work / "root"
    (root / "etc/rustusb/client-pin.txt").chmod(0o644)
    if role == "server":
        secure.native.copy_binary(probe, "/usr/bin/prepare-gate", root)
        provenance["usr/bin/prepare-gate"] = digest(root / "usr/bin/prepare-gate")
    (work / "payload-dependencies.json").write_text(json.dumps(secure.validate_payload(root), indent=2) + "\n")
    (root / "init").write_text(guest_source(case, stop))
    (root / "init").chmod(0o755)
    shutil.copyfile(root / "init", work / "generated-init.sh")
    subprocess.run(["dash", "-n", str(root / "init")], check=True)
    provenance["init"] = digest(root / "init")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
    archive = work / "prepare-initramfs.cpio.gz"
    archive.write_bytes(gzip.compress(packed.stdout))
    return archive, provenance

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--case", choices=["native-resource", "native-available", "usbfs-resource", "usbfs-initializer"], default="native-resource")
    parser.add_argument("--stop", choices=["job", "term", "session"], default="job")
    parser.add_argument("--family", choices=["ipv4", "ipv6"], default="ipv4")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence uses the locked all-features build")
    args = parser.parse_args()
    args.backend = "native" if args.case.startswith("native-") else "usbfs"
    if (args.stop == "session" and args.backend != "native") or (args.case == "native-available" and args.stop != "session"):
        parser.error("local StopSession is Native only; native-available requires --stop session")
    if os.geteuid() == 0 or os.uname().machine != "x86_64": raise RuntimeError("ordinary x86_64 host user required")
    if not 60 <= args.timeout <= 900: raise RuntimeError("timeout must be 60..900 seconds")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": 1, "result": "FAIL", "backend": args.backend, "family": args.family, "case": args.case, "stop": args.stop,
               "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "head": git("rev-parse", "HEAD"),
               "git_status_before": git("status", "--porcelain"), "source_inputs": inputs(),
               "scope": "two disposable QEMU guests; actual UID2000 agent and helper; same-job two-device preparation; selected fsync or GET_CONFIGURATION syscall ENTRY; no kernel-active or pre-listener I/O claim; old-product RED remains FAIL",
               "build_features": "--workspace --all-features --release --locked", "build_skipped": args.skip_build}
    processes, handles, connections = {}, [], {}
    qmp = ftdi = None
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
        if inputs() != summary["source_inputs"]: raise RuntimeError("build changed frozen inputs")
        summary["binary_sha256"] = {name: digest(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        command(["qemu-system-x86_64", "--version"], evidence / "tool-versions.log")
        command(["rustc", "--version", "--verbose"], evidence / "tool-versions.log")
        with tempfile.TemporaryDirectory(prefix="rustusb-prepare-") as temporary:
            work = Path(temporary)
            probe = work / "prepare-gate"
            command(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", OWN / "prepare-gate.c", "-o", probe], evidence / "probe-build.log")
            summary["probe_sha256"] = digest(probe)
            certs = credentials(work, evidence)
            archives = {}
            summary["payload_sha256"] = {}
            summary["initramfs_sha256"] = {}
            summary["credential_payloads"] = {}
            for role in ["server", "client"]:
                role_work = work / f"{role}-payload"
                role_work.mkdir()
                archives[role], summary["payload_sha256"][role] = payload(role_work, args.kernel, certs, role, probe, args.case, args.stop)
                shutil.copyfile(role_work / "generated-init.sh", evidence / f"{role}-generated-init.sh")
                shutil.copyfile(role_work / "payload-dependencies.json", evidence / f"{role}-payload-dependencies.json")
                summary["initramfs_sha256"][role] = digest(archives[role])
                summary["credential_payloads"][role] = sorted(path.name for path in (role_work / "root/etc/rustusb").iterdir())
                for name, sha in summary["binary_sha256"].items():
                    if summary["payload_sha256"][role][f"usr/bin/{name}"] != sha:
                        raise RuntimeError("ELF changed during role-specific payload copy")
            image = work / "vmlinuz"
            try: shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            summary["kernel_image_sha256"] = digest(image)
            kernel_config = Path("/boot") / f"config-{args.kernel}"
            shutil.copyfile(kernel_config, evidence / "kernel.config")
            summary["kernel_config_sha256"] = digest(kernel_config)
            summary["kernel"] = args.kernel
            links = socket.socketpair()
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            deadline = time.monotonic() + args.timeout
            for index, role in enumerate(["server", "client"]):
                for channel in ["serial", "evidence"]: (evidence / f"{role}-{channel}.log").touch()
                output = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(output)
                cmd = ["qemu-system-x86_64", "-m", "768", "-smp", "2", *acceleration, "-rtc", "base=utc,clock=host", "-kernel", str(image), "-initrd", str(archives[role]),
                       "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.agent=1 rustusb.prepare_stop=1 rustusb.role={role} rustusb.backend={args.backend} rustusb.family={args.family}",
                       "-display", "none", "-serial", f"file:{evidence / (role+'-serial.log')}", "-serial", f"file:{evidence / (role+'-evidence.log')}",
                       "-chardev", f"socket,id=control,path={work / (role+'-control.sock')},server=on,wait=off", "-serial", "chardev:control",
                       "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=external,fd={links[index].fileno()}",
                       "-device", f"e1000,netdev=external,mac=52:54:00:00:00:{61+index}",
                       "-object", f"filter-dump,id=capture,netdev=external,file={evidence / (role+'-external.pcap')}"]
                if role == "server" and args.backend != "virtual":
                    cmd += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,id=secure-kbd,bus=usb.0,port=1",
                            "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off",
                            "-device", "usb-serial,id=secure-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
                            "-qmp", f"unix:{work / 'qmp.sock'},server=on,wait=off"]
                summary.setdefault("qemu_commands", {})[role] = cmd
                processes[role] = subprocess.Popen(cmd, pass_fds=[links[index].fileno()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=output)
            for link in links: link.close()
            for role in processes: connections[role] = connect_unix(work / f"{role}-control.sock", processes[role], deadline)
            if args.backend != "virtual":
                qmp = physical.Qmp(work / "qmp.sock", deadline, processes["server"])
                ftdi = connect_unix(work / "ftdi.sock", processes["server"], deadline)
                ftdi.setblocking(False)
            tx = bytearray()
            control_log = evidence / "host-control.jsonl"

            def send_once(mark, role, message):
                if mark in sent: return
                connections[role].sendall(message.encode() + b"\n")
                with control_log.open("a") as log:
                    log.write(json.dumps({"monotonic": time.monotonic(), "mark": mark, "role": role, "command": message}) + "\n")
                sent.add(mark)

            while time.monotonic() < deadline:
                logs = {role: (evidence / f"{role}-evidence.log").read_text(errors="replace") for role in processes}
                for role, log in logs.items():
                    for line in log.splitlines():
                        if "RUSTUSB_PREPARE_RESULT: FAIL" in line:
                            marker = {"role": role, "line": line}
                            if marker not in summary.setdefault("observed_failure_markers", []): summary["observed_failure_markers"].append(marker)
                    kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
                    if "Kernel panic" in kernel or "Oops:" in kernel: raise RuntimeError(f"{role} kernel fault")
                # A latched held-cleanup failure is not an immediate host abort:
                # the guest first releases this exact syscall and collects reuse.
                keys = re.search(r"^AGENT_KEYS (\[.*\])$", logs["server"], re.M)
                if keys: send_once("keys", "client", keys[1])
                if "PREPARE_SERVER_READY\n" in logs["server"] and "PREPARE_CLIENT_READY\n" in logs["client"]:
                    send_once("start-target", "server", "START_TARGET")
                if "PREPARE_GATE_HELD\n" in logs["server"]: send_once("held", "client", "HELD")
                if "PREPARE_LISTENER_REFUSED\n" in logs["client"] or "PREPARE_LOCAL_LISTENER_PROBED\n" in logs["client"]: send_once("stop-target", "server", "STOP_TARGET")
                if "PREPARE_LOCAL_SURVIVOR_READY\n" in logs["server"]: send_once("start-survivor", "client", "START_SURVIVOR")
                if "PREPARE_LOCAL_WAIT_HID\n" in logs["client"] and "survivor-injected" not in sent:
                    arguments = {"keys": [{"type": "qcode", "data": "1"}], "hold-time": 100}
                    response = qmp.call("send-key", arguments)
                    (evidence / "qmp-survivor-input.json").write_text(json.dumps({"command":"send-key","arguments":arguments,"return":response}) + "\n")
                    sent.add("survivor-injected")
                if "PREPARE_LOCAL_SURVIVOR_IO\n" in logs["client"]: send_once("survivor-io", "server", "SURVIVOR_IO")
                if "PREPARE_LOCAL_DETACH_HID\n" in logs["server"]: send_once("detach-survivor", "client", "DETACH_SURVIVOR")
                if "PREPARE_LOCAL_SURVIVOR_DETACHED\n" in logs["client"]: send_once("survivor-detached", "server", "SURVIVOR_DETACHED")
                if "PREPARE_LOCAL_EMPTY_READY\n" in logs["server"]: send_once("empty-catalog-request", "client", "EMPTY_CATALOG")
                if "PREPARE_LOCAL_EMPTY_CATALOG\n" in logs["client"]: send_once("empty-catalog-confirmed", "server", "EMPTY_CATALOG")
                if "PREPARE_LOCAL_JOB_CLOSED\n" in logs["server"]: send_once("local-job-closed", "client", "LOCAL_JOB_CLOSED")
                if "PREPARE_LOCAL_SURVIVOR_CLOSED\n" in logs["client"]: send_once("survivor-closed", "server", "SURVIVOR_CLOSED")
                if "PREPARE_REUSE_READY\n" in logs["server"]: send_once("start-reuse", "client", "START_REUSE")
                while select.select([ftdi], [], [], 0)[0]:
                    chunk = ftdi.recv(4096)
                    if not chunk: break
                    tx.extend(chunk)
                    (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                    if len(tx) > 35: raise RuntimeError("unexpected extra FTDI output")
                if "AGENT_CLIENT_WAIT_INPUT round=1 hid=yes\n" in logs["client"] and "injected" not in sent:
                    arguments = {"keys": [{"type": "qcode", "data": "1"}], "hold-time": 100}
                    response = qmp.call("send-key", arguments)
                    (evidence / "qmp-input.json").write_text(json.dumps({"command":"send-key","arguments":arguments,"return":response}) + "\n")
                    fixture = b"RUSTUSB-RX-1-0001:0123456789abcdef\n"
                    ftdi.settimeout(2); ftdi.sendall(fixture); ftdi.setblocking(False)
                    (evidence / "ftdi-host-injected-1.bin").write_bytes(fixture)
                    sent.add("injected")
                if "PREPARE_CLIENT_REUSED\n" in logs["client"] and len(tx) == 35: send_once("stop-reuse", "server", "STOP_REUSE")
                if "PREPARE_SERVER_CLOSED\n" in logs["server"]: send_once("server-closed", "client", "SERVER_CLOSED")
                if "PREPARE_CLIENT_CLOSED\n" in logs["client"] and "server-closed" in sent:
                    for role in ["server", "client"]: send_once("finish-"+role, role, "FINISH")
                if all(process.poll() is not None for process in processes.values()): break
                # Premature failures enter the finally ABORT/release collector.
                if summary.get("observed_failure_markers") and "finish-server" not in sent:
                    raise RuntimeError("guest failed before complete held/release/reuse sequence")
                time.sleep(0.05)
            else: raise RuntimeError("two-guest prepare run deadline exceeded")
            if any(process.wait() != 0 for process in processes.values()): raise RuntimeError("nonzero actual QEMU exit")
            if ftdi:
                while select.select([ftdi], [], [], 0)[0]:
                    chunk = ftdi.recv(4096)
                    if not chunk: break
                    tx.extend(chunk)
                (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                (evidence / "qmp-events.json").write_text(json.dumps(qmp.events, indent=2) + "\n")
            summary["guest_artifacts"] = artifacts(evidence)
            verifier = load("prepare_local_verifier", OWN / "verify_local.py") if args.stop == "session" else verification
            summary["facts"] = verifier.verify(evidence, args, summary)
            if summary["facts"]["failures"]: raise RuntimeError("; ".join(summary["facts"]["failures"]))
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
                time.sleep(0.05)
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        for connection in connections.values(): connection.close()
        if qmp: qmp.close()
        if ftdi: ftdi.close()
        for handle in handles: handle.close()
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        summary["head_after"] = git("rev-parse", "HEAD")
        summary["git_status_after"] = git("status", "--porcelain")
        summary["source_guard"] = inputs() == summary["source_inputs"]
        summary["binary_guard"] = all(digest(REPO / "target/release" / name) == sha for name, sha in summary.get("binary_sha256", {}).items())
        summary["head_guard"] = summary["head_after"] == summary["head"]
        summary["clean_guard"] = not summary["git_status_before"] and not summary["git_status_after"]
        if not all(summary[key] for key in ["source_guard", "binary_guard", "head_guard", "clean_guard"]): summary["result"] = "FAIL"
        if processes and "guest_artifacts" not in summary:
            try: summary["guest_artifacts"] = artifacts(evidence)
            except Exception as error: summary["artifact_error"] = str(error)
        summary["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        summary["artifact_manifest"] = {str(path.relative_to(evidence)): {"bytes": path.stat().st_size, "sha256": digest(path)}
                                        for path in evidence.rglob("*") if path.is_file() and path.name != "summary.json"}
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"result": summary["result"], "backend": args.backend, "family": args.family, "case": args.case, "stop": args.stop,
                      "evidence": str(evidence), "error": summary.get("error")}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
