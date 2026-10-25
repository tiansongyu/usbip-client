# Automatic sharing for fixed profiles

The optional application agent can persist bounded rules and start an entire
administrator-selected profile when its condition is observed. Rules continue
after the requesting CLI exits. This is an early CLI/service feature;
**GUI rule editing and dynamic device pools remain unfinished**.

[Native local-rule validation](research/automatic-sharing-runtime-validation.md)
covers actual IPv4/IPv6 QEMU HID/FTDI I/O, manual pause across restart and explicit
rearm. [Rust-engine rule tests](research/automatic-sharing-rust-qemu-validation.md)
add framed-v2 virtual remote import and local USBFS rules on both IP families,
with three directional faults, original I/O and exact cleanup. Physical/Native remote keys report
`safety_evidence_unavailable`: their catalog lacks the complete configuration and
alternate-setting evidence needed for unattended import. These rules do not
establish hardware acceptance or general storage sharing.

## Administrator setup

First install the helper, provision both endpoints' TLS/device permissions and
configure the [application agent](application-service-guide.md). Build the CLI
with `secure-transport`; virtual serving additionally needs `rust-backend`, and
USBFS needs `physical-nusb`. Rules grant no new helper operation or USB authority.

Create a separate root-owned policy under protected root-owned ancestors. Its
`broker_uid` must match the loaded agent. Each grant binds an exact operator UID,
profile, **complete configured device pool**, trigger/rearm selection and quota.
There are no wildcard matches or user-selected endpoints, keys or commands.
Ordinary Read/Start/Stop permissions remain necessary; remote conditions also
require Discover. Audit controls the independent rule-event read.

For a Native server profile named `lab-server`, generate a candidate from its
public server configuration, inspect the exact pool, then install it as root.
Adjust operator UID `1000`, profile name and paths to the deployment:

```sh
jq '{schema_version:1,broker_uid:.broker_uid,grants:[{
  operator_uid:1000,profile:"lab-server",devices:.devices,
  triggers:["local_profile_ready"],rearm:["once"],max_rules:4
}]}' /etc/rustusb/server.json > automation.json

cat automation.json
sudo install -o root -g root -m 0644 automation.json /etc/rustusb/automation.json
```

Run the agent **as its configured dedicated nonroot broker account**:

```sh
rustusb agent --config /etc/rustusb/agent.json run \
  --automation-policy /etc/rustusb/automation.json
```

Without this explicit option the agent starts no automatic observers or jobs.
Existing manual operations remain available. Enabling policy upgrades its private
journal to strict **disk3** before opening the control listener. Older agents
cannot read disk3; do not downgrade the executable against that journal. Removing
policy neither downgrades the journal nor deletes old ownership records.

The policy uses schema1, at most64 KiB/64 unique grants, 1..16 exact keys per grant
and1..4 rules per operator/profile. Local conditions require Native or USBFS
server profiles. `remote_device_available` requires a client profile; the currently
permitted remote keys are only the fixed `virtual:hid` and `virtual:cdc-acm`
profiles with their validated descriptor shapes. A remote grant's `devices` must
contain its one configured key.

Policy, ACL and credential inputs are immutable for that service instance.
Changing files does not revoke running work. Cooperatively stop and actually
join the old agent before starting it with changed inputs, following the existing
service/credential retirement rules. There is no live policy reload.

## Create and inspect a rule

As the authorized operator, create a disabled rule first:

```sh
rustusb agent --config /etc/rustusb/agent.json rule lab-server create \
  --name "Lab Native pool" --trigger local-profile-ready \
  --settle-seconds 2 --rearm once | tee rule-create.jsonl

rustusb agent --config /etc/rustusb/agent.json rules lab-server
```

Names are1..64 UTF-8 bytes, without controls or surrounding whitespace. Settle
periods are2..120 seconds. Creation is disabled unless `--enabled` is supplied.
Rules affect the **whole profile**, so one matching device never authorizes
silently sharing the rest of a pool.

