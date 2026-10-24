# Hostname fallback with real USB imports

From an ordinary x86_64 host and a clean committed checkout:

```sh
python3 -B scripts/qemu_dual_stack/run.py --backend native --family ipv4 \
  --kernel 6.8.0-106-generic --evidence /tmp/NEW-EMPTY-DIRECTORY
```

`--family` names the healthy family; the hostname's preferred candidate uses the
opposite family and a permanent nonexistent MAC on the isolated guest link.
`native|virtual|usbfs` and `ipv4|ipv6` are six separate runs. The guest alone gets
protected hosts/gai/NSS/neighbor inputs. No host network/USB mutation, DNS server,
TAP or public listener is used. Never execute generated PID1 on the host.

The original secure suite still requires both real HID/serial cycles, helper
refusals, actual process waits, kernel receipts, complete root journals and full
original driver restoration. The TLS service name stays independent of the
endpoint hostname. A strict named healthy PCAP projection removes only exact
empty client SYNs to the stale address/nonexistent MAC, then passes every other
packet record unchanged to the original classifier. Raw PCAP is never rewritten.
Actual client capture must show each stale first attempt before its healthy
opposite-family connection within the existing ten-second deadline.

Old sequential failure and later clean product runs must retain their actual
verdicts and source/ELF guards. This is OS hostname resolution using hosts files;
it is not wire-DNS, VPN, performance, hardware, full RFC8305 or fullG3 acceptance.
