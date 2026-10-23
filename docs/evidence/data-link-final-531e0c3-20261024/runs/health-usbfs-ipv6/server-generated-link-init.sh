#!/bin/sh
# Disposable PID1 only. Host coordination uses ttyS2, never the external NIC.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || exit 2
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.link_health=1 '*) ;; *) exit 2 ;; esac
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
        echo "RUSTUSB_LINK_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        failure_journal native-channels /var/lib/rustusb/native/channels.json
        failure_journal agent-state /var/lib/rustusb/agent/state.json
        failure_journal physical-journal /var/lib/rustusb/physical/journal.json
        dmesg > /tmp/failure-dmesg.log
        cat /proc/net/tcp /proc/net/tcp6 > /tmp/failure-tcp.log
    fi
    dump_artifacts
    artifact_status=$?
    if [ "$artifact_status" != 0 ]; then
        echo "RUSTUSB_LINK_RESULT: FAIL role=$ROLE stage=artifact-dump original_exit=$status artifact_exit=$artifact_status"
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
    # shellcheck disable=SC2034
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

case " $(cat /proc/cmdline) " in *' rustusb.link_health=1 '*) ;; *) exit 2 ;; esac
AGENT_COMMON=1
#!/bin/sh
# Composed after the audited secure harness PID1/network/artifact helpers.
# This fragment cannot run alone and exposes no production test-input RPC.
[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = usbfs ] || exit 2
agent() { (exec 5<&-; /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json "$@"); }
agent_other() { (exec 5<&-; /usr/bin/setpriv --reuid=1002 --regid=1002 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json "$@"); }
# Read-only configuration evidence, captured after each actual agent startup.
# Paths below are fixed by this disposable harness, never accepted from RPC.
config_evidence() {
    CONFIG_MARK=$1
    CONFIG_FILES='/etc/rustusb/agent.json /etc/rustusb/policy.json /etc/rustusb/ca.der /etc/rustusb/server.der /etc/rustusb/client.der /etc/rustusb/client-pin.txt'
    if [ "$ROLE" = server ]; then
        CONFIG_FILES="$CONFIG_FILES /etc/rustusb/server.json"
    else
        CONFIG_FILES="$CONFIG_FILES /etc/rustusb/client-hid.json /etc/rustusb/client-serial.json"
    fi
    : > "/tmp/config-metadata-$CONFIG_MARK.jsonl"
    for config_path in / /etc /etc/rustusb /run /run/agent /var /var/lib /var/lib/rustusb /var/lib/rustusb/agent $CONFIG_FILES "/etc/rustusb/$ROLE-key.der"; do
        [ ! -L "$config_path" ] && [ -e "$config_path" ]
        config_copy=''
        if [ -d "$config_path" ]; then
            config_kind=directory
        elif [ "$config_path" = "/etc/rustusb/$ROLE-key.der" ]; then
            [ -f "$config_path" ]
            config_kind=private_key_metadata_only
            # Deliberately no cat/cp/hash/read of private key bytes.
        else
            [ -f "$config_path" ]
            config_kind=public_file
            config_copy="actual-config-$CONFIG_MARK-$(basename "$config_path")"
            cp "$config_path" "/tmp/$config_copy"
        fi
        jq -nc --arg path "$config_path" --arg kind "$config_kind" --arg copy "$config_copy" \
            --arg canonical "$(readlink -f "$config_path")" --arg mode "$(stat -c '%a' "$config_path")" \
            --argjson uid "$(stat -c '%u' "$config_path")" --argjson gid "$(stat -c '%g' "$config_path")" \
            --argjson links "$(stat -c '%h' "$config_path")" --argjson bytes "$(stat -c '%s' "$config_path")" \
            --argjson device "$(stat -c '%d' "$config_path")" --argjson inode "$(stat -c '%i' "$config_path")" \
            '{path:$path,kind:$kind,canonical:$canonical,uid:$uid,gid:$gid,mode:$mode,links:$links,bytes:$bytes,device:$device,inode:$inode,captured_file:(if $copy=="" then null else $copy end)}' >> "/tmp/config-metadata-$CONFIG_MARK.jsonl"
    done
    jq -s 'sort_by(.path)' "/tmp/config-metadata-$CONFIG_MARK.jsonl" > "/tmp/config-metadata-$CONFIG_MARK.json"
    echo "AGENT_CONFIG_CAPTURED role=$ROLE epoch=$CONFIG_MARK private_key_bytes=false"
}

