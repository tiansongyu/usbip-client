# Deferred Phase 3 security evidence slice

Read-only recommendation retained after root selected logging as the next deliverable. This is a plan, not an executed test or current goal replacement.

Highest-value subsequent slice: actual remote authentication/device-authorization negatives plus cooperative full-agent stop, trusted credential/pin replacement, and restart. Native and USBFS each one IPv4 framed-v2 double-guest run are an initial bounded scope. Reuse actual configuration/public DER/pin/metadata collectors, Rootproof, HID/serial I/O and source/ELF guards; do not relabel old positive/blackhole matrices as new security coverage.

Software already exercises real rustls handshake failures over duplex; loopback TCP/TLS backend tests exercise direct forbidden IMPORT, key/BusID mismatch, malformed prelude and unchanged owner. Root-permission config tests use an explicitly substituted test trust context. QEMU currently validates positive production-loader permissions, nonbroker rejection, encrypted USB traffic and contention, but not every certificate/config negative.

Proposed new cases: missing/unknown-CA/expired/unmapped client credentials; limited-principal Catalog and direct forbidden serial IMPORT during idle and held states; malformed/mismatched key/BusID refusal before a new stub/claim; actual root/broker wrong-mode/symlink/writable-parent startup rejection. During refusals existing owner receipts remain unchanged and the allowed sibling continues real I/O. Existing server export/grants are expected and must not be mistaken for unauthorized new admissions.

Rotation contract: LoadedConfig::load loads all profile credentials at agent process startup. StopJob/StartJob alone does not reload them. Cooperatively stop the full agent, verify exact helper terminal/full baseline, replace trusted files, restart, reject the old pin and import with the new certificate for fresh real I/O. This proves restart-based pin withdrawal, not CRL/OCSP, hot reload, or retroactive live-session revocation.

Resumption is already explicitly disabled on both ends in tls.rs, with repeated actual-handshake Full assertions in tls/tests.rs. No new resumption RED was identified. Certificate revocation integration, broader authentication rate limiting, complete secret scanning, application retry/privileged-abuse QEMU and full G3 remain separate unfinished work.
