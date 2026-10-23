#!/bin/sh
# Disposable PID1 only; ptrace, USB, modules and listeners stay inside guests.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo 'Refusing host-state init outside a disposable QEMU guest' >&2
    exit 2
}
export PATH=/usr/bin:/bin:/sbin
export LC_ALL=C
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.host_state_test=1 '*) ;; *) exit 2 ;; esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/host-state /etc/rustusb
chmod 755 /run/rustusb /etc/rustusb
chmod 700 /run/host-state
mount -t devpts devpts /dev/pts
ROLE=unknown OPERATION=unknown MODE=unknown NONCE=unknown STAGE=boot
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in
        rustusb.role=*) ROLE=${parameter#*=} ;;
        rustusb.operation=*) OPERATION=${parameter#*=} ;;
        rustusb.mode=*) MODE=${parameter#*=} ;;
        rustusb.nonce=*) NONCE=${parameter#*=} ;;
    esac
done
case "$ROLE" in server|client) ;; *) exit 2 ;; esac
case "$OPERATION" in configuration|alternate|clear) ;; *) exit 2 ;; esac
case "$MODE" in unlink|eof) ;; *) exit 2 ;; esac
case "$NONCE" in ''|*[!A-Za-z0-9_-]*) exit 2 ;; esac
[ "${#NONCE}" -le 48 ]
cleanup() {
    status=$?
    trap - EXIT
    set +e
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_HOST_STATE_DIAGNOSTIC role=$ROLE stage=$STAGE exit=$status"
        for file in /tmp/*.log /tmp/*.json /tmp/*.jsonl /tmp/server.events /run/rustusb-gate/*.json /run/rustusb-gate/*.jsonl; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
        ifconfig -a
        dmesg | tail -70
        echo "RUSTUSB_HOST_STATE_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
    fi
    for pid in "${TRACER_PID:-}" "${RPC_PID:-}" "${HELPER_PID:-}" "${PROBE_PID:-}"; do
        [ -z "$pid" ] || { kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; }
    done
    sync
    poweroff -f
}
trap cleanup EXIT
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
identity() {
    kill -0 "$1"
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$1/status")" = 1000:1000:1000:1000 ]
    [ "$(awk '/^Gid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$1/status")" = 1000:1000:1000:1000 ]
    for cap in CapEff CapPrm CapAmb CapInh; do
        [ "$(awk -v field="$cap:" '$1 == field {print $2}' "/proc/$1/status")" = 0000000000000000 ]
    done
}
echo "RUSTUSB_HOST_STATE_BOOT role=$ROLE operation=$OPERATION mode=$MODE nonce=$NONCE kernel=$(uname -r)"
for module in usb-common usbcore e1000; do [ ! -f "/modules/$module.ko" ] || insmod "/modules/$module.ko"; done
ifconfig lo 127.0.0.1 up
if [ "$ROLE" = client ]; then
    ifconfig eth0 192.0.2.2 netmask 255.255.255.0 up
else
    for module in mii usbnet cdc_ether uhci-hcd; do [ ! -f "/modules/$module.ko" ] || insmod "/modules/$module.ko"; done
    ifconfig eth0 192.0.2.1 netmask 255.255.255.0 up
    PEER_NET=
    for interface in /sys/class/net/*; do
        [ "$(cat "$interface/address")" != 52:54:00:cc:00:01 ] || PEER_NET=$(basename "$interface")
    done
    [ -n "$PEER_NET" ] && [ "$PEER_NET" != eth0 ]
    ifconfig "$PEER_NET" down
    echo "RUSTUSB_HOST_STATE_PEER_QUIET: PASS interface=$PEER_NET down=true"
fi
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = 0 ]
[ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
echo "RUSTUSB_HOST_STATE_ISOLATION: PASS role=$ROLE ip_forward=0 native_usb_modules=false"
echo 'RUSTUSB_HOST_STATE_EXECUTABLES_BEGIN'
sha256sum /usr/bin/rustusb /usr/bin/rustusb-helper /usr/bin/host-state-wire-probe /usr/bin/usbfs-syscall-gate
echo 'RUSTUSB_HOST_STATE_EXECUTABLES_END'
if [ "$ROLE" = client ]; then
    STAGE=ordinary-wire-probe
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/host-state-wire-probe "$OPERATION" "$MODE" "$NONCE" > /tmp/probe.log 2>&1 & PROBE_PID=$!
    for attempt in $(seq 1 100); do
        [ "$(awk '/^Uid:/ {print $2}' "/proc/$PROBE_PID/status")" != 1000 ] || break
        kill -0 "$PROBE_PID"
        sleep 0.01
    done
    identity "$PROBE_PID"
    if wait "$PROBE_PID"; then cat /tmp/probe.log; else cat /tmp/probe.log; exit 1; fi
    PROBE_PID=
    echo 'RUSTUSB_HOST_STATE_PROBE_UID: PASS uid=1000'
    echo "RUSTUSB_HOST_STATE_RESULT: PASS role=$ROLE"
    exit 0
fi

STAGE=original-device
for attempt in $(seq 1 150); do
    unpriv --json devices > /tmp/devices.json
    [ "$(jq 'length' /tmp/devices.json)" != 1 ] || break
    sleep 0.02
done
jq -e 'length == 1 and .[0].vendor_id == 1317 and .[0].product_id == 42146 and (.[0].export_blockers | length) == 0' /tmp/devices.json > /dev/null
DEVICE_ID=$(jq -r '.[0].id' /tmp/devices.json)
BUS=$(jq -r '.[0].bus_id' /tmp/devices.json)
[ "$(cat "/sys/bus/usb/devices/$BUS/speed")" = 12 ]
[ "$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS/bNumInterfaces")" = 2 ]
GENERATION=$(stat -L -c '%d:%i' "/sys/bus/usb/devices/$BUS")
NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$BUS/busnum")" "$(cat "/sys/bus/usb/devices/$BUS/devnum")")
[ -c "$NODE" ] && [ "$(stat -c '%u' "$NODE")" = 0 ]
chmod 600 "$NODE"
NODE_IDENTITY=$(stat -c '%d:%i:%t:%T' "$NODE")
NODE_DEVICE=$(stat -c '%d' "$NODE")
NODE_INODE=$(stat -c '%i' "$NODE")
major=$(stat -c '%t' "$NODE"); minor=$(stat -c '%T' "$NODE")
major=$((0x$major)); minor=$((0x$minor))
NODE_RDEV=$((((major & 4095) << 8) | ((major & ~4095) << 32) | (minor & 255) | ((minor & ~255) << 12)))
for uid in 1000 1001; do
    if /usr/bin/setpriv --reuid="$uid" --regid="$uid" --clear-groups /bin/busybox sh -c 'exec 3<> "$1"' sh "$NODE" > "/tmp/direct-open-$uid.log" 2>&1; then exit 1; fi
    grep -F 'Permission denied' "/tmp/direct-open-$uid.log"
done
sysfs_observation() {
    [ "$(stat -L -c '%d:%i' "/sys/bus/usb/devices/$BUS")" = "$GENERATION" ]
    cfg=$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS/bConfigurationValue")
    [ "$cfg" = 1 ]
    driver0=$(readlink "/sys/bus/usb/devices/$BUS:1.0/driver" 2>/dev/null | sed 's#.*/##' || true)
    driver1=$(readlink "/sys/bus/usb/devices/$BUS:1.1/driver" 2>/dev/null | sed 's#.*/##' || true)
    alt0=$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS:1.0/bAlternateSetting")
    alt1=$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS:1.1/bAlternateSetting")
    [ "$(stat -c '%d:%i:%t:%T' "$NODE")" = "$NODE_IDENTITY" ] && [ "$(stat -c '%u:%a' "$NODE")" = 0:600 ]
    jq -n --arg generation "$GENERATION" --arg config "$cfg" --arg d0 "$driver0" --arg d1 "$driver1" --arg a0 "$alt0" --arg a1 "$alt1" \
        '{generation:$generation,configuration:($config|tonumber),interfaces:[{number:0,driver:(if $d0 == "" then null else $d0 end),alternate:($a0|tonumber)},{number:1,driver:(if $d1 == "" then null else $d1 end),alternate:($a1|tonumber)}]}' > /tmp/sysfs.json
}
original_state() {
    for attempt in $(seq 1 100); do
        sysfs_observation
        if jq -e '.configuration == 1 and .interfaces == [{number:0,driver:"cdc_ether",alternate:0},{number:1,driver:"cdc_ether",alternate:1}]' /tmp/sysfs.json > /dev/null; then return 0; fi
        sleep 0.02
    done
    return 1
}
original_state
cp /tmp/sysfs.json /tmp/original-sysfs.json
echo 'RUSTUSB_HOST_STATE_ORIGINAL: PASS config=1 interfaces=2 alt0=0 alt1=1 drivers=cdc_ether node=root0600 direct_open=denied'

