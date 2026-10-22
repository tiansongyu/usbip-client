#!/bin/sh
# PID 1 of a disposable QEMU guest only; no physical USB or host network changes.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo 'Refusing to run outside the disposable QEMU guest init' >&2
    exit 2
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in
    *' rustusb.role=server '*|*' rustusb.role=client-a '*|*' rustusb.role=client-b '*) ;;
    *) echo 'Invalid contention guest role' >&2; exit 2 ;;
esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /var/run/vhci_hcd /etc/rustusb
chmod 755 /run/rustusb /etc/rustusb
mount -t devpts devpts /dev/pts
ROLE=unknown
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in rustusb.role=*) ROLE=${parameter#*=} ;; esac
done
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_CONTENTION_RESULT: FAIL role=$ROLE exit=$status"
        for file in /tmp/helper.log /tmp/usbipd.log /tmp/devices.json /tmp/discover.json /tmp/attach.json /tmp/sessions.json /tmp/owned.json /tmp/rustusb-busy.log /tmp/linux-busy.log /tmp/denied.log; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
        if [ "$ROLE" != server ] && [ -S /run/rustusb/control.sock ]; then
            echo 'DIAGNOSTIC authoritative sessions after failure'
            unpriv --json sessions || true
        fi
        dmesg | tail -50
    fi
    sync
    poweroff -f
}
trap cleanup EXIT
echo "RUSTUSB_CONTENTION_BOOT role=$ROLE kernel=$(uname -r)"
for name in usb-common usbcore e1000 xhci-hcd xhci-pci-renesas xhci-pci evdev hid hid-generic usbhid usbip-core; do
    [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
done
ifconfig lo 127.0.0.1 up
case "$ROLE" in
    server)
        [ ! -f /modules/usbip-host.ko ] || insmod /modules/usbip-host.ko
        ifconfig eth0 192.0.2.1 netmask 255.255.255.0 up
        ifconfig eth1 192.0.3.1 netmask 255.255.255.0 up
        ;;
    client-a|client-b)
        [ ! -f /modules/vhci-hcd.ko ] || insmod /modules/vhci-hcd.ko
        if [ "$ROLE" = client-a ]; then PEER=192.0.2.1; ADDRESS=192.0.2.2; else PEER=192.0.3.1; ADDRESS=192.0.3.2; fi
        ifconfig eth0 "$ADDRESS" netmask 255.255.255.0 up
        ;;
    *) exit 2 ;;
