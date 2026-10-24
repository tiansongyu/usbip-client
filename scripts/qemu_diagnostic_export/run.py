#!/usr/bin/env python3
"""Installed Native IPv4 diagnostic export during a real withheld WAL fsync entry.

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
logging_support=module("diagnostic_logging_support",REPO/"scripts/qemu_process_logging/run.py")
verification=module("diagnostic_export_verification",HERE/"verify.py")

def inputs():
    result=logging_support.inputs()
    gate=REPO/"scripts/qemu_prepare_recording_shutdown/prepare-gate.c"
    result[str(gate.relative_to(REPO))]={"sha256":digest(gate),"bytes":gate.stat().st_size}
    for path in sorted(HERE.iterdir()):
        if path.is_file() and path.suffix in [".py",".sh",".md"]:
            result[str(path.relative_to(REPO))]={"sha256":digest(path),"bytes":path.stat().st_size}
    return dict(sorted(result.items()))

once,replace_once=logging_support.once,logging_support.replace_once

def diagnostic_gate_source():
    # Reuse the actual preparation gate; adapt only its fixed launch allowlist.
    # The original file and every ptrace/timeout/FD/cleanup byte stay unchanged.
    original=(REPO/"scripts/qemu_prepare_recording_shutdown/prepare-gate.c").read_bytes().decode("utf-8")
    old='''        strcmp(argv[11], "agent") != 0 || strcmp(argv[12], "--config") != 0 ||
        strcmp(argv[13], "/etc/rustusb/agent.json") != 0 || strcmp(argv[14], "run") != 0) {'''
    new='''        strcmp(argv[11], "--log-directory") != 0 ||
        strcmp(argv[12], "/var/log/rustusb/agent") != 0 ||
        strcmp(argv[13], "--log-max-bytes") != 0 || strcmp(argv[14], "262144") != 0 ||
        strcmp(argv[15], "--log-retain") != 0 || strcmp(argv[16], "4") != 0 ||
        strcmp(argv[17], "agent") != 0 || strcmp(argv[18], "--config") != 0 ||
        strcmp(argv[19], "/etc/rustusb/agent.json") != 0 || strcmp(argv[20], "run") != 0) {'''
    generated=replace_once(replace_once(original,'if (argc != 15 ||','if (argc != 21 ||'),old,new)
    restored=replace_once(replace_once(generated,'if (argc != 21 ||','if (argc != 15 ||'),new,old)
    if restored!=original: raise RuntimeError("diagnostic gate changed outside its fixed argv template")
    provenance={"original_path":"scripts/qemu_prepare_recording_shutdown/prepare-gate.c",
                "original_sha256":hashlib.sha256(original.encode()).hexdigest(),
                "generated_sha256":hashlib.sha256(generated.encode()).hexdigest(),
                "reverse_substitution_exact":True,"changed_scope":"fixed argc15 to21 and six exact logging arguments only"}
    return generated,provenance

def guest_source():
    # Reuse audited boot/helper/config/real-I/O collectors; no old test verdict.
    prefix=once(logging_support.guest_source(),"# This always releases our removable test lock before the base failure collector.\n")[0]
    prefix=prefix.replace("RUSTUSB_DIAGNOSTIC_RESULT", "RUSTUSB_DIAGNOSTIC_RESULT")
    prefix=prefix.replace("--log-max-bytes 4096", "--log-max-bytes 262144")
    prefix=replace_once(prefix,"GATE_PID='' GATE_NONCE='' AGENT_PID='' HELPER_PID=''", "AGENT_PID='' HELPER_PID=''")
    original=(REPO/"scripts/qemu/guest-agent-init.sh").read_text()
    server='    STAGE=server-devices\n'+once(once(original,'if [ "$ROLE" = server ]; then\n    STAGE=server-devices\n')[1],"    manifest; agent_start 1; denied server\n")[0]
    client='    STAGE=client-prepare\n'+once(once(original,'else\n    STAGE=client-prepare\n')[1],"    manifest; agent_start 1; denied hid\n")[0]
    body=(HERE/"guest-init.sh").read_text()
    for token,setup in [("@@SERVER_SETUP@@",server),("@@CLIENT_SETUP@@",client)]:
        setup=replace_once(setup,'{schema_version:1,broker_uid:2000,','{schema_version:2,data_link:{protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,')
        body=replace_once(body,token,setup)
    return prefix+'\nDIAGNOSTIC_COMMON=1\n'+body

def payload(work,kernel,certificates,role):
    _,provenance=secure.payload(work,kernel,certificates,role)
    root=work/"root"
    (root/"etc/rustusb/client-pin.txt").chmod(0o644)
    native.copy_binary(Path(shutil.which("flock") or "/usr/bin/flock"),"/usr/bin/flock",root)
    if role=="server":
        gate=work/"prepare-gate"
        source,gate_provenance=diagnostic_gate_source()
        gate_source=work/"generated-diagnostic-gate.c"
        gate_source.write_text(source)
        (work/"gate-source-provenance.json").write_text(json.dumps(gate_provenance,indent=2)+"\n")
        command(["cc","-std=c11","-O2","-Wall","-Wextra","-Werror",str(gate_source),"-o",str(gate)],work/"gate-build.log")
        native.copy_binary(gate,"/usr/bin/prepare-gate",root)
        provenance["usr/bin/prepare-gate"]=digest(gate)
    (root/"init").write_text(guest_source());(root/"init").chmod(0o755)
    shutil.copyfile(root/"init",work/"generated-diagnostic-init.sh")
    subprocess.run(["dash","-n",str(root/"init")],check=True)
    for name in ["init","usr/bin/flock"]: provenance[name]=digest(root/name)
    dependencies=secure.validate_payload(root)
    (work/"payload-dependencies.json").write_text(json.dumps(dependencies,indent=2)+"\n")
    names=b".\0"+b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*"))+b"\0"
    packed=subprocess.run(["cpio","--null","-o","--format=newc","--owner=0:0"],input=names,cwd=root,capture_output=True,check=True)
    archive=work/"diagnostic-initramfs.cpio.gz";archive.write_bytes(gzip.compress(packed.stdout))
    return archive,provenance

def check_final_guest_logs(evidence):
    # Polling is not a terminal observation: either guest can emit its last
    # marker/artifact error between a poll and the actual process wait.
    for role in ("server","client"):
        log=(evidence/f"{role}-evidence.log").read_text(errors="strict")
        expected=f"RUSTUSB_DIAGNOSTIC_RESULT: PASS role={role}"
        pass_lines=[line for line in log.splitlines() if line.startswith("RUSTUSB_DIAGNOSTIC_RESULT: PASS")]
        if pass_lines!=[expected]:
            raise RuntimeError(role+" final unique exact guest PASS marker missing")
        for forbidden in ("RUSTUSB_DIAGNOSTIC_RESULT: FAIL","SECURE_ARTIFACT_ERROR","OVERSIZE","POWEROFF_RETURNED"):
            if forbidden in log:
                raise RuntimeError(role+" final guest failure/artifact marker: "+forbidden)


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
               "scope": "two disposable Native IPv4/framed-v2 guests; same live capabilities before/after complete private diagnostic export during a removable state.next fsync-entry gate; no permanent kernel blocking or hardware acceptance",
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
        command(["cc", "--version"], evidence / "tool-versions.log")
        with tempfile.TemporaryDirectory(prefix="rustusb-diagnostic-export-") as temporary:
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
                shutil.copyfile(role_work / "generated-diagnostic-init.sh", evidence / f"{role}-generated-diagnostic-init.sh")
                shutil.copyfile(role_work / "payload-dependencies.json", evidence / f"{role}-payload-dependencies.json")
                summary["initramfs_sha256"][role] = digest(archives[role])
                summary["credential_payloads"][role] = sorted(path.name for path in (role_work / "root/etc/rustusb").iterdir())
                if role == "server":
                    for name in ["gate-build.log","generated-diagnostic-gate.c","gate-source-provenance.json"]:
                        shutil.copyfile(role_work/name,evidence/name)
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
                       "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.secure=1 rustusb.agent=1 rustusb.diagnostic_export=1 rustusb.prepare_stop=1 rustusb.role={role} rustusb.backend={args.backend} rustusb.family={args.family}",
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
                markers = [{"role": role, "line": line} for role, log in logs.items() for line in log.splitlines() if "RUSTUSB_DIAGNOSTIC_RESULT: FAIL" in line]
                if markers:
                    summary.setdefault("observed_failure_markers", markers)
                    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
                    raise RuntimeError("guest reported actual failure")
                for role in processes:
                    kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
                    if "Kernel panic" in kernel or "Oops:" in kernel: raise RuntimeError(f"{role} kernel fault")
                if all(f"DIAG_NETWORK_READY role={role}\n" in logs[role] for role in processes):
                    for role in processes: send_once("network-"+role, role, "NETWORK_READY")
                keys = re.search(r"^AGENT_KEYS (\[.*\])$", logs["server"], re.M)
                if keys: send_once("keys", "client", keys[1])
                while select.select([ftdi], [], [], 0)[0]:
                    chunk = ftdi.recv(4096)
                    if not chunk: break
                    tx.extend(chunk)
                    (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                    if len(tx) > 70: raise RuntimeError("unexpected extra FTDI output")
                if "DIAG_SERVER_READY\n" in logs["server"] and "keys" in sent:
                    send_once("start1","client","START1")
                for round_id in [1,2]:
                    if f"AGENT_CLIENT_WAIT_INPUT round={round_id} " in logs["client"] and f"input-{round_id}" not in sent:
                        arguments={"keys":[{"type":"qcode","data":str(round_id)}],"hold-time":100}
                        response=qmp.call("send-key",arguments)
                        with (evidence/"qmp-input.jsonl").open("a") as log:
                            log.write(json.dumps({"round":round_id,"command":"send-key","arguments":arguments,"return":response})+"\n")
                        fixture=f"RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n".encode()
                        ftdi.settimeout(2);ftdi.sendall(fixture);ftdi.setblocking(False)
                        (evidence/f"ftdi-host-injected-{round_id}.bin").write_bytes(fixture)
                        sent.add(f"input-{round_id}")
                if "AGENT_CLIENT_IO round=1 " in logs["client"] and len(tx)>=35:
                    send_once("observe1","server","OBSERVE1")
                if "DIAG_EXPORTED_GATE_HELD\n" in logs["server"]:
                    send_once("io2","client","IO2")
                if "AGENT_CLIENT_IO round=2 " in logs["client"] and len(tx)>=70:
                    send_once("io2-complete","server","IO2_COMPLETE")
                if "DIAG_GATE_RELEASED\n" in logs["server"]:
                    send_once("close1","client","CLOSE1")
                if "LOG_CLIENT_STOPPED epoch=1\n" in logs["client"]:
                    send_once("stop1","server","STOP1")
                if "DIAG_SERVER_STOPPED\n" in logs["server"]:
                    for role in processes: send_once("final-"+role,role,"FINAL")
                if all(process.poll() is not None for process in processes.values()): break
                if any(process.poll() is not None and f"RUSTUSB_DIAGNOSTIC_RESULT: PASS role={role}" not in logs[role] for role, process in processes.items()):
                    raise RuntimeError("guest exited without terminal PASS")
                time.sleep(0.05)
            else: raise RuntimeError("two-guest agent run deadline exceeded")
            if any(process.wait() != 0 for process in processes.values()): raise RuntimeError("nonzero actual QEMU exit")
            check_final_guest_logs(evidence)
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
                    if path.name == "executable-gate.txt":
                        if actual != summary["payload_sha256"][role]["usr/bin/prepare-gate"]: raise RuntimeError("actual tracer ELF mismatch")
                        continue
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
        # window. Let the 30s removable gate / 120s tracer watchdog and cleanup finish.
        if summary["result"] != "PASS" and connections:
            for role, connection in connections.items():
                if processes[role].poll() is None:
                    collect(role+" abort control",lambda connection=connection:connection.sendall(b"ABORT\n"))
            capture_deadline = time.monotonic() + 150
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
