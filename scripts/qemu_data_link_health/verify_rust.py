"""Rust virtual/USBFS lifecycle proof for the actual data-link experiment.

This verifier is separate from the packet auditor. Assertions are evidence
checks, never a count of USB cases. It does not call an old runner's verifier.
"""
import hashlib
import json
import re
import struct
from pathlib import Path

import audit as packet_audit

PHASES = ("c2s", "s2c", "both")
DATA_LINK = {"protocol": "framed_v2", "interval_millis": 1000, "response_timeout_millis": 3000}
PROOFS = {"native_import": "native_import_released", "native_stub": "native_stub_available",
          "native_export": "native_export_restored"}


def compose_guest(prefix, template, backend):
    """Compose disposable guest code at exact, source-checked shared anchors."""
    if backend not in ("virtual", "usbfs"):
        raise ValueError("Rust guest backend must be virtual or usbfs")
    def replace(text, old, new):
        if text.count(old) != 1:
            raise RuntimeError("Rust guest composition anchor changed: " + repr(old))
        return text.replace(old, new)
    prefix = replace(prefix, '[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2',
                     f'[ "${{AGENT_COMMON:-}}" = 1 ] && [ "$BACKEND" = {backend} ] || exit 2')
    prefix = replace(prefix, "record_client_locals() {", "physical_client_locals() {")
    prefix = replace(prefix, "actual_io() {", "physical_actual_io() {")
    template = replace(template, '[ "${LINK_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2',
                       f'[ "${{LINK_COMMON:-}}" = 1 ] && [ "$BACKEND" = {backend} ] || exit 2')
    publication = '''    for phase_file in "/tmp/agent-$1.json" "/tmp/root-$1.json"; do publish "$phase_file"; done
    if [ "$ROLE" = server ]; then publish "/tmp/host-$1.json"; else publish "/tmp/vhci-$1.txt"; fi'''
    template = replace(template, publication, '''    publish "/tmp/agent-$1.json"
    if [ "$ROLE" = client ]; then
        publish "/tmp/root-$1.json"; publish "/tmp/vhci-$1.txt"
    else
        publish "/tmp/native-$1.status"
        if [ "$BACKEND" = usbfs ]; then
            publish "/tmp/root-$1.json"; publish "/tmp/host-$1.json"
        else
            publish "/tmp/physical-$1.status"
        fi
    fi''')
    anchor = 'if [ "$ROLE" = server ]; then\n@@SERVER_SETUP@@'
    fragment = Path(__file__).with_name("guest-rust-fragments.sh").read_text()
    template = replace(template, anchor, fragment + "\n" + anchor)
    template = replace(template, "@@SERVER_SETUP@@", "    rust_server_setup")
    template = replace(template, "@@CLIENT_SETUP@@", "    rust_client_setup")
    if template.count("    native_host_phase ") != 3:
        raise RuntimeError("Rust guest host phase anchors changed")
    template = template.replace("    native_host_phase ", "    rust_host_phase ")
    template = replace(template, "    host_baseline /tmp/host-restored.json; cmp /tmp/host-before.json /tmp/host-restored.json", "    rust_server_final")
    template = replace(template, "    jq -e '.schema_version==2 and .records==[] and .exports==[]' /tmp/root-final.json > /dev/null", "    rust_final_root_check")
    return prefix + "\nLINK_COMMON=1\n" + template


def require(value, message):
    if not value:
        raise RuntimeError("data-link lifecycle: " + message)


def one(rows, predicate, label):
    values = [row for row in rows if predicate(row)]
    require(len(values) == 1, "one exact " + label)
    return values[0]


def application_disk(state, role, expected_schemas=None):
    """Closed disk2 by default; only a fixture's explicit role may require disk3."""
    expected_schemas = expected_schemas or {}
    require(set(expected_schemas) <= {"client", "server"}, "known explicit application schema role")
    version = expected_schemas.get(role, 2)
    require(version in (2, 3), "known explicit application disk version")
    fields = {"schema_version", "sequence", "jobs", "sessions", "requests", "audit", "link_history"}
    if version == 3:
        fields.add("automation")
    require(set(state) == fields and state["schema_version"] == version,
            f"actual strict private {role} application disk{version}")
    if version == 3:
        journal = state["automation"]
        require(isinstance(journal, dict) and set(journal) == {"sequence", "rules", "mutations", "origins", "events"},
                "required nonnull complete automation object")
        require(type(journal["sequence"]) is int and journal["sequence"] >= 0
                and all(isinstance(journal[name], list) for name in ("rules", "mutations", "origins", "events")),
                "typed automation sequence and collections")


