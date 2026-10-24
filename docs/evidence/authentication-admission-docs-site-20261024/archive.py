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


archive('authentication-admission-docs-site-20261024', [('/tmp/rustusb-authentication-admission-docs-site-20261024', 'qa')], [('/tmp/update-rustusb-authentication-admission-docs-20261024.py', 'methods/update-rustusb-authentication-admission-docs-20261024.py'), ('/tmp/preview-rustusb-authentication-admission-20261024.py', 'methods/preview-rustusb-authentication-admission-20261024.py'), ('/tmp/preview-rustusb-authentication-admission-second-20261024.py', 'methods/preview-rustusb-authentication-admission-second-20261024.py')], [], {'kind': 'documentation_site_static_http_and_actual_cua_observations', 'maintained_sources': 33, 'local_links': 563, 'external_links_not_all_fetched': 63, 'site_checks': 177, 'screenshot': 'unavailable_no_pixel_claim', 'runtime_assets': 'five unchanged from30481f0', 'global_skill': 'four files unchanged by this delivery', 'publication': 'pending_actual_push'})