agent_snapshot() {
    cp /var/lib/rustusb/agent/state.json "/tmp/agent-$1.json"
    cp /var/lib/rustusb/native/channels.json "/tmp/root-$1.json"
    if [ "$ROLE" = client ]; then native_snapshot "/tmp/vhci-$1.txt"; else host_baseline "/tmp/host-$1.json"; fi
    echo "AGENT_SNAPSHOT role=$ROLE mark=$1"
}
agent_start() {
    AGENT_EPOCH=$1
    (exec 5<&-; exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run > "/tmp/agent-$AGENT_EPOCH.stdout" 2> "/tmp/agent-$AGENT_EPOCH.log") &
    AGENT_PID=$!
    # shellcheck disable=SC2034
    for agent_poll in $(seq 1 200); do
        if agent profiles > "/tmp/profiles-$AGENT_EPOCH.json" 2> "/tmp/profiles-$AGENT_EPOCH.log"; then break; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    jq -e '.schema_version==1 and .journal_healthy==true and .response.result=="profiles"' "/tmp/profiles-$AGENT_EPOCH.json" > /dev/null
    identity "$AGENT_PID" "agent-$AGENT_EPOCH"
    config_evidence "$AGENT_EPOCH"
    [ "$(stat -c '%u:%a' /run/agent)" = 2000:711 ]
    [ "$(stat -c '%u:%a' /var/lib/rustusb/agent)" = 2000:700 ]
    [ "$(stat -c '%u:%a' /run/agent/control.sock)" = 2000:666 ]
    printf 'pid=%s\nepoch=%s\ncontrol=%s\ndata=%s\nsocket=%s\n' "$AGENT_PID" "$AGENT_EPOCH" "$(stat -c '%u:%a' /run/agent)" "$(stat -c '%u:%a' /var/lib/rustusb/agent)" "$(stat -c '%u:%a' /run/agent/control.sock)" > "/tmp/agent-identity-$AGENT_EPOCH.txt"
}
start_job() {
    agent start "$1" > "/tmp/start-$1-$AGENT_EPOCH.json" 2> "/tmp/start-$1-$AGENT_EPOCH.log"
    jq -e '.schema_version==1 and .journal_healthy==true and .response.result=="started" and .response.replayed==false and .response.job.operator_uid==1000' "/tmp/start-$1-$AGENT_EPOCH.json" > /dev/null
    # The requesting CLI has actually returned before any I/O below.
    echo "AGENT_START_RETURNED role=$ROLE profile=$1 epoch=$AGENT_EPOCH wait=0"
}
sessions() { agent sessions "$1" > "$2"; jq -e '.journal_healthy and .response.result=="sessions" and .response.next_after==null' "$2" > /dev/null; }
wait_sessions() {
    # shellcheck disable=SC2034
    for session_poll in $(seq 1 400); do
        sessions "$1" "$2"
        if jq -e "$3" "$2" > /dev/null; then return 0; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    return 1
}
await_empty() {
    # shellcheck disable=SC2034
    for empty_poll in $(seq 1 300); do
        # Keep read/schema failures outside conditional functions: ash disables
        # errexit throughout a function invoked as an if condition.
        native_snapshot "$1"
        jq -e '.schema_version==2 and (.records|type)=="array" and (.exports|type)=="array" and (.terminals|type)=="array"' /var/lib/rustusb/native/channels.json > /dev/null
        if awk '$1=="hs" || $1=="ss" {n++;if($3!=4)bad=1} END {exit !(n>0&&!bad)}' "$1" && jq -e '.records==[] and .exports==[]' /var/lib/rustusb/native/channels.json > /dev/null; then return 0; fi
        sleep 0.05
    done
    return 1
}
denied() {
    # A newly opened empty application store has not committed a state file.
    # Preserve that absence and actual public empty responses, not a fake file.
    [ ! -e /var/lib/rustusb/agent/state.json ] && [ ! -L /var/lib/rustusb/agent/state.json ]
    echo absent > /tmp/agent-before-denied.status
    agent jobs "$1" > /tmp/jobs-before-denied.json
    sessions "$1" /tmp/sessions-before-denied.json
    jq -e '.schema_version==1 and .journal_healthy and .response=={result:"jobs",jobs:[],next_after:null}' /tmp/jobs-before-denied.json > /dev/null
    jq -e '.schema_version==1 and .journal_healthy and .response=={result:"sessions",sessions:[],next_after:null}' /tmp/sessions-before-denied.json > /dev/null
    if [ -f /var/lib/rustusb/native/channels.json ]; then cp /var/lib/rustusb/native/channels.json /tmp/root-before-denied.json; else echo absent > /tmp/root-before-denied.json; fi
    if agent_other start "$1" > /tmp/denied-uid.json 2> /tmp/denied-uid.log; then return 1; else denied_rc=$?; fi
    [ "$denied_rc" = 1 ]
    if agent start unknown-profile > /tmp/denied-profile.json 2> /tmp/denied-profile.log; then return 1; else denied_rc=$?; fi
    [ "$denied_rc" = 1 ]
    for denied_file in /tmp/denied-uid.json /tmp/denied-profile.json; do jq -e '.schema_version==1 and .journal_healthy and .response=={result:"error",code:"permission"}' "$denied_file" > /dev/null; done
    cp /var/lib/rustusb/agent/state.json /tmp/agent-after-denied.json
    jq -e '.jobs==[] and .sessions==[] and .requests==[] and ([.audit[]|select(.action=="denied" and .failure=="permission")]|length)==2' /tmp/agent-after-denied.json > /dev/null
    if [ -f /var/lib/rustusb/native/channels.json ]; then cp /var/lib/rustusb/native/channels.json /tmp/root-after-denied.json; else echo absent > /tmp/root-after-denied.json; fi
    cmp /tmp/root-before-denied.json /tmp/root-after-denied.json
    echo "AGENT_DENIED role=$ROLE unauthorized_uid=1002 unknown_profile=true resources_unchanged=true"
}
manifest() {
    mkdir -p /run/agent /var/lib/rustusb/agent
    chown 2000:2000 /run/agent /var/lib/rustusb/agent
    chmod 711 /run/agent; chmod 700 /var/lib/rustusb/agent
    jq -n --slurpfile profiles /tmp/agent-profiles.json '{schema_version:1,broker_uid:2000,helper_socket:"/run/rustusb/control.sock",control_directory:"/run/agent",data_directory:"/var/lib/rustusb/agent",profiles:$profiles[0],operators:[{uid:1000,profiles:[$profiles[0][].id],actions:["read","start","stop","discover","audit"]}]}' > /etc/rustusb/agent.json
    chmod 644 /etc/rustusb/agent.json
    [ "$(stat -c '%u:%a' /etc/rustusb/agent.json)" = 0:644 ]
    cp /etc/rustusb/agent.json /tmp/agent-manifest.json
}
physical_client_locals() {
    cp /var/lib/rustusb/native/channels.json "/tmp/client-receipts-$1.json"
    jq -e '.schema_version==2 and .exports==[] and (.records|length)==2 and all(.records[]; .owner.uid==2000 and .correlation!=null and .state.phase=="observed" and .state.receipt.session.status==6)' "/tmp/client-receipts-$1.json" > /dev/null
    HID_LOCAL=$(jq -er --arg key "$HID_KEY" '.records[]|select(.target.device_key==$key)|.state.receipt.session.local_bus_id' "/tmp/client-receipts-$1.json")
    SERIAL_LOCAL=$(jq -er --arg key "$SERIAL_KEY" '.records[]|select(.target.device_key==$key)|.state.receipt.session.local_bus_id' "/tmp/client-receipts-$1.json")
    [ "$HID_LOCAL" != "$SERIAL_LOCAL" ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idVendor")" = 0627 ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idProduct")" = 0001 ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/speed")" = 480 ]
    [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/idVendor")" = 0403 ]
    [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/idProduct")" = 6001 ]
    [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/speed")" = 12 ]
    # shellcheck disable=SC2034
    for local_poll in $(seq 1 150); do
        EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
        TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$SERIAL_LOCAL")" -name 'ttyUSB*' | head -1)
        [ -z "$EVENT" ] || [ -z "$TTY" ] || break
        sleep 0.05
    done
    [ -n "$EVENT" ] && [ -n "$TTY" ]
    [ "$(basename "$(readlink "/sys/bus/usb/devices/$HID_LOCAL:1.0/driver")")" = usbhid ]
    [ "$(basename "$(readlink "/sys/bus/usb/devices/$SERIAL_LOCAL:1.0/driver")")" = ftdi_sio ]
    EVENT=/dev/input/$(basename "$EVENT") TTY=/dev/$(basename "$TTY")
    jq -nc --arg hid "$HID_LOCAL" --arg serial "$SERIAL_LOCAL" --arg hid_generation "$(stat -Lc '%d:%i' "/sys/bus/usb/devices/$HID_LOCAL")" --arg serial_generation "$(stat -Lc '%d:%i' "/sys/bus/usb/devices/$SERIAL_LOCAL")" '{hid:$hid,serial:$serial,hid_generation:$hid_generation,serial_generation:$serial_generation}' > "/tmp/local-identities-$1.json"
}
physical_actual_io() {
    IO_ROUND=$1 IO_HID=$2
    if [ "$IO_HID" = yes ]; then
        : > "/tmp/input-$IO_ROUND.bin"
        exec 3< "$EVENT"
        cat <&3 3<&- 5<&- > "/tmp/input-$IO_ROUND.bin" & INPUT_PID=$!
        exec 3<&-
    fi
    stty -F "$TTY" raw -echo 57600 clocal -hupcl
    exec 4<> "$TTY"
    timeout 15 dd bs=1 count=35 <&4 > "/tmp/rx-$IO_ROUND.bin" 2> "/tmp/dd-$IO_ROUND.log" & RX_PID=$!
    echo "AGENT_CLIENT_WAIT_INPUT round=$IO_ROUND hid=$IO_HID"
    wait "$RX_PID"
    printf 'RUSTUSB-RX-%s-0001:0123456789abcdef\n' "$IO_ROUND" > "/tmp/expected-rx-$IO_ROUND.bin"
    cmp "/tmp/rx-$IO_ROUND.bin" "/tmp/expected-rx-$IO_ROUND.bin"
    printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$IO_ROUND" > "/tmp/tx-$IO_ROUND.bin"
    cat "/tmp/tx-$IO_ROUND.bin" >&4
    if [ "$IO_HID" = yes ]; then
        # shellcheck disable=SC2034
    for input_poll in $(seq 1 150); do
            if od -An -tu4 -w24 -v "/tmp/input-$IO_ROUND.bin" | awk -v code="$((IO_ROUND+1))" 'NF==6 && $5==code*65536+1 && $6==1 {down=1} NF==6 && $5==code*65536+1 && $6==0 {up=1} END {exit !(down&&up)}'; then break; fi
            sleep 0.05
        done
        od -An -tu4 -w24 -v "/tmp/input-$IO_ROUND.bin" | awk -v code="$((IO_ROUND+1))" 'NF==6 && $5==code*65536+1 && $6==1 {down=1} NF==6 && $5==code*65536+1 && $6==0 {up=1} END {exit !(down&&up)}'
        kill "$INPUT_PID"; wait "$INPUT_PID" || true
    fi
    exec 4<&-
    echo "AGENT_CLIENT_IO round=$IO_ROUND hid=$IO_HID rx=35 tx=35"
}

