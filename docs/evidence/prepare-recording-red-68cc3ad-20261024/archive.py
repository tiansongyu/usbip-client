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


archive('prepare-recording-red-68cc3ad-20261024', [('/tmp/rustusb-prepare-stop-native-resource-job-68cc3ad-20261024', 'prepare-stop-native-resource-job-68cc3ad-20261024'), ('/tmp/rustusb-prepare-stop-usbfs-resource-job-68cc3ad-20261024', 'prepare-stop-usbfs-resource-job-68cc3ad-20261024'), ('/tmp/rustusb-prepare-stop-old-qemu-review-20261024', 'prepare-stop-old-qemu-review-20261024'), ('/tmp/rustusb-prepare-native-red-raw-review-20261024', 'prepare-native-red-raw-review-20261024'), ('/tmp/rustusb-prepare-harness-static-20261024', 'prepare-harness-static-20261024'), ('/tmp/rustusb-prepare-harness-static-revision2-20261024', 'prepare-harness-static-revision2-20261024'), ('/tmp/rustusb-prepare-harness-static-revision3-20261024', 'prepare-harness-static-revision3-20261024'), ('/tmp/rustusb-prepare-harness-static-revision4-20261024', 'prepare-harness-static-revision4-20261024'), ('/tmp/rustusb-prepare-harness-revision2-final-fixtures-20261024', 'prepare-harness-revision2-final-fixtures-20261024'), ('/tmp/rustusb-prepare-harness-revision2-fixtures-20261024', 'prepare-harness-revision2-fixtures-20261024'), ('/tmp/rustusb-prepare-harness-revision2-originals-20261024', 'prepare-harness-revision2-originals-20261024')], [('/tmp/rustusb-prepare-harness-author-handoff-20261024.json', 'review/rustusb-prepare-harness-author-handoff-20261024.json'), ('/tmp/rustusb-prepare-harness-author-handoff-revision2-20261024.json', 'review/rustusb-prepare-harness-author-handoff-revision2-20261024.json'), ('/tmp/rustusb-prepare-harness-root-doc-delta-20261024.json', 'review/rustusb-prepare-harness-root-doc-delta-20261024.json'), ('/tmp/rustusb-prepare-harness-readonly-review-20261024.json', 'review/rustusb-prepare-harness-readonly-review-20261024.json'), ('/tmp/check-prepare-static.py', 'review/check-prepare-static.py'), ('/tmp/check-prepare-static-revision4.py', 'review/check-prepare-static-revision4.py'), ('/tmp/check-prepare-mapping-and-arm-final-20261024.py', 'review/check-prepare-mapping-and-arm-final-20261024.py')], [], dict(result='FAIL',commit='68cc3adf87abf43fcedd2b227c6ad20a5bae4370',clean=True,runs=2,guest_exit_zero=4,cases=['native-resource/job','usbfs-resource/job'],cli_sha256='bb6fb276c0cbfbb73c2cdb8af0f73d87045ac7235b4943cf096c0643d570814a',helper_sha256='b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5',notes=['Both original-product failures are retained, not expected-pass conversions. Product Rust inputs equal e10b5fb individually; new guest-only harness is separate.','Actual secondResource file-fsync ENTRY stays held; two Native exports or two USBFS characterFDs/records remain owned with no exact root terminal during the held window.','The selected syscall resumes and returns0; old original capabilities then receive exact root proofs and the full original driver baseline returns.','A later fresh epoch actually transfers KEY1 down/up and35bytes each FTDI direction. Reuse does not turn the held-phase failure into PASS.','Native161 andUSBFS175 harness checks each have exactly3 failing required verdicts; zero guest exit codes alone are not success.','Independent-method reconciliation checks1395base64 originals,1795raw SHA originals and two174source sets against Git68; reviewer participation is disclosed.','Additional author raw review uses another method; it does not create another QEMU run or independent implementation.','StopJob CLI actually times out at5s/exit1; its later actor_uid1000 audit is separate from acceptance/release proof. No held-window TERM occurs.','Direct final VHCI NULL is captured; old-node Gone is supported by exact frozen-helper NativeImportReleased proof, not an independent final sysfsstat. Initial overbroad reviewer assertion is retained with correction.','Static parser/empty-jq pitfalls and fixes remain static provenance, not product/QEMU failures.','No preparation-stage USBFS interface claim, original siblingI/O before listener, kernel-internal hard-block, hardware G1G2 or fullPhase3 claim.']))
