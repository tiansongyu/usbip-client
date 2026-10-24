# Linux hotplug notification research — 2026-10-24

**Recommendation:** use the existing locked rustix 1.1.5 safe netlink API as kernel event hints, not a device database. Bind before initial enumeration. Keep the actual sysfs scanner authoritative and add bounded periodic reconciliation, because events can be lost and driver/property updates can settle after an add event. No event grants a capability or proves cleanup. No new parser/socket crate is necessary.

## Fixed components and actual interface

- rustix 1.1.5 (2026-09-16), Rust 1.65 MSRV, MIT/Apache-2.0/LLVM exception alternatives, already locked checksum `891efababe418670775f199f0d233d84843c227a0949a883ce15b37c78d6629d`, cached VCS `287214b889865d8e1406a0ee71cc409b6f6191c8`. `net` enables `SocketAddrNetlink`; `event` enables safe `poll`. No libc/libudev development package needed for this route.
- `socket_with(AddressFamily::NETLINK, SocketType::DGRAM, SocketFlags::CLOEXEC | NONBLOCK, Some(netlink::KOBJECT_UEVENT)) -> Result<OwnedFd, Errno>`; `bind(&fd, &SocketAddrNetlink::new(0, 1))`. Zero local port requests automatic unique port assignment.
- `recvfrom(&fd, &mut [u8;8192], RecvFlags::DONTWAIT | TRUNC) -> Result<(usize,usize,Option<SocketAddrAny>),Errno>`: first length is initialized/stored bytes; second is full original datagram length. Original length above capacity discards the truncated event and requires rescan. Never slice with the larger length.
- Accept only the returned source address convertible to `SocketAddrNetlink` with `pid()==0 && groups()==1`, before reading payload. Reject other sources without executing payload paths. Fixed Linux v5.15/v6.8 sets this control-block source for kernel messages; userspace sender control-block port is assigned by the kernel, independent of payload. A privileged kernel relay of synthetic events requires target-netns `CAP_SYS_ADMIN`; it is still merely a hint. Ordinary-user receive is explicitly enabled by `NL_CFG_F_NONROOT_RECV`.
- Request SO_RCVBUF 65536 using `set_socket_recv_buffer_size` (not privileged FORCE), read actual value back, and enforce the application's accepted cap if necessary. Linux accounts/doubles socket buffer values; actual requested 64KiB was 128KiB. This is kernel socket accounting, not a guarantee of queue payload capacity.

## Event and ownership behavior

Process all `SUBSYSTEM=usb` actions, including usb_interface bind/unbind/change and future unknown actions; do not restrict to usb_device add/remove. Parse only bounded relevant ASCII byte fields, avoid retaining/logging arbitrary environment or raw paths/serials. Kernel header/properties are NUL-separated, not generic netlink framing. Sequence gaps can conservatively trigger resync, but are not exact loss counts: sequence is global and namespace delivery may filter events. ENOBUFS, truncation or malformed trusted packets must invalidate observation continuity; don't set NETLINK_NO_ENOBUFS. INTR retries; AGAIN ends a finite drain; other receive errors retain errno and mark source failure.

Tokio 1.54.1 `AsyncFd::new` is actually deprecated; replacements register/register_with_interest are unsafe. Avoid both legacy suppression and new core unsafe. Safe `rustix::event::poll(&mut [PollFd::new(&netlink,IN),PollFd::new(&wake,IN)], timeout)` in a retained dedicated blocking owner works. Nonblocking one-byte UnixStream wake plus sticky stop and finite poll timeout permit cooperative shutdown. Check wake first and cap packet draining per iteration; keep the actual worker/quota until it really exits. Blocking scan/kernel I/O still cannot be forcibly cancelled.

Generation is independent of the candidate fingerprint: preserve scanner dev/inode and current USB address/bus data, and treat reuse/known removal as a new observation generation. Fingerprint equality does not prove physical identity. Coalesced remove/add may leave an indistinguishable final snapshot; don't claim every transient is reconstructed or derive authorization from Bus ID/event path.

## Alternatives and observed evidence

Existing vendored nusb 0.2.8 watcher uses udev group2, filters add/remove usb_device, logs/drops receive errors and raw property values, and does not expose lost-event state. Its unchanged watcher is unsuitable for this authoritative service. kobject-uevent 0.2.0 (MIT, 2024-12-30) is only a parser: it allocates a complete HashMap, takes duplicate keys last-wins and has no source/truncation/resource policy. Extra netlink-sys 0.9.0/nlink 0.33.0 candidates were metadata-screened only, not audited; no claim of verified suitability.

Actual unprivileged x86_64 Linux Rust 1.97.1 probes: first legacy AsyncFd probe exit0 with deprecation warning retained. Separate forbid-unsafe safe-poll build/run exit0 without warnings, UID1000/group1, actual wake/thread join and original FD closure. Both observed zero datagrams. No synthetic send, device mutation, hotplug I/O test, ENOBUFS execution or performance qualification occurred. The 87us wake/join observation is a single local scheduling observation, not a latency guarantee. Crate query for `uevent` returned404, retained.

## Primary references and confidence

- [Linux v6.8 kobject_uevent.c](https://github.com/torvalds/linux/blob/v6.8/lib/kobject_uevent.c): alloc_uevent_skb, uevent_net_rcv_skb, uevent_net_init. v5.15 corresponding source also retained/checked.
- [Linux v6.8 af_netlink.c](https://github.com/torvalds/linux/blob/v6.8/net/netlink/af_netlink.c): bind/send/recv source ownership, truncation and error handling.
- [systemd v249 monitor](https://github.com/systemd/systemd/blob/v249/src/libsystemd/sd-device/device-monitor.c) and v255: corroborating group/source checks and initialized-versus-kernel distinction, LGPL research only.
- [rustix 1.1.5 recvfrom](https://docs.rs/rustix/1.1.5/rustix/net/fn.recvfrom.html), [safe poll](https://docs.rs/rustix/1.1.5/rustix/event/fn.poll.html), fixed cached sources and licenses retained.
- [rustix issue1698](https://github.com/bytecodealliance/rustix/issues/1698), open at capture: locked UCred has nonzero Pid, kernel SCM credentials can have pid0. Do not enable/parse SCM_CREDENTIALS for this listener. This is a source-backed integration hazard, not a newly confirmed project vulnerability or CVE.

High confidence: fixed safe API, kernel source identity/receive contract, actual socket lifecycle. Unverified: real guest hotplug, socket overflow under load, exact target distro kernel patches, every transient, device function or hardware compatibility. GPL/LGPL upstream files are research evidence, not code copied into the MIT product.
