from pathlib import Path
import json, hashlib

repo = Path('/home/ubuntu/github/usbip-client')
def archive(name, trees, singles, listed, scope):
    out = repo / 'docs/evidence' / name
    out.mkdir(exist_ok=False)
    records = []
    seen = set()
    def copy(source, dest, expected=None):
        source = Path(source)
        assert source.is_file() and not source.is_symlink(), source
        b = source.read_bytes()
        digest = hashlib.sha256(b).hexdigest()
        if expected:
            assert len(b) == expected['bytes'] and digest == expected['sha256'], source
        assert dest not in seen, dest
        seen.add(dest)
        p = out / dest
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
        assert p.read_bytes() == b
        records.append(dict(path=dest, original=str(source), bytes=len(b), sha256=digest))
    for root, prefix in trees:
        root = Path(root)
        for p in sorted(root.rglob('*')):
            if p.is_file():
                copy(p, prefix + '/' + str(p.relative_to(root)))
    for source, dest in singles:
        copy(source, dest)
    for handoff, key, prefix in listed:
        for row in json.loads(Path(handoff).read_text())[key]:
            copy(row['path'], prefix + '/' + str(Path(row['path']).relative_to('/tmp')), row)
    copy(__file__, 'archive.py')
    b = (json.dumps(scope, indent=2) + '\n').encode()
    (out/'scope.json').write_bytes(b)
    records.append(dict(path='scope.json', original='generated archive scope', bytes=len(b), sha256=hashlib.sha256(b).hexdigest()))
    (out/'manifest.json').write_text(json.dumps(dict(schema_version=1, files=records), indent=2)+'\n')
    print(name, 'files',len(records)+1,'bytes',sum(r['bytes'] for r in records))


archive('server-security-observations-c097cf8-20261024', [
('/tmp/rustusb-security-observations-software-first-20261024','software'),
('/tmp/rustusb-security-observations-source-review-20261024','source-review'),
('/tmp/rustusb-security-observations-signals-c097cf8-20261024','signals'),
('/tmp/rustusb-security-observations-backpressure-c097cf8-20261024','backpressure'),
('/tmp/rustusb-security-observations-matrix-c097cf8-20261024','orchestration'),
('/tmp/rustusb-security-observations-review-c097cf8-20261024','raw-review'),
('/tmp/rustusb-security-observations-native-ipv4-first-c097cf8-20261024','runs/native-ipv4'),
('/tmp/rustusb-security-observations-native-ipv6-first-c097cf8-20261024','runs/native-ipv6'),
('/tmp/rustusb-security-observations-virtual-ipv4-first-c097cf8-20261024','runs/virtual-ipv4'),
('/tmp/rustusb-security-observations-virtual-ipv6-first-c097cf8-20261024','runs/virtual-ipv6'),
('/tmp/rustusb-security-observations-usbfs-ipv4-first-c097cf8-20261024','runs/usbfs-ipv4'),
('/tmp/rustusb-security-observations-usbfs-ipv6-first-c097cf8-20261024','runs/usbfs-ipv6'),
('/tmp/rustusb-security-observations-v2-diagnostic-regression-c097cf8-20261024','runs/v2-diagnostic-regression'),
], [
('/tmp/run-rustusb-security-observations-software-20261024.py','methods/software.py'),
('/tmp/run-rustusb-security-observations-matrix-c097cf8-20261024.py','methods/matrix.py'),
('/tmp/review-rustusb-security-observations-c097cf8-20261024.py','methods/raw-review.py'),
], [], dict(scope='Clean c097cf8:808 software executions,194 software inputs; six raw-v1 authentication/counter pairs and separate Native-v4/v2 diagnostic regression,14 guest exits0. Four signal and two backpressure cases use prebuilt new CLI. All original source/ELF/HEAD/clean guards passed. Same-author raw/provenance check is not independent security audit or new cases. Private keys and ELF bodies excluded. Original historical FAILs retain previous archives; early dirty development failures only have work-log/tool records. Hardware G1/G2, full G3, installed managed counter/v2 hostile counts and persistent security audit remain incomplete.'))
