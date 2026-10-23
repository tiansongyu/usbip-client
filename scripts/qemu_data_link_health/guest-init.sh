#!/bin/sh
# Appended only to the source-locked disposable agent common init.
[ "${LINK_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2
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
    for phase_file in "/tmp/agent-$1.json" "/tmp/root-$1.json"; do publish "$phase_file"; done
    if [ "$ROLE" = server ]; then publish "/tmp/host-$1.json"; else publish "/tmp/vhci-$1.txt"; fi
}
network_ready
if [ "$ROLE" = server ]; then
@@SERVER_SETUP@@
    manifest; agent_start 1; denied server
    start_profile server listener
    wait_sessions server /tmp/server-ready.json '.response.sessions|map(select(.role=="export" and .state=="Available" and .evidence=="export_ready" and .terminal==false))|length==2'
    native_host_phase initial-exported exported
    agent_snapshot initial-exported
    echo LINK_SERVER_READY
    for phase in c2s s2c both; do
        STAGE="server-$phase"
        host_expect "PRE_$phase"
        wait_sessions server "/tmp/server-pre-$phase.json" '.response.sessions|map(select(.role=="incoming" and .state=="Connected" and .terminal==false))|length==2'
        HID_SESSION=$(jq -er --arg key "$HID_KEY" '.response.sessions[]|select(.role=="incoming" and .device_key==$key and .terminal==false)|.id' "/tmp/server-pre-$phase.json")
        wait_reply server "$HID_SESSION" "/tmp/health-$phase-server-pre.json"
        native_host_phase "pre-$phase" held
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
    native_host_phase final-held held
    agent_snapshot final-held
    echo LINK_FINAL_HELD
    host_expect CLIENT_CLOSED
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-1
    host_baseline /tmp/host-restored.json; cmp /tmp/host-before.json /tmp/host-restored.json
    agent_snapshot final
    jq -e '.schema_version==2 and .records==[] and .exports==[]' /tmp/root-final.json > /dev/null
    echo LINK_SERVER_CLOSED
else
@@CLIENT_SETUP@@
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
