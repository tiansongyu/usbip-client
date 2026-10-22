"""Decode only the two private synthetic guest-data PCAPs; no live capture."""
from pathlib import Path
import hashlib
import ipaddress
import json
import struct

root = Path(__file__).resolve().parent
facts = []
for name in ["data-usb.pcap", "data-peer.pcap"]:
    data = (root / name).read_bytes()
    if data[:4] != b"\xd4\xc3\xb2\xa1":
        raise ValueError("expected little-endian classic PCAP")
    offset = 24
    index = 0
    while offset < len(data):
        _, _, caplen, _ = struct.unpack_from("<IIII", data, offset)
        offset += 16
        frame = data[offset:offset + caplen]
        offset += caplen
        index += 1
        if frame[6:12] != bytes.fromhex("405400cc0002"):
            continue
        segments = []
        position = 0
        while position + 14 <= len(frame):
            ether_type = frame[position + 12:position + 14]
            segment = {"offset": position, "ether_type": ether_type.hex()}
            if ether_type == b"\x86\xdd" and position + 54 <= len(frame):
                length = 54 + int.from_bytes(frame[position + 18:position + 20], "big")
                segment["length"] = length
            elif ether_type == b"\x08\x06" and position + 42 <= len(frame):
                length = 42
                segment.update(length=length, op=int.from_bytes(frame[position + 20:position + 22], "big"),
                               sender_ip=str(ipaddress.ip_address(frame[position + 28:position + 32])),
                               target_ip=str(ipaddress.ip_address(frame[position + 38:position + 42])))
            else:
                break
            segments.append(segment)
            position += length
        facts.append({"capture": name, "packet": index, "captured_ethernet_length": len(frame),
                      "segments": segments, "segments_exactly_fill_one_captured_frame": position == len(frame)})
result = {"capture_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                              for name in ["data-usb.pcap", "data-peer.pcap"]},
          "client_frames": facts,
          "interpretation": "Multiple complete Ethernet frames are concatenated inside each captured client-origin Ethernet packet. The trailing frame is ARP request 198.18.0.2 -> 198.18.0.1, not a standalone packet. Correlates with QEMU6.2 usb-net ECM OUT buffering until a USBPacket length below64."}
print(json.dumps(result, indent=2))
