#!/usr/bin/env python3
"""Independently reconcile raw two-sided PCAP bytes with the valve journal.

A packet audit result is not a USB, heartbeat deadline, Catalog, or cleanup
verdict. It establishes the exact flow/drop boundary and encrypted wire facts.
No existing QEMU verifier/pcap_review is called. No private keys are read.
"""
import argparse
from collections import Counter, defaultdict
import ipaddress
import json
from pathlib import Path
import re
import struct
import stat

from packets import Flow, MACS, MAX_FRAME, ROLES, decode, digest, validate_private
from valve import Binding, DIRECTIONS, MAX_LOG_BYTES, MAX_LOG_RECORDS

MAX_PCAP_BYTES = 64 * 1024 * 1024
MAX_PACKETS = 100_000
MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_BINDING_BYTES = 1024 * 1024
MAX_CLIENT_HELLO = 64 * 1024
MAX_HELLO_EXTENSIONS = 64
V2_ALPN = b"rustusb-usbip/2"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def bounded(path, limit):
    require(path.is_file() and path.stat().st_size <= limit, "missing/oversized artifact: " + path.name)
    raw = path.read_bytes()
    require(len(raw) <= limit, "artifact grew beyond bound")
    return raw


def _network_observation(value, role, stage, number):
    require(set(value) == {"schema_version", "role", "stage", "observation", "guest_uptime", "carrier", "operstate"},
            "network observation fields changed")
    require(value["schema_version"] == 1 and value["role"] == role and value["stage"] == stage
            and type(value["observation"]) is int and value["observation"] == number,
            "wrong network observation identity/order")
    uptime = value["guest_uptime"]
    require(isinstance(uptime, str) and re.fullmatch(r"[0-9]{1,10}\.[0-9]{2}", uptime),
            "invalid actual guest uptime")
    require(type(value["carrier"]) is int and value["carrier"] in (0, 1)
            and value["operstate"] in ("unknown", "notpresent", "down", "lowerlayerdown", "testing", "dormant", "up"),
            "invalid actual carrier/operstate")
    seconds, fraction = uptime.split(".")
    return int(seconds) * 100 + int(fraction)


def network_ready_sample(directory, role):
    """Validate actual read-only link observations before host acknowledges them."""
    require(role in ROLES, "unknown network-ready role")
    directory = Path(directory)
    names = ("network-ready-observations.jsonl", "network-ready.json", "network-ready.txt")
    originals = {name: bounded(directory / name, 64 * 1024) for name in names}
    lines = originals[names[0]].splitlines(keepends=True)
    require(1 <= len(lines) <= 200 and all(line.endswith(b"\n") for line in lines), "network poll count/complete records")
    samples = [json.loads(line) for line in lines]
    times = [_network_observation(sample, role, "ready", number) for number, sample in enumerate(samples, 1)]
    require(all(earlier <= later for earlier, later in zip(times, times[1:]))
            and times[-1] - times[0] <= 1000, "network readiness exceeds bounded monotonic observation window")
    require(all(sample["carrier"] != 1 or sample["operstate"] != "up" for sample in samples[:-1]),
            "network polling continued after readiness")
    require(samples[-1]["carrier"] == 1 and samples[-1]["operstate"] == "up"
            and originals[names[1]] == lines[-1], "network-ready is not the actual first complete ready sample")
    snapshot = originals[names[2]].decode()
    interface = re.search(r"^\d+: eth0: <([^>]+)>", snapshot, re.M)
    last = 1 if role == "server" else 2
    require(interface is not None and "UP" in interface[1].split(",") and "NO-CARRIER" not in interface[1].split(",")
            and f"inet 192.0.2.{last}/24 " in snapshot
            and f"inet6 fd42:7275:7374::{last}/64 " in snapshot, "actual ready interface/address snapshot incomplete")
    return {"role": role, "poll_count": len(samples), "guest_uptime": samples[-1]["guest_uptime"],
            "carrier": 1, "operstate": "up", "artifact_sha256": {name: digest(raw) for name, raw in originals.items()}}


