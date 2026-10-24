"""Read captured files only; admission observations do not replace original USB proof."""
import json
import re

def verify(evidence, args, base, authentication_checker):
    original = authentication_checker.verify(evidence, args, base)
    server = evidence / "server-artifacts"
    client = evidence / "client-artifacts"
    checks = []
    def require(condition, label):
        if not condition:
            raise RuntimeError("admission evidence: " + label)
        checks.append(label)
    def obj(root, name):
        return json.loads((root / name).read_bytes())
    def raw(root, name):
        return (root / name).read_bytes()
    def integer(root, name):
        value = raw(root, name).strip()
        require(re.fullmatch(rb"[0-9]{1,12}", value) is not None, name + " bounded actual integer")
        return int(value)
    def process(label):
        status = raw(client, f"auth-{label}.status").decode()
        uid = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", status, re.M)
        require(uid is not None and [int(v) for v in uid.groups()] == [2000]*4, label + " actual UID2000")
        stat = raw(client, f"auth-{label}.stat").decode()
        pid, _, tail = stat.partition(" (")
        fields = tail.rsplit(") ", 1)[1].split()
        return (int(pid), int(fields[19]), raw(client, f"auth-{label}.exe").split()[0].decode())
    def admission(name):
        sample = obj(server, name)
        require(set(sample) == {"scope", "policy", "admitted", "limited", "saturated", "consistent"}, name + " closed admission DTO")
        require(sample["scope"] == "loaded_tls_server_configuration" and sample["policy"] == {"burst":32,"interval_millis":500}, name + " actual custom policy and scope")
        require(sample["consistent"] is True and sample["saturated"] is False, name + " coherent counters")
        require(all(type(sample[key]) is int and 0 <= sample[key] <= 2**64-1 for key in ["admitted", "limited"]), name + " exact bounded totals")
        return sample

    logs = {role:(evidence / f"{role}-evidence.log").read_text() for role in ["server","client"]}
    controls = [json.loads(line) for line in (evidence / "host-control.jsonl").read_text().splitlines()]
    by_mark = {row["mark"]:row for row in controls}
    busybox=obj(evidence,"client-admission-payload-provenance.json")["bin/busybox"]
    require(len(by_mark)==len(controls), "host sends each exact command once")
    facts=[]
    for cycle in [1,2]:
        before=admission(f"rate-admission-before-{cycle}.json")
        limited=admission(f"rate-admission-limited-{cycle}.json")
        during=admission(f"rate-admission-after-io-{cycle}.json")
        drained=admission(f"rate-admission-drained-{cycle}.json")
        recovered=admission(f"rate-admission-recovered-{cycle}.json")
        require(before["limited"]==0, f"epoch{cycle} original refusal fixture did not exhaust quota")
        require(before["limited"] < limited["limited"] < during["limited"] <= drained["limited"], f"epoch{cycle} actual refusal progress through I/O")
        require(recovered["limited"]==drained["limited"] and recovered["admitted"]==drained["admitted"]+1, f"epoch{cycle} one authorized admission after actual probe drain")
        require(integer(client,f"rate-recovery-{cycle}.wait")==0 and obj(client,f"rate-recovery-{cycle}.json")==[], f"epoch{cycle} real discovery completed with both devices still busy")
        require(integer(client,f"rate-workers-alive-after-io-{cycle}.txt") in range(1,9), f"epoch{cycle} actual worker alive after complete original I/O")
        for kind in ["hid","serial"]:
            require(process(f"rate-{kind}-before-{cycle}")==process(f"rate-{kind}-after-io-{cycle}"), f"epoch{cycle} same original {kind} process/generation through I/O")
        require(raw(client,f"rate-client-before-{cycle}.json")==raw(client,f"rate-client-after-{cycle}.json"), f"epoch{cycle} original import ownership unchanged by probes/recovery")
        require(raw(client,f"rate-client-before-{cycle}.txt")==raw(client,f"rate-client-after-{cycle}.txt"), f"epoch{cycle} original VHCI identity unchanged")
        attempts=0
        for worker in range(1,9):
            require(process(f"rate-worker-{cycle}-{worker}")[2]==busybox, f"epoch{cycle} worker{worker} actual executed BusyBox ELF")
            require(integer(client,f"rate-worker-{cycle}-{worker}.wait")==0, f"epoch{cycle} worker{worker} real wait0")
            start=integer(client,f"rate-worker-{cycle}-{worker}.started")
            end=integer(client,f"rate-worker-{cycle}-{worker}.finished")
            require(end>=start+800, f"epoch{cycle} worker{worker} actual eight-second uptime window")
            rows=[]
            for line in raw(client,f"rate-worker-{cycle}-{worker}.timeline").splitlines():
                require(re.fullmatch(rb"[0-9]+ [0-9]+ [0-9]+ [01]",line) is not None, f"epoch{cycle} worker{worker} exact child wait row")
                rows.append([int(x) for x in line.split()])
            require(0<len(rows)<=256 and [r[0] for r in rows]==list(range(1,len(rows)+1)), f"epoch{cycle} worker{worker} bounded complete attempt sequence")
            require(all(start<=r[1]<=r[2]<=end for r in rows) and all(a[2]<=b[1] for a,b in zip(rows,rows[1:])), f"epoch{cycle} worker{worker} monotonic actual child chronology")
            attempts+=len(rows)
        tls_before=obj(server,f"rate-tls-before-{cycle}.json")
        tls_drained=obj(server,f"rate-tls-drained-{cycle}.json")
        tls_recovered=obj(server,f"rate-tls-recovered-{cycle}.json")
        require(tls_before["tls"]["in_flight"]==tls_drained["tls"]["in_flight"]==tls_recovered["tls"]["in_flight"]==0, f"epoch{cycle} actual TLS attempts drained")
        accepted=drained["admitted"]-before["admitted"]
        refused=drained["limited"]-before["limited"]
        quota=tls_drained["tls"]["connection_limit"]-tls_before["tls"]["connection_limit"]
        outer=tls_drained["listener_connection_limit"]-tls_before["listener_connection_limit"]
        require(accepted>0 and refused>0 and attempts==accepted+refused+quota+outer, f"epoch{cycle} all actual client attempts accounted by distinct admission/connection stages")
        require(tls_drained["tls"]["attempts"]-tls_before["tls"]["attempts"]==accepted+quota, f"epoch{cycle} rate refusals excluded from unchanged TLS DTO")
        require(tls_drained["tls"]["unmapped_certificate"]-tls_before["tls"]["unmapped_certificate"]==accepted, f"epoch{cycle} admitted probes reached actual unmapped-leaf rejection")
        require(tls_recovered["tls"]["authenticated"]==tls_drained["tls"]["authenticated"]+1 and tls_recovered["tls"]["attempts"]==tls_drained["tls"]["attempts"]+1, f"epoch{cycle} actual new local TLS authentication after refill")
        marks=[f"rate-begin-{cycle}",f"rate-go-{cycle}",f"rate-running-{cycle}",f"auth-input-ready-{cycle}",f"io-{cycle}",f"rate-done-{cycle}",f"rate-recover-{cycle}",f"rate-recovered-{cycle}",f"rate-checked-{cycle}",f"close-{cycle}"]
        require(all(mark in by_mark for mark in marks) and [by_mark[m]["monotonic"] for m in marks]==sorted(by_mark[m]["monotonic"] for m in marks), f"epoch{cycle} actual phase commands preserve input/I/O/drain/recovery-before-close chronology")
        require(all(f"{marker} cycle={cycle}" in logs[role] for role,marker in [("server","RATE_SERVER_LIMITED"),("client","RATE_CLIENT_IO_ACTIVE"),("server","RATE_SERVER_DRAINED"),("client","RATE_CLIENT_RECOVERED"),("server","RATE_SERVER_CHECKED")]), f"epoch{cycle} actual endpoint barriers present")
        facts.append({"epoch":cycle,"actual_openssl_wait_rows":attempts,"admitted":accepted,"rate_refused":refused,"tls_budget_refused":quota,"outer_budget_refused":outer})
    return {"original_authentication":original,"checks":checks,"epochs":facts,
            "scope":"Two original credential epochs with actual worker waits/refusals/live I/O/refill and original complete USB/root cleanup. Predicates/wait rows are not USB or software cases.",
            "limits":["raw-v1 only","fast OpenSSL children lack per-executed-ELF samples","no per-source fairness/DoS/benchmark/hardware/fullG3 claim","mixed PCAP retained without new ciphertext classifier verdict"]}
