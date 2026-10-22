#!/usr/bin/env python3
"""Own-process stdout backpressure/SIGINT reproduction; no USB operations.

Run: python3 /tmp/rustusb-reproduce-virtual-stdout-backpressure.py BINARY OUTPUT
The pre-fix observation is separately preserved in
/tmp/rustusb-virtual-stdout-backpressure-20261022.json.
"""
import datetime
import hashlib
import json
import pathlib
import signal
import subprocess
import sys
import threading
import time

binary = pathlib.Path(sys.argv[1]).resolve()
destination = pathlib.Path(sys.argv[2])
command = [str(binary), "virtual-server", "--listen", "127.0.0.1:0", "--control-stdin"]
report = {
    "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "binary": str(binary),
    "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
    "scope": "ordinary-user localhost virtual-server subprocess only; no USB or module operations",
    "command": command,
    "steps": [
        "Read only the initial ready line.",
        "Write 100 bounded snapshot JSONL commands, leave stdin open, and stop reading stdout.",
        "Send SIGINT after 300 ms; wait 2 seconds without draining stdout.",
        "Resume stdout draining in a reader thread, keep stdin open, and wait up to 3 seconds.",
    ],
}
process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
drained = bytearray()
try:
    report["ready_bytes"] = len(process.stdout.readline())
    controls = b"".join(json.dumps({"id": "snapshot-" + str(index), "op": "snapshot"}).encode() + b"\n" for index in range(100))
    process.stdin.write(controls)
    process.stdin.flush()
    time.sleep(0.3)
    process.send_signal(signal.SIGINT)
    try:
        report["exit_before_drain"] = process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        report["blocked_after_sigint_seconds"] = 2
    def drain():
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            drained.extend(chunk)
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        report["exit_after_drain"] = process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        report["still_running_after_drain_seconds"] = 3
        process.kill()
        process.wait(timeout=2)
    reader.join(timeout=2)
    report["drained_stdout_bytes"] = len(drained)
    report["stderr"] = process.stderr.read().decode("utf-8", errors="replace")
finally:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=2)
    process.stdin.close()
    process.stdout.close()
    process.stderr.close()
destination.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
