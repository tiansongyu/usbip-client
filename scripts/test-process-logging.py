#!/usr/bin/env python3
"""Actual ordinary-UID CLI logging subprocess checks; never builds or uses USB.

Requires a prebuilt rust-backend CLI and a fresh evidence directory. Uses only
loopback memory USB/IP profiles, private caller-owned log stores under ~/.cache,
and signals/resource limits applied to its own unreaped children. Failures and
partial files are retained. This is software evidence, not kernel/physical USB.
"""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SUPPORT = ROOT / "scripts/test-server-signals.py"
MAX_CAPTURE = 1024 * 1024
SEGMENT_BYTES = 4096
RETAIN = 2
EXIT_SECONDS = 8


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def error_fact(error):
    return {"type": type(error).__name__, "message": str(error),
            "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))}


def support():
    spec = importlib.util.spec_from_file_location("logging_signal_support", SUPPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sources():
    names = {"Cargo.toml", "Cargo.lock", "scripts/test-process-logging.py", "scripts/test-server-signals.py"}
    for folder in ["crates", "vendor"]:
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and (folder == "vendor" or path.suffix in {".rs", ".toml"}):
                names.add(str(path.relative_to(ROOT)))
    files = [{"path": name, "bytes": (ROOT / name).stat().st_size, "sha256": sha(ROOT / name)}
             for name in sorted(names)]
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                              timeout=10).stdout.decode().strip()
    return {"head": git("rev-parse", "HEAD"), "git_status": git("status", "--porcelain=v1"),
            "files": files, "fingerprint": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def metadata(path):
    value = path.lstat()
    return {"path": str(path), "device": value.st_dev, "inode": value.st_ino,
            "mode": oct(stat.S_IMODE(value.st_mode)), "uid": value.st_uid,
            "gid": value.st_gid, "nlink": value.st_nlink, "bytes": value.st_size}


def prepare_cache(home):
    require(home.is_absolute() and ".." not in home.parts, "HOME must be an absolute nontraversing path")
    uid = os.getuid()
    # Validate before creating anything. Never follow a symlink or repair an
    # existing ancestor/cache; the actual product retains its own checked opens.
    for path in reversed((home, *home.parents)):
        value = path.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid in {0, uid}
                and stat.S_IMODE(value.st_mode) & 0o022 == 0,
                "HOME ancestry must be owned and protected directories")
    cache = home / ".cache"
    created = False
    try:
        value = cache.lstat()
    except FileNotFoundError:
        try:
            cache.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass  # A concurrent caller still has to pass the same checks.
        value = cache.lstat()
    mode = stat.S_IMODE(value.st_mode)
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == uid
            and mode & 0o022 == 0 and (not created or mode == 0o700),
            "HOME/.cache must be an owned protected directory")
    return cache


def binary_receipt(path):
    return {**metadata(path), "sha256": sha(path)}


def raw_files(directory):
    return [{**metadata(path), "sha256": sha(path)} for path in sorted(directory.iterdir()) if path.is_file()]


def archive_store(store, target):
    target.mkdir()
    rows = []
    for path in sorted(store.iterdir()):
        require(path.is_file() and not path.is_symlink(), "unexpected nonregular log artifact")
        data = path.read_bytes()
        (target / path.name).write_bytes(data)
        rows.append({**metadata(path), "archived": path.name, "sha256": hashlib.sha256(data).hexdigest()})
    save(target / "original-metadata.json", rows)
    return rows


