#!/bin/sh
# Sourced only by the generated disposable guest init. No USBFS test actor.
# All device observations below are read-only; only the product helper restores.
sysfs_observation() {
    : > /tmp/sysfs-devices.jsonl
    for auto_which in hid ftdi; do
        if [ "$auto_which" = hid ]; then auto_bus=$HID_BUS; auto_id=$HID_ID; auto_node=$HID_NODE; else auto_bus=$FTDI_BUS; auto_id=$FTDI_ID; auto_node=$FTDI_NODE; fi
        auto_base=/sys/bus/usb/devices/$auto_bus
        auto_gen=$(stat -L -c '%d:%i' "$auto_base") || return 1
        auto_ni=$(stat -L -c '%d:%i:%t:%T' "$auto_node") || return 1
        auto_mode=$(stat -c '%u:%a' "$auto_node") || return 1
        auto_cfg=$(cat "$auto_base/bConfigurationValue" | trim) || return 1
        auto_dn=$(cat "$auto_base/devnum" | trim) || return 1
        printf '%s\n' "$auto_cfg" "$auto_dn" | grep -E '^[0-9]+$' > /dev/null || return 1
        : > /tmp/autorestore-interfaces.jsonl
        for auto_intf in "$auto_base":*; do
            [ -d "$auto_intf" ] || return 1
            auto_number=$(cat "$auto_intf/bInterfaceNumber" | trim) || return 1
            auto_alt=$(cat "$auto_intf/bAlternateSetting" | trim) || return 1
            printf '%s\n' "$auto_number" | grep -E '^[0-9a-fA-F]{2}$' > /dev/null || return 1
            printf '%s\n' "$auto_alt" | grep -E '^[0-9]+$' > /dev/null || return 1
            auto_number=$((0x$auto_number))
            auto_driver=$(readlink "$auto_intf/driver" 2>/dev/null | sed 's#.*/##')
            jq -Mcn --argjson number "$auto_number" --argjson alternate "$auto_alt" --arg driver "$auto_driver" '{number:$number,alternate:$alternate,driver:(if $driver=="" then null else $driver end)}' >> /tmp/autorestore-interfaces.jsonl || return 1
        done
        jq -sc 'sort_by(.number)' /tmp/autorestore-interfaces.jsonl > /tmp/autorestore-interfaces.json || return 1
        jq -e 'length>0 and ([.[].number]|length==(unique|length))' /tmp/autorestore-interfaces.json > /dev/null || return 1
        jq -Mcn --arg id "$auto_id" --arg bus "$auto_bus" --arg node "$auto_node" --arg gen "$auto_gen" --arg ni "$auto_ni" --arg mode "$auto_mode" --argjson cfg "$auto_cfg" --argjson dn "$auto_dn" --slurpfile interfaces /tmp/autorestore-interfaces.json '{device_id:$id,bus_id:$bus,node:$node,generation:$gen,node_identity:$ni,node_owner_mode:$mode,devnum:$dn,configuration:$cfg,interfaces:$interfaces[0]}' >> /tmp/sysfs-devices.jsonl || return 1
    done
    jq -s '.' /tmp/sysfs-devices.jsonl > /tmp/sysfs.json || return 1
}

autorestore_helper_identity() {
    helper_alive || return 1
    auto_exe=$(readlink "/proc/$HELPER_PID/exe") || return 1
    auto_uids=$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$HELPER_PID/status") || return 1
    [ "$auto_exe" = /usr/bin/rustusb-helper ] && [ "$auto_uids" = 0:0:0:0 ] || return 1
    jq -Mcn --argjson pid "$HELPER_PID" --argjson start "$HELPER_START" --arg exe "$auto_exe" --arg uids "$auto_uids" '{pid:$pid,starttime:$start,exe:$exe,uids:($uids|split(":")|map(tonumber))}' > /tmp/autorestore-helper-identity.json || return 1
}

