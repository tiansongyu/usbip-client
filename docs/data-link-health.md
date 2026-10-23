# Observe the USB/IP data link

Framed v2 measures responses on the same mutually authenticated TLS connection that carries a device's IMPORT and USB/IP traffic. Use `agent health` to inspect its latest probe, byte counters and terminal cause. A transport reply does not prove that a USB request completed or that a device has been released.

This page describes the current implementation and operating interface. Validation is in progress; it does not claim completed QEMU, hardware or full Phase 3 acceptance.

## Choose a transport version

Provision the dedicated Linux broker accounts, certificates, device ACLs and root helper using the [secure guide](secure-guide.md). For managed jobs, also install the fixed profiles and operator permissions from the [application service guide](application-service-guide.md).

| TLS configuration | Data connection | Health evidence |
| --- | --- | --- |
| `schema_version: 1`, without `data_link` | Raw USB/IP inside TLS; ALPN `rustusb-usbip/1` | `protocol: "raw_v1"`, `probe: "unavailable_raw_v1"`; RTT and transport statistics are `null` |
| `schema_version: 2`, with the required `data_link` object | One framed data stream per TLS connection; ALPN `rustusb-usbip/2` | Same-connection probe observations and bounded byte-progress statistics |

Both versions require TLS 1.3, mutual certificate verification, server-name verification and the existing principal/device ACLs. “Raw v1” here describes the secure stream format; it is separate from the legacy plaintext commands. A failed v2 connection does not downgrade to v1. Configure both endpoints for the same version.

The [configuration parser](../crates/rustusb-transport/src/config.rs) rejects unknown fields and missing v2 fields. To enable v2, change the **server and client TLS configuration files** to `schema_version: 2` and add this top-level object, retaining their existing identities, addresses, credentials and device rules:

```json
"data_link": {
  "protocol": "framed_v2",
  "interval_millis": 5000,
  "response_timeout_millis": 15000
}
```

This is a field to add to each complete JSON configuration, not a complete configuration file. The values match the production `ProbePolicy::default()` of a five-second interval and a fifteen-second pending-response timeout. Configuration files must supply both values explicitly; omitting them does not select defaults.

For a controlled environment needing a shorter observation window, the parser also accepts:

```json
"data_link": {
  "protocol": "framed_v2",
  "interval_millis": 1000,
  "response_timeout_millis": 3000
}
```

The interval must be 1,000–60,000 milliseconds. The timeout must be at least twice the interval and no greater than 300,000 milliseconds. Each endpoint applies its own policy. The interval is between completed probes; the timeout starts when a probe is enqueued, so scheduling and transport backpressure consume that budget. These rules come from the [probe policy and driver](../crates/rustusb-transport/src/link.rs).

Keep the agent manifest at `schema_version: 1`; its profiles still refer to the TLS files. Helper authorization is unchanged. Stop the owning broker cooperatively, wait for its cleanup, and restart it to load changes. Updating a file does not reconfigure a running connection. The same TLS files work with managed `client`, `native`, `virtual` and `usbfs` profiles, subject to their existing build features and device permissions. Foreground `secure-*` commands use the selected format too, but `agent health` lists sessions owned by the agent.

## Read health as an operator

The agent must already be running as its dedicated broker UID. Use your operator account with `read` permission for the requested profile. These examples use the manifest and profile names from the application service guide:

```sh
rustusb agent --config /etc/rustusb/agent.json health lab-keyboard
rustusb agent --config /etc/rustusb/agent.json sessions lab-keyboard
rustusb agent --config /etc/rustusb/agent.json health lab-server
```

The first command reports the client's import sessions. On the server, correlate entries with `role: "incoming"` to actual imported data connections. An `export` entry represents a published device, not one particular connection.

Commands print JSON without requiring `--json`. The envelope remains `schema_version: 1`, with `journal_healthy` and `response`. A health response uses `response.result: "session_health"`, `response.sessions` and `response.next_after`. Each session observation contains `session_id`, `instance_id`, `role`, `availability` and `link`. Use the same session ID in the ordinary sessions response to inspect device state, evidence and cleanup separately.

A page contains at most 16 observations within the existing bounded response size. When `response.next_after` is non-null, pass that exact session ID to obtain the next page:

```sh
rustusb agent --config /etc/rustusb/agent.json health lab-keyboard \
  --after <next_after-session-uuid>
```

Replace the angle-bracket placeholder; it is not a literal shell argument. Stop paging when `next_after` is `null`. The cursor selects IDs after the last returned ID within the same profile; it is not a permission token or a snapshot transaction.

Health reads use the existing profile `read` ACL and the requesting process's Unix peer UID. They grant no start/stop or helper capability. Authorized successful observations bypass the persistence actor, so a slow journal acknowledgement does not by itself block a live read. Denied or failed reads use the ordinary error/audit path. The [CLI](../crates/rustusb-cli/src/agent.rs), [RPC schema](../crates/rustusb-service/src/rpc.rs) and [IPC handling](../crates/rustusb-service/src/ipc.rs) define this interface.

## Interpret an observation

