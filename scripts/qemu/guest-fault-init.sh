#!/bin/sh
# Disposable guest only. Never source or execute on the host.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo "Refusing to run outside the disposable QEMU guest init" >&2
    exit 2
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in
    *' rustusb.fault=transport-abort '*) ;;
    *) echo "Missing transport-abort guest marker" >&2; exit 2 ;;
esac
ROLE=unknown
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in rustusb.role=*) ROLE=${parameter#*=} ;; esac
done
case "$ROLE" in server|client) ;; *) exit 2 ;; esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /var/run/vhci_hcd /etc/rustusb
chmod 755 /run/rustusb /etc/rustusb
mount -t devpts devpts /dev/pts
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
snapshot() {
    label=$1
    echo "RUSTUSB_FAULT_SNAPSHOT_BEGIN role=$ROLE label=$label uptime=$(cut -d' ' -f1 /proc/uptime)"
    /usr/bin/ss -Htn state established || true
    if [ "$ROLE" = client ]; then
        for attribute in /sys/devices/platform/vhci_hcd.*/status*; do
            [ ! -f "$attribute" ] || { echo "VHCI $attribute"; cat "$attribute"; }
        done
        unpriv --json sessions || true
    elif [ -n "${BUSID:-}" ] && [ -f "/sys/bus/usb/devices/$BUSID/usbip_status" ]; then
        echo "usbip_status=$(cat "/sys/bus/usb/devices/$BUSID/usbip_status")"
    fi
    echo "RUSTUSB_FAULT_SNAPSHOT_END role=$ROLE label=$label"
}
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_FAULT_RESULT: FAIL role=$ROLE exit=$status"
        snapshot failed || true
        for file in /tmp/helper.log /tmp/usbipd.log /tmp/attach.json /tmp/sessions.json /tmp/release-sessions.json /tmp/release-errors.log /tmp/tcp-kill.txt; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
    fi
    echo "RUSTUSB_FAULT_DMESG_BEGIN role=$ROLE"
    dmesg | tail -100
    echo "RUSTUSB_FAULT_DMESG_END role=$ROLE"
    sync
    poweroff -f
}
trap cleanup EXIT
echo "RUSTUSB_FAULT_BOOT role=$ROLE kernel=$(uname -r) fault=explicit-tcp-abort"
for name in usb-common usbcore e1000 xhci-hcd xhci-pci-renesas xhci-pci evdev hid hid-generic usbhid usbip-core inet_diag tcp_diag; do
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
start_helper() {
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &
    HELPER_PID=$!
    for i in $(seq 1 50); do
        [ ! -S /run/rustusb/control.sock ] || break
        sleep 0.1
    done
    [ -S /run/rustusb/control.sock ]
    unpriv --json status
    echo "RUSTUSB_FAULT_HELPER role=$ROLE cli_uid=1000 helper_alive=true"
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
    /usr/bin/usbipd -4 > /tmp/usbipd.log 2>&1 &
    USBIPD_PID=$!
    echo "RUSTUSB_FAULT_SERVER_READY bus_id=$BUSID"
    for fault in 1 2; do
        action=$(timeout 40 nc -l -p 5001)
        [ "$action" = "abort$fault" ]
        [ "$(cat "/sys/bus/usb/devices/$BUSID/usbip_status")" = 2 ]
        kill -0 "$USBIPD_PID"
        snapshot "before-abort-$fault"
        /usr/bin/ss -Htn state established src 192.0.2.1 and sport = :3240 and dst 192.0.2.2 > /tmp/tcp-before.txt
        [ "$(awk 'NF {n++} END {print n+0}' /tmp/tcp-before.txt)" = 1 ]
        LOCAL_TUPLE=$(awk 'NF {print $(NF-1)}' /tmp/tcp-before.txt)
        REMOTE_TUPLE=$(awk 'NF {print $NF}' /tmp/tcp-before.txt)
        [ "$LOCAL_TUPLE" = 192.0.2.1:3240 ]
        case "$REMOTE_TUPLE" in 192.0.2.2:*) ;; *) exit 1 ;; esac
        REMOTE_PORT=${REMOTE_TUPLE##*:}
        case "$REMOTE_PORT" in ''|*[!0-9]*) exit 1 ;; esac
        [ "$REMOTE_PORT" -ge 1 ] && [ "$REMOTE_PORT" -le 65535 ]
        echo "RUSTUSB_TCP_ABORT_TARGET fault=$fault local=$LOCAL_TUPLE remote=$REMOTE_TUPLE unique_established=1 mechanism=SOCK_DESTROY"
        # Only this isolated guest's exact established USB/IP data tuple is aborted.
        # The listener, control socket, usbipd process, and host network are untouched.
        /usr/bin/ss -K -Htn state established src 192.0.2.1 and sport = :3240 and dst 192.0.2.2 and dport = ":$REMOTE_PORT" > /tmp/tcp-kill.txt 2>&1
        echo "RUSTUSB_TCP_DESTROY_OUTPUT_BEGIN fault=$fault"
        cat /tmp/tcp-kill.txt
        echo "RUSTUSB_TCP_DESTROY_OUTPUT_END fault=$fault"
        export BUSID
        timeout 15 /bin/sh -c 'while [ "$(cat "/sys/bus/usb/devices/$BUSID/usbip_status")" != 1 ]; do sleep 0.1; done'
        /usr/bin/ss -Htn state established src 192.0.2.1 and sport = :3240 and dst 192.0.2.2 and dport = ":$REMOTE_PORT" > /tmp/tcp-after.txt
        [ ! -s /tmp/tcp-after.txt ]
        /usr/bin/ss -Hltn sport = :3240 > /tmp/tcp-listener.txt
        [ "$(awk 'NF {n++} END {print n+0}' /tmp/tcp-listener.txt)" = 1 ]
        kill -0 "$USBIPD_PID"
        kill -0 "$HELPER_PID"
        snapshot "after-abort-$fault"
        echo "RUSTUSB_TCP_ABORT: PASS fault=$fault remote_port=$REMOTE_PORT before=1 after=0 usbip_status=1 usbipd_alive=true helper_alive=true"
        # Do not let the client reattach before this server-side AVAILABLE
        # observation; otherwise it can legitimately disappear between polls.
        printf 'released%s' "$fault" | timeout 15 nc -l -p 5002
    done
    action=$(timeout 40 nc -l -p 5001)
    [ "$action" = unexport ]
    export BUSID
    timeout 15 /bin/sh -c 'while [ "$(cat "/sys/bus/usb/devices/$BUSID/usbip_status")" != 1 ]; do sleep 0.1; done'
    unpriv --json unexport "$ID"
    [ "$(readlink "/sys/bus/usb/devices/$BUSID/driver" | sed 's#.*/##')" = usb ]
    export BUSID
    timeout 15 /bin/sh -c 'while [ "$(readlink "/sys/bus/usb/devices/$BUSID:1.0/driver" 2>/dev/null | sed "s#.*/##")" != usbhid ]; do sleep 0.1; done'
    echo "RUSTUSB_FAULT_SERVER_RESTORED: PASS local_driver=usb interface_driver=usbhid"
    echo "RUSTUSB_FAULT_RESULT: PASS role=server"
