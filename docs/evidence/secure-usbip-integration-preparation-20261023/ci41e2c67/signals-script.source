#!/usr/bin/env python3
"""Ordinary-user virtual-server SIGINT/SIGTERM lifecycle evidence (Linux).

Default: locked all-feature release build, then ready/blocked stdout x INT/TERM.
Only this script's child receives signals. No physical USB, module, ptrace, or
network configuration is used. A blocked-output PASS proves bounded exit and
data-socket closure; only a received stopped snapshot proves backend resources.
Every run needs a fresh evidence directory; failures are retained, never retried.
"""

import argparse
import array
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
MAX_LINE = 65536
WIRE_SECONDS = 3.0
CONTROL_SECONDS = 3.0
EXIT_SECONDS = 8.0
PIPE_SIZE = 4096
PROFILES = {"1-1": ("hid_keyboard", 1, 1, 8), "1-2": ("cdc_acm", 2, 2, 64)}
BUILD = ["cargo", "build", "--workspace", "--release", "--locked", "--all-features"]


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(131072):
            value.update(block)
    return value.hexdigest()


def source_state():
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=10).stdout

    names = git("ls-files", "-co", "--exclude-standard", "-z").split(b"\0")
    paths = set()
    for encoded in names:
        if not encoded:
            continue
        name = os.fsdecode(encoded)
        path = Path(name)
        if (name == "Cargo.lock" or path.suffix in {".rs", ".toml"}
                or name.startswith("vendor/")
                or name == "scripts/test-server-signals.py"):
            if (ROOT / path).is_file():
                paths.add(name)
    fingerprint = hashlib.sha256()
    files = []
    for name in sorted(paths):
        path = ROOT / name
        size = path.stat().st_size
        sha = digest(path)
        fingerprint.update(os.fsencode(name) + b"\0" + str(size).encode() + b"\0"
                           + sha.encode() + b"\n")
        files.append({"path": name, "length": size, "sha256": sha})
    status = git("status", "--porcelain=v1").decode()
    return {"head": git("rev-parse", "HEAD").decode().strip(),
            "git_status": status, "dirty": bool(status), "files": files,
            "source_sha256": fingerprint.hexdigest()}


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("phase deadline expired")
    return seconds


def exact(stream, length, deadline):
    result = bytearray()
    while len(result) < length:
        stream.settimeout(remaining(deadline))
        block = stream.recv(length - len(result))
        if not block:
            raise RuntimeError("unexpected protocol EOF")
        result.extend(block)
    return bytes(result)


def send(stream, data, deadline):
    stream.settimeout(remaining(deadline))
    stream.sendall(data)


def write_control(fd, value):
    data = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    if len(data) > 4096:
        raise RuntimeError("control exceeds atomic pipe limit")
    # Only a few small controls are sent, and stdin stays open until child exit.
    if not select.select([], [fd], [], CONTROL_SECONDS)[1]:
        raise TimeoutError("control input was not writable")
    if os.write(fd, data) != len(data):
        raise RuntimeError("short control pipe write")


class Output:
    """Bounded JSON reader; all consumed bytes are retained verbatim."""

    def __init__(self, fd, artifact):
        self.fd = fd
        self.raw = artifact.open("wb")
        self.buffer = bytearray()
        self.events = []
        self.eof = False
        self.frozen = False
        self.total = 0

    def receive(self, deadline):
        if self.frozen:
            raise RuntimeError("stdout must never be drained after filling")
        if not select.select([self.fd], [], [], remaining(deadline))[0]:
            raise TimeoutError("server stdout deadline expired")
        data = os.read(self.fd, MAX_LINE)
        self.raw.write(data)
        self.raw.flush()
        self.total += len(data)
        if self.total > 1024 * 1024:
            raise RuntimeError("server stdout exceeds evidence limit")
        self.eof = not data
        self.buffer.extend(data)

    def line(self, deadline):
        while b"\n" not in self.buffer:
            if len(self.buffer) > MAX_LINE or self.eof:
                raise RuntimeError("missing or oversized JSON event")
            self.receive(deadline)
        line, _, rest = self.buffer.partition(b"\n")
        if len(line) > MAX_LINE:
            raise RuntimeError("oversized JSON event")
        self.buffer = bytearray(rest)
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise RuntimeError("unexpected event schema")
        self.events.append(value)
        return value

    def finish(self, deadline):
        while not self.eof:
            self.receive(deadline)
        while self.buffer:
            if b"\n" not in self.buffer:
                raise RuntimeError("truncated final JSON event")
            self.line(deadline)


