#!/bin/sh
# Disposable QEMU PID1 only. No host USB/module/network operations.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo 'Refusing to run outside the disposable QEMU guest init' >&2
    exit 2
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in
    *' rustusb.role=server '*|*' rustusb.role=client '*) ;;
    *) echo 'Invalid composite guest role' >&2; exit 2 ;;
esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/composite /etc/rustusb /var/run/vhci_hcd
chmod 755 /run/rustusb /etc/rustusb
mount -t devpts devpts /dev/pts
ROLE=unknown
STAGE=boot
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in rustusb.role=*) ROLE=${parameter#*=} ;; esac
done
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_COMPOSITE_DIAGNOSTIC role=$ROLE stage=$STAGE exit=$status"
        ifconfig -a
        cat /proc/net/dev /proc/net/arp /proc/net/route
        for interface in /sys/class/net/*; do
            echo "NETWORK_STATE interface=$(basename "$interface") operstate=$(cat "$interface/operstate") carrier=$(cat "$interface/carrier" 2>/dev/null || true)"
        done
        if [ "$ROLE" = server ] && [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
            snapshot failure-observation || true
        fi
        for file in /tmp/*.log /tmp/*.json /tmp/server.events /tmp/peer.err; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
        if [ "$ROLE" = client ] && [ -S /run/rustusb/control.sock ]; then unpriv --json sessions || true; fi
        dmesg | tail -70
        echo "RUSTUSB_COMPOSITE_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
    fi
    sync
    poweroff -f
}
trap cleanup EXIT
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
probe() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/composite-wire-probe "$@"; }
echo "RUSTUSB_COMPOSITE_BOOT role=$ROLE kernel=$(uname -r)"
for module in usb-common usbcore e1000 mii usbnet cdc_ether; do
    [ ! -f "/modules/$module.ko" ] || insmod "/modules/$module.ko"
done
ifconfig lo 127.0.0.1 up
if [ "$ROLE" = server ]; then
    for module in xhci-hcd xhci-pci-renesas xhci-pci; do [ ! -f "/modules/$module.ko" ] || insmod "/modules/$module.ko"; done
    ifconfig eth0 192.0.2.1 netmask 255.255.255.0 up
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    PEER_NET=
    for interface in /sys/class/net/*; do
        [ "$(cat "$interface/address")" != 52:54:00:cc:00:01 ] || PEER_NET=$(basename "$interface")
    done
    [ -n "$PEER_NET" ] && [ "$PEER_NET" != eth0 ]
    ifconfig "$PEER_NET" down
    echo "RUSTUSB_COMPOSITE_PEER_QUIET: PASS interface=$PEER_NET down=true raw_probe_isolated=true"
else
    for module in usbip-core vhci-hcd; do [ ! -f "/modules/$module.ko" ] || insmod "/modules/$module.ko"; done
    ifconfig eth0 192.0.2.2 netmask 255.255.255.0 up
fi
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = 0 ]
echo "RUSTUSB_COMPOSITE_ISOLATION: PASS role=$ROLE ip_forward=0"
send_control() {
    for attempt in $(seq 1 100); do
        if printf '%s' "$2" | nc -w 1 "$1" 5001; then return 0; fi
        sleep 0.1
    done
    echo "control delivery failed: $2"
    return 1
}
receive_control() {
    MESSAGE=$(timeout 60 nc -l -p 5001)
    [ "$MESSAGE" = "$1" ] || { echo "unexpected control message: $MESSAGE expected=$1"; return 1; }
}
identity() {
    kill -0 "$1"
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$1/status")" = 1000:1000:1000:1000 ]
    for cap in CapEff CapPrm CapAmb; do
        [ "$(awk -v field="$cap:" '$1 == field {print $2}' "/proc/$1/status")" = 0000000000000000 ]
    done
}
start_helper() {
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper > /tmp/helper.log 2>&1 &
    for attempt in $(seq 1 100); do [ ! -S /run/rustusb/control.sock ] || break; sleep 0.05; done
    [ -S /run/rustusb/control.sock ]
    unpriv --json doctor > /tmp/doctor.json
    jq -e '.schema_version == 1 and .helper.status == "available" and .helper.peer_verified == true and .network_security_verified == false and .hardware_test_performed == false and .built_in_encryption == false' /tmp/doctor.json > /dev/null
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then exit 1; fi
    grep -F 'caller UID is not approved by the administrator' /tmp/denied.log
    echo "RUSTUSB_COMPOSITE_UID_GATE: PASS role=$ROLE allowed_uid=1000 denied_uid=1001"
}
if [ "$ROLE" = server ]; then
    STAGE=original-composite-state
    for attempt in $(seq 1 150); do
        unpriv --json devices > /tmp/devices.json
        [ "$(jq 'length' /tmp/devices.json)" != 1 ] || break
        sleep 0.05
    done
    STAGE=original-scanner-safety
    jq -e 'length == 1 and .[0].vendor_id == 1317 and .[0].product_id == 42146 and (.[0].export_blockers | length) == 0' /tmp/devices.json > /dev/null
    DEVICE_ID=$(jq -r '.[0].id' /tmp/devices.json)
    BUS=$(jq -r '.[0].bus_id' /tmp/devices.json)
    STAGE=original-model-attributes
    SPEED=$(cat "/sys/bus/usb/devices/$BUS/speed")
    INTERFACES=$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS/bNumInterfaces")
    echo "RUSTUSB_COMPOSITE_OBSERVED bus=$BUS speed=$SPEED bNumInterfaces=$INTERFACES"
    [ "$SPEED" = 12 ]
    [ "$INTERFACES" = 2 ]
    STAGE=original-device-generation
    GENERATION=$(stat -L -c '%d:%i' "/sys/bus/usb/devices/$BUS")
    original_state() {
        for attempt in $(seq 1 150); do
            [ "$(stat -L -c '%d:%i' "/sys/bus/usb/devices/$BUS")" = "$GENERATION" ]
            config=$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS/bConfigurationValue")
            driver0=$(readlink "/sys/bus/usb/devices/$BUS:1.0/driver" 2>/dev/null | sed 's#.*/##')
            driver1=$(readlink "/sys/bus/usb/devices/$BUS:1.1/driver" 2>/dev/null | sed 's#.*/##')
            alt0=$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS:1.0/bAlternateSetting" 2>/dev/null || true)
            alt1=$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS:1.1/bAlternateSetting" 2>/dev/null || true)
            [ "$attempt" != 1 ] || echo "RUSTUSB_COMPOSITE_OBSERVED config=$config driver0=$driver0 driver1=$driver1 alt0=$alt0 alt1=$alt1"
            if [ "$config:$driver0:$driver1:$alt0:$alt1" = 1:cdc_ether:cdc_ether:0:1 ]; then return 0; fi
            sleep 0.05
        done
        echo "original state not restored config=$config driver0=$driver0 driver1=$driver1 alt0=$alt0 alt1=$alt1"
        return 1
    }
    STAGE=original-drivers-and-alternates
    original_state
    STAGE=original-unconfigured-network
    ORIGINAL_NET=
    for path in /sys/bus/usb/devices/"$BUS":1.*/net/*; do [ ! -d "$path" ] || ORIGINAL_NET=$(basename "$path"); done
    echo "RUSTUSB_COMPOSITE_OBSERVED original_net=$ORIGINAL_NET peer_net=$PEER_NET"
    [ -n "$ORIGINAL_NET" ] && [ "$ORIGINAL_NET" != "$PEER_NET" ]
    [ -z "$(ifconfig "$ORIGINAL_NET" | sed -n 's/.*inet addr:\([^ ]*\).*/\1/p')" ]
    echo 'RUSTUSB_COMPOSITE_ROUTE_ISOLATION: PASS server_usb_ipv4=false forwarding=false'
    STAGE=original-usbfs-node
    NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$BUS/busnum")" "$(cat "/sys/bus/usb/devices/$BUS/devnum")")
    echo "RUSTUSB_COMPOSITE_OBSERVED node=$NODE metadata=$(stat -c '%F %u:%g %a' "$NODE")"
    [ -c "$NODE" ] && [ "$(stat -c '%u' "$NODE")" = 0 ]
    chmod 600 "$NODE"
    for uid in 1000 1001; do
        STAGE="original-direct-open-$uid"
        if /usr/bin/setpriv --reuid="$uid" --regid="$uid" --clear-groups /bin/busybox sh -c 'exec 3<> "$1"' sh "$NODE" > /tmp/direct-open.log 2>&1; then exit 1; fi
    done
    echo "RUSTUSB_COMPOSITE_ORIGINAL: PASS config=1 interfaces=2 driver0=cdc_ether driver1=cdc_ether alt0=0 alt1=1 speed=12 node=root0600"
    jq -n --arg id "$DEVICE_ID" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[$id],peers:[],trusted_network:true}' > /etc/rustusb/policy.json
    STAGE=helper-capability
    start_helper
    /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/composite-wire-probe helper-denied "$DEVICE_ID" > /tmp/denied-open.json
    jq -e 'keys == ["data","error","ok"] and .ok == false and .data == null and (.error | contains("caller UID is not approved by the administrator"))' /tmp/denied-open.json > /dev/null
    cat /tmp/denied-open.json
    echo 'RUSTUSB_COMPOSITE_OPEN_DENIED: PASS uid=1001 explicit_refusal=true'
    chown 1000:1000 /run/composite
    chmod 700 /run/composite
    mkfifo -m 600 /run/composite/control
    chown 1000:1000 /run/composite/control
    exec 3<> /run/composite/control
    (
        exec 3<&-
        exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb physical-server \
            --device-id "$DEVICE_ID" --listen 192.0.2.1:3240 --allow-insecure-test-network \
            --allow-peer 192.0.2.2 --control-stdin < /run/composite/control > /tmp/server.events 2> /tmp/server.log
    ) &
    SERVER_PID=$!
    for attempt in $(seq 1 150); do
        if jq -se 'any(.[]; .event == "ready" and .schema_version == 1 and (.snapshot.devices | length) == 1)' /tmp/server.events > /dev/null 2>&1; then break; fi
        kill -0 "$SERVER_PID"
        sleep 0.05
    done
    jq -se 'any(.[]; .event == "ready")' /tmp/server.events > /dev/null
    SERVER_START=$(awk '{print $22}' "/proc/$SERVER_PID/stat")
    server_identity() {
        identity "$SERVER_PID"
        [ "$(awk '{print $22}' "/proc/$SERVER_PID/stat")" = "$SERVER_START" ]
        [ "$(stat -c '%u:%a' "$NODE")" = 0:600 ]
        found=false
        for descriptor in /proc/"$SERVER_PID"/fd/*; do [ "$(readlink "$descriptor")" != "$NODE" ] || found=true; done
        [ "$found" = true ]
        [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    }
    server_identity
    echo 'RUSTUSB_COMPOSITE_SERVER_UID: PASS uid=1000 capabilities=0 helper_fd=true server_native_modules=false'
    control() {
        printf '{"id":"%s","op":"%s"}\n' "$1" "$2" | /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox tee /run/composite/control > /dev/null
        for attempt in $(seq 1 100); do
            if jq -se --arg id "$1" 'any(.[]; .id == $id and (.event == "snapshot" or (.event == "ack" and .ok == true)))' /tmp/server.events > /dev/null 2>&1; then return 0; fi
            kill -0 "$SERVER_PID"
            sleep 0.05
        done
        return 1
    }
    snapshot() {
        control "$1" snapshot
        jq -s --arg id "$1" '.[] | select(.event == "snapshot" and .id == $id) | .snapshot' /tmp/server.events > "/tmp/$1.json"
        jq -e '.schema_version == 1 and (.devices | length) == 1' "/tmp/$1.json" > /dev/null
        server_identity
    }
    released() {
        jq -e '.buffer_bytes_reserved == 0 and all(.devices[]; .state == "available" and .leased == false and .pending_urbs == 0 and .queued_urbs == 0 and .usbfs_inflight == 0 and .cleanup_verified == true and .configuration == 1 and .current_drivers == .original_drivers and (.current_drivers | length) == 2 and all(.current_drivers[]; .driver == "cdc_ether"))' "$1" > /dev/null
        original_state
    }
    STAGE=raw-lifecycle-cleanup
    receive_control lifecycle-passed
    snapshot baseline
    released /tmp/baseline.json
    jq -e '.devices[0].usbfs_reaped >= 10 and .devices[0].cancelled >= 2 and .devices[0].counters.controls > 10 and .devices[0].counters.errors >= 3' /tmp/baseline.json > /dev/null
    echo 'RUSTUSB_COMPOSITE_BASELINE: PASS original_config_alt_drivers=true inflight=0 budget=0'
    ifconfig "$PEER_NET" 198.18.0.1 netmask 255.255.255.252 up
    echo "RUSTUSB_COMPOSITE_PEER_NETWORK: PASS interface=$PEER_NET address=198.18.0.1 host_network=false"
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/composite-wire-probe udp-peer > /tmp/peer.log 2> /tmp/peer.err &
    PEER_PID=$!
    for attempt in $(seq 1 100); do grep -q 'RUSTUSB_COMPOSITE_UDP_PEER_READY' /tmp/peer.log && break; sleep 0.05; done
    identity "$PEER_PID"
    echo 'RUSTUSB_COMPOSITE_PEER_UID: PASS uid=1000 capabilities=0'
    send_control 192.0.2.2 lifecycle-checked
    for cycle in 1 2; do
        STAGE="cycle-$cycle"
        receive_control "cycle-$cycle-ready"
        snapshot "held-$cycle"
        jq -e 'all(.devices[]; .state == "leased" and .leased == true and .configuration == 1 and (.current_drivers | length) == 2 and all(.current_drivers[]; .driver == "usbfs"))' "/tmp/held-$cycle.json" > /dev/null
        for number in 0 1; do
            [ "$(readlink "/sys/bus/usb/devices/$BUS:1.$number/driver" | sed 's#.*/##')" = usbfs ]
            [ "$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$BUS:1.$number/bAlternateSetting")" = "$number" ]
        done
        echo "RUSTUSB_COMPOSITE_HELD: PASS cycle=$cycle config=1 alt1=1 both_drivers=usbfs"
        send_control 192.0.2.2 "cycle-$cycle-input"
        receive_control "cycle-$cycle-io"
        snapshot "io-$cycle"
        jq -e --slurpfile before "/tmp/held-$cycle.json" '.devices[0].counters.bulk_in > $before[0].devices[0].counters.bulk_in and .devices[0].counters.bulk_out > $before[0].devices[0].counters.bulk_out' "/tmp/io-$cycle.json" > /dev/null
        send_control 192.0.2.2 "cycle-$cycle-detach"
        receive_control "cycle-$cycle-released"
        snapshot "released-$cycle"
        released "/tmp/released-$cycle.json"
        echo "RUSTUSB_COMPOSITE_RELEASED: PASS cycle=$cycle config=1 alt0=0 alt1=1 drivers=cdc_ether inflight=0 budget=0"
        send_control 192.0.2.2 "cycle-$cycle-clean"
        receive_control "cycle-$cycle-checked"
    done
    STAGE=shutdown
    wait "$PEER_PID"
    [ ! -s /tmp/peer.err ]
    grep -F 'RUSTUSB_COMPOSITE_UDP_PEER: PASS cycles=2' /tmp/peer.log
    echo 'RUSTUSB_COMPOSITE_PEER_EVENTS_BEGIN'
    cat /tmp/peer.log
    echo 'RUSTUSB_COMPOSITE_PEER_EVENTS_END'
    control stop shutdown
    wait "$SERVER_PID"
    exec 3<&-
    jq -s '.[] | select(.event == "stopped") | .snapshot' /tmp/server.events > /tmp/stopped.json
    released /tmp/stopped.json
    jq -se 'all(.[]; .event != "error") and any(.[]; .event == "stopped")' /tmp/server.events > /dev/null
    for needle in RUSTUSB-ECM-REQUEST- RUSTUSB-ECM-RESPONSE-; do
        if grep -F "$needle" /tmp/server.log /tmp/server.events; then
            echo 'fixed test payload leaked into production server output'
            exit 1
        fi
    done
    echo 'RUSTUSB_COMPOSITE_PRODUCTION_LOGS: PASS fixed_payload_absent=true level=default'
    echo 'RUSTUSB_COMPOSITE_STOPPED: PASS exit=0 restored=true pending=0 inflight=0 budget=0'
    echo 'RUSTUSB_COMPOSITE_EVENTS_BEGIN'
    cat /tmp/server.events
    echo 'RUSTUSB_COMPOSITE_EVENTS_END'
    [ ! -s /tmp/server.log ] || { echo 'RUSTUSB_COMPOSITE_STDERR_BEGIN'; cat /tmp/server.log; echo 'RUSTUSB_COMPOSITE_STDERR_END'; }
    send_control 192.0.2.2 finished
else
    STAGE=client-helper
    jq -n '{allowed_uids:[1000],export_device_ids:[],peers:[{host:"192.0.2.1",port:3240}],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    discover() {
        for attempt in $(seq 1 150); do
            if unpriv --json --timeout 2 discover 192.0.2.1 > /tmp/discover.json 2>/dev/null && jq -e 'length == 1 and .[0].vendor_id == 1317 and .[0].product_id == 42146' /tmp/discover.json > /dev/null; then return 0; fi
            sleep 0.05
        done
        return 1
    }
    discover
    BUS=$(jq -r '.[0].bus_id' /tmp/discover.json)
    STAGE=raw-lifecycle
    probe lifecycle
    send_control 192.0.2.1 lifecycle-passed
    receive_control lifecycle-checked
    for cycle in 1 2; do
        STAGE="client-cycle-$cycle"
        if [ "$cycle" = 1 ]; then
            unpriv --json attach 192.0.2.1 "$BUS" > /tmp/attach.json
        else
            echo 'RUSTUSB_COMPOSITE_OFFICIAL_ATTACH command=usbip_attach'
            /usr/bin/usbip attach -r 192.0.2.1 -b "$BUS"
            /usr/bin/usbip port > /tmp/official-port.log
        fi
        for attempt in $(seq 1 150); do
            if unpriv --json sessions > /tmp/sessions.json && jq -e 'length == 1 and .[0].session.status == 6 and .[0].session.vendor_id == 1317 and .[0].session.product_id == 42146' /tmp/sessions.json > /dev/null; then break; fi
            sleep 0.05
        done
        managed=true
        [ "$cycle" = 1 ] || managed=false
        jq -e --argjson managed "$managed" 'length == 1 and .[0].managed == $managed and .[0].session.status == 6' /tmp/sessions.json > /dev/null
        if [ "$cycle" = 1 ]; then
            jq -e --slurpfile receipt /tmp/attach.json '.[0].session == $receipt[0]' /tmp/sessions.json > /dev/null
        else
            jq '.[0].session' /tmp/sessions.json > /tmp/attach.json
        fi
        PORT=$(jq -r '.port' /tmp/attach.json)
        LOCAL=$(jq -r '.local_bus_id' /tmp/attach.json)
        USB_NET=
        for attempt in $(seq 1 150); do
            for path in /sys/bus/usb/devices/"$LOCAL":1.*/net/*; do [ ! -d "$path" ] || USB_NET=$(basename "$path"); done
            [ -z "$USB_NET" ] || break
            sleep 0.05
        done
        [ -n "$USB_NET" ] && [ "$USB_NET" != eth0 ]
        [ "$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$LOCAL/bConfigurationValue")" = 1 ]
        [ "$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$LOCAL/bNumInterfaces")" = 2 ]
        for number in 0 1; do
            [ "$(readlink "/sys/bus/usb/devices/$LOCAL:1.$number/driver" | sed 's#.*/##')" = cdc_ether ]
            [ "$(tr -d ' \t\r\n' < "/sys/bus/usb/devices/$LOCAL:1.$number/bAlternateSetting")" = "$number" ]
        done
        ifconfig "$USB_NET" 198.18.0.2 netmask 255.255.255.252 up
        awk -v name="$USB_NET" '$1 == name && $2 == "000012C6" && $8 == "FCFFFFFF" { found=1 } END {exit !found}' /proc/net/route
        probe busy "$BUS"
        if [ "$cycle" = 2 ]; then
            if unpriv --json detach "$PORT" > /tmp/unmanaged-denied.log 2>&1; then exit 1; fi
            grep -F 'port is not owned by this helper instance' /tmp/unmanaged-denied.log
            echo 'RUSTUSB_COMPOSITE_OFFICIAL_OWNERSHIP: PASS managed=false helper_detach=denied'
        fi
        echo "RUSTUSB_COMPOSITE_ATTACHED: PASS cycle=$cycle managed=$managed interfaces=2 alt1=1 driver=cdc_ether data_route=$USB_NET"
        send_control 192.0.2.1 "cycle-$cycle-ready"
        receive_control "cycle-$cycle-input"
        if probe udp-client "$cycle" > /tmp/udp-client.log 2>&1; then
            cat /tmp/udp-client.log
        else
            cat /tmp/udp-client.log
            send_control 192.0.2.1 "cycle-$cycle-io-failed" || true
            exit 1
        fi
        unpriv --json sessions > /tmp/sessions.json
        jq -e --argjson managed "$managed" --slurpfile receipt /tmp/attach.json 'length == 1 and .[0].managed == $managed and .[0].session == $receipt[0]' /tmp/sessions.json > /dev/null
        echo "RUSTUSB_COMPOSITE_IO: PASS cycle=$cycle exact_udp=true same_receipt=true"
        send_control 192.0.2.1 "cycle-$cycle-io"
        receive_control "cycle-$cycle-detach"
        ifconfig "$USB_NET" down
        if [ "$cycle" = 1 ]; then unpriv --json detach "$PORT"; else /usr/bin/usbip detach -p "$PORT"; fi
        for attempt in $(seq 1 150); do
            if unpriv --json sessions > /tmp/sessions.json && jq -e 'length == 0' /tmp/sessions.json > /dev/null && [ ! -e "/sys/bus/usb/devices/$LOCAL" ]; then break; fi
            sleep 0.05
        done
        jq -e 'length == 0' /tmp/sessions.json > /dev/null
        [ ! -e "/sys/bus/usb/devices/$LOCAL" ]
        discover
        echo "RUSTUSB_COMPOSITE_CLIENT_RELEASED: PASS cycle=$cycle sessions=0 old_device_gone=true"
        send_control 192.0.2.1 "cycle-$cycle-released"
        receive_control "cycle-$cycle-clean"
        probe health
        send_control 192.0.2.1 "cycle-$cycle-checked"
    done
    receive_control finished
fi
echo "RUSTUSB_COMPOSITE_RESULT: PASS role=$ROLE"
