from pathlib import Path

common = Path('/tmp/archive-rustusb-rotating-logs-initial-20261024.py').read_text().split("\narchive(", 1)[0]
exec(compile(common, '<existing exact-copy archive helper>', 'exec'))

archive('diagnostic-export-qemu-harness-source-20261024', [
 ('/tmp/rustusb-diagnostic-harness-author-20261024', 'initial-author-freeze'),
 ('/tmp/rustusb-diagnostic-harness-static-20261024', 'initial-static'),
 ('/tmp/rustusb-diagnostic-harness-author-revision2-20261024', 'revision2-author'),
 ('/tmp/rustusb-diagnostic-harness-cross-review-20261024', 'cross-review'),
], [], [], dict(kind='QEMU harness source/static/pre-run methods, no QEMU execution',
 initial_static_checks=80, revision2_constructed_checks=27,
 final_run_sha256='5937e995b8e0285c4f7e23738005c11330abaff543bebf0a4639077aff61d912',
 final_verify_sha256='6745a40570f031b0d91aef20fd5c7718ba448f36b23b844dcf36f3b6e700a735',
 notes=['Initial80 results keep their frozen source scope; the static jq extraction collector failure is preserved.',
 'Cross-review identified UUID insertion-order mismatch, missing candidate/actual-broker binding, and missing postwait terminal marker checks.',
 'Revision2 changes only run/verify; guest/README/generated init remain byte-identical. The old projection checker actually rejects the new reordered valid fixture; malformed fields/aliases and late/missing/wrong PASS/error markers remain rejected.',
 'The original C gate, 30s hold/120s process limits and inherited exact Rootproof/baseline/NULL/Gone/State-Protocol peer-close predicates are unchanged.',
 'These are authored/cross-reviewed source methods, not USB cases, test executions, hardware results or an independently implemented lifecycle auditor.']))
