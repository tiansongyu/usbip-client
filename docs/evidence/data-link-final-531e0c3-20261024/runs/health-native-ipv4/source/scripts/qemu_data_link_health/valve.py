"""Finite, ordinary-user Ethernet valve for two QEMU socket netdevs.

All methods run on the runner's one thread. Arming drops selected completed
Ethernet frames, never closes a socket or injects TCP/TLS bytes. A queue/log/
format violation fails the experiment; it is not an implicit packet drop.
"""
from collections import deque
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import selectors
import socket
import struct
import time

from packets import ADDRESSES, Flow, MACS, MAX_FRAME, ROLES, decode, digest, validate_private

MAX_QUEUE_FRAMES = 64
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_LOG_RECORDS = 100_000
DIRECTIONS = {"c2s": frozenset(("client",)), "s2c": frozenset(("server",)),
              "both": frozenset(ROLES)}


@dataclass(frozen=True)
class Binding:
    """References to actual raw health samples; not network authorization."""
    client_instance: str
    client_session: str
    client_connection: int
    client_health_sha256: str
    server_instance: str
    server_session: str
    server_connection: int
    server_health_sha256: str

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name.endswith("_connection"):
                valid = type(value) is int and 1 <= value < 2**64
            elif name.endswith("_sha256"):
                valid = isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            else:
                valid = isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
            if not valid:
                raise ValueError("invalid bounded health binding field: " + name)


@dataclass
class Endpoint:
    sock: socket.socket
    header: bytearray = field(default_factory=bytearray)
    body: bytearray = field(default_factory=bytearray)
    wanted: int | None = None
    output: deque = field(default_factory=deque)
    output_offset: int = 0
    eof: bool = False


