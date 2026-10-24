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
base_cleanup() {
    status=$?
    trap - EXIT
    # A diagnostic failure must not replace the original failure, skip later
    # files or exit PID1 before poweroff. Keep each encoder failure explicit.
    set +e
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_LOGGING_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        failure_journal native-channels /var/lib/rustusb/native/channels.json
        failure_journal agent-state /var/lib/rustusb/agent/state.json
        failure_journal physical-journal /var/lib/rustusb/physical/journal.json
        dmesg > /tmp/failure-dmesg.log
        cat /proc/net/tcp /proc/net/tcp6 > /tmp/failure-tcp.log
    fi
    dump_artifacts
    artifact_status=$?
    if [ "$artifact_status" != 0 ]; then
        echo "RUSTUSB_LOGGING_RESULT: FAIL role=$ROLE stage=artifact-dump original_exit=$status artifact_exit=$artifact_status"
    fi
    sync
    poweroff -f
    poweroff_status=$?
    echo "SECURE_POWEROFF_RETURNED role=$ROLE original_exit=$status artifact_exit=$artifact_status poweroff_exit=$poweroff_status"
    # The bounded host runner will terminate this failed guest. Never exit PID1
    # and manufacture a kernel panic merely because the final syscall returned.
    while :; do sleep 1; done
}
trap base_cleanup EXIT
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
    /usr/bin/rustusb-helper --log-directory /var/log/rustusb/helper --log-max-bytes 4096 --log-retain 4 --physical-state-dir /var/lib/rustusb/physical --native-state-dir /var/lib/rustusb/native > /tmp/helper.log 2>&1 &
    HELPER_PID=$!
    # shellcheck disable=SC2034
    for attempt in $(seq 1 100); do [ ! -S /run/rustusb/control.sock ] || break; kill -0 "$HELPER_PID"; sleep 0.05; done
    [ -S /run/rustusb/control.sock ]
    [ "$(readlink "/proc/$HELPER_PID/exe")" = /usr/bin/rustusb-helper ]
    sha256sum "/proc/$HELPER_PID/exe" > /tmp/executable-helper.txt
    cat "/proc/$HELPER_PID/status" > /tmp/identity-helper.txt
    cp /etc/rustusb/policy.json /tmp/helper-policy.json
    logging_started helper started
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
[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2
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
    (exec 5<&-; exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb --log-directory /var/log/rustusb/agent --log-max-bytes 4096 --log-retain 4 agent --config /etc/rustusb/agent.json run > "/tmp/agent-$AGENT_EPOCH.stdout" 2> "/tmp/agent-$AGENT_EPOCH.log") &
    AGENT_PID=$!
    # shellcheck disable=SC2034
    for agent_poll in $(seq 1 200); do
        if agent profiles > "/tmp/profiles-$AGENT_EPOCH.json" 2> "/tmp/profiles-$AGENT_EPOCH.log"; then break; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    jq -e '.schema_version==1 and .journal_healthy==true and .response.result=="profiles"' "/tmp/profiles-$AGENT_EPOCH.json" > /dev/null
    identity "$AGENT_PID" "agent-$AGENT_EPOCH"
    config_evidence "$AGENT_EPOCH"
    logging_started agent "$AGENT_EPOCH"
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

PROCESS_LOGGING_COMMON=1
#!/bin/sh
# Appended to existing boot and read-only Native/agent helpers. Guest PID1 only.
[ "${PROCESS_LOGGING_COMMON:-}" = 1 ] || exit 2
case "$ROLE:$BACKEND:$FAMILY" in server:native:ipv4|client:native:ipv4) ;; *) exit 2 ;; esac
GATE_PID='' GATE_NONCE='' AGENT_PID='' HELPER_PID=''
mkdir -p /var/log/rustusb/helper /var/log/rustusb/agent
chmod 755 /var/log /var/log/rustusb
chmod 700 /var/log/rustusb/helper /var/log/rustusb/agent
chown 2000:2000 /var/log/rustusb/agent
log_meta() {
    log_prefix=$1 log_directory=$2
    : > "/tmp/$log_prefix-metadata.jsonl"
    for log_path in "$log_directory" "$log_directory"/*; do
        [ -e "$log_path" ] || continue
        [ ! -L "$log_path" ]
        log_capture=''
        if [ -f "$log_path" ]; then
            case "$(basename "$log_path")" in
                segment-??.ndjson) log_capture="$log_prefix-$(basename "$log_path")"; cp "$log_path" "/tmp/$log_capture" ;;
                writer.lock|access.lock) ;;
                *) return 1 ;;
            esac
        else [ -d "$log_path" ] && [ "$log_path" = "$log_directory" ]; fi
        jq -nc --arg path "$log_path" --arg capture "$log_capture" --arg mode "$(stat -c '%a' "$log_path")" --arg kind "$(stat -c '%F' "$log_path")" --arg canonical "$(readlink -f "$log_path")" --argjson uid "$(stat -c '%u' "$log_path")" --argjson gid "$(stat -c '%g' "$log_path")" --argjson bytes "$(stat -c '%s' "$log_path")" --argjson links "$(stat -c '%h' "$log_path")" --argjson device "$(stat -c '%d' "$log_path")" --argjson inode "$(stat -c '%i' "$log_path")" '{path:$path,canonical:$canonical,capture:$capture,mode:$mode,kind:$kind,uid:$uid,gid:$gid,bytes:$bytes,links:$links,device:$device,inode:$inode}' >> "/tmp/$log_prefix-metadata.jsonl"
    done
    jq -s . "/tmp/$log_prefix-metadata.jsonl" > "/tmp/$log_prefix-metadata.json"
}
logging_started() {
    started_kind=$1 started_epoch=$2
    started_file="/var/log/rustusb/$started_kind/segment-00.ndjson"
    # shellcheck disable=SC2034
    for started_poll in $(seq 1 100); do
        if [ -f "$started_file" ] && jq -se 'any(.[]; .event.kind?=="started")' "$started_file" > /dev/null; then break; fi
        sleep 0.05
    done
    started_copy="/tmp/logs-$started_kind-$started_epoch-started.ndjson"
    if [ "$started_kind" = helper ]; then started_copy=/tmp/logs-helper-started.ndjson; fi
    # The live writer can append after readiness was observed. Take an
    # independent shared-lock OFD while copying the actual complete segment.
    /usr/bin/flock -s "/var/log/rustusb/$started_kind/access.lock" /bin/cp "$started_file" "$started_copy"
    jq -se 'any(.[]; .event.kind?=="started")' "$started_copy" > /dev/null
}
local_gone() {
    [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
    jq -nc --arg hid "$HID_LOCAL" --arg serial "$SERIAL_LOCAL" --arg uptime "$(cat /proc/uptime)" '{hid:$hid,serial:$serial,hid_exists:false,serial_exists:false,uptime:$uptime}' > "/tmp/local-gone-$1.json"
}
thread_receipt() {
    thread_label=$1
    : > "/tmp/threads-$thread_label.jsonl"
    for thread_task in /proc/"$AGENT_PID"/task/*; do
        thread_name=$(cat "$thread_task/comm")
        case "$thread_name" in rustusb-log-*)
            thread_call=$(cat "$thread_task/syscall")
            thread_fd='' thread_target='' thread_stat=''
            # Kernel syscall fields deliberately split into positional arguments.
            # shellcheck disable=SC2086
            set -- $thread_call
            if [ "$#" -ge 3 ] && [ "$1" = 73 ]; then
                thread_fd=$(( $2 ))
                thread_target=$(readlink "/proc/$AGENT_PID/fd/$thread_fd")
                thread_stat=$(stat -Lc '%d:%i:%u:%a:%h' "/proc/$AGENT_PID/fd/$thread_fd")
            fi
            jq -nc --arg tid "$(basename "$thread_task")" --arg comm "$thread_name" --arg syscall "$thread_call" --arg wchan "$(cat "$thread_task/wchan")" --arg fd "$thread_fd" --arg target "$thread_target" --arg stat "$thread_stat" '{tid:$tid,comm:$comm,syscall:$syscall,wchan:$wchan,fd:$fd,target:$target,stat:$stat}' >> "/tmp/threads-$thread_label.jsonl" ;;
        esac
    done
}
process_receipt() {
    receipt_pid=$1 receipt_label=$2
    kill -0 "$receipt_pid"
    cat "/proc/$receipt_pid/stat" > "/tmp/proc-$receipt_label.stat"
    cat "/proc/$receipt_pid/status" > "/tmp/proc-$receipt_label.status"
    cat "/proc/$receipt_pid/cmdline" > "/tmp/proc-$receipt_label.cmdline"
    sha256sum "/proc/$receipt_pid/exe" > "/tmp/proc-$receipt_label.exe.sha256"
    : > "/tmp/proc-$receipt_label-fds.jsonl"
    for receipt_fd in /proc/"$receipt_pid"/fd/*; do
        [ -e "$receipt_fd" ] || continue
        if receipt_target=$(readlink "$receipt_fd") && receipt_stat=$(stat -Lc '%d:%i:%u:%a:%F' "$receipt_fd" 2>/dev/null); then
            jq -nc --arg fd "$(basename "$receipt_fd")" --arg target "$receipt_target" --arg stat "$receipt_stat" '{fd:$fd,target:$target,stat:$stat}' >> "/tmp/proc-$receipt_label-fds.jsonl"
        else
            printf 'fd=%s disappeared_during_snapshot=true\n' "$(basename "$receipt_fd")" >> "/tmp/proc-$receipt_label-fd-races.txt"
        fi
    done
    cat /proc/uptime > "/tmp/proc-$receipt_label.uptime"
}
# This always releases our removable test lock before the base failure collector.
logging_cleanup() {
    original=$?
    trap - EXIT
    set +e
    if [ -n "$GATE_PID" ] && kill -0 "$GATE_PID" 2>/dev/null; then
        printf '%s\n' "$GATE_NONCE" | timeout 2 tee /run/log-gate/release > /dev/null
        sleep 0.1
        kill -TERM "$GATE_PID" 2>/dev/null
    fi
    for last_pid in "$AGENT_PID" "$HELPER_PID"; do [ -z "$last_pid" ] || kill -TERM "$last_pid" 2>/dev/null; done
    if [ "$original" != 0 ]; then
        log_meta logs-failure-agent /var/log/rustusb/agent
        log_meta logs-failure-helper /var/log/rustusb/helper
    fi
    (exit "$original")
    base_cleanup
}
trap logging_cleanup EXIT
: > /tmp/logs-ancestors.jsonl
for ancestor in / /var /var/log /var/log/rustusb; do
    jq -nc --arg path "$ancestor" --arg canonical "$(readlink -f "$ancestor")" --arg mode "$(stat -c '%a' "$ancestor")" --argjson uid "$(stat -c '%u' "$ancestor")" --argjson gid "$(stat -c '%g' "$ancestor")" '{path:$path,canonical:$canonical,mode:$mode,uid:$uid,gid:$gid}' >> /tmp/logs-ancestors.jsonl
done
# Carrier is an actual read-only readiness barrier, not a longer nc deadline.
# shellcheck disable=SC2034
for network_poll in $(seq 1 200); do
    carrier=$(cat /sys/class/net/eth0/carrier) operstate=$(cat /sys/class/net/eth0/operstate)
    if [ "$carrier:$operstate" = 1:up ]; then break; fi
    sleep 0.05
done
[ "$carrier:$operstate" = 1:up ]
jq -nc --arg role "$ROLE" --arg carrier "$carrier" --arg operstate "$operstate" --arg uptime "$(cat /proc/uptime)" '{role:$role,carrier:$carrier,operstate:$operstate,uptime:$uptime}' > /tmp/network-ready.json
ip addr show eth0 > /tmp/network-ready-address.txt
echo "LOG_NETWORK_READY role=$ROLE"
host_expect NETWORK_READY
logs_operator() { (exec 5<&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json logs --agent-config /etc/rustusb/agent.json "$@"); }
logs_other() { (exec 5<&-; exec /usr/bin/setpriv --reuid=1002 --regid=1002 --clear-groups /usr/bin/rustusb --json logs --agent-config /etc/rustusb/agent.json "$@"); }
read_pages() {
    page_profile=$1 page_label=$2 page_after=''
    for page_number in $(seq 1 80); do
        page_file="/tmp/read-$page_label-$page_number.json"
        if [ -n "$page_after" ]; then logs_operator --profile "$page_profile" --after "$page_after" > "$page_file"; else logs_operator --profile "$page_profile" > "$page_file"; fi
        jq -e '.schema_version==1 and .event=="logs" and .page.schema_version==1 and (.page.records|length)<=16 and .page.scanned_bytes<=262144 and .page.partial_tail==false' "$page_file" > /dev/null
        if jq -e '.page.more==false' "$page_file" > /dev/null; then return 0; fi
        page_after=$(jq -er '.page.next_after' "$page_file")
    done
    return 1
}
permission_checks() {
    log_profile=$1
    cp /var/lib/rustusb/native/channels.json /tmp/root-before-log-refusals.json
    jq '{jobs,sessions:(.sessions|map(del(.metrics))),requests}' /var/lib/rustusb/agent/state.json > /tmp/resources-before-log-refusals.json
    if logs_other --profile "$log_profile" > /tmp/logs-denied-uid.stdout 2> /tmp/logs-denied-uid.stderr; then return 1; else rc=$?; fi
    [ "$rc" = 1 ]; echo "$rc" > /tmp/logs-denied-uid.wait
    if logs_operator --profile unknown-profile > /tmp/logs-denied-profile.stdout 2> /tmp/logs-denied-profile.stderr; then return 1; else rc=$?; fi
    [ "$rc" = 1 ]; echo "$rc" > /tmp/logs-denied-profile.wait
    # Cursor bytes cannot be interpreted as a path; this fails before IPC.
    if logs_operator --profile "$log_profile" --after /etc/rustusb/server-key.der > /tmp/logs-denied-path.stdout 2> /tmp/logs-denied-path.stderr; then return 1; else rc=$?; fi
    [ "$rc" = 2 ]; echo "$rc" > /tmp/logs-denied-path.wait
    cp /var/lib/rustusb/native/channels.json /tmp/root-after-log-refusals.json
    jq '{jobs,sessions:(.sessions|map(del(.metrics))),requests}' /var/lib/rustusb/agent/state.json > /tmp/resources-after-log-refusals.json
    cmp /tmp/root-before-log-refusals.json /tmp/root-after-log-refusals.json
    cmp /tmp/resources-before-log-refusals.json /tmp/resources-after-log-refusals.json
    cp /var/lib/rustusb/agent/state.json /tmp/agent-after-log-refusals.json
    # A committed audit need not already be written by the independent worker.
    # Observe an actual caught-up page before requiring its projection below.
    # shellcheck disable=SC2034
    for log_sync_poll in $(seq 1 100); do
        logs_operator --profile "$log_profile" > /tmp/logs-caught-up.json
        if jq -e '.page.status.healthy and .page.status.queued==0 and .page.status.written==.page.status.accepted' /tmp/logs-caught-up.json > /dev/null; then break; fi
        sleep 0.05
    done
    jq -e '.page.status.healthy and .page.status.queued==0 and .page.status.written==.page.status.accepted' /tmp/logs-caught-up.json > /dev/null
    read_pages "$log_profile" "$log_profile-normal"
    if [ "$ROLE" = client ]; then read_pages serial serial-normal; fi
}
flood() {
    flood_count=$1 flood_mark=$2
    cp /var/lib/rustusb/agent/state.json "/tmp/flood-$flood_mark-before.json"
    : > "/tmp/flood-$flood_mark-receipts.jsonl"
    for flood_index in $(seq 1 "$flood_count"); do
        if agent start unknown-profile > /tmp/flood-response.json 2> /tmp/flood-response.stderr; then return 1; else flood_rc=$?; fi
        [ "$flood_rc" = 1 ]
        jq -e '.schema_version==1 and .journal_healthy and .response=={result:"error",code:"permission"}' /tmp/flood-response.json > /dev/null
        jq -c --argjson index "$flood_index" --argjson wait "$flood_rc" --slurpfile response /tmp/flood-response.json '{index:$index,wait:$wait,response:$response[0],sequence:.sequence,audit:(.audit|map(select(.action=="denied" and .actor_uid==1000 and .profile==null))|last)}' /var/lib/rustusb/agent/state.json >> "/tmp/flood-$flood_mark-receipts.jsonl"
    done
    cp /var/lib/rustusb/agent/state.json "/tmp/flood-$flood_mark-after.json"
    echo "LOG_FLOOD_COMMITTED role=$ROLE mark=$flood_mark count=$flood_count"
}
wait_stopped() {
    stop_label=$1 stop_expected=$2
    # shellcheck disable=SC2034
    for stop_poll in $(seq 1 400); do kill -0 "$AGENT_PID" 2>/dev/null || break; sleep 0.05; done
    if kill -0 "$AGENT_PID" 2>/dev/null; then return 1; fi
    if wait "$AGENT_PID"; then stop_rc=0; else stop_rc=$?; fi
    echo "$stop_rc" > "/tmp/wait-agent-$stop_label.txt"
    [ "$stop_rc" = "$stop_expected" ]
    AGENT_PID=''
    log_meta "logs-agent-$stop_label" /var/log/rustusb/agent
}
server_released() {
    release_mark=$1
    # shellcheck disable=SC2034
    for release_poll in $(seq 1 300); do
        cp /var/lib/rustusb/native/channels.json "/tmp/root-$release_mark.json"
        host_baseline "/tmp/host-$release_mark.json"
        cp /var/lib/rustusb/agent/state.json "/tmp/agent-$release_mark.json"
        if jq -e --slurpfile app "/tmp/agent-$release_mark.json" --slurpfile held "/tmp/agent-held-$epoch.json" '
            def identity: {id,instance_id,job_id,profile,role,device_key,principal,attempts:(.attempts|map(del(.proof)))};
            . as $root | .schema_version==2 and .exports==[] and .records==[] and
            ($app[0].jobs|map(.id)|sort)==($held[0].jobs|map(.id)|sort) and
            ($app[0].sessions|map(identity)|sort_by(.id))==($held[0].sessions|map(identity)|sort_by(.id)) and
            ($app[0].sessions|length)>0 and all($app[0].jobs[]; .state=="finished") and
            all($app[0].sessions[]; .terminal and .cleanup=="verified") and
            (.terminals|length)==([$app[0].sessions[].attempts[]]|length) and
            all($app[0].sessions[]; . as $session | all(.attempts[]; . as $attempt |
                [$root.terminals[].terminal|select(.kind==$attempt.kind and .correlation==$attempt.correlation and .resource_id==$attempt.resource_id and .device_key==$session.device_key and .owner_uid==2000 and .proof==(if $attempt.kind=="native_export" then "native_export_restored" else "native_stub_available" end))]|length==1))' "/tmp/root-$release_mark.json" > /dev/null && cmp -s /tmp/host-before.json "/tmp/host-$release_mark.json"; then return 0; fi
        sleep 0.05
    done
    return 1
}
gate_snapshot() {
    gate_mark=$1
    kill -0 "$GATE_PID"; kill -0 "$AGENT_PID"
    process_receipt "$GATE_PID" "gate-$gate_mark"
    process_receipt "$AGENT_PID" "agent-$gate_mark"
    cp /proc/locks "/tmp/gate-$gate_mark-proc-locks.txt"
    thread_receipt "$gate_mark"
    cp /var/log/rustusb/agent/segment-00.ndjson "/tmp/gate-$gate_mark-segment.ndjson"
    gate_fd=''
    for fd in /proc/"$GATE_PID"/fd/*; do if [ "$(readlink "$fd")" = /var/log/rustusb/agent/access.lock ]; then [ -z "$gate_fd" ]; gate_fd=$(basename "$fd"); fi; done
    [ -n "$gate_fd" ]
    jq -nc --arg nonce "$GATE_NONCE" --argjson pid "$GATE_PID" --arg fd "$gate_fd" --arg lock "$(stat -c '%d:%i:%u:%a:%h' /var/log/rustusb/agent/access.lock)" --arg segment "$(stat -c '%d:%i:%u:%a:%h' /var/log/rustusb/agent/segment-00.ndjson)" --arg uptime "$(cat /proc/uptime)" '{nonce:$nonce,pid:$pid,fd:$fd,lock:$lock,segment:$segment,uptime:$uptime}' > "/tmp/gate-$gate_mark.json"
    awk -v pid="$GATE_PID" '$2=="FLOCK" && $4=="WRITE" && $5==pid {found=1} END{exit !found}' /proc/locks
}
writer_blocked() {
    # shellcheck disable=SC2034
    for writer_poll in $(seq 1 100); do
        : > /tmp/logger-thread-observations.jsonl
        writer_found=0
        for task in /proc/"$AGENT_PID"/task/*; do
            thread_comm=$(cat "$task/comm")
            case "$thread_comm" in rustusb-log-*)
                syscall=$(cat "$task/syscall")
                jq -nc --arg tid "$(basename "$task")" --arg comm "$thread_comm" --arg syscall "$syscall" --arg wchan "$(cat "$task/wchan")" '{tid:$tid,comm:$comm,syscall:$syscall,wchan:$wchan}' >> /tmp/logger-thread-observations.jsonl
                if [ "$thread_comm" = rustusb-log-wri ]; then
                    # shellcheck disable=SC2086
                    set -- $syscall
                    if [ "$#" -ge 3 ] && [ "$1" = 73 ] && [ "$3" = 0x2 ]; then
                        syscall_fd=$(( $2 ))
                        if [ "$(readlink "/proc/$AGENT_PID/fd/$syscall_fd")" = /var/log/rustusb/agent/access.lock ]; then writer_found=1; fi
                    fi
                fi ;;
            esac
        done
        [ "$writer_found" = 0 ] || return 0
        sleep 0.05
    done
    return 1
}
gate_start() {
    mkdir -p /run/log-gate; chmod 700 /run/log-gate
    mkfifo -m 600 /run/log-gate/release
    stat -c '%n:%F:%u:%g:%a:%d:%i:%h' /run/log-gate /run/log-gate/release > /tmp/gate-control-metadata.txt
    GATE_NONCE=$(cat /proc/sys/kernel/random/uuid)
    : > /tmp/gate-process.stdout; : > /tmp/gate-process.stderr
    (exec 5<&-; exec /usr/bin/flock -x /var/log/rustusb/agent/access.lock /bin/sh /logging-gate.sh "$GATE_NONCE") > /tmp/gate-process.stdout 2> /tmp/gate-process.stderr &
    GATE_PID=$!
    # shellcheck disable=SC2034
    for gate_poll in $(seq 1 100); do
        if grep -Fx "LOG_GATE_HELD nonce=$GATE_NONCE" /tmp/gate-process.stdout > /dev/null; then break; fi
        kill -0 "$GATE_PID"; sleep 0.05
    done
    grep -Fx "LOG_GATE_HELD nonce=$GATE_NONCE" /tmp/gate-process.stdout
    gate_snapshot before
}
busy_readers() {
    READERS=''
    for reader_id in 1 2 3 4 5; do
        (exec 5<&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json logs --agent-config /etc/rustusb/agent.json --profile server --follow > "/tmp/read-busy-$reader_id.stdout" 2> "/tmp/read-busy-$reader_id.stderr") &
        reader_pid=$!; READERS="$READERS $reader_pid"
        echo "$reader_pid" > "/tmp/read-busy-$reader_id.pid"
        # Observe the exec transition, not the parent shell/setpriv executable.
        # shellcheck disable=SC2034
        for reader_exec_poll in $(seq 1 100); do
            [ "$(readlink "/proc/$reader_pid/exe")" != /usr/bin/rustusb ] || break
            kill -0 "$reader_pid"; sleep 0.01
        done
        [ "$(readlink "/proc/$reader_pid/exe")" = /usr/bin/rustusb ]
        [ "$(awk '/^Uid:/{print $2 ":" $3 ":" $4 ":" $5}' "/proc/$reader_pid/status")" = 1000:1000:1000:1000 ]
        process_receipt "$reader_pid" "reader-$reader_id"
    done
    # shellcheck disable=SC2034
    for busy_poll in $(seq 1 80); do
        if grep -l '"event":"logs_busy"' /tmp/read-busy-*.stdout > /tmp/read-busy-observed.txt; then break; fi
        sleep 0.05
    done
    [ -s /tmp/read-busy-observed.txt ]
    # Busy can be observed before both already-owned reader threads reach flock.
    # Wait for the actual syscalls; cancelling their RPCs does not release them.
    # shellcheck disable=SC2034
    for reader_block_poll in $(seq 1 100); do
        thread_receipt busy
        if jq -se '[.[]|select(.comm=="rustusb-log-rea" and (.syscall|test("^73 0x[0-9a-f]+ 0x1 ")) and .target=="/var/log/rustusb/agent/access.lock")]|length==2' /tmp/threads-busy.jsonl > /dev/null; then break; fi
        sleep 0.05
    done
    jq -se '[.[]|select(.comm=="rustusb-log-rea" and (.syscall|test("^73 0x[0-9a-f]+ 0x1 ")) and .target=="/var/log/rustusb/agent/access.lock")]|length==2' /tmp/threads-busy.jsonl > /dev/null
    # Readers may have arrived in any order; no claim that process5 arrived fifth.
    for reader_pid in $READERS; do kill -TERM "$reader_pid" 2>/dev/null || true; done
    for reader_pid in $READERS; do if wait "$reader_pid"; then reader_rc=0; else reader_rc=$?; fi; printf '%s %s\n' "$reader_pid" "$reader_rc" >> /tmp/read-busy-waits.txt; done
    cp /var/lib/rustusb/agent/state.json /tmp/agent-after-busy.json
    echo LOG_READERS_BUSY
}
if [ "$ROLE" = server ]; then
    STAGE=server-devices
    for name in xhci-hcd xhci-pci-renesas xhci-pci usbip-core usbip-host; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    [ -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
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
    HID_KEY="native:$HID_ID" SERIAL_KEY="native:$SERIAL_ID"
    host_baseline /tmp/host-before.json
    jq -e 'length==2 and all(.[]; (.configuration|tonumber)==1 and (.interfaces|length)==1) and ([.[].interfaces[].driver]|sort)==["ftdi_sio","usbhid"]' /tmp/host-before.json > /dev/null
    for bus in "$HID_BUS" "$SERIAL_BUS"; do
        node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$bus/busnum")" "$(cat "/sys/bus/usb/devices/$bus/devnum")")
        [ -c "$node" ] && [ "$(stat -c '%u' "$node")" = 0 ]; chmod 600 "$node"
    done
    jq -n --arg hid "$HID_ID" --arg serial "$SERIAL_ID" '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_native_device_ids:[$hid,$serial]}' > /etc/rustusb/policy.json
    start_helper
    jq -nc --arg hid "$HID_KEY" --arg serial "$SERIAL_KEY" '[$hid,$serial]' > /tmp/keys.json
    echo "AGENT_KEYS $(cat /tmp/keys.json)"
    PIN=$(cat /etc/rustusb/client-pin.txt)
    jq -n --arg listen "$LISTEN" --arg pin "$PIN" --slurpfile keys /tmp/keys.json '{schema_version:2,data_link:{protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,listen:$listen,devices:$keys[0],principals:[{id:"qemu-client",pins:[$pin],devices:$keys[0]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server.json
    chown 2000:2000 /etc/rustusb/server-key.der; chmod 600 /etc/rustusb/server-key.der
    echo '[{"id":"server","backend":"native","config":"/etc/rustusb/server.json"}]' > /tmp/agent-profiles.json

    manifest
    for epoch in 1 2 3; do
        STAGE="server-epoch-$epoch"
        agent_start "$epoch"
        process_receipt "$AGENT_PID" "agent-$epoch"
        start_job server
        wait_sessions server "/tmp/server-ready-$epoch.json" '.response.sessions|map(select(.role=="export" and .state=="Available" and .terminal==false))|length==2'
        native_host_phase "exported-$epoch" exported
        agent_snapshot "exported-$epoch"
        echo "LOG_SERVER_READY epoch=$epoch"
        host_expect "HELD$epoch"
        wait_sessions server "/tmp/server-held-$epoch.json" '.response.sessions|map(select(.role=="incoming" and .state=="Connected" and .terminal==false))|length==2'
        native_host_phase "held-$epoch" held; agent_snapshot "held-$epoch"
        agent health server > "/tmp/health-server-$epoch.json"
        echo "LOG_SERVER_HELD epoch=$epoch"
        if [ "$epoch" = 1 ]; then
            host_expect NORMAL1
            permission_checks server
            flood 64 normal
            read_pages server server-rotated
            echo LOG_SERVER_NORMAL_COMPLETE
            host_expect STOP1
            kill -TERM "$AGENT_PID"; wait_stopped 1 0
        elif [ "$epoch" = 2 ]; then
            host_expect FAULT2
            gate_start
            flood 512 blocked
            writer_blocked
            busy_readers
            gate_snapshot before-stop
            cp /var/lib/rustusb/agent/state.json /tmp/agent-before-stop-held.json
            echo "LOG_AGENT_TERM pid=$AGENT_PID"
            cat /proc/uptime > /tmp/term-sent.uptime
            kill -TERM "$AGENT_PID"
            server_released clean-held
            # The async service unlinks this endpoint only after its actual owner
            # and bounded request tasks join. The outer synchronous owner then
            # waits for the still-blocked logger threads, before process exit.
            # shellcheck disable=SC2034
            for joined_poll in $(seq 1 200); do
                kill -0 "$AGENT_PID"
                cat "/proc/$AGENT_PID/syscall" > /tmp/agent-main-after-clean.syscall
                if [ ! -e /run/agent/control.sock ] && [ ! -L /run/agent/control.sock ] && grep '^202 ' /tmp/agent-main-after-clean.syscall > /dev/null; then break; fi
                sleep 0.05
            done
            [ ! -e /run/agent/control.sock ] && [ ! -L /run/agent/control.sock ]
            grep '^202 ' /tmp/agent-main-after-clean.syscall > /dev/null
            cat "/proc/$AGENT_PID/wchan" > /tmp/agent-main-after-clean.wchan
            jq -nc --argjson pid "$AGENT_PID" --arg uptime "$(cat /proc/uptime)" '{pid:$pid,control_absent:true,uptime:$uptime}' > /tmp/agent-control-after-clean.json
            gate_snapshot after-clean
            echo LOG_ROOT_CLEAN_GATE_HELD
            host_expect RELEASE_GATE
            gate_snapshot before-release
            cat /proc/uptime > /tmp/gate-release-request.uptime
            printf '%s\n' "$GATE_NONCE" | timeout 2 tee /run/log-gate/release > /dev/null
            if wait "$GATE_PID"; then gate_rc=0; else gate_rc=$?; fi
            echo "$gate_rc" > /tmp/wait-gate.txt
            [ "$gate_rc" = 0 ]; GATE_PID=''
            cat /proc/uptime > /tmp/gate-reaped.uptime
            wait_stopped 2 1
            grep -F 'logging failed after the requested operation' /tmp/agent-2.log
            grep -F 'logging lost records; the requested operation may have completed' /tmp/agent-2.log
        else
            host_expect STOP3
            kill -TERM "$AGENT_PID"; wait_stopped 3 0
        fi
        server_released "restored-$epoch"
        cp "/tmp/host-restored-$epoch.json" "/tmp/host-stopped-$epoch.json"
        agent_snapshot "stopped-$epoch"
        echo "LOG_SERVER_STOPPED epoch=$epoch"
    done
    host_expect FINAL
else
    STAGE=client-prepare
    for name in usbip-core vhci-hcd; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    host_read; printf '%s\n' "$CONTROL" > /tmp/keys.json
    jq -e 'length==2 and all(.[]; startswith("native:"))' /tmp/keys.json > /dev/null
    HID_KEY=$(jq -er '.[0]' /tmp/keys.json) SERIAL_KEY=$(jq -er '.[1]' /tmp/keys.json)
    jq -n --slurpfile keys /tmp/keys.json '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_imports:[{server_id:"qemu-server",device_keys:$keys[0]}]}' > /etc/rustusb/policy.json
    start_helper
    chown 2000:2000 /etc/rustusb/client-key.der; chmod 600 /etc/rustusb/client-key.der
    for profile in hid serial; do
        if [ "$profile" = hid ]; then key=$HID_KEY; else key=$SERIAL_KEY; fi
        jq -n --arg host "$HOST" --arg key "$key" '{schema_version:2,data_link:{protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,server_id:"qemu-server",endpoint:{host:$host,port:3241},server_name:"rustusb-qemu.test",device:$key,ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/client.der"],private_key:"/etc/rustusb/client-key.der"}' > "/etc/rustusb/client-$profile.json"
    done
    echo '[{"id":"hid","backend":"client","config":"/etc/rustusb/client-hid.json"},{"id":"serial","backend":"client","config":"/etc/rustusb/client-serial.json"}]' > /tmp/agent-profiles.json

    manifest
    for epoch in 1 2 3; do
        STAGE="client-epoch-$epoch"
        host_expect "START$epoch"
        agent_start "$epoch"
        process_receipt "$AGENT_PID" "agent-$epoch"
        agent discover hid > "/tmp/catalog-$epoch.json"
        jq -e --slurpfile keys /tmp/keys.json '.response.result=="catalog" and (.response.devices|map(.device_key)|sort)==($keys[0]|sort)' "/tmp/catalog-$epoch.json" > /dev/null
        for profile in hid serial; do
            start_job "$profile"
            wait_sessions "$profile" "/tmp/client-$profile-connected-$epoch.json" '.response.sessions|map(select(.state=="Connected" and .evidence=="local_enumeration" and .terminal==false))|length==1'
        done
        record_client_locals "$epoch"; agent_snapshot "held-$epoch"
        for health_profile in hid serial; do agent health "$health_profile" > "/tmp/health-$health_profile-$epoch.json"; done
        actual_io "$epoch" yes
        if [ "$epoch" = 1 ]; then
            permission_checks hid
            echo LOG_CLIENT_NORMAL_COMPLETE
            host_expect CLOSE1
        elif [ "$epoch" = 2 ]; then
            host_expect CHECK_CLOSED2
            for profile in hid serial; do wait_sessions "$profile" "/tmp/client-$profile-ended-2.json" '.response.sessions|map(select(.terminal==false))|length==0'; done
            await_empty /tmp/vhci-eof-held.txt
            [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
            local_gone eof-held
            agent_snapshot eof-held
            echo LOG_CLIENT_EOF_GATE_HELD
        else host_expect CLOSE3; fi
        kill -TERM "$AGENT_PID"; wait_stopped "$epoch" 0
        await_empty "/tmp/vhci-stopped-$epoch.txt"
        [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
        local_gone "$epoch"
        agent_snapshot "stopped-$epoch"
        echo "LOG_CLIENT_STOPPED epoch=$epoch"
    done
    host_expect FINAL
fi
STAGE=helper-stop
kill -TERM "$HELPER_PID"; wait_exit "$HELPER_PID" helper; HELPER_PID=''
log_meta logs-helper-final /var/log/rustusb/helper
STAGE=complete
echo "RUSTUSB_LOGGING_RESULT: PASS role=$ROLE"
