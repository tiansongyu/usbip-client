# Export private diagnostics

`rustusb diagnostics` creates one bounded, private JSON document for troubleshooting. It observes prerequisites and optional application history; it does not change devices or stop a running USB job. Linux is required for checked artifact publication. This implementation has passed its software gates; actual CLI lifecycle and installed QEMU validation are still pending in this increment.

## Local capture

Build the CLI using the [Native guide](native-guide.md). As the ordinary user who will own the artifact, create a private directory under a protected home directory:

```bash
mkdir -m 700 "$HOME/rustusb-diagnostics"
rustusb diagnostics --output "$HOME/rustusb-diagnostics/local.json" --json
```

Use a fresh filename each time. The parent must already exist, belong to the caller and have mode0700. Its ancestors must be protected directories owned by root or the caller. Symlinks, writable ancestors and existing destinations are refused. Run the command without a process `--log-directory` writer.

The default capture observes fixed Native tools/modules and the helper diagnostic endpoint. A missing tool or unavailable helper is a collected observation, not proof that USB sharing is ready. The command does not scan raw devices, load modules or run USB management commands.

To include your own stored process logs, add `--logs-directory`:

```bash
rustusb diagnostics --output "$HOME/rustusb-diagnostics/with-logs.json" \
  --logs-directory "$HOME/rustusb-logs" --json
```

The existing [log-store access rules](logging.md) apply. Disk-only history has no live logger status; this alone does not make its history capture incomplete. Retention gaps, partial tails, corruption, lost records and limits are explicitly reported.

## One application profile

A build with `secure-transport` can use the administrator's protected public agent manifest. The calling UID needs both `Read` and `Audit` permission for the exact profile. This mode does not load the broker's private TLS credentials or open its journal directly.

```bash
rustusb diagnostics --output "$HOME/rustusb-diagnostics/agent.json" \
  --agent-config /etc/rustusb/agent.json --profile office --json
```

This mode and `--logs-directory` are mutually exclusive. The service pins one coherent **already committed** profile view, then exports its retained jobs, sessions, original attempts and audit events. It samples health and process logs independently; these samples can be newer than the committed view. A new health session is marked as absent from that view. Snapshot slots are finite and expire after30 seconds, so Busy or Expired produces an explicit partial result.

Audit scope includes the exact profile plus only this caller's unscoped denials, including when root exports. All returned identities are projected again by the exporter. Reading diagnostic metadata does not provide admission, device readiness or recovery proof.

## Results and cancellation

The document is at most4 MiB. It replaces identifiers with per-capture aliases and omits raw UIDs/PIDs, paths, addresses, serials, credentials, USB contents, tool output and arbitrary tracing messages. Original operation errors, cleanup outcomes and transport-end categories remain separate. Numeric byte counts are observations, not a performance benchmark.

Stdout returns one receipt after actual accepted work completes. Check both its section summary and `file` result:

| Result | Meaning |
| --- | --- |
| Complete capture, Published file, exit0 | All requested observations were collected and final publication was synchronized and checked. This does not establish USB compatibility or network security. |
| Partial capture, Published file, exit1 | A valid final document records missing or limited requested information. |
| Cancelled capture, Partial file, exit1 | A stop observed before accepting the write leaves a valid cancelled document under the generated `.partial` basename. |
| File failure, exit1 | Preserve the reported partial or possible final artifact. Written bytes and their SHA256 describe accepted writes; post-rename uncertainty does not imply that no file exists. |

SIGINT/SIGTERM stops new collection and waits for accepted probe, file and metadata work. A signal racing accepted final publication preserves its actual result. Native command timeouts start termination and explicitly wait for child reaping; this may exceed a local observation deadline. The60-second collection budget does not cancel permanently blocked OS filesystem calls. A full stdout consumer can fail delivery after file work has completed; inspect the selected filename before retrying.

The [design and limits](research/diagnostic-export-design.md) describe the publication sequence and independent committed view. Real hardware G1/G2, permanently blocked kernel operations and full Phase3 acceptance remain separate work.
