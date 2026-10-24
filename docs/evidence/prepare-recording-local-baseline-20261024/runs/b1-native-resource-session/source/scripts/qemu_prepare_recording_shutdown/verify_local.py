"""Local Native stop verdict: one original owner closes, the sibling must survive."""
import hashlib
import json
import struct


def verify(evidence, args, summary):
    checks, failures = [], []
    def check(ok, label):
        checks.append({"check": label, "passed": bool(ok)})
        if not ok: failures.append(label)
    def raw(role, name): return (evidence / f"{role}-artifacts" / name).read_bytes()
    def obj(role, name): return json.loads(raw(role, name))
    def server(name): return obj("server", name)
    def rows(name): return [json.loads(line) for line in raw("server", name).splitlines()]
    def terms(root): return [row["terminal"] for row in root["terminals"]]
    def proof(session, attempt):
        return {"resource_id": attempt["resource_id"], "kind": attempt["kind"], "device_key": session["device_key"], "owner_uid": 2000, "correlation": attempt["correlation"], "proof": {"native_export": "native_export_restored", "native_stub": "native_stub_available", "native_import": "native_import_released"}[attempt["kind"]]}
    def key_io(data, label):
        check(bool(data) and len(data) % 24 == 0, label + " complete actual evdev records")
        keys = [struct.unpack("<qqHHi", data[i:i+24])[2:] for i in range(0, len(data)-23, 24)]
        check((1, 2, 1) in keys and (1, 2, 0) in keys, label + " actual KEY1 down/up")
    check(args.stop == "session" and args.case in {"native-resource", "native-available"} and args.backend == "native", "local Native scope only")
    selected, original = server("selected-state.json"), server("state-before-ack.json")
    target, survivor = server("local-target.json"), server("local-survivor.json")
    sessions = server("selected-sessions.json")
    selection = server("selection.json")
    keys = server("keys.json")
    check(len(keys) == 2 and target["device_key"] == keys[1] and survivor["device_key"] == keys[0], "second serial export is local target, original HID is the survivor")
    check(len(sessions) == 2 and {s["id"] for s in sessions} == {target["id"], survivor["id"]} and all(s["role"] == "export" and s["profile"] == "pool" and s["job_id"] == selection["job"] and len(s["attempts"]) == 1 and s["attempts"][0]["resource_id"] for s in sessions), "two actual owned exports, one job and exact local target")
    check(target["instance_id"] == survivor["instance_id"] and target["id"] != survivor["id"] and target["attempts"][0]["resource_id"] != survivor["attempts"][0]["resource_id"], "distinct capability attempts in the same epoch")
    check(original["schema_version"] == selected["schema_version"] == 2 and original["sequence"] + 1 == selected["sequence"] and selected["audit"][-1]["session_id"] == target["id"], "original target's exact unpublished WAL transition")
    if args.case == "native-resource":
        check(selected["audit"][-1]["action"] == "resource" and sum(a["resource_id"] is not None for s in original["sessions"] for a in s["attempts"]) == 1, "first owner Resource ACK completed, target second Resource ACK held")
    else:
        check(selected["audit"][-1]["action"] == "observe" and selected["audit"][-1]["state"] == "Available" and selected["audit"][-1]["evidence"] == "export_ready" and all(s["state"] == "Available" for s in selected["sessions"]), "target Available/ExportReady is the actual held transition")
        check(any(s["id"] == survivor["id"] and s["state"] == "Available" for s in original["sessions"]) and any(s["id"] == target["id"] and s["state"] != "Available" for s in original["sessions"]) and all(s["attempts"][0]["resource_id"] for s in original["sessions"]), "sibling Available and both Resource ACKs precede target Available gate")
    candidate = server(f'gate-candidate-{selection["candidate"]}.json')
    exited = server(f'gate-exit-{selection["candidate"]}.json')
    before, after = server("gate-held-before.json"), server("gate-held-after.json")
    armed, ready = server("gate-armed.json"), server("gate-ready.json")
    events = rows("gate-events.jsonl")
    check([e for e in events if e["event"] == "armed"] == [armed] and armed["nonce"] == "prepare-stop-v1" and armed["operation"] == "resource", "actual unique armed receipt before triggering work")
    check(ready["tracee_pid"] == armed["tracee_pid"] == candidate["tracee_pid"] and ready["tracee_starttime"] == armed["tracee_starttime"] == candidate["tracee_starttime"] and ready["monotonic_ns"] < armed["monotonic_ns"] < candidate["monotonic_ns"], "same actual tracer owner armed before selected fsync")
    fields = ["candidate", "tracee_pid", "tracee_starttime", "tid", "fd", "request", "file_device", "file_inode"]
    check(all(candidate[k] == before[k] == after[k] == exited[k] for k in fields) and candidate["request"] == 74 and candidate["file_inode"] > 0, "exact original protected state.next fsync TID/FD/inode held and resumed")
    releases = [e for e in events if e["event"] == "released"]
    check(before["held"] is True and after["held"] is True and len(releases) == 1 and candidate["monotonic_ns"] <= before["monotonic_ns"] < after["monotonic_ns"] <= releases[0]["monotonic_ns"] < exited["monotonic_ns"], "real held receipts bracket local cleanup before sole explicit release")
    check(exited["return_value"] == 0 and exited["is_error"] is False and exited["monotonic_ns"]-candidate["monotonic_ns"] < 30_000_000_000, "original fsync returns success within guest-only finite gate")
    check(not any(e["event"] == "signal_delivery" and e.get("signal") == 15 and before["monotonic_ns"] < e["monotonic_ns"] < after["monotonic_ns"] for e in events), "local StopSession is not whole-agent SIGTERM")
    check(any(e["event"] == "signal_delivery" and e.get("signal") == 15 and e["monotonic_ns"] > exited["monotonic_ns"] for e in events), "later global shutdown actually delivers SIGTERM after original ACK")
    check(events[-1]["event"] == "finished" and events[-1]["tracee_exit_code"] == 0 and raw("server", "wait-tracer.txt") == b"0\n", "traced original owner and tracer actually join")
    check(not any(e["event"] in {"abort_cleanup", "trace_event_error", "failure"} for e in events), "no emergency-release path disguised as success")
    held = server("root-held.json")
    check(held["schema_version"] == 2 and held["records"] == [] and held["terminals"] == [] and len(held["exports"]) == 2, "original actual root stores both preparing exports")
    root_by_id = {r["lease_id"]:r for r in held["exports"]}
    for session in sessions:
        attempt = session["attempts"][0]
        record = root_by_id[attempt["resource_id"]]
        check(record["correlation"] == attempt["correlation"] and record["owner"]["uid"] == 2000 and record["owner"]["pid"] == candidate["tracee_pid"] and record["owner"]["starttime"] == candidate["tracee_starttime"] and record["bound"] is not None and record["stub"] is None, "actual accepted bound export and owner " + session["id"])
    survivor_record = root_by_id[survivor["attempts"][0]["resource_id"]]
    target_proof = proof(target, target["attempts"][0])
    baseline, held_host = server("host-before.json"), server("host-held.json")
    check(len(baseline) == len(held_host) == 2 and {r["vendor"] for r in baseline} == {"0627", "0403"} and {r["interfaces"][0]["driver"] for r in baseline} == {"usbhid", "ftdi_sio"}, "actual complete original HID and serial baselines")
    check(all(r["device_driver"] == "usbip-host" and r["configuration_bytes"] == r["count_bytes"] == 0 and r["configuration"] == r["num_interfaces"] == "" and r["interfaces"] == [] and r["usbip_status"] == 1 for r in held_host), "both original Native exports bound but not imported at gate")
    target_bus = next(r["bus"] for r in baseline if r["vendor"] == "0403")
    wanted_host = sorted([r for r in baseline if r["bus"] == target_bus]+[r for r in held_host if r["bus"] != target_bus], key=lambda r:r["bus"])
    def local_root(root): return root["records"] == [] and root["exports"] == [survivor_record] and terms(root) == [target_proof]
    observations = rows("local-held-observations.jsonl")
    check(bool(observations), "nonempty real local held observations")
    successful = []
    for row in observations:
        i = row["observation"]
        root_ok = local_root(server(f"root-observe-{i}.json"))
        host_ok = server(f"host-observe-{i}.json") == wanted_host
        check(row["target_exact_terminal_and_original_sibling"] == root_ok and row["target_restored_and_sibling_bound"] == host_ok and row["gate_released"] is False, f"local observation {i} independently recomputed from exact originals")
        if root_ok and host_ok:successful.append(i)
    check(bool(successful), "only serial target really closes while original HID capability remains unchanged under the gate")
    outcome = server("local-held-outcome.json")
    check(outcome["failed"] == 0 and outcome["local_cleanup_while_held"] is True and outcome["target"] == target["id"] and outcome["survivor"] == survivor["id"], "actual local verdict keeps exact target and sibling identities")
    check(local_root(server("root-local-after-ack.json")) and server("host-local-after-ack.json") == wanted_host, "original sibling survives successful target ACK and metadata finalization")
    jobs = server("local-jobs.json")["response"]["jobs"]
    check(len(jobs) == 1 and jobs[0]["id"] == selection["job"] and jobs[0]["state"] == "running" and jobs[0]["instance_id"] == survivor["instance_id"], "same original job remains running after local stop")
    local_sessions = server("local-sessions.json")["response"]["sessions"]
    check(len(local_sessions) == 2 and any(s["id"] == target["id"] and s["terminal"] and s["cleanup"] == "verified" for s in local_sessions) and any(s["id"] == survivor["id"] and not s["terminal"] and s["state"] == "Available" for s in local_sessions), "target terminal and original sibling Available are distinct")
    listeners = [line.split() for line in raw("server", "listeners-held.txt").decode().splitlines() if len(line.split()) > 3]
    bound = any(row[3] == "0A" and row[1].endswith(":0CA9") for row in listeners)
    check(bound == (args.case == "native-available"), "actual held listener presence matches Resource versus Available boundary")
    check(raw("client", "held-connect.wait") == (b"0\n" if bound else b"1\n"), "actual held TCP connection result matches observed listener")
    if not bound: check(b"Connection refused" in raw("client", "held-connect.stderr"), "Resource boundary actually refuses the pre-listener TCP attempt")
    catalog = obj("client", "local-catalog.json")["response"]
    check(catalog["result"] == "catalog" and catalog["devices"] == [{"device_key":survivor["device_key"],"device":survivor_record["registration"]["published_device"]}], "actual authenticated catalog exposes only original HID publication")
    denied = obj("client", "local-denied-sessions.json")["response"]["sessions"]
    check(len(denied) == 1 and denied[0]["device_key"] == target["device_key"] and denied[0]["terminal"] and denied[0]["operation_error"] == "permission" and denied[0]["cleanup"] == "not_started" and denied[0]["attempts"] == [], "ordinary target start refuses unavailable serial with no helper attempt or fake root proof")
    check(raw("client", "local-before-denied-root.json") == raw("client", "local-after-denied-root.json"), "target refusal leaves actual client root store unchanged")
    survivor_import = obj("client", "local-survivor-receipt.json")["records"]
    check(len(survivor_import) == 1 and survivor_import[0]["target"]["device_key"] == survivor["device_key"] and survivor_import[0]["state"]["phase"] == "observed" and survivor_import[0]["state"]["receipt"]["session"]["status"] == 6, "original surviving publication is truly imported through kernel VHCI")
    during = server("root-local-survivor-io.json")
    check(len(during["exports"]) == 1 and {k:v for k,v in during["exports"][0].items() if k != "stub"} == {k:v for k,v in survivor_record.items() if k != "stub"} and during["exports"][0]["stub"]["state"]["phase"] == "observed" and terms(during) == [target_proof], "same original HID export owns the actual live stub after target refusal")
    key_io(raw("client", "local-survivor-input.bin"), "surviving original HID")
    detached = obj("client", "root-local-detached.json")
    imported_session = obj("client", "local-survivor-sessions.json")["response"]["sessions"][0]
    check(detached["records"] == detached["exports"] == [] and terms(detached) == [proof(imported_session, imported_session["attempts"][0])], "original HID import normally detaches with exact root proof before last export stop")
    check(obj("client", "local-stop-import.json")["response"] == {"result":"stop_accepted","owner_present":True}, "actual authorized original import stop response")
    after_detach = server("root-local-after-detach.json")
    check(len(after_detach["exports"]) == 1 and after_detach["exports"][0] == survivor_record and target_proof in terms(after_detach) and len(terms(after_detach)) == 2, "original HID export still active and exact stub already retired after detach")
    empty = server("root-local-empty.json")
    check(empty["records"] == empty["exports"] == [] and len(terms(empty)) == 3 and all(proof(s,s["attempts"][0]) in terms(empty) for s in sessions) and server("host-local-empty.json") == baseline, "both exports individually cancelled with complete actual baselines and exact retained proof")
    empty_jobs = server("local-empty-jobs.json")["response"]["jobs"]
    check(len(empty_jobs) == 1 and empty_jobs[0]["id"] == selection["job"] and empty_jobs[0]["instance_id"] == survivor["instance_id"] and empty_jobs[0]["state"] == "running", "same original empty-pool job remains Running after last local export stop")
    check(server("local-stop-survivor.json")["response"] == {"result":"stop_accepted","owner_present":True}, "actual authorized last original export stop response")
    empty_listener = [line.split() for line in raw("server", "local-empty-listeners.txt").decode().splitlines() if len(line.split()) > 3]
    check(any(row[3] == "0A" and row[1].endswith(":0CA9") for row in empty_listener), "same configured listener remains bound after all exports individually stop")
    check(obj("client", "local-empty-catalog.json")["response"] == {"result":"catalog","devices":[]}, "actual authenticated mTLS catalog is empty before global agent SIGTERM")
    control = [json.loads(line) for line in (evidence/"host-control.jsonl").read_text().splitlines()]
    marks = [row["mark"] for row in control]
    check(all(marks.count(mark)==1 for mark in ["survivor-io","detach-survivor","survivor-detached","empty-catalog-request","empty-catalog-confirmed","local-job-closed"]) and marks.index("survivor-io") < marks.index("detach-survivor") < marks.index("survivor-detached") < marks.index("empty-catalog-request") < marks.index("empty-catalog-confirmed") < marks.index("local-job-closed"), "actual host/guest barriers preserve I/O then detach then last-export stop then empty TLS catalog then global stop")
    for stage in ["after-release", "final"]:
        root = server("root-"+stage+".json")
        check(root["records"] == root["exports"] == [] and server("host-"+stage+".json") == baseline, stage + " global stop restores both complete baselines")
        check(all(proof(s,s["attempts"][0]) in terms(root) for s in sessions), stage + " preserves both original exact export terminal proofs")
    fresh = server("root-reuse-ready.json")["exports"]
    check(len(fresh) == 2 and {r["lease_id"] for r in fresh}.isdisjoint(root_by_id) and all(r["owner"]["pid"] != candidate["tracee_pid"] for r in fresh), "fresh agent holds distinct actual resource IDs after global stop")
    fresh_client = obj("client", "client-receipts-reuse.json")["records"]
    check(len(fresh_client) == 2 and {r["target"]["device_key"] for r in fresh_client} == set(keys) and all(r["state"]["phase"] == "observed" and r["state"]["receipt"]["session"]["status"] == 6 for r in fresh_client), "fresh two-device true kernel enumeration")
    check(raw("client", "rx-1.bin") == (evidence/"ftdi-host-injected-1.bin").read_bytes() == b"RUSTUSB-RX-1-0001:0123456789abcdef\n", "fresh FTDI receive exact 35 bytes")
    check(raw("client", "tx-1.bin") == (evidence/"ftdi-host-rx.bin").read_bytes() == b"RUSTUSB-TX-1-0001:fedcba9876543210\n", "fresh FTDI transmit exact 35 bytes")
    key_io(raw("client", "input-1.bin"), "fresh HID")
    for role in ["server", "client"]:
        root, state = obj(role,"root-final.json"), obj(role,"agent-final.json")
        check(root["records"] == root["exports"] == [], role+" actual final root owners empty")
        expected_jobs, expected_sessions, expected_attempts = (2,7,7) if role=="server" else (4,4,3)
        check(state["schema_version"] == 2 and len(state["jobs"]) == expected_jobs and all(j["state"] == "finished" for j in state["jobs"]), role+" exact durable job history")
        check(len(state["sessions"]) == expected_sessions and all(s["terminal"] for s in state["sessions"]), role+" exact nonempty terminal session history")
        attempts = [(s,a) for s in state["sessions"] for a in s["attempts"]]
        check(len(attempts) == len(terms(root)) == expected_attempts, role+" exact real-attempt/root-terminal count excludes target no-start refusal")
        for session, attempt in attempts:
            check(session["cleanup"] == "verified" and terms(root).count(proof(session,attempt)) == 1, role+" original exact cleanup proof "+session["id"])
        if role == "client":
            no_attempt = [s for s in state["sessions"] if not s["attempts"]]
            check(no_attempt == denied, "original permission/no-start session preserved without invented proof")
        else: check({s["id"] for s in sessions}.issubset({s["id"] for s in state["sessions"]}), "both original export session IDs preserved through new epoch")
        for name in ["helper","agent-1"]+(["agent-2"] if role=="server" else []):
            fields = dict(line.split(":",1) for line in raw(role,"identity-"+name+".txt").decode().splitlines() if ":" in line)
            uid = "0" if name=="helper" else "2000"
            check(fields["Uid"].split() == [uid]*4, role+" actual "+name+" owner UID")
            if name!="helper":check(all(int(fields[k],16)==0 for k in ["CapInh","CapPrm","CapEff","CapAmb"]), role+" actual "+name+" no capabilities")
            program = "rustusb-helper" if name=="helper" else "rustusb"
            check(raw(role,"executable-"+name+".txt").decode().split()[0] == summary["binary_sha256"][program], role+" actual "+name+" frozen ELF")
        log=(evidence/(role+"-evidence.log")).read_text()
        check("RUSTUSB_PREPARE_RESULT: PASS role="+role in log and "RUSTUSB_PREPARE_RESULT: FAIL" not in log, role+" actual terminal PASS")
        kernel=(evidence/(role+"-serial.log")).read_text(errors="replace")
        check("Kernel panic" not in kernel and "Oops:" not in kernel, role+" final complete S0 scan")
        check(hashlib.sha256((evidence/(role+"-generated-init.sh")).read_bytes()).hexdigest()==summary["payload_sha256"][role]["init"], role+" actual generated init payload SHA")
    check(raw("server","executable-gate.txt").decode().split()[0]==summary["probe_sha256"], "actual guest-only tracer ELF")
    check(raw("server","wait-agent-2.txt")==raw("client","wait-agent-1.txt")==b"0\n", "both final actual agent parents reap exit zero")
    for name in ["local-detached-vhci.txt","local-vhci-empty.txt","vhci-final.txt"]:
        ports=[line.split() for line in raw("client",name).decode().splitlines() if line.split() and line.split()[0] in {"hs","ss"}]
        check(bool(ports) and all(int(row[2])==4 for row in ports), "actual VHCI all NULL "+name)
    return {"checks":checks,"check_count":len(checks),"failures":failures,"local_cleanup_observations":successful,"scope":"one actual Native Resource or Available fsync-entry gate; exact local serial stop; original HID grant survives and performs real KEY1 after original ACK; normal detach and last local export stop preserve same Running job and actual empty mTLS catalog; later whole-job stop and new two-device real HID/35B FTDI reuse; no in-kernel-active or hardware claim"}
