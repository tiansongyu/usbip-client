# NSS answers versus TCP scheduling

2026-10-24. Read-only component investigation; no repository edit, host network
or USB change, product test, Cargo build or new guest execution was performed.
The future multi-on/broker-UID run was live when this report was frozen; its
outcome is not assumed here.

## Fixed sources and actual host identity

The host is Ubuntu22.04.5. The actual executed libc and installed libc6 package
identify **2.35-0ubuntu3.14**. The separate ldd/libc-bin tool identifies3.13;
ldd's version is therefore not substituted for the actual libc identity.
Actual libc/NSS library hashes and selected exported-symbol observations are
retained without copying executable bodies.

The [glibc2.35 release tag](https://github.com/bminor/glibc/tree/glibc-2.35)
resolves through its annotated tag to fixed commit
`f94f6d8a3572840d3ba42ab9ace3ea522c99c0c2`. Selected upstream source and LGPL
license are preserved. A request for the exact Ubuntu3.14 source descriptor on
the official archive returned404. Consequently this research does not claim the
Ubuntu patched binary is byte-identical to that upstream source or that its
patch set has been audited.

Pinned Rust1.97.1 is commit`8bab26f4f68e0e26f0bb7960be334d5b520ea452`.
The installed stable rust-src and compiler identify that same release. Both
selected installed source files match their exact upstream Git bytes; source,
license and commit provenance are preserved.

## Source-backed behavior

[Rust lookup_host](https://github.com/rust-lang/rust/blob/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/sys/net/connection/socket/mod.rs)
zeroes a non-null getaddrinfo hints structure and sets only SOCK_STREAM. On
Linux that gives AF_UNSPEC with flags0, rather than AI_ADDRCONFIG. The
[ToSocketAddrs adapter](https://github.com/rust-lang/rust/blob/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/net/socket_addr.rs)
collects the resulting iterator. A getent command using different hints cannot
be assumed equivalent to the Rust probe.

In fixed [glibc files-hosts.c](https://github.com/bminor/glibc/blob/f94f6d8a3572840d3ba42ab9ace3ea522c99c0c2/nss/nss_files/files-hosts.c),
`_nss_files_gethostbyname4_r` accepts either family, matches canonical names or
aliases, and stops after the first matching hosts entry when HCONF_FLAG_MULTI
is clear. It continues gathering matching entries when that flag is set.
Fixed getaddrinfo selects that NSS function for AF_UNSPEC where available.

[res_hconf.c](https://github.com/bminor/glibc/blob/f94f6d8a3572840d3ba42ab9ace3ea522c99c0c2/resolv/res_hconf.c)
starts with zeroed flags, reads /etc/host.conf, and permits RESOLV_HOST_CONF and
RESOLV_MULTI overrides. With no file or override enabling multi, the flag stays
clear. Initialization uses libc_once; comparisons after a file change require
fresh processes. The host's own host.conf contains multi on; that says nothing
about an independently constructed initramfs.

Therefore repeated same-hostname IPv6/IPv4 lines do not by themselves prove that
this files-backed lookup supplies both addresses. gai.conf can order returned
addresses; it does not manufacture the omitted second hosts entry.

## Observed unchanged-guest probe, narrowly scoped

The selected original receipts were copied from
`/tmp/rustusb-dual-stack-native-ipv4-probe-first-20261024` without modification.
They show:

- Actual probe executable SHA`924552cebc4cd26282c8d0c8a2298d3c3aec4c2f91aaa9869795d263ddf59e0b`,
  matching its compilation receipt; the receipt's source SHA also matches the
  exact clean636efa Git blob.
- RAW_COUNT1 and only`[fd42:7275:7374::99]:3241`, despite the saved hosts file
  containing that stale entry followed by192.0.2.1 for the same hostname.
- Saved NSS policy hosts:files, the exact IPv6-preferred gai policy, and the
  fixture's absence marker for host.conf. The marker's raw bytes are preserved.
- The original clean`636efa25454599ccf9058ba4dada233608688ace` paired run remains
  host FAIL, both guest exits0 and all four guards true.

This is a root-executed diagnostic per the author. These selected receipts lack
an independent UID sample, and are not the product process or a broker-UID
answer-set observation. They support the specific NSS fixture hypothesis and
contradict an assumption that the diagnostic received two candidates. They do
not prove a scheduler defect or its absence in the product execution. Earlier
failed QEMU experiments with unobserved candidate lists cannot be called
installed two-candidate racer counterfactuals.

## Next discriminating observations

1. Preserve the unchanged-NSS probe and its original host FAIL. Record the
   host.conf absence/content, relevant override absence and actual UID.
2. Install protected host.conf with multi on before any fresh broker-UID probe
   or product process. Capture its identity/mode and actual ordered answer list.
   This is a controlled fixture correction, not a production resolver change.
3. Only if that actual list contains both expected families can subsequent SYN
   chronology and real USB I/O test the fallback policy. If two eligible answers
   are present yet no backup SYN occurs, inspect actual remaining operation
   budget, source/ELF mapping and scheduling separately.

No broker-UID comparison, multi-on success, full DNS/VPN, product recovery,
performance, hardware or complete G3 result is claimed by this research.
