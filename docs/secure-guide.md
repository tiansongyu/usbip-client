# Secure USB/IP broker guide

This experimental Linux path runs device discovery, import and the following USB/IP data stream through mutual TLS. The unprivileged broker owns the network connection; a root helper owns the approved kernel or USBFS capability. Six paired QEMU scenarios cover Native, virtual and USBFS over IPv4/IPv6; see the [validation report](research/secure-usbip-validation.md) and [integration contract](research/secure-usbip-integration.md). Physical hardware and the full Phase 3 acceptance remain pending.

The existing `export`, `discover`, `attach`, `virtual-server` and `physical-server` commands keep their existing network contracts. Use the `secure-*` commands below for this path.

## Prerequisites

- A dedicated nonroot broker account on each Linux endpoint. The examples use UID `2000`; substitute the actual UID consistently. Its UID must be absent from the helper's interactive `allowed_uids`. Processes sharing that UID share its trust domain.
- Administrator-approved test devices and a client with `vhci_hcd`. A Native server also needs `usbip_host` and the distribution's matching `usbip` tool. The secure server does not start `usbipd`.
- A CA and separate server/client certificates and PKCS#8 private keys in DER format. Certificates must be valid now, chain to the configured CA, and have the appropriate server/client authentication purpose. The server certificate must cover the configured `server_name`.
- A root-owned `/etc/rustusb` directory and root-controlled ancestors, without group/other write access. Keep CA signing keys outside both endpoints. Each endpoint receives only its own private key.

Build the CLI and helper:

```sh
cargo build --workspace --release --all-features --locked
```

For a Native-only CLI, `cargo build -p rustusb-cli --features secure-transport --release --locked` enables the secure commands without the Rust USB device engine. Both builds still use Linux VHCI on the client.

## Trusted files

Install the configuration, CA certificates and public certificate chains as root-owned regular files, typically mode `0644`. Install the endpoint's private key as a regular file owned by root or the broker, with mode `0600`; it must actually be readable by the broker. Use root-owned parent directories. Symlinks, hardlinks, nonregular files and files that change during reading are rejected.

The broker reads these files at startup. Stop it cooperatively and restart to rotate credentials or change policy. There are no CLI overrides for credentials, endpoint, device key, ALPN or certificate verification. TLS 1.3, mutual certificate verification and ALPN `rustusb-usbip/1` are mandatory.

The server additionally maps the SHA-256 digest of each authorized client's **leaf DER certificate** to its principal. Obtain the 64 lowercase hexadecimal digits with `openssl dgst -sha256 client.der`; copy only the digest into `pins`. A valid CA chain alone does not grant access to a device.

## Server configuration

This example publishes one virtual HID profile. Replace the address and certificate digest with actual deployment values before use:

```json
{
  "schema_version": 1,
  "broker_uid": 2000,
  "listen": "192.0.2.10:3241",
  "devices": ["virtual:hid"],
  "principals": [{
    "id": "test-client",
    "pins": ["<64 lowercase hexadecimal digits from the client leaf certificate>"],
    "devices": ["virtual:hid"]
  }],
  "ca_certificates": ["/etc/rustusb/ca.der"],
  "certificate_chain": ["/etc/rustusb/server.der"],
  "private_key": "/etc/rustusb/server-key.der"
}
```

Run as the configured broker account:

```sh
rustusb secure-server --config /etc/rustusb/server.json --backend virtual
```

The server pool contains 1–16 unique keys. Principal rules can grant only keys in that pool. Choose one backend per process:

| Backend option | Key namespace | Server helper pool |
| --- | --- | --- |
| `virtual` | `virtual:hid`, `virtual:cdc-acm` | No device grant needed |
| `usbfs` | `physical:usb-<64 lowercase hexadecimal digits>` | `secure_physical_device_ids` |
| `native` | `native:usb-<64 lowercase hexadecimal digits>` | `secure_native_device_ids` |

For either physical route, obtain and review the exact `usb-…` fingerprint using `rustusb devices --json`. Replace the virtual key in both the server pool and principal rule. The helper pool contains the **unqualified** `usb-…` fingerprint. Keep all secure pools disjoint from each other and from legacy Native/USBFS device pools. Native publishes the actual pre-export descriptor snapshot. Its CLI snapshot is not live configuration or driver state; verify current phases through helper receipts and independent sysfs observations.

## Client configuration and helper policy

The endpoint host may be DNS, IPv4 or IPv6. `server_name` is the independent certificate verification name; it is not inferred from a returned device:

```json
{
  "schema_version": 1,
  "broker_uid": 2000,
  "server_id": "lab-server",
  "endpoint": {"host": "192.0.2.10", "port": 3241},
  "server_name": "usb.example.test",
  "device": "virtual:hid",
  "ca_certificates": ["/etc/rustusb/ca.der"],
  "certificate_chain": ["/etc/rustusb/client.der"],
  "private_key": "/etc/rustusb/client-key.der"
}
```

The client's root-managed helper policy authorizes the same logical server and exact device key:

```json
{
  "allowed_uids": [],
  "export_device_ids": [],
  "physical_device_ids": [],
  "peers": [],
  "trusted_network": false,
  "secure_broker_uid": 2000,
  "secure_imports": [{
    "server_id": "lab-server",
    "device_keys": ["virtual:hid"]
  }],
  "secure_physical_device_ids": [],
  "secure_native_device_ids": []
}
```

For a physical server, use a separate server helper policy with its approved secure device pool, broker UID and no secure imports unless explicitly needed. `trusted_network` does not create TLS and is not used to impersonate TLS authorization. See the [Native guide](native-guide.md) for installing the root helper, protected control socket and required kernel modules. Its Native channel journal defaults to `/var/lib/rustusb/native`; the USBFS journal remains separate.

Run discovery and a long-lived import as the client broker account:

```sh
rustusb secure-discover --config /etc/rustusb/client.json --json
rustusb secure-attach --config /etc/rustusb/client.json
```

Discovery matches each stable key to the standard USB/IP descriptor in the same authenticated catalog. Import selects the configured key, validates the standard IMPORT reply and keeps the same TLS connection for URBs. It does not select the first device or authorize a changing Bus ID.

## Stop and verify

Send SIGINT or SIGTERM to the importing broker and await its exit. A successful `disconnected` event with `cleanup_verified: true` follows the helper's actual close response. Verify the original VHCI connection is gone. Then stop the server and await its exit; Native export cleanup verifies the original driver/configuration baseline, and USBFS uses its existing grant/Finish lifecycle.

Unconfirmed Native operations survive helper restart as pending records and block new ownership. As root, use `rustusb native-pending` and its bounded `--after` cursor. `rustusb native-reconcile <channel-id>` performs read-only verification after the old owner has ended and the old resource is demonstrably released. It does not detach an unknown port or restore a driver. Do not delete journal files to clear an unresolved operation.

Limit network access to the configured TLS endpoint and verify both address families. There should be no `usbipd` or plaintext device listener for this deployment. Real hardware acceptance, general cross-configuration compatibility, ISO, crash recovery and the full Phase 3 state/audit requirements remain separate gates.
