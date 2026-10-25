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
        echo "RUSTUSB_AGENT_RESULT: FAIL role=$ROLE stage=$STAGE exit=$status"
        failure_journal native-channels /var/lib/rustusb/native/channels.json
        failure_journal agent-state /var/lib/rustusb/agent/state.json
        failure_journal physical-journal /var/lib/rustusb/physical/journal.json
        dmesg > /tmp/failure-dmesg.log
        cat /proc/net/tcp /proc/net/tcp6 > /tmp/failure-tcp.log
    fi
    dump_artifacts
    artifact_status=$?
    if [ "$artifact_status" != 0 ]; then
        echo "RUSTUSB_AGENT_RESULT: FAIL role=$ROLE stage=artifact-dump original_exit=$status artifact_exit=$artifact_status"
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
[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2
#!/bin/sh
# Test-only disposable guest actions; no product test RPC or forged OS identity.
settled_refresh() {
    case "$1" in true|false) ;; *) return 2;; esac
    gui_dom '!document.getElementById("refresh").disabled'
    gui_js "const c=document.querySelector('.settings input[type=checkbox]');c.checked=$1;document.querySelector('.settings-actions button').click();return true;" /run/decision-refresh-toggle.json
    gui_wait ".pending==null and .options.auto_refresh==$1" /run/decision-refresh-options.json
    gui_dom "!document.getElementById('refresh').disabled && document.querySelector('.settings input[type=checkbox]').checked===$1"
}
settled_actor() {
    { echo "PID=$AGENT_PID";cat "/proc/$AGENT_PID/status" "/proc/$AGENT_PID/stat";sha256sum "/proc/$AGENT_PID/exe"; } >"$1"
}
settled_refusal() {
    cp /var/lib/rustusb/agent/state.json /tmp/decision-before.json
    gui_wait '.rules.rules[0].revision=="2" and .rules.decision_support and .options.auto_refresh==false and .pending==null' /tmp/decision-observed-old.json
    kill -STOP "$AGENT_PID"
    for poll in $(seq 1 100); do grep -q '^State:.*T' "/proc/$AGENT_PID/status" && break;sleep .01;done
    cat "/proc/$AGENT_PID/status" "/proc/$AGENT_PID/stat" >/tmp/decision-stopped-agent.txt
    gui_rule_submit service-cas-refusal
    cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/decision-confirm-refusal.json
    gui_wait '.pending!=null and .unresolved_rule.settled_protocol==true' /tmp/decision-pending.json
    gui_http POST "/session/$GUI_SESSION/refresh" '{}' /tmp/decision-renderer-refresh.json
    gui_wait '.pending!=null and .unresolved_rule.settled_protocol==true' /tmp/decision-reloaded.json
    gui_wait '.pending==null and .unresolved_rule!=null and (.rules.result|contains("Response unknown"))' /tmp/decision-unknown.json
    kill -CONT "$AGENT_PID"
    for poll in $(seq 1 300); do
        if jq -e '.rule_rejections.sequence==1 and (.rule_rejections.entries|length)==1' /var/lib/rustusb/agent/state.json >/dev/null;then break;fi
        kill -0 "$AGENT_PID";sleep .05
    done
    cp /var/lib/rustusb/agent/state.json /tmp/decision-late.json
    settled_actor /tmp/decision-agent-before.txt
    kill -TERM "$AGENT_PID";wait_exit "$AGENT_PID" agent-decision-restart
    agent_start 1
    settled_actor /tmp/decision-agent-after.txt
    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-restart.json
    gui_dom '[...document.querySelectorAll("button")].some(b=>b.textContent==="Query original rule edit"&&!b.disabled)'
    cp /var/lib/rustusb/agent/state.json /tmp/decision-before-query.json
    gui_js '[...document.querySelectorAll("button")].find(b=>b.textContent==="Query original rule edit").click();return true;' /run/decision-query.json
    gui_confirm;cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/decision-confirm-query.json
    gui_wait '.pending==null and .unresolved_rule==null and .last_rule_decision.decision.decision=="rejected" and (.rules.result|startswith("Rejected original rule edit "))' /tmp/decision-resolved.json
    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-query.json
    DECISION_ID=$(jq -er '.value.last_rule_decision.original.request_id' /tmp/decision-resolved.json)
    set +e
    agent rule server --settled retry "$DECISION_ID" >/tmp/decision-cli-query.jsonl 2>/tmp/decision-cli-query.log
    decision_cli_exit=$?
    set -e
    echo "$decision_cli_exit" >/tmp/decision-cli-query.wait
    [ "$decision_cli_exit" = 1 ]
    jq -s -e 'length==2 and .[0].event=="rule_decision_query" and .[0].protocol=="settled" and .[0].request_id==.[1].response.request_id and .[1].response.result=="rule_decided" and .[1].response.replayed and .[1].response.decision=={decision:"rejected",sequence:1,failure:"state"}' /tmp/decision-cli-query.jsonl >/dev/null
    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-cli-query.json
    gui_rules_settings
    gui_dom 'document.getElementById("rule-restore")&&!document.getElementById("rule-restore").disabled'
    gui_js 'document.getElementById("rule-restore").click();return true;' /run/decision-restore.json
    gui_confirm;cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/decision-confirm-restore.json
    gui_dom 'document.getElementById("rule-name")?.value==="Preserved stale draft"'
    gui_js 'return {name:document.getElementById("rule-name").value,originalRevision:document.querySelector(".rule-editor .section-note").textContent.includes("Original revision 2")?"2":null,profile:document.querySelector(".rule-editor .section-note").textContent.includes("Draft profile: server")?"server":null,settled:document.getElementById("rule-rejected").textContent.includes("Settled rejection")};' /tmp/decision-restored-draft.json
    settled_refresh true
}

