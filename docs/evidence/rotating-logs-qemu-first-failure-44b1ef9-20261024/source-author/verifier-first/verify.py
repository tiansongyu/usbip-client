"""Raw lifecycle and process-log checks for the three-epoch QEMU experiment.

Checks are evidence predicates, never a count of USB cases. This module reads
saved originals; it neither queries a running guest nor calls another verifier.
"""
import hashlib
import json
import os
import re
import struct
from decimal import Decimal
from pathlib import Path


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
KEY = re.compile(r"native:usb-[0-9a-f]{64}\Z")
PROFILE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
FAILURES = {"invalid_input", "permission", "protocol", "io", "timeout", "state",
            "unsupported", "command", "unsafe_device", "cancelled", "persistence"}
ACTIONS = {"start", "denied", "stop", "admission", "attempt", "resource", "observe",
           "finish", "recovery", "discover"}
EVIDENCE = {"intent", "prepared", "local_enumeration", "export_ready", "import_reply_flushed",
            "closing", "released", "unconfirmed", "root_journal_proof"}
STATES = {"Discovered", "Available", "Exporting", "Connecting", "Connected", "Disconnecting", "Disconnected", "Error", "Recovering"}
CODES = {"discovery_completed", "operation_started", "operation_completed", "operation_failed",
         "native_attach", "native_detach", "native_export", "native_unexport", "network_ended",
         "resource_released", "diagnostic"}
TARGETS = {"rustusb", "rustusb_cli", "rustusb_daemon", "rustusb_service", "rustusb_native",
           "rustusb_linux", "rustusb_protocol", "rustusb_core", "rustusb_rust_backend", "rustusb_transport"}
PROOFS = {"native_import": "native_import_released", "native_stub": "native_stub_available",
          "native_export": "native_export_restored"}
DATA_LINK = {"protocol": "framed_v2", "interval_millis": 1000, "response_timeout_millis": 3000}


