#!/bin/sh
# Owned disposable guest only; no host USB, NIC, helper or USB/IP kernel modules.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || exit 2
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.gui=native '*) ;; *) exit 2;; esac
mount -t sysfs sysfs /sys; mount -t devtmpfs devtmpfs /dev
mount -t tmpfs tmpfs /tmp; mount -t tmpfs -o mode=0755 tmpfs /run
stty -F /dev/ttyS1 raw -echo 115200 clocal -hupcl
exec >/dev/ttyS1 2>&1
STAGE=boot; AGENT=''; DRIVER=''; XVFB=''; GUI=''
collect() {
    for file in /tmp/gui-*; do
        [ -f "$file" ] || continue
        bytes=$(wc -c < "$file"); [ "$bytes" -le 2097152 ] || return 1
        name=$(basename "$file")
        echo "RUSTUSB_GUI_FILE_BEGIN name=$name bytes=$bytes sha256=$(sha256sum "$file" | awk '{print $1}')"
        base64 "$file"; echo "RUSTUSB_GUI_FILE_END name=$name"
    done
}
cleanup() {
    code=$?; trap - EXIT; set +e
    # New-session HTTP can fail after the driver has already launched the GUI.
    if [ -z "$GUI" ] && [ -f /tmp/gui-pid.txt ]; then
        candidate=$(cat /tmp/gui-pid.txt)
        case "$candidate" in ''|*[!0-9]*) ;; *)
            if [ "$(readlink "/proc/$candidate/exe")" = /usr/bin/rustusb-gui ]; then GUI=$candidate; fi ;;
        esac
    fi
    for pid in "$GUI" "$DRIVER" "$AGENT" "$XVFB"; do
        [ -n "$pid" ] || continue
        kill -TERM "$pid" 2>/dev/null
        for attempt in $(seq 1 200); do
            [ ! -e "/proc/$pid/status" ] && break
            grep -q '^State:.*Z' "/proc/$pid/status" && break
            sleep .05
        done
        if [ -e "/proc/$pid/status" ] && ! grep -q '^State:.*Z' "/proc/$pid/status"; then
            echo "RUSTUSB_GUI_FORCED_CHILD: $pid"; kill -KILL "$pid"; code=1
        fi
        wait "$pid"
    done
    collect || code=1
    if [ "$code" = 0 ]; then echo 'RUSTUSB_GUI_RESULT: PASS'; else echo "RUSTUSB_GUI_RESULT: FAIL stage=$STAGE exit=$code"; fi
    sync; poweroff -f
}
trap cleanup EXIT
for name in usb-common usbcore evdev hid hid-generic usbhid xhci-hcd xhci-pci-renesas xhci-pci; do
    [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
done
ip link set lo up
mkdir -p /dev/pts /run/agent /var/lib/rustusb/agent /home/operator/.config/rustusb /run/user/1000
mount -t devpts devpts /dev/pts
chown 2000:2000 /run/agent /var/lib/rustusb/agent; chmod 711 /run/agent; chmod 700 /var/lib/rustusb/agent
chown -R 1000:1000 /home/operator /run/user/1000; chmod 700 /home/operator /home/operator/.config /home/operator/.config/rustusb /run/user/1000
chown 2000:2000 /etc/rustusb/server-key.der; chmod 600 /etc/rustusb/server-key.der
key="native:usb-$(printf '%064d' 1)"; pin=$(sha256sum /etc/rustusb/client.der | awk '{print $1}')
jq -n --arg key "$key" --arg pin "$pin" '{schema_version:1,broker_uid:2000,listen:"127.0.0.1:3241",devices:[$key],principals:[{id:"qemu-client",pins:[$pin],devices:[$key]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server.json
jq -n '{schema_version:1,broker_uid:2000,helper_socket:"/run/rustusb/control.sock",control_directory:"/run/agent",data_directory:"/var/lib/rustusb/agent",profiles:[{id:"observe",backend:"native",config:"/etc/rustusb/server.json"}],operators:[{uid:1000,profiles:["observe"],actions:["read","audit"]}]}' >/etc/rustusb/agent.json
chmod 644 /etc/rustusb/server.json /etc/rustusb/agent.json
cp /etc/rustusb/agent.json /tmp/gui-manifest.json
stat -c '%u:%a:%h' /etc/rustusb/agent.json /etc/rustusb/server-key.der >/tmp/gui-config-stat.txt
/usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /bin/sh -c '[ ! -r /etc/rustusb/server-key.der ]'
/usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run >/tmp/gui-agent.stdout 2>/tmp/gui-agent.stderr & AGENT=$!
for attempt in $(seq 1 200); do
    if /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json profiles >/tmp/gui-profiles.json 2>/tmp/gui-profiles.stderr; then break; fi
    kill -0 "$AGENT"; sleep .05
done
jq -e '.journal_healthy and (.response.profiles|length)==1' /tmp/gui-profiles.json >/dev/null
/usr/bin/Xvfb :88 -screen 0 1280x900x24 -nolisten tcp >/tmp/gui-xvfb.stdout 2>/tmp/gui-xvfb.stderr & XVFB=$!
for attempt in $(seq 1 100); do [ ! -S /tmp/.X11-unix/X88 ] || break; sleep .05; done
export DISPLAY=:88 GDK_BACKEND=x11 TAURI_WEBVIEW_AUTOMATION=true LIBGL_ALWAYS_SOFTWARE=1 HOME=/home/operator XDG_RUNTIME_DIR=/run/user/1000 XDG_CACHE_HOME=/home/operator/.cache
/usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/WebKitWebDriver --host=127.0.0.1 --port=15888 >/tmp/gui-driver.stdout 2>/tmp/gui-driver.stderr & DRIVER=$!
http() {
    method=$1; route=$2; body=$3; output=$4
    curl --silent --show-error --fail-with-body --max-time 35 -X "$method" -H 'Content-Type: application/json' --data "$body" "http://127.0.0.1:15888$route" >"$output"
}
for attempt in $(seq 1 100); do
    if curl -fsS http://127.0.0.1:15888/status >/tmp/gui-driver-status.json 2>/dev/null; then break; fi
    sleep .05
done
STAGE=session
http POST /session '{"capabilities":{"alwaysMatch":{"webkitgtk:browserOptions":{"binary":"/usr/bin/gui-launcher","args":["--manifest","/etc/rustusb/agent.json","--settings-directory","/home/operator/.config/rustusb"]}}}}' /tmp/gui-session.json
SESSION=$(jq -er '.value.sessionId' /tmp/gui-session.json)
GUI=$(cat /tmp/gui-pid.txt)
actor() {
    { echo "PID=$GUI"; cat "/proc/$GUI/status" "/proc/$GUI/stat"; echo "EXE=$(readlink "/proc/$GUI/exe")"; sha256sum "/proc/$GUI/exe";
      for fd in /proc/"$GUI"/fd/*; do echo "FD=$(basename "$fd") TARGET=$(readlink "$fd")"; done;
    } >"/tmp/gui-actor-$1.txt"
}
actor initial
[ "$(awk '/^Uid:/{print $2 ":" $3 ":" $4 ":" $5}' "/proc/$GUI/status")" = 1000:1000:1000:1000 ]
js() {
    script=$1; dest=$2
    jq -n --arg script "$script" '{script:$script,args:[]}' >/tmp/request.json
    http POST "/session/$SESSION/execute/sync" "$(cat /tmp/request.json)" "$dest"
    jq -e 'has("value") and (.value|type)!="object" or (.value.error? == null)' "$dest" >/dev/null
}
async_js() {
    script=$1; dest=$2
    jq -n --arg script "$script" '{script:$script,args:[]}' >/tmp/request.json
    http POST "/session/$SESSION/execute/async" "$(cat /tmp/request.json)" "$dest"
}
snapshot() { async_js 'const done=arguments[arguments.length-1]; window.__TAURI__.core.invoke("gui_snapshot").then(done,e=>done({error:String(e)}));' "$1"; }
wait_view() {
    predicate=$1; dest=$2
    for attempt in $(seq 1 200); do
        snapshot "$dest"
        if jq -e ".value | $predicate" "$dest" >/dev/null; then return 0; fi
        sleep .05
    done
    return 1
}
wait_dom() {
    predicate=$1
    for attempt in $(seq 1 200); do
        js "return !!($predicate);" /tmp/gui-dom-wait.json
        if jq -e '.value==true' /tmp/gui-dom-wait.json >/dev/null; then return 0; fi
        sleep .05
    done
    return 1
}
shot() { http GET "/session/$SESSION/screenshot" '' "/tmp/gui-$1-screenshot.json"; }
wait_view '.profile=="observe" and .local.state=="Current"' /tmp/gui-initial.json
wait_dom 'document.getElementById("profile").value==="observe"'
js 'return {text:document.body.innerText,url:location.href,title:document.title,bridge:!!window.__TAURI__};' /tmp/gui-initial-dom.json
shot initial
# The actual command wrapper must reject extra fields, before reaching the real agent.
async_js 'const done=arguments[arguments.length-1];window.__TAURI__.core.invoke("gui_action",{action:{action:"start",path:"/tmp/arbitrary"}}).then(v=>done({unexpected:v}),e=>done({rejected:String(e)}));' /tmp/gui-strict-input.json
jq -e '.value.rejected and (.value.unexpected==null)' /tmp/gui-strict-input.json >/dev/null
[ ! -e /var/lib/rustusb/agent/state.json ]
STAGE=hotplug
echo 'RUSTUSB_GUI_REQUEST: add'
wait_view '.local.devices | length == 1' /tmp/gui-added.json
wait_dom 'document.querySelector(".device-name")'
js 'document.querySelector(".device-name").click();return true;' /tmp/gui-select.json
wait_dom 'document.querySelector(".detail").innerText.includes("device_address")'
js 'return document.querySelector(".detail").innerText;' /tmp/gui-detail-before.json
shot added
# Search is a real input event; clearing restores the same actual inventory.
js 'const s=document.getElementById("search");s.value="no-match";s.dispatchEvent(new Event("input"));return document.getElementById("content").innerText;' /tmp/gui-search-empty.json
js 'const s=document.getElementById("search");s.value="";s.dispatchEvent(new Event("input"));return true;' /tmp/gui-search-clear.json
echo 'RUSTUSB_GUI_REQUEST: remove'
wait_view '.local.devices | length == 0' /tmp/gui-removed.json
wait_dom 'document.querySelector(".detail").innerText.includes("Select an observed device")'
js 'return document.querySelector(".detail").innerText;' /tmp/gui-detail-removed.json
echo 'RUSTUSB_GUI_REQUEST: readd'
wait_view '.local.devices | length == 1' /tmp/gui-readded.json
wait_dom 'document.querySelector(".device-name")'
js 'document.querySelector(".device-name").click();return true;' /tmp/gui-select-again.json
wait_dom 'document.querySelector(".detail").innerText.includes("device_address")'
js 'return document.querySelector(".detail").innerText;' /tmp/gui-detail-after.json
# Six real pages, including no-Discover remote status; no fake network/data owner.
for index in 1 2 3 4 5 0; do
    js "document.querySelectorAll('nav button')[$index].click();return true;" /tmp/gui-page-click.json
    sleep .3
    js 'return document.body.innerText;' "/tmp/gui-page-$index.json"
done
STAGE=confirmation
js 'document.getElementById("start").click();return {open:document.getElementById("confirmation").open,focus:document.activeElement.value};' /tmp/gui-confirm-default.json
jq -e '.value=={open:true,focus:"cancel"}' /tmp/gui-confirm-default.json >/dev/null
js 'document.querySelector("dialog [value=cancel]").click();return true;' /tmp/gui-cancel.json
[ ! -e /var/lib/rustusb/agent/state.json ]
# A real read/audit-only OS peer is denied, and the original UUID remains in Rust.
js 'document.getElementById("start").click();document.getElementById("confirm-action").click();return true;' /tmp/gui-start-confirm.json
wait_view '.unresolved!=null and (.status|contains("Agent refused: Permission"))' /tmp/gui-denied.json
sleep .3
js 'return document.body.innerText;' /tmp/gui-denied-dom.json
shot denied
js 'location.reload();return true;' /tmp/gui-reload.json
sleep .5
wait_view '.unresolved!=null' /tmp/gui-after-renderer-reload.json
wait_dom 'document.getElementById("start").textContent==="Resolve original start" && !document.getElementById("start").disabled'
sleep .2
js 'document.getElementById("start").click();document.getElementById("confirm-action").click();return true;' /tmp/gui-retry-confirm.json
sleep .3
retry_ack=$(jq -r '.value.acknowledged|tonumber+1' /tmp/gui-denied.json)
wait_view ".acknowledged==\"$retry_ack\" and .unresolved!=null" /tmp/gui-retried.json
cp /var/lib/rustusb/agent/state.json /tmp/gui-agent-after.json
jq -e '.jobs==[] and .sessions==[] and .requests==[] and (.audit|length)==2 and all(.audit[];.actor_uid==1000 and .action=="denied" and .failure=="permission")' /tmp/gui-agent-after.json >/dev/null
STAGE=preferences
js 'document.querySelectorAll("nav button")[5].click();return true;' /tmp/gui-settings-click.json
sleep .3
js 'const s=document.querySelector("input[type=number]");s.value="3";document.querySelector(".settings input[type=checkbox]").checked=false;document.querySelector(".settings-actions button").click();return true;' /tmp/gui-settings-apply.json
wait_view '.options.refresh_seconds=="3" and .options.auto_refresh==false' /tmp/gui-settings-applied.json
sleep .2
js 'document.querySelectorAll(".settings-actions button")[1].click();return true;' /tmp/gui-settings-save.json
wait_view '.preferences_status=="Saved preferences"' /tmp/gui-settings-saved.json
cp /home/operator/.config/rustusb/preferences.json /tmp/gui-saved-preferences.json
stat -c '%u:%a:%h' /home/operator/.config/rustusb/preferences.json >/tmp/gui-preferences-stat.txt
js 'document.querySelectorAll("nav button")[0].click();return true;' /tmp/gui-narrow-page.json
http POST "/session/$SESSION/window/rect" '{"width":800,"height":600}' /tmp/gui-narrow-rect.json
wait_dom 'getComputedStyle(document.querySelector(".detail")).display==="none"'
js 'document.querySelector(".device-name").click();return true;' /tmp/gui-narrow-select.json
wait_dom 'document.querySelector(".detail").innerText.includes("device_address")'
js 'document.getElementById("details-toggle").click();return {expanded:document.getElementById("details-toggle").getAttribute("aria-expanded"),visible:getComputedStyle(document.querySelector(".detail")).display!=="none"};' /tmp/gui-narrow-details.json
jq -e '.value=={expanded:"true",visible:true}' /tmp/gui-narrow-details.json >/dev/null
shot narrow
STAGE=shutdown
actor before-stop
kill -TERM "$GUI"
for attempt in $(seq 1 200); do [ ! -e "/proc/$GUI/status" ] && [ -f /tmp/gui-wait.txt ] && break; sleep .05; done
[ ! -e "/proc/$GUI/status" ] && [ "$(cat /tmp/gui-wait.txt)" = 1 ]; GUI=''
grep -F 'start outcome unresolved: profile observe request_id' /tmp/gui-app.stderr >/dev/null
grep -F 'local worker joined' /tmp/gui-app.stderr >/dev/null
# Service is independently still alive and answers actual authorized reads.
kill -0 "$AGENT"
/usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json profiles >/tmp/gui-agent-after-window.json
kill -TERM "$AGENT"; set +e; wait "$AGENT"; result=$?; set -e; echo "$result" >/tmp/gui-agent-wait.txt; [ "$result" = 0 ]; AGENT=''
cmp /tmp/gui-agent-after.json /var/lib/rustusb/agent/state.json
[ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ] && [ ! -e /run/rustusb/control.sock ]
echo 'RUSTUSB_GUI_NO_USB_OWNERS: modules_absent=true helper_absent=true admitted_requests=0'
STAGE=complete
