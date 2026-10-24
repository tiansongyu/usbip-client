# Disposable-guest observer probes. Added after the original boot/identity helpers.
catalog_identity() {
    C_PID=$1 C_LABEL=$2 C_UID=$3
    kill -0 "$C_PID"
    [ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$C_PID/status")" = "$C_UID:$C_UID:$C_UID:$C_UID" ]
    for c_cap in CapInh CapPrm CapEff CapAmb; do
        [ "$(awk -v key="$c_cap:" '$1==key {print $2}' "/proc/$C_PID/status")" = 0000000000000000 ]
    done
    cat "/proc/$C_PID/status" > "/tmp/catalog-$C_LABEL.status.txt"
    cat "/proc/$C_PID/stat" > "/tmp/catalog-$C_LABEL.stat.txt"
    sha256sum "/proc/$C_PID/exe" > "/tmp/catalog-$C_LABEL.sha256.txt"
    ls -l "/proc/$C_PID/fd" > "/tmp/catalog-$C_LABEL.fds.txt"
    cat "/proc/$C_PID/fdinfo/1" > "/tmp/catalog-$C_LABEL.stdout-fdinfo.txt"
}
catalog_point() {
    C_FILE=$1 C_MARK=$2 C_STATE=$3 C_COUNT=$4 C_AFTER=$5
    for c_try in $(seq 1 240); do
        if jq -s --arg state "$C_STATE" --argjson count "$C_COUNT" --argjson after "$C_AFTER" -f /etc/rustusb/catalog-point.jq "$C_FILE" > "/tmp/catalog-$C_MARK.candidate.json" 2> "/tmp/catalog-$C_MARK.parse.txt" &&
            jq -e 'type=="object" and .head.observation_only==true' "/tmp/catalog-$C_MARK.candidate.json" > /dev/null 2>&1; then
            mv "/tmp/catalog-$C_MARK.candidate.json" "/tmp/catalog-$C_MARK.json"
            echo "CATALOG_POINT mark=$C_MARK state=$C_STATE count=$C_COUNT"
            return 0
        fi
        sleep 0.05
    done
    return 1
}
catalog_wait_code() {
    C_PID=$1 C_LABEL=$2 C_EXPECTED=$3
    for c_wait in $(seq 1 200); do [ -e "/proc/$C_PID" ] || break; sleep 0.05; done
    [ ! -e "/proc/$C_PID" ]
    if wait "$C_PID"; then C_RC=0; else C_RC=$?; fi
    printf '%s\n' "$C_RC" > "/tmp/catalog-$C_LABEL.wait.txt"
    [ "$C_RC" = "$C_EXPECTED" ]
}
catalog_cli_cases() {
    cp /etc/rustusb/catalog-point.jq /tmp/catalog-point.jq
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/sh -c '[ ! -r /etc/rustusb/client-key.der ]'
    echo 0 > /tmp/catalog-operator-private-key-unreadable.txt
    # Direct dedicated broker: the real protected private-key loader is exercised.
    (exec 5<&-; exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb --json secure-discover --config /etc/rustusb/client-hid.json --watch --refresh-seconds 120 > /tmp/catalog-direct.stdout.txt 2> /tmp/catalog-direct.stderr.txt) &
    C_DIRECT=$!
    catalog_point /tmp/catalog-direct.stdout.txt direct current 2 -1
    catalog_identity "$C_DIRECT" direct 2000
    kill -TERM "$C_DIRECT"; catalog_wait_code "$C_DIRECT" direct 0

    # Fill a fresh actual FIFO before starting the CLI. Nobody drains it before wait.
    mkfifo /tmp/catalog-full.fifo
    exec 7<>/tmp/catalog-full.fifo
    (exec 5<&-; exec dd if=/dev/zero bs=4096 count=1024 > /tmp/catalog-full.fifo 2> /tmp/catalog-prefill.stderr.txt) &
    C_FILL=$!
    for c_fill in $(seq 1 200); do
        [ "$(cat "/proc/$C_FILL/wchan")" = pipe_write ] && break
        sleep 0.05
    done
    [ "$(cat "/proc/$C_FILL/wchan")" = pipe_write ]
    cat "/proc/$C_FILL/syscall" > /tmp/catalog-prefill.syscall.txt
    kill -TERM "$C_FILL"; catalog_wait_code "$C_FILL" prefill 143
    cp /var/lib/rustusb/agent/state.json /tmp/catalog-before-full-agent.json
    (exec 5<&-; exec 1>/tmp/catalog-full.fifo; exec 7>&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json agent --config /etc/rustusb/agent.json discover hid --watch --refresh-seconds 120 2> /tmp/catalog-full.stderr.txt) &
    C_FULL=$!
    for c_block in $(seq 1 240); do
        C_THREAD=''
        for c_thread in /proc/"$C_FULL"/task/*; do
            if [ "$(cat "$c_thread/wchan" 2>/dev/null)" = pipe_write ] && grep -q '^1 0x1 ' "$c_thread/syscall" 2>/dev/null; then C_THREAD=$c_thread; break; fi
        done
        if [ -n "$C_THREAD" ] && jq -e --slurpfile old /tmp/catalog-before-full-agent.json '.sequence>$old[0].sequence and ([.audit[]|select(.sequence>$old[0].sequence and .action=="discover" and .actor_uid==1000)]|length)==1' /var/lib/rustusb/agent/state.json > /dev/null 2>&1; then break; fi
        sleep 0.05
    done
    [ -n "$C_THREAD" ]
    cp /var/lib/rustusb/agent/state.json /tmp/catalog-during-full-agent.json
    jq -e --slurpfile old /tmp/catalog-before-full-agent.json '([.audit[]|select(.sequence>$old[0].sequence and .action=="discover" and .actor_uid==1000)]|length)==1' /tmp/catalog-during-full-agent.json > /dev/null
    cat "$C_THREAD/syscall" > /tmp/catalog-full.syscall.txt
    cat "$C_THREAD/wchan" > /tmp/catalog-full.wchan.txt
    printf 'pid=%s\nthread=%s\nstdout_drained_before_wait=false\n' "$C_FULL" "$C_THREAD" > /tmp/catalog-full.observation.txt
    catalog_identity "$C_FULL" full 1000
    kill -INT "$C_FULL"; catalog_wait_code "$C_FULL" full 0
    exec 7>&-
    rm /tmp/catalog-full.fifo

    # Read one actual successful batch, then close the only reader before the next.
    mkfifo /tmp/catalog-closed.fifo
    exec 7<>/tmp/catalog-closed.fifo
    (exec 5<&-; exec 1>/tmp/catalog-closed.fifo; exec 7>&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json agent --config /etc/rustusb/agent.json discover hid --watch 2> /tmp/catalog-closed.stderr.txt) &
    C_CLOSED=$!
    : > /tmp/catalog-closed.stdout.txt
    for c_read in $(seq 1 100); do
        IFS= read -r -t 5 C_LINE <&7
        printf '%s\n' "$C_LINE" >> /tmp/catalog-closed.stdout.txt
        if printf '%s\n' "$C_LINE" | jq -e '.event=="catalog_end" and .device_count==2' > /dev/null; then break; fi
    done
    catalog_point /tmp/catalog-closed.stdout.txt closed current 2 -1
    catalog_identity "$C_CLOSED" closed 1000
    exec 7>&-
    catalog_wait_code "$C_CLOSED" closed 1
    grep -F 'Broken pipe' /tmp/catalog-closed.stderr.txt > /dev/null
    rm /tmp/catalog-closed.fifo

    # Fast denial: retain actual exit/body and before/after kernel-UID audit facts.
    cp /var/lib/rustusb/agent/state.json /tmp/catalog-before-denied-agent.json
    if agent_other discover hid --watch --json > /tmp/catalog-denied.stdout.txt 2> /tmp/catalog-denied.stderr.txt; then C_DENIED=0; else C_DENIED=$?; fi
    printf '%s\n' "$C_DENIED" > /tmp/catalog-denied.wait.txt
    [ "$C_DENIED" = 1 ]
    catalog_point /tmp/catalog-denied.stdout.txt denied failed -1 -1
    cp /var/lib/rustusb/agent/state.json /tmp/catalog-after-denied-agent.json
    echo CATALOG_CLI_CASES_COMPLETE
}
catalog_watch_start() {
    (exec 5<&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb --json agent --config /etc/rustusb/agent.json discover hid --watch > /tmp/catalog-watch.stdout.txt 2> /tmp/catalog-watch.stderr.txt) &
    CATALOG_WATCH_PID=$!
    catalog_point /tmp/catalog-watch.stdout.txt ready current 2 -1
    catalog_identity "$CATALOG_WATCH_PID" watch-start 1000
}
catalog_pause_before_kill() {
    # Force a fresh completed response, then stop all watcher threads during idle.
    catalog_point /tmp/catalog-watch.stdout.txt latest-before-pause current 2 -1
    jq '.head.revision' /tmp/catalog-latest-before-pause.json > /tmp/catalog-before-pause-revision.json
    catalog_point /tmp/catalog-watch.stdout.txt before-pause current 2 "$(cat /tmp/catalog-before-pause-revision.json)"
    kill -STOP "$CATALOG_WATCH_PID"
    for c_stop in $(seq 1 100); do
        [ "$(awk '/^State:/ {print $2}' "/proc/$CATALOG_WATCH_PID/status")" = T ] && break
        sleep 0.01
    done
    [ "$(awk '/^State:/ {print $2}' "/proc/$CATALOG_WATCH_PID/status")" = T ]
    catalog_identity "$CATALOG_WATCH_PID" watch-paused 1000
}
catalog_local_outage() {
    kill -CONT "$CATALOG_WATCH_PID"
    catalog_point /tmp/catalog-watch.stdout.txt stale stale 2 "$(jq '.head.revision' /tmp/catalog-before-pause.json)"
}
catalog_recovered() {
    catalog_point /tmp/catalog-watch.stdout.txt recovered current 2 "$(jq '.head.revision' /tmp/catalog-stale.json)"
    catalog_identity "$CATALOG_WATCH_PID" watch-recovered 1000
}
catalog_stop_export() {
    C_KEY=$1 C_MARK=$2
    agent sessions server > "/tmp/catalog-export-$C_MARK-before.json"
    C_EXPORT=$(jq -er --arg key "$C_KEY" '.response.sessions[]|select(.role=="export" and .device_key==$key and .terminal==false)|.id' "/tmp/catalog-export-$C_MARK-before.json")
    agent stop-session "$C_EXPORT" > "/tmp/catalog-export-$C_MARK-stop.json"
    jq -e '.response=={result:"stop_accepted",owner_present:true}' "/tmp/catalog-export-$C_MARK-stop.json" > /dev/null
    for c_export in $(seq 1 200); do
        agent sessions server > "/tmp/catalog-export-$C_MARK-after.json"
        if jq -e --arg id "$C_EXPORT" '.response.sessions|map(select(.id==$id))|length==1 and all(.[]; .terminal and .cleanup=="verified" and .state=="Disconnected")' "/tmp/catalog-export-$C_MARK-after.json" > /dev/null; then break; fi
        sleep 0.05
    done
    jq -e --arg id "$C_EXPORT" '.response.sessions|map(select(.id==$id))|length==1 and all(.[]; .terminal and .cleanup=="verified" and .state=="Disconnected")' "/tmp/catalog-export-$C_MARK-after.json" > /dev/null
    agent_snapshot "catalog-$C_MARK"
}
catalog_remote_empty_wait() {
    catalog_point /tmp/catalog-watch.stdout.txt one current 1 "$(jq '.head.revision' /tmp/catalog-recovered.json)"
    jq -e --arg key "$SERIAL_KEY" '.devices|length==1 and .[0].device_key==$key' /tmp/catalog-one.json > /dev/null
    echo CATALOG_CLIENT_ONE
    catalog_point /tmp/catalog-watch.stdout.txt empty current 0 "$(jq '.head.revision' /tmp/catalog-one.json)"
    catalog_identity "$CATALOG_WATCH_PID" watch-empty 1000
    kill -TERM "$CATALOG_WATCH_PID"; catalog_wait_code "$CATALOG_WATCH_PID" watch 0
    CATALOG_WATCH_PID=''
    echo CATALOG_CLIENT_EMPTY_STOPPED
}
