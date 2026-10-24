from pathlib import Path

common = Path('/tmp/archive-rustusb-rotating-logs-initial-20261024.py').read_text().split("\narchive(", 1)[0]
exec(compile(common, '<existing exact-copy archive helper>', 'exec'))

archive('diagnostic-export-research-20261024', [
 ('/tmp/rustusb-diagnostic-export-native-preflight-20261024', 'native-preflight'),
 ('/tmp/rustusb-diagnostic-export-service-preflight-20261024', 'service-preflight'),
 ('/tmp/rustusb-diagnostic-export-storage-research-20261024', 'fixed-source-storage-research'),
 ('/tmp/rustusb-diagnostic-owned-probe-author-20261024', 'native-author'),
 ('/tmp/rustusb-diagnostic-output-author-20261024', 'output-author'),
 ('/tmp/rustusb-diagnostic-artifact-author-20261024', 'artifact-author'),
 ('/tmp/rustusb-diagnostic-service-author-handoff-20261024', 'service-author'),
 ('/tmp/rustusb-diagnostic-output-cross-review-20261024', 'output-cross-review'),
 ('/tmp/rustusb-diagnostic-export-combination-review-20261024', 'combination-cross-review'),
], [('/tmp/rustusb-diagnostic-export-service-schema-review-20261024.md', 'service-schema-review.md')], [], dict(
 kind='fixed-source research and source-only author/cross-review handoffs, not runtime acceptance',
 baseline='46cd950affb0c949b164d0051be7bf70f365fac9', design_commit='88179f203cd89c46fd68f4f437e5ff3f7053affc',
 notes=['Historical author-freeze hashes retain their exact scope; the software archive records final actually compiled sources.',
 'Authors/cross-reviewers participated in implementation; method independence is not an independent implementation audit.',
 'Fixed Rust 1.97.1 sources and failed version URL attempts are distinguished from the separately initially consulted stable 1.99.1 page.',
 'No external implementation was copied into product sources. Only the internal diagnostic package is added.',
 'The combination review used getuid before the final precise geteuid change; final compiled source snapshots show the actual effective-UID filter.']))

archive('diagnostic-export-software-initial-20261024', [
 ('/tmp/rustusb-diagnostic-lock-resolution-20261024', 'lock-initial-preparation-failure'),
 ('/tmp/rustusb-diagnostic-lock-resolution-second-20261024', 'lock-final-resolution'),
 ('/tmp/rustusb-diagnostic-dev-first-20261024', 'first-focused-fixture-failure'),
 ('/tmp/rustusb-diagnostic-first-failure-method-review-20261024', 'first-failure-source-review'),
 ('/tmp/rustusb-diagnostic-software-first-20261024', 'full-gate'),
], [
 ('/tmp/run-rustusb-diagnostic-dev-first-20261024.py', 'runners/focused.py'),
 ('/tmp/run-rustusb-diagnostic-software-first-20261024.py', 'runners/full.py'),
 ('/tmp/rustusb-diagnostic-default-build-20261024/build.log', 'default-build/build.log'),
 ('/tmp/rustusb-diagnostic-default-build-20261024/result.json', 'default-build/result.json'),
 ('/tmp/rustusb-diagnostic-default-build-20261024/inputs-before.json', 'default-build/inputs-before.json'),
], [], dict(result='PASS full software gates after a preserved focused fixture failure', actual_head='88179f203cd89c46fd68f4f437e5ff3f7053affc', actual_clean=False,
 selected_inputs=191, default_test_executions=341, all_features_test_executions=432, vendor_test_executions=20,
 total_test_executions=793, result_rows=47, failed=0, ignored=0,
 cli_sha256='67fff5700d3282f711659c8b3fae363d5ee4f80d9a8bfdd42476dba388dfc419',
 helper_sha256='f1e1e1717b4380af8c99132b92098818870602c8d6a39e2195b4e65b1804ae0b',
 default_cli_sha256='a6d12305506c97fda617be0f11f7fceb28d7d3469ac8e33c498a97ca5807a99f',
 notes=['Initial Python tomllib import failed before Cargo; no-deps metadata then failed to resolve a new lock entry before locked inventory. Both attempts remain separate.',
 'Final 159 to 160 package change is only internal; complete external Cargo.lock blocks are byte-identical.',
 'First focused run had diagnostic20/24 PASS and service10 PASS/1 FAIL; Native reap and CLI check commands were not reached in that failed run.',
 'The failure was a fixture reading a Weak monitor after the owner was consumed; corrected test requires actual ShutdownReport counters and actual disk Audit sequence1.',
 'Full gate ran all ten required Cargo commands and secure-only Native normal tree inspection with no engine/nusb/rusb/libusb; all191 input bytes remained stable.',
 'Default ELF was separately built against the same191 inputs; its large target directory is not an evidence archive or a claimed binary upload.',
 'These dirty runs are not relabelled clean by later commit mapping. CLI subprocesses, new QEMU and CI remain pending.']))
