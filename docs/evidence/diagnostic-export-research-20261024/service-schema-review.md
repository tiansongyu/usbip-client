# Service diagnostic snapshot schema review

2026-10-24. Read-only review of `docs/research/diagnostic-export-design.md` at product baseline `46cd950affb0c949b164d0051be7bf70f365fac9`. Proposed interface; no code edited or compiled. Earlier preflight remains unchanged at `/tmp/rustusb-diagnostic-export-service-preflight-20261024/`.

The complete coherent single-profile design is implementable without a new business journal schema or a service/collector dependency cycle. The service owns authenticated metadata publication/pinning; the new optional-service collector depends on it, applies bundle aliases and owns local output/probes. Existing public Job/Session/AuditEvent types can be used inside the authorized local diagnostic page: the collector must project them into the separate closed artifact schema, not Serialize them directly as export content.

## Required lifetime clarification

A registry-entry limit alone is not a limit of four actually retained snapshots. Page can clone an Arc, release the registry lock, and still hold the old snapshot while concurrent End/expiry removes its entry and another Begin allocates a new one. Charge each pinned snapshot object until its **last actual Arc** drops (an owned four-slot semaphore permit is a suitable internal metadata quota; it grants no USB or TLS authority). Expiry removes access, not an in-flight reference's charge. Actual Begin must use nonwaiting try-admission and return Busy. Do not block the Runtime publication path on snapshot capacity.

The current published Arc is not a pinned-slot allocation. Sharing it from several snapshots is allowed but must not evade the per-UID/actual-slot contract. At most four pins plus current/publishing state clones and finite page encodings must be included in the documented memory account. No registry/control lock crosses await, copying, filtering, serialization or filesystem work.

## Exact proposed IPC additions

Keep RequestEnvelope/ResponseEnvelope schema1 and old variants unchanged. Requests:

```rust,ignore
BeginDiagnostic { profile: String }
DiagnosticPage {
    profile: String,
    snapshot: DiagnosticToken,
    kind: DiagnosticKind,
    after: u16,
}
EndDiagnostic { profile: String, snapshot: DiagnosticToken }
```

Public service module types (derive Clone/serde as appropriate; all structs/variants reject unknown fields):

```rust,ignore
pub struct DiagnosticToken {
    pub instance_id: InstanceId,
    pub number: NonZeroU64,
}
pub enum DiagnosticKind { Jobs, Sessions, Audit }
pub struct DiagnosticCounts {
    pub jobs: u16,
    pub sessions: u16,
    pub audit: u16,
}
pub struct DiagnosticHeader {
    pub snapshot: DiagnosticToken,
    pub backend: Backend,
    pub committed_sequence: u64,
    pub publication_age_millis: u64,
    pub counts: DiagnosticCounts,
    pub audit_oldest_retained_sequence: u64,
    pub expires_after_millis: u64, // fixed 30000 at successful Begin
}
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DiagnosticItems {
    Jobs { jobs: Vec<Job> },
    Sessions { sessions: Vec<Session> },
    Audit { events: Vec<AuditEvent> },
}
pub struct DiagnosticPage {
    pub snapshot: DiagnosticToken,
    pub committed_sequence: u64,
    pub after: u16,
    pub total: u16,
    pub next_after: Option<u16>, // required explicit null/value
    pub items: DiagnosticItems,
}
pub enum DiagnosticFailure {
    Busy, Expired, InvalidCursor, Unavailable, EncodingLimit,
}
```

Responses: `DiagnosticStarted { header }`, `DiagnosticPage { page }`, `DiagnosticEnded {}`, and `DiagnosticError { code }`. Naming is ready for root to freeze. `DiagnosticEnded {}` deliberately does not reveal whether a token existed or which UID owned it. Business `journal_healthy` remains in the outer envelope as its own current sample; it is not frozen with the commit. Snapshot publication failure returns Unavailable (or explicitly marked stale), never an empty successful view or WAL poison.

