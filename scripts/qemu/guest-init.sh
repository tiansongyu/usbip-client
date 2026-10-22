#!/bin/sh
# Disposable guest only: never run this script directly on the host.
set -eu
# PID 1 plus an explicit kernel-provided init environment marker are required
# before the first mount. A normal host invocation cannot pass the PID check.
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo "Refusing to run outside the disposable QEMU guest init" >&2
    exit 2
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in
    *' rustusb.role=server '*|*' rustusb.role=client '*) ;;
    *) echo "Missing QEMU guest role; refusing further mounts" >&2; exit 2 ;;
esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /var/run/vhci_hcd /etc/rustusb
chmod 755 /run/rustusb /etc/rustusb
mount -t devpts devpts /dev/pts
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_QEMU_RESULT: FAIL role=$ROLE exit=$status"
        ls -ld / /etc /etc/rustusb /etc/rustusb/policy.json /run /run/rustusb /usr /usr/bin
        for file in /tmp/helper.log /tmp/usbipd.log /tmp/discover.json /tmp/attach.json /tmp/devices.json; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
        dmesg | tail -40
    fi
    sync
    poweroff -f
}
ROLE=unknown
trap cleanup EXIT
case " $(cat /proc/cmdline) " in
    *' rustusb.role=server '*) ROLE=server ;;
    *' rustusb.role=client '*) ROLE=client ;;
    *) exit 1 ;;
