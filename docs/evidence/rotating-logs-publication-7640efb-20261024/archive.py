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


archive('rotating-logs-publication-7640efb-20261024', [
 ('/tmp/rustusb-logging-publication-7640efb-20261024','publication'),
 ('/tmp/rustusb-logging-publication-7640efb-method-review-20261024','method-review'),
], [(str(Path('/tmp')/n),'review/'+n) for n in [
 'verify-rustusb-logging-live-7640efb-20261024.py',
 'review-rustusb-logging-publication-7640efb-20261024.py',
]], [], dict(result='PASS',content_commit='7640efbbe2f57c2a791b38da339695976e80205d',
 software_run=40056501727,pages_run=40056501893,
 software_jobs=[120760539311,120760539410,120760539417,120760539489],
 pages_jobs=[120760539854,120760602005],canonical_live_resources=6,all_live_equal_Git_blobs=True,
 notes=['Normal push to requested SSH remote, actual remote main equals content head and clean-tree receipt retained;177 selected software inputs equal saved723/current/Git bytes.',
 'All four actual native software jobs and Pagescheck/deploy completed/success at exactcontenthead; status-only verification does not recount723 or relabel89 detailedCI/48cQEMU.',
 'Canonical HTTPS200 response bytes/headers/time match all six content Git blobs. Actuallive CUA desktop1280x720/mobile390x844, keyboardfocus/URL and zero consolewarn/error transcribed separately from rawHTTP.',
 'Initial favicon/viewport image observations were pending and later loaded; no fabricated instant state. Screenshot attempt actuallyfailed. No current pixel/noJS/clipboard/reducedmotion emulation claim.',
 'Defaultviewport actualreset1280x720 and publictab8 deliverable retained; temporarypreview tab/server were alreadyclosed in localQA archive.',
 'Independent receipt-method211 diagnostics not USBtests, reviewer authored record/worker and limitedharnessfix: not independentimplementation audit.',
 'Initial208 diagnostic report with inprogress statuses and final wrongfilename-parameter FileNotFoundError retained; only actual final metadata supports success.',
 'No absent runnerTMP/executable/secret/wire originals invented, first44b1/41fce failures and originaldirtysoftware/old23 provenance retained.',
 'The next receipt-only commit leaves product177 and all six website source blobs unchanged. FullPhase0–7/hardware/fullG3 remain active unfinished work.']))