STAGE=helper-authorization
jq -n --arg id "$DEVICE_ID" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[$id],peers:[],trusted_network:true}' > /etc/rustusb/policy.json
chmod 644 /etc/rustusb/policy.json
unpriv --json doctor > /tmp/doctor-before.json
jq -e '.helper.status == "missing"' /tmp/doctor-before.json > /dev/null
/usr/bin/rustusb-helper > /tmp/helper.log 2>&1 & HELPER_PID=$!
for attempt in $(seq 1 100); do [ ! -S /run/rustusb/control.sock ] || break; sleep 0.02; done
[ -S /run/rustusb/control.sock ]
unpriv --json doctor > /tmp/doctor-after.json
jq -e '.schema_version == 1 and .helper.status == "available" and .helper.peer_verified == true and .helper.kernel_available == false and .network_security_verified == false and .hardware_test_performed == false and .built_in_encryption == false' /tmp/doctor-after.json > /dev/null
if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then exit 1; fi
grep -F 'caller UID is not approved by the administrator' /tmp/denied.log
echo 'RUSTUSB_HOST_STATE_UID_GATE: PASS allowed_uid=1000 denied_uid=1001 peer_verified=true'

STAGE=traced-ordinary-server
mkdir -m 700 /run/rustusb-gate
mkfifo -m 600 /run/host-state/server-control
exec 3<> /run/host-state/server-control
(
    exec 3<&-
    exec /usr/bin/usbfs-syscall-gate --directory /run/rustusb-gate --usb-node "$NODE" -- \
        /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb physical-server \
        --device-id "$DEVICE_ID" --listen 192.0.2.1:3240 --allow-insecure-test-network \
        --allow-peer 192.0.2.2 --control-stdin < /run/host-state/server-control > /tmp/server.events 2> /tmp/server.log
) & TRACER_PID=$!
for attempt in $(seq 1 150); do
    if [ -f /run/rustusb-gate/ready.json ] && jq -se 'any(.[]; .event == "ready" and .schema_version == 1)' /tmp/server.events > /dev/null 2>&1; then break; fi
    kill -0 "$TRACER_PID"
    sleep 0.02
