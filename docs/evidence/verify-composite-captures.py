#!/usr/bin/env python3
"""Read-only, independent validation of the two private ECM Ethernet captures.

Usage: python3 docs/evidence/verify-composite-captures.py <evidence-directory>
The input is synthetic QEMU test traffic, never a host-interface capture.
"""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import struct


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return (~total) & 65535


def verify(path):
    require(path.stat().st_size <= 16 * 1024 * 1024, "capture exceeds verifier bound")
    raw = path.read_bytes()
    endian = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">"}.get(raw[:4])
    require(endian is not None and len(raw) >= 24, "unsupported or truncated PCAP header")
    require(struct.unpack_from(endian + "I", raw, 20)[0] == 1, "capture is not Ethernet")
    offset = 24
    observations = []
    while offset < len(raw):
        require(offset + 16 <= len(raw), "truncated PCAP packet header")
        _, _, captured, original = struct.unpack_from(endian + "IIII", raw, offset)
        offset += 16
        require(captured == original and captured <= 65536 and offset + captured <= len(raw),
                "truncated or oversized captured Ethernet packet")
        frame = raw[offset:offset + captured]
        offset += captured
        require(len(frame) >= 14, "truncated Ethernet header")
        if frame[12:14] != b"\x08\x00":
            continue
        require(len(frame) >= 34 and frame[14] >> 4 == 4, "invalid IPv4 header")
        header = (frame[14] & 15) * 4
        total = int.from_bytes(frame[16:18], "big")
        require(header >= 20 and total >= header and 14 + total <= len(frame), "invalid IPv4 length")
        require(checksum(frame[14:14 + header]) == 0, "invalid IPv4 checksum")
        if frame[23] != 17:
            continue
        require(int.from_bytes(frame[20:22], "big") & 0x3fff == 0, "unexpected fragmented test UDP")
        require(total >= header + 8, "truncated UDP header")
        udp = frame[14 + header:14 + total]
        source_port, target_port, length, udp_checksum = struct.unpack_from("!HHHH", udp)
        require(length == len(udp), "UDP length differs from IP payload")
        if (source_port, target_port) not in [(48002, 48001), (48001, 48002)]:
            continue
        # These test datagrams exceed Ethernet's minimum frame size. Additional
        # bytes would hide the same concatenation defect seen with old xHCI.
        require(len(frame) == 14 + total, "test Ethernet packet contains concatenated extra bytes")
        source = socket.inet_ntoa(frame[26:30])
        target = socket.inet_ntoa(frame[30:34])
        request = source_port == 48002
        require((source, target) == (("198.18.0.2", "198.18.0.1") if request else
                                     ("198.18.0.1", "198.18.0.2")), "test UDP route differs")
        if udp_checksum:
            pseudo = frame[26:34] + b"\0\x11" + length.to_bytes(2, "big")
            require(checksum(pseudo + udp) == 0, "invalid test UDP checksum")
        direction = "REQUEST" if request else "RESPONSE"
        cycle = 1 + sum(item["direction"] == direction for item in observations)
        require(cycle <= 2, "duplicate test UDP payload")
        tail = "0123456789abcdef" if request else "fedcba9876543210"
        payload = udp[8:]
        require(payload == f"RUSTUSB-ECM-{direction}-{cycle}:{tail}".encode(),
                "captured test UDP bytes or sequence differ")
        observations.append({"direction": direction, "cycle": cycle, "bytes": len(payload),
                             "sha256": hashlib.sha256(payload).hexdigest()})
    require([(item["direction"], item["cycle"]) for item in observations] ==
            [("REQUEST", 1), ("RESPONSE", 1), ("REQUEST", 2), ("RESPONSE", 2)],
            "both exact two-cycle request/response exchanges are required")
    return {"capture": path.name, "capture_bytes": len(raw),
            "capture_sha256": hashlib.sha256(raw).hexdigest(), "actual_udp_payloads": observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    captures = [verify(args.directory / name) for name in ["data-usb.pcap", "data-peer.pcap"]]
    require(captures[0]["actual_udp_payloads"] == captures[1]["actual_udp_payloads"],
            "the two private endpoints observed different application payloads")
    print(json.dumps({"result": "PASS", "scope": "actual private Ethernet IPv4/UDP bytes only",
                      "captures": captures}, indent=2))


if __name__ == "__main__":
    main()
