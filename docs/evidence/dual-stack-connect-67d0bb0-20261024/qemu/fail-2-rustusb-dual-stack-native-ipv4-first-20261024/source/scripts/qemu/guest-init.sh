#!/bin/sh
# Disposable guest only: never run this script directly on the host.
set -eu
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
ROLE=unknown
SCENARIO=managed
DEVICE=hid
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in
        rustusb.role=*) ROLE=${parameter#*=} ;;
        rustusb.scenario=*) SCENARIO=${parameter#*=} ;;
        rustusb.device=*) DEVICE=${parameter#*=} ;;
    esac
done
case "$SCENARIO" in managed|linux-client|linux-server|reconnect) ;; *) exit 2 ;; esac
case "$DEVICE" in
    hid) INTERFACE_DRIVER=usbhid; VENDOR=0627; PRODUCT=0001 ;;
    ftdi-serial) INTERFACE_DRIVER=ftdi_sio; VENDOR=0403; PRODUCT=6001 ;;
    *) exit 2 ;;
esac
[ "$SCENARIO" != reconnect ] || [ "$DEVICE" = hid ]
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_QEMU_RESULT: FAIL role=$ROLE scenario=$SCENARIO device=$DEVICE exit=$status"
        ls -ld / /etc /etc/rustusb /etc/rustusb/policy.json /run /run/rustusb /usr /usr/bin
        for file in /tmp/helper.log /tmp/usbipd.log /tmp/discover.json /tmp/attach.json /tmp/devices.json /tmp/sessions.json /tmp/denied.log /tmp/doctor-before.json /tmp/doctor-after.json /tmp/doctor-denied.json; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
        if [ "$ROLE" = client ] && [ -S /run/rustusb/control.sock ]; then
            echo "DIAGNOSTIC kernel sessions after failure"
            unpriv --json sessions || true
        fi
        dmesg | tail -50
    fi
    sync
    poweroff -f
}
trap cleanup EXIT
echo "RUSTUSB_QEMU_BOOT role=$ROLE scenario=$SCENARIO device=$DEVICE kernel=$(uname -r)"
for name in usb-common usbcore e1000 xhci-hcd xhci-pci-renesas xhci-pci evdev hid hid-generic usbhid usbserial ftdi_sio usbip-core; do
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
    unpriv --json doctor > /tmp/doctor-before.json
    jq -e '.schema_version == 1 and .helper.status == "missing" and .helper.peer_verified == false' /tmp/doctor-before.json
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &
    for i in $(seq 1 50); do
        [ ! -S /run/rustusb/control.sock ] || break
        sleep 0.1
    done
    [ -S /run/rustusb/control.sock ] || { echo "helper did not start"; return 1; }
    unpriv --json doctor > /tmp/doctor-after.json
    /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json doctor > /tmp/doctor-denied.json
    for file in /tmp/doctor-before.json /tmp/doctor-after.json /tmp/doctor-denied.json; do
        echo "DIAGNOSTIC $file"
        cat "$file"
        jq -e '.schema_version == 1 and .built_in_encryption == false and .network_security_verified == false and .hardware_test_performed == false' "$file"
    done
    jq -e '.helper.status == "available" and .helper.peer_verified == true and .native.usbip.check.status == "available" and ((.native.usbipd.check.status == "available" and (.native.usbipd.version | type) == "string") or (.native.usbipd.check.status == "requires_root" and .native.usbipd.version == null))' /tmp/doctor-after.json
    jq -e '.helper.status == "permission_denied" and .helper.peer_verified == true' /tmp/doctor-denied.json
    if [ "$ROLE" = server ]; then
        jq -e '.native.usbip_host.sysfs.status == "available" and .helper.kernel_available == false' /tmp/doctor-after.json
    else
        jq -e '.native.vhci_hcd.sysfs.status == "available" and .helper.kernel_available == true' /tmp/doctor-after.json
    fi
    echo "RUSTUSB_DOCTOR_GATE: PASS role=$ROLE missing_before=true root_peer_verified=true denied_uid=1001 network_verified=false hardware_verified=false"
    echo "RUSTUSB_DOCTOR_USBIPD role=$ROLE status=$(jq -r '.native.usbipd.check.status' /tmp/doctor-after.json)"
}
wait_available() {
    for i in $(seq 1 100); do
        [ "$(cat "/sys/bus/usb/devices/$BUSID/usbip_status")" != 1 ] || return 0
        sleep 0.1
    done
    echo "server export did not return to available"
    return 1
}
if [ "$ROLE" = server ]; then
    sleep 1
    unpriv --json devices > /tmp/devices.json
    [ "$(jq 'length' /tmp/devices.json)" = 1 ]
    [ "$(jq '.[0].export_blockers | length' /tmp/devices.json)" = 0 ]
    ID=$(jq -r '.[0].id' /tmp/devices.json)
    BUSID=$(jq -r '.[0].bus_id' /tmp/devices.json)
    [ "$(cat "/sys/bus/usb/devices/$BUSID/idVendor")" = "$VENDOR" ]
    [ "$(cat "/sys/bus/usb/devices/$BUSID/idProduct")" = "$PRODUCT" ]
    jq -n --arg id "$ID" '{allowed_uids:[1000],export_device_ids:[$id],peers:[],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    export_device() {
        if [ "$SCENARIO" = linux-server ]; then
            /usr/bin/usbip bind --busid="$BUSID"
            if unpriv --json unexport "$ID" > /tmp/denied.log 2>&1; then
                echo "helper unexpectedly took over external export"
                exit 1
            fi
            grep -F 'existing export is not owned by this helper instance' /tmp/denied.log
            [ "$(readlink "/sys/bus/usb/devices/$BUSID/driver" | sed 's#.*/##')" = usbip-host ]
            [ "$(cat "/sys/bus/usb/devices/$BUSID/usbip_status")" = 1 ]
            echo "RUSTUSB_EXTERNAL_EXPORT_GATE: PASS ownership=unmanaged unchanged=true"
        else
            unpriv --json export "$ID"
        fi
    }
    export_device
    /usr/bin/usbipd -4 > /tmp/usbipd.log 2>&1 &
    echo "RUSTUSB_SERVER_READY bus_id=$BUSID cli_uid=$(/usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups id -u)"
    CYCLES='1 2'
    [ "$SCENARIO" != reconnect ] || CYCLES=3
    for cycle in $CYCLES; do
        action=$(nc -l -p 5001)
        [ "$action" = unexport ]
        wait_available
        if [ "$SCENARIO" = linux-server ]; then
            /usr/bin/usbip unbind --busid="$BUSID"
        else
            unpriv --json unexport "$ID"
        fi
        [ "$(readlink "/sys/bus/usb/devices/$BUSID/driver" | sed 's#.*/##')" = usb ]
        for i in $(seq 1 50); do
            LOCAL_DRIVER=$(readlink "/sys/bus/usb/devices/$BUSID:1.0/driver" 2>/dev/null | sed 's#.*/##')
            [ "$LOCAL_DRIVER" != "$INTERFACE_DRIVER" ] || break
            sleep 0.1
        done
        [ "$LOCAL_DRIVER" = "$INTERFACE_DRIVER" ]
        echo "RUSTUSB_SERVER_UNEXPORTED cycle=$cycle local_driver=usb interface_driver=$INTERFACE_DRIVER"
        if [ "$cycle" = 1 ]; then
            action=$(nc -l -p 5001)
            [ "$action" = export ]
            export_device
            echo "RUSTUSB_SERVER_REEXPORTED cycle=2"
        fi
    done
    sleep 3
    echo "RUSTUSB_QEMU_RESULT: PASS role=server"
