# Fixed nusb source and Linux extension

This directory contains every file from the published **nusb 0.2.8** crate,
whose `.cargo_vcs_info.json` identifies upstream commit
`c80218d342740321190436bc45304ebbbf16bef0` in
<https://github.com/kevinmehall/nusb>. The archive SHA256 is
`03fba503685f5c3e9f6a11ff9711c16086ab6beacaa6ad3df9e217a91a8d4c82`.
The original complete `LICENSE-MIT` and `LICENSE-APACHE` remain unchanged.
Upstream grants the choice `MIT OR Apache-2.0`; this optional dependency adds no
libusb C library. Original source, examples, hidden Cargo and workflow metadata,
and the source crate's own Cargo.lock are preserved.

`RUSTUSB-PROVENANCE.json` records the original and current SHA256 of all 70
archive files. `RUSTUSB.patch` is the exact difference against that archive.
The Cargo extraction marker `.cargo-ok` is not an archive file and is omitted.
Build products belong outside this directory.

The Linux additions reuse the existing usbfs URB submission, DISCARDURB,
REAPURB, buffer allocation, ownership, and event loop. They add no unsafe block,
pointer operation, dependency, or copied Linux implementation. One existing
`cancel` wrapper now returns the existing DISCARDURB syscall's accepted result;
its ioctl and pointer handling are unchanged. Existing portable APIs retain
their behavior, and ordinary submission resets flags when reusing an idle URB
previously used by the new raw API.

- `RawCompletion` retains the original negative Linux status, actual length,
  and IN bytes, including partial failures. OUT returns its real actual length
  and never echoes data. Control setup's eight bytes are excluded. An invalid
  kernel-reported length fails with overflow before the buffer is accessed.
- `Device` and `Interface` expose raw control requests; the owned handle has
  explicit cancellation and is still awaited for its terminal completion.
  Interface control retains the claim until reaping; a device-wide caller must
  retain all claims itself. Drop requests cancellation and retains kernel-owned
  memory until completion, but does not wait or certify cleanup.
- `Endpoint::submit_raw` accepts the original IN length, including 0, 1, and 15,
  with no MPS rounding or extra-data queue. The Linux usbfs implementation
  accepts these lengths; overflow remains a real failed USB completion. Buffers
  are bounded to 64KiB. SHORT_NOT_OK is IN-only; ZERO_PACKET is bulk-OUT-only.
  The physical adapter consumes host ownership/DMA bookkeeping as metadata and
  does not grant it local memory authority. NO_INTERRUPT is an optional host
  scheduling hint and does not suppress the required userspace completion.
- `poll_complete_raw` is cancellation-safe and reports an empty queue as a
  typed error. `cancel_one_raw` refuses any queue depth other than one and
  reports whether DISCARDURB accepted that particular request. Acceptance is
  separate from the later cancellation/completion race. The adapter keeps one
  physical request per endpoint and a bounded logical FIFO.
- `detach_and_claim_interface_no_reattach` retains the original atomic
  `DISCONNECT_CLAIM` and `EXCEPT_DRIVER usbfs` protection against stealing an
  existing userspace claim. It disables automatic reattachment so a whole-device
  owner can release all interfaces before restoring only the drivers it owned.
  Release/attach success still requires independent generation and driver
  verification; no Drop log is considered proof of restoration.

Compile-only prototype work is not USB interoperability evidence. The three
new raw control tests use initialized memory only; upstream's other eleven
library tests also do not open USB. Run them without creating vendor artifacts:

```sh
cargo test --manifest-path vendor/nusb/Cargo.toml --lib --features tokio \
  --target-dir /tmp/rustusb-nusb-vendor-tests
```

The RustUSB physical adapter and isolated QEMU usbfs tests provide the separate
request, cancellation, cleanup, driver, and actual I/O evidence. This extension
does not add ISO support or certify arbitrary physical hardware.
