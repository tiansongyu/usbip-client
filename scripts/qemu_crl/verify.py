"""Read-only CRL receipts, retaining every original authentication assertion."""
import hashlib
import json
import re


def verify(evidence, args, base, original):
    inherited = original.verify(evidence, args, base)
    server = evidence / "server-artifacts"
    client = evidence / "client-artifacts"
    checks = []

    def require(value, label):
        if not value:
            raise RuntimeError("CRL evidence: " + label)
        checks.append(label)

    def obj(root, name):
        return json.loads((root / name).read_bytes())

    def data(root, name):
        return (root / name).read_bytes()

    def proc(label):
        stat = data(server, "auth-server-crl-" + label + ".stat").decode()
        pid, _, fields = stat.partition(" (")
        start = int(fields.rsplit(") ", 1)[1].split()[19])
        uid = re.search(r"^Uid:\s+2000\s+2000\s+2000\s+2000$", data(server, "auth-server-crl-" + label + ".status").decode(), re.M)
        executable = data(server, "auth-server-crl-" + label + ".exe").decode().split()[0]
        require(uid is not None and executable == base.digest(base.REPO / "target/release/rustusb"), label + " actual unprivileged server ELF")
        status = data(server, "auth-server-crl-" + label + ".status").decode()
        require(all(re.search(r"^" + field + r":\s+0+$", status, re.M) for field in ["CapInh", "CapPrm", "CapEff", "CapAmb"]), label + " no process capabilities")
        return int(pid), start

    policies = [obj(server, "auth-server-config-" + str(epoch) + ".json") for epoch in [1, 2]]
    for name in ["blocked", "rotated"]:
        pin = hashlib.sha256((evidence / ("crl-" + name + "-public.der")).read_bytes()).hexdigest()
        for policy in policies:
            rows = [row for row in policy["principals"] if row["id"] == "crl-" + name]
            require(len(rows) == 1 and rows[0]["pins"] == [pin] and rows[0]["devices"] == policy["devices"], name + " remains exactly authorized across restart")
    require(all(p["certificate_revocation_lists"] == ["/etc/rustusb/active-crl.der"] for p in policies), "both actual configs enable CRLs")
    facts = obj(evidence, "crl-fixture-facts.json")
    require(len(facts["openssl_checks"]) == 5 and all(row["exit"] == (2 if row["expected_certificate_error"] else 0) for row in facts["openssl_checks"]), "independent actual CRL fixture verification")
    process = {}
    case_rows = []
    for label in ["free-1", "held-1", "free-2", "held-2"]:
        epoch = int(label[-1])
        before, after = [obj(server, "crl-security-" + side + "-" + label + ".json") for side in ["before", "after"]]
        for snapshot in [before, after]:
            require(snapshot["consistent"] is True and snapshot["saturated"] is False and snapshot["tls"]["in_flight"] == 0, label + " resolved coherent TLS")
        # Five actual TLS attempts: blocked, rotated, server-list refusal,
        # bad-signature refusal. Six remaining cases fail before connecting.
        delta = {key: after["tls"][key] - before["tls"][key] for key in before["tls"]}
        require(delta["attempts"] == 5, label + " exact actual TLS attempts")
        require(delta["authenticated"] == (2 if epoch == 1 else 1), label + " actual unrevoked authentication count")
        require(delta["handshake_failed"] == (3 if epoch == 1 else 4), label + " actual CRL authentication refusals")
        require(all(delta[key] == 0 for key in delta if key not in ["attempts", "authenticated", "handshake_failed"]), label + " no unmapped/timeout/quota substitute")
        expected_active = facts["lists"]["crl-epoch1" if label == "free-1" else "crl-epoch2"]["sha256"]
        require(data(server, "crl-active-" + label + ".sha256").decode().split()[0] == expected_active, label + " actual active file bytes")
        for side in ["before", "after"]:
            identity = proc(side + "-" + label)
            process.setdefault(epoch, identity)
            require(process[epoch] == identity, label + side + " same original server generation")
        for suffix in ["txt", "presence"]:
            require(data(client, "crl-client-before-" + label + "." + suffix) == data(client, "crl-client-after-" + label + "." + suffix), label + " unchanged original client " + suffix)
        if data(client, "crl-client-before-" + label + ".presence").strip() == b"present":
            require(data(client, "crl-client-before-" + label + ".json") == data(client, "crl-client-after-" + label + ".json"), label + " unchanged original client journal")
        if args.backend in ["native", "usbfs"]:
            prefix = "server-native-auth-" if args.backend == "native" else "auth-physical-"
            require(data(server, prefix + "crl-before-" + label + ".json") == data(server, prefix + "crl-after-" + label + ".json"), label + " original root records unchanged")
            require(data(server, "host-auth-crl-before-" + label + ".json") == data(server, "host-auth-crl-after-" + label + ".json"), label + " full original driver baseline unchanged")
        else:
            require(obj(server, "auth-crl-before-" + label + ".json") == obj(server, "auth-crl-after-" + label + ".json"), label + " original virtual lease/counters unchanged")
        for name in ["blocked", "rotated", "server", "bad-signature", "expired", "future", "malformed", "overlap", "empty", "null", "positive"]:
            expected = 0 if name == "positive" or name == "rotated" and epoch == 1 else 1
            require(data(client, "crl-" + label + "-" + name + ".wait").strip() == str(expected).encode(), label + name + " actual wait")
            body = data(client, "crl-" + label + "-" + name + ".stdout")
            config = obj(client, "crl-" + label + "-" + name + ".config.json")
            require(config["broker_uid"] == 2000, label + name + " fixed loader UID")
            if expected:
                require(body == b"" and bool(data(client, "crl-" + label + "-" + name + ".stderr")), label + name + " actual refusal/no authorized output")
            else:
                rows = json.loads(body)
                require(type(rows) is list and len(rows) == (0 if label.startswith("held") else 2), label + name + " complete actual catalog")
                require({r["device_key"] for r in rows} == (set() if label.startswith("held") else set(policies[0]["devices"])), label + name + " exact visible keys")
            case_rows.append({"batch": label, "case": name, "actual_exit": expected})
    require(process[1] != process[2], "actual server replacement after original wait/cleanup")
    return {"original_authentication": inherited, "crl_checks": checks, "crl_client_waits": case_rows,
            "scope": "four added CRL barriers in original raw-v1 paired fixture; counters and actual old owner/file-replacement/restart are separate from USB cleanup",
            "limitations": ["new fast negative CLI children have no separate live executed-ELF sample", "no hot active-session revocation, online retrieval, persistent CRL-number anti-rollback, hardware or complete G3"]}
