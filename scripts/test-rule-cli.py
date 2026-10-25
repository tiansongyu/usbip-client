#!/usr/bin/env python3
"""Disposable metadata-only peer: CLI UUID, unknown reply, and stdout/signal gates.

Requires ordinary Linux user and passwordless sudo solely to create/remove its
unique protected /run parent. Never opens USB, a helper, or service credentials.
The test peer is not evidence of a real mutation commit. Uses existing ELF.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def exact(peer, size):
    result = b""
    while len(result) < size:
        block = peer.recv(size - len(result))
        if not block:
            raise RuntimeError("peer unexpectedly closed")
        result += block
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("ordinary user required")
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=False)
    binary = ROOT / "target/release/rustusb"
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    summary = {"result": "FAIL", "elf_sha256": sha, "cases": [], "scope": "CLI adapter only; no real service commit or USB operation"}
    directory = Path("/run") / ("rustusb-rule-cli-" + uuid.uuid4().hex)
    children = []
    listener = None
    try:
        subprocess.run(["sudo", "-n", "install", "-d", "-m", "755", str(directory)], check=True)
        subprocess.run(["sudo", "-n", "install", "-d", "-m", "711", "-o", str(os.geteuid()), "-g", str(os.getegid()), str(directory / "control")], check=True)
        manifest = {"schema_version": 1, "broker_uid": os.geteuid(), "helper_socket": str(directory / "unused.sock"), "control_directory": str(directory / "control"), "data_directory": str(directory / "unused-data"), "profiles": [{"id": "fixture", "backend": "client", "config": str(directory / "unused-tls.json")}], "operators": []}
        source = out / "manifest.json"
        save(source, manifest)
        subprocess.run(["sudo", "-n", "install", "-m", "644", str(source), str(directory / "manifest.json")], check=True)
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(directory / "control/control.sock"))
        (directory / "control/control.sock").chmod(0o666)
        listener.listen(4)
        listener.settimeout(2)
        prefix = [str(binary), "agent", "--config", str(directory / "manifest.json"), "rule", "fixture"]
        create = ["create", "--name", "fixture", "--trigger", "remote-device-available"]

        def start(tail, stdout=subprocess.PIPE):
            child = subprocess.Popen(prefix + tail, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE)
            children.append(child)
            return child

        def receive():
            peer, _ = listener.accept()
            peer.settimeout(2)
            size = struct.unpack(">I", exact(peer, 4))[0]
            if not 0 < size < 65536:
                raise RuntimeError("invalid frame length")
            value = json.loads(exact(peer, size))
            return peer, value

        def finish(child, name, expected):
            stdout, stderr = child.communicate(timeout=8)
            stdout = stdout or b""
            (out / (name + ".stdout")).write_bytes(stdout)
            (out / (name + ".stderr")).write_bytes(stderr)
            save(out / (name + "-wait.json"), {"pid": child.pid, "actual_exit": child.returncode})
            if child.returncode != expected:
                raise RuntimeError(f"{name} actual exit {child.returncode}, expected {expected}")
            return stdout, stderr

        child = start(create)
        peer, request = receive()
        save(out / "unknown-request.json", request)
        original = request["request"]["request_id"]
        peer.close()  # Lost response is deliberate; no fake commit is recorded.
        stdout, stderr = finish(child, "unknown", 1)
        frames = [json.loads(line) for line in stdout.splitlines()]
        assert len(frames) == 1 and frames[0] == {"schema_version": 1, "event": "rule_mutation_intent", "request_id": original}
        assert original.encode() in stderr and b"response unknown" in stderr
        assert request["request"]["op"] == "mutate_rule"
        summary["cases"].append({"name": "unknown-reply", "request_id": original, "actual_exit": 1})

        child = start(["retry", original])
        peer, request = receive()
        save(out / "retry-request.json", request)
        assert request == {"schema_version": 1, "request": {"op": "retry_rule_mutation", "profile": "fixture", "request_id": original}}
        reply = {"schema_version": 1, "journal_healthy": True, "response": {"result": "rule_mutation_expired", "request_id": original}}
        encoded = json.dumps(reply).encode()
        peer.sendall(struct.pack(">I", len(encoded)) + encoded)
        peer.close()
        stdout, _ = finish(child, "retry", 1)
        frames = [json.loads(line) for line in stdout.splitlines()]
        assert len(frames) == 2 and frames[0]["event"] == "rule_mutation_query" and frames[0]["request_id"] == original and frames[1] == reply
        summary["cases"].append({"name": "query-only-expired", "request_id": original, "actual_exit": 1})

        for name, notification in [("blocked-timeout", None), ("blocked-int", signal.SIGINT), ("blocked-term", signal.SIGTERM)]:
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
            try:
                fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
                prefill = b"p" * 4096
                assert os.write(write_fd, prefill) == len(prefill)
                child = start(create, write_fd)
                os.close(write_fd)
                write_fd = -1
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    # The actual writer thread exists only after signal registration
                    # and successful protected manifest loading.
                    names = [path.read_text().strip() for path in Path(f"/proc/{child.pid}/task").glob("*/comm")]
                    if any(name.startswith("virtual-test-st") for name in names):
                        break
                    time.sleep(0.01)
                else:
                    raise RuntimeError("original stdout owner was not observed")
                assert not select.select([listener], [], [], 0.15)[0], "RPC reached peer before complete intent"
                if notification is not None:
                    child.send_signal(notification)
                _, stderr = finish(child, name, 1 if notification is None else 0)
                assert not select.select([listener], [], [], 0.15)[0], "incomplete intent dispatched RPC"
                assert os.read(read_fd, 4096) == prefill
                tail = b""
                while select.select([read_fd], [], [], 0.1)[0]:
                    block = os.read(read_fd, 65536)
                    if not block:
                        break
                    tail += block
                (out / (name + "-pipe-tail.stdout")).write_bytes(tail)
                if notification is None:
                    assert b"stdout consumer is blocked" in stderr
                # A completed frame racing process exit would still be only intent,
                # never an extra generic JSON error or a service acknowledgement.
                assert not tail or all(json.loads(line)["event"] == "rule_mutation_intent" for line in tail.splitlines())
                summary["cases"].append({"name": name, "no_rpc": True, "actual_exit": child.returncode})
            finally:
                os.close(read_fd)
                if write_fd >= 0:
                    os.close(write_fd)
        summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = f"{type(error).__name__}: {error}"
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                summary.setdefault("forced_test_cleanup", []).append({"pid": child.pid, "actual_exit": child.returncode})
        if listener:
            listener.close()
        if directory.exists():
            subprocess.run(["sudo", "-n", "rm", "-rf", "--", str(directory)], check=True)
        summary["elf_guard"] = hashlib.sha256(binary.read_bytes()).hexdigest() == sha
        if not summary["elf_guard"]:
            summary["result"] = "FAIL"
        save(out / "summary.json", summary)
    print(json.dumps({key: summary[key] for key in ["result", "cases", "elf_guard"]} | {"error": summary.get("error")}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
