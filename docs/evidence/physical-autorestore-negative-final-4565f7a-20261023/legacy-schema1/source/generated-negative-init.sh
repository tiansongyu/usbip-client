#!/bin/sh
# Disposable guest only: durable grant records, normal finish and explicit administrative reconciliation.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || {
    echo 'Refusing to run outside the disposable QEMU guest init' >&2; exit 2;
}
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.journal_test=1 '*) ;; *) exit 2 ;; esac
AUTORESTORE_SCENARIO=owner-death
NEGATIVE_SCENARIO=legacy-schema1
ROLE=unknown
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in rustusb.role=*) ROLE=${parameter#*=} ;; esac
done
[ "$ROLE" = server ] || [ "$ROLE" = client ]
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/journal /etc/rustusb /var/run/vhci_hcd
mount -t devpts devpts /dev/pts
STAGE=boot
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_JOURNAL_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        for file in /tmp/helper.log /tmp/initial.log /tmp/initial.events /tmp/primary-outcome.json /tmp/restart.log /tmp/restart.events /tmp/restart-outcome.json /tmp/observations.jsonl /tmp/devices.json /tmp/sessions.json /tmp/doctor.json /tmp/denied.log /tmp/denied-open.json /tmp/outcome.json /tmp/journal-observations.jsonl /tmp/waits.jsonl /tmp/admin-responses.jsonl /tmp/client-outcomes.jsonl /tmp/helper-before-restart.log /tmp/autorestore-metadata.json /tmp/autorestore-helper-logs.jsonl /tmp/autorestore-readonly.jsonl /tmp/autorestore-primary.json /tmp/autorestore-recovery-window.json /tmp/autorestore-live-window.json /tmp/autorestore-live-disk.json /tmp/autorestore-killed-disk.json /tmp/autorestore-interfaces.json /tmp/autorestore-disk.json /tmp/negative-metadata.json /tmp/negative-helper-starts.jsonl /tmp/negative-helper-logs.jsonl /tmp/negative-readonly.jsonl /tmp/negative-windows.jsonl /tmp/negative-policies.jsonl /tmp/negative-killed-disk.json /tmp/negative-operations.json /tmp/negative-c.log /tmp/negative-c-events.jsonl /tmp/negative-c-identity.json /tmp/negative-helper-1.log /tmp/negative-helper-2.log /tmp/negative-helper-3.log; do
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
echo "RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role=$ROLE device=ttyS1 kernel_console=ttyS0"
echo "RUSTUSB_JOURNAL_BOOT role=$ROLE kernel=$(uname -r)"
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
echo "RUSTUSB_JOURNAL_ISOLATION: PASS role=$ROLE carrier=1 ip_forward=0"
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
    MESSAGE=$(timeout 60 nc -l -p 5001)
    [ "$MESSAGE" = "$1" ] || { echo "unexpected control message: $MESSAGE expected=$1"; return 1; }
}
start_helper() {
    chmod 644 /etc/rustusb/policy.json
    (exec 3<&- 9<&-; exec /usr/bin/rustusb-helper > /tmp/helper.log 2>&1) &
    HELPER_PID=$!
    for attempt in $(seq 1 100); do [ ! -S /run/rustusb/control.sock ] || break; sleep 0.05; done
    [ -S /run/rustusb/control.sock ]
    HELPER_START=$(awk '{print $22}' "/proc/$HELPER_PID/stat")
    unpriv --json doctor > /tmp/doctor.json
    jq -e '.schema_version == 1 and .helper.status == "available" and .helper.peer_verified == true and .network_security_verified == false and .hardware_test_performed == false and .built_in_encryption == false' /tmp/doctor.json > /dev/null
    if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb --json sessions > /tmp/denied.log 2>&1; then return 1; fi
    grep -F 'caller UID is not approved by the administrator' /tmp/denied.log > /dev/null
    echo "RUSTUSB_JOURNAL_HELPER: PASS role=$ROLE allowed_uid=1000 denied_uid=1001"
}
helper_alive() {
    kill -0 "$HELPER_PID" 2>/dev/null && [ "$(awk '{print $22}' "/proc/$HELPER_PID/stat" 2>/dev/null)" = "$HELPER_START" ]
}
ordinary_identity() {
    kill -0 "$1" || return 1
    [ "$(readlink "/proc/$1/exe")" = /usr/bin/rustusb ] || return 1
    [ "$(awk '{print $22}' "/proc/$1/stat")" = "$2" ] || return 1
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$1/status")" = 1000:1000:1000:1000 ] || return 1
    for cap in CapEff CapPrm CapAmb; do
        [ "$(awk -v cap="$cap:" '$1 == cap {print $2}' "/proc/$1/status")" = 0000000000000000 ] || return 1
    done
}
admin_refusal() {
    [ "$1" != 0 ] || return 1
    # A nonzero exit alone could be clap, socket, or I/O failure. Require the
    # actual machine-readable product error for this precise refusal boundary.
    jq -se --arg expected "$2" 'length==1 and (.[0] | type=="object" and .ok==false and (.error|type)=="string" and (.error|contains($expected)))' /tmp/api.json > /dev/null || return 1
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
    . /usr/lib/guest-autorestore-fragments.sh
    . /usr/lib/guest-autorestore-negative-fragments.sh
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
    echo 'RUSTUSB_JOURNAL_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600'
    chown 1000:1000 /run/journal
    chmod 700 /run/journal
    launch_server() {
        EPOCH=$1
        CONTROL=/run/journal/$EPOCH.control
        EVENTS=/tmp/$EPOCH.events
        mkfifo -m 600 "$CONTROL"
        chown 1000:1000 "$CONTROL"
        exec 3<> "$CONTROL"
        (
            exec 3<&- 9<&-
            exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb physical-server \
                --device-id "$HID_ID" --device-id "$FTDI_ID" --listen 192.0.2.1:3240 \
                --allow-insecure-test-network --allow-peer 192.0.2.2 --control-stdin \
                < "$CONTROL" > "$EVENTS" 2> "/tmp/$EPOCH.log"
        ) &
        SERVER_PID=$!
        SERVER_START=$(awk '{print $22}' "/proc/$SERVER_PID/stat" 2>/dev/null || true)
    }
    exited() {
        state=$(awk '{print $3}' "/proc/$1/stat" 2>/dev/null || true)
        [ -z "$state" ] || [ "$state" = Z ]
    }
    server_identity() {
        ordinary_identity "$SERVER_PID" "$SERVER_START" || return 1
        : > /tmp/fds.jsonl || return 1
        for node in "$HID_NODE" "$FTDI_NODE"; do
            found=false
            for fd in /proc/"$SERVER_PID"/fd/*; do
                if [ "$(readlink "$fd")" = "$node" ]; then
                    found=true
                    identity=$(stat -L -c '%d:%i:%t:%T' "$fd") || return 1
                    node_identity=$(stat -L -c '%d:%i:%t:%T' "$node") || return 1
                    [ "$identity" = "$node_identity" ] || return 1
                    jq -Mcn --arg node "$node" --arg fd "${fd##*/}" --arg identity "$identity" '{node:$node,fd:($fd|tonumber),identity:$identity}' >> /tmp/fds.jsonl || return 1
                fi
            done
            [ "$found" = true ] || return 1
        done
        jq -s '.' /tmp/fds.jsonl > /tmp/fds.json || return 1
        jq -Mcn --argjson pid "$SERVER_PID" --argjson start "$SERVER_START" --slurpfile fds /tmp/fds.json \
            '{pid:$pid,starttime:$start,exe:"/usr/bin/rustusb",uids:[1000,1000,1000,1000],cap_effective:0,cap_permitted:0,cap_ambient:0,fds:$fds[0]}' > /tmp/identity.json || return 1
    }
    control() {
        printf '{"id":"%s","op":"%s"}\n' "$1" "$2" | timeout 3 /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox tee "$CONTROL" > /dev/null || return 1
        until_tick=$(( $(ticks) + 500 ))
        while [ "$(ticks)" -lt "$until_tick" ]; do
            if jq -se --arg id "$1" 'any(.[]; .id==$id and (.event=="snapshot" or (.event=="ack" and .ok==true)))' "$EVENTS" > /dev/null 2>&1; then return 0; fi
            if exited "$SERVER_PID"; then return 1; fi
            sleep 0.05
        done
        return 1
    }
    snapshot() {
        control "$1" snapshot
        jq -se --arg id "$1" '[.[]|select(.id==$id)] | length==1 and .[0].schema_version==1 and .[0].event=="snapshot"' "$EVENTS" > /dev/null
        jq -s --arg id "$1" '.[]|select(.id==$id and .event=="snapshot")|.snapshot' "$EVENTS" > /tmp/snapshot.json
        server_identity
    }
    observation() {
        sysfs_observation
        jq -Mcn --arg tag "$1" --arg epoch "$EPOCH" --slurpfile sysfs /tmp/sysfs.json --slurpfile snapshot /tmp/snapshot.json --slurpfile identity /tmp/identity.json \
            '{"label":$tag,epoch:$epoch,sysfs:$sysfs[0],snapshot:$snapshot[0],identity:$identity[0]}' >> /tmp/observations.jsonl
    }
    record() {
        sysfs_observation
        /usr/bin/rustusb --json physical-pending > /tmp/pending.json
        jq -e '.schema_version==1 and (.records|type)=="array" and .next_after==null' /tmp/pending.json > /dev/null
        jq -e 'type=="object"' /var/lib/rustusb/physical/journal.json > /dev/null
        dir_mode=$(stat -c '%u:%a' /var/lib/rustusb/physical)
        file_mode=$(stat -c '%u:%a' /var/lib/rustusb/physical/journal.json)
        journal_sha=$(sha256sum /var/lib/rustusb/physical/journal.json | cut -d' ' -f1)
        [ "$dir_mode:$file_mode" = 0:700:0:600 ]
        jq -Mcn --arg tag "$1" --arg dm "$dir_mode" --arg fm "$file_mode" --arg sha "$journal_sha" --slurpfile sysfs /tmp/sysfs.json --slurpfile pending /tmp/pending.json --slurpfile disk /var/lib/rustusb/physical/journal.json \
            '{label:$tag,sysfs:$sysfs[0],pending:$pending[0],journal:$disk[0],journal_directory_owner_mode:$dm,journal_file_owner_mode:$fm,journal_sha256:$sha}' >> /tmp/journal-observations.jsonl
        printf 'RUSTUSB_JOURNAL_RECORD '; tail -n 1 /tmp/journal-observations.jsonl
        echo "RUSTUSB_JOURNAL_PENDING_BEGIN label=$1"; cat /tmp/pending.json; echo "RUSTUSB_JOURNAL_PENDING_END label=$1"
        echo "RUSTUSB_JOURNAL_DISK_BEGIN label=$1"; cat /var/lib/rustusb/physical/journal.json; echo "RUSTUSB_JOURNAL_DISK_END label=$1"
    }
    records_count() { jq -e --argjson n "$1" '(.records|length)==$n' /tmp/pending.json > /dev/null; }
    normal_stop() {
        control stop shutdown
        until_tick=$(( $(ticks) + 2000 ))
        while [ "$(ticks)" -lt "$until_tick" ]; do exited "$SERVER_PID" && break; sleep 0.05; done
        exited "$SERVER_PID"
        if wait "$SERVER_PID"; then code=0; else code=$?; fi
        jq -Mcn --arg epoch "$EPOCH" --argjson code "$code" '{label:$epoch,event:"server_wait",wait_code:$code,signal:null}' >> /tmp/waits.jsonl
        printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl
        [ "$code" = 0 ]
        jq -se '([.[]|select(.event=="stopped")]|length)==1 and .[-1].event=="stopped" and .[-1].snapshot.buffer_bytes_reserved==0 and (. [-1].snapshot.devices|length)==2 and all(.[-1].snapshot.devices[];.state=="available" and (.leased|not) and .cleanup_verified and .pending_urbs==0 and .queued_urbs==0 and .usbfs_inflight==0 and .current_drivers==.original_drivers)' "$EVENTS" > /dev/null
        dump_epoch
    }
    dump_epoch() {
        echo "RUSTUSB_JOURNAL_EVENTS_BEGIN epoch=$EPOCH"; cat "$EVENTS"; echo "RUSTUSB_JOURNAL_EVENTS_END epoch=$EPOCH"
        echo "RUSTUSB_JOURNAL_STDERR_BEGIN epoch=$EPOCH"; cat "/tmp/$EPOCH.log"; echo "RUSTUSB_JOURNAL_STDERR_END epoch=$EPOCH"
    }
    start_ready() {
        launch_server "$1"
        for attempt in $(seq 1 100); do
            jq -se 'any(.[];.event=="ready")' "$EVENTS" > /dev/null 2>&1 && break
            kill -0 "$SERVER_PID"; sleep 0.05
        done
        jq -se 'length==1 and .[0].schema_version==1 and .[0].event=="ready" and .[0].address=="192.0.2.1:3240"' "$EVENTS" > /dev/null
        snapshot ready
        jq -e '.buffer_bytes_reserved==0 and (.devices|length)==2 and all(.devices[];.state=="available" and (.leased|not) and .cleanup_verified and .pending_urbs==0 and .queued_urbs==0 and .usbfs_inflight==0 and .current_drivers==.original_drivers)' /tmp/snapshot.json > /dev/null
        observation "$1-ready"
        record "$1-ready"
        records_count 2
    }
    actual_pending() {
        PRE_ID=
        for poll in $(seq 1 100); do
            snapshot "pending-$poll"
            if jq -e '.buffer_bytes_reserved>0 and (.devices|length)==2 and all(.devices[];.state=="leased" and .leased and .pending_urbs>=1 and .usbfs_inflight>=1 and all(.current_drivers[];.driver=="usbfs"))' /tmp/snapshot.json > /dev/null; then PRE_ID=pending-$poll; break; fi
            sleep 0.05
        done
        [ -n "$PRE_ID" ]
        observation "$EPOCH-pending"
        record "$EPOCH-pending"
        records_count 2
    }
    refused_server() {
        launch_server "$1"
        until_tick=$(( $(ticks) + 1000 ))
        while [ "$(ticks)" -lt "$until_tick" ]; do exited "$SERVER_PID" && break; sleep 0.05; done
        exited "$SERVER_PID"
        if wait "$SERVER_PID"; then code=0; else code=$?; fi
        jq -Mcn --arg epoch "$EPOCH" --argjson code "$code" '{label:$epoch,event:"server_wait",wait_code:$code,signal:null}' >> /tmp/waits.jsonl
        printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl
        [ "$code" != 0 ]
        [ ! -s "$EVENTS" ] || jq -se 'all(.[];.event!="ready")' "$EVENTS" > /dev/null
        [ -s "/tmp/$EPOCH.log" ]
        grep -F 'unresolved physical grant blocks new authorization; inspect physical-pending and reconcile the original baseline' "/tmp/$EPOCH.log" > /dev/null
        dump_epoch
        record "$EPOCH-refused"
        records_count 2
        jq -e --slurpfile before /tmp/killed-pending.json '.records==$before[0].records' /tmp/pending.json > /dev/null
    }
    : > /tmp/journal-observations.jsonl
    : > /tmp/waits.jsonl
    : > /tmp/observations.jsonl
    : > /tmp/admin-responses.jsonl
    negative_begin
    record initial
    records_count 0
    if /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/busybox head -c 1 /var/lib/rustusb/physical/journal.json > /tmp/journal-open.log 2>&1; then exit 1; fi
    echo 'RUSTUSB_JOURNAL_DISK_GATE: PASS ordinary_uid1000=denied directory=root0700 file=root0600'
    STAGE=normal-service
    start_ready normal
    echo 'RUSTUSB_JOURNAL_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false'
    send_control 192.0.2.2 cycle-1-ready
    receive_control cycle-1-io
    actual_pending
    normal_stop
    record normal-closed
    records_count 0
    jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json > /dev/null
    send_control 192.0.2.2 cycle-1-stopped
    receive_control cycle-1-observed
    STAGE=normal-reopen-without-import
    start_ready reopened
    normal_stop
    record reopened-closed
    records_count 0
    jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json > /dev/null
    negative_killed_epoch
    STAGE=authorized-after-automatic-recovery
    start_ready recovered
    send_control 192.0.2.2 cycle-3-ready
    receive_control cycle-3-io
    actual_pending
    normal_stop
    record recovered-closed
    records_count 0
    jq -e --slurpfile original /tmp/original-sysfs.json '.==$original[0]' /tmp/sysfs.json > /dev/null
    send_control 192.0.2.2 cycle-3-stopped
    receive_control cycle-3-observed
    helper_alive
    autorestore_helper_log final
    echo 'RUSTUSB_JOURNAL_OBSERVATIONS_BEGIN'; cat /tmp/observations.jsonl; echo 'RUSTUSB_JOURNAL_OBSERVATIONS_END'
    echo 'RUSTUSB_JOURNAL_RECORDS_BEGIN'; cat /tmp/journal-observations.jsonl; echo 'RUSTUSB_JOURNAL_RECORDS_END'
    echo 'RUSTUSB_JOURNAL_WAITS_BEGIN'; cat /tmp/waits.jsonl; echo 'RUSTUSB_JOURNAL_WAITS_END'
    echo 'RUSTUSB_JOURNAL_ADMIN_BEGIN'; cat /tmp/admin-responses.jsonl; echo 'RUSTUSB_JOURNAL_ADMIN_END'
    echo 'RUSTUSB_JOURNAL_COLLECTION_COMPLETE role=server'
    send_control 192.0.2.2 finished
