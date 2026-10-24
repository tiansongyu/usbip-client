# Exact d9ebe2e CI method review

Official software run `40067501829` at commit
`d9ebe2eadf8490081035199c91d17bbba41bc57e` completed successfully on the four
Ubuntu 22.04/24.04 x86_64/aarch64 jobs. `first/report.json` is the first actual
parser execution, with 617 passing diagnostic predicates and no parser failure.
Predicates and subprocess receipts are not new USB case counts.

Each job has 343 default + 434 all-features + 20 vendor test executions: 797,
47 libtest result rows, zero failed/ignored/measured/filtered. Executable labels
and named tests are preserved. Cargo stderr and libtest stdout may interleave;
the parser pairs their respective ordered sequences and requires every explicit
running count and named-test count to match the corresponding result. It uses
the corrected method from the earlier dff36a3 review, without altering that
review's preserved parser failure or connector failures.

Each job also contains the original four signal, two backpressure and six logging
scenarios. Five real TLS adapter cases are already within the 797 test total.
The six logging groups have 38 child receipts: 11 actual `/proc/exe` receipts and
27 explicitly fast argv-only receipts. Actual errno 27 / 16-byte partial-write
and stopped virtual-resource snapshots are preserved in the official summary.

The new diagnostic-export scope is nine actual groups per job and 13 child
receipts: four full process/executable samples, six process-identity-only samples,
and three pre-exec-only samples. The groups cover refusal, local capture, logs,
partial write failure, successful DEVLIST with human/JSON stderr write failure,
INT/TERM at removable syscall entry, accepted publication under a signal, and
output backpressure. The two stderr variants are children within one group.
Their observed exits are 1, not 101. The separately executed four default-only
local diagnostic groups are not present in this CI and are not inferred.

The parser checks actual safe document projections, metadata, receipt hashes,
publication/cancellation distinctions, same inode across the captured syscall
entry and final output, and actual wait statuses. The entry gates are removable
ptrace stops before kernel execution; they do not establish cancellation of a
hard-blocked kernel filesystem operation. CI does not exercise a real root helper
or hardware for these ordinary-user local cases.

Root captured the four official connector `structuredContent.content` strings
as exact UTF-8 chunks. The raw directory is
`/tmp/rustusb-diagnostic-ci-d9ebe2e-root-20261024`. The four pre-delivered SHA-256
values are checked. All original BOM and newline bytes are retained. The initial
in-progress response remains separate from the final successful run and jobs
responses. `metadata-byte-comparison.json` also compares the final run body to the
response's exact UTF-8 content. Pages `40067501862` supplies status-only evidence.

The logged inventories contain 16,033 unique paths / 398 unique Git blobs at the
exact commit; many paths are archived evidence. They are not compilation inputs.
The 192 actual frozen inputs of the second full software gate map individually to
Git d9ebe2e. That gate ran in a dirty 2ca5f5b working tree and is not relabelled a
clean d9ebe2e execution. Earlier dff36a3 / 793 evidence remains separate.

Official logs contain platform ELF and ephemeral artifact hash/stat receipts.
The CI ELF binaries, private runtime file bodies, ptrace traces and protocol
captures are not locally available and are not reconstructed. Source confirms
that legacy data EOF is sampled after process wait; no before-exit EOF timestamp
is inferred. No QEMU, hardware or full Phase 3 acceptance follows from this review.

Reproduce into a NEW output directory, without API/build/product execution:

```sh
python3 /tmp/rustusb-diagnostic-ci-d9ebe2e-method-review-20261024/review.py \
  --output /tmp/rustusb-diagnostic-ci-d9ebe2e-new-review
```

The parser imports no product or harness verifier. Its only subprocesses read
Git objects. This reviewer authored logging storage, service registry code and a
separate QEMU verifier, so this is an independent-method cross-review of actual
evidence, not independent implementation review.
