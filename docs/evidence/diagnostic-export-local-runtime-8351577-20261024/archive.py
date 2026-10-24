from pathlib import Path

common = Path('/tmp/archive-rustusb-rotating-logs-initial-20261024.py').read_text().split("\narchive(", 1)[0]
exec(compile(common, '<existing exact-copy archive helper>', 'exec'))

archive('diagnostic-export-local-runtime-8351577-20261024', [
 ('/tmp/rustusb-diagnostic-local-fixed-20261024', 'actual-runtime'),
 ('/tmp/rustusb-diagnostic-local-fixed-independent-review-20261024', 'diagnostic-method-review'),
 ('/tmp/rustusb-diagnostic-local-legacy-cross-review-20261024', 'legacy-method-review'),
], [('/tmp/run-rustusb-diagnostic-local-fixed-20261024.py','runner.py')], [], dict(
 result='PASS actual clean835 diagnostic and inherited signal/backpressure matrix',
 actual_head='8351577459d25ae36d7ac8924cfdae7880e5292f', actual_clean=True,
 all_features_groups=9, all_features_children=13, default_groups=4, default_children=6,
 legacy_signals=4, legacy_backpressure=2, selected_source_build_inputs=192,
 all_cli_sha256='9708a50ee836378cd64f8726dae36caded96d4fca39faae215aa30658ef49b48',
 default_cli_sha256='88c00373baab4e25b6df149dbe74d4009fbe1b2932ffe7039b1fcf1ff55ffdb0',
 diagnostic_proc_scopes={'complete_proc_and_executable':5,'identity_only':9,'wrapper_pre_exec_only':5},
 notes=['The13 diagnostic groups have19 actual children, not19 USB cases. Group stderr-success contains two human/JSON console subchecks.',
 'Three actual retained syscall entries: fchmod91 for INT/TERM and fsync74 for accepted publication; real signal forwarding precedes still-held observation, same actual FD/namespace/final inode and actual return0/is_error0/reap are recorded.',
 'Prepare cancellation leaves valid Cancelled partial; accepted publication remains Complete/Published with cancel_observed true. This is a removable entry gate, not hard-kernel cancellation.',
 'Both actual ELF selections, all192 selected source/build inputs and runtime inventories205 match; source/binary/HEAD/clean guards remain true.',
 'Some fast subprocesses lack an executed /proc ELF sample; exact5/9/5 scopes are preserved. Legacy BP has no per-child ELF receipts.',
 'Diagnostic BP did not retain the drained4096 padding bytes; successful controlled DEVLIST did not retain raw8B/12B wire. Their runtime assertions/PASS and actual wait/output remain bound to frozen source, without fabricated independent raw replay.',
 'Legacy blocked stdout has actual pipe_write/no-drain/wait0/TCP EOF sampled after wait; only ready mode has stopped resource0. Do not move those observations earlier.',
 'Early read-only reviewer KeyError and method-only1284/38 predicates remain separate; implementation participants are not described as an independent implementation audit.',
 'The first clean2ca FAIL and earlier dirty correction2groups/3children are not merged or rejudged. Software797 is the separately archived dirty2ca192-input run, mapped to Git835.']))

archive('diagnostic-export-qemu-first-failure-bde3aa9-20261024', [
 ('/tmp/rustusb-diagnostic-qemu-first-bde3aa9-20261024', 'actual-run'),
 ('/tmp/rustusb-diagnostic-gate-launch-correction-20261024', 'launch-source-initial-correction'),
 ('/tmp/rustusb-diagnostic-gate-template-check-20261024', 'actual-compiler-and-isolated-argv-predicates'),
 ('/tmp/rustusb-diagnostic-gate-template-source-review-20261024', 'source-cross-review'),
], [('/tmp/check-rustusb-diagnostic-gate-template-20261024.py','check-template.py')], [], dict(
 result='FAIL original actual cleanbde run retained; fixed launch not yet rerun in QEMU',
 actual_head='bde3aa922086592a6982da3f60e946d21ed58995', actual_clean=True,
 guest_exit_codes={'server':0,'client':0}, primary_error='guest reported actual failure',
 all_four_guards=True, successful_usb_io_acceptance=False, diagnostic_export_pass=False,
 notes=['Server actual agent log says invalid fixed test invocation; original C permits15 exact args, logging launch supplied21. This occurs before server agent startup, not a demonstrated product USB failure.',
 'Both helpers started; client reached ready profiles and then failed during host failure collection. No new USB export/I/O or private diagnostic acceptance is inferred from guest wait0.',
 'The original C remains exactly2b032df5. Generated diagnostic8f7dc401 changes only argc and six exact fixed logging flags; exact reverse substitution reconstructs all original bytes.',
 'Actual29 host commands are3 C compilations and26 isolated argv string-predicate executions. The complete generated privileged tracer was compiled but not executed on the host; no product CLI/ptrace/USB run occurred in these predicate checks.',
 'Changed/missing/extra arguments are rejected; original argc15 predicate reproduces rejection of actual first-run21 arguments. This does not prove a successful guest launch or QEMU acceptance.',
 'Cross-review20 predicates, original80/27 preflight methods and compiler checks are not new USB cases and do not replace the original FAIL.',
 'A subsequent fixed launch must use a new clean revision/evidence directory and preserve ptrace selectors, FD/return/cleanup and30s/120s limits.']))
