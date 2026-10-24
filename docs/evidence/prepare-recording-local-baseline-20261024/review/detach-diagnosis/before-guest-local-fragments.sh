#!/bin/sh
# Source-locked composition fragments. Never execute this file as a guest init.
exit 2
# BEGIN FUNCTIONS
local_candidate() {
    if [ "$CASE" = native-resource ]; then
        jq -e --arg key "$SERIAL_KEY" '.audit[-1] as $a | .sessions as $s | $a.action=="resource" and $a.profile=="pool" and ($s|length)==2 and all($s[];.role=="export" and .terminal==false and (.attempts|length)==1 and .attempts[0].resource_id!=null) and any($s[];.id==$a.session_id and .device_key==$key)' "$1" > /dev/null
    else
        jq -e --arg key "$SERIAL_KEY" '.audit[-1] as $a | .sessions as $s | $a.action=="observe" and $a.profile=="pool" and $a.state=="Available" and $a.evidence=="export_ready" and ($s|length)==2 and all($s[];.role=="export" and .state=="Available" and .terminal==false and (.attempts|length)==1 and .attempts[0].resource_id!=null) and any($s[];.id==$a.session_id and .device_key==$key)' "$1" > /dev/null
    fi
}
local_identify() {
    TARGET_SESSION=$(jq -er '.audit[-1].session_id' /tmp/selected-state.json)
    jq -ce --arg id "$TARGET_SESSION" --arg key "$SERIAL_KEY" '.[]|select(.id==$id and .device_key==$key)' /tmp/selected-sessions.json > /tmp/local-target.json
    jq -ce --arg key "$HID_KEY" '.[]|select(.device_key==$key)' /tmp/selected-sessions.json > /tmp/local-survivor.json
    SURVIVOR_SESSION=$(jq -er .id /tmp/local-survivor.json)
    TARGET_BUS=$SERIAL_BUS
}
local_root_proof() {
    jq -e --slurpfile target /tmp/local-target.json --slurpfile survivor /tmp/local-survivor.json --slurpfile original /tmp/root-held.json '
      $target[0] as $t | $survivor[0] as $s | $t.attempts[0] as $a |
      .records==[] and .exports==[$original[0].exports[]|select(.lease_id==$s.attempts[0].resource_id)] and
      (.exports|length)==1 and (.terminals|length)==1 and
      any(.terminals[].terminal;.resource_id==$a.resource_id and .kind=="native_export" and .owner_uid==2000 and .correlation==$a.correlation and .device_key==$t.device_key and .proof=="native_export_restored")' "$1" > /dev/null
}
local_host_proof() {
    jq -e --arg target "$TARGET_BUS" --slurpfile before /tmp/host-before.json --slurpfile held /tmp/host-held.json '
      length==2 and .==([$before[0][]|select(.bus==$target)]+[$held[0][]|select(.bus!=$target)]|sort_by(.bus))' "$1" > /dev/null
}
# END FUNCTIONS
# BEGIN SERVER
    local_identify
    host_expect STOP_TARGET
    STAGE=local-held-stop
    read -r STOP_UPTIME STOP_IDLE < /proc/uptime
    agent stop-session "$TARGET_SESSION" > /tmp/stop-target.json 2> /tmp/stop-target.log & STOP_PID=$!
    LOCAL_RELEASED=false
    : > /tmp/local-held-observations.jsonl
    for observation in $(seq 1 500); do
        [ ! -f "$GATE/exit-$SELECTED.json" ]
        root_snapshot "observe-$observation"; fd_snapshot "observe-$observation"
        jq -e '.schema_version==2 and (.exports|type)=="array" and (.terminals|type)=="array"' "/tmp/root-observe-$observation.json" > /dev/null
        root_ok=false host_ok=false
        if local_root_proof "/tmp/root-observe-$observation.json"; then root_ok=true; fi
        if local_host_proof "/tmp/host-observe-$observation.json"; then host_ok=true; fi
        read -r observed_uptime observed_idle < /proc/uptime
        jq -nc --argjson observation "$observation" --arg uptime "$observed_uptime" --argjson root "$root_ok" --argjson host "$host_ok" '{observation:$observation,uptime:$uptime,target_exact_terminal_and_original_sibling:$root,target_restored_and_sibling_bound:$host,gate_released:false}' >> /tmp/local-held-observations.jsonl
        if [ "$root_ok:$host_ok" = true:true ]; then LOCAL_RELEASED=true; break; fi
        if awk -v start="$STOP_UPTIME" -v now="$observed_uptime" 'BEGIN{exit !(now-start>=15)}'; then break; fi
        sleep 0.01
    done
    [ "$LOCAL_RELEASED" = true ] || CASE_FAILED=1
    root_snapshot before-release; fd_snapshot before-release
    jq -nc --arg case "$CASE" --arg target "$TARGET_SESSION" --arg survivor "$SURVIVOR_SESSION" --argjson failed "$CASE_FAILED" --argjson released "$LOCAL_RELEASED" '{case:$case,stop:"session",target:$target,survivor:$survivor,failed:$failed,local_cleanup_while_held:$released}' > /tmp/local-held-outcome.json
    atomic_control observe after
    for observe_poll in $(seq 1 100); do [ ! -f "$GATE/held-after.json" ] || break; sleep 0.01; done
    [ -f "$GATE/held-after.json" ]
    echo "PREPARE_LOCAL_OBSERVED failed=$CASE_FAILED"
    atomic_control "release-$SELECTED" release
    for release_poll in $(seq 1 300); do [ ! -f "$GATE/exit-$SELECTED.json" ] || break; sleep 0.01; done
    [ -f "$GATE/exit-$SELECTED.json" ]
    if wait "$STOP_PID"; then echo 0 > /tmp/wait-stop.txt; else echo "$?" > /tmp/wait-stop.txt; fi
    wait "$START_PID"
    wait_sessions pool /tmp/local-sessions.json '.response.sessions|length==2 and any(.device_key|startswith("native:")) and any(.terminal and .cleanup=="verified") and any(.terminal==false and .state=="Available")'
    jq -e --arg target "$TARGET_SESSION" --arg survivor "$SURVIVOR_SESSION" '.response.sessions|any(.id==$target and .terminal and .cleanup=="verified") and any(.id==$survivor and .terminal==false and .state=="Available")' /tmp/local-sessions.json > /dev/null
    agent jobs pool > /tmp/local-jobs.json
    jq -e --arg id "$TARGET_JOB" '.response.jobs|length==1 and .[0].id==$id and .[0].state=="running"' /tmp/local-jobs.json > /dev/null
    root_snapshot local-after-ack
    local_root_proof /tmp/root-local-after-ack.json
    local_host_proof /tmp/host-local-after-ack.json
    echo PREPARE_LOCAL_SURVIVOR_READY
    host_expect SURVIVOR_IO
    root_snapshot local-survivor-io
    echo PREPARE_LOCAL_DETACH_HID
    host_expect SURVIVOR_DETACHED
    root_snapshot local-after-detach
    agent stop-session "$SURVIVOR_SESSION" > /tmp/local-stop-survivor.json
    jq -e '.response.result=="stop_accepted" and .response.owner_present' /tmp/local-stop-survivor.json > /dev/null
    wait_sessions pool /tmp/local-empty-sessions.json '.response.sessions|length==3 and all(.terminal and .cleanup=="verified")'
    root_snapshot local-empty
    cmp /tmp/host-before.json /tmp/host-local-empty.json
    check_target_proof
    jq -e '.records==[] and .exports==[] and (.terminals|length)==3' /tmp/root-local-empty.json > /dev/null
    agent jobs pool > /tmp/local-empty-jobs.json
    jq -e --arg id "$TARGET_JOB" '.response.jobs|length==1 and .[0].id==$id and .[0].state=="running"' /tmp/local-empty-jobs.json > /dev/null
    cat /proc/net/tcp /proc/net/tcp6 > /tmp/local-empty-listeners.txt
    echo PREPARE_LOCAL_EMPTY_READY
    host_expect EMPTY_CATALOG
    # Whole-job stop follows actual empty-catalog TLS exchange, not last export close.
    kill -TERM "$AGENT_PID"
    if wait "$TRACER_PID"; then echo 0 > /tmp/wait-tracer.txt; else trace_wait=$?; echo "$trace_wait" > /tmp/wait-tracer.txt; CASE_FAILED=1; fi
    TRACER_PID='' AGENT_PID=''
    root_snapshot after-release
    cmp /tmp/host-before.json /tmp/host-after-release.json
    check_target_proof
    jq -e '.records==[] and .exports==[]' /tmp/root-after-release.json > /dev/null
    echo PREPARE_LOCAL_JOB_CLOSED
    host_expect SURVIVOR_CLOSED
