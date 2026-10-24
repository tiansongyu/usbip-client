# Service log integration: read-only preflight

Scope: design/source review only; no repository changes, Cargo, QEMU, or Git mutation. Source snapshot is recorded separately. Proposed contract: docs/research/rotating-logs-design.md.

## Required integration contracts

1. Reuse Manifest::authorize(kernel_uid, exact_profile, Action::Audit), not Action::Read. Root's existing Audit permission still requires a configured profile. No request-supplied path, actor UID, broad wildcard or helper log access. Authorize before cursor/history evaluation. The cursor is continuation only.
2. IPC must handle authorized ReadLogs outside the Runtime command queue for every outcome: page, disabled, Busy, invalid cursor or read failure. Only denied requests retain the existing bounded actor denial/audit path. Never hold Controls or Store mutex while awaiting a reader. Capture journal_healthy independently; log failure must not change it.
3. The logging reader owner must offer nonwaiting bounded admission: two actual workers plus two queued jobs. Cancelling the RPC's five-second wait or dropping a response receiver does not cancel a started filesystem operation or release its actual permit. A fifth request returns Busy promptly and cannot fill the other ordinary RPC slots awaiting a reader permit.
4. Agent filtering must select typed application audit projections: exact authorized profile, plus unscoped Denied entries for the caller's actual UID. Root may receive the explicitly allowed unscoped administrative observations. Arbitrary generic process records do not become visible merely by carrying a matching profile string. Zero visible results after a bounded scan still need a progress cursor.
5. Runtime::persist captures its previous committed sequence. After Store::commit(stored) actually succeeds, assign self.state=next and nonblocking-submit only retained new AuditEvents with sequence>previous_sequence. Do this before fallible refresh_health, so a committed transaction is not suppressed by a later registry error. Do not mirror before commit, replay the old audit ring at startup, or await logger capacity/filesystem. A failed commit emits no committed audit projection.
6. AuditEvent has original sequence, actor UID, optional profile/job/session, action/failure/state/evidence/cleanup; it has no original attempt ID or instance ID. Add the current committing application instance separately, preserving the original audit fields. Do not invent an attempt or relabel an old job's original instance during recovery.
7. Keep existing serve(config, stop) as the no-logging wrapper. A new serve_with_logging(config, stop, Option<LogMonitor>) can pass the nonowning monitor to IPC and Runtime before startup recovery commits. The CLI owns and shuts down the actual logger only after the service has joined USB owners and business records.

## Minimal meaningful checks for implementation

- Real UDS: Audit allowed vs Read-only denied; exact-profile separation; another UID cannot obtain unscoped denials; caller-provided cursor does not broaden scope.
- Hold the real Store commit gate and read previously written log records through IPC while the Runtime actor is stalled. Stop watches remain responsive. This does not assert that incomplete business work has finished.
- Hold actual reader work, fill two active/two queued slots, observe Busy on the fifth; disconnect/deadline does not free actual capacity until work returns.
- A committed transaction projects exactly its new audit sequence once; delayed/failed commit projects none before success. Startup old history does not become a new commit. Queue full/log I/O failure leaves business journal health and successful commit unchanged.
- Maximum valid page plus envelope fits existing 64 KiB frame; filtered empty scan advances; malformed cursor and disabled sink produce bounded explicit responses.

No intrinsic authorization blocker was found. Implementation depends on frozen typed LogMonitor admission/submit/filter/page APIs; the above are requirements to retain, not claims of passing new tests.
