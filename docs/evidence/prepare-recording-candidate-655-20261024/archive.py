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


archive('prepare-recording-candidate-655-20261024', [(str(Path('/tmp')/n),n.removeprefix('rustusb-')) for n in ['rustusb-prepare-stop-service-first-20261024','rustusb-prepare-stop-software-first-20261024','rustusb-prepare-stop-local-runtime-20261024','rustusb-prepare-stop-full-software-review-20261024','rustusb-prepare-local-red-20261024','rustusb-prepare-reserved-local-red-20261024']], [(str(Path('/tmp')/n),'review/'+n) for n in ['run-rustusb-prepare-stop-service-first-20261024.py','run-rustusb-prepare-stop-software-first-20261024.py','run-rustusb-prepare-local-red-20261024.py','run-rustusb-prepare-reserved-local-red-20261024.py','rustusb-prepare-native-source-review-initial-20261024.json','rustusb-prepare-native-source-review-delta-20261024.json','rustusb-prepare-native-source-review-scope-addendum-20261024.json','rustusb-prepare-local-cancel-reserved-design-20261024.json']], [], dict(status='CANDIDATE_INCOMPLETE',software_gate='PASS',actual_run='dirty21a96fa',selected_inputs=167,default=274,all_features=361,vendor=20,executions=655,result_rows=39,qualified_observed_names=381,cli_sha256='b7d610e002317d73c3603ebfb82866e0447f97dd5696236b81978b5998881554',helper_sha256='b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5',notes=['Ten actual Cargo steps pass, with both warnings-as-errors rustdoc builds; 167 selected inputs remain stable.','Earlier service-only first55/64 run predates the identity attribution fix and remains separately labeled.','Original four signal/two output-backpressure runs pass with the same actual all-features releaseCLI.','Subsequent source review and five actual RED assertions expose interrupted identity and startup local-stop/sibling defects; these results do not constitute final feature acceptance.','Four new assertions execute0pass4fail, then reserved-unsent executes0pass1fail, with original655 product behavior unchanged and source snapshots retained.','Never invent a root proof or mark reserved-unsent NotStarted/Verified to resolve metadata uncertainty.','Raw compiler/debugELF provenance strengths remain explicit; tests/diagnostic predicates are not USB hardware or QEMU cases.','No future-commit mapping, clean-run relabeling, fullPhase3 or hardware acceptance is claimed.']))
