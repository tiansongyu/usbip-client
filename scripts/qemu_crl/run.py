#!/usr/bin/env python3
"""Offline CRL refusals and cooperative CRL replacement in two disposable guests."""
import gzip
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


auth = module("crl_authentication_base", ROOT / "scripts/qemu_authentication/run.py")
base = auth.base
checker = module("crl_raw_checker", HERE / "verify.py")
original_generated = auth.generated_init
original_credentials = auth.credentials


def inputs():
    result = auth.inputs()
    for path in sorted(HERE.iterdir()):
        if path.is_file() and path.suffix in [".py", ".sh", ".md"]:
            result[str(path.relative_to(ROOT))] = {"sha256": base.digest(path), "bytes": path.stat().st_size}
    return dict(sorted(result.items()))


def credentials(work, evidence):
    directory = original_credentials(work, evidence)
    log = evidence / "certificate-generation.log"
    command = lambda args: base.command(["openssl", *args], log)
    for name in ["crl-blocked", "crl-rotated"]:
        command(["req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=Disposable " + name,
                 "-keyout", directory / (name + "-key.pem"), "-out", directory / (name + ".csr")])
        command(["x509", "-req", "-days", "2", "-in", directory / (name + ".csr"),
                 "-CA", directory / "ca.pem", "-CAkey", directory / "ca-key.pem", "-CAcreateserial",
                 "-extfile", directory / "replacement.ext", "-out", directory / (name + ".pem")])
        command(["x509", "-in", directory / (name + ".pem"), "-outform", "DER", "-out", directory / (name + ".der")])
        command(["pkcs8", "-topk8", "-nocrypt", "-in", directory / (name + "-key.pem"),
                 "-outform", "DER", "-out", directory / (name + "-key.der")])
        shutil.copyfile(directory / (name + ".der"), evidence / (name + "-public.der"))
    (directory / "crlnumber").write_text("1000\n")
    (directory / "crl.conf").write_text(
        "[ca]\ndefault_ca=issuer\n[issuer]\n"
        f"database={directory}/index.txt\nserial={directory}/serial\nnew_certs_dir={directory}\n"
        f"certificate={directory}/ca.pem\nprivate_key={directory}/ca-key.pem\ncrlnumber={directory}/crlnumber\n"
        "default_md=sha256\ndefault_days=2\ndefault_crl_days=2\npolicy=names\ncrl_extensions=crl_ext\n"
        "[names]\ncommonName=supplied\n[crl_ext]\nauthorityKeyIdentifier=keyid:always\n")

    def generate(name, extra=()):
        command(["ca", "-batch", "-gencrl", "-config", directory / "crl.conf", *extra, "-out", directory / (name + ".pem")])
        command(["crl", "-in", directory / (name + ".pem"), "-outform", "DER", "-out", directory / (name + ".der")])

    generate("crl-clean")
    command(["ca", "-batch", "-config", directory / "crl.conf", "-revoke", directory / "crl-blocked.pem"])
    generate("crl-epoch1")
    command(["ca", "-batch", "-config", directory / "crl.conf", "-revoke", directory / "crl-rotated.pem"])
    generate("crl-epoch2")
    command(["ca", "-batch", "-config", directory / "crl.conf", "-revoke", directory / "server.pem"])
    generate("crl-server")
    generate("crl-expired", ["-crl_lastupdate", "20200101000000Z", "-crl_nextupdate", "20210101000000Z"])
    generate("crl-future", ["-crl_lastupdate", "20300101000000Z", "-crl_nextupdate", "20400101000000Z"])
    bad = bytearray((directory / "crl-clean.der").read_bytes())
    bad[-1] ^= 1
    (directory / "crl-bad-signature.der").write_bytes(bad)
    (directory / "crl-malformed.der").write_bytes(b"not a CRL")
    public = ["crl-clean", "crl-epoch1", "crl-epoch2", "crl-server", "crl-expired", "crl-future", "crl-bad-signature", "crl-malformed"]
    facts = {}
    for name in public:
        shutil.copyfile(directory / (name + ".der"), evidence / (name + "-public.der"))
        p = subprocess.run(["openssl", "crl", "-inform", "DER", "-in", str(directory / (name + ".der")), "-noout", "-text"],
                           capture_output=True)
        (evidence / (name + "-inspection.txt")).write_bytes(p.stdout + p.stderr)
        facts[name] = {"sha256": base.digest(directory / (name + ".der")), "inspection_exit": p.returncode}
    verify_rows = []
    for identity, crl_name, expected in [
        ("crl-blocked", "crl-clean", 0), ("crl-blocked", "crl-epoch1", 23),
        ("crl-rotated", "crl-epoch1", 0), ("crl-rotated", "crl-epoch2", 23),
        ("server", "crl-server", 23),
    ]:
        p = subprocess.run(["openssl", "verify", "-CAfile", str(directory / "ca.pem"), "-crl_check",
                            "-CRLfile", str(directory / (crl_name + ".pem")), str(directory / (identity + ".pem"))],
                           capture_output=True)
        (evidence / (identity + "-" + crl_name + "-verify.txt")).write_bytes(p.stdout + p.stderr)
        if p.returncode != (0 if expected == 0 else 2):
            raise RuntimeError("unexpected independent CRL verification exit")
        if expected and (f"error {expected} ").encode() not in p.stderr:
            raise RuntimeError("missing actual independent revoked-certificate result")
        verify_rows.append({"identity": identity, "crl": crl_name, "exit": p.returncode, "expected_certificate_error": expected})
    (evidence / "crl-fixture-facts.json").write_text(json.dumps({"lists": facts, "openssl_checks": verify_rows}, indent=2) + "\n")
    return directory


