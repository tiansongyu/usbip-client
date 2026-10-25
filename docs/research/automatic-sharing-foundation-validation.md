# Automatic sharing: authorization and rule input foundation

2026-10-25. The validation library is implemented at clean
`52fd127d7ca2e9cb34d20d9d4178917c231af711`. **At that revision, automatic sharing was not available
yet.** The subsequent [fixed-profile runtime](automatic-sharing-runtime-validation.md)
adds those paths at its own tested revision. This historical unit adds neither an agent startup option nor rule RPCs, persistence,
a scheduler or GUI rule controls. The complete [design](automatic-sharing-design.md)
and its remaining delivery gates still apply.

## Implemented boundary

[`service::automation`](../../crates/rustusb-service/src/automation.rs) provides
a root-managed `AutomationPolicy` and finite rule inputs. Policy construction
requires the existing protected `read_root_document` path; ordinary user JSON
cannot deserialize an authority object. There are no new filesystem trust rules,
dependencies or credential loaders. Policy metadata retain exact keys and grant
limits, without retaining TLS credentials or live resource handles.

The schema-1 policy is capped at 64 KiB and 64 grants. Each unique operator/profile
pair selects the **entire** loaded fixed device pool, 1..16 exact keys, allowed
trigger/rearm kinds and 1..4 rules. Duplicate selections are rejected before set
normalization. Missing/additional keys, wrong broker UID, credential/backend
inconsistency and unknown fields fail closed. LocalProfileReady applies to Native
or USBFS; RemoteDeviceAvailable requires Client. A virtual server has no local
physical-arrival trigger.

Every authorization rechecks current Read/Start/Stop, plus Discover for remote
conditions, exact explicit delegation, trigger/rearm and loaded pool binding.
Manual Start or implicit root Read/Audit grants no automation. A revoked operator
ACL can leave the startup policy parseable while rule authorization fails; this
does not require refusing unrelated service cleanup. No live policy reload is
implemented.

`RuleSpec` accepts a 1..64-byte UTF-8 name without controls/surrounding whitespace,
a typed condition, a 2..120-second settle period and explicit Once/NewObservation.
Canonical RuleId and checked positive ExpectedRevision reject malformed IDs,
zero and exhausted revision inputs. Create/Replace/SetEnabled/Delete are closed
struct variants. No user rule can supply a UID, endpoint, path, credential,
device selection or expression. CAS against committed state and mutation retry
identity are **not implemented** by these input types.

## Software evidence

The exact final commands, full output and 392 frozen source/build/harness input
hashes are in [the evidence directory](../evidence/automatic-sharing-foundation-software-20261025/summary.json).

| Gate at clean `52fd127d` | Passed | Failed / ignored |
| --- | ---: | ---: |
| `bash scripts/check.sh` | 978 | 0 / 0 |
| `bash scripts/check-gui.sh` | 11 | 0 / 0 |

The root gate includes required fmt, default/all-features clippy/tests/release
builds/rustdoc and the separate vendored nusb tests. Ten new unit tests execute
in each root feature configuration; one compile-fail doc test executes in each,
guarding against ordinary JSON constructing `AutomationPolicy`. These 22 extra
executions account for 956→978. The GUI gate retains its original 11 tests and
separate release build/rustdoc/JavaScript checks. Existing 34 GLib dependency
warnings remain visible; they were not suppressed. Source inputs, HEAD and clean
status remained identical through both actual gate processes; the outer handle
returned 0 before any documentation edits.

Documentation QA checked 10 files and 190 local link targets. All 392 frozen
inputs and three built ELFs still matched. The old config/store/model/RPC/runtime/
IPC/frontend and lockfiles were byte-identical to the prior head. The English
README, eight website files and four global `readme-site` skill files, including
its Codex symlink, were preserved. The evidence manifest includes the original
gate files, collection/check methods, actual CLI help and `qa.json`.

The tests use synthetic fixed-profile metadata and existing generated in-process
TLS fixtures. They cover complete pool/order binding, unsupported backend,
subset/extra/duplicate keys, changed UID/pool/credential kind, foreign/broker/root
identities, explicit root delegation, empty policy, revoked action ACLs, finite
schema/byte/count limits, strict mutation fields and rule names/IDs/revisions.
The public trusted reader rejects an ordinary temporary policy file and preserves
its bytes. Positive policy/schema tests use the private binding routine, rather
than claiming a new positive root-filesystem integration test.

Development history: the initial focused ten tests passed. The first focused
all-features clippy run failed on a test-only single-item clone-to-slice; using
`std::slice::from_ref` fixed it. Its output is retained in the session tool
history and the [work log](../work-log.md); the archived logs are the final clean
gates, not a reconstructed transcript of that failed run.

Final built ELF hashes, **not new QEMU results**:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| all-features CLI | 20,317,600 | `7417838a4db9ec1ec1376174b63b23f0d2eedd6645ff3eebd86120cac887c558` |
| helper | 7,525,544 | `475b2f97e8b4e31dd1af479193913c637d09dc8c3b1debd13442c0f88f6748f3` |
| GUI | 16,995,504 | `807d7e2c50e25a46ae1466fcd0324965be2b70aebf3c3f39c358d2e03219f175` |

## Remaining work and evidence limits

Manifest1, disk2, old Job/Session/Metrics and IPC1 are unchanged. The new module
has no production caller yet. A valid grant/spec proves neither nonstorage nor
freshness, availability, helper authority, admission or cleanup. No USB operation,
automatic trigger or QEMU/hardware acceptance was exercised in this unit. Earlier
QEMU results retain their own recorded source/ELF provenance; they do not validate
these newly built binaries.

Next integration must implement strict new journal migration and bounded rule
CRUD/CAS/retry, occurrence plus Start intent in one transaction, manual Stop
suppression, retained tombstones/unresolved ownership, actual observation/network
owner shutdown and finite scheduling. Then add shared frontend/GUI management,
QEMU trigger/duplicate/restart/storage/generation/real-I/O tests and Native/USBFS
dynamic-pool proofs. Package/systemd lifecycle, real hardware G1/G2, full G3/G5
and the original later phases remain open. The overall design is still draft;
this library does not complete automatic sharing.

## Publication receipt

Content `1b332c5d611d3db9a68c8f1e9da34af1e7668f46` was pushed normally to
the authorized SSH remote. The [software workflow](https://github.com/tiansongyu/usbip-client/actions/runs/40151207871)
and [GUI workflow](https://github.com/tiansongyu/usbip-client/actions/runs/40151207859)
completed successfully on Ubuntu 22.04/24.04, x86_64/aarch64. Each of the eight
jobs has successful actual architecture and full-gate steps. The
[Pages workflow](https://github.com/tiansongyu/usbip-client/actions/runs/40151207842)
also completed its check and deploy jobs successfully.

The [receipt](../evidence/automatic-sharing-foundation-publication-1b332c5d-20261025/receipt.json)
and manifest preserve 41 original API/HTTP/command/method files, 876,582 bytes,
including running and terminal responses separately. Seven canonical HTTPS
resources returned 200 with exact Git bytes; 392 inputs, three local ELFs, eight
website files and four global skill files/symlink remained intact. CI logs and
CI-built ELFs were not fetched; no per-platform test counts are inferred from
job status. Local 978+11 remains attributed to clean `52fd127d`. The final receipt
commit contains documentation/evidence only and is not relabeled as the tested
source or the content head of these CI runs.
