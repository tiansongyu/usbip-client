#!/usr/bin/env python3
"""Unprivileged CLI regression: full stdout must not retain a virtual lease."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import struct
import subprocess
import tempfile
import time


def exact(stream, length):
    data = b""
    while len(data) < length:
        part = stream.recv(length - len(data))
        if not part:
            raise RuntimeError("unexpected EOF during IMPORT")
        data += part
    return data


def run(binary, mode):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(binary), "--json", "virtual-server", "--listen", "127.0.0.1:0", "--control-stdin"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        stream = None
        try:
            fcntl.fcntl(process.stdout.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(5):
                    raise RuntimeError("virtual server did not become ready")
            ready = json.loads(process.stdout.readline())
            host, port = ready["address"].rsplit(":", 1)
            stream = socket.create_connection((host, int(port)), timeout=3)
            stream.sendall(bytes.fromhex("0111800300000000") + b"1-1" + bytes(29))
            if exact(stream, 8) != bytes.fromhex("0111000300000000"):
                raise RuntimeError("virtual lease was not acquired")
            exact(stream, 312)
            # 100 commands fit the stdin pipe; their snapshots exceed the fixed
            # stdout capacity many times. No further stdout drain is performed.
            commands = b"".join(json.dumps({"id": f"bp-{i}", "op": "snapshot"}).encode() + b"\n" for i in range(100))
            if len(commands) > 4096:
                raise RuntimeError("internal test commands exceed the input pipe budget")
            process.stdin.write(commands)
            process.stdin.flush()
            time.sleep(0.2)
            started = time.monotonic()
            if mode == "sigint":
                process.send_signal(signal.SIGINT)
            process.wait(timeout=8)
            elapsed = time.monotonic() - started
            if (mode == "sigint" and (process.returncode != 0 or elapsed > 2)) or (mode == "deadline" and (process.returncode == 0 or not 4 <= elapsed <= 7)):
                raise RuntimeError(f"{mode} did not have bounded expected exit: {process.returncode}, {elapsed:.3f}s")
            stream.settimeout(1)
            if stream.recv(1) != b"":
                raise RuntimeError("virtual data connection survived CLI exit")
            errors.seek(0)
            stderr = errors.read().decode(errors="replace")
            if mode == "deadline" and "server stdout consumer is blocked" not in stderr:
                raise RuntimeError("stdout deadline diagnostic missing")
            return {"mode": mode, "exit": process.returncode, "elapsed_seconds": round(elapsed, 3),
                    "stdout_capacity": 4096, "stdout_drained_after_fill": False, "lease_socket_closed": True}
        finally:
            if stream is not None:
                stream.close()
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("target/release/rustusb"))
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run this lifecycle test as an ordinary user")
    result = {"result": "PASS", "scope": "local CLI output backpressure; no physical USB or kernel changes",
              "host_uid": os.geteuid(), "checks": [run(args.binary.resolve(), mode) for mode in ["sigint", "deadline"]]}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
