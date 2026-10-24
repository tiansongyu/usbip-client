# Managed catalog responsiveness and ownership

The application service now runs remote catalog I/O outside its single session
supervisor. A silent remote TLS peer no longer prevents that supervisor from
answering unrelated commands. Two supervisor-owned catalog slots bound accepted
work, including requests whose local caller has gone away. Successful replies
still require the original durable discovery audit.

This is a Phase 3 fix, not full G3 acceptance. It does not implement remote
auto-sharing, a TUI, a GUI, or general crash recovery. The
[design](catalog-supervisor-design.md) records the ownership contract.

## Tested revision and software

All final local software and QEMU runs below use clean
`b6756265a4ae57ab8058b1f65133f2397af3f063`, on 2026-10-24. The
[archive manifest](../evidence/catalog-supervisor-b675626-20261024/manifest.json)
indexes original logs, copied sources, methods and raw guest artifacts. Its
[read-only review](../evidence/catalog-supervisor-b675626-20261024/review.json)
checks original bytes and Git blobs without importing the original runners or
verifiers; it is a same-author review, not an independent security audit.

| Local check | Actual result |
| --- | --- |
| Formatting and default/all-feature Clippy with warnings denied | Passed |
| Default workspace tests | 385 passed |
| All-feature workspace tests | 477 passed |
| Locked nusb vendor library tests | 20 passed |
| Default/all-feature release and rustdoc with warnings denied | Passed |
| Total test executions | 882; zero failed or ignored |

The [software summary](../evidence/catalog-supervisor-b675626-20261024/software-summary.json)
retains all ten exact commands and actual exit codes. Its selected 220 source,
manifest, toolchain, vendor and check-script inputs were copied before the gate,
unchanged afterwards, and independently matched to the tested Git revision.
This selection includes more vendor/reference files than earlier report scopes;
its count must not be used to reinterpret those earlier runs.

Five new test functions use real loopback sockets and the actual runtime, with
temporary-filesystem recording. Their subscenarios are included in the Cargo
counts, not extra USB test cases:

- A TCP peer receives a real TLS ClientHello and stays silent. An unrelated
  `Profiles` response arrives while the catalog is still waiting.
- Two silent peers fill both slots. Dropping the first response waiter does not
  free capacity; a third authorized read returns `state` without a connection.
  Unauthorized and unknown-profile reads return `permission` and retain two
  durable denials. Both external stop and command-channel EOF close the original
  sockets and join the supervisor while the peer sockets remain held.
- Real authenticated raw-v1 and framed-v2 catalogs finish their network owners.
  A real store mutex gate prevents the successful response until the original
  audit commit finishes; disk, store and memory then agree.
- An unhealthy journal starts no catalog worker or connection.
- A framed-v2 peer holds its reverse FIN open. Unrelated commands still work,
  and service stop joins the catalog owner before the fixture releases that
  remote gate. Peer driver termination is also observed and joined.

These five loopback tests do not contact the helper, import USB, load modules or
change host drivers.
The 200 ms regression observation is a test deadline, not a latency benchmark.

## Actual QEMU regression

| Existing managed Native fixture | Result | Actual QEMU exits |
| --- | --- | --- |
| IPv4 | Passed | Server 0, client 0 |
| IPv6 | Passed | Server 0, client 0 |

Both runs use `scripts/qemu/run-agent-qemu.py`, its normal locked all-feature
release build and two disposable guests per run. Each retains 212 frozen inputs,
all four source/binary/head/clean guards, four original live broker ELF receipts
and two original helper ELF receipts. The
[IPv4](../evidence/catalog-supervisor-b675626-20261024/agent-ipv4-summary.json)
and [IPv6](../evidence/catalog-supervisor-b675626-20261024/agent-ipv6-summary.json)
summaries preserve exact configuration, observations and artifact hashes.

The original fixture verifies authenticated discovery, three HID/FTDI I/O rounds,
targeted stop, client-agent SIGKILL and exact retained helper-proof coordination
after restart, then complete original cleanup and driver baselines. Its 264
assertions per pair are audit assertions, not USB case counts. Each run records
105 serial bytes in each direction. The fixture's existing encrypted external
capture checks remain in force. It uses raw-v1; the new framed-v2 congestion and
shutdown checks above are local loopback tests, not an installed QEMU matrix.