esac
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = 0 ]
echo "RUSTUSB_CONTENTION_ISOLATION role=$ROLE ip_forward=0"
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
start_helper() {
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &
    for i in $(seq 1 50); do
        [ ! -S /run/rustusb/control.sock ] || break
        sleep 0.1
    done
    [ -S /run/rustusb/control.sock ]
    unpriv --json doctor > /tmp/doctor.json
    jq -e '.schema_version == 1 and .helper.status == "available" and .helper.peer_verified == true and .network_security_verified == false and .hardware_test_performed == false and .built_in_encryption == false' /tmp/doctor.json
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then
        echo 'unapproved UID unexpectedly accepted'
        exit 1
    fi
    grep -F 'caller UID is not approved by the administrator' /tmp/denied.log
    echo "RUSTUSB_CONTENTION_UID_GATE: PASS role=$ROLE denied_uid=1001 allowed_uid=1000"
}
send_control() {
    for attempt in $(seq 1 100); do
        if printf '%s' "$3" | nc -w 1 "$1" "$2"; then return 0; fi
        sleep 0.1
    done
    echo "control delivery failed: $3"
    return 1
}
receive_control() {
    MESSAGE=$(timeout 30 nc -l -p "$1")
    [ "$MESSAGE" = "$2" ] || { echo "unexpected control message: $MESSAGE expected=$2"; return 1; }
}
wait_server_status() {
    for attempt in $(seq 1 100); do
        [ "$(cat "/sys/bus/usb/devices/$BUSID/usbip_status")" != "$1" ] || return 0
        sleep 0.1
    done
    echo "server status did not become $1"
    return 1
}
if [ "$ROLE" = server ]; then
    sleep 1
    unpriv --json devices > /tmp/devices.json
    [ "$(jq 'length' /tmp/devices.json)" = 1 ]
    [ "$(jq '.[0].export_blockers | length' /tmp/devices.json)" = 0 ]
    ID=$(jq -r '.[0].id' /tmp/devices.json)
    BUSID=$(jq -r '.[0].bus_id' /tmp/devices.json)
    [ "$(cat "/sys/bus/usb/devices/$BUSID/idVendor")" = 0627 ]
    [ "$(cat "/sys/bus/usb/devices/$BUSID/idProduct")" = 0001 ]
    jq -n --arg id "$ID" '{allowed_uids:[1000],export_device_ids:[$id],peers:[],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    unpriv --json export "$ID"
    /usr/bin/usbipd -4 -d > /tmp/usbipd.log 2>&1 &
    echo "RUSTUSB_CONTENTION_SERVER_READY bus_id=$BUSID"
    receive_control 5002 b-ready
    receive_control 5001 a-ready
    send_control 192.0.2.2 5003 acquire
    receive_control 5001 a-holding
    wait_server_status 2
    echo 'RUSTUSB_CONTENTION_SERVER_USED owner=client-a status=2'
    send_control 192.0.3.2 5003 attempt-busy
    receive_control 5002 b-busy-rejected
    wait_server_status 2
    echo 'RUSTUSB_CONTENTION_SERVER_STILL_USED owner=client-a status=2'
    send_control 192.0.2.2 5003 continue
    receive_control 5001 a-released
    wait_server_status 1
    send_control 192.0.3.2 5003 acquire
    receive_control 5002 b-released
    wait_server_status 1
    unpriv --json unexport "$ID"
    [ "$(readlink "/sys/bus/usb/devices/$BUSID/driver" | sed 's#.*/##')" = usb ]
    for attempt in $(seq 1 50); do
        DRIVER=$(readlink "/sys/bus/usb/devices/$BUSID:1.0/driver" 2>/dev/null | sed 's#.*/##')
        [ "$DRIVER" != usbhid ] || break
        sleep 0.1
    done
    [ "$DRIVER" = usbhid ]
    echo 'RUSTUSB_CONTENTION_SERVER_RESTORED local_driver=usb interface_driver=usbhid'
    send_control 192.0.2.2 5003 finished
    send_control 192.0.3.2 5003 finished
    sleep 2