# Composed only into owned disposable PID1, never a product testing API.
GUI_XVFB='' GUI_DRIVER='' GUI_PID='' GUI_LABEL='' GUI_SESSION=''
gui_infra() {
    mkdir -p /home/operator/.config/rustusb /run/user/1000
    chown -R 1000:1000 /home/operator /run/user/1000
    chmod 700 /home/operator /home/operator/.config /home/operator/.config/rustusb /run/user/1000
    export DISPLAY=:88 GDK_BACKEND=x11 TAURI_WEBVIEW_AUTOMATION=true LIBGL_ALWAYS_SOFTWARE=1 HOME=/home/operator XDG_RUNTIME_DIR=/run/user/1000 XDG_CACHE_HOME=/home/operator/.cache
    cp /etc/rustusb/gui-runtime-elfs.json /tmp/gui-runtime-elfs.json
    /usr/bin/Xvfb :88 -screen 0 1280x900x24 -nolisten tcp >/tmp/gui-xvfb.stdout 2>/tmp/gui-xvfb.stderr & GUI_XVFB=$!
    for gui_poll in $(seq 1 100); do [ ! -S /tmp/.X11-unix/X88 ] || break; kill -0 "$GUI_XVFB"; sleep .05; done
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/sh -c '[ ! -r /etc/rustusb/server-key.der ] && [ ! -r /etc/rustusb/client-key.der ]'
    echo 0 >/tmp/gui-operator-key-unreadable.txt
}
gui_http() {
    local method=$1 route=$2 body=$3 dest=$4
    /usr/bin/curl --silent --show-error --fail-with-body --max-time 35 -X "$method" -H 'Content-Type: application/json' --data "$body" "http://127.0.0.1:15888$route" >"$dest"
}
gui_js() {
    local script=$1 dest=$2
    jq -n --arg script "$script" '{script:$script,args:[]}' >/run/gui-request.json
    gui_http POST "/session/$GUI_SESSION/execute/sync" "$(cat /run/gui-request.json)" "$dest"
    jq -e 'has("value") and ((.value|type)!="object" or (.value|has("error")|not))' "$dest" >/dev/null
}
gui_snapshot() {
    jq -n '{script:"const done=arguments[arguments.length-1];window.__TAURI__.core.invoke(\"gui_snapshot\").then(done,e=>done({error:String(e)}));",args:[]}' >/run/gui-request.json
    gui_http POST "/session/$GUI_SESSION/execute/async" "$(cat /run/gui-request.json)" "$1"
    jq -e '.value.error? == null' "$1" >/dev/null
}
gui_wait() {
    local predicate=$1 dest=$2
    for gui_poll in $(seq 1 200); do
        gui_snapshot "$dest"
        if jq -e ".value | $predicate" "$dest" >/dev/null; then return 0; fi
        kill -0 "$GUI_PID"; sleep .05
    done
    return 1
}
gui_dom() {
    local predicate=$1
    for gui_poll in $(seq 1 200); do
        gui_js "return !!($predicate);" /run/gui-dom.json
        if jq -e '.value==true' /run/gui-dom.json >/dev/null; then return 0; fi
        kill -0 "$GUI_PID"; sleep .05
    done
    return 1
}
gui_open() {
    GUI_LABEL=$1; local profile=$2
    [ -z "$GUI_PID" ] && [ -z "$GUI_DRIVER" ]
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/WebKitWebDriver --host=127.0.0.1 --port=15888 >"/tmp/gui-$GUI_LABEL-driver.stdout" 2>"/tmp/gui-$GUI_LABEL-driver.stderr" & GUI_DRIVER=$!
    for gui_poll in $(seq 1 100); do
        if /usr/bin/curl -fsS http://127.0.0.1:15888/status > /run/gui-driver.json 2>/dev/null; then break; fi
        kill -0 "$GUI_DRIVER"; sleep .05
    done
    jq -n --arg session_label "$GUI_LABEL" '{capabilities:{alwaysMatch:{"webkitgtk:browserOptions":{binary:"/usr/bin/gui-launcher",args:[$session_label,"--manifest","/etc/rustusb/agent.json","--settings-directory","/home/operator/.config/rustusb"]}}}}' >/run/gui-session-request.json
    gui_http POST /session "$(cat /run/gui-session-request.json)" "/tmp/gui-$GUI_LABEL-session.json"
    { cat "/proc/$GUI_DRIVER/status" "/proc/$GUI_DRIVER/stat"; sha256sum "/proc/$GUI_DRIVER/exe"; } >"/tmp/gui-$GUI_LABEL-driver-actor.txt"
    GUI_SESSION=$(jq -er '.value.sessionId' "/tmp/gui-$GUI_LABEL-session.json")
    GUI_PID=$(cat "/tmp/gui-$GUI_LABEL.pid")
    { echo "PID=$GUI_PID"; cat "/proc/$GUI_PID/status" "/proc/$GUI_PID/stat"; echo "EXE=$(readlink "/proc/$GUI_PID/exe")"; sha256sum "/proc/$GUI_PID/exe";
      for gui_fd in /proc/"$GUI_PID"/fd/*; do echo "FD=$(basename "$gui_fd") TARGET=$(readlink "$gui_fd")"; done;
    } >"/tmp/gui-$GUI_LABEL-actor.txt"
    gui_wait '.profiles|length>0' /run/gui-ready.json
    gui_dom '!document.getElementById("profile").disabled'
    gui_js "const p=document.getElementById('profile');p.value='$profile';p.dispatchEvent(new Event('change'));return true;" /run/gui-select-profile.json
    gui_wait ".profile==\"$profile\" and .pending==null" /run/gui-profile-ready.json
    gui_dom "document.getElementById('profile').value==='$profile' && !document.getElementById('start').disabled"
}
gui_confirm() {
    gui_dom 'document.getElementById("confirmation").open'
    gui_js 'return {text:document.getElementById("confirm-description").textContent,focus:document.activeElement.value};' "/tmp/gui-$GUI_LABEL-confirm.json"
    jq -e '.value.focus=="cancel"' "/tmp/gui-$GUI_LABEL-confirm.json" >/dev/null
    gui_js 'document.getElementById("confirm-action").click();return true;' /run/gui-confirm.json
}
gui_close() {
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/gui-native-close "$GUI_PID" >"/tmp/gui-$GUI_LABEL-close.txt" 2>&1
    for gui_poll in $(seq 1 200); do
        [ -e "/proc/$GUI_PID/status" ] || { [ ! -f "/tmp/gui-$GUI_LABEL.wait" ] || break; }
        sleep .05
    done
    [ ! -e "/proc/$GUI_PID/status" ] && [ "$(cat "/tmp/gui-$GUI_LABEL.wait")" = 0 ]
    grep -F 'local worker joined' "/tmp/gui-$GUI_LABEL.stderr" >/dev/null
    echo "GUI_NATIVE_CLOSED role=$ROLE label=$GUI_LABEL pid=$GUI_PID wait=0"
    GUI_PID=''
    kill -TERM "$GUI_DRIVER"; set +e; wait "$GUI_DRIVER"; gui_driver_exit=$?; set -e
    echo "$gui_driver_exit" >"/tmp/gui-$GUI_LABEL-driver.wait"; GUI_DRIVER=''; GUI_SESSION=''
}
gui_start_job() {
    local profile=$1 label="start-$1-$AGENT_EPOCH" request job
    cp /var/lib/rustusb/agent/state.json "/tmp/gui-$label-before.json"
    gui_open "$label" "$profile"
    gui_js 'document.getElementById("start").click();return true;' /run/gui-start-click.json
    gui_confirm
    gui_wait '(.status|startswith("Admitted job ")) and .unresolved==null and .pending==null' "/tmp/gui-$label-admitted.json"
    gui_dom 'document.getElementById("status").textContent.includes("Admitted job")'
    gui_js 'return document.body.innerText;' "/tmp/gui-$label-dom.json"
    cp /var/lib/rustusb/agent/state.json "/tmp/gui-$label-after.json"
    request=$(jq -er --arg profile "$profile" --slurpfile old "/tmp/gui-$label-before.json" '[.requests[]|select(.id as $id|($old[0].requests|map(.id)|index($id))==null)]|select(length==1 and .[0].uid==1000 and .[0].profile==$profile)|.[0].id' "/tmp/gui-$label-after.json")
    agent start "$profile" --retry --request-id "$request" >"/tmp/start-$profile-$AGENT_EPOCH.json" 2>"/tmp/start-$profile-$AGENT_EPOCH.log"
    jq -e '.response.result=="started" and .response.replayed and .response.job.operator_uid==1000' "/tmp/start-$profile-$AGENT_EPOCH.json" >/dev/null
    cp /var/lib/rustusb/agent/state.json "/tmp/gui-$label-after-retry.json"
    jq -e --slurpfile old "/tmp/gui-$label-after.json" '.requests==$old[0].requests' "/tmp/gui-$label-after-retry.json" >/dev/null
    job=$(jq -er '.response.job.id' "/tmp/start-$profile-$AGENT_EPOCH.json")
    gui_wait "any(.jobs[];.id==\"$job\" and .state==\"running\" and .stop_requested==false) and any(.sessions[];.terminal==false and (.state==\"Connected\" or .state==\"Available\"))" "/tmp/gui-$label-active.json"
    gui_close
    agent jobs "$profile" >"/tmp/gui-$label-after-close.json"
    jq -e --arg id "$job" 'any(.response.jobs[];.id==$id and .state=="running" and .stop_requested==false)' "/tmp/gui-$label-after-close.json" >/dev/null
    kill -0 "$AGENT_PID"
    echo "AGENT_START_RETURNED role=$ROLE profile=$profile epoch=$AGENT_EPOCH wait=0"
}
gui_stop() {
    local profile=$1 kind=$2 id=$3 label=$4
    gui_open "$label" "$profile"
    gui_wait "any(.$kind[];.id==\"$id\")" /run/gui-stop-ready.json
    gui_js 'document.querySelectorAll("nav button")[2].click();return true;' /run/gui-connections.json
    gui_dom 'document.getElementById("page-title").textContent==="Connections" && !document.getElementById("start").disabled'
    if [ "$ROLE:$label" = client:stop-job-serial-2 ]; then
        gui_wait 'any(.health[];.link.protocol=="raw_v1" and .link.last_round_trip_micros==null)' /tmp/gui-raw-health.json
        gui_dom 'document.querySelector("details summary")'
        gui_js 'document.querySelector("details summary").click();return document.body.innerText;' /tmp/gui-raw-health-dom.json
        jq -e '.value|contains("RTT unavailable")' /tmp/gui-raw-health-dom.json >/dev/null
    fi
    gui_js "const rows=[...document.querySelectorAll('tbody tr')].filter(r=>r.cells[0]?.firstChild?.firstChild?.textContent==='$id');if(rows.length!==1)throw Error('exact row absent/ambiguous');rows[0].querySelector('button').click();return true;" /run/gui-stop-click.json
    gui_confirm
    jq -e --arg id "$id" '.value.text|contains($id)' "/tmp/gui-$GUI_LABEL-confirm.json" >/dev/null
    gui_wait '(.status|startswith("Stop accepted")) and .pending==null' "/tmp/gui-$GUI_LABEL-accepted.json"
    gui_dom 'document.getElementById("status").textContent.includes("Stop accepted")'
    gui_js 'return document.body.innerText;' "/tmp/gui-$GUI_LABEL-dom.json"
    gui_close
}
gui_stop_job() {
    local profile=$1 id
    id=$(jq -er '.response.job.id' "/tmp/start-$profile-$AGENT_EPOCH.json")
    gui_stop "$profile" jobs "$id" "stop-job-$profile-$AGENT_EPOCH"
    wait_sessions "$profile" "/tmp/gui-stop-job-$profile-$AGENT_EPOCH-sessions.json" '.response.sessions|length>0 and all(.[]; .terminal and .cleanup=="verified")'
}
gui_remote() {
    gui_open remote-catalog hid
    gui_js 'document.getElementById("eligible").checked=true;document.getElementById("eligible").dispatchEvent(new Event("change"));document.querySelectorAll("nav button")[1].click();return true;' /run/gui-remote-click.json
    gui_wait '.remote.state=="current" and (.remote.devices|length)==2 and .pending==null' /tmp/gui-remote-catalog.json
    gui_dom 'document.querySelectorAll(".device-name").length===2'
    gui_js 'const s=document.getElementById("search");s.value="does-not-match";s.dispatchEvent(new Event("input"));return document.getElementById("content").innerText;' /tmp/gui-remote-search-empty.json
    gui_js 'const s=document.getElementById("search");s.value="";s.dispatchEvent(new Event("input"));document.querySelector(".device-name").click();return {text:document.getElementById("content").innerText,filterHidden:document.getElementById("eligible").closest("label").hidden};' /tmp/gui-remote-detail.json
    gui_http GET "/session/$GUI_SESSION/screenshot" '' /tmp/gui-remote-screenshot.json
    gui_close
}
gui_infra_stop() {
    [ -z "$GUI_PID" ] && [ -z "$GUI_DRIVER" ]
    kill -TERM "$GUI_XVFB"; set +e; wait "$GUI_XVFB"; gui_xvfb_exit=$?; set -e
    echo "$gui_xvfb_exit" >/tmp/gui-xvfb.wait; GUI_XVFB=''
}
gui_cleanup() {
    local original=$?
    [ -z "${AGENT_PID:-}" ] || kill -CONT "$AGENT_PID" 2>/dev/null || true
    trap - EXIT; set +e
    if [ -z "$GUI_PID" ] && [ -n "$GUI_LABEL" ] && [ -f "/tmp/gui-$GUI_LABEL.pid" ]; then
        candidate=$(cat "/tmp/gui-$GUI_LABEL.pid")
        case "$candidate" in ''|*[!0-9]*) ;; *) [ "$(readlink "/proc/$candidate/exe")" != /usr/bin/rustusb-gui ] || GUI_PID=$candidate ;; esac
    fi
    for child in "$GUI_PID" "$GUI_DRIVER" "$GUI_XVFB"; do
        [ -n "$child" ] || continue
        kill -TERM "$child" 2>/dev/null
        for gui_poll in $(seq 1 150); do [ -e "/proc/$child/status" ] || break; grep -q '^State:.*Z' "/proc/$child/status" && break; sleep .05; done
        if [ -e "/proc/$child/status" ] && ! grep -q '^State:.*Z' "/proc/$child/status"; then echo "GUI_FORCED_CHILD $child";kill -KILL "$child";original=1;fi
    done
    for child in "$GUI_DRIVER" "$GUI_XVFB"; do
        [ -n "$child" ] || continue
        wait "$child"; child_wait=$?
        echo "$child_wait" >"/tmp/gui-cleanup-$child.wait"
    done
    gui_original_result() { return "$original"; }
    gui_original_result
    cleanup
}
trap gui_cleanup EXIT

#!/bin/sh
# Disposable native UI fixture only; every edit uses the original real agent RPC.
gui_rules_settings() {
    gui_dom '!document.getElementById("refresh").disabled'
    gui_js 'document.querySelectorAll("nav button")[5].click();return true;' /run/gui-rules-settings.json
    gui_wait '.rules.current and .pending==null' /run/gui-rules-settings-ready.json
    if jq -e '.value.rules.policy_enabled and .value.rules.grant!=null' /run/gui-rules-settings-ready.json >/dev/null; then
        gui_dom 'document.getElementById("rule-new") && !document.getElementById("rule-new").disabled && !document.getElementById("refresh").disabled'
    else
        gui_dom 'document.querySelector(".automation .section-note")?.textContent==="Policy disabled by administrator" && document.getElementById("rule-new").disabled && !document.getElementById("refresh").disabled'
    fi
}
gui_rules_no_policy() {
    gui_open rules-no-policy server
    gui_rules_settings
    gui_dom 'document.getElementById("rule-new").disabled'
    gui_js 'return {policy:document.querySelector(".automation .section-note").textContent,disabled:document.getElementById("rule-new").disabled};' /tmp/gui-no-policy.json
    cp /var/lib/rustusb/agent/state.json /tmp/gui-no-policy-before.json
    jq -n '{script:"const done=arguments[arguments.length-1];window.__TAURI__.core.invoke(\"gui_action\",{action:{action:\"rule_create\",spec:{name:\"denied\",trigger:\"local_profile_ready\",settle_seconds:2,rearm:\"once\"}}}).then(value=>done({unexpected:value}),e=>done({error:String(e)}));",args:[]}' >/run/gui-request.json
    gui_http POST "/session/$GUI_SESSION/execute/async" "$(cat /run/gui-request.json)" /tmp/gui-no-policy-rejected.json
    cp /var/lib/rustusb/agent/state.json /tmp/gui-no-policy-after.json
    gui_close
}
gui_rule_fill() {
    gui_js "const n=document.getElementById('rule-name');n.value='$1';n.dispatchEvent(new Event('input'));return true;" /run/gui-rule-fill.json
}
gui_rule_submit() {
    gui_js 'document.getElementById("rule-submit").click();return true;' /run/gui-rule-submit.json
    gui_confirm
    cp "/tmp/gui-$GUI_LABEL-confirm.json" "/tmp/gui-rule-confirm-$1.json"
}
gui_rule_revision() {
    gui_wait ".pending==null and .unresolved_rule==null and .rules.current and (.rules.rules|length)==1 and .rules.rules[0].revision==\"$1\"" "/tmp/gui-rule-revision-$1.json"
    gui_dom "document.querySelector('.rule-row .data-id')?.textContent.includes('revision $1 ·') && !document.getElementById('refresh').disabled"
}
gui_rule_edit() {
    gui_js 'document.querySelector("[data-rule-action=edit]").click();return document.getElementById("confirmation").open;' /run/gui-rule-edit.json
    if jq -e '.value==true' /run/gui-rule-edit.json >/dev/null; then
        gui_confirm
        cp "/tmp/gui-$GUI_LABEL-confirm.json" "/tmp/gui-rule-confirm-inspect-$1.json"
    fi
    gui_dom 'document.getElementById("rule-name")'
}
gui_rule_change() {
    gui_js "document.querySelector('[data-rule-action=$1]').click();return true;" /run/gui-rule-change.json
    gui_confirm
    cp "/tmp/gui-$GUI_LABEL-confirm.json" "/tmp/gui-rule-confirm-$1-$AGENT_EPOCH.json"
}
gui_rules_start() {
    gui_open "rules-$AGENT_EPOCH" server
    gui_rules_settings
    if [ "$AGENT_EPOCH" = 1 ]; then
        gui_js 'document.getElementById("rule-new").click();const n=document.getElementById("rule-name");n.value="界".repeat(33);n.dispatchEvent(new Event("input"));document.getElementById("rule-submit").click();return {invalid:n.getAttribute("aria-invalid")==="true",focused:document.activeElement.id==="rule-errors"};' /tmp/gui-rule-invalid.json
        jq -e '.value.invalid and .value.focused' /tmp/gui-rule-invalid.json >/dev/null
        gui_rule_fill 'QEMU GUI fixed pool'
        gui_js 'const n=document.getElementById("rule-name");n.focus();n.setSelectionRange(2,5);return true;' /run/gui-rule-caret.json
        gui_snapshot /run/gui-rule-revision-before.json
        revision=$(jq -er '.value.revision' /run/gui-rule-revision-before.json)
        gui_wait "(.revision|tonumber)>$revision and .pending==null" /tmp/gui-rule-refresh-advanced.json
        sleep 3
        gui_js 'const n=document.getElementById("rule-name");return {name:n.value,focus:document.activeElement.id,start:n.selectionStart,end:n.selectionEnd};' /tmp/gui-rule-draft.json
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-before.json
        kill -STOP "$AGENT_PID"
        for poll in $(seq 1 100); do grep -q '^State:.*T' "/proc/$AGENT_PID/status" && break; sleep .01; done
        cat "/proc/$AGENT_PID/status" "/proc/$AGENT_PID/stat" >/tmp/gui-rule-stopped-agent.txt
        gui_rule_submit create
        gui_wait '.pending!=null and .unresolved_rule!=null' /tmp/gui-rule-pending.json
        gui_http POST "/session/$GUI_SESSION/refresh" '{}' /tmp/gui-rule-renderer-refresh.json
        gui_wait '.pending!=null and .unresolved_rule!=null' /tmp/gui-rule-reloaded.json
        gui_wait '.pending==null and .unresolved_rule!=null and (.rules.result|contains("Response unknown"))' /tmp/gui-rule-unknown.json
        kill -CONT "$AGENT_PID"
        for poll in $(seq 1 300); do
            if jq -e '(.automation.rules|length)==1 and (.automation.mutations|length)==1' /var/lib/rustusb/agent/state.json >/dev/null; then break; fi
            kill -0 "$AGENT_PID"; sleep .05
        done
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-late-commit.json
        RULE_ID=$(jq -er '.automation.rules[0].id' /tmp/gui-rule-late-commit.json)
        gui_dom '[...document.querySelectorAll("button")].some(b=>b.textContent==="Query original rule edit" && !b.disabled)'
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-before-resolve.json
        gui_js '[...document.querySelectorAll("button")].find(b=>b.textContent==="Query original rule edit").click();return true;' /run/gui-rule-query.json
        gui_confirm
        cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/gui-rule-confirm-query.json
        gui_wait '.pending==null and .unresolved_rule==null and (.rules.result|startswith("Resolved original rule "))' /tmp/gui-rule-resolved.json
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-after-resolve.json
        # Renderer reload returns to Local; explicitly return to Settings.
        gui_rules_settings
        gui_rule_revision 1
        gui_rule_edit 1
        gui_rule_fill 'GUI first replacement'
        gui_rule_submit replace-2
        gui_rule_revision 2
        gui_rule_edit 2
        gui_rule_fill 'Preserved stale draft'
        settled_refresh false
        agent rule server disable "$RULE_ID" --expected-revision 2 > /tmp/gui-rule-concurrent-disable.jsonl
        settled_refusal
        gui_rule_revision 3
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-stale-before.json
        gui_js 'document.getElementById("rule-submit").click();return {text:document.getElementById("rule-errors").textContent,name:document.getElementById("rule-name").value};' /tmp/gui-rule-stale-draft.json
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-stale-after.json
        gui_rule_edit 3
        gui_rule_fill 'QEMU GUI fixed pool'
        gui_rule_submit replace-4
        gui_rule_revision 4
        gui_rule_change enable
        gui_rule_revision 5
        fire_revision=5
    else
        agent rules server > /tmp/rules-restarted.json
        jq -e '.response.rules|length==1 and all(.[];.enabled and .paused_by_user and .revision==6)' /tmp/rules-restarted.json >/dev/null
        sleep 3
        cp /var/lib/rustusb/agent/state.json /tmp/agent-paused-restart.json
        gui_rule_change enable
        gui_rule_revision 7
        fire_revision=7
    fi
    for auto_poll in $(seq 1 300); do
        agent rules server > "/tmp/rules-fired-$AGENT_EPOCH.json"
        if jq -e --argjson revision "$fire_revision" '.response.rules|length==1 and all(.[];.last_origin!=null and .last_origin.revision==$revision and .last_job!=null)' "/tmp/rules-fired-$AGENT_EPOCH.json" >/dev/null; then break; fi
        kill -0 "$AGENT_PID"; sleep .05
    done
    AUTO_START_ID=$(jq -er '.response.rules[0].last_origin.request_id' "/tmp/rules-fired-$AGENT_EPOCH.json")
    agent start server --retry --request-id "$AUTO_START_ID" > "/tmp/start-server-$AGENT_EPOCH.json"
    jq -e '.response.result=="started" and .response.replayed and .response.job.operator_uid==1000' "/tmp/start-server-$AGENT_EPOCH.json" >/dev/null
    cp /var/lib/rustusb/agent/state.json "/tmp/agent-auto-admitted-$AGENT_EPOCH.json"
    gui_wait '.rules.current and .rules.rules[0].last_job.state=="running"' "/tmp/gui-rule-active-$AGENT_EPOCH.json"
    if [ "$AGENT_EPOCH" = 2 ]; then
        gui_rule_change disable
        gui_rule_revision 8
        gui_wait '.rules.rules[0].enabled==false and .rules.rules[0].last_job.state=="running"' /tmp/gui-rule-disabled-active.json
        gui_rule_change delete
        gui_rule_revision 9
        gui_wait '.rules.rules[0].deleted and .rules.rules[0].last_job.state=="running"' /tmp/gui-rule-deleted-active.json
    fi
    gui_js 'return document.body.innerText;' "/tmp/gui-rule-dom-$AGENT_EPOCH.json"
    gui_http GET "/session/$GUI_SESSION/screenshot" '' "/tmp/gui-rule-screenshot-$AGENT_EPOCH.json"
    gui_close
    agent jobs server > "/tmp/gui-rules-after-close-$AGENT_EPOCH.json"
    echo "AUTOMATION_GUI_START_OBSERVED epoch=$AGENT_EPOCH original_request=$AUTO_START_ID"
}

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
    (exec 5<&-; automatic_agent_exec > "/tmp/agent-$AGENT_EPOCH.stdout" 2> "/tmp/agent-$AGENT_EPOCH.log") &
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
#!/bin/sh
# Disposable paired fixture extension; no production input or synthetic ACK.
automatic_agent_exec() {
    if [ "${AUTOMATION_ENABLED:-0}" = 1 ]; then
        exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run --automation-decisions-policy /etc/rustusb/automation.json
    fi
    exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run
}
automatic_policy() {
    # The old no-policy denial sequence has finished. Join its actual idle owner.
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-no-policy
    jq -e '.schema_version==2 and .jobs==[] and .sessions==[]' /var/lib/rustusb/agent/state.json > /dev/null
    jq -n --slurpfile keys /tmp/keys.json '{schema_version:2,settled_rule_results:true,broker_uid:2000,grants:[{operator_uid:1000,profile:"server",devices:$keys[0],triggers:["local_profile_ready"],rearm:["once"],max_rules:4}]}' > /etc/rustusb/automation.json
    chmod 644 /etc/rustusb/automation.json
    [ "$(stat -c '%u:%a:%h' /etc/rustusb/automation.json)" = 0:644:1 ]
    cp /etc/rustusb/automation.json /tmp/automation-policy.json
    AUTOMATION_ENABLED=1
    agent_start 1
    jq -e '.schema_version==4 and .jobs==[] and .automation.rules==[] and .rule_rejections=={sequence:0,entries:[]}' /var/lib/rustusb/agent/state.json > /dev/null
    echo AUTOMATION_POLICY_ENABLED
}
automatic_start_job() {
    [ "$ROLE" = server ] && [ "$1" = server ]
    if [ "$AGENT_EPOCH" = 1 ]; then
        agent rule server create --name 'QEMU fixed Native pool' --trigger local-profile-ready --settle-seconds 2 --enabled > /tmp/rule-create.jsonl 2> /tmp/rule-create.log
        jq -s -e 'length==2 and .[0].event=="rule_mutation_intent" and .[1].response.result=="rule_mutation_applied" and .[0].request_id==.[1].response.request_id and .[1].response.replayed==false and .[1].response.outcome.revision==1' /tmp/rule-create.jsonl > /dev/null
        RULE_ID=$(jq -sr '.[1].response.outcome.rule_id' /tmp/rule-create.jsonl)
        MUTATION_ID=$(jq -sr '.[0].request_id' /tmp/rule-create.jsonl)
        agent rule server retry "$MUTATION_ID" > /tmp/rule-create-retry.jsonl
        jq -s -e 'length==2 and .[0].event=="rule_mutation_query" and .[1].response.replayed==true and .[1].response.outcome.revision==1' /tmp/rule-create-retry.jsonl > /dev/null
    else
        agent rules server > /tmp/rules-restarted.json
        jq -e '.response.rules|length==1 and all(.[];.enabled and .paused_by_user and .revision==2 and .status=="paused_by_user")' /tmp/rules-restarted.json > /dev/null
        # More than the minimum settle period; no explicit rearm has committed.
        sleep 3
        cp /var/lib/rustusb/agent/state.json /tmp/agent-paused-restart.json
        jq -e '(.jobs|length)==1 and (.automation.origins|length)==1 and (.automation.rules|length)==1 and .automation.rules[0].paused_by_user' /tmp/agent-paused-restart.json > /dev/null
        agent rule server enable "$RULE_ID" --expected-revision 2 > /tmp/rule-enable.jsonl
        jq -s -e 'length==2 and .[0].event=="rule_mutation_intent" and .[1].response.result=="rule_mutation_applied" and .[1].response.outcome.revision==3 and .[1].response.replayed==false' /tmp/rule-enable.jsonl > /dev/null
    fi
    for auto_poll in $(seq 1 300); do
        agent rules server > "/tmp/rules-fired-$AGENT_EPOCH.json"
        if jq -e --argjson revision "$((AGENT_EPOCH*2-1))" '.response.rules|length==1 and all(.[];.last_origin!=null and .last_origin.revision==$revision and .last_job!=null)' "/tmp/rules-fired-$AGENT_EPOCH.json" > /dev/null; then break; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    AUTO_START_ID=$(jq -er '.response.rules[0].last_origin.request_id' "/tmp/rules-fired-$AGENT_EPOCH.json")
    # This real RPC only reads the Start already admitted by the background rule.
    agent start server --retry --request-id "$AUTO_START_ID" > "/tmp/start-server-$AGENT_EPOCH.json"
    jq -e '.schema_version==1 and .journal_healthy and .response.result=="started" and .response.replayed and .response.job.operator_uid==1000' "/tmp/start-server-$AGENT_EPOCH.json" > /dev/null
    agent rule-events server > "/tmp/rule-events-$AGENT_EPOCH.json"
    cp /var/lib/rustusb/agent/state.json "/tmp/agent-auto-admitted-$AGENT_EPOCH.json"
    echo "AUTOMATION_START_OBSERVED epoch=$AGENT_EPOCH original_request=$AUTO_START_ID"
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
    jq -n --arg listen "$LISTEN" --arg pin "$PIN" --slurpfile keys /tmp/keys.json '{schema_version:1,broker_uid:2000,listen:$listen,devices:$keys[0],principals:[{id:"qemu-client",pins:[$pin],devices:$keys[0]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server.json
    chown 2000:2000 /etc/rustusb/server-key.der; chmod 600 /etc/rustusb/server-key.der
    echo '[{"id":"server","backend":"native","config":"/etc/rustusb/server.json"}]' > /tmp/agent-profiles.json
    manifest; agent_start 1; denied server
    gui_infra
    gui_rules_no_policy
    automatic_policy
    for epoch in 1 2; do
        STAGE="server-epoch-$epoch"
        gui_rules_start
        wait_sessions server "/tmp/server-ready-$epoch.json" '.response.sessions|map(select(.role=="export" and .state=="Available" and .evidence=="export_ready" and .terminal==false))|length==2'
        native_host_phase "exported-$epoch" exported
        agent_snapshot "exported-$epoch"
        echo "AGENT_SERVER_READY epoch=$epoch"
        if [ "$epoch" = 1 ]; then
            host_expect HELD1
            wait_sessions server /tmp/server-held-1.json '.response.sessions|map(select(.role=="incoming" and .state=="Connected" and .evidence=="import_reply_flushed" and .terminal==false))|length==2'
            native_host_phase held-1 held; agent_snapshot held-1
            echo AGENT_SERVER_HELD1
            host_expect STOP_HID
            STOP_ID=$(jq -er --arg key "$HID_KEY" '.response.sessions[]|select(.role=="incoming" and .device_key==$key and .terminal==false)|.id' /tmp/server-held-1.json)
            agent stop-session "$STOP_ID" > /tmp/stop-hid.json
            jq -e '.response=={result:"stop_accepted",owner_present:true}' /tmp/stop-hid.json > /dev/null
            wait_sessions server /tmp/server-target-stopped.json '(.response.sessions|map(select(.role=="incoming" and .terminal and .cleanup=="verified"))|length)==1 and (.response.sessions|map(select(.role=="incoming" and .state=="Connected" and .terminal==false))|length)==1'
            agent_snapshot target-stopped
            echo AGENT_SERVER_HID_STOPPED
            host_expect CLIENT_RECOVERED
            wait_sessions server /tmp/server-old-released.json '.response.sessions|map(select(.role=="incoming"))|length==2 and all(.[]; .terminal and .cleanup=="verified")'
        else
            host_expect HELD3
            wait_sessions server /tmp/server-held-3.json '.response.sessions|map(select(.role=="incoming" and .state=="Connected" and .terminal==false))|length==2'
            native_host_phase held-3 held; agent_snapshot held-3
            echo AGENT_SERVER_HELD3
            host_expect CLIENT_CLOSED
        fi
        kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" "agent-$epoch"
        host_baseline "/tmp/host-restored-$epoch.json"; cmp /tmp/host-before.json "/tmp/host-restored-$epoch.json"
        agent_snapshot "stopped-$epoch"
        jq -e '.records==[] and .exports==[] and .schema_version==2' "/tmp/root-stopped-$epoch.json" > /dev/null
        if [ "$epoch" = 1 ]; then
            agent_start 2
            echo AGENT_SERVER_RESTARTED
            host_expect RESTART2
        fi
    done
    echo AGENT_SERVER_FINISHED
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
        jq -n --arg host "$HOST" --arg key "$key" '{schema_version:1,broker_uid:2000,server_id:"qemu-server",endpoint:{host:$host,port:3241},server_name:"rustusb-qemu.test",device:$key,ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/client.der"],private_key:"/etc/rustusb/client-key.der"}' > "/etc/rustusb/client-$profile.json"
    done
    echo '[{"id":"hid","backend":"client","config":"/etc/rustusb/client-hid.json"},{"id":"serial","backend":"client","config":"/etc/rustusb/client-serial.json"}]' > /tmp/agent-profiles.json
    manifest; agent_start 1; denied hid
    host_expect START
    for family in 4 6; do
        if [ "$family" = 4 ]; then peer=192.0.2.1; else peer=fd42:7275:7374::1; fi
        if timeout 5 /usr/bin/nc-openbsd "-$family" -z -v -w 2 "$peer" 3240 > "/tmp/plaintext-$family.log" 2>&1; then exit 1; else plain_rc=$?; fi
        [ "$plain_rc" = 1 ]; grep -F 'Connection refused' "/tmp/plaintext-$family.log"
    done
    agent discover hid > /tmp/catalog-initial.json
    jq -e --slurpfile keys /tmp/keys.json '.response.result=="catalog" and (.response.devices|map(.device_key)|sort)==($keys[0]|sort)' /tmp/catalog-initial.json > /dev/null
    for epoch in 1 2; do
        STAGE="client-epoch-$epoch"
        for profile in hid serial; do
            start_job "$profile"
            wait_sessions "$profile" "/tmp/client-$profile-connected-$epoch.json" '.response.sessions|map(select(.state=="Connected" and .evidence=="local_enumeration" and .terminal==false))|length==1'
        done
        if [ "$epoch" = 1 ]; then round=1; else round=3; fi
        record_client_locals "$round"; agent_snapshot "held-$round"
        actual_io "$round" yes
        if [ "$epoch" = 1 ]; then
            host_expect HID_STOPPED
            wait_sessions hid /tmp/client-hid-stopped.json '.response.sessions|length==1 and all(.[]; .terminal and .cleanup=="verified")'
            [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ -d "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
            native_snapshot /tmp/vhci-target-stopped.txt
            awk '$1=="hs"||$1=="ss"{if($3==6)used++;else if($3!=4)bad=1}END{exit !(used==1&&!bad)}' /tmp/vhci-target-stopped.txt
            agent_snapshot target-stopped
            actual_io 2 no
            host_expect KILL_RESTART
            agent_snapshot before-kill
            kill -KILL "$AGENT_PID"
            if wait "$AGENT_PID"; then kill_rc=0; else kill_rc=$?; fi
            [ "$kill_rc" = 137 ]; echo "$kill_rc" > /tmp/wait-agent-kill.txt
            await_empty /tmp/vhci-after-kill.txt
            [ ! -e "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
            agent_snapshot after-kill
            agent_start 2
            wait_sessions serial /tmp/client-serial-recovered.json '.response.sessions|length==1 and all(.[]; .state=="Disconnected" and .terminal and .cleanup=="verified" and .evidence=="root_journal_proof" and (.attempts|length)>0 and all(.attempts[]; .proof!=null))'
            agent_snapshot recovered
            echo AGENT_CLIENT_RECOVERED
            host_expect START2
        else
            host_expect CLOSE
            kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-2
            await_empty /tmp/vhci-final.txt
            [ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$SERIAL_LOCAL" ]
            agent_snapshot final
            echo AGENT_CLIENT_CLOSED
            host_expect FINISH
        fi
    done
fi
[ "$ROLE" != server ] || gui_infra_stop
STAGE=complete
echo "RUSTUSB_AGENT_RESULT: PASS role=$ROLE"
