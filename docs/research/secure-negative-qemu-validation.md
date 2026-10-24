# Installed authentication failures and startup credential retirement

2026-10-24. **Six paired QEMU runs passed at clean `25870c61b1c7dffb9c6ccb4bed8e1e3a6bc2856b`, with 12 guest exits 0.** Native, virtual and USBFS each run separately on IPv4 and IPv6. Each pair has two server credential epochs in the same guests, retained helper instances, four before/after refusal barriers and two actual USB I/O rounds. This is a raw-v1 security-validation increment, not completion of G3 or the Phase 0–7 project.

The [test contract](secure-negative-qemu-design.md) preserves design decisions and failed attempts. The [runner](../../scripts/qemu_authentication/README.md) explains reproduction; the [operator guide](../secure-guide.md) describes the actual startup-only credential model. This increment changes test infrastructure and documentation, not Rust product behavior or external dependencies.

## Verified matrix and identity

| Backend | Network | Paired runs | Credential epochs / actual I/O rounds | Guest exits |
| --- | --- | --- | --- | --- |
| Native | IPv4 | 1 | 2 / 2 | 0, 0 |
| Native | IPv6 | 1 | 2 / 2 | 0, 0 |
| Virtual HID / CDC ACM | IPv4 | 1 | 2 / 2 | 0, 0 |
| Virtual HID / CDC ACM | IPv6 | 1 | 2 / 2 | 0, 0 |
| USBFS HID / FTDI | IPv4 | 1 | 2 / 2 | 0, 0 |
| USBFS HID / FTDI | IPv6 | 1 | 2 / 2 | 0, 0 |

Every run performs its default `--workspace --all-features --release --locked` build. The same 201 selected harness inputs include all 192 selected software source/build inputs; source, executable, HEAD and clean-worktree guards remain true. These lists are not rustc dep-info or unique-test counts.

| Actual release executable in all six runs | SHA-256 |
| --- | --- |
| CLI | `9708a50ee836378cd64f8726dae36caded96d4fca39faae215aa30658ef49b48` |
| Helper | `7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322` |

Actual long-lived broker proc/executable receipts show UID2000 and original/replacement process identities. Helpers retain their original PID/start/UID0/executable across credential epochs. Virtual servers need no server helper; their existing client helper is matched instead. Fast negative CLI/OpenSSL invocations retain fixed `setpriv` role/config/source selection and actual exit records, without individual executed-ELF samples. They are not assigned stronger process observations than were collected.

## Refusals with real devices and original owners

Each run performs the following once before and once during active imports in **each** credential epoch:

| Request | Actual acceptance condition |
| --- | --- |
| Valid, CA-trusted but unmapped client leaf | CLI exit 1, no catalog; retained mapping-refusal I/O outcome |
| Expired client leaf | Exit 1, no catalog; leaf deliberately pinned, so pinning cannot bypass standard validity checking |
| Wrong client certificate purpose | Exit 1, no catalog; pinned leaf still rejected |
| Unknown client CA | Exit 1, no catalog despite the leaf being pinned |
| Wrong server name / wrong server trust anchor | Separate product-client exit 1, no catalog, fixed service authentication failure |
| No client certificate / no ALPN / unrelated ALPN | Three independent OpenSSL clients return actual failure and no application reply |
| Restricted principal directly IMPORTs the hidden serial key and its actual Bus ID | Exact standard 8-byte IMPORT reply, status **4**, through verified TLS; not merely a client-side catalog miss |
| Authorized duplicate IMPORT while both originals are active | Exact standard 8-byte IMPORT reply, status **2**, without a second owner |
| Old leaf after server restart with its exact DER pin removed | Product-client exit 1 before and during replacement-client imports |

Two additional restricted-principal catalog controls return only HID while the devices are free. Each paired run has **44 targeted negative-client exit records**, 264 across the six runs. These count selected client invocations, not all operating-system children, USB cases or additional software tests. OpenSSL may return 0 or 1 after a complete denial reply because TLS close handling is separate; the exact reply bytes and bounded real exit are both preserved. Handshake-only failures require empty application output and exit 1.

The host explicitly orders each before snapshot, client batch, after snapshot and continuation. Original client kernel/channel observations match across each batch. Native server export/stub journals and complete hardware/configuration observations remain identical across its before/after barrier; USBFS grant records and host observations likewise match; virtual lease/state/counters match. The subsequent actual device I/O uses the original active import receipts. The retained raw files and fixed implementation establish this tested behavior; no claim of tracing every transient kernel action is added.

The refusal set keeps actual errors. In particular, TLS 1.3 client handshake completion can precede server-side pin mapping, so an unmapped or retired leaf can end in missing `close_notify` or Linux errno104 rather than a clean EOF or client-side handshake error. The actual reset branch is not independently traced. Certificate failures retain fatal-alert categories or the service's fixed server-authentication projection. Arbitrary I/O failures are not accepted as interchangeable certificate errors.

## Same-guest credential retirement and cleanup