LINK_COMMON=1
#!/bin/sh
# Appended only to the source-locked disposable agent common init.
[ "${LINK_COMMON:-}" = 1 ] && [ "$BACKEND" = usbfs ] || exit 2
publish() {
    publish_file=$1
    [ "$publish_file" = "/tmp/$(basename "$publish_file")" ] && [ -f "$publish_file" ]
    publish_size=$(wc -c < "$publish_file")
    [ "$publish_size" -le 1048576 ]
    echo "LINK_RAW_BEGIN $(basename "$publish_file") $publish_size"
    /usr/bin/base64 "$publish_file"
    echo "LINK_RAW_END $(basename "$publish_file")"
}
network_sample() {
    NETWORK_SAMPLE_STAGE=$1 NETWORK_SAMPLE_NUMBER=$2
    # shellcheck disable=SC2034
    read -r NETWORK_UPTIME NETWORK_IDLE < /proc/uptime
    NETWORK_CARRIER=$(cat /sys/class/net/eth0/carrier)
    NETWORK_OPERSTATE=$(cat /sys/class/net/eth0/operstate)
    case "$NETWORK_CARRIER" in 0|1) ;; *) return 1 ;; esac
    case "$NETWORK_OPERSTATE" in unknown|notpresent|down|lowerlayerdown|testing|dormant|up) ;; *) return 1 ;; esac
    NETWORK_CENTISECONDS=$(printf '%s\n' "$NETWORK_UPTIME" | awk '{printf "%.0f",$1*100}')
    jq -nc --arg role "$ROLE" --arg stage "$NETWORK_SAMPLE_STAGE" --arg uptime "$NETWORK_UPTIME" \
        --argjson number "$NETWORK_SAMPLE_NUMBER" --argjson carrier "$NETWORK_CARRIER" --arg operstate "$NETWORK_OPERSTATE" \
        '{schema_version:1,role:$role,stage:$stage,observation:$number,guest_uptime:$uptime,carrier:$carrier,operstate:$operstate}'
}
network_ready() {
    STAGE=network-link-ready
    cp /tmp/network.txt /tmp/network-before-ready.txt
    : > /tmp/network-ready-observations.jsonl
    for network_poll in $(seq 1 200); do
        network_sample ready "$network_poll" >> /tmp/network-ready-observations.jsonl
        if [ "$network_poll" = 1 ]; then NETWORK_STARTED=$NETWORK_CENTISECONDS; fi
        [ "$((NETWORK_CENTISECONDS-NETWORK_STARTED))" -le 1000 ]
        if [ "$NETWORK_CARRIER" = 1 ] && [ "$NETWORK_OPERSTATE" = up ]; then break; fi
        sleep 0.05
    done
    [ "$NETWORK_CARRIER" = 1 ] && [ "$NETWORK_OPERSTATE" = up ]
    tail -n 1 /tmp/network-ready-observations.jsonl > /tmp/network-ready.json
    ip addr show dev eth0 > /tmp/network-ready.txt
    publish /tmp/network-ready-observations.jsonl
    publish /tmp/network-ready.json
    publish /tmp/network-ready.txt
    echo "LINK_NETWORK_READY role=$ROLE"
    host_expect NETWORK_READY_ACK
    echo "LINK_NETWORK_READY_ACK role=$ROLE"
}
start_profile() {
    agent start "$1" > "/tmp/start-$1-$2.json" 2> "/tmp/start-$1-$2.log"
    jq -e '.schema_version==1 and .journal_healthy and .response.result=="started" and .response.replayed==false and .response.job.operator_uid==1000' "/tmp/start-$1-$2.json" > /dev/null
    echo "LINK_START_RETURNED role=$ROLE profile=$1 mark=$2 wait=0"
}
health() {
    agent health "$1" > "$2"
    jq -e '.schema_version==1 and .journal_healthy and .response.result=="session_health" and .response.next_after==null' "$2" > /dev/null
}
wait_reply() {
    REPLY_PROFILE=$1 REPLY_ID=$2 REPLY_FILE=$3
    # shellcheck disable=SC2034
    for reply_poll in $(seq 1 160); do
        health "$REPLY_PROFILE" "$REPLY_FILE"
        if jq -e --arg id "$REPLY_ID" '.response.sessions|any(.session_id==$id and .availability=="live" and .link.protocol=="framed_v2" and .link.phase=="open" and .link.end==null and .link.last_reply_sequence!=null and .link.last_round_trip_micros!=null)' "$REPLY_FILE" > /dev/null; then return 0; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    return 1
}
wait_terminal() {
    TERMINAL_PROFILE=$1 TERMINAL_ID=$2 TERMINAL_FILE=$3
    # shellcheck disable=SC2034
    for terminal_poll in $(seq 1 240); do
        sessions "$TERMINAL_PROFILE" "$TERMINAL_FILE"
        if jq -e --arg id "$TERMINAL_ID" '.response.sessions|any(.id==$id and .terminal and .cleanup=="verified")' "$TERMINAL_FILE" > /dev/null; then return 0; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    return 1
}
phase_snapshot() {
    agent_snapshot "$1"
    cat /proc/net/tcp /proc/net/tcp6 > "/tmp/tcp-$1.txt"
    cat "/proc/$AGENT_PID/stat" > "/tmp/owner-stat-$1.txt"
    : > "/tmp/owner-sockets-$1.txt"
    for owner_fd in /proc/"$AGENT_PID"/fd/*; do
        if owner_target=$(readlink "$owner_fd"); then :; else
            [ ! -e "$owner_fd" ] && [ ! -L "$owner_fd" ]
            printf '%s gone_during_read\n' "${owner_fd##*/}" >> "/tmp/owner-sockets-$1.txt"
            continue
        fi
        case "$owner_target" in socket:*) printf '%s %s\n' "${owner_fd##*/}" "$owner_target" >> "/tmp/owner-sockets-$1.txt" ;; esac
    done
    publish "/tmp/agent-$1.json"
    if [ "$ROLE" = client ]; then
        publish "/tmp/root-$1.json"; publish "/tmp/vhci-$1.txt"
    else
        publish "/tmp/native-$1.status"
        if [ "$BACKEND" = usbfs ]; then
            publish "/tmp/root-$1.json"; publish "/tmp/host-$1.json"
        else
            publish "/tmp/physical-$1.status"
        fi
    fi
}
network_ready
#!/bin/sh
# Included after the common helper definitions, before the actual role workflow.
# No product input RPC or synthetic root cleanup receipt is introduced here.
[ "${LINK_COMMON:-}" = 1 ] || exit 2
case "$BACKEND" in virtual|usbfs) ;; *) exit 2 ;; esac
rust_server_setup() {
    STAGE=rust-server-prepare
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    if [ "$BACKEND" = usbfs ]; then
        for name in xhci-hcd xhci-pci-renesas xhci-pci; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
        # shellcheck disable=SC2034
        for device_poll in $(seq 1 100); do
            legacy --json devices > /tmp/devices.json
            if jq -e 'length==2 and all(.[]; .export_blockers==[])' /tmp/devices.json > /dev/null; then break; fi
            sleep 0.05
        done
        jq -e 'length==2 and all(.[]; .export_blockers==[])' /tmp/devices.json > /dev/null
        HID_ID=$(jq -er '.[]|select(.vendor_id==1575 and .product_id==1)|.id' /tmp/devices.json)
        SERIAL_ID=$(jq -er '.[]|select(.vendor_id==1027 and .product_id==24577)|.id' /tmp/devices.json)
        HID_BUS=$(jq -er --arg id "$HID_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
        SERIAL_BUS=$(jq -er --arg id "$SERIAL_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
        HID_KEY="physical:$HID_ID" SERIAL_KEY="physical:$SERIAL_ID"
        host_baseline /tmp/host-before.json
        jq -e 'length==2 and all(.[]; (.configuration|tonumber)==1 and (.interfaces|length)==1) and ([.[].interfaces[].driver]|sort)==["ftdi_sio","usbhid"]' /tmp/host-before.json > /dev/null
        publish /tmp/host-before.json
        for bus in "$HID_BUS" "$SERIAL_BUS"; do
            node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$bus/busnum")" "$(cat "/sys/bus/usb/devices/$bus/devnum")")
            [ -c "$node" ] && [ "$(stat -c '%u' "$node")" = 0 ]
            chmod 600 "$node"
            for uid in 1000 2000; do
                # shellcheck disable=SC2016
                if (exec 5<&-; /usr/bin/setpriv --reuid="$uid" --regid="$uid" --clear-groups /bin/busybox sh -c 'exec 6<> "$1"' sh "$node") > /tmp/direct-usbfs-denied.log 2>&1; then return 1; fi
            done
        done
        jq -n --arg hid "$HID_ID" --arg serial "$SERIAL_ID" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_native_device_ids:[],secure_physical_device_ids:[$hid,$serial]}' > /etc/rustusb/policy.json
    else
        HID_KEY=virtual:hid SERIAL_KEY=virtual:cdc-acm
        legacy --json devices > /tmp/devices.json
        jq -e 'length==0' /tmp/devices.json > /dev/null
        jq -n '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_native_device_ids:[],secure_physical_device_ids:[]}' > /etc/rustusb/policy.json
    fi
    jq -nc --arg hid "$HID_KEY" --arg serial "$SERIAL_KEY" '[$hid,$serial]' > /tmp/keys.json
    echo "AGENT_KEYS $(cat /tmp/keys.json)"
    start_helper
    PIN=$(cat /etc/rustusb/client-pin.txt)
    jq -n --arg listen "$LISTEN" --arg pin "$PIN" --slurpfile keys /tmp/keys.json '{schema_version:2,data_link:{protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,listen:$listen,devices:$keys[0],principals:[{id:"qemu-client",pins:[$pin],devices:$keys[0]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server.json
    chown 2000:2000 /etc/rustusb/server-key.der; chmod 600 /etc/rustusb/server-key.der
    jq -nc --arg backend "$BACKEND" '[{id:"server",backend:$backend,config:"/etc/rustusb/server.json"}]' > /tmp/agent-profiles.json
}
rust_client_setup() {
    STAGE=rust-client-prepare
    for name in usbip-core vhci-hcd; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    host_read; printf '%s\n' "$CONTROL" > /tmp/keys.json
    if [ "$BACKEND" = virtual ]; then
        jq -e '.==["virtual:hid","virtual:cdc-acm"]' /tmp/keys.json > /dev/null
    else
        jq -e 'length==2 and all(.[]; startswith("physical:usb-"))' /tmp/keys.json > /dev/null
    fi
    HID_KEY=$(jq -er '.[0]' /tmp/keys.json) SERIAL_KEY=$(jq -er '.[1]' /tmp/keys.json)
    jq -n --slurpfile keys /tmp/keys.json '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_imports:[{server_id:"qemu-server",device_keys:$keys[0]}]}' > /etc/rustusb/policy.json
    start_helper
    chown 2000:2000 /etc/rustusb/client-key.der; chmod 600 /etc/rustusb/client-key.der
    for profile in hid serial; do
        if [ "$profile" = hid ]; then key=$HID_KEY; else key=$SERIAL_KEY; fi
        jq -n --arg host "$HOST" --arg key "$key" '{schema_version:2,data_link:{protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,server_id:"qemu-server",endpoint:{host:$host,port:3241},server_name:"rustusb-qemu.test",device:$key,ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/client.der"],private_key:"/etc/rustusb/client-key.der"}' > "/etc/rustusb/client-$profile.json"
    done
    echo '[{"id":"hid","backend":"client","config":"/etc/rustusb/client-hid.json"},{"id":"serial","backend":"client","config":"/etc/rustusb/client-serial.json"}]' > /tmp/agent-profiles.json
}
# Absence is an actual observation, not an invented empty journal object.
rust_no_native() {
    [ ! -e /var/lib/rustusb/native/channels.json ] && [ ! -L /var/lib/rustusb/native/channels.json ]
    printf 'absent\n' > "/tmp/native-$1.status"
}
agent_snapshot() {
    cp /var/lib/rustusb/agent/state.json "/tmp/agent-$1.json"
    if [ "$ROLE" = client ]; then
        cp /var/lib/rustusb/native/channels.json "/tmp/root-$1.json"
        native_snapshot "/tmp/vhci-$1.txt"
    else
        rust_no_native "$1"
        if [ "$BACKEND" = usbfs ]; then
            cp /var/lib/rustusb/physical/journal.json "/tmp/root-$1.json"
            jq -e '.schema_version==3 and (.records|type)=="array" and (.terminals|type)=="array"' "/tmp/root-$1.json" > /dev/null
            host_baseline "/tmp/host-$1.json"
        else
            [ ! -e /var/lib/rustusb/physical/journal.json ] && [ ! -L /var/lib/rustusb/physical/journal.json ]
            printf 'absent\n' > "/tmp/physical-$1.status"
        fi
    fi
    echo "AGENT_SNAPSHOT role=$ROLE mark=$1"
}
rust_host_phase() {
    if [ "$BACKEND" = usbfs ]; then
        host_baseline "/tmp/host-$1.json"
        if [ "$2" = exported ]; then cmp /tmp/host-before.json "/tmp/host-$1.json"; fi
    fi
}
rust_server_final() {
    rust_no_native final
    if [ "$BACKEND" = usbfs ]; then
        host_baseline /tmp/host-restored.json
        cmp /tmp/host-before.json /tmp/host-restored.json
    else
        legacy --json devices > /tmp/devices-final.json
        cmp /tmp/devices.json /tmp/devices-final.json
    fi
}
rust_final_root_check() {
    if [ "$BACKEND" = usbfs ]; then
        jq -e '.schema_version==3 and .records==[] and (.terminals|length)==2' /tmp/root-final.json > /dev/null
    else
        [ "$(cat /tmp/native-final.status)" = absent ] && [ "$(cat /tmp/physical-final.status)" = absent ]
    fi
}
record_client_locals() {
    if [ "$BACKEND" = usbfs ]; then physical_client_locals "$1"; return; fi
    cp /var/lib/rustusb/native/channels.json "/tmp/client-receipts-$1.json"
    jq -e '.schema_version==2 and .exports==[] and (.records|length)==2 and all(.records[]; .owner.uid==2000 and .correlation!=null and .state.phase=="observed" and .state.receipt.session.status==6)' "/tmp/client-receipts-$1.json" > /dev/null
    HID_LOCAL=$(jq -er --arg key "$HID_KEY" '.records[]|select(.target.device_key==$key)|.state.receipt.session.local_bus_id' "/tmp/client-receipts-$1.json")
    SERIAL_LOCAL=$(jq -er --arg key "$SERIAL_KEY" '.records[]|select(.target.device_key==$key)|.state.receipt.session.local_bus_id' "/tmp/client-receipts-$1.json")
    [ "$HID_LOCAL" != "$SERIAL_LOCAL" ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idVendor")" = 1234 ] && [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idProduct")" = 0001 ]
    [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/idVendor")" = 1234 ] && [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/idProduct")" = 0002 ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/speed")" = 12 ] && [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/speed")" = 12 ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/bNumInterfaces")" = 1 ] && [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/bNumInterfaces")" = 2 ]
    # shellcheck disable=SC2034
    for local_poll in $(seq 1 150); do
        EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
        TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$SERIAL_LOCAL")" -name 'ttyACM*' | head -1)
        [ -z "$EVENT" ] || [ -z "$TTY" ] || break
        sleep 0.05
    done
    [ -n "$EVENT" ] && [ -n "$TTY" ]
    [ "$(basename "$(readlink "/sys/bus/usb/devices/$HID_LOCAL:1.0/driver")")" = usbhid ]
    [ "$(basename "$(readlink "/sys/bus/usb/devices/$SERIAL_LOCAL:1.0/driver")")" = cdc_acm ]
    EVENT=/dev/input/$(basename "$EVENT") TTY=/dev/$(basename "$TTY")
    jq -nc --arg hid "$HID_LOCAL" --arg serial "$SERIAL_LOCAL" --arg hid_generation "$(stat -Lc '%d:%i' "/sys/bus/usb/devices/$HID_LOCAL")" --arg serial_generation "$(stat -Lc '%d:%i' "/sys/bus/usb/devices/$SERIAL_LOCAL")" '{hid:$hid,serial:$serial,hid_generation:$hid_generation,serial_generation:$serial_generation}' > "/tmp/local-identities-$1.json"
}
actual_io() {
    if [ "$BACKEND" = usbfs ]; then
        physical_actual_io "$1" "$2"
        publish "/tmp/rx-$1.bin"; publish "/tmp/tx-$1.bin"
        return
    fi
    IO_ROUND=$1 IO_HID=$2
    stty -F "$TTY" raw -echo 57600 clocal -hupcl
    exec 4<> "$TTY"
    timeout 15 dd bs=1 count=35 <&4 > "/tmp/echo-$IO_ROUND.bin" 2> "/tmp/dd-echo-$IO_ROUND.log" & RX_PID=$!
    echo "AGENT_CLIENT_WAIT_INPUT round=$IO_ROUND hid=$IO_HID"
    host_expect "INPUT_$IO_ROUND"
    printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$IO_ROUND" > "/tmp/tx-$IO_ROUND.bin"
    cat "/tmp/tx-$IO_ROUND.bin" >&4
    wait "$RX_PID"
    cmp "/tmp/tx-$IO_ROUND.bin" "/tmp/echo-$IO_ROUND.bin"
    exec 4<&-
    publish "/tmp/echo-$IO_ROUND.bin"; publish "/tmp/tx-$IO_ROUND.bin"
    echo "AGENT_CLIENT_IO round=$IO_ROUND hid=$IO_HID rx=35 tx=35 cdc_echo=true hid_key_observed=false"
}

