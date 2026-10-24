# Installed offline CRL verification

Use an ordinary x86_64 host, a clean committed tree, a fresh evidence path and
the normal locked all-feature build:

    python3 scripts/qemu_crl/run.py --backend native --family ipv4 --evidence /tmp/crl-NEW

Run native, virtual and usbfs separately for IPv4 and IPv6. The fixture composes
the original authentication suite without changing its assertions: two actual
server epochs, four original hostile batches, two real HID/serial I/O rounds,
old server wait, full original root proofs and driver/VHCI cleanup remain.
It never passes through or changes host USB.

Four added barriers run the actual UID2000 CLI with protected DER/configs.
A correctly pinned identity is always revoked; another remains pinned in both
epochs but is revoked only by the second CRL. The active CRL file changes before
the old server stops, while its loaded policy still allows that identity.
Only after original cooperative stop/wait and full baseline restoration does
the new server reject it. This is immutable-snapshot/restart behavior, not hot
revocation or a new-process resource-zero cleanup claim.

Client cases also check revoked server, bad CRL signature, expired/future lists,
malformed DER, overlapping issuer lists, explicit empty/null and a successful
authorized catalog. Preconnection loader failures and actual TLS failures are
separated by exact server-counter deltas; missing output is not authentication.
Original and added snapshots compare actual resource identities, not just counts.

Only public certificate/CRL DER and inspection are retained; CA/private keys stay
in temporary role-separated payloads. Fast new negative CLI children lack
individual live ELF samples; original long server/helper identities remain.
Generated shell is syntax checked and every raw original artifact/failed run is
retained. This fixture is raw-v1; framed CRL tests are actual local TLS/TCP tests,
not an installed framed rejection matrix. Full G3 and hardware remain separate.

The first IPv6 failure is retained: OpenSSL returned104 for an absent-ALPN probe
that still supplied an application request. The CRL extension now supplies a
saved empty input to that negotiation-only probe. Original exit1/no-reply and
all counter rules remain; an exact protocol-mismatch increment is also required.
There is no new104/timeout acceptance or claim of traced TCP-reset causality.
