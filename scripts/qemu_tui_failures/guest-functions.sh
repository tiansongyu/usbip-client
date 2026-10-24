# Composed only into disposable PID1. Never an installed product input.
tui_wait_phase() {
    for tui_poll in $(seq 1 500); do
        [ ! -f "$TUI_PREFIX.$1" ] || return 0
        kill -0 "$TUI_PID"
        sleep 0.05
    done
    return 1
}
tui_phase() { cat /proc/uptime > "$TUI_PREFIX.$1"; }
tui_agent_observation() {
    cat "/proc/$AGENT_PID/status" > "$TUI_PREFIX-agent-$1.status"
    cat "/proc/$AGENT_PID/stat" > "$TUI_PREFIX-agent-$1.stat"
    cat /proc/uptime > "$TUI_PREFIX-agent-$1.uptime"
}
tui_unknown_start() {
    TUI_PREFIX="/tmp/tui-$2"
    (exec 5<&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/tui-failure-pty "$1" "$TUI_PREFIX") & TUI_PID=$!
    tui_wait_phase ready
    tui_agent_observation before
    kill -STOP "$AGENT_PID"
    for tui_poll in $(seq 1 100); do
        if grep -q '^State:.*T' "/proc/$AGENT_PID/status"; then break; fi
        sleep 0.01
    done
    grep -q '^State:.*T' "/proc/$AGENT_PID/status"
    tui_agent_observation paused
    cp /var/lib/rustusb/agent/state.json "$TUI_PREFIX-before-issued.json"
    tui_phase paused
    tui_wait_phase issued
    tui_wait_phase timeout
    tui_agent_observation timeout
    grep -q '^State:.*T' "$TUI_PREFIX-agent-timeout.status"
    cp /var/lib/rustusb/agent/state.json "$TUI_PREFIX-after-timeout.json"
    cmp "$TUI_PREFIX-before-issued.json" "$TUI_PREFIX-after-timeout.json"
    tui_phase continue
    kill -CONT "$AGENT_PID"
    for tui_poll in $(seq 1 400); do
        cp /var/lib/rustusb/agent/state.json "$TUI_PREFIX-after-admit.json"
        if jq -e --arg profile "$1" --slurpfile old "$TUI_PREFIX-before-issued.json" '[.requests[]|select(.id as $id|($old[0].requests|map(.id)|index($id))==null)]|length==1 and .[0].uid==1000 and .[0].profile==$profile' "$TUI_PREFIX-after-admit.json" > /dev/null; then break; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    jq -e --slurpfile old "$TUI_PREFIX-before-issued.json" '(.requests|length)==($old[0].requests|length)+1 and (.jobs|length)==($old[0].jobs|length)+1' "$TUI_PREFIX-after-admit.json" > /dev/null
    tui_agent_observation resumed
    tui_phase retry
    tui_wait_phase retry-issued
    tui_wait_phase read-ready
    cp /var/lib/rustusb/agent/state.json "$TUI_PREFIX-before-read.json"
    kill -STOP "$AGENT_PID"
    for tui_poll in $(seq 1 100); do
        if grep -q '^State:.*T' "/proc/$AGENT_PID/status"; then break; fi
        sleep 0.01
    done
    grep -q '^State:.*T' "/proc/$AGENT_PID/status"
    tui_agent_observation read-paused
    tui_phase read-paused
    tui_wait_phase read-issued
    tui_wait_phase read-timeout
    tui_agent_observation read-timeout
    grep -q '^State:.*T' "$TUI_PREFIX-agent-read-timeout.status"
    cp /var/lib/rustusb/agent/state.json "$TUI_PREFIX-after-read-timeout.json"
    cmp "$TUI_PREFIX-before-read.json" "$TUI_PREFIX-after-read-timeout.json"
    tui_phase read-continue
    kill -CONT "$AGENT_PID"
    tui_agent_observation read-resumed
    tui_phase read-resumed
    wait "$TUI_PID"
    echo "TUI_UNKNOWN_WAIT role=$ROLE label=$2 wait=0 original_agent=$AGENT_PID"
}
