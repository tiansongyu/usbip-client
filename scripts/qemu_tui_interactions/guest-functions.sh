# Disposable PID1 composition only; no production testing RPC.
TUI_PID='' AGENT_PID=''
tui_wait() {
    for tui_poll in $(seq 1 500); do
        [ ! -f "$TUI_PREFIX.$1" ] || return 0
        kill -0 "$TUI_PID"; sleep 0.05
    done
    return 1
}
tui_signal() { cat /proc/uptime > "$TUI_PREFIX.$1"; }
tui_launch() {
    TUI_PREFIX="/tmp/tui-$1"
    (exec 5<&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/tui-interaction-pty "$1" "$TUI_PREFIX") & TUI_PID=$!
}
tui_join() { wait "$TUI_PID"; TUI_PID=''; }
tui_actor() {
    tui_child=$(cat "$TUI_PREFIX.pid")
    [ "$(readlink "/proc/$tui_child/exe")" = /usr/bin/rustusb ]
    [ "$(awk '/^Uid:/{print $2 ":" $3 ":" $4 ":" $5}' "/proc/$tui_child/status")" = 1000:1000:1000:1000 ]
    {
        echo "PID=$tui_child"
        cat "/proc/$tui_child/status" "/proc/$tui_child/stat"
        echo "EXE=$(readlink "/proc/$tui_child/exe")"
        sha256sum "/proc/$tui_child/exe"
        tr '\000' ' ' < "/proc/$tui_child/cmdline"; echo
        for tui_fd in /proc/"$tui_child"/fd/*; do
            echo "FD=$(basename "$tui_fd") TARGET=$(readlink "$tui_fd")"
            cat "/proc/$tui_child/fdinfo/$(basename "$tui_fd")"
        done
        echo NETLINK_TABLE; cat /proc/net/netlink
    } > "/tmp/tui-actor-$1.txt"
}
frontend_setup() {
    mkdir -p /dev/pts /run/agent /var/lib/rustusb/agent /home/operator/.config/rustusb
    mount -t devpts devpts /dev/pts
    chown 2000:2000 /run/agent /var/lib/rustusb/agent
    chmod 711 /run/agent; chmod 700 /var/lib/rustusb/agent
    chown 1000:1000 /home/operator /home/operator/.config /home/operator/.config/rustusb
    chmod 700 /home/operator /home/operator/.config /home/operator/.config/rustusb
    chown 2000:2000 /etc/rustusb/server-key.der
    chmod 600 /etc/rustusb/server-key.der
    tui_key="native:usb-$(printf '%064d' 1)"
    tui_pin=$(sha256sum /etc/rustusb/client.der | awk '{print $1}')
    jq -n --arg key "$tui_key" --arg pin "$tui_pin" '{schema_version:1,broker_uid:2000,listen:"127.0.0.1:3241",devices:[$key],principals:[{id:"qemu-client",pins:[$pin],devices:[$key]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server.json
    jq -n '{schema_version:1,broker_uid:2000,helper_socket:"/run/rustusb/control.sock",control_directory:"/run/agent",data_directory:"/var/lib/rustusb/agent",profiles:[{id:"observe",backend:"native",config:"/etc/rustusb/server.json"}],operators:[{uid:1000,profiles:["observe"],actions:["read","audit"]}]}' > /etc/rustusb/agent.json
    chmod 644 /etc/rustusb/server.json /etc/rustusb/agent.json
    cp /etc/rustusb/server.json /tmp/tui-actual-server.json
    cp /etc/rustusb/agent.json /tmp/tui-actual-manifest.json
    stat -c '%u:%a:%h' /etc/rustusb/agent.json /etc/rustusb/server.json /etc/rustusb/server-key.der > /tmp/tui-config-stat.txt
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/sh -c '[ ! -r /etc/rustusb/server-key.der ]'
    echo 0 > /tmp/tui-operator-key-unreadable.txt
    (exec 5<&-; exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run > /tmp/agent.stdout 2> /tmp/agent.stderr) & AGENT_PID=$!
    for tui_poll in $(seq 1 200); do
        if /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json profiles > /tmp/tui-agent-profiles.json 2> /tmp/tui-agent-profiles.stderr; then break; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    jq -e '.journal_healthy and .response=={result:"profiles",profiles:[{id:"observe",backend:"native"}]}' /tmp/tui-agent-profiles.json > /dev/null
    cat "/proc/$AGENT_PID/status" "/proc/$AGENT_PID/stat" > /tmp/agent-identity.txt
    sha256sum "/proc/$AGENT_PID/exe" > /tmp/agent-exe-sha256.txt
    [ ! -e /var/lib/rustusb/agent/state.json ]
}
frontend_remaining() {
    STAGE=frontend-preferences
    printf '%s\n' '{"schema_version":1,"options":{"refresh_seconds":3,"auto_refresh":false,"monochrome":true}}' > /home/operator/.config/rustusb/preferences.json
    chown 1000:1000 /home/operator/.config/rustusb/preferences.json; chmod 600 /home/operator/.config/rustusb/preferences.json
    cp /home/operator/.config/rustusb/preferences.json /tmp/tui-preferences-initial.json
    tui_launch preferences
    tui_wait defaults-shown
    cmp /tmp/tui-preferences-initial.json /home/operator/.config/rustusb/preferences.json
    printf '%s\n' '{"schema_version":1,"options":{"refresh_seconds":5,"auto_refresh":false,"monochrome":true}}' > /home/operator/.config/rustusb/preferences.json
    cp /home/operator/.config/rustusb/preferences.json /tmp/tui-preferences-changed.json
    stat -c '%u:%a:%h' /home/operator/.config/rustusb/preferences.json > /tmp/tui-preferences-changed-stat.txt
    tui_signal changed
    tui_join
    cmp /tmp/tui-preferences-changed.json /home/operator/.config/rustusb/preferences.json
    STAGE=frontend-reload-permission
    tui_launch reload-error
    tui_wait ready
    chmod 644 /home/operator/.config/rustusb/preferences.json
    stat -c '%u:%a:%h' /home/operator/.config/rustusb/preferences.json > /tmp/tui-preferences-error-stat.txt
    tui_signal changed
    tui_join
    cmp /tmp/tui-preferences-changed.json /home/operator/.config/rustusb/preferences.json
    chmod 600 /home/operator/.config/rustusb/preferences.json
    STAGE=frontend-agent-denied
    [ ! -e /var/lib/rustusb/agent/state.json ]
    tui_launch deny
    tui_join
    cp /var/lib/rustusb/agent/state.json /tmp/tui-agent-after-denials.json
    jq -e '.jobs==[] and .sessions==[] and .requests==[] and (.audit|length)==2 and all(.audit[]; .actor_uid==1000 and .action=="denied" and .failure=="permission")' /tmp/tui-agent-after-denials.json > /dev/null
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    cat /proc/net/tcp /proc/net/tcp6 > /tmp/tui-tcp-after.txt
    kill -TERM "$AGENT_PID"
    set +e; wait "$AGENT_PID"; tui_wait_status=$?; set -e
    echo "$tui_wait_status" > /tmp/agent-wait.txt
    [ "$tui_wait_status" = 0 ] && [ ! -e "/proc/$AGENT_PID" ] && [ ! -e /run/agent/control.sock ]
    AGENT_PID=''
    cp /var/lib/rustusb/agent/state.json /tmp/tui-agent-final.json
    cmp /tmp/tui-agent-after-denials.json /tmp/tui-agent-final.json
    [ ! -e /run/rustusb/control.sock ]
    echo 'TUI_NO_USB_OWNERS modules_absent=true helper_socket_absent=true admitted_requests=0'
}
frontend_collect() {
    mkdir -p /tmp/tui-frozen
    for tui_file in /tmp/tui-* /tmp/agent-* /tmp/agent.stdout /tmp/agent.stderr; do
        [ -f "$tui_file" ] || continue
        tui_name=$(basename "$tui_file")
        cp "$tui_file" "/tmp/tui-frozen/$tui_name" || return 1
        tui_frozen="/tmp/tui-frozen/$tui_name"
        tui_bytes=$(wc -c < "$tui_frozen")
        [ "$tui_bytes" -le 1048576 ] || return 1
        echo "RUSTUSB_TUI_FILE_BEGIN name=$tui_name bytes=$tui_bytes sha256=$(sha256sum "$tui_frozen" | awk '{print $1}')"
        base64 "$tui_frozen" || return 1
        echo "RUSTUSB_TUI_FILE_END name=$tui_name"
    done
}
frontend_cleanup() {
    frontend_status=$?
    trap - EXIT
    set +e
    for frontend_child in "$TUI_PID" "$AGENT_PID"; do
        [ -n "$frontend_child" ] || continue
        kill -TERM "$frontend_child" 2>/dev/null
        for frontend_poll in $(seq 1 100); do
            [ ! -e "/proc/$frontend_child/status" ] && break
            grep -q '^State:.*Z' "/proc/$frontend_child/status" && break
            sleep 0.05
        done
        if [ -e "/proc/$frontend_child/status" ] && ! grep -q '^State:.*Z' "/proc/$frontend_child/status"; then
            echo "RUSTUSB_TUI_FORCED_CHILD pid=$frontend_child"
            kill -KILL "$frontend_child"; frontend_status=1
        fi
        wait "$frontend_child"
    done
    frontend_collect || frontend_status=1
    # Pass the original result into the unchanged monitor collector/cleanup.
    frontend_result() { return "$frontend_status"; }
    frontend_result
    monitor_cleanup
}
