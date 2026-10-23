#!/bin/sh
# Disposable guest only: one owned SIGKILL and the first no-IMPORT restart.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo 'Refusing to run outside the disposable QEMU guest init' >&2; exit 2;
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.crash_test=1 '*) ;; *) exit 2 ;; esac
ROLE=unknown
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in rustusb.role=*) ROLE=${parameter#*=} ;; esac
done
[ "$ROLE" = server ] || [ "$ROLE" = client ]
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/crash /etc/rustusb /var/run/vhci_hcd
mount -t devpts devpts /dev/pts
STAGE=boot
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_CRASH_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        for file in /tmp/helper.log /tmp/initial.log /tmp/initial.events /tmp/primary-outcome.json /tmp/restart.log /tmp/restart.events /tmp/restart-outcome.json /tmp/observations.jsonl /tmp/devices.json /tmp/sessions.json /tmp/doctor.json /tmp/denied.log /tmp/denied-open.json /tmp/outcome.json; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
    fi
    sync
    poweroff -f
}
trap cleanup EXIT
STAGE=evidence-channel
[ -c /dev/ttyS1 ]
stty -F /dev/ttyS1 raw -echo 115200 clocal -hupcl
exec > /dev/ttyS1 2>&1
echo "RUSTUSB_CRASH_EVIDENCE_CHANNEL: PASS role=$ROLE device=ttyS1 kernel_console=ttyS0"
echo "RUSTUSB_CRASH_BOOT role=$ROLE kernel=$(uname -r)"
for name in usb-common usbcore e1000 evdev hid hid-generic usbhid usbserial ftdi_sio; do
    [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
done
ifconfig lo 127.0.0.1 up
if [ "$ROLE" = server ]; then
    for name in xhci-hcd xhci-pci-renesas xhci-pci; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    ifconfig eth0 192.0.2.1 netmask 255.255.255.0 up
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
else
    for name in usbip-core vhci-hcd; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    ifconfig eth0 192.0.2.2 netmask 255.255.255.0 up
fi
for attempt in $(seq 1 100); do [ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && break; sleep 0.05; done
[ "$(cat /sys/class/net/eth0/carrier)" = 1 ]
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = 0 ]
echo "RUSTUSB_CRASH_ISOLATION: PASS role=$ROLE carrier=1 ip_forward=0"
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
probe() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/physical-wire-probe 192.0.2.1:3240 "$@"; }
ticks() { awk '{printf "%.0f\n", $1 * 100}' /proc/uptime; }
trim() { tr -d ' \t\r\n'; }
send_control() {
    # Fixed coordination tokens only; these retries never resend a USB request.
    for attempt in $(seq 1 100); do
        if printf '%s' "$2" | nc -w 1 "$1" 5001; then return 0; fi
        sleep 0.1
    done
    echo "control delivery failed: $2"; return 1
}
receive_control() {
    MESSAGE=$(timeout 40 nc -l -p 5001)
    [ "$MESSAGE" = "$1" ] || { echo "unexpected control message: $MESSAGE expected=$1"; return 1; }
}
start_helper() {
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &
    HELPER_PID=$!
    for attempt in $(seq 1 100); do [ ! -S /run/rustusb/control.sock ] || break; sleep 0.05; done
    [ -S /run/rustusb/control.sock ]
    HELPER_START=$(awk '{print $22}' "/proc/$HELPER_PID/stat")
    unpriv --json doctor > /tmp/doctor.json
    jq -e '.schema_version == 1 and .helper.status == "available" and .helper.peer_verified == true and .network_security_verified == false and .hardware_test_performed == false and .built_in_encryption == false' /tmp/doctor.json > /dev/null
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then return 1; fi
    grep -F 'caller UID is not approved by the administrator' /tmp/denied.log > /dev/null
    echo "RUSTUSB_CRASH_HELPER: PASS role=$ROLE allowed_uid=1000 denied_uid=1001"
}
helper_alive() {
    kill -0 "$HELPER_PID" 2>/dev/null && [ "$(awk '{print $22}' "/proc/$HELPER_PID/stat" 2>/dev/null)" = "$HELPER_START" ]
}
ordinary_identity() {
    kill -0 "$1" || return 1
    [ "$(readlink "/proc/$1/exe")" = /usr/bin/rustusb ] || return 1
    [ "$(awk '{print $22}' "/proc/$1/stat")" = "$2" ] || return 1
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$1/status")" = 1000:1000:1000:1000 ] || return 1
    for cap in CapEff CapPrm CapAmb; do
        [ "$(awk -v cap="$cap:" '$1 == cap {print $2}' "/proc/$1/status")" = 0000000000000000 ] || return 1
    done
}
if [ "$ROLE" = server ]; then
    STAGE=select-simulated-devices
    for attempt in $(seq 1 100); do
        unpriv --json devices > /tmp/devices.json
        jq -e 'length == 2 and all(.[]; (.export_blockers | length) == 0)' /tmp/devices.json > /dev/null && break
        sleep 0.05
    done
    jq -e 'length == 2 and all(.[]; (.export_blockers | length) == 0)' /tmp/devices.json > /dev/null
    HID_ID=$(jq -r '.[] | select(.vendor_id == 1575 and .product_id == 1) | .id' /tmp/devices.json)
    FTDI_ID=$(jq -r '.[] | select(.vendor_id == 1027 and .product_id == 24577) | .id' /tmp/devices.json)
    HID_BUS=$(jq -r '.[] | select(.vendor_id == 1575 and .product_id == 1) | .bus_id' /tmp/devices.json)
    FTDI_BUS=$(jq -r '.[] | select(.vendor_id == 1027 and .product_id == 24577) | .bus_id' /tmp/devices.json)
    [ -n "$HID_ID" ] && [ -n "$FTDI_ID" ] && [ "$HID_ID" != "$FTDI_ID" ]
    HID_NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$HID_BUS/busnum")" "$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")")
    FTDI_NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$FTDI_BUS/busnum")" "$(cat "/sys/bus/usb/devices/$FTDI_BUS/devnum")")
    for node in "$HID_NODE" "$FTDI_NODE"; do
        [ -c "$node" ] && [ "$(stat -c '%u' "$node")" = 0 ]
        chmod 600 "$node"
        for uid in 1000 1001; do
            if /usr/bin/setpriv --reuid="$uid" --regid="$uid" --clear-groups /bin/busybox sh -c 'exec 3<> "$1"' sh "$node" > /tmp/direct-open.log 2>&1; then exit 1; fi
        done
    done
    sysfs_observation() {
        : > /tmp/sysfs-devices.jsonl
        for which in hid ftdi; do
            if [ "$which" = hid ]; then bus=$HID_BUS; id=$HID_ID; node=$HID_NODE; else bus=$FTDI_BUS; id=$FTDI_ID; node=$FTDI_NODE; fi
            base=/sys/bus/usb/devices/$bus
            generation=$(stat -L -c '%d:%i' "$base" 2>/dev/null || true)
            node_identity=$(stat -L -c '%d:%i:%t:%T' "$node" 2>/dev/null || true)
            mode=$(stat -c '%u:%a' "$node" 2>/dev/null || true)
            config=$(cat "$base/bConfigurationValue" 2>/dev/null | trim)
            devnum=$(cat "$base/devnum" 2>/dev/null | trim)
            alt=$(cat "$base:1.0/bAlternateSetting" 2>/dev/null | trim)
            driver=$(readlink "$base:1.0/driver" 2>/dev/null | sed 's#.*/##')
            jq -Mcn --arg id "$id" --arg bus "$bus" --arg node "$node" --arg gen "$generation" --arg ni "$node_identity" --arg mode "$mode" --arg cfg "$config" --arg dn "$devnum" --arg alt "$alt" --arg driver "$driver" \
                '{device_id:$id,bus_id:$bus,node:$node,generation:$gen,node_identity:$ni,node_owner_mode:$mode,devnum:(try ($dn|tonumber) catch null),configuration:(try ($cfg|tonumber) catch null),interfaces:[{number:0,alternate:(try ($alt|tonumber) catch null),driver:(if $driver == "" then null else $driver end)}]}' >> /tmp/sysfs-devices.jsonl
        done
        jq -s '.' /tmp/sysfs-devices.jsonl > /tmp/sysfs.json
    }
    for attempt in $(seq 1 150); do
        sysfs_observation
        jq -e 'length == 2 and .[0].interfaces == [{number:0,alternate:0,driver:"usbhid"}] and .[1].interfaces == [{number:0,alternate:0,driver:"ftdi_sio"}]' /tmp/sysfs.json > /dev/null && break
        sleep 0.05
    done
    jq -e 'length == 2 and all(.[]; .configuration == 1 and .node_owner_mode == "0:600" and (.generation|length)>0 and (.node_identity|length)>0) and .[0].interfaces == [{number:0,alternate:0,driver:"usbhid"}] and .[1].interfaces == [{number:0,alternate:0,driver:"ftdi_sio"}]' /tmp/sysfs.json > /dev/null
    cp /tmp/sysfs.json /tmp/original-sysfs.json
    jq -n --arg hid "$HID_ID" --arg ftdi "$FTDI_ID" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[$hid,$ftdi],peers:[],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    jq -e '.helper.kernel_available == false' /tmp/doctor.json > /dev/null
    /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/physical-wire-probe helper-denied "$HID_ID" > /tmp/denied-open.json
    jq -e '.ok == false and .data == null and (.error|contains("caller UID is not approved by the administrator"))' /tmp/denied-open.json > /dev/null
    echo 'RUSTUSB_CRASH_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600'
    chown 1000:1000 /run/crash
    chmod 700 /run/crash
    launch_server() {
        EPOCH=$1
        CONTROL=/run/crash/$EPOCH.control
        EVENTS=/tmp/$EPOCH.events
        mkfifo -m 600 "$CONTROL"
        chown 1000:1000 "$CONTROL"
        if [ "$EPOCH" = initial ]; then exec 3<> "$CONTROL"; else exec 9<> "$CONTROL"; fi
        (
            exec 3<&- 9<&-
            exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb physical-server \
                --device-id "$HID_ID" --device-id "$FTDI_ID" --listen 192.0.2.1:3240 \
                --allow-insecure-test-network --allow-peer 192.0.2.2 --control-stdin \
                < "$CONTROL" > "$EVENTS" 2> "/tmp/$EPOCH.log"
        ) &
        SERVER_PID=$!
        SERVER_START=$(awk '{print $22}' "/proc/$SERVER_PID/stat" 2>/dev/null || true)
    }
    exited() {
        state=$(awk '{print $3}' "/proc/$1/stat" 2>/dev/null || true)
        [ -z "$state" ] || [ "$state" = Z ]
    }
    server_identity() {
        ordinary_identity "$SERVER_PID" "$SERVER_START" || return 1
        : > /tmp/fds.jsonl || return 1
        for node in "$HID_NODE" "$FTDI_NODE"; do
            found=false
            for fd in /proc/"$SERVER_PID"/fd/*; do
                if [ "$(readlink "$fd")" = "$node" ]; then
                    found=true
                    identity=$(stat -L -c '%d:%i:%t:%T' "$fd") || return 1
                    node_identity=$(stat -L -c '%d:%i:%t:%T' "$node") || return 1
                    [ "$identity" = "$node_identity" ] || return 1
                    jq -Mcn --arg node "$node" --arg fd "${fd##*/}" --arg identity "$identity" '{node:$node,fd:($fd|tonumber),identity:$identity}' >> /tmp/fds.jsonl || return 1
                fi
            done
            [ "$found" = true ] || return 1
        done
        jq -s '.' /tmp/fds.jsonl > /tmp/fds.json || return 1
        jq -Mcn --argjson pid "$SERVER_PID" --argjson start "$SERVER_START" --slurpfile fds /tmp/fds.json \
            '{pid:$pid,starttime:$start,exe:"/usr/bin/rustusb",uids:[1000,1000,1000,1000],cap_effective:0,cap_permitted:0,cap_ambient:0,fds:$fds[0]}' > /tmp/identity.json || return 1
    }
    control() {
        printf '{"id":"%s","op":"%s"}\n' "$1" "$2" | timeout 3 /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox tee "$CONTROL" > /dev/null || return 1
        until_tick=$(( $(ticks) + 500 ))
        while [ "$(ticks)" -lt "$until_tick" ]; do
            if jq -se --arg id "$1" 'any(.[]; .id==$id and (.event=="snapshot" or (.event=="ack" and .ok==true)))' "$EVENTS" > /dev/null 2>&1; then return 0; fi
            if exited "$SERVER_PID"; then return 1; fi
            sleep 0.05
        done
        return 1
    }
    snapshot() {
        control "$1" snapshot
        jq -se --arg id "$1" '[.[]|select(.id==$id)] | length==1 and .[0].schema_version==1 and .[0].event=="snapshot"' "$EVENTS" > /dev/null
        jq -s --arg id "$1" '.[]|select(.id==$id and .event=="snapshot")|.snapshot' "$EVENTS" > /tmp/snapshot.json
        server_identity
    }
    observation() {
        sysfs_observation
        jq -Mcn --arg tag "$1" --arg epoch "$EPOCH" --slurpfile sysfs /tmp/sysfs.json --slurpfile snapshot /tmp/snapshot.json --slurpfile identity /tmp/identity.json \
            '{"label":$tag,epoch:$epoch,sysfs:$sysfs[0],snapshot:$snapshot[0],identity:$identity[0]}' >> /tmp/observations.jsonl
    }
    : > /tmp/observations.jsonl
    STAGE=initial-server-ready
    launch_server initial
    for attempt in $(seq 1 100); do
        jq -se 'any(.[];.event=="ready")' "$EVENTS" > /dev/null 2>&1 && break
        kill -0 "$SERVER_PID"; sleep 0.05
    done
    jq -se 'length==1 and .[0].schema_version==1 and .[0].event=="ready" and .[0].address=="192.0.2.1:3240"' "$EVENTS" > /dev/null
    snapshot baseline
    jq -e '.buffer_bytes_reserved==0 and (.devices|length)==2 and all(.devices[];.state=="available" and (.leased|not) and .cleanup_verified and .pending_urbs==0 and .queued_urbs==0 and .usbfs_inflight==0 and .current_drivers==.original_drivers)' /tmp/snapshot.json > /dev/null
    observation baseline
    echo 'RUSTUSB_CRASH_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false'
    send_control 192.0.2.2 main-ready
    receive_control attached
    STAGE=initial-held-imports
    snapshot held
    jq -e '(.devices|length)==2 and all(.devices[];.state=="leased" and .leased and all(.current_drivers[];.driver=="usbfs"))' /tmp/snapshot.json > /dev/null
    observation held
    send_control 192.0.2.2 io
    receive_control io-complete
    STAGE=real-pending-before-kill
    PRE_ID=
    for poll in $(seq 1 100); do
        snapshot "pending-$poll"
        if jq -e '.buffer_bytes_reserved>0 and (.devices|length)==2 and all(.devices[];.state=="leased" and .leased and .pending_urbs>=1 and .usbfs_inflight>=1 and all(.current_drivers[];.driver=="usbfs"))' /tmp/snapshot.json > /dev/null; then PRE_ID=pending-$poll; break; fi
        sleep 0.05
    done
    [ -n "$PRE_ID" ]
    observation pre-crash
    jq -e --slurpfile original /tmp/original-sysfs.json 'length==2 and all(.[];.interfaces==[{number:0,alternate:0,driver:"usbfs"}]) and (map(del(.interfaces))==($original[0]|map(del(.interfaces))))' /tmp/sysfs.json > /dev/null
    server_identity
    helper_alive
    CRASH_PID=$SERVER_PID; CRASH_START=$SERVER_START
    STAGE=crash-observation
    CRASH_TICK=$(ticks); DEADLINE=$((CRASH_TICK + 2000))
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox kill -KILL "$CRASH_PID"
    echo "RUSTUSB_CRASH_SIGNAL: PASS signal=9 count=1 sender_uid=1000 pid=$CRASH_PID starttime=$CRASH_START"
    send_control 192.0.2.2 kill-sent
    EXITED=false; RESTORED=false; WITHIN_WINDOW=false
    while [ "$(ticks)" -lt "$DEADLINE" ]; do
        if exited "$CRASH_PID"; then EXITED=true; fi
        sysfs_observation
        if jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json > /dev/null; then RESTORED=true; else RESTORED=false; fi
        if [ "$EXITED" = true ] && [ "$RESTORED" = true ]; then
            [ "$(ticks)" -gt "$DEADLINE" ] || WITHIN_WINDOW=true
            break
        fi
        sleep 0.05
    done
    CRASH_WAIT=null
    if [ "$EXITED" = true ]; then
        # wait137 is the signal fact, not an early exit or a recovery PASS.
        if wait "$CRASH_PID"; then CRASH_WAIT=0; else CRASH_WAIT=$?; fi
    fi
    PID_ABSENT=false; [ -e "/proc/$CRASH_PID" ] || PID_ABSENT=true
    FD_ABSENT=false; [ -d "/proc/$CRASH_PID/fd" ] || FD_ABSENT=true
    HELPER_ALIVE=false; if helper_alive; then HELPER_ALIVE=true; fi
    sysfs_observation
    cp /tmp/sysfs.json /tmp/post-crash-sysfs.json
    if jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json > /dev/null; then RESTORED=true; else RESTORED=false; fi
    jq -Mcn --slurpfile sysfs /tmp/sysfs.json '{"label":"post-crash",epoch:"initial",sysfs:$sysfs[0],snapshot:null,snapshot_observable:false,reason:"SIGKILL ended the process; no final backend snapshot is inferred"}' >> /tmp/observations.jsonl
    jq -Mcn --argjson pid "$CRASH_PID" --argjson start "$CRASH_START" --arg pre "$PRE_ID" --argjson wait "$CRASH_WAIT" --argjson exited "$EXITED" --argjson absent "$PID_ABSENT" --argjson fd "$FD_ABSENT" --argjson restored "$RESTORED" --argjson window "$WITHIN_WINDOW" --argjson helper "$HELPER_ALIVE" --argjson elapsed "$(( $(ticks) - CRASH_TICK ))" \
        '{schema_version:1,event:"crash_outcome",role:"server",epoch:"initial",signal:9,signal_count:1,sender_uid:1000,pid:$pid,starttime:$start,pre_snapshot_id:$pre,wait_code:$wait,process_exit_observed:$exited,pid_absent:$absent,fd_table_absent:$fd,restored_sysfs:$restored,within_observation_window:$window,helper_alive:$helper,observation_centiseconds:$elapsed,observation_limit_seconds:20,stdin_kept_open:true,shutdown_before_crash:false,final_backend_snapshot:null,final_backend_snapshot_observable:false}' > /tmp/primary-outcome.json
    echo 'RUSTUSB_CRASH_INITIAL_EVENTS_BEGIN'; cat /tmp/initial.events; echo 'RUSTUSB_CRASH_INITIAL_EVENTS_END'
    echo 'RUSTUSB_CRASH_INITIAL_STDERR_BEGIN'; cat /tmp/initial.log; echo 'RUSTUSB_CRASH_INITIAL_STDERR_END'
    printf 'RUSTUSB_CRASH_OUTCOME '; cat /tmp/primary-outcome.json
    echo 'RUSTUSB_CRASH_PRIMARY_EVIDENCE_COMPLETE role=server'
    send_control 192.0.2.2 primary-server-observed
    receive_control primary-client-observed
    # Both primary raw observations are saved before the first restart attempt.
    STAGE=first-restart-observation
    launch_server restart
    RESTART_PID=$SERVER_PID; RESTART_START=$SERVER_START
    READY=false; RESTART_EXITED=false
    READY_DEADLINE=$(( $(ticks) + 1000 ))
    while [ "$(ticks)" -lt "$READY_DEADLINE" ]; do
        if jq -se 'any(.[];.schema_version==1 and .event=="ready")' "$EVENTS" > /dev/null 2>&1; then READY=true; break; fi
        if exited "$RESTART_PID"; then RESTART_EXITED=true; break; fi
        sleep 0.05
    done
    IDENTITY_OK=false; SHUTDOWN_ATTEMPTED=false; SHUTDOWN_ACK=false
    RESTART_WAIT=null
    printf 'null\n' > /tmp/snapshot.json
    printf 'null\n' > /tmp/identity.json
    if [ "$READY" = true ]; then
        if server_identity; then IDENTITY_OK=true; fi
        jq -s '[.[]|select(.event=="ready")][0].snapshot' "$EVENTS" > /tmp/snapshot.json
        observation restart-ready
        if [ "$IDENTITY_OK" = true ]; then
            SHUTDOWN_ATTEMPTED=true
            if control restart-stop shutdown; then SHUTDOWN_ACK=true; fi
        fi
        STOP_DEADLINE=$(( $(ticks) + 2000 ))
        while [ "$(ticks)" -lt "$STOP_DEADLINE" ]; do
            if exited "$RESTART_PID"; then RESTART_EXITED=true; break; fi
            sleep 0.05
        done
    else
        if [ "$RESTART_EXITED" = true ]; then observation restart-rejected; else observation restart-timeout; fi
    fi
    if [ "$RESTART_EXITED" = true ]; then
        if wait "$RESTART_PID"; then RESTART_WAIT=0; else RESTART_WAIT=$?; fi
    fi
    sysfs_observation
    RESTART_RESTORED=false
    if jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json > /dev/null; then RESTART_RESTORED=true; fi
    jq -Mcn --slurpfile sysfs /tmp/sysfs.json '{"label":"post-restart",epoch:"restart",sysfs:$sysfs[0],snapshot:null,snapshot_source:"actual restart events, never the killed epoch"}' >> /tmp/observations.jsonl
    HELPER_ALIVE=false; if helper_alive; then HELPER_ALIVE=true; fi
    jq -Mcn --argjson pid "$RESTART_PID" --arg start "$RESTART_START" --argjson ready "$READY" --argjson identity "$IDENTITY_OK" --argjson attempted "$SHUTDOWN_ATTEMPTED" --argjson ack "$SHUTDOWN_ACK" --argjson exited "$RESTART_EXITED" --argjson wait "$RESTART_WAIT" --argjson restored "$RESTART_RESTORED" --argjson helper "$HELPER_ALIVE" \
        '{schema_version:1,event:"restart_outcome",role:"server",epoch:"restart",attempted:true,attempt_count:1,pid:$pid,starttime:(try ($start|tonumber) catch null),ready_observed:$ready,identity_verified:$identity,shutdown_control_attempted:$attempted,shutdown_ack_observed:$ack,process_exit_observed:$exited,wait_code:$wait,restored_initial_sysfs:$restored,helper_alive:$helper,new_imports_sent:false,startup_limit_seconds:10,shutdown_limit_seconds:20}' > /tmp/restart-outcome.json
    echo 'RUSTUSB_CRASH_RESTART_EVENTS_BEGIN'; cat /tmp/restart.events; echo 'RUSTUSB_CRASH_RESTART_EVENTS_END'
    echo 'RUSTUSB_CRASH_RESTART_STDERR_BEGIN'; cat /tmp/restart.log; echo 'RUSTUSB_CRASH_RESTART_STDERR_END'
    echo 'RUSTUSB_CRASH_OBSERVATIONS_BEGIN'; cat /tmp/observations.jsonl; echo 'RUSTUSB_CRASH_OBSERVATIONS_END'
    printf 'RUSTUSB_CRASH_RESTART_OUTCOME '; cat /tmp/restart-outcome.json
    echo 'RUSTUSB_CRASH_COLLECTION_COMPLETE role=server'
    send_control 192.0.2.2 restart-finished
    receive_control client-finished
    PASS=true
    if [ "$CRASH_WAIT" != 137 ] || [ "$RESTORED" != true ] || [ "$WITHIN_WINDOW" != true ] || [ "$READY" != true ] || [ "$IDENTITY_OK" != true ] || [ "$RESTART_WAIT" != 0 ] || [ "$RESTART_RESTORED" != true ] || [ "$SHUTDOWN_ACK" != true ] || [ "$HELPER_ALIVE" != true ]; then PASS=false; fi
    if ! jq -se '([.[]|select(.event=="stopped")]|length)==1 and .[-1].event=="stopped" and .[-1].snapshot.buffer_bytes_reserved==0 and all(.[-1].snapshot.devices[];.state=="available" and (.leased|not) and .cleanup_verified and .pending_urbs==0 and .queued_urbs==0 and .usbfs_inflight==0)' /tmp/restart.events > /dev/null; then PASS=false; fi
    send_control 192.0.2.2 finished
