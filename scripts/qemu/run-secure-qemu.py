#!/usr/bin/env python3
"""Two disposable guests: actual mTLS catalog/IMPORT/URB, kernel VHCI and I/O.

One isolated NIC socketpair is the complete external link. Host coordination,
evidence, QMP and simulated FTDI are separate Unix/serial channels. No host USB,
tap, public listener, module load or product stand-in is used. Virtual, USBFS and
Native secure servers x IPv4/IPv6 are six distinct actual-product runs.
"""
import argparse
import base64
import datetime
import gzip
import hashlib
import importlib.util
import ipaddress
import json
import lzma
import os
from pathlib import Path
import re
import select
import shutil
import socket
import struct
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
OWN = ["run-secure-qemu.py", "guest-secure-init.sh"]
REQUIRED_APPLETS = {
    "sh", "mount", "mkdir", "stty", "cat", "basename", "wc", "dmesg",
    "sync", "poweroff", "grep", "seq", "sleep", "date", "insmod", "awk",
    "sha256sum", "chmod", "cp", "readlink", "sed", "stat", "timeout", "tee",
    "rm", "chown", "mkfifo", "cmp", "find", "head", "dd", "od", "tr", "uname",
}


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts/qemu" / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


native = module("secure_native_payload", "run-native-qemu.py")
physical = module("secure_qmp", "run-physical-qemu.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def inputs():
    names = set(git("ls-files", "--cached", "--others", "--exclude-standard").splitlines())
    scripts = {*OWN, "run-native-qemu.py", "guest-init.sh", "run-physical-qemu.py"}
    selected = sorted(name for name in names if
                      name in {"Cargo.toml", "Cargo.lock"} or
                      ((name.startswith("crates/") or name.startswith("vendor/")) and
                       (name.endswith(".rs") or name.endswith("Cargo.toml"))) or
                      name in {"scripts/qemu/" + script for script in scripts})
    return {name: {"sha256": digest(REPO / name), "bytes": (REPO / name).stat().st_size}
            for name in selected}


def command(args, log):
    with log.open("ab") as output:
        output.write((json.dumps([str(arg) for arg in args]) + "\n").encode())
        output.flush()
        subprocess.run(args, cwd=REPO, stdout=output, stderr=subprocess.STDOUT, check=True)


def credentials(work, evidence):
    """Ephemeral test CA/PKCS#8 credentials; only public DER survives temp cleanup."""
    directory = work / "credentials"
    directory.mkdir(mode=0o700)
    log = evidence / "certificate-generation.log"
    command(["openssl", "version", "-a"], log)
    command(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
             "-subj", "/CN=RustUSB disposable QEMU CA", "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", directory / "ca-key.pem",
             "-out", directory / "ca.pem"], log)
    command(["openssl", "x509", "-in", directory / "ca.pem", "-outform", "DER", "-out", directory / "ca.der"], log)
    for role in ["server", "client"]:
        subject = "rustusb-qemu.test" if role == "server" else "qemu-client"
        extension = "serverAuth" if role == "server" else "clientAuth"
        (directory / f"{role}.ext").write_text(
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            f"extendedKeyUsage={extension}\n" + ("subjectAltName=DNS:rustusb-qemu.test\n" if role == "server" else ""))
        command(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={subject}",
                 "-keyout", directory / f"{role}-key.pem", "-out", directory / f"{role}.csr"], log)
        command(["openssl", "x509", "-req", "-days", "2", "-in", directory / f"{role}.csr",
                 "-CA", directory / "ca.pem", "-CAkey", directory / "ca-key.pem", "-CAcreateserial",
                 "-extfile", directory / f"{role}.ext", "-out", directory / f"{role}.pem"], log)
        command(["openssl", "x509", "-in", directory / f"{role}.pem", "-outform", "DER", "-out", directory / f"{role}.der"], log)
        command(["openssl", "pkcs8", "-topk8", "-nocrypt", "-in", directory / f"{role}-key.pem",
                 "-outform", "DER", "-out", directory / f"{role}-key.der"], log)
    for name in ["ca", "server", "client"]:
        shutil.copyfile(directory / f"{name}.der", evidence / f"{name}-public.der")
    return directory


def validate_payload(root):
    """Check the actual copied applet binary, links and every guest executable dependency."""
    busybox = root / "bin/busybox"
    available = set(subprocess.check_output([str(busybox), "--list"], text=True).splitlines())
    missing = sorted(REQUIRED_APPLETS - available)
    if missing:
        raise RuntimeError(f"copied BusyBox lacks required guest applets: {missing}")
    for name in sorted(REQUIRED_APPLETS):
        path = root / "bin" / name
        if not path.is_symlink() or path.resolve() != busybox.resolve():
            raise RuntimeError(f"required guest applet link is missing or differs: {name}")
    dependencies = {}
    programs = [busybox, *sorted((root / "usr/bin").iterdir())]
    for program in programs:
        if not program.is_file() or program.read_bytes()[:4] != b"\x7fELF":
            raise RuntimeError(f"guest program is not an actual ELF: {program.relative_to(root)}")
        result = subprocess.run(["ldd", str(program)], capture_output=True, text=True)
        output = result.stdout + result.stderr
        if "not found" in output:
            raise RuntimeError(f"guest ELF has an unresolved host dependency: {program.name}")
        libraries = {}
        for name in sorted(set(re.findall(r"/[^\s()]+", output))):
            library = Path(name)
            if not library.is_file():
                continue
            copied = root / str(library).lstrip("/")
            if not copied.is_file() or digest(copied) != digest(library):
                raise RuntimeError(f"guest dependency absent or changed: {program.name}: {library.name}")
            libraries[str(library)] = digest(copied)
        if result.returncode and not any(marker in output for marker in ["not a dynamic executable", "statically linked"]):
            raise RuntimeError(f"cannot inspect guest ELF dependencies: {program.name}")
        dependencies[str(program.relative_to(root))] = {"sha256": digest(program), "libraries": libraries}
    required_programs = {"rustusb", "rustusb-helper", "jq", "setpriv", "nc-openbsd", "ip", "base64", "usbip", "usbipd"}
    if not required_programs <= {program.name for program in programs}:
        raise RuntimeError("required standalone guest program missing")
    # Execute only the trusted coreutils encoder, using a fixed public fixture.
    # No guest init, USB utility, product executable or probe is executed here.
    fixture = b"RustUSB diagnostic encoder preflight\n\x00\xff"
    encoded = subprocess.check_output([str(root / "usr/bin/base64")], input=fixture)
    if base64.b64decode(encoded, validate=False) != fixture:
        raise RuntimeError("copied diagnostic encoder failed exact-byte round trip")
    return {"scope": "actual copied ELF/applet/dependency checks and encoder fixture only; no guest/USB execution",
            "busybox_applets": sorted(available), "required_applets": sorted(REQUIRED_APPLETS),
            "standalone_programs": sorted(required_programs), "executables": dependencies,
            "encoder_fixture_sha256": hashlib.sha256(fixture).hexdigest()}