done
[ -f /run/rustusb-gate/ready.json ]
SERVER_PID=$(cat /run/rustusb-gate/tracee.pid)
SERVER_START=$(awk '{print $22}' "/proc/$SERVER_PID/stat")
server_identity() {
    identity "$SERVER_PID"
    [ "$(awk '{print $22}' "/proc/$SERVER_PID/stat")" = "$SERVER_START" ]
    [ "$(stat -c '%u:%a' "$NODE")" = 0:600 ]
    [ "$(stat -c '%d:%i:%t:%T' "$NODE")" = "$NODE_IDENTITY" ]
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
}
server_identity
jq -e --argjson pid "$SERVER_PID" --arg start "$SERVER_START" '.schema_version == 1 and .tracee_pid == $pid and (.tracee_starttime|tostring) == $start' /run/rustusb-gate/ready.json > /dev/null
echo 'RUSTUSB_HOST_STATE_SERVER_UID: PASS uid=1000 capabilities=0 tracer_root=true helper_separate=true'
SNAP_COUNTER=0
: > /tmp/observations.jsonl
control() {
    printf '{"id":"%s","op":"%s"}\n' "$1" "$2" >&3
    for attempt in $(seq 1 50); do
        if jq -se --arg id "$1" 'any(.[]; .id == $id and (.event == "snapshot" or (.event == "ack" and .ok == true)))' /tmp/server.events > /dev/null 2>&1; then return 0; fi
        kill -0 "$SERVER_PID"
        sleep 0.01
    done
    return 1
}
snapshot() {
    SNAP_COUNTER=$((SNAP_COUNTER + 1))
    SNAP_ID="snapshot-$SNAP_COUNTER"
    control "$SNAP_ID" snapshot
    jq -s --arg id "$SNAP_ID" '.[] | select(.event == "snapshot" and .id == $id) | .snapshot' /tmp/server.events > /tmp/snapshot.json
    jq -e '.schema_version == 1 and (.devices|length) == 1 and .devices[0].configuration == 1' /tmp/snapshot.json > /dev/null
    server_identity
}
record_observation() {
    sysfs_observation
    jq -cn --arg tag "$1" --slurpfile snapshot /tmp/snapshot.json --slurpfile sysfs /tmp/sysfs.json \
        '{"label":$tag,"snapshot":$snapshot[0],"sysfs":$sysfs[0]}' >> /tmp/observations.jsonl
}
wait_snapshot() {
    for attempt in $(seq 1 40); do
        snapshot
        if jq -e "$2" /tmp/snapshot.json > /dev/null; then record_observation "$1"; return 0; fi
        sleep 0.01
    done
    return 1
}
RELEASED_FILTER='.buffer_bytes_reserved == 0 and all(.devices[]; .state == "available" and .leased == false and .pending_urbs == 0 and .queued_urbs == 0 and .usbfs_inflight == 0 and .cleanup_verified == true and .configuration == 1 and .speed == 2 and .ep0_max_packet_size == 64 and .current_drivers == .original_drivers and .current_drivers == [{interface:0,driver:"cdc_ether"},{interface:1,driver:"cdc_ether"}])'
wait_snapshot baseline "$RELEASED_FILTER"
original_state
case "$OPERATION" in configuration) EXPECTED_REQUEST=$((0x80045505)) ;; alternate) EXPECTED_REQUEST=$((0x80085504)) ;; clear) EXPECTED_REQUEST=$((0x80045515)) ;; esac
held_identity() {
    server_identity
    jq -e --arg nonce "$NONCE" --arg operation "$OPERATION" --argjson request "$EXPECTED_REQUEST" \
        --argjson dev "$NODE_DEVICE" --argjson inode "$NODE_INODE" --argjson rdev "$NODE_RDEV" \
        --argjson pid "$SERVER_PID" --arg start "$SERVER_START" \
        '.schema_version == 1 and .event == "held" and .nonce == $nonce and .operation == $operation and .request == $request and .node_device == $dev and .node_inode == $inode and .node_rdev == $rdev and .tracee_pid == $pid and (.tracee_starttime|tostring) == $start and (.fd|type) == "number" and .fd >= 0 and .fd < 4096 and .tid > 0 and .monotonic_ns > 0' /run/rustusb-gate/held.json > /dev/null
    FD=$(jq -r '.fd' /run/rustusb-gate/held.json)
    [ "$(stat -L -c '%d:%i:%t:%T' "/proc/$SERVER_PID/fd/$FD")" = "$NODE_IDENTITY" ]
    [ "$(readlink "/proc/$SERVER_PID/fd/$FD")" = "$NODE" ]
    [ -d "/proc/$SERVER_PID/task/$(jq -r '.tid' /run/rustusb-gate/held.json)" ]
    [ ! -f /run/rustusb-gate/exit.json ]
    jq -se --slurpfile held /run/rustusb-gate/held.json 'all(.[]; .event != "failure") and all(.[] | select(.event == "ioctl_entry" and .monotonic_ns >= $held[0].monotonic_ns); .mutation_counts == $held[0].mutation_counts)' /run/rustusb-gate/events.jsonl > /dev/null
    sysfs_observation
    if [ "$OPERATION" != configuration ]; then
        jq -e '.interfaces == [{number:0,driver:"usbfs",alternate:0},{number:1,driver:"usbfs",alternate:1}]' /tmp/sysfs.json > /dev/null
    fi
    if [ -f /tmp/held-sysfs.json ]; then jq -e --slurpfile held /tmp/held-sysfs.json '. == $held[0]' /tmp/sysfs.json > /dev/null; fi
}
read_rpc() {
    LINE=
    IFS= read -r -n 129 -t 3 LINE <&6
    [ "${#LINE}" -le 128 ] && [ "$LINE" = "$1 $NONCE" ]
    echo "RUSTUSB_HOST_STATE_RPC command=$1 nonce=$NONCE"
    STAGE="rpc-$1"
}
respond() { printf '%s %s\n' "$1" "$NONCE" >&7; }
mkfifo -m 600 /run/host-state/rpc-in /run/host-state/rpc-out
exec 6<> /run/host-state/rpc-in
exec 7<> /run/host-state/rpc-out
(
    exec 3<&- 6<&- 7<&-
    exec timeout 110 nc -l -p 5002 < /run/host-state/rpc-out > /run/host-state/rpc-in
) & RPC_PID=$!
respond READY
read_rpc ARM
printf '%s %s\n' "$NONCE" "$OPERATION" > /run/rustusb-gate/arm.tmp
mv /run/rustusb-gate/arm.tmp /run/rustusb-gate/arm
for attempt in $(seq 1 100); do
    jq -se --arg nonce "$NONCE" --arg operation "$OPERATION" 'any(.[]; .event == "armed" and .nonce == $nonce and .operation == $operation)' /run/rustusb-gate/events.jsonl > /dev/null 2>&1 && break
    sleep 0.01