else
    cat > /etc/rustusb/policy.json <<'JSON'
{"allowed_uids":[1000],"export_device_ids":[],"peers":[{"host":"192.0.2.1","port":3240}],"trusted_network":true}
JSON
    start_helper
    cat > /tmp/wait-release.sh <<'SH'
set -eu
while :; do
    if /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json sessions > /tmp/release-sessions.json 2>> /tmp/release-errors.log && [ "$(jq 'length' /tmp/release-sessions.json)" = 0 ] && [ ! -e "/sys/bus/usb/devices/$CHECK_LOCAL" ] && awk -v port="$CHECK_PORT" '
        $1 ~ /^(hs|ss)$/ && $2 == port {seen++; if ($3 == 4) empty++}
        END {exit !(seen == 1 && empty == 1)}
    ' /sys/devices/platform/vhci_hcd.*/status*; then
        exit 0
    fi
    sleep 0.1
done
SH
    wait_available() {
        for i in $(seq 1 60); do
            if unpriv --json --timeout 1 discover 192.0.2.1 > /tmp/discover.json 2>/dev/null && [ "$(jq 'length' /tmp/discover.json)" = 1 ]; then
                return 0
            fi
            sleep 0.1
        done
        return 1
    }
    wait_released() {
        export CHECK_LOCAL=$LOCAL CHECK_PORT=$PORT
        timeout 15 /bin/sh /tmp/wait-release.sh
        snapshot "released-cycle-$cycle"
        echo "RUSTUSB_FAULT_KERNEL_RELEASE: PASS cycle=$cycle port=$PORT kernel_state=NULL kernel_sessions=0 old_device_removed=true"
    }
    wait_server_release() {
        for i in $(seq 1 50); do
            ready=$(nc -w 1 192.0.2.1 5002 2>/dev/null) || ready=
            [ "$ready" != "released$cycle" ] || return 0
            sleep 0.1
        done
        echo "server release acknowledgement missing for cycle $cycle"
        return 1
    }
    for cycle in 1 2 3; do
        wait_available
        BUSID=$(jq -r '.[0].bus_id' /tmp/discover.json)
        unpriv --json attach 192.0.2.1 "$BUSID" > /tmp/attach.json
        jq -e '.status == 6 and .local_device_address != null and .local_device_generation != null' /tmp/attach.json
        echo "RUSTUSB_FAULT_RECEIPT_BEGIN cycle=$cycle"
        cat /tmp/attach.json
        echo "RUSTUSB_FAULT_RECEIPT_END cycle=$cycle"
        PORT=$(jq -r '.port' /tmp/attach.json)
        LOCAL=$(jq -r '.local_bus_id' /tmp/attach.json)
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 1 and .[0].managed == true and .[0].session.status == 6' /tmp/sessions.json
        if [ "$cycle" = 3 ]; then
            [ "$(jq -c '.local_device_generation' /tmp/attach.json)" != "$PREVIOUS_GENERATION" ]
            echo "RUSTUSB_FAULT_DIRECT_RECONNECT: PASS cycle=3 managed=true new_generation=true explicit_detach=false"
        fi
        snapshot "attached-cycle-$cycle"
        [ "$(cat "/sys/bus/usb/devices/$LOCAL/idVendor")" = 0627 ]
        [ "$(cat "/sys/bus/usb/devices/$LOCAL/idProduct")" = 0001 ]
        for i in $(seq 1 50); do
            DRIVER=$(readlink "/sys/bus/usb/devices/$LOCAL:1.0/driver" 2>/dev/null | sed 's#.*/##')
            [ "$DRIVER" != usbhid ] || break
            sleep 0.1
        done
        [ "$DRIVER" = usbhid ]
        EVENT=
        for i in $(seq 1 50); do
            EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$LOCAL")" -name 'event*' -type d | head -1)
            [ -z "$EVENT" ] || break
            sleep 0.1
        done
        [ -n "$EVENT" ]
        : > /tmp/keyboard.bin
        # Subscribe before announcing readiness, so QMP cannot race the
        # background child's open and lose an otherwise valid key event.
        exec 3< "/dev/input/$(basename "$EVENT")"
        timeout 5 cat <&3 > /tmp/keyboard.bin &
        reader=$!
        exec 3<&-
        echo "RUSTUSB_FAULT_WAIT_INPUT cycle=$cycle local_bus_id=$LOCAL driver=usbhid"
        wait "$reader" || true
        BYTE_COUNT=$(wc -c < /tmp/keyboard.bin)
        [ "$BYTE_COUNT" -gt 0 ] && [ "$((BYTE_COUNT % 24))" = 0 ]
        od -An -tu4 -w24 -v /tmp/keyboard.bin | awk 'NF == 6 && $5 == 131073 && $6 == 1 {found=1} END {exit !found}'
        echo "RUSTUSB_FAULT_HID_INPUT: PASS cycle=$cycle KEY_1_down"
        if [ "$cycle" -lt 3 ]; then
            PREVIOUS_GENERATION=$(jq -c '.local_device_generation' /tmp/attach.json)
            printf 'abort%s' "$cycle" | nc -w 1 192.0.2.1 5001
            wait_released
            wait_server_release
            if [ "$cycle" = 1 ]; then
                unpriv --json detach "$PORT"
                echo "RUSTUSB_FAULT_OWNED_CLEANUP: PASS cycle=1 already_kernel_released=true cli_uid=1000"
            fi
        else
            unpriv --json detach "$PORT"
            wait_released
            printf unexport | nc -w 1 192.0.2.1 5001
        fi
    done
    echo "RUSTUSB_FAULT_RESULT: PASS role=client"
fi