class Valve:
    def __init__(self, sockets, log_path, family):
        if set(sockets) != set(ROLES) or family not in (4, 6):
            raise ValueError("valve requires the two fixed roles and IP family")
        if len({sock.fileno() for sock in sockets.values()}) != 2:
            raise ValueError("valve endpoints must be distinct sockets")
        for sock in sockets.values():
            if sock.family != socket.AF_UNIX or sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
                raise ValueError("valve only accepts private AF_UNIX stream socketpairs")
        self.family = family
        self.endpoints = {role: Endpoint(sock) for role, sock in sockets.items()}
        self.selector = selectors.DefaultSelector()
        self.log = Path(log_path).open("x", encoding="utf-8")
        self.log_bytes = self.records = self.packet_id = 0
        self.last_clock = 0
        self.flows = {}
        self.rule = None
        self.used_rules = set()
        self.used_targets = set()
        self.exit_allowed = self.finished = self.closed = False
        self.failure = None
        self.original_fds = self.fd_facts()
        self._record("start", family=family, macs=MACS, max_frame=MAX_FRAME,
                     max_queue_frames=MAX_QUEUE_FRAMES, mechanism="ethernet_drop_fd_retained",
                     endpoint_fds=self.original_fds)
        for role, endpoint in self.endpoints.items():
            endpoint.sock.setblocking(False)
            self.selector.register(endpoint.sock, selectors.EVENT_READ, role)

    def fd_facts(self):
        result = {}
        for role, endpoint in self.endpoints.items():
            status = os.fstat(endpoint.sock.fileno())
            result[role] = {"fd": endpoint.sock.fileno(), "dev": status.st_dev,
                            "ino": status.st_ino, "mode": status.st_mode}
        return result

    def _record(self, event, **facts):
        now = time.monotonic_ns()
        if now < self.last_clock:
            raise RuntimeError("monotonic clock moved backwards")
        row = {"schema_version": 1, "index": self.records + 1,
               "monotonic_ns": now, "event": event, **facts}
        raw = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        if self.records >= MAX_LOG_RECORDS or self.log_bytes + len(raw.encode()) > MAX_LOG_BYTES:
            raise RuntimeError("valve evidence bound exceeded")
        self.log.write(raw)
        self.log.flush()
        self.records += 1
        self.log_bytes += len(raw.encode())
        self.last_clock = now
        return row["index"]

    def _update(self, role):
        endpoint = self.endpoints[role]
        flags = (0 if endpoint.eof else selectors.EVENT_READ)
        if endpoint.output:
            flags |= selectors.EVENT_WRITE
        try:
            self.selector.get_key(endpoint.sock)
            registered = True
        except KeyError:
            registered = False
        if flags:
            if registered:
                self.selector.modify(endpoint.sock, flags, role)
            else:
                self.selector.register(endpoint.sock, flags, role)
        elif registered:
            self.selector.unregister(endpoint.sock)

    def _observe(self, packet):
        if packet.flow is None or 3241 not in (packet.flow.sport, packet.flow.dport):
            return
        state = self.flows.setdefault(packet.flow, {"syn": None, "payload_packets": 0})
        if packet.flags & 2:
            if state["syn"] not in (None, packet.sequence):
                raise RuntimeError("TCP tuple reused in bounded experiment")
            state["syn"] = packet.sequence
        if packet.payload:
            if state["syn"] is None:
                raise RuntimeError("TLS flow capture began after SYN")
            state["payload_packets"] += 1

    def _packet(self, origin, raw):
        packet = decode(raw)
        validate_private(packet, origin, self.family)
        self._observe(packet)
        self.packet_id += 1
        selected = self.rule and packet.flow in (self.rule["flow"], self.rule["flow"].reverse())
        dropped = bool(selected and origin in DIRECTIONS[self.rule["direction"]])
        rule_id = self.rule["id"] if self.rule else None
        self._record("packet", packet_id=self.packet_id, origin=origin,
                     bytes=len(raw), sha256=digest(raw), packet=packet.facts(),
                     rule_id=rule_id, action="drop" if dropped else "queue")
        if dropped:
            self.rule["dropped"][origin] += 1
            return
        destination = "server" if origin == "client" else "client"
        endpoint = self.endpoints[destination]
        if endpoint.eof or len(endpoint.output) >= MAX_QUEUE_FRAMES:
            raise RuntimeError("destination ended or finite forwarding queue exhausted")
        endpoint.output.append((self.packet_id, origin, struct.pack("!I", len(raw)) + raw))
        self._update(destination)

    def _receive(self, role):
        endpoint = self.endpoints[role]
        for _ in range(32):  # Fairness: a busy guest cannot starve the other one.
            buffer = endpoint.header if endpoint.wanted is None else endpoint.body
            needed = (4 if endpoint.wanted is None else endpoint.wanted) - len(buffer)
            try:
                chunk = endpoint.sock.recv(needed)
            except BlockingIOError:
                return
            if not chunk:
                if endpoint.header or endpoint.body or endpoint.wanted is not None:
                    raise RuntimeError("guest ended inside a QEMU Ethernet frame")
                if not self.exit_allowed:
                    raise RuntimeError("guest network EOF before terminal barrier")
                endpoint.eof = True
                self._record("guest_eof", role=role)
                self._update(role)
                return
            buffer.extend(chunk)
            if endpoint.wanted is None and len(endpoint.header) == 4:
                endpoint.wanted = struct.unpack("!I", endpoint.header)[0]
                if not 14 <= endpoint.wanted <= MAX_FRAME:
                    raise RuntimeError("QEMU frame length outside finite bound")
            elif endpoint.wanted is not None and len(endpoint.body) == endpoint.wanted:
                self._packet(role, bytes(endpoint.body))
                endpoint.header.clear()
                endpoint.body.clear()
                endpoint.wanted = None

    def _send(self, role):
        endpoint = self.endpoints[role]
        for _ in range(32):
            if not endpoint.output:
                break
            packet_id, origin, framed = endpoint.output[0]
            try:
                count = endpoint.sock.send(memoryview(framed)[endpoint.output_offset:])
            except BlockingIOError:
                break
            if count <= 0:
                raise RuntimeError("zero-byte QEMU forwarding write")
            endpoint.output_offset += count
            if endpoint.output_offset == len(framed):
                endpoint.output.popleft()
                endpoint.output_offset = 0
                self._record("forward", packet_id=packet_id, origin=origin, destination=role)
        self._update(role)

    def poll(self, timeout=0.05):
        if self.closed or self.finished or self.failure is not None:
            raise RuntimeError("valve is no longer running")
        if not 0 <= timeout <= 1:
            raise ValueError("valve poll must remain bounded")
        try:
            for key, flags in self.selector.select(timeout):
                if flags & selectors.EVENT_WRITE:
                    self._send(key.data)
                if flags & selectors.EVENT_READ:
                    self._receive(key.data)
        except Exception as error:
            self.failure = type(error).__name__
            self._record("failure", category=self.failure)
            raise

    def quiescent(self):
        return all(not item.output and not item.header and not item.body and item.wanted is None
                   for item in self.endpoints.values())

    def arm(self, rule_id, flow, direction, binding):
        if self.rule or self.exit_allowed or self.finished or self.closed or self.failure:
            raise RuntimeError("cannot arm in current valve phase")
        if rule_id != direction or direction not in DIRECTIONS or rule_id in self.used_rules:
            raise ValueError("one unique c2s/s2c/both rule required")
        if not isinstance(binding, Binding) or not isinstance(flow, Flow):
            raise ValueError("typed observed flow/binding required")
        if (flow.version != self.family or flow.src != ADDRESSES[self.family]["client"]
                or flow.dst != ADDRESSES[self.family]["server"] or flow.dport != 3241):
            raise ValueError("rule must select this private client-to-server TLS flow")
        if flow in self.used_targets or not self.quiescent():
            raise RuntimeError("new flow and empty input/output buffers required at arm")
        observations = [self.flows.get(item) for item in (flow, flow.reverse())]
        if any(item is None or item["syn"] is None or not item["payload_packets"] for item in observations):
            raise RuntimeError("both SYN and actual payload directions must precede arm")
        if self.fd_facts() != self.original_fds:
            raise RuntimeError("valve socket identities changed")
        self.rule = {"id": rule_id, "flow": flow, "direction": direction,
                     "dropped": dict.fromkeys(ROLES, 0)}
        self.used_rules.add(rule_id)
        self.used_targets.add(flow)
        self._record("arm", rule_id=rule_id, flow=flow.value(), direction=direction,
                     binding=asdict(binding), syn_sequences=[item["syn"] for item in observations],
                     buffers_empty=True, endpoint_fds=self.fd_facts())

    def disarm(self, rule_id, terminal_evidence_sha256):
        if not self.rule or self.rule["id"] != rule_id or not re.fullmatch(r"[0-9a-f]{64}", terminal_evidence_sha256):
            raise ValueError("matching rule and raw terminal evidence hash required")
        if not self.quiescent() or any(self.rule["dropped"][role] == 0 for role in DIRECTIONS[self.rule["direction"]]):
            raise RuntimeError("rule has no actual drop or forwarding barrier is incomplete")
        if self.fd_facts() != self.original_fds:
            raise RuntimeError("valve socket identities changed")
        self._record("disarm", rule_id=rule_id, dropped=self.rule["dropped"],
                     terminal_evidence_sha256=terminal_evidence_sha256,
                     endpoint_fds=self.fd_facts(), buffers_empty=True)
        self.rule = None

    def allow_guest_exit(self):
        if self.rule or not self.quiescent() or self.failure or self.exit_allowed:
            raise RuntimeError("guest exit only follows completed rule/forwarding barriers")
        self.exit_allowed = True
        self._record("guest_exit_allowed")

    def finish(self):
        if (not self.exit_allowed or self.rule or not self.quiescent() or self.failure
                or not all(item.eof for item in self.endpoints.values())):
            raise RuntimeError("valve finish has unresolved packets/guest endpoints")
        self._record("finished", packet_count=self.packet_id, rules=sorted(self.used_rules))
        self.finished = True

    def close(self):
        if self.closed:
            return
        try:
            self._record("close", finished=self.finished, failure=self.failure)
        finally:
            self.closed = True
            self.selector.close()
            for endpoint in self.endpoints.values():
                endpoint.sock.close()
            self.log.close()
