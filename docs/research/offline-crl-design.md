# Offline certificate revocation lists

2026-10-24 UTC / 2026-10-25 Asia/Shanghai. Baseline `e6177063`.
Status: implementing. This advances Phase 3 certificate revocation; it does not
complete G3, hot revocation, physical acceptance or later UI stages.

## Contract

Both trusted TLS configuration schemas gain optional
`certificate_revocation_lists`: 1..16 root-protected complete DER CRL paths when
present. Missing preserves existing behavior; explicit null/empty, duplicate
fields, bad paths, malformed lists and unsupported policies fail closed. Files
use the existing public credential checks (root-owned ancestors, regular file,
no symlinks/hardlinks, no writable group/other, identity before/after read).
Each list is at most 64 KiB, the entire set at most 256 KiB. There is no network
CRL/OCSP fetching, caller override or extra USB/helper capability.

The memory API is `TrustRoots::with_crls(self, Vec<Vec<u8>>) -> Result<Self>`.
It attaches one nonempty immutable set, rejects repeated configuration, and keeps
existing TLS constructor signatures. The default trust roots remain explicit;
no system roots, unauthenticated clients, TLS downgrade or insecure verifier.

Use pinned rustls 0.23.46 WebPkiClientVerifier and WebPkiServerVerifier with CRLs,
full-chain checks, denied unknown revocation status and enforced expiration.
Do not call `only_check_end_entity_revocation`, `allow_unknown_revocation_status`
or `allow_unauthenticated`. Existing leaf-pin principal/device ACL checks remain
after PKI, and existing client/server session resumption and early data stay off.
The trust anchor is outside the peer certification path checked for revocation.

## Freshness and ambiguous lists

Pinned rustls-webpki 0.103.16 ignores thisUpdate and chooses the first applicable
CRL, rather than merging lists or selecting the newest CRL number. Actual isolated
research handshakes reproduce both behaviors. Therefore merely enabling its CRL
option is insufficient for the contract.

Add pinned x509-parser 0.18.1 with default features disabled, for bounded metadata
parsing only. Signature, issuer authority, certificate chain/time/usage/name and
revocation decisions remain with rustls/webpki. Parse the entire DER object,
reject trailing input and malformed/duplicate extensions, require nextUpdate and
nonnegative `thisUpdate < nextUpdate`. Permit multiple distinct issuer DNs, but
reject repeated raw DER issuer names regardless of CRL number or key. Reject IDP,
delta/freshest-CRL policies and certificateIssuer entry extensions: the initial
supported policy is one direct complete list per issuer. No bespoke ASN.1 or
cryptographic implementation is introduced.

For all configured lists, compute the common window
`max(thisUpdate) <= now < min(nextUpdate)`. Both TLS configs install a small
`rustls::time_provider::TimeProvider` wrapper around a checked actual SystemTime clock (pre-epoch/unavailable returns
None; no upstream panic-prone time conversion).
It returns no time outside that window, so the standard verifiers fail closed
at each new certificate verification, including after clock rollback. Startup
rejects a set with no currently valid common window before listener/device use.
An unused list expiring also blocks new handshakes: this conservative whole-set
rule is deliberate. Certificate verification just before expiry can complete its
handshake afterwards. Existing established streams are not revalidated or killed.

Loaded CRLs are snapshots. Updating files requires cooperative stop/actual cleanup
and restart of the owning service; changing a file cannot affect old configs,
clones or active connections. There is no persistent CRL-number anti-rollback
database. An administrator can load an older still-valid list; distribution,
renewal before nextUpdate and trusted wall-clock operation remain their duties.
Omitting CRLs retains the prior PKI/pin policy, not a revocation guarantee.

## Source basis and integration

Three independent read-only research tasks inspect the pinned verifier, rcgen
fixture and metadata-parser components. Reports/probes are under separate fresh
`/tmp/rustusb-crl-*-research-20261024` roots; archive only reviewed sources/output,
never private keys. The writing-design skill calls for independent component
research; project instructions keep the findings in existing research/skill
conventions rather than generating unrelated component skills.

- [rustls client verifier](https://docs.rs/rustls/0.23.46/rustls/server/struct.ClientCertVerifierBuilder.html)
  and [server verifier](https://docs.rs/rustls/0.23.46/rustls/client/struct.ServerCertVerifierBuilder.html):
  exact local `webpki/client_verifier.rs`, `server_verifier.rs` and
  `verify.rs`; expiration defaults to Ignore unless explicitly enabled.
- [TimeProvider](https://docs.rs/rustls/0.23.46/rustls/time_provider/trait.TimeProvider.html):
  local `time_provider.rs`, TLS13 client/server certificate verification and
  `config.current_time()` turn None into an actual TLS error.
- [x509-parser CRL](https://docs.rs/x509-parser/0.18.1/x509_parser/revocation_list/struct.CertificateRevocationList.html):
  `FromDer`, issuer raw DER, last/next update and duplicate-extension checks.
  Metadata parsing does not authenticate a list.
- [rcgen CRL](https://docs.rs/rcgen/0.14.10/rcgen/struct.CertificateRevocationListParams.html):
  existing dev-only ring configuration can issue complete signed DER lists with
  explicit certificate serials; no product key-generation dependency is added.
- [RFC 5280, sections 5 and 6.3](https://www.rfc-editor.org/rfc/rfc5280.html):
  CRL freshness, issuer, distribution scopes and validation. This implementation
  supports a restricted complete-list policy, not every PKIX distribution mode.

Official versioned docs.rs pages were inaccessible in the root web tool; exact
locked local source and actual isolated probes supply version-specific evidence.
The metadata parser was locked but not active in the prior workspace; making it
a normal dependency requires inventory/license and Native-only feature checks.

## Verification required

First exercise actual memory TLS with fresh lists and unrevoked peers, revoked
client/server/intermediate, missing issuer lists, wrong issuer/signature, corrupt
DER and multiple issuer chains. A client-side TLS1.3 completion alone does not
prove the server accepted its certificate; assert server outcome and no peer
stream/principal on refusal. Preserve name/CA/pin/ALPN and quota behavior.

Use a deterministic test clock inside the real guarded provider to cross both
window boundaries after configuration, then perform actual TLS handshakes.
Check that an already-established connection continues I/O, while new ones fail;
reload/rollback and policy scope must not be inferred from process counters.
Trusted-file fixtures cover both schemas/roles, limits, explicit null/empty,
duplicates, permissions, symlinks, immutable loaded state and invalid CRLs.

Run the full normal default/all-features quality checks and locked vendor tests,
record exact clean source/build bytes, and test installed ordinary-user CLI in
paired QEMU. Preserve actual HID/serial I/O, old resource proofs/driver baselines,
real owner waits and IPv4/IPv6 encryption/isolation. Add CRL refusal/restart checks
without replacing original assertions. Every failed collector/run remains FAIL;
guest exit 0 does not repair missing evidence. Do not extend real-device support
or call G3 complete from this slice.