autorestore_helper_log() {
    auto_log_mark=$1
    autorestore_helper_identity || return 1
    auto_lines=$(wc -l < /tmp/helper.log) || return 1
    : > /tmp/autorestore-complete-helper.log
    if [ "$auto_lines" -gt 0 ]; then sed -n "1,${auto_lines}p" /tmp/helper.log > /tmp/autorestore-complete-helper.log || return 1; fi
    jq -Mcn --arg mark "$auto_log_mark" --argjson tick "$(ticks)" --slurpfile helper /tmp/autorestore-helper-identity.json --rawfile log /tmp/autorestore-complete-helper.log '{label:$mark,tick:$tick,helper:$helper[0],log:$log}' >> /tmp/autorestore-helper-logs.jsonl || return 1
    printf 'RUSTUSB_AUTORESTORE_HELPER '; tail -n 1 /tmp/autorestore-helper-logs.jsonl
}

autorestore_begin() {
    : > /tmp/autorestore-helper-logs.jsonl
    : > /tmp/autorestore-readonly.jsonl
    AUTO_SEQUENCE=0
    auto_boot=$(cat /proc/sys/kernel/random/boot_id) || return 1
    jq -Mcn --arg scenario "$AUTORESTORE_SCENARIO" --arg boot "$auto_boot" --slurpfile policy /etc/rustusb/policy.json '{scenario:$scenario,boot_id:$boot,policy:$policy[0],recovery_deadline_centiseconds:2000,live_window_centiseconds:500,product_tick_milliseconds:250,usbfs_test_probe:false,administrator_reconcile:false}' > /tmp/autorestore-metadata.json || return 1
    printf 'RUSTUSB_AUTORESTORE_METADATA '; cat /tmp/autorestore-metadata.json
    autorestore_helper_log initial || return 1
}

# This function deliberately does not call any CLI or read the helper socket.
# The copied disk image is one actual open of the atomically replaced file.
autorestore_readonly() {
    auto_phase=$1
    auto_owner_mode=$2
    sysfs_observation || return 1
    autorestore_helper_identity || return 1
    cp /var/lib/rustusb/physical/journal.json /tmp/autorestore-disk.json || return 1
    jq -e '.schema_version==2 and (.records|type)=="array"' /tmp/autorestore-disk.json > /dev/null || return 1
    auto_dm=$(stat -c '%u:%a' /var/lib/rustusb/physical) || return 1
    auto_fm=$(stat -c '%u:%a' /var/lib/rustusb/physical/journal.json) || return 1
    [ "$auto_dm:$auto_fm" = 0:700:0:600 ] || return 1
    auto_sha=$(sha256sum /tmp/autorestore-disk.json | cut -d' ' -f1) || return 1
    if [ "$auto_owner_mode" = alive ]; then
        server_identity || return 1
        cp /tmp/identity.json /tmp/autorestore-owner.json || return 1
    else
        [ "$auto_owner_mode" = reaped ] && [ ! -e "/proc/$KILL_PID" ] || return 1
        printf 'null\n' > /tmp/autorestore-owner.json
    fi
    AUTO_SEQUENCE=$((AUTO_SEQUENCE + 1))
    jq -Mcn --arg phase "$auto_phase" --argjson sequence "$AUTO_SEQUENCE" --argjson tick "$(ticks)" --arg dm "$auto_dm" --arg fm "$auto_fm" --arg sha "$auto_sha" --slurpfile sysfs /tmp/sysfs.json --slurpfile disk /tmp/autorestore-disk.json --rawfile disk_raw /tmp/autorestore-disk.json --slurpfile owner /tmp/autorestore-owner.json --slurpfile helper /tmp/autorestore-helper-identity.json '{phase:$phase,sequence:$sequence,tick:$tick,sysfs:$sysfs[0],journal:$disk[0],journal_raw:$disk_raw,journal_directory_owner_mode:$dm,journal_file_owner_mode:$fm,journal_sha256:$sha,owner:$owner[0],helper:$helper[0],pending_api_called:false}' > /tmp/autorestore-readonly.json || return 1
    cat /tmp/autorestore-readonly.json >> /tmp/autorestore-readonly.jsonl || return 1
    printf 'RUSTUSB_AUTORESTORE_READONLY '; cat /tmp/autorestore-readonly.json
}

