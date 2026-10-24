# Store and read bounded process logs

RustUSB can write sanitized process logs to a private directory and read them as human text or bounded JSON pages. File logging is optional. The default remains stderr; `--log-json` selects JSON stderr and `RUST_LOG` selects tracing levels.

File logs are operational observations. They do not acknowledge USB admission, prove device release or replace the application's durable audit journal. The [design](research/rotating-logs-design.md) explains these boundaries.

## Start with a private local directory

Build the default Native CLI as described in the [README](../README.md). Create a dedicated directory owned by your user, with mode 0700 and protected root/user-owned ancestors. No component may be a symlink or writable by other users/groups.

```sh
install -d -m 0700 "$HOME/rustusb-logs"
./target/release/rustusb --log-directory "$HOME/rustusb-logs" devices
./target/release/rustusb --json logs --directory "$HOME/rustusb-logs"
```

`devices` is read-only. This example exercises local discovery/logging, not USB sharing. File records contain a discovery count rather than raw device descriptors or serial numbers. Explicit `devices --json` output remains a separate interface that can include serial numbers.

Use a separate dedicated directory for each concurrently running process. A second writer is refused. Log readers open existing files without creating a writer or changing retention.

## Configure rotation

```sh
rustusb --log-directory "$HOME/rustusb-logs" \
  --log-max-bytes 1048576 --log-retain 4 devices
```

`--log-max-bytes` is 4096..8388608 bytes per segment, including its header and complete record lines. Its default is 4 MiB. `--log-retain` is 2..16 segments, including the current segment; its default is eight. Both require `--log-directory`. The hard ceiling is 128 MiB of segment payload per store; filesystem metadata is additional.

Rotation happens before the next complete JSON line would exceed the byte limit. Restart starts a new process epoch and segment; it does not reuse an old process's identity. Retention removes only validated files belonging to that store. Age-based deletion, compression and secure erasure are not implemented.

The directory must already exist. Existing files must have the expected UID, exactly 0600 mode, regular type and one link. Unsafe, ambiguous or corrupt stores fail with an error. Lowering the byte limit below an existing segment's size is refused without rewriting history; preserve or archive the old store before choosing a smaller limit.

File mode sends process events to the asynchronous NDJSON file sink rather than stderr. It therefore conflicts with `--log-json`. It has no stderr tee that could stall file progress when a console reader stops. Final fixed error diagnostics can still appear on stderr after operation cleanup and logger shutdown.

## Enable logging for the application service

Build with `secure-transport` or the all-features workspace build. The administrator provisions a private directory for the existing dedicated broker UID. For example, with broker UID/GID 2000:

```sh
sudo install -d -o root -g root -m 0755 /var/log/rustusb
sudo install -d -o 2000 -g 2000 -m 0700 /var/log/rustusb/agent
```

Run as that configured broker account:

```sh
rustusb --log-directory /var/log/rustusb/agent \
  agent --config /etc/rustusb/agent.json run
```

The startup option selects an operational log destination. It does not override the root-managed manifest, profiles, TLS credentials, endpoints or device pool. Runtime RPC callers cannot select a log path, retention, level or writer.

An ordinary operator reads the permitted profile through the authenticated agent socket:

```sh
rustusb --json logs --agent-config /etc/rustusb/agent.json \
  --profile lab-keyboard --limit 16
rustusb logs --agent-config /etc/rustusb/agent.json \
  --profile lab-keyboard --follow
```

Use an actual configured profile. Each page requires its existing `audit` action; `read` alone is insufficient. The service returns that profile's typed audit mirrors and the requesting UID's unscoped denial events. A profile-looking field on a generic process diagnostic does not grant visibility. Root can also read unscoped typed administrative audit events, subject to the interface's profile selection rules. This agent interface does not return generic process diagnostics.

Successful audit commits are mirrored with their original sequence, IDs, action, state, failure and cleanup categories. Tracing levels do not suppress this separate typed mirror. The mirror can lose records; the protected `state.json` audit transaction remains authoritative. An agent without file logging returns a distinct disabled-log result rather than inventing an empty healthy store.

## Read pages and follow updates

```sh
rustusb --json logs --directory "$HOME/rustusb-logs" --limit 16
rustusb --json logs --directory "$HOME/rustusb-logs" --after '<returned-cursor>'
rustusb logs --directory "$HOME/rustusb-logs" --follow
```

Directory mode requires local filesystem ownership, or local root administration. Agent mode requires profile authorization. The modes are mutually exclusive. The logs command cannot also enable a process-file writer, even for another directory.

Each successful JSON line has `event: "logs"` and a `page`. Inspect `records`, `next_after`, `more`, `gap`, `partial_tail` and `status`. A page scans at most 256 KiB and returns at most 16 records. Filtering can produce an empty page with a progress cursor; that does not establish that the entire history is empty. Continue with `next_after` while `more` is true.

Cursors identify generated segment UUIDs and complete-line offsets. They are not filenames or authorization tokens. Retention or an incomplete old tail produces an explicit gap. A standalone directory reader cannot establish live-worker health: its `status` is unavailable. Agent pages can include the actual running logger's health and loss counters.

Follow polls finite pages and reports temporary Busy results. Ctrl-C/SIGTERM stops this observer, not the USB service or its jobs. A blocked stdout consumer has a bounded output deadline. A genuinely blocked filesystem read still has to finish; stopping an observer does not make that kernel operation cancellable.

## Helper logs and failures

An administrator can configure `rustusb-helper --log-directory /var/log/rustusb/helper` after provisioning a separate root-owned 0700 directory. Its records remain root-only. There is no broker RPC for reading arbitrary helper files. Local root can inspect this store using directory-mode `rustusb logs`.

Every stored record is capped at 2 KiB. The finite 256-record queue never waits for filesystem progress. File and reader workers hold no USB capability or mutation lock. Actual USB cleanup and required business records finish before the logger drains and joins its real workers.

Queue loss, invalid projections and actual write/rename/sync/identity errors are visible; the process does not silently reopen a failed sink as healthy. A runtime logging failure can produce a nonzero final exit **after the requested USB operation or cleanup completed**. Inspect the operation's response and authoritative sessions before retrying. A log error does not roll back a device mutation or change a session's cleanup proof.

Restart preserves an incomplete final line and reports a gap; it does not return that line as a valid event or fabricate a successful shutdown. Unexpected malformed full records/headers require investigation with the original bytes preserved. SIGKILL may lose queued records. Permanent filesystem/kernel blocking, or an unread final stderr diagnostic, can still delay process exit; neither substitutes for actual worker completion.

The persistent projection excludes raw tracing messages/errors, USB payload, HID events, descriptors, serials, certificates, pins, keys, arbitrary paths and subprocess stderr. Diagnostic records use closed codes and approved scalars; human output uses fixed templates. This does not turn logs into an immutable audit trail or claim that arbitrary legacy `RUST_LOG=trace` stderr is a sanitized export. Use [private diagnostic export](diagnostics.md) for a separate bounded document with per-capture identity aliases and explicit partial results. It cannot run with a process `--log-directory` writer.
