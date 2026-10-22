"""Offline cross-check of private synthetic UDP captures and guest USBMON."""
from pathlib import Path
import hashlib
import ipaddress
import json
import struct

root = Path(__file__).resolve().parent
captures = {}
for name in ["data-usb.pcap", "data-peer.pcap"]:
    data = (root / name).read_bytes()
    if data[:4] != b"\xd4\xc3\xb2\xa1":
        raise ValueError("expected little-endian classic PCAP")
    offset = 24
    observations = []
    while offset < len(data):
        _, _, length, actual = struct.unpack_from("<IIII", data, offset)
        offset += 16
        frame = data[offset:offset + length]
        offset += length
        if frame[12:14] != b"\x08\x00" or len(frame) < 42 or frame[23] != 17:
            continue
        ihl = (frame[14] & 15) * 4
        total = int.from_bytes(frame[16:18], "big")
        if ihl < 20 or len(frame) != 14 + total:
            raise ValueError("IPv4 frame boundary differs")
        udp = frame[14 + ihl:14 + total]
        source, destination, size, _ = struct.unpack_from("!HHHH", udp)
        if size != len(udp):
            raise ValueError("UDP length differs")
        payload = udp[8:]
        sender = str(ipaddress.ip_address(frame[26:30]))
        receiver = str(ipaddress.ip_address(frame[30:34]))
        expected = []
        for cycle in [1, 2]:
            expected.append((cycle, "request", "198.18.0.2", "198.18.0.1", 48002, 48001,
                             f"RUSTUSB-ECM-REQUEST-{cycle}:0123456789abcdef".encode()))
            expected.append((cycle, "response", "198.18.0.1", "198.18.0.2", 48001, 48002,
                             f"RUSTUSB-ECM-RESPONSE-{cycle}:fedcba9876543210".encode()))
        found = [item for item in expected if (sender, receiver, source, destination, payload) == item[2:]]
        if len(found) != 1:
            raise ValueError("UDP contents/endpoint not the exact fixed test frame")
        cycle, direction = found[0][:2]
        observations.append({"cycle": cycle, "direction": direction, "ethernet_length": actual,
                             "udp_payload_bytes": len(payload), "actual_payload_sha256": hashlib.sha256(payload).hexdigest(),
                             "exact_match": True})
    if [(entry["cycle"], entry["direction"]) for entry in observations] != [(1, "request"), (1, "response"), (2, "request"), (2, "response")]:
        raise ValueError("four exact ordered UDP frames required in each independent data capture")
    captures[name] = {"sha256": hashlib.sha256(data).hexdigest(), "udp_frames": observations}
server = (root / "server-serial.log").read_text()
client = (root / "client-serial.log").read_text()
wire = [json.loads(line) for line in client.splitlines() if line.startswith('{"event":"composite_probe_case"')]
wire = [entry for entry in wire if entry["case"] == "real_interrupt_stall_preserves_connection"]
if len(wire) != 1:
    raise ValueError("exactly one wire IRQ observation required")
completions = []
for line in server.splitlines():
    fields = line.split()
    if len(fields) >= 8 and fields[2] == "C" and fields[3].startswith("Ii:") and fields[3].endswith(":1") and fields[4].split(":")[0] == "-32" and fields[5] == "1":
        submission = [row for row in server.splitlines() if row.startswith(fields[0] + " ") and " S " + fields[3] + " " in row and " 1 <" in row]
        if len(submission) != 1 or fields[6] != "=":
            raise ValueError("IRQ completion lacks matching submission/data")
        buffer_hex = "".join(fields[7:]).lower()
        if wire[0]["status"] != -32 or wire[0]["actual_length"] != 1 or wire[0]["buffer_hex"] != buffer_hex:
            raise ValueError("wire error/actual/opaque buffer differs from independent USBMON")
        completions.append({"submission": submission[0], "completion": line, "wire": wire[0],
                            "classification": "QEMU UHCI kernel-reported partial/opaque buffer; not a claim that device transferred one byte"})
if len(completions) != 1:
    raise ValueError("one independent kernel IRQ completion required")
print(json.dumps({"private_udp_captures": captures, "kernel_reported_interrupt_partial": completions[0]}, indent=2))
