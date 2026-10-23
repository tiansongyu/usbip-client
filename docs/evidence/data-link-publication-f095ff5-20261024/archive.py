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


archive('data-link-publication-f095ff5-20261024', [('/tmp/rustusb-data-link-publication-f095ff5-20261024','publication')], [('/tmp/verify-rustusb-data-link-live-site-20261024.py','verify-live.py')], [], dict(result='PASS',content_commit='f095ff51b35134c3209d49d21b77ce695c9554b0',software_ci_run=40032354147,pages_run=40032354233,notes=['Official push-triggered workflow status is retained separately from detailed626-execution CI531 logs.','Six live HTTPS resource bytes exactly match the content Git blobs.','Browser actual desktop/mobile/keyboard/URL/image/console checks are DOM/layout observations, not screenshots.','Original165 software inputs map exactly to content blobs, without relabeling the original dirty run.','Local preview tab and exact owned servers are cleaned up; live public tab retained.','No additional USB cases, QEMU runs, hardware or whole Phase3 acceptance is inferred.']))