if [ "$ROLE" = server ]; then
    rust_server_setup
    manifest; agent_start 1; denied server
    start_profile server listener
    wait_sessions server /tmp/server-ready.json '.response.sessions|map(select(.role=="export" and .state=="Available" and .evidence=="export_ready" and .terminal==false))|length==2'
    rust_host_phase initial-exported exported
    agent_snapshot initial-exported
    echo LINK_SERVER_READY
    for phase in c2s s2c both; do
        STAGE="server-$phase"
        host_expect "PRE_$phase"
        wait_sessions server "/tmp/server-pre-$phase.json" '.response.sessions|map(select(.role=="incoming" and .state=="Connected" and .terminal==false))|length==2'
        HID_SESSION=$(jq -er --arg key "$HID_KEY" '.response.sessions[]|select(.role=="incoming" and .device_key==$key and .terminal==false)|.id' "/tmp/server-pre-$phase.json")
        wait_reply server "$HID_SESSION" "/tmp/health-$phase-server-pre.json"
        rust_host_phase "pre-$phase" held
        phase_snapshot "pre-$phase"
        publish "/tmp/health-$phase-server-pre.json"
        echo "LINK_PRE_READY role=server phase=$phase"
        host_expect "ARM_$phase"
        wait_terminal server "$HID_SESSION" "/tmp/server-terminal-$phase.json"
        health server "/tmp/health-$phase-server-terminal.json"
        phase_snapshot "fault-$phase"
        publish "/tmp/health-$phase-server-terminal.json"
        publish "/tmp/server-terminal-$phase.json"
        echo "LINK_FAULT_CLOSED role=server phase=$phase"
        host_expect "NEXT_$phase"
    done
    host_expect FINAL_HELD
    wait_sessions server /tmp/server-final-held.json '.response.sessions|map(select(.role=="incoming" and .state=="Connected" and .terminal==false))|length==2'
    HID_SESSION=$(jq -er --arg key "$HID_KEY" '.response.sessions[]|select(.role=="incoming" and .device_key==$key and .terminal==false)|.id' /tmp/server-final-held.json)
    wait_reply server "$HID_SESSION" /tmp/health-final-server-held.json
    rust_host_phase final-held held
    agent_snapshot final-held
    echo LINK_FINAL_HELD
    host_expect CLIENT_CLOSED
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-1
    rust_server_final
    agent_snapshot final
    rust_final_root_check
    echo LINK_SERVER_CLOSED
