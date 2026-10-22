#!/bin/sh
# PID1 of an isolated QEMU guest only. The host never runs USB operations.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo 'Refusing to run outside the disposable QEMU guest init' >&2
    exit 2
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in
    *' rustusb.role=server '*|*' rustusb.role=client '*) ;;
    *) echo 'Invalid physical guest role' >&2; exit 2 ;;
esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/physical /var/run/vhci_hcd /etc/rustusb
chmod 755 /run/rustusb /etc/rustusb
mount -t devpts devpts /dev/pts
ROLE=unknown
HOT_UNPLUG=0
ALLOW_GONE=false
STAGE=boot
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in
        rustusb.role=*) ROLE=${parameter#*=} ;;
        rustusb.hot_unplug=*) HOT_UNPLUG=${parameter#*=} ;;
    esac
done
[ "$HOT_UNPLUG" = 0 ] || [ "$HOT_UNPLUG" = 1 ]
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_PHYSICAL_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        for file in /tmp/helper.log /tmp/server.log /tmp/server.events /tmp/devices.json /tmp/discover.json /tmp/attach-hid.json /tmp/attach-ftdi.json /tmp/sessions.json /tmp/doctor.json /tmp/denied.log /tmp/denied-fd.log /tmp/dd.log; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
        if [ "$ROLE" = client ] && [ -S /run/rustusb/control.sock ]; then unpriv --json sessions || true; fi
        dmesg | tail -70
    fi
    sync
    poweroff -f
}
trap cleanup EXIT
echo "RUSTUSB_PHYSICAL_BOOT role=$ROLE kernel=$(uname -r)"
for name in usb-common usbcore e1000 evdev hid hid-generic usbhid usbserial ftdi_sio; do
    [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
done
ifconfig lo 127.0.0.1 up
if [ "$ROLE" = server ]; then
    for name in xhci-hcd xhci-pci-renesas xhci-pci; do
        [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
    done
    ifconfig eth0 192.0.2.1 netmask 255.255.255.0 up
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    echo 'RUSTUSB_PHYSICAL_SERVER_MODULES: PASS usbip_host=false vhci_hcd=false'
else
    for name in usbip-core vhci-hcd; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    ifconfig eth0 192.0.2.2 netmask 255.255.255.0 up
fi
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = 0 ]
echo "RUSTUSB_PHYSICAL_ISOLATION role=$ROLE ip_forward=0"
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
probe() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/physical-wire-probe 192.0.2.1:3240 "$@"; }
send_control() {
    for attempt in $(seq 1 100); do
        if printf '%s' "$2" | nc -w 1 "$1" 5001; then return 0; fi
        sleep 0.1
    done
    echo "control delivery failed: $2"
    return 1
}
receive_control() {
    MESSAGE=$(timeout 40 nc -l -p 5001)
    [ "$MESSAGE" = "$1" ] || { echo "unexpected control message: $MESSAGE expected=$1"; return 1; }
}
start_helper() {
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &
    for attempt in $(seq 1 100); do
        [ ! -S /run/rustusb/control.sock ] || break
        sleep 0.05
    done
    [ -S /run/rustusb/control.sock ]
    unpriv --json doctor > /tmp/doctor.json
    jq -e '.schema_version == 1 and .helper.status == "available" and .helper.peer_verified == true and .network_security_verified == false and .hardware_test_performed == false and .built_in_encryption == false' /tmp/doctor.json > /dev/null
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then
        echo 'unapproved UID unexpectedly accepted'
        exit 1
    fi
    grep -F 'caller UID is not approved by the administrator' /tmp/denied.log
}
if [ "$ROLE" = server ]; then
    STAGE=select-simulated-devices
    for attempt in $(seq 1 100); do
        unpriv --json devices > /tmp/devices.json
        [ "$(jq 'length' /tmp/devices.json)" != 2 ] || break
        sleep 0.05
    done
    jq -e 'length == 2 and all(.[]; (.export_blockers | length) == 0)' /tmp/devices.json > /dev/null
    HID_ID=$(jq -r '.[] | select(.vendor_id == 1575 and .product_id == 1) | .id' /tmp/devices.json)
    FTDI_ID=$(jq -r '.[] | select(.vendor_id == 1027 and .product_id == 24577) | .id' /tmp/devices.json)
    HID_BUS=$(jq -r '.[] | select(.vendor_id == 1575 and .product_id == 1) | .bus_id' /tmp/devices.json)
    FTDI_BUS=$(jq -r '.[] | select(.vendor_id == 1027 and .product_id == 24577) | .bus_id' /tmp/devices.json)
    [ -n "$HID_ID" ] && [ -n "$FTDI_ID" ] && [ "$HID_ID" != "$FTDI_ID" ]
    original_drivers() {
        for attempt in $(seq 1 150); do
            HID_DRIVER=$(readlink "/sys/bus/usb/devices/$HID_BUS:1.0/driver" 2>/dev/null | sed 's#.*/##')
            FTDI_DRIVER=$(readlink "/sys/bus/usb/devices/$FTDI_BUS:1.0/driver" 2>/dev/null | sed 's#.*/##')
            [ "$HID_DRIVER" != usbhid ] || [ "$FTDI_DRIVER" != ftdi_sio ] || return 0
            sleep 0.05
        done
        echo "original driver restoration failed: HID=$HID_DRIVER FTDI=$FTDI_DRIVER"
        return 1
    }
    original_drivers
    [ "$(cat "/sys/bus/usb/devices/$HID_BUS/speed")" = 480 ]
    [ "$(cat "/sys/bus/usb/devices/$FTDI_BUS/speed")" = 12 ]
    HID_NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$HID_BUS/busnum")" "$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")")
    HID_DEVNUM=$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")
    FTDI_NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$FTDI_BUS/busnum")" "$(cat "/sys/bus/usb/devices/$FTDI_BUS/devnum")")
    for node in "$HID_NODE" "$FTDI_NODE"; do
        [ -c "$node" ]
        [ "$(stat -c '%u' "$node")" = 0 ]
        chmod 600 "$node"
        [ "$(stat -c '%u:%a' "$node")" = 0:600 ]
        for uid in 1000 1001; do
            if /usr/bin/setpriv --reuid="$uid" --regid="$uid" --clear-groups /bin/busybox sh -c 'exec 3<> "$1"' sh "$node" > /tmp/direct-open.log 2>&1; then
                echo "unprivileged direct usbfs open unexpectedly succeeded uid=$uid"
                exit 1
            fi
        done
    done
    echo 'RUSTUSB_PHYSICAL_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied nodes=root0600'
    jq -n --arg hid "$HID_ID" --arg ftdi "$FTDI_ID" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[$hid,$ftdi],peers:[],trusted_network:true}' > /etc/rustusb/policy.json
    STAGE=physical-helper-authorization
    start_helper
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb physical-server \
        --device-id "$HID_ID" --device-id "$FTDI_ID" --listen 127.0.0.1:0 --control-stdin < /dev/null > /tmp/denied-fd.log 2>&1; then
        echo 'unapproved UID unexpectedly obtained physical device capabilities'
        exit 1
    fi
    grep -F 'caller UID is not approved by the administrator' /tmp/denied-fd.log
    echo 'RUSTUSB_PHYSICAL_HELPER_UID_GATE: PASS denied_uid=1001 allowed_uid=1000'
    chown 1000:1000 /run/physical
    chmod 700 /run/physical
    mkfifo -m 600 /run/physical/control
    chown 1000:1000 /run/physical/control
    exec 3<> /run/physical/control
    (
        exec 3<&-
        exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb physical-server \
            --device-id "$HID_ID" --device-id "$FTDI_ID" --listen 192.0.2.1:3240 \
            --allow-insecure-test-network --allow-peer 192.0.2.2 --control-stdin \
            < /run/physical/control > /tmp/server.events 2> /tmp/server.log
    ) &
    SERVER_PID=$!
    STAGE=physical-server-ready
    for attempt in $(seq 1 100); do
        if jq -se 'any(.[]; .event == "ready")' /tmp/server.events > /dev/null 2>&1; then break; fi
        kill -0 "$SERVER_PID"
        sleep 0.05
    done
    jq -se 'any(.[]; .schema_version == 1 and .event == "ready" and .address == "192.0.2.1:3240" and (.snapshot.devices | length) == 2)' /tmp/server.events > /dev/null
    SERVER_START=$(awk '{print $22}' "/proc/$SERVER_PID/stat")
    server_identity() {
        kill -0 "$SERVER_PID"
        [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$SERVER_PID/status")" = 1000:1000:1000:1000 ]
        [ "$(awk '{print $22}' "/proc/$SERVER_PID/stat")" = "$SERVER_START" ]
        for capability in CapEff CapPrm CapAmb; do
            [ "$(awk -v name="$capability:" '$1 == name { print $2 }' "/proc/$SERVER_PID/status")" = 0000000000000000 ]
        done
        for node in "$HID_NODE" "$FTDI_NODE"; do
            if [ "$ALLOW_GONE" = true ] && [ "$node" = "$HID_NODE" ]; then
                [ ! -e "$node" ]
                continue
            fi
            [ "$(stat -c '%u:%a' "$node")" = 0:600 ]
            found=false
            for fd in /proc/"$SERVER_PID"/fd/*; do
                [ "$(readlink "$fd")" != "$node" ] || found=true
            done
            [ "$found" = true ]
        done
        [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    }
    control() {
        printf '%s\n' "$1" | /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox tee /run/physical/control > /dev/null
        for attempt in $(seq 1 100); do
            if jq -se --arg id "$2" 'any(.[]; .id == $id and (.event == "ack" or .event == "snapshot"))' /tmp/server.events > /dev/null 2>&1; then
                jq -se --arg id "$2" 'all(.[] | select(.id == $id); .schema_version == 1 and (.event == "snapshot" or (.event == "ack" and .ok == true)))' /tmp/server.events > /dev/null
                return 0
            fi
            kill -0 "$SERVER_PID"
            sleep 0.05
        done
        echo "server control acknowledgment missing id=$2"
        return 1
    }
    snapshot() {
        control "{\"id\":\"$1\",\"op\":\"snapshot\"}" "$1"
        jq -s --arg id "$1" '.[] | select(.event == "snapshot" and .id == $id) | .snapshot' /tmp/server.events > "/tmp/$1.json"
        jq -e '.schema_version == 1 and (.devices | length) == 2' "/tmp/$1.json" > /dev/null
        server_identity
    }
    released_snapshot() {
        jq -e '.buffer_bytes_reserved == 0 and all(.devices[]; .state == "available" and .leased == false and .pending_urbs == 0 and .queued_urbs == 0 and .usbfs_inflight == 0 and .cleanup_verified == true and .current_drivers == .original_drivers)' "$1" > /dev/null
        original_drivers
    }
    server_identity
    echo 'RUSTUSB_PHYSICAL_SERVER_UID: PASS uid=1000 app_root=false capabilities=0'
    STAGE=independent-real-usb-lifecycle
    receive_control lifecycle-passed
    snapshot baseline
    released_snapshot /tmp/baseline.json
    jq -e --arg hid "$HID_BUS" --arg ftdi "$FTDI_BUS" '
        (.devices[] | select(.bus_id == $hid) | .cancelled >= 2 and .usbfs_reaped >= 4 and .counters.unlink_cancelled >= 1) and
        (.devices[] | select(.bus_id == $ftdi) | .cancelled >= 1 and .usbfs_reaped >= 4)
    ' /tmp/baseline.json > /dev/null
    echo 'RUSTUSB_PHYSICAL_PROBE_CLEANUP: PASS original_drivers=true inflight=0'
    send_control 192.0.2.2 lifecycle-checked
    for cycle in 1 2; do
        STAGE="cycle-$cycle"
        receive_control "cycle-$cycle-ready"
        snapshot "held-$cycle"
        jq -e 'all(.devices[]; .state == "leased" and .leased == true and .configuration == 1 and all(.current_drivers[]; .driver == "usbfs"))' "/tmp/held-$cycle.json" > /dev/null
        [ "$(readlink "/sys/bus/usb/devices/$HID_BUS:1.0/driver" | sed 's#.*/##')" = usbfs ]
        [ "$(readlink "/sys/bus/usb/devices/$FTDI_BUS:1.0/driver" | sed 's#.*/##')" = usbfs ]
        echo "RUSTUSB_PHYSICAL_SERVER_HELD: PASS cycle=$cycle drivers=usbfs leases=2"
        send_control 192.0.2.2 "cycle-$cycle-input"
        receive_control "cycle-$cycle-io-complete"
        snapshot "io-$cycle"
        jq -e --arg hid "$HID_BUS" --arg ftdi "$FTDI_BUS" 'all(.devices[]; .state == "leased" and .counters.controls > 0) and (.devices[] | select(.bus_id == $hid) | .counters.interrupt_in >= 2) and (.devices[] | select(.bus_id == $ftdi) | .counters.bulk_in > 0 and .counters.bulk_out > 0)' "/tmp/io-$cycle.json" > /dev/null
        send_control 192.0.2.2 "cycle-$cycle-detach"
        receive_control "cycle-$cycle-released"
        snapshot "released-$cycle"
        released_snapshot "/tmp/released-$cycle.json"
        echo "RUSTUSB_PHYSICAL_SERVER_RELEASED: PASS cycle=$cycle inflight=0 drivers=usbhid,ftdi_sio"
        send_control 192.0.2.2 "cycle-$cycle-clean"
        receive_control "cycle-$cycle-checked"
    done
    if [ "$HOT_UNPLUG" = 1 ]; then
        STAGE=active-hot-unplug
        receive_control unplug-ready
        snapshot unplug-held
        jq -e 'all(.devices[]; .state == "leased" and .leased == true)' /tmp/unplug-held.json > /dev/null
        send_control 192.0.2.2 unplug-before
        receive_control unplug-before-io
        send_control 192.0.2.2 unplug-remove
        receive_control unplug-hid-gone
        ALLOW_GONE=true
        [ ! -e "/sys/bus/usb/devices/$HID_BUS" ] && [ ! -e "$HID_NODE" ]
        snapshot unplug-gone
        jq -e --arg hid "$HID_BUS" --arg ftdi "$FTDI_BUS" '
            (.devices[] | select(.bus_id == $hid) | .state == "gone" and .leased == false and .pending_urbs == 0 and .queued_urbs == 0 and .usbfs_inflight == 0) and
            (.devices[] | select(.bus_id == $ftdi) | .state == "leased" and .leased == true)
        ' /tmp/unplug-gone.json > /dev/null
        echo 'RUSTUSB_PHYSICAL_GONE: PASS old_hid_gone=true ftdi_leased=true inflight_hid=0'
        send_control 192.0.2.2 unplug-after
        receive_control unplug-after-io
        snapshot unplug-io
        jq -e --arg ftdi "$FTDI_BUS" --slurpfile before /tmp/unplug-gone.json '
            (.devices[] | select(.bus_id == $ftdi)) as $after |
            ($before[0].devices[] | select(.bus_id == $ftdi)) as $old |
            $after.state == "leased" and $after.counters.bulk_in > $old.counters.bulk_in and $after.counters.bulk_out > $old.counters.bulk_out
        ' /tmp/unplug-io.json > /dev/null
        send_control 192.0.2.2 unplug-detach
        receive_control unplug-released
        snapshot unplug-released
        jq -e --arg hid "$HID_BUS" --arg ftdi "$FTDI_BUS" '
            .buffer_bytes_reserved == 0 and all(.devices[]; .pending_urbs == 0 and .queued_urbs == 0 and .usbfs_inflight == 0) and
            (.devices[] | select(.bus_id == $hid) | .state == "gone") and
            (.devices[] | select(.bus_id == $ftdi) | .state == "available" and .cleanup_verified == true and .current_drivers == .original_drivers)
        ' /tmp/unplug-released.json > /dev/null
        [ "$(readlink "/sys/bus/usb/devices/$FTDI_BUS:1.0/driver" | sed 's#.*/##')" = ftdi_sio ]
        send_control 192.0.2.2 unplug-reinsert
        for attempt in $(seq 1 200); do
            if [ -f "/sys/bus/usb/devices/$HID_BUS/devnum" ] && [ "$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")" != "$HID_DEVNUM" ]; then break; fi
            sleep 0.05
        done
        [ "$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")" != "$HID_DEVNUM" ]
        NEW_HID_NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$HID_BUS/busnum")" "$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")")
        [ -c "$NEW_HID_NODE" ] && [ "$NEW_HID_NODE" != "$HID_NODE" ]
        chmod 600 "$NEW_HID_NODE"
        [ "$(stat -c '%u:%a' "$NEW_HID_NODE")" = 0:600 ]
        for attempt in $(seq 1 100); do
            driver=$(readlink "/sys/bus/usb/devices/$HID_BUS:1.0/driver" 2>/dev/null | sed 's#.*/##')
            [ "$driver" != usbhid ] || break
            sleep 0.05
        done
        [ "$driver" = usbhid ]
        snapshot replug-stale
        jq -e --arg hid "$HID_BUS" '(.devices[] | select(.bus_id == $hid) | .state == "gone" and .leased == false and .pending_urbs == 0 and .usbfs_inflight == 0)' /tmp/replug-stale.json > /dev/null
        echo 'RUSTUSB_PHYSICAL_REINSERT_STALE: PASS new_generation=true old_slot_gone=true nodes=root0600'
        send_control 192.0.2.2 unplug-reinsert-checked
        receive_control unplug-checked
    fi
    STAGE=shutdown
    control '{"id":"stop","op":"shutdown"}' stop
    wait "$SERVER_PID"
    exec 3<&-
    jq -s '.[] | select(.event == "stopped") | .snapshot' /tmp/server.events > /tmp/stopped.json
    if [ "$HOT_UNPLUG" = 0 ]; then
        released_snapshot /tmp/stopped.json
        echo 'RUSTUSB_PHYSICAL_STOPPED: PASS exit=0 original_drivers=true inflight=0'
    else
        jq -e '.buffer_bytes_reserved == 0 and all(.devices[]; .leased == false and .pending_urbs == 0 and .queued_urbs == 0 and .usbfs_inflight == 0) and ([.devices[] | select(.state == "gone")] | length) == 1 and ([.devices[] | select(.state == "available" and .cleanup_verified == true)] | length) == 1' /tmp/stopped.json > /dev/null
        [ "$(readlink "/sys/bus/usb/devices/$FTDI_BUS:1.0/driver" | sed 's#.*/##')" = ftdi_sio ]
        echo 'RUSTUSB_PHYSICAL_STOPPED: PASS exit=0 ftdi_driver=ftdi_sio gone_hid=true inflight=0'
    fi
    jq -se 'all(.[]; .event != "error") and any(.[]; .event == "stopped")' /tmp/server.events > /dev/null
    echo 'RUSTUSB_PHYSICAL_EVENTS_BEGIN'
    cat /tmp/server.events
    echo 'RUSTUSB_PHYSICAL_EVENTS_END'
    [ ! -s /tmp/server.log ] || { echo 'RUSTUSB_PHYSICAL_STDERR_BEGIN'; cat /tmp/server.log; echo 'RUSTUSB_PHYSICAL_STDERR_END'; }
    send_control 192.0.2.2 finished
