#!/bin/sh
# Disposable native UI fixture only; every edit uses the original real agent RPC.
gui_rules_settings() {
    gui_dom '!document.getElementById("refresh").disabled'
    gui_js 'document.querySelectorAll("nav button")[5].click();return true;' /run/gui-rules-settings.json
    gui_wait '.rules.current and .pending==null' /run/gui-rules-settings-ready.json
    if jq -e '.value.rules.policy_enabled and .value.rules.grant!=null' /run/gui-rules-settings-ready.json >/dev/null; then
        gui_dom 'document.getElementById("rule-new") && !document.getElementById("rule-new").disabled && !document.getElementById("refresh").disabled'
    else
        gui_dom 'document.querySelector(".automation .section-note")?.textContent==="Policy disabled by administrator" && document.getElementById("rule-new").disabled && !document.getElementById("refresh").disabled'
    fi
}
gui_rules_no_policy() {
    gui_open rules-no-policy server
    gui_rules_settings
    gui_dom 'document.getElementById("rule-new").disabled'
    gui_js 'return {policy:document.querySelector(".automation .section-note").textContent,disabled:document.getElementById("rule-new").disabled};' /tmp/gui-no-policy.json
    cp /var/lib/rustusb/agent/state.json /tmp/gui-no-policy-before.json
    jq -n '{script:"const done=arguments[arguments.length-1];window.__TAURI__.core.invoke(\"gui_action\",{action:{action:\"rule_create\",spec:{name:\"denied\",trigger:\"local_profile_ready\",settle_seconds:2,rearm:\"once\"}}}).then(value=>done({unexpected:value}),e=>done({error:String(e)}));",args:[]}' >/run/gui-request.json
    gui_http POST "/session/$GUI_SESSION/execute/async" "$(cat /run/gui-request.json)" /tmp/gui-no-policy-rejected.json
    cp /var/lib/rustusb/agent/state.json /tmp/gui-no-policy-after.json
    gui_close
}
gui_rule_fill() {
    gui_js "const n=document.getElementById('rule-name');n.value='$1';n.dispatchEvent(new Event('input'));return true;" /run/gui-rule-fill.json
}
gui_rule_submit() {
    gui_js 'document.getElementById("rule-submit").click();return true;' /run/gui-rule-submit.json
    gui_confirm
    cp "/tmp/gui-$GUI_LABEL-confirm.json" "/tmp/gui-rule-confirm-$1.json"
}
gui_rule_revision() {
    gui_wait ".pending==null and .unresolved_rule==null and .rules.current and (.rules.rules|length)==1 and .rules.rules[0].revision==\"$1\"" "/tmp/gui-rule-revision-$1.json"
    gui_dom "document.querySelector('.rule-row .data-id')?.textContent.includes('revision $1 ·') && !document.getElementById('refresh').disabled"
}
gui_rule_edit() {
    gui_js 'document.querySelector("[data-rule-action=edit]").click();return document.getElementById("confirmation").open;' /run/gui-rule-edit.json
    if jq -e '.value==true' /run/gui-rule-edit.json >/dev/null; then
        gui_confirm
        cp "/tmp/gui-$GUI_LABEL-confirm.json" "/tmp/gui-rule-confirm-inspect-$1.json"
    fi
    gui_dom 'document.getElementById("rule-name")'
}
gui_rule_change() {
    gui_js "document.querySelector('[data-rule-action=$1]').click();return true;" /run/gui-rule-change.json
    gui_confirm
    cp "/tmp/gui-$GUI_LABEL-confirm.json" "/tmp/gui-rule-confirm-$1-$AGENT_EPOCH.json"
}
gui_rules_start() {
    gui_open "rules-$AGENT_EPOCH" server
    gui_rules_settings
    if [ "$AGENT_EPOCH" = 1 ]; then
        gui_js 'document.getElementById("rule-new").click();const n=document.getElementById("rule-name");n.value="界".repeat(33);n.dispatchEvent(new Event("input"));document.getElementById("rule-submit").click();return {invalid:n.getAttribute("aria-invalid")==="true",focused:document.activeElement.id==="rule-errors"};' /tmp/gui-rule-invalid.json
        jq -e '.value.invalid and .value.focused' /tmp/gui-rule-invalid.json >/dev/null
        gui_rule_fill 'QEMU GUI fixed pool'
        gui_js 'const n=document.getElementById("rule-name");n.focus();n.setSelectionRange(2,5);return true;' /run/gui-rule-caret.json
        gui_snapshot /run/gui-rule-revision-before.json
        revision=$(jq -er '.value.revision' /run/gui-rule-revision-before.json)
        gui_wait "(.revision|tonumber)>$revision and .pending==null" /tmp/gui-rule-refresh-advanced.json
        sleep 3
        gui_js 'const n=document.getElementById("rule-name");return {name:n.value,focus:document.activeElement.id,start:n.selectionStart,end:n.selectionEnd};' /tmp/gui-rule-draft.json
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-before.json
        kill -STOP "$AGENT_PID"
        for poll in $(seq 1 100); do grep -q '^State:.*T' "/proc/$AGENT_PID/status" && break; sleep .01; done
        cat "/proc/$AGENT_PID/status" "/proc/$AGENT_PID/stat" >/tmp/gui-rule-stopped-agent.txt
        gui_rule_submit create
        gui_wait '.pending!=null and .unresolved_rule!=null' /tmp/gui-rule-pending.json
        gui_http POST "/session/$GUI_SESSION/refresh" '{}' /tmp/gui-rule-renderer-refresh.json
        gui_wait '.pending!=null and .unresolved_rule!=null' /tmp/gui-rule-reloaded.json
        gui_wait '.pending==null and .unresolved_rule!=null and (.rules.result|contains("Response unknown"))' /tmp/gui-rule-unknown.json
        kill -CONT "$AGENT_PID"
        for poll in $(seq 1 300); do
            if jq -e '(.automation.rules|length)==1 and (.automation.mutations|length)==1' /var/lib/rustusb/agent/state.json >/dev/null; then break; fi
            kill -0 "$AGENT_PID"; sleep .05
        done
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-late-commit.json
        RULE_ID=$(jq -er '.automation.rules[0].id' /tmp/gui-rule-late-commit.json)
        gui_dom '[...document.querySelectorAll("button")].some(b=>b.textContent==="Query original rule edit" && !b.disabled)'
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-before-resolve.json
        gui_js '[...document.querySelectorAll("button")].find(b=>b.textContent==="Query original rule edit").click();return true;' /run/gui-rule-query.json
        gui_confirm
        cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/gui-rule-confirm-query.json
        gui_wait '.pending==null and .unresolved_rule==null and (.rules.result|startswith("Resolved original rule "))' /tmp/gui-rule-resolved.json
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-after-resolve.json
        # Renderer reload returns to Local; explicitly return to Settings.
        gui_rules_settings
        gui_rule_revision 1
        gui_rule_edit 1
        gui_rule_fill 'GUI first replacement'
        gui_rule_submit replace-2
        gui_rule_revision 2
        gui_rule_edit 2
        gui_rule_fill 'Preserved stale draft'
        agent rule server disable "$RULE_ID" --expected-revision 2 > /tmp/gui-rule-concurrent-disable.jsonl
        gui_rule_revision 3
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-stale-before.json
        gui_js 'document.getElementById("rule-submit").click();return {text:document.getElementById("rule-errors").textContent,name:document.getElementById("rule-name").value};' /tmp/gui-rule-stale-draft.json
        cp /var/lib/rustusb/agent/state.json /tmp/gui-rule-stale-after.json
        gui_rule_edit 3
        gui_rule_fill 'QEMU GUI fixed pool'
        gui_rule_submit replace-4
        gui_rule_revision 4
        gui_rule_change enable
        gui_rule_revision 5
        fire_revision=5
    else
        agent rules server > /tmp/rules-restarted.json
        jq -e '.response.rules|length==1 and all(.[];.enabled and .paused_by_user and .revision==6)' /tmp/rules-restarted.json >/dev/null
        sleep 3
        cp /var/lib/rustusb/agent/state.json /tmp/agent-paused-restart.json
        gui_rule_change enable
        gui_rule_revision 7
        fire_revision=7
    fi
    for auto_poll in $(seq 1 300); do
        agent rules server > "/tmp/rules-fired-$AGENT_EPOCH.json"
        if jq -e --argjson revision "$fire_revision" '.response.rules|length==1 and all(.[];.last_origin!=null and .last_origin.revision==$revision and .last_job!=null)' "/tmp/rules-fired-$AGENT_EPOCH.json" >/dev/null; then break; fi
        kill -0 "$AGENT_PID"; sleep .05
    done
    AUTO_START_ID=$(jq -er '.response.rules[0].last_origin.request_id' "/tmp/rules-fired-$AGENT_EPOCH.json")
    agent start server --retry --request-id "$AUTO_START_ID" > "/tmp/start-server-$AGENT_EPOCH.json"
    jq -e '.response.result=="started" and .response.replayed and .response.job.operator_uid==1000' "/tmp/start-server-$AGENT_EPOCH.json" >/dev/null
    cp /var/lib/rustusb/agent/state.json "/tmp/agent-auto-admitted-$AGENT_EPOCH.json"
    gui_wait '.rules.current and .rules.rules[0].last_job.state=="running"' "/tmp/gui-rule-active-$AGENT_EPOCH.json"
    if [ "$AGENT_EPOCH" = 2 ]; then
        gui_rule_change disable
        gui_rule_revision 8
        gui_wait '.rules.rules[0].enabled==false and .rules.rules[0].last_job.state=="running"' /tmp/gui-rule-disabled-active.json
        gui_rule_change delete
        gui_rule_revision 9
        gui_wait '.rules.rules[0].deleted and .rules.rules[0].last_job.state=="running"' /tmp/gui-rule-deleted-active.json
    fi
    gui_js 'return document.body.innerText;' "/tmp/gui-rule-dom-$AGENT_EPOCH.json"
    gui_http GET "/session/$GUI_SESSION/screenshot" '' "/tmp/gui-rule-screenshot-$AGENT_EPOCH.json"
    gui_close
    agent jobs server > "/tmp/gui-rules-after-close-$AGENT_EPOCH.json"
    echo "AUTOMATION_GUI_START_OBSERVED epoch=$AGENT_EPOCH original_request=$AUTO_START_ID"
}
