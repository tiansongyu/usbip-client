# Preparation-recording shutdown validation

2026-10-24. **The revised implementation passed 667 local software test executions, four native CI platforms, and the final 23-run QEMU matrix with 46 guest exits of zero.** The final QEMU sequence is locked to clean `1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16`. The old-product failures and the first revised-product sampling failure remain separate failed runs; the latter sequence left 22 runs unexecuted.

This report covers cooperative stop while preparation or ready-state persistence is delayed, including local Native export cancellation. It implements the [preparation shutdown design](prepare-recording-shutdown-design.md) within the existing [application service](application-service-design.md) and [shared-owner](shared-owner-design.md) contracts. It does not complete Phase 3 or physical-device acceptance.

## What changed

An application recording acknowledgement can be delayed after the helper has already returned a real resource. Stop now reaches the actual owner independently of that acknowledgement. Cleanup and durable completion remain separate facts: resource release does not fabricate a successful record, and an unfinished record can still prevent final job completion or process exit.

| Owner or boundary | Implemented behavior |
| --- | --- |
| Service-wide shutdown | Independent sticky stop fanout reaches current controls. Jobs and sessions registered later inherit shutdown; sessions also inherit their parent job's stop. A poisoned control lock remains an explicit error after fanout and retained-task collection. |
| Native preparation | Each received export gets one actual-close watcher before Resource recording. Previously received exports remain reachable while a later Begin, Attempt, Resource or Available acknowledgement waits. Actual closes precede record finalization. |
| Rust/USBFS preparation | Received source FDs or the initialized backend are closed, and every received grant's Finish is attempted, before retained application records are awaited. A bound server is shut down through its actual owner. |
| Client after remote IMPORT | Stop closes and joins the authenticated network path before waiting for the original Attempt acknowledgement. A late acknowledgement cannot start new local helper work after stop or stop-sender disappearance. |
| Original record order | Interrupted waits retain the same recording future. It settles before that session's Closing and Finish records. Operation, cleanup and recording errors remain distinct. |

Already-started helper exchanges and USBFS initializers remain owned and are awaited to their actual result. A subsequent stop prevents later bind or work; cancelling the waiter is not used as cleanup evidence. Read-only helper Status retains its earlier cancellable startup behavior.

Foreground output acknowledgement still means successful output delivery, not durable admission. Managed execution does not fall back to untracked helper operations or an in-memory acknowledgement. This slice adds no privileged recovery RPC or production fault-injection option.

### Native local cancellation and error preservation

A known received-key mismatch is captured before Resource recording. Stop cannot erase its operation error or its eventual session failure, including when the original record completes with a separate error.

Local export cancellation is distinct from job stop and real resource failure. Only the selected export closes; safe siblings remain available. Catalog and IMPORT check the local stop directly. When all exports are individually stopped, the same job and listener remain alive with an empty authenticated catalog. A real cleanup failure or recording failure still fails the job.

An already reserved but unsent helper attempt remains conservatively Unconfirmed. It is neither relabelled NotStarted nor given a fabricated root proof, and no new helper operation is dispatched merely to obtain a terminal. Its bounded private unresolved result is reported at final job cleanup without becoming authority to close an unrelated safe sibling.

## Software and version attribution

The complete `bash -x scripts/check.sh` run executed on **dirty `b1de03488474af74a4155232cda73a00e1e0f142`**. Its 167 selected inputs remained unchanged and individually match the Git blobs of:

- Product commit `8ec570ad160c59c89303a0f63ed9651696a70b71`.
- Initial merged revision `2222de87439f61e8a2e939e7dc15cd845d38aa4e`.
- Final QEMU revision `1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16`.
- CI revision `5ffdc6cfcc0e3c2de386121d0277d7daf0ed4e9f`.

This input reconciliation does not turn the actual modified-worktree run into a clean run. The complete gate passed formatting, default/all-features Clippy, tests, release builds, strict rustdoc and vendor tests: **280 default + 367 all-features + 20 vendor = 667 executions**, across 39 result rows, with zero failures or ignored tests. These are executions across configurations, not distinct USB cases.

The Native-only check and dependency tree also passed. That path includes the service and transport/h2 while excluding the Rust USB engine, nusb and rusb. The original four local signal cases and two output-backpressure cases passed with the same all-features CLI and stable input/binary guards:

| Artifact | SHA-256 |
| --- | --- |
| Revised CLI | `72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2` |
| Helper | `b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5` |

Those local signal/backpressure checks use ordinary-user loopback virtual devices. Ready cases expose the final resource snapshot; blocked-output cases establish process and connection closure without a final backend snapshot. They do not validate privileged USB cleanup.

