# Exact dff36a3 CI method review

This directory reviews official GitHub Actions run `40061061065` at commit
`dff36a32f352ec37b778af6690e7939edf655dc7`. It does not validate the later stderr
fix or the separate 192-input / 797-execution candidate.

The final result is `final-revision2/report.json`: 331 diagnostic predicates pass.
Each of the four native jobs executed 341 default + 432 all-features + 20 vendor
checks, 793 total, with 47 libtest result rows and no failed, ignored, measured or
filtered executions. Five real TLS adapter cases are already included in those
libtest totals. Four signal, two backpressure and six process-logging scenarios
are separately checked from actual official log summaries. Each logging run has
38 child receipts: 11 sampled through `/proc/exe`, 27 explicitly fast argv-only
receipts. Those are child observations, not 38 USB cases.

Root acquired the four log strings through the official GitHub connector and
wrote `structuredContent.content` directly as UTF-8 chunks. Raw files are in the
sibling `rustusb-diagnostic-ci-dff36a3-root-20261024` directory. All four retain
their original UTF-8 BOM and LF bytes; no newline normalization was used. Exact
byte and SHA receipts are checked. The final metadata gives completed/success for
the exact push run and four jobs. Pages run `40061061057` is status-only evidence
for the then-published content, not a claim about a subsequent website revision.

The first independent-method parser run is retained in `final-first/`: it found
all execution counts but failed two ARM executable-context assertions. Cargo's
stderr label for the next executable appeared before the preceding stdout result.
Revision 2 pairs the ordered stderr labels with ordered stdout libtest blocks and
still requires 47 labels, 47 explicit running counts, 47 results and all 793 named
executions. `parser-revision2-method.json` preserves the exact offending line
positions and hashes. Neither raw logs nor test predicates were weakened.
`review-root-logs.py` is the initial parser; it is not overwritten.

Reproduce the final review into a NEW output directory:

```sh
python3 /tmp/rustusb-diagnostic-ci-dff36a3-20261024/review-root-logs-revision2.py \
  --output /tmp/rustusb-diagnostic-ci-dff36a3-new-review
```

The script makes no network/API call, does not import a product or harness
verifier and does not execute Cargo, product CLI or QEMU. Its only subprocesses
read exact Git objects. The 15,650 distinct logged source paths (many are archived
evidence copies) match 390 distinct Git blobs at dff36a3. This inventory is not the
compilation input count. The actual 191 frozen inputs from the first full gate
match dff36a3 individually; that full gate actually ran in a dirty 88179f2 working
tree and is not relabelled a clean dff execution.

The official logs contain per-platform ELF hashes and runtime artifact hashes.
The CI binaries and ephemeral runner `/tmp` artifact bodies were not uploaded
here and are not reconstructed. The file-size failure facts include errno 27,
16 actual partial bytes and a stopped snapshot with both virtual devices fully
released. The fixed harness samples data-socket EOF after process wait; it does
not prove a timestamp for EOF before exit. No physical USB, real hardware, QEMU,
permanent filesystem-stall cancellation or complete Phase 3 claim follows.

Five original connector transport failures and the initial successful-status
metadata remain preserved. This reviewer authored the logging storage and service
diagnostic registry, plus a separate QEMU verifier. This is an independent method
cross-review of the actual official evidence, not independent implementation.
