#!/bin/sh
# Functions sourced only by the generated disposable server init.
guardian_drain() {
    guardian_lines=$(wc -l < /tmp/guardian-probe.log)
    if [ "$guardian_lines" -gt "$GUARDIAN_LINES" ]; then
        sed -n "$((GUARDIAN_LINES + 1)),${guardian_lines}p" /tmp/guardian-probe.log
        GUARDIAN_LINES=$guardian_lines
    fi
}
guardian_alive() {
    kill -0 "$GUARDIAN_PID" 2>/dev/null &&
        [ "$(awk '{print $22}' "/proc/$GUARDIAN_PID/stat" 2>/dev/null)" = "$GUARDIAN_START" ]
}
guardian_json() {
    if [ "$GUARDIAN_LINES" -gt 0 ]; then
        sed -n "1,${GUARDIAN_LINES}s/^RUSTUSB_GUARDIAN_PROBE //p" /tmp/guardian-probe.log > /tmp/guardian-probe.jsonl
    else
        : > /tmp/guardian-probe.jsonl
    fi
}
guardian_command() {
    guardian_alive || return 1
    GUARDIAN_SEQ=$((GUARDIAN_SEQ + 1))
    printf '%s %s\n' "$GUARDIAN_SEQ" "$*" | timeout 3 /bin/busybox tee /run/guardian/control >/dev/null || return 1
    guardian_until=$(( $(ticks) + 500 ))
    while [ "$(ticks)" -lt "$guardian_until" ]; do
        guardian_drain || return 1
        guardian_json || return 1
        if jq -se --argjson seq "$GUARDIAN_SEQ" 'any(.[]; .event=="fatal" and .seq==$seq)' /tmp/guardian-probe.jsonl >/dev/null; then return 1; fi
        if jq -se --argjson seq "$GUARDIAN_SEQ" '[.[]|select(.event=="done" and .seq==$seq)]|length==1' /tmp/guardian-probe.jsonl >/dev/null; then
            jq -sc --argjson seq "$GUARDIAN_SEQ" '[.[]|select(.seq==$seq)]' /tmp/guardian-probe.jsonl > /tmp/guardian-reply.json
            return 0
        fi
        sleep 0.05
    done
    echo 'RUSTUSB_GUARDIAN_COMMAND_TIMEOUT'; return 1
}
guardian_ioctl() {
    guardian_expected=$1; shift
    guardian_command "$@" || return 1
    if [ "$guardian_expected" = busy ]; then
        jq -e '[.[]|select(.event=="ioctl")]|length==1 and .[0].rc == -1 and .[0].errno == 16' /tmp/guardian-reply.json >/dev/null || return 1
    elif [ "$1" = connect ]; then
        jq -e '[.[]|select(.event=="ioctl")]|length==1 and .[0].rc >= 0 and .[0].errno == 0' /tmp/guardian-reply.json >/dev/null || return 1
    else
        jq -e '[.[]|select(.event=="ioctl")]|length==1 and .[0].rc == 0 and .[0].errno == 0' /tmp/guardian-reply.json >/dev/null || return 1
    fi
}
guardian_record() {
    guardian_tag=$1
    sysfs_observation || return 1
    : > /tmp/guardian-full-interfaces.jsonl
    for guardian_bus in "$HID_BUS" "$FTDI_BUS"; do
        : > /tmp/guardian-one-interfaces.jsonl
        for guardian_intf in "/sys/bus/usb/devices/$guardian_bus":*; do
            [ -d "$guardian_intf" ] || return 1
            guardian_number=$(cat "$guardian_intf/bInterfaceNumber" | trim) || return 1
            guardian_alt=$(cat "$guardian_intf/bAlternateSetting" | trim) || return 1
            guardian_driver=$(readlink "$guardian_intf/driver" 2>/dev/null | sed 's#.*/##')
            guardian_number=$((0x$guardian_number))
            jq -Mcn --argjson number "$guardian_number" --argjson alternate "$guardian_alt" --arg driver "$guardian_driver" '{number:$number,alternate:$alternate,driver:(if $driver=="" then null else $driver end)}' >> /tmp/guardian-one-interfaces.jsonl || return 1
        done
        jq -sc --arg bus "$guardian_bus" '{bus_id:$bus,interfaces:(sort_by(.number))}' /tmp/guardian-one-interfaces.jsonl >> /tmp/guardian-full-interfaces.jsonl || return 1
    done
    jq -s '.' /tmp/guardian-full-interfaces.jsonl > /tmp/guardian-full-interfaces.json || return 1
    jq -e --slurpfile full /tmp/guardian-full-interfaces.json 'map({bus_id,interfaces})==$full[0]' /tmp/sysfs.json >/dev/null || return 1
    /usr/bin/rustusb --json physical-pending > /tmp/guardian-pending.json || return 1
    guardian_alive || return 1
    : > /tmp/guardian-fds.jsonl
    for guardian_fd in /proc/"$GUARDIAN_PID"/fd/*; do
        guardian_target=$(readlink "$guardian_fd") || return 1
        if [ "$guardian_target" = "$HID_NODE" ] || [ "$guardian_target" = "$FTDI_NODE" ]; then
            guardian_identity=$(stat -L -c '%d:%i:%t:%T' "$guardian_fd") || return 1
            jq -Mcn --arg fd "${guardian_fd##*/}" --arg node "$guardian_target" --arg identity "$guardian_identity" '{fd:($fd|tonumber),node:$node,identity:$identity}' >> /tmp/guardian-fds.jsonl || return 1
        fi
    done
    jq -s '.' /tmp/guardian-fds.jsonl > /tmp/guardian-fds.json || return 1
    guardian_boot=$(cat /proc/sys/kernel/random/boot_id) || return 1
    guardian_uid=$(awk '/^Uid:/{print $2 ":" $3 ":" $4 ":" $5}' "/proc/$GUARDIAN_PID/status") || return 1
    [ "$guardian_uid" = 0:0:0:0 ] || return 1
    jq -Mcn --arg label "$guardian_tag" --arg boot "$guardian_boot" --argjson seq "$GUARDIAN_SEQ" --argjson pid "$GUARDIAN_PID" --argjson start "$GUARDIAN_START" --argjson tick "$(ticks)" --slurpfile original /tmp/original-sysfs.json --slurpfile sysfs /tmp/sysfs.json --slurpfile full /tmp/guardian-full-interfaces.json --slurpfile pending /tmp/guardian-pending.json --slurpfile disk /var/lib/rustusb/physical/journal.json --slurpfile fds /tmp/guardian-fds.json '{label:$label,seq:$seq,tick:$tick,boot_id:$boot,guardian:{pid:$pid,starttime:$start,uids:[0,0,0,0],fds:$fds[0]},original:$original[0],sysfs:$sysfs[0],full_interfaces:$full[0],pending:$pending[0],journal:$disk[0]}' > /tmp/guardian-record.json || return 1
    printf 'RUSTUSB_GUARDIAN_RECORD '; cat /tmp/guardian-record.json
}
guardian_start() {
    [ "$HID_NODE" = /dev/bus/usb/001/002 ] && [ "$FTDI_NODE" = /dev/bus/usb/001/003 ] || return 1
    [ "$HID_BUS" = 1-1 ] && [ "$FTDI_BUS" = 1-2 ] || return 1
    mkdir -m 700 /run/guardian || return 1
    mkfifo -m 600 /run/guardian/control || return 1
    : > /tmp/guardian-probe.log
    # Only the root probe holds the FIFO open read/write. The init shell never
    # retains a control FD that a later unprivileged CLI could inherit.
    (exec 3<&- 9<&-; exec 0<> /run/guardian/control; exec /usr/bin/usbfs-guardian-probe > /tmp/guardian-probe.log 2>&1) &
    GUARDIAN_PID=$!
    GUARDIAN_START=$(awk '{print $22}' "/proc/$GUARDIAN_PID/stat") || return 1
    GUARDIAN_SEQ=0; GUARDIAN_LINES=0
    for guardian_poll in $(seq 1 100); do
        guardian_drain || return 1
        guardian_json || return 1
        if jq -se --argjson pid "$GUARDIAN_PID" --argjson start "$GUARDIAN_START" '[.[]|select(.event=="ready" and .pid==$pid and .starttime==$start and .uid==0)]|length==1' /tmp/guardian-probe.jsonl >/dev/null; then
            guardian_record b-opened-before-import || return 1
            return 0
        fi
        guardian_alive || return 1
        sleep 0.05
    done
    return 1
}
guardian_arm() {
    server_identity || return 1
    guardian_hid_fd=$(jq -er --arg node "$HID_NODE" '[.[]|select(.node==$node)]|select(length==1)|.[0].fd' /tmp/fds.json) || return 1
    guardian_ftdi_fd=$(jq -er --arg node "$FTDI_NODE" '[.[]|select(.node==$node)]|select(length==1)|.[0].fd' /tmp/fds.json) || return 1
    guardian_command arm "$SERVER_PID" "$SERVER_START" "$guardian_hid_fd" "$guardian_ftdi_fd" || return 1
    guardian_record dup-armed-live || return 1
    ordinary_identity "$SERVER_PID" "$SERVER_START" || return 1
}
guardian_after_kill() {
    guardian_until=$(( $(ticks) + 2000 ))
    while [ "$(ticks)" -lt "$guardian_until" ]; do exited "$KILL_PID" && break; sleep 0.05; done
    exited "$KILL_PID" || return 1
    if wait "$KILL_PID"; then GUARDIAN_KILL_WAIT=0; else GUARDIAN_KILL_WAIT=$?; fi
    jq -Mcn --argjson pid "$KILL_PID" --argjson start "$KILL_START" --argjson code "$GUARDIAN_KILL_WAIT" '{pid:$pid,starttime:$start,wait_code:$code,signal:9,signal_count:1,terminal_resources:null}' > /tmp/guardian-kill-wait.json || return 1
    printf 'RUSTUSB_GUARDIAN_WAIT '; cat /tmp/guardian-kill-wait.json
    [ "$GUARDIAN_KILL_WAIT" = 137 ] && [ ! -e "/proc/$KILL_PID" ] || return 1
    guardian_record dup-retained-after-wait137 || return 1
    jq -e 'all(.[];.interfaces==[{number:0,alternate:0,driver:"usbfs"}])' /tmp/sysfs.json >/dev/null || return 1
    for guardian_device in 0 1; do
        guardian_ioctl busy claim B "$guardian_device" || return 1
        guardian_ioctl busy connect "$guardian_device" || return 1
    done
    guardian_record dup-refusal-preserved || return 1
    guardian_command drop || return 1
    for guardian_poll in $(seq 1 100); do
        sysfs_observation || return 1
        jq -e 'all(.[];.interfaces==[{number:0,alternate:0,driver:null}])' /tmp/sysfs.json >/dev/null && break
        sleep 0.05
    done
    jq -e 'all(.[];.interfaces==[{number:0,alternate:0,driver:null}])' /tmp/sysfs.json >/dev/null || return 1
    guardian_record duplicate-closed-b-retained || return 1
    for guardian_device in 0 1; do guardian_ioctl ok claim B "$guardian_device" || return 1; done
    guardian_record independent-b-claimed || return 1
    for guardian_device in 0 1; do guardian_ioctl ok release B "$guardian_device" || return 1; done
    guardian_record independent-b-released || return 1
    guardian_command copen || return 1
    for guardian_device in 0 1; do guardian_ioctl ok claim C "$guardian_device" || return 1; done
    guardian_record external-c-claimed || return 1
    for guardian_device in 0 1; do
        guardian_ioctl busy claim B "$guardian_device" || return 1
        guardian_ioctl busy connect "$guardian_device" || return 1
    done
    guardian_record external-c-preserved || return 1
    for guardian_device in 0 1; do guardian_ioctl ok release C "$guardian_device" || return 1; done
    guardian_command cclose || return 1
    guardian_record external-c-closed || return 1
    jq -e 'all(.[];.interfaces==[{number:0,alternate:0,driver:null}])' /tmp/sysfs.json >/dev/null || return 1
}
guardian_restore() {
    guardian_record before-b-connect || return 1
    for guardian_device in 0 1; do guardian_ioctl ok connect "$guardian_device" || return 1; done
    for guardian_poll in $(seq 1 100); do
        sysfs_observation || return 1
        jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json >/dev/null && break
        sleep 0.05
    done
    jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json >/dev/null || return 1
    guardian_record b-restored-original || return 1
    guardian_command quit || return 1
    guardian_until=$(( $(ticks) + 500 ))
    while [ "$(ticks)" -lt "$guardian_until" ]; do exited "$GUARDIAN_PID" && break; sleep 0.05; done
    exited "$GUARDIAN_PID" || return 1
    if wait "$GUARDIAN_PID"; then guardian_code=0; else guardian_code=$?; fi
    guardian_drain || return 1
    jq -Mcn --argjson pid "$GUARDIAN_PID" --argjson start "$GUARDIAN_START" --argjson code "$guardian_code" '{pid:$pid,starttime:$start,wait_code:$code,role:"guardian"}' > /tmp/guardian-final-wait.json || return 1
    printf 'RUSTUSB_GUARDIAN_WAIT '; cat /tmp/guardian-final-wait.json
    [ "$guardian_code" = 0 ] && [ ! -e "/proc/$GUARDIAN_PID" ] || return 1
    echo 'RUSTUSB_GUARDIAN_COLLECTION_COMPLETE role=server'
}
