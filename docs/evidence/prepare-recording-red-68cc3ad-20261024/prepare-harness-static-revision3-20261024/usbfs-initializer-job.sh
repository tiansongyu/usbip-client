#!/bin/sh
# Disposable PID1 only. Host coordination uses ttyS2, never the external NIC.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || exit 2
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.agent=1 '*) ;; *) exit 2 ;; esac
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
        echo "RUSTUSB_PREPARE_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        failure_journal native-channels /var/lib/rustusb/native/channels.json
        failure_journal agent-state /var/lib/rustusb/agent/state.json
        failure_journal physical-journal /var/lib/rustusb/physical/journal.json
        dmesg > /tmp/failure-dmesg.log
        cat /proc/net/tcp /proc/net/tcp6 > /tmp/failure-tcp.log
    fi
    dump_artifacts
    artifact_status=$?
    if [ "$artifact_status" != 0 ]; then
        echo "RUSTUSB_PREPARE_RESULT: FAIL role=$ROLE stage=artifact-dump original_exit=$status artifact_exit=$artifact_status"
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

case " $(cat /proc/cmdline) " in *' rustusb.agent=1 '*) ;; *) exit 2 ;; esac
AGENT_COMMON=1
#!/bin/sh
# Composed after the audited secure harness PID1/network/artifact helpers.
# This fragment cannot run alone and exposes no production test-input RPC.
[ "${AGENT_COMMON:-}" = 1 ] || exit 2
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
record_client_locals() {
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
actual_io() {
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

CASE=usbfs-initializer STOP_MODE=job PREPARE_COMMON=1
#!/bin/sh
# Appended to source-locked secure boot + agent helper functions only.
[ "${PREPARE_COMMON:-}" = 1 ] || exit 2
case " $(cat /proc/cmdline) " in *' rustusb.prepare_stop=1 '*) ;; *) exit 2 ;; esac
case "$CASE:$STOP_MODE" in native-resource:job|native-resource:term|usbfs-resource:job|usbfs-resource:term|usbfs-initializer:job|usbfs-initializer:term) ;; *) exit 2 ;; esac
GATE=/run/prepare-gate TRACER_PID='' AGENT_PID='' CANDIDATE='' SELECTED='' CASE_FAILED=0
atomic_control() { printf '%s\n' "$2" > "$GATE/.$1.tmp"; chmod 600 "$GATE/.$1.tmp"; mv "$GATE/.$1.tmp" "$GATE/$1"; }
prepare_cleanup() {
    prepare_status=$?
    trap - EXIT
    set +e
    if [ "$ROLE" = server ] && [ -d "$GATE" ]; then
        # Always release the same held syscall before cooperative process cleanup.
        if [ -n "$TRACER_PID" ] && kill -0 "$TRACER_PID" 2>/dev/null; then atomic_control abort abort; fi
        if [ -n "$AGENT_PID" ]; then kill -TERM "$AGENT_PID"; fi
        if [ -n "$TRACER_PID" ]; then
            for cleanup_poll in $(seq 1 150); do kill -0 "$TRACER_PID" 2>/dev/null || break; sleep 0.05; done
        fi
        for gate_file in "$GATE"/*; do [ ! -f "$gate_file" ] || cp "$gate_file" "/tmp/gate-$(basename "$gate_file")"; done
    elif [ -n "$AGENT_PID" ]; then kill -TERM "$AGENT_PID"; fi
    failure_journal agent-state /var/lib/rustusb/agent/state.json
    failure_journal native-channels /var/lib/rustusb/native/channels.json
    failure_journal physical-journal /var/lib/rustusb/physical/journal.json
    dmesg > /tmp/failure-dmesg.log
    if [ "$prepare_status" != 0 ]; then echo "RUSTUSB_PREPARE_RESULT: FAIL role=$ROLE stage=$STAGE exit=$prepare_status"; fi
    dump_artifacts
    artifact_status=$?
    [ "$artifact_status" = 0 ] || echo "RUSTUSB_PREPARE_RESULT: FAIL role=$ROLE stage=artifact-dump exit=$artifact_status"
    sync; poweroff -f
    while :; do sleep 1; done
}
trap prepare_cleanup EXIT
root_snapshot() {
    if [ "$BACKEND" = native ] || [ "$ROLE" = client ]; then ROOT_FILE=/var/lib/rustusb/native/channels.json; else ROOT_FILE=/var/lib/rustusb/physical/journal.json; fi
    cp "$ROOT_FILE" "/tmp/root-$1.json"
    if [ -f /var/lib/rustusb/agent/state.json ]; then cp /var/lib/rustusb/agent/state.json "/tmp/agent-$1.json"; fi
    if [ "$ROLE" = server ]; then host_baseline "/tmp/host-$1.json"; else native_snapshot "/tmp/vhci-$1.txt"; fi
}
fd_snapshot() {
    : > "/tmp/fds-$1.jsonl"
    for cap_fd in /proc/"$AGENT_PID"/fd/*; do
        [ -e "$cap_fd" ] || continue
        jq -nc --arg fd "${cap_fd##*/}" --arg target "$(readlink "$cap_fd")" --arg dev "$(stat -Lc '%d' "$cap_fd")" --arg inode "$(stat -Lc '%i' "$cap_fd")" --arg rdev "$(stat -Lc '%t:%T' "$cap_fd")" '{fd:$fd,target:$target,device:$dev,inode:$inode,rdev:$rdev}' >> "/tmp/fds-$1.jsonl"
    done
    jq -s . "/tmp/fds-$1.jsonl" > "/tmp/fds-$1.json"
    cat "/proc/$AGENT_PID/stat" > "/tmp/owner-stat-$1.txt"
    cat /proc/net/unix > "/tmp/unix-$1.txt"
}
actual_config() {
    for config_file in /etc/rustusb/agent.json /etc/rustusb/policy.json /etc/rustusb/server-*.json /etc/rustusb/client-*.json /etc/rustusb/ca.der /etc/rustusb/server.der /etc/rustusb/client.der /etc/rustusb/client-pin.txt; do
        [ ! -f "$config_file" ] || cp "$config_file" "/tmp/actual-$(basename "$config_file")"
    done
    : > /tmp/config-mode.txt
    for config_path in / /etc /etc/rustusb /run /run/agent /var /var/lib /var/lib/rustusb /var/lib/rustusb/agent /etc/rustusb/*.json /etc/rustusb/*.der; do
        [ ! -e "$config_path" ] || stat -c '%n %F %u %g %a %h %s %d %i' "$config_path" >> /tmp/config-mode.txt
    done
    # Private key contents are never copied/read/hashed.
}
traced_agent() {
    mkdir -m 700 "$GATE"
    (exec 5<&-; exec /usr/bin/prepare-gate --directory "$GATE" --usb-node "$HID_NODE" -- /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run > /tmp/agent-1.stdout 2> /tmp/agent-1.log) & TRACER_PID=$!
    for trace_poll in $(seq 1 200); do
        if [ -f "$GATE/ready.json" ]; then break; fi
        kill -0 "$TRACER_PID"; sleep 0.05
    done
    sha256sum "/proc/$TRACER_PID/exe" > /tmp/executable-gate.txt
    cat "/proc/$TRACER_PID/status" > /tmp/identity-gate.txt
    AGENT_PID=$(cat "$GATE/tracee.pid")
    jq -e --argjson pid "$AGENT_PID" '.uid==2000 and .tracee_pid==$pid and .event=="ready"' "$GATE/ready.json" > /dev/null
    for agent_poll in $(seq 1 200); do
        if agent profiles > /tmp/profiles-1.json 2> /tmp/profiles-1.log; then break; fi
        kill -0 "$TRACER_PID"; sleep 0.05
    done
    jq -e '.response.result=="profiles" and .journal_healthy' /tmp/profiles-1.json > /dev/null
    identity "$AGENT_PID" agent-1; actual_config
}
select_candidate() {
    for candidate_number in $(seq 1 32); do
        for gate_poll in $(seq 1 400); do
            [ ! -f "$GATE/candidate-$candidate_number.json" ] || break
            kill -0 "$TRACER_PID"; sleep 0.01
        done
        CANDIDATE=$candidate_number
        cp "$GATE/candidate-$CANDIDATE.json" "/tmp/gate-candidate-$CANDIDATE.json"
        if [ "$CASE" = usbfs-initializer ]; then
            cp /var/lib/rustusb/agent/state.json /tmp/selected-state.json
            SELECTED=$CANDIDATE; atomic_control "decision-$CANDIDATE" select; break
        fi
        cp /var/lib/rustusb/agent/state.next "/tmp/draft-$CANDIDATE.json"
        jq -e '.schema_version==2 and (.audit|length)>0 and (.sessions|type)=="array"' "/tmp/draft-$CANDIDATE.json" > /dev/null
        if jq -e '(.audit[-1].action=="resource") and (.audit[-1].profile=="pool") and ([.sessions[]|select(.profile=="pool" and .role=="export" and .terminal==false and .attempts[-1].resource_id!=null)]|length)==2' "/tmp/draft-$CANDIDATE.json" > /dev/null; then
            cp "/tmp/draft-$CANDIDATE.json" /tmp/selected-state.json
            cp /var/lib/rustusb/agent/state.json /tmp/state-before-ack.json
            SELECTED=$CANDIDATE; atomic_control "decision-$CANDIDATE" select; break
        fi
        atomic_control "decision-$CANDIDATE" skip
    done
    [ -n "$SELECTED" ]
    for select_poll in $(seq 1 100); do
        if grep -F '"event":"selected"' "$GATE/events.jsonl" > /dev/null; then break; fi
        sleep 0.01
    done
    grep -F '"event":"selected"' "$GATE/events.jsonl" > /dev/null
    jq -ce '[.sessions[]|select(.profile=="pool" and .role=="export" and .terminal==false)]|select(length==2 and all(.[];.attempts|length==1))' /tmp/selected-state.json > /tmp/selected-sessions.json
    TARGET_JOB=$(jq -er 'map(.job_id)|unique|select(length==1)|.[0]' /tmp/selected-sessions.json)
    jq -ce 'map(.attempts[0])|select(length==2 and all(.[];.resource_id!=null))' /tmp/selected-sessions.json > /tmp/selected-attempts.json
    root_snapshot held; fd_snapshot held
    cat /proc/net/tcp /proc/net/tcp6 > /tmp/listeners-held.txt
    jq -nc --argjson candidate "$SELECTED" --arg case "$CASE" --arg stop "$STOP_MODE" --arg job "$TARGET_JOB" '{case:$case,stop:$stop,candidate:$candidate,job:$job}' > /tmp/selection.json
    atomic_control observe before
    for observe_poll in $(seq 1 100); do [ ! -f "$GATE/held-before.json" ] || break; sleep 0.01; done
    [ -f "$GATE/held-before.json" ]
    echo PREPARE_GATE_HELD
}
check_target_proof() {
    if [ "$BACKEND" = native ]; then
        jq -e --slurpfile sessions /tmp/selected-sessions.json '
          . as $root | ($sessions[0]|length)==2 and all($sessions[0][];
          . as $s | .attempts[0] as $a |
          all($root.exports[];.lease_id!=$a.resource_id) and
          any($root.terminals[].terminal;.resource_id==$a.resource_id and .kind=="native_export" and .owner_uid==2000 and .correlation==$a.correlation and .device_key==$s.device_key and .proof=="native_export_restored"))' "$ROOT_FILE" > /dev/null
    else
        jq -e --slurpfile sessions /tmp/selected-sessions.json '
          . as $root | ($sessions[0]|length)==2 and all($sessions[0][];
          . as $s | .attempts[0] as $a |
          all($root.records[];.lease_id!=$a.resource_id) and
          any($root.terminals[];.resource_id==$a.resource_id and .kind=="usbfs_grant" and .owner_uid==2000 and .correlation==$a.correlation and .device_key==$s.device_key and .proof=="physical_baseline_restored"))' "$ROOT_FILE" > /dev/null
    fi
}
config_evidence() { actual_config; }
# Actual link state, not a fixed startup sleep.
for network_poll in $(seq 1 200); do
    if [ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && [ "$(cat /sys/class/net/eth0/operstate)" = up ]; then break; fi
    sleep 0.05
done
[ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && [ "$(cat /sys/class/net/eth0/operstate)" = up ]
ip addr show dev eth0 > /tmp/network-ready.txt
if [ "$ROLE" = server ]; then
    STAGE=server-setup
    for name in xhci-hcd xhci-pci-renesas xhci-pci; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    if [ "$BACKEND" = native ]; then for name in usbip-core usbip-host; do insmod "/modules/$name.ko"; done; fi
    for device_poll in $(seq 1 100); do
        legacy --json devices > /tmp/devices.json
        if jq -e 'length==2 and all(.[];.export_blockers==[])' /tmp/devices.json > /dev/null; then break; fi
        sleep 0.05
    done
    jq -e 'length==2 and all(.[];.export_blockers==[])' /tmp/devices.json > /dev/null
    HID_ID=$(jq -er '.[]|select(.vendor_id==1575 and .product_id==1)|.id' /tmp/devices.json)
    SERIAL_ID=$(jq -er '.[]|select(.vendor_id==1027 and .product_id==24577)|.id' /tmp/devices.json)
    HID_BUS=$(jq -er --arg id "$HID_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
    SERIAL_BUS=$(jq -er --arg id "$SERIAL_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
    if [ "$BACKEND" = native ]; then key_prefix=native; else key_prefix=physical; fi
    HID_KEY="$key_prefix:$HID_ID" SERIAL_KEY="$key_prefix:$SERIAL_ID"
    host_baseline /tmp/host-before.json
    for bus in "$HID_BUS" "$SERIAL_BUS"; do node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$bus/busnum")" "$(cat "/sys/bus/usb/devices/$bus/devnum")"); chmod 600 "$node"; done
    HID_NODE=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$HID_BUS/busnum")" "$(cat "/sys/bus/usb/devices/$HID_BUS/devnum")")
    : > /tmp/usb-nodes-before.jsonl
    for bus in "$HID_BUS" "$SERIAL_BUS"; do
        base="/sys/bus/usb/devices/$bus"
        node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "$base/busnum")" "$(cat "$base/devnum")")
        [ -c "$node" ] && [ ! -L "$node" ]
        jq -nc --arg bus "$bus" --arg node "$node" \
          --arg filesystem_device "$(stat -c '%d' "$node")" --arg inode "$(stat -c '%i' "$node")" \
          --arg rdev "$(stat -c '%t:%T' "$node")" --arg sysfs_dev "$(cat "$base/dev")" \
          --arg generation "$(stat -Lc '%d:%i' "$base")" --arg mode "$(stat -c '%a' "$node")" \
          --argjson uid "$(stat -c '%u' "$node")" --argjson links "$(stat -c '%h' "$node")" \
          '{bus:$bus,node:$node,filesystem_device:$filesystem_device,inode:$inode,rdev:$rdev,sysfs_dev:$sysfs_dev,generation:$generation,mode:$mode,uid:$uid,links:$links,character_device:true}' >> /tmp/usb-nodes-before.jsonl
    done
    jq -s 'sort_by(.bus)' /tmp/usb-nodes-before.jsonl > /tmp/usb-nodes-before.json
    jq -n --arg hid "$HID_ID" --arg serial "$SERIAL_ID" --arg backend "$BACKEND" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_native_device_ids:(if $backend=="native" then [$hid,$serial] else [] end),secure_physical_device_ids:(if $backend=="usbfs" then [$hid,$serial] else [] end),physical_connect_restore_device_ids:[]}' > /etc/rustusb/policy.json
    start_helper
    jq -nc --arg hid "$HID_KEY" --arg serial "$SERIAL_KEY" '[$hid,$serial]' > /tmp/keys.json
    echo "AGENT_KEYS $(cat /tmp/keys.json)"
    if [ "$FAMILY" = ipv4 ]; then address=192.0.2.1:3241; else address='[fd42:7275:7374::1]:3241'; fi
    jq -n --arg listen "$address" --arg pin "$(cat /etc/rustusb/client-pin.txt)" --slurpfile keys /tmp/keys.json '{schema_version:1,broker_uid:2000,listen:$listen,devices:$keys[0],principals:[{id:"qemu-client",pins:[$pin],devices:$keys[0]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server-pool.json
    chown 2000:2000 /etc/rustusb/server-key.der; chmod 600 /etc/rustusb/server-key.der
    jq -nc --arg backend "$BACKEND" '[{id:"pool",backend:$backend,config:"/etc/rustusb/server-pool.json"}]' > /tmp/agent-profiles.json
    manifest; traced_agent; AGENT_EPOCH=1
    fd_snapshot idle
    echo PREPARE_SERVER_READY
    host_expect START_TARGET
    STAGE=target-prepare
    if [ "$CASE" = usbfs-initializer ]; then ARMED_OPERATION=initializer; else ARMED_OPERATION=resource; fi
    atomic_control arm "$ARMED_OPERATION"
    # Observe a complete actual tracer event before creating the only initializer.
    # A partial trailing write is never accepted as an armed acknowledgement.
    for arm_poll in $(seq 1 100); do
        awk '/"event":"armed"/ && /}$/ {print}' "$GATE/events.jsonl" > /tmp/gate-armed.json
        if jq -es --arg operation "$ARMED_OPERATION" --slurpfile ready "$GATE/ready.json" 'length==1 and (.[0]|.event=="armed" and .nonce=="prepare-stop-v1" and .operation==$operation and .tracee_pid==$ready[0].tracee_pid and .tracee_starttime==$ready[0].tracee_starttime)' /tmp/gate-armed.json > /dev/null; then break; fi
        kill -0 "$TRACER_PID"; sleep 0.01
    done
    jq -es --arg operation "$ARMED_OPERATION" --slurpfile ready "$GATE/ready.json" 'length==1 and (.[0]|.event=="armed" and .nonce=="prepare-stop-v1" and .operation==$operation and .tracee_pid==$ready[0].tracee_pid and .tracee_starttime==$ready[0].tracee_starttime)' /tmp/gate-armed.json > /dev/null
    echo PREPARE_GATE_ARMED
    agent start pool > /tmp/start-pool-1.json 2> /tmp/start-pool-1.log & START_PID=$!
    select_candidate
    host_expect STOP_TARGET
    STAGE=held-stop
    read -r STOP_UPTIME STOP_IDLE < /proc/uptime
    if [ "$STOP_MODE" = job ]; then
        agent stop-job "$TARGET_JOB" > /tmp/stop-target.json 2> /tmp/stop-target.log & STOP_PID=$!
    else
        /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /bin/busybox kill -TERM "$AGENT_PID"
        STOP_PID=''
    fi
    RELEASED=false
    : > /tmp/held-observations.jsonl
    for observation in $(seq 1 500); do
        [ ! -f "$GATE/exit-$SELECTED.json" ]
        root_snapshot "observe-$observation"; fd_snapshot "observe-$observation"
        baseline_equal=false; no_fd=false; proof=false
        if cmp -s /tmp/host-before.json "/tmp/host-observe-$observation.json"; then baseline_equal=true; fi
        # The USBFS grant is a character FD. Native export ownership is the
        # supervised control UDS + root receipt; its full FD table is retained.
        if jq -e 'all(.[];.target|startswith("/dev/bus/usb/")|not)' "/tmp/fds-observe-$observation.json" > /dev/null; then no_fd=true; fi
        if check_target_proof; then proof=true; fi
        read -r observed_uptime observed_idle < /proc/uptime
        jq -nc --argjson observation "$observation" --arg uptime "$observed_uptime" --argjson baseline "$baseline_equal" --argjson fd_closed "$no_fd" --argjson proof "$proof" '{observation:$observation,uptime:$uptime,baseline_equal:$baseline,target_fd_closed:$fd_closed,exact_root_terminal:$proof,gate_released:false}' >> /tmp/held-observations.jsonl
        if [ "$CASE" = usbfs-initializer ]; then
            if [ "$proof" = true ] || [ "$no_fd" = true ]; then CASE_FAILED=1; fi
            [ "$observation" -lt 5 ] || break
        elif [ "$baseline_equal:$no_fd:$proof" = true:true:true ]; then RELEASED=true; break; fi
        # Whole observation bounded by guest uptime, independent of slow sampling.
        elapsed=$(awk -v start="$STOP_UPTIME" -v now="$observed_uptime" 'BEGIN{print now-start}')
        if awk -v elapsed="$elapsed" 'BEGIN{exit !(elapsed>=15)}'; then break; fi
        sleep 0.01
    done
    if [ "$CASE" != usbfs-initializer ] && [ "$RELEASED" != true ]; then CASE_FAILED=1; fi
    root_snapshot before-release; fd_snapshot before-release
    jq -nc --arg case "$CASE" --arg stop "$STOP_MODE" --argjson failed "$CASE_FAILED" --argjson resource_released "$RELEASED" --arg uptime "$STOP_UPTIME" '{case:$case,stop:$stop,failed:$failed,resource_released_while_gate_held:$resource_released,stop_uptime:$uptime}' > /tmp/held-outcome.json
    atomic_control observe after
    for observe_poll in $(seq 1 100); do [ ! -f "$GATE/held-after.json" ] || break; sleep 0.01; done
    [ -f "$GATE/held-after.json" ]
    echo "PREPARE_OBSERVED failed=$CASE_FAILED"
    atomic_control "release-$SELECTED" release
    for release_poll in $(seq 1 300); do [ ! -f "$GATE/exit-$SELECTED.json" ] || break; sleep 0.01; done
    [ -f "$GATE/exit-$SELECTED.json" ]
    if [ -n "$STOP_PID" ]; then if wait "$STOP_PID"; then echo 0 > /tmp/wait-stop.txt; else echo "$?" > /tmp/wait-stop.txt; fi; fi
    wait "$START_PID"
    if [ "$STOP_MODE" = job ]; then
        wait_sessions pool /tmp/target-final.json '.response.sessions|length==2 and all(.[];.terminal and .cleanup=="verified")'
        kill -TERM "$AGENT_PID"
    fi
    # Tracer is the actual parent/reaper; shell waits the tracer exactly once.
    if wait "$TRACER_PID"; then echo 0 > /tmp/wait-tracer.txt; else trace_wait=$?; echo "$trace_wait" > /tmp/wait-tracer.txt; CASE_FAILED=1; fi
    TRACER_PID='' AGENT_PID=''
    root_snapshot after-release
    cmp /tmp/host-before.json /tmp/host-after-release.json
    check_target_proof
    jq -e '.records==[] and ((.exports // [])==[])' /tmp/root-after-release.json > /dev/null
    echo PREPARE_TARGET_CLOSED
    # The first failure remains latched; a fresh actual attempt only tests reuse.
    agent_start 2
    start_job pool
    wait_sessions pool /tmp/reuse-ready.json '.response.sessions|([.[]|select(.terminal==false and .role=="export" and .state=="Available")]|length)==2'
    root_snapshot reuse-ready
    echo PREPARE_REUSE_READY
    host_expect STOP_REUSE
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-2; AGENT_PID=''
    root_snapshot final
    cmp /tmp/host-before.json /tmp/host-final.json
    jq -e '.records==[] and ((.exports // [])==[])' /tmp/root-final.json > /dev/null
    echo PREPARE_SERVER_CLOSED
    host_expect FINISH
    if [ "$CASE_FAILED" != 0 ]; then STAGE=actual-held-cleanup-missing; exit 1; fi

else
    STAGE=client-setup
    for name in usbip-core vhci-hcd; do insmod "/modules/$name.ko"; done
    host_read; printf '%s\n' "$CONTROL" > /tmp/keys.json
    HID_KEY=$(jq -er '.[0]' /tmp/keys.json) SERIAL_KEY=$(jq -er '.[1]' /tmp/keys.json)
    jq -n --slurpfile keys /tmp/keys.json '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_imports:[{server_id:"qemu-server",device_keys:$keys[0]}]}' > /etc/rustusb/policy.json
    start_helper
    chown 2000:2000 /etc/rustusb/client-key.der; chmod 600 /etc/rustusb/client-key.der
    for profile in hid serial; do
        if [ "$profile" = hid ]; then key=$HID_KEY; else key=$SERIAL_KEY; fi
        port=3241
        jq -n --arg host "$HOST" --arg key "$key" --argjson port "$port" '{schema_version:1,broker_uid:2000,server_id:"qemu-server",endpoint:{host:$host,port:$port},server_name:"rustusb-qemu.test",device:$key,ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/client.der"],private_key:"/etc/rustusb/client-key.der"}' > "/etc/rustusb/client-$profile.json"
    done
    echo '[{"id":"hid","backend":"client","config":"/etc/rustusb/client-hid.json"},{"id":"serial","backend":"client","config":"/etc/rustusb/client-serial.json"}]' > /tmp/agent-profiles.json
    manifest
    # The common startup config collector assumes old file names; use this fixture collector.
    config_evidence() { actual_config; }
    agent_start 1
    echo PREPARE_CLIENT_READY
    host_expect HELD
    # No listener may bind while either initial resource acknowledgement is held.
    if timeout 5 /usr/bin/nc-openbsd -z -v -w 2 "$HOST" 3241 > /tmp/held-connect.stdout 2> /tmp/held-connect.stderr; then exit 1; else echo "$?" > /tmp/held-connect.wait; fi
    grep -F 'Connection refused' /tmp/held-connect.stderr
    echo PREPARE_LISTENER_REFUSED
    host_expect START_REUSE
    start_job hid; start_job serial
    wait_sessions hid /tmp/client-hid-connected.json '.response.sessions|any(.state=="Connected" and .terminal==false)'
    wait_sessions serial /tmp/client-serial-connected.json '.response.sessions|any(.state=="Connected" and .terminal==false)'
    record_client_locals reuse
    root_snapshot reused
    actual_io 1 yes
    echo PREPARE_CLIENT_REUSED
    host_expect SERVER_CLOSED
    wait_sessions hid /tmp/client-hid-final.json '.response.sessions|all(.terminal and .cleanup=="verified")'
    wait_sessions serial /tmp/client-serial-final.json '.response.sessions|all(.terminal and .cleanup=="verified")'
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-1; AGENT_PID=''
    await_empty /tmp/vhci-final.txt
    root_snapshot final
    echo PREPARE_CLIENT_CLOSED
    host_expect FINISH
fi
STAGE=finished
for gate_file in "$GATE"/*; do [ ! -f "$gate_file" ] || cp "$gate_file" "/tmp/gate-$(basename "$gate_file")"; done
echo "RUSTUSB_PREPARE_RESULT: PASS role=$ROLE case=$CASE stop=$STOP_MODE"