def history(store, complete=True):
    segments = sorted(store.glob("segment-*.ndjson"), reverse=True)
    require(0 < len(segments) <= RETAIN, "segment count exceeds configured bound")
    records, headers = [], []
    for path in segments:
        info = metadata(path)
        require(info["uid"] == os.getuid() and info["mode"] == "0o600" and info["nlink"] == 1,
                "actual segment ownership/mode/link differs")
        raw = path.read_bytes()
        require(len(raw) <= SEGMENT_BYTES, "segment crossed configured byte limit")
        lines = raw.splitlines(keepends=True)
        require(lines and lines[0].endswith(b"\n"), "missing complete segment header")
        header = json.loads(lines[0])
        require(set(header) == {"schema_version", "segment", "epoch", "ordinal"}
                and header["schema_version"] == 1, "unexpected header shape")
        headers.append(header)
        for line in lines[1:]:
            if not line.endswith(b"\n") and not complete:
                continue
            require(line.endswith(b"\n") and len(line) <= 2048, "split/oversized log record")
            record = json.loads(line)
            require(record["schema_version"] == 1 and record["epoch"] == header["epoch"],
                    "record schema/epoch mismatch")
            records.append(record)
    require(len({h["segment"] for h in headers}) == len(headers), "duplicate segment identity")
    require([h["ordinal"] for h in headers] == sorted({h["ordinal"] for h in headers}),
            "retained segment order changed")
    identities = [(r["epoch"], r["sequence"]) for r in records]
    require(len(set(identities)) == len(identities), "duplicate process record sequence")
    for previous, current in zip(records, records[1:]):
        if previous["epoch"] == current["epoch"]:
            require(previous["sequence"] < current["sequence"], "process sequence moved backwards")
    return {"headers": headers, "records": records, "payload_bytes": sum(p.stat().st_size for p in segments)}


def live_sequence(store):
    # Only used as a bounded observation barrier; final parsing is strict.
    best = -1
    for path in store.glob("segment-*.ndjson"):
        try:
            lines = path.read_bytes().splitlines(keepends=True)[1:]
        except FileNotFoundError:
            continue
        for line in lines:
            if line.endswith(b"\n"):
                value = json.loads(line)
                best = max(best, value["sequence"])
    return best