def proc_identity(pid):
    text = Path(f"/proc/{pid}/stat").read_text()
    # comm may contain spaces or parentheses; fields after its last ')' start at 3.
    fields = text[text.rfind(")") + 2:].split()
    status = Path(f"/proc/{pid}/status").read_text().splitlines()
    uid = next(line.split()[1:] for line in status if line.startswith("Uid:"))
    return {"pid": pid, "starttime": int(fields[19]), "uids": [int(x) for x in uid]}


def own_signal(process, identity, number):
    if process.poll() is not None:
        raise RuntimeError("child exited before requested signal")
    current = proc_identity(process.pid)
    if current != identity or current["uids"] != [os.getuid()] * 4:
        raise RuntimeError("owned child identity changed; refusing signal")
    process.send_signal(number)
    return {"signal": number, "pid": process.pid, "identity": current,
            "monotonic_ns": time.monotonic_ns()}


def pipe_bytes(fd):
    value = array.array("i", [0])
    fcntl.ioctl(fd, termios.FIONREAD, value, True)
    return value[0]


def stdout_flags(pid):
    lines = Path(f"/proc/{pid}/fdinfo/1").read_text().splitlines()
    return int(next(line.split()[1] for line in lines if line.startswith("flags:")), 8)


def wait_writer(process, identity, deadline):
    observations = []
    while True:
        if process.poll() is not None or proc_identity(process.pid) != identity:
            raise RuntimeError("child exited while observing blocked writer")
        for task in sorted(Path(f"/proc/{process.pid}/task").iterdir()):
            try:
                name = (task / "comm").read_text().strip()
                if not name.startswith("virtual-test-"):
                    continue
                wait = (task / "wchan").read_text().strip()
            except FileNotFoundError:
                continue
            observation = {"tid": int(task.name), "comm": name, "wchan": wait,
                           "monotonic_ns": time.monotonic_ns()}
            if wait.endswith("pipe_write"):
                return observation
            if len(observations) < 20:
                observations.append(observation)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"writer did not enter pipe_write: {observations!r}")
        time.sleep(min(0.01, remaining(deadline)))


def import_device(address, bus_id, protocol):
    _, number, _, _ = PROFILES[bus_id]
    stream = socket.create_connection(address, timeout=WIRE_SECONDS)
    try:
        deadline = time.monotonic() + WIRE_SECONDS
        request = bytes.fromhex("0111800300000000") + bus_id.encode().ljust(32, b"\0")
        send(stream, request, deadline)
        header = exact(stream, 8, deadline)
        if header != bytes.fromhex("0111000300000000"):
            raise RuntimeError(f"IMPORT rejected {bus_id}: {header.hex()}")
        device = exact(stream, 312, deadline)
        bus_number, device_number, speed = struct.unpack_from("!III", device, 288)
        vid, pid = struct.unpack_from("!HH", device, 300)
        if (device[256:288].split(b"\0", 1)[0] != bus_id.encode()
                or (bus_number, device_number, speed, vid, pid)
                != (1, number, 2, 0x1234, number)):
            raise RuntimeError("IMPORT metadata differs from fixed virtual profile")
        protocol.append({"operation": "IMPORT", "bus_id": bus_id,
                         "request_hex": request.hex(), "header_hex": header.hex(),
                         "device_hex": device.hex(), "devid": (1 << 16) | number})
        return stream
    except BaseException:
        stream.close()
        raise


def submit(stream, bus_id, sequence, direction, endpoint, length, setup, protocol):
    number = PROFILES[bus_id][1]
    frame = struct.pack("!7I3i", 1, sequence, (1 << 16) | number,
                        direction, endpoint, 0, length, 0, 0, 0) + setup
    send(stream, frame, time.monotonic() + WIRE_SECONDS)
    protocol.append({"operation": "SUBMIT", "bus_id": bus_id,
                     "sequence": sequence, "frame_hex": frame.hex()})


