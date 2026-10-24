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


archive('authentication-admission-publication-5845b14-20261024', [('/tmp/rustusb-authentication-admission-publication-5845b14-20261024', 'publication')], [('/tmp/check-rustusb-authentication-admission-live-20261024.py', 'methods/live-http.py')], [], {'kind': 'actual_normal_push_final_primary_ci_pages_and_live_byte_receipts', 'content_head': '5845b14ba41b6d112cf264845e5beecbb7533ef3', 'ci_run': 40084212856, 'pages_run': 40084212885, 'ci_platforms': 4, 'live_resources': 6, 'notes': ['Primary API status/step receipts only; no full CI logs or CI ELF recounted.', 'Local824 belongs clean47f; 13 paired QEMU belongs clean d619.', 'Initial live mobile image sample false after resize retained, then actual settled responsive image sample true; no screenshot/pixel claim.', 'Later documentation receipt commit does not change these product/site Git blobs.']})
