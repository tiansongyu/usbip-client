#!/bin/sh
# Additional server-only flow for three separately judged negative contracts.
NEG_HELPER_EPOCH=0; NEG_ACTIVE=false; C_ACTIVE=false; NEG_SEQUENCE=0
NEG_HELPER=/usr/bin/rustusb-helper
[ "$NEGATIVE_SCENARIO" != legacy-schema1 ] || NEG_HELPER=/usr/libexec/rustusb-helper-legacy
for neg_file in helper-starts helper-logs readonly windows policies; do : > "/tmp/negative-$neg_file.jsonl"; done

autorestore_helper_identity() {
    helper_alive || return 1
    neg_exe=$(readlink "/proc/$HELPER_PID/exe") || return 1
    neg_uids=$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$HELPER_PID/status") || return 1
    [ "$neg_exe" = "$NEG_HELPER" ] && [ "$neg_uids" = 0:0:0:0 ] || return 1
    neg_sha=$(sha256sum "/proc/$HELPER_PID/exe" | cut -d' ' -f1) || return 1
    jq -e --arg path "$neg_exe" --arg sha "$neg_sha" 'any(.helpers[];.path==$path and .sha256==$sha)' /usr/lib/negative-binaries.json > /dev/null || return 1
    jq -Mcn --argjson epoch "$NEG_HELPER_EPOCH" --argjson pid "$HELPER_PID" --argjson start "$HELPER_START" --arg exe "$neg_exe" --arg sha "$neg_sha" --arg uids "$neg_uids" '{epoch:$epoch,pid:$pid,starttime:$start,exe:$exe,sha256:$sha,uids:($uids|split(":")|map(tonumber))}' > /tmp/autorestore-helper-identity.json || return 1
}
negative_policy_record() {
    neg_policy_sha=$(sha256sum /etc/rustusb/policy.json | cut -d' ' -f1) || return 1
    jq -Mcn --argjson epoch "$NEG_HELPER_EPOCH" --arg sha "$neg_policy_sha" --rawfile raw /etc/rustusb/policy.json '{helper_epoch:$epoch,sha256:$sha,raw:$raw,policy:($raw|fromjson)}' >> /tmp/negative-policies.jsonl || return 1
    printf 'RUSTUSB_AUTONEG_POLICY '; tail -n 1 /tmp/negative-policies.jsonl
}
start_helper() {
    [ "$NEG_ACTIVE" = false ]
    case "$NEG_HELPER" in /usr/bin/rustusb-helper|/usr/libexec/rustusb-helper-legacy) ;; *) return 1 ;; esac
    chmod 644 /etc/rustusb/policy.json
    NEG_HELPER_EPOCH=$((NEG_HELPER_EPOCH + 1))
    negative_policy_record
    (exec 3<&- 9<&-; exec "$NEG_HELPER" > /tmp/helper.log 2>&1) &
    HELPER_PID=$!
    for neg_try in $(seq 1 100); do [ ! -S /run/rustusb/control.sock ] || break; sleep 0.05; done
    [ -S /run/rustusb/control.sock ]
    HELPER_START=$(awk '{print $22}' "/proc/$HELPER_PID/stat")
    NEG_ACTIVE=true
    autorestore_helper_identity
    cat /tmp/autorestore-helper-identity.json >> /tmp/negative-helper-starts.jsonl
    printf 'RUSTUSB_AUTONEG_HELPER_START '; cat /tmp/autorestore-helper-identity.json
    unpriv --json doctor > /tmp/doctor.json
    jq -e '.schema_version==1 and .helper.status=="available" and .helper.peer_verified and .network_security_verified==false and .hardware_test_performed==false and .built_in_encryption==false' /tmp/doctor.json > /dev/null
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then return 1; fi
    grep -F 'caller UID is not approved by the administrator' /tmp/denied.log > /dev/null
}
negative_operations() {
    neg_lines=$(wc -l < /tmp/helper.log) || return 1
    : > /tmp/negative-complete-helper.log
    if [ "$neg_lines" -gt 0 ]; then sed -n "1,${neg_lines}p" /tmp/helper.log > /tmp/negative-complete-helper.log || return 1; fi
    jq -Rsc 'gsub("\u001b\\[[0-9;]*m";"") | split("\n") | map(select(contains("physical_auto_restore"))) | map(. as $line | (capture("lease_id=\"?(?<lease_id>[a-zA-Z0-9_-]+)\"? +phase=\"(?<phase>attempt|retired|failed)\"") // error("unknown automatic recovery event")) + {raw:$line})' /tmp/negative-complete-helper.log > /tmp/negative-operations.json || return 1
}
negative_log() {
    neg_log_mark=$1
    autorestore_helper_identity || return 1
    negative_operations || return 1
    jq -Mcn --arg mark "$neg_log_mark" --argjson tick "$(ticks)" --slurpfile helper /tmp/autorestore-helper-identity.json --slurpfile ops /tmp/negative-operations.json --rawfile log /tmp/negative-complete-helper.log '{label:$mark,tick:$tick,helper:$helper[0],operations:$ops[0],log:$log}' >> /tmp/negative-helper-logs.jsonl || return 1
    printf 'RUSTUSB_AUTONEG_HELPER_LOG '; tail -n 1 /tmp/negative-helper-logs.jsonl
}
# Preserve legacy final call site while using the negative identity/log schema.
autorestore_helper_log() { negative_log "$1"; }
negative_stop() {
    negative_log before-stop
    neg_old_epoch=$NEG_HELPER_EPOCH; neg_old_pid=$HELPER_PID; neg_old_start=$HELPER_START
    kill -TERM "$HELPER_PID"
    neg_until=$(( $(ticks) + 1000 ))
    while [ "$(ticks)" -lt "$neg_until" ]; do exited "$HELPER_PID" && break; sleep 0.05; done
    exited "$HELPER_PID"
    if wait "$HELPER_PID"; then neg_code=0; else neg_code=$?; fi
    jq -Mcn --argjson epoch "$neg_old_epoch" --argjson pid "$neg_old_pid" --argjson start "$neg_old_start" --argjson code "$neg_code" '{event:"negative_helper_wait",epoch:$epoch,pid:$pid,starttime:$start,wait_code:$code,signal:15}' >> /tmp/waits.jsonl
    printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl
    [ "$neg_code" = 0 ] && [ ! -S /run/rustusb/control.sock ]
    cp /tmp/helper.log "/tmp/negative-helper-$neg_old_epoch.log"
    NEG_ACTIVE=false
}
negative_begin() {
    neg_boot=$(cat /proc/sys/kernel/random/boot_id)
    jq -Mcn --arg scenario "$NEGATIVE_SCENARIO" --arg boot "$neg_boot" --slurpfile binaries /usr/lib/negative-binaries.json '{scenario:$scenario,boot_id:$boot,binaries:$binaries[0],rejection_window_centiseconds:2000,no_retry_window_centiseconds:500,tail_automatic_recovery:($scenario!="legacy-schema1")}' > /tmp/negative-metadata.json
    printf 'RUSTUSB_AUTONEG_METADATA '; cat /tmp/negative-metadata.json
}
negative_policy() {
    [ "$NEG_ACTIVE" = false ]
    if [ "$1" = enabled ]; then neg_allow=true; else [ "$1" = disabled ]; neg_allow=false; fi
    jq -n --arg hid "$HID_ID" --arg ftdi "$FTDI_ID" --argjson allow "$neg_allow" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[$hid,$ftdi],physical_connect_restore_device_ids:(if $allow then [$hid,$ftdi] else [] end),peers:[],trusted_network:true}' > /etc/rustusb/policy.json
}
negative_c_identity() {
    if [ "$C_ACTIVE" = false ]; then printf 'null\n' > /tmp/negative-c-identity.json; return 0; fi
    kill -0 "$C_PID" || return 1
    [ "$(awk '{print $22}' "/proc/$C_PID/stat")" = "$C_START" ] || return 1
    [ "$(readlink "/proc/$C_PID/exe")" = /usr/bin/usbfs-external-claim-probe ] || return 1
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$C_PID/status")" = 0:0:0:0 ] || return 1
    neg_c_sha=$(sha256sum "/proc/$C_PID/exe" | cut -d' ' -f1) || return 1
    jq -e --arg sha "$neg_c_sha" '.external_claim_probe.sha256==$sha' /usr/lib/negative-binaries.json > /dev/null || return 1
    : > /tmp/negative-c-fds.jsonl
    for neg_node in "$HID_NODE" "$FTDI_NODE"; do
        neg_found=0
        for neg_fd in /proc/"$C_PID"/fd/*; do
            if [ "$(readlink "$neg_fd")" = "$neg_node" ]; then
                neg_found=$((neg_found + 1))
                neg_identity=$(stat -L -c '%d:%i:%t:%T' "$neg_fd") || return 1
                [ "$neg_identity" = "$(stat -L -c '%d:%i:%t:%T' "$neg_node")" ] || return 1
                jq -Mcn --arg node "$neg_node" --argjson fd "${neg_fd##*/}" --arg identity "$neg_identity" '{node:$node,fd:$fd,identity:$identity}' >> /tmp/negative-c-fds.jsonl || return 1
            fi
        done
        [ "$neg_found" = 1 ] || return 1
    done
    jq -sc --argjson pid "$C_PID" --argjson start "$C_START" --arg sha "$neg_c_sha" '{pid:$pid,starttime:$start,sha256:$sha,exe:"/usr/bin/usbfs-external-claim-probe",uids:[0,0,0,0],fds:.}' /tmp/negative-c-fds.jsonl > /tmp/negative-c-identity.json || return 1
}
negative_readonly() {
    neg_mark=$1
    sysfs_observation || return 1
    if [ "$NEG_ACTIVE" = true ]; then autorestore_helper_identity || return 1; else printf 'null\n' > /tmp/autorestore-helper-identity.json; fi
    negative_c_identity || return 1
    cp /var/lib/rustusb/physical/journal.json /tmp/negative-disk.json || return 1
    neg_dm=$(stat -c '%u:%a' /var/lib/rustusb/physical) || return 1
    neg_fm=$(stat -c '%u:%a' /var/lib/rustusb/physical/journal.json) || return 1
    [ "$neg_dm:$neg_fm" = 0:700:0:600 ] || return 1
    neg_sha=$(sha256sum /tmp/negative-disk.json | cut -d' ' -f1) || return 1
    NEG_SEQUENCE=$((NEG_SEQUENCE + 1))
    jq -Mcn --arg mark "$neg_mark" --argjson seq "$NEG_SEQUENCE" --argjson tick "$(ticks)" --arg sha "$neg_sha" --arg dm "$neg_dm" --arg fm "$neg_fm" --slurpfile sysfs /tmp/sysfs.json --rawfile disk /tmp/negative-disk.json --slurpfile helper /tmp/autorestore-helper-identity.json --slurpfile external /tmp/negative-c-identity.json '{label:$mark,sequence:$seq,tick:$tick,sysfs:$sysfs[0],journal:($disk|fromjson),journal_raw:$disk,journal_sha256:$sha,journal_directory_owner_mode:$dm,journal_file_owner_mode:$fm,helper:$helper[0],external:$external[0],pending_api_called:false}' > /tmp/negative-readonly.json || return 1
    cat /tmp/negative-readonly.json >> /tmp/negative-readonly.jsonl || return 1
    printf 'RUSTUSB_AUTONEG_READONLY '; cat /tmp/negative-readonly.json
}
negative_watch() {
    neg_watch_mark=$1; neg_duration=$2; neg_driver=$3
    neg_start=$(ticks); neg_deadline=$((neg_start + neg_duration))
    while :; do
        negative_readonly "$neg_watch_mark"
        cmp /tmp/negative-killed-disk.json /tmp/negative-disk.json
        if [ "$neg_driver" = original ]; then
            jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json > /dev/null
        else
            jq -e --arg driver "$neg_driver" --slurpfile original /tmp/original-sysfs.json '.==($original[0]|map(.interfaces |= map(.driver=(if $driver=="null" then null else $driver end))))' /tmp/sysfs.json > /dev/null
        fi
        [ "$(ticks)" -lt "$neg_deadline" ] || break
        sleep 0.2
    done
    jq -Mcn --arg mark "$neg_watch_mark" --argjson start "$neg_start" --argjson finish "$(ticks)" --argjson minimum "$neg_duration" '{label:$mark,start_tick:$start,end_tick:$finish,minimum_centiseconds:$minimum}' >> /tmp/negative-windows.jsonl
    printf 'RUSTUSB_AUTONEG_WINDOW '; tail -n 1 /tmp/negative-windows.jsonl
    negative_log "$neg_watch_mark"
}
negative_c_drain() {
    neg_c_lines=$(wc -l < /tmp/negative-c.log) || return 1
    if [ "$neg_c_lines" -gt "$C_LINES" ]; then sed -n "$((C_LINES + 1)),${neg_c_lines}p" /tmp/negative-c.log || return 1; C_LINES=$neg_c_lines; fi
    sed -n "1,${C_LINES}s/^RUSTUSB_EXTERNAL_C //p" /tmp/negative-c.log > /tmp/negative-c-events.jsonl || return 1
}
negative_c_start() {
    [ "$NEG_ACTIVE" = false ]
    mkdir -m 700 /run/negative-c
    mkfifo -m 600 /run/negative-c/control
    neg_c_dir=$(stat -c '%u:%a' /run/negative-c)
    neg_c_fifo=$(stat -c '%u:%a' /run/negative-c/control)
    [ -p /run/negative-c/control ] && [ "$neg_c_dir:$neg_c_fifo" = 0:700:0:600 ]
    jq -Mcn --arg dir "$neg_c_dir" --arg fifo "$neg_c_fifo" '{directory_owner_mode:$dir,fifo_owner_mode:$fifo,is_fifo:true,shell_holds_control_fd:false}' > /tmp/negative-c-control.json
    printf 'RUSTUSB_AUTONEG_C_CONTROL '; cat /tmp/negative-c-control.json
    : > /tmp/negative-c.log
    (exec 3<&- 9<&-; exec 0<> /run/negative-c/control; exec /usr/bin/usbfs-external-claim-probe > /tmp/negative-c.log 2>&1) &
    C_PID=$!; C_START=$(awk '{print $22}' "/proc/$C_PID/stat"); C_LINES=0; C_ACTIVE=true
    neg_c_until=$(( $(ticks) + 500 ))
    while [ "$(ticks)" -lt "$neg_c_until" ]; do
        negative_c_drain
        if jq -se --argjson pid "$C_PID" --argjson start "$C_START" '[.[]|select(.event=="ready" and .pid==$pid and .starttime==$start and .uid==0)]|length==1' /tmp/negative-c-events.jsonl > /dev/null; then break; fi
        kill -0 "$C_PID"; sleep 0.05
    done
    jq -se '[.[]|select(.event=="ready")]|length==1' /tmp/negative-c-events.jsonl > /dev/null
    negative_c_identity
    negative_readonly c-claimed-before-helper
}
negative_c_release() {
    negative_c_identity
    printf 'release\n' | timeout 3 /bin/busybox tee /run/negative-c/control > /dev/null
    neg_c_until=$(( $(ticks) + 500 ))
    while [ "$(ticks)" -lt "$neg_c_until" ]; do negative_c_drain; exited "$C_PID" && break; sleep 0.05; done
    exited "$C_PID"
    if wait "$C_PID"; then neg_c_code=0; else neg_c_code=$?; fi
    negative_c_drain
    jq -Mcn --argjson pid "$C_PID" --argjson start "$C_START" --argjson code "$neg_c_code" '{event:"external_c_wait",pid:$pid,starttime:$start,wait_code:$code}' >> /tmp/waits.jsonl
    printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl
    C_ACTIVE=false
    [ "$neg_c_code" = 0 ]
}
negative_wait_failed() {
    neg_fail_until=$(( $(ticks) + 500 ))
    while [ "$(ticks)" -lt "$neg_fail_until" ]; do
        negative_operations
        if jq -e --slurpfile disk /tmp/negative-killed-disk.json 'length==4 and ([.[]|select(.phase=="failed")|.lease_id]|sort)==([$disk[0].records[].lease_id]|sort) and ([.[]|select(.phase=="attempt")|.lease_id]|sort)==([$disk[0].records[].lease_id]|sort)' /tmp/negative-operations.json > /dev/null; then return 0; fi
        helper_alive; sleep 0.05
    done
    return 1
}
negative_restore() {
    neg_restore_start=$(ticks); neg_restore_until=$((neg_restore_start + 2000)); neg_restored=false
    while [ "$(ticks)" -lt "$neg_restore_until" ]; do
        negative_readonly tail-automatic-recovery
        if jq -e --slurpfile original /tmp/original-sysfs.json '.sysfs==$original[0] and .journal.schema_version==2 and .journal.records==[]' /tmp/negative-readonly.json > /dev/null; then neg_restored=true; break; fi
        sleep 0.05
    done
    [ "$neg_restored" = true ]
    printf 'RUSTUSB_AUTONEG_RECOVERY_PRIMARY '; cat /tmp/negative-readonly.json
    jq -Mcn --argjson start "$neg_restore_start" --argjson finish "$(ticks)" '{label:"tail-automatic-recovery",start_tick:$start,end_tick:$finish,maximum_centiseconds:2000}' >> /tmp/negative-windows.jsonl
    printf 'RUSTUSB_AUTONEG_WINDOW '; tail -n 1 /tmp/negative-windows.jsonl
    record tail-retired
    records_count 0
    negative_log tail-restored
}
negative_killed_epoch() {
    STAGE=negative-killed-epoch
    start_ready killed
    send_control 192.0.2.2 cycle-2-ready
    receive_control cycle-2-io
    actual_pending
    cp /tmp/pending.json /tmp/killed-pending.json
    cp /var/lib/rustusb/physical/journal.json /tmp/negative-killed-disk.json
    negative_stop
    server_identity
    KILL_PID=$SERVER_PID; KILL_START=$SERVER_START
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox kill -KILL "$KILL_PID"
    echo "RUSTUSB_JOURNAL_SIGNAL: PASS signal=9 count=1 sender_uid=1000 pid=$KILL_PID starttime=$KILL_START"
    neg_until=$(( $(ticks) + 1000 ))
    while [ "$(ticks)" -lt "$neg_until" ]; do exited "$KILL_PID" && break; sleep 0.05; done
    exited "$KILL_PID"
    if wait "$KILL_PID"; then neg_code=0; else neg_code=$?; fi
    jq -Mcn --argjson code "$neg_code" --argjson pid "$KILL_PID" --argjson start "$KILL_START" '{label:"killed",event:"server_wait",wait_code:$code,signal:9,signal_count:1,sender_uid:1000,pid:$pid,starttime:$start,final_backend_snapshot:null,final_backend_snapshot_observable:false}' >> /tmp/waits.jsonl
    printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl
    [ "$neg_code" = 137 ] && [ ! -e "/proc/$KILL_PID" ]
    dump_epoch
    send_control 192.0.2.2 cycle-2-killed
    negative_readonly killed-with-helper-stopped
    jq -e --slurpfile original /tmp/original-sysfs.json '.==($original[0]|map(.interfaces |= map(.driver=null)))' /tmp/sysfs.json > /dev/null
    NEG_HELPER=/usr/bin/rustusb-helper
    if [ "$NEGATIVE_SCENARIO" = policy-revoked ]; then negative_policy disabled; else negative_policy enabled; fi
    if [ "$NEGATIVE_SCENARIO" = external-claim ]; then negative_c_start; fi
    start_helper
    STAGE=negative-rejection-window
    if [ "$NEGATIVE_SCENARIO" = external-claim ]; then
        negative_wait_failed
        negative_watch rejected 2000 usbfs
    else
        negative_watch rejected 2000 null
        jq -e 'length==0' /tmp/negative-operations.json > /dev/null
    fi
    printf 'RUSTUSB_AUTONEG_REJECTION_PRIMARY '; cat /tmp/negative-readonly.json
    record negative-observed
    refused_server negative-new-grant
    negative_log after-refused-grant
    send_control 192.0.2.2 cycle-2-server-observed
    receive_control cycle-2-observed
    STAGE=negative-explicit-tail
    if [ "$NEGATIVE_SCENARIO" = legacy-schema1 ]; then
        negative_stop
        printf '%s' "$HID_BUS:1.0" > /sys/bus/usb/drivers/usbhid/bind
        printf '%s' "$FTDI_BUS:1.0" > /sys/bus/usb/drivers/ftdi_sio/bind
        start_helper
        negative_watch manual-restored-still-pending 500 original
        jq -e 'length==0' /tmp/negative-operations.json > /dev/null
        record manual-restored
        for neg_lease in $(jq -r '.records[].lease_id' /tmp/killed-pending.json); do
            /usr/bin/rustusb --json physical-reconcile "$neg_lease" > /tmp/api.json 2> /tmp/api-error.log
            jq -e '.status=="retired" and .observation=="restored"' /tmp/api.json > /dev/null
            jq -Mcn --arg lease "$neg_lease" --rawfile out /tmp/api.json --rawfile err /tmp/api-error.log '{phase:"legacy-manual-retired",lease_id:$lease,uid:0,exit_code:0,stdout:$out,stderr:$err}' >> /tmp/admin-responses.jsonl
        done
        record tail-retired
        records_count 0
        negative_log tail-manual-retired
    else
        if [ "$NEGATIVE_SCENARIO" = external-claim ]; then
            negative_c_release
            negative_watch released-no-retry 500 null
        fi
        negative_stop
        negative_policy enabled
        start_helper
        negative_restore
    fi
}
