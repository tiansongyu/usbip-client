"""Read actual terminal files; do not run product commands or modify captures."""
import hashlib
import json
import re
import struct


def verify(evidence, args, base):
    server = evidence / "server-artifacts"
    client = evidence / "client-artifacts"
    checks = []

    def require(condition, name):
        if not condition:
            raise RuntimeError("authentication evidence: " + name)
        checks.append(name)

    def data(root, name):
        return (root / name).read_bytes()

    def obj(root, name):
        return json.loads(data(root, name))

    def events(root, name):
        return [json.loads(line) for line in data(root, name).splitlines()]

    def proc(root, label):
        stat = data(root, f"auth-{label}.stat").decode()
        pid, _, fields = stat.partition(" (")
        tail = fields.rsplit(") ", 1)[1].split()
        status = data(root, f"auth-{label}.status").decode()
        uid = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", status, re.M)
        require(uid is not None, label + " UID receipt")
        executable = data(root, f"auth-{label}.exe").decode().split()[0]
        return {"pid": int(pid), "start": int(tail[19]), "uids": [int(x) for x in uid.groups()], "exe": executable}

    logs = {}
    for role in ["server", "client"]:
        log = (evidence / f"{role}-evidence.log").read_text()
        logs[role] = log
        require(f"RUSTUSB_SECURE_RESULT: PASS role={role}" in log, role + " terminal PASS")
        require(not any(mark in log for mark in ["RUSTUSB_SECURE_RESULT: FAIL", "SECURE_ARTIFACT_ERROR", "SECURE_ARTIFACT_OVERSIZE", "SECURE_POWEROFF_RETURNED"]), role + " no failed/partial collection")
        require(f"SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false" in log, role + " isolated private roles")
        require("SECURE_RNG_RTC_READY crng=true " in log, role + " real RNG/time")
        kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
        require("Kernel panic" not in kernel and "Oops:" not in kernel, role + " no reported kernel fault")
    first_policy = obj(server, "auth-server-config-1.json")
    second_policy = obj(server, "auth-server-config-2.json")
    public_names = ["client", "replacement", "limited", "unmapped", "expired", "badeku", "outsider"]
    pins = {name: hashlib.sha256((evidence / f"{name}-public.der").read_bytes()).hexdigest() for name in public_names}
    require(len(set(pins.values())) == len(pins), "all credential leaves distinct")
    require(first_policy["devices"] == second_policy["devices"], "same device pool across restart")
    pool = first_policy["devices"]
    first = {row["id"]: row for row in first_policy["principals"]}
    second = {row["id"]: row for row in second_policy["principals"]}
    require(first.pop("old-client")["pins"] == [pins["client"]] and first == second, "only old principal removed")
    require(second["replacement"]["pins"] == [pins["replacement"]] and second["replacement"]["devices"] == pool, "replacement remains authorized")
    require(second["restricted"] == {"id": "restricted", "pins": [pins["limited"]], "devices": [pool[0]]}, "restricted exact HID principal")
    for name, rule in [("expired", "expired"), ("badeku", "badeku"), ("outsider", "unknown-ca")]:
        require(second[rule]["pins"] == [pins[name]], name + " leaf is pinned despite PKI failure")
    require(not any(pins["unmapped"] in row["pins"] for row in second.values()), "valid unmapped leaf absent")
    retiring = proc(server, "retiring-server")
    replacement = proc(server, "replacement-server")
    client_helper_before = proc(client, "client-helper-before-free-1")
    client_helper_after = proc(client, "client-helper-after-held-2")
    require(client_helper_before == client_helper_after and client_helper_before["uids"] == [0] * 4, "same actual client helper across both epochs")
    helper_before = proc(server, "retained-helper") if args.backend != "virtual" else client_helper_before
    helper_after = proc(server, "retained-helper-after") if args.backend != "virtual" else client_helper_after
    require(retiring["pid"] != replacement["pid"] and retiring["start"] <= replacement["start"], "actual server process replaced")
    require(retiring["uids"] == replacement["uids"] == [2000] * 4, "both broker epochs ordinary UID2000")
    require(retiring["exe"] == replacement["exe"] == base.digest(base.REPO / "target/release/rustusb"), "both actual broker ELFs")
    require(helper_before == helper_after and helper_before["uids"] == [0] * 4, "same actual existing root helper retained")
    require(helper_before["exe"] == base.digest(base.REPO / "target/release/rustusb-helper"), "retained helper actual ELF")
    for profile in ["hid", "serial"]:
        config = obj(client, f"auth-client-{profile}-epoch2.json")
        require(config["certificate_chain"] == ["/etc/rustusb/replacement.der"] and config["private_key"] == "/etc/rustusb/replacement-key.der", profile + " actual replacement configuration")
    require(data(server, "wait-retiring-server.txt").strip() == b"0", "original server actual wait0")
    require(data(server, "wait-server.txt").strip() == b"0", "replacement server actual wait0")
    first_events = events(server, "auth-server-epoch1.events")
    final_events = events(server, "server.events")
    for epoch, rows in [(1, first_events), (2, final_events)]:
        require(sum(x.get("event") == "ready" for x in rows) == 1 and sum(x.get("event") == "stopped" for x in rows) == 1, f"epoch{epoch} ready/stopped receipts")
        require(all(x.get("event") != "error" for x in rows), f"epoch{epoch} no top-level operation error")
    catalog = obj(client, "catalog-initial.json")
    require(len(catalog) == 2 and [row["device_key"] for row in catalog] != [], "actual initial catalog")
    remote = {row["device_key"]: row["device"] for row in catalog}
    require(set(remote) == set(pool), "catalog and server stable pool match")
    labels = ["free-1", "held-1", "free-2", "held-2"]
    wire_results = []
    for label in labels:
        security = {side: obj(server, f"auth-security-{side}-{label}.json") for side in ["before", "after"]}
        for side, sample in security.items():
            require(set(sample) == {"scope", "tls", "framing", "listener_connection_limit", "saturated", "consistent"}, label + side + " closed security snapshot")
            require(sample["scope"] == "loaded_tls_server_configuration" and sample["consistent"] is True and sample["saturated"] is False, label + side + " explicit coherent volatile scope")
            tls = sample["tls"]
            require(set(tls) == {"attempts", "in_flight", "authenticated", "handshake_failed", "timed_out", "unmapped_certificate", "protocol_mismatch", "connection_limit", "cancelled"}, label + side + " closed TLS categories")
            require(all(type(value) is int and 0 <= value <= 2**64-1 for value in tls.values()), label + side + " bounded numeric TLS fields")
            require(tls["attempts"] == sum(value for key, value in tls.items() if key != "attempts"), label + side + " coherent TLS accounting")
            require(sample["listener_connection_limit"] == 0 and tls["connection_limit"] == 0, label + side + " no unobserved quota refusal")
            require(set(sample["framing"]) == {"attempts", "in_flight", "ready", "failed", "timed_out", "cancelled"} and all(type(value) is int and value == 0 for value in sample["framing"].values()), label + side + " raw-v1 no fabricated framing")
        before, after = security["before"]["tls"], security["after"]["tls"]
        require(all(after[key] >= before[key] for key in before if key != "in_flight"), label + " monotonic TLS totals")
        require(after["unmapped_certificate"] - before["unmapped_certificate"] == (2 if label.endswith("2") else 1), label + " actual unmapped/retired local refusal count")
        # Rustls may reject ALPN during negotiation or the fixed post-handshake
        # check. Do not infer certificate causes from their aggregate IO errors.
        require(after["handshake_failed"] + after["protocol_mismatch"] - before["handshake_failed"] - before["protocol_mismatch"] == 8, label + " actual generic handshake/protocol refusal count")
        require(after["authenticated"] > before["authenticated"], label + " real locally authenticated attempts counted")
        require(after["in_flight"] == 0 and after["timed_out"] == before["timed_out"] and after["cancelled"] == before["cancelled"], label + " all hostile TLS attempts resolved")
        for marker in ["WAIT", "SAVED", "CHECKED"]:
            require(f"AUTH_SERVER_{marker} label={label}\n" in logs["server"], label + " server " + marker)
        require(f"AUTH_CLIENT_DONE label={label}\n" in logs["client"], label + " actual client completion")
        for suffix in ["txt", "presence"]:
            require(data(client, f"auth-client-before-{label}.{suffix}") == data(client, f"auth-client-after-{label}.{suffix}"), label + " original client " + suffix)
        presence = data(client, f"auth-client-before-{label}.presence").strip()
        require(presence in [b"present", b"absent"], label + " journal presence")
        if presence == b"present":
            require(data(client, f"auth-client-before-{label}.json") == data(client, f"auth-client-after-{label}.json"), label + " original client journal")
        if label.startswith("held"):
            require(presence == b"present" and len(obj(client, f"auth-client-before-{label}.json")["records"]) == 2, label + " two original import records")
        for name in ["unmapped", "expired", "badeku", "outsider", "wrong-name", "wrong-trust"] + (["retired"] if label.endswith("2") else []):
            require(data(client, f"auth-{label}-{name}.wait").strip() == b"1", label + " " + name + " actual failure exit")
            require(data(client, f"auth-{label}-{name}.stdout") == b"", label + " " + name + " no authorized output")
            require(bool(data(client, f"auth-{label}-{name}.stderr")), label + " " + name + " error retained")
        for name in ["absent-cert", "absent-alpn", "wrong-alpn"]:
            require(data(client, f"auth-{label}-{name}.reply") == b"" and data(client, f"auth-{label}-{name}.wait").strip() == b"1", label + " " + name + " rejected actual wire client")
        for name, status in [("unauthorized", 4)] + ([("duplicate", 2)] if label.startswith("held") else []):
            request = data(client, f"auth-{label}-unauthorized.request")
            length = struct.unpack(">I", request[:4])[0]
            require(0 < length < 256 and len(request) == 4 + length + 40, label + " exact direct IMPORT request size")
            prelude = json.loads(request[4:4 + length])
            require(prelude == {"schema_version": 1, "op": "import", "device_key": pool[1]}, label + " actual serial stable key")
            require(request[4 + length:12 + length] == struct.pack(">HHI", 0x111, 0x8003, 0), label + " standard IMPORT header")
            bus = remote[pool[1]]["bus_id"].encode()
            require(request[12 + length:] == bus.ljust(32, b"\0"), label + " exact actual BusID")
            require(data(client, f"auth-{label}-{name}.reply") == struct.pack(">HHI", 0x111, 3, status), label + " " + name + " actual standard failure reply")
            require(data(client, f"auth-{label}-{name}.wait").strip() in [b"0", b"1"], label + " " + name + " actual bounded wait")
            wire_results.append({"batch": label, "kind": name, "status": status})
        if label.startswith("free"):
            limited = obj(client, f"auth-{label}-limited.json")
            require(len(limited) == 1 and limited[0]["device_key"] == pool[0], label + " actual restricted catalog")
        for side in ["before", "after"]:
            sample = obj(server, f"auth-{side}-{label}.json")
            require(len(sample["devices"]) == 2, label + side + " server two original devices")
        if args.backend == "native":
            for prefix in ["server-native-auth-", "host-auth-"]:
                require(data(server, f"{prefix}before-{label}.json") == data(server, f"{prefix}after-{label}.json"), label + " exact original " + prefix)
        elif args.backend == "usbfs":
            for prefix in ["auth-physical-", "host-auth-"]:
                require(data(server, f"{prefix}before-{label}.json") == data(server, f"{prefix}after-{label}.json"), label + " exact original " + prefix)
        else:
            require(obj(server, f"auth-before-{label}.json") == obj(server, f"auth-after-{label}.json"), label + " original virtual lease/counters")
    receipts = []
    for cycle in [1, 2]:
        for direction, ending in [("RX", "0123456789abcdef"), ("TX", "fedcba9876543210")]:
            require(data(client, f"{direction.lower()}-{cycle}.bin") == f"RUSTUSB-{direction}-{cycle}-0001:{ending}\n".encode(), f"epoch{cycle} actual serial {direction}")
        input_bytes = data(client, f"input-{cycle}.bin")
        require(len(input_bytes) % 24 == 0, f"epoch{cycle} complete input_event records")
        transitions = {struct.unpack("<qqHHi", input_bytes[i:i + 24])[2:] for i in range(0, len(input_bytes), 24)}
        require({(1, cycle + 1, 0), (1, cycle + 1, 1)} <= transitions, f"epoch{cycle} actual HID down/up")
        if args.backend == "virtual":
            require(data(client, f"echo-{cycle}.bin") == data(client, f"tx-{cycle}.bin"), f"epoch{cycle} CDC echo")
        for profile in ["hid", "serial"]:
            rows = events(client, f"attach-{profile}-{cycle}.events")
            connected = [x for x in rows if x.get("event") == "connected"]
            require(len(connected) == 1 and connected[0]["transport"] == "mtls", f"epoch{cycle} {profile} original connected receipt")
            require(any(x.get("event") == "disconnected" and x.get("cleanup_verified") is True for x in rows), f"epoch{cycle} {profile} verified cleanup")
            require(data(client, f"wait-{profile}-{cycle}.txt").strip() == b"0", f"epoch{cycle} {profile} actual wait0")
            receipts.append(connected[0]["receipt"])
        empty = obj(client, f"channels-empty-{cycle}.json")
        require(empty["records"] == [] and empty["exports"] == [], f"epoch{cycle} client journal empty")
        require(obj(client, f"catalog-reuse-{cycle}.json") == catalog, f"epoch{cycle} catalog reuse by actual configured client")
        ports = data(client, f"native-empty-{cycle}.txt").decode().splitlines()
        states = [line.split()[2] for line in ports if line.startswith(("hs ", "ss "))]
        require(bool(states) and all(re.fullmatch(r"[0-9]{1,3}", value) and int(value, 10) == 4 for value in states), f"epoch{cycle} all actual VHCI ports NULL")
        require(f"SECURE_CLIENT_RELEASED cycle={cycle} signal=SIGTERM wait=0 kernel_null=true local_gone=true" in logs["client"], f"epoch{cycle} original local generations Gone")
    require(len({x["channel_id"] for x in receipts}) == 4, "four distinct original client channel IDs")
    if args.backend != "virtual":
        for name in ["host-after-denied.json", "auth-host-after-retire.json", "host-final.json"]:
            require(data(server, name) == data(server, "host-before.json"), "complete physical baseline " + name)
        expected = b"".join(f"RUSTUSB-TX-{n}-0001:fedcba9876543210\n".encode() for n in [1, 2])
        require((evidence / "ftdi-host-rx.bin").read_bytes() == expected, "actual host FTDI70B")
    if args.backend == "native":
        leases, stubs = [], []
        for cycle in [1, 2]:
            exported = obj(server, f"auth-native-epoch{cycle}-exported.json")["exports"]
            held = obj(server, f"server-native-held-{cycle}.json")["exports"]
            released = obj(server, f"server-native-released-{cycle}.json")["exports"]
            clean = lambda rows: sorted(({k: v for k, v in row.items() if k != "stub"} for row in rows), key=lambda row: row["lease_id"])
            require(len(exported) == len(held) == len(released) == 2 and clean(exported) == clean(held) == clean(released), f"epoch{cycle} exact original export registrations")
            require(all(x["stub"]["state"]["phase"] == "observed" and x["stub"]["state"]["receipt"]["usbip_status"] == 2 for x in held), f"epoch{cycle} original stub actual USED")
            require(all(x["stub"] is None for x in released), f"epoch{cycle} original stubs retired")
            leases.extend(x["lease_id"] for x in exported)
            stubs.extend(x["stub"]["channel_id"] for x in held)
        require(len(set(leases)) == len(set(stubs)) == 4, "fresh export/stub identities after real server restart")
        for name in ["auth-native-retired.json", "server-native-final.json"]:
            state = obj(server, name)
            require(state["exports"] == state["records"] == [], name + " actual empty helper")
    if args.backend == "usbfs":
        for name in ["auth-physical-retired.json", "physical-final.json"]:
            require(obj(server, name)["records"] == [], name + " original grants retired")
    return {"checks": checks, "scope": "one paired run, two same-guest credential epochs, four hostile batches, real HID/serial I/O and actual cleanup; predicates are not USB cases",
            "original_server": retiring, "new_server": replacement, "same_helper": helper_before,
            "wire_import_failures": wire_results, "client_channels": [x["channel_id"] for x in receipts],
            "pcap_scope": "Raw retained; no new classifier verdict for mixed successful and rejected handshakes",
            "limitations": ["startup pin retirement, not CRL/OCSP or hot revocation", "raw-v1 only", "no hardware G1/G2 or complete G3", "fast negative CLI/openssl children lack per-executed-ELF samples; fixed setpriv/config/source and real waits retained"]}