`after` means **number of already consumed rows in this snapshot's filtered, deterministic section**: 0 first page; `==total` is an empty terminal page; `>total` InvalidCursor. Jobs/sessions sort by their immutable UUIDs; audit sorts by original sequence. Each response returns <=16 complete rows and <=60000 encoded item payload bytes, with the complete envelope independently <=65536. A too-large individual row is EncodingLimit, not empty `next_after:null`. Returned next_after equals old offset + returned count only when more remain. Reject mismatched token instances and malformed NonZero/canonical ID values; no token rotation or retry can wrap the checked counter.

## Authorization/filter/idempotence details

Begin, Page and End first check both Read and Audit on the currently configured exact profile using actual SO_PEERCRED UID. Only then evaluate token, cursor, capacity or publication status. Every authorized error uses the fast independent IPC response; denied requests retain the existing durable denial path. Tokens never act as capabilities to other profiles or UIDs.

For the root's stated `UID-own-denial` contract, use this exact audit predicate:

```text
entry.profile == selected_profile
OR (entry.profile is None AND entry.action == Denied AND entry.actor_uid == caller_uid)
```

This applies to root too: selecting a profile is not an implicit all-profile/unscoped administrative dump. If root instead wants the old Audit root-admin unscoped rule, document that choice explicitly; do not silently borrow it from a different read API. Header audit count is the **filtered** retained count. The oldest retained sequence is the global ring floor and must be labeled as such; it cannot imply older profile events never existed.

End is idempotent and metadata-only. An exact current token bound to this UID/profile is removed; unknown, expired or foreign tokens all produce the same empty successful End without touching another slot. Page uses one uniform Expired outcome for those cases, avoiding a token-existence oracle. End never cancels an already copied page or a log reader. A local collector may best-effort End even after cancellation; failure to deliver End only leaves bounded metadata until expiry.

## Publication/ownership details

Runtime retains its current State. After actual successful Store commit and `self.state = next`, preserve the existing committed audit mirror; clone the immutable bounded State outside registry locks and publish by short replacement. A failed commit must not publish its candidate. A publication/poison problem after a successful commit must not return a fake persistence failure or suppress committed mirrors. Publish initialization before listener bind.

If publication uses try_lock and can skip a committed version, retain a separately visible latest-commit sequence/failure so a later Begin cannot mislabel an old view current. The simpler viable approach is a short blocking metadata mutex whose holder never performs expensive work or awaits; on poison, diagnostic reads become Unavailable without poisoning business health. Root must select this explicitly in implementation.

Begin pins the existing committed Arc, not a new filesystem read or clone under the registry lock. Filtering/counting and page encoding happen after lock release. Health and logs remain independent subsequent observations. Do not load LoadedConfig or private TLS keys in an operator collector; use the existing protected public manifest/client endpoint path. No snapshot calls JobContext Begin/Attempt/Observe/Finish or helper Inspect to manufacture state.

## Focused fixtures before acceptance

- Four pins held, expired/Ended while a Page clone remains: fifth admission still Busy until actual snapshot charge drops; per-UID duplicate Begin cannot replace a live pin.
- Snapshot all retained profile jobs/sessions/audit through 16-row/byte-limited pages while actual commits advance: token, sequence, counts and values remain fixed, no mix or skip.
- A real stalled uncommitted transaction: snapshot returns previous committed facts independently; failed transaction never appears; postcommit publication error remains distinct from WAL health.
- Read-only/Audit-only/unknown profile/root unknown profile/cross UID/cross profile/old instance and counter overflow; no history/token inspection before authorization.
- Invalid/terminal offsets, all filtered-out history, empty audit ring, per-row limit and envelope limit; no silent complete-empty substitute.
- End twice, foreign End, expiry and disconnect; no device stop, new helper request, actual log-reader quota release or mutation of original records.

No remaining architectural blocker beyond these lifetime and exact filter/End decisions. This review does not claim any implementation or runtime PASS.