class Run:
    def __init__(self, binary, evidence, utility):
        self.binary, self.evidence, self.util = binary, evidence, utility
        self.children = []
        self.client_sockets = []
        self.collection_errors = []
        self.primary_checkpoint_attempted = False
        self.env = dict(os.environ, RUST_LOG="debug", RUST_BACKTRACE="0", LC_ALL="C")

    def spawn(self, name, arguments, stdout=subprocess.PIPE, ignore_xfsz=False):
        directory = self.evidence / name
        directory.mkdir()
        errors = (directory / "stderr.bin").open("wb")
        argv = [str(self.binary), *arguments]
        wrapper = [sys.executable, "-c",
                   "import os,signal,sys; signal.signal(signal.SIGXFSZ,signal.SIG_IGN); os.execv(sys.argv[1],sys.argv[1:])"]
        process = subprocess.Popen(wrapper + argv if ignore_xfsz else argv, cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=stdout, stderr=errors,
                                   env=self.env, close_fds=True, bufsize=0)
        row = {"name": name, "argv": argv, "pid": process.pid, "started_monotonic_ns": time.monotonic_ns(),
               "ignore_sigxfsz_exec_wrapper": ignore_xfsz, "forced_cleanup": False}
        child = {"process": process, "row": row, "directory": directory, "stderr": errors, "output": None}
        self.children.append(child)
        self.capture(child, required=False)
        return child

    def capture(self, child, required=True):
        process, row = child["process"], child["row"]
        try:
            identity = self.util.proc_identity(process.pid)
            require(identity["uids"] == [os.getuid()] * 4, "child has unexpected UID")
            executable = Path(f"/proc/{process.pid}/exe")
            actual, selected = executable.stat(), self.binary.stat()
            if (actual.st_dev, actual.st_ino) != (selected.st_dev, selected.st_ino):
                if required:
                    raise RuntimeError("child has not exec'd selected binary")
                row["pre_exec_identity"] = identity
                return
            require(sha(executable) == sha(self.binary), "executed ELF differs from selected binary")
            row["identity"] = identity
            row["executed_binary"] = {"resolved": str(executable.resolve()), "device": actual.st_dev,
                                      "inode": actual.st_ino, "sha256": sha(executable)}
            (child["directory"] / "proc-status.txt").write_text(Path(f"/proc/{process.pid}/status").read_text())
            (child["directory"] / "proc-stat.txt").write_text(Path(f"/proc/{process.pid}/stat").read_text())
        except (FileNotFoundError, ProcessLookupError):
            if required:
                raise
            row["fast_exit_proc_scope"] = "selected argv/binary guards only; /proc executable not sampled"

    def wait(self, child, timeout=EXIT_SECONDS):
        process = child["process"]
        code = process.wait(timeout=timeout)
        child["row"].update(returncode=code, waited_monotonic_ns=time.monotonic_ns())
        child["stderr"].flush()
        save(child["directory"] / "process.json", child["row"])
        return code

    def command(self, name, arguments, expected=0):
        child = self.spawn(name, arguments)
        stdout, _ = child["process"].communicate(timeout=EXIT_SECONDS)
        require(len(stdout) <= MAX_CAPTURE, "command stdout exceeds evidence cap")
        (child["directory"] / "stdout.bin").write_bytes(stdout)
        code = self.wait(child)
        require(code == expected, f"{name} exited {code}, expected {expected}")
        return stdout, child

    def signal(self, child, number):
        self.capture(child)
        fact = self.util.own_signal(child["process"], child["row"]["identity"], number)
        child["row"].setdefault("signals", []).append(fact)
        return fact

    def collect(self, operation, action):
        """A secondary collection failure must never replace the primary test error."""
        try:
            return action()
        except Exception as error:
            self.collection_errors.append({"operation": operation, **error_fact(error)})
            return None

    def primary_checkpoint(self, summary):
        if not self.primary_checkpoint_attempted:
            self.primary_checkpoint_attempted = True
            self.collect("primary-failure-checkpoint", lambda: save(
                self.evidence / "primary-failure.json", summary))

    def finalize(self):
        failures = []
        for child in self.children:
            process = child["process"]
            name = child["row"]["name"]
            alive = self.collect(name + ":poll-before-cleanup", lambda: process.poll() is None)
            if alive:
                child["row"]["forced_cleanup"] = True
                failures.append(child["row"]["name"])
                try:
                    self.signal(child, signal.SIGKILL)
                except Exception as error:
                    # Popen still owns this unreaped child. This emergency path
                    # never counts as the cooperative outcome being tested.
                    child["row"]["emergency_identity_error"] = str(error)
                    self.collection_errors.append({"operation": name + ":emergency-signal-identity", **error_fact(error)})
                    self.collect(name + ":emergency-kill", process.kill)
                self.collect(name + ":emergency-wait", lambda: self.wait(child, 3))
            if child["output"] is not None:
                self.collect(name + ":close-stdout-artifact", child["output"].raw.close)
                self.collect(name + ":save-stdout-events", lambda: save(
                    child["directory"] / "stdout-events.json", child["output"].events))
            if "protocol" in child:
                self.collect(name + ":save-protocol", lambda: save(child["directory"] / "protocol.json", child["protocol"]))
            for label, stream in [("stdin", process.stdin), ("stdout", process.stdout)]:
                if stream is not None:
                    self.collect(name + ":close-" + label, stream.close)
            self.collect(name + ":close-stderr", child["stderr"].close)
            child["row"]["returncode"] = self.collect(name + ":poll-final", process.poll)
            self.collect(name + ":save-process", lambda: save(child["directory"] / "process.json", child["row"]))
        for index, stream in enumerate(self.client_sockets):
            self.collect(f"client-socket-{index}:close", stream.close)
        return failures

    def archive_final(self, store, destination):
        """Keep collecting other original files even if one source/copy fails."""
        self.collect(str(destination) + ":mkdir", destination.mkdir)
        paths = self.collect(str(store) + ":list", lambda: sorted(store.iterdir()))
        rows = []
        for path in paths or []:
            def copy_one():
                require(path.is_file() and not path.is_symlink(), "unexpected nonregular log artifact")
                data = path.read_bytes()
                (destination / path.name).write_bytes(data)
                return {**metadata(path), "archived": path.name, "sha256": hashlib.sha256(data).hexdigest()}
            row = self.collect(str(path) + ":archive-original", copy_one)
            if row is not None:
                rows.append(row)
        self.collect(str(destination) + ":save-metadata", lambda: save(destination / "original-metadata.json", rows))

    def final_summary(self, summary):
        """Attempt the requested report, with a fresh /tmp fallback if its write fails."""
        path = self.evidence / "summary.json"
        for fallback in [False, True]:
            try:
                if fallback:
                    summary["result"] = "FAIL"
                    descriptor, filename = tempfile.mkstemp(prefix="rustusb-process-logging-summary-failure-", suffix=".json")
                    os.close(descriptor)
                    path = Path(filename)
                summary["summary_destination"] = str(path)
                save(path, summary)
                return path, sha(path)
            except Exception as error:
                self.collection_errors.append({"operation": str(path) + ":save-final-summary", **error_fact(error)})
        # Neither filesystem accepted a report. Preserve the original primary
        # error and every collection error in the caller's captured stderr.
        print(json.dumps(summary, sort_keys=True), file=sys.stderr, flush=True)
        return None, None


