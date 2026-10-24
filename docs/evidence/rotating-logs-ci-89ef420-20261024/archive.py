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


archive('rotating-logs-ci-89ef420-20261024', [
 ('/tmp/rustusb-logging-ci-89ef420-20261024','official'),
 ('/tmp/rustusb-logging-ci-89ef420-method-review-20261024','method-review'),
], [], [],
 dict(result='PASS',commit='89ef4203163da65fb09c8a1ce1f6d31995eaa70d',run_id=40053603593,attempt=1,
 jobs=[120751515434,120751515466,120751515541,120751515568],
 software_executions_per_job=723,default=308,all_features=395,vendor=20,software_result_rows_per_job=43,
 software_failed=0,software_ignored=0,new_logging_cases_per_job=6,old_signal_cases_per_job=4,old_backpressure_cases_per_job=2,
 notes=['Four actual native Ubuntu 22.04/24.04 x86_64/aarch64 jobs completed/success; initial in-progress and final official snapshots remain separate.',
 'Each new logging summary records38 child processes,11 actual/proc ELF samples and27 fast command/binary-only cases; no executed-ELF sample is invented for fast children.',
 'Actual22 pipe_write and24 anon_pipe_write backpressure observations remain platform-specific.',
 '190 logging source entries and14865 old signal inventory entries include historical archives; their14866-path/362-unique-Gitblob correspondence is not a compilation source count.',
 'Method review304 diagnostics not USB cases, reviewer authored logging storage and QEMU verifier: method independence not independent implementation.',
 'Official logs expose only some file contents and some artifact hashes. Missing runner/tmp files, executable bytes, private data and per-child BP receipts are not reconstructed.',
 'The raw local EFBIG captures, fixed-script passed assertions and official summarized CI observations have different evidence strength; after-wait EOF timing is retained.',
 'Original41fce four setup failures remain separately archived FAIL; no hardware or new QEMU result is inferred.']))

