# Server authentication observations: validation

2026-10-24. Product `d2d6564`; clean software and installed checkpoint
`c097cf8556b0b75f95918503daa819dfc2911831`. The
[design](server-security-observations-design.md) defines the scope and ownership.

The implementation exposes bounded, volatile TLS/framing/outer-budget counters
through the foreground `server_security` control and authorized
`agent server-security PROFILE`. Counts belong to the loaded TLS configuration;
clones and job restarts share them. The agent reports its actual instance ID.
They are not persisted audit records, device occupancy or cleanup proofs.

## Software and process checks

At clean `c097cf8`, the complete `bash -x scripts/check.sh` passed:

| Execution scope | Passed | Failed / ignored |
| --- | ---: | ---: |
| Default workspace | 348 | 0 / 0 |
| All-features workspace | 440 | 0 / 0 |
| Vendored nusb library | 20 | 0 / 0 |
| Total executions | 808 | 0 / 0 |

Formatting, both Clippy configurations, both release builds, both warning-strict
rustdoc builds and Native-only feature isolation also passed. All 194 selected
Rust/build inputs were copied before the run and remained identical afterwards.
This is a new execution count, separate from the older 797/793/723 reports.

Tests use real memory-DER TLS and loopback TCP: successful local authentication,
generic failure, unmapped leaf, timeout, cancellation, sixteen held permits and
an actual refused seventeenth attempt. Metadata survives configuration drop
without retaining the TLS budget. Separate framed setup tests cover success,
cancellation, malformed input and the real five-second deadline. Saturation and
recovered poisoned metadata remain explicit.

Real foreground controls observe sixteen stalled TCP handshakes and an outer
listener refusal, then retain totals across two job starts of the same loaded
configuration. Raw-v1 cancellation and framed-v2 deadline completion preserve
their existing shutdown behavior. Real Unix IPC uses the actual peer UID:
denials enter and commit the existing durable audit path; Read-only authorized
success and client-profile Unsupported complete while a real Store mutex holds
an already-polled commit. The authorized requests never enter the actor queue.
These software fixtures do not claim kernel USB ownership or an installed
managed-agent counter read under a QEMU WAL gate.

The same prebuilt CLI passed the existing four ordinary-user signal scenarios
and two output-backpressure scenarios. The latter fixture has no independent
executed-ELF or raw-wire capture for each child; its original bounds remain.

## Installed paired QEMU

Each command used a fresh directory, an ordinary x86_64 host, the actual Ubuntu
`6.8.0-106-generic` kernel and the default locked all-features release build:

```sh
python3 -B scripts/qemu_authentication/run.py --backend native --family ipv4 \
  --kernel 6.8.0-106-generic --evidence /tmp/NEW-EMPTY-DIRECTORY
```

The six independent combinations `native|virtual|usbfs` × `ipv4|ipv6` all passed
at clean `c097cf8`: twelve guest exits zero, all source/ELF/HEAD/clean guards true,
and the same 203 selected harness inputs containing the 194 software inputs.
Every pair retains two credential epochs, four original free/held barriers,
actual HID and serial I/O after the hostile batches, actual client cleanup,
server cooperative stop/wait, full original driver baseline and startup pin
retirement. The [original authentication report](secure-negative-qemu-validation.md)
and its four preserved host FAILs retain their original revisions and verdicts.

New snapshots before and after each of the 24 controlled batches show:

| Local TLS delta per batch | Epoch one | Epoch two |
| --- | ---: | ---: |
| Attempts | 11 | 12 |
| Authenticated | 2 | 2 |
| Generic handshake failure | 7 | 7 |
| Completed protocol mismatch | 1 | 1 |
| Unmapped leaf, including retired A in epoch two | 1 | 2 |

All sampled in-flight counts resolve to zero, with no timeout, cancellation,
quota refusal, saturation or inconsistent metadata in these batches. Raw-v1
framing counts remain zero. The new server's pre-batch epoch-two totals are zero;
the actual old server wait and restoration precede that restart. Across the
controlled barriers this is 276 TLS attempts: 228 local refusals and 48 locally
authenticated results. The 264 targeted negative-client wait files include
unauthorized/duplicate IMPORTs after successful TLS; they are not 264 TLS failures
or new USB/software test cases. Generic errors do not identify exact wire causes.

