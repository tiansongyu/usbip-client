# Extra code is composed only into the disposable PID1 init, never product RPC.
tui_run() {
    TUI_MODE=$1 TUI_LABEL=$2
    shift 2
    (exec 5<&-; exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/tui-action-pty "$TUI_MODE" "/tmp/tui-$TUI_LABEL" "$@")
    echo "TUI_ACTION_WAIT role=$ROLE label=$TUI_LABEL wait=0"
}
tui_preferences() {
    mkdir -p /home/operator/.config
    chown 1000:1000 /home/operator /home/operator/.config
    chmod 700 /home/operator /home/operator/.config
    tui_run save settings-save
    cp /home/operator/.config/rustusb/preferences.json /tmp/tui-saved-preferences.json
    stat -c '%u:%a:%h' /home/operator/.config/rustusb /home/operator/.config/rustusb/preferences.json /home/operator/.config/rustusb/preferences.lock > /tmp/tui-preferences-stat.txt
    jq -e '.schema_version==1 and .options=={refresh_seconds:3,auto_refresh:false,monochrome:true}' /tmp/tui-saved-preferences.json > /dev/null
    tui_run load settings-load
    tui_run override settings-override
    cmp /tmp/tui-saved-preferences.json /home/operator/.config/rustusb/preferences.json
    tui_run save-error settings-error
    stat -c '%u:%a:%h' /home/operator/.config/rustusb/preferences.json > /tmp/tui-preferences-error-stat.txt
    cmp /tmp/tui-saved-preferences.json /home/operator/.config/rustusb/preferences.json
    chmod 600 /home/operator/.config/rustusb/preferences.json
    cp /home/operator/.config/rustusb/preferences.json /tmp/tui-final-preferences.json
    [ -f "/etc/rustusb/$ROLE-key.der" ]
    /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/sh -c '[ ! -r /etc/rustusb/server-key.der ] && [ ! -r /etc/rustusb/client-key.der ]'
    echo 0 > /tmp/tui-operator-key-unreadable.txt
}
tui_start_job() {
    TUI_PROFILE=$1 TUI_LABEL="start-$1-$AGENT_EPOCH"
    cp /var/lib/rustusb/agent/state.json "/tmp/tui-$TUI_LABEL-before.json"
    tui_run "start-$TUI_PROFILE" "$TUI_LABEL"
    cp /var/lib/rustusb/agent/state.json "/tmp/tui-$TUI_LABEL-after.json"
    # The only mutating Start in this path came from the actual terminal.
    # Resolve its existing identity using RetryStart, which cannot create work.
    TUI_REQUEST=$(jq -er --arg profile "$TUI_PROFILE" --slurpfile old "/tmp/tui-$TUI_LABEL-before.json" '[.requests[]|select(.id as $id|($old[0].requests|map(.id)|index($id))==null)]|select(length==1 and .[0].uid==1000 and .[0].profile==$profile)|.[0].id' "/tmp/tui-$TUI_LABEL-after.json")
    agent start "$TUI_PROFILE" --retry --request-id "$TUI_REQUEST" > "/tmp/start-$TUI_PROFILE-$AGENT_EPOCH.json" 2> "/tmp/start-$TUI_PROFILE-$AGENT_EPOCH.log"
    jq -e --arg profile "$TUI_PROFILE" '.schema_version==1 and .journal_healthy and .response.result=="started" and .response.replayed==true and .response.job.operator_uid==1000 and .response.job.profile==$profile' "/tmp/start-$TUI_PROFILE-$AGENT_EPOCH.json" > /dev/null
    cp /var/lib/rustusb/agent/state.json "/tmp/tui-$TUI_LABEL-after-retry.json"
    jq -e --slurpfile old "/tmp/tui-$TUI_LABEL-after.json" '.requests==$old[0].requests' "/tmp/tui-$TUI_LABEL-after-retry.json" > /dev/null
    echo "AGENT_START_RETURNED role=$ROLE profile=$TUI_PROFILE epoch=$AGENT_EPOCH wait=0"
}
tui_stop_job() {
    TUI_PROFILE=$1
    TUI_JOB=$(jq -er '.response.job.id' "/tmp/start-$TUI_PROFILE-$AGENT_EPOCH.json")
    tui_run "stop-job-$TUI_PROFILE" "stop-job-$TUI_PROFILE-$AGENT_EPOCH" "$TUI_JOB"
    wait_sessions "$TUI_PROFILE" "/tmp/tui-stop-job-$TUI_PROFILE-$AGENT_EPOCH-sessions.json" '.response.sessions|length>0 and all(.[]; .terminal and .cleanup=="verified")'
    agent jobs "$TUI_PROFILE" > "/tmp/tui-stop-job-$TUI_PROFILE-$AGENT_EPOCH-jobs.json"
}