autorestore_live_restart() {
    STAGE=autorestore-live-owner-before-restart
    receive_control cycle-2-attached
    snapshot attached
    observation killed-attached
    record killed-attached
    jq -e '.buffer_bytes_reserved>0 and (.devices|length)==2 and all(.devices[];.state=="leased" and .leased and all(.current_drivers[];.driver=="usbfs"))' /tmp/snapshot.json > /dev/null || return 1
    jq -e --slurpfile original /tmp/original-sysfs.json '.==($original[0]|map(.interfaces |= map(.driver="usbfs")))' /tmp/sysfs.json > /dev/null || return 1
    cp /tmp/pending.json /tmp/autorestore-live-pending.json || return 1
    cp /var/lib/rustusb/physical/journal.json /tmp/autorestore-live-disk.json || return 1
    autorestore_helper_log live-before-restart || return 1
    OLD_HELPER_PID=$HELPER_PID; OLD_HELPER_START=$HELPER_START
    kill -TERM "$HELPER_PID" || return 1
    auto_until=$(( $(ticks) + 1000 ))
    while [ "$(ticks)" -lt "$auto_until" ]; do exited "$HELPER_PID" && break; sleep 0.05; done
    exited "$HELPER_PID" || return 1
    if wait "$HELPER_PID"; then helper_code=0; else helper_code=$?; fi
    jq -Mcn --argjson old "$OLD_HELPER_PID" --argjson start "$OLD_HELPER_START" --argjson code "$helper_code" '{event:"helper_stop",old_pid:$old,old_starttime:$start,old_wait_code:$code}' >> /tmp/waits.jsonl || return 1
    printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl
    [ "$helper_code" = 0 ] && [ ! -S /run/rustusb/control.sock ] || return 1
    cp /tmp/helper.log /tmp/helper-before-restart.log || return 1
    ordinary_identity "$SERVER_PID" "$SERVER_START" || return 1
    start_helper
    [ "$HELPER_PID" != "$OLD_HELPER_PID" ] || return 1
    jq -Mcn --argjson old "$OLD_HELPER_PID" --argjson oldstart "$OLD_HELPER_START" --argjson new "$HELPER_PID" --argjson newstart "$HELPER_START" --argjson code "$helper_code" '{event:"helper_restart",old_pid:$old,old_starttime:$oldstart,new_pid:$new,new_starttime:$newstart,old_wait_code:$code,state_directory_unchanged:true}' >> /tmp/waits.jsonl || return 1
    printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl
    snapshot helper-reloaded
    observation helper-reloaded
    record helper-reloaded
    jq -e --slurpfile before /tmp/autorestore-live-pending.json '.==$before[0]' /tmp/pending.json > /dev/null || return 1
    autorestore_helper_log live-after-restart || return 1
    STAGE=autorestore-live-owner-window
    AUTO_LIVE_START=$(ticks)
    auto_until=$((AUTO_LIVE_START + 500))
    while [ "$(ticks)" -lt "$auto_until" ]; do
        autorestore_readonly live-owner alive || return 1
        jq -e --slurpfile before /tmp/autorestore-live-disk.json '.==$before[0]' /tmp/autorestore-disk.json > /dev/null || return 1
        jq -e --slurpfile original /tmp/original-sysfs.json '.==($original[0]|map(.interfaces |= map(.driver="usbfs")))' /tmp/sysfs.json > /dev/null || return 1
        sleep 0.1
    done
    autorestore_readonly live-owner alive || return 1
    jq -e --slurpfile before /tmp/autorestore-live-disk.json '.==$before[0]' /tmp/autorestore-disk.json > /dev/null || return 1
    jq -e --slurpfile original /tmp/original-sysfs.json '.==($original[0]|map(.interfaces |= map(.driver="usbfs")))' /tmp/sysfs.json > /dev/null || return 1
    snapshot live-held
    observation live-held
    record live-held
    jq -e --slurpfile before /tmp/autorestore-live-pending.json '.==$before[0]' /tmp/pending.json > /dev/null || return 1
    autorestore_helper_log live-before-io || return 1
    if grep -F 'physical_auto_restore' /tmp/helper.log > /dev/null; then echo 'unexpected restoration operation while owner is alive'; return 1; fi
    jq -Mcn --argjson start "$AUTO_LIVE_START" --argjson finish "$(ticks)" --argjson owner "$SERVER_PID" --argjson ownerstart "$SERVER_START" '{start_tick:$start,end_tick:$finish,owner_pid:$owner,owner_starttime:$ownerstart}' > /tmp/autorestore-live-window.json || return 1
    printf 'RUSTUSB_AUTORESTORE_LIVE_WINDOW '; cat /tmp/autorestore-live-window.json
    send_control 192.0.2.2 cycle-2-io-allowed
}

