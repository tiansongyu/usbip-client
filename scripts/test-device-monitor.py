#!/usr/bin/env python3
"""Exercise actual ordinary-user monitor processes, including real stdout failures.

No device, driver, module, network policy, helper or host sysfs is changed.
Host metadata output remains in a private local directory and is NOT an archive
input. Public observations retain only a closed, payload-free projection.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def actor(process, binary_hash):
    root = Path(f"/proc/{process.pid}")
    status = (root / "status").read_text()
    uid = [int(value) for line in status.splitlines() if line.startswith("Uid:") for value in line.split()[1:]]
    digest = hashlib.sha256((root / "exe").read_bytes()).hexdigest()
    assert uid == [os.getuid()] * 4 and digest == binary_hash, "actual UID/ELF differs"
    actual_fds = []
    socket_inodes = set()
    for path in sorted((root / "fd").iterdir()):
        try:
            target = os.readlink(path)
            if target.startswith("socket:["):
                socket_inodes.add(target[8:-1])
            # Do not retain arbitrary descriptor paths from a host process.
            actual_fds.append({"fd": int(path.name), "kind": "socket" if target.startswith("socket:") else "other"})
        except FileNotFoundError:
            pass
    netlink = []
    for line in Path("/proc/net/netlink").read_text().splitlines()[1:]:
        row = line.split()
        if len(row) >= 10 and row[-1] in socket_inodes:
            netlink.append({"protocol": int(row[1]), "port_id": int(row[2]), "groups": row[3], "drops": int(row[-2]), "inode": row[-1]})
    assert any(row["protocol"] == 15 and int(row["groups"], 16) == 1 for row in netlink), "actual kernel group-1 source not observed"
    stat = (root / "stat").read_text()
    return {"pid": process.pid, "uid": uid, "executable_sha256": digest,
            "start_ticks": int(stat.rsplit(")", 1)[1].split()[19]), "fds": actual_fds, "own_netlink": netlink}


def blocked_write(process):
    observed = []
    for task in Path(f"/proc/{process.pid}/task").iterdir():
        item = {"tid": int(task.name)}
        try:
            item["wchan"] = (task / "wchan").read_text().strip()
            item["syscall"] = (task / "syscall").read_text().strip()
        except (PermissionError, ProcessLookupError, FileNotFoundError) as error:
            item["observation_error"] = type(error).__name__
        fields = item.get("syscall", "").split()
        # x86_64 write=1, aarch64 write=64. wchan independently identifies the pipe.
        try:
            item["stdout_write"] = int(fields[0], 0) in (1, 64) and int(fields[1], 0) == 1
        except (IndexError, ValueError):
            item["stdout_write"] = False
        observed.append(item)
    matches = [row for row in observed if row["stdout_write"] and "pipe_write" in row.get("wchan", "")]
    return matches, observed


def wait_until(process, action, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert process.poll() is None, f"process ended early: {process.returncode}"
        value = action()
        if value:
            return value
        time.sleep(0.01)
    raise TimeoutError("required actual process state not observed")


def current_batch(process):
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    raw = bytearray()
    pending = bytearray()
    begin = None
    records = []
    changes = []
    projection = []
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline:
            for key, _ in selector.select(timeout=0.2):
                chunk = os.read(key.fd, 65536)
                assert chunk, "stdout ended before successful inventory"
                raw.extend(chunk)
                assert len(raw) <= 8 * 1024 * 1024
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    assert len(line) < 65536
                    frame = json.loads(line)
                    assert frame["schema_version"] == 1
                    kind = frame["event"]
                    if kind == "snapshot_begin":
                        assert begin is None
                        begin = frame
                        records, changes = [], []
                        projection.append(frame)
                    elif kind == "device":
                        assert begin and frame["revision"] == begin["revision"] and frame["instance"] == begin["instance"]
                        observation = frame["observation"]
                        assert isinstance(observation["device"]["id"], str) and observation["device"]["id"].startswith("usb-")
                        assert set(observation["stamp"]) == {"sysfs_device", "sysfs_inode", "bus_number", "device_number"}
                        records.append(frame)
                    elif kind == "device_change":
                        assert begin and frame["revision"] == begin["revision"] and frame["instance"] == begin["instance"]
                        changes.append(frame)
                    elif kind == "snapshot_end":
                        assert begin and frame["revision"] == begin["revision"] and frame["instance"] == begin["instance"]
                        assert frame["device_count"] == begin["device_count"] and frame["change_count"] == begin["change_count"]
                        assert len(changes) == begin["change_count"]
                        if begin["device_count"] is None:
                            assert not records and begin["state"] in ("initializing", "stale")
                        else:
                            assert len(records) == begin["device_count"]
                        projection.append(frame)
                        if begin["state"] == "current":
                            assert begin["device_count"] is not None and begin["failure"] is None
                            return bytes(raw), projection, len(records), len(changes)
                        begin = None
                    else:
                        raise AssertionError("unexpected initial monitor event")
    finally:
        selector.close()
    raise TimeoutError("no complete current snapshot before original deadline")


def case(binary, expected, root, name):
    out = root / name
    out.mkdir()
    private = out / ".private"
    private.mkdir(mode=0o700)
    summary = {"case": name, "result": "FAIL", "host_uid": os.getuid(), "mutations": False}
    process = None
    read_fd = None
    stderr_file = (private / "stderr").open("wb")
    try:
        if name == "interrupt":
            process = subprocess.Popen([str(binary), "devices", "--watch", "--json"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=stderr_file, env={**os.environ, "RUST_LOG": "error"})
            raw, projection, count, change_count = current_batch(process)
            (private / "initial-stdout").write_bytes(raw)
            save(out / "batch-projection.json", {"frames_without_device_or_change_payloads": projection,
                "validated_device_records": count, "validated_change_records": change_count,
                "private_raw_bytes": len(raw), "scope": "Host device bodies deliberately omitted, not reconstructed."})
            save(out / "actor.json", actor(process, expected))
            process.send_signal(signal.SIGINT)
            more, _ = process.communicate(timeout=8)
            (private / "remaining-stdout").write_bytes(more)
            assert process.returncode == 0, f"actual child wait {process.returncode}"
            summary["signal"] = "SIGINT"
            summary["complete_current_inventory"] = True
            summary["stopped_receipt_observed"] = any(json.loads(line).get("event") == "stopped" for line in more.splitlines() if line.strip() and line.startswith(b"{"))
        else:
            read_fd, write_fd = os.pipe()
            size = fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
            assert size == 4096
            assert os.write(write_fd, b"P" * size) == size
            try:
                process = subprocess.Popen([str(binary), "devices", "--watch", "--json"], stdin=subprocess.DEVNULL,
                                           stdout=write_fd, stderr=stderr_file, env={**os.environ, "RUST_LOG": "error"})
            finally:
                os.close(write_fd)
            def observe_blocked():
                match, observed = blocked_write(process)
                return (match, observed) if match else None
            match, observed = wait_until(process, observe_blocked)
            assert match
            save(out / "blocked-write.json", {"matching_actual_stdout_writers": match, "tasks": observed, "pipe_capacity": size, "prefilled": size})
            save(out / "actor.json", actor(process, expected))
            if name == "backpressure-term":
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=8)
                assert process.returncode == 0, f"actual child wait {process.returncode}"
                received = bytearray()
                while chunk := os.read(read_fd, 65536):
                    received.extend(chunk)
                assert bytes(received) == b"P" * size, "blocked in-flight output was unexpectedly completed or rewritten"
                summary["signal"] = "SIGTERM"
                summary["reader_observed_eof"] = True
                summary["preserved_prefill_bytes"] = len(received)
            else:
                process.send_signal(signal.SIGSTOP)
                def stopped():
                    status = Path(f"/proc/{process.pid}/status").read_text()
                    return status if any(line.startswith("State:") and "T" in line for line in status.splitlines()) else None
                held = wait_until(process, stopped)
                (out / "actual-stopped-status.txt").write_text(held)
                os.close(read_fd)
                read_fd = None
                process.send_signal(signal.SIGCONT)
                process.wait(timeout=8)
                assert process.returncode == 1, f"actual child wait {process.returncode}"
                summary["reader_closed_while_child_stopped"] = True
                summary["continued_actual_child"] = True
        summary["actual_wait_code"] = process.returncode
        summary["proc_absent"] = not Path(f"/proc/{process.pid}").exists()
        assert summary["proc_absent"]
        summary["result"] = "PASS"
    except BaseException as error:
        summary["failure_type"] = type(error).__name__
        summary["failure"] = str(error)
        raise
    finally:
        if process is not None and process.poll() is None:
            summary["forced_cleanup"] = True
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            summary["cleanup_wait_code"] = process.returncode
        if read_fd is not None:
            os.close(read_fd)
        if process is not None and process.poll() is not None:
            summary["actual_wait_code"] = process.returncode
            summary["proc_absent"] = not Path(f"/proc/{process.pid}").exists()
        stderr_file.close()
        stderr = (private / "stderr").read_bytes()
        summary["private_stderr_bytes"] = len(stderr)
        # Expected CLI errors are fixed; do not publish arbitrary host metadata.
        summary["broken_pipe_reported"] = b"Broken pipe" in stderr or b"broken pipe" in stderr
        save(out / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() != 0, "run as an ordinary user"
    binary = args.binary.resolve(strict=True)
    expected = hashlib.sha256(binary.read_bytes()).hexdigest()
    args.evidence.mkdir(parents=True, exist_ok=False, mode=0o700)
    save(args.evidence / "selection.json", {"argv_binary": str(binary), "sha256": expected, "host_uid": os.getuid(), "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    results = [case(binary, expected, args.evidence, name) for name in ["interrupt", "backpressure-term", "closed-output"]]
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == expected
    save(args.evidence / "summary.json", {"result": "PASS", "cases": results, "count": len(results), "binary_sha256": expected,
        "scope": "Actual ordinary-user CLI/UID/ELF, real pipe blocking/closure and actual wait. No host USB mutation or hotplug injection. Raw host metadata remains private and is excluded from archive-files.json."})
    files = []
    for path in sorted(args.evidence.rglob("*")):
        if path.is_file() and ".private" not in path.parts:
            data = path.read_bytes()
            files.append({"path": str(path.resolve()), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    save(args.evidence / "archive-files.json", {"files": files, "excluded": "All .private directories contain unredacted local host command output; never publish or recursively archive."})
    print("PASS: three actual local device-monitor process cases; host metadata excluded from archive inputs")

if __name__ == "__main__":
    main()