def payload(work, kernel, certificates, role):
    if role not in ("server", "client"):
        raise RuntimeError("unknown payload role")
    native.initramfs(work, kernel)
    root = work / "root"
    shutil.copyfile(REPO / "scripts/qemu/guest-secure-init.sh", root / "init")
    (root / "init").chmod(0o755)
    for name, path in [("nc-openbsd", Path("/bin/nc.openbsd")), ("ip", Path(shutil.which("ip") or "/usr/sbin/ip")),
                       ("base64", Path(shutil.which("base64") or "/usr/bin/base64"))]:
        native.copy_binary(path, f"/usr/bin/{name}", root)
    # CDC ACM is the only module absent from the shared Native payload builder.
    tree = Path("/lib/modules") / kernel
    candidates = list(tree.rglob("cdc-acm.ko*"))
    if candidates:
        source = candidates[0]
        data = source.read_bytes()
        if source.suffix == ".xz": data = lzma.decompress(data)
        elif source.suffix == ".gz": data = gzip.decompress(data)
        elif source.suffix == ".zst": data = subprocess.check_output(["zstd", "-d", "-c", str(source)])
        (root / "modules/cdc-acm.ko").write_bytes(data)
    elif "/cdc-acm.ko" not in (tree / "modules.builtin").read_text():
        raise RuntimeError("CDC ACM is neither a module nor recorded built-in")
    config = root / "etc/rustusb"
    config.mkdir(mode=0o755)
    for name in ["ca.der", "server.der", "client.der", f"{role}-key.der"]:
        shutil.copyfile(certificates / name, config / name)
        (config / name).chmod(0o600 if "key" in name else 0o644)
    (config / "client-pin.txt").write_text(digest(config / "client.der") + "\n")
    (config / "host-created-epoch.txt").write_text(str(int(time.time())) + "\n")
    other = "client" if role == "server" else "server"
    if (config / f"{other}-key.der").exists():
        raise RuntimeError("opposite role private key entered payload")
    (root / "etc/passwd").write_text("root:x:0:0:root:/:/bin/sh\nlegacy:x:1000:1000:legacy:/:/sbin/nologin\nbroker:x:2000:2000:broker:/:/sbin/nologin\n")
    preflight = validate_payload(root)
    (work / "payload-dependencies.json").write_text(json.dumps(preflight, indent=2) + "\n")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    archive = work / "secure-initramfs.cpio.gz"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
    archive.write_bytes(gzip.compress(packed.stdout))
    provenance = {str(path.relative_to(root)): digest(path) for path in root.rglob("*")
                  if path.is_file() and not path.is_symlink() and
                  (str(path.relative_to(root)).startswith(("usr/bin/", "modules/")) or path == root / "init")}
    return archive, provenance


def connect_unix(path, process, deadline):
    while time.monotonic() < deadline:
        if process.poll() is not None: raise RuntimeError("QEMU exited while opening control channel")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.connect(str(path))
            connection.settimeout(2)
            return connection
        except (FileNotFoundError, ConnectionRefusedError):
            connection.close()
            time.sleep(0.05)
    raise RuntimeError("control channel startup deadline expired")