# END SERVER
# BEGIN CLIENT
    echo PREPARE_CLIENT_READY
    host_expect HELD
    if [ "$CASE" = native-resource ]; then
        if timeout 5 /usr/bin/nc-openbsd -z -v -w 2 "$HOST" 3241 > /tmp/held-connect.stdout 2> /tmp/held-connect.stderr; then exit 1; else echo "$?" > /tmp/held-connect.wait; fi
        grep -F 'Connection refused' /tmp/held-connect.stderr
    else
        timeout 5 /usr/bin/nc-openbsd -z -v -w 2 "$HOST" 3241 > /tmp/held-connect.stdout 2> /tmp/held-connect.stderr
        echo 0 > /tmp/held-connect.wait
    fi
    echo PREPARE_LOCAL_LISTENER_PROBED
    host_expect START_SURVIVOR
    agent discover hid > /tmp/local-catalog.json
    jq -e --arg key "$HID_KEY" '.response.result=="catalog" and (.response.devices|length)==1 and .response.devices[0].device_key==$key' /tmp/local-catalog.json > /dev/null
    cp /var/lib/rustusb/native/channels.json /tmp/local-before-denied-root.json
    start_job serial
    cp /tmp/start-serial-1.json /tmp/local-denied-start.json
    wait_sessions serial /tmp/local-denied-sessions.json '.response.sessions|length==1 and all(.[];.terminal and .operation_error=="permission" and .cleanup=="not_started" and .attempts==[])'
    cp /var/lib/rustusb/native/channels.json /tmp/local-after-denied-root.json
    cmp /tmp/local-before-denied-root.json /tmp/local-after-denied-root.json
    start_job hid
    cp /tmp/start-hid-1.json /tmp/local-survivor-start.json
    wait_sessions hid /tmp/local-survivor-sessions.json '.response.sessions|length==1 and all(.[];.state=="Connected" and .terminal==false)'
    cp /var/lib/rustusb/native/channels.json /tmp/local-survivor-receipt.json
    jq -e --arg key "$HID_KEY" '.schema_version==2 and (.records|length)==1 and .records[0].target.device_key==$key and .records[0].state.phase=="observed" and .records[0].state.receipt.session.status==6' /tmp/local-survivor-receipt.json > /dev/null
    HID_LOCAL=$(jq -er '.records[0].state.receipt.session.local_bus_id' /tmp/local-survivor-receipt.json)
    for input_poll in $(seq 1 150); do
        EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
        [ -z "$EVENT" ] || break
        sleep 0.05
    done
    [ -n "$EVENT" ]
    EVENT=/dev/input/$(basename "$EVENT")
    : > /tmp/local-survivor-input.bin
    exec 3< "$EVENT"
    cat <&3 3<&- 5<&- > /tmp/local-survivor-input.bin & INPUT_PID=$!
    exec 3<&-
    echo PREPARE_LOCAL_WAIT_HID
    for input_poll in $(seq 1 150); do
        if od -An -tu4 -w24 -v /tmp/local-survivor-input.bin | awk 'NF==6 && $5==131073 && $6==1 {down=1} NF==6 && $5==131073 && $6==0 {up=1} END {exit !(down&&up)}'; then break; fi
        sleep 0.05
    done
    od -An -tu4 -w24 -v /tmp/local-survivor-input.bin | awk 'NF==6 && $5==131073 && $6==1 {down=1} NF==6 && $5==131073 && $6==0 {up=1} END {exit !(down&&up)}'
    kill "$INPUT_PID"; wait "$INPUT_PID" || true
    root_snapshot local-survivor-io
    echo PREPARE_LOCAL_SURVIVOR_IO
    host_expect DETACH_SURVIVOR
    LOCAL_IMPORT_SESSION=$(jq -er '.response.sessions[0].id' /tmp/local-survivor-sessions.json)
    agent stop-session "$LOCAL_IMPORT_SESSION" > /tmp/local-stop-import.json
    jq -e '.response.result=="stop_accepted" and .response.owner_present' /tmp/local-stop-import.json > /dev/null
    wait_sessions hid /tmp/local-detached-sessions.json '.response.sessions|length==1 and all(.[];.terminal and .cleanup=="verified")'
    await_empty /tmp/local-detached-vhci.txt
    root_snapshot local-detached
    echo PREPARE_LOCAL_SURVIVOR_DETACHED
    host_expect EMPTY_CATALOG
    agent discover hid > /tmp/local-empty-catalog.json
    jq -e '.response.result=="catalog" and .response.devices==[]' /tmp/local-empty-catalog.json > /dev/null
    echo PREPARE_LOCAL_EMPTY_CATALOG
    host_expect LOCAL_JOB_CLOSED
    wait_sessions hid /tmp/local-survivor-final.json '.response.sessions|length==1 and all(.[];.terminal and .cleanup=="verified")'
    await_empty /tmp/local-vhci-empty.txt
    root_snapshot local-job-closed
    echo PREPARE_LOCAL_SURVIVOR_CLOSED
# END CLIENT
