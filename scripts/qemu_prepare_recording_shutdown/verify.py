"""Raw prepare-gate facts; failures are retained, never converted to expected PASS."""
import hashlib
import json
import os
from pathlib import Path
import struct


def usbfs_fd_mapping(fds, records, hosts, nodes):
    """Match real received character FDs through node/rdev and original receipt.

    PhysicalDeviceReceipt.node_device is Linux st_rdev, not filesystem st_dev.
    All four independently captured views are required; a count is insufficient.
    """
    facts = []
    def add(ok, label): facts.append((bool(ok), label))
    usb_fds = [fd for fd in fds if fd["target"].startswith("/dev/bus/usb/")]
    add(len(usb_fds) == len(records) == len(hosts) == len(nodes) == 2, "exact two FD/receipt/host/node observations")
    add(len({row["bus"] for row in hosts}) == len({row["bus"] for row in nodes}) == 2, "distinct actual hardware buses")
    matched_fds = []
    for record in records:
        baseline = record["baseline"]
        receipt = baseline["receipt"]
        bus = baseline["bus_id"]
        host_rows = [row for row in hosts if row["bus"] == bus]
        node_rows = [row for row in nodes if row["bus"] == bus]
        add(len(host_rows) == len(node_rows) == 1, "one original host/node for " + bus)
        if len(host_rows) != 1 or len(node_rows) != 1: continue
        host, node = host_rows[0], node_rows[0]
        address = (receipt["bus_number"], receipt["usb_device_number"])
        expected_path = "/dev/bus/usb/%03d/%03d" % address
        candidates = [fd for fd in usb_fds if fd["target"] == expected_path]
        add(len(candidates) == 1 and node["node"] == expected_path, "one actual received node path " + bus)
        if len(candidates) != 1: continue
        fd = candidates[0]
        matched_fds.append(fd["fd"])
        add(node["character_device"] is True and node["uid"] == 0 and node["mode"] == "600" and node["links"] == 1, "protected actual character node " + bus)
        add(int(fd["device"]) == int(node["filesystem_device"]) and int(fd["inode"]) == int(node["inode"]) == receipt["node_inode"], "FD actual filesystem identity matches original node inode " + bus)
        fd_rdev = tuple(int(value, 16) for value in fd["rdev"].split(":"))
        node_rdev = tuple(int(value, 16) for value in node["rdev"].split(":"))
        sysfs_rdev = tuple(int(value) for value in node["sysfs_dev"].split(":"))
        add(len(fd_rdev) == len(node_rdev) == len(sysfs_rdev) == 2 and fd_rdev == node_rdev == sysfs_rdev, "FD/node/sysfs actual major and minor " + bus)
        add(os.makedev(*fd_rdev) == receipt["node_device"], "original receipt node_device is actual st_rdev " + bus)
        add(address == (int(host["busnum"]), int(host["devnum"])) and address[0] == int(bus.split("-", 1)[0]), "original bus and USB address " + bus)
        expected_generation = (receipt["sysfs_device"], receipt["sysfs_inode"])
        add(tuple(map(int, node["generation"].split(":"))) == tuple(map(int, host["generation"].split(":"))) == expected_generation, "original full sysfs directory generation " + bus)
        interfaces = [{"number": int(row["number"], 16), "alternate": int(row["alt"]), "driver": row["driver"]} for row in host["interfaces"]]
        add(baseline["configuration"] == int(host["configuration"]) and baseline["interfaces"] == interfaces, "original configuration and complete interface/driver baseline " + bus)
    add(len(matched_fds) == len(set(matched_fds)) == 2, "bijection of the two actual accepted character capabilities")
    return facts