esac
echo "RUSTUSB_QEMU_BOOT role=$ROLE kernel=$(uname -r)"
for name in usb-common usbcore e1000 xhci-hcd xhci-pci-renesas xhci-pci evdev hid hid-generic usbhid usbip-core; do
    [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
done
ifconfig lo 127.0.0.1 up
if [ "$ROLE" = server ]; then
    [ ! -f /modules/usbip-host.ko ] || insmod /modules/usbip-host.ko
    ifconfig eth0 192.0.2.1 netmask 255.255.255.0 up
else
    [ ! -f /modules/vhci-hcd.ko ] || insmod /modules/vhci-hcd.ko
    ifconfig eth0 192.0.2.2 netmask 255.255.255.0 up
fi
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
start_helper() {
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &
    for i in $(seq 1 50); do
        [ ! -S /run/rustusb/control.sock ] || return 0
        sleep 0.1
    done
    echo "helper did not start"
    return 1
}
if [ "$ROLE" = server ]; then
    sleep 1
    unpriv --json devices > /tmp/devices.json
    # This guest has exactly one synthetic USB device: QEMU's keyboard.
    [ "$(jq 'length' /tmp/devices.json)" = 1 ]
    [ "$(jq '.[0].export_blockers | length' /tmp/devices.json)" = 0 ]
    ID=$(jq -r '.[0].id' /tmp/devices.json)
    BUSID=$(jq -r '.[0].bus_id' /tmp/devices.json)
    jq -n --arg id "$ID" '{allowed_uids:[1000],export_device_ids:[$id],peers:[],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    unpriv --json export "$ID"
    /usr/bin/usbipd -4 > /tmp/usbipd.log 2>&1 &
    echo "RUSTUSB_SERVER_READY bus_id=$BUSID cli_uid=$(/usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups id -u)"
    for cycle in 1 2; do
        action=$(nc -l -p 5001)
        [ "$action" = unexport ]
        for i in $(seq 1 50); do
            [ "$(cat "/sys/bus/usb/devices/$BUSID/usbip_status")" != 1 ] || break
            sleep 0.1
        done
        unpriv --json unexport "$ID"
        [ "$(readlink "/sys/bus/usb/devices/$BUSID/driver" | sed 's#.*/##')" = usb ]
        for i in $(seq 1 50); do
            LOCAL_DRIVER=$(readlink "/sys/bus/usb/devices/$BUSID:1.0/driver" 2>/dev/null | sed 's#.*/##')
            [ "$LOCAL_DRIVER" != usbhid ] || break
            sleep 0.1
        done
        [ "$LOCAL_DRIVER" = usbhid ]
        echo "RUSTUSB_SERVER_UNEXPORTED cycle=$cycle local_driver=usb interface_driver=usbhid"
        if [ "$cycle" = 1 ]; then
            action=$(nc -l -p 5001)
            [ "$action" = export ]
            unpriv --json export "$ID"
            echo "RUSTUSB_SERVER_REEXPORTED cycle=2"
        fi
    done
    # Keep server alive long enough for client's final DEVLIST=empty assertion.
    sleep 3
    echo "RUSTUSB_QEMU_RESULT: PASS role=server"
else
    cat > /etc/rustusb/policy.json <<'JSON'
{"allowed_uids":[1000],"export_device_ids":[],"peers":[{"host":"192.0.2.1","port":3240}],"trusted_network":true}
JSON
    start_helper
    # Exercise the OS peer-credential gate using an unapproved, unprivileged UID.
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.json 2>/dev/null; then
        echo "unapproved UID unexpectedly succeeded"
        exit 1
    fi
    echo "RUSTUSB_UID_GATE: PASS denied_uid=1001 allowed_uid=1000"
    wait_count() {
        expected=$1
        for i in $(seq 1 60); do
            if unpriv --json --timeout 2 discover 192.0.2.1 > /tmp/discover.json 2>/dev/null; then
                [ "$(jq 'length' /tmp/discover.json)" != "$expected" ] || return 0
            fi
            sleep 0.2
        done
        echo "remote DEVLIST count did not become $expected"
        return 1
    }
    for cycle in 1 2; do
        wait_count 1
        BUSID=$(jq -r '.[0].bus_id' /tmp/discover.json)
        unpriv --json attach 192.0.2.1 "$BUSID" > /tmp/attach.json
        cat /tmp/attach.json
        PORT=$(jq -r '.port' /tmp/attach.json)
        LOCAL=$(jq -r '.local_bus_id' /tmp/attach.json)
        [ "$PORT" != null ]
        [ "$LOCAL" != null ]
        unpriv --json sessions > /tmp/sessions.json
        [ "$(jq '[.[] | select(.managed == true)] | length' /tmp/sessions.json)" = 1 ]
        EVENT=
        for i in $(seq 1 50); do
            EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$LOCAL")" -name 'event*' -type d | head -1)
            [ -z "$EVENT" ] || break
            sleep 0.1
        done
        [ -n "$EVENT" ]
        EVENT=$(basename "$EVENT")
        [ "$(readlink "/sys/bus/usb/devices/$LOCAL:1.0/driver" | sed 's#.*/##')" = usbhid ]
        timeout 5 cat "/dev/input/$EVENT" > /tmp/keyboard.bin &
        reader=$!
        echo "RUSTUSB_WAIT_INPUT cycle=$cycle local_bus_id=$LOCAL driver=usbhid"
        # BusyBox timeout exits 143 while GNU timeout uses 124. The captured
        # evdev event, rather than the timeout implementation, is the assertion.
        wait "$reader" || true
        # x86_64 input_event has two 64-bit timestamps followed by type:u16,
        # code:u16, value:i32. Inspect aligned records, not timestamp substrings.
        BYTE_COUNT=$(wc -c < /tmp/keyboard.bin)
        [ "$BYTE_COUNT" -gt 0 ] && [ "$((BYTE_COUNT % 24))" = 0 ]
        if od -An -tu4 -w24 -v /tmp/keyboard.bin | awk '
            NF == 6 && $5 == 131073 && $6 == 1 { found = 1 }
            END { exit !found }
        '; then
            echo "RUSTUSB_HID_INPUT: PASS cycle=$cycle KEY_1_down"
        else
            echo "HID input missing cycle=$cycle"
            exit 1
        fi
        unpriv --json detach "$PORT"
        unpriv --json sessions > /tmp/sessions.json
        [ "$(jq 'length' /tmp/sessions.json)" = 0 ]
        for i in $(seq 1 50); do
            [ -e "/sys/bus/usb/devices/$LOCAL" ] || break
            sleep 0.1
        done
        [ ! -e "/sys/bus/usb/devices/$LOCAL" ]
        echo "RUSTUSB_CLIENT_DETACHED cycle=$cycle kernel_sessions=0 device_removed=true"
        printf unexport | nc -w 1 192.0.2.1 5001
        wait_count 0
        if [ "$cycle" = 1 ]; then
            printf export | nc -w 1 192.0.2.1 5001
        fi
    done
    echo "RUSTUSB_QEMU_RESULT: PASS role=client"
fi
