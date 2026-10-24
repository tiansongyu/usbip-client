#!/bin/sh
# PID1-only composition; all device and ptrace activity stays in this disposable VM.
[ "${DIAGNOSTIC_COMMON:-}" = 1 ] || exit 2
case " $(cat /proc/cmdline) " in *' rustusb.diagnostic_export=1 '*) ;; *) exit 2 ;; esac
GATE=/run/prepare-gate TRACER_PID='' SELECTED='' EXPORT_PID='' DENIAL_PID=''
atomic_control() { printf '%s\n' "$2" > "$GATE/.$1.tmp"; chmod 600 "$GATE/.$1.tmp"; mv "$GATE/.$1.tmp" "$GATE/$1"; }
diagnostic_cleanup() {
    original=$?
    trap - EXIT
    set +e
    if [ -n "$TRACER_PID" ] && kill -0 "$TRACER_PID" 2>/dev/null; then atomic_control abort abort; fi
    for owned_pid in "$EXPORT_PID" "$DENIAL_PID" "$AGENT_PID" "$HELPER_PID"; do [ -z "$owned_pid" ] || kill -TERM "$owned_pid" 2>/dev/null; done
    if [ -n "$TRACER_PID" ]; then
        # shellcheck disable=SC2034
        for cleanup_poll in $(seq 1 200); do kill -0 "$TRACER_PID" 2>/dev/null || break; sleep 0.05; done
    fi
    if [ -d "$GATE" ]; then for gate_file in "$GATE"/*; do [ ! -f "$gate_file" ] || cp "$gate_file" "/tmp/gate-$(basename "$gate_file")"; done; fi
    failure_journal agent-state /var/lib/rustusb/agent/state.json
    failure_journal native-channels /var/lib/rustusb/native/channels.json
    dmesg > /tmp/failure-dmesg.log
    if [ "$original" != 0 ]; then echo "RUSTUSB_DIAGNOSTIC_RESULT: FAIL role=$ROLE stage=$STAGE exit=$original"; fi
    dump_artifacts
    artifact_status=$?
    [ "$artifact_status" = 0 ] || echo "RUSTUSB_DIAGNOSTIC_RESULT: FAIL role=$ROLE stage=artifacts exit=$artifact_status"
    sync; poweroff -f
    while :; do sleep 1; done
}
trap diagnostic_cleanup EXIT
# Actual carrier barrier, bounded observations rather than a startup sleep.
# shellcheck disable=SC2034
for network_poll in $(seq 1 200); do
    carrier=$(cat /sys/class/net/eth0/carrier) operstate=$(cat /sys/class/net/eth0/operstate)
    [ "$carrier:$operstate" != 1:up ] || break
    sleep 0.05
done
[ "$carrier:$operstate" = 1:up ]
jq -nc --arg role "$ROLE" --arg carrier "$carrier" --arg operstate "$operstate" --arg uptime "$(cat /proc/uptime)" '{role:$role,carrier:$carrier,operstate:$operstate,uptime:$uptime}' > /tmp/network-ready.json
ip addr show eth0 > /tmp/network-ready-address.txt
echo "DIAG_NETWORK_READY role=$ROLE"
host_expect NETWORK_READY
manifest() {
    mkdir -p /run/agent /var/lib/rustusb/agent
    chown 2000:2000 /run/agent /var/lib/rustusb/agent
    chmod 711 /run/agent; chmod 700 /var/lib/rustusb/agent
    jq -n --slurpfile profiles /tmp/agent-profiles.json '{schema_version:1,broker_uid:2000,helper_socket:"/run/rustusb/control.sock",control_directory:"/run/agent",data_directory:"/var/lib/rustusb/agent",profiles:$profiles[0],operators:[{uid:1000,profiles:[$profiles[0][].id],actions:["read","start","stop","discover","audit"]},{uid:1002,profiles:[$profiles[0][].id],actions:["read"]},{uid:1003,profiles:[$profiles[0][].id],actions:["audit"]}]}' > /etc/rustusb/agent.json
    chmod 644 /etc/rustusb/agent.json
    cp /etc/rustusb/agent.json /tmp/agent-manifest.json
}
traced_agent() {
    mkdir -m 700 "$GATE"
    (exec 5<&-; exec /usr/bin/prepare-gate --directory "$GATE" --usb-node "$HID_NODE" -- /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb --log-directory /var/log/rustusb/agent --log-max-bytes 262144 --log-retain 4 agent --config /etc/rustusb/agent.json run > /tmp/agent-1.stdout 2> /tmp/agent-1.log) & TRACER_PID=$!
    # shellcheck disable=SC2034
    for trace_poll in $(seq 1 200); do [ ! -f "$GATE/ready.json" ] || break; kill -0 "$TRACER_PID"; sleep 0.05; done
    AGENT_PID=$(cat "$GATE/tracee.pid") AGENT_EPOCH=1
    jq -e --argjson pid "$AGENT_PID" '.uid==2000 and .tracee_pid==$pid and .event=="ready"' "$GATE/ready.json" > /dev/null
    sha256sum "/proc/$TRACER_PID/exe" > /tmp/executable-gate.txt
    cat "/proc/$TRACER_PID/status" > /tmp/identity-gate.txt
    process_receipt "$TRACER_PID" tracer-start
    # shellcheck disable=SC2034
    for agent_poll in $(seq 1 200); do
        if agent profiles > /tmp/profiles-1.json 2> /tmp/profiles-1.log; then break; fi
        kill -0 "$TRACER_PID"; sleep 0.05
    done
    jq -e '.schema_version==1 and .journal_healthy and .response.result=="profiles"' /tmp/profiles-1.json > /dev/null
    identity "$AGENT_PID" agent-1; config_evidence 1; logging_started agent 1
    process_receipt "$AGENT_PID" agent-start
}
export_diagnostic() {
    export_uid=$1 export_mark=$2 export_path=$3
    cat /proc/uptime > "/tmp/export-$export_mark-before.uptime"
    (exec 5<&-; exec /usr/bin/setpriv --reuid="$export_uid" --regid="$export_uid" --clear-groups /usr/bin/rustusb --json --socket /run/rustusb/control.sock diagnostics --output "$export_path" --agent-config /etc/rustusb/agent.json --profile server > "/tmp/export-$export_mark.stdout" 2> "/tmp/export-$export_mark.stderr") & EXPORT_PID=$!
    if [ "$export_mark" = held ]; then
        # shellcheck disable=SC2034
        for export_poll in $(seq 1 200); do
            if [ "$(readlink "/proc/$EXPORT_PID/exe")" = /usr/bin/rustusb ]; then break; fi
            kill -0 "$EXPORT_PID"; sleep 0.001
        done
        [ "$(readlink "/proc/$EXPORT_PID/exe")" = /usr/bin/rustusb ]
        process_receipt "$EXPORT_PID" "export-$export_mark"
    fi
    if wait "$EXPORT_PID"; then export_rc=0; else export_rc=$?; fi
    echo "$export_rc" > "/tmp/export-$export_mark.wait"
    EXPORT_PID=''
    cat /proc/uptime > "/tmp/export-$export_mark-after.uptime"
}
diagnostic_rpc() {
    rpc_mark=$1 rpc_json=$2
    printf '%s' "$rpc_json" > "/tmp/rpc-$rpc_mark.request.json"
    rpc_bytes=$(wc -c < "/tmp/rpc-$rpc_mark.request.json")
    [ "$rpc_bytes" -gt 0 ] && [ "$rpc_bytes" -lt 256 ]
    rpc_octet=$(printf '%03o' "$rpc_bytes")
    { printf '\000\000\000'; printf '%b' "\\$rpc_octet"; cat "/tmp/rpc-$rpc_mark.request.json"; } |
      (exec 5<&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/nc-openbsd -N -w 2 -U /run/agent/control.sock) > "/tmp/rpc-$rpc_mark.response.bin"
    dd if="/tmp/rpc-$rpc_mark.response.bin" bs=1 skip=4 2>/dev/null > "/tmp/rpc-$rpc_mark.response.json"
    jq -e '.schema_version==1 and .journal_healthy' "/tmp/rpc-$rpc_mark.response.json" > /dev/null
    cat /proc/uptime > "/tmp/rpc-$rpc_mark.uptime"
}
file_metadata() {
    meta_mark=$1 meta_path=$2
    [ ! -L "$meta_path" ]
    jq -nc --arg path "$meta_path" --arg canonical "$(readlink -f "$meta_path")" --arg mode "$(stat -c '%a' "$meta_path")" --arg kind "$(stat -c '%F' "$meta_path")" --argjson uid "$(stat -c '%u' "$meta_path")" --argjson gid "$(stat -c '%g' "$meta_path")" --argjson bytes "$(stat -c '%s' "$meta_path")" --argjson links "$(stat -c '%h' "$meta_path")" --argjson device "$(stat -c '%d' "$meta_path")" --argjson inode "$(stat -c '%i' "$meta_path")" '{path:$path,canonical:$canonical,mode:$mode,kind:$kind,uid:$uid,gid:$gid,bytes:$bytes,links:$links,device:$device,inode:$inode}' > "/tmp/file-$meta_mark.json"
}
observe_gate() {
    observe_mark=$1
    [ ! -f "$GATE/exit-$SELECTED.json" ]
    atomic_control observe "$observe_mark"
    # shellcheck disable=SC2034
    for observe_poll in $(seq 1 100); do [ ! -f "$GATE/held-$observe_mark.json" ] || break; sleep 0.01; done
    [ -f "$GATE/held-$observe_mark.json" ]
    cp "$GATE/held-$observe_mark.json" "/tmp/gate-held-$observe_mark.json"
}
arm_and_select() {
    atomic_control arm resource
    # shellcheck disable=SC2034
    for arm_poll in $(seq 1 100); do
        awk '/"event":"armed"/ && /}$/ {print}' "$GATE/events.jsonl" > /tmp/gate-armed.json
        if jq -es --slurpfile ready "$GATE/ready.json" 'length==1 and (.[0]|.event=="armed" and .operation=="resource" and .nonce=="prepare-stop-v1" and .tracee_pid==$ready[0].tracee_pid and .tracee_starttime==$ready[0].tracee_starttime)' /tmp/gate-armed.json > /dev/null; then break; fi
        kill -0 "$TRACER_PID"; sleep 0.01
    done
    jq -es 'length==1 and .[0].event=="armed"' /tmp/gate-armed.json > /dev/null
    # Background actual operator request: its durable denial waits at this gate.
    agent start withheld-denial-canary > /tmp/withheld-denial.stdout 2> /tmp/withheld-denial.stderr & DENIAL_PID=$!
    for candidate in $(seq 1 32); do
        # shellcheck disable=SC2034
        for candidate_poll in $(seq 1 400); do [ ! -f "$GATE/candidate-$candidate.json" ] || break; kill -0 "$TRACER_PID"; sleep 0.01; done
        cp /var/lib/rustusb/agent/state.next "/tmp/draft-$candidate.json"
        cp /var/lib/rustusb/agent/state.json "/tmp/committed-$candidate.json"
        if jq -e --slurpfile old "/tmp/committed-$candidate.json" '.sequence==($old[0].sequence+1) and any(.audit[];.sequence==($old[0].sequence+1) and .actor_uid==1000 and .action=="denied" and .profile==null)' "/tmp/draft-$candidate.json" > /dev/null; then
            SELECTED=$candidate
            cp "/tmp/draft-$candidate.json" /tmp/selected-draft.json
            cp "/tmp/committed-$candidate.json" /tmp/selected-committed.json
            atomic_control "decision-$candidate" select
            break
        fi
        atomic_control "decision-$candidate" skip
    done
    [ -n "$SELECTED" ]
    # Selection must be acknowledged before requesting the real held receipt.
    # shellcheck disable=SC2034
    for selected_poll in $(seq 1 100); do
        if grep -F '"event":"selected"' "$GATE/events.jsonl" > /dev/null; then break; fi
        sleep 0.01
    done
    grep -F '"event":"selected"' "$GATE/events.jsonl" > /dev/null
    printf '%s\n' "$SELECTED" > /tmp/selected-candidate.txt
    observe_gate before
    process_receipt "$AGENT_PID" gate-before
    agent_snapshot gate-before
    cmp /tmp/selected-committed.json /tmp/agent-gate-before.json
    echo DIAG_GATE_HELD
}
if [ "$ROLE" = server ]; then
@@SERVER_SETUP@@
    HID_NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$HID_BUS/busnum")" "$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")")
    manifest; traced_agent
    start_job server
    wait_sessions server /tmp/server-ready-1.json '.response.sessions|map(select(.role=="export" and .state=="Available" and .terminal==false))|length==2'
    echo DIAG_SERVER_READY
    host_expect OBSERVE1
    wait_sessions server /tmp/server-held-1.json '.response.sessions|map(select(.role=="incoming" and .state=="Connected" and .terminal==false))|length==2'
    native_host_phase held-1 held; agent_snapshot held-1
    agent health server > /tmp/health-before.json
    STAGE=permissions-before-gate
    cp /var/lib/rustusb/native/channels.json /tmp/root-before-refusals.json
    jq '{jobs,sessions:(.sessions|map(del(.metrics))),requests}' /var/lib/rustusb/agent/state.json > /tmp/resources-before-refusals.json
    for export_uid in 1000 1002 1003; do
        mkdir -m 700 "/var/lib/rustusb/export-$export_uid"; chown "$export_uid:$export_uid" "/var/lib/rustusb/export-$export_uid"
        file_metadata "parent-$export_uid" "/var/lib/rustusb/export-$export_uid"
    done
    for export_uid in 1002 1003; do
        export_diagnostic "$export_uid" "denied-$export_uid" "/var/lib/rustusb/export-$export_uid/denied.json"
        [ "$export_rc" = 1 ]
        jq -e '.event=="diagnostic_export" and .capture=="partial" and any(.summary.sections[];.stage=="agent" and .failure=="permission")' "/tmp/export-denied-$export_uid.stdout" > /dev/null
        cp "/var/lib/rustusb/export-$export_uid/denied.json" "/tmp/denied-bundle-$export_uid.json"
    done
    cp /var/lib/rustusb/native/channels.json /tmp/root-after-refusals.json
    jq '{jobs,sessions:(.sessions|map(del(.metrics))),requests}' /var/lib/rustusb/agent/state.json > /tmp/resources-after-refusals.json
    cmp /tmp/root-before-refusals.json /tmp/root-after-refusals.json
    cmp /tmp/resources-before-refusals.json /tmp/resources-after-refusals.json
    # Require actual multi-page retained audit data, not a constructed snapshot.
    for audit_request in $(seq 1 20); do
        if agent start diagnostic-private-canary > "/tmp/prewarm-$audit_request.stdout" 2> "/tmp/prewarm-$audit_request.stderr"; then exit 1; else [ "$?" = 1 ]; fi
    done
    STAGE=wal-fsync-held
    arm_and_select
    STAGE=export-while-held
    export_diagnostic 1000 held /var/lib/rustusb/export-1000/bundle.json
    [ "$export_rc" = 0 ]
    jq -e '.schema_version==1 and .event=="diagnostic_export" and .capture=="complete" and .summary.complete and .file.result=="written" and .file.receipt.publication=="published"' /tmp/export-held.stdout > /dev/null
    cp /var/lib/rustusb/export-1000/bundle.json /tmp/diagnostic-bundle.json
    file_metadata held /var/lib/rustusb/export-1000/bundle.json
    diagnostic_rpc post-export-begin '{"schema_version":1,"request":{"op":"begin_diagnostic","profile":"server"}}'
    jq -e '.response.result=="diagnostic_started"' /tmp/rpc-post-export-begin.response.json > /dev/null
    end_request=$(jq -c '{schema_version:1,request:{op:"end_diagnostic",profile:"server",snapshot:.response.header.snapshot}}' /tmp/rpc-post-export-begin.response.json)
    diagnostic_rpc post-export-end "$end_request"
    jq -e '.response=={result:"diagnostic_ended"}' /tmp/rpc-post-export-end.response.json > /dev/null
    [ ! -f "$GATE/exit-$SELECTED.json" ]
    cmp /tmp/selected-committed.json /var/lib/rustusb/agent/state.json
    agent health server > /tmp/health-during.json
    echo DIAG_EXPORTED_GATE_HELD
    host_expect IO2_COMPLETE
    STAGE=second-io-gate-held
    agent_snapshot gate-after
    process_receipt "$AGENT_PID" gate-after
    cmp /tmp/selected-committed.json /tmp/agent-gate-after.json
    cmp /tmp/root-gate-before.json /tmp/root-gate-after.json
    native_host_phase gate-after held
    observe_gate after
    cat /proc/uptime > /tmp/gate-release-request.uptime
    atomic_control "release-$SELECTED" release
    # shellcheck disable=SC2034
    for release_poll in $(seq 1 300); do [ ! -f "$GATE/exit-$SELECTED.json" ] || break; sleep 0.01; done
    [ -f "$GATE/exit-$SELECTED.json" ]
    if wait "$DENIAL_PID"; then denial_rc=0; else denial_rc=$?; fi
    echo "$denial_rc" > /tmp/withheld-denial.wait; DENIAL_PID=''
    # shellcheck disable=SC2034
    for commit_poll in $(seq 1 300); do
        cp /var/lib/rustusb/agent/state.json /tmp/agent-after-release.json
        if jq -e --slurpfile old /tmp/selected-committed.json 'any(.audit[];.sequence==($old[0].sequence+1) and .action=="denied" and .actor_uid==1000)' /tmp/agent-after-release.json > /dev/null; then break; fi
        sleep 0.01
    done
    jq -e --slurpfile old /tmp/selected-committed.json 'any(.audit[];.sequence==($old[0].sequence+1) and .action=="denied" and .actor_uid==1000)' /tmp/agent-after-release.json > /dev/null
    echo DIAG_GATE_RELEASED
    # Existing destination rejection happens before observer startup: do not
    # require /proc sampling of this deliberately short-lived second invocation.
    if (exec 5<&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json --socket /run/rustusb/control.sock diagnostics --output /var/lib/rustusb/export-1000/bundle.json --agent-config /etc/rustusb/agent.json --profile server) > /tmp/export-existing.stdout 2> /tmp/export-existing.stderr; then exit 1; else echo "$?" > /tmp/export-existing.wait; fi
    cmp /var/lib/rustusb/export-1000/bundle.json /tmp/diagnostic-bundle.json
    file_metadata existing /var/lib/rustusb/export-1000/bundle.json
    host_expect STOP1
    STAGE=actual-server-cleanup
    kill -TERM "$AGENT_PID"
    if wait "$TRACER_PID"; then trace_rc=0; else trace_rc=$?; fi
    echo "$trace_rc" > /tmp/wait-tracer.txt
    [ "$trace_rc" = 0 ]; TRACER_PID='' AGENT_PID=''
    agent_snapshot stopped-1
    host_baseline /tmp/host-restored-1.json; cmp /tmp/host-before.json /tmp/host-restored-1.json
    log_meta logs-agent-1 /var/log/rustusb/agent
    echo DIAG_SERVER_STOPPED
    host_expect FINAL
else
@@CLIENT_SETUP@@
    manifest; agent_start 1
    process_receipt "$AGENT_PID" agent-start
    host_expect START1
    agent discover hid > /tmp/catalog-1.json
    for profile in hid serial; do
        start_job "$profile"
        wait_sessions "$profile" "/tmp/client-$profile-connected-1.json" '.response.sessions|map(select(.state=="Connected" and .evidence=="local_enumeration" and .terminal==false))|length==1'
    done
    record_client_locals 1; agent_snapshot held-1
    actual_io 1 yes
    host_expect IO2
    cmp /tmp/client-receipts-1.json /var/lib/rustusb/native/channels.json
    actual_io 2 yes
    agent_snapshot preserved
    host_expect CLOSE1
    cat /proc/uptime > /tmp/client-stop-request.uptime
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-1; AGENT_PID=''
    await_empty /tmp/vhci-stopped-1.txt; local_gone 1; agent_snapshot stopped-1
    log_meta logs-agent-1 /var/log/rustusb/agent
    echo 'LOG_CLIENT_STOPPED epoch=1'
    host_expect FINAL
fi
STAGE=helper-stop
kill -TERM "$HELPER_PID"; wait_exit "$HELPER_PID" helper; HELPER_PID=''
log_meta logs-helper-final /var/log/rustusb/helper
STAGE=complete
echo "RUSTUSB_DIAGNOSTIC_RESULT: PASS role=$ROLE"
