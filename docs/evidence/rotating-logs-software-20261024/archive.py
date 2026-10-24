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


archive('rotating-logs-research-20261024', [
 ('/tmp/rustusb-logging-research-20261024','research'),
 ('/tmp/rustusb-logging-service-preflight-20261024','service-preflight'),
 ('/tmp/rustusb-logging-service-author-20261024','service-author'),
 ('/tmp/rustusb-logging-worker-initial-20261024','worker-initial'),
 ('/tmp/rustusb-logging-lock-preparation-20261024','lock-preparation'),
 ('/tmp/rustusb-logging-lock-resolution-20261024','lock-resolution'),
], [(str(Path('/tmp')/n),'reviews/'+n) for n in [
 'rustusb-phase3-rust-crash-coordination-review-20261024.md',
 'rustusb-phase3-rust-crash-coordination-review-20261024.json',
 'rustusb-rotating-logs-design-readonly-review-20261024.json',
 'rustusb-rotating-logs-storage-preflight-20261024.json',
 'rustusb-logging-storage-author-handoff-20261024.json',
 'rustusb-logging-record-worker-author-handoff-20261024.json',
]], [], dict(kind='research_source_preflight_and_author_handoff_not_runtime_acceptance',
 original_head='bd6e08be8b304767452dc98b6606e2e65448c2da',
 notes=['Fixed tracing-appender 0.2.5 and file-rotate 0.8.0 sources/provenance and licenses are preserved; these compatibility findings are not new dependency imports or security advisories.',
 'Only a new internal package was added: external package versions/checksums unchanged, inventory 158 to 159.',
 'Author handoff hashes describe the historical author freeze. Later style changes have their own actual source snapshots in software archives.',
 'Original repository source references can be resolved in Git; hash-only reviews do not imply original runtime source copies or execution.',
 'Crash coordination and complete G3 findings remain separately scoped and unfinished.']))

archive('rotating-logs-software-20261024', [
 ('/tmp/rustusb-logging-dev-first-20261024','development'),
 ('/tmp/rustusb-logging-software-first-20261024','first-full-gate-failure'),
 ('/tmp/rustusb-logging-clippy-second-20261024','second-clippy-failure'),
 ('/tmp/rustusb-logging-software-second-20261024','final-full-gate'),
 ('/tmp/rustusb-logging-software-second-independent-review-20261024','review/initial-parser'),
 ('/tmp/rustusb-logging-software-second-independent-review-corrected-20261024','review/corrected-parser'),
], [(str(Path('/tmp')/n),'review/'+n) for n in [
 'run-rustusb-logging-dev-first-20261024.py',
 'run-rustusb-logging-software-first-20261024.py',
 'run-rustusb-logging-clippy-second-20261024.py',
 'run-rustusb-logging-software-second-20261024.py',
 'rustusb-logging-723-input-map-f3cf8d4-20261024.json',
 'review-rustusb-logging-software-second-20261024.py',
 'review-rustusb-logging-software-second-corrected-20261024.py',
 'rustusb-logging-software-second-independent-review-handoff-20261024.json',
]], [], dict(result='PASS_after_two_preserved_style_failures',
 actual_head='48a4b013fceb4493cf07c10cac999b8c41f76dae',actual_clean=False,
 committed_matching_product='f3cf8d4f13077cbcbb48c9e3e27918b87ca31a0d',
 selected_compilation_inputs=177,default_executions=308,all_features_executions=395,vendor_executions=20,
 total_test_executions=723,result_rows=43,qualified_function_names=415,failed=0,ignored=0,
 cli_sha256='6cc828f3769d1ebd4c2e7b566707b7788f107151bc21155843c315a818b8b474',
 helper_sha256='bb0ea543bf6cc1af840c4e793b26906a6f9062b534c00b0c6549dce3fce834a1',
 notes=['The full gate is an actual dirty source run with all 177 before/after source bytes preserved; matching later Git blobs does not relabel it clean.',
 'First development 22 logger plus 4 service tests is a separate earlier source scope; root then added 2 strict DTO/cursor tests and observer cancellation behavior.',
 'First full gate and second all-features Clippy failed on two separate collapsible-if warnings; no test executions occurred in either failed gate.',
 'Final gate contains 10 actual Cargo commands and two rustdoc warnings-as-errors checks plus Native-only check/tree with no engine/nusb/rusb/libusb1-sys in normal dependencies.',
 'Initial review parser module-name error is retained; corrected review diagnostic counts are not software test counts.',
 'Reviewer implemented record/worker: statistical/source method independence is not independent implementation.']))

archive('rotating-logs-local-runtime-20261024', [
 ('/tmp/rustusb-logging-local-runtime-first-20261024','actual-runtime'),
 ('/tmp/rustusb-logging-local-runtime-independent-review-20261024','review/initial-parser'),
 ('/tmp/rustusb-logging-local-runtime-independent-review-corrected-20261024','review/corrected-parser'),
 ('/tmp/rustusb-process-logging-runner-author-20261024','runner/first-static'),
 ('/tmp/rustusb-process-logging-runner-author-revision2-20261024','runner/revision2-static'),
 ('/tmp/rustusb-process-logging-source-review-20261024','runner/source-review'),
], [(str(Path('/tmp')/n),'review/'+n) for n in [
 'run-rustusb-logging-local-runtime-first-20261024.py',
 'review-rustusb-logging-local-runtime-first-20261024.py',
 'review-rustusb-logging-local-runtime-first-corrected-20261024.py',
 'rustusb-logging-local-runtime-independent-review-handoff-20261024.json',
]], [], dict(result='PASS',source_head='f3cf8d4f13077cbcbb48c9e3e27918b87ca31a0d',source_clean=True,
 compiled_input_guard_count=177,new_runtime_logging_cases=6,original_signal_cases=4,original_backpressure_cases=2,
 cli_sha256='6cc828f3769d1ebd4c2e7b566707b7788f107151bc21155843c315a818b8b474',
 notes=['No rebuild in the clean checkout: selected external ELF is from the actual dirty 723 gate above.',
 'New 6 cases: startup refusals (7 internal steps), rotation/restart/cursors, follow INT, follow TERM, real 4096-byte stdout backpressure, real child RLIMIT_FSIZE EFBIG.',
 'EFBIG preserves same inode/FD and a real 16-byte trailing fragment, actual virtual stopped resources zero, then actual wait exit 1, then two data EOF observations. Parent did not sample EOF before process exit.',
 '38 new subprocess records contain only 11 actual /proc executable samples. 27 fast children have selected-argv/binary guards without a sampled executed ELF.',
 'New source inventory 190 differs from 177 compilation inputs. Old signal inventory 14149 includes 13951 historical documentation paths; those are not current compilation inputs.',
 'Original 4 signals and 2 backpressure runs retain their old evidence scope. Missing per-child ELF or raw wire in old backpressure is not reconstructed.',
 'Only loopback memory virtual profiles, no helper or host USB/sysfs/kernel mutation. No hardware acceptance.',
 'Two initial parser ID/module mistakes remain in original review artifacts; corrected method does not alter the actual runtime result.',
 'Reviewer authored record/worker; independent raw/statistical method is not independent implementation.']))

