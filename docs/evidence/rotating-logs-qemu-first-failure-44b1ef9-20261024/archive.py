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


archive('rotating-logs-qemu-first-failure-44b1ef9-20261024', [
 ('/tmp/rustusb-process-logging-qemu-first-coordinator-20261024','actual-run'),
 ('/tmp/rustusb-process-logging-qemu-author-20261024','source-author/first'),
 ('/tmp/rustusb-process-logging-qemu-author-revision2-20261024','source-author/startup-snapshot-lock'),
 ('/tmp/rustusb-process-logging-qemu-static-20261024','static/runner'),
 ('/tmp/rustusb-process-logging-verifier-author-20261024','source-author/verifier-first'),
 ('/tmp/rustusb-process-logging-verifier-static-20261024','static/verifier'),
 ('/tmp/rustusb-process-logging-qemu-eof-whitespace-20261024','source-author/eof-whitespace'),
 ('/tmp/rustusb-process-logging-qemu-first-raw-method-20261024','method/lifecycle'),
 ('/tmp/rustusb-process-logging-qemu-first-log-review-20261024','method/logging'),
 ('/tmp/rustusb-process-logging-peer-close-source-review-20261024','method/peer-close-source'),
], [(str(Path('/tmp')/n),'review/'+n) for n in [
 'run-rustusb-process-logging-qemu-first-20261024.py',
 'rustusb-process-logging-qemu-source-cross-review-20261024.json',
 'review-rustusb-process-logging-first-20261024.py',
]], [], dict(result='FAIL',commit='44b1ef96be4524b43e9f914214fb9f3166519365',clean=True,
 paired_runs=1,epochs_in_same_run=3,guest_exit_zero=2,host_exit_code=1,
 selected_harness_inputs=189,matching_software_inputs=177,
 cli_sha256='6cc828f3769d1ebd4c2e7b566707b7788f107151bc21155843c315a818b8b474',
 helper_sha256='bb0ea543bf6cc1af840c4e793b26906a6f9062b534c00b0c6549dce3fce834a1',
 notes=['The actual host verifier first rejected a normal server incoming operation_error State despite independently verified cleanup. Full original result remains FAIL even though both guests completed and exited0.',
 'Four normal/fresh original incoming IDs retain State and protocol end, matched client local_stop. Source Failure mapping and earlier clean1582 observations support a narrow expectation correction, not clean EOF, traced reset causality or product cleanup failure.',
 'H independently checked actual3 key pairs/35bytes each direction per epoch, original server12/client6 root terminals, full baseline/VHCI NULL/generations and original lock owner, EX/SH and nonce cleanup-before-release ordering.',
 'N independently checked logging/schema/bounds/UID/ELF/committed mirrors/7pages76filtered records. Blocked server actualStopped accepted=written281/dropped289/unhealthy and wait1 with LossRecords; other7 owners healthy drain.',
 '746 QEMU original artifacts and529 base64 blocks verified without modification; coordinator adds its own distinct files. Method diagnostic counts383/3167/24 are not software or USB test counts.',
 'Author/preflight records include first static-parser error, startup-copy race fix, original44 metadata fixtures and later EOF whitespace fix with identical AST. None is relabelled actual QEMU PASS.',
 'Reviewers authored portions of implementation/harness: independent methods are not independent implementation audits.',
 'New verifier must be committed and run in a fresh clean directory. No rerun of a revised verifier over these originals changes this original verdict.',
 'No host USB, physical hardware G1/G2, permanent filesystem cancellation, kernel-active or complete Phase3 acceptance.']))

