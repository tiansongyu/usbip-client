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

archive('prepare-recording-final-1582f06-20261024',[
('/tmp/rustusb-prepare-stop-final-1582f06-20261024','runs'),
('/tmp/rustusb-prepare-final-158-raw-review-20261024','review/prepare'),
('/tmp/rustusb-prepare-stop-health-independent-review-attempt1-20261024','review/health-first'),
('/tmp/rustusb-prepare-stop-health-independent-review-final-20261024','review/health-final'),
('/tmp/rustusb-prepare-stop-legacy-nine-review-20261024','review/legacy-nine'),
('/tmp/rustusb-prepare-final-input-map-20261024','review/source-mapping'),
],[(str(Path('/tmp')/n),'review/'+n) for n in [
'run-rustusb-prepare-stop-final-matrix-20261024.py',
'reconcile-rustusb-prepare-final-inputs-20261024.py',
'rustusb-prepare-stop-667-input-map-1582f06-20261024.json',
'rustusb-prepare-stop-health-independent-review-delivery-20261024.json',
'review-rustusb-prepare-stop-health-1582f06-20261024.py',
'review-rustusb-prepare-stop-health-sha-final-20261024.py',
'rustusb-prepare-validation-doc-input-map-20261024.json',
]],[],dict(result='PASS',commit='1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16',clean=True,runs=23,prepare_runs=8,health_runs=6,legacy_runs=9,guest_exit_zero=46,directional_fault_phases=18,selected_matrix_code_inputs=222,
cli_sha256='72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2',helper_sha256='b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5',
notes=['Actual execution55568 was waited to exit0. All23 locked all-features builds, source/binary/head/clean guards and sameELF checks pass.',
'Eight new scenarios:Native/USBFS Resource StopJob/SIGTERM, accepted USBFS initializer StopJob/SIGTERM, Native Resource/Available local StopSession.',
'Initializer is actual accepted syscall-entry retention, same invocation resumed and actual Finish; not kernel-internal hard-block cancellation.',
'Local target release does not close original sibling; actual HID I/O, exact original stub retirement precedes last export stop, samejob/listener authenticated emptycatalog, then fresh reuse.',
'Six existing v2 runs contain18 targeted fault phases. Nine older raw-v1 regressions remain a separate group.',
'All initial failed product runs and222 sampling failure retain original verdict/source elsewhere; none is overwritten.',
'Independent reconciliation methods include contributing authors. Diagnostics/source entries/packet counts are not extra USBcases.',
'No hardwareG1/G2/ISO/WindowsUSB/performance or completePhase3 acceptance.']))

archive('prepare-recording-ci-5ffdc6c-20261024',[
('/tmp/rustusb-prepare-stop-ci-5ffdc6c-20261024','official'),
('/tmp/rustusb-prepare-stop-ci-independent-5ffdc6c-20261024','review'),
],[],[],dict(result='PASS',commit='5ffdc6cfcc0e3c2de386121d0277d7daf0ed4e9f',run_id=40044605684,attempt=1,jobs=[120723319604,120723319703,120723319780,120723319787],executions_per_job=667,result_rows=39,default=280,all_features=367,vendor=20,
notes=['Four actual native Ubuntu22.04/24.04 x86_64/aarch64 successful jobs. Full official responses and exactUTF8 decoded logs retained.',
'Five TLS cases are inside parent libtest already counted, original four signals and two backpressure cases have separate scopes.',
'Independent155 diagnostic checks pass, four10549-entry source inventories equal exact5ff Gitblobs and same fingerprint; entries are not compiled-source count.',
'Actual22 kernel6.8.0-1064 uses pipe_write,24 kernel6.17.0-1022 uses anon_pipe_write, not inferred solely from platform label.',
'First job-envelope parser error retained. Initial two patch-derived newline variants retained underinitial-derived; final logs exactly encode original officialcontent.',
'No uploaded runner temporary originals or ELFfiles reconstructed. Childproc receipts, readybackend snapshots and blockedclosure proof remain distinct.',
'QEMU continues to belong to clean1582. Later docs-only status checks cannot relabel these detailed logs.']))