def generated_init():
    source = original_generated()
    for name in ["auth_policy", "auth_client_configs", "auth_server_barrier", "auth_client_batch"]:
        old = name + "() {"
        if source.count(old) != 1:
            raise RuntimeError("ambiguous original auth function: " + name)
        source = source.replace(old, "crl_original_" + old, 1)
    anchor = 'if [ "$ROLE" = server ]; then\n    STAGE=server-prepare'
    if source.count(anchor) != 1:
        raise RuntimeError("missing CRL function insertion point")
    return source.replace(anchor, (HERE / "extra.sh").read_text() + "\n" + anchor, 1)


def payload(work, kernel, certificates, role):
    auth.payload(work, kernel, certificates, role)
    root = work / "root"
    config = root / "etc/rustusb"
    names = ["crl-clean", "crl-epoch1", "crl-epoch2", "crl-server", "crl-expired", "crl-future", "crl-bad-signature", "crl-malformed",
             "crl-blocked", "crl-rotated"]
    for name in names:
        shutil.copyfile(certificates / (name + ".der"), config / (name + ".der"))
        (config / (name + ".der")).chmod(0o644)
    for name in ["crl-blocked", "crl-rotated"]:
        (config / (name + "-pin.txt")).write_text(base.digest(config / (name + ".der")) + "\n")
        if role == "client":
            shutil.copyfile(certificates / (name + "-key.der"), config / (name + "-key.der"))
            (config / (name + "-key.der")).chmod(0o600)
    (auth.run_evidence / (role + "-generated-crl-init.sh")).write_bytes((root / "init").read_bytes())
    preflight = base.validate_payload(root)
    (work / "payload-dependencies.json").write_text(json.dumps(preflight, indent=2) + "\n")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
    archive = work / "crl-initramfs.cpio.gz"
    archive.write_bytes(gzip.compress(packed.stdout))
    provenance = {str(path.relative_to(root)): base.digest(path) for path in root.rglob("*") if path.is_file() and not path.is_symlink() and
                  (str(path.relative_to(root)).startswith(("usr/bin/", "modules/")) or path == root / "init")}
    return archive, provenance


def coordinate(logs, send, evidence, args):
    auth.coordinate(logs, send, evidence, args)
    for label in ["free-1", "held-1", "free-2", "held-2"]:
        if f"CRL_CLIENT_WAIT label={label}\n" in logs["client"] and f"CRL_SERVER_WAIT label={label}\n" in logs["server"]:
            send("crl-begin-" + label, "server", "CRL_BEGIN " + label)
        if f"CRL_SERVER_SAVED label={label}\n" in logs["server"]:
            send("crl-go-" + label, "client", "CRL_GO " + label)
        if f"CRL_CLIENT_DONE label={label}\n" in logs["client"]:
            send("crl-end-" + label, "server", "CRL_END " + label)
        if f"CRL_SERVER_CHECKED label={label}\n" in logs["server"]:
            send("crl-checked-" + label, "client", "CRL_CHECKED " + label)


def main():
    auth.generated_init = generated_init
    base.inputs = inputs
    base.credentials = credentials
    base.payload = payload
    base.verify = lambda evidence, args: checker.verify(evidence, args, base, auth.checker)
    return base.main(coordinate=coordinate, scope="one paired raw-v1 CRL run; preserves original two credential epochs/four auth barriers/actual USB I/O/full cleanup, adds complete-CRL refusals and immutable file replacement then actual cooperative restart; no hot revocation/hardware/fullG3")


if __name__ == "__main__":
    sys.exit(main())
