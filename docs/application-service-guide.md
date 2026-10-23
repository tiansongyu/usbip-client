# Run the shared application service

The experimental Linux `rustusb agent` owns USB/IP jobs independently of the CLI that starts or observes them. Operators select administrator-defined profiles; they cannot replace a profile's endpoint, TLS identity, device pool or helper policy. The library runs the actual client/server tasks in its process and retains their cleanup owners. This is the service entry point for future TUI and GUI clients.

Native agent ownership, targeted stop and root-proof/restart scenarios have software and paired IPv4/IPv6 QEMU evidence; the optional managed Rust runners currently have software coverage. See the [validation report](research/application-service-validation.md), [implementation contract](research/application-service-design.md) and [work log](work-log.md) for exact scope. Existing foreground `secure-attach` and `secure-server` commands remain separate adapters in this revision; consolidating their implementations is unfinished.

## Prerequisites

Use Linux and the repository's stable Rust toolchain. Install the root helper, explicitly authorize the test devices and provision both endpoints' TLS configurations as described in the [secure guide](secure-guide.md). The service runs as the same dedicated **nonzero broker UID** named in those configurations and the helper policy. Operators use their own accounts. The broker UID is the trust domain for the full helper pool: do not share it with operators; profile ACLs cannot isolate another process running under that same UID. Root has read/audit access; it does not implicitly gain start/stop permission.

Build all engines:

```sh
cargo build --workspace --release --all-features --locked
```

For Native-only serving/import, build the CLI with `--features secure-transport`; this does not pull in the Rust device engine or nusb. Virtual serving additionally needs `rust-backend`; the experimental USBFS engine needs `physical-nusb`.

## Install a public manifest

Create `/etc/rustusb/agent.json` as a root-owned regular file, typically `0644`, under protected root-owned ancestors. The manifest contains only public references; the broker loads private keys through the existing trusted TLS loader. Replace the example UIDs and paths with the actual deployment values:

```json
{
  "schema_version": 1,
  "broker_uid": 2000,
  "helper_socket": "/run/rustusb/control.sock",
  "control_directory": "/run/rustusb-agent",
  "data_directory": "/var/lib/rustusb-agent",
  "profiles": [
    {"id": "lab-server", "backend": "native", "config": "/etc/rustusb/server.json"},
    {"id": "lab-keyboard", "backend": "client", "config": "/etc/rustusb/client-keyboard.json"}
  ],
  "operators": [
    {
      "uid": 1000,
      "profiles": ["lab-server", "lab-keyboard"],
      "actions": ["read", "start", "stop", "discover", "audit"]
    }
  ]
}
```

The server and client profiles may instead live on separate endpoints, each with its own manifest, broker identity and helper policy. Allowed backends are `client`, `native`, `virtual` and `usbfs`; every key in a server configuration must match its backend. All profile configurations are validated before any listener or USB capability is acquired. There are at most 16 profiles and 64 operator rules, with exact profile/action allowlists and no wildcard.

An administrator creates two distinct directories under root-protected ancestors. The final control directory belongs to the broker and has mode `0711`; the final data directory belongs to the broker and has mode `0700`. For the example UID, on the intended test endpoint:

```sh
sudo install -d -o 2000 -g 2000 -m 0711 /run/rustusb-agent
sudo install -d -o 2000 -g 2000 -m 0700 /var/lib/rustusb-agent
```

The agent creates its fixed `control.sock` with mode `0666`. Each request is still authorized using the kernel's Unix peer UID. A socket connection alone grants no action. The client checks the protected parent chain, socket identity and actual server UID. Separate advisory locks prevent two service processes from owning the same endpoint or journal.

## Start and inspect jobs

Run the service as the configured broker account:

```sh
rustusb agent --config /etc/rustusb/agent.json run
```

As an authorized operator, list profiles and start a job:

```sh
rustusb agent --config /etc/rustusb/agent.json profiles
rustusb agent --config /etc/rustusb/agent.json start lab-keyboard
rustusb agent --config /etc/rustusb/agent.json jobs lab-keyboard
rustusb agent --config /etc/rustusb/agent.json sessions lab-keyboard
```

Start returns after the intent is durably recorded, with a job ID. It does not promise that device enumeration has finished. The request process can exit while the owned job continues. One live or unresolved job per profile is allowed. A server job can have multiple export sessions and multiple independent incoming IMPORT sessions.

Session states use the nine-state model. Client `Connected` requires the actual local VHCI enumeration receipt. Incoming server `Connected` means the complete local IMPORT reply was flushed; it does not prove remote kernel enumeration. The response includes the role and supporting evidence. Device availability and connection state are separate observations.