else
    STAGE=client-helper
    jq -n '{allowed_uids:[1000],export_device_ids:[],peers:[{host:"192.0.2.1",port:3240}],trusted_network:true}' > /etc/rustusb/policy.json
    start_helper
    jq -e '.helper.kernel_available==true and .native.vhci_hcd.sysfs.status=="available"' /tmp/doctor.json > /dev/null
    : > /tmp/client-outcomes.jsonl
    for cycle in 1 2 3; do
        receive_control "cycle-$cycle-ready"
        STAGE="client-cycle-$cycle-attach"
        for attempt in $(seq 1 150); do
            if unpriv --json --timeout 2 discover 192.0.2.1 > /tmp/discover.json 2>/dev/null && jq -e 'length==2 and any(.[];.vendor_id==1575 and .product_id==1) and any(.[];.vendor_id==1027 and .product_id==24577)' /tmp/discover.json > /dev/null; then break; fi
            sleep 0.05
        done
        jq -e 'length==2' /tmp/discover.json > /dev/null
        HID_BUS=$(jq -r '.[]|select(.vendor_id==1575)|.bus_id' /tmp/discover.json)
        FTDI_BUS=$(jq -r '.[]|select(.vendor_id==1027)|.bus_id' /tmp/discover.json)
        unpriv --json attach 192.0.2.1 "$HID_BUS" > /tmp/attach-hid.json
        unpriv --json attach 192.0.2.1 "$FTDI_BUS" > /tmp/attach-ftdi.json
        HID_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-hid.json)
        FTDI_LOCAL=$(jq -r '.local_bus_id' /tmp/attach-ftdi.json)
        for file in /tmp/attach-hid.json /tmp/attach-ftdi.json; do
            local=$(jq -r '.local_bus_id' "$file")
            [ "$(stat -L -c '%d:%i' "/sys/bus/usb/devices/$local")" = "$(jq -r '.local_device_generation|"\(.filesystem_device):\(.inode)"' "$file")" ]
            [ "$(cat "/sys/bus/usb/devices/$local/devnum"|trim)" = "$(jq -r '.local_device_address' "$file")" ]
        done
        EVENT=; TTY=
        for attempt in $(seq 1 100); do
            EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d|head -1)
            TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$FTDI_LOCAL")" -name 'ttyUSB*' -type d|head -1)
            [ -z "$EVENT" ] || [ -z "$TTY" ] || break
            sleep 0.05
        done
        [ -n "$EVENT" ] && [ -n "$TTY" ]
        : > /tmp/input.bin
        exec 3< "/dev/input/$(basename "$EVENT")"
        cat <&3 > /tmp/input.bin & INPUT_READER=$!
        exec 3<&-
        TTY=/dev/$(basename "$TTY")
        stty -F "$TTY" raw -echo 115200 clocal -hupcl
        exec 4<> "$TTY"
        if [ "$AUTORESTORE_SCENARIO" = helper-restart ] && [ "$cycle" = 2 ]; then
            send_control 192.0.2.1 cycle-2-attached
            receive_control cycle-2-io-allowed
        fi
        timeout 8 dd bs=1 count=35 <&4 > /tmp/rx.bin 2> /tmp/dd.log & SERIAL_READER=$!
        probe busy "$HID_BUS" "$FTDI_BUS"
        STAGE="client-cycle-$cycle-io"
        echo "RUSTUSB_JOURNAL_WAIT_INPUT key=$cycle"
        echo "RUSTUSB_JOURNAL_WAIT_SERIAL cycle=$cycle bytes=35"
        wait "$SERIAL_READER"
        printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$cycle" > /tmp/expected.bin
        [ "$(wc -c < /tmp/rx.bin)" = 35 ] && cmp /tmp/rx.bin /tmp/expected.bin
        printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$cycle" > /tmp/tx.bin
        cat /tmp/tx.bin >&4
        keycode=$((cycle + 1)); KEY_SEEN=false
        for attempt in $(seq 1 100); do
            bytes=$(wc -c < /tmp/input.bin)
            if [ "$bytes" -gt 0 ] && [ "$((bytes % 24))" = 0 ] && od -An -tu4 -w24 -v /tmp/input.bin|awk -v code="$((keycode * 65536 + 1))" 'NF==6 && $5==code && $6==1 {down=1} NF==6 && $5==code && $6==0 {up=1} END {exit !(down&&up)}'; then KEY_SEEN=true; break; fi
            sleep 0.05
        done
        [ "$KEY_SEEN" = true ]
        unpriv --json sessions > /tmp/sessions.json
        jq -e --slurpfile hid /tmp/attach-hid.json --slurpfile ftdi /tmp/attach-ftdi.json 'length==2 and all(.[];.managed and .session.status==6) and any(.[];.session==$hid[0]) and any(.[];.session==$ftdi[0])' /tmp/sessions.json > /dev/null
        send_control 192.0.2.1 "cycle-$cycle-io"
        if [ "$cycle" = 2 ]; then receive_control cycle-2-killed; else receive_control "cycle-$cycle-stopped"; fi
        STAGE="client-cycle-$cycle-disconnect"
        until_tick=$(( $(ticks) + 2000 ))
        DISCONNECTED=false
        while [ "$(ticks)" -lt "$until_tick" ]; do
            if timeout 2 /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json sessions > /tmp/after.json 2>/tmp/session-error.log && jq -e 'length==0' /tmp/after.json > /dev/null && [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$FTDI_LOCAL" ]; then DISCONNECTED=true; break; fi
            sleep 0.05
        done
        : > /tmp/vhci.txt
        for file in /sys/devices/platform/vhci_hcd.*/status /sys/devices/platform/vhci_hcd.*/status.[0-9]*; do [ ! -f "$file" ] || { printf 'STATUS %s\n' "$file" >> /tmp/vhci.txt; cat "$file" >> /tmp/vhci.txt; }; done
        input_hex=$(od -An -tx1 -v /tmp/input.bin|tr -d ' \n'); rx_hex=$(od -An -tx1 -v /tmp/rx.bin|tr -d ' \n'); tx_hex=$(od -An -tx1 -v /tmp/tx.bin|tr -d ' \n')
        jq -Mcn --argjson cycle "$cycle" --argjson disconnected "$DISCONNECTED" --slurpfile hid /tmp/attach-hid.json --slurpfile ftdi /tmp/attach-ftdi.json --slurpfile sessions /tmp/after.json --rawfile vhci /tmp/vhci.txt --arg input "$input_hex" --arg rx "$rx_hex" --arg tx "$tx_hex" '{cycle:$cycle,spontaneous_disconnect:$disconnected,receipts:[$hid[0],$ftdi[0]],sessions:$sessions[0],vhci_status:$vhci,input_event_hex:$input,rx_hex:$rx,tx_hex:$tx,detach_before_observation:false}' >> /tmp/client-outcomes.jsonl
        [ "$DISCONNECTED" = true ]
        if [ "$cycle" = 2 ]; then receive_control cycle-2-server-observed; fi
        kill "$INPUT_READER" 2>/dev/null || true; wait "$INPUT_READER" 2>/dev/null || true
        exec 4<&-
        unpriv --json detach "$(jq -r '.port' /tmp/attach-hid.json)"
        unpriv --json detach "$(jq -r '.port' /tmp/attach-ftdi.json)"
        send_control 192.0.2.1 "cycle-$cycle-observed"
    done
    receive_control finished
    helper_alive
    echo 'RUSTUSB_JOURNAL_CLIENT_BEGIN'; cat /tmp/client-outcomes.jsonl; echo 'RUSTUSB_JOURNAL_CLIENT_END'
    echo 'RUSTUSB_JOURNAL_COLLECTION_COMPLETE role=client'
fi
echo "RUSTUSB_JOURNAL_RESULT: PASS role=$ROLE"