done
jq -se --arg nonce "$NONCE" --arg operation "$OPERATION" 'any(.[]; .event == "armed" and .nonce == $nonce and .operation == $operation)' /run/rustusb-gate/events.jsonl > /dev/null
respond ARMED
read_rpc HELD
for attempt in $(seq 1 100); do [ ! -f /run/rustusb-gate/held.json ] || break; sleep 0.01; done
held_identity
wait_snapshot held '.buffer_bytes_reserved == 256 and all(.devices[]; .state == "leased" and .leased == true and .cleanup_verified == false and .pending_urbs == 1 and .queued_urbs == 0 and .usbfs_inflight == 0)'
cp /tmp/sysfs.json /tmp/held-sysfs.json
echo 'RUSTUSB_HOST_STATE_HELD: PASS syscall_entry=true kernel_handler_active_unproven=true pending=1 budget=256'
respond HELD
if [ "$MODE" = unlink ]; then
    read_rpc WITNESS
    held_identity
    wait_snapshot witness '.buffer_bytes_reserved == 640 and all(.devices[]; .state == "leased" and .leased == true and .cleanup_verified == false and .pending_urbs == 2 and .queued_urbs >= 1 and .usbfs_inflight == 0)'
    echo 'RUSTUSB_HOST_STATE_WITNESS: PASS pending=2 budget=640 dependent_deferred=true'
    respond WITNESS
