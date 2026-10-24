#!/bin/sh
# Appended to source-locked secure boot + agent helper functions only.
[ "${PREPARE_COMMON:-}" = 1 ] || exit 2
case " $(cat /proc/cmdline) " in *' rustusb.prepare_stop=1 '*) ;; *) exit 2 ;; esac
case "$CASE:$STOP_MODE" in native-resource:job|native-resource:term|usbfs-resource:job|usbfs-resource:term|usbfs-initializer:job|usbfs-initializer:term) ;; *) exit 2 ;; esac
GATE=/run/prepare-gate TRACER_PID='' AGENT_PID='' CANDIDATE='' SELECTED='' CASE_FAILED=0
atomic_control() { printf '%s\n' "$2" > "$GATE/.$1.tmp"; chmod 600 "$GATE/.$1.tmp"; mv "$GATE/.$1.tmp" "$GATE/$1"; }
prepare_cleanup() {
    prepare_status=$?
    trap - EXIT
    set +e
    if [ "$ROLE" = server ] && [ -d "$GATE" ]; then
        # Always release the same held syscall before cooperative process cleanup.
        if [ -n "$TRACER_PID" ] && kill -0 "$TRACER_PID" 2>/dev/null; then atomic_control abort abort; fi
        if [ -n "$AGENT_PID" ]; then kill -TERM "$AGENT_PID"; fi
        if [ -n "$TRACER_PID" ]; then
            for cleanup_poll in $(seq 1 150); do kill -0 "$TRACER_PID" 2>/dev/null || break; sleep 0.05; done
        fi
        for gate_file in "$GATE"/*; do [ ! -f "$gate_file" ] || cp "$gate_file" "/tmp/gate-$(basename "$gate_file")"; done
    elif [ -n "$AGENT_PID" ]; then kill -TERM "$AGENT_PID"; fi
    failure_journal agent-state /var/lib/rustusb/agent/state.json
    failure_journal native-channels /var/lib/rustusb/native/channels.json
    failure_journal physical-journal /var/lib/rustusb/physical/journal.json
    dmesg > /tmp/failure-dmesg.log
    if [ "$prepare_status" != 0 ]; then echo "RUSTUSB_PREPARE_RESULT: FAIL role=$ROLE stage=$STAGE exit=$prepare_status"; fi
    dump_artifacts
    artifact_status=$?
    [ "$artifact_status" = 0 ] || echo "RUSTUSB_PREPARE_RESULT: FAIL role=$ROLE stage=artifact-dump exit=$artifact_status"
    sync; poweroff -f
    while :; do sleep 1; done
}
trap prepare_cleanup EXIT
root_snapshot() {
    if [ "$BACKEND" = native ] || [ "$ROLE" = client ]; then ROOT_FILE=/var/lib/rustusb/native/channels.json; else ROOT_FILE=/var/lib/rustusb/physical/journal.json; fi
    cp "$ROOT_FILE" "/tmp/root-$1.json"
    if [ -f /var/lib/rustusb/agent/state.json ]; then cp /var/lib/rustusb/agent/state.json "/tmp/agent-$1.json"; fi
    if [ "$ROLE" = server ]; then host_baseline "/tmp/host-$1.json"; else native_snapshot "/tmp/vhci-$1.txt"; fi
}
fd_snapshot() {
    : > "/tmp/fds-$1.jsonl"
    for cap_fd in /proc/"$AGENT_PID"/fd/*; do
        [ -e "$cap_fd" ] || continue
        jq -nc --arg fd "${cap_fd##*/}" --arg target "$(readlink "$cap_fd")" --arg dev "$(stat -Lc '%d' "$cap_fd")" --arg inode "$(stat -Lc '%i' "$cap_fd")" --arg rdev "$(stat -Lc '%t:%T' "$cap_fd")" '{fd:$fd,target:$target,device:$dev,inode:$inode,rdev:$rdev}' >> "/tmp/fds-$1.jsonl"
    done
    jq -s . "/tmp/fds-$1.jsonl" > "/tmp/fds-$1.json"
    cat "/proc/$AGENT_PID/stat" > "/tmp/owner-stat-$1.txt"
    cat /proc/net/unix > "/tmp/unix-$1.txt"
}
actual_config() {
    for config_file in /etc/rustusb/agent.json /etc/rustusb/policy.json /etc/rustusb/server-*.json /etc/rustusb/client-*.json /etc/rustusb/ca.der /etc/rustusb/server.der /etc/rustusb/client.der /etc/rustusb/client-pin.txt; do
        [ ! -f "$config_file" ] || cp "$config_file" "/tmp/actual-$(basename "$config_file")"
    done
    : > /tmp/config-mode.txt
    for config_path in / /etc /etc/rustusb /run /run/agent /var /var/lib /var/lib/rustusb /var/lib/rustusb/agent /etc/rustusb/*.json /etc/rustusb/*.der; do
        [ ! -e "$config_path" ] || stat -c '%n %F %u %g %a %h %s %d %i' "$config_path" >> /tmp/config-mode.txt
    done
    # Private key contents are never copied/read/hashed.
}
traced_agent() {
    mkdir -m 700 "$GATE"
    (exec 5<&-; exec /usr/bin/prepare-gate --directory "$GATE" --usb-node "$HID_NODE" -- /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run > /tmp/agent-1.stdout 2> /tmp/agent-1.log) & TRACER_PID=$!
    for trace_poll in $(seq 1 200); do
        if [ -f "$GATE/ready.json" ]; then break; fi
        kill -0 "$TRACER_PID"; sleep 0.05
    done
    sha256sum "/proc/$TRACER_PID/exe" > /tmp/executable-gate.txt
    cat "/proc/$TRACER_PID/status" > /tmp/identity-gate.txt
    AGENT_PID=$(cat "$GATE/tracee.pid")
    jq -e --argjson pid "$AGENT_PID" '.uid==2000 and .tracee_pid==$pid and .event=="ready"' "$GATE/ready.json" > /dev/null
    for agent_poll in $(seq 1 200); do
        if agent profiles > /tmp/profiles-1.json 2> /tmp/profiles-1.log; then break; fi
        kill -0 "$TRACER_PID"; sleep 0.05
    done
    jq -e '.response.result=="profiles" and .journal_healthy' /tmp/profiles-1.json > /dev/null
    identity "$AGENT_PID" agent-1; actual_config
}
select_candidate() {
    for candidate_number in $(seq 1 32); do
        for gate_poll in $(seq 1 400); do
            [ ! -f "$GATE/candidate-$candidate_number.json" ] || break
            kill -0 "$TRACER_PID"; sleep 0.01
        done
        CANDIDATE=$candidate_number
        cp "$GATE/candidate-$CANDIDATE.json" "/tmp/gate-candidate-$CANDIDATE.json"
        if [ "$CASE" = usbfs-initializer ]; then
            cp /var/lib/rustusb/agent/state.json /tmp/selected-state.json
            SELECTED=$CANDIDATE; atomic_control "decision-$CANDIDATE" select; break
        fi
        cp /var/lib/rustusb/agent/state.next "/tmp/draft-$CANDIDATE.json"
        jq -e '.schema_version==2 and (.audit|length)>0 and (.sessions|type)=="array"' "/tmp/draft-$CANDIDATE.json" > /dev/null
        if jq -e '(.audit[-1].action=="resource") and (.audit[-1].profile=="pool") and ([.sessions[]|select(.profile=="pool" and .role=="export" and .terminal==false and .attempts[-1].resource_id!=null)]|length)==2' "/tmp/draft-$CANDIDATE.json" > /dev/null; then
            cp "/tmp/draft-$CANDIDATE.json" /tmp/selected-state.json
            cp /var/lib/rustusb/agent/state.json /tmp/state-before-ack.json
            SELECTED=$CANDIDATE; atomic_control "decision-$CANDIDATE" select; break
        fi
        atomic_control "decision-$CANDIDATE" skip
    done
    [ -n "$SELECTED" ]
    for select_poll in $(seq 1 100); do
        if grep -F '"event":"selected"' "$GATE/events.jsonl" > /dev/null; then break; fi
        sleep 0.01
    done
    grep -F '"event":"selected"' "$GATE/events.jsonl" > /dev/null
    jq -ce '[.sessions[]|select(.profile=="pool" and .role=="export" and .terminal==false)]|select(length==2 and all(.[];.attempts|length==1))' /tmp/selected-state.json > /tmp/selected-sessions.json
    TARGET_JOB=$(jq -er 'map(.job_id)|unique|select(length==1)|.[0]' /tmp/selected-sessions.json)
    jq -ce 'map(.attempts[0])|select(length==2 and all(.[];.resource_id!=null))' /tmp/selected-sessions.json > /tmp/selected-attempts.json
    root_snapshot held; fd_snapshot held
    cat /proc/net/tcp /proc/net/tcp6 > /tmp/listeners-held.txt
    jq -nc --argjson candidate "$SELECTED" --arg case "$CASE" --arg stop "$STOP_MODE" --arg job "$TARGET_JOB" '{case:$case,stop:$stop,candidate:$candidate,job:$job}' > /tmp/selection.json
    atomic_control observe before
    for observe_poll in $(seq 1 100); do [ ! -f "$GATE/held-before.json" ] || break; sleep 0.01; done
    [ -f "$GATE/held-before.json" ]
    echo PREPARE_GATE_HELD
}
check_target_proof() {
    # Judge the saved observation, never a later read of the live journal.
    [ "$#" = 1 ] || return 2
    if [ "$BACKEND" = native ]; then
        jq -e --slurpfile sessions /tmp/selected-sessions.json '
          . as $root | ($sessions[0]|length)==2 and all($sessions[0][];
          . as $s | .attempts[0] as $a |
          all($root.exports[];.lease_id!=$a.resource_id) and
          any($root.terminals[].terminal;.resource_id==$a.resource_id and .kind=="native_export" and .owner_uid==2000 and .correlation==$a.correlation and .device_key==$s.device_key and .proof=="native_export_restored"))' "$1" > /dev/null
    else
        jq -e --slurpfile sessions /tmp/selected-sessions.json '
          . as $root | ($sessions[0]|length)==2 and all($sessions[0][];
          . as $s | .attempts[0] as $a |
          all($root.records[];.lease_id!=$a.resource_id) and
          any($root.terminals[];.resource_id==$a.resource_id and .kind=="usbfs_grant" and .owner_uid==2000 and .correlation==$a.correlation and .device_key==$s.device_key and .proof=="physical_baseline_restored"))' "$1" > /dev/null
    fi
}
config_evidence() { actual_config; }
# Actual link state, not a fixed startup sleep.
for network_poll in $(seq 1 200); do
    if [ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && [ "$(cat /sys/class/net/eth0/operstate)" = up ]; then break; fi
    sleep 0.05
done
[ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && [ "$(cat /sys/class/net/eth0/operstate)" = up ]
ip addr show dev eth0 > /tmp/network-ready.txt
if [ "$ROLE" = server ]; then
    STAGE=server-setup
    for name in xhci-hcd xhci-pci-renesas xhci-pci; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    if [ "$BACKEND" = native ]; then for name in usbip-core usbip-host; do insmod "/modules/$name.ko"; done; fi
    for device_poll in $(seq 1 100); do
        legacy --json devices > /tmp/devices.json
        if jq -e 'length==2 and all(.[];.export_blockers==[])' /tmp/devices.json > /dev/null; then break; fi
        sleep 0.05
    done
    jq -e 'length==2 and all(.[];.export_blockers==[])' /tmp/devices.json > /dev/null
    HID_ID=$(jq -er '.[]|select(.vendor_id==1575 and .product_id==1)|.id' /tmp/devices.json)
    SERIAL_ID=$(jq -er '.[]|select(.vendor_id==1027 and .product_id==24577)|.id' /tmp/devices.json)
    HID_BUS=$(jq -er --arg id "$HID_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
    SERIAL_BUS=$(jq -er --arg id "$SERIAL_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
    if [ "$BACKEND" = native ]; then key_prefix=native; else key_prefix=physical; fi
    HID_KEY="$key_prefix:$HID_ID" SERIAL_KEY="$key_prefix:$SERIAL_ID"
    host_baseline /tmp/host-before.json
    for bus in "$HID_BUS" "$SERIAL_BUS"; do node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$bus/busnum")" "$(cat "/sys/bus/usb/devices/$bus/devnum")"); chmod 600 "$node"; done
    HID_NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$HID_BUS/busnum")" "$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")")
    : > /tmp/usb-nodes-before.jsonl
    for bus in "$HID_BUS" "$SERIAL_BUS"; do
        base="/sys/bus/usb/devices/$bus"
        node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "$base/busnum")" "$(cat "$base/devnum")")
        [ -c "$node" ] && [ ! -L "$node" ]
        jq -nc --arg bus "$bus" --arg node "$node" \
          --arg filesystem_device "$(stat -c '%d' "$node")" --arg inode "$(stat -c '%i' "$node")" \
          --arg rdev "$(stat -c '%t:%T' "$node")" --arg sysfs_dev "$(cat "$base/dev")" \
          --arg generation "$(stat -Lc '%d:%i' "$base")" --arg mode "$(stat -c '%a' "$node")" \
          --argjson uid "$(stat -c '%u' "$node")" --argjson links "$(stat -c '%h' "$node")" \
          '{bus:$bus,node:$node,filesystem_device:$filesystem_device,inode:$inode,rdev:$rdev,sysfs_dev:$sysfs_dev,generation:$generation,mode:$mode,uid:$uid,links:$links,character_device:true}' >> /tmp/usb-nodes-before.jsonl
    done
    jq -s 'sort_by(.bus)' /tmp/usb-nodes-before.jsonl > /tmp/usb-nodes-before.json
    jq -n --arg hid "$HID_ID" --arg serial "$SERIAL_ID" --arg backend "$BACKEND" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_native_device_ids:(if $backend=="native" then [$hid,$serial] else [] end),secure_physical_device_ids:(if $backend=="usbfs" then [$hid,$serial] else [] end),physical_connect_restore_device_ids:[]}' > /etc/rustusb/policy.json
    start_helper
    jq -nc --arg hid "$HID_KEY" --arg serial "$SERIAL_KEY" '[$hid,$serial]' > /tmp/keys.json
    echo "AGENT_KEYS $(cat /tmp/keys.json)"
    if [ "$FAMILY" = ipv4 ]; then address=192.0.2.1:3241; else address='[fd42:7275:7374::1]:3241'; fi
    jq -n --arg listen "$address" --arg pin "$(cat /etc/rustusb/client-pin.txt)" --slurpfile keys /tmp/keys.json '{schema_version:1,broker_uid:2000,listen:$listen,devices:$keys[0],principals:[{id:"qemu-client",pins:[$pin],devices:$keys[0]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server-pool.json
    chown 2000:2000 /etc/rustusb/server-key.der; chmod 600 /etc/rustusb/server-key.der
    jq -nc --arg backend "$BACKEND" '[{id:"pool",backend:$backend,config:"/etc/rustusb/server-pool.json"}]' > /tmp/agent-profiles.json
    manifest; traced_agent; AGENT_EPOCH=1
    fd_snapshot idle
    echo PREPARE_SERVER_READY
    host_expect START_TARGET
    STAGE=target-prepare
    if [ "$CASE" = usbfs-initializer ]; then ARMED_OPERATION=initializer; else ARMED_OPERATION=resource; fi
    atomic_control arm "$ARMED_OPERATION"
    # Observe a complete actual tracer event before creating the only initializer.
    # A partial trailing write is never accepted as an armed acknowledgement.
    for arm_poll in $(seq 1 100); do
        awk '/"event":"armed"/ && /}$/ {print}' "$GATE/events.jsonl" > /tmp/gate-armed.json
        if jq -es --arg operation "$ARMED_OPERATION" --slurpfile ready "$GATE/ready.json" 'length==1 and (.[0]|.event=="armed" and .nonce=="prepare-stop-v1" and .operation==$operation and .tracee_pid==$ready[0].tracee_pid and .tracee_starttime==$ready[0].tracee_starttime)' /tmp/gate-armed.json > /dev/null; then break; fi
        kill -0 "$TRACER_PID"; sleep 0.01
    done
    jq -es --arg operation "$ARMED_OPERATION" --slurpfile ready "$GATE/ready.json" 'length==1 and (.[0]|.event=="armed" and .nonce=="prepare-stop-v1" and .operation==$operation and .tracee_pid==$ready[0].tracee_pid and .tracee_starttime==$ready[0].tracee_starttime)' /tmp/gate-armed.json > /dev/null
    echo PREPARE_GATE_ARMED
    agent start pool > /tmp/start-pool-1.json 2> /tmp/start-pool-1.log & START_PID=$!
    select_candidate
    host_expect STOP_TARGET
    STAGE=held-stop
    read -r STOP_UPTIME STOP_IDLE < /proc/uptime
    if [ "$STOP_MODE" = job ]; then
        agent stop-job "$TARGET_JOB" > /tmp/stop-target.json 2> /tmp/stop-target.log & STOP_PID=$!
    else
        /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /bin/busybox kill -TERM "$AGENT_PID"
        STOP_PID=''
    fi
    RELEASED=false
    : > /tmp/held-observations.jsonl
    for observation in $(seq 1 500); do
        [ ! -f "$GATE/exit-$SELECTED.json" ]
        root_snapshot "observe-$observation"; fd_snapshot "observe-$observation"
        baseline_equal=false; no_fd=false; proof=false
        if cmp -s /tmp/host-before.json "/tmp/host-observe-$observation.json"; then baseline_equal=true; fi
        # The USBFS grant is a character FD. Native export ownership is the
        # supervised control UDS + root receipt; its full FD table is retained.
        if jq -e 'all(.[];.target|startswith("/dev/bus/usb/")|not)' "/tmp/fds-observe-$observation.json" > /dev/null; then no_fd=true; fi
        if check_target_proof "/tmp/root-observe-$observation.json"; then proof=true; fi
        read -r observed_uptime observed_idle < /proc/uptime
        jq -nc --argjson observation "$observation" --arg uptime "$observed_uptime" --argjson baseline "$baseline_equal" --argjson fd_closed "$no_fd" --argjson proof "$proof" '{observation:$observation,uptime:$uptime,baseline_equal:$baseline,target_fd_closed:$fd_closed,exact_root_terminal:$proof,gate_released:false}' >> /tmp/held-observations.jsonl
        if [ "$CASE" = usbfs-initializer ]; then
            if [ "$proof" = true ] || [ "$no_fd" = true ]; then CASE_FAILED=1; fi
            [ "$observation" -lt 5 ] || break
        elif [ "$baseline_equal:$no_fd:$proof" = true:true:true ]; then RELEASED=true; break; fi
        # Whole observation bounded by guest uptime, independent of slow sampling.
        elapsed=$(awk -v start="$STOP_UPTIME" -v now="$observed_uptime" 'BEGIN{print now-start}')
        if awk -v elapsed="$elapsed" 'BEGIN{exit !(elapsed>=15)}'; then break; fi
        sleep 0.01
    done
    if [ "$CASE" != usbfs-initializer ] && [ "$RELEASED" != true ]; then CASE_FAILED=1; fi
    root_snapshot before-release; fd_snapshot before-release
    jq -nc --arg case "$CASE" --arg stop "$STOP_MODE" --argjson failed "$CASE_FAILED" --argjson resource_released "$RELEASED" --arg uptime "$STOP_UPTIME" '{case:$case,stop:$stop,failed:$failed,resource_released_while_gate_held:$resource_released,stop_uptime:$uptime}' > /tmp/held-outcome.json
    atomic_control observe after
    for observe_poll in $(seq 1 100); do [ ! -f "$GATE/held-after.json" ] || break; sleep 0.01; done
    [ -f "$GATE/held-after.json" ]
    echo "PREPARE_OBSERVED failed=$CASE_FAILED"
    atomic_control "release-$SELECTED" release
    for release_poll in $(seq 1 300); do [ ! -f "$GATE/exit-$SELECTED.json" ] || break; sleep 0.01; done
    [ -f "$GATE/exit-$SELECTED.json" ]
    if [ -n "$STOP_PID" ]; then if wait "$STOP_PID"; then echo 0 > /tmp/wait-stop.txt; else echo "$?" > /tmp/wait-stop.txt; fi; fi
    wait "$START_PID"
    if [ "$STOP_MODE" = job ]; then
        wait_sessions pool /tmp/target-final.json '.response.sessions|length==2 and all(.[];.terminal and .cleanup=="verified")'
        kill -TERM "$AGENT_PID"
    fi
    # Tracer is the actual parent/reaper; shell waits the tracer exactly once.
    if wait "$TRACER_PID"; then echo 0 > /tmp/wait-tracer.txt; else trace_wait=$?; echo "$trace_wait" > /tmp/wait-tracer.txt; CASE_FAILED=1; fi
    TRACER_PID='' AGENT_PID=''
    root_snapshot after-release
    cmp /tmp/host-before.json /tmp/host-after-release.json
    check_target_proof /tmp/root-after-release.json
    jq -e '.records==[] and ((.exports // [])==[])' /tmp/root-after-release.json > /dev/null
    echo PREPARE_TARGET_CLOSED
    # The first failure remains latched; a fresh actual attempt only tests reuse.
    agent_start 2
    start_job pool
    wait_sessions pool /tmp/reuse-ready.json '.response.sessions|([.[]|select(.terminal==false and .role=="export" and .state=="Available")]|length)==2'
    root_snapshot reuse-ready
    echo PREPARE_REUSE_READY
    host_expect STOP_REUSE
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-2; AGENT_PID=''
    root_snapshot final
    cmp /tmp/host-before.json /tmp/host-final.json
    jq -e '.records==[] and ((.exports // [])==[])' /tmp/root-final.json > /dev/null
    echo PREPARE_SERVER_CLOSED
    host_expect FINISH
    if [ "$CASE_FAILED" != 0 ]; then STAGE=actual-held-cleanup-missing; exit 1; fi

else
    STAGE=client-setup
    for name in usbip-core vhci-hcd; do insmod "/modules/$name.ko"; done
    host_read; printf '%s\n' "$CONTROL" > /tmp/keys.json
    HID_KEY=$(jq -er '.[0]' /tmp/keys.json) SERIAL_KEY=$(jq -er '.[1]' /tmp/keys.json)
    jq -n --slurpfile keys /tmp/keys.json '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_imports:[{server_id:"qemu-server",device_keys:$keys[0]}]}' > /etc/rustusb/policy.json
    start_helper
    chown 2000:2000 /etc/rustusb/client-key.der; chmod 600 /etc/rustusb/client-key.der
    for profile in hid serial; do
        if [ "$profile" = hid ]; then key=$HID_KEY; else key=$SERIAL_KEY; fi
        port=3241
        jq -n --arg host "$HOST" --arg key "$key" --argjson port "$port" '{schema_version:1,broker_uid:2000,server_id:"qemu-server",endpoint:{host:$host,port:$port},server_name:"rustusb-qemu.test",device:$key,ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/client.der"],private_key:"/etc/rustusb/client-key.der"}' > "/etc/rustusb/client-$profile.json"
    done
    echo '[{"id":"hid","backend":"client","config":"/etc/rustusb/client-hid.json"},{"id":"serial","backend":"client","config":"/etc/rustusb/client-serial.json"}]' > /tmp/agent-profiles.json
    manifest
    # The common startup config collector assumes old file names; use this fixture collector.
    config_evidence() { actual_config; }
    agent_start 1
    echo PREPARE_CLIENT_READY
    host_expect HELD
    # No listener may bind while either initial resource acknowledgement is held.
    if timeout 5 /usr/bin/nc-openbsd -z -v -w 2 "$HOST" 3241 > /tmp/held-connect.stdout 2> /tmp/held-connect.stderr; then exit 1; else echo "$?" > /tmp/held-connect.wait; fi
    grep -F 'Connection refused' /tmp/held-connect.stderr
    echo PREPARE_LISTENER_REFUSED
    host_expect START_REUSE
    start_job hid; start_job serial
    wait_sessions hid /tmp/client-hid-connected.json '.response.sessions|any(.state=="Connected" and .terminal==false)'
    wait_sessions serial /tmp/client-serial-connected.json '.response.sessions|any(.state=="Connected" and .terminal==false)'
    record_client_locals reuse
    root_snapshot reused
    actual_io 1 yes
    echo PREPARE_CLIENT_REUSED
    host_expect SERVER_CLOSED
    wait_sessions hid /tmp/client-hid-final.json '.response.sessions|all(.terminal and .cleanup=="verified")'
    wait_sessions serial /tmp/client-serial-final.json '.response.sessions|all(.terminal and .cleanup=="verified")'
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-1; AGENT_PID=''
    await_empty /tmp/vhci-final.txt
    root_snapshot final
    echo PREPARE_CLIENT_CLOSED
    host_expect FINISH
fi
STAGE=finished
for gate_file in "$GATE"/*; do [ ! -f "$gate_file" ] || cp "$gate_file" "/tmp/gate-$(basename "$gate_file")"; done
echo "RUSTUSB_PREPARE_RESULT: PASS role=$ROLE case=$CASE stop=$STOP_MODE"