The new software fixtures exercise real owned Unix endpoints, production sequencing functions, original `JobMessage` acknowledgements, and actual TCP/memory-DER mTLS. They cover retained records, multiple owners, local versus global stop, late registration, sender disappearance, late errors and reserved-unsent cancellation. A Native management fixture explicitly authorizes both device keys, then verifies stopped direct IMPORT status 4, live reserved status 2 and repeated empty catalogs in raw-v1 and framed-v2. It does not bypass the helper's root checks or claim kernel, trusted-file-loader or physical-device coverage.

The four native jobs in [CI run 40044605684](https://github.com/tiansongyu/usbip-client/actions/runs/40044605684) passed at exact `5ffdc6c`: Ubuntu 22.04 and 24.04 on x86_64 and aarch64. Separate parsing of the official logs confirmed **667 executions and 39 result rows per job**, with no failed or ignored tests, plus the original four signal and two backpressure scenarios. The five TLS lifecycle scenarios belong to an already-counted parent test and are not added to 667. Platform, compiler, executable and source receipts are retained in the [CI archive](../evidence/prepare-recording-ci-5ffdc6c-20261024/manifest.json). These ordinary-user CI results do not provide ARM USB or privileged QEMU coverage.

The earlier 655-execution candidate remains separately attributed. Source review subsequently found the interrupted identity-error and local-stop/sibling gaps; five new assertions actually failed against that candidate before the correction. Its green suite is not evidence for those later requirements. Development and candidate originals are retained in the [development archive](../evidence/prepare-recording-development-20261024/manifest.json) and [655-candidate archive](../evidence/prepare-recording-candidate-655-20261024/manifest.json).

## Preserved old-product QEMU failures

All runs in this section used the old CLI `bb6fb276c0cbfbb73c2cdb8af0f73d87045ac7235b4943cf096c0643d570814a`, with the helper hash above. They used disposable x86_64 guests and Linux `6.8.0-106-generic`. The application broker ran as UID2000, authorized operator commands as UID1000, and the helper as root.

| Clean revision | Actual run | Retained result |
| --- | --- | --- |
| `68cc3ad` | Native Resource / StopJob | FAIL: resources did not close while the original record was held. |
| `68cc3ad` | USBFS Resource / StopJob | FAIL: both received USBFS FDs and their pending root records remained held. |
| `b1de034` | Native Resource / StopSession | FAIL: the expected held-cleanup failure plus a separate missing server-stub completion observation. Four original verifier failures remain. |
| `b529f9e` | Native Resource / StopSession | FAIL: only the expected held-cleanup, latched outcome and server-result failures. |
| `b529f9e` | Native Available / StopSession | FAIL: the same three expected failures; the listener was already bound at this boundary. |

Each run completed artifact collection with two QEMU guest exits of zero and valid source, binary, HEAD and clean guards. Those exit codes do not change the failed lifecycle result.

The two `68cc3ad` runs held the actual second Resource transaction at the original `state.next` fsync syscall entry. The exact PID/starttime, TID, FD and inode remained correlated across the held observations. Native retained both exports; USBFS retained two actual character FDs mapped to their exact node/generation receipts. Neither produced a root terminal during the held window. Only after that same fsync resumed and returned zero did original cleanup, exact correlated root proofs and the full original driver/configuration/alternate baselines complete. A new epoch then performed actual KEY1 down/up and 35 bytes in each FTDI direction. That successful reuse did not erase either original FAIL. See the [old global-gate evidence](../evidence/prepare-recording-red-68cc3ad-20261024/manifest.json).

The local cases select the original serial export as the StopSession target and preserve the original HID sibling. Resource has no listener yet; Available follows both Resource acknowledgements and the HID Available acknowledgement, with an actual bound listener and successful TCP connection. A TCP connection at that stage is not itself a TLS handshake or IMPORT.

In both `b529f9e` runs the target still failed to close under the original record gate. After releasing that exact syscall, the same original job continued with the original HID capability, an authenticated HID-only catalog and actual KEY1 I/O. An ordinary serial start was refused with Permission, NotStarted and zero helper attempts; its root store was unchanged. This is a catalog-selection refusal, not a stale direct-IMPORT QEMU test.

The StopSession callers did not receive successful durable replies during the held phase: Resource reported a timeout, while Available reported unexpected EOF; both exited 1. Their later exact UID1000 stop audit and the controlled operation sequence are retained. The differing caller errors are not assigned an unverified common cause.

### Client detach and server stub retirement are separate

The first local run at `b1de034` sampled the server immediately after the client reported its own import cleanup. The client had a Native import-release proof and NULL VHCI ports, but the server root journal still held the original observed stub and only the serial-export terminal. The same-phase host sysfs already showed AVAILABLE, with HID `devnum=0` rather than its original `2`. The stored prior stub receipt's USED value was not a current sysfs observation.

Only the later last-export stop was followed by all three server terminals. Those later proofs cannot be backfilled into the earlier detach phase. The originals establish a missing server completion barrier and the sampled states; they do not establish a particular scheduling race or what that run would have done without its subsequent export stop.

The `b529f9e` harness adds a bounded, read-only server barrier before that last export stop. It requires the exact original stub terminal, the unchanged original HID export with no stub, and the complete expected sysfs identity/configuration/interface state. It preserves every poll. Timeout fails before issuing the last local export stop; later failure cleanup cannot supply an earlier successful sample.

Both actual `b529f9e` runs recorded an incomplete first sample and a complete second sample. The accepted originals contained the exact stub proof and original HID `devnum=2`. Resource accepted at guest uptime 21.17 and requested the last export stop at 21.18; Available accepted at 21.18 and requested stop at 21.19. These are trace-order observations, not performance guarantees or a counterfactual explanation of `b1de034`.

After normal HID import detach and individual cancellation of its export, the same job remained Running and the same listener returned a real authenticated empty catalog. Only then did whole-agent TERM occur. Fresh two-device capabilities in the next epoch performed HID and bidirectional 35-byte FTDI I/O, followed by complete baseline restoration. Exact retained server/client histories separate real attempts and root proofs from the serial no-start denial. These successful tails do not convert the initial held-phase failures into PASS.

## First revised-product matrix: preserved sampling failure

The first clean revised-product sequence at `2222de8` ran only Native Resource / StopJob and returned **FAIL**. Its CLI/helper bytes matched the 667-execution software artifacts, both guests exited zero, and all four guards were true. The remaining **22 runs were not executed** in that sequence.

The first guest observation combined an earlier saved root snapshot with a predicate that reread the later live journal. The saved snapshot contained two exports and no terminals, while the observation claimed exact terminal proof. The host verifier correctly rejected that inconsistency. Independent raw review traced the mismatch to the source's live-journal reread; the original run remains FAIL.

A separate second saved sample and the before-release originals did contain both exact original export terminals and complete restored baselines while the same fsync gate remained held. The original syscall subsequently returned zero, the owner joined, and fresh HID/FTDI reuse completed. These are useful narrow observations of the revised behavior, not acceptance of the failed overall run or any of its unexecuted companions.

The sampling correction evaluates the saved snapshot that accompanies each observation. It was then exercised by the fresh clean `1582f06` sequence below. No regenerated summary or substituted snapshot repairs the original failed evidence; the [saved-sample harness archive](../evidence/prepare-recording-harness-saved-samples-20261024/manifest.json) retains the correction and its checks.

## Final clean QEMU matrix

The replacement sequence at clean **`1582f06` completed all 23 runs with PASS**, and all **46 guest exits were zero**. Its 222 selected global inputs remained unchanged and matched the frozen commit. Every run used the exact CLI/helper bytes listed above; the separately reconciled 167 software inputs also match this revision. Per-run selectors and source manifests remain distinct from the global set. The [final archive](../evidence/prepare-recording-final-1582f06-20261024/manifest.json) contains commands, source/input maps, ELF receipts, original guest artifacts and the separate raw-review methods.

| Group | Actual runs | Observed scope |
| --- | --- | --- |
| Native Resource: StopJob and agent SIGTERM | 2 | Both exact original exports retired and full baselines returned while the original Resource fsync remained held; only then was that same syscall released and the owner joined. |
| USBFS Resource: StopJob and agent SIGTERM | 2 | Both actual received FDs and exact grant receipts were accounted for; their Finish proofs and original baselines completed before the held Resource fsync was released. |
| Accepted USBFS initializer: StopJob and agent SIGTERM | 2 | The original initialization syscall and both received capabilities remained owned during stop. After the exact syscall returned, initialization joined and both grants completed Finish without starting a new listener. |
| Native Resource and Available: StopSession | 2 | Only the selected serial export closed under the record gate. The original HID sibling remained, later performed real I/O, and its individual stop left the same job/listener with an authenticated empty catalog. |
| Framed-v2 data-link health: Native, USBFS and virtual, each on IPv4/IPv6 | 6 | Three directional faults per run, 18 in total, with same-connection probe/timeout evidence, continuing serial or CDC I/O, fresh HID capabilities and real cleanup. |
| Existing foreground, agent and startup-signal regressions | 9 | Six raw-v1 foreground backend/address-family cases, two Native agent cases and the existing signal case, including real I/O, cleanup and reuse within their original scopes. |

### Preparation and local-stop observations

The Resource gates hold an actual application `state.next` fsync at entry. Saved root, host and FD snapshots now supply their own proof predicates. In the four Resource/global-stop runs, exact original resource retirement and full driver/configuration/alternate restoration occurred before the same gate was released. The original fsync then returned zero; pending records settled in order and owners were joined. The StopJob and SIGTERM paths are separately exercised, rather than inferred from each other.

The two initializer runs cover a different boundary. Five held samples retained both exact USBFS FDs/receipts and the full baseline. After release, the original ioctl returned `1`, the initializer completed, and both original grants obtained their exact Finish proofs. The frozen C probe classifies the operation as GET_CONFIGURATION; raw receipts independently identify the ioctl request, node, TID and FD. This validates ownership across an accepted initialization call stopped at syscall entry. It does **not** demonstrate cancellation inside an active kernel operation, interface claiming or a pending URB.

In the two local-stop runs, the selected serial export retired while its original Resource or Available acknowledgement remained held. The untouched original HID export was correlated through its root receipt and later actual KEY1 I/O. After client detach, the read-only server barrier sampled the exact original stub's retirement and complete original HID identity before permitting the last export StopSession. The same original job/listener then served an actual empty mTLS catalog. The serial catalog-selection denial retained zero helper attempts and no invented root proof. Only a later global TERM stopped that job; a fresh epoch obtained two new capabilities and performed KEY1 plus 35-byte FTDI I/O in each direction, followed by complete restoration.

### Data-link and compatibility observations

The six health runs retained the same serial or CDC capability through all three fault phases and seven I/O rounds: 245 bytes per direction per run, with actual 35-byte I/O during each fault phase. Native and USBFS have corresponding host serial sink bytes; virtual uses real client CDC echo and does not claim injected HID keys. The faults bind to the actual session/instance/TCP tuple after a matched reply, and each phase has at least one observed probe timeout. The review found 35 timeout samples at 3000–3002 ms and one passive I/O termination; those observations are not a universal timing bound or USB-completion measurement. Four successive HID capabilities/generations, continuing catalog operations and the appropriate final root or Rust-owner cleanup were separately checked. A virtual server has no physical grant or physical-server root proof.

The nine existing regression runs passed a separate raw lifecycle review with 18 guest exits of zero. It decoded original framed artifacts, matched frozen Git blobs and actual guest ELF receipts, checked I/O and lifecycle correlation, and confirmed all 2,594 inventoried originals remained byte-identical. Private application disk2, managed Native helper disk2 and untracked foreground helper disk1 were checked as separate formats. Raw-v1 health remains unavailable. The USBFS foreground collector has only its final physical journal, so this review does not invent intermediate immutable grant snapshots. The historical signal scenario still observes queued read-only Status startup, and the agent KILL case occurs after tty closure; neither supplies a pending-URB or kernel-active initialization claim.

Three separate raw-review methods passed for the eight preparation cases, six health cases and nine existing regressions. They do not call the original harness verifiers to obtain their independent-method conclusions. Reviewers participated in product or harness work, so this is method cross-review rather than a claim of wholly independent implementation. Initial reviewer parser mistakes and corrected reports remain retained. Diagnostic predicate counts are not additional software executions or USB cases.

## Evidence bundles

| Bundle | Scope |
| --- | --- |
| [Final software](../evidence/prepare-recording-final-software-20261024/manifest.json) | Actual dirty-worktree 667 gate, source reconciliation, Native-only check and same-ELF local signal/backpressure evidence. |
| [Local baselines](../evidence/prepare-recording-local-baseline-20261024/manifest.json) | Original local `b1de034` failure, two `b529f9e` failures, detach barrier change and separate raw reviews. |
| [Initial revised-product sampling failure](../evidence/prepare-recording-sampling-failure-2222de8-20261024/manifest.json) | First `2222de8` run, saved/live mismatch diagnosis, original FAIL and 22 unexecuted entries. |
| [Saved-sample harness correction](../evidence/prepare-recording-harness-saved-samples-20261024/manifest.json) | Saved-input binding change and its source/fixture review. |
| [Final clean QEMU matrix](../evidence/prepare-recording-final-1582f06-20261024/manifest.json) | All 23 runs, source maps, original artifacts, ELF receipts and three raw-review methods. |
| [Exact-revision CI](../evidence/prepare-recording-ci-5ffdc6c-20261024/manifest.json) | Four official native-platform job logs, source/toolchain/executable receipts and separate log parsing. |

## Remaining limits

- The guest gate stops at syscall entry. It does not reproduce a kernel-internal active stall or make a hard-blocked syscall cancellable.
- Cleanup before a delayed application record does not guarantee bounded process exit. Accepted helper work, initialization, the original record and later durable finalization are still retained and joined.
- Root, sysfs and application files are sequential read snapshots, not one atomic cross-filesystem transaction. A predicate must be bound to the exact saved input it describes.
- VHCI NULL is directly sampled. Original local-generation disappearance is supported by the exact root cleanup proof and frozen helper checks where no separate final sysfs-stat artifact was collected.
- QEMU emulated HID/FTDI and ordinary-user software tests do not establish physical hardware G1/G2, Windows USB support, or completion of all Phase 3 requirements. The broader project objective remains open.
