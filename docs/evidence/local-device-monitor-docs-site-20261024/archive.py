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


archive('local-device-monitor-docs-site-20261024', [('/tmp/rustusb-hotplug-docs-site-20261024','qa')], [('/tmp/capture-rustusb-hotplug-docs-qa-20261024.py','methods/capture.py'),('/tmp/capture-rustusb-hotplug-local-http-20261024.py','methods/http.py'),('/tmp/preview-rustusb-hotplug-20261024.py','methods/preview.py')], [], dict(kind='local_documentation_and_browser_DOM_checks_not_publication',software_commit='c0346d61d01b6a5f1605f55a6591d87fedd0d615',qemu_commit='f66da6bd64b960aa54a2878ffab0bbe232e8c85d',notes=['Screenshot API unavailable, no pixel review','Actual CSP no-script preview keeps all commands and panels readable','Six local HTTP resources equal maintained source bytes','Initial mobile desktop SVG and later mobile SVG both retained','Five runtime assets and four global readme-site sources unchanged','New process method source equals actually executed final local method','Owned preview actual wait0, PID absent, temporary tabs closed, viewport reset observed']))
