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


archive('rotating-logs-docs-site-20261024', [
 ('/tmp/rustusb-rotating-logs-docs-site-qa-20261024','qa'),
 ('/tmp/rustusb-rotating-logs-guide-skill-review-20261024','method/guide-skill'),
 ('/tmp/rustusb-rotating-logs-final-docs-review-20261024','method/final-docs'),
], [('/tmp/rustusb-rotating-logs-doc-links-first-20261024.py','review/doc-link-check.py')], [],
 dict(result='PASS_LOCAL_QA_PUBLICATION_PENDING',changed_content_scope='English and Chinese README, project website, operator guide and evidence/maintenance documentation',
 local_doc_sources=19,local_links=437,external_not_fetched=49,static_site_checks=153,
 notes=['Readme-site global4source files and Codex discovery symlink unchanged from their original published snapshot, fixed rstop reference f2abbaac retained; not reinstalled.',
 'Actual CUA project-prefix desktop1280x800/mobile390x844 show correct723/6/4 plus separate focused48c and historical1582/23, no horizontal page overflow, loaded responsive SVG and keyboard selection/focus/URL, warn/error console empty.',
 'Initial mobile resource observation retained desktop SVG; settled actual observation shows mobile SVG. Both retained, not an instant-layout claim.',
 'Five CSS/JS/assets byte-identical toac4; old noJS/clipboard/reduced-motion evidence keeps its historical scope. No current pixel screenshot or preference-emulation claim.',
 'Browser records and static command results are explicitly structured transcriptions of actual tools, not fabricated saved raw subprocess or screenshot artifacts.',
 'Exact19checked source bytes match the link-checker source hashes; later content commit can map them to Git without relabelling the earlier dirty checks.',
 'Temporary preview tab closed, viewport actualnextread1280x720; exact owned preview2934166 CTRL-C exit0 and/proc absent confirmed.',
 '15-document independent source-method review preserves initial stale-logs/rotation findings and final corrections; other49 external links not individually fetched.',
 'Live deployment has not yet been claimed; publishing/HTTP byte receipts have a later distinct archive.']))