def network_readiness(evidence):
    """Check captured readiness and host ACK/START ordering, without cross-guest clock arithmetic."""
    evidence = Path(evidence)
    controls = [json.loads(line) for line in bounded(evidence / "host-control.jsonl", 64 * 1024).splitlines()]
    require(1 <= len(controls) <= 100 and all(type(row["monotonic_ns"]) is int and row["monotonic_ns"] > 0 for row in controls),
            "bounded actual host control timestamps required")
    require(all(a["monotonic_ns"] <= b["monotonic_ns"] for a, b in zip(controls, controls[1:])), "host control clock/order changed")
    starts = [(number, row) for number, row in enumerate(controls) if row["command"] == "START"]
    require(len(starts) == 1 and starts[0][1]["mark"] == "start" and starts[0][1]["role"] == "client", "one actual client START required")
    start_index, start = starts[0]
    facts = []
    for role in ROLES:
        observed = network_ready_sample(evidence / f"{role}-artifacts", role)
        require(network_ready_sample(evidence / "live" / role, role) == observed, "ready raw bytes changed after host ACK")
        acknowledgements = [(number, row) for number, row in enumerate(controls)
                            if row["mark"] == f"network-ready-{role}"]
        require(len(acknowledgements) == 1, "one actual network ACK for each role")
        ack_index, ack = acknowledgements[0]
        require(ack["role"] == role and ack["command"] == "NETWORK_READY_ACK" and ack["observed"] == observed
                and ack_index < start_index and ack["monotonic_ns"] <= start["monotonic_ns"],
                "START preceded validated actual link readiness")
        log = bounded(evidence / f"{role}-evidence.log", MAX_LOG_BYTES).decode()
        ready = f"LINK_NETWORK_READY role={role}\n"
        accepted = f"LINK_NETWORK_READY_ACK role={role}\n"
        require(log.count(ready) == log.count(accepted) == 1 and log.index(ready) < log.index(accepted),
                "guest did not observe its network barrier acknowledgement")
        if role == "client":
            before_probe = bounded(evidence / f"{role}-artifacts" / "network-probe-start.json", 4096)
            require(before_probe == bounded(evidence / "live" / role / "network-probe-start.json", 4096), "actual probe readiness bytes changed")
            sample = json.loads(before_probe)
            probe_time = _network_observation(sample, role, "negative-probes", 1)
            seconds, fraction = observed["guest_uptime"].split(".")
            require(probe_time >= int(seconds) * 100 + int(fraction) and sample["carrier"] == 1 and sample["operstate"] == "up",
                    "negative connection probe preceded actual carrier readiness")
            marker = "LINK_NETWORK_PROBES_START\n"
            require(log.count(marker) == 1 and log.index(accepted) < log.index(marker), "negative probe preceded the guest ACK barrier")
            observed["probe_start_sha256"] = digest(before_probe)
            observed["probe_start_guest_uptime"] = sample["guest_uptime"]
        observed["ack_host_monotonic_ns"] = ack["monotonic_ns"]
        facts.append(observed)
    return {"scope": "Actual initial carrier/operstate and address precondition; not TLS health or USB completion",
            "roles": facts, "start_host_monotonic_ns": start["monotonic_ns"]}