autorestore_killed_epoch() {
    STAGE=autorestore-killed-epoch
    start_ready killed
    send_control 192.0.2.2 cycle-2-ready
    if [ "$AUTORESTORE_SCENARIO" = helper-restart ]; then autorestore_live_restart; fi
    receive_control cycle-2-io
    actual_pending
    cp /tmp/pending.json /tmp/killed-pending.json || return 1
    cp /var/lib/rustusb/physical/journal.json /tmp/autorestore-killed-disk.json || return 1
    server_identity || return 1
    helper_alive || return 1
    autorestore_helper_log before-kill || return 1
    if grep -F 'physical_auto_restore' /tmp/helper.log > /dev/null; then echo 'unexpected restoration operation before owner death'; return 1; fi
    KILL_PID=$SERVER_PID; KILL_START=$SERVER_START
    STAGE=autorestore-kill-and-reap
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox kill -KILL "$KILL_PID" || return 1
    echo "RUSTUSB_JOURNAL_SIGNAL: PASS signal=9 count=1 sender_uid=1000 pid=$KILL_PID starttime=$KILL_START"
    auto_until=$(( $(ticks) + 1000 ))
    while [ "$(ticks)" -lt "$auto_until" ]; do exited "$KILL_PID" && break; sleep 0.05; done
    exited "$KILL_PID" || return 1
    if wait "$KILL_PID"; then code=0; else code=$?; fi
    jq -Mcn --arg epoch "$EPOCH" --argjson code "$code" --argjson pid "$KILL_PID" --argjson start "$KILL_START" '{label:$epoch,event:"server_wait",wait_code:$code,signal:9,signal_count:1,sender_uid:1000,pid:$pid,starttime:$start,final_backend_snapshot:null,final_backend_snapshot_observable:false}' >> /tmp/waits.jsonl || return 1
    printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl
    [ "$code" = 137 ] && [ ! -e "/proc/$KILL_PID" ] || return 1
    dump_epoch
    send_control 192.0.2.2 cycle-2-killed
    STAGE=autorestore-readonly-recovery-window
    AUTO_RECOVERY_START=$(ticks)
    auto_until=$((AUTO_RECOVERY_START + 2000))
    AUTO_RECOVERED=false
    while [ "$(ticks)" -lt "$auto_until" ]; do
        autorestore_readonly after-owner-reaped reaped || return 1
        if jq -e --slurpfile original /tmp/original-sysfs.json '.sysfs==$original[0] and .journal.schema_version==2 and .journal.records==[]' /tmp/autorestore-readonly.json > /dev/null; then
            cp /tmp/autorestore-readonly.json /tmp/autorestore-primary.json || return 1
            AUTO_RECOVERED=true
            break
        fi
        sleep 0.05
    done
    [ "$AUTO_RECOVERED" = true ] || return 1
    printf 'RUSTUSB_AUTORESTORE_PRIMARY '; cat /tmp/autorestore-primary.json
    jq -Mcn --argjson start "$AUTO_RECOVERY_START" --argjson finish "$(ticks)" '{start_tick:$start,end_tick:$finish,pending_api_before_primary:false}' > /tmp/autorestore-recovery-window.json || return 1
    printf 'RUSTUSB_AUTORESTORE_RECOVERY_WINDOW '; cat /tmp/autorestore-recovery-window.json
    # First helper API observation after the pure disk/sysfs success above.
    record automatically-restored
    records_count 0
    autorestore_helper_log restored || return 1
    send_control 192.0.2.2 cycle-2-server-observed
    receive_control cycle-2-observed
}
