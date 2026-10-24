#!/usr/bin/env python3
"""Actual ordinary-user TUI hotplug, checked settings reload and real agent denial.

One disposable guest, no NIC/host USB/helper process/USB/IP data operation.
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
import subprocess
import sys
import tempfile
import time
sys.dont_write_bytecode=True
REPO=Path(__file__).resolve().parents[2]
OWN=Path(__file__).resolve().parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
monitor=module('tui_interactions_monitor',REPO/'scripts/qemu_device_monitor/run.py')
secure=module('tui_interactions_secure',REPO/'scripts/qemu/run-secure-qemu.py')
native,physical=monitor.native,monitor.physical
review=module('tui_interactions_review',OWN/'verify.py')
git,digest,command=monitor.git,monitor.digest,monitor.command

def inputs():
    result=monitor.inputs();result.update(secure.inputs())
    paths=[p for p in OWN.iterdir()if p.is_file()and p.suffix in ['.py','.c','.sh','.md']]
    paths +=[REPO/'scripts/qemu_tui'/n for n in ['decode.py','requirements.txt']]
    for p in paths:result[str(p.relative_to(REPO))]={'bytes':p.stat().st_size,'sha256':digest(p)}
    return dict(sorted(result.items()))

def guest():
    text=(REPO/'scripts/qemu_device_monitor/guest-init.sh').read_text()
    def replace(old,new):
        nonlocal text
        if text.count(old)!=1:raise RuntimeError('unique interaction anchor absent: '+repr(old))
        text=text.replace(old,new,1)
    replace('STAGE=boot\n','STAGE=boot\n'+(OWN/'guest-functions.sh').read_text()+'\n')
    replace('\ncleanup() {\n','\nmonitor_cleanup() {\n')
    replace('trap cleanup EXIT','trap frontend_cleanup EXIT')
    replace('/usr/bin/udevadm monitor','frontend_setup\n/usr/bin/udevadm monitor')
    replace('actor initial\n','actor initial\ntui_launch hotplug\ntui_wait ready\ntui_actor initial\n')
    replace('stamp first /tmp/point-added.json\n','stamp first /tmp/point-added.json\ntui_signal added\ntui_wait added-shown\n')
    replace('[ ! -e "/sys/bus/usb/devices/$BUS" ]\nREV=', '[ ! -e "/sys/bus/usb/devices/$BUS" ]\ntui_signal removed\ntui_wait removed-shown\nREV=')
    replace('actor before-stop\n','actor before-stop\ntui_signal readded\ntui_wait readded-shown\ntui_actor before-stop\ntui_signal finish\ntui_join\n')
    replace('STAGE=complete\n','frontend_remaining\nSTAGE=complete\n')
    return text

def payload(work,kernel,evidence):
    credentials=secure.credentials(work,evidence)
    secure.payload(work,kernel,credentials,'server')
    root=work/'root';(root/'init').write_text(guest());(root/'init').chmod(0o755)
    shutil.copyfile(root/'init',evidence/'generated-init.sh');subprocess.run(['dash','-n',str(root/'init')],check=True)
    shutil.copyfile(REPO/'scripts/qemu_device_monitor/point.jq',root/'etc/monitor-point.jq')
    tool=shutil.which('udevadm')
    if not tool:raise RuntimeError('independent kernel-event utility missing')
    native.copy_binary(Path(tool),'/usr/bin/udevadm',root)
    driver=work/'tui-interaction-pty'
    command(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(OWN/'pty-driver.c'),'-lutil','-o',str(driver)],evidence/'driver-build.log')
    native.copy_binary(driver,'/usr/bin/tui-interaction-pty',root)
    copied={n:digest(root/'usr/bin'/n)for n in ['rustusb','rustusb-helper','udevadm','base64','tui-interaction-pty','jq','setpriv']}
    (evidence/'payload-sha256.json').write_text(json.dumps(copied,indent=2)+'\n')
    (evidence/'payload-preflight.json').write_text(json.dumps(secure.validate_payload(root),indent=2)+'\n')
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode()for p in root.rglob('*'))+b'\0'
    packed=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,capture_output=True,check=True)
    (evidence/'cpio.stderr').write_bytes(packed.stderr)
    archive=work/'tui-interaction-initramfs.cpio.gz';archive.write_bytes(gzip.compress(packed.stdout));return archive,copied

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--features", choices=["tui", "all"], required=True)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() != 0 and os.uname().machine == "x86_64"
    assert re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel) and 60 <= args.timeout <= 900
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"result": "FAIL", "schema_version": 1, "head": git("rev-parse", "HEAD"), "git_status_before": git("status", "--porcelain"),
        "source_inputs": inputs(), "features": args.features, "kernel": args.kernel, "host_uid": os.getuid(), "host_architecture": os.uname().machine,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "scope": __doc__, "forced_termination": False}
    process = None
    qmp = None
    commands = []
    try:
        assert summary["git_status_before"] == "", "clean committed source required"
        for name in summary["source_inputs"]:
            path = evidence / "source" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, path)
        argv = ["cargo", "build", "--workspace", "--release", "--locked"]
        if args.features == "tui":
            argv += ["--features", "rustusb-cli/tui"]
        if args.features == "all":
            argv.append("--all-features")
        summary["build_command"] = argv
        command(argv, evidence / "build.log")
        assert inputs() == summary["source_inputs"]
        summary["binary_sha256"] = {name: digest(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        command(["qemu-system-x86_64", "--version"], evidence / "qemu-version.log")
        command(["rustc", "--version", "--verbose"], evidence / "rustc.log")
        with tempfile.TemporaryDirectory(prefix="rustusb-local-monitor-") as directory:
            work = Path(directory)
            image = work / "vmlinuz"
            try:
                shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            archive, copied = payload(work, args.kernel, evidence)
            assert all(copied[name] == summary["binary_sha256"][name] for name in summary["binary_sha256"])
            summary["kernel_sha256"] = digest(image)
            summary["initramfs_sha256"] = digest(archive)
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            summary["acceleration"] = "kvm" if "-enable-kvm" in acceleration else "tcg"
            for channel in ["kernel", "evidence"]:
                (evidence / f"guest-{channel}.log").touch()
            argv = ["qemu-system-x86_64", "-m", "512", "-smp", "2", *acceleration,
                "-kernel", str(image), "-initrd", str(archive), "-append", "console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.monitor=local rustusb.tui=integration",
                "-display", "none", "-serial", f"file:{evidence/'guest-kernel.log'}", "-serial", f"file:{evidence/'guest-evidence.log'}",
                "-monitor", "none", "-no-reboot", "-nic", "none", "-device", "qemu-xhci,id=usb", "-qmp", f"unix:{work/'qmp.sock'},server=on,wait=off"]
            summary["qemu_command"] = argv
            with (evidence / "qemu.stderr").open("wb") as stderr:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
                summary["actual_qemu_pid"] = process.pid
                deadline = time.monotonic() + args.timeout
                qmp = physical.Qmp(work / "qmp.sock", deadline, process)
                sent = set()
                while time.monotonic() < deadline:
                    log = (evidence / "guest-evidence.log").read_text(errors="replace")
                    if "RUSTUSB_MONITOR_RESULT: FAIL" in log:
                        raise RuntimeError("guest reported actual failure; original channels retained")
                    kernel = (evidence / "guest-kernel.log").read_text(errors="replace")
                    if any(marker in kernel for marker in ["Kernel panic", "Oops:", "BUG:"]):
                        raise RuntimeError("actual guest kernel fault")
                    for stage, cmd, arguments in [
                        ("add", "device_add", {"driver": "usb-kbd", "id": "monitor-kbd", "bus": "usb.0", "port": "1"}),
                        ("remove", "device_del", {"id": "monitor-kbd"}),
                        ("readd", "device_add", {"driver": "usb-kbd", "id": "monitor-kbd", "bus": "usb.0", "port": "1"}),
                        ("final-remove", "device_del", {"id": "monitor-kbd"}),
                    ]:
                        if f"RUSTUSB_MONITOR_REQUEST: {stage}\n" not in log or stage in sent:
                            continue
                        if stage == "readd":
                            until = time.monotonic() + 5
                            while not any(row.get("event") == "DEVICE_DELETED" and row.get("data", {}).get("device") == "monitor-kbd" for row in qmp.events):
                                assert time.monotonic() < until, "actual first QMP device deletion not observed"
                                qmp.read(0.05)
                        response = qmp.call(cmd, arguments)
                        commands.append({"stage": stage, "monotonic": time.monotonic(), "command": cmd, "arguments": arguments, "return": response})
                        sent.add(stage)
                        (evidence / "qmp-commands.json").write_text(json.dumps(commands, indent=2) + "\n")
                    if process.poll() is not None:
                        break
                    if "final-remove" not in sent:
                        qmp.read(0)
                    time.sleep(0.05)
                else:
                    raise TimeoutError("actual owned VM did not finish before the run deadline")
                summary["actual_qemu_wait"] = process.wait(timeout=5)
                assert summary["actual_qemu_wait"] == 0 and len(sent) == 4
                (evidence / "qmp-events.json").write_text(json.dumps(qmp.events, indent=2) + "\n")
                summary["verification"] = review.verify(evidence, summary)
                summary["result"] = "PASS"
    except BaseException as error:
        summary["failure_type"] = type(error).__name__
        summary["failure"] = str(error)
        raise
    finally:
        if qmp:
            qmp.close()
        if process and process.poll() is None:
            summary["forced_termination"] = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            summary["actual_qemu_wait"] = process.returncode
        summary["source_inputs_after"] = inputs()
        summary["head_after"] = git("rev-parse", "HEAD")
        summary["git_status_after"] = git("status", "--porcelain")
        summary["guards"] = {"sources_unchanged": summary["source_inputs_after"] == summary["source_inputs"],
            "git_head_unchanged": summary["head_after"] == summary["head"], "git_status_unchanged": summary["git_status_after"] == summary["git_status_before"],
            "binaries_unchanged": "binary_sha256" in summary and summary["binary_sha256"] == {name: digest(REPO / "target/release" / name) for name in summary["binary_sha256"]}}
        if not all(summary["guards"].values()):
            summary["result"] = "FAIL"
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    assert summary["result"] == "PASS"
    print("PASS: actual TUI hotplug/settings and expected permission failure; original monitor checks retained")

if __name__ == "__main__":
    main()
