#!/usr/bin/env python3
"""Actual private diagnostic CLI lifecycle checks; no builds or host USB.

Linux x86_64/aarch64 ordinary-UID subprocesses. A ptrace syscall-entry gate
holds one actual fresh partial-file fchmod or fsync without altering registers,
results or file bytes. It tests removable observer delays, not kernel hardblock.
"""

import argparse
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SUPPORT = ROOT / "scripts/test-process-logging.py"
spec = importlib.util.spec_from_file_location("diagnostic_process_support", SUPPORT)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
require, save, sha, metadata = support.require, support.save, support.sha, support.metadata

TRACEME, SYSCALL, SETOPTIONS, GETINFO = 0, 24, 0x4200, 0x420e
TRACE_OPTIONS = 1 | 8 | 16 | (1 << 20)  # syscall tags, threads, exec, EXITKILL
WALL = 0x40000000
LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.ptrace.restype = ctypes.c_long
TRACE_WRAPPER = """import ctypes,os,signal,sys
libc=ctypes.CDLL(None,use_errno=True)
if libc.ptrace(0,0,0,0)!=0: raise OSError(ctypes.get_errno(),'PTRACE_TRACEME')
os.kill(os.getpid(),signal.SIGSTOP)
os.execv(sys.argv[1],sys.argv[1:])
"""
LIMIT_WRAPPER = """import os,resource,signal,sys
signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE,(16,16))
os.execv(sys.argv[1],sys.argv[1:])
"""


def ptrace(request, pid, address=0, data=0):
    ctypes.set_errno(0)
    result = LIBC.ptrace(ctypes.c_uint(request), ctypes.c_uint(pid),
                         ctypes.c_void_p(address), ctypes.c_void_p(data))
    if result == -1 and ctypes.get_errno():
        raise OSError(ctypes.get_errno(), "owned diagnostic ptrace operation")
    return result


class Run(support.Run):
    def spawn(self, name, arguments, stdout=subprocess.PIPE, wrapper=None):
        directory = self.evidence / name
        directory.mkdir()
        errors = (directory / "stderr.bin").open("wb")
        argv = [str(self.binary), *arguments]
        actual = [sys.executable, "-c", wrapper, *argv] if wrapper else argv
        process = subprocess.Popen(actual, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=errors, env=self.env,
                                   close_fds=True, bufsize=0)
        row = {"name": name, "argv": argv, "pid": process.pid,
               "started_monotonic_ns": time.monotonic_ns(), "forced_cleanup": False,
               "wrapper": "ptrace_traceme" if wrapper == TRACE_WRAPPER else
                          "rlimit_fsize_16_sigxfsz_ignored" if wrapper else None}
        child = {"process": process, "row": row, "directory": directory,
                 "stderr": errors, "output": None}
        self.children.append(child)
        if wrapper != TRACE_WRAPPER:
            self.capture(child, required=False)
        return child


def sources():
    value = support.sources()
    value["diagnostic_runner"] = {"path": str(SELF), "sha256": sha(SELF)}
    return value


def private(base, name):
    path = base / name
    path.mkdir(mode=0o700)
    return path


def arguments(path, *extra):
    return ["diagnostics", "--output", str(path), "--json", *extra]


def receipt(raw):
    require(len(raw) < 65536 and raw.endswith(b"\n") and len(raw.splitlines()) == 1,
            "stdout is not exactly one bounded JSON receipt")
    value = json.loads(raw)
    require(value["schema_version"] == 1 and value["event"] == "diagnostic_export",
            "unexpected receipt protocol")
    return value


def document(path, value, published=True):
    result = value["file"]
    require(result["result"] == "written", "expected actual file receipt")
    result = result["receipt"]
    require(result["publication"] == ("published" if published else "partial"), "wrong publication state")
    actual = path if published else path.parent / result["partial_name"]
    info = metadata(actual)
    raw = actual.read_bytes()
    require(info["uid"] == os.getuid() and info["mode"] == "0o600" and info["nlink"] == 1,
            "actual artifact ownership/mode/link differs")
    require(0 < len(raw) <= 4 * 1024 * 1024 and len(raw) == result["written_bytes"]
            and hashlib.sha256(raw).hexdigest() == result["sha256"], "artifact bytes/digest differ")
    data = json.loads(raw)
    require(data["schema_version"] == 1 and data["capture"] == value["capture"]
            and data["summary"] == value["summary"], "document and receipt disagree")
    require(str(path).encode() not in raw and str(path.parent).encode() not in raw,
            "output path leaked into artifact")
    require(data["privacy"]["credentials_and_payloads_omitted"]
            and data["privacy"]["raw_messages_omitted"], "missing privacy contract")
    return {"metadata": info, "sha256": result["sha256"], "document": data}