One additional unchanged `scripts/qemu_diagnostic_export/run.py` paired Native
IPv4/framed-v2 regression passed at the same clean checkpoint: two guest exits
zero and all four guards true. The original WAL syscall-entry gate, complete
private export, second actual HID/FTDI round before release, exact root terminal
proofs, VHCI NULL/Gone and full baseline remain required. It does not query the
new counter RPC and does not supply framed-v2 hostile-authentication coverage.
Thus this delivery has seven paired runs and fourteen successful guest exits;
the six refusal runs and the one diagnostic regression are separate scopes.

## Provenance and preserved limits

The all-features CLI SHA-256 is
`9b8299bed3ddb25b5d40f4f0ec61a91e23f91efaefcae646143d0cf75ab7512b`;
helper remains
`7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322`.
A separate same-author read-only method imports no original runner/verifier:
4,427 original files remain byte-identical, 2,673 base64 artifact blocks are
redecoded against saved files, all selected inputs match their exact Git blobs,
and actual counter deltas/reset are recalculated. This is provenance checking,
not an independent security audit, another execution or a PCAP verdict.

Raw software/process/source-review, seven QEMU runs and the recalculation method
are retained in [the evidence archive](../evidence/server-security-observations-c097cf8-20261024/manifest.json).
Private keys and build ELF bodies are not uploaded. Early dirty development
compile/borrow failures and the corrected three-second test expectation are
recorded in the design/work log; they were not frozen clean product failures.

No hardware G1/G2, complete G3, persistent security history, hot credential
revocation, long-term exhaustion qualification or general crash recovery is
claimed. Installed managed-agent counter reads and installed framed-v2 negative
counts remain untested; local real-IPC and framing tests are explicitly separate.
The full Phase 0–7 goal remains active.

## Documentation and site checks

English/Chinese README and the static showcase link this newer scope separately
from earlier diagnostic/authentication evidence. Thirty maintained sources pass
543 local or repository-mapped links and table checks; 56 external links were
not all fetched. Node syntax and whitespace checks pass. Six local project-prefix
HTTP resources match their source bytes; the five runtime assets and all four
global `readme-site` skill sources remain unchanged.

Actual CUA checks at 1280×720 and 390×844 find the new evidence link, loaded images
and no horizontal overflow. ArrowRight/Home retain correct tab focus, selection
and URL. A temporary identical HTML variant omitting the single app.js tag keeps
commands and evidence readable; browser-wide JavaScript was not disabled.
Console warnings/errors are empty. Screenshot capture returned Unable to capture
screenshot, so no new pixel review is claimed. Reduced-motion and clipboard
behavior were not rerun; their source assets are unchanged. The owned preview
server exited zero and its original PID is absent. Publishing/CI is recorded
separately after a real push, not inferred from these local checks.


## Published revision and CI

Normal SSH push published `dfd2b83bc5e7d843b515c2fa187f277603487e91`. Its
[four native CI jobs](https://github.com/tiansongyu/usbip-client/actions/runs/40077177969)
all completed successfully, including the actual native-architecture check. The
[Pages check/deployment](https://github.com/tiansongyu/usbip-client/actions/runs/40077177869)
also completed successfully. These are primary final API job/step statuses; full
CI logs and CI ELFs were not fetched or independently recounted. The 808 count
above remains the local clean-c097 execution. All 194 software inputs match dfd
byte-for-byte; the QEMU results remain locked to their actual clean c097 runs.

All six [public site](https://tiansongyu.github.io/usbip-client/) resources returned
HTTP 200 with exact dfd source bytes. CUA also observed the new evidence link,
loaded images and no horizontal overflow at the public desktop viewport. The
final documentation/publication receipt changes no software input or site
resource and is not an additional software, CI or QEMU acceptance run. Local
`check-site.py` separately passed 173 asset/link/SVG checks.

Final publication review passes 545 local/repository-mapped links across the same
30 maintained sources, with no table errors; 59 external links were not all
fetched. This later review preserves the earlier 543-link source snapshot.
