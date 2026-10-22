#!/bin/sh
# Disposable two-guest userspace virtual USB lab, never a host administration script.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo 'Refusing to run outside the disposable QEMU guest init' >&2
    exit 2
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in
    *' rustusb.role=server '*|*' rustusb.role=client '*) ;;
    *) echo 'Invalid userspace guest role' >&2; exit 2 ;;
esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/virtual /var/run/vhci_hcd /etc/rustusb
chmod 755 /run/rustusb /etc/rustusb
mount -t devpts devpts /dev/pts
ROLE=unknown
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in rustusb.role=*) ROLE=${parameter#*=} ;; esac
done
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_USERSPACE_RESULT: FAIL role=$ROLE exit=$status"
        for file in /tmp/helper.log /tmp/server.log /tmp/server.events /tmp/discover.json /tmp/attach-hid.json /tmp/attach-cdc.json /tmp/sessions.json /tmp/doctor.json /tmp/denied.log /tmp/probe.log; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
        if [ "$ROLE" = client ] && [ -S /run/rustusb/control.sock ]; then
            echo 'DIAGNOSTIC authoritative sessions after failure'
            unpriv --json sessions || true
        fi
        dmesg | tail -70
    fi
    sync
    poweroff -f
}
trap cleanup EXIT
echo "RUSTUSB_USERSPACE_BOOT role=$ROLE kernel=$(uname -r)"
[ ! -f /modules/e1000.ko ] || insmod /modules/e1000.ko
ifconfig lo 127.0.0.1 up
if [ "$ROLE" = server ]; then
    ifconfig eth0 192.0.2.1 netmask 255.255.255.0 up
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    echo 'RUSTUSB_USERSPACE_SERVER_MODULES: PASS usbip_host=false vhci_hcd=false'
else
    for name in usb-common usbcore evdev hid hid-generic usbhid usbip-core vhci-hcd cdc-acm; do
        [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
    done
    ifconfig eth0 192.0.2.2 netmask 255.255.255.0 up
fi
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = 0 ]
echo "RUSTUSB_USERSPACE_ISOLATION role=$ROLE ip_forward=0"
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
probe() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/userspace-wire-probe 192.0.2.1:3240 "$@"; }
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
if [ "$ROLE" = server ]; then
    chown 1000:1000 /run/virtual
    chmod 700 /run/virtual
    mkfifo -m 600 /run/virtual/control
    chown 1000:1000 /run/virtual/control
    # The root test coordinator owns no application state. It merely keeps the
    # FIFO alive; both the server and each control writer execute as UID1000.
    exec 3<> /run/virtual/control
    (
        exec 3<&-
        exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb virtual-server \
            --listen 192.0.2.1:3240 --allow-insecure-test-network --allow-peer 192.0.2.2 --control-stdin \
            < /run/virtual/control > /tmp/server.events 2> /tmp/server.log
    ) &
    SERVER_PID=$!
    for attempt in $(seq 1 100); do
        if jq -se 'any(.[]; .event == "ready")' /tmp/server.events > /dev/null 2>&1; then break; fi
        kill -0 "$SERVER_PID"
        sleep 0.05
    done
    jq -se 'any(.[]; .schema_version == 1 and .event == "ready" and .address == "192.0.2.1:3240" and (.devices | length) == 2)' /tmp/server.events > /dev/null
    SERVER_START=$(awk '{print $22}' "/proc/$SERVER_PID/stat")
    server_identity() {
        kill -0 "$SERVER_PID"
        [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$SERVER_PID/status")" = 1000:1000:1000:1000 ]
        [ "$(awk '{print $22}' "/proc/$SERVER_PID/stat")" = "$SERVER_START" ]
        [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    }
    control() {
        printf '%s\n' "$1" | /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox tee /run/virtual/control > /dev/null
        for attempt in $(seq 1 100); do
            if jq -se --arg id "$2" 'any(.[]; .id == $id and (.event == "ack" or .event == "snapshot"))' /tmp/server.events > /dev/null 2>&1; then
                jq -se --arg id "$2" 'all(.[] | select(.id == $id); .schema_version == 1 and (.event == "snapshot" or (.event == "ack" and .ok == true)))' /tmp/server.events > /dev/null
                return 0
            fi
            kill -0 "$SERVER_PID"
            sleep 0.05
        done
        echo "server did not acknowledge control id=$2"
        return 1
    }
    snapshot() {
        control "{\"id\":\"$1\",\"op\":\"snapshot\"}" "$1"
        jq -s --arg id "$1" '.[] | select(.event == "snapshot" and .id == $id) | .snapshot' /tmp/server.events > "/tmp/$1.json"
        jq -e '.schema_version == 1 and (.devices | length) == 2 and all(.devices[]; .exported == true)' "/tmp/$1.json" > /dev/null
        server_identity
    }
    server_identity
    echo 'RUSTUSB_USERSPACE_SERVER_UID: PASS uid=1000 app_root=false'
    receive_control protocol-passed
    snapshot baseline
    jq -e 'all(.devices[]; .leased == false and .pending_urbs == 0 and .configuration == 0 and .hid_queued_reports == 0 and .cdc_queued_bytes == 0) and (.devices[] | select(.profile == "hid_keyboard") | .counters.unlink_cancelled) >= 1 and (.devices[] | select(.profile == "cdc_acm") | .counters.cdc_out_bytes == 16 and .counters.cdc_in_bytes == 16)' /tmp/baseline.json > /dev/null
    echo 'RUSTUSB_USERSPACE_MALFORMED_SURVIVAL: PASS same_pid=true uid=1000 lease=0 pending=0'
    send_control 192.0.2.2 protocol-checked
    for cycle in 1 2; do
        receive_control "cycle-$cycle-ready"
        snapshot "held-$cycle"
        jq -e 'all(.devices[]; .leased == true and .configuration == 1) and (.devices[] | select(.profile == "cdc_acm") | .line_coding.rate == 115200 and .line_coding.data_bits == 8)' "/tmp/held-$cycle.json" > /dev/null
        key=$((29 + cycle))
        control "{\"id\":\"key-$cycle\",\"op\":\"hid_key\",\"key\":$key}" "key-$cycle"
        printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$cycle" > /tmp/inject.bin
        [ "$(wc -c < /tmp/inject.bin)" = 35 ]
        hex=$(od -An -tx1 -v /tmp/inject.bin | tr -d ' \n')
        control "{\"id\":\"emit-$cycle\",\"op\":\"cdc_emit\",\"hex\":\"$hex\"}" "emit-$cycle"
        receive_control "cycle-$cycle-io-complete"
        snapshot "io-$cycle"
        jq -e --argjson n "$cycle" --slurpfile base /tmp/baseline.json '
            all(.devices[]; .leased == true and .configuration == 1 and .hid_queued_reports == 0 and .cdc_queued_bytes == 0) and
            (.devices[] | select(.profile == "hid_keyboard") | .counters.hid_taps) == $n and
            (.devices[] | select(.profile == "hid_keyboard") | .counters.interrupt) >= (2 * $n) and
            (.devices[] | select(.profile == "cdc_acm") | .counters.cdc_out_bytes) == (16 + 35 * $n) and
            (.devices[] | select(.profile == "cdc_acm") | .counters.cdc_in_bytes) == (16 + 70 * $n) and
            (.devices[] | select(.profile == "cdc_acm") | .counters.cdc_injected_bytes) == (35 * $n) and
            all(.devices[]; .counters.control > 0) and
            (.devices[] | select(.profile == "cdc_acm") | .counters.bulk_in > 0 and .counters.bulk_out > 0)
        ' "/tmp/io-$cycle.json" > /dev/null
        echo "RUSTUSB_USERSPACE_COUNTERS: PASS cycle=$cycle hid_taps=$cycle cdc_out=$((35 * cycle)) cdc_in=$((70 * cycle)) cdc_injected=$((35 * cycle)) probe_bytes_excluded=16"
        send_control 192.0.2.2 "cycle-$cycle-detach"
        receive_control "cycle-$cycle-released"
        snapshot "released-$cycle"
        jq -e 'all(.devices[]; .leased == false and .pending_urbs == 0 and .configuration == 0 and .hid_queued_reports == 0 and .cdc_queued_bytes == 0)' "/tmp/released-$cycle.json" > /dev/null
        echo "RUSTUSB_USERSPACE_SERVER_RELEASED: PASS cycle=$cycle lease=0 pending=0 queues=0"
        send_control 192.0.2.2 "cycle-$cycle-clean"
        receive_control "cycle-$cycle-checked"
    done
    server_identity
    control '{"id":"stop","op":"shutdown"}' stop
    wait "$SERVER_PID"
    exec 3<&-
    jq -se 'any(.[]; .event == "stopped" and .snapshot.schema_version == 1 and all(.snapshot.devices[]; .leased == false and .pending_urbs == 0)) and all(.[]; .event != "error")' /tmp/server.events > /dev/null
    echo 'RUSTUSB_USERSPACE_STOPPED: PASS exit=0 lease=0 pending=0 same_pid=true'
    echo 'RUSTUSB_USERSPACE_EVENTS_BEGIN'
    cat /tmp/server.events
    echo 'RUSTUSB_USERSPACE_EVENTS_END'
    [ ! -s /tmp/server.log ] || { echo 'RUSTUSB_USERSPACE_STDERR_BEGIN'; cat /tmp/server.log; echo 'RUSTUSB_USERSPACE_STDERR_END'; }
    send_control 192.0.2.2 finished
else
    jq -n '{allowed_uids:[1000],export_device_ids:[],peers:[{host:"192.0.2.1",port:3240}],trusted_network:true}' > /etc/rustusb/policy.json
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &
    for attempt in $(seq 1 100); do
        [ ! -S /run/rustusb/control.sock ] || break
        sleep 0.05
    done
    [ -S /run/rustusb/control.sock ]
    unpriv --json doctor > /tmp/doctor.json
    jq -e '.schema_version == 1 and .helper.status == "available" and .helper.peer_verified == true and .helper.kernel_available == true and .native.vhci_hcd.sysfs.status == "available" and .network_security_verified == false and .hardware_test_performed == false and .built_in_encryption == false' /tmp/doctor.json > /dev/null
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then
        echo 'unapproved UID unexpectedly accepted'
        exit 1
    fi
    grep -F 'caller UID is not approved by the administrator' /tmp/denied.log
    echo 'RUSTUSB_USERSPACE_UID_GATE: PASS denied_uid=1001 allowed_uid=1000'
    discover_two() {
        for attempt in $(seq 1 100); do
            if unpriv --json --timeout 2 discover 192.0.2.1 > /tmp/discover.json 2>/dev/null && jq -e 'length == 2 and (map(.bus_id) | sort) == ["1-1","1-2"]' /tmp/discover.json > /dev/null; then return 0; fi
            sleep 0.05
        done
        return 1
    }
    discover_two
    probe protocol > /tmp/probe.log
    cat /tmp/probe.log
    send_control 192.0.2.1 protocol-passed
    receive_control protocol-checked
    for cycle in 1 2; do
        unpriv --json attach 192.0.2.1 1-1 > /tmp/attach-hid.json
        unpriv --json attach 192.0.2.1 1-2 > /tmp/attach-cdc.json
        cat /tmp/attach-hid.json /tmp/attach-cdc.json
        HID_PORT=$(jq -r '.port' /tmp/attach-hid.json)
        HID_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-hid.json)
        CDC_PORT=$(jq -r '.port' /tmp/attach-cdc.json)
        CDC_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-cdc.json)
        [ "$HID_LOCAL" != null ] && [ "$CDC_LOCAL" != null ] && [ "$HID_PORT" != "$CDC_PORT" ]
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 2 and all(.[]; .managed == true and .session.status == 6)' /tmp/sessions.json > /dev/null
        [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idVendor")" = 1234 ]
        [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idProduct")" = 0001 ]
        [ "$(cat "/sys/bus/usb/devices/$CDC_LOCAL/idVendor")" = 1234 ]
        [ "$(cat "/sys/bus/usb/devices/$CDC_LOCAL/idProduct")" = 0002 ]
        # Linux v6.8 deliberately formats bNumInterfaces with "%2d\n".
        # Compare its numeric value, including the leading space for two.
        [ "$(cat "/sys/bus/usb/devices/$CDC_LOCAL/bNumInterfaces")" -eq 2 ]
        echo "RUSTUSB_USERSPACE_IDENTITIES: PASS cycle=$cycle hid=1234:0001 cdc=1234:0002 cdc_interfaces=2"
        EVENT=
        TTY=
        for attempt in $(seq 1 100); do
            EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
            TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$CDC_LOCAL")" -name 'ttyACM*' -type d | head -1)
            [ -z "$EVENT" ] || [ -z "$TTY" ] || break
            sleep 0.05
        done
        [ -n "$EVENT" ] && [ -n "$TTY" ]
        [ "$(readlink "/sys/bus/usb/devices/$HID_LOCAL:1.0/driver" | sed 's#.*/##')" = usbhid ]
        [ "$(readlink "/sys/bus/usb/devices/$CDC_LOCAL:1.0/driver" | sed 's#.*/##')" = cdc_acm ]
        [ "$(cat "/sys/bus/usb/devices/$CDC_LOCAL:1.0/bInterfaceClass")" = 02 ]
        [ "$(cat "/sys/bus/usb/devices/$CDC_LOCAL:1.1/bInterfaceClass")" = 0a ]
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
        probe busy
        echo "RUSTUSB_USERSPACE_ATTACHED: PASS cycle=$cycle sessions=2 hid_driver=usbhid cdc_driver=cdc_acm cdc_interfaces=2"
        send_control 192.0.2.1 "cycle-$cycle-ready"
        wait "$SERIAL_READER"
        printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$cycle" > /tmp/expected.bin
        [ "$(wc -c < /tmp/rx.bin)" = 35 ]
        cmp /tmp/rx.bin /tmp/expected.bin
        : > /tmp/echo.bin
        timeout 8 dd bs=1 count=35 <&4 > /tmp/echo.bin 2> /tmp/dd.log &
        SERIAL_READER=$!
        printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$cycle" > /tmp/tx.bin
        [ "$(wc -c < /tmp/tx.bin)" = 35 ]
        cat /tmp/tx.bin >&4
        wait "$SERIAL_READER"
        [ "$(wc -c < /tmp/echo.bin)" = 35 ]
        cmp /tmp/echo.bin /tmp/tx.bin
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
        jq -e --slurpfile hid /tmp/attach-hid.json --slurpfile cdc /tmp/attach-cdc.json 'length == 2 and all(.[]; .managed == true) and any(.[]; .session == $hid[0]) and any(.[]; .session == $cdc[0])' /tmp/sessions.json > /dev/null
        echo "RUSTUSB_USERSPACE_HID: PASS cycle=$cycle key_code=$key_code down=true up=true same_receipt=true"
        echo "RUSTUSB_USERSPACE_CDC: PASS cycle=$cycle injected_in=35 out=35 echoed_in=35 exact_bytes=true tty_echo=false same_receipt=true"
        send_control 192.0.2.1 "cycle-$cycle-io-complete"
        receive_control "cycle-$cycle-detach"
        kill "$INPUT_READER"
        wait "$INPUT_READER" || true
        exec 4<&-
        unpriv --json detach "$HID_PORT"
        unpriv --json detach "$CDC_PORT"
        unpriv --json sessions > /tmp/sessions.json
        jq -e 'length == 0' /tmp/sessions.json > /dev/null
        [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$CDC_LOCAL" ]
        echo "RUSTUSB_USERSPACE_CLIENT_RELEASED: PASS cycle=$cycle kernel_sessions=0 both_devices_removed=true"
        send_control 192.0.2.1 "cycle-$cycle-released"
        receive_control "cycle-$cycle-clean"
        discover_two
        probe health
        echo "RUSTUSB_USERSPACE_REDISCOVERED: PASS cycle=$cycle available_profiles=2"
        send_control 192.0.2.1 "cycle-$cycle-checked"
    done
    receive_control finished
fi
echo "RUSTUSB_USERSPACE_RESULT: PASS role=$ROLE"
