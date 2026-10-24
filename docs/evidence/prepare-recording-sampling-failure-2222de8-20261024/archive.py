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

archive('prepare-recording-sampling-failure-2222de8-20261024',[
('/tmp/rustusb-prepare-stop-final-2222de8-20261024','runs'),
('/tmp/rustusb-prepare-stop-222-raw-review-20261024','review/raw'),
],[(str(Path('/tmp')/n),'review/'+n) for n in ['run-rustusb-prepare-stop-final-matrix-20261024.py']],[],dict(result='FAIL',commit='2222de87439f61e8a2e939e7dc15cd845d38aa4e',runs_executed=1,remaining_unrun=22,guest_exit_zero=2,
notes=['First saved observation root was still active, but proof boolean was computed from later live root journal. Strict verifier correctly rejects inconsistency.',
'Second saved observation proves exact two retirements and complete baseline while original syscall remains held, without converting original FAIL to PASS.',
'Original syscall released and returns0, old owners joined, fresh actual HID and35B serial each direction complete.',
'420 original bytes/SHA and881 author diagnostic checks reconciled separately; no independent implementation claim.',
'No skipped case is marked executed, no product or timeout relaxation is implied.']))