else
    rust_client_setup
    manifest; agent_start 1; denied hid
    host_expect START
    network_sample negative-probes 1 > /tmp/network-probe-start.json
    [ "$NETWORK_CARRIER" = 1 ] && [ "$NETWORK_OPERSTATE" = up ]
    publish /tmp/network-probe-start.json
    echo LINK_NETWORK_PROBES_START
    for family in 4 6; do
        if [ "$family" = 4 ]; then peer=192.0.2.1; else peer=fd42:7275:7374::1; fi
        if timeout 5 /usr/bin/nc-openbsd "-$family" -z -v -w 2 "$peer" 3240 > "/tmp/plaintext-$family.log" 2>&1; then exit 1; else plain_rc=$?; fi
        [ "$plain_rc" = 1 ]; grep -F 'Connection refused' "/tmp/plaintext-$family.log" > /dev/null
    done
    agent discover hid > /tmp/catalog-initial.json
    jq -e '.response.result=="catalog" and (.response.devices|length)==2' /tmp/catalog-initial.json > /dev/null
    start_profile serial persistent
    wait_sessions serial /tmp/serial-connected.json '.response.sessions|map(select(.state=="Connected" and .terminal==false))|length==1'
    round=1
    for phase in c2s s2c both; do
        STAGE="client-$phase"
        start_profile hid "$phase"
        wait_sessions hid "/tmp/client-pre-$phase.json" '.response.sessions|map(select(.state=="Connected" and .evidence=="local_enumeration" and .terminal==false))|length==1'
        HID_SESSION=$(jq -er '.response.sessions[]|select(.terminal==false)|.id' "/tmp/client-pre-$phase.json")
        record_client_locals "$round"
        actual_io "$round" yes
        wait_reply hid "$HID_SESSION" "/tmp/health-$phase-client-pre.json"
        phase_snapshot "pre-$phase"
        publish "/tmp/health-$phase-client-pre.json"
        echo "LINK_PRE_READY role=client phase=$phase"
        host_expect "ARM_$phase"
        agent discover hid > "/tmp/catalog-during-$phase.json"
        jq -e '.schema_version==1 and .journal_healthy and .response.result=="catalog"' "/tmp/catalog-during-$phase.json" > /dev/null
        publish "/tmp/catalog-during-$phase.json"
        round=$((round+1))
        actual_io "$round" no
        wait_terminal hid "$HID_SESSION" "/tmp/client-terminal-$phase.json"
        HID_JOB=$(jq -er --arg id "$HID_SESSION" '.response.sessions[]|select(.id==$id)|.job_id' "/tmp/client-terminal-$phase.json")
        # Do not race a fresh start against the old job's final owner join.
        # shellcheck disable=SC2034
        for job_poll in $(seq 1 100); do
            agent jobs hid > "/tmp/jobs-terminal-$phase.json"
            if jq -e --arg id "$HID_JOB" '.journal_healthy and .response.result=="jobs" and any(.response.jobs[]; .id==$id and .state=="finished")' "/tmp/jobs-terminal-$phase.json" > /dev/null; then break; fi
            sleep 0.05
        done
        jq -e --arg id "$HID_JOB" 'any(.response.jobs[]; .id==$id and .state=="finished")' "/tmp/jobs-terminal-$phase.json" > /dev/null
        health hid "/tmp/health-$phase-client-terminal.json"
        [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ -d "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
        jq -nc --arg old "$HID_LOCAL" --arg sibling "$SERIAL_LOCAL" --arg sibling_generation "$(stat -Lc '%d:%i' "/sys/bus/usb/devices/$SERIAL_LOCAL")" '{old_local_bus_id:$old,old_local_exists:false,sibling_local_bus_id:$sibling,sibling_generation:$sibling_generation}' > "/tmp/local-fault-$phase.json"
        phase_snapshot "fault-$phase"
        publish "/tmp/health-$phase-client-terminal.json"
        publish "/tmp/client-terminal-$phase.json"
        publish "/tmp/local-fault-$phase.json"
        echo "LINK_FAULT_CLOSED role=client phase=$phase"
        host_expect "NEXT_$phase"
        round=$((round+1))
    done
    STAGE=client-explicit-reuse
    start_profile hid final
    wait_sessions hid /tmp/client-final-held.json '.response.sessions|map(select(.state=="Connected" and .terminal==false))|length==1'
    record_client_locals 7; agent_snapshot final-held
    actual_io 7 yes
    HID_SESSION=$(jq -er '.response.sessions[]|select(.terminal==false)|.id' /tmp/client-final-held.json)
    wait_reply hid "$HID_SESSION" /tmp/health-final-client-held.json
    host_expect CLOSE
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-1
    await_empty /tmp/vhci-final.txt
    [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
    agent_snapshot final
    echo LINK_CLIENT_CLOSED
fi
# Teardown only: all operation/USB proofs above precede link-down. TIME_WAIT or
# FIN_WAIT sockets need not disappear before USB/owned-FD cleanup is verified.
ip link set eth0 down
echo "LINK_NETWORK_DOWN role=$ROLE"
host_expect FINISH
STAGE=complete
echo "RUSTUSB_LINK_RESULT: PASS role=$ROLE"