else
    read_rpc ACCEPTED
    held_identity
    wait_snapshot accepted '.buffer_bytes_reserved == 512 and all(.devices[]; .state == "leased" and .leased == true and .cleanup_verified == false and .pending_urbs == 2 and .queued_urbs >= 1 and .usbfs_inflight == 0)'
    echo 'RUSTUSB_HOST_STATE_ACCEPTED: PASS pending=2 budget=512 server_accepted=true'
    respond ACCEPTED
    read_rpc CLOSED
    held_identity
    wait_snapshot closed '.buffer_bytes_reserved == 512 and all(.devices[]; .state == "closing" and .leased == true and .cleanup_verified == false and .pending_urbs == 2 and .queued_urbs == 0 and .usbfs_inflight == 0)'
    echo 'RUSTUSB_HOST_STATE_CLOSED: PASS closing=true pending=2 budget=512 no_early_restore=true'
    respond CLOSED
fi
read_rpc RELEASE
held_identity
printf '%s\n' "$NONCE" > /run/rustusb-gate/release.tmp
mv /run/rustusb-gate/release.tmp /run/rustusb-gate/release
for attempt in $(seq 1 150); do [ ! -f /run/rustusb-gate/exit.json ] || break; sleep 0.01; done
retval=0; is_error=false
[ "$OPERATION" != clear ] || { retval=-32; is_error=true; }
jq -e --slurpfile held /run/rustusb-gate/held.json --argjson retval "$retval" --argjson is_error "$is_error" \
    '.schema_version == 1 and .event == "exit" and .nonce == $held[0].nonce and .operation == $held[0].operation and .tid == $held[0].tid and .fd == $held[0].fd and .request == $held[0].request and .node_device == $held[0].node_device and .node_inode == $held[0].node_inode and .node_rdev == $held[0].node_rdev and .mutation_counts == $held[0].mutation_counts and .monotonic_ns > $held[0].monotonic_ns and .return_value == $retval and .is_error == $is_error' /run/rustusb-gate/exit.json > /dev/null