Jobs/sessions are paginated. Pass the returned `next_after` as `--after` to read the next page. Pages contain at most 16 entries and remain within the 64 KiB transport budget. Events and audit use monotonic integer cursors and explicitly report `oldest_available` and `gap` when retained history has expired.

## Retry a start safely

For an operation whose request identity must survive a client timeout, generate a UUID before the first call and retain it:

```sh
rustusb agent --config /etc/rustusb/agent.json start lab-keyboard \
  --request-id 11111111-1111-4111-8111-111111111111
```

Retry the **same** authorized operation with `--retry`:

```sh
rustusb agent --config /etc/rustusb/agent.json start lab-keyboard \
  --request-id 11111111-1111-4111-8111-111111111111 --retry
```

Within retained history, the original UID/profile/request ID returns its original job without starting another one. An unknown or expired **retry** returns `request_expired` and starts nothing. A call without `--retry` expresses a new start intent; do not use it to guess the outcome of an expired operation. Retention is bounded to 256 request records and can shorten when old completed jobs are pruned. IDs are references, not authorization tokens.

## Stop and verify completion

Request one connection's stop with its session ID:

```sh
rustusb agent --config /etc/rustusb/agent.json stop-session <session-uuid>
```

This supports imported/incoming connections, including a targeted incoming connection while another device continues. Native also supports stopping an individual export. For a Rust virtual/USBFS export pool, stop the owning job; individual export-slot removal is not implemented and returns `unsupported`.

```sh
rustusb agent --config /etc/rustusb/agent.json stop-job <job-uuid>
rustusb agent --config /etc/rustusb/agent.json sessions lab-keyboard
```

`stop_accepted` is a cooperative request. Actual completion is a terminal session with `cleanup` equal to `verified` or `not_started`. `unconfirmed`, `Error` or `Recovering` require investigation; they never make a device available. Operation failure remains separate from cleanup, so an EOF or I/O error can coexist with verified resource release.

SIGINT/SIGTERM stop the entire service cooperatively. The service continues processing owner facts while its actual task/lease tree finishes. Repeated signals do not abort that wait. SIGKILL and crashes do not provide a cleanup receipt.

## Observe and recover

```sh
rustusb agent --config /etc/rustusb/agent.json events lab-keyboard
rustusb agent --config /etc/rustusb/agent.json audit lab-keyboard
rustusb agent --config /etc/rustusb/agent.json discover lab-keyboard
```

The data directory contains a bounded, atomically replaced `state.json`: active jobs/sessions, minimal audit and retained request identities commit together before acknowledgement. New admission uses at most half the 2 MiB journal budget, reserving space for received capability IDs, final counters and terminal observations. Corruption or uncertain writes refuse new admission; existing owners still perform real cleanup. Preserve the directory when restarting the agent.

On restart, unresolved sessions retain their original instance/session/attempt IDs and enter `Recovering`. The agent performs read-only queries for the helper's exact retained terminal proofs. The same query also runs for an unconfirmed session whose job owner has already returned in the current epoch; a live owner is never retired by this observer. A proof must match kind, correlation, stable device key, broker UID and any already recorded resource ID. Missing, evicted, legacy or no-longer-authorized records remain unknown; a new process reporting zero live resources does not resolve an old session. The agent does not adopt old handles, grant itself reconciliation privileges or automatically resume storage writes. Root coordination remains through the existing helper's pending/reconcile commands.

An intent-only job with no admitted session has no possible helper acquisition under the runner contract and may finish without claiming any old device was released. A reserved attempt that was never sent, or a helper failure without a received handle/proof, remains conservatively unconfirmed. Virtual export sessions and incoming sessions of either Rust backend have no independent helper attempt. After a crash, the current service cannot retire those sessions using another export/grant's proof; root helper coordination does not automatically remove application records. Do not remove an unresolved profile from the manifest before coordination: records remain preserved, but the current profile-scoped RPC does not expose removed profiles; root can inspect the protected journal directly.

Minimal audit records contain fixed action/error categories, OS UIDs, profile/job/session IDs and role-specific state/cleanup evidence. They exclude raw USB content, serial numbers, private keys, certificate bodies and arbitrary internal error strings. The retained ring has at most 1,024 events and is not permanent log archival or log rotation. Audit reads may include unscoped permission denials for the requesting UID; root can read all such denials through an authorized profile query.

Counters are actual post-IMPORT USB/IP application bytes, including protocol headers and partial successful writes. They are neither USB payload bytes nor TLS wire bytes. Raw USB/IP v1 has no inserted heartbeat or measured RTT; those remain unavailable pending versioned data framing and independent validation.