def require(value, message):
    if not value:
        raise RuntimeError("process logging evidence: " + message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def decode(raw):
    """Do not silently discard duplicate fields or accept NaN/Infinity."""
    def bad_constant(_):
        raise RuntimeError("process logging evidence: nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=bad_constant)


def shape(value, names, label):
    require(type(value) is dict and set(value) == set(names.split()), label + " exact fields")


def integer(value, maximum=(1 << 64) - 1, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def nullable(value, predicate):
    return value is None or predicate(value)


def identity(value):
    return type(value) is str and UUID.fullmatch(value) is not None


def cursor(value):
    if type(value) is not str or len(value) > 58 or value.count(":") != 1:
        return False
    segment, offset = value.split(":")
    return (identity(segment) and re.fullmatch(r"0|[1-9][0-9]*", offset) is not None
            and int(offset) <= 8 * 1024 * 1024)


def validate_status(value, stopped=False):
    shape(value, "healthy closing queued accepted written dropped oversized projection_errors overflowed last_written failure", "log status")
    require(all(type(value[k]) is bool for k in ("healthy", "closing", "overflowed")), "status booleans")
    require(integer(value["queued"], 256) and all(integer(value[k]) for k in
            ("accepted", "written", "dropped", "oversized", "projection_errors")), "bounded status counters")
    require(value["written"] <= value["accepted"] and nullable(value["last_written"], cursor)
            and nullable(value["failure"], lambda v: v in FAILURES), "consistent status fields")
    loss_free = (all(value[k] == 0 for k in ("dropped", "oversized", "projection_errors"))
                 and not value["overflowed"] and value["failure"] is None)
    require(not value["healthy"] or loss_free, "healthy status cannot hide loss")
    if stopped:
        require(value["closing"] and value["queued"] == 0, "Stopped means drained and closed admission")


def validate_record(value):
    """Independently validate the closed, payload-free on-disk schema."""
    shape(value, "schema_version epoch sequence elapsed_millis wall_unix_millis level event", "record")
    require(integer(value["schema_version"], 1, 1) and identity(value["epoch"])
            and integer(value["sequence"], minimum=1) and integer(value["elapsed_millis"])
            and nullable(value["wall_unix_millis"], integer)
            and value["level"] in {"trace", "debug", "info", "warn", "error"}, "record scalar types")
    event = value["event"]
    require(type(event) is dict and "kind" in event, "typed event object")
    kind = event["kind"]
    if kind == "started":
        shape(event, "kind pid uid", "Started")
        require(integer(event["pid"], (1 << 32) - 1, 1) and integer(event["uid"], (1 << 32) - 1), "actual process scalar types")
    elif kind == "stopped":
        shape(event, "kind status", "Stopped")
        validate_status(event["status"], stopped=True)
    elif kind == "audit":
        shape(event, "kind application_instance audit_sequence actor_uid profile job_id session_id action failure state evidence cleanup", "Audit")
        require(identity(event["application_instance"]) and integer(event["audit_sequence"], minimum=1)
                and integer(event["actor_uid"], (1 << 32) - 1), "audit identity/counters")
        require(nullable(event["profile"], lambda v: type(v) is str and PROFILE.fullmatch(v) is not None)
                and all(nullable(event[k], identity) for k in ("job_id", "session_id")), "audit optional identifiers")
        require(event["action"] in ACTIONS and nullable(event["failure"], lambda v: v in FAILURES)
                and nullable(event["state"], lambda v: v in STATES)
                and nullable(event["evidence"], lambda v: v in EVIDENCE)
                and nullable(event["cleanup"], lambda v: v in {"not_started", "verified", "unconfirmed"}), "audit closed categories")
    elif kind == "diagnostic":
        shape(event, "kind code target line failure errno fields omitted_fields", "Diagnostic")
        target = event["target"]
        require(type(target) is str and len(target) <= 128
                and re.fullmatch(r"[a-z0-9_:]+", target) is not None
                and any(target == root or target.startswith(root + "::") for root in TARGETS), "approved static target")
        require(event["code"] in CODES and nullable(event["line"], lambda v: integer(v, (1 << 32) - 1, 1))
                and nullable(event["failure"], lambda v: v in FAILURES)
                and nullable(event["errno"], lambda v: integer(v, 4095, 1))
                and integer(event["omitted_fields"], (1 << 32) - 1), "diagnostic closed fields")
        require(type(event["fields"]) is dict, "scalar field map")
        bounds = {"count": (1 << 32) - 1, "device_count": (1 << 32) - 1,
                  "session_count": (1 << 32) - 1, "interface": 255,
                  "bytes_to_peer": (1 << 64) - 1, "bytes_from_peer": (1 << 64) - 1,
                  "elapsed_millis": (1 << 64) - 1}
        for key, scalar in event["fields"].items():
            require((key in {"success", "network_closed"} and type(scalar) is bool)
                    or (key in bounds and integer(scalar, bounds[key])), "approved scalar field type/range")
    else:
        raise RuntimeError("process logging evidence: unknown event kind")


def parse_segment(raw):
    require(raw and len(raw) <= 8 * 1024 * 1024 and raw.endswith(b"\n"), "bounded complete original segment")
    lines = raw.splitlines(keepends=True)
    require(len(lines[0]) <= 256, "bounded complete header")
    header = decode(lines[0])
    shape(header, "schema_version segment epoch ordinal", "segment header")
    require(integer(header["schema_version"], 1, 1) and identity(header["segment"]) and identity(header["epoch"])
            and integer(header["ordinal"], minimum=1), "header identity/order")
    records, offsets, offset = [], [], len(lines[0])
    for line in lines[1:]:
        require(len(line) <= 2048 and line.endswith(b"\n"), "whole bounded record line")
        record = decode(line)
        validate_record(record)
        require(record["epoch"] == header["epoch"], "record belongs to actual segment epoch")
        offset += len(line)
        records.append(record)
        offsets.append(offset)
    require(all(a["sequence"] < b["sequence"] for a, b in zip(records, records[1:])),
            "within-segment admission sequence order")
    return {"header": header, "records": records, "offsets": offsets, "bytes": len(raw)}


def one(values, predicate, label):
    selected = [value for value in values if predicate(value)]
    require(len(selected) == 1, "one exact " + label)
    return selected[0]


def expected_proof(session, attempt):
    require(attempt["kind"] in PROOFS, "Native attempt kind")
    return {"resource_id": attempt["resource_id"], "kind": attempt["kind"],
            "device_key": session["device_key"], "owner_uid": 2000,
            "correlation": attempt["correlation"], "proof": PROOFS[attempt["kind"]]}


class Originals:
    def __init__(self, evidence):
        self.evidence = Path(evidence)
        self.files = {}
        self.checks = []

    def raw(self, role, name):
        require(role in {"server", "client"} and "/" not in name and name not in {".", ".."}, "fixed original path")
        path = self.evidence / (role + "-artifacts") / name
        data = path.read_bytes()
        require(len(data) <= 16 * 1024 * 1024, "bounded original " + name)
        self.files[role + "/" + name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        return data

    def data(self, role, name):
        return decode(self.raw(role, name))

    def check(self, condition, label):
        self.checks.append({"check": label, "passed": bool(condition)})
        require(condition, label)


def check_root_history(originals, role, epoch, current, root, held, jobs_finished=True, peer_close_ids=frozenset()):
    count = (4 if role == "server" else 2) * epoch
    originals.check(current["schema_version"] == 2 and root["schema_version"] == 2,
                    f"{role} epoch{epoch} actual private state/root schema2")
    originals.check(len(current["sessions"]) == count and len(current["jobs"]) == (epoch if role == "server" else 2 * epoch),
                    f"{role} epoch{epoch} exact retained nonempty histories")
    originals.check(len({s["id"] for s in current["sessions"]}) == count
                    and len({j["id"] for j in current["jobs"]}) == len(current["jobs"]), f"{role} unique retained identities")
    by_id = {s["id"]: s for s in current["sessions"]}
    originals.check(all(s["id"] in by_id and all(by_id[s["id"]][k] == s[k]
                    for k in ("id", "instance_id", "job_id", "profile", "role", "device_key", "principal"))
                    for s in held["sessions"]), f"{role} same actually held session identities retained")
    for original in held["sessions"]:
        ended = by_id[original["id"]]
        originals.check([{k: v for k, v in a.items() if k != "proof"} for a in ended["attempts"]]
                        == [{k: v for k, v in a.items() if k != "proof"} for a in original["attempts"]],
                        f"{role} exact originally held capability attempt {original['id']}")
        for old_attempt, new_attempt in zip(original["attempts"], ended["attempts"], strict=True):
            expected = PROOFS[old_attempt["kind"]]
            originals.check(new_attempt["proof"] in (None, expected) if old_attempt["proof"] is None
                            else new_attempt["proof"] == old_attempt["proof"] == expected,
                            f"{role} only exact root-proof addition permitted {original['id']}")
    terminals = [row["terminal"] for row in root["terminals"]]
    originals.check(root["records"] == root["exports"] == [] and len(terminals) == count,
                    f"{role} epoch{epoch} no root owners and exact retained terminal count")
    originals.check(all(row["server_id"] == ("qemu-server" if role == "client" else None) for row in root["terminals"]),
                    f"{role} epoch{epoch} retained proof has exact logical server scope")
    for session in current["sessions"]:
        allowed_error = {None, "io", "state"} if role == "client" and session["id"] in peer_close_ids else {None}
        originals.check(session["terminal"] is True and session["state"] == "Disconnected" and session["evidence"] == "released"
                        and session["cleanup"] == "verified" and session["operation_error"] in allowed_error
                        and len(session["attempts"]) == 1, f"{role} actual successful owner cleanup {session['id']}")
        attempt = session["attempts"][0]
        originals.check(terminals.count(expected_proof(session, attempt)) == 1,
                        f"{role} full original root proof {session['id']}")
        originals.check(any(j["id"] == session["job_id"] and j["instance_id"] == session["instance_id"]
                            and (not jobs_finished or j["state"] == "finished") and j["operator_uid"] == 1000 for j in current["jobs"]),
                        f"{role} real completed job relationship {session['id']}")


def validate_page(value):
    shape(value, "schema_version records next_after oldest_available gap partial_tail scanned_bytes more status", "log page")
    require(integer(value["schema_version"], 1, 1) and type(value["records"]) is list
            and len(value["records"]) <= 16 and integer(value["scanned_bytes"], 256 * 1024), "bounded page")
    require(all(type(value[k]) is bool for k in ("gap", "partial_tail", "more"))
            and all(nullable(value[k], cursor) for k in ("next_after", "oldest_available")), "page metadata types")
    if value["status"] is not None:
        validate_status(value["status"])
    for record in value["records"]:
        validate_record(record)
    require(sum(len(json.dumps(r, separators=(",", ":"), ensure_ascii=False).encode()) + 1
                for r in value["records"]) <= 32768, "page record bytes bounded")


def audit_matches(event, state, instance):
    if event["kind"] != "audit" or event["application_instance"] != instance:
        return False
    wanted = {k: v for k, v in event.items() if k not in {"kind", "application_instance", "audit_sequence"}}
    wanted["sequence"] = event["audit_sequence"]
    return state["audit"].count(wanted) == 1


def verify_store(originals, role, process, epoch, expected_uid, pid):
    """Check saved bytes and inode metadata, never the current guest filesystem."""
    prefix = f"logs-{process}-{epoch}-"
    meta = originals.data(role, prefix + "metadata.json")
    require(type(meta) is list and all(type(row) is dict for row in meta), "store metadata array")
    originals.check(len({r["path"] for r in meta}) == len(meta), role + " " + prefix + " unique metadata paths")
    directory_path = "/var/log/rustusb/" + process
    directory = one(meta, lambda r: r["path"] == directory_path, "actual log directory")
    for row in meta:
        shape(row, "path canonical capture mode kind uid gid bytes links device inode", "store metadata")
    originals.check(directory["kind"] == "directory" and directory["capture"] == "" and directory["canonical"] == directory_path
                    and directory["uid"] == directory["gid"] == expected_uid
                    and int(directory["mode"], 8) == 0o700 and integer(directory["inode"], minimum=1)
                    and integer(directory["device"]), role + " " + prefix + " private actual directory")
    by_name = {r["path"].rsplit("/", 1)[-1]: r for r in meta if r is not directory}
    names = set(by_name)
    originals.check({"writer.lock", "access.lock", "segment-00.ndjson"}.issubset(names)
                    and names <= {"writer.lock", "access.lock"} | {f"segment-{n:02}.ndjson" for n in range(4)},
                    role + " " + prefix + " fixed finite 4096-byte/four-slot store")
    originals.check(len(by_name) + 1 == len(meta), role + " " + prefix + " no duplicate basenames")
    segments = []
    for name, row in by_name.items():
        originals.check(row["path"] == row["canonical"] == directory_path + "/" + name
                        and row["kind"] in {"regular file", "regular empty file"}
                        and row["uid"] == row["gid"] == expected_uid and int(row["mode"], 8) == 0o600
                        and row["links"] == 1 and integer(row["inode"], minimum=1) and integer(row["device"]),
                        role + " " + prefix + name + " trusted original metadata")
        if name.endswith(".lock"):
            originals.check(row["bytes"] == 0 and row["capture"] == "",
                            role + " " + prefix + name + " actual lock remains empty")
        else:
            originals.check(row["capture"] == prefix + name, role + " " + prefix + name + " fixed capture")
            raw = originals.raw(role, row["capture"])
            originals.check(len(raw) == row["bytes"], role + " " + prefix + name + " exact captured bytes")
            segment = parse_segment(raw)
            originals.check(segment["bytes"] <= 4096, role + " " + prefix + name + " configured byte cap")
            segments.append((name, segment))
    originals.check({name for name, _ in segments} == {f"segment-{n:02}.ndjson" for n in range(len(segments))},
                    role + " " + prefix + " contiguous actual slots")
    segments.sort(reverse=True)
    headers = [s["header"] for _, s in segments]
    originals.check(len({h["segment"] for h in headers}) == len(headers)
                    and all(a["ordinal"] + 1 == b["ordinal"] for a, b in zip(headers, headers[1:])),
                    role + " " + prefix + " independent segment UUID/order")
    records = [r for _, s in segments for r in s["records"]]
    originals.check(len({(r["epoch"], r["sequence"]) for r in records}) == len(records)
                    and all(a["epoch"] != b["epoch"] or a["sequence"] < b["sequence"]
                            for a, b in zip(records, records[1:])), role + " " + prefix + " unique admission order")
    startup_name = "logs-helper-started.ndjson" if process == "helper" else prefix + "started.ndjson"
    startup = parse_segment(originals.raw(role, startup_name))
    start = one(startup["records"], lambda r: r["event"]["kind"] == "started", "actual Started record")
    originals.check(start["sequence"] == 1 and start["event"] == {"kind": "started", "pid": pid, "uid": expected_uid},
                    role + " " + prefix + " actual PID/UID startup before USB")
    current_epoch = start["epoch"]
    originals.check(headers[-1]["epoch"] == current_epoch, role + " " + prefix + " final active belongs to actual process")
    stopped = one(records, lambda r: r["epoch"] == current_epoch and r["event"]["kind"] == "stopped", "actual drained Stopped record")
    originals.check(records[-1] == stopped, role + " " + prefix + " actual final complete stopped record")
    status = stopped["event"]["status"]
    originals.check(status["written"] == status["accepted"] and status["queued"] == 0
                    and status["failure"] is None and status["oversized"] == status["projection_errors"] == 0
                    and status["overflowed"] is False, role + " " + prefix + " actual sync/drain counters")
    lost = role == "server" and process == "agent" and epoch == 2
    originals.check((status["dropped"] > 0 and not status["healthy"]) if lost
                    else (status["dropped"] == 0 and status["healthy"]), role + " " + prefix + " expected distinct loss result")
    return {"epoch": current_epoch, "records": records, "headers": headers, "status": status,
            "directory": directory, "files": by_name}


def proc_identity(originals, role, process, epoch, uid, binary_sha):
    name = process if epoch is None else f"{process}-{epoch}"
    status = originals.raw(role, "identity-" + name + ".txt").decode()
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    originals.check(fields["Uid"].split() == [str(uid)] * 4, role + " " + name + " actual OS UID")
    if uid:
        originals.check(all(int(fields[k], 16) == 0 for k in ("CapInh", "CapPrm", "CapEff", "CapAmb")),
                        role + " " + name + " no effective or inherited capabilities")
    originals.check(originals.raw(role, "executable-" + name + ".txt").decode().split()[0] == binary_sha,
                    role + " " + name + " actually executed selected ELF")
    pid = int(fields["Pid"].strip())
    originals.check(pid > 1, role + " " + name + " actual process identifier")
    return pid


def vhci(originals, name, used):
    lines = originals.raw("client", name).decode().splitlines()
    rows = [line.split() for line in lines if line.startswith(("hs ", "ss "))]
    originals.check(bool(rows) and all(int(row[2]) in {4, 6} for row in rows)
                    and sum(int(row[2]) == 6 for row in rows) == used, "actual VHCI USED/NULL " + name)
    return rows


def check_epoch(originals, epoch, keys, baseline):
    """Same exact capabilities perform I/O and later acquire matching root proof."""
    for role in ("server", "client"):
        held = originals.data(role, f"agent-held-{epoch}.json")
        root = originals.data(role, f"root-held-{epoch}.json")
        stopped = originals.data(role, f"agent-stopped-{epoch}.json")
        released = originals.data(role, f"root-stopped-{epoch}.json")
        active = [s for s in held["sessions"] if not s["terminal"]]
        wanted_roles = {"export", "incoming"} if role == "server" else {"import"}
        originals.check(len(active) == len(wanted_roles) * 2 and {s["role"] for s in active} == wanted_roles
                        and all(s["device_key"] in keys and len(s["attempts"]) == 1 for s in active),
                        f"{role} epoch{epoch} exact real active owners")
        instance = active[0]["instance_id"]
        originals.check(all(s["instance_id"] == instance for s in active), f"{role} epoch{epoch} one current application instance")
        for owned_role in wanted_roles:
            selected = [s for s in active if s["role"] == owned_role]
            originals.check(len(selected) == 2 and {s["device_key"] for s in selected} == set(keys),
                            f"{role} epoch{epoch} complete key pool for {owned_role}")
            for session in selected:
                expected_state, evidence = ("Available", "export_ready") if owned_role == "export" else (
                    "Connected", "import_reply_flushed" if owned_role == "incoming" else "local_enumeration")
                originals.check(session["state"] == expected_state and session["evidence"] == evidence
                                and session["operation_error"] is None, f"{role} epoch{epoch} role-specific actual connection evidence")
                attempt = session["attempts"][0]
                originals.check(attempt["correlation"]["instance_id"] == instance
                                and attempt["correlation"]["session_id"] == session["id"]
                                and attempt["resource_id"] is not None, f"{role} epoch{epoch} actual received correlation")
                if role == "server":
                    export = one(root["exports"], lambda e: e["registration"]["device_key"] == session["device_key"], "held export")
                    receipt = export if owned_role == "export" else export["stub"]
                    resource = receipt["lease_id"] if owned_role == "export" else receipt["channel_id"]
                else:
                    receipt = one(root["records"], lambda r: r["target"]["device_key"] == session["device_key"], "held import")
                    originals.check(receipt["target"]["server_id"] == "qemu-server", "actual helper-approved logical server")
                    resource = receipt["channel_id"]
                originals.check(resource == attempt["resource_id"] and receipt["correlation"] == attempt["correlation"],
                                f"{role} epoch{epoch} session maps to exact root capability")
                if "owner" in receipt:
                    originals.check(receipt["owner"]["uid"] == 2000 and receipt["owner"]["pid"] > 1
                                    and receipt["owner"]["starttime"] > 0, f"{role} epoch{epoch} kernel-observed broker owner")
        originals.check(root["schema_version"] == 2 and len(root["terminals"]) == (epoch - 1) * len(active),
                        f"{role} epoch{epoch} prior terminals remain without premature current proof")
        originals.check((root["records"] == [] and len(root["exports"]) == 2) if role == "server"
                        else (root["exports"] == [] and len(root["records"]) == 2), f"{role} exact held resource classes")
        peer_close_ids = {s["id"] for s in originals.data("client", "agent-held-2.json")["sessions"]
                          if s["role"] == "import" and not s["terminal"]} if role == "client" and epoch >= 2 else set()
        check_root_history(originals, role, epoch, stopped, released, held, peer_close_ids=peer_close_ids)
        current_ids = {s["id"] for s in active}
        health = [row for row in stopped["link_history"] if row["session_id"] in current_ids]
        originals.check(len(health) == len(active), f"{role} epoch{epoch} actual final health observations retained")
        for session in active:
            item = one(health, lambda h: h["session_id"] == session["id"] and h["instance_id"] == instance, "same-session health")
            originals.check(item["link"] is None if session["role"] == "export" else
                            (item["link"]["protocol"] == "framed_v2" and item["link"]["phase"] == "ended"
                             and item["link"]["end"] is not None), f"{role} epoch{epoch} actual data link versus export distinction")
    current = originals.data("server", f"host-held-{epoch}.json")
    exports = originals.data("server", f"root-held-{epoch}.json")["exports"]
    for index, key in enumerate(keys):
        registration = one(exports, lambda r: r["registration"]["device_key"] == key, "exact published physical key")["registration"]
        generation = registration["generation"]
        before = one(baseline, lambda r: r["bus"] == generation["bus_id"], "original physical baseline")
        originals.check(generation["vendor_id"] == (0x0627 if index == 0 else 0x0403)
                        and generation["product_id"] == int(before["product"], 16)
                        and generation["device_version"] == int(before["release"], 16)
                        and generation["device_number"] == int(before["devnum"])
                        and generation["bus_number"] == int(before["busnum"])
                        and generation["sysfs_path"] == before["path"]
                        and f"{generation['filesystem_device']}:{generation['inode']}" == before["generation"],
                        f"epoch{epoch} exact helper-approved physical key and original full generation")
        profile = {"configuration": int(before["configuration"]), "interfaces": [
            {"number": int(i["number"], 16), "alternate": int(i["alt"]), "class": int(i["class"], 16),
             "subclass": int(i["subclass"], 16), "protocol": int(i["protocol"], 16), "driver": i["driver"]}
            for i in before["interfaces"]]}
        originals.check(registration["original_profile"] == profile
                        and registration["original_device_driver"] == before["device_driver"],
                        f"epoch{epoch} exact privileged original restoration scope")
    dynamic = {"configuration", "num_interfaces", "configuration_bytes", "count_bytes", "interfaces", "device_driver", "usbip_status"}
    originals.check([{k: v for k, v in r.items() if k not in dynamic} for r in current]
                    == [{k: v for k, v in r.items() if k not in dynamic} for r in baseline], f"epoch{epoch} full original physical identity")
    for before, observed in zip(baseline, current, strict=True):
        originals.check(observed["device_driver"] == "usbip-host" and observed["usbip_status"] == 2
                        and str(observed["configuration"]) == str(before["configuration"])
                        and str(observed["num_interfaces"]) == str(before["num_interfaces"])
                        and observed["interfaces"] == [dict(i, driver="") for i in before["interfaces"]],
                        f"epoch{epoch} actual Native USED configuration and complete interface alternates")
    originals.check(originals.data("server", f"host-restored-{epoch}.json") == baseline,
                    f"epoch{epoch} complete original drivers/configuration/interfaces restored")
    receipts = originals.data("client", f"client-receipts-{epoch}.json")["records"]
    held_records = originals.data("client", f"root-held-{epoch}.json")["records"]
    originals.check(receipts == held_records and len(receipts) == 2, f"epoch{epoch} actual local enumeration receipts are original held capabilities")
    local = originals.data("client", f"local-identities-{epoch}.json")
    rows = vhci(originals, f"vhci-held-{epoch}.txt", 2)
    for index, key in enumerate(keys):
        record = one(receipts, lambda r: r["target"]["device_key"] == key, "actual client receipt")
        receipt = record["state"]["receipt"]
        session = receipt["session"]
        generation = session["local_device_generation"]
        name = "hid" if index == 0 else "serial"
        publication = one(exports, lambda r: r["registration"]["device_key"] == key, "server publication")["registration"]["published_device"]
        originals.check(record["target"]["remote"] == publication, f"epoch{epoch} authenticated remote metadata matches original publication {name}")
        originals.check(record["state"]["phase"] == "observed" and session["status"] == 6
                        and receipt["profile"] is not None and receipt["kernel_socket_inode"] > 0
                        and local[name] == session["local_bus_id"]
                        and local[name + "_generation"] == f"{generation['filesystem_device']}:{generation['inode']}",
                        f"epoch{epoch} actual kernel enumeration and local generation {name}")
        port = one(rows, lambda r: int(r[1]) == session["port"], "actual occupied VHCI port")
        originals.check(int(port[2]) == 6 and port[6] == session["local_bus_id"]
                        and int(port[4], 16) == session["remote_device_id"], f"epoch{epoch} exact real VHCI mapping {name}")
    vhci(originals, f"vhci-stopped-{epoch}.txt", 0)
    gone = originals.data("client", f"local-gone-{epoch}.json")
    originals.check(gone["hid"] == local["hid"] and gone["serial"] == local["serial"]
                    and gone["hid_exists"] is False and gone["serial_exists"] is False,
                    f"epoch{epoch} actual original local devices gone")
    rx = f"RUSTUSB-RX-{epoch}-0001:0123456789abcdef\n".encode()
    tx = f"RUSTUSB-TX-{epoch}-0001:fedcba9876543210\n".encode()
    originals.check(originals.raw("client", f"rx-{epoch}.bin") == rx
                    and originals.raw("client", f"tx-{epoch}.bin") == tx
                    and (originals.evidence / f"ftdi-host-injected-{epoch}.bin").read_bytes() == rx,
                    f"epoch{epoch} exact actual 35-byte FTDI data both directions")
    event_bytes = originals.raw("client", f"input-{epoch}.bin")
    originals.check(bool(event_bytes) and len(event_bytes) % 24 == 0, f"epoch{epoch} complete actual evdev records")
    events = [struct.unpack("<qqHHi", event_bytes[i:i + 24])[2:] for i in range(0, len(event_bytes), 24)]
    originals.check((1, epoch + 1, 1) in events and (1, epoch + 1, 0) in events,
                    f"epoch{epoch} real KEY{epoch} down/up")
    client_end = originals.data("client", f"agent-stopped-{epoch}.json")
    return {"epoch": epoch, "serial_bytes_each_direction": 35, "hid_key_code": epoch + 1,
            "client_resources": [r["channel_id"] for r in receipts],
            "client_generations": [r["state"]["receipt"]["session"]["local_device_generation"] for r in receipts],
            "actual_client_operation_errors": {s["id"]: s["operation_error"] for s in client_end["sessions"]},
            "peer_close_scope": "Only the exact epoch2 original two imports may retain Io/State from peer TERM or helper invalidation; all cleanup remains actual Verified/Disconnected with exact Root proof"}


def lines(originals, role, name):
    return [decode(line) for line in originals.raw(role, name).splitlines()]


def uptime(value):
    word = value.decode().split()[0] if isinstance(value, bytes) else value.split()[0]
    require(re.fullmatch(r"[0-9]+\.[0-9]+", word) is not None, "actual monotonic uptime")
    return Decimal(word)


def saved_proc(originals, role, label, wanted_uid, wanted_sha):
    status = originals.raw(role, f"proc-{label}.status").decode()
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    text = originals.raw(role, f"proc-{label}.stat").decode()
    split = text.rfind(") ")
    require(split > 0, "complete saved proc stat")
    pid, starttime = int(text.split(" ", 1)[0]), int(text[split + 2:].split()[19])
    originals.check(int(fields["Pid"]) == pid and fields["Uid"].split() == [str(wanted_uid)] * 4
                    and starttime > 0, role + " " + label + " same saved OS process identity")
    originals.check(originals.raw(role, f"proc-{label}.exe.sha256").decode().split()[0] == wanted_sha,
                    role + " " + label + " actual executable bytes")
    fds = lines(originals, role, f"proc-{label}-fds.jsonl")
    originals.check(not any(row["target"] == "/dev/ttyS2" for row in fds),
                    role + " " + label + " does not inherit guest host-control channel")
    return {"pid": pid, "starttime": starttime,
            "fds": fds,
            "uptime": uptime(originals.raw(role, f"proc-{label}.uptime"))}


def check_flood(originals, mark, count):
    before = originals.data("server", f"flood-{mark}-before.json")
    after = originals.data("server", f"flood-{mark}-after.json")
    receipts = lines(originals, "server", f"flood-{mark}-receipts.jsonl")
    originals.check(len(receipts) == count and [r["index"] for r in receipts] == list(range(1, count + 1)),
                    mark + " bounded actual refusal count")
    def owners(state):
        return {"jobs": state["jobs"], "requests": state["requests"],
                "sessions": [{k: v for k, v in s.items() if k != "metrics"} for s in state["sessions"]]}
    originals.check(owners(before) == owners(after),
                    mark + " denied inputs cause no resource/job/session attempt")
    previous = before["sequence"]
    for row in receipts:
        response, audit = row["response"], row["audit"]
        originals.check(row["wait"] == 1 and response["schema_version"] == 1 and response["journal_healthy"] is True
                        and response["response"] == {"result": "error", "code": "permission"},
                        mark + " actual denied IPC result " + str(row["index"]))
        originals.check(row["sequence"] > previous and previous < audit["sequence"] <= row["sequence"]
                        and audit["actor_uid"] == 1000 and audit["profile"] is None and audit["action"] == "denied"
                        and audit["failure"] == "permission" and audit["job_id"] is None and audit["session_id"] is None
                        and after["audit"].count(audit) == 1, mark + " actual committed audit " + str(row["index"]))
        previous = row["sequence"]
    originals.check(after["sequence"] >= previous, mark + " saved published WAL includes final real denial")
    return {"requests": count, "first_sequence": receipts[0]["sequence"], "last_sequence": previous}


def check_lock_fault(originals, summary, baseline):
    stages = ("before", "before-stop", "after-clean", "before-release")
    snapshots = [originals.data("server", f"gate-{stage}.json") for stage in stages]
    first = snapshots[0]
    originals.check(identity(first["nonce"]) and integer(first["pid"], minimum=2), "real bounded gate nonce and PID")
    lock_dev, lock_ino, lock_uid, lock_mode, lock_links = first["lock"].split(":")
    originals.check(int(lock_uid) == 2000 and int(lock_mode, 8) == 0o600 and int(lock_links) == 1,
                    "gate opens original broker-owned private access lock")
    processes = []
    agent_processes = []
    prior_time = Decimal(0)
    for stage, item in zip(stages, snapshots, strict=True):
        originals.check(all(item[k] == first[k] for k in ("nonce", "pid", "fd", "lock", "segment")),
                        stage + " same independent flock owner and original storage identities")
        gate = saved_proc(originals, "server", "gate-" + stage, 0, summary["payload_sha256"]["server"]["usr/bin/flock"])
        agent = saved_proc(originals, "server", "agent-" + stage, 2000, summary["binary_sha256"]["rustusb"])
        processes.append(gate)
        agent_processes.append(agent)
        originals.check(gate["pid"] == item["pid"] and gate["pid"] != agent["pid"], stage + " independently alive lock owner and application")
        held_fd = one(gate["fds"], lambda f: f["fd"] == item["fd"], "actual gate descriptor")
        stat_fields = held_fd["stat"].split(":", 4)
        originals.check(held_fd["target"] == "/var/log/rustusb/agent/access.lock"
                        and stat_fields[:4] == [lock_dev, lock_ino, "2000", "600"]
                        and stat_fields[4] in {"regular file", "regular empty file"}, stage + " FD resolves to exact access inode")
        locks = [line.split() for line in originals.raw("server", f"gate-{stage}-proc-locks.txt").decode().splitlines()]
        actual = [row for row in locks if len(row) >= 8 and row[1:5] == ["FLOCK", "ADVISORY", "WRITE", str(gate["pid"])]]
        def same_inode(row):
            major, minor, inode = row[5].split(":")
            return (int(major, 16) == os.major(int(lock_dev)) and int(minor, 16) == os.minor(int(lock_dev))
                    and int(inode) == int(lock_ino) and row[6:] == ["0", "EOF"])
        originals.check(len([row for row in actual if same_inode(row)]) == 1, stage + " actual exclusive flock at exact inode")
        now = uptime(item["uptime"])
        originals.check(prior_time <= gate["uptime"] <= now and agent["uptime"] <= now,
                        stage + " monotonic saved process and lock samples")
        prior_time = now
        originals.check(originals.raw("server", f"gate-{stage}-segment.ndjson")
                        == originals.raw("server", "gate-before-segment.ndjson"), stage + " actual log bytes cannot advance under lock")
    originals.check(len({(p["pid"], p["starttime"]) for p in processes}) == 1
                    and len({(p["pid"], p["starttime"]) for p in agent_processes}) == 1,
                    "gate and application are each the same process generation throughout obstruction")
    original_agent = saved_proc(originals, "server", "agent-2", 2000, summary["binary_sha256"]["rustusb"])
    originals.check((agent_processes[0]["pid"], agent_processes[0]["starttime"])
                    == (original_agent["pid"], original_agent["starttime"]), "blocked owner is original actual epoch2 agent")
    threads = lines(originals, "server", "threads-busy.jsonl")
    flock_threads = []
    for item in threads:
        call = item["syscall"].split()
        if len(call) >= 3 and call[0] == "73":
            originals.check(item["fd"] == str(int(call[1], 16)) and item["target"] == "/var/log/rustusb/agent/access.lock"
                            and item["stat"] == first["lock"], "actual blocked worker descriptor matches original lock")
            flock_threads.append((item, int(call[2], 16)))
    writers = [item for item, op in flock_threads if item["comm"] == "rustusb-log-wri" and op == 2]
    readers = [item for item, op in flock_threads if item["comm"].startswith("rustusb-log-rea") and op == 1]
    originals.check(len(writers) == 1 and len(readers) == 2 and len({i["tid"] for i in writers + readers}) == 3,
                    "actual separate writer flockEX and two reader flockSH syscalls remain blocked")
    busy_seen, reader_ids = [], []
    for number in range(1, 6):
        receipt = saved_proc(originals, "server", f"reader-{number}", 1000, summary["binary_sha256"]["rustusb"])
        reader_ids.append(receipt["pid"])
        originals.check(originals.raw("server", f"read-busy-{number}.pid").strip() == str(receipt["pid"]).encode(),
                        "actual observer PID receipt " + str(number))
        for row in lines(originals, "server", f"read-busy-{number}.stdout"):
            shape(row, "schema_version event", "Busy observer event")
            originals.check(row == {"schema_version": 1, "event": "logs_busy"}, "only actual Busy observations escape held reads")
            busy_seen.append(number)
    originals.check(bool(busy_seen) and len(set(reader_ids)) == 5, "real finite-reader saturation reports Busy without claiming arrival order")
    waits = [line.split() for line in originals.raw("server", "read-busy-waits.txt").decode().splitlines()]
    originals.check(len(waits) == 5 and {int(row[0]) for row in waits} == set(reader_ids)
                    and all(row[1] == "0" for row in waits), "all actual observers cancel and are really reaped")
    held = originals.data("server", "agent-held-2.json")
    clean = originals.data("server", "agent-clean-held.json")
    root = originals.data("server", "root-clean-held.json")
    check_root_history(originals, "server", 2, clean, root, held)
    originals.check(originals.data("server", "host-clean-held.json") == baseline,
                    "saved full original driver/configuration baseline while lock owner and agent remain alive")
    client_clean = originals.data("client", "agent-eof-held.json")
    client_root = originals.data("client", "root-eof-held.json")
    client_held = originals.data("client", "agent-held-2.json")
    peer_close_ids = {s["id"] for s in client_held["sessions"] if s["role"] == "import" and not s["terminal"]}
    check_root_history(originals, "client", 2, client_clean, client_root, client_held,
                       jobs_finished=False, peer_close_ids=peer_close_ids)
    vhci(originals, "vhci-eof-held.txt", 0)
    local = originals.data("client", "local-identities-2.json")
    gone = originals.data("client", "local-gone-eof-held.json")
    originals.check(gone["hid"] == local["hid"] and gone["serial"] == local["serial"]
                    and gone["hid_exists"] is False and gone["serial_exists"] is False,
                    "client original two generations actually gone before gate release")
    term_time = uptime(originals.raw("server", "term-sent.uptime"))
    request_time = uptime(originals.raw("server", "gate-release-request.uptime"))
    reaped_time = uptime(originals.raw("server", "gate-reaped.uptime"))
    control = originals.data("server", "agent-control-after-clean.json")
    originals.check(control["pid"] == original_agent["pid"] and control["control_absent"] is True
                    and term_time <= uptime(control["uptime"]) <= uptime(snapshots[2]["uptime"])
                    and originals.raw("server", "agent-main-after-clean.syscall").split()[0] == b"202"
                    and bool(originals.raw("server", "agent-main-after-clean.wchan").strip()),
                    "original agent remains alive in observed futex after actual service socket removal and complete USB cleanup")
    originals.check(uptime(snapshots[1]["uptime"]) <= term_time <= uptime(snapshots[2]["uptime"])
                    <= uptime(snapshots[3]["uptime"]) <= request_time <= reaped_time
                    and reaped_time - uptime(first["uptime"]) <= 180, "actual TERM, cleaned-but-live, same gate release and reap order")
    originals.check(originals.raw("server", "gate-process.stdout").decode().splitlines()
                    == ["LOG_GATE_HELD nonce=" + first["nonce"], "LOG_GATE_RELEASED nonce=" + first["nonce"]]
                    and originals.raw("server", "gate-process.stderr") == b""
                    and originals.raw("server", "wait-gate.txt") == b"0\n", "sole matching release nonce and actual successful gate wait")
    controls = [decode(line) for line in (originals.evidence / "host-control.jsonl").read_bytes().splitlines()]
    marks = [row["mark"] for row in controls]
    originals.check(marks.count("check-closed2") == marks.count("release-gate") == 1
                    and marks.index("check-closed2") < marks.index("release-gate"), "host closes original client before releasing logger gate")
    server_log = (originals.evidence / "server-evidence.log").read_text()
    client_log = (originals.evidence / "client-evidence.log").read_text()
    ordered = ["LOG_FLOOD_COMMITTED role=server mark=blocked count=512\n",
               f"LOG_AGENT_TERM pid={original_agent['pid']}\n", "LOG_ROOT_CLEAN_GATE_HELD\n", "LOG_SERVER_STOPPED epoch=2\n"]
    originals.check(all(server_log.count(mark) == 1 for mark in ordered)
                    and [server_log.index(mark) for mark in ordered] == sorted(server_log.index(mark) for mark in ordered),
                    "actual server progression separates original loss, TERM, cleaned-under-lock and final wait")
    originals.check(client_log.count("LOG_CLIENT_EOF_GATE_HELD\n") == 1
                    and client_log.index("LOG_CLIENT_EOF_GATE_HELD\n") < client_log.index("LOG_CLIENT_STOPPED epoch=2\n"),
                    "actual client reports EOF retirement before its own TERM and host release condition")
    return {"nonce": first["nonce"], "gate_pid": first["pid"], "agent_pid": original_agent["pid"],
            "actual_blocked_readers": 2, "actual_blocked_writer": 1,
            "busy_observers": sorted(set(busy_seen)), "held_seconds": str(reaped_time - uptime(first["uptime"])),
            "join_phase_basis": "saved service endpoint removal, complete original USB retirement and main-thread futex, correlated with the fixed synchronous outer-main ownership order; futex alone does not identify a JoinHandle",
            "scope": "removable independent flock obstruction; no permanent filesystem cancellation or kernel-active USB claim"}


def check_configuration(originals, keys):
    public = {name: (originals.evidence / (name + "-public.der")).read_bytes() for name in ("ca", "server", "client")}
    pin = hashlib.sha256(public["client"]).hexdigest()
    for role in ("server", "client"):
        profiles = [{"id": "server", "backend": "native", "config": "/etc/rustusb/server.json"}] if role == "server" else [
            {"id": name, "backend": "client", "config": f"/etc/rustusb/client-{name}.json"} for name in ("hid", "serial")]
        manifest = {"schema_version": 1, "broker_uid": 2000, "helper_socket": "/run/rustusb/control.sock",
                    "control_directory": "/run/agent", "data_directory": "/var/lib/rustusb/agent", "profiles": profiles,
                    "operators": [{"uid": 1000, "profiles": [p["id"] for p in profiles], "actions": ["read", "start", "stop", "discover", "audit"]}]}
        originals.check(originals.data(role, "agent-manifest.json") == manifest,
                        role + " root-owned profile/action/OS identity authorization policy")
        policy = originals.data(role, "helper-policy.json")
        originals.check(policy["allowed_uids"] == [1000] and policy["secure_broker_uid"] == 2000
                        and policy["trusted_network"] is False and policy["export_device_ids"] == [] and policy["peers"] == [],
                        role + " actual dedicated broker and legacy helper isolation")
        originals.check(policy["secure_native_device_ids"] == [k.split(":", 1)[1] for k in keys]
                        if role == "server" else policy["secure_imports"] == [{"server_id": "qemu-server", "device_keys": keys}],
                        role + " actual root secure device pool")
        expected = {"agent.json": manifest, "policy.json": policy}
        if role == "server":
            expected["server.json"] = {"schema_version": 2, "data_link": DATA_LINK, "broker_uid": 2000,
                "listen": "192.0.2.1:3241", "devices": keys,
                "principals": [{"id": "qemu-client", "pins": [pin], "devices": keys}],
                "ca_certificates": ["/etc/rustusb/ca.der"], "certificate_chain": ["/etc/rustusb/server.der"],
                "private_key": "/etc/rustusb/server-key.der"}
        else:
            for name, key in zip(("hid", "serial"), keys, strict=True):
                expected[f"client-{name}.json"] = {"schema_version": 2, "data_link": DATA_LINK, "broker_uid": 2000,
                    "server_id": "qemu-server", "endpoint": {"host": "192.0.2.1", "port": 3241},
                    "server_name": "rustusb-qemu.test", "device": key, "ca_certificates": ["/etc/rustusb/ca.der"],
                    "certificate_chain": ["/etc/rustusb/client.der"], "private_key": "/etc/rustusb/client-key.der"}
        directories = {"/", "/etc", "/etc/rustusb", "/run", "/run/agent", "/var", "/var/lib", "/var/lib/rustusb", "/var/lib/rustusb/agent"}
        public_paths = {"/etc/rustusb/" + name for name in expected} | {"/etc/rustusb/" + name + ".der" for name in public} | {"/etc/rustusb/client-pin.txt"}
        private = "/etc/rustusb/" + role + "-key.der"
        for epoch in (1, 2, 3):
            prefix = f"actual-config-{epoch}-"
            for name, expected_value in expected.items():
                originals.check(originals.data(role, prefix + name) == expected_value
                                and originals.raw(role, prefix + name) == originals.raw(role, "actual-config-1-" + name),
                                f"{role} epoch{epoch} exact unchanged actual {name}")
            originals.check(originals.raw(role, prefix + "client-pin.txt") == (pin + "\n").encode(),
                            f"{role} epoch{epoch} exact public client certificate pin")
            for name, raw in public.items():
                originals.check(originals.raw(role, prefix + name + ".der") == raw,
                                f"{role} epoch{epoch} actual public {name} certificate bytes")
            metadata = originals.data(role, f"config-metadata-{epoch}.json")
            by_path = {row["path"]: row for row in metadata}
            originals.check(len(metadata) == len(by_path) and set(by_path) == directories | public_paths | {private},
                            f"{role} epoch{epoch} exact fixed trusted-file permission coverage")
            for path, row in by_path.items():
                originals.check(row["canonical"] == path and row["inode"] > 0 and row["device"] >= 0,
                                f"{role} epoch{epoch} actual nonsymlink identity {path}")
                mode = int(row["mode"], 8)
                if path in directories:
                    broker = path in {"/run/agent", "/var/lib/rustusb/agent"}
                    originals.check(row["kind"] == "directory" and row["captured_file"] is None
                                    and row["uid"] == row["gid"] == (2000 if broker else 0)
                                    and (mode == (0o711 if path == "/run/agent" else 0o700) if broker else mode & 0o022 == 0),
                                    f"{role} epoch{epoch} actual protected ancestor {path}")
                elif path == private:
                    originals.check(row["kind"] == "private_key_metadata_only" and row["uid"] == row["gid"] == 2000
                                    and mode == 0o600 and row["links"] == 1 and row["captured_file"] is None and row["bytes"] > 0,
                                    f"{role} epoch{epoch} only own private key metadata")
                else:
                    name = prefix + path.rsplit("/", 1)[1]
                    originals.check(row["kind"] == "public_file" and row["uid"] == row["gid"] == 0
                                    and mode == 0o644 and row["links"] == 1 and row["captured_file"] == name
                                    and row["bytes"] == len(originals.raw(role, name)), f"{role} epoch{epoch} root-controlled public input {path}")
        originals.check(not any(p.name.endswith("-key.der") for p in (originals.evidence / (role + "-artifacts")).iterdir()),
                        role + " private DER bytes never archived")
        ancestors = lines(originals, role, "logs-ancestors.jsonl")
        originals.check({r["path"] for r in ancestors} == {"/", "/var", "/var/log", "/var/log/rustusb"}
                        and len(ancestors) == 4 and all(r["path"] == r["canonical"] and r["uid"] == r["gid"] == 0
                        and int(r["mode"], 8) & 0o022 == 0 for r in ancestors), role + " actual protected logging ancestors")
    return {"public_certificate_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in public.items()},
            "schema": 2, "data_link": DATA_LINK, "private_key_bytes_captured": False}


def check_pages_and_audit(originals, stores):
    totals = {}
    for role in ("server", "client"):
        first_epoch = originals.data(role, "agent-stopped-1.json")
        for epoch in (1, 2, 3):
            state = originals.data(role, f"agent-stopped-{epoch}.json")
            current = originals.data(role, f"agent-held-{epoch}.json")
            instance = next(s["instance_id"] for s in current["sessions"] if not s["terminal"])
            for record in stores[(role, "agent", epoch)]["records"]:
                if record["event"]["kind"] == "audit":
                    event = record["event"]
                    # A retained sealed segment may belong to a prior process.
                    source_instance = next((s["instance_id"] for s in state["sessions"] if s["instance_id"] == event["application_instance"]), None)
                    originals.check(source_instance is not None and audit_matches(event, state, source_instance),
                                    role + " actual process audit is exact already-committed WAL projection")
        originals.check(originals.raw(role, "root-before-log-refusals.json") == originals.raw(role, "root-after-log-refusals.json")
                        and originals.raw(role, "resources-before-log-refusals.json") == originals.raw(role, "resources-after-log-refusals.json"),
                        role + " actual read refusals leave jobs/sessions/requests and root resources unchanged")
        for reason, status in (("uid", 1), ("profile", 1), ("path", 2)):
            originals.check(originals.raw(role, "logs-denied-" + reason + ".wait") == f"{status}\n".encode()
                            and originals.raw(role, "logs-denied-" + reason + ".stdout") == b""
                            and bool(originals.raw(role, "logs-denied-" + reason + ".stderr")),
                            role + " actual refused read " + reason)
        labels = {"server-normal": "server", "server-rotated": "server"} if role == "server" else {"hid-normal": "hid", "serial-normal": "serial"}
        for label, profile in labels.items():
            paths = list((originals.evidence / (role + "-artifacts")).glob("read-" + label + "-*.json"))
            numbers = sorted(int(p.stem.rsplit("-", 1)[1]) for p in paths)
            originals.check(bool(numbers) and numbers == list(range(1, len(numbers) + 1)) and len(numbers) <= 80,
                            role + " " + label + " actual finite pagination")
            previous, seen, profile_hits = None, set(), 0
            for number in numbers:
                wrapper = originals.data(role, f"read-{label}-{number}.json")
                shape(wrapper, "schema_version event page", "CLI page wrapper")
                originals.check(wrapper["schema_version"] == 1 and wrapper["event"] == "logs", "machine stdout contains only typed log page")
                page = wrapper["page"]
                validate_page(page)
                originals.check(not page["partial_tail"] and page["status"] is not None and page["status"]["healthy"],
                                role + " " + label + " actual live healthy normal reader status")
                if number < numbers[-1]:
                    originals.check(page["more"] is True and page["next_after"] is not None and page["next_after"] != previous,
                                    role + " " + label + " scan has real forward cursor")
                else:
                    originals.check(page["more"] is False, role + " " + label + " actual bounded history completed")
                previous = page["next_after"]
                for record in page["records"]:
                    event = record["event"]
                    originals.check(event["kind"] == "audit" and (event["profile"] == profile or
                                    (event["profile"] is None and event["action"] == "denied" and event["actor_uid"] == 1000)),
                                    role + " " + label + " only exact typed profile/self-unscoped denial")
                    originals.check((record["epoch"], record["sequence"]) not in seen, role + " " + label + " no duplicate pagination record")
                    seen.add((record["epoch"], record["sequence"]))
                    originals.check(audit_matches(event, first_epoch, event["application_instance"]), role + " " + label + " actual page maps to same-epoch retained committed audit")
                    profile_hits += int(event["profile"] == profile)
            if label.endswith("normal"):
                originals.check(profile_hits > 0, role + " " + label + " positive exact-profile evidence, not an empty refusal")
            totals[role + "/" + label] = {"pages": len(numbers), "records": len(seen), "exact_profile_records": profile_hits}
    return totals


def verify(evidence, summary):
    """Check completed captured guests; final host exit/source guards stay in run.py."""
    originals = Originals(evidence)
    originals.check(summary["backend"] == "native" and summary["family"] == "ipv4"
                    and summary["build_skipped"] is False, "exact installed Native IPv4 all-features locked-build scope")
    originals.check(summary["git_status_before"] == "" and re.fullmatch(r"[0-9a-f]{40}", summary["head"]) is not None
                    and bool(summary["source_inputs"]), "actual clean committed frozen source selection")
    for name, row in summary["source_inputs"].items():
        path = originals.evidence / "source" / name
        originals.check(path.stat().st_size == row["bytes"] and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"],
                        "actual frozen selected source " + name)
    originals.check(hashlib.sha256((originals.evidence / "kernel.config").read_bytes()).hexdigest()
                    == summary["kernel_config_sha256"], "actual selected kernel configuration bytes")
    keys = originals.data("server", "keys.json")
    originals.check(type(keys) is list and len(keys) == len(set(keys)) == 2
                    and all(type(key) is str and KEY.fullmatch(key) for key in keys)
                    and originals.data("client", "keys.json") == keys, "same two exact qualified Native device keys")
    baseline = originals.data("server", "host-before.json")
    originals.check(len(baseline) == 2 and {r["vendor"] for r in baseline} == {"0627", "0403"}
                    and {i["driver"] for r in baseline for i in r["interfaces"]} == {"usbhid", "ftdi_sio"},
                    "actual complete original HID and FTDI driver baselines")
    epochs = [check_epoch(originals, epoch, keys, baseline) for epoch in (1, 2, 3)]
    resources = [resource for epoch in epochs for resource in epoch["client_resources"]]
    generations = [json.dumps(generation, sort_keys=True) for epoch in epochs for generation in epoch["client_generations"]]
    originals.check(len(set(resources)) == 6 and len(set(generations)) == 6,
                    "all six actual imported capabilities/generations are fresh over real restart and reuse")
    tx = b"".join(f"RUSTUSB-TX-{epoch}-0001:fedcba9876543210\n".encode() for epoch in (1, 2, 3))
    originals.check((originals.evidence / "ftdi-host-rx.bin").read_bytes() == tx and len(tx) == 105,
                    "actual independent host receives all 105 ordered FTDI bytes")
    stores = {}
    for role in ("server", "client"):
        helper_pid = proc_identity(originals, role, "helper", None, 0, summary["binary_sha256"]["rustusb-helper"])
        stores[(role, "helper", "final")] = verify_store(originals, role, "helper", "final", 0, helper_pid)
        originals.check(originals.raw(role, "wait-helper.txt") == b"0\n", role + " actual original helper TERM and reap0")
        processes = []
        for epoch in (1, 2, 3):
            pid = proc_identity(originals, role, "agent", epoch, 2000, summary["binary_sha256"]["rustusb"])
            process = saved_proc(originals, role, f"agent-{epoch}", 2000, summary["binary_sha256"]["rustusb"])
            originals.check(pid == process["pid"], f"{role} epoch{epoch} independently saved actual process identity")
            processes.append((pid, process["starttime"]))
            root = originals.data(role, f"root-held-{epoch}.json")
            capabilities = root["exports"] if role == "server" else root["records"]
            originals.check(all(r["owner"]["pid"] == pid and r["owner"]["uid"] == 2000
                                and r["owner"]["starttime"] == process["starttime"] for r in capabilities),
                            f"{role} epoch{epoch} helper receipts pin original actual broker generation")
            stores[(role, "agent", epoch)] = verify_store(originals, role, "agent", epoch, 2000, pid)
            expected_exit = 1 if role == "server" and epoch == 2 else 0
            originals.check(originals.raw(role, f"wait-agent-{epoch}.txt") == f"{expected_exit}\n".encode(),
                            f"{role} epoch{epoch} actual parent wait records expected final status")
            errors = originals.raw(role, f"agent-{epoch}.log")
            if expected_exit:
                originals.check(errors == b"logging failed after the requested operation; inspect its result before retrying: logging lost records; the requested operation may have completed\n",
                                "only final logging loss failure after successful real USB cleanup")
            else:
                originals.check(errors == b"", f"{role} epoch{epoch} file-only logger does not tee to stderr")
            originals.check(originals.raw(role, f"agent-{epoch}.stdout") == b"", f"{role} epoch{epoch} logger never pollutes machine stdout")
        originals.check(len(set(processes)) == 3 and len({stores[(role, "agent", epoch)]["epoch"] for epoch in (1, 2, 3)}) == 3,
                        role + " three distinct actual process and log epochs")
        normal = stores[(role, "agent", 1)]
        if role == "server":
            startup = parse_segment(originals.raw(role, "logs-agent-1-started.ndjson"))
            originals.check(normal["headers"][-1]["ordinal"] > startup["header"]["ordinal"]
                            and len(normal["headers"]) == 4, "normal actual same-process byte rotation reaches bounded retention")
        for epoch in (2, 3):
            prior = stores[(role, "agent", epoch - 1)]
            startup = parse_segment(originals.raw(role, f"logs-agent-{epoch}-started.ndjson"))
            originals.check(startup["header"]["ordinal"] == prior["headers"][-1]["ordinal"] + 1,
                            f"{role} epoch{epoch} actual restart seals previous active and advances ordinal")
        log = (originals.evidence / (role + "-evidence.log")).read_text()
        kernel = (originals.evidence / (role + "-serial.log")).read_text(errors="replace")
        originals.check(log.count("RUSTUSB_LOGGING_RESULT: PASS role=" + role) == 1 and all(mark not in log for mark in
                        ("RUSTUSB_LOGGING_RESULT: FAIL", "SECURE_ARTIFACT_ERROR", "SECURE_ARTIFACT_OVERSIZE", "SECURE_POWEROFF_RETURNED")),
                        role + " complete successful guest collection, not guest exit code alone")
        originals.check("Kernel panic" not in kernel and "Oops:" not in kernel, role + " complete final kernel fault scan")
        originals.check(f"SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false" in log
                        and "SECURE_RNG_RTC_READY crng=true " in log, role + " actual credential isolation and RNG/clock readiness")
        init = (originals.evidence / (role + "-generated-logging-init.sh")).read_bytes()
        originals.check(hashlib.sha256(init).hexdigest() == summary["payload_sha256"][role]["init"],
                        role + " exact generated guest program in payload")
        for program in ("rustusb", "rustusb-helper"):
            originals.check(summary["payload_sha256"][role]["usr/bin/" + program] == summary["binary_sha256"][program],
                            role + " actual selected executable payload " + program)
    configuration = check_configuration(originals, keys)
    floods = {mark: check_flood(originals, mark, count) for mark, count in (("normal", 64), ("blocked", 512))}
    gate = check_lock_fault(originals, summary, baseline)
    pages = check_pages_and_audit(originals, stores)
    sensitive = [b"unknown-profile", b"RUSTUSB-RX-", b"RUSTUSB-TX-", b"-----BEGIN", b"PRIVATE KEY", b"/etc/rustusb/"]
    sensitive.extend(configuration["public_certificate_sha256"][name].encode() for name in ("ca", "server", "client"))
    for role in ("server", "client"):
        for path in (originals.evidence / (role + "-artifacts")).glob("logs-*.ndjson"):
            raw = originals.raw(role, path.name)
            parse_segment(raw)
            originals.check(not any(value in raw for value in sensitive), role + " stored typed log omits request labels, actual USB payloads, credential text and paths " + path.name)
    return {"checks": originals.checks, "check_count": len(originals.checks), "failures": [],
            "epochs": epochs, "configuration": configuration, "committed_refusal_floods": floods,
            "gate": gate, "pages": pages, "source_artifacts": originals.files,
            "logging_status": {"/".join(map(str, k)): v["status"] for k, v in stores.items()},
            "scope": "three actual Native/framed-v2 epochs; same saved original capability/root proof and live OS/kernel observations; normal rotation/authorized typed reads, removable independent access-lock fault, USB cleanup before release, final actual log loss and fresh real I/O; predicates are not USB-case counts; no permanent filesystem cancellation, in-kernel-active window, hardware, or complete Phase3 claim"}





