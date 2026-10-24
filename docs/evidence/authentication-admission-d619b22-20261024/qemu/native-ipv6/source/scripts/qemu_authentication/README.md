# Installed authentication refusals and startup pin retirement

Run from a clean committed checkout as an ordinary x86_64 host user:

```sh
python3 -B scripts/qemu_authentication/run.py --backend native --family ipv4 \
  --kernel 6.8.0-106-generic --evidence /tmp/auth-native-ipv4-NEW-CLEAN-SHA
```

Run `virtual`, `usbfs` and `native` separately with `ipv4` and `ipv6`; each needs a new evidence directory. The default performs a locked all-features release build. Dependencies and the isolated guest topology are shared with `scripts/qemu/run-secure-qemu.py`; OpenSSL is additionally copied and its actual ELF/libraries checked. Never run the generated PID1 on the host. No host USB operation is performed.

The [design](../../docs/research/secure-negative-qemu-design.md) defines two credential epochs within the same guests and helper instances, four explicit before/after refusal barriers, actual product discovery failures, restricted direct IMPORT and duplicate IMPORT using an independent TLS client, and actual HID/serial I/O after the hostile batches. Epoch two removes the old pin only after cooperative server stop, actual wait and complete original baseline restoration; the replacement certificate must then use the real product path. This is startup policy retirement, not hot revocation or a CRL/OCSP feature.

Capture public certificates, exact protected configurations, original channels, actual waits, kernel/helper observations, request/reply bytes, QMP/serial input and PCAP. Private keys stay solely in the temporary role-specific payload and are not archived. Raw-v1 is explicit; a mixed accepted/rejected handshake capture is not passed through the older all-positive packet classifier. Fast negative processes have fixed source/role/config and actual waits but no assumed per-executed-ELF samples.

The current fixture additionally reads the actual `server_security` foreground
control before/after all four hostile batches. Its verifier checks closed numeric
fields, coherent attempt accounting, generic handshake/protocol versus unmapped
leaf deltas, no invented raw-v1 framing and resolved attempts. These volatile
counters do not replace any existing owner, I/O, retirement or cleanup check.
Earlier reports belong to their original harness revisions and did not collect
this new interface.

No PASS is implied by this README. First failures remain failures; guest exit zero does not override the host verdict. Hardware, framed-v2-specific negative coverage, long-term/exhaustion behavior and complete G3 remain separate work.
