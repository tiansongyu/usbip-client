#!/bin/sh
# Test-only disposable guest actions; no product test RPC or forged OS identity.
settled_refresh() {
    case "$1" in true|false) ;; *) return 2;; esac
    gui_dom '!document.getElementById("refresh").disabled'
    gui_js "const c=document.querySelector('.settings input[type=checkbox]');c.checked=$1;document.querySelector('.settings-actions button').click();return true;" /run/decision-refresh-toggle.json
    gui_wait ".pending==null and .options.auto_refresh==$1" /run/decision-refresh-options.json
    gui_dom "!document.getElementById('refresh').disabled && document.querySelector('.settings input[type=checkbox]').checked===$1"
}
settled_actor() {
    { echo "PID=$AGENT_PID";cat "/proc/$AGENT_PID/status" "/proc/$AGENT_PID/stat";sha256sum "/proc/$AGENT_PID/exe"; } >"$1"
}
settled_refusal() {
    cp /var/lib/rustusb/agent/state.json /tmp/decision-before.json
    gui_wait '.rules.rules[0].revision=="2" and .rules.decision_support and .options.auto_refresh==false and .pending==null' /tmp/decision-observed-old.json
    kill -STOP "$AGENT_PID"
    for poll in $(seq 1 100); do grep -q '^State:.*T' "/proc/$AGENT_PID/status" && break;sleep .01;done
    cat "/proc/$AGENT_PID/status" "/proc/$AGENT_PID/stat" >/tmp/decision-stopped-agent.txt
    gui_rule_submit service-cas-refusal
    cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/decision-confirm-refusal.json
    gui_wait '.pending!=null and .unresolved_rule.settled_protocol==true' /tmp/decision-pending.json
    gui_http POST "/session/$GUI_SESSION/refresh" '{}' /tmp/decision-renderer-refresh.json
    gui_wait '.pending!=null and .unresolved_rule.settled_protocol==true' /tmp/decision-reloaded.json
    gui_wait '.pending==null and .unresolved_rule!=null and (.rules.result|contains("Response unknown"))' /tmp/decision-unknown.json
    kill -CONT "$AGENT_PID"
    for poll in $(seq 1 300); do
        if jq -e '.rule_rejections.sequence==1 and (.rule_rejections.entries|length)==1' /var/lib/rustusb/agent/state.json >/dev/null;then break;fi
        kill -0 "$AGENT_PID";sleep .05
    done
    cp /var/lib/rustusb/agent/state.json /tmp/decision-late.json
    settled_actor /tmp/decision-agent-before.txt
    kill -TERM "$AGENT_PID";wait_exit "$AGENT_PID" agent-decision-restart
    agent_start 1
    settled_actor /tmp/decision-agent-after.txt
    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-restart.json
    gui_dom '[...document.querySelectorAll("button")].some(b=>b.textContent==="Query original rule edit"&&!b.disabled)'
    cp /var/lib/rustusb/agent/state.json /tmp/decision-before-query.json
    gui_js '[...document.querySelectorAll("button")].find(b=>b.textContent==="Query original rule edit").click();return true;' /run/decision-query.json
    gui_confirm;cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/decision-confirm-query.json
    gui_wait '.pending==null and .unresolved_rule==null and .last_rule_decision.decision.decision=="rejected" and (.rules.result|startswith("Rejected original rule edit "))' /tmp/decision-resolved.json
    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-query.json
    DECISION_ID=$(jq -er '.value.last_rule_decision.original.request_id' /tmp/decision-resolved.json)
    set +e
    agent rule server --settled retry "$DECISION_ID" >/tmp/decision-cli-query.jsonl 2>/tmp/decision-cli-query.log
    decision_cli_exit=$?
    set -e
    echo "$decision_cli_exit" >/tmp/decision-cli-query.wait
    [ "$decision_cli_exit" = 1 ]
    jq -s -e 'length==2 and .[0].event=="rule_decision_query" and .[0].protocol=="settled" and .[0].request_id==.[1].response.request_id and .[1].response.result=="rule_decided" and .[1].response.replayed and .[1].response.decision=={decision:"rejected",sequence:1,failure:"state"}' /tmp/decision-cli-query.jsonl >/dev/null
    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-cli-query.json
    gui_rules_settings
    gui_dom 'document.getElementById("rule-restore")&&!document.getElementById("rule-restore").disabled'
    gui_js 'document.getElementById("rule-restore").click();return true;' /run/decision-restore.json
    gui_confirm;cp "/tmp/gui-$GUI_LABEL-confirm.json" /tmp/decision-confirm-restore.json
    gui_dom 'document.getElementById("rule-name")?.value==="Preserved stale draft"'
    gui_js 'return {name:document.getElementById("rule-name").value,originalRevision:document.querySelector(".rule-editor .section-note").textContent.includes("Original revision 2")?"2":null,profile:document.querySelector(".rule-editor .section-note").textContent.includes("Draft profile: server")?"server":null,settled:document.getElementById("rule-rejected").textContent.includes("Settled rejection")};' /tmp/decision-restored-draft.json
    settled_refresh true
}
