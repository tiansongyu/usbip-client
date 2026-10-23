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


archive('prepare-recording-development-20261024', [(str(Path('/tmp')/n),n.removeprefix('rustusb-')) for n in ['rustusb-prepare-stop-red-20261024','rustusb-prepare-stop-root-fixed-20261024','rustusb-prepare-stop-native-red-20261024','rustusb-prepare-stop-rust-client-first-20261024','rustusb-prepare-stop-software-independent-review-20261024','rustusb-prepare-recording-rust-client-author-20261024','rustusb-prepare-gate-first-compile-20261024']], [(str(Path('/tmp')/n),'review/'+n) for n in ['run-rustusb-prepare-stop-red-20261024.py','run-rustusb-prepare-stop-root-fixed-20261024.py','run-rustusb-prepare-stop-native-red-20261024.py','run-rustusb-prepare-stop-rust-client-first-20261024.py','rustusb-prepare-stop-upstream-20261024.json','rustusb-prepare-ack-stall-design-20261024.md','rustusb-prepare-ack-stall-design-20261024.json','rustusb-accepted-prepare-stop-design-20261024.md','rustusb-accepted-prepare-stop-design-20261024.json','rustusb-prepare-stop-sticky-source-review-20261024.json']], [], dict(status='DEVELOPMENT_PARTIAL',baseline='c2464ed',runs=[dict(name='root-shutdown-red',actual='dirty c2464ed',passed=1,failed=3,inputs=165),dict(name='root-shutdown-fixed',actual='dirty c2464ed',passed=4,failed=0,inputs=165),dict(name='native-resource-red',actual='dirty c2464ed',passed=0,failed=1,inputs=165),dict(name='rust-client-first',actual='dirty c2464ed',passed=28,failed=0,filtered=29,inputs=166)],notes=['Four input sets remain stable;661 comparisons are not independent USB cases.','Actual logs distinguish assertion failures after compilation from compiler failures.','Three shutdown RED tests retain their assertions after formatting-only changes; the original Native Resource test is a real private production sequencing phase.','Store mutex/owned Unix/memory-DER TCP mTLS/virtual lease fixtures do not prove actual helper, fsync or kernel cleanup.','C1 sender-loss revision and root poison regression are source-only in this archive, not included in the first28/fourPASS.','Shared target lacks then-captured per-run ELF receipts; current target bytes are not substituted.','Guest-only C gate compiles with-Werror; it has not run on the host or in QEMU here.','Reviewer participated in service implementation; independent method is not independent authorship.','No complete software gate, QEMU, hardware or fullPhase3 acceptance is claimed.']))