def refusals(run, base):
    parent = private(base, "refusals")
    final = parent / "existing.json"
    final.write_bytes(b"PRESERVE_ORIGINAL_DIAGNOSTIC_DESTINATION\n")
    final.chmod(0o600)
    before = {**metadata(final), "sha256": sha(final)}
    raw, _ = run.command("existing-destination", arguments(final), expected=1)
    value = receipt(raw)
    require(value["file"]["failure"]["publication"] == "not_created"
            and value["file"]["failure"]["kind"] == "already_exists", "existing file not refused")
    require(before == {**metadata(final), "sha256": sha(final)}, "existing output changed")
    link = base / "linked-parent"
    link.symlink_to(parent, target_is_directory=True)
    raw, _ = run.command("symlink-parent", arguments(link / "new.json"), expected=1)
    denied = receipt(raw)
    require(denied["file"]["failure"]["publication"] == "not_created"
            and not (parent / "new.json").exists(), "symlink parent accepted")
    require(not list(parent.glob("*.partial")), "refusal unexpectedly created staging")
    return {"existing": value, "symlink": denied, "original": before}


def local_capture(run, base):
    parent = private(base, "local")
    final = parent / "local.json"
    raw, _ = run.command("local-capture", arguments(final))
    value = receipt(raw)
    require(value["capture"] == "complete" and value["summary"]["complete"], "local capture incomplete")
    return {"receipt": value, **document(final, value)}


def log_captures(run, base):
    store = private(base, "source-logs")
    run.command("create-real-local-log", ["--log-directory", str(store), "doctor", "--json"])
    parent = private(base, "log-artifacts")
    final = parent / "valid.json"
    raw, _ = run.command("local-log-capture", arguments(final, "--logs-directory", str(store)))
    value = receipt(raw)
    captured = document(final, value)
    records = captured["document"]["logs"]["records"]
    require(records and any(row["event"]["kind"] == "started" for row in records), "actual startup absent")
    require(all("pid" not in row["event"] for row in records), "raw PID retained")
    raw_source = [json.loads(line) for p in store.glob("segment-*.ndjson")
                  for line in p.read_bytes().splitlines()]
    artifact = final.read_bytes()
    for row in raw_source:
        if "epoch" in row:
            require(row["epoch"].encode() not in artifact, "raw source epoch leaked")
    require(captured["document"]["logs"]["pages"][0]["live_status"] is None
            and value["capture"] == "complete", "disk-only live-status absence became false failure")
    # Preserve the original closed store, then use a distinct malformed fixture.
    support.archive_store(store, run.evidence / "source-logs-before-corruption")
    segment = next(iter(store.glob("segment-*.ndjson")))
    with segment.open("ab") as stream:
        stream.write(b'{"SENSITIVE_CORRUPT_INPUT":"DO_NOT_EXPORT"}\n')
    partial = parent / "corrupt.json"
    raw, _ = run.command("corrupt-log-capture", arguments(partial, "--logs-directory", str(store)), expected=1)
    bad = receipt(raw)
    require(bad["capture"] == "partial" and not bad["summary"]["complete"], "corrupt source became complete")
    bad_file = document(partial, bad)
    require(b"SENSITIVE_CORRUPT_INPUT" not in partial.read_bytes()
            and b"DO_NOT_EXPORT" not in partial.read_bytes(), "corrupt source text leaked")
    return {"valid_receipt": value, "valid_file": captured, "corrupt_receipt": bad,
            "corrupt_file": bad_file}