echo "RUSTUSB_HOST_STATE_EXIT: PASS operation=$OPERATION retval=$retval untouched=true"
respond RELEASE
if [ "$OPERATION:$MODE" = clear:unlink ]; then
    read_rpc PENDING
    wait_snapshot pending '.buffer_bytes_reserved == 384 and all(.devices[]; .state == "leased" and .leased == true and .cleanup_verified == false and .pending_urbs == 1 and .queued_urbs == 0 and .usbfs_inflight == 1)'
    jq -e '.interfaces == [{number:0,driver:"usbfs",alternate:0},{number:1,driver:"usbfs",alternate:1}]' /tmp/sysfs.json > /dev/null
    echo 'RUSTUSB_HOST_STATE_PENDING: PASS pending=1 queued=0 inflight=1 budget=384 raw_submitted=true'
    respond PENDING
fi
read_rpc RESTORED
wait_snapshot restored "$RELEASED_FILTER"
original_state
jq -e --slurpfile original /tmp/original-sysfs.json '. == $original[0]' /tmp/sysfs.json > /dev/null
echo 'RUSTUSB_HOST_STATE_RESTORED: PASS original_config_alt_drivers=true pending=0 queued=0 inflight=0 budget=0'
respond RESTORED
read_rpc DONE
control stop shutdown
wait "$TRACER_PID"
TRACER_PID=
exec 3<&-
jq -s '.[] | select(.event == "stopped") | .snapshot' /tmp/server.events > /tmp/snapshot.json
jq -e "$RELEASED_FILTER" /tmp/snapshot.json > /dev/null
original_state
record_observation stopped
jq -se 'all(.[]; .schema_version == 1 and .event != "error") and ([.[] | select(.event == "stopped")] | length) == 1 and .[-1].event == "stopped"' /tmp/server.events > /dev/null
jq -se 'all(.[]; .schema_version == 1 and .event != "failure") and any(.[]; .event == "finished")' /run/rustusb-gate/events.jsonl > /dev/null
echo 'RUSTUSB_HOST_STATE_STOPPED: PASS ordinary_server_exit=0 tracer_exit=0 restored=true'
echo 'RUSTUSB_HOST_STATE_OBSERVATIONS_BEGIN'; cat /tmp/observations.jsonl; echo 'RUSTUSB_HOST_STATE_OBSERVATIONS_END'
echo 'RUSTUSB_HOST_STATE_EVENTS_BEGIN'; cat /tmp/server.events; echo 'RUSTUSB_HOST_STATE_EVENTS_END'
echo 'RUSTUSB_HOST_STATE_TRACE_BEGIN'; cat /run/rustusb-gate/events.jsonl; echo 'RUSTUSB_HOST_STATE_TRACE_END'
echo 'RUSTUSB_HOST_STATE_BEACONS_BEGIN'
jq -cn --slurpfile ready /run/rustusb-gate/ready.json --slurpfile held /run/rustusb-gate/held.json --slurpfile exit /run/rustusb-gate/exit.json '{ready:$ready[0],held:$held[0],exit:$exit[0]}'
echo 'RUSTUSB_HOST_STATE_BEACONS_END'
echo 'RUSTUSB_HOST_STATE_STDERR_BEGIN'; cat /tmp/server.log; echo 'RUSTUSB_HOST_STATE_STDERR_END'
respond DONE
exec 6<&- 7<&-
for attempt in $(seq 1 100); do kill -0 "$RPC_PID" 2>/dev/null || break; sleep 0.01; done
if kill -0 "$RPC_PID" 2>/dev/null; then exit 1; fi
wait "$RPC_PID"
RPC_PID=
echo "RUSTUSB_HOST_STATE_RESULT: PASS role=$ROLE"
