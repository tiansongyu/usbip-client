#!/bin/sh
# Disposable guest only: one real physical service, two held imports, one TERM.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo 'Refusing to run outside the disposable QEMU guest init' >&2; exit 2;
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.sigterm_test=1 '*) ;; *) exit 2 ;; esac
ROLE=unknown
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in rustusb.role=*) ROLE=${parameter#*=} ;; esac
done
[ "$ROLE" = server ] || [ "$ROLE" = client ]
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/sigterm /etc/rustusb /var/run/vhci_hcd
mount -t devpts devpts /dev/pts
STAGE=boot
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_SIGTERM_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        for file in /tmp/helper.log /tmp/startup.log /tmp/startup.events /tmp/startup-outcome.json /tmp/server.log /tmp/server.events /tmp/observations.jsonl /tmp/devices.json /tmp/sessions.json /tmp/doctor.json /tmp/denied.log /tmp/denied-open.json /tmp/outcome.json; do
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
echo "RUSTUSB_SIGTERM_EVIDENCE_CHANNEL: PASS role=$ROLE device=ttyS1 kernel_console=ttyS0"
echo "RUSTUSB_SIGTERM_BOOT role=$ROLE kernel=$(uname -r)"
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
echo "RUSTUSB_SIGTERM_ISOLATION: PASS role=$ROLE carrier=1 ip_forward=0"
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
    echo "RUSTUSB_SIGTERM_HELPER: PASS role=$ROLE allowed_uid=1000 denied_uid=1001"
}
helper_alive() {
    kill -0 "$HELPER_PID" 2>/dev/null && [ "$(awk '{print $22}' "/proc/$HELPER_PID/stat" 2>/dev/null)" = "$HELPER_START" ]
}
ordinary_identity() {
    kill -0 "$1"
    [ "$(readlink "/proc/$1/exe")" = /usr/bin/rustusb ]
    [ "$(awk '{print $22}' "/proc/$1/stat")" = "$2" ]
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$1/status")" = 1000:1000:1000:1000 ]
    for cap in CapEff CapPrm CapAmb; do [ "$(awk -v cap="$cap:" '$1 == cap {print $2}' "/proc/$1/status")" = 0000000000000000 ]; done
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
    echo 'RUSTUSB_SIGTERM_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600'
    chown 1000:1000 /run/sigterm
    chmod 700 /run/sigterm
    # A real stopped helper leaves this separate ordinary child awaiting Status.
    # A connected UDS/backlog is not evidence that the helper accepted a request.
    STAGE=startup-helper-wait
    [ ! -f /modules/unix_diag.ko ] || insmod /modules/unix_diag.ko
    helper_alive
    [ "$(readlink "/proc/$HELPER_PID/exe")" = /usr/bin/rustusb-helper ]
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$HELPER_PID/status")" = 0:0:0:0 ]
    kill -STOP "$HELPER_PID"
    for attempt in $(seq 1 60); do
        [ "$(awk '{print $3}' "/proc/$HELPER_PID/stat")" = T ] && break
        sleep 0.05
    done
    [ "$(awk '{print $3}' "/proc/$HELPER_PID/stat")" = T ]
    mkfifo -m 600 /run/sigterm/startup-control
    chown 1000:1000 /run/sigterm/startup-control
    exec 9<> /run/sigterm/startup-control
    (
        exec 9<&-
        exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb physical-server \
            --device-id "$HID_ID" --device-id "$FTDI_ID" --listen 192.0.2.1:3240 \
            --allow-insecure-test-network --allow-peer 192.0.2.2 --control-stdin \
            < /run/sigterm/startup-control > /tmp/startup.events 2> /tmp/startup.log
    ) &
    STARTUP_PID=$!
    STARTUP_START=$(awk '{print $22}' "/proc/$STARTUP_PID/stat")
    CONNECTED_INODE=
    CONNECTED_FD=
    for attempt in $(seq 1 100); do
        /usr/bin/ss -xneapH -O > /tmp/startup-ss.txt
        for fd in /proc/"$STARTUP_PID"/fd/*; do
            target=$(readlink "$fd" 2>/dev/null || true)
            case "$target" in
                socket:\[*\])
                    inode=${target#socket:[}; inode=${inode%]}
                    if awk -v inode="$inode" -v pid="$HELPER_PID" '$2=="LISTEN" && $5=="/run/rustusb/control.sock" && index($0,"pid=" pid ",") {for(i=1;i<=NF;i++) if($i=="peers:") for(j=i+1;j<=NF;j++) if($j==inode) found=1} END {exit !found}' /tmp/startup-ss.txt; then
                        CONNECTED_INODE=$inode; CONNECTED_FD=${fd##*/}; break
                    fi
                    ;;
            esac
        done
        [ -z "$CONNECTED_INODE" ] || break
        kill -0 "$STARTUP_PID"; sleep 0.05
    done
    [ -n "$CONNECTED_INODE" ]
    [ "$(readlink "/proc/$STARTUP_PID/fd/$CONNECTED_FD")" = "socket:[$CONNECTED_INODE]" ]
    awk -v inode="$CONNECTED_INODE" '$7==inode && $5=="0001" && $6=="03" {found=1} END {exit !found}' /proc/net/unix
    ordinary_identity "$STARTUP_PID" "$STARTUP_START"
    helper_alive
    [ "$(awk '{print $3}' "/proc/$HELPER_PID/stat")" = T ]
    [ ! -s /tmp/startup.events ]
    cp /proc/net/unix /tmp/startup-unix.txt
    cp /proc/net/tcp /tmp/startup-tcp.txt
    if [ -f /proc/net/tcp6 ]; then cp /proc/net/tcp6 /tmp/startup-tcp6.txt; else : > /tmp/startup-tcp6.txt; fi
    ! awk '$2 ~ /:0CA8$/ && $4=="0A" {found=1} END {exit !found}' /tmp/startup-tcp.txt /tmp/startup-tcp6.txt
    STARTUP_TICK=$(ticks); STARTUP_DEADLINE=$((STARTUP_TICK + 300))
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox kill -TERM "$STARTUP_PID"
    STARTUP_EXITED=false
    while [ "$(ticks)" -lt "$STARTUP_DEADLINE" ]; do
        state=$(awk '{print $3}' "/proc/$STARTUP_PID/stat" 2>/dev/null || true)
        if [ -z "$state" ] || [ "$state" = Z ]; then
            [ "$(ticks)" -gt "$STARTUP_DEADLINE" ] || STARTUP_EXITED=true
            break
        fi
        sleep 0.05
    done
    STARTUP_WAIT=null
    if [ "$STARTUP_EXITED" = true ]; then
        if wait "$STARTUP_PID"; then STARTUP_WAIT=0; else STARTUP_WAIT=$?; fi
    fi
    STARTUP_ELAPSED=$(( $(ticks) - STARTUP_TICK ))
    helper_alive
    kill -CONT "$HELPER_PID"
    sysfs_observation
    STARTUP_UNCHANGED=false
    if jq -e --slurpfile original /tmp/original-sysfs.json '. == $original[0]' /tmp/sysfs.json > /dev/null; then STARTUP_UNCHANGED=true; fi
    jq -Mcn --argjson pid "$STARTUP_PID" --argjson start "$STARTUP_START" --arg inode "$CONNECTED_INODE" --argjson fd "$CONNECTED_FD" --argjson helper_pid "$HELPER_PID" --argjson helper_start "$HELPER_START" --argjson exited "$STARTUP_EXITED" --argjson wait "$STARTUP_WAIT" --argjson elapsed "$STARTUP_ELAPSED" --argjson unchanged "$STARTUP_UNCHANGED" --slurpfile sysfs /tmp/sysfs.json --rawfile unix /tmp/startup-unix.txt --rawfile ss /tmp/startup-ss.txt --rawfile tcp /tmp/startup-tcp.txt --rawfile tcp6 /tmp/startup-tcp6.txt \
        '{schema_version:1,event:"startup_term_outcome",pid:$pid,starttime:$start,uid:1000,signal:15,signal_count:1,sender_uid:1000,helper_pid:$helper_pid,helper_starttime:$helper_start,helper_stopped_before_signal:true,connected_unix_inode:$inode,connected_unix_fd:$fd,unix_observation:$unix,unix_ss_observation:$ss,tcp_observation:$tcp,tcp6_observation:$tcp6,ready_before_signal:false,listener_before_signal:false,helper_acceptance_observed:false,wait_code:$wait,process_exit_observed:$exited,observation_centiseconds:$elapsed,observation_limit_seconds:3,helper_continued:true,sysfs_unchanged:$unchanged,sysfs:$sysfs[0]}' > /tmp/startup-outcome.json
    printf 'RUSTUSB_SIGTERM_STARTUP_OUTCOME '; cat /tmp/startup-outcome.json
    echo 'RUSTUSB_SIGTERM_STARTUP_EVENTS_BEGIN'; cat /tmp/startup.events; echo 'RUSTUSB_SIGTERM_STARTUP_EVENTS_END'
    echo 'RUSTUSB_SIGTERM_STARTUP_STDERR_BEGIN'; cat /tmp/startup.log; echo 'RUSTUSB_SIGTERM_STARTUP_STDERR_END'
    exec 9<&-
    # If a broken child still exists it may later bind3240: stop this run rather
    # than kill it and misattribute the subsequent main service's observations.
    [ "$STARTUP_EXITED" = true ] && [ "$STARTUP_UNCHANGED" = true ]
    STARTUP_PASS=true
    [ "$STARTUP_WAIT" = 0 ] || STARTUP_PASS=false
    unpriv --json doctor > /tmp/doctor-after-startup.json
    jq -e '.helper.status == "available" and .helper.peer_verified == true' /tmp/doctor-after-startup.json > /dev/null
    mkfifo -m 600 /run/sigterm/control
    chown 1000:1000 /run/sigterm/control
    exec 3<> /run/sigterm/control
    (
        exec 3<&-
        exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb physical-server \
            --device-id "$HID_ID" --device-id "$FTDI_ID" --listen 192.0.2.1:3240 \
            --allow-insecure-test-network --allow-peer 192.0.2.2 --control-stdin \
            < /run/sigterm/control > /tmp/server.events 2> /tmp/server.log
    ) &
    SERVER_PID=$!
    STAGE=server-ready
    for attempt in $(seq 1 100); do
        jq -se 'any(.[]; .event == "ready")' /tmp/server.events > /dev/null 2>&1 && break
        kill -0 "$SERVER_PID"; sleep 0.05
    done
    jq -se 'length == 1 and .[0].schema_version == 1 and .[0].event == "ready" and .[0].address == "192.0.2.1:3240"' /tmp/server.events > /dev/null
    SERVER_START=$(awk '{print $22}' "/proc/$SERVER_PID/stat")
    server_identity() {
        ordinary_identity "$SERVER_PID" "$SERVER_START"
        : > /tmp/fds.jsonl
        for node in "$HID_NODE" "$FTDI_NODE"; do
            found=false
            for fd in /proc/"$SERVER_PID"/fd/*; do
                if [ "$(readlink "$fd")" = "$node" ]; then
                    found=true
                    identity=$(stat -L -c '%d:%i:%t:%T' "$fd")
                    [ "$identity" = "$(stat -L -c '%d:%i:%t:%T' "$node")" ]
                    jq -Mcn --arg node "$node" --arg fd "${fd##*/}" --arg identity "$identity" '{node:$node,fd:($fd|tonumber),identity:$identity}' >> /tmp/fds.jsonl
                fi
            done
            [ "$found" = true ]
        done
        jq -s '.' /tmp/fds.jsonl > /tmp/fds.json
        jq -Mcn --argjson pid "$SERVER_PID" --argjson start "$SERVER_START" --slurpfile fds /tmp/fds.json \
            '{pid:$pid,starttime:$start,exe:"/usr/bin/rustusb",uids:[1000,1000,1000,1000],cap_effective:0,cap_permitted:0,cap_ambient:0,fds:$fds[0]}' > /tmp/identity.json
    }
    snapshot() {
        printf '{"id":"%s","op":"snapshot"}\n' "$1" | /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox tee /run/sigterm/control > /dev/null
        for attempt in $(seq 1 100); do
            jq -se --arg id "$1" 'any(.[]; .event == "snapshot" and .id == $id)' /tmp/server.events > /dev/null 2>&1 && break
            kill -0 "$SERVER_PID"; sleep 0.05
        done
        jq -se --arg id "$1" '[.[] | select(.id == $id)] | length == 1 and .[0].schema_version == 1 and .[0].event == "snapshot"' /tmp/server.events > /dev/null
        jq -s --arg id "$1" '.[] | select(.event == "snapshot" and .id == $id) | .snapshot' /tmp/server.events > /tmp/snapshot.json
        server_identity
    }
    record_observation() {
        sysfs_observation
        jq -Mcn --arg tag "$1" --slurpfile sysfs /tmp/sysfs.json --slurpfile snapshot /tmp/snapshot.json --slurpfile fds /tmp/fds.json --slurpfile identity /tmp/identity.json \
            '{"label":$tag,sysfs:$sysfs[0],snapshot:$snapshot[0],fds:$fds[0],identity:$identity[0]}' >> /tmp/observations.jsonl
    }
    : > /tmp/observations.jsonl
    snapshot baseline
    jq -e '.schema_version == 1 and (.devices|length)==2 and .buffer_bytes_reserved == 0 and all(.devices[]; .state == "available" and .leased == false and .cleanup_verified == true and .pending_urbs == 0 and .queued_urbs == 0 and .usbfs_inflight == 0 and .current_drivers == .original_drivers)' /tmp/snapshot.json > /dev/null
    record_observation baseline
    echo 'RUSTUSB_SIGTERM_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false'
    send_control 192.0.2.2 main-ready
    receive_control attached
    STAGE=two-held-imports
    snapshot held
    jq -e '(.devices|length)==2 and all(.devices[]; .state == "leased" and .leased == true and all(.current_drivers[]; .driver == "usbfs"))' /tmp/snapshot.json > /dev/null
    record_observation held
    send_control 192.0.2.2 io
    receive_control io-complete
    STAGE=real-pending-before-term
    PRE_ID=
    for poll in $(seq 1 100); do
        snapshot "pending-$poll"
        if jq -e '.buffer_bytes_reserved > 0 and (.devices|length)==2 and all(.devices[]; .state == "leased" and .leased == true and .pending_urbs >= 1 and .usbfs_inflight >= 1 and all(.current_drivers[]; .driver == "usbfs"))' /tmp/snapshot.json > /dev/null; then PRE_ID=pending-$poll; break; fi
        sleep 0.05
    done
    [ -n "$PRE_ID" ]
    cp /tmp/snapshot.json /tmp/pre-term.json
    record_observation term-pending
    jq -e --slurpfile original /tmp/original-sysfs.json 'length==2 and all(.[]; .interfaces == [{number:0,alternate:0,driver:"usbfs"}]) and (map(del(.interfaces)) == ($original[0]|map(del(.interfaces))))' /tmp/sysfs.json > /dev/null
    server_identity
    helper_alive
    echo "RUSTUSB_SIGTERM_HELD_PENDING: PASS leases=2 each_real_inflight=true buffer_reserved=true pid=$SERVER_PID starttime=$SERVER_START"
    STAGE=term-observation
    TERM_TICK=$(ticks)
    DEADLINE=$((TERM_TICK + 2000))
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox kill -TERM "$SERVER_PID"
    echo "RUSTUSB_SIGTERM_SENT: PASS signal=15 count=1 sender_uid=1000 pid=$SERVER_PID starttime=$SERVER_START"
    send_control 192.0.2.2 term-sent
    EXITED=false
    RESTORED=false
    WITHIN_WINDOW=false
    while [ "$(ticks)" -lt "$DEADLINE" ]; do
        state=$(awk '{print $3}' "/proc/$SERVER_PID/stat" 2>/dev/null || true)
        if [ -z "$state" ] || [ "$state" = Z ]; then EXITED=true; fi
        sysfs_observation
        if jq -e --slurpfile original /tmp/original-sysfs.json '. == $original[0]' /tmp/sysfs.json > /dev/null; then RESTORED=true; else RESTORED=false; fi
        if [ "$EXITED" = true ] && [ "$RESTORED" = true ]; then
            [ "$(ticks)" -gt "$DEADLINE" ] || WITHIN_WINDOW=true
            break
        fi
        sleep 0.05
    done
    WAIT_CODE=null
    if [ "$EXITED" = true ]; then
        # In particular, wait143 is data: do not let set-e skip recovery evidence.
        if wait "$SERVER_PID"; then WAIT_CODE=0; else WAIT_CODE=$?; fi
    fi
    PID_ABSENT=false; [ -e "/proc/$SERVER_PID" ] || PID_ABSENT=true
    FD_TABLE_ABSENT=false; [ -d "/proc/$SERVER_PID/fd" ] || FD_TABLE_ABSENT=true
    HELPER_ALIVE=false; if helper_alive; then HELPER_ALIVE=true; fi
    sysfs_observation
    if jq -e --slurpfile original /tmp/original-sysfs.json '. == $original[0]' /tmp/sysfs.json > /dev/null; then RESTORED=true; else RESTORED=false; fi
    jq -Mcn --argjson exited "$EXITED" --argjson restored "$RESTORED" --slurpfile sysfs /tmp/sysfs.json '{label:"post-term",process_exit_observed:$exited,restored_sysfs:$restored,sysfs:$sysfs[0],snapshot:null,snapshot_source:"see actual stopped event if present"}' >> /tmp/observations.jsonl
    jq -Mcn --argjson pid "$SERVER_PID" --argjson start "$SERVER_START" --argjson wait "$WAIT_CODE" --argjson exited "$EXITED" --argjson absent "$PID_ABSENT" --argjson fd_absent "$FD_TABLE_ABSENT" --argjson restored "$RESTORED" --argjson window "$WITHIN_WINDOW" --argjson helper "$HELPER_ALIVE" --arg pre "$PRE_ID" --argjson elapsed "$(( $(ticks) - TERM_TICK ))" \
        '{schema_version:1,event:"sigterm_outcome",role:"server",signal:15,signal_count:1,sender_uid:1000,pid:$pid,starttime:$start,wait_code:$wait,process_exit_observed:$exited,pid_absent:$absent,fd_table_absent:$fd_absent,restored_sysfs:$restored,within_observation_window:$window,helper_alive:$helper,pre_snapshot_id:$pre,observation_centiseconds:$elapsed,observation_limit_seconds:20,stdin_kept_open:true,shutdown_control_sent:false}' > /tmp/outcome.json
    echo 'RUSTUSB_SIGTERM_EVENTS_BEGIN'; cat /tmp/server.events; echo 'RUSTUSB_SIGTERM_EVENTS_END'
    echo 'RUSTUSB_SIGTERM_OBSERVATIONS_BEGIN'; cat /tmp/observations.jsonl; echo 'RUSTUSB_SIGTERM_OBSERVATIONS_END'
    echo 'RUSTUSB_SIGTERM_STDERR_BEGIN'; cat /tmp/server.log; echo 'RUSTUSB_SIGTERM_STDERR_END'
    printf 'RUSTUSB_SIGTERM_OUTCOME '; cat /tmp/outcome.json
    echo 'RUSTUSB_SIGTERM_COLLECTION_COMPLETE role=server'
    send_control 192.0.2.2 server-observed
    receive_control client-observed
    PASS=$STARTUP_PASS
    if ! jq -e '.wait_code == 0 and .process_exit_observed and .pid_absent and .fd_table_absent and .restored_sysfs and .within_observation_window and .helper_alive' /tmp/outcome.json > /dev/null; then PASS=false; fi
    if ! jq -se 'all(.[]; .schema_version == 1 and .event != "error") and ([.[] | select(.event == "stopped")]|length)==1 and .[-1].event == "stopped" and .[-1].snapshot.buffer_bytes_reserved == 0 and (.[-1].snapshot.devices|length)==2 and all(.[-1].snapshot.devices[]; .state=="available" and .cleanup_verified and (.leased|not) and .pending_urbs==0 and .queued_urbs==0 and .usbfs_inflight==0 and .current_drivers==.original_drivers)' /tmp/server.events > /dev/null; then PASS=false; fi
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
    echo 'RUSTUSB_SIGTERM_WAIT_INPUT key=1'
    echo 'RUSTUSB_SIGTERM_WAIT_SERIAL bytes=35'
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
    echo 'RUSTUSB_SIGTERM_REAL_IO: PASS key_code=2 down=true up=true serial_in=35 serial_out=35 same_receipts=true readers_kept_open=true'
    send_control 192.0.2.1 io-complete
    receive_control term-sent
    STAGE=client-term-observation
    TERM_TICK=$(ticks); DEADLINE=$((TERM_TICK + 2000))
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
    for status_file in /sys/devices/platform/vhci_hcd.*/status /sys/devices/platform/vhci_hcd.*/status.[0-9]*; do
        [ ! -f "$status_file" ] || { printf 'STATUS %s\n' "$status_file" >> /tmp/vhci.txt; cat "$status_file" >> /tmp/vhci.txt; }
    done
    HID_GONE=false; [ -e "/sys/bus/usb/devices/$HID_LOCAL" ] || HID_GONE=true
    FTDI_GONE=false; [ -e "/sys/bus/usb/devices/$FTDI_LOCAL" ] || FTDI_GONE=true
    HELPER_ALIVE=false; if helper_alive; then HELPER_ALIVE=true; fi
    rx_sha=$(sha256sum /tmp/rx.bin | cut -d' ' -f1)
    tx_sha=$(sha256sum /tmp/tx.bin | cut -d' ' -f1)
    input_hex=$(od -An -tx1 -v /tmp/input.bin | tr -d ' \n')
    jq -Mcn --argjson disconnected "$DISCONNECTED" --argjson hid_gone "$HID_GONE" --argjson ftdi_gone "$FTDI_GONE" --argjson helper "$HELPER_ALIVE" --slurpfile hid /tmp/attach-hid.json --slurpfile ftdi /tmp/attach-ftdi.json --slurpfile sessions /tmp/sessions-after.json --rawfile vhci /tmp/vhci.txt --arg rx "$rx_sha" --arg tx "$tx_sha" --arg input "$input_hex" --argjson elapsed "$(( $(ticks) - TERM_TICK ))" \
        '{schema_version:1,event:"sigterm_outcome",role:"client",spontaneous_disconnect:$disconnected,hid_generation_gone:$hid_gone,ftdi_generation_gone:$ftdi_gone,helper_alive:$helper,receipts:[$hid[0],$ftdi[0]],sessions:$sessions[0],vhci_status:$vhci,serial_in_bytes:35,serial_out_bytes:35,rx_sha256:$rx,tx_sha256:$tx,input_event_hex:$input,input_event_format:"linux_x86_64_24",observation_centiseconds:$elapsed,observation_limit_seconds:20,detach_before_observation:false}' > /tmp/outcome.json
    printf 'RUSTUSB_SIGTERM_OUTCOME '; cat /tmp/outcome.json
    echo 'RUSTUSB_SIGTERM_COLLECTION_COMPLETE role=client'
    receive_control server-observed
    send_control 192.0.2.1 client-observed
    # Only after spontaneous observations may test-owned cache entries/readers go.
    kill "$INPUT_READER" 2>/dev/null || true
    wait "$INPUT_READER" 2>/dev/null || true
    exec 4<&-
    PASS=true
    if [ "$DISCONNECTED" = true ]; then
        if ! unpriv --json detach "$(jq -r '.port' /tmp/attach-hid.json)"; then PASS=false; fi
        if ! unpriv --json detach "$(jq -r '.port' /tmp/attach-ftdi.json)"; then PASS=false; fi
    else PASS=false; fi
    [ "$HELPER_ALIVE" = true ] || PASS=false
    receive_control finished
fi
if [ "$PASS" = true ]; then
    echo "RUSTUSB_SIGTERM_RESULT: PASS role=$ROLE"
else
    echo "RUSTUSB_SIGTERM_RESULT: FAIL role=$ROLE stage=cooperative-stop observation_complete=true"
fi