Every new `agent rule` operation writes two bounded NDJSON frames: an intent/query
frame containing its original request UUID, followed by the ordinary response
envelope. The intent must be completely written before RPC dispatch; it is
**not a commit**. Preserve its UUID. A blocked or interrupted incomplete intent
does not dispatch the request. After dispatch, interruption/timeout ends only the
client wait: the accepted mutation may still commit.

Extract the rule ID from a successful Create result and enable it using exact
revision compare-and-swap:

```sh
RULE_ID=$(jq -sr '.[-1].response.outcome.rule_id' rule-create.jsonl)
rustusb agent --config /etc/rustusb/agent.json rule lab-server enable \
  "$RULE_ID" --expected-revision 1
rustusb agent --config /etc/rustusb/agent.json rule-events lab-server
```

Check the response before using its ID/revision; failures return a nonzero exit.
For an unknown response, query only the **original mutation UUID**:

```sh
REQUEST_ID=$(jq -r 'select(.event=="rule_mutation_intent")|.request_id' rule-create.jsonl)
rustusb agent --config /etc/rustusb/agent.json rule lab-server retry "$REQUEST_ID"
```

`retry` performs no edit. An expired/unknown query does not prove that a late
original cannot commit. Inspect rules and preserve that identity; never silently
issue another Create or a fresh UUID to resolve uncertainty. `--request-id` also
accepts an explicitly retained original UUID. The rule-mutation namespace is
separate from Start's request namespace.

`replace`, `enable`, `disable` and `delete` require the exact rule ID and
`--expected-revision` read from the current view. Replace changes name/trigger/
settle/rearm and preserves an existing manual pause. Explicit Enable rearms and
clears that pause only after its commit, provided no newer Stop arrived during
the commit. A revision conflict makes no edit; reread before deciding what to do.

## Conditions, stopping and recovery

Local rules require a complete Current scan no more than6 seconds old, exact
unambiguous device identities, a stable2..120-second observation and all current
storage/export-safety checks. The token includes boot identity and exact sysfs
generation/address tuples. Starting a new observer or repeating a hint is not a
new device occurrence. Helpers still recheck the actual privileged mutation.

Remote rules use real authenticated Discover requests, one automatic poll at a
time within the existing two-slot supervisor. One slot remains available to
manual requests. Matching fresh successful polls must span the settle interval;
this does not guarantee uninterrupted presence between polls. Poll errors/stale
data cannot fire. Busy catalog visibility never stops an existing USB job, and
advertised tuples are not physical-device generations.

`once` consumes the occurrence even when its admitted job later fails.
`new-observation` permits a different validated token after old ownership is
resolved. An explicit authorized revision change can rearm the same observation.
No automatic admission bypasses a live profile owner, unresolved previous session
or overlapping Native/USBFS physical pool. After durable Start admission, stale
source data or an inherited Stop prevents backend entry; the original job ends
with State failure rather than fabricated USB success.

A valid manual `stop-job` or `stop-session` immediately suppresses future automatic
starts for that profile and persistently pauses its rules. Targeted session stop
still preserves the original safe siblings. Read `paused_by_user`, `enabled`,
`last_origin`, `last_job` and separate observation/admission errors together:
an active old job can remain visible while future triggers are paused.

Disable/Delete stop future triggers; they do not release the current job. Use
the ordinary Stop operation and inspect actual session cleanup. Read-authorized
owners may disable/delete after automation delegation has been removed.
Unresolved deleted records remain counted until their old ownership is resolved.

Rule edits, automatic origin, original Start request/job/audit and firing event
use the existing atomic journal writer. Readable rule views and independent rule
events bypass WAL waits; they are metadata, not USB capabilities or release
proofs. Service shutdown directly stops and joins the actual observer, network
and USB owner trees. Permanent filesystem/kernel/DNS blocking retains the
existing limits. See the [design](research/automatic-sharing-design.md) and
[validation boundaries](research/automatic-sharing-runtime-validation.md).
