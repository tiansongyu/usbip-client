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

archive('prepare-recording-harness-saved-samples-20261024',[
('/tmp/rustusb-prepare-saved-proof-binding-20261024','author'),
('/tmp/rustusb-prepare-saved-proof-binding-independent-review-20261024','review'),
], [('/tmp/review-rustusb-prepare-saved-proof-binding-20261024.py','review/review.py')],[],dict(result='PASS_STATIC',harness_commit='1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16',author_diagnostics=60,independent_method_diagnostics=21,
notes=['Only explicit saved-file proof arguments and one-argument guard changed; strict verifiers, timeouts, C gate and dispatchers retain exact bytes.',
'Actual Native222 and USBFS68 saved/live divergence fixtures exercise original and revised shell functions, not USB runtime.',
'Original222 FAIL remains separate with22 unrun; new clean1582 complete matrix is a separate execution.',
'Author overlap is explicit; diagnostic counts are not USB cases.']))
