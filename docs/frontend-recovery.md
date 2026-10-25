# Recover an original frontend request after restart

The Linux GUI and TUI save their original confirmed Start and rule-edit identities
before issuing IPC. If the **whole frontend process** exits, reopen it against the
same trusted agent manifest and explicitly query the original request. Restarting
the frontend never automatically starts a profile or resubmits a rule edit.
[Validation and limits](research/frontend-intent-recovery-validation.md).

## State location and access

The shared worker uses `$XDG_STATE_HOME/rustusb`, falling back to absolute
`$HOME/.local/state/rustusb`. Relative/empty XDG values are ignored. This is local
state, separate from frontend preferences; `--no-preferences` and
`--preferences-directory` do not disable or relocate it. Set an absolute
`XDG_STATE_HOME` before launching either frontend if a different location is
needed. Keep the location on the intended host; cross-host shared-home behavior
and filesystem power-loss durability are not established by the process tests.

Startup may create private directories and a per-agent lock. Directories must be
caller-owned0700 and files0600, regular and single-linked, below checked ancestors.
The opaque per-endpoint filename and strict16KiB document bind the loaded
manifest's control socket context, broker UID and actual user UID. A hash names
the file; it does not authenticate a USB device or grant an agent action.

System/Settings report **local request recovery** independently of the agent
journal. Only one frontend per endpoint owns the writer at a time. Another
frontend can observe, query a retained original or request an exact Stop, but
cannot start new UUID work or clear the first writer's file. Close the other
frontend and reopen to acquire the writer. A missing usable state location,
malformed file, wrong permissions, changed endpoint/broker binding or recording
failure also blocks new Start/rule edits. Preserve the original file and repair
the reported cause before reopening. There is no automatic purge or reset.

## Resolve an original request

1. Reopen the GUI/TUI with the same protected agent manifest and ordinary user.
   Inspect the displayed original profile and UUID, even if the current profile
   selection or service epoch has changed.
2. For Start, choose **Resolve original start** (TUI `e`). Its default-Cancel
   confirmation queries only the original Start UUID. A matching Started result
   identifies its admitted job; inspect Connections for its actual lifecycle.
3. For a rule, choose **Query original rule edit**. The retained original protocol
   decides the query; current support/policy changes never turn it into a new
   mutation. [Settled decisions](automatic-sharing.md#opt-in-to-settled-edit-decisions)
   can return Applied or an already persisted Rejected result.
4. After an exact settled refusal, **Restore rejected draft** defaults Cancel and
   restores that issued request's original definition/profile/revision. Inspect
   current rules and explicitly choose a latest revision before another edit.
   The last issued rule receipt/draft also survives a frontend-process restart.

The file is updated before the resolved slot is cleared in the UI. If the agent
returns a result but local recording fails, the original identity remains and
the status distinguishes that result from the local failure. After repairing
storage and reopening, query that same identity again. Querying does not edit,
rearm, import or release a USB device.

Generic errors and expired/evicted results still do not prove that an original
never committed. This includes a legacy Start refusal without a durable negative
receipt. Keep the UUID and reconcile actual agent jobs/rules/sessions with the
administrator; do not resolve uncertainty by deleting state or issuing a fresh
request. A locally saved intent also does not prove it was dispatched. A safe
force-clear workflow for all unknown/expired outcomes remains unfinished.

## What is retained

At most one pending Start, one pending rule edit and one last rule receipt are
kept per endpoint. Rule payload/protocol and exact revisions are retained; no
private TLS key, USB payload, arbitrary command or new endpoint comes from this
file. Every real IPC request is still authorized by the agent using OS identity.

Unconfirmed renderer form edits and unsaved preferences are not automatically
saved by this mechanism. Closing a renderer is separate from restarting the Rust
frontend process. The checked file is user metadata, not a service audit, USB
ownership or cleanup proof. Stop remains an independent exact-job/session action;
StopAccepted still requires observing actual release.

All accepted file tasks and issued IPC calls are joined on cooperative exit.
Permanent filesystem blocking can delay that exit. Process-kill tests do not
establish arbitrary power-loss, hostile same-UID tampering, general USB crash
recovery, installed reboot behavior or full GUI/hardware acceptance.