else
    jq -n --arg peer "$PEER" '{allowed_uids:[1000],export_device_ids:[],peers:[{host:$peer,port:3240}],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    for attempt in $(seq 1 100); do
        if unpriv --json --timeout 2 discover "$PEER" > /tmp/discover.json 2>/dev/null && [ "$(jq 'length' /tmp/discover.json)" = 1 ]; then break; fi
        sleep 0.1
    done
    [ "$(jq 'length' /tmp/discover.json)" = 1 ]
    BUSID=$(jq -r '.[0].bus_id' /tmp/discover.json)
    attach_managed() {
        unpriv --json attach "$PEER" "$BUSID" > /tmp/attach.json
        cat /tmp/attach.json
        PORT=$(jq -r '.port' /tmp/attach.json)
        LOCAL=$(jq -r '.local_bus_id' /tmp/attach.json)
        [ "$PORT" != null ] && [ "$LOCAL" != null ]
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 1 and .[0].managed == true and .[0].session.status == 6' /tmp/sessions.json
        [ "$(cat "/sys/bus/usb/devices/$LOCAL/idVendor")" = 0627 ]
        [ "$(cat "/sys/bus/usb/devices/$LOCAL/idProduct")" = 0001 ]
        EVENT=
        for attempt in $(seq 1 50); do
            EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$LOCAL")" -name 'event*' -type d | head -1)
            [ -z "$EVENT" ] || break
            sleep 0.1
        done
        [ -n "$EVENT" ]
        [ "$(readlink "/sys/bus/usb/devices/$LOCAL:1.0/driver" | sed 's#.*/##')" = usbhid ]
        EVENT=/dev/input/$(basename "$EVENT")
        cat "$EVENT" > /tmp/input.bin &
        reader=$!
    }
    wait_key() {
        for attempt in $(seq 1 100); do
            BYTES=$(wc -c < /tmp/input.bin)
            if [ "$BYTES" -gt 0 ] && [ "$((BYTES % 24))" = 0 ] && od -An -tu4 -w24 -v /tmp/input.bin | awk -v code="$1" '
                NF == 6 && $5 == code * 65536 + 1 && $6 == 1 { found = 1 }
                END { exit !found }
            '; then
                echo "RUSTUSB_CONTENTION_INPUT: PASS role=$ROLE phase=$2 key_code=$1"
                return 0
            fi
            sleep 0.1
        done
        echo "expected HID key $1 missing"
        return 1
    }
    detach_managed() {
        kill "$reader"
        wait "$reader" || true
        unpriv --json detach "$PORT"
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 0' /tmp/sessions.json
        [ ! -e "/sys/bus/usb/devices/$LOCAL" ]
        echo "RUSTUSB_CONTENTION_RELEASED role=$ROLE kernel_sessions=0 device_removed=true"
    }
    if [ "$ROLE" = client-a ]; then
        send_control "$PEER" 5001 a-ready
        receive_control 5003 acquire
        attach_managed
        cp /tmp/attach.json /tmp/owned.json
        echo 'RUSTUSB_CONTENTION_WAIT_INPUT role=client-a phase=before key=1'
        wait_key 2 before
        send_control "$PEER" 5001 a-holding
        receive_control 5003 continue
        unpriv --json sessions > /tmp/sessions.json
        jq -e --slurpfile owned /tmp/owned.json 'length == 1 and .[0].managed == true and .[0].session == $owned[0]' /tmp/sessions.json
        cat /tmp/sessions.json
        echo 'RUSTUSB_CONTENTION_WAIT_INPUT role=client-a phase=after key=2'
        wait_key 3 after
        echo 'RUSTUSB_CONTENTION_OWNER_UNCHANGED: PASS role=client-a receipt=true reader_continuous=true'
        detach_managed
        send_control "$PEER" 5001 a-released
    else
        send_control "$PEER" 5002 b-ready
        receive_control 5003 attempt-busy
        if unpriv --json attach "$PEER" "$BUSID" > /tmp/rustusb-busy.log 2>&1; then
            echo 'second RustUSB client unexpectedly imported occupied device'
            exit 1
        fi
        echo 'DIAGNOSTIC /tmp/rustusb-busy.log'
        cat /tmp/rustusb-busy.log
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 0' /tmp/sessions.json
        if grep -F 'requested device is absent from remote export list' /tmp/rustusb-busy.log; then
            MANAGED_REASON=not_advertised
        elif grep -F 'Device busy' /tmp/rustusb-busy.log; then
            MANAGED_REASON=native_busy
        else
            echo 'RustUSB rejection did not establish a known occupied-device observation'
            exit 1
        fi
        echo "RUSTUSB_CONTENTION_MANAGED_REJECT: PASS role=client-b kernel_sessions=0 reason=$MANAGED_REASON"
        {
            printf '\001\021\200\003\000\000\000\000'
            printf '%-32s' "$BUSID" | tr ' ' '\000'
        } > /tmp/import-request.bin
        [ "$(wc -c < /tmp/import-request.bin)" = 40 ]
        timeout 5 nc -w 3 "$PEER" 3240 < /tmp/import-request.bin > /tmp/import-reply.bin
        [ "$(wc -c < /tmp/import-reply.bin)" = 8 ]
        HEX=$(od -An -tx1 -v /tmp/import-reply.bin | tr -d ' \n')
        [ "$HEX" = 0111000300000002 ]
        echo "RUSTUSB_CONTENTION_WIRE_BUSY: PASS role=client-b reply_hex=$HEX status=2"
        if /usr/bin/usbip attach --remote="$PEER" --busid="$BUSID" > /tmp/linux-busy.log 2>&1; then
            echo 'second official client unexpectedly imported occupied device'
            exit 1
        fi
        cat /tmp/linux-busy.log
        grep -F 'Device busy' /tmp/linux-busy.log
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 0' /tmp/sessions.json
        echo 'RUSTUSB_CONTENTION_NATIVE_REJECT: PASS role=client-b kernel_sessions=0'
        send_control "$PEER" 5002 b-busy-rejected
        receive_control 5003 acquire
        attach_managed
        echo 'RUSTUSB_CONTENTION_WAIT_INPUT role=client-b phase=handoff key=3'
        wait_key 4 handoff
        detach_managed
        send_control "$PEER" 5002 b-released
    fi
    receive_control 5003 finished
    unpriv --json --timeout 2 discover "$PEER" > /tmp/discover.json
    jq -e 'length == 0' /tmp/discover.json
    echo "RUSTUSB_CONTENTION_EMPTY: PASS role=$ROLE remote_devices=0"
fi
echo "RUSTUSB_CONTENTION_RESULT: PASS role=$ROLE"
