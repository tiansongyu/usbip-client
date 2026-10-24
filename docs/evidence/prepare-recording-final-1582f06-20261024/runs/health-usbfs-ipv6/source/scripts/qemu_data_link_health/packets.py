"""Bounded private-link Ethernet decoding; no TLS decryption or USB parsing.

The accepted IPv6 control subset matches the fixed isolated Linux guest's
RS/NS/NA/MLDv2 traffic. Unknown protocols and fragments are errors, not skips.
"""
from dataclasses import dataclass
import hashlib
import ipaddress
import struct

MAX_FRAME = 4096 + 65536  # QEMU v6.2.0 include/net/net.h: NET_BUFSIZE.
ROLES = ("server", "client")
MACS = {"server": "52:54:00:00:00:61", "client": "52:54:00:00:00:62"}
ADDRESSES = {
    4: {"server": "192.0.2.1", "client": "192.0.2.2"},
    6: {"server": "fd42:7275:7374::1", "client": "fd42:7275:7374::2"},
}


@dataclass(frozen=True)
class Flow:
    version: int
    src: str
    sport: int
    dst: str
    dport: int

    def __post_init__(self):
        if self.version not in (4, 6):
            raise ValueError("invalid IP family")
        for address in (self.src, self.dst):
            parsed = ipaddress.ip_address(address)
            if parsed.version != self.version or str(parsed) != address:
                raise ValueError("noncanonical flow address")
        if any(type(p) is not int or not 1 <= p <= 65535 for p in (self.sport, self.dport)):
            raise ValueError("invalid flow port")

    def reverse(self):
        return Flow(self.version, self.dst, self.dport, self.src, self.sport)

    def value(self):
        return [self.version, self.src, self.sport, self.dst, self.dport]

    @classmethod
    def from_value(cls, value):
        if not isinstance(value, list) or len(value) != 5:
            raise ValueError("invalid five-tuple shape")
        return cls(*value)


@dataclass(frozen=True)
class Packet:
    source_mac: str
    kind: str
    flow: Flow | None = None
    sequence: int | None = None
    acknowledgement: int | None = None
    flags: int | None = None
    payload: bytes = b""

    def facts(self):
        result = {"source_mac": self.source_mac, "kind": self.kind}
        if self.flow is not None:
            result.update(flow=self.flow.value(), sequence=self.sequence,
                          acknowledgement=self.acknowledgement, flags=self.flags,
                          payload_length=len(self.payload))
        return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _control_v6(packet, body, alert, extension):
    if len(body) < 8 or body[1] != 0:
        raise ValueError("short/nonzero-code ICMPv6 control")
    pseudo = packet[8:40] + len(body).to_bytes(4, "big") + b"\0\0\0\x3a" + body
    pseudo += b"\0" * (len(pseudo) % 2)
    checksum = sum(int.from_bytes(pseudo[i:i + 2], "big") for i in range(0, len(pseudo), 2))
    while checksum >> 16:
        checksum = (checksum & 65535) + (checksum >> 16)
    if checksum != 65535:
        raise ValueError("invalid ICMPv6 checksum")
    src, dst = ipaddress.ip_address(packet[8:24]), ipaddress.ip_address(packet[24:40])
    kind = body[0]
    if kind == 143:
        if not (extension and alert and packet[7] == 1 and src.is_link_local and str(dst) == "ff02::16"):
            raise ValueError("invalid MLDv2 scope")
        count = int.from_bytes(body[6:8], "big")
        if body[4:6] != b"\0\0" or not 1 <= count <= 16:
            raise ValueError("invalid MLDv2 record count")
        cursor = 8
        for _ in range(count):
            if cursor + 20 > len(body):
                raise ValueError("truncated MLDv2 record")
            record, auxiliary, sources = struct.unpack("!BBH", body[cursor:cursor + 4])
            end = cursor + 20 + 16 * sources
            if record not in range(1, 7) or auxiliary or sources > 16 or end > len(body):
                raise ValueError("unsupported MLDv2 record")
            if not ipaddress.ip_address(body[cursor + 4:cursor + 20]).is_multicast:
                raise ValueError("nonmulticast MLDv2 group")
            for start in range(cursor + 20, end, 16):
                address = ipaddress.ip_address(body[start:start + 16])
                if address.is_multicast or address.is_unspecified:
                    raise ValueError("invalid MLDv2 source")
            cursor = end
        if cursor != len(body):
            raise ValueError("MLDv2 trailing bytes")
        return "icmpv6_mldv2"
    if kind not in (133, 135, 136) or extension or alert or packet[7] != 255:
        raise ValueError("unaccounted ICMPv6 control")
    if kind == 133:
        if body[4:8] != b"\0" * 4 or str(dst) != "ff02::2" or not (src.is_link_local or src.is_unspecified):
            raise ValueError("invalid router solicitation")
        cursor, allowed = 8, 1
    else:
        if len(body) < 24 or ipaddress.ip_address(body[8:24]).is_multicast:
            raise ValueError("invalid neighbor target")
        reserved = int.from_bytes(body[4:8], "big")
        if (kind == 135 and reserved) or (kind == 136 and reserved & 0x1fffffff):
            raise ValueError("invalid neighbor reserved bits")
        cursor, allowed = 24, 1 if kind == 135 else 2
    option = False
    while cursor < len(body):
        if cursor + 8 > len(body) or body[cursor] != allowed or body[cursor + 1] != 1 or option:
            raise ValueError("invalid neighbor option")
        option = True
        cursor += 8
    if src.is_multicast or (src.is_unspecified and (option or kind == 136)):
        raise ValueError("invalid neighbor source")
    return {133: "icmpv6_rs", 135: "icmpv6_ns", 136: "icmpv6_na"}[kind]