class Virtual:
    def __init__(self, run, name, store, fault=False, acquire=True):
        self.run, self.store, self.streams, self.protocol = run, store, {}, []
        self.child = run.spawn(name, ["--json", "--log-directory", str(store), "--log-max-bytes", "4096",
                                     "--log-retain", "2", "virtual-server", "--listen", "127.0.0.1:0",
                                     "--control-stdin"], ignore_xfsz=fault)
        self.output = run.util.Output(self.child["process"].stdout.fileno(), self.child["directory"] / "stdout.bin")
        self.child["output"] = self.output
        self.child["protocol"] = self.protocol
        ready = self.output.line(time.monotonic() + 5)
        require(ready.get("event") == "ready", "virtual owner did not become ready")
        run.capture(self.child)
        host, port = ready["address"].rsplit(":", 1)
        require(host == "127.0.0.1", "test listener escaped loopback")
        self.address = (host, int(port))
        if acquire:
            for bus_id, (_, _, endpoint, length) in run.util.PROFILES.items():
                stream = run.util.import_device(self.address, bus_id, self.protocol)
                self.streams[bus_id] = stream
                run.client_sockets.append(stream)
                run.util.configure(stream, bus_id, self.protocol)
                run.util.submit(stream, bus_id, 2, 1, endpoint, length, bytes(8), self.protocol)
            deadline = time.monotonic() + 3
            while True:
                before = self.snapshot("held")
                if run.util.all_pending(before["snapshot"]):
                    break
                require(time.monotonic() < deadline, "pending INs did not become actual")
                time.sleep(.01)
            require(not select.select(list(self.streams.values()), [], [], 0)[0], "pending IN completed unexpectedly")
            save(self.child["directory"] / "held.json", before)

    def snapshot(self, marker):
        self.run.util.write_control(self.child["process"].stdin.fileno(), {"id": marker, "op": "snapshot"})
        event = self.output.line(time.monotonic() + 3)
        require(event.get("event") == "snapshot" and event.get("id") == marker, "snapshot receipt mismatch")
        return event

    def bad_peer(self, wait_log=True):
        before = live_sequence(self.store)
        with socket.create_connection(self.address, timeout=3) as peer:
            peer.sendall(bytes.fromhex("0111ffff00000000"))
            peer.shutdown(socket.SHUT_WR)
            peer.settimeout(3)
            require(peer.recv(1) == b"", "malformed management peer was not closed")
        if wait_log:
            deadline = time.monotonic() + 3
            while live_sequence(self.store) <= before:
                require(time.monotonic() < deadline, "actual connection completion did not generate a file record")
                time.sleep(.005)

    def stop(self, expected=0):
        self.run.signal(self.child, signal.SIGTERM)
        deadline = time.monotonic() + EXIT_SECONDS
        while not self.output.eof:
            self.output.receive(deadline)
        self.output.finish(deadline)
        code = self.run.wait(self.child, max(.1, deadline - time.monotonic()))
        require(code == expected, f"virtual owner expected exit {expected}, got {code}")
        if expected == 0:
            require((self.child["directory"] / "stderr.bin").read_bytes() == b"",
                    "file-only logger synchronously teed diagnostics to stderr")
        stopped = [event for event in self.output.events if event.get("event") == "stopped"]
        require(len(stopped) == 1 and self.run.util.all_released(stopped[0]["snapshot"]),
                "actual stopped snapshot lacks complete virtual cleanup")
        for bus_id, stream in self.streams.items():
            stream.settimeout(1)
            data = stream.recv(1)
            self.protocol.append({"operation": "actual_data_eof", "bus_id": bus_id, "bytes_hex": data.hex()})
            require(data == b"", "owned data socket did not reach EOF")
            stream.close()
        self.streams.clear()
        require(all(event.get("event") in {"ready", "snapshot", "stopped", "control_error", "shutdown"}
                    for event in self.output.events), "process logger polluted machine stdout")
        save(self.child["directory"] / "protocol.json", self.protocol)
        save(self.child["directory"] / "stopped.json", stopped[0])
        return stopped[0]


def private_store(base, name):
    path = base / name
    path.mkdir(mode=0o700)
    return path


