#!/bin/sh
# Composed after the audited secure harness PID1/network/artifact helpers.
# This fragment cannot run alone and exposes no production test-input RPC.
[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2
agent() { (exec 5<&-; /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json "$@"); }
agent_other() { (exec 5<&-; /usr/bin/setpriv --reuid=1002 --regid=1002 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json "$@"); }
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
    cp /var/lib/rustusb/agent/state.json /tmp/agent-before-denied.json
    if [ -f /var/lib/rustusb/native/channels.json ]; then cp /var/lib/rustusb/native/channels.json /tmp/root-before-denied.json; else echo absent > /tmp/root-before-denied.json; fi
    if agent_other start "$1" > /tmp/denied-uid.json 2> /tmp/denied-uid.log; then return 1; else denied_rc=$?; fi
    [ "$denied_rc" = 1 ]
    if agent start unknown-profile > /tmp/denied-profile.json 2> /tmp/denied-profile.log; then return 1; else denied_rc=$?; fi
    [ "$denied_rc" = 1 ]
    for denied_file in /tmp/denied-uid.json /tmp/denied-profile.json; do jq -e '.schema_version==1 and .journal_healthy and .response=={result:"error",code:"permission"}' "$denied_file" > /dev/null; done
    cp /var/lib/rustusb/agent/state.json /tmp/agent-after-denied.json
    jq -e --slurpfile before /tmp/agent-before-denied.json '.jobs==$before[0].jobs and .sessions==$before[0].sessions and .requests==$before[0].requests and ([.audit[]|select(.action=="denied" and .failure=="permission")]|length)==2' /tmp/agent-after-denied.json > /dev/null
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
        cat "$EVENT" > "/tmp/input-$IO_ROUND.bin" & INPUT_PID=$!
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
    for epoch in 1 2; do
        STAGE="server-epoch-$epoch"
        start_job server
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
STAGE=complete
echo "RUSTUSB_AGENT_RESULT: PASS role=$ROLE"
