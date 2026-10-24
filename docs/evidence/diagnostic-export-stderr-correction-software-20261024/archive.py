from pathlib import Path

common = Path('/tmp/archive-rustusb-rotating-logs-initial-20261024.py').read_text().split("\narchive(", 1)[0]
exec(compile(common, '<existing exact-copy archive helper>', 'exec'))

archive('diagnostic-export-local-first-failure-20261024', [
 ('/tmp/rustusb-diagnostic-local-first-20261024', 'sparse-preflight-first'),
 ('/tmp/rustusb-diagnostic-local-second-20261024', 'sparse-preflight-second'),
 ('/tmp/rustusb-diagnostic-local-third-20261024', 'actual-clean2ca-failure'),
 ('/tmp/rustusb-diagnostic-process-runner-author-20261024', 'runner-first-static'),
 ('/tmp/rustusb-diagnostic-process-runner-source-review-20261024', 'runner-final-source-review'),
 ('/tmp/rustusb-diagnostic-stderr-first-failure-research-20261024', 'stderr-fixed-source-research'),
], [
 ('/tmp/run-rustusb-diagnostic-local-first-20261024.py', 'runners/preflight-first.py'),
 ('/tmp/run-rustusb-diagnostic-local-second-20261024.py', 'runners/preflight-second.py'),
 ('/tmp/run-rustusb-diagnostic-local-third-20261024.py', 'runners/actual-first.py'),
 ('/tmp/rustusb-diagnostic-stderr-differential-20261024/result.json', 'old-product-differential/result.json'),
] + [(str(Path('/tmp/rustusb-diagnostic-stderr-differential-20261024')/mode/name), 'old-product-differential/'+mode+'/'+name)
     for mode in ['debug', 'off'] for name in ['stdout.bin', 'stderr.bin', 'partial.bin']], [], dict(
 result='FAIL retained original clean2ca actual process run; later scenarios unrun',
 actual_head='2ca5f5bfe395f97d06807d00a1d97201d94ebe5d', actual_clean=True,
 product_commit='dff36a32f352ec37b778af6690e7939edf655dc7',
 cli_sha256='67fff5700d3282f711659c8b3fae363d5ee4f80d9a8bfdd42476dba388dfc419',
 case_results={'refusals':'PASS','local':'PASS','logs':'PASS','write-failure':'FAIL'}, children=7,
 notes=['Two earlier sparse-worktree guards failed before any CLI child: Git2.34.1 requires init --cone before set. Their original runners/preflight records are separate.',
 'Actual strict fourth scenario received FileError errno27/partial16/digest correctly, but original wait101 failed required exit1. The real regular stderr also stopped at16 bytes; no panic backtrace is claimed.',
 'All7 children were reaped, no forced cleanup/secondary collection error; source/binary/head/clean guards remained true. Signal, backpressure, default and old regressions were not run.',
 'First static runner b086 predates final33730B/27d33 source; final reviewed source and actual selected source copy are retained separately.',
 'Actual two-child old-binary diagnostic differential debug101 versus off1 is diagnosis only; disabling logs is not acceptance or a workaround proposal.',
 'Copied original ELF remains locally at /tmp/rustusb-diagnostic-stderr-differential-20261024/original-rustusb with the stated hash; it is not uploaded in this source/evidence archive.',
 'The final source review includes fixed Linux UAPI URLs/hashes; fixed Rust sources/licenses are separately retained in the existing research archive.']))

archive('diagnostic-export-stderr-correction-software-20261024', [
 ('/tmp/rustusb-diagnostic-software-second-20261024', 'full-gate'),
 ('/tmp/rustusb-diagnostic-stderr-correction-dev-20261024', 'actual-focused-runtime'),
 ('/tmp/rustusb-stderr-write-failure-source-review-20261024', 'source-review'),
], [
 ('/tmp/run-rustusb-diagnostic-software-second-20261024.py', 'runner.py'),
 ('/tmp/rustusb-diagnostic-default-build-second-20261024/build.log', 'default-build/build.log'),
 ('/tmp/rustusb-diagnostic-default-build-second-20261024/result.json', 'default-build/result.json'),
 ('/tmp/rustusb-diagnostic-default-build-second-20261024/start.json', 'default-build/start.json'),
 ('/tmp/rustusb-diagnostic-default-build-second-20261024/inputs-before.json', 'default-build/inputs-before.json'),
], [], dict(result='PASS software gates and two focused actual stderr correction scenarios',
 actual_head='2ca5f5bfe395f97d06807d00a1d97201d94ebe5d', actual_clean=False,
 selected_inputs=192, default_test_executions=343, all_features_test_executions=434, vendor_test_executions=20,
 total_test_executions=797, result_rows=47, failed=0, ignored=0,
 cli_sha256='9708a50ee836378cd64f8726dae36caded96d4fca39faae215aa30658ef49b48',
 helper_sha256='7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322',
 default_cli_sha256='88c00373baab4e25b6df149dbe74d4009fbe1b2932ffe7039b1fcf1ff55ffdb0',
 runtime_cases=2, runtime_children=3,
 notes=['All192 selected actual source bytes remained stable; later matching Git blobs must not relabel this dirty run clean.',
 'Unit additions delegate real failed File writes, preserving EBADF then ENOSPC and latching the first error; they do not claim std stderr reports a closed descriptor as EBADF.',
 'Actual new fixed-file-failure retains regular stderr and FSIZE16; output partial16/errno27/digest unchanged and actual wait1.',
 'Second case has human/JSON console subchecks: actual controlled loopback empty DEVLIST succeeds, stdout[] unchanged, console loss forces wait1. This is no host device scan or USB benchmark.',
 'All3 actual children reaped, source/ELF guards true, no forced cleanup/secondary errors. These2 cases are not the complete9-case observer matrix.',
 'Default release built separately against the same192 source bytes; build target/ELF bodies are not uploaded here.',
 'Previous793/clean2ca original failure and CI dff keep their source scope. Full process matrix, installed QEMU and revised CI remain pending.']))
