# Included only in disposable guest PID1 after the base/authentication functions.
RATE_PIDS='' RATE_CYCLE=''
rate_sample() {
    local rate_label rate_op rate_poll
    rate_label=$1 rate_op=$2
    printf '{"op":"%s","id":"%s"}\n' "$rate_op" "$rate_label" | timeout 3 /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /bin/busybox tee /run/broker/control > /dev/null
    for rate_poll in $(seq 1 100); do
        if jq -se --arg id "$rate_label" --arg op "$rate_op" 'any(.[]; .event==$op and .id==$id)' /tmp/server.events > /dev/null 2>&1; then
            jq -s --arg id "$rate_label" --arg op "$rate_op" '.[]|select(.event==$op and .id==$id)|.snapshot' /tmp/server.events > "/tmp/$rate_label.json"
            return 0
        fi
        kill -0 "$SERVER_PID"; sleep 0.05
    done
    return 1
}
rate_join() {
    local rate_pid rate_worker rate_rc rate_failed rate_file
    rate_worker=0 rate_failed=0
    for rate_pid in ${RATE_PIDS:-}; do
        rate_worker=$((rate_worker+1))
        if wait "$rate_pid"; then rate_rc=0; else rate_rc=$?; fi
        echo "$rate_rc" > "/tmp/rate-worker-$RATE_CYCLE-$rate_worker.wait"
        [ "$rate_rc" = 0 ] || rate_failed=1
    done
    RATE_PIDS=''
    case "${RATE_CYCLE:-}" in 1|2)
        for rate_file in /run/broker/rate-"$RATE_CYCLE"/*; do
            [ -f "$rate_file" ] || continue
            cp "$rate_file" "/tmp/rate-worker-$RATE_CYCLE-$(basename "$rate_file")" || rate_failed=1
        done ;;
    esac
    [ "$rate_failed" = 0 ]
}
rate_client_begin() {
    local rate_worker rate_pid rate_poll rate_expected
    RATE_CYCLE=$1
    echo "RATE_CLIENT_WAIT cycle=$RATE_CYCLE"
    host_expect "RATE_GO $RATE_CYCLE"
    auth_client_capture "/tmp/rate-client-before-$RATE_CYCLE"
    auth_process "$HID_PID" "rate-hid-before-$RATE_CYCLE"
    auth_process "$SERIAL_PID" "rate-serial-before-$RATE_CYCLE"
    mkdir -m 700 "/run/broker/rate-$RATE_CYCLE"
    chown 2000:2000 "/run/broker/rate-$RATE_CYCLE"
    RATE_PIDS=''
    rate_expected=$(sha256sum /bin/busybox | awk '{print $1}')
    for rate_worker in $(seq 1 8); do
        (exec 3<&- 4<&- 5<&-; exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /bin/sh /etc/rustusb/rate-worker.sh "$RATE_CYCLE" "$rate_worker" "$FAMILY") < /dev/null > "/tmp/rate-worker-$RATE_CYCLE-$rate_worker.launch" 2>&1 &
        rate_pid=$!; RATE_PIDS="$RATE_PIDS $rate_pid"
        for rate_poll in $(seq 1 100); do
            kill -0 "$rate_pid"
            if awk '/^Uid:/{exit !($2==2000 && $3==2000 && $4==2000 && $5==2000)}' "/proc/$rate_pid/status" && [ "$(sha256sum "/proc/$rate_pid/exe" | awk '{print $1}')" = "$rate_expected" ]; then break; fi
            sleep 0.01
        done
        awk '/^Uid:/{exit !($2==2000 && $3==2000 && $4==2000 && $5==2000)}' "/proc/$rate_pid/status"
        [ "$(sha256sum "/proc/$rate_pid/exe" | awk '{print $1}')" = "$rate_expected" ]
        auth_process "$rate_pid" "rate-worker-$RATE_CYCLE-$rate_worker"
    done
    echo "RATE_CLIENT_RUNNING cycle=$RATE_CYCLE"
}
rate_server_gate() {
    local rate_cycle rate_poll rate_before
    rate_cycle=$1
    echo "RATE_SERVER_WAIT cycle=$rate_cycle"
    host_expect "RATE_BEGIN $rate_cycle"
    rate_sample "rate-admission-before-$rate_cycle" server_admission
    rate_sample "rate-tls-before-$rate_cycle" server_security
    auth_state "rate-before-$rate_cycle" held
    rate_before=$(jq -r '.limited' "/tmp/rate-admission-before-$rate_cycle.json")
    echo "RATE_SERVER_SAVED cycle=$rate_cycle"
    host_expect "RATE_RUNNING $rate_cycle"
    for rate_poll in $(seq 1 100); do
        rate_sample "rate-admission-poll-$rate_cycle-$rate_poll" server_admission
        if jq -e --argjson before "$rate_before" '.limited>$before' "/tmp/rate-admission-poll-$rate_cycle-$rate_poll.json" > /dev/null; then
            cp "/tmp/rate-admission-poll-$rate_cycle-$rate_poll.json" "/tmp/rate-admission-limited-$rate_cycle.json"
            break
        fi
        sleep 0.05
    done
    [ -f "/tmp/rate-admission-limited-$rate_cycle.json" ]
    cat /proc/uptime > "/tmp/rate-server-limited-$rate_cycle.uptime"
    echo "RATE_SERVER_LIMITED cycle=$rate_cycle"
}
rate_client_finish() {
    local rate_cycle rate_pid rate_alive rate_rc
    rate_cycle=$1 rate_alive=0
    for rate_pid in $RATE_PIDS; do
        if [ -f "/proc/$rate_pid/status" ] && awk '/^State:/{exit ($2=="Z" || $2=="X")}' "/proc/$rate_pid/status"; then rate_alive=$((rate_alive+1)); fi
    done
    [ "$rate_alive" -gt 0 ]; echo "$rate_alive" > "/tmp/rate-workers-alive-after-io-$rate_cycle.txt"
    cat /proc/uptime > "/tmp/rate-client-io-$rate_cycle.uptime"
    auth_process "$HID_PID" "rate-hid-after-io-$rate_cycle"
    auth_process "$SERIAL_PID" "rate-serial-after-io-$rate_cycle"
    echo "RATE_CLIENT_IO_ACTIVE cycle=$rate_cycle workers=$rate_alive"
    rate_join
    echo "RATE_CLIENT_FLOOD_DONE cycle=$rate_cycle"
    host_expect "RATE_RECOVER $rate_cycle"
    sleep 1.1
    if broker --json secure-discover --config /etc/rustusb/client-hid.json > "/tmp/rate-recovery-$rate_cycle.json" 2> "/tmp/rate-recovery-$rate_cycle.stderr"; then rate_rc=0; else rate_rc=$?; fi
    echo "$rate_rc" > "/tmp/rate-recovery-$rate_cycle.wait"
    [ "$rate_rc" = 0 ]
    jq -e 'type=="array" and length==0' "/tmp/rate-recovery-$rate_cycle.json" > /dev/null
    auth_client_capture "/tmp/rate-client-after-$rate_cycle"
    cmp "/tmp/rate-client-before-$rate_cycle.txt" "/tmp/rate-client-after-$rate_cycle.txt"
    cmp "/tmp/rate-client-before-$rate_cycle.json" "/tmp/rate-client-after-$rate_cycle.json"
    echo "RATE_CLIENT_RECOVERED cycle=$rate_cycle"
    host_expect "RATE_RECOVERY_CHECKED $rate_cycle"
}
rate_server_after_io() {
    local rate_cycle rate_poll
    rate_cycle=$1
    rate_sample "rate-admission-after-io-$rate_cycle" server_admission
    cat /proc/uptime > "/tmp/rate-server-after-io-$rate_cycle.uptime"
    host_expect "RATE_FLOOD_DONE $rate_cycle"
    for rate_poll in $(seq 1 100); do
        rate_sample "rate-tls-drain-$rate_cycle-$rate_poll" server_security
        if jq -e '.tls.in_flight==0' "/tmp/rate-tls-drain-$rate_cycle-$rate_poll.json" > /dev/null; then
            cp "/tmp/rate-tls-drain-$rate_cycle-$rate_poll.json" "/tmp/rate-tls-drained-$rate_cycle.json"; break
        fi
        sleep 0.05
    done
    [ -f "/tmp/rate-tls-drained-$rate_cycle.json" ]
    rate_sample "rate-admission-drained-$rate_cycle" server_admission
    auth_state "rate-drained-$rate_cycle" held
    echo "RATE_SERVER_DRAINED cycle=$rate_cycle"
    host_expect "RATE_RECOVERED $rate_cycle"
    rate_sample "rate-admission-recovered-$rate_cycle" server_admission
    rate_sample "rate-tls-recovered-$rate_cycle" server_security
    auth_state "rate-recovered-$rate_cycle" held
    if [ "$BACKEND" = native ]; then
        cmp "/tmp/server-native-auth-rate-before-$rate_cycle.json" "/tmp/server-native-auth-rate-recovered-$rate_cycle.json"
    elif [ "$BACKEND" = usbfs ]; then
        cmp "/tmp/auth-physical-rate-before-$rate_cycle.json" "/tmp/auth-physical-rate-recovered-$rate_cycle.json"
    fi
    echo "RATE_SERVER_CHECKED cycle=$rate_cycle"
}