def read_page(run, store, name, after=None, limit=16):
    before = raw_files(store)
    arguments = ["--json", "logs", "--directory", str(store), "--limit", str(limit)]
    if after:
        arguments += ["--after", after]
    output, _ = run.command(name, arguments)
    rows = [json.loads(line) for line in output.splitlines()]
    require(len(rows) == 1 and rows[0].get("schema_version") == 1 and rows[0].get("event") == "logs",
            "log reader stdout contains non-page bytes")
    require(before == raw_files(store), "read-only observer changed log files")
    require(len(rows[0]["page"]["records"]) <= limit, "reader exceeded requested page count")
    return rows[0]["page"]


def rotation_and_restart(run, base):
    store = private_store(base, "rotation")
    server = Virtual(run, "rotation-owner", store)
    first = history(store)["headers"][0]
    for _ in range(28):
        server.bad_peer()
    require(run.util.all_pending(server.snapshot("after-log-traffic")["snapshot"]),
            "logging traffic changed held virtual resources")
    server.stop()
    current = history(store)
    require(len(current["headers"]) == RETAIN and current["headers"][-1]["ordinal"] > first["ordinal"],
            "same-process byte rotation did not occur")
    require(len({header["epoch"] for header in current["headers"]}) == 1,
            "byte rotation unexpectedly changed process epoch")
    archive_store(store, run.evidence / "rotation-initial-files")
    seen, cursor, first_cursor = [], None, None
    for index in range(128):
        page = read_page(run, store, f"cursor-{index}", cursor, 1)
        seen.extend(page["records"])
        if first_cursor is None:
            first_cursor = page["next_after"]
        if not page["more"]:
            break
        require(page["next_after"] and page["next_after"] != cursor, "reader cursor made no progress")
        cursor = page["next_after"]
    else:
        raise RuntimeError("bounded log history never completed")
    require(seen == current["records"], "CLI pagination differs from actual complete NDJSON records")
    epochs = [current["headers"][-1]["epoch"]]
    for index in range(3):
        restarted = Virtual(run, f"restart-{index}", store, acquire=False)
        restarted.stop()
        facts = history(store)
        epochs.append(facts["headers"][-1]["epoch"])
        archive_store(store, run.evidence / f"restart-{index}-files")
    require(len(set(epochs)) == 4, "restart reused a log process epoch")
    page = read_page(run, store, "expired-cursor", first_cursor)
    require(page["gap"] is True, "retention-expired cursor was not reported as a gap")
    stdout, _ = run.command("human-page", ["logs", "--directory", str(store), "--limit", "2"])
    require(stdout and b"Next cursor:" in stdout, "human bounded log page missing continuation")
    return {"epochs": epochs, "retained_segments": len(history(store)["headers"]),
            "expired_cursor": first_cursor, "actual_reader_matches_raw_records": True,
            "store": str(store)}


def follow_cancel(run, base, number):
    name = signal.Signals(number).name.lower()
    store = private_store(base, "follow-" + name)
    server = Virtual(run, "follow-owner-" + name, store)
    child = run.spawn("follow-reader-" + name, ["--json", "logs", "--directory", str(store), "--follow"])
    output = run.util.Output(child["process"].stdout.fileno(), child["directory"] / "stdout.bin")
    child["output"] = output
    first = output.line(time.monotonic() + 5)
    require(first.get("event") == "logs", "follow did not produce actual initial page")
    run.capture(child)
    server.bad_peer()
    event = None
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        event = output.line(deadline)
        require(event.get("event") in {"logs", "logs_busy"}, "follow stdout polluted")
        if event.get("event") == "logs" and any(r["event"]["kind"] == "diagnostic" for r in event["page"]["records"]):
            break
    require(event and event.get("event") == "logs" and event["page"]["records"], "follow did not observe newly written record")
    run.signal(child, number)
    output.finish(time.monotonic() + EXIT_SECONDS)
    require(run.wait(child) == 0, "follow signal was not a clean observer exit")
    require(server.child["process"].poll() is None, "observer stopped independent owner process")
    held = server.snapshot("after-observer-exit")
    require(run.util.all_pending(held["snapshot"]), "observer cancellation changed actual virtual ownership")
    require(not select.select(list(server.streams.values()), [], [], 0)[0], "observer closed actual data socket")
    save(child["directory"] / "owner-still-held.json", held)
    server.stop()
    archive_store(store, run.evidence / f"follow-{name}-files")
    return {"signal": number, "observer_exit": 0, "owner_unchanged_after_observer": True}