def pcap(path, family):
    raw = bounded(path, MAX_PCAP_BYTES)
    require(len(raw) >= 24, "missing PCAP header")
    magic = raw[:4]
    require(magic in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4"), "unsupported PCAP encoding")
    endian = "<" if magic[0] == 0xd4 else ">"
    _, major, minor, _, _, snaplen, network = struct.unpack(endian + "IHHIIII", raw[:24])
    require((major, minor, network) == (2, 4, 1) and snaplen >= 65535, "non-Ethernet/truncated PCAP")
    offset, count = 24, 0
    result = {role: [] for role in ROLES}
    reverse_macs = {value: role for role, value in MACS.items()}
    while offset < len(raw):
        require(count < MAX_PACKETS and offset + 16 <= len(raw), "PCAP packet bound/header")
        _, micros, captured, original = struct.unpack(endian + "IIII", raw[offset:offset + 16])
        offset += 16
        require(micros < 1_000_000 and 14 <= captured == original <= min(MAX_FRAME, snaplen), "PCAP truncated packet/clock")
        frame = raw[offset:offset + captured]
        require(len(frame) == captured, "PCAP incomplete frame")
        offset += captured
        packet = decode(frame)
        require(packet.source_mac in reverse_macs, "unaccounted Ethernet sender")
        origin = reverse_macs[packet.source_mac]
        validate_private(packet, origin, family)
        result[origin].append((frame, packet))
        count += 1
    return result, {"bytes": len(raw), "sha256": digest(raw), "packets": count}


def _socket_address(value):
    require(isinstance(value, str) and len(value) <= 64, "invalid health socket address")
    host, port = value.rsplit(":", 1)
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    host = str(ipaddress.ip_address(host))
    require(port.isascii() and port.isdecimal(), "invalid health port")
    return host, int(port)


def health_binding(evidence, rule, role):
    binding = rule["binding"]
    raw = bounded(evidence / "bindings" / f'{rule["rule_id"]}-{role}-health.json', MAX_BINDING_BYTES)
    require(digest(raw) == binding[f"{role}_health_sha256"], "arm does not match actual health bytes")
    value = json.loads(raw)
    response = value["response"]
    require(value["schema_version"] == 1 and response["result"] == "session_health", "wrong health RPC response")
    sessions = response["sessions"]
    require(isinstance(sessions, list) and len(sessions) <= 16, "health page bound")
    matches = [item for item in sessions if item["session_id"] == binding[f"{role}_session"]]
    require(len(matches) == 1, "bound health session is not unique")
    sample = matches[0]
    require(sample["instance_id"] == binding[f"{role}_instance"] and sample["availability"] == "live", "non-live/wrong epoch binding")
    require(sample["role"] == ("import" if role == "client" else "incoming"), "wrong data-session role")
    link = sample["link"]
    require(link["protocol"] == "framed_v2" and link["phase"] == "open" and link["end"] is None, "non-live v2 data link")
    identity = link["identity"]
    require(identity["connection"] == binding[f"{role}_connection"], "connection identity mismatch")
    local, peer = _socket_address(identity["tcp"]["local"]), _socket_address(identity["tcp"]["peer"])
    flow = Flow.from_value(rule["flow"])
    expected = flow if role == "client" else flow.reverse()
    require((local, peer) == ((expected.src, expected.sport), (expected.dst, expected.dport)), "health tuple mismatch")
    require(type(link["last_reply_sequence"]) is int and link["last_reply_sequence"] > 0
            and type(link["last_round_trip_micros"]) is int and link["last_round_trip_micros"] >= 0,
            "no actual pre-arm matching reply/RTT")
    return {"role": role, "sha256": digest(raw), "session_id": sample["session_id"],
            "instance_id": sample["instance_id"], "identity": identity}


def _tls13(body):
    if not body or body[0] != 2:
        return False
    require(len(body) >= 44 and int.from_bytes(body[1:4], "big") == len(body) - 4, "ServerHello shape")
    cursor = 39 + body[38]
    require(cursor + 5 <= len(body), "ServerHello session ID")
    cursor += 3
    end = cursor + 2 + int.from_bytes(body[cursor:cursor + 2], "big")
    cursor += 2
    require(end == len(body), "ServerHello extensions size")
    selected = None
    while cursor < end:
        require(cursor + 4 <= end, "short ServerHello extension")
        kind, size = struct.unpack("!HH", body[cursor:cursor + 4])
        cursor += 4
        require(cursor + size <= end, "ServerHello extension overrun")
        if kind == 43:
            require(selected is None, "duplicate TLS supported_versions")
            selected = body[cursor:cursor + size]
        cursor += size
    return selected == b"\x03\x04"


def _client_alpn(message):
    """Parse one complete plaintext ClientHello; never search ciphertext for ALPN.

    RFC8446 section4.1.2 and RFC7301 section3.1 length-delimited shapes. These
    fixed broker fixtures allow a bounded ClientHello and exactly one v2 offer,
    not arbitrary TLS client fingerprints or decrypted negotiated-ALPN claims.
    """
    require(45 <= len(message) <= MAX_CLIENT_HELLO and message[0] == 1
            and int.from_bytes(message[1:4], "big") == len(message) - 4,
            "ClientHello handshake length/type")
    require(message[4:6] == b"\x03\x03", "ClientHello legacy version")
    session_size = message[38]
    cursor = 39 + session_size
    require(session_size <= 32 and cursor + 2 <= len(message), "ClientHello session ID")
    cipher_size = int.from_bytes(message[cursor:cursor + 2], "big")
    cursor += 2
    require(cipher_size >= 2 and cipher_size % 2 == 0
            and cursor + cipher_size + 2 <= len(message), "ClientHello cipher suites")
    cursor += cipher_size
    compression_size = message[cursor]
    cursor += 1
    require(compression_size == 1 and message[cursor:cursor + 1] == b"\0",
            "ClientHello TLS1.3 compression")
    cursor += 1
    require(cursor + 2 <= len(message), "ClientHello extensions missing")
    end = cursor + 2 + int.from_bytes(message[cursor:cursor + 2], "big")
    cursor += 2
    require(end == len(message), "ClientHello extension vector length")
    seen, protocols = set(), None
    while cursor < end:
        require(cursor + 4 <= end and len(seen) < MAX_HELLO_EXTENSIONS,
                "ClientHello extension header/count")
        kind, size = struct.unpack("!HH", message[cursor:cursor + 4])
        cursor += 4
        require(kind not in seen and cursor + size <= end,
                "ClientHello duplicate/truncated extension")
        seen.add(kind)
        value = message[cursor:cursor + size]
        cursor += size
        if kind != 16:
            continue
        require(len(value) >= 3 and int.from_bytes(value[:2], "big") == len(value) - 2,
                "ClientHello ALPN vector length")
        protocols, offset = [], 2
        while offset < len(value):
            length = value[offset]
            offset += 1
            require(length > 0 and offset + length <= len(value), "ClientHello ALPN name length")
            protocols.append(value[offset:offset + length])
            offset += length
        require(offset == len(value), "ClientHello ALPN trailing bytes")
    require(protocols == [V2_ALPN], "ClientHello must offer exactly framed-v2 ALPN")
    return ["rustusb-usbip/2"]


def tls_facts(flow, packets, armed_at):
    syns = {packet.sequence for _, packet in packets if packet.flags & 2}
    require(len(syns) == 1, "capture missing SYN or tuple reused")
    start = (next(iter(syns)) + 1) % 2**32
    chunks = [((packet.sequence + bool(packet.flags & 2) - start) % 2**32, packet.payload, index)
              for index, packet in packets if packet.payload]
    require(chunks, "TLS direction lacks actual payload")
    wire = bytearray()
    last_payload_index = 0
    for offset, chunk, index in sorted(chunks, key=lambda item: (item[0], item[2])):
        require(offset <= len(wire) and offset + len(chunk) <= MAX_STREAM_BYTES, "source TCP gap/stream bound")
        overlap = min(len(chunk), len(wire) - offset)
        require(wire[offset:offset + overlap] == chunk[:overlap], "conflicting TCP retransmission")
        wire.extend(chunk[overlap:])
        last_payload_index = max(last_payload_index, index)
    require(wire[0] == 22, "data flow did not start with TLS handshake")
    for marker in (b"\x01\x11\x80\x05\0\0\0\0", b"\x01\x11\x80\x03\0\0\0\0", b"RUSTUSB-RX-", b"RUSTUSB-TX-", b'"op":"catalog"', b'"op":"import"'):
        require(marker not in wire, "recognizable clear management/fixture bytes")
    position = records = application = 0
    selected13 = False
    client_hellos, client_handshake = 0, bytearray()
    while position < len(wire):
        if position + 5 > len(wire):
            break
        content, version, size = struct.unpack("!BHH", wire[position:position + 5])
        require(content in (20, 21, 22, 23) and version in (0x0301, 0x0303) and size <= 18432,
                "invalid TLS record prefix")
        end = position + 5 + size
        if end > len(wire):
            break
        if content == 22:
            if flow.sport == 3241:
                selected13 |= _tls13(wire[position + 5:end])
            else:
                client_handshake.extend(wire[position + 5:end])
                require(len(client_handshake) <= MAX_CLIENT_HELLO, "ClientHello reassembly bound")
                while len(client_handshake) >= 4:
                    length = 4 + int.from_bytes(client_handshake[1:4], "big")
                    require(45 <= length <= MAX_CLIENT_HELLO, "ClientHello declared length bound")
                    if len(client_handshake) < length:
                        break
                    require(client_hellos < 2, "unexpected repeated ClientHello")
                    _client_alpn(bytes(client_handshake[:length]))
                    del client_handshake[:length]
                    client_hellos += 1
        elif content == 23 and flow.dport == 3241:
            require(not client_handshake, "ClientHello incomplete before encrypted records")
        records += 1
        application += content == 23
        position = end
    tail = len(wire) - position
    require(not tail or (armed_at is not None and last_payload_index > armed_at), "unexplained incomplete TLS tail")
    require(application > 0, "no complete encrypted TLS records")
    if flow.sport == 3241:
        require(selected13, "TLS1.3 selection missing")
    else:
        require(client_hellos >= 1 and not client_handshake, "complete ClientHello ALPN offer missing")
    return {"flow": flow.value(), "source_bytes": len(wire), "sha256": digest(wire),
            "complete_records": records, "complete_encrypted_records": application,
            "client_hellos": client_hellos if flow.dport == 3241 else None,
            "offered_alpn": ["rustusb-usbip/2"] if flow.dport == 3241 else None,
            "unconfirmed_tail_bytes_after_target_fault": tail}


def audit(evidence):
    evidence = Path(evidence)
    readiness = network_readiness(evidence)
    raw = bounded(evidence / "valve.jsonl", MAX_LOG_BYTES)
    require(raw.endswith(b"\n"), "valve final line incomplete")
    lines = raw.splitlines()
    require(2 <= len(lines) <= MAX_LOG_RECORDS, "valve record count")
    rows = [json.loads(line) for line in lines]
    start = rows[0]
    require(start["event"] == "start" and start["mechanism"] == "ethernet_drop_fd_retained"
            and start["macs"] == MACS and start["max_frame"] == MAX_FRAME, "wrong valve start")
    family = start["family"]
    require(family in (4, 6) and set(start["endpoint_fds"]) == set(ROLES), "invalid valve family/FD roles")
    identities = set()
    for item in start["endpoint_fds"].values():
        require(set(item) == {"fd", "dev", "ino", "mode"}
                and all(type(value) is int and value >= 0 for value in item.values())
                and stat.S_ISSOCK(item["mode"]), "invalid actual socket FD facts")
        identities.add((item["dev"], item["ino"]))
    require(len(identities) == 2, "valve endpoints are not distinct socket objects")
    captures, capture_meta = {}, {}
    for role in ROLES:
        captures[role], capture_meta[role] = pcap(evidence / f"{role}-external.pcap", family)
    source_index, seen, queued, forwarded = Counter(), {}, set(), {role: [] for role in ROLES}
    flow_packets, syn, payload_count = defaultdict(list), {}, Counter()
    rule, rules, rule_ids, used_flows = None, [], set(), set()
    closed_roles, exit_allowed, finished = set(), False, False
    last_clock = 0
    reset_families = set()
    refusal_syns, refusal_proofs = {}, []
    for number, row in enumerate(rows, 1):
        require(row["schema_version"] == 1 and row["index"] == number and type(row["monotonic_ns"]) is int
                and row["monotonic_ns"] >= last_clock, "valve ordering/clock")
        last_clock = row["monotonic_ns"]
        event = row["event"]
        if number == 1:
            continue
        require(not finished or event == "close", "events after finish")
        if event == "packet":
            require(not finished, "packet after finish")
            origin, packet_id = row["origin"], row["packet_id"]
            require(origin in ROLES and packet_id == len(seen) + 1, "packet identity/order")
            source = captures[origin][origin]
            index = source_index[origin]
            require(index < len(source), "journal packet missing from origin PCAP")
            frame, packet = source[index]
            source_index[origin] += 1
            require(row["bytes"] == len(frame) and row["sha256"] == digest(frame)
                    and row["packet"] == packet.facts(), "journal metadata differs from raw PCAP")
            selected = rule and packet.flow in (rule["target"], rule["target"].reverse())
            drop = bool(selected and origin in DIRECTIONS[rule["direction"]])
            require(row["rule_id"] == (rule["rule_id"] if rule else None)
                    and row["action"] == ("drop" if drop else "queue"), "wrong flow/direction drop decision")
            seen[packet_id] = (origin, frame, packet)
            if drop:
                rule["actual_drops"][origin] += 1
            else:
                queued.add(packet_id)
            if packet.flow is not None:
                flow = packet.flow
                service_port = flow.dport if origin == "client" else flow.sport
                if service_port == 3240:
                    if origin == "client" and packet.flags & 2:
                        require(packet.flags & ~0xc2 == 0 and not packet.payload,
                                "3240 refusal request is not a plain SYN")
                        require(refusal_syns.get(flow) in (None, packet.sequence),
                                "3240 refusal tuple reused")
                        refusal_syns[flow] = packet.sequence
                    if origin == "server" and packet.flags & 4:
                        request = flow.reverse()
                        sequence = refusal_syns.get(request)
                        require(sequence is not None and packet.flags == 0x14
                                and packet.sequence == 0
                                and packet.acknowledgement == (sequence + 1) % 2**32,
                                "3240 reset lacks exact prior reverse-tuple SYN/RST-ACK proof")
                        reset_families.add(flow.version)
                        refusal_proofs.append({"family": flow.version,
                                               "client_flow": request.value(),
                                               "client_syn_sequence": sequence,
                                               "server_reset_sequence": packet.sequence,
                                               "server_reset_acknowledgement": packet.acknowledgement,
                                               "server_reset_packet_id": packet_id})
                else:
                    flow_packets[flow].append((number, packet))
                    if packet.flags & 2:
                        require(syn.get(flow) in (None, packet.sequence), "tuple reused")
                        syn[flow] = packet.sequence
                    if packet.payload:
                        payload_count[flow] += 1
        elif event == "forward":
            packet_id = row["packet_id"]
            require(packet_id in queued, "duplicate/unreceived/dropped forward")
            queued.remove(packet_id)
            origin, _, _ = seen[packet_id]
            destination = "server" if origin == "client" else "client"
            require(row["origin"] == origin and row["destination"] == destination, "forward direction mismatch")
            forwarded[origin].append(packet_id)
        elif event == "arm":
            target = Flow.from_value(row["flow"])
            require(not rule and not queued and not exit_allowed and row["buffers_empty"] is True,
                    "arm without empty forwarding barrier")
            require(row["endpoint_fds"] == start["endpoint_fds"], "socket replaced before arm")
            direction = row["direction"]
            require(direction in DIRECTIONS and row["rule_id"] == direction and direction not in rule_ids
                    and target not in used_flows and target.version == family and target.dport == 3241,
                    "nonunique/different target rule")
            require(all(item in syn and payload_count[item] > 0 for item in (target, target.reverse())),
                    "target lacked actual two-way pre-arm flow")
            require(row["syn_sequences"] == [syn[target], syn[target.reverse()]], "wrong TCP epoch binding")
            Binding(**row["binding"])
            rule = dict(row, target=target, actual_drops=dict.fromkeys(ROLES, 0))
            rule["health_sources"] = [health_binding(evidence, row, role) for role in ROLES]
            rules.append(rule)
            rule_ids.add(direction)
            used_flows.add(target)
        elif event == "disarm":
            require(rule and row["rule_id"] == rule["rule_id"] and not queued and row["buffers_empty"] is True,
                    "disarm without rule/forwarding barrier")
            require(row["endpoint_fds"] == start["endpoint_fds"], "socket replaced/closed during fault")
            require(row["dropped"] == rule["actual_drops"] and all(row["dropped"][role] > 0
                    for role in DIRECTIONS[rule["direction"]]), "fault did not actually drop selected direction")
            terminal = bounded(evidence / "bindings" / f'{row["rule_id"]}-terminal.json', MAX_BINDING_BYTES)
            require(digest(terminal) == row["terminal_evidence_sha256"], "terminal artifact hash mismatch")
            json.loads(terminal)
            rule["disarmed_index"] = number
            rule["terminal_sha256"] = digest(terminal)
            rule = None
        elif event == "guest_exit_allowed":
            require(not rule and not queued and not exit_allowed, "invalid guest-exit barrier")
            exit_allowed = True
        elif event == "guest_eof":
            require(exit_allowed and row["role"] in ROLES and row["role"] not in closed_roles, "early/duplicate network EOF")
            closed_roles.add(row["role"])
        elif event == "finished":
            require(not rule and not queued and closed_roles == set(ROLES) and rule_ids == set(DIRECTIONS)
                    and row["packet_count"] == len(seen) and row["rules"] == sorted(rule_ids), "incomplete valve terminal")
            finished = True
        elif event == "close":
            require(number == len(rows) and finished and row["finished"] is True and row["failure"] is None,
                    "valve closed before complete evidence")
        else:
            raise ValueError("unexpected valve event: " + str(event))
    require(finished and rows[-1]["event"] == "close", "no complete valve close")
    for origin in ROLES:
        destination = "server" if origin == "client" else "client"
        require(source_index[origin] == len(captures[origin][origin]), "unaccounted source packets")
        require([seen[index][1] for index in forwarded[origin]] == [frame for frame, _ in captures[destination][origin]],
                "destination PCAP differs from exact forwarded bytes/order")
    require(reset_families == {4, 6}, "actual IPv4/IPv6 3240 refusals absent")
    armed_at = {flow: rule["index"] for rule in rules for flow in (rule["target"], rule["target"].reverse())}
    streams = [tls_facts(flow, packets, armed_at.get(flow)) for flow, packets in flow_packets.items()]
    rule_facts = []
    for rule in rules:
        collateral = []
        for flow, packets in flow_packets.items():
            if flow in (rule["target"], rule["target"].reverse()):
                continue
            count = sum(bool(packet.payload) for index, packet in packets if rule["index"] < index < rule["disarmed_index"])
            if count:
                collateral.append({"flow": flow.value(), "payload_packets_during_fault": count})
        require(collateral, "no other actual TLS payload during fault")
        rule_facts.append({"id": rule["rule_id"], "flow": rule["flow"], "drops": rule["actual_drops"],
                           "health_sources": rule["health_sources"], "other_flows": collateral,
                           "terminal_artifact_sha256": rule["terminal_sha256"]})
    return {"schema_version": 1, "result": "PACKET_AUDIT_PASS", "family": family,
            "scope": "Exact bounded Ethernet/flow/drop/FD journal plus two raw PCAPs. TLS ciphertext framing/signatures only; no decryption. Does not decide heartbeat timing, Catalog semantics, USB I/O or terminal proof.",
            "valve_sha256": digest(raw), "packets": len(seen), "captures": capture_meta,
            "initial_network_readiness": readiness, "plaintext_3240_refusals": refusal_proofs,
            "rules": rule_facts, "tls_source_directions": streams}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"schema_version": 1, "result": "FAIL", "scope": "packet audit only"}
    try:
        result = audit(args.evidence)
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        result["error"] = str(error)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps({"result": result["result"], "report": str(args.output)}))
    return 0 if result["result"] == "PACKET_AUDIT_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