def proof(root, session):
    require(len(session["attempts"]) == 1, "one owned capability per Native session")
    attempt = session["attempts"][0]
    require(attempt["kind"] == "native_import", "client proof is an actual Native import")
    result = one([row["terminal"] for row in root["terminals"]],
                 lambda t: t["kind"] == attempt["kind"] and t["correlation"] == attempt["correlation"], "retained root terminal")
    require(result["owner_uid"] == 2000 and result["device_key"] == session["device_key"]
            and result["resource_id"] == attempt["resource_id"] and result["proof"] == PROOFS[attempt["kind"]], "full root proof identity/type")
    require(attempt["proof"] in (None, result["proof"]), "root proof echo")
    return result


def backend_for_keys(keys):
    if keys == ["virtual:hid", "virtual:cdc-acm"]:
        return "virtual"
    require(len(keys) == 2 and all(re.fullmatch(r"physical:usb-[0-9a-f]{64}", key) for key in keys),
            "exact virtual or physical stable-key namespace")
    return "usbfs"


def physical_proof(root, session):
    require(len(session["attempts"]) == 1, "one real USBFS grant per export")
    attempt = session["attempts"][0]
    require(attempt["kind"] == "usbfs_grant", "actual USBFS attempt namespace")
    result = one(root["terminals"], lambda item: item["correlation"] == attempt["correlation"], "physical Finish terminal")
    require(result["kind"] == "usbfs_grant" and result["owner_uid"] == 2000
            and result["device_key"] == session["device_key"] and result["resource_id"] == attempt["resource_id"]
            and result["proof"] == "physical_baseline_restored", "full same-capability physical baseline proof")
    require(attempt["proof"] in (None, result["proof"]), "physical proof echo")
    return result


def physical_grants(root, exports, keys, baseline):
    require(set(root) == {"schema_version", "records", "terminals"} and root["schema_version"] == 3
            and len(root["records"]) == 2 and root["terminals"] == [], "exact held tracked physical disk3")
    require(len(baseline) == 2 and len({x["bus"] for x in baseline}) == 2, "complete original physical pair")
    require({"physical:" + r["baseline"]["device_id"] for r in root["records"]} == set(keys), "exact approved grant pair")
    for key in keys:
        record = one(root["records"], lambda r: "physical:" + r["baseline"]["device_id"] == key, "original physical grant")
        session = one(exports, lambda x: x["device_key"] == key, "correlated export session")
        require(len(session["attempts"]) == 1, "one attempt for original grant")
        attempt = session["attempts"][0]
        require(attempt["kind"] == "usbfs_grant" and attempt["resource_id"] == record["lease_id"]
                and attempt["correlation"] == record["correlation"] and attempt["proof"] is None,
                "original actual grant/resource/correlation, no premature Finish")
        require(record["correlation"]["session_id"] == session["id"]
                and record["correlation"]["instance_id"] == session["instance_id"], "exact application epoch and session")
        require(record["owner"]["uid"] == 2000 and record["owner"]["pid"] > 0 and record["owner"]["start_time"] > 0
                and record["recovery"] == "manual_only", "actual trusted owner and no automatic recovery grant")
        original = record["baseline"]
        observed = one(baseline, lambda b: b["bus"] == original["bus_id"], "full original sysfs baseline")
        receipt = original["receipt"]
        require(observed["generation"] == f"{receipt['sysfs_device']}:{receipt['sysfs_inode']}"
                and int(observed["busnum"]) == receipt["bus_number"] and int(observed["devnum"]) == receipt["usb_device_number"]
                and receipt["node_device"] > 0 and receipt["node_inode"] > 0,
                "same original sysfs/USBFS generation and address")
        interfaces = [{"number": int(i["number"], 16), "alternate": int(i["alt"]), "driver": i["driver"] or None}
                      for i in observed["interfaces"]]
        require(original["configuration"] == int(observed["configuration"]) == 1
                and original["interfaces"] == interfaces, "full original configuration/alternate/driver baseline")


def physical_phase(baseline, observed, fault):
    require(len(baseline) == len(observed) == 2, "complete physical phase capture")
    for before in baseline:
        row = one(observed, lambda item: item["bus"] == before["bus"], "same physical phase bus")
        require({k: v for k, v in row.items() if k != "interfaces"} == {k: v for k, v in before.items() if k != "interfaces"},
                "fixed generation/configuration/device driver throughout USBFS fault")
        restored = fault and before["vendor"] == "0627"
        expected = before["interfaces"] if restored else [dict(item, driver="usbfs") for item in before["interfaces"]]
        require(row["interfaces"] == expected and row["device_driver"] == "usb" and row["usbip_status"] is None,
                "actual target driver restored while original sibling remains USBFS claimed")


