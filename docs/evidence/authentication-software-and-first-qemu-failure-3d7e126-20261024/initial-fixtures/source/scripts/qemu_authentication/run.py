#!/usr/bin/env python3
"""Installed authentication refusals and cooperative startup pin retirement.

Two disposable guests; never invoke the generated PID1 on the host. Use a clean
checkpoint and a new evidence directory. Backend/family/kernel/build arguments
are those of scripts/qemu/run-secure-qemu.py. See README.md and the design.
"""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


base = module("authentication_secure_base", ROOT / "scripts/qemu/run-secure-qemu.py")
checker = module("authentication_raw_checker", HERE / "verify.py")
original_credentials = base.credentials
original_payload = base.payload
original_inputs = base.inputs


def inputs():
    result = original_inputs()
    for path in [ROOT / "rust-toolchain.toml", ROOT / "scripts/check.sh", *HERE.glob("*.py"), *HERE.glob("*.sh"), HERE / "README.md"]:
        result[str(path.relative_to(ROOT))] = {"sha256": base.digest(path), "bytes": path.stat().st_size}
    return dict(sorted(result.items()))


def credentials(work, evidence):
    directory = original_credentials(work, evidence)
    log = evidence / "certificate-generation.log"
    command = lambda args: base.command(["openssl", *args], log)
    command(["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Disposable unrelated CA",
             "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-keyout", directory / "other-ca-key.pem", "-out", directory / "other-ca.pem"])
    command(["x509", "-in", directory / "other-ca.pem", "-outform", "DER", "-out", directory / "other-ca.der"])
    (directory / "index.txt").touch()
    (directory / "serial").write_text("1000\n")
    (directory / "ca.conf").write_text(
        f"[ca]\ndefault_ca=issuer\n[issuer]\ndatabase={directory}/index.txt\nserial={directory}/serial\n"
        f"new_certs_dir={directory}\ncertificate={directory}/ca.pem\nprivate_key={directory}/ca-key.pem\n"
        "default_md=sha256\ndefault_days=2\npolicy=names\n[names]\ncommonName=supplied\n")
    for name in ["replacement", "limited", "unmapped", "expired", "badeku", "outsider"]:
        eku = "serverAuth" if name == "badeku" else "clientAuth"
        (directory / f"{name}.ext").write_text(
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            f"extendedKeyUsage={eku}\n")
        command(["req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN=Disposable {name}",
                 "-keyout", directory / f"{name}-key.pem", "-out", directory / f"{name}.csr"])
        if name == "expired":
            command(["ca", "-batch", "-notext", "-config", directory / "ca.conf", "-startdate", "20200101000000Z",
                     "-enddate", "20210101000000Z", "-extfile", directory / f"{name}.ext", "-in", directory / f"{name}.csr",
                     "-out", directory / f"{name}.pem"])
        else:
            issuer = "other-ca" if name == "outsider" else "ca"
            command(["x509", "-req", "-days", "2", "-in", directory / f"{name}.csr", "-CA", directory / f"{issuer}.pem",
                     "-CAkey", directory / f"{issuer}-key.pem", "-CAcreateserial", "-extfile", directory / f"{name}.ext",
                     "-out", directory / f"{name}.pem"])
        command(["x509", "-in", directory / f"{name}.pem", "-outform", "DER", "-out", directory / f"{name}.der"])
        command(["pkcs8", "-topk8", "-nocrypt", "-in", directory / f"{name}-key.pem", "-outform", "DER", "-out", directory / f"{name}-key.der"])
    facts = {}
    for name in ["ca", "other-ca", "server", "client", "replacement", "limited", "unmapped", "expired", "badeku", "outsider"]:
        shutil.copyfile(directory / f"{name}.der", evidence / f"{name}-public.der")
        inspection = subprocess.run(["openssl", "x509", "-in", str(directory / f"{name}.pem"), "-noout", "-dates", "-subject", "-issuer", "-purpose"], capture_output=True, check=True)
        (evidence / f"{name}-public-inspection.txt").write_bytes(inspection.stdout + inspection.stderr)
        if name in ["ca", "other-ca", "server"]:
            continue
        verification = subprocess.run(["openssl", "verify", "-purpose", "sslclient", "-CAfile", str(directory / "ca.pem"), str(directory / f"{name}.pem")], capture_output=True)
        (evidence / f"{name}-host-verify.txt").write_bytes(verification.stdout + verification.stderr)
        expected = name not in ["expired", "badeku", "outsider"]
        if (verification.returncode == 0) != expected:
            raise RuntimeError(f"certificate fixture does not have intended verification result: {name}")
        facts[name] = {"sha256": base.digest(directory / f"{name}.der"), "host_verify_exit": verification.returncode,
                       "expected_standard_client_pki": expected}
    (evidence / "certificate-fixture-facts.json").write_text(json.dumps(facts, indent=2) + "\n")
    return directory