def blocked_observer(run, base):
    store = private_store(base, "observer-backpressure")
    server = Virtual(run, "bp-owner", store)
    read_fd, write_fd = os.pipe()
    padding_fd = None
    try:
        require(fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096) == 4096, "stdout pipe capacity differs")
        padding_fd = os.open(f"/proc/self/fd/{write_fd}", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        count = os.write(padding_fd, b"#" * 4096)
        require(count == 4096 and run.util.pipe_bytes(read_fd) == 4096, "stdout pipe not filled exactly")
        child = run.spawn("bp-reader", ["--json", "logs", "--directory", str(store), "--follow"], stdout=write_fd)
        deadline = time.monotonic() + 2
        observed = None
        observations = []
        while time.monotonic() < deadline and observed is None:
            run.capture(child)
            for task in sorted(Path(f"/proc/{child['process'].pid}/task").iterdir()):
                try:
                    comm, wchan = (task / "comm").read_text().strip(), (task / "wchan").read_text().strip()
                except FileNotFoundError:
                    continue
                if comm.startswith("virtual-test-"):
                    item = {"tid": int(task.name), "comm": comm, "wchan": wchan, "monotonic_ns": time.monotonic_ns()}
                    observations.append(item)
                    if wchan in {"pipe_write", "anon_pipe_write"}:
                        observed = item
            time.sleep(.005)
        save(child["directory"] / "blocked-writer-observations.json", observations)
        require(observed is not None, "actual observer writer never blocked in the pipe")
        require(run.util.stdout_flags(child["process"].pid) & os.O_NONBLOCK == 0,
                "child stdout OFD was made nonblocking")
        started = child["row"]["started_monotonic_ns"]
        require(run.wait(child) == 1, "output deadline did not fail the log observer")
        elapsed = (time.monotonic_ns() - started) / 1e9
        require(4 <= elapsed <= 8, "observer did not use bounded actual output deadline")
        require(run.util.pipe_bytes(read_fd) == 4096, "test drained blocked stdout")
        errors = (child["directory"] / "stderr.bin").read_bytes()
        require(b"stdout consumer is blocked" in errors, "bounded output failure diagnostic missing")
        require(run.util.all_pending(server.snapshot("after-reader-deadline")["snapshot"]),
                "observer output backpressure retained or stopped unrelated USB owner")
        server.stop()
        archive_store(store, run.evidence / "bp-files")
        return {"capacity": 4096, "synthetic_padding_bytes": 4096, "stdout_read_after_fill": False,
                "observed_wait": observed, "exit": 1, "elapsed_seconds": elapsed,
                "scope": "observer deadline and independent virtual owner; no unread observer page claim"}
    finally:
        if padding_fd is not None:
            os.close(padding_fd)
        os.close(write_fd)
        os.close(read_fd)


def startup_refusals(run, base):
    store = private_store(base, "startup-valid")
    bad = private_store(base, "startup-writable")
    bad.chmod(0o755)
    link = base / "startup-link"
    link.symlink_to(store, target_is_directory=True)
    tail = ["virtual-server", "--listen", "127.0.0.1:0", "--control-stdin"]
    cases = [
        ("flags-below-size", ["--log-directory", str(store), "--log-max-bytes", "4095", *tail], 2),
        ("flags-above-retain", ["--log-directory", str(store), "--log-retain", "17", *tail], 2),
        ("flags-missing-directory", ["--log-max-bytes", "4096", *tail], 2),
        ("flags-console-conflict", ["--log-directory", str(store), "--log-json", *tail], 2),
        ("flags-reader-writer", ["--log-directory", str(store), "logs", "--directory", str(store)], 1),
        ("startup-bad-mode", ["--log-directory", str(bad), *tail], 1),
        ("startup-symlink", ["--log-directory", str(link), *tail], 1),
    ]
    for name, args, code in cases:
        stdout, child = run.command(name, args, code)
        require(not stdout, "rejected startup emitted application/ready stdout")
        require((child["directory"] / "stderr.bin").stat().st_size > 0, "startup refusal was silent")
    require(not list(store.iterdir()) and not list(bad.iterdir()), "rejected startup created operational log state")
    return {"cases": len(cases), "child_waits": [c[0] for c in cases], "device_work": "no ready event; startup arguments/private-directory gates"}


def runtime_file_failure(run, base):
    store = private_store(base, "fsize-failure")
    server = Virtual(run, "fsize-owner", store, fault=True)
    child = server.child
    status = Path(f"/proc/{child['process'].pid}/status").read_text()
    ignored = int(next(line.split()[1] for line in status.splitlines() if line.startswith("SigIgn:")), 16)
    require(ignored & (1 << (signal.SIGXFSZ - 1)), "exec wrapper did not retain SIGXFSZ ignore")
    active = store / "segment-00.ndjson"
    before = metadata(active)
    cap = before["bytes"] + 16
    require(cap < SEGMENT_BYTES, "fault would test rotation/header instead of active record write")
    run.capture(child)
    previous = resource.prlimit(child["process"].pid, resource.RLIMIT_FSIZE)
    require(previous[0] == resource.RLIM_INFINITY or previous[0] >= cap, "ambient fsize is too low")
    changed_at = time.monotonic_ns()
    resource.prlimit(child["process"].pid, resource.RLIMIT_FSIZE, (cap, previous[1]))
    require(resource.prlimit(child["process"].pid, resource.RLIMIT_FSIZE) == (cap, previous[1]), "child resource limit not applied")
    fds = []
    for fd in sorted(Path(f"/proc/{child['process'].pid}/fd").iterdir()):
        try:
            if fd.resolve() == active:
                actual = fd.stat()
                require((actual.st_dev, actual.st_ino) == (before["device"], before["inode"]),
                        "actual descriptor differs from original active log file")
                fds.append({"fd": int(fd.name), "resolved": str(fd.resolve()),
                            "actual_fd_device": actual.st_dev, "actual_fd_inode": actual.st_ino,
                            "actual_fd_size": actual.st_size, "path_stat": metadata(active),
                            "fdinfo": Path(f"/proc/{child['process'].pid}/fdinfo/{fd.name}").read_text()})
        except FileNotFoundError:
            continue
    require(fds, "actual child did not hold the sampled active log inode")
    server.bad_peer(wait_log=False)
    deadline = time.monotonic() + 3
    while active.stat().st_size != cap:
        require(time.monotonic() < deadline, "actual limited log write did not reach configured boundary")
        time.sleep(.005)
    after = metadata(active)
    require((before["device"], before["inode"]) == (after["device"], after["inode"]), "fault changed the original log inode")
    require(not active.read_bytes().endswith(b"\n"), "expected actual partial record was not preserved")
    held = server.snapshot("held-after-log-io-failure")
    require(run.util.all_pending(held["snapshot"]), "logger failure changed existing virtual owner before stop")
    save(child["directory"] / "rlimit-fault.json", {"identity": child["row"]["identity"], "limit_before": previous,
         "limit_after": [cap, previous[1]], "set_monotonic_ns": changed_at, "file_before": before, "file_after": after,
         "actual_log_fds": fds, "sigxfsz_ignored": True, "held_after_actual_partial_write": held})
    stopped = server.stop(expected=1)
    errors = (child["directory"] / "stderr.bin").read_bytes()
    require(b"logging failed after the requested operation; inspect its result before retrying:" in errors,
            "post-cleanup logging failure was not explicit")
    require(b"logging I/O:" in errors and b"os error 27" in errors, "actual file-size I/O failure category missing")
    archive_store(store, run.evidence / "fsize-failed-originals")
    facts = history(store, complete=False)
    require(not any(r["event"]["kind"] == "stopped" for r in facts["records"]),
            "failed logger forged successful drained shutdown")
    return {"exit": 1, "actual_partial_log_bytes": 16, "errno": 27,
            "actual_virtual_stopped": stopped, "log_originals_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path, help="must not exist")
    args = parser.parse_args()
    if sys.platform != "linux" or os.getuid() == 0 or os.getuid() != os.geteuid():
        parser.error("requires Linux and an ordinary non-setuid caller")
    binary = args.binary.resolve(strict=True)
    require(binary.is_file() and os.access(binary, os.X_OK), "selected binary is not executable")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    utility = support()
    run = Run(binary, evidence, utility)
    summary = {"schema_version": 1, "result": "FAIL", "completed": False,
               "collection_errors": run.collection_errors, "command": sys.argv, "python": sys.version,
               "uid": os.getuid(), "host": list(os.uname()), "cases": [],
               "scope": "actual logging subprocesses and loopback virtual memory devices; no host USB/root/helper/kernel mutation",
               "build": "external prebuilt executable; this runner never builds",
               "reused_helpers": {"path": str(SUPPORT), "sha256": sha(SUPPORT),
                                  "scope": "bounded stdout/USBIP framing, fixed-profile pending/release predicates and child signal identity; not its runner"}}
    base = None
    try:
        summary["sources_before"] = sources()
        summary["binary_before"] = binary_receipt(binary)
        cache = prepare_cache(Path.home())
        base = Path(tempfile.mkdtemp(prefix="rustusb-process-logs-", dir=cache))
        base.chmod(0o700)
        summary["private_log_root"] = str(base)
        summary["parent_metadata"] = [metadata(path) for path in [Path.home(), cache, base]]
        for name, action in [
            ("startup-refusals", lambda: startup_refusals(run, base)),
            ("rotation-restart-cursors", lambda: rotation_and_restart(run, base)),
            ("follow-sigint", lambda: follow_cancel(run, base, signal.SIGINT)),
            ("follow-sigterm", lambda: follow_cancel(run, base, signal.SIGTERM)),
            ("observer-output-backpressure", lambda: blocked_observer(run, base)),
            ("runtime-file-size-io-failure", lambda: runtime_file_failure(run, base)),
        ]:
            case = {"name": name, "result": "FAIL", "started_monotonic_ns": time.monotonic_ns()}
            summary["cases"].append(case)
            try:
                case["facts"] = action()
                case["result"] = "PASS"
            except Exception as error:
                case["error"] = error_fact(error)
                summary.setdefault("error", case["error"])
                run.primary_checkpoint(summary)
                raise
            finally:
                case["completed_monotonic_ns"] = time.monotonic_ns()
                run.collect(name + ":save-case", lambda: save(evidence / (name + ".json"), case))
        summary["result"] = "PASS"
    except Exception as error:
        summary.setdefault("error", error_fact(error))
        run.primary_checkpoint(summary)
    finally:
        run.collect("pre-cleanup-checkpoint", lambda: save(evidence / "pre-cleanup-summary.json", summary))
        forced = run.collect("finalize-children", run.finalize)
        summary["forced_cleanup"] = forced
        if forced or forced is None:
            summary["result"] = "FAIL"
        summary["sources_after"] = run.collect("sources-after", sources)
        summary["binary_after"] = run.collect("binary-after", lambda: binary_receipt(binary))
        summary["source_unchanged"] = bool(summary["sources_after"]) and summary.get("sources_before") == summary["sources_after"]
        summary["binary_unchanged"] = bool(summary["binary_after"]) and summary.get("binary_before") == summary["binary_after"]
        if not summary["source_unchanged"] or not summary["binary_unchanged"]:
            summary["result"] = "FAIL"
        if base is not None:
            # Retain every original, including failure tails. Never delete a
            # failed store or rewrite it to make a later run pass.
            stores = run.collect("private-stores:list", lambda: sorted(base.iterdir()))
            for store in stores or []:
                is_directory = run.collect(str(store) + ":classify", lambda: store.is_dir() and not store.is_symlink())
                if is_directory:
                    destination = evidence / ("final-store-" + store.name)
                    run.collect(str(store) + ":archive-final-store", lambda: run.archive_final(store, destination))
        summary["children"] = [child["row"] for child in run.children]
        summary["artifacts"] = []
        artifacts = run.collect("artifacts:list", lambda: sorted(evidence.rglob("*")))
        for path in artifacts or []:
            def artifact():
                if path.is_file():
                    return {"path": str(path.relative_to(evidence)), "bytes": path.stat().st_size, "sha256": sha(path)}
                return None
            value = run.collect(str(path) + ":artifact-hash", artifact)
            if value is not None:
                summary["artifacts"].append(value)
        if run.collection_errors:
            summary["result"] = "FAIL"
        summary["completed"] = True
        report_path, report_sha = run.final_summary(summary)
    print(json.dumps({"result": summary["result"], "evidence": str(evidence),
                      "summary": str(report_path) if report_path else None, "sha256": report_sha}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
