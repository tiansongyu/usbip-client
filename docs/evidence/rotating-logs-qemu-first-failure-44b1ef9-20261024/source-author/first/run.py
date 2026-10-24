#!/usr/bin/env python3
"""Installed Native agent IPv4/framed-v2 process logging and removable lock fault.

Builds/runs only when explicitly invoked by the host coordinator. No host USB.
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
import select
import shutil
import socket
import struct
import subprocess
import tempfile
import time

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load locked source module")
    value=importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value
agent=module("logging_agent_support", REPO/"scripts/qemu/run-agent-qemu.py")
secure=agent.secure
physical=agent.physical
native=secure.native
digest,git,command=secure.digest,secure.git,secure.command
credentials,connect_unix,artifacts=secure.credentials,secure.connect_unix,secure.artifacts
verification=module("process_logging_verification",HERE/"verify.py")

def inputs():
    result=agent.inputs()
    for path in sorted(HERE.iterdir()):
        if path.is_file() and path.suffix in [".py",".sh",".md"]:
            result[str(path.relative_to(REPO))]={"sha256":digest(path),"bytes":path.stat().st_size}
    return dict(sorted(result.items()))

def once(text,anchor):
    if text.count(anchor)!=1: raise RuntimeError("shared composition anchor changed: "+anchor)
    return text.split(anchor,1)

def replace_once(text,anchor,replacement):
    if text.count(anchor)!=1: raise RuntimeError("shared replacement anchor changed: "+anchor)
    return text.replace(anchor,replacement)

def guest_source():
    original=(REPO/"scripts/qemu/guest-agent-init.sh").read_text()
    server_anchor='if [ "$ROLE" = server ]; then\n    STAGE=server-devices\n'
    prefix=once(agent.guest_source(),server_anchor)[0]
    prefix=prefix.replace("RUSTUSB_AGENT_RESULT","RUSTUSB_LOGGING_RESULT")
    prefix=replace_once(prefix,"cleanup() {", "base_cleanup() {")
    prefix=replace_once(prefix,"trap cleanup EXIT", "trap base_cleanup EXIT")
    helper='/usr/bin/rustusb-helper --physical-state-dir'
    if prefix.count(helper)!=1: raise RuntimeError("shared helper launch anchor changed")
    prefix=prefix.replace(helper,'/usr/bin/rustusb-helper --log-directory /var/log/rustusb/helper --log-max-bytes 4096 --log-retain 4 --physical-state-dir')
    launch='/usr/bin/rustusb agent --config /etc/rustusb/agent.json run >'
    if prefix.count(launch)!=1: raise RuntimeError("shared agent launch anchor changed")
    prefix=prefix.replace(launch,'/usr/bin/rustusb --log-directory /var/log/rustusb/agent --log-max-bytes 4096 --log-retain 4 agent --config /etc/rustusb/agent.json run >')
    prefix=replace_once(prefix,'    config_evidence "$AGENT_EPOCH"\n','    config_evidence "$AGENT_EPOCH"\n    logging_started agent "$AGENT_EPOCH"\n')
    prefix=replace_once(prefix,'    cp /etc/rustusb/policy.json /tmp/helper-policy.json\n','    cp /etc/rustusb/policy.json /tmp/helper-policy.json\n    logging_started helper started\n')
    body=(HERE/"guest-init.sh").read_text()
    server='    STAGE=server-devices\n'+once(once(original,server_anchor)[1],"    manifest; agent_start 1; denied server\n")[0]
    client='    STAGE=client-prepare\n'+once(once(original,'else\n    STAGE=client-prepare\n')[1],"    manifest; agent_start 1; denied hid\n")[0]
    anchor='{schema_version:1,broker_uid:2000,'
    replacement='{schema_version:2,data_link:{protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,'
    for text in [server,client]:
        if text.count(anchor)!=1: raise RuntimeError("shared TLS configuration anchor changed")
    for token in ["@@SERVER_SETUP@@","@@CLIENT_SETUP@@"]:
        if body.count(token)!=1: raise RuntimeError("logging setup anchor changed")
    body=body.replace("@@SERVER_SETUP@@",server.replace(anchor,replacement)).replace("@@CLIENT_SETUP@@",client.replace(anchor,replacement))
    return prefix+'\nPROCESS_LOGGING_COMMON=1\n'+body

def payload(work,kernel,certificates,role):
    _,provenance=secure.payload(work,kernel,certificates,role)
    root=work/"root"
    (root/"etc/rustusb/client-pin.txt").chmod(0o644)
    native.copy_binary(Path(shutil.which("flock") or "/usr/bin/flock"),"/usr/bin/flock",root)
    (root/"init").write_text(guest_source());(root/"init").chmod(0o755)
    shutil.copyfile(HERE/"gate.sh",root/"logging-gate.sh");(root/"logging-gate.sh").chmod(0o755)
    shutil.copyfile(root/"init",work/"generated-logging-init.sh")
    subprocess.run(["dash","-n",str(root/"init")],check=True)
    for name in ["init","logging-gate.sh","usr/bin/flock"]: provenance[name]=digest(root/name)
    dependencies=secure.validate_payload(root)
    (work/"payload-dependencies.json").write_text(json.dumps(dependencies,indent=2)+"\n")
    names=b".\0"+b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*"))+b"\0"
    packed=subprocess.run(["cpio","--null","-o","--format=newc","--owner=0:0"],input=names,cwd=root,capture_output=True,check=True)
    archive=work/"logging-initramfs.cpio.gz";archive.write_bytes(gzip.compress(packed.stdout))
    return archive,provenance

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--backend", choices=["native"], default="native")
    parser.add_argument("--family", choices=["ipv4"], default="ipv4")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence uses the locked all-features build")
    args = parser.parse_args()
    if os.geteuid() == 0 or os.uname().machine != "x86_64": raise RuntimeError("ordinary x86_64 host user required")
    if not 60 <= args.timeout <= 900: raise RuntimeError("timeout must be 60..900 seconds")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": 1, "result": "FAIL", "backend": args.backend, "family": args.family,
               "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "head": git("rev-parse", "HEAD"),
               "git_status_before": git("status", "--porcelain"), "source_inputs": inputs(),
               "scope": "two disposable Native IPv4/framed-v2 guests; normal, bounded logger-flock fault and actual fresh USB reuse; removable logger obstruction is not permanent filesystem cancellation or hardware acceptance",
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
        with tempfile.TemporaryDirectory(prefix="rustusb-process-logging-") as temporary:
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
                shutil.copyfile(role_work / "generated-logging-init.sh", evidence / f"{role}-generated-logging-init.sh")
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
                       "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.secure=1 rustusb.agent=1 rustusb.process_logging=1 rustusb.role={role} rustusb.backend={args.backend} rustusb.family={args.family}",
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
                markers = [{"role": role, "line": line} for role, log in logs.items() for line in log.splitlines() if "RUSTUSB_LOGGING_RESULT: FAIL" in line]
                if markers:
                    summary.setdefault("observed_failure_markers", markers)
                    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
                    raise RuntimeError("guest reported actual failure")
                for role in processes:
                    kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
                    if "Kernel panic" in kernel or "Oops:" in kernel: raise RuntimeError(f"{role} kernel fault")
                if all(f"LOG_NETWORK_READY role={role}\n" in logs[role] for role in processes):
                    for role in processes: send_once("network-"+role, role, "NETWORK_READY")
                keys = re.search(r"^AGENT_KEYS (\[.*\])$", logs["server"], re.M)
                if keys: send_once("keys", "client", keys[1])
                while select.select([ftdi], [], [], 0)[0]:
                    chunk = ftdi.recv(4096)
                    if not chunk: break
                    tx.extend(chunk)
                    (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                    if len(tx) > 105: raise RuntimeError("unexpected extra FTDI output")
                for round_id in [1, 2, 3]:
                    if f"LOG_SERVER_READY epoch={round_id}\n" in logs["server"] and "keys" in sent:
                        send_once(f"start-{round_id}", "client", f"START{round_id}")
                    if f"AGENT_CLIENT_WAIT_INPUT round={round_id} " in logs["client"]:
                        send_once(f"held-{round_id}", "server", f"HELD{round_id}")
                        if f"LOG_SERVER_HELD epoch={round_id}\n" in logs["server"] and f"input-{round_id}" not in sent:
                            arguments = {"keys": [{"type": "qcode", "data": str(round_id)}], "hold-time": 100}
                            response = qmp.call("send-key", arguments)
                            with (evidence / "qmp-input.jsonl").open("a") as log:
                                log.write(json.dumps({"round": round_id, "command": "send-key", "arguments": arguments, "return": response}) + "\n")
                            fixture = f"RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n".encode()
                            ftdi.settimeout(2); ftdi.sendall(fixture); ftdi.setblocking(False)
                            (evidence / f"ftdi-host-injected-{round_id}.bin").write_bytes(fixture)
                            sent.add(f"input-{round_id}")
                if "AGENT_CLIENT_IO round=1 " in logs["client"] and len(tx)>=35:
                    send_once("normal1", "server", "NORMAL1")
                if "LOG_SERVER_NORMAL_COMPLETE\n" in logs["server"] and "LOG_CLIENT_NORMAL_COMPLETE\n" in logs["client"]:
                    send_once("close1", "client", "CLOSE1")
                if "LOG_CLIENT_STOPPED epoch=1\n" in logs["client"]:
                    send_once("stop1", "server", "STOP1")
                if "AGENT_CLIENT_IO round=2 " in logs["client"] and len(tx)>=70:
                    send_once("fault2", "server", "FAULT2")
                if "LOG_ROOT_CLEAN_GATE_HELD\n" in logs["server"]:
                    send_once("check-closed2", "client", "CHECK_CLOSED2")
                if "LOG_CLIENT_STOPPED epoch=2\n" in logs["client"]:
                    send_once("release-gate", "server", "RELEASE_GATE")
                if "AGENT_CLIENT_IO round=3 " in logs["client"] and len(tx)>=105:
                    send_once("close3", "client", "CLOSE3")
                if "LOG_CLIENT_STOPPED epoch=3\n" in logs["client"]:
                    send_once("stop3", "server", "STOP3")
                if "LOG_SERVER_STOPPED epoch=3\n" in logs["server"]:
                    for role in processes: send_once("final-"+role,role,"FINAL")
                if all(process.poll() is not None for process in processes.values()): break
                if any(process.poll() is not None and f"RUSTUSB_LOGGING_RESULT: PASS role={role}" not in logs[role] for role, process in processes.items()):
                    raise RuntimeError("guest exited without terminal PASS")
                time.sleep(0.05)
            else: raise RuntimeError("two-guest agent run deadline exceeded")
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
            if len(execution_hashes) != 6:
                raise RuntimeError("missing actual broker executable/identity observations")
            if len(helper_hashes) != 2:
                raise RuntimeError("missing actual privileged helper executable observations")
            summary["actual_broker_executable_receipts"] = execution_hashes
            summary["actual_helper_executable_receipts"] = helper_hashes
            summary["facts"] = verification.verify(evidence, summary)
            for role in processes:
                final_kernel=(evidence / f"{role}-serial.log").read_text(errors="replace")
                if "Kernel panic" in final_kernel or "Oops:" in final_kernel: raise RuntimeError(f"{role} final kernel fault")
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = f"{type(error).__name__}: {error}"
        # Preserve the primary failure before any collector/close can also fail.
        try: (evidence / "primary-failure.json").write_text(json.dumps(summary, indent=2) + "\n")
        except OSError as failure: summary.setdefault("collection_errors", []).append({"operation":"primary checkpoint","error":str(failure)})
    finally:
        def collect(label, operation, default=None):
            try: return operation()
            except Exception as failure:
                summary.setdefault("collection_errors", []).append({"operation":label,"error":f"{type(failure).__name__}: {failure}"})
                summary["result"] = "FAIL"
                return default
        # A failed scenario never becomes successful during this collection
        # window. Let the independent 180s removable gate and cleanup finish.
        if summary["result"] != "PASS" and connections:
            for role, connection in connections.items():
                if processes[role].poll() is None:
                    collect(role+" abort control",lambda connection=connection:connection.sendall(b"ABORT\n"))
            capture_deadline = time.monotonic() + 200
            while any(process.poll() is None for process in processes.values()) and time.monotonic() < capture_deadline:
                time.sleep(0.05)
        for role, process in processes.items():
            if process.poll() is None:
                collect(role+" terminate",process.terminate)
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    collect(role+" kill",process.kill)
                    collect(role+" final wait",lambda process=process:process.wait(timeout=5))
                except Exception as failure:
                    summary.setdefault("collection_errors", []).append({"operation":role+" wait","error":str(failure)})
                    collect(role+" kill after wait error",process.kill)
                    collect(role+" final wait",lambda process=process:process.wait(timeout=5))
        for role, connection in connections.items(): collect(role+" control close",connection.close)
        if qmp: collect("QMP close",qmp.close)
        if ftdi: collect("FTDI close",ftdi.close)
        for number, handle in enumerate(handles): collect(f"stderr close {number}",handle.close)
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        summary["head_after"] = collect("head after",lambda:git("rev-parse", "HEAD"))
        summary["git_status_after"] = collect("status after",lambda:git("status", "--porcelain"))
        summary["source_guard"] = collect("source guard",lambda:inputs() == summary["source_inputs"],False)
        summary["binary_guard"] = collect("binary guard",lambda:bool(summary.get("binary_sha256")) and all(digest(REPO / "target/release" / name) == sha for name, sha in summary.get("binary_sha256", {}).items()),False)
        summary["head_guard"] = summary["head_after"] == summary["head"]
        summary["clean_guard"] = summary["git_status_before"] == summary["git_status_after"] == ""
        if not all(summary[key] for key in ["source_guard", "binary_guard", "head_guard", "clean_guard"]): summary["result"] = "FAIL"
        if processes and "guest_artifacts" not in summary:
            summary["guest_artifacts"] = collect("decode remaining guest artifacts",lambda:artifacts(evidence))
        summary["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        manifest = {}
        for path in sorted(evidence.rglob("*")):
            if path.is_file() and path.name != "summary.json":
                row=collect("manifest "+str(path.relative_to(evidence)),lambda path=path:{"bytes":path.stat().st_size,"sha256":digest(path)})
                if row is not None: manifest[str(path.relative_to(evidence))]=row
        summary["artifact_manifest"] = manifest
        if summary.get("collection_errors"): summary["result"] = "FAIL"
        try: (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        except OSError as failure:
            summary["result"]="FAIL"
            summary["summary_persistence_error"]=str(failure)
            fallback=Path(tempfile.mkdtemp(prefix="rustusb-logging-summary-failure-"))/"summary.json"
            fallback.write_text(json.dumps(summary,indent=2)+"\n")
            print(json.dumps({"summary_fallback":str(fallback),"primary_error":summary.get("error")}))
    print(json.dumps({"result": summary["result"], "backend": args.backend, "family": args.family,
                      "evidence": str(evidence), "error": summary.get("error")}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
