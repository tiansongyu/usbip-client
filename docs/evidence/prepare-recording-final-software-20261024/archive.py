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

archive('prepare-recording-final-software-20261024', [
('/tmp/rustusb-prepare-stop-software-ab-first-20261024','software'),
('/tmp/rustusb-prepare-stop-local-runtime-ab-20261024','runtime'),
('/tmp/rustusb-prepare-stop-service-ab-first-20261024','service-development'),
('/tmp/rustusb-prepare-stop-software-ab-review-20261024','review/final'),
('/tmp/rustusb-prepare-stop-software-ab-review-initial-20261024','review/initial'),
('/tmp/rustusb-prepare-recording-rust-client-author-20261024','authors/rust-client'),
], [(str(Path('/tmp')/n),'review/'+n) for n in [
'run-rustusb-prepare-stop-software-ab-first-20261024.py',
'run-rustusb-prepare-stop-local-runtime-ab-20261024.py',
'run-rustusb-prepare-stop-service-ab-first-20261024.py',
'review-rustusb-prepare-stop-software-ab-20261024.py',
'review-rustusb-prepare-stop-software-ab-20261024-initial.py',
'rustusb-prepare-stop-software-ab-review-delivery-20261024.json',
'rustusb-prepare-stop-667-input-map-staged-20261024.json',
'rustusb-prepare-stop-667-input-map-8ec570a-20261024.json',
'rustusb-native-prepare-local-author-handoff-20261024.json',
'rustusb-prepare-native-ab-source-review-20261024.json',
]], [], dict(result='PASS',actual_run_source='dirty b1de03488474af74a4155232cda73a00e1e0f142',
matched_product_commit='8ec570ad160c59c89303a0f63ed9651696a70b71',inputs=167,default=280,all_features=367,vendor=20,total_executions=667,result_rows=39,failed=0,ignored=0,qualified_observed_names=387,
cli_sha256='72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2',helper_sha256='b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5',
notes=['Actual dirty run source is preserved and individually mapped, not relabeled clean.',
'Ten full-gate Cargo steps, two rustdocs, native-only check/tree, four signals and two original backpressure scenarios pass.',
'Signal child ELF receipts differ in strength from backpressure workflow/build provenance.',
'Full wrapper protects167 inputs; broad signal inventory contains9712 entries, intersects166 selected inputs and omits scripts/check.sh by its selector.',
'First independent parser overbroad inventory expectation and correction retained; not product failure.',
'Review author contributed Native code; independent method does not mean independent implementation.',
'No QEMU/hardware/full Phase3 acceptance inferred from these software results.']))

archive('prepare-recording-local-baseline-20261024', [
('/tmp/rustusb-prepare-stop-local-resource-b1de034-20261024','runs/b1-native-resource-session'),
('/tmp/rustusb-prepare-stop-local-resource-b529f9e-20261024','runs/b529-native-resource-session'),
('/tmp/rustusb-prepare-stop-local-available-b529f9e-20261024','runs/b529-native-available-session'),
('/tmp/rustusb-prepare-local-detach-diagnosis-20261024','review/detach-diagnosis'),
('/tmp/rustusb-prepare-local-detach-review-20261024','review/detach-method'),
('/tmp/rustusb-prepare-local-old-two-review-20261024','review/two-baselines'),
('/tmp/rustusb-prepare-local-detach-static-20261024','harness/detach-static'),
('/tmp/rustusb-prepare-local-detach-fixtures-20261024','harness/detach-fixtures'),
('/tmp/rustusb-prepare-local-detach-frozen-sources-20261024','harness/detach-sources'),
('/tmp/rustusb-prepare-local-detach-merge-20261024','harness/merge'),
('/tmp/rustusb-prepare-local-static-20261024','harness/initial-static'),
('/tmp/rustusb-prepare-local-static-revision2-20261024','harness/static-revision2'),
], [(str(Path('/tmp')/n),'review/'+n) for n in [
'rustusb-prepare-local-author-handoff-20261024.json',
'rustusb-prepare-local-detach-author-handoff-20261024.json',
'rustusb-prepare-local-source-review-20261024.json',
'check-prepare-local-static-20261024.py',
'check-prepare-local-static-revision2-20261024.py',
'check-prepare-local-detach-static-20261024.py',
'check-prepare-local-detach-fixtures-20261024.py',
]], [], dict(result='RETAINED_NEGATIVES',runs=3,guest_exit_zero=6,
commits=['b1de03488474af74a4155232cda73a00e1e0f142','b529f9ec5c83f3c1ef71151684aff324b6de00d8'],
product_inputs_match='e10b5fbe9df89216c0002ea11a51d41837fd9b03',
notes=['Original b1 Resource local FAIL has four failed predicates; detach root snapshot was sampled before original stub retirement.',
'Actual devnum0 and usbip_status1 differ from historical receipt2; no specific race cause inferred.',
'Two b529 baselines each retain expected three failed held-release predicates, and now require read-only exact original-stub retirement before last export stop.',
'All failures release original gate, join actual owners and complete fresh simulated-device I/O, without changing the original FAIL.',
'New samples do not establish a counterfactual for the earlier b1 run.',
'Available StopSession caller actually returned unexpected EOF, Resource timed out; both exit1 with no success response. Reviewer first overbroad expectation retained.',
'Diagnostic counts, source copies and static fixtures are not additional USB cases.',
'Author/method overlap is explicit; no hardware acceptance.']))