def decode(frame):
    if not 14 <= len(frame) <= MAX_FRAME:
        raise ValueError("Ethernet length outside fixed QEMU bound")
    mac = ":".join(f"{b:02x}" for b in frame[6:12])
    ether = int.from_bytes(frame[12:14], "big")
    packet = frame[14:]
    if ether == 0x0806:
        if len(packet) < 28 or packet[:6] != b"\0\x01\x08\0\x06\x04" or int.from_bytes(packet[6:8], "big") not in (1, 2):
            raise ValueError("unsupported ARP")
        return Packet(mac, "arp")
    if ether == 0x0800:
        if len(packet) < 20 or packet[0] >> 4 != 4:
            raise ValueError("invalid IPv4 header")
        header, total = (packet[0] & 15) * 4, int.from_bytes(packet[2:4], "big")
        if not 20 <= header <= total <= len(packet):
            raise ValueError("IPv4 length")
        if packet[9] != 6 or int.from_bytes(packet[6:8], "big") & 0x3fff:
            raise ValueError("unknown IPv4 protocol or fragment")
        version = 4
        src, dst = str(ipaddress.ip_address(packet[12:16])), str(ipaddress.ip_address(packet[16:20]))
        body = packet[header:total]
    elif ether == 0x86dd:
        if len(packet) < 40 or packet[0] >> 4 != 6:
            raise ValueError("invalid IPv6 header")
        total = 40 + int.from_bytes(packet[4:6], "big")
        if total > len(packet) or total == 40:
            raise ValueError("IPv6 length/jumbogram")
        cursor, protocol, alert, extension = 40, packet[6], False, False
        if protocol == 0:
            if cursor + 8 > total:
                raise ValueError("short Hop-by-Hop header")
            end = cursor + 8 * (packet[cursor + 1] + 1)
            if end > total or end - cursor > 256:
                raise ValueError("Hop-by-Hop bound")
            protocol, option = packet[cursor], cursor + 2
            while option < end:
                kind = packet[option]
                if kind == 0:
                    option += 1
                    continue
                if option + 2 > end:
                    raise ValueError("short Hop-by-Hop option")
                size = packet[option + 1]
                if option + 2 + size > end:
                    raise ValueError("Hop-by-Hop option overrun")
                value = packet[option + 2:option + 2 + size]
                if kind == 1 and not any(value):
                    pass
                elif kind == 5 and not alert and value == b"\0\0":
                    alert = True
                else:
                    raise ValueError("unknown/duplicate Hop-by-Hop option")
                option += 2 + size
            cursor, extension = end, True
        if protocol not in (6, 58):
            raise ValueError("unknown IPv6 protocol/extension/fragment")
        body = packet[cursor:total]
        if protocol == 58:
            return Packet(mac, _control_v6(packet, body, alert, extension))
        if alert:
            raise ValueError("Router Alert on TCP")
        version = 6
        src, dst = str(ipaddress.ip_address(packet[8:24])), str(ipaddress.ip_address(packet[24:40]))
    else:
        raise ValueError("unknown Ethernet protocol")
    if len(body) < 20:
        raise ValueError("short TCP header")
    header = (body[12] >> 4) * 4
    if not 20 <= header <= len(body):
        raise ValueError("TCP header length")
    sport, dport, sequence, acknowledgement = struct.unpack("!HHII", body[:12])
    return Packet(mac, "tcp", Flow(version, src, sport, dst, dport),
                  sequence, acknowledgement, body[13], body[header:])


def validate_private(packet, origin, family):
    if origin not in ROLES or family not in (4, 6) or packet.source_mac != MACS[origin]:
        raise ValueError("private-link source identity mismatch")
    if packet.flow is None:
        return
    flow = packet.flow
    peer = "client" if origin == "server" else "server"
    if flow.src != ADDRESSES[flow.version][origin] or flow.dst != ADDRESSES[flow.version][peer]:
        raise ValueError("TCP outside the two fixed guest addresses")
    service_port = flow.sport if origin == "server" else flow.dport
    if service_port == 3240:
        if packet.payload:
            raise ValueError("plaintext 3240 payload")
    elif service_port != 3241 or flow.version != family:
        raise ValueError("unexpected service port/family")