else
    STAGE=client-helper
    jq -n '{allowed_uids:[1000],export_device_ids:[],peers:[{host:"192.0.2.1",port:3240}],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    jq -e '.helper.kernel_available == true and .native.vhci_hcd.sysfs.status == "available"' /tmp/doctor.json > /dev/null
    echo 'RUSTUSB_PHYSICAL_UID_GATE: PASS denied_uid=1001 allowed_uid=1000'
    discover_two() {
        for attempt in $(seq 1 150); do
            if unpriv --json --timeout 2 discover 192.0.2.1 > /tmp/discover.json 2>/dev/null && jq -e 'length == 2 and any(.[]; .vendor_id == 1575 and .product_id == 1) and any(.[]; .vendor_id == 1027 and .product_id == 24577)' /tmp/discover.json > /dev/null; then return 0; fi
            sleep 0.05
        done
        return 1
    }
    discover_two
    HID_BUS=$(jq -r '.[] | select(.vendor_id == 1575) | .bus_id' /tmp/discover.json)
    FTDI_BUS=$(jq -r '.[] | select(.vendor_id == 1027) | .bus_id' /tmp/discover.json)
    STAGE=real-usb-wire-probe
    probe lifecycle
    send_control 192.0.2.1 lifecycle-passed
    receive_control lifecycle-checked
    for cycle in 1 2; do
        STAGE="client-cycle-$cycle"
        unpriv --json attach 192.0.2.1 "$HID_BUS" > /tmp/attach-hid.json
        unpriv --json attach 192.0.2.1 "$FTDI_BUS" > /tmp/attach-ftdi.json
        cat /tmp/attach-hid.json /tmp/attach-ftdi.json
        HID_PORT=$(jq -r '.port' /tmp/attach-hid.json)
        HID_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-hid.json)
        FTDI_PORT=$(jq -r '.port' /tmp/attach-ftdi.json)
        FTDI_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-ftdi.json)
        [ "$HID_LOCAL" != null ] && [ "$FTDI_LOCAL" != null ] && [ "$HID_PORT" != "$FTDI_PORT" ]
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 2 and all(.[]; .managed == true and .session.status == 6)' /tmp/sessions.json > /dev/null
        [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idVendor")" = 0627 ]
        [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idProduct")" = 0001 ]
        [ "$(cat "/sys/bus/usb/devices/$FTDI_LOCAL/idVendor")" = 0403 ]
        [ "$(cat "/sys/bus/usb/devices/$FTDI_LOCAL/idProduct")" = 6001 ]
        [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/speed")" = 480 ]
        [ "$(cat "/sys/bus/usb/devices/$FTDI_LOCAL/speed")" = 12 ]
        EVENT=
        TTY=
        for attempt in $(seq 1 100); do
            EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
            TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$FTDI_LOCAL")" -name 'ttyUSB*' -type d | head -1)
            [ -z "$EVENT" ] || [ -z "$TTY" ] || break
            sleep 0.05
        done
        [ -n "$EVENT" ] && [ -n "$TTY" ]
        [ "$(readlink "/sys/bus/usb/devices/$HID_LOCAL:1.0/driver" | sed 's#.*/##')" = usbhid ]
        [ "$(readlink "/sys/bus/usb/devices/$FTDI_LOCAL:1.0/driver" | sed 's#.*/##')" = ftdi_sio ]
        EVENT=/dev/input/$(basename "$EVENT")
        TTY=/dev/$(basename "$TTY")
        : > /tmp/input.bin
        exec 3< "$EVENT"
        cat <&3 > /tmp/input.bin &
        INPUT_READER=$!
        exec 3<&-
        stty -F "$TTY" raw -echo 115200 clocal -hupcl
        exec 4<> "$TTY"
        : > /tmp/rx.bin
        timeout 8 dd bs=1 count=35 <&4 > /tmp/rx.bin 2> /tmp/dd.log &
        SERIAL_READER=$!
        probe busy "$HID_BUS" "$FTDI_BUS"
        echo "RUSTUSB_PHYSICAL_ATTACHED: PASS cycle=$cycle sessions=2 hid_driver=usbhid serial_driver=ftdi_sio"
        send_control 192.0.2.1 "cycle-$cycle-ready"
        receive_control "cycle-$cycle-input"
        echo "RUSTUSB_PHYSICAL_WAIT_INPUT cycle=$cycle"
        echo "RUSTUSB_PHYSICAL_WAIT_SERIAL cycle=$cycle"
        wait "$SERIAL_READER"
        printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$cycle" > /tmp/expected.bin
        [ "$(wc -c < /tmp/rx.bin)" = 35 ]
        cmp /tmp/rx.bin /tmp/expected.bin
        printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$cycle" > /tmp/tx.bin
        [ "$(wc -c < /tmp/tx.bin)" = 35 ]
        cat /tmp/tx.bin >&4
        key_code=$((cycle + 1))
        key_seen=false
        for attempt in $(seq 1 100); do
            bytes=$(wc -c < /tmp/input.bin)
            if [ "$bytes" -gt 0 ] && [ "$((bytes % 24))" = 0 ] && od -An -tu4 -w24 -v /tmp/input.bin | awk -v code="$key_code" '
                NF == 6 && $5 == code * 65536 + 1 && $6 == 1 { down = 1 }
                NF == 6 && $5 == code * 65536 + 1 && $6 == 0 { up = 1 }
                END { exit !(down && up) }
            '; then key_seen=true; break; fi
            sleep 0.05
        done
        [ "$key_seen" = true ]
        unpriv --json sessions > /tmp/sessions.json
        jq -e --slurpfile hid /tmp/attach-hid.json --slurpfile ftdi /tmp/attach-ftdi.json 'length == 2 and all(.[]; .managed == true) and any(.[]; .session == $hid[0]) and any(.[]; .session == $ftdi[0])' /tmp/sessions.json > /dev/null
        echo "RUSTUSB_PHYSICAL_HID: PASS cycle=$cycle key_code=$key_code down=true up=true same_receipt=true"
        echo "RUSTUSB_PHYSICAL_FTDI: PASS cycle=$cycle in=35 out=35 exact_bytes=true tty_echo=false same_receipt=true"
        send_control 192.0.2.1 "cycle-$cycle-io-complete"
        receive_control "cycle-$cycle-detach"
        kill "$INPUT_READER"
        wait "$INPUT_READER" || true
        exec 4<&-
        unpriv --json detach "$HID_PORT"
        unpriv --json detach "$FTDI_PORT"
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 0' /tmp/sessions.json > /dev/null
        [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$FTDI_LOCAL" ]
        discover_two
        echo "RUSTUSB_PHYSICAL_CLIENT_RELEASED: PASS cycle=$cycle kernel_sessions=0 both_devices_removed=true"
        send_control 192.0.2.1 "cycle-$cycle-released"
        receive_control "cycle-$cycle-clean"
        probe health
        echo "RUSTUSB_PHYSICAL_REDISCOVERED: PASS cycle=$cycle available_devices=2"
        send_control 192.0.2.1 "cycle-$cycle-checked"
    done
    if [ "$HOT_UNPLUG" = 1 ]; then
        STAGE=client-active-hot-unplug
        unpriv --json attach 192.0.2.1 "$HID_BUS" > /tmp/attach-hid.json
        unpriv --json attach 192.0.2.1 "$FTDI_BUS" > /tmp/attach-ftdi.json
        HID_PORT=$(jq -r '.port' /tmp/attach-hid.json)
        HID_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-hid.json)
        FTDI_PORT=$(jq -r '.port' /tmp/attach-ftdi.json)
        FTDI_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-ftdi.json)
        EVENT=
        TTY=
        for attempt in $(seq 1 100); do
            EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
            TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$FTDI_LOCAL")" -name 'ttyUSB*' -type d | head -1)
            [ -z "$EVENT" ] || [ -z "$TTY" ] || break
            sleep 0.05
        done
        [ -n "$EVENT" ] && [ -n "$TTY" ]
        : > /tmp/hot-input.bin
        exec 3< "/dev/input/$(basename "$EVENT")"
        cat <&3 > /tmp/hot-input.bin &
        INPUT_READER=$!
        exec 3<&-
        TTY=/dev/$(basename "$TTY")
        stty -F "$TTY" raw -echo 115200 clocal -hupcl
        exec 4<> "$TTY"
        hot_frame() {
            : > /tmp/hot-rx.bin
            timeout 8 dd bs=1 count=35 <&4 > /tmp/hot-rx.bin 2> /tmp/dd.log &
            SERIAL_READER=$!
            echo "RUSTUSB_PHYSICAL_WAIT_SERIAL cycle=$1"
            wait "$SERIAL_READER"
            printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$1" > /tmp/expected.bin
            [ "$(wc -c < /tmp/hot-rx.bin)" = 35 ]
            cmp /tmp/hot-rx.bin /tmp/expected.bin
            printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$1" >&4
        }
        send_control 192.0.2.1 unplug-ready
        receive_control unplug-before
        hot_frame 3
        send_control 192.0.2.1 unplug-before-io
        receive_control unplug-remove
        echo 'RUSTUSB_PHYSICAL_WAIT_UNPLUG device=physical-kbd'
        for attempt in $(seq 1 200); do
            [ -e "/sys/bus/usb/devices/$HID_LOCAL" ] || break
            sleep 0.05
        done
        [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ]
        unpriv --json detach "$HID_PORT"
        unpriv --json sessions > /tmp/sessions.json
        jq -e --slurpfile ftdi /tmp/attach-ftdi.json 'length == 1 and .[0].managed == true and .[0].session == $ftdi[0]' /tmp/sessions.json > /dev/null
        echo 'RUSTUSB_PHYSICAL_UNPLUG: PASS hid_kernel_gone=true ftdi_same_receipt=true'
        send_control 192.0.2.1 unplug-hid-gone
        receive_control unplug-after
        hot_frame 4
        unpriv --json sessions > /tmp/sessions.json
        jq -e --slurpfile ftdi /tmp/attach-ftdi.json 'length == 1 and .[0].managed == true and .[0].session == $ftdi[0]' /tmp/sessions.json > /dev/null
        echo 'RUSTUSB_PHYSICAL_FTDI_CONTINUES: PASS before=35 after=35 exact_bytes=true same_receipt=true'
        send_control 192.0.2.1 unplug-after-io
        receive_control unplug-detach
        kill "$INPUT_READER" 2>/dev/null || true
        wait "$INPUT_READER" || true
        exec 4<&-
        unpriv --json detach "$FTDI_PORT"
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 0' /tmp/sessions.json > /dev/null
        [ ! -e "/sys/bus/usb/devices/$FTDI_LOCAL" ]
        send_control 192.0.2.1 unplug-released
        receive_control unplug-reinsert
        echo 'RUSTUSB_PHYSICAL_WAIT_REINSERT device=physical-kbd'
        receive_control unplug-reinsert-checked
        unpriv --json discover 192.0.2.1 > /tmp/discover.json
        jq -e 'length == 1 and .[0].vendor_id == 1027 and .[0].product_id == 24577' /tmp/discover.json > /dev/null
        echo 'RUSTUSB_PHYSICAL_NO_AUTO_AUTH: PASS reinserted_hid_not_advertised=true available_devices=1'
        send_control 192.0.2.1 unplug-checked
    fi
    receive_control finished
fi
echo "RUSTUSB_PHYSICAL_RESULT: PASS role=$ROLE"
