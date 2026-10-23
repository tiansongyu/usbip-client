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


archive('data-link-docs-site-20261024', [('/tmp/rustusb-data-link-docs-site-review-20261024','qa'),('/tmp/rustusb-data-link-publication-20261024','first-push')], [(str(Path('/tmp')/n),'review/'+n) for n in ['rustusb-data-link-final-doc-link-check-20261024.py','rustusb-data-link-final-doc-statements-review-20261024.json','rustusb-data-link-final-doc-links-review-20261024.json','rustusb-website-web-guidelines-review-20261024.json','rustusb-website-narrow-fix-handoff-20261024.json','rustusb-website-copy-feedback-readonly-review-20261024.json','rustusb-data-link-preview-server-20261024.py','rustusb-data-link-preview-policy-server-20261024.py']], [], dict(result='PASS',publication='Local source QA only; live updated content is verified separately after push.',notes=['142 static checks/351 local links/14 source docs; no table errors.','Actual local CUA desktop/mobile/deep URL/history/keyboard/no-JS/normal clipboard and policy-denied fallback observations are preserved.','Screenshot capture is unavailable; reduced-motion CSSOM is inspected without preference emulation.','Existing global readme-site four sources are retained and discovery verified.','Initial public push is exact531; its Pages success concerns the earlier page, not this updated local page.']))
