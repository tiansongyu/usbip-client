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

p=Path('/tmp/rustusb-prepare-stop-publication-ac4e802-20261024')
r=json.loads((p/'software-final.response.json').read_text());d=json.loads(r['structuredContent']['content']);assert d['head_sha']=='ac4e802848257e1a36767137e2e56bef4dda6683' and d['status']=='completed' and d['conclusion']=='success'
j=json.loads((p/'software-jobs-final.response.json').read_text())['structuredContent']['jobs'];assert len(j)==4 and all(x['status']=='completed' and x['conclusion']=='success' for x in j)
archive('prepare-recording-publication-ac4e802-20261024',[(str(p),'publication'),('/tmp/rustusb-prepare-stop-publication-initial-independent-review-20261024','review/initial'),('/tmp/rustusb-prepare-stop-publication-final-independent-review-20261024','review/final')],[(str(Path('/tmp')/n),'review/'+n) for n in [
'verify-rustusb-prepare-stop-live-site-20261024.py','review-rustusb-prepare-stop-publication-ac4e802-20261024.py','finalize-rustusb-prepare-publication-docs-20261024.py','verify-rustusb-staged-archives-20261024.py','review-rustusb-prepare-stop-publication-final-20261024.py',
]],[],dict(result='PASS',content_commit='ac4e802848257e1a36767137e2e56bef4dda6683',software_workflow=40045915202,pages_workflow=40045915200,
notes=['Exact contentcommit four native software jobs successful and Pagescheck/deploysuccessful; this is a status check, detailed667 logs remain5ff.',
'Software selected167 inputs individually match contentGitblobs. Actual software run remainsdirtyb1, QEMUclean1582, detailedCI5ff; no relabeling.',
'Six live HTTPS200 resources exactly equal actualac4 Gitblobs.',
'Actual CUA publicpage desktop1280x720/mobile390x844 counts667/23/46/18, loaded local diagrams, keyboardURL and console0 observed. Viewport reset1280x720, publictab marked deliverable.',
'No screenshot/pixel, new noJS/clipboard/reducedmotion emulation claims. Local previews alreadyclosed.',
'Later receipt-only commit does not change product167 or website sixfiles.']))