else
    STAGE=client-helper
    jq -n '{allowed_uids:[1000],export_device_ids:[],peers:[{host:"192.0.2.1",port:3240}],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    jq -e '.helper.kernel_available == true and .native.vhci_hcd.sysfs.status == "available"' /tmp/doctor.json > /dev/null
    receive_control main-ready
    for attempt in $(seq 1 150); do
        if unpriv --json --timeout 2 discover 192.0.2.1 > /tmp/discover.json 2>/dev/null && jq -e 'length==2 and any(.[]; .vendor_id==1575 and .product_id==1) and any(.[]; .vendor_id==1027 and .product_id==24577)' /tmp/discover.json > /dev/null; then break; fi
        sleep 0.05
    done
    jq -e 'length==2' /tmp/discover.json > /dev/null
    HID_BUS=$(jq -r '.[] | select(.vendor_id==1575) | .bus_id' /tmp/discover.json)
    FTDI_BUS=$(jq -r '.[] | select(.vendor_id==1027) | .bus_id' /tmp/discover.json)
    STAGE=client-attach
    unpriv --json attach 192.0.2.1 "$HID_BUS" > /tmp/attach-hid.json
    unpriv --json attach 192.0.2.1 "$FTDI_BUS" > /tmp/attach-ftdi.json
    HID_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-hid.json)
    FTDI_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-ftdi.json)
    unpriv --json sessions > /tmp/sessions.json
    jq -e --slurpfile hid /tmp/attach-hid.json --slurpfile ftdi /tmp/attach-ftdi.json 'length==2 and all(.[]; .managed==true and .session.status==6) and any(.[]; .session==$hid[0]) and any(.[]; .session==$ftdi[0])' /tmp/sessions.json > /dev/null
    for file in /tmp/attach-hid.json /tmp/attach-ftdi.json; do
        local=$(jq -r '.local_bus_id' "$file")
        [ "$(stat -L -c '%d:%i' "/sys/bus/usb/devices/$local")" = "$(jq -r '.local_device_generation | "\(.filesystem_device):\(.inode)"' "$file")" ]
        [ "$(cat "/sys/bus/usb/devices/$local/devnum" | trim)" = "$(jq -r '.local_device_address' "$file")" ]
    done
    EVENT=; TTY=
    for attempt in $(seq 1 100); do
        EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
        TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$FTDI_LOCAL")" -name 'ttyUSB*' -type d | head -1)
        [ -z "$EVENT" ] || [ -z "$TTY" ] || break
        sleep 0.05
    done
    [ -n "$EVENT" ] && [ -n "$TTY" ]
    [ "$(readlink "/sys/bus/usb/devices/$HID_LOCAL:1.0/driver" | sed 's#.*/##')" = usbhid ]
    [ "$(readlink "/sys/bus/usb/devices/$FTDI_LOCAL:1.0/driver" | sed 's#.*/##')" = ftdi_sio ]
    : > /tmp/input.bin
    exec 3< "/dev/input/$(basename "$EVENT")"
    cat <&3 > /tmp/input.bin &
    INPUT_READER=$!
    exec 3<&-
    TTY=/dev/$(basename "$TTY")
    stty -F "$TTY" raw -echo 115200 clocal -hupcl
    exec 4<> "$TTY"
    : > /tmp/rx.bin
    timeout 8 dd bs=1 count=35 <&4 > /tmp/rx.bin 2> /tmp/dd.log &
    SERIAL_READER=$!
    probe busy "$HID_BUS" "$FTDI_BUS"
    send_control 192.0.2.1 attached
    receive_control io
    STAGE=client-real-io
    echo 'RUSTUSB_CRASH_WAIT_INPUT key=1'
    echo 'RUSTUSB_CRASH_WAIT_SERIAL bytes=35'
    wait "$SERIAL_READER"
    printf 'RUSTUSB-RX-1-0001:0123456789abcdef\n' > /tmp/expected.bin
    [ "$(wc -c < /tmp/rx.bin)" = 35 ] && cmp /tmp/rx.bin /tmp/expected.bin
    printf 'RUSTUSB-TX-1-0001:fedcba9876543210\n' > /tmp/tx.bin
    cat /tmp/tx.bin >&4
    KEY_SEEN=false
    for attempt in $(seq 1 100); do
        bytes=$(wc -c < /tmp/input.bin)
        if [ "$bytes" -gt 0 ] && [ "$((bytes % 24))" = 0 ] && od -An -tu4 -w24 -v /tmp/input.bin | awk 'NF==6 && $5==131073 && $6==1 {down=1} NF==6 && $5==131073 && $6==0 {up=1} END {exit !(down&&up)}'; then KEY_SEEN=true; break; fi
        sleep 0.05
    done
    [ "$KEY_SEEN" = true ]
    unpriv --json sessions > /tmp/sessions.json
    jq -e --slurpfile hid /tmp/attach-hid.json --slurpfile ftdi /tmp/attach-ftdi.json 'length==2 and all(.[]; .managed) and any(.[]; .session==$hid[0]) and any(.[]; .session==$ftdi[0])' /tmp/sessions.json > /dev/null
    echo 'RUSTUSB_CRASH_REAL_IO: PASS key_code=2 down=true up=true serial_in=35 serial_out=35 same_receipts=true readers_kept_open=true'
    send_control 192.0.2.1 io-complete
    receive_control kill-sent
    STAGE=client-crash-observation
    CRASH_TICK=$(ticks); DEADLINE=$((CRASH_TICK + 2000))
    DISCONNECTED=false
    printf 'null\n' > /tmp/sessions-after.json
    while [ "$(ticks)" -lt "$DEADLINE" ]; do
        if timeout 2 /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json sessions > /tmp/sessions-attempt.json 2> /tmp/sessions-error.log; then
            cp /tmp/sessions-attempt.json /tmp/sessions-after.json
            if jq -e 'length==0' /tmp/sessions-after.json > /dev/null && [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$FTDI_LOCAL" ] && [ "$(ticks)" -le "$DEADLINE" ]; then DISCONNECTED=true; break; fi
        fi
        sleep 0.05
    done
    : > /tmp/vhci.txt
    for file in /sys/devices/platform/vhci_hcd.*/status /sys/devices/platform/vhci_hcd.*/status.[0-9]*; do
        [ ! -f "$file" ] || { printf 'STATUS %s\n' "$file" >> /tmp/vhci.txt; cat "$file" >> /tmp/vhci.txt; }
    done
    HID_GONE=false; [ -e "/sys/bus/usb/devices/$HID_LOCAL" ] || HID_GONE=true
    FTDI_GONE=false; [ -e "/sys/bus/usb/devices/$FTDI_LOCAL" ] || FTDI_GONE=true
    HELPER_ALIVE=false; if helper_alive; then HELPER_ALIVE=true; fi
    input_hex=$(od -An -tx1 -v /tmp/input.bin | tr -d ' \n')
    rx_hex=$(od -An -tx1 -v /tmp/rx.bin | tr -d ' \n')
    rx_sha=$(sha256sum /tmp/rx.bin | cut -d' ' -f1)
    tx_sha=$(sha256sum /tmp/tx.bin | cut -d' ' -f1)
    jq -Mcn --argjson disconnected "$DISCONNECTED" --argjson hg "$HID_GONE" --argjson fg "$FTDI_GONE" --argjson helper "$HELPER_ALIVE" --slurpfile hid /tmp/attach-hid.json --slurpfile ftdi /tmp/attach-ftdi.json --slurpfile sessions /tmp/sessions-after.json --rawfile vhci /tmp/vhci.txt --arg input "$input_hex" --arg rxhex "$rx_hex" --arg rx "$rx_sha" --arg tx "$tx_sha" --argjson elapsed "$(( $(ticks) - CRASH_TICK ))" \
        '{schema_version:1,event:"crash_outcome",role:"client",spontaneous_disconnect:$disconnected,hid_generation_gone:$hg,ftdi_generation_gone:$fg,helper_alive:$helper,receipts:[$hid[0],$ftdi[0]],sessions:$sessions[0],vhci_status:$vhci,input_event_hex:$input,input_event_format:"linux_x86_64_24",rx_hex:$rxhex,rx_sha256:$rx,tx_sha256:$tx,serial_in_bytes:35,serial_out_bytes:35,observation_centiseconds:$elapsed,observation_limit_seconds:20,detach_before_observation:false,new_imports_after_crash:false}' > /tmp/outcome.json
    printf 'RUSTUSB_CRASH_OUTCOME '; cat /tmp/outcome.json
    echo 'RUSTUSB_CRASH_PRIMARY_EVIDENCE_COMPLETE role=client'
    receive_control primary-server-observed
    send_control 192.0.2.1 primary-client-observed
    # No second IMPORT: observe the first new service registration only.
    receive_control restart-finished
    kill "$INPUT_READER" 2>/dev/null || true
    wait "$INPUT_READER" 2>/dev/null || true
    exec 4<&-
    PASS=true
    if [ "$DISCONNECTED" = true ]; then
        if ! unpriv --json detach "$(jq -r '.port' /tmp/attach-hid.json)"; then PASS=false; fi
        if ! unpriv --json detach "$(jq -r '.port' /tmp/attach-ftdi.json)"; then PASS=false; fi
    else PASS=false; fi
    [ "$HELPER_ALIVE" = true ] || PASS=false
    echo 'RUSTUSB_CRASH_COLLECTION_COMPLETE role=client'
    send_control 192.0.2.1 client-finished
    receive_control finished
fi
if [ "$PASS" = true ]; then
    echo "RUSTUSB_CRASH_RESULT: PASS role=$ROLE"
else
    echo "RUSTUSB_CRASH_RESULT: FAIL role=$ROLE stage=recovery-contract evidence_collection_complete=true"
fi