def verify(evidence, args, summary):
    checks, failures = [], []
    def check(ok, label):
        checks.append({"check": label, "passed": bool(ok)})
        if not ok: failures.append(label)
    def raw(role, name): return (evidence / f"{role}-artifacts" / name).read_bytes()
    def obj(role, name): return json.loads(raw(role, name))
    def server(name): return obj("server", name)
    def rows(name): return [json.loads(line) for line in raw("server", name).splitlines()]
    def terminals(root): return [item.get("terminal", item) for item in root["terminals"]]
    def active(root): return root.get("exports", []) if args.backend == "native" else root["records"]
    def proof_for(root, session):
        attempt = session["attempts"][0]
        expected = {"resource_id": attempt["resource_id"], "kind": attempt["kind"],
                    "device_key": session["device_key"], "owner_uid": 2000,
                    "correlation": attempt["correlation"],
                    "proof": "native_export_restored" if args.backend == "native" else "physical_baseline_restored"}
        return sum(item == expected for item in terminals(root)) == 1 and all(item["lease_id"] != attempt["resource_id"] for item in active(root))
    selection = server("selection.json")
    sessions = server("selected-sessions.json")
    selected = server("selected-state.json")
    check(selection["case"] == args.case and selection["stop"] == args.stop, "actual selected gate mode")
    check(len(sessions) == 2 and len({s["id"] for s in sessions}) == 2 and all(s["profile"] == "pool" and s["role"] == "export" and not s["terminal"] and s["job_id"] == selection["job"] and len(s["attempts"]) == 1 for s in sessions), "two real export sessions in the same preparing job")
    check(len({s["attempts"][0]["resource_id"] for s in sessions}) == 2 and all(s["attempts"][0]["resource_id"] for s in sessions), "two distinct accepted resource receipts")
    check(selected["schema_version"] == 2 and len(selected["sessions"]) == 2 and len(selected["jobs"]) == 1, "actual unpublished/accepted state contains only the initial preparing pool")
    candidate = server(f'gate-candidate-{selection["candidate"]}.json')
    ended = server(f'gate-exit-{selection["candidate"]}.json')
    before, after = server("gate-held-before.json"), server("gate-held-after.json")
    identity = ["candidate", "tracee_pid", "tracee_starttime", "tid", "fd", "request", "file_device", "file_inode"]
    check(all(before[key] == after[key] == candidate[key] == ended[key] for key in identity), "same actual TID/FD/syscall and file identity before and after held observation and exit")
    check(before["held"] is True and after["held"] is True and candidate["monotonic_ns"] <= before["monotonic_ns"] < after["monotonic_ns"] < ended["monotonic_ns"], "tracer confirms held on both sides of cleanup evidence before explicit release")
    events = rows("gate-events.jsonl")
    armed = server("gate-armed.json")
    ready = server("gate-ready.json")
    arm_events = [event for event in events if event["event"] == "armed"]
    check(arm_events == [armed] and armed["nonce"] == "prepare-stop-v1" and armed["operation"] == candidate["operation"], "actual unique tracer armed receipt is saved before start")
    check(armed["tracee_pid"] == ready["tracee_pid"] == candidate["tracee_pid"] and armed["tracee_starttime"] == ready["tracee_starttime"] == candidate["tracee_starttime"] and ready["monotonic_ns"] < armed["monotonic_ns"] < candidate["monotonic_ns"], "same actual owner armed before the selected syscall entry")
    server_log = (evidence / "server-evidence.log").read_text()
    check(server_log.count("PREPARE_GATE_ARMED\n") == 1 and server_log.index("PREPARE_GATE_ARMED\n") < server_log.index("PREPARE_GATE_HELD\n"), "guest observes armed before entering the selected hold")
    releases = [e for e in events if e["event"] == "released"]
    check(len(releases) == 1 and after["monotonic_ns"] <= releases[0]["monotonic_ns"] < ended["monotonic_ns"], "exactly one selected release, after held-after receipt")
    check(ended["is_error"] is False and ended["return_value"] == (1 if args.case == "usbfs-initializer" else 0), "same resumed syscall returns actual success without fabricated result")
    check(not any(e["event"] in {"abort_cleanup", "trace_event_error", "failure"} for e in events), "normal trace completed without emergency release or ptrace failure")
    check(events[-1]["event"] == "finished" and events[-1]["tracee_exit_code"] == 0 and raw("server", "wait-tracer.txt") == b"0\n", "actual traced agent reaped successfully and tracer reaped once")
    check(0 < ended["monotonic_ns"] - candidate["monotonic_ns"] < 30_000_000_000, "bounded actual selected gate duration")
    if args.stop == "term":
        check(any(e["event"] == "signal_delivery" and e.get("signal") == 15 and before["monotonic_ns"] < e["monotonic_ns"] < after["monotonic_ns"] for e in events), "actual SIGTERM delivery while the original syscall remains held")
    else:
        check(raw("server", "wait-stop.txt").strip() in {b"0", b"1"}, "authorized StopJob CLI actual wait recorded; timeout is not cleanup proof")
        check(not any(e["event"] == "signal_delivery" and e.get("signal") == 15 and before["monotonic_ns"] < e["monotonic_ns"] < after["monotonic_ns"] for e in events), "job stop is distinct from whole-agent SIGTERM")
    root_held = server("root-held.json")
    accepted = active(root_held)
    check(len(accepted) == 2, "root held store contains exactly both live grants")
    for session in sessions:
        attempt = session["attempts"][0]
        record = [r for r in accepted if r["lease_id"] == attempt["resource_id"]]
        check(len(record) == 1 and record[0]["correlation"] == attempt["correlation"], "held root correlation " + session["id"])
        if record:
            owner = record[0]["owner"]
            check(owner["uid"] == 2000 and owner["pid"] == candidate["tracee_pid"] and int(owner.get("starttime", owner.get("start_time", -1))) == candidate["tracee_starttime"], "actual accepted owner PID/starttime/UID " + session["id"])
            if args.backend == "native":
                check(record[0]["bound"] is not None and record[0]["stub"] is None, "real bound Native export, no IMPORT yet " + session["id"])
    baseline = server("host-before.json")
    check(len(baseline) == 2 and {r["vendor"] for r in baseline} == {"0627", "0403"} and {r["interfaces"][0]["driver"] for r in baseline} == {"usbhid", "ftdi_sio"}, "nonempty original HID and FTDI driver baseline")
    check(all(r["configuration"] == "1" and len(r["interfaces"]) == 1 for r in baseline), "complete original active configuration/interface baseline")
    held_host = server("host-held.json")
    dynamic = {"configuration", "num_interfaces", "configuration_bytes", "count_bytes", "interfaces", "device_driver", "usbip_status"}
    hardware = lambda rows: [{k:v for k,v in row.items() if k not in dynamic} for row in rows]
    check(hardware(held_host) == hardware(baseline), "original two hardware generations unchanged while preparing")
    if args.backend == "native":
        check(len(held_host) == 2 and all(row["configuration"] == "" and row["num_interfaces"] == "" and row["configuration_bytes"] == 0 and row["count_bytes"] == 0 and row["interfaces"] == [] and row["device_driver"] == "usbip-host" and row["usbip_status"] == 1 for row in held_host), "two actual Native exports are unconfigured/AVAILABLE before any IMPORT")
    else:
        check(held_host == baseline, "USBFS prepare/initializer does not claim or change either original interface")
    held_fds = server("fds-held.json")
    usb_fds = [f for f in held_fds if f["target"].startswith("/dev/bus/usb/")]
    if args.backend == "usbfs":
        check(len(usb_fds) == 2 and len({f["rdev"] for f in usb_fds}) == 2, "two actual agent USBFS character capabilities after SCM_RIGHTS delivery")
        for ok, label in usbfs_fd_mapping(held_fds, accepted, baseline, server("usb-nodes-before.json")):
            check(ok, "USBFS accepted FD mapping: " + label)
        for session in sessions:
            attempt = session["attempts"][0]
            check(any(record["lease_id"] == attempt["resource_id"] and session["device_key"] == "physical:" + record["baseline"]["device_id"] for record in accepted), "USBFS original device key matches the exact accepted resource " + session["id"])
    else: check(sum(f["target"].startswith("socket:[") for f in held_fds) >= 2, "actual agent socket FD table retained; exact Native ownership is bound by root receipts")
    observations = rows("held-observations.jsonl")
    check(bool(observations), "nonempty actual held-window observations")
    cleanup_observed = []
    for observation in observations:
        number = observation["observation"]
        root = server(f"root-observe-{number}.json")
        host = server(f"host-observe-{number}.json")
        fds = server(f"fds-observe-{number}.json")
        actual_closed = not any(f["target"].startswith("/dev/bus/usb/") for f in fds)
        actual_proof = all(proof_for(root, session) for session in sessions)
        check(observation["baseline_equal"] == (host == baseline) and observation["target_fd_closed"] == actual_closed and observation["exact_root_terminal"] == actual_proof and observation["gate_released"] is False, f"held observation {number} recomputed from raw root/sysfs/FD files")
        if args.case == "usbfs-initializer":
            check(root["records"] == accepted and host == baseline, f"initializer observation {number} retains both exact grants and original profile")
            for ok, label in usbfs_fd_mapping(fds, accepted, baseline, server("usb-nodes-before.json")):
                check(ok, f"initializer observation {number}: " + label)
        if host == baseline and actual_closed and actual_proof: cleanup_observed.append(number)
    outcome = server("held-outcome.json")
    if args.case == "usbfs-initializer":
        check(not cleanup_observed and all(not row["target_fd_closed"] and not row["exact_root_terminal"] for row in observations), "accepted initializer and both grants retained until actual blocked worker returns")
        check(candidate["request"] != 74 and candidate["operation"] == "initializer", "fixed GET_CONFIGURATION ioctl-entry gate, not interface claim or kernel-active observation")
        hid = next(row for row in baseline if row["vendor"] == "0627")
        hid_path = "/dev/bus/usb/%03d/%03d" % (int(hid["busnum"]), int(hid["devnum"]))
        hid_fd = next(row for row in usb_fds if row["target"] == hid_path)
        check(candidate["fd"] == int(hid_fd["fd"]) and candidate["node_device"] == int(hid_fd["device"]) and candidate["node_inode"] == int(hid_fd["inode"]) and candidate["node_rdev"] == os.makedev(*(int(value, 16) for value in hid_fd["rdev"].split(":"))), "actual held initializer FD is the exact received HID capability")
        check(not any(e["event"] == "syscall_entry" and e.get("kind") in {"claim", "configuration", "alternate", "release", "driver"} and e["monotonic_ns"] < ended["monotonic_ns"] for e in events), "target initialization performs no observed host-state mutations before gate exit")
    else:
        check(bool(cleanup_observed), "both actual resources released and exact root proofs recorded before stalled Resource ACK is released")
        check(candidate["operation"] == "resource" and candidate["request"] == 74 and candidate["file_inode"] > 0, "actual protected state.next fsync entry")
        original = server("state-before-ack.json")
        check(original["sequence"] + 1 == selected["sequence"] and selected["audit"][-1]["action"] == "resource", "selected Resource is unpublished next durable transition")
        check(sum(a["resource_id"] is not None for s in original["sessions"] for a in s["attempts"]) == 1, "first owner Resource acknowledged, second actual owner ACK still pending")
    check(outcome["failed"] == 0, "guest held-phase result is actual PASS; RED never relabelled")
    for mark in ["after-release", "final"]:
        root = server(f"root-{mark}.json")
        check(not root["records"] and not root.get("exports", []), mark + " root active records empty")
        check(server(f"host-{mark}.json") == baseline, mark + " complete original two-device baseline restored")
        check(all(proof_for(root, session) for session in sessions), mark + " original exact two resource terminal proofs preserved")
    fresh = server("agent-reuse-ready.json")
    live = [s for s in fresh["sessions"] if not s["terminal"] and s["role"] == "export"]
    check(len(live) == 2 and {s["device_key"] for s in live} == {s["device_key"] for s in sessions} and not ({s["attempts"][0]["resource_id"] for s in live} & {s["attempts"][0]["resource_id"] for s in sessions}), "fresh two-device real grant uses new resource/attempt identities")
    client = obj("client", "client-receipts-reuse.json")
    check(len(client["records"]) == 2 and all(r["state"]["phase"] == "observed" and r["state"]["receipt"]["session"]["status"] == 6 for r in client["records"]), "fresh actual two-device kernel VHCI receipt")
    check({r["target"]["device_key"] for r in client["records"]} == {s["device_key"] for s in sessions}, "actual imported devices are the originally gated pool")
    check(raw("client", "rx-1.bin") == b"RUSTUSB-RX-1-0001:0123456789abcdef\n" == (evidence / "ftdi-host-injected-1.bin").read_bytes(), "actual fresh FTDI receive exact 35 bytes")
    check(raw("client", "tx-1.bin") == b"RUSTUSB-TX-1-0001:fedcba9876543210\n" == (evidence / "ftdi-host-rx.bin").read_bytes(), "actual fresh FTDI transmit exact 35 bytes")
    ev = raw("client", "input-1.bin")
    check(len(ev) > 0 and len(ev) % 24 == 0, "actual evdev complete records")
    keys = [struct.unpack("<qqHHi", ev[i:i+24])[2:] for i in range(0, len(ev) - 23, 24)]
    check((1, 2, 1) in keys and (1, 2, 0) in keys, "actual fresh KEY1 down and up")
    check(b"Connection refused" in raw("client", "held-connect.stderr") and raw("client", "held-connect.wait") == b"1\n", "actual no-listener refusal while same pool prepares")
    check(not any(line.split()[3] == "0A" and line.split()[1].endswith(":0CA9") for line in raw("server", "listeners-held.txt").decode().splitlines() if len(line.split()) > 3), "actual server TCP tables show no 3241 listener while gate is held")
    for role in ["server", "client"]:
        terminal = obj(role, "root-final.json")
        state = obj(role, "agent-final.json")
        check(terminal["records"] == [] and terminal.get("exports", []) == [], role + " final exact active root records empty")
        check(state["schema_version"] == 2 and len(state["jobs"]) == 2 and all(j["state"] == "finished" for j in state["jobs"]), role + " both actual jobs durably finished")
        expected_sessions = 6 if role == "server" else 2
        check(len(state["sessions"]) == expected_sessions and all(s["terminal"] and s["cleanup"] == "verified" for s in state["sessions"]), role + " exact complete session history and verified cleanup")
        attempts = [(s, a) for s in state["sessions"] for a in s["attempts"]]
        proofs = terminals(terminal)
        expected_attempts = (6 if args.backend == "native" else 4) if role == "server" else 2
        check(len(attempts) == expected_attempts and len(proofs) == expected_attempts, role + " exact nonempty root terminal/attempt cardinality")
        for session, attempt in attempts:
            expected_proof = {"native_export": "native_export_restored", "native_stub": "native_stub_available", "native_import": "native_import_released", "usbfs_grant": "physical_baseline_restored"}[attempt["kind"]]
            check(sum(t["resource_id"] == attempt["resource_id"] and t["kind"] == attempt["kind"] and t["correlation"] == attempt["correlation"] and t["device_key"] == session["device_key"] and t["owner_uid"] == 2000 and t["proof"] == expected_proof for t in proofs) == 1, role + " final terminal exact identity " + session["id"])
        log = (evidence / f"{role}-evidence.log").read_text()
        check(f"RUSTUSB_PREPARE_RESULT: PASS role={role}" in log and "RUSTUSB_PREPARE_RESULT: FAIL" not in log, role + " actual terminal guest PASS")
        kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
        check("Kernel panic" not in kernel and "Oops:" not in kernel, role + " final full S0 panic/Oops scan")
        for name in ["helper", "agent-1"] + (["agent-2"] if role == "server" else []):
            fields = dict(line.split(":", 1) for line in raw(role, f"identity-{name}.txt").decode().splitlines() if ":" in line)
            uid = "0" if name == "helper" else "2000"
            check(fields["Uid"].split() == [uid] * 4, role + " actual " + name + " UIDs")
            if name != "helper": check(all(int(fields[key], 16) == 0 for key in ["CapInh", "CapPrm", "CapEff", "CapAmb"]), role + " actual nonroot " + name + " no capabilities")
            program = "rustusb-helper" if name == "helper" else "rustusb"
            check(raw(role, f"executable-{name}.txt").decode().split()[0] == summary["binary_sha256"][program], role + " actual executed " + name + " frozen ELF")
        check(hashlib.sha256((evidence / f"{role}-generated-init.sh").read_bytes()).hexdigest() == summary["payload_sha256"][role]["init"], role + " exact executed init payload SHA")
    check(raw("server", "executable-gate.txt").decode().split()[0] == summary["probe_sha256"], "actual guest-only root tracer ELF")
    check(raw("server", "wait-agent-2.txt") == b"0\n" and raw("client", "wait-agent-1.txt") == b"0\n", "actual fresh server/client agents both reaped with exit zero")
    vhci = raw("client", "vhci-final.txt").decode().splitlines()
    ports = [line.split() for line in vhci if line.split() and line.split()[0] in {"hs", "ss"}]
    check(bool(ports) and all(int(row[2]) == 4 for row in ports), "all actual final VHCI ports NULL")
    return {"checks": checks, "check_count": len(checks), "failures": failures,
            "scope": "one selected syscall-entry gate, two actual accepted resources, real explicit release and join, subsequent fresh HID/FTDI reuse; no in-kernel-active blockage, pre-listener I/O, independent sibling I/O, ARM or hardware claim",
            "held_cleanup_observations": cleanup_observed,
            "selected_candidate": selection["candidate"], "raw_gate_elapsed_ns": ended["monotonic_ns"] - candidate["monotonic_ns"]}
