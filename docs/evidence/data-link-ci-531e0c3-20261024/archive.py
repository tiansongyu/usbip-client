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

archive('data-link-final-531e0c3-20261024', [
 ('/tmp/rustusb-data-link-ready-531e0c3-20261024','runs'),
 ('/tmp/rustusb-data-link-network-method-review-final-20261024','review/network-final'),
 ('/tmp/rustusb-data-link-final-lifecycle-review-20261024','review/lifecycle'),
], [(str(Path('/tmp')/n),'review/'+n) for n in [
 'run-rustusb-data-link-ready-matrix-20261024.py',
 'rustusb-data-link-ready-root-20261024.json',
 'rustusb-data-link-network-method-review-handoff-20261024.json',
 'rustusb-data-link-network-ready-handoff-20261024.json',
 'rustusb-data-link-network-ready-source-review-20261024.json',
 'rustusb-data-link-backend-dispatch-handoff-20261024.json',
]], [
 ('/tmp/rustusb-data-link-network-method-review-handoff-20261024.json','preserved_review_attempts','review/parser-attempts'),
 ('/tmp/rustusb-data-link-network-ready-handoff-20261024.json','static_fixture_originals','review/readiness-originals'),
 ('/tmp/rustusb-data-link-backend-dispatch-handoff-20261024.json','evidence','review/backend-originals'),
], dict(result='PASS',commit='531e0c32a4b0e7252ca771ab4f10042c71c8c195',clean=True,
 runs=15,new_framed_v2_runs=6,legacy_raw_v1_runs=9,directional_fault_phases=18,guest_exit_zero=30,
 cli_sha256='bb6fb276c0cbfbb73c2cdb8af0f73d87045ac7235b4943cf096c0643d570814a',
 helper_sha256='b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5',
 notes=['Every copied original retains exact bytes and SHA-256; scope is generated metadata.',
 'Network/lifecycle reviewers also authored portions of the harness/product; independent methods are not independent implementations.',
 'Predicate totals, packet counts, source entries and timeout observations are not additional USB test cases.',
 'Native/USBFS idle-connected evidence is not pending-URB proof; Virtual HID is enumeration/idle without key injection.',
 'Some final server-side link ends are protocol while resource cleanup is independently verified.',
 'No real-device G1/G2, complete Phase 3, performance, ISO or Windows USB acceptance is inferred.',
 'Earlier failed runs retain their separate immutable archives.']))

archive('data-link-ci-531e0c3-20261024', [('/tmp/rustusb-data-link-ci-531e0c3-20261024','official')],
 [(str(Path('/tmp')/n),'review/'+n) for n in [
 'rustusb-data-link-ci-531e0c3-independent-review-20261024.json',
 'rustusb-data-link-ci-531e0c3-artifact-manifest-20261024.json',
 'review-rustusb-data-link-ci-531e0c3-20261024.py']], [],
 dict(result='PASS',commit='531e0c32a4b0e7252ca771ab4f10042c71c8c195',run_id=40029976284,attempt=1,
 jobs=[120675534150,120675534276,120675534288,120675534313],executions_per_job=626,result_rows_per_job=39,
 default=260,all_features=346,vendor=20,qualified_observed_names=366,
 notes=['Four actual native Ubuntu 22.04/24.04 x86_64/aarch64 successful jobs.',
 'Full official connector responses and matching decoded logs are retained.',
 'No uploaded workflow artifacts; missing runner temporary originals are not reconstructed.',
 'Five TLS scenarios lack per-child ELF/full before/stopped originals; four signals have actual ELF receipts; two backpressure scenarios have workflow-build provenance.',
 'Diagnostic review predicates and source inventory entries are not USB test cases.']))
