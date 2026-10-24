#!/bin/sh
# Disposable PID1 only. Host coordination uses ttyS2, never the external NIC.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || exit 2
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.secure=1 '*) ;; *) exit 2 ;; esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/broker /var/run/vhci_hcd /var/lib/rustusb
mount -t devpts devpts /dev/pts
ROLE=unknown BACKEND=unknown FAMILY=unknown STAGE=boot
# Kernel command line is deliberately a whitespace-separated word list.
# shellcheck disable=SC2013
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in
        rustusb.role=*) ROLE=${parameter#*=} ;;
        rustusb.backend=*) BACKEND=${parameter#*=} ;;
        rustusb.family=*) FAMILY=${parameter#*=} ;;
    esac
done
case "$ROLE:$BACKEND:$FAMILY" in server:virtual:ipv4|server:virtual:ipv6|server:usbfs:ipv4|server:usbfs:ipv6|server:native:ipv4|server:native:ipv6|client:virtual:ipv4|client:virtual:ipv6|client:usbfs:ipv4|client:usbfs:ipv6|client:native:ipv4|client:native:ipv6) ;; *) exit 2 ;; esac
stty -F /dev/ttyS1 raw -echo 115200 clocal -hupcl
stty -F /dev/ttyS2 raw -echo 115200 clocal -hupcl
exec > /dev/ttyS1 2>&1
exec 5<> /dev/ttyS2
dump_artifacts() {
    artifact_failed=0
    dump_root=/run/rustusb-artifact-dump
    if ! mkdir -p "$dump_root" || ! chmod 700 "$dump_root"; then
        echo 'SECURE_ARTIFACT_ERROR dump-directory unavailable=true'
        return 1
    fi
    for file in /tmp/*; do
        [ -f "$file" ] || continue
        name=$(basename "$file")
        if cp "$file" "$dump_root/raw"; then :; else
            copy_status=$?
            echo "SECURE_ARTIFACT_ERROR $name copy_exit=$copy_status"
            artifact_failed=1
            continue
        fi
        size=$(wc -c < "$dump_root/raw")
        if [ "$size" -gt 1048576 ]; then echo "SECURE_ARTIFACT_OVERSIZE $name $size"; artifact_failed=1; continue; fi
        # Complete encoding outside the enumerated directory before opening a
        # block, so one failure cannot corrupt extraction of later artifacts.
        if /usr/bin/base64 "$dump_root/raw" > "$dump_root/encoded"; then
            echo "SECURE_ARTIFACT_BEGIN $name $size"
            cat "$dump_root/encoded"
            echo "SECURE_ARTIFACT_END $name"
        else
            encoder_status=$?
            echo "SECURE_ARTIFACT_ERROR $name encoder_exit=$encoder_status"
            artifact_failed=1
        fi
    done
    return "$artifact_failed"
}
failure_journal() {
    # These are fixed diagnostic paths supplied below, never helper operations.
    # Each file is an independent read snapshot, not a joint transaction.
    journal_label=$1
    journal_source=$2
    journal_prefix="/tmp/failure-$journal_label"
    printf 'source=%s\nlimit_bytes=1048576\n' "$journal_source" > "$journal_prefix.status.txt"
    if [ -L "$journal_source" ]; then
        printf 'status=nonregular\n' >> "$journal_prefix.status.txt"
    elif [ ! -e "$journal_source" ]; then
        printf 'status=absent\n' >> "$journal_prefix.status.txt"
    elif [ ! -f "$journal_source" ]; then
        printf 'status=nonregular\n' >> "$journal_prefix.status.txt"
    elif timeout 2 head -c 1048577 "$journal_source" > "$journal_prefix.capture" 2> "$journal_prefix.read.log"; then
        journal_bytes=$(wc -c < "$journal_prefix.capture")
        if [ "$journal_bytes" -gt 1048576 ]; then
            head -c 1048576 "$journal_prefix.capture" > "$journal_prefix.prefix.bin"
            printf 'status=over_limit\ncomplete=false\n' >> "$journal_prefix.status.txt"
        elif cp "$journal_prefix.capture" "$journal_prefix.json" 2>> "$journal_prefix.read.log"; then
            printf 'status=copied\ncomplete=true\nbytes=%s\n' "$journal_bytes" >> "$journal_prefix.status.txt"
        else
            journal_copy_status=$?
            printf 'status=copy_failed\ncopy_exit=%s\n' "$journal_copy_status" >> "$journal_prefix.status.txt"
        fi
    else
        journal_read_status=$?
        printf 'status=unreadable\nread_exit=%s\n' "$journal_read_status" >> "$journal_prefix.status.txt"
    fi
    rm -f "$journal_prefix.capture"
}
cleanup() {
    status=$?
    trap - EXIT
    # A diagnostic failure must not replace the original failure, skip later
    # files or exit PID1 before poweroff. Keep each encoder failure explicit.
    set +e
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_SECURE_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        failure_journal native-channels /var/lib/rustusb/native/channels.json
        failure_journal physical-journal /var/lib/rustusb/physical/journal.json
        dmesg > /tmp/failure-dmesg.log
        cat /proc/net/tcp /proc/net/tcp6 > /tmp/failure-tcp.log
    fi
    dump_artifacts
    artifact_status=$?
    if [ "$artifact_status" != 0 ]; then
        echo "RUSTUSB_SECURE_RESULT: FAIL role=$ROLE stage=artifact-dump original_exit=$status artifact_exit=$artifact_status"
    fi
    sync
    poweroff -f
    poweroff_status=$?
    echo "SECURE_POWEROFF_RETURNED role=$ROLE original_exit=$status artifact_exit=$artifact_status poweroff_exit=$poweroff_status"
    # The bounded host runner will terminate this failed guest. Never exit PID1
    # and manufacture a kernel panic merely because the final syscall returned.
    while :; do sleep 1; done
}
trap cleanup EXIT
echo "SECURE_BOOT role=$ROLE backend=$BACKEND family=$FAMILY kernel=$(uname -r)"
STAGE=credentials-rng-rtc
if [ "$ROLE" = server ]; then OTHER_ROLE=client; else OTHER_ROLE=server; fi
[ -f "/etc/rustusb/$ROLE-key.der" ]
[ ! -e "/etc/rustusb/$OTHER_ROLE-key.der" ]
[ ! -e /etc/rustusb/ca-key.pem ] && [ ! -e /etc/rustusb/ca-key.der ]
echo "SECURE_CREDENTIAL_ISOLATION role=$ROLE own_key=true opposite_key=false ca_key=false"
# Wait for the kernel's actual CRNG-ready marker. Do not manufacture entropy or
# alter certificate verification time. A host without usable RNG fails here.
# shellcheck disable=SC2034
for rng_attempt in $(seq 1 200); do
    if dmesg | grep -F 'random: crng init done' > /tmp/random-ready.txt; then break; fi
    sleep 0.05
done
grep -F 'random: crng init done' /tmp/random-ready.txt > /dev/null
created=$(cat /etc/rustusb/host-created-epoch.txt)
now=$(date -u +%s)
[ "$now" -ge "$((created-5))" ] && [ "$now" -le "$((created+1800))" ]
cat /proc/driver/rtc > /tmp/rtc.txt
printf 'host_payload_epoch=%s\nguest_epoch=%s\n' "$created" "$now" > /tmp/clock-observation.txt
echo "SECURE_RNG_RTC_READY crng=true guest_epoch=$now"
for name in usb-common usbcore e1000 evdev hid hid-generic usbhid usbserial ftdi_sio cdc-acm; do
    [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
done
ip link set lo up
ip link set eth0 up
if [ "$ROLE" = server ]; then LAST=1; else LAST=2; fi
echo 0 > /proc/sys/net/ipv6/conf/eth0/accept_dad
ip addr add "192.0.2.$LAST/24" dev eth0
ip -6 addr add "fd42:7275:7374::$LAST/64" dev eth0
ip addr show dev eth0 > /tmp/network.txt
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = 0 ]
[ "$(cat /proc/sys/net/ipv6/conf/all/forwarding)" = 0 ]
if [ "$FAMILY" = ipv4 ]; then HOST=192.0.2.1; LISTEN=192.0.2.1:3241; else HOST=fd42:7275:7374::1; LISTEN='[fd42:7275:7374::1]:3241'; fi
if [ "$ROLE" = client ]; then
    STAGE=dual-stack-hostname
    if [ "$FAMILY" = ipv4 ]; then
        BAD=fd42:7275:7374::99 GOOD=192.0.2.1
        printf 'precedence ::/0 100\nprecedence ::ffff:0:0/96 10\n' > /etc/gai.conf
        /usr/bin/ip -6 neigh replace "$BAD" lladdr 52:54:00:00:00:99 nud permanent dev eth0
    else
        BAD=192.0.2.99 GOOD=fd42:7275:7374::1
        printf 'precedence ::/0 10\nprecedence ::ffff:0:0/96 100\n' > /etc/gai.conf
        /usr/bin/ip -4 neigh replace "$BAD" lladdr 52:54:00:00:00:99 nud permanent dev eth0
    fi
    printf '127.0.0.1 localhost\n::1 localhost\n%s rustusb-dual-stack.test\n%s rustusb-dual-stack.test\n' "$BAD" "$GOOD" > /etc/hosts
    printf 'hosts: files\n' > /etc/nsswitch.conf
    cp /etc/hosts /tmp/dual-hosts.txt
    cp /etc/gai.conf /tmp/dual-gai.txt
    cp /etc/nsswitch.conf /tmp/dual-nsswitch.txt
    /usr/bin/ip -4 neigh show dev eth0 > /tmp/dual-neighbors4.txt
    /usr/bin/ip -6 neigh show dev eth0 > /tmp/dual-neighbors6.txt
    HOST=rustusb-dual-stack.test
    echo "DUAL_STACK_READY healthy=$FAMILY stale=$BAD mac=52:54:00:00:00:99"
fi
broker() { /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb "$@"; }
legacy() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
host_read() {
    # /bin/sh in this initramfs is BusyBox ash, which supports bounded read -t.
    # shellcheck disable=SC3045
    IFS= read -r -t 60 CONTROL <&5
    [ "${#CONTROL}" -le 4096 ]
}
host_expect() { host_read; [ "$CONTROL" = "$1" ]; }
identity() {
    kill -0 "$1"
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$1/status")" = 2000:2000:2000:2000 ]
    for cap in CapInh CapPrm CapEff CapAmb; do
        [ "$(awk -v key="$cap:" '$1 == key {print $2}' "/proc/$1/status")" = 0000000000000000 ]
    done
    cat "/proc/$1/status" > "/tmp/identity-$2.txt"
    sha256sum "/proc/$1/exe" > "/tmp/executable-$2.txt"
}
wait_exit() {
    for attempt in $(seq 1 200); do kill -0 "$1" 2>/dev/null || break; sleep 0.05; done
    if kill -0 "$1" 2>/dev/null; then echo "process did not exit: $2"; return 1; fi
    if wait "$1"; then rc=0; else rc=$?; fi
    echo "$rc" > "/tmp/wait-$2.txt"
    [ "$rc" = 0 ]
}
start_helper() {
    chmod 644 /etc/rustusb/policy.json
    /usr/bin/rustusb-helper --physical-state-dir /var/lib/rustusb/physical --native-state-dir /var/lib/rustusb/native > /tmp/helper.log 2>&1 &
    HELPER_PID=$!
    for attempt in $(seq 1 100); do [ ! -S /run/rustusb/control.sock ] || break; kill -0 "$HELPER_PID"; sleep 0.05; done
    [ -S /run/rustusb/control.sock ]
    [ "$(readlink "/proc/$HELPER_PID/exe")" = /usr/bin/rustusb-helper ]
    sha256sum "/proc/$HELPER_PID/exe" > /tmp/executable-helper.txt
    cat "/proc/$HELPER_PID/status" > /tmp/identity-helper.txt
    cp /etc/rustusb/policy.json /tmp/helper-policy.json
}
host_baseline() {
    : > /tmp/baseline-rows.jsonl
    for bus in "$HID_BUS" "$SERIAL_BUS"; do
        base="/sys/bus/usb/devices/$bus"
        : > /tmp/interfaces.jsonl
        for interface in "$base":*; do
            [ -d "$interface" ] || continue
            driver=$(readlink "$interface/driver" 2>/dev/null | sed 's#.*/##')
            jq -nc --arg name "$(basename "$interface")" --arg driver "$driver" \
                --arg number "$(cat "$interface/bInterfaceNumber")" --arg alt "$(cat "$interface/bAlternateSetting")" \
                --arg class "$(cat "$interface/bInterfaceClass")" --arg subclass "$(cat "$interface/bInterfaceSubClass")" --arg protocol "$(cat "$interface/bInterfaceProtocol")" \
                '{name:$name,number:$number,alt:$alt,class:$class,subclass:$subclass,protocol:$protocol,driver:$driver}' >> /tmp/interfaces.jsonl
        done
        jq -nc --arg bus "$bus" --arg generation "$(stat -Lc '%d:%i' "$base")" \
            --arg devnum "$(cat "$base/devnum")" --arg cfg "$(cat "$base/bConfigurationValue")" \
            --arg count "$(cat "$base/bNumInterfaces")" --slurpfile interfaces /tmp/interfaces.jsonl \
            --arg path "$(readlink -f "$base")" --arg busnum "$(cat "$base/busnum")" \
            --arg vendor "$(cat "$base/idVendor")" --arg product "$(cat "$base/idProduct")" \
            --arg speed "$(cat "$base/speed")" --arg release "$(cat "$base/bcdDevice")" \
            --arg device_driver "$(readlink "$base/driver" | sed 's#.*/##')" \
            --argjson usbip_status "$(if [ -f "$base/usbip_status" ]; then cat "$base/usbip_status"; else echo null; fi)" \
            --argjson configuration_bytes "$(wc -c < "$base/bConfigurationValue")" --argjson count_bytes "$(wc -c < "$base/bNumInterfaces")" \
            '{bus:$bus,generation:$generation,devnum:$devnum,path:$path,busnum:$busnum,vendor:$vendor,product:$product,speed:$speed,release:$release,configuration:$cfg,num_interfaces:$count,configuration_bytes:$configuration_bytes,count_bytes:$count_bytes,interfaces:($interfaces|sort_by(.name)),device_driver:$device_driver,usbip_status:$usbip_status}' >> /tmp/baseline-rows.jsonl
    done
    jq -s 'sort_by(.bus)' /tmp/baseline-rows.jsonl > "$1"
}
native_host_phase() {
    host_baseline "/tmp/host-$1.json"
    jq -e --arg phase "$2" --slurpfile before /tmp/host-before.json '
        def hardware: del(.configuration,.num_interfaces,.configuration_bytes,.count_bytes,.interfaces,.device_driver,.usbip_status);
        length==2 and (map(hardware))==($before[0]|map(hardware)) and
        all(.[]; .device_driver=="usbip-host") and
        (if $phase=="exported" then
            all(.[]; .configuration=="" and .num_interfaces=="" and .configuration_bytes==0 and .count_bytes==0 and .interfaces==[] and .usbip_status==1)
         else
            all(.[]; (.configuration|tonumber)==1 and (.num_interfaces|tonumber)==1 and (.interfaces|length)==1) and
            (map(.interfaces|map(.driver="")))==($before[0]|map(.interfaces|map(.driver=""))) and
            all(.[]; all(.interfaces[]; .driver=="")) and
            all(.[]; .usbip_status==(if $phase=="held" then 2 else 1 end))
         end)' "/tmp/host-$1.json" > /dev/null
}
native_server_journal() {
    cp /var/lib/rustusb/native/channels.json "/tmp/server-native-$1.json"
    jq -e --arg phase "$2" --argjson pid "$SERVER_PID" --slurpfile keys /tmp/keys.json --slurpfile original /tmp/server-native-exported.json '
        .schema_version==1 and .records==[] and (.exports|length)==2 and
        ([.exports[].registration.device_key]|sort)==($keys[0]|sort) and
        ([.exports[]|del(.stub)]|sort_by(.lease_id))==($original[0].exports|map(del(.stub))|sort_by(.lease_id)) and
        all(.exports[]; .owner.uid==2000 and .owner.pid==$pid and .bound!=null and .registration.original_device_driver=="usb" and
            .bound.generation==.registration.generation and .bound.bound_profile.configuration==null and .bound.bound_profile.interfaces==[]) and
        (if $phase=="held" then all(.exports[]; .stub.state.phase=="observed" and .stub.state.receipt.usbip_status==2 and .stub.state.receipt.export==.bound)
         else all(.exports[]; .stub==null) end)' "/tmp/server-native-$1.json" > /dev/null
}
native_snapshot() {
    : > "$1"
    for controller in /sys/devices/platform/vhci_hcd.*; do
        [ -d "$controller" ] || continue
        for status in "$controller"/status*; do
            [ -f "$status" ] || continue
            echo "FILE $status" >> "$1"
            cat "$status" >> "$1"
        done
    done
    [ -s "$1" ]
}
native_empty() {
    native_snapshot "$1"
    awk '$1 == "hs" || $1 == "ss" {n++; if ($3 != 4) bad=1} END {exit !(n > 0 && !bad)}' "$1"
    [ ! -e /var/lib/rustusb/native/channels.json ] || jq -e '.schema_version == 1 and .records == [] and .exports == []' /var/lib/rustusb/native/channels.json > /dev/null
}
control() {
    printf '%s\n' "$1" | timeout 3 /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /bin/busybox tee /run/broker/control > /dev/null
    # Separate counter from callers' polling loops.
    # shellcheck disable=SC2034
    for control_attempt in $(seq 1 100); do
        if jq -se --arg id "$2" 'any(.[]; .id == $id and (.event == "snapshot" or (.event == "ack" and .ok == true)))' /tmp/server.events > /dev/null 2>&1; then return 0; fi
        kill -0 "$SERVER_PID"; sleep 0.05
    done
    return 1
}
snapshot() {
    control "{\"id\":\"$1\",\"op\":\"snapshot\"}" "$1"
    jq -s --arg id "$1" '.[] | select(.event == "snapshot" and .id == $id) | .snapshot' /tmp/server.events > "/tmp/$1.json"
}
released() {
    for attempt in $(seq 1 100); do
        snapshot "released-$1-$attempt"
        file="/tmp/released-$1-$attempt.json"
        if [ "$BACKEND" = virtual ]; then
            jq -e 'all(.devices[]; .leased == false and .pending_urbs == 0 and .configuration == 0 and .hid_queued_reports == 0 and .cdc_queued_bytes == 0)' "$file" > /dev/null && break
        elif [ "$BACKEND" = usbfs ]; then
            jq -e '.buffer_bytes_reserved == 0 and all(.devices[]; .state == "available" and .leased == false and .pending_urbs == 0 and .queued_urbs == 0 and .usbfs_inflight == 0 and .cleanup_verified == true and .current_drivers == .original_drivers)' "$file" > /dev/null && break
        else
            jq -e '.metadata_scope=="published_registration_not_live_configuration" and (.devices|length)==2 and all(.devices[]; .accepting_imports==true)' "$file" > /dev/null && break
        fi
        rm "$file"; sleep 0.05
    done
    [ -f "$file" ]
    cp "$file" "/tmp/released-$1.json"
    if [ "$BACKEND" = usbfs ]; then host_baseline "/tmp/host-released-$1.json"; cmp /tmp/host-before.json "/tmp/host-released-$1.json"; fi
    if [ "$BACKEND" = native ]; then native_host_phase "released-$1" released; native_server_journal "released-$1" released; fi
}
if [ "$ROLE" = server ]; then
    STAGE=server-prepare
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    if [ "$BACKEND" != virtual ]; then
        for name in xhci-hcd xhci-pci-renesas xhci-pci; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
        if [ "$BACKEND" = native ]; then
            for name in usbip-core usbip-host; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
            [ -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
        fi
        for attempt in $(seq 1 100); do
            legacy --json devices > /tmp/devices.json
            jq -e 'length == 2 and all(.[]; (.export_blockers|length) == 0)' /tmp/devices.json > /dev/null && break
            sleep 0.05
        done
        jq -e 'length == 2 and all(.[]; (.export_blockers|length) == 0)' /tmp/devices.json > /dev/null
        HID_ID=$(jq -er '.[]|select(.vendor_id==1575 and .product_id==1)|.id' /tmp/devices.json)
        SERIAL_ID=$(jq -er '.[]|select(.vendor_id==1027 and .product_id==24577)|.id' /tmp/devices.json)
        HID_BUS=$(jq -er --arg id "$HID_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
        SERIAL_BUS=$(jq -er --arg id "$SERIAL_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
        if [ "$BACKEND" = native ]; then KEY_NAMESPACE=native; else KEY_NAMESPACE=physical; fi
        HID_KEY="$KEY_NAMESPACE:$HID_ID" SERIAL_KEY="$KEY_NAMESPACE:$SERIAL_ID"
        host_baseline /tmp/host-before.json
        jq -e 'length == 2 and all(.[]; (.configuration|tonumber)==1 and (.interfaces|length)==1) and ([.[].interfaces[].driver]|sort)==["ftdi_sio","usbhid"]' /tmp/host-before.json > /dev/null
        for bus in "$HID_BUS" "$SERIAL_BUS"; do
            node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$bus/busnum")" "$(cat "/sys/bus/usb/devices/$bus/devnum")")
            [ -c "$node" ] && [ "$(stat -c '%u' "$node")" = 0 ]
            chmod 600 "$node"
            for uid in 1000 2000; do
                # The nested shell receives the node as its own $1.
                # shellcheck disable=SC2016
                if /usr/bin/setpriv --reuid="$uid" --regid="$uid" --clear-groups /bin/busybox sh -c 'exec 6<> "$1"' sh "$node" > /tmp/direct-usbfs-denied.log 2>&1; then exit 1; fi
            done
        done
        [ "$(cat "/sys/bus/usb/devices/$HID_BUS/speed")" = 480 ]
        [ "$(cat "/sys/bus/usb/devices/$SERIAL_BUS/speed")" = 12 ]
        if [ "$BACKEND" = native ]; then
            jq -n --arg hid "$HID_ID" --arg serial "$SERIAL_ID" '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_native_device_ids:[$hid,$serial]}' > /etc/rustusb/policy.json
            jq -nc --arg id "$HID_ID" '{op:"prepare_native_export",device_id:$id}' > /tmp/denied-request.json
        else
            jq -n --arg hid "$HID_ID" --arg serial "$SERIAL_ID" '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_physical_device_ids:[$hid,$serial]}' > /etc/rustusb/policy.json
            jq -nc --arg id "$HID_ID" '{op:"open_secure_physical",journal_schema_version:1,device_id:$id}' > /tmp/denied-request.json
        fi
        start_helper
        timeout 5 /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/nc-openbsd -N -U -w 3 /run/rustusb/control.sock < /tmp/denied-request.json > /tmp/denied-response.json
        jq -e '.ok == false and .data == null and (.error|contains("not the configured secure broker"))' /tmp/denied-response.json > /dev/null
        host_baseline /tmp/host-after-denied.json
        cmp /tmp/host-before.json /tmp/host-after-denied.json
        [ ! -e /var/lib/rustusb/physical/journal.json ] || jq -e '.records == []' /var/lib/rustusb/physical/journal.json > /dev/null
        [ ! -e /var/lib/rustusb/native/channels.json ] || jq -e '.records==[] and .exports==[]' /var/lib/rustusb/native/channels.json > /dev/null
        echo 'SECURE_HELPER_DENIED role=server uid=1000 unchanged=true'
    else
        HID_KEY=virtual:hid SERIAL_KEY=virtual:cdc-acm
    fi
    jq -nc --arg hid "$HID_KEY" --arg serial "$SERIAL_KEY" '[$hid,$serial]' > /tmp/keys.json
    echo "SECURE_KEYS $(cat /tmp/keys.json)"
    PIN=$(cat /etc/rustusb/client-pin.txt)
    jq -n --arg listen "$LISTEN" --arg pin "$PIN" --slurpfile keys /tmp/keys.json \
        '{schema_version:1,broker_uid:2000,listen:$listen,devices:$keys[0],principals:[{id:"qemu-client",pins:[$pin],devices:$keys[0]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server.json
    cp /etc/rustusb/server.json /tmp/server-config.json
    chown 2000:2000 /etc/rustusb/server-key.der /run/broker
    chmod 600 /etc/rustusb/server-key.der
    chmod 700 /run/broker
    mkfifo -m 600 /run/broker/control
    chown 2000:2000 /run/broker/control
    exec 3<> /run/broker/control
    : > /tmp/server.events
    (exec 3<&- 5<&-; exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb secure-server --backend "$BACKEND" --config /etc/rustusb/server.json --control-stdin < /run/broker/control > /tmp/server.events 2> /tmp/server.log) &
    SERVER_PID=$!
    for attempt in $(seq 1 200); do jq -se 'any(.[]; .event=="ready" and .transport=="mutual_tls")' /tmp/server.events > /dev/null 2>&1 && break; kill -0 "$SERVER_PID"; sleep 0.05; done
    jq -se --arg address "$LISTEN" 'any(.[]; .event=="ready" and .transport=="mutual_tls" and .address==$address)' /tmp/server.events > /dev/null
    if [ "$BACKEND" = native ]; then
        native_host_phase exported exported
        native_server_journal exported exported
        jq -se --slurpfile keys /tmp/keys.json 'any(.[]; .event=="ready" and .backend=="native" and .snapshot.metadata_scope=="published_registration_not_live_configuration" and ([.snapshot.devices[].device_key]|sort)==($keys[0]|sort) and all(.snapshot.devices[]; .accepting_imports==true and .published_device.configuration==1 and (.published_device.interfaces|length)==1))' /tmp/server.events > /dev/null
    fi
    identity "$SERVER_PID" server
    echo 'SECURE_SERVER_READY'
    for cycle in 1 2; do
        STAGE="server-cycle-$cycle"
        host_expect "INJECT $cycle"
        snapshot "held-$cycle"
        if [ "$BACKEND" = native ]; then
            jq -e '.metadata_scope=="published_registration_not_live_configuration" and (.devices|length)==2 and all(.devices[]; .accepting_imports==false and .published_device.configuration==1)' "/tmp/held-$cycle.json" > /dev/null
            native_host_phase "held-$cycle" held
            native_server_journal "held-$cycle" held
        else
            jq -e '(.devices|length)==2 and all(.devices[]; .leased==true and .configuration==1)' "/tmp/held-$cycle.json" > /dev/null
        fi
        if [ "$BACKEND" = virtual ]; then
            control "{\"id\":\"key-$cycle\",\"op\":\"hid_key\",\"key\":$((29+cycle))}" "key-$cycle"
            printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$cycle" > "/tmp/injected-$cycle.bin"
            hex=$(od -An -tx1 -v "/tmp/injected-$cycle.bin" | tr -d ' \n')
            control "{\"id\":\"emit-$cycle\",\"op\":\"cdc_emit\",\"hex\":\"$hex\"}" "emit-$cycle"
        fi
        echo "SECURE_SERVER_INJECTED cycle=$cycle"
        host_expect "IO $cycle"
        snapshot "io-$cycle"
        if [ "$BACKEND" = virtual ]; then
            jq -e --argjson cycle "$cycle" 'all(.devices[]; .leased==true and .configuration==1) and (.devices[]|select(.profile=="hid_keyboard")|.counters.hid_taps)==$cycle and (.devices[]|select(.profile=="cdc_acm")|.counters.cdc_out_bytes)==(35*$cycle) and (.devices[]|select(.profile=="cdc_acm")|.counters.cdc_in_bytes)==(70*$cycle) and (.devices[]|select(.profile=="cdc_acm")|.counters.cdc_injected_bytes)==(35*$cycle)' "/tmp/io-$cycle.json" > /dev/null
        elif [ "$BACKEND" = usbfs ]; then
            jq -e --arg hid "$HID_ID" --arg serial "$SERIAL_ID" 'all(.devices[]; .leased==true and .configuration==1 and .counters.controls>0) and (.devices[]|select(.device_id==$hid)|.counters.interrupt_in)>=2 and (.devices[]|select(.device_id==$serial)|.counters.bulk_in)>0 and (.devices[]|select(.device_id==$serial)|.counters.bulk_out)>0' "/tmp/io-$cycle.json" > /dev/null
        else
            jq -e '.metadata_scope=="published_registration_not_live_configuration" and (.devices|length)==2 and all(.devices[]; .accepting_imports==false)' "/tmp/io-$cycle.json" > /dev/null
            native_host_phase "io-$cycle" held
        fi
        echo "SECURE_SERVER_IO cycle=$cycle"
        host_expect "RELEASED $cycle"
        released "$cycle"
        identity "$SERVER_PID" "server-$cycle"
        echo "SECURE_SERVER_RELEASED cycle=$cycle"
    done
    STAGE=server-stop
    host_expect STOP
    kill -TERM "$SERVER_PID"
    wait_exit "$SERVER_PID" server
    exec 3<&-
    if [ "$BACKEND" = native ]; then
        jq -se 'any(.[]; .event=="stopped" and .backend=="native" and .transport=="mutual_tls" and .exports_closed==2 and .export_cleanup_verified==true) and all(.[]; .event!="error")' /tmp/server.events > /dev/null
    else
        jq -se 'any(.[]; .event=="stopped" and all(.snapshot.devices[]; .leased==false and .pending_urbs==0)) and all(.[]; .event!="error")' /tmp/server.events > /dev/null
    fi
    if [ "$BACKEND" != virtual ]; then
        host_baseline /tmp/host-final.json
        cmp /tmp/host-before.json /tmp/host-final.json
        if [ "$BACKEND" = usbfs ]; then
            cp /var/lib/rustusb/physical/journal.json /tmp/physical-final.json
            jq -e '.records == []' /tmp/physical-final.json > /dev/null
        else
            cp /var/lib/rustusb/native/channels.json /tmp/server-native-final.json
            jq -e '.schema_version==1 and .records==[] and .exports==[]' /tmp/server-native-final.json > /dev/null
        fi
    fi
    echo 'SECURE_SERVER_STOPPED signal=SIGTERM wait=0'
else
    STAGE=client-prepare
    for name in usbip-core vhci-hcd; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    host_read
    printf '%s\n' "$CONTROL" > /tmp/keys.json
    jq -e 'length==2 and all(.[]; type=="string")' /tmp/keys.json > /dev/null
    HID_KEY=$(jq -r '.[0]' /tmp/keys.json) SERIAL_KEY=$(jq -r '.[1]' /tmp/keys.json)
    jq -n --slurpfile keys /tmp/keys.json '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_imports:[{server_id:"qemu-server",device_keys:$keys[0]}]}' > /etc/rustusb/policy.json
    start_helper
    chown 2000:2000 /etc/rustusb/client-key.der
    chmod 600 /etc/rustusb/client-key.der
    for profile in hid serial; do
        if [ "$profile" = hid ]; then key=$HID_KEY; else key=$SERIAL_KEY; fi
        jq -n --arg host "$HOST" --arg key "$key" '{schema_version:1,broker_uid:2000,server_id:"qemu-server",endpoint:{host:$host,port:3241},server_name:"rustusb-qemu.test",device:$key,ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/client.der"],private_key:"/etc/rustusb/client-key.der"}' > "/etc/rustusb/client-$profile.json"
        cp "/etc/rustusb/client-$profile.json" "/tmp/client-$profile-config.json"
    done
    host_expect START
    broker --json secure-discover --config /etc/rustusb/client-hid.json > /tmp/catalog-initial.json
    jq -e --slurpfile keys /tmp/keys.json 'length==2 and (map(.device_key)|sort)==($keys[0]|sort)' /tmp/catalog-initial.json > /dev/null
    native_empty /tmp/native-before-denied.txt
    jq -c '.[0] | {op:"prepare_native_import",target:{server_id:"qemu-server",device_key:.device_key,remote:.device}}' /tmp/catalog-initial.json > /tmp/denied-request.json
    timeout 5 /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/nc-openbsd -N -U -w 3 /run/rustusb/control.sock < /tmp/denied-request.json > /tmp/denied-response.json
    jq -e '.ok==false and .data==null and (.error|contains("not the configured secure broker"))' /tmp/denied-response.json > /dev/null
    native_empty /tmp/native-after-denied.txt
    cmp /tmp/native-before-denied.txt /tmp/native-after-denied.txt
    echo 'SECURE_HELPER_DENIED role=client uid=1000 unchanged=true'
    for family in 4 6; do
        if [ "$family" = 4 ]; then peer=192.0.2.1; else peer=fd42:7275:7374::1; fi
        if timeout 5 /usr/bin/nc-openbsd "-$family" -z -v -w 2 "$peer" 3240 > "/tmp/plaintext-$family.log" 2>&1; then exit 1; else rc=$?; fi
        [ "$rc" = 1 ]
        grep -F 'Connection refused' "/tmp/plaintext-$family.log"
    done
    echo 'SECURE_PLAINTEXT_REFUSED ipv4=true ipv6=true'
    for cycle in 1 2; do
        STAGE="client-cycle-$cycle"
        for profile in hid serial; do
            : > "/tmp/attach-$profile-$cycle.events"
            (exec 5<&-; exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb secure-attach --config "/etc/rustusb/client-$profile.json" > "/tmp/attach-$profile-$cycle.events" 2> "/tmp/attach-$profile-$cycle.log") &
            pid=$!
            if [ "$profile" = hid ]; then HID_PID=$pid; else SERIAL_PID=$pid; fi
            for attempt in $(seq 1 300); do jq -se 'any(.[]; .event=="connected" and .transport=="mtls")' "/tmp/attach-$profile-$cycle.events" > /dev/null 2>&1 && break; kill -0 "$pid"; sleep 0.05; done
            jq -se 'any(.[]; .event=="connected" and .transport=="mtls" and .receipt.kernel.session.status==6)' "/tmp/attach-$profile-$cycle.events" > /dev/null
            identity "$pid" "$profile-$cycle"
        done
        HID_LOCAL=$(jq -sr '.[]|select(.event=="connected")|.receipt.kernel.session.local_bus_id' "/tmp/attach-hid-$cycle.events")
        SERIAL_LOCAL=$(jq -sr '.[]|select(.event=="connected")|.receipt.kernel.session.local_bus_id' "/tmp/attach-serial-$cycle.events")
        [ "$HID_LOCAL" != null ] && [ "$SERIAL_LOCAL" != null ] && [ "$HID_LOCAL" != "$SERIAL_LOCAL" ]
        if [ "$BACKEND" = virtual ]; then
            HID_VID=1234 SERIAL_VID=1234 SERIAL_PID_HEX=0002 SERIAL_IFS=2
        else
            HID_VID=0627 SERIAL_VID=0403 SERIAL_PID_HEX=6001 SERIAL_IFS=1
        fi
        [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idVendor")" = "$HID_VID" ]
        [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idProduct")" = 0001 ]
        [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/idVendor")" = "$SERIAL_VID" ]
        [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/idProduct")" = "$SERIAL_PID_HEX" ]
        [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/bNumInterfaces")" -eq "$SERIAL_IFS" ]
        if [ "$BACKEND" != virtual ]; then
            [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/speed")" = 480 ]
            [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/speed")" = 12 ]
            jq -se 'any(.[]; .event=="connected" and .receipt.target.remote.speed==3)' "/tmp/attach-hid-$cycle.events" > /dev/null
            jq -se 'any(.[]; .event=="connected" and .receipt.target.remote.speed==2)' "/tmp/attach-serial-$cycle.events" > /dev/null
        fi
        jq -nc --arg hid "$HID_LOCAL" --arg serial "$SERIAL_LOCAL" --arg hid_vid "$HID_VID" --arg serial_vid "$SERIAL_VID" --arg serial_pid "$SERIAL_PID_HEX" --argjson serial_interfaces "$SERIAL_IFS" '{hid:{bus_id:$hid,vendor:$hid_vid,product:"0001"},serial:{bus_id:$serial,vendor:$serial_vid,product:$serial_pid,interfaces:$serial_interfaces}}' > "/tmp/local-identities-$cycle.json"
        native_snapshot "/tmp/native-held-$cycle.txt"
        cp /var/lib/rustusb/native/channels.json "/tmp/channels-held-$cycle.json"
        jq -e '.schema_version==1 and .exports==[] and (.records|length)==2 and all(.records[]; .owner.uid==2000 and .state.phase=="observed")' "/tmp/channels-held-$cycle.json" > /dev/null
        if timeout 15 /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb secure-attach --config /etc/rustusb/client-hid.json > "/tmp/contender-$cycle.log" 2>&1; then exit 1; else rc=$?; fi
        [ "$rc" = 1 ]
        grep -F 'configured remote device is unavailable or unauthorized' "/tmp/contender-$cycle.log"
        native_snapshot "/tmp/native-after-contender-$cycle.txt"
        cmp "/tmp/native-held-$cycle.txt" "/tmp/native-after-contender-$cycle.txt"
        cmp "/tmp/channels-held-$cycle.json" /var/lib/rustusb/native/channels.json
        echo "SECURE_CONTENDER_REFUSED cycle=$cycle catalog_unavailable=true resources_unchanged=true"
        EVENT='' TTY=''
        for attempt in $(seq 1 150); do
            EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
            TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$SERIAL_LOCAL")" -name 'ttyACM*' -o -name 'ttyUSB*' | head -1)
            [ -z "$EVENT" ] || [ -z "$TTY" ] || break
            sleep 0.05
        done
        [ -n "$EVENT" ] && [ -n "$TTY" ]
        [ "$(basename "$(readlink "/sys/bus/usb/devices/$HID_LOCAL:1.0/driver")")" = usbhid ]
        if [ "$BACKEND" = virtual ]; then expected_driver=cdc_acm; else expected_driver=ftdi_sio; fi
        [ "$(basename "$(readlink "/sys/bus/usb/devices/$SERIAL_LOCAL:1.0/driver")")" = "$expected_driver" ]
        EVENT=/dev/input/$(basename "$EVENT") TTY=/dev/$(basename "$TTY")
        : > "/tmp/input-$cycle.bin"
        cat "$EVENT" > "/tmp/input-$cycle.bin" & INPUT_PID=$!
        stty -F "$TTY" raw -echo 57600 clocal -hupcl
        exec 4<> "$TTY"
        timeout 15 dd bs=1 count=35 <&4 > "/tmp/rx-$cycle.bin" 2> "/tmp/dd-rx-$cycle.log" & RX_PID=$!
        echo "SECURE_CLIENT_WAIT_INPUT cycle=$cycle"
        wait "$RX_PID"
        printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$cycle" > "/tmp/expected-rx-$cycle.bin"
        [ "$(wc -c < "/tmp/rx-$cycle.bin")" = 35 ]
        cmp "/tmp/rx-$cycle.bin" "/tmp/expected-rx-$cycle.bin"
        if [ "$BACKEND" = virtual ]; then timeout 15 dd bs=1 count=35 <&4 > "/tmp/echo-$cycle.bin" 2> "/tmp/dd-echo-$cycle.log" & ECHO_PID=$!; fi
        printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$cycle" > "/tmp/tx-$cycle.bin"
        cat "/tmp/tx-$cycle.bin" >&4
        if [ "$BACKEND" = virtual ]; then wait "$ECHO_PID"; cmp "/tmp/tx-$cycle.bin" "/tmp/echo-$cycle.bin"; fi
        for attempt in $(seq 1 150); do
            if od -An -tu4 -w24 -v "/tmp/input-$cycle.bin" | awk -v code="$((cycle+1))" 'NF==6 && $5==code*65536+1 && $6==1 {down=1} NF==6 && $5==code*65536+1 && $6==0 {up=1} END {exit !(down&&up)}'; then break; fi
            sleep 0.05
        done
        od -An -tu4 -w24 -v "/tmp/input-$cycle.bin" | awk -v code="$((cycle+1))" 'NF==6 && $5==code*65536+1 && $6==1 {down=1} NF==6 && $5==code*65536+1 && $6==0 {up=1} END {exit !(down&&up)}'
        echo "SECURE_CLIENT_IO cycle=$cycle key_code=$((cycle+1)) rx=35 tx=35"
        host_expect "CLOSE $cycle"
        kill "$INPUT_PID"; wait "$INPUT_PID" || true
        exec 4<&-
        kill -TERM "$HID_PID" "$SERIAL_PID"
        wait_exit "$HID_PID" "hid-$cycle"
        wait_exit "$SERIAL_PID" "serial-$cycle"
        for profile in hid serial; do jq -se 'any(.[]; .event=="disconnected" and .cleanup_verified==true)' "/tmp/attach-$profile-$cycle.events" > /dev/null; done
        native_empty "/tmp/native-empty-$cycle.txt"
        cp /var/lib/rustusb/native/channels.json "/tmp/channels-empty-$cycle.json"
        [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
        echo "SECURE_CLIENT_RELEASED cycle=$cycle signal=SIGTERM wait=0 kernel_null=true local_gone=true"
        host_expect "CLEAN $cycle"
        broker --json secure-discover --config /etc/rustusb/client-hid.json > "/tmp/catalog-reuse-$cycle.json"
        jq -e --slurpfile original /tmp/catalog-initial.json '.==$original[0]' "/tmp/catalog-reuse-$cycle.json" > /dev/null
        echo "SECURE_CLIENT_REUSE cycle=$cycle"
    done
    host_expect FINISH
fi
echo "RUSTUSB_SECURE_RESULT: PASS role=$ROLE"
