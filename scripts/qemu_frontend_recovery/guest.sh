# Disposable guest-only native process crashes; never a product test endpoint.
recovery_capture() {
    local label=$1 file count=0
    { stat -c 'DIRECTORY uid=%u mode=%a' /home/operator/.local/state/rustusb
      for file in /home/operator/.local/state/rustusb/intent-*.json; do
          [ -f "$file" ] || continue
          count=$((count+1));stat -c 'FILE uid=%u mode=%a links=%h name=%n' "$file"
          cp "$file" "/tmp/recovery-$label-document.json"
      done
      [ "$count" = 1 ]
    } >"/tmp/recovery-$label-file.txt"
}
recovery_kill() {
    local label=$1 old_pid=$GUI_PID old_label=$GUI_LABEL
    { echo "PID=$old_pid LABEL=$old_label";cat "/proc/$old_pid/status" "/proc/$old_pid/stat";sha256sum "/proc/$old_pid/exe"; } >"/tmp/recovery-$label-actor.txt"
    kill -KILL "$old_pid"
    for recovery_poll in $(seq 1 200); do
        [ -e "/proc/$old_pid/status" ] || { [ ! -f "/tmp/gui-$old_label.wait" ] || break; }
        sleep .05
    done
    [ ! -e "/proc/$old_pid/status" ] && [ "$(cat "/tmp/gui-$old_label.wait")" = 137 ]
    echo "GUI_PLANNED_PROCESS_KILL role=$ROLE label=$old_label pid=$old_pid actual_wait=137" | tee "/tmp/recovery-$label-kill.txt"
    GUI_PID=''
    kill -TERM "$GUI_DRIVER" 2>/dev/null || true
    set +e;wait "$GUI_DRIVER";recovery_driver_exit=$?;set -e
    echo "$recovery_driver_exit" >"/tmp/gui-$old_label-driver.wait"
    GUI_DRIVER='' GUI_SESSION=''
}
recovery_denied_window() (
    local label=$1 bad=$2
    GUI_PID='' GUI_DRIVER='' GUI_SESSION='' GUI_PORT=15889 GUI_EXPECT_READONLY=1
    if [ "$bad" = yes ]; then
        mkdir -p /home/operator/recovery-bad/rustusb
        chown -R 1000:1000 /home/operator/recovery-bad
        chmod 755 /home/operator/recovery-bad/rustusb
        export XDG_STATE_HOME=/home/operator/recovery-bad
    fi
    cp /var/lib/rustusb/agent/state.json "/tmp/recovery-$label-agent-before.json"
    gui_open "$label" hid
    gui_wait '.pending==null and .recovery_writable==false and .unresolved==null' "/tmp/recovery-$label-view.json"
    gui_dom 'document.getElementById("start").disabled'
    jq -n '{script:"const done=arguments[arguments.length-1];window.__TAURI__.core.invoke(\"gui_action\",{action:{action:\"start\"}}).then(value=>done({unexpected:value}),e=>done({error:String(e)}));",args:[]}' >/run/gui-request.json
    gui_http POST "/session/$GUI_SESSION/execute/async" "$(cat /run/gui-request.json)" "/tmp/recovery-$label-denied.json"
    jq -e '.value.error and (.value|has("unexpected")|not)' "/tmp/recovery-$label-denied.json" >/dev/null
    gui_snapshot "/tmp/recovery-$label-after.json"
    cp /var/lib/rustusb/agent/state.json "/tmp/recovery-$label-agent-after.json"
    gui_close
)
recovery_start_job() {
    local profile=$1 request
    if [ "$AGENT_EPOCH" != 1 ] || [ "$profile" != hid ]; then start_job "$profile";return;fi
    gui_open recovery-start-before hid
    gui_wait '.recovery_writable and .unresolved==null and .pending==null' /tmp/recovery-start-ready.json
    recovery_denied_window recovery-competing no
    recovery_denied_window recovery-bad yes
    cp /var/lib/rustusb/agent/state.json /tmp/recovery-start-agent-before.json
    kill -STOP "$AGENT_PID"
    for recovery_poll in $(seq 1 100);do grep -q '^State:.*T' "/proc/$AGENT_PID/status" && break;sleep .01;done
    cat "/proc/$AGENT_PID/status" "/proc/$AGENT_PID/stat" >/tmp/recovery-start-agent-stopped.txt
    gui_js 'document.getElementById("start").click();return true;' /run/recovery-start-click.json
    gui_confirm
    cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/recovery-start-confirm.json
    gui_wait '.pending!=null and .unresolved!=null' /tmp/recovery-start-pending.json
    request=$(jq -er '.value.unresolved.request_id' /tmp/recovery-start-pending.json)
    recovery_capture start-pending
    recovery_kill start
    kill -CONT "$AGENT_PID"
    for recovery_poll in $(seq 1 300);do
        if jq -e --arg id "$request" 'any(.requests[];.id==$id and .uid==1000 and .profile=="hid")' /var/lib/rustusb/agent/state.json >/dev/null;then break;fi
        kill -0 "$AGENT_PID";sleep .05
    done
    cp /var/lib/rustusb/agent/state.json /tmp/recovery-start-late.json
    gui_open recovery-start-after hid
    gui_wait '.recovery_writable and .unresolved!=null and .pending==null' /tmp/recovery-start-restored.json
    recovery_capture start-restored
    cp /var/lib/rustusb/agent/state.json /tmp/recovery-start-agent-before-query.json
    gui_js 'document.getElementById("start").click();return true;' /run/recovery-start-query.json
    gui_confirm;cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/recovery-start-query-confirm.json
    gui_wait '.unresolved==null and .pending==null and (.status|startswith("Resolved original job "))' /tmp/recovery-start-resolved.json
    recovery_capture start-resolved
    cp /var/lib/rustusb/agent/state.json /tmp/recovery-start-agent-after-query.json
    agent start hid --retry --request-id "$request" >"/tmp/start-hid-$AGENT_EPOCH.json" 2>"/tmp/start-hid-$AGENT_EPOCH.log"
    jq -e '.response.result=="started" and .response.replayed and .response.job.operator_uid==1000' "/tmp/start-hid-$AGENT_EPOCH.json" >/dev/null
    gui_close
    kill -0 "$AGENT_PID"
    echo "AGENT_START_RETURNED role=$ROLE profile=hid epoch=$AGENT_EPOCH wait=0"
}
