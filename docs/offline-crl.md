# Configure offline certificate revocation lists

RustUSB can require signed, current, complete CRLs when authenticating a new TLS
peer. This is optional offline revocation checking for both raw-v1 and framed-v2
TLS configurations. It preserves normal CA, certificate validity/usage, server
name, leaf-pin principal and device-ACL checks.

Use a CLI built with `secure-transport` (also included by `--all-features`) and
complete the [secure setup](secure-guide.md) first. The dedicated broker loads
credentials; ordinary operators continue to use their fixed agent profiles.
CRLs do not grant an operator access to private keys or change helper policy.

## Provision the lists

Obtain current, complete, direct CRLs from the CA administrators responsible for
the **peer's** certification path. A server checks its clients; a client checks
its server. Include one list for each issuing CA in that path, including the
issuer of an intermediate certificate. The configured trust anchor itself is
outside the path checked for revocation.

If necessary, convert a CA-issued PEM list to DER:

```sh
openssl crl -in current.crl.pem -outform DER -out current.crl.der
```

Conversion alone does not authenticate its issuer or signature. Install the DER
files under the same protected root-owned credential directory used by the
secure configurations. Public CRLs can be root-owned mode 0644; files and all
ancestors must satisfy the existing protected-path rules. Symlinks, hardlinks,
nonregular files, writable group/other permissions and changing reads fail.

Add this field to each applicable existing schema-1 or schema-2 TLS JSON file:

```json
"certificate_revocation_lists": [
  "/etc/rustusb/pki/root-issued.crl.der",
  "/etc/rustusb/pki/intermediate-issued.crl.der"
]
```

For a directly root-issued peer, only that issuer's list is needed. The names
above are examples; select the actual lists for your PKI. The field accepts
1–16 paths, at most 64 KiB per list and 256 KiB in total. When present, null or
an empty array is an error. Omitting the field retains the prior PKI/pin policy;
it does not enable revocation checking.

Each configured list must be a complete DER object. RustUSB rejects duplicate
issuer names, overlapping lists, delta/freshest-CRL policies and scoped/indirect
lists. It does not merge CRLs or select the highest CRL number. Multiple CA keys
with the same raw issuer name cannot each supply a separate list in this mode.
Missing issuer coverage or an invalid relevant signature fails the handshake;
metadata parsing at startup does not authenticate every unused list.

## Verify new connections

Start the dedicated broker with the provisioned configuration. From the
configured broker account, a client can perform a read-only discovery:

```sh
rustusb secure-discover --config /etc/rustusb/client.json
```

A successful authorized catalog can be empty when devices are reserved. A
revoked peer must not produce an authenticated catalog or import. A TLS1.3
client's local handshake completion alone is not the server's acceptance of that
client; use the completed application response and server observations.

A list must have `thisUpdate <= now < nextUpdate`, with a nextUpdate present.
RustUSB conservatively requires that window for **every** list in the loaded
set, including unused issuers. New certificate verification fails if any list
expires, is not yet valid or becomes future-dated after a clock rollback. The
checked instant is certificate verification, so a handshake can finish just
past expiry if it was checked before the boundary. The system clock remains a
trusted operational input.

Errors stay redacted and generic. A handshake-failure counter alone does not
identify a particular revoked certificate, signature fault or clock problem.
Revocation checks do not replace authentication admission limits or device
ownership/cleanup evidence.

## Renew or revoke credentials

Issue and install the new complete lists before their expiration. They are
immutable loaded snapshots: replacing a file does not update an existing TLS
configuration, clone, watch process or authenticated connection.

Cooperatively stop the owning foreground broker or agent, observe its actual
exit and required session cleanup, then restart it with the updated files. For
an agent, stopping and starting only a job does **not** reload credentials: all
profiles are loaded at agent startup and job clones share those inputs. Follow
[stop and completion rules](application-service-guide.md#stop-and-verify-completion)
before restarting the service. Do not use a new process's empty resource count
to prove an old USB owner was retired.

Existing authenticated streams are not continuously rechecked or terminated by
CRL replacement/expiry. There is no online CRL/OCSP retrieval, hot revocation or
persistent CRL-number anti-rollback database. An older still-valid list can be
loaded by an administrator. This feature does not complete the full security,
hardware, storage recovery or later UI requirements.

See the [implementation design](research/offline-crl-design.md) and
[exact validation](research/offline-crl-validation.md).