def phase_proof(evidence, phase, live=False, application_schemas=None):
    require(phase in PHASES, "known fault phase")
    evidence = Path(evidence)
    files = {}
    def original(role, name):
        path = evidence / "live" / role / name if live else evidence / f"{role}-artifacts" / name
        raw = path.read_bytes()
        require(len(raw) <= 1048576, "bounded phase original")
        files[f"{role}/{name}"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        return raw
    def data(role, name):
        return json.loads(original(role, name))
    keys = json.loads((evidence / "keys.json").read_text())
    require(len(keys) == len(set(keys)) == 2, "two distinct actual keys")
    backend = backend_for_keys(keys)
    target_key, sibling_key = keys
    outcomes = []
    for role, wanted_role in (("client", "import"), ("server", "incoming")):
        before = data(role, f"agent-pre-{phase}.json")
        after = data(role, f"agent-fault-{phase}.json")
        application_disk(before, role, application_schemas)
        application_disk(after, role, application_schemas)
        target = one(before["sessions"], lambda s: s["device_key"] == target_key and s["role"] == wanted_role and not s["terminal"], "pre-fault target")
        ended = one(after["sessions"], lambda s: s["id"] == target["id"], "same terminal target")
        for field in ("id", "instance_id", "job_id", "device_key", "profile", "role"):
            require(ended[field] == target[field], "target identity retained")
        require(ended["terminal"] and ended["cleanup"] == "verified" and ended["state"] == "Disconnected", "target actually released")
        before_sibling = one(before["sessions"], lambda s: s["device_key"] == sibling_key and s["role"] == wanted_role and not s["terminal"], "original live sibling")
        after_sibling = one(after["sessions"], lambda s: s["id"] == before_sibling["id"], "retained sibling")
        for field in ("id", "instance_id", "job_id", "device_key", "attempts", "terminal", "cleanup", "state"):
            require(after_sibling[field] == before_sibling[field], "sibling lifecycle/capability unchanged")
        require(after_sibling["state"] == "Connected" and after_sibling["operation_error"] is None, "sibling remains connected")
        health = data(role, f"health-{phase}-{role}-terminal.json")
        sample = one(health["response"]["sessions"], lambda h: h["session_id"] == target["id"], "actual ended health")
        prior = data(role, f"health-{phase}-{role}-pre.json")
        prior = one(prior["response"]["sessions"], lambda h: h["session_id"] == target["id"], "actual pre-arm health")
        require(prior["availability"] == "live" and sample["availability"] == "owner_ended",
                "live original observation becomes actual owner-ended history")
        link = sample["link"]
        require(sample["instance_id"] == target["instance_id"] and link["identity"] == prior["link"]["identity"]
                and link["protocol"] == "framed_v2" and link["phase"] == "ended" and link["end"] is not None,
                "same data connection actual driver ended")
        require(link["sample_sequence"] >= prior["link"]["sample_sequence"]
                and link["elapsed_millis"] >= prior["link"]["elapsed_millis"], "monotonic sample progression")
        if link["end"] == "probe_timeout":
            require(link["probe"] == "timed_out" and ended["operation_error"] == "timeout", "timeout remains operation failure after cleanup")
            elapsed = link["elapsed_millis"] - link["last_probe_enqueued_millis"]
            require(3000 <= elapsed <= 5000, "actual 3-second probe deadline plus bounded 2-second guest scheduling observation")
            require(link["last_reply_sequence"] < link["last_probe_sequence"], "outstanding probe was not matched")
        terminal = None
        if role == "client":
            root_before = data(role, f"root-pre-{phase}.json")
            root_after = data(role, f"root-fault-{phase}.json")
            require(root_after["schema_version"] == 2 and len(root_after["terminals"]) == PHASES.index(phase) + 1, "exact retired target count")
            terminal = proof(root_after, ended)
            serial = one(root_before["records"], lambda r: r["target"]["device_key"] == sibling_key, "serial import receipt")
            target_record = one(root_before["records"], lambda r: r["target"]["device_key"] == target_key, "HID import receipt")
            require(root_after["exports"] == [] and root_after["records"] == [serial], "only original target import retired")
            require(terminal["resource_id"] == target_record["channel_id"], "proof matches original target capability")
            local = data(role, f"local-fault-{phase}.json")
            require(local["old_local_exists"] is False and local["old_local_bus_id"] == target_record["state"]["receipt"]["session"]["local_bus_id"], "actual old target local generation gone")
            serial_session = serial["state"]["receipt"]["session"]
            generation = serial_session["local_device_generation"]
            require(local["sibling_local_bus_id"] == serial_session["local_bus_id"]
                    and local["sibling_generation"] == f"{generation['filesystem_device']}:{generation['inode']}",
                    "actual original sibling generation remains")
            rows = [line.split() for line in original(role, f"vhci-fault-{phase}.txt").decode().splitlines() if line.startswith(("hs ", "ss "))]
            require(rows and sum(int(row[2]) == 6 for row in rows) == 1 and all(int(row[2]) in (4, 6) for row in rows), "actual one USED and remaining NULL at disarm")
            serial_row = one(rows, lambda row: int(row[1]) == serial_session["port"], "original serial port")
            target_row = one(rows, lambda row: int(row[1]) == target_record["state"]["receipt"]["session"]["port"], "original target port")
            require(int(target_row[2]) == 4 and int(serial_row[2]) == 6 and serial_row[6] == serial_session["local_bus_id"]
                    and int(serial_row[4], 16) == serial_session["remote_device_id"], "exact target NULL and original serial mapping")
        else:
            require(target["attempts"] == ended["attempts"] == before_sibling["attempts"] == after_sibling["attempts"] == [],
                    "Rust incoming cleanup has no Native helper capability or Root terminal")
            require(original(role, f"native-pre-{phase}.status") == original(role, f"native-fault-{phase}.status") == b"absent\n",
                    "Rust server has no Native journal")
            exports_before = [x for x in before["sessions"] if x["role"] == "export"]
            exports_after = [x for x in after["sessions"] if x["role"] == "export"]
            require(len(exports_before) == 2 and {x["device_key"] for x in exports_before} == set(keys), "two exact Rust exports")
            for export in exports_before:
                retained = one(exports_after, lambda x: x["id"] == export["id"], "unchanged export session")
                for field in ("id", "instance_id", "job_id", "device_key", "attempts", "state", "terminal", "cleanup"):
                    require(retained[field] == export[field], "export persists across incoming fault")
                require(retained["state"] == "Available" and not retained["terminal"], "export remains available")
            if backend == "usbfs":
                grants_before = data(role, f"root-pre-{phase}.json")
                grants_after = data(role, f"root-fault-{phase}.json")
                require(grants_before == grants_after, "full immutable USBFS grants remain unchanged until server stop")
                physical_grants(grants_before, exports_before, keys, data(role, "host-before.json"))
                physical_phase(data(role, "host-before.json"), data(role, f"host-pre-{phase}.json"), False)
                physical_phase(data(role, "host-before.json"), data(role, f"host-fault-{phase}.json"), True)
            else:
                require(original(role, f"physical-pre-{phase}.status") == original(role, f"physical-fault-{phase}.status") == b"absent\n",
                        "virtual server has no physical journal")
                require(all(x["attempts"] == [] for x in exports_before), "virtual exports have no root grant")
        outcomes.append({"role": role, "session_id": target["id"], "connection": link["identity"],
                         "end": link["end"], "operation_error": ended["operation_error"], "cleanup": ended["cleanup"],
                         "last_probe_enqueued_millis": link["last_probe_enqueued_millis"],
                         "ended_elapsed_millis": link["elapsed_millis"], "root_terminal": terminal})
    require(any(row["end"] == "probe_timeout" for row in outcomes), "at least one actual same-data probe timeout")
    round_id = (PHASES.index(phase) + 1) * 2
    tx = f"RUSTUSB-TX-{round_id}-0001:fedcba9876543210\n".encode()
    require(original("client", f"tx-{round_id}.bin") == tx and len(tx) == 35, "actual sibling 35-byte write before disarm")
    if backend == "virtual":
        require(original("client", f"echo-{round_id}.bin") == tx, "actual sibling CDC 35-byte self-echo before disarm")
    else:
        rx = f"RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n".encode()
        require(original("client", f"rx-{round_id}.bin") == rx, "actual sibling FTDI 35-byte receive before disarm")
    return {"schema_version": 1, "phase": phase, "backend": backend,
            "scope": "Actual Rust engine cleanup and client Root proof before disarm; idle/after-I/O fault, no pending-URB claim",
            "outcomes": outcomes, "source_artifacts": files}


def verify(evidence, args, application_schemas=None, extra_operators=None):
    evidence = Path(evidence)
    def raw(role, name):
        return (evidence / f"{role}-artifacts" / name).read_bytes()
    def data(role, name):
        return json.loads(raw(role, name))
    def vhci(name, count):
        rows = [line.split() for line in raw("client", name).decode().splitlines() if line.startswith(("hs ", "ss "))]
        require(rows and sum(int(r[2]) == 6 for r in rows) == count and all(int(r[2]) in (4, 6) for r in rows), "actual VHCI USED/NULL " + name)
    require(args.backend in ("virtual", "usbfs"), "known Rust backend")
    keys = data("client", "keys.json")
    require(keys == data("server", "keys.json") == json.loads((evidence / "keys.json").read_text()) and len(set(keys)) == 2, "actual common two keys")
    require(backend_for_keys(keys) == args.backend, "actual keys match requested backend")
    phase_facts = []
    for phase in PHASES:
        current = phase_proof(evidence, phase, application_schemas=application_schemas)
        require(current == json.loads((evidence / "bindings" / f"{phase}-terminal.json").read_text()), "late raw artifacts equal pre-disarm proof")
        phase_facts.append(current)
        for role in ("client", "server"):
            require(raw(role, f"health-{phase}-{role}-pre.json") == (evidence / "bindings" / f"{phase}-{role}-health.json").read_bytes(), "arm exact actual health bytes")
            for path in (evidence / "live" / role).iterdir():
                require(path.read_bytes() == raw(role, path.name), "live source identical to final original " + path.name)
        catalog = data("client", f"catalog-during-{phase}.json")
        require(catalog["schema_version"] == 1 and catalog["journal_healthy"] is True and catalog["response"]["result"] == "catalog", "independent Catalog succeeds during target fault")
        vhci(f"vhci-pre-{phase}.txt", 2)
        vhci(f"vhci-fault-{phase}.txt", 1)
    if args.backend == "usbfs":
        baseline = data("server", "host-before.json")
        require(data("server", "host-restored.json") == baseline, "full original configuration/interfaces/drivers and generation restored")
        initial = data("server", "root-initial-exported.json")
        physical_grants(initial, [x for x in data("server", "agent-initial-exported.json")["sessions"] if x["role"] == "export"], keys, baseline)
        for phase in PHASES:
            for stage in ("pre", "fault"):
                require(data("server", f"root-{stage}-{phase}.json") == initial, "original full grants unchanged across all faults")
                physical_phase(baseline, data("server", f"host-{stage}-{phase}.json"), stage == "fault")
        require(data("server", "root-final-held.json") == initial, "original grants retained through final explicit reuse")
    else:
        require(data("server", "devices.json") == data("server", "devices-final.json") == [], "virtual server has no physical device pool")
        for mark in ["initial-exported", "final-held", "final"]:
            require(raw("server", f"native-{mark}.status") == raw("server", f"physical-{mark}.status") == b"absent\n", "observed virtual journal absence")
    expected_tx = b""
    previous_serial = None
    hid_ids, hid_generations = set(), set()
    held_ids = {"server": set(), "client": set()}
    for round_id in range(1, 8):
        rx = f"RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n".encode()
        tx = f"RUSTUSB-TX-{round_id}-0001:fedcba9876543210\n".encode()
        require(len(tx) == 35 and raw("client", f"tx-{round_id}.bin") == tx, "actual 35-byte serial write")
        if args.backend == "usbfs":
            require(raw("client", f"rx-{round_id}.bin") == rx
                    and (evidence / f"ftdi-host-injected-{round_id}.bin").read_bytes() == rx, "real FTDI 35 bytes each way")
        else:
            require(raw("client", f"echo-{round_id}.bin") == tx, "real CDC 35-byte echo")
            require(not (evidence / "client-artifacts" / f"input-{round_id}.bin").exists(), "no invented virtual key event")
        expected_tx += tx
        if round_id % 2:
            if args.backend == "usbfs":
                events = raw("client", f"input-{round_id}.bin")
                require(events and len(events) % 24 == 0, "complete x86_64 evdev records")
                values = [struct.unpack("<qqHHi", events[offset:offset + 24])[2:] for offset in range(0, len(events), 24)]
                require((1, round_id + 1, 1) in values and (1, round_id + 1, 0) in values, "actual HID press/release")
            records = data("client", f"client-receipts-{round_id}.json")["records"]
            require(len(records) == 2, "two actual local import receipts")
            serial = one(records, lambda r: r["target"]["device_key"] == keys[1], "retained serial receipt")
            hid = one(records, lambda r: r["target"]["device_key"] == keys[0], "new HID receipt")
            if previous_serial is not None:
                require(serial == previous_serial, "same serial capability across all three faults")
            previous_serial = serial
            hid_ids.add(hid["channel_id"])
            for record in records:
                receipt = record["state"]["receipt"]
                require(record["owner"]["uid"] == 2000 and record["owner"]["starttime"] > 0 and record["correlation"] is not None
                        and record["state"]["phase"] == "observed" and receipt["session"]["status"] == 6
                        and receipt["kernel_socket_inode"] > 0 and receipt["profile"] is not None
                        and receipt["session"]["local_device_generation"] is not None, "actual complete strong local receipt")
                is_hid = record["target"]["device_key"] == keys[0]
                expected = ((0x1234, 1 if is_hid else 2, 2, 1 if is_hid else 2) if args.backend == "virtual"
                            else ((1575, 1, 3, 1) if is_hid else (1027, 24577, 2, 1)))
                remote = record["target"]["remote"]
                require((remote["vendor_id"], remote["product_id"], remote["speed"], len(remote["interfaces"])) == expected
                        and receipt["session"]["vendor_id"] == expected[0] and receipt["session"]["product_id"] == expected[1]
                        and receipt["profile"]["configuration"] == 1 and len(receipt["profile"]["interfaces"]) == expected[3],
                        "actual local and remote metadata/configuration match requested device profile")
                locals_observed = data("client", f"local-identities-{round_id}.json")
                name = "hid" if is_hid else "serial"
                generation = receipt["session"]["local_device_generation"]
                require(locals_observed[name] == receipt["session"]["local_bus_id"]
                        and locals_observed[name + "_generation"] == f"{generation['filesystem_device']}:{generation['inode']}",
                        "direct sysfs observation matches actual receipt generation")
            hid_generations.add(json.dumps(hid["state"]["receipt"]["session"]["local_device_generation"], sort_keys=True))
    require(len(expected_tx) == 245, "seven actual 35-byte serial rounds")
    if args.backend == "usbfs":
        require((evidence / "ftdi-host-rx.bin").read_bytes() == expected_tx, "actual host receives 245 ordered FTDI bytes")
    require(len(hid_ids) == len(hid_generations) == 4, "fresh HID capabilities and local generations after each fault")
    for role in ("server", "client"):
        log = (evidence / f"{role}-evidence.log").read_text()
        kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
        require(log.count(f"RUSTUSB_LINK_RESULT: PASS role={role}") == 1 and all(mark not in log for mark in ("RUSTUSB_LINK_RESULT: FAIL", "SECURE_ARTIFACT_ERROR", "SECURE_ARTIFACT_OVERSIZE", "SECURE_POWEROFF_RETURNED")), "complete successful guest evidence")
        require("Kernel panic" not in kernel and "Oops:" not in kernel, "final complete S0 recheck")
        require(f"SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false" in log and "SECURE_RNG_RTC_READY crng=true " in log, "real clock/RNG and isolated keys")
        status = raw(role, "identity-agent-1.txt").decode()
        require(re.search(r"^Uid:\s+2000\s+2000\s+2000\s+2000$", status, re.M) is not None
                and all(re.search(rf"^{cap}:\s+0000000000000000$", status, re.M) for cap in ("CapInh", "CapPrm", "CapEff", "CapAmb")), "actual unprivileged agent identity")
        require(re.search(r"^Uid:\s+0\s+0\s+0\s+0$", raw(role, "identity-helper.txt").decode(), re.M) is not None, "actual root helper identity")
        require(raw(role, "wait-agent-1.txt").strip() == b"0", "actual agent TERM/reap0")
        final = data(role, "agent-final.json")
        expected_sessions, expected_jobs = (7, 1) if role == "server" else (5, 5)
        application_disk(final, role, application_schemas)
        require(len(final["sessions"]) == expected_sessions and len(final["jobs"]) == expected_jobs, "exact complete retained session/job history")
        require(len({s["id"] for s in final["sessions"]}) == expected_sessions and len({j["id"] for j in final["jobs"]}) == expected_jobs, "unique history identities")
        require(all(s["terminal"] and s["cleanup"] == "verified" and s["state"] == "Disconnected" for s in final["sessions"])
                and all(j["state"] == "finished" and j["operator_uid"] == 1000 for j in final["jobs"]), "actual final task/resource retirement")
        if role == "client":
            root = data(role, "root-final.json")
            require(root["schema_version"] == 2 and root["records"] == root["exports"] == [] and len(root["terminals"]) == 5, "exact client Root terminals and no active capabilities")
        elif args.backend == "usbfs":
            root = data(role, "root-final.json")
            require(set(root) == {"schema_version", "records", "terminals"} and root["schema_version"] == 3
                    and root["records"] == [] and len(root["terminals"]) == 2, "exact two real physical Finish terminals")
        if role == "server":
            require(raw(role, "native-final.status") == b"absent\n", "Rust final cleanup does not use Native exports")
        for session in final["sessions"]:
            if role == "client":
                proof(root, session)
            elif args.backend == "usbfs" and session["role"] == "export":
                physical_proof(root, session)
            else:
                require(session["attempts"] == [], "Rust engine/virtual terminal does not invent helper proof")
            require(any(j["id"] == session["job_id"] and j["instance_id"] == session["instance_id"] for j in final["jobs"]), "original actual job/session linkage")
        history = final["link_history"]
        require(len(history) == expected_sessions and len({h["session_id"] for h in history}) == expected_sessions, "one final health history per real session")
        for session in final["sessions"]:
            item = one(history, lambda h: h["session_id"] == session["id"] and h["instance_id"] == session["instance_id"], "health history identity")
            if session["role"] == "export":
                require(item["link"] is None, "export is not a data connection")
            else:
                require(item["link"]["protocol"] == "framed_v2" and item["link"]["phase"] == "ended"
                        and item["link"]["end"] is not None and item["link"]["last_reply_sequence"] is not None,
                        "actual ended v2 samples, never fabricated raw RTT")
        for phase in PHASES:
            held = data(role, f"agent-pre-{phase}.json")
            held_ids[role].update(s["id"] for s in held["sessions"])
        held_ids[role].update(s["id"] for s in data(role, "agent-final-held.json")["sessions"])
        require(held_ids[role] == {s["id"] for s in final["sessions"]}, "final history retains every actual held session")
        after = data(role, "agent-after-denied.json")
        require(all(after[name] == [] for name in ("jobs", "sessions", "requests")) and raw(role, "root-before-denied.json") == raw(role, "root-after-denied.json"), "unauthorized request has no resource effect")
        for reason in ("uid", "profile"):
            require(data(role, f"denied-{reason}.json")["response"] == {"result": "error", "code": "permission"}, "real typed authorization refusal")
    vhci("vhci-final.txt", 0)
    configuration = verify_collected_configuration(evidence, args.family, args.backend, extra_operators)
    wire = packet_audit.audit(evidence)
    return {"scope": "Rust same-data TLS idle/after-I/O blackhole/cleanup/reuse; no pending-URB or hardware claim",
            "backend": args.backend,
            "phases": phase_facts, "actual_configuration": configuration, "packet_audit": wire,
            "io_rounds": 7, "hid_key_codes": [2, 4, 6, 8] if args.backend == "usbfs" else [], "virtual_hid_scope": "real enumeration and idle fault, no injected key" if args.backend == "virtual" else None, "serial_each_direction_bytes": 245}


def verify_collected_configuration(evidence, family, backend, extra_operators=None):
    """Exact observed public configuration/DER and permission metadata only."""
    observations=[]
    def get(role,name):return (evidence/f'{role}-artifacts'/name).read_bytes()
    def require(ok,description):
        if not ok:raise RuntimeError('actual configuration evidence: '+description)
    public={name:(evidence/f'{name}-public.der').read_bytes() for name in ['ca','server','client']}
    pin=hashlib.sha256(public['client']).hexdigest()
    keys=json.loads(get('server','keys.json'))
    for role in ['server','client']:
        policy=json.loads(get(role,'helper-policy.json'))
        require(policy['allowed_uids']==[1000] and policy['secure_broker_uid']==2000 and policy['trusted_network'] is False and policy['export_device_ids']==[] and policy['peers']==[],f'{role} actual helper broker/legacy isolation')
        if role=='server':
            require(policy['secure_native_device_ids']==[] and policy['physical_device_ids']==[], 'Rust server grants cannot use legacy or Native pool')
            require(policy['secure_physical_device_ids']==([key.split(':',1)[1] for key in keys] if backend=='usbfs' else []),'actual approved USBFS pool matches public TLS devices')
        else:
            require(policy['secure_imports']==[{'server_id':'qemu-server','device_keys':keys}],f'{role} actual helper logical server/device pool matches client profiles')
        profiles=[{'id':'server','backend':backend,'config':'/etc/rustusb/server.json'}] if role=='server' else [{'id':profile,'backend':'client','config':f'/etc/rustusb/client-{profile}.json'} for profile in ['hid','serial']]
        operators=[{'uid':1000,'profiles':[p['id'] for p in profiles],'actions':['read','start','stop','discover','audit']}]
        operators += (extra_operators or {}).get(role, [])
        manifest={'schema_version':1,'broker_uid':2000,'helper_socket':'/run/rustusb/control.sock','control_directory':'/run/agent','data_directory':'/var/lib/rustusb/agent','profiles':profiles,'operators':operators}
        expected={'agent.json':manifest,'policy.json':policy}
        if role=='server':
            expected['server.json']={'schema_version':2,'data_link':DATA_LINK,'broker_uid':2000,'listen':'192.0.2.1:3241' if family=='ipv4' else '[fd42:7275:7374::1]:3241','devices':keys,'principals':[{'id':'qemu-client','pins':[pin],'devices':keys}],'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/server.der'],'private_key':'/etc/rustusb/server-key.der'}
        else:
            for profile,key in zip(['hid','serial'],keys,strict=True):
                expected[f'client-{profile}.json']={'schema_version':2,'data_link':DATA_LINK,'broker_uid':2000,'server_id':'qemu-server','endpoint':{'host':'192.0.2.1' if family=='ipv4' else 'fd42:7275:7374::1','port':3241},'server_name':'rustusb-qemu.test','device':key,'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/client.der'],'private_key':'/etc/rustusb/client-key.der'}
        directories={'/','/etc','/etc/rustusb','/run','/run/agent','/var','/var/lib','/var/lib/rustusb','/var/lib/rustusb/agent'}
        public_paths={'/etc/rustusb/'+name for name in expected}|{'/etc/rustusb/'+name+'.der' for name in public}|{'/etc/rustusb/client-pin.txt'}
        private='/etc/rustusb/'+role+'-key.der'
        for epoch in [1]:
            prefix=f'actual-config-{epoch}-'
            for name,value in expected.items():
                require(json.loads(get(role,prefix+name))==value,f'{role} epoch{epoch} actual {name} exact semantic fields')
                require(get(role,prefix+name)==get(role,'actual-config-1-'+name),f'{role} {name} bytes unchanged over restart')
            require(get(role,prefix+'agent.json')==get(role,'agent-manifest.json'),f'{role} actual startup manifest matches previously captured public manifest')
            require(get(role,prefix+'client-pin.txt')==(pin+'\n').encode(),f'{role} epoch{epoch} actual public pin text equals exact raw client DER SHA256')
            for name,der in public.items():
                require(get(role,prefix+name+'.der')==der,f'{role} epoch{epoch} actual {name} public DER equals retained host DER')
            metadata=json.loads(get(role,f'config-metadata-{epoch}.json'));by={row['path']:row for row in metadata}
            require(len(metadata)==len(by) and set(by)==directories|public_paths|{private},f'{role} epoch{epoch} exact fixed permission coverage')
            for path,row in by.items():
                require(row['canonical']==path and row['inode']>0 and row['device']>=0,f'{role} exact nonsymlink path {path}')
                mode=int(row['mode'],8)
                if path in directories:
                    broker=path in ['/run/agent','/var/lib/rustusb/agent']
                    require(row['kind']=='directory' and row['captured_file'] is None and row['uid']==row['gid']==(2000 if broker else 0) and (mode==(0o711 if path=='/run/agent' else 0o700) if broker else mode&0o022==0),f'{role} actual protected ancestor {path}')
                elif path==private:
                    require(row['kind']=='private_key_metadata_only' and row['uid']==row['gid']==2000 and mode==0o600 and row['links']==1 and row['captured_file'] is None and row['bytes']>0,f'{role} own key metadata only')
                else:
                    capture=prefix+path.rsplit('/',1)[1]
                    require(row['kind']=='public_file' and row['uid']==row['gid']==0 and mode==0o644 and row['links']==1 and row['captured_file']==capture and row['bytes']==len(get(role,capture)),f'{role} actual root-controlled public file {path}')
            require(not any(p.name.endswith('-key.der') for p in (evidence/f'{role}-artifacts').iterdir()),f'{role} no key bytes in artifacts')
            observations.append({'role':role,'epoch':epoch,'fixed_metadata_entries':len(metadata),'json_files':sorted(expected),'public_der_sha256':{name:hashlib.sha256(der).hexdigest() for name,der in public.items()},'private_key_bytes_captured':False})
    return observations