def configure(stream, bus_id, protocol):
    deadline = time.monotonic() + WIRE_SECONDS
    submit(stream, bus_id, 1, 0, 0, 0, bytes([0, 9, 1, 0, 0, 0, 0, 0]), protocol)
    reply = exact(stream, 48, deadline)
    command, sequence, devid, direction, endpoint = struct.unpack_from("!5I", reply)
    status, actual = struct.unpack_from("!iI", reply, 20)
    if (command, sequence, devid, direction, endpoint, status, actual) != (3, 1, 0, 0, 0, 0, 0):
        raise RuntimeError(f"SET_CONFIGURATION failed: {reply.hex()}")
    protocol.append({"operation": "RET_SUBMIT", "bus_id": bus_id,
                     "sequence": 1, "status": status, "actual_length": actual,
                     "frame_hex": reply.hex()})


def devices(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise RuntimeError("snapshot schema is not 1")
    values = snapshot.get("devices")
    if not isinstance(values, list) or len(values) != 2:
        raise RuntimeError("snapshot does not contain exactly two devices")
    found = {value["bus_id"]: value for value in values}
    if set(found) != set(PROFILES):
        raise RuntimeError("snapshot bus IDs differ from fixed profiles")
    for bus_id, value in found.items():
        if value.get("profile") != PROFILES[bus_id][0]:
            raise RuntimeError("snapshot profile differs from fixed bus ID")
    return found


def all_pending(snapshot):
    return all(value.get("leased") is True and value.get("pending_urbs") == 1
               and value.get("configuration") == 1
               and value.get("hid_queued_reports") == 0
               and value.get("cdc_queued_bytes") == 0
               for value in devices(snapshot).values())


def all_released(snapshot):
    return all(value.get("leased") is False and value.get("pending_urbs") == 0
               and value.get("configuration") == 0
               and value.get("hid_queued_reports") == 0
               and value.get("cdc_queued_bytes") == 0
               for value in devices(snapshot).values())


def run_case(binary, mode, signal_name, directory):
    directory.mkdir()
    result = {"mode": mode, "signal": signal_name, "result": "FAIL",
              "assertions": [], "before": None, "after": None,
              "stdout_drained_after_fill": False, "forced_cleanup": False}
    protocol = []
    streams = {}
    process = None
    output = None
    identity = None
    descriptors = []

    def check(name, passed, detail=None, fatal=True):
        result["assertions"].append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed and fatal:
            raise RuntimeError(name)

    try:
        read_fd, write_fd = os.pipe()
        descriptors.extend([read_fd, write_fd])
        capacity = fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, PIPE_SIZE)
        check("stdout pipe capacity is exactly 4096", capacity == PIPE_SIZE, capacity)
        env = dict(os.environ, RUST_LOG="warn", RUST_BACKTRACE="0", LC_ALL="C")
        argv = [str(binary), "--json", "virtual-server", "--listen", "127.0.0.1:0", "--control-stdin"]
        result["argv"] = argv
        with (directory / "stderr.log").open("wb") as errors:
            process = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.PIPE,
                                       stdout=write_fd, stderr=errors, env=env,
                                       close_fds=True, bufsize=0)
            identity = proc_identity(process.pid)
            result["child"] = identity
            check("child has ordinary caller UID", identity["uids"] == [os.getuid()] * 4)
            executable = Path(f"/proc/{process.pid}/exe")
            actual_binary = executable.stat()
            selected_binary = binary.stat()
            result["executed_binary"] = {
                "proc_exe": str(executable), "resolved": str(executable.resolve()),
                "device": actual_binary.st_dev, "inode": actual_binary.st_ino,
                "sha256": digest(executable)}
            check("owned child executes the selected binary inode and bytes",
                  (actual_binary.st_dev, actual_binary.st_ino)
                  == (selected_binary.st_dev, selected_binary.st_ino)
                  and result["executed_binary"]["sha256"] == digest(binary))
            output = Output(read_fd, directory / "stdout-consumed.bin")
            ready = output.line(time.monotonic() + 5)
            check("first event is ready", ready.get("event") == "ready", ready)
            host, port = ready["address"].rsplit(":", 1)
            check("listener is IPv4 loopback", ipaddress.ip_address(host) == ipaddress.ip_address("127.0.0.1"))
            address = (host, int(port))
            for bus_id, (_, _, endpoint, length) in PROFILES.items():
                streams[bus_id] = import_device(address, bus_id, protocol)
                configure(streams[bus_id], bus_id, protocol)
                submit(streams[bus_id], bus_id, 2, 1, endpoint, length, bytes(8), protocol)
            deadline = time.monotonic() + CONTROL_SECONDS
            attempt = 0
            while True:
                attempt += 1
                request_id = f"before-{attempt}"
                write_control(process.stdin.fileno(), {"id": request_id, "op": "snapshot"})
                event = output.line(deadline)
                check("snapshot response matches request", event.get("event") == "snapshot" and event.get("id") == request_id)
                if all_pending(event["snapshot"]):
                    break
                remaining(deadline)
                if attempt >= 100:
                    raise RuntimeError("pending snapshot did not converge within 100 observations")
                time.sleep(min(0.01, remaining(deadline)))
            result["before"] = event
            save_json(directory / "before.json", event)
            check("both configured profiles have one pending IN", all_pending(event["snapshot"]))
            check("pending data sockets have no completion bytes", not select.select(list(streams.values()), [], [], 0)[0])
            pipe_proof = {"capacity": capacity, "bytes_before": pipe_bytes(read_fd),
                          "original_writer_flags_before": fcntl.fcntl(write_fd, fcntl.F_GETFL),
                          "child_stdout_flags_before": stdout_flags(process.pid)}
            result["pipe"] = pipe_proof
            if mode == "blocked":
                check("all stdout observed before fill", not output.buffer and pipe_bytes(read_fd) == 0)
                # Reopening our own pipe through proc creates a separate OFD. Never
                # F_SETFL the inherited writer: that would make child stdout nonblocking.
                padding_fd = os.open(f"/proc/self/fd/{write_fd}", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                descriptors.append(padding_fd)
                padding = bytearray()
                while len(padding) <= PIPE_SIZE:
                    try:
                        written = os.write(padding_fd, b"#")
                    except BlockingIOError:
                        pipe_proof["independent_writer_eagain"] = True
                        break
                    if written != 1:
                        raise RuntimeError("synthetic padding write was not one byte")
                    padding.extend(b"#")
                (directory / "synthetic-padding.bin").write_bytes(padding)
                pipe_proof.update({"synthetic_padding_bytes": len(padding),
                                   "independent_writer_flags": fcntl.fcntl(padding_fd, fcntl.F_GETFL),
                                   "original_writer_flags_after": fcntl.fcntl(write_fd, fcntl.F_GETFL),
                                   "child_stdout_flags_after": stdout_flags(process.pid),
                                   "bytes_after_fill": pipe_bytes(read_fd)})
                check("independent writer reached EAGAIN at exact capacity",
                      pipe_proof.get("independent_writer_eagain") is True
                      and len(padding) == PIPE_SIZE and pipe_bytes(read_fd) == PIPE_SIZE)
                check("only independent OFD has O_NONBLOCK",
                      pipe_proof["independent_writer_flags"] & os.O_NONBLOCK
                      and not any(pipe_proof[field] & os.O_NONBLOCK for field in
                                  ["original_writer_flags_before", "original_writer_flags_after",
                                   "child_stdout_flags_before", "child_stdout_flags_after"]))
                output.frozen = True
                write_control(process.stdin.fileno(), {"id": "blocked-output", "op": "snapshot"})
                pipe_proof["blocked_writer"] = wait_writer(process, identity, time.monotonic() + 2)
                check("actual child writer is blocked in pipe_write", True, pipe_proof["blocked_writer"])
                check("pipe remains full immediately before signal", pipe_bytes(read_fd) == PIPE_SIZE)
            number = {"sigint": signal.SIGINT, "sigterm": signal.SIGTERM}[signal_name]
            sent = own_signal(process, identity, number)
            result["sent"] = sent
            deadline = time.monotonic() + EXIT_SECONDS
            wait_error = None
            try:
                if mode == "ready":
                    # Parent retains a writer for the blocked mode; close it in
                    # ready mode so real child stdout EOF can also be observed.
                    os.close(write_fd)
                    descriptors.remove(write_fd)
                    while process.poll() is None:
                        if not output.eof and select.select([read_fd], [], [], min(0.05, remaining(deadline)))[0]:
                            output.receive(deadline)
                        elif output.eof:
                            time.sleep(min(0.01, remaining(deadline)))
                    output.finish(deadline)
                process.wait(timeout=remaining(deadline))
            except (TimeoutError, subprocess.TimeoutExpired) as error:
                wait_error = str(error)
            elapsed = (time.monotonic_ns() - sent["monotonic_ns"]) / 1_000_000_000
            sockets = {}
            for bus_id, stream in streams.items():
                try:
                    stream.settimeout(1)
                    received = stream.recv(1)
                    sockets[bus_id] = {"eof": received == b"", "unexpected_hex": received.hex()}
                except OSError as error:
                    sockets[bus_id] = {"eof": False, "error": repr(error)}
            stopped = [event for event in output.events if event.get("event") == "stopped"]
            after = {"returncode": process.poll(), "signal_exit_seconds": elapsed,
                     "wait_error": wait_error, "data_sockets": sockets,
                     "stopped_events": stopped, "stdout_eof": output.eof,
                     "stdout_unread_bytes": pipe_bytes(read_fd),
                     "stdout_read_after_fill": False,
                     "backend_cleanup_observed": mode == "ready" and len(stopped) == 1}
            result["after"] = after
            save_json(directory / "after.json", after)
            check("child exited without forced cleanup", wait_error is None and process.poll() is not None, wait_error, fatal=False)
            check("cooperative exit status is zero", process.poll() == 0,
                  {"actual": process.poll(), "old_SIGTERM_minus15_is_failure": True}, fatal=False)
            check("signal exit is within eight seconds", elapsed <= EXIT_SECONDS, elapsed, fatal=False)
            for bus_id, value in sockets.items():
                check(f"{bus_id} data socket reached actual EOF", value["eof"], value, fatal=False)
            if mode == "ready":
                check("exactly one stopped event received", len(stopped) == 1, len(stopped), fatal=False)
                if len(stopped) == 1:
                    check("stopped snapshot proves both leases/pending/queues/configuration zero",
                          all_released(stopped[0].get("snapshot")), stopped[0], fatal=False)
                check("ready stdout reached actual EOF", output.eof, fatal=False)
            else:
                check("stdout remained full and was never drained", pipe_bytes(read_fd) == PIPE_SIZE,
                      {"read_after_fill": False, "bytes": pipe_bytes(read_fd)}, fatal=False)
                check("blocked child stdout flags stayed blocking until signal",
                      pipe_proof["child_stdout_flags_after"] & os.O_NONBLOCK == 0,
                      pipe_proof["child_stdout_flags_after"], fatal=False)
                result["cleanup_scope"] = "exit and two socket EOF observations; no stopped snapshot or backend resource claim while stdout remains unread"
            if all(check["passed"] for check in result["assertions"]):
                result["result"] = "PASS"
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error),
                           "traceback": traceback.format_exc()}
    finally:
        # Any failure is retained before emergency cleanup; never mistake a kill
        # or closing the client's socket for cooperative backend shutdown.
        if process is not None and process.poll() is None:
            result["forced_cleanup"] = True
            result["result"] = "FAIL"
            try:
                result["emergency_signal"] = own_signal(process, identity, signal.SIGKILL)
                process.wait(timeout=3)
            except Exception as error:
                result["emergency_cleanup_error"] = repr(error)
        if process is not None:
            result["final_returncode"] = process.poll()
            if process.stdin is not None:
                process.stdin.close()
        for stream in streams.values():
            stream.close()
        if output is not None:
            output.raw.close()
            save_json(directory / "stdout-events.json", output.events)
            result["stdout_consumed_bytes"] = output.total
            result["stdout_raw_scope"] = "verbatim consumed prefix only; blocked mode never reads the synthetic padding or later child output"
        for fd in descriptors:
            os.close(fd)
        save_json(directory / "protocol.json", protocol)
        result["artifacts"] = [{"path": path.name, "length": path.stat().st_size, "sha256": digest(path)}
                               for path in sorted(directory.iterdir()) if path.is_file()]
        save_json(directory / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/release/rustusb")
    parser.add_argument("--skip-build", action="store_true", help="explicit prebuilt-binary run; recorded as such")
    parser.add_argument("--signal", choices=["all", "sigint", "sigterm"], default="all")
    parser.add_argument("--mode", choices=["all", "ready", "blocked"], default="all")
    parser.add_argument("--evidence", type=Path, help="new directory; default is a fresh /tmp directory")
    args = parser.parse_args()
    if sys.platform != "linux" or os.getuid() == 0 or os.geteuid() != os.getuid():
        parser.error("run on Linux as an ordinary, non-setuid user")
    binary = args.binary.resolve()
    if not args.skip_build and binary != (ROOT / "target/release/rustusb").resolve():
        parser.error("custom --binary requires --skip-build to avoid misleading build provenance")
    if args.evidence is None:
        directory = Path(tempfile.mkdtemp(prefix="rustusb-server-signals-"))
    else:
        directory = args.evidence.resolve()
        directory.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": 1, "result": "FAIL", "host_uid": os.getuid(),
               "host": list(os.uname()), "python": sys.version, "command": sys.argv,
               "scope": "ordinary-user loopback virtual profiles only; no physical USB, modules, ptrace, or network configuration",
               "limits_seconds": {"wire_frame": WIRE_SECONDS, "control": CONTROL_SECONDS,
                                  "signal_exit": EXIT_SECONDS, "startup": 5, "writer_wait": 2},
               "evidence": str(directory), "cases": []}
    try:
        summary["source_before"] = source_state()
        save_json(directory / "source-before.json", summary["source_before"])
        summary["build"] = {"skipped": args.skip_build, "command": None if args.skip_build else BUILD,
                            "features": "external prebuilt; not inferred" if args.skip_build else "workspace all-features release locked"}
        if not args.skip_build:
            with (directory / "build.log").open("wb") as log:
                built = subprocess.run(BUILD, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            summary["build"]["returncode"] = built.returncode
            if built.returncode != 0:
                raise RuntimeError("locked release build failed; see build.log")
        metadata = binary.stat()
        if not stat.S_ISREG(metadata.st_mode) or not os.access(binary, os.X_OK):
            raise RuntimeError("selected binary is not an executable regular file")
        summary["binary_before"] = {"path": str(binary), "length": metadata.st_size,
                                    "device": metadata.st_dev, "inode": metadata.st_ino, "sha256": digest(binary)}
        signals = ["sigint", "sigterm"] if args.signal == "all" else [args.signal]
        modes = ["ready", "blocked"] if args.mode == "all" else [args.mode]
        for mode in modes:
            for name in signals:
                result = run_case(binary, mode, name, directory / f"{mode}-{name}")
                summary["cases"].append(result)
                # stderr is not a pipe: neither logging backpressure nor an
                # uncooperative caller's stderr is claimed by this experiment.
                print(f"{mode}/{name}: {result['result']}", flush=True)
        summary["binary_after_sha256"] = digest(binary)
        summary["source_after"] = source_state()
        save_json(directory / "source-after.json", summary["source_after"])
        summary["source_unchanged"] = (summary["source_before"]["source_sha256"] == summary["source_after"]["source_sha256"]
                                       and summary["source_before"]["head"] == summary["source_after"]["head"])
        summary["binary_unchanged"] = summary["binary_before"]["sha256"] == summary["binary_after_sha256"]
        if (summary["cases"] and all(case["result"] == "PASS" for case in summary["cases"])
                and summary["source_unchanged"] and summary["binary_unchanged"]):
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = {"type": type(error).__name__, "message": str(error),
                            "traceback": traceback.format_exc()}
    finally:
        summary["artifacts"] = [{"path": str(path.relative_to(directory)), "length": path.stat().st_size,
                                 "sha256": digest(path)} for path in sorted(directory.rglob("*")) if path.is_file()]
        save_json(directory / "summary.json", summary)
    print(json.dumps({"result": summary["result"], "evidence": str(directory),
                      "summary_sha256": digest(directory / "summary.json")}, sort_keys=True))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