| `availability` | Meaning |
| --- | --- |
| `live` | A current session reporter supplied the observation. Check its phase and probe; this label does not mean the link or USB device is healthy. |
| `previous_epoch` | A retained observation from an earlier agent process. Its monotonic times and connection ID are historical. |
| `owner_ended` | No current observation owner remains for this session; the stored sample is historical. Inspect the session's cleanup evidence separately. |
| `not_observed` | A data-link observation has not been registered. `link` is `null`; there is no measured zero RTT. |
| `not_data_session` | An export registration, which has no single incoming data connection. `link` is `null`. |

For raw v1, the unavailable projection has `phase: "ended"`, `end: null`, no identity and no statistics. That fixed projection is **not** an assertion that the USB session ended. The [health DTO](../crates/rustusb-core/src/link_health.rs) and [session projection](../crates/rustusb-service/src/health.rs) keep these meanings separate.

For framed v2, inspect these fields inside `link`:

| Field | How to use it |
| --- | --- |
| `identity.connection`, `identity.tcp.local`, `identity.tcp.peer` | Identify the actual TCP connection. Qualify the process-local connection number with `instance_id` and `session_id`; it is not a globally unique or authenticated token. |
| `sample_sequence`, `elapsed_millis` | Local publication sequence and monotonic time since this connection was created. Compare samples from the same connection only. |
| `phase` | `starting`, `open`, `draining` or `ended`. A draining connection can retain buffered bytes or an open reverse direction. |
| `probe` | `awaiting_first_reply`, `pending`, `reply_observed`, `timed_out` or `stopped`. `reply_observed` describes the latest matched library response, not a permanent health guarantee. |
| `last_probe_sequence`, `last_probe_enqueued_millis` | Local probe number and enqueue time. The number is not an on-wire nonce, and enqueue time is not wire-transmit time. |
| `last_reply_sequence`, `last_reply_millis`, `last_round_trip_micros` | Latest matched reply and enqueue-to-library-reply elapsed time, including queueing and scheduling. RTT may decrease in a later sample. Null means no measured reply. |
| `stats.app_read_bytes`, `stats.app_write_bytes` | Application bytes delivered or accepted on this connection, including management and USB/IP headers. They exclude framing/TLS overhead and are not USB payload counts or remote-consumption acknowledgements. |
| `stats.receive_pending_bytes` | Received payload still awaiting local application consumption, including the pump and bounded pipe. It is not process memory usage. |
| `stats.send_capacity_blocked`, `stats.transport_write_blocked` | Current observed flow-control or underlying-write backpressure. Neither alone proves a broken device. |
| `end` | Typed driver termination, such as `local_stop`, `peer_eof`, `probe_timeout`, `local_data_stall`, `io`, `protocol` or `counter_overflow`. Null means no terminal cause has been recorded. |

The `last_app_read_millis` and `last_app_write_millis` values under `stats` are also connection-relative times, not wall-clock timestamps. Existing session `metrics` retain their previous post-IMPORT counting contract; link statistics cover the entire data connection and need not equal those counters.

## Respond to a timeout

A `probe_timeout` means the outstanding same-connection probe exceeded its configured deadline. The owner then stops network forwarding and follows its actual device/helper cleanup path. A `local_data_stall` instead means the transport connection ended but received bytes were not consumed within the local drain grace, which uses the configured response timeout. It must not be interpreted as a missing peer response.

Read both `health` and `sessions` after a failure. A terminal link and an operation error do not prove cleanup. Require the session's actual `cleanup: "verified"` or `"not_started"` evidence before treating it as released; `unconfirmed` remains unresolved. Accepted helper mutations or initialization that are blocked in their actual owner still require that owner to finish. Export preparation before a data connection exists retains its separate persistence-wait limitations.

Probes measure the transport endpoint on the device's data connection. A matching response does not prove USB enumeration, URB completion, physical-device responsiveness or driver restoration. Catalog discovery uses a separate authenticated connection and cannot stand in for an imported device's health. Restarting an agent creates a new epoch; it does not refresh an old RTT or resume an old connection.

## Preserve the agent journal when upgrading

The private agent `state.json` now uses disk schema 2 with `link_history`, associated with the original session and instance IDs. On opening a valid schema-1 journal, the new store strictly validates the old shape and atomically writes schema 2. Old data sessions receive an explicit raw-v1 unavailable projection; exports receive no link. This does not invent earlier probe measurements.

Schema 2 requires its fields, including explicit nullable health fields, and rejects unknown fields, invalid associations or contradictory samples. It is not automatically downgraded. An older agent that only understands schema 1 cannot use this upgraded journal. Keep the journal and use a compatible agent; do not delete it or restore stale records to bypass unresolved ownership. The agent manifest, public IPC envelope, existing Session/Metrics bodies and helper journals have their own versions and are not changed by this private upgrade.

Live probes update bounded in-memory observations. Normal durable transactions checkpoint the latest available sample, and orderly owner completion records the final observation. A crash can therefore leave an older checkpoint, never a guaranteed last probe. Historical samples remain historical after restart. The journal stays capped at 2 MiB; admission remains within its original 1 MiB budget and now reserves up to 2 KiB per health entry for later growth. Retention removes health entries with their sessions. These rules are implemented by the [store](../crates/rustusb-service/src/store.rs) and [runtime](../crates/rustusb-service/src/runtime.rs).

For implementation boundaries and the independent driver/cleanup contract, see the [data-link design](research/data-link-health-design.md). Use the repository's current validation reports for measured results; this operating guide does not replace those evidence records.