def file_failure(run, base):
    parent = private(base, "write-failure")
    final = parent / "final.json"
    child = run.spawn("write-limit-16", arguments(final), wrapper=LIMIT_WRAPPER)
    raw, _ = child["process"].communicate(timeout=10)
    (child["directory"] / "stdout.bin").write_bytes(raw)
    require(run.wait(child) == 1, "file write failure did not exit1")
    value = receipt(raw)
    failure = value["file"]["failure"]
    require(failure["kind"] == "io" and failure["errno"] == errno.EFBIG
            and failure["publication"] == "partial" and failure["written_bytes"] == 16,
            "actual EFBIG/partial byte receipt differs")
    partial = parent / failure["partial_name"]
    require(not final.exists() and partial.stat().st_size == 16 and sha(partial) == failure["sha256"],
            "actual partial bytes not preserved")
    return {"receipt": value, "partial": metadata(partial), "sha256": sha(partial)}


def blocked_output(run, base):
    parent = private(base, "blocked-output")
    final = parent / "final.json"
    read_fd, write_fd = os.pipe()
    try:
        capacity = fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
        require(capacity == 4096 and os.write(write_fd, b"X" * capacity) == capacity, "pipe not filled")
        child = run.spawn("blocked-receipt", arguments(final), stdout=write_fd)
        os.close(write_fd)
        write_fd = None
        started = time.monotonic()
        require(run.wait(child, timeout=12) == 1, "blocked receipt did not exit1")
        elapsed = time.monotonic() - started
        require(elapsed >= 4.5, "stdout deadline was not actually exercised")
        raw = os.read(read_fd, 65536)
        require(raw == b"X" * capacity and os.read(read_fd, 1) == b"", "receipt unexpectedly drained early")
        data = json.loads(final.read_bytes())
        require(data["capture"] == "complete" and data["summary"]["complete"],
                "stdout failure discarded actual completed artifact")
        return {"pipe_capacity": capacity, "no_drain_until_actual_wait": True,
                "elapsed_seconds": elapsed, "file": metadata(final), "sha256": sha(final),
                "capture": data["capture"]}
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def trace_gate(run, base, name, mode, number):
    parent = private(base, name)
    final = parent / "final.json"
    child = run.spawn(name, arguments(final), wrapper=TRACE_WRAPPER)
    process = child["process"]
    events, tids = [], {process.pid}
    held = None
    released = False
    signal_at = None
    returning = None
    actual_return = None
    stops = 0
    started = time.monotonic()
    syscalls = {"x86_64": {"prepare": 91, "publish": 74}, "aarch64": {"prepare": 52, "publish": 82}}
    desired = syscalls[os.uname().machine][mode]

    def event(kind, **fields):
        events.append({"event": kind, "monotonic_ns": time.monotonic_ns(), **fields})

    def resume(tid, signum=0):
        try:
            ptrace(SYSCALL, tid, 0, signum)
        except OSError as error:
            if error.errno != errno.ESRCH:
                raise

    def terminal(tid, status):
        tids.discard(tid)
        if tid == process.pid:
            process.returncode = os.waitstatus_to_exitcode(status)
            child["row"]["actual_waitpid_status"] = status
            child["row"]["wait_method"] = "actual ptrace waitpid(__WALL) reaped original child"
            event("actual_reaped", pid=tid, status=status, returncode=process.returncode)

    try:
        pid, status = os.waitpid(process.pid, 0)
        require(pid == process.pid and os.WIFSTOPPED(status) and os.WSTOPSIG(status) == signal.SIGSTOP,
                "owned TRACEME startup stop missing")
        ptrace(SETOPTIONS, pid, 0, TRACE_OPTIONS)
        event("tracing_started", pid=pid, identity=run.util.proc_identity(pid))
        resume(pid)
        while process.returncode is None:
            require(time.monotonic() - started < 30, "finite ptrace process observation expired")
            require(stops < 100000 and len(tids) <= 256, "ptrace observation bounds exceeded")
            if held is not None and signal_at is None:
                run.capture(child)
                identity = run.util.proc_identity(process.pid)
                require(identity == child["row"]["identity"], "tracee identity changed before signal")
                os.kill(process.pid, number)
                signal_at = time.monotonic()
                event("signal_sent", number=number, identity=identity)
            if held is not None and not released and signal_at is not None and time.monotonic() - signal_at >= 0.5:
                current = metadata(Path(f"/proc/{held['tid']}/fd/{held['fd']}").resolve(strict=True))
                require((current["device"], current["inode"]) == (held["file"]["device"], held["file"]["inode"]),
                        "held actual output FD changed")
                require(process.returncode is None and not final.exists(), "capture escaped its accepted held worker")
                event("still_joining_before_release", tid=held["tid"], file=current)
                returning = held["tid"]
                released = True
                event("release_same_syscall", tid=returning, fd=held["fd"], syscall=desired)
                resume(returning)
            try:
                tid, status = os.waitpid(-1, os.WNOHANG | WALL)
            except ChildProcessError:
                require(process.returncode is not None, "tracee vanished without actual wait receipt")
                break
            if tid == 0:
                time.sleep(0.002)
                continue
            if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                terminal(tid, status)
                continue
            require(os.WIFSTOPPED(status), "unexpected tracee wait status")
            stops += 1
            tids.add(tid)
            stopped = os.WSTOPSIG(status)
            if stopped == (signal.SIGTRAP | 0x80):
                buffer = ctypes.create_string_buffer(88)
                length = ptrace(GETINFO, tid, 88, ctypes.addressof(buffer))
                require(length >= 24, "kernel syscall-info API unavailable")
                op = buffer.raw[0]
                if op == 1:
                    arch = struct.unpack_from("I", buffer.raw, 4)[0]
                    require(arch == {"x86_64": 0xc000003e, "aarch64": 0xc00000b7}[os.uname().machine],
                            "tracee syscall architecture differs from selected native ABI")
                    syscall, fd = struct.unpack_from("QQ", buffer.raw, 24)
                    if held is None and syscall == desired:
                        descriptor = Path(f"/proc/{tid}/fd/{fd}")
                        try:
                            target = descriptor.resolve(strict=True)
                            info = metadata(target)
                        except FileNotFoundError:
                            target = None
                        if target is not None and target.parent == parent and target.name.startswith(".rustusb-diagnostic-") and target.name.endswith(".partial"):
                            require(info["mode"] == "0o600" and info["uid"] == os.getuid() and info["nlink"] == 1,
                                    "held artifact is not the caller's original private file")
                            require(info["bytes"] == 0 if mode == "prepare" else info["bytes"] > 0,
                                    "held output stage does not match actual byte progress")
                            held = {"tid": tid, "fd": fd, "syscall": syscall, "file": info,
                                    "syscall_info_arch": arch}
                            event("held_syscall_entry", **held)
                            save(child["directory"] / "held.json", held)
                            continue
                elif op == 2 and tid == returning:
                    actual_return = struct.unpack_from("q", buffer.raw, 24)[0]
                    event("same_syscall_returned", tid=tid, result=actual_return)
                    returning = None
                resume(tid)
            elif stopped == signal.SIGTRAP or stopped == signal.SIGSTOP:
                resume(tid)
            else:
                event("actual_signal_delivery", tid=tid, number=stopped)
                resume(tid, stopped)
        require(held is not None and released and actual_return == 0, "actual held syscall completion missing")
        require(any(e["event"] == "actual_signal_delivery" and e["number"] == number for e in events),
                "signal was not delivered to actual tracee")
        raw = process.stdout.read(65536)
        (child["directory"] / "stdout.bin").write_bytes(raw)
        run.wait(child)
        value = receipt(raw)
        if mode == "prepare":
            require(process.returncode == 1 and value["capture"] == "cancelled" and not final.exists(),
                    "prepare cancellation did not preserve valid cancelled partial")
            facts = document(final, value, published=False)
        else:
            require(process.returncode == 0 and value["capture"] == "complete" and value["cancel_observed"],
                    "accepted publication was incorrectly undone by signal")
            facts = document(final, value)
        return {"receipt": value, "file": facts, "held": held, "stops": stops,
                "actual_syscall_return": actual_return, "signal": number,
                "boundary": "removable syscall entry, before kernel execution; no register/result modification"}
    finally:
        if process.returncode is None:
            child["row"]["forced_cleanup"] = True
            run.collect(name + ":emergency-kill", lambda: os.kill(process.pid, signal.SIGKILL))
            for tid in list(tids):
                run.collect(name + ":emergency-resume", lambda tid=tid: resume(tid))
            deadline = time.monotonic() + 5
            while process.returncode is None and time.monotonic() < deadline:
                try:
                    tid, status = os.waitpid(-1, os.WNOHANG | WALL)
                except ChildProcessError:
                    break
                if tid == 0:
                    time.sleep(0.005)
                elif os.WIFEXITED(status) or os.WIFSIGNALED(status):
                    terminal(tid, status)
                elif os.WIFSTOPPED(status):
                    resume(tid, signal.SIGKILL)
        run.collect(name + ":save-gate-events", lambda: save(child["directory"] / "gate-events.json", events))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=["refusals", "local", "logs", "write-failure", "sigint", "sigterm", "publication-race", "backpressure"],
                        default=["refusals", "local", "logs", "write-failure", "sigint", "sigterm", "publication-race", "backpressure"])
    args = parser.parse_args()
    require(sys.platform == "linux" and os.getuid() != 0 and os.getuid() == os.geteuid()
            and os.uname().machine in {"x86_64", "aarch64"}, "ordinary non-setuid Linux native caller required")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    run = Run(args.binary.resolve(strict=True), evidence, support.support())
    run.env["TOKIO_WORKER_THREADS"] = "2"
    summary = {"schema_version": 1, "result": "FAIL", "cases": [], "collection_errors": run.collection_errors,
               "scope": "actual ordinary-UID private diagnostic subprocesses; no host USB/helper/module mutation",
               "command": sys.argv, "uid": os.getuid(), "python": sys.version, "host": list(os.uname()),
               "env_overrides": {"TOKIO_WORKER_THREADS": "2"}, "build": "externally selected prebuilt ELF; never builds"}
    base = None
    try:
        summary["sources_before"] = sources()
        summary["binary_before"] = support.binary_receipt(run.binary)
        cache = support.prepare_cache(Path.home())
        base = Path(tempfile.mkdtemp(prefix="rustusb-diagnostic-process-", dir=cache))
        base.chmod(0o700)
        summary["private_root"] = str(base)
        actions = {"refusals": lambda: refusals(run, base), "local": lambda: local_capture(run, base),
                   "logs": lambda: log_captures(run, base), "write-failure": lambda: file_failure(run, base),
                   "sigint": lambda: trace_gate(run, base, "prepare-sigint", "prepare", signal.SIGINT),
                   "sigterm": lambda: trace_gate(run, base, "prepare-sigterm", "prepare", signal.SIGTERM),
                   "publication-race": lambda: trace_gate(run, base, "accepted-publication-signal", "publish", signal.SIGTERM),
                   "backpressure": lambda: blocked_output(run, base)}
        for name in args.cases:
            row = {"name": name, "result": "FAIL", "started_monotonic_ns": time.monotonic_ns()}
            summary["cases"].append(row)
            try:
                row["facts"] = actions[name]()
                row["result"] = "PASS"
            except Exception as error:
                row["error"] = support.error_fact(error)
                summary.setdefault("error", row["error"])
                run.primary_checkpoint(summary)
                raise
            finally:
                row["completed_monotonic_ns"] = time.monotonic_ns()
                run.collect(name + ":save-case", lambda: save(evidence / (name + ".json"), row))
        summary["result"] = "PASS"
    except Exception as error:
        summary.setdefault("error", support.error_fact(error))
        run.primary_checkpoint(summary)
    finally:
        forced = run.collect("finalize", run.finalize)
        summary["forced_cleanup"] = forced
        summary["sources_after"] = run.collect("sources-after", sources)
        summary["binary_after"] = run.collect("binary-after", lambda: support.binary_receipt(run.binary))
        summary["source_unchanged"] = bool(summary["sources_after"]) and summary.get("sources_before") == summary["sources_after"]
        summary["binary_unchanged"] = bool(summary["binary_after"]) and summary.get("binary_before") == summary["binary_after"]
        if base is not None:
            summary["private_artifacts"] = str(base)
            stores = run.collect("private-artifacts:list", lambda: sorted(base.iterdir()))
            for store in stores or []:
                is_directory = run.collect(str(store) + ":classify", lambda store=store: store.is_dir() and not store.is_symlink())
                if is_directory:
                    run.collect(str(store) + ":archive", lambda store=store: run.archive_final(store, evidence / ("final-" + store.name)))
        summary["children"] = [child["row"] for child in run.children]
        if (forced or forced is None or any(child["row"]["forced_cleanup"] for child in run.children)
                or run.collection_errors or not summary["source_unchanged"] or not summary["binary_unchanged"]):
            summary["result"] = "FAIL"
        path, digest = run.final_summary(summary)
        print(json.dumps({"result": summary["result"], "summary": str(path), "sha256": digest,
                          "cases": [{"name": row["name"], "result": row["result"]} for row in summary["cases"]]}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