Both software and QEMU use these all-feature release bytes:

```text
rustusb        1c547bf24094bc89691978daa9ded1ab299b3ad30a27f714d5b03dc96c014688
rustusb-helper 0b68adf4a5d5e3339a05da41792956254fcdfb2e3df74749e7b463c0ae906296
```

The unchanged default CLI remains
`e85fa916acc9379f58598922c469092aba53726830809c382cbdd9f62e945295`.
The original four virtual-server INT/TERM ready/blocked-output process cases and
two backpressure cases also pass with the all-feature CLI. Signal children all
exit 0 without forced cleanup. Backpressure INT exits 0; its deliberately blocked
output deadline exits 1 and closes the original lease socket. That older fixture
does not provide per-child live ELF receipts; the command wrapper records the
selected binary before and after, without inventing missing observations.

## Retained failures and limits

The original-runtime regression failed with exit 101 after a real ClientHello,
the unanswered unrelated request, peer release and actual supervisor join.
This was `b60ce981` product code with a new test fixture, not a clean final build;
its whole pre-format test source and executed ELF were not separately frozen.
The raw failure stays a failure. A later development compile failed with test-only
E0502 borrow scopes; a narrower pinned-future scope fixed it. The first targeted
pass, four-test pass, final five-test pass and Clippy output all remain in the
[development history](../evidence/catalog-supervisor-b675626-20261024/development-history.json)
and compressed raw archive. The unavailable fixed-version Tokio documentation
request is recorded in the design; no successful online fetch is claimed.

Authorization still precedes capacity evaluation. Credentials and endpoints are
immutable loaded profiles, never RPC input. Workers have no USB/helper capability
and never write the business store. Lost callers cannot abort accepted network
owners or erase successful audit. Capacity or journal refusal uses the existing
`state` failure category; this protocol does not distinguish the two causes.

The supervisor still waits on real audit commits. A permanently blocked store or
libc resolver can delay process exit; dropping a network awaiter does not cancel
an already running resolver. Two slots are a resource bound, not fairness or a
sustained-attack guarantee. A catalog is an authenticated snapshot, not a device
lease or a guarantee that a later import succeeds. Hardware G1/G2, complete G3,
remaining Phase 2 limitations and Phases 4–7 retain their original status.

## Inspect or reproduce

Run the [normal complete software checks](../../scripts/check.sh), or the two
unchanged guest fixtures from a clean source tree and fresh evidence paths:

```sh
scripts/check.sh
python3 scripts/qemu/run-agent-qemu.py --family ipv4 --evidence /tmp/catalog-agent-ipv4-NEW
python3 scripts/qemu/run-agent-qemu.py --family ipv6 --evidence /tmp/catalog-agent-ipv6-NEW
```

The manifest's `raw-evidence.tar.gz` contains closed relative member names with
per-file SHA-256/lengths, including all original failures and the actual method
scripts. Inspect or extract into a fresh temporary directory. No ELF, private key
or host-device `.private` output is included. Publication is recorded separately;
these local results alone are not CI or live-site deployment evidence.

## Publication

Content `57c8750e4bdcced2a0144882cc6f94d353a34852` was pushed normally and the
remote main ref matched. The
[software workflow](https://github.com/tiansongyu/usbip-client/actions/runs/40105141484)
completed successfully on Ubuntu 22.04/24.04, both x86_64 and aarch64. The
[Pages workflow](https://github.com/tiansongyu/usbip-client/actions/runs/40105141472)
check and deploy jobs also succeeded. Six canonical HTTPS bodies returned 200
and exactly matched that content revision's Git blobs. The website files are
unchanged from `30fb57a7`; this turn performed no new browser/layout checks.

The [publication manifest](../evidence/catalog-supervisor-publication-57c8750-20261024/manifest.json)
retains the original primary run/job/step API responses, push receipt and HTTP
bodies/headers. CI conclusions do not substitute for fetched full logs, per-job
executed ELF receipts or a per-platform recount of 882; those were not collected.
Local 882-execution software and the two paired QEMU runs remain assigned to
`b6756265`. The final receipt changes only documentation/evidence and preserves
the tested product inputs and website bytes.
