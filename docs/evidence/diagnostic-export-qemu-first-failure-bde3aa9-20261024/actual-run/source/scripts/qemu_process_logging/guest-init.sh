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
@@SERVER_SETUP@@
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
@@CLIENT_SETUP@@
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
