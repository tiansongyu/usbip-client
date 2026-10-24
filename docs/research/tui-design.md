# Ordinary-user terminal frontend

Status: implementation design, 2026-10-25. Scope: requirements 4.9 and a first
usable Phase 4 frontend, without declaring the remaining Phase 1–3 acceptance
gates complete.

## Readiness and boundaries

The service already owns real jobs, sessions, durable admission, bounded audit,
fixed-profile authorization and cooperative cleanup. Local and authenticated
remote observers are shared libraries, not CLI subprocess output. Their exact
evidence remains E31–E44 in the acceptance ledger. S1–S6 are release gates:
physical G1/G2, general crash recovery without independent proof, permanent
kernel/filesystem stalls and target installation evidence remain open. S6's
systemd installation evidence belongs to Phase 5. Developing this frontend does
not turn those missing results into passes. Online OCSP, hot CRL replacement and
CRL number antirollback are not additional prerequisites invented for this UI.

## Decisions

- A nonempty `rustusb-tui` crate and optional CLI `tui` feature. The default
  Native CLI dependency graph remains independent of terminal/TLS dependencies.
- Ratatui 0.30.2 and Crossterm 0.29.0, MIT, compatible with Rust 1.97. Official
  [Ratatui setup](https://ratatui.rs/tutorials/counter-app/basic-app/) and
  [Crossterm events](https://docs.rs/crossterm/0.29.0/crossterm/event/index.html),
  plus exact downloaded source and isolated probes, inform terminal ownership.
  Component research is performed independently as required by the design
  skill; project conventions keep its digest here, not in unrelated skills.
- Explicit fallible terminal initialization/restoration; do not call Ratatui's
  panic-on-error `init`. One blocking thread owns poll, read and draw. No network
  or sysfs scan blocks a Tokio worker or the terminal input loop.
- Six pages: Local, Remote, Connections, System, Logs and Settings. Stable row
  keys survive refresh/filter/sort; keyboard-only navigation, bounded search,
  visible freshness, details and default-cancel confirmation. Narrow terminals
  use a compact list and details overlay. State labels accompany colors.
- `ui-ux-pro-max` operations-dashboard search supports freshness, pause controls,
  keyboard focus and a dark slate palette. Its glass blur, landing-page CTAs,
  fonts, mobile pixels and browser animation recommendations do not fit a
  terminal and are not adopted. Use terminal font, cyan focus, text status and
  no animation, with a monochrome option.

## Service contract

The frontend reads only the public protected agent manifest. It never loads TLS
private keys, executes usbip, writes sysfs, starts an agent, or calls root helper
mutations. Every action uses existing `ipc::call` and independent real UID/profile
authorization. No RPC, disk schema or USB ownership change is needed.

Profile start always means the **whole fixed administrator configuration**;
selecting a local/remote row never changes its device keys. The confirmation
states this explicitly. Stop targets an exact returned job/session identity;
accepted stop is not release. A lost Start response retains its original request
UUID, offers only `RetryStart`, and blocks new start intents until that ambiguity
is resolved or the frontend exits. A retry expired result is not proof an old
operation never ran. UI exit leaves service jobs running.

RPC pages remain finite and explicitly partial (16 rows). Next/first controls
use the actual returned cursors; filtering/sorting cover only the displayed page.
Audit has separate Audit authorization, gaps and retained history. Job/session,
health and audit samples are independent, not a transactional global snapshot.
Previous state is visibly stale on read failure; permission failure clears it.

Local discovery owns one `DeviceMonitor`, consumes complete snapshots, and joins
the actual worker. Remote discovery starts only on the Remote page with a client
profile, respects Discover, and stops/joins before replacing its source. No
metadata subscription owns a device. Refresh pause affects frontend polling,
not service jobs or the local kernel observer. Settings initially manage this
invocation's refresh interval, polling and theme; persistent user preferences and
editing administrator policy remain separate unfinished work.

## Shutdown and verification

Register INT/TERM before manifest loading; join that accepted blocking load.
Terminal quit/error or external stop fans out a sticky stop. Finish any issued
mutating RPC (its bounded response can be unknown), stop and truly join both
metadata owners, and restore cursor/raw/alternate-screen independently, reporting
the first restoration failure. Do not abort owner tasks or impose fake cleanup
deadlines. Repeated signals cannot drop this cleanup. Permanent OS/terminal
writes may still block.

Untrusted display strings lose control/bidi characters and have finite length;
no raw string becomes a terminal command. Verify model transitions, confirmation
and unknown-start behavior, resize/Unicode rendering with actual TestBackend,
and real PTY startup/navigation/INT/TERM restoration. Installed QEMU evidence must
use ordinary UID, actual agent RPC and actual USB I/O; renderer fixtures alone
cannot prove redirection. Run the full workspace gate and record original
failures before meaningful commits and normal push.
