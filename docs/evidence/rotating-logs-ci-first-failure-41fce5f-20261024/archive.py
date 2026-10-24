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


archive('rotating-logs-ci-first-failure-41fce5f-20261024', [
 ('/tmp/rustusb-logging-ci-41fce5f-20261024','official'),
 ('/tmp/rustusb-logging-cache-precondition-fix-20261024','review-and-fixtures'),
], [('/home/ubuntu/github/usbip-client/scripts/test-process-logging.py','corrected-runner/test-process-logging.py')], [],
 dict(result='FAIL',commit='41fce5ff60f6b548e9151ee2d4c6decdb2c99e7d',run_id=40052402641,
 jobs=[120747703286,120747703493,120747703505,120747703514],
 software_executions_per_job=723,software_result_rows_per_job=43,software_failed=0,software_ignored=0,
 old_signal_cases_per_job=4,old_backpressure_cases_per_job=2,new_logging_cases_executed_per_job=0,
 new_logging_children_per_job=0,corrected_runner_commit='89ef4203163da65fb09c8a1ce1f6d31995eaa70d',
 corrected_runner_sha256='957d1a407366632f8130764ac010574129066cfcf878d7899bcbbb248bf6c2b4',
 notes=['Four actual native Ubuntu 22.04/24.04 x86_64/aarch64 jobs failed at the identical missing HOME/.cache setup precondition before any new logging case.',
 'Earlier software723, four signal and two backpressure steps passed; the full jobs remain failures.',
 'Official connector response bytes and exact content UTF8 decoded logs are preserved; no runner temporary originals or extra child ELF are reconstructed.',
 'The corrected runner validates ancestors first, creates only missing cache0700 and never chmods existing paths. Product checked-path constraints and Rust177 inputs are unchanged.',
 '12 real ordinary-UID filesystem function fixtures and 60 read-only failure-analysis diagnostics passed. These are not CLI/USB test executions.',
 'Original precondition source and exact diff retained. Corrected CI has a separate run identity and result.']))

