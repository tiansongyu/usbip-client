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
    jq -e 'has("value") and (.value.error? == null)' "$dest" >/dev/null
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
    gui_original_result() { return "$original"; }
    gui_original_result
    cleanup
}
trap gui_cleanup EXIT