else
    cat > /etc/rustusb/policy.json <<'JSON'
{"allowed_uids":[1000],"export_device_ids":[],"peers":[{"host":"192.0.2.1","port":3240}],"trusted_network":true}
JSON
    start_helper
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then
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
    snapshot_session() {
        for i in $(seq 1 100); do
            unpriv --json sessions > /tmp/sessions.json
            if [ "$(jq 'length' /tmp/sessions.json)" = 1 ] && [ "$(jq -r '.[0].session.status' /tmp/sessions.json)" = 6 ]; then
                PORT=$(jq -r '.[0].session.port' /tmp/sessions.json)
                LOCAL=$(jq -r '.[0].session.local_bus_id' /tmp/sessions.json)
                [ "$LOCAL" != null ] && return 0
            fi
            sleep 0.1
        done
        echo "unique enumerated session not observed"
        return 1
    }
    check_device() {
        [ "$(cat "/sys/bus/usb/devices/$LOCAL/idVendor")" = "$VENDOR" ]
        [ "$(cat "/sys/bus/usb/devices/$LOCAL/idProduct")" = "$PRODUCT" ]
        for i in $(seq 1 50); do
            DRIVER=$(readlink "/sys/bus/usb/devices/$LOCAL:1.0/driver" 2>/dev/null | sed 's#.*/##')
            [ "$DRIVER" != "$INTERFACE_DRIVER" ] || break
            sleep 0.1
        done
        [ "$DRIVER" = "$INTERFACE_DRIVER" ]
    }
    function_io() {
        check_device
        if [ "$DEVICE" = hid ]; then
            EVENT=
            for i in $(seq 1 50); do
                EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$LOCAL")" -name 'event*' -type d | head -1)
                [ -z "$EVENT" ] || break
                sleep 0.1
            done
            [ -n "$EVENT" ]
            EVENT=$(basename "$EVENT")
            timeout 5 cat "/dev/input/$EVENT" > /tmp/keyboard.bin &
            reader=$!
            echo "RUSTUSB_WAIT_INPUT cycle=$cycle local_bus_id=$LOCAL driver=usbhid"
            wait "$reader" || true
            BYTE_COUNT=$(wc -c < /tmp/keyboard.bin)
            [ "$BYTE_COUNT" -gt 0 ] && [ "$((BYTE_COUNT % 24))" = 0 ]
            if od -An -tu4 -w24 -v /tmp/keyboard.bin | awk '
                NF == 6 && $5 == 131073 && $6 == 1 { found = 1 }
                END { exit !found }
            '; then
                echo "RUSTUSB_HID_INPUT: PASS cycle=$cycle KEY_1_down"
            else
                echo "HID input missing cycle=$cycle"
                return 1
            fi
        else
            TTY=
            for i in $(seq 1 50); do
                TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$LOCAL")" -name 'ttyUSB*' -type d | head -1)
                [ -z "$TTY" ] || break
                sleep 0.1
            done
            [ -n "$TTY" ]
            TTY=/dev/$(basename "$TTY")
            stty -F "$TTY" raw -echo 115200
            printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$cycle" > /tmp/serial-expected.bin
            EXPECTED_BYTES=$(wc -c < /tmp/serial-expected.bin)
            timeout 5 dd if="$TTY" of=/tmp/serial-rx.bin bs=1 count="$EXPECTED_BYTES" 2>/tmp/serial-dd.log &
            reader=$!
            echo "RUSTUSB_WAIT_SERIAL cycle=$cycle local_bus_id=$LOCAL driver=ftdi_sio bytes=$EXPECTED_BYTES"
            wait "$reader" || true
            cmp /tmp/serial-expected.bin /tmp/serial-rx.bin
            printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$cycle" > "$TTY"
            echo "RUSTUSB_SERIAL_INPUT: PASS cycle=$cycle driver=ftdi_sio bytes=$EXPECTED_BYTES sha256=$(sha256sum /tmp/serial-rx.bin | cut -d' ' -f1)"
        fi
    }
    wait_removed() {
        for i in $(seq 1 100); do
            unpriv --json sessions > /tmp/sessions.json
            if [ "$(jq 'length' /tmp/sessions.json)" = 0 ] && [ ! -e "/sys/bus/usb/devices/$LOCAL" ]; then
                echo "RUSTUSB_CLIENT_DETACHED cycle=$cycle kernel_sessions=0 device_removed=true"
                return 0
            fi
            sleep 0.1
        done
        echo "client port or previous USB device remains"
        return 1
    }
    CYCLES='1 2'
    [ "$SCENARIO" != reconnect ] || CYCLES='1 2 3'
    for cycle in $CYCLES; do
        wait_count 1
        BUSID=$(jq -r '.[0].bus_id' /tmp/discover.json)
        if [ "$SCENARIO" = linux-client ] || { [ "$SCENARIO" = reconnect ] && [ "$cycle" = 3 ]; }; then
            /usr/bin/usbip list --remote=192.0.2.1 > /tmp/linux-list.log
            cat /tmp/linux-list.log
            grep -F "$BUSID:" /tmp/linux-list.log
            /usr/bin/usbip attach --remote=192.0.2.1 --busid="$BUSID"
            snapshot_session
            [ "$(jq -r '.[0].managed' /tmp/sessions.json)" = false ]
            /usr/bin/usbip port > /tmp/linux-port.log
            cat /tmp/linux-port.log
            grep -F "192.0.2.1:3240/$BUSID" /tmp/linux-port.log
            if [ "$SCENARIO" = reconnect ]; then
                [ "$PORT" = "$OWNED_PORT" ] && [ "$LOCAL" = "$OWNED_LOCAL" ]
                [ "$(jq -r '.[0].session.remote_device_id' /tmp/sessions.json)" = "$OWNED_REMOTE" ]
                NEW_ADDRESS=$(jq -r '.[0].session.local_device_address' /tmp/sessions.json)
                NEW_GENERATION=$(jq -c '.[0].session.local_device_generation' /tmp/sessions.json)
                [ "$NEW_ADDRESS" != null ] && [ "$NEW_GENERATION" != null ]
                [ "$NEW_ADDRESS" != "$OWNED_ADDRESS" ] || [ "$NEW_GENERATION" != "$OWNED_GENERATION" ]
                if unpriv --json detach "$PORT" > /tmp/denied.log 2>&1; then
                    echo "old helper unexpectedly detached replacement external session"
                    exit 1
                fi
                grep -F 'port identity changed since import' /tmp/denied.log
                snapshot_session
                [ "$PORT" = "$OWNED_PORT" ] && [ "$LOCAL" = "$OWNED_LOCAL" ]
                [ "$(jq -r '.[0].managed' /tmp/sessions.json)" = false ]
                echo "RUSTUSB_REUSED_PORT_GATE: PASS port=$PORT local_bus_id=$LOCAL ownership=external unchanged=true"
            else
                if unpriv --json detach "$PORT" > /tmp/denied.log 2>&1; then
                    echo "helper unexpectedly detached external session"
                    exit 1
                fi
                grep -F 'port is not owned by this helper instance' /tmp/denied.log
                snapshot_session
                [ "$(jq -r '.[0].managed' /tmp/sessions.json)" = false ]
                echo "RUSTUSB_EXTERNAL_SESSION_GATE: PASS cycle=$cycle ownership=unmanaged unchanged=true"
            fi
            echo "RUSTUSB_LINUX_CLIENT: PASS cycle=$cycle list=true port=true"
        else
            unpriv --json attach 192.0.2.1 "$BUSID" > /tmp/attach.json
            cat /tmp/attach.json
            PORT=$(jq -r '.port' /tmp/attach.json)
            LOCAL=$(jq -r '.local_bus_id' /tmp/attach.json)
            [ "$PORT" != null ] && [ "$LOCAL" != null ]
            unpriv --json sessions > /tmp/sessions.json
            [ "$(jq '[.[] | select(.managed == true)] | length' /tmp/sessions.json)" = 1 ]
            if [ "$SCENARIO" = reconnect ] && [ "$cycle" = 2 ]; then
                [ "$PORT" = "$OWNED_PORT" ] && [ "$LOCAL" = "$OWNED_LOCAL" ]
                echo "RUSTUSB_DIRECT_RECONNECT: PASS port=$PORT local_bus_id=$LOCAL"
            fi
        fi
        function_io
        if [ "$SCENARIO" = linux-client ] || [ "$SCENARIO" = reconnect ]; then
            OWNED_PORT=$PORT
            OWNED_LOCAL=$LOCAL
            OWNED_REMOTE=$(jq -r '.[0].session.remote_device_id' /tmp/sessions.json)
            OWNED_ADDRESS=$(jq -r '.[0].session.local_device_address' /tmp/sessions.json)
            OWNED_GENERATION=$(jq -c '.[0].session.local_device_generation' /tmp/sessions.json)
            /usr/bin/usbip detach --port="$PORT"
        else
            unpriv --json detach "$PORT"
        fi
        wait_removed
        if [ "$SCENARIO" = reconnect ] && [ "$cycle" != 3 ]; then
            echo "RUSTUSB_EXTERNAL_RELEASE: PASS cycle=$cycle port=$PORT"
            continue
        fi
        printf unexport | nc -w 1 192.0.2.1 5001
        wait_count 0
        if [ "$cycle" = 1 ]; then
            printf export | nc -w 1 192.0.2.1 5001
        fi
    done
    echo "RUSTUSB_QEMU_RESULT: PASS role=client"
fi