def artifacts(evidence):
    """Extract exact guest files after exit, including complete failure artifacts."""
    result = {}
    pattern = re.compile(r"^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$", re.M | re.S)
    for role in ["server", "client"]:
        directory = evidence / f"{role}-artifacts"
        directory.mkdir(exist_ok=True)
        log = (evidence / f"{role}-evidence.log").read_text(errors="replace")
        entries = {}
        for match in pattern.finditer(log):
            name, count, encoded = match.groups()
            if name in entries: raise RuntimeError(f"duplicate guest artifact: {role}/{name}")
            data = base64.b64decode("".join(encoded.split()), validate=True)
            if len(data) != int(count): raise RuntimeError(f"truncated guest artifact: {role}/{name}")
            (directory / name).write_bytes(data)
            entries[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if log.count("SECURE_ARTIFACT_BEGIN ") != len(entries):
            raise RuntimeError(f"incomplete artifact dump: {role}")
        result[role] = entries
    return result


def pcap_review(path, family, device_ids):
    """Parse the entire Ethernet capture and contiguous TCP byte streams, not grep alone."""
    def ipv6_payload(packet, total):
        # This isolated test link permits one bounded Hop-by-Hop header only.
        # Parse its payload protocol; never discard an extension-wrapped TCP flow.
        protocol, cursor, router_alert = packet[6], 40, False
        extensions = 0
        if protocol == 0:
            if cursor+2 > total: raise RuntimeError("truncated IPv6 Hop-by-Hop header")
            size = (packet[cursor+1]+1)*8
            if size > 256 or cursor+size > total: raise RuntimeError("IPv6 Hop-by-Hop length exceeds bound")
            protocol, end = packet[cursor], cursor+size
            option = cursor+2
            while option < end:
                kind = packet[option]
                if kind == 0:  # Pad1
                    option += 1
                    continue
                if option+2 > end: raise RuntimeError("short IPv6 Hop-by-Hop option")
                length = packet[option+1]
                value = packet[option+2:option+2+length]
                if option+2+length > end: raise RuntimeError("IPv6 Hop-by-Hop option overrun")
                if kind == 1:
                    if any(value): raise RuntimeError("nonzero IPv6 PadN bytes")
                elif kind == 5:
                    if router_alert or value != b"\0\0": raise RuntimeError("unsupported/duplicate IPv6 Router Alert")
                    router_alert = True
                else: raise RuntimeError("unaccounted IPv6 Hop-by-Hop option")
                option += 2+length
            cursor, extensions = end, 1
        if protocol not in (6, 58):
            raise RuntimeError("unaccounted IPv6 protocol or unsupported extension/fragment chain")
        if protocol == 6 and router_alert:
            raise RuntimeError("unexpected Router Alert on TCP")
        return protocol, packet[cursor:total], router_alert, extensions

    def ipv6_control(packet, body, router_alert, extensions):
        # RFC 4861 RS/NS/NA and RFC 3810 MLDv2 only; no generic ICMP skip.
        if len(body) < 8 or body[1] != 0: raise RuntimeError("short/unsupported ICMPv6 control")
        pseudo = packet[8:40]+len(body).to_bytes(4, "big")+b"\0\0\0\x3a"+body
        if len(pseudo) % 2: pseudo += b"\0"
        checksum = sum(int.from_bytes(pseudo[i:i+2], "big") for i in range(0, len(pseudo), 2))
        while checksum >> 16: checksum = (checksum & 0xffff)+(checksum >> 16)
        if checksum != 0xffff: raise RuntimeError("invalid ICMPv6 control checksum")
        src, dst = ipaddress.ip_address(packet[8:24]), ipaddress.ip_address(packet[24:40])
        kind = body[0]
        if kind == 143:
            if not (extensions == 1 and router_alert and packet[7] == 1 and src.is_link_local and str(dst) == "ff02::16"):
                raise RuntimeError("invalid MLDv2 link scope/Router Alert")
            count = int.from_bytes(body[6:8], "big")
            if body[4:6] != b"\0\0" or not 1 <= count <= 16: raise RuntimeError("invalid MLDv2 record count/reserved bytes")
            cursor = 8
            for _ in range(count):
                if cursor+20 > len(body): raise RuntimeError("truncated MLDv2 record")
                record, auxiliary, sources = struct.unpack("!BBH", body[cursor:cursor+4])
                end = cursor+20+16*sources
                if record not in range(1, 7) or auxiliary != 0 or sources > 16 or end > len(body):
                    raise RuntimeError("unsupported/bounded MLDv2 record shape")
                if not ipaddress.ip_address(body[cursor+4:cursor+20]).is_multicast:
                    raise RuntimeError("MLDv2 group is not multicast")
                for index in range(cursor+20, end, 16):
                    address = ipaddress.ip_address(body[index:index+16])
                    if address.is_multicast or address.is_unspecified: raise RuntimeError("invalid MLDv2 source address")
                cursor = end
            if cursor != len(body): raise RuntimeError("unaccounted MLDv2 trailing bytes")
            return "143_mldv2_report"
        if kind not in (133, 135, 136) or extensions or router_alert or packet[7] != 255:
            raise RuntimeError("unaccounted ICMPv6 control type/extension/hop limit")
        if kind == 133:
            if body[4:8] != b"\0"*4 or str(dst) != "ff02::2" or not (src.is_link_local or src.is_unspecified):
                raise RuntimeError("invalid router solicitation scope/reserved bytes")
            cursor, allowed_option = 8, 1
        else:
            if len(body) < 24 or ipaddress.ip_address(body[8:24]).is_multicast:
                raise RuntimeError("invalid neighbor control target/length")
            reserved = int.from_bytes(body[4:8], "big")
            if (kind == 135 and reserved) or (kind == 136 and reserved & 0x1fffffff):
                raise RuntimeError("nonzero neighbor control reserved bits")
            cursor, allowed_option = 24, 1 if kind == 135 else 2
        seen_option = False
        while cursor < len(body):
            if cursor+8 > len(body) or body[cursor] != allowed_option or body[cursor+1] != 1 or seen_option:
                raise RuntimeError("unaccounted/malformed neighbor control option")
            seen_option = True
            cursor += 8
        if src.is_multicast or (src.is_unspecified and (seen_option or kind == 136)):
            raise RuntimeError("invalid neighbor control source")
        return {133: "133_router_solicitation", 135: "135_neighbor_solicitation", 136: "136_neighbor_advertisement"}[kind]

    def server_selected_tls13(body):
        if not body or body[0] != 2:
            return False
        if len(body) < 44 or int.from_bytes(body[1:4], "big") != len(body)-4:
            raise RuntimeError("malformed captured ServerHello")
        cursor = 39 + body[38]  # handshake header, legacy_version, random, session_id
        if cursor+5 > len(body): raise RuntimeError("truncated ServerHello session ID")
        cursor += 3  # cipher suite and legacy compression
        end = cursor+2+int.from_bytes(body[cursor:cursor+2], "big")
        cursor += 2
        if end != len(body): raise RuntimeError("ServerHello extension length differs")
        selected = None
        while cursor < end:
            if cursor+4 > end: raise RuntimeError("short ServerHello extension")
            kind, size = struct.unpack("!HH", body[cursor:cursor+4])
            cursor += 4
            if cursor+size > end: raise RuntimeError("ServerHello extension overrun")
            if kind == 43:
                if selected is not None: raise RuntimeError("duplicate supported_versions")
                selected = body[cursor:cursor+size]
            cursor += size
        return selected == b"\x03\x04"

    if path.stat().st_size > 256 * 1024 * 1024:
        raise RuntimeError("external capture exceeds 256 MiB review bound")
    data = path.read_bytes()
    if len(data) < 24: raise RuntimeError("pcap header missing")
    magic = data[:4]
    if magic == b"\xd4\xc3\xb2\xa1": endian = "<"
    elif magic == b"\xa1\xb2\xc3\xd4": endian = ">"
    else: raise RuntimeError("unsupported pcap encoding")
    _, major, minor, _, _, snaplen, network = struct.unpack(endian + "IHHIIII", data[:24])
    if (major, minor, network) != (2, 4, 1) or snaplen < 65535: raise RuntimeError("pcap does not retain full Ethernet packets")
    offset, packets = 24, 0
    streams, resets = {}, set()
    control_types, ipv6_extensions = {}, 0
    while offset < len(data):
        if offset + 16 > len(data): raise RuntimeError("truncated pcap packet header")
        _, _, captured, original = struct.unpack(endian + "IIII", data[offset:offset+16])
        offset += 16
        frame = data[offset:offset+captured]
        offset += captured
        if captured != original or len(frame) != captured or captured < 14: raise RuntimeError("truncated captured frame")
        packets += 1
        kind = int.from_bytes(frame[12:14], "big")
        packet = frame[14:]
        if kind == 0x0800:
            if len(packet) < 20 or packet[0] >> 4 != 4:
                raise RuntimeError("short/invalid IPv4 header")
            header = (packet[0] & 15) * 4
            total = int.from_bytes(packet[2:4], "big")
            if not 20 <= header <= total <= len(packet): raise RuntimeError("truncated IPv4 packet")
            if packet[9] != 6: raise RuntimeError("unaccounted external IPv4 protocol")
            if int.from_bytes(packet[6:8], "big") & 0x3fff: raise RuntimeError("fragmented external TCP unsupported")
            version = 4
            src, dst = str(ipaddress.ip_address(packet[12:16])), str(ipaddress.ip_address(packet[16:20]))
            tcp = packet[header:total]
        elif kind == 0x86dd:
            if len(packet) < 40 or packet[0] >> 4 != 6:
                raise RuntimeError("short/invalid IPv6 header")
            total = 40+int.from_bytes(packet[4:6], "big")
            if total > len(packet): raise RuntimeError("truncated IPv6 packet")
            protocol, tcp, router_alert, extensions = ipv6_payload(packet, total)
            ipv6_extensions += extensions
            if protocol == 58:
                control = ipv6_control(packet, tcp, router_alert, extensions)
                control_types[control] = control_types.get(control, 0)+1
                continue
            version = 6
            src, dst = str(ipaddress.ip_address(packet[8:24])), str(ipaddress.ip_address(packet[24:40]))
        elif kind == 0x0806: continue  # isolated-link ARP
        else: raise RuntimeError(f"unexpected external EtherType {kind:#x}")
        if len(tcp) < 20: raise RuntimeError("short external TCP packet")
        tcp_header = (tcp[12] >> 4)*4
        if not 20 <= tcp_header <= len(tcp): raise RuntimeError("invalid TCP header length")
        sport, dport, seq = struct.unpack("!HHI", tcp[:8])
        flags = tcp[13]
        if sport == 3240 or dport == 3240:
            if sport == 3240 and flags & 4: resets.add(version)
            if len(tcp) != tcp_header: raise RuntimeError("plaintext-port payload observed")
            continue
        if 3241 not in (sport, dport): raise RuntimeError("unaccounted TCP on external link")
        if version != (4 if family == "ipv4" else 6): raise RuntimeError("TLS used the wrong IP family")
        key = (version, src, sport, dst, dport)
        state = streams.setdefault(key, {"syn": None, "chunks": []})
        if flags & 2:
            if state["syn"] not in (None, seq): raise RuntimeError("TCP tuple reused during this bounded capture")
            state["syn"] = seq
        payload_bytes = tcp[tcp_header:]
        if payload_bytes: state["chunks"].append((seq + int(bool(flags & 2)), payload_bytes))
    if resets != {4, 6}: raise RuntimeError("both IPv4/IPv6 3240 refusals not observed as real RST")
    flows = []
    for key, state in streams.items():
        if not state["chunks"]: continue
        if state["syn"] is None: raise RuntimeError("capture missed stream handshake")
        start = state["syn"] + 1
        assembled = bytearray()
        for sequence, chunk in sorted(state["chunks"], key=lambda part: (part[0]-start) % 2**32):
            index = (sequence - start) % 2**32
            if index < 0 or index > len(assembled): raise RuntimeError("TCP stream capture contains a gap")
            overlap = min(len(chunk), len(assembled)-index)
            if assembled[index:index+overlap] != chunk[:overlap]: raise RuntimeError("conflicting TCP retransmission")
            assembled.extend(chunk[overlap:])
        wire = bytes(assembled)
        if not wire or wire[0] != 22: raise RuntimeError("external application stream did not start as TLS handshake")
        for marker in [b"\x01\x11\x80\x05\0\0\0\0", b"\x01\x11\x80\x03\0\0\0\0", b"RUSTUSB-RX-", b"RUSTUSB-TX-", b'"op":"catalog"', b'"op":"import"']:
            if marker in wire: raise RuntimeError("clear USB/IP management or fixture data on external link")
        for index in range(max(0, len(wire)-20+1)):
            cmd, _, devid, direction, endpoint = struct.unpack("!IIIII", wire[index:index+20])
            if cmd in (1, 2, 3, 4) and devid in device_ids and direction <= 1 and endpoint <= 15:
                raise RuntimeError("recognizable clear USB/IP URB header on external link")
        position, records, application = 0, 0, 0
        server_hello_tls13 = False
        while position < len(wire):
            if position+5 > len(wire): raise RuntimeError("incomplete TLS record header")
            content, version, length = struct.unpack("!BHH", wire[position:position+5])
            end = position+5+length
            if content not in (20, 21, 22, 23) or version not in (0x0301, 0x0303) or length > 18432 or end > len(wire):
                raise RuntimeError("external TCP bytes do not form complete bounded TLS records")
            body = wire[position+5:end]
            if content == 22 and server_selected_tls13(body):
                server_hello_tls13 = True
            application += content == 23
            records += 1
            position = end
        if not application: raise RuntimeError("TLS stream lacks encrypted application records")
        if key[2] == 3241 and not server_hello_tls13: raise RuntimeError("TLS 1.3 ServerHello selection absent")
        if key[4] == 3241 and b"rustusb-usbip/1" not in wire: raise RuntimeError("client offered ALPN not captured")
        flows.append({"tuple": key, "stream_bytes": len(wire), "tls_records": records,
                      "encrypted_application_records": application, "stream_sha256": hashlib.sha256(wire).hexdigest()})
    if len(flows) < 16 or len(flows) % 2: raise RuntimeError("insufficient complete bidirectional TLS connections")
    return {"packets": packets, "flows": flows, "port_3240_rst_families": sorted(resets),
            "ipv6_hop_by_hop_headers": ipv6_extensions, "icmpv6_control_types": control_types,
            "scope": "full external Ethernet capture; contiguous TCP/TLS framing and absence of specified clear management/URB/fixture signatures; no TLS key logging/decryption"}


def verify(evidence, args):
    """Terminal raw evidence only; checks are grouped facts, not USB case counts."""
    facts = {}
    for role in ["server", "client"]:
        log = (evidence / f"{role}-evidence.log").read_text()
        if f"RUSTUSB_SECURE_RESULT: PASS role={role}" not in log or "RUSTUSB_SECURE_RESULT: FAIL" in log:
            raise RuntimeError(f"missing terminal guest success: {role}")
        if "SECURE_ARTIFACT_OVERSIZE" in log: raise RuntimeError("guest artifact was not fully retained")
        if "SECURE_ARTIFACT_ERROR" in log or "SECURE_POWEROFF_RETURNED" in log:
            raise RuntimeError("guest collection or poweroff failed")
        if f"SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false" not in log:
            raise RuntimeError("guest role-specific credential isolation assertion absent")
        if "SECURE_RNG_RTC_READY crng=true " not in log:
            raise RuntimeError("guest actual CRNG/RTC readiness assertion absent")
    root = evidence / "client-artifacts"
    catalog = json.loads((root / "catalog-initial.json").read_text())
    if len(catalog) != 2: raise RuntimeError("authenticated catalog did not contain exactly two devices")
    device_ids = {(entry["device"]["bus_number"] << 16) | entry["device"]["device_number"] for entry in catalog}
    receipts = []
    for cycle in [1, 2]:
        for direction in ["RX", "TX"]:
            ending = "0123456789abcdef" if direction == "RX" else "fedcba9876543210"
            expected = f"RUSTUSB-{direction}-{cycle}-0001:{ending}\n".encode()
            if (root / f"{direction.lower()}-{cycle}.bin").read_bytes() != expected: raise RuntimeError("serial fixture bytes differ")
        events = (root / f"input-{cycle}.bin").read_bytes()
        if len(events) % 24: raise RuntimeError("partial Linux input_event capture")
        transitions = {struct.unpack("<qqHHi", events[index:index+24])[2:] for index in range(0, len(events), 24)}
        if not {(1, cycle+1, 0), (1, cycle+1, 1)} <= transitions: raise RuntimeError("actual HID key down/up absent")
        for profile in ["hid", "serial"]:
            stream = [json.loads(line) for line in (root / f"attach-{profile}-{cycle}.events").read_text().splitlines()]
            connected = [event for event in stream if event.get("event") == "connected"]
            if len(connected) != 1 or connected[0]["transport"] != "mtls": raise RuntimeError("ambiguous secure connected receipt")
            if not any(event.get("event") == "disconnected" and event.get("cleanup_verified") is True for event in stream): raise RuntimeError("cleanup receipt absent")
            if (root / f"wait-{profile}-{cycle}.txt").read_text().strip() != "0": raise RuntimeError("broker did not really exit zero")
            receipts.append(connected[0]["receipt"])
        if args.backend == "virtual" and (root / f"echo-{cycle}.bin").read_bytes() != (root / f"tx-{cycle}.bin").read_bytes(): raise RuntimeError("CDC echo differs")
        empty_state = json.loads((root / f"channels-empty-{cycle}.json").read_text())
        if empty_state["records"] != [] or empty_state["exports"] != []:
            raise RuntimeError("client Native channel intent not retired or unexpected export ownership")
        if json.loads((root / f"catalog-reuse-{cycle}.json").read_text()) != catalog: raise RuntimeError("catalog did not return after cleanup")
    if len({receipt["channel_id"] for receipt in receipts}) != 4: raise RuntimeError("channel identity reused")
    if args.backend != "virtual":
        server = evidence / "server-artifacts"
        originals = ["host-after-denied.json", "host-final.json"]
        if args.backend == "usbfs": originals += ["host-released-1.json", "host-released-2.json"]
        for name in originals:
            if (server / name).read_bytes() != (server / "host-before.json").read_bytes(): raise RuntimeError("complete original physical baseline differs")
        if (evidence / "ftdi-host-rx.bin").read_bytes() != b"".join(f"RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n".encode() for cycle in [1, 2]): raise RuntimeError("host FTDI exact TX bytes differ")
        if args.backend == "native":
            final_state = json.loads((server / "server-native-final.json").read_text())
            if final_state["records"] or final_state["exports"]:
                raise RuntimeError("Native server export state not retired")
            baseline = json.loads((server / "host-before.json").read_text())
            exports = json.loads((server / "server-native-exported.json").read_text())["exports"]
            stub_ids = []
            for cycle in [1, 2]:
                held = json.loads((server / f"server-native-held-{cycle}.json").read_text())["exports"]
                released = json.loads((server / f"server-native-released-{cycle}.json").read_text())["exports"]
                for record in held:
                    stub_ids.append(record["stub"]["channel_id"])
                    if record["stub"]["state"]["phase"] != "observed" or record["stub"]["state"]["receipt"]["usbip_status"] != 2:
                        raise RuntimeError("Native held stub was not observed USED")
                if any(record["stub"] is not None for record in released):
                    raise RuntimeError("Native stub remained after cooperative close")
                for collection in [held, released]:
                    clean = lambda rows: sorted(({k: v for k, v in row.items() if k != "stub"} for row in rows), key=lambda row: row["lease_id"])
                    if clean(collection) != clean(exports): raise RuntimeError("Native export registration changed between cycles")
            if len(set(stub_ids)) != 4: raise RuntimeError("Native server stub channel identity reused")
            for stage, occupied in [("exported", 1), ("held-1", 2), ("io-1", 2), ("released-1", 1), ("held-2", 2), ("io-2", 2), ("released-2", 1)]:
                snapshot = json.loads((server / f"host-{stage}.json").read_text())
                dynamic = {"configuration", "num_interfaces", "configuration_bytes", "count_bytes", "interfaces", "device_driver", "usbip_status"}
                hardware = lambda rows: [{key: value for key, value in row.items() if key not in dynamic} for row in rows]
                if hardware(snapshot) != hardware(baseline): raise RuntimeError("Native hardware generation changed")
                for index, device in enumerate(snapshot):
                    if device["device_driver"] != "usbip-host" or device["usbip_status"] != occupied:
                        raise RuntimeError("Native sysfs phase driver/occupancy differs")
                    if stage == "exported":
                        if any(device[key] != expected for key, expected in {"configuration": "", "num_interfaces": "", "configuration_bytes": 0, "count_bytes": 0, "interfaces": []}.items()):
                            raise RuntimeError("Native exported profile was not truly unconfigured")
                    else:
                        expected = [dict(interface, driver="") for interface in baseline[index]["interfaces"]]
                        if int(device["configuration"]) != 1 or int(device["num_interfaces"]) != len(expected) or device["interfaces"] != expected:
                            raise RuntimeError("Native configured phase complete interfaces differ")
            facts["native_server"] = {"export_leases": [row["lease_id"] for row in exports], "stub_channels": stub_ids,
                                      "publication_scope": "pre-export registration; phase proven separately by sysfs",
                                      "complete_original_baseline_after_server_term": True}
    for role in ["server", "client"]:
        policy = evidence / f"{role}-artifacts/helper-policy.json"
        if policy.exists() and json.loads(policy.read_text())["trusted_network"] is not False: raise RuntimeError("harness asserted legacy trusted network")
    facts["cycles"] = [{"cycle": cycle, "hid_key_code": cycle+1, "serial_each_direction_bytes": 35} for cycle in [1, 2]]
    facts["receipts"] = receipts
    facts["external_link"] = pcap_review(evidence / "server-external.pcap", args.family, device_ids)
    return facts


def main(coordinate=None, scope=None, phase_ready=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--backend", choices=["virtual", "usbfs", "native"], required=True)
    parser.add_argument("--family", choices=["ipv4", "ipv6"], required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence uses the locked all-features build")
    args = parser.parse_args()
    if os.geteuid() == 0 or os.uname().machine != "x86_64": raise RuntimeError("ordinary x86_64 host user required")
    if not 60 <= args.timeout <= 900: raise RuntimeError("timeout must be 60..900 seconds")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": 1, "result": "FAIL", "backend": args.backend, "family": args.family,
               "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "head": git("rev-parse", "HEAD"),
               "git_status_before": git("status", "--porcelain"), "source_inputs": inputs(),
               "scope": scope or "two disposable QEMU guests, dedicated UID2000 actual secure CLI; simulated devices only; virtual/USBFS/Native x IPv4/IPv6 are six independent runs, no hardware claim",
               "build_features": "--workspace --all-features --release --locked", "build_skipped": args.skip_build}
    processes, handles, connections = {}, [], {}
    qmp = ftdi = None
    sent = set()
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    try:
        if summary["git_status_before"]: raise RuntimeError("clean committed source required")
        for name in summary["source_inputs"]:
            destination = evidence / "source" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, destination)
        if not args.skip_build:
            command(["cargo", "build", "--workspace", "--all-features", "--release", "--locked"], evidence / "build.log")
        if inputs() != summary["source_inputs"]: raise RuntimeError("build changed frozen inputs")
        summary["binary_sha256"] = {name: digest(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        command(["qemu-system-x86_64", "--version"], evidence / "tool-versions.log")
        command(["rustc", "--version", "--verbose"], evidence / "tool-versions.log")
        with tempfile.TemporaryDirectory(prefix="rustusb-secure-") as temporary:
            work = Path(temporary)
            certs = credentials(work, evidence)
            archives = {}
            summary["payload_sha256"] = {}
            summary["initramfs_sha256"] = {}
            summary["credential_payloads"] = {}
            for role in ["server", "client"]:
                role_work = work / f"{role}-payload"
                role_work.mkdir()
                archives[role], summary["payload_sha256"][role] = payload(role_work, args.kernel, certs, role)
                shutil.copyfile(role_work / "payload-dependencies.json", evidence / f"{role}-payload-dependencies.json")
                summary["initramfs_sha256"][role] = digest(archives[role])
                summary["credential_payloads"][role] = sorted(path.name for path in (role_work / "root/etc/rustusb").iterdir())
                for name, sha in summary["binary_sha256"].items():
                    if summary["payload_sha256"][role][f"usr/bin/{name}"] != sha:
                        raise RuntimeError("ELF changed during role-specific payload copy")
            image = work / "vmlinuz"
            try: shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            summary["kernel_image_sha256"] = digest(image)
            kernel_config = Path("/boot") / f"config-{args.kernel}"
            shutil.copyfile(kernel_config, evidence / "kernel.config")
            summary["kernel_config_sha256"] = digest(kernel_config)
            summary["kernel"] = args.kernel
            links = socket.socketpair()
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            deadline = time.monotonic() + args.timeout
            for index, role in enumerate(["server", "client"]):
                for channel in ["serial", "evidence"]: (evidence / f"{role}-{channel}.log").touch()
                output = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(output)
                cmd = ["qemu-system-x86_64", "-m", "768", "-smp", "2", *acceleration, "-rtc", "base=utc,clock=host", "-kernel", str(image), "-initrd", str(archives[role]),
                       "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.secure=1 rustusb.role={role} rustusb.backend={args.backend} rustusb.family={args.family}",
                       "-display", "none", "-serial", f"file:{evidence / (role+'-serial.log')}", "-serial", f"file:{evidence / (role+'-evidence.log')}",
                       "-chardev", f"socket,id=control,path={work / (role+'-control.sock')},server=on,wait=off", "-serial", "chardev:control",
                       "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=external,fd={links[index].fileno()}",
                       "-device", f"e1000,netdev=external,mac=52:54:00:00:00:{61+index}",
                       "-object", f"filter-dump,id=capture,netdev=external,file={evidence / (role+'-external.pcap')}"]
                if role == "server" and args.backend != "virtual":
                    cmd += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,id=secure-kbd,bus=usb.0,port=1",
                            "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off",
                            "-device", "usb-serial,id=secure-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
                            "-qmp", f"unix:{work / 'qmp.sock'},server=on,wait=off"]
                summary.setdefault("qemu_commands", {})[role] = cmd
                processes[role] = subprocess.Popen(cmd, pass_fds=[links[index].fileno()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=output)
            for link in links: link.close()
            for role in processes: connections[role] = connect_unix(work / f"{role}-control.sock", processes[role], deadline)
            if args.backend != "virtual":
                qmp = physical.Qmp(work / "qmp.sock", deadline, processes["server"])
                ftdi = connect_unix(work / "ftdi.sock", processes["server"], deadline)
                ftdi.setblocking(False)
            tx = bytearray()
            control_log = evidence / "host-control.jsonl"

            def send_once(mark, role, message):
                if mark in sent: return
                connections[role].sendall(message.encode() + b"\n")
                with control_log.open("a") as log:
                    log.write(json.dumps({"monotonic": time.monotonic(), "mark": mark, "role": role, "command": message}) + "\n")
                sent.add(mark)

            while time.monotonic() < deadline:
                logs = {role: (evidence / f"{role}-evidence.log").read_text(errors="replace") for role in processes}
                if any("RUSTUSB_SECURE_RESULT: FAIL" in log for log in logs.values()): raise RuntimeError("guest reported actual failure")
                for role in processes:
                    kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
                    if "Kernel panic" in kernel or "Oops:" in kernel: raise RuntimeError(f"{role} kernel fault")
                keys = re.search(r"^SECURE_KEYS (\[.*\])$", logs["server"], re.M)
                if coordinate is not None:
                    coordinate(logs, send_once, evidence, args)
                if keys: send_once("keys", "client", keys[1])
                if "SECURE_SERVER_READY\n" in logs["server"] and "keys" in sent: send_once("start", "client", "START")
                if ftdi:
                    while select.select([ftdi], [], [], 0)[0]:
                        chunk = ftdi.recv(4096)
                        if not chunk: break
                        tx.extend(chunk)
                        (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                        if len(tx) > 70: raise RuntimeError("unexpected extra FTDI output")
                for cycle in [1, 2]:
                    if f"SECURE_CLIENT_WAIT_INPUT cycle={cycle}\n" in logs["client"] and (phase_ready is None or phase_ready(logs, "input", cycle)):
                        send_once(f"inject-{cycle}", "server", f"INJECT {cycle}")
                        if ftdi and f"usb-inject-{cycle}" not in sent:
                            arguments = {"keys": [{"type": "qcode", "data": str(cycle)}], "hold-time": 100}
                            response = qmp.call("send-key", arguments)
                            with (evidence / "qmp-input.jsonl").open("a") as log:
                                log.write(json.dumps({"command": "send-key", "arguments": arguments, "return": response}) + "\n")
                            ftdi.settimeout(2)
                            fixture = f"RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n".encode()
                            ftdi.sendall(fixture)
                            ftdi.setblocking(False)
                            (evidence / f"ftdi-host-injected-{cycle}.bin").write_bytes(fixture)
                            sent.add(f"usb-inject-{cycle}")
                    if f"SECURE_CLIENT_IO cycle={cycle} " in logs["client"] and f"SECURE_SERVER_INJECTED cycle={cycle}\n" in logs["server"]:
                        send_once(f"io-{cycle}", "server", f"IO {cycle}")
                    if f"SECURE_SERVER_IO cycle={cycle}\n" in logs["server"] and (not ftdi or len(tx) >= 35*cycle) and (phase_ready is None or phase_ready(logs, "close", cycle)):
                        send_once(f"close-{cycle}", "client", f"CLOSE {cycle}")
                    if f"SECURE_CLIENT_RELEASED cycle={cycle} " in logs["client"]:
                        send_once(f"released-{cycle}", "server", f"RELEASED {cycle}")
                    if f"SECURE_SERVER_RELEASED cycle={cycle}\n" in logs["server"]:
                        send_once(f"clean-{cycle}", "client", f"CLEAN {cycle}")
                if "SECURE_CLIENT_REUSE cycle=2\n" in logs["client"]: send_once("stop", "server", "STOP")
                if "SECURE_SERVER_STOPPED " in logs["server"]: send_once("finish", "client", "FINISH")
                if all(process.poll() is not None for process in processes.values()): break
                if any(process.poll() is not None and f"RUSTUSB_SECURE_RESULT: PASS role={role}" not in logs[role] for role, process in processes.items()):
                    raise RuntimeError("guest exited without terminal PASS")
                time.sleep(0.05)
            else: raise RuntimeError("two-guest secure run deadline exceeded")
            if any(process.wait() != 0 for process in processes.values()): raise RuntimeError("nonzero actual QEMU exit")
            if ftdi:
                while select.select([ftdi], [], [], 0)[0]:
                    chunk = ftdi.recv(4096)
                    if not chunk: break
                    tx.extend(chunk)
                (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                (evidence / "qmp-events.json").write_text(json.dumps(qmp.events, indent=2) + "\n")
            summary["guest_artifacts"] = artifacts(evidence)
            execution_hashes = []
            helper_hashes = []
            for role in ["server", "client"]:
                for path in (evidence / f"{role}-artifacts").glob("executable-*.txt"):
                    actual = path.read_text().split()[0]
                    helper = path.name == "executable-helper.txt"
                    program = "rustusb-helper" if helper else "rustusb"
                    if actual != summary["binary_sha256"][program]:
                        raise RuntimeError("actual guest broker executable differs from frozen ELF")
                    (helper_hashes if helper else execution_hashes).append(str(path.relative_to(evidence)))
            if len(execution_hashes) != 7:
                raise RuntimeError("missing actual broker executable/identity observations")
            if len(helper_hashes) != (1 if args.backend == "virtual" else 2):
                raise RuntimeError("missing actual privileged helper executable observations")
            summary["actual_broker_executable_receipts"] = execution_hashes
            summary["actual_helper_executable_receipts"] = helper_hashes
            summary["facts"] = verify(evidence, args)
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = f"{type(error).__name__}: {error}"
    finally:
        # Preserve the first failure and give PID1's failure trap time to finish
        # its raw artifact dump. ABORT is deliberately never an accepted command.
        # Killing QEMU at the first FAIL marker would discard the following dump.
        if summary["result"] != "PASS" and connections:
            for role, connection in connections.items():
                if processes[role].poll() is None:
                    try: connection.sendall(b"ABORT\n")
                    except OSError: pass
            capture_deadline = time.monotonic() + 25
            while any(process.poll() is None for process in processes.values()) and time.monotonic() < capture_deadline:
                time.sleep(0.05)
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        for connection in connections.values(): connection.close()
        if qmp: qmp.close()
        if ftdi: ftdi.close()
        for handle in handles: handle.close()
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        summary["head_after"] = git("rev-parse", "HEAD")
        summary["git_status_after"] = git("status", "--porcelain")
        summary["source_guard"] = inputs() == summary["source_inputs"]
        summary["binary_guard"] = all(digest(REPO / "target/release" / name) == sha for name, sha in summary.get("binary_sha256", {}).items())
        summary["head_guard"] = summary["head_after"] == summary["head"]
        summary["clean_guard"] = not summary["git_status_before"] and not summary["git_status_after"]
        if not all(summary[key] for key in ["source_guard", "binary_guard", "head_guard", "clean_guard"]): summary["result"] = "FAIL"
        if processes and "guest_artifacts" not in summary:
            try: summary["guest_artifacts"] = artifacts(evidence)
            except Exception as error: summary["artifact_error"] = str(error)
        summary["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        summary["artifact_manifest"] = {str(path.relative_to(evidence)): {"bytes": path.stat().st_size, "sha256": digest(path)}
                                        for path in evidence.rglob("*") if path.is_file() and path.name != "summary.json"}
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"result": summary["result"], "backend": args.backend, "family": args.family,
                      "evidence": str(evidence), "error": summary.get("error")}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