The first server policy includes old client A and replacement B. After A's first real I/O round, both original imports stop cooperatively, the original server receives SIGTERM and actually exits 0. Native/USBFS restore the complete original device driver/configuration/interface baseline; their original export/grant records are empty **before** the replacement server starts. This does not use a new process's empty state as proof of old cleanup.

The same guests and helper instances then start a new broker PID with A's exact leaf pin removed and B retained. A fails in both free and held stages. The product's actual B configuration must authenticate, import and perform the second I/O round, then complete cleanup. Each epoch captures real keyboard down/up events and 35 serial bytes each way. That is 70 bytes in each direction per pair; virtual CDC also checks its actual OUT echo. These are exact synthetic fixtures, not throughput or latency benchmarks.

All four original client channel IDs per pair are distinct; the original local generations disappear and all 16 VHCI ports are NULL after each epoch. Native also matches the original export registrations and USED stub receipts within each epoch, retires its stubs, and requires fresh export/stub identities after the real server restart. Final Native/USBFS cleanup matches the complete pre-test baseline. Operation errors, observed transport closes and actual cleanup retain separate meanings.

This is **cooperative startup pin retirement**. Changing a file does not revoke an already authenticated stream or mutate a running policy. No CRL/OCSP, hot reload, arbitrary FD revocation, old crash recovery or established-stream expiry behavior is inferred.

## Software and raw evidence

The complete software gate ran at clean **`3d7e126d50ac31b4dea7e77c7f4a7819a03e73f4`**: 343 default + 434 all-features + 20 vendor = **797 executions**, 47 result rows, zero failed or ignored. Formatting, both Clippy configurations, release builds, strict rustdoc and Native-only dependency isolation passed. All 192 selected inputs were unchanged and match the final matrix revision. These are a new actual execution of unchanged Rust sources, not 797 new unique tests or a relabelled earlier modified-worktree run.

The [initial software/fixture/failure archive](../evidence/authentication-software-and-first-qemu-failure-3d7e126-20261024/manifest.json) retains commands, outputs and source copies. OpenSSL creates disposable public test certificates with fixed negative purposes/expiry, while actual guest RNG/time checks remain enabled; no clock override is used. Public DER, inspection output, exact configurations and private-key metadata are retained. CA and endpoint private key bytes remain outside evidence and the opposite role's private payload.

The [final six-run archive](../evidence/authentication-qemu-final-25870c6-20261024/manifest.json) contains exact guest artifacts, source copies, generated init, actual identities/waits, wire requests/replies, input captures and PCAP. A separately written standard-library method by the harness author verifies 3,844 original files, independently decodes 2,342 guest artifact blocks, maps all 201 source inputs to the tested Git commit and preserves all original bytes. Its 7,042 predicates, and the original host-verifier predicates, are diagnostic checks rather than new test cases. This is source/raw-method separation, not an independent implementation audit.

Raw PCAP is saved without applying the earlier all-positive classifier to a mixture of accepted and rejected TLS handshakes. The older [six secure data-path scenarios](secure-usbip-validation.md), framed-v2 [health matrix](data-link-health-validation.md), [23 preparation-stop runs](prepare-recording-shutdown-validation.md) and [diagnostic export scenario](diagnostic-export-validation.md) retain their own revisions and executable scopes. No larger single-revision aggregate is claimed.

## Preserved failures

| Original clean run | Actual terminal result and correction |
| --- | --- |
| `3d7e126` Native IPv4 | Host FAIL, both guests 0; first unmapped-client exit1 was correct, but its missing-close-notify text was omitted from the harness assertion. Later stages unexecuted. [Original archive](../evidence/authentication-software-and-first-qemu-failure-3d7e126-20261024/manifest.json). |
| `176fee3` Native IPv4 | Host FAIL, both guests 0; four certificate cases reached expected rejection, then wrong-name's existing fixed service permission projection was omitted. [Original archive](../evidence/authentication-qemu-second-failure-176fee3-20261024/manifest.json). |
| `8acc90b` Native IPv4 | Host FAIL, both guests 0; first free batch completed and both imports were active, then unmapped-client errno104 differed from the earlier TLS EOF text. Only mapping-refusal cases gained the exact reset alternative. [Original archive](../evidence/authentication-qemu-third-failure-8acc90b-20261024/manifest.json). |
| `72cbc60` Native IPv4 | Both guests completed both credential/I/O epochs, but host FAIL compared decimal `004` with string `4`. A separate numeric-parser/raw-method pass did not rejudge that FAIL. [Original archive](../evidence/authentication-qemu-fourth-failure-72cbc60-20261024/manifest.json). |

All corrected acceptance runs use fresh directories at clean `25870c6`. No Rust product behavior, certificate verifier, privilege boundary or deadline was relaxed to make the tests pass.

Real hardware G1/G2, complete G3, framed-v2-specific negative tests, sustained authentication exhaustion, full production isolation/audit and PKI rotation/revocation, permanent kernel/filesystem blocking, broader crash recovery and later TUI/GUI/packaging/Windows phases remain unfinished.
