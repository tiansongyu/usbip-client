#!/usr/bin/env python3
"""Run an actual ordinary-user device watcher in one disposable Linux QEMU guest.

Only guest root changes the deliberately added QEMU keyboard and its driver.
No host USB passthrough, network NIC, helper, USB/IP service or device writes.
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
sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[2]
OWN = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


native = module("monitor_native_payload", REPO / "scripts/qemu/run-native-qemu.py")
physical = module("monitor_qmp", REPO / "scripts/qemu/run-physical-qemu.py")
review = module("monitor_review", OWN / "verify.py")


def git(*argv):
    return subprocess.check_output(["git", *argv], cwd=REPO, text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs():
    paths = {REPO / name for name in ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "scripts/check.sh", "scripts/qemu/run-native-qemu.py", "scripts/qemu/guest-init.sh", "scripts/qemu/run-physical-qemu.py"]}
    for base in ["crates", "vendor/nusb"]:
        paths.update(path for path in (REPO / base).rglob("*") if path.is_file() and (path.suffix == ".rs" or path.name == "Cargo.toml") and "target" not in path.parts)
    paths.update(OWN / name for name in ["run.py", "verify.py", "guest-init.sh", "point.jq"])
    return {str(path.relative_to(REPO)): {"bytes": path.stat().st_size, "sha256": digest(path)} for path in sorted(paths)}


def command(argv, path):
    with path.open("wb") as output:
        result = subprocess.run(argv, cwd=REPO, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"command failed {result.returncode}: {argv[0]}; original output {path.name}")


def payload(work, kernel, evidence):
    native.initramfs(work, kernel)
    root = work / "root"
    shutil.copyfile(OWN / "guest-init.sh", root / "init")
    (root / "init").chmod(0o755)
    shutil.copyfile(OWN / "point.jq", root / "etc/monitor-point.jq")
    (root / "etc/passwd").write_text("root:x:0:0:root:/:/bin/sh\nmonitor:x:2000:2000:observer:/:/bin/sh\n")
    for name in ["udevadm", "base64"]:
        tool = shutil.which(name)
        if not tool:
            raise RuntimeError(f"required independent guest utility missing: {name}")
        native.copy_binary(tool, f"/usr/bin/{name}", root)
    programs = ["rustusb", "rustusb-helper", "udevadm", "base64", "jq", "setpriv"]
    copied = {}
    for name in programs:
        path = root / "usr/bin" / name
        assert path.is_file() and path.read_bytes()[:4] == b"\x7fELF" and os.access(path, os.X_OK), name
        copied[name] = digest(path)
    applets = ["sh", "awk", "basename", "cat", "cp", "env", "find", "grep", "insmod", "kill", "mkdir", "mount", "poweroff", "printf", "readlink", "seq", "sha256sum", "sleep", "stat", "stty", "sync", "tr", "wc"]
    assert all((root / "bin" / name).is_file() for name in applets), "required guest applet missing"
    fixture = b"monitor-encoder-preflight\x00\xff\n"
    encoded = subprocess.run([str(root / "usr/bin/base64")], input=fixture, capture_output=True, check=True)
    import base64
    assert base64.b64decode(encoded.stdout) == fixture
    (evidence / "payload-preflight.json").write_text(json.dumps({"copied_programs": copied, "required_applets": applets, "encoder_fixture_sha256": hashlib.sha256(fixture).hexdigest(), "scope": "Payload existence/ELF plus actual copied encoder host check, not guest execution or ABI qualification"}, indent=2) + "\n")
    (evidence / "payload-sha256.json").write_text(json.dumps(copied, indent=2) + "\n")
    archive = work / "monitor-initramfs.cpio.gz"
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    result = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
    (evidence / "cpio.stderr").write_bytes(result.stderr)
    archive.write_bytes(gzip.compress(result.stdout))
    return archive, copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--features", choices=["default", "all"], required=True)
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
                "-kernel", str(image), "-initrd", str(archive), "-append", "console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.monitor=local",
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
    print("PASS: actual UID2000 kernel-triggered local inventory, driver changes, reinserted generation and real observer exit")

if __name__ == "__main__":
    main()