def generated_init():
    source = (ROOT / "scripts/qemu/guest-secure-init.sh").read_text()

    def replace(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise RuntimeError(f"fixed guest source anchor is absent or ambiguous: {old[:90]!r}")
        source = source.replace(old, new, 1)

    replace('if [ "$ROLE" = server ]; then\n    STAGE=server-prepare',
            (HERE / "extra.sh").read_text() + '\nif [ "$ROLE" = server ]; then\n    STAGE=server-prepare')
    replace('    cp /etc/rustusb/server.json /tmp/server-config.json',
            '    auth_policy 1\n    cp /etc/rustusb/server.json /tmp/server-config.json')
    replace("    echo 'SECURE_SERVER_READY'", "    auth_process \"$HELPER_PID\" initial-helper\n    echo 'SECURE_SERVER_READY'\n    auth_server_barrier free-1 exported")
    replace('        if [ "$BACKEND" = virtual ]; then\n            control "{\\"id\\":\\"key-$cycle',
            '        auth_server_barrier "held-$cycle" held\n        if [ "$BACKEND" = virtual ]; then\n            control "{\\"id\\":\\"key-$cycle')
    replace('        echo "SECURE_SERVER_RELEASED cycle=$cycle"',
            '        if [ "$cycle" = 1 ]; then auth_rotate; fi\n        echo "SECURE_SERVER_RELEASED cycle=$cycle"\n        if [ "$cycle" = 1 ]; then auth_server_barrier free-2 exported; fi')
    replace('    host_expect START\n    broker --json secure-discover', '    auth_client_configs\n    host_expect START\n    broker --json secure-discover')
    replace("    echo 'SECURE_PLAINTEXT_REFUSED ipv4=true ipv6=true'", "    echo 'SECURE_PLAINTEXT_REFUSED ipv4=true ipv6=true'\n    auth_client_batch free-1 1")
    replace('        : > "/tmp/input-$cycle.bin"', '        auth_client_batch "held-$cycle" "$cycle"\n        : > "/tmp/input-$cycle.bin"')
    replace('        host_expect "CLEAN $cycle"',
            '        host_expect "CLEAN $cycle"\n        if [ "$cycle" = 1 ]; then\n            for profile in hid serial; do\n                jq \'.certificate_chain=["/etc/rustusb/replacement.der"]|.private_key="/etc/rustusb/replacement-key.der"\' "/etc/rustusb/client-$profile.json" > /run/auth-client.json\n                cp /run/auth-client.json "/etc/rustusb/client-$profile.json"\n            done\n            auth_client_batch free-2 2\n        fi')
    # Each server epoch starts fresh counters; both USB I/O rounds remain real.
    replace('(.counters.hid_taps)==$cycle', '(.counters.hid_taps)==1') if '(.counters.hid_taps)==$cycle' in source else None
    source = source.replace('.counters.hid_taps)==$cycle', '.counters.hid_taps)==1')
    source = source.replace('(35*$cycle)', '35').replace('(70*$cycle)', '70')
    return source


def payload(work, kernel, certificates, role):
    original_payload(work, kernel, certificates, role)
    root = work / "root"
    base.native.copy_binary(Path(shutil.which("openssl")), "/usr/bin/openssl", root)
    config = root / "etc/rustusb"
    public = ["ca.pem", "other-ca.der", "client.pem", "replacement.pem", "limited.pem", "unmapped.pem", "expired.pem", "badeku.pem", "outsider.pem"]
    public += [f"{name}.der" for name in ["replacement", "limited", "unmapped", "expired", "badeku", "outsider"]]
    private = [f"{name}-key.{suffix}" for name in ["client", "replacement", "limited", "unmapped", "expired", "badeku", "outsider"] for suffix in ["der", "pem"]] if role == "client" else []
    for name in public + private:
        shutil.copyfile(certificates / name, config / name)
        (config / name).chmod(0o600 if name in private else 0o644)
    for name in ["replacement", "limited", "expired", "badeku", "outsider"]:
        (config / f"{name}-pin.txt").write_text(base.digest(config / f"{name}.der") + "\n")
    if any((config / name).exists() for name in ["ca-key.pem", "other-ca-key.pem", "server-key.der" if role == "client" else "client-key.der"]):
        raise RuntimeError("foreign/CA private key entered role payload")
    (root / "init").write_text(generated_init())
    (root / "init").chmod(0o755)
    (work / "generated-auth-init.sh").write_bytes((root / "init").read_bytes())
    preflight = base.validate_payload(root)
    (work / "payload-dependencies.json").write_text(json.dumps(preflight, indent=2) + "\n")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
    archive = work / "auth-initramfs.cpio.gz"
    archive.write_bytes(gzip.compress(packed.stdout))
    provenance = {str(path.relative_to(root)): base.digest(path) for path in root.rglob("*") if path.is_file() and not path.is_symlink() and
                  (str(path.relative_to(root)).startswith(("usr/bin/", "modules/")) or path == root / "init")}
    return archive, provenance


def coordinate(logs, send, evidence, args):
    for label in ["free-1", "held-1", "free-2", "held-2"]:
        if f"AUTH_CLIENT_WAIT label={label}\n" in logs["client"] and f"AUTH_SERVER_WAIT label={label}\n" in logs["server"]:
            send("auth-begin-" + label, "server", "AUTH_BEGIN " + label)
        if f"AUTH_SERVER_SAVED label={label}\n" in logs["server"]:
            send("auth-go-" + label, "client", "AUTH_GO " + label)
        if f"AUTH_CLIENT_DONE label={label}\n" in logs["client"]:
            send("auth-end-" + label, "server", "AUTH_END " + label)
        if f"AUTH_SERVER_CHECKED label={label}\n" in logs["server"]:
            send("auth-checked-" + label, "client", "AUTH_CHECKED " + label)
    for role in ["server", "client"]:
        path = evidence / f"{role}-generated-auth-init.sh"
        if not path.exists():
            path.write_text(generated_init())


def main():
    base.inputs = inputs
    base.credentials = credentials
    base.payload = payload
    base.verify = lambda evidence, args: checker.verify(evidence, args, base)
    return base.main(coordinate=coordinate)


if __name__ == "__main__":
    sys.exit(main())
