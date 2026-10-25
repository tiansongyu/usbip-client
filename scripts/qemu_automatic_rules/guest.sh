#!/bin/sh
# Disposable paired fixture extension; no production input or synthetic ACK.
automatic_agent_exec() {
    if [ "${AUTOMATION_ENABLED:-0}" = 1 ]; then
        exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run --automation-policy /etc/rustusb/automation.json
    fi
    exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run
}
automatic_policy() {
    # The old no-policy denial sequence has finished. Join its actual idle owner.
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-no-policy
    jq -e '.schema_version==2 and .jobs==[] and .sessions==[]' /var/lib/rustusb/agent/state.json > /dev/null
    jq -n --slurpfile keys /tmp/keys.json '{schema_version:1,broker_uid:2000,grants:[{operator_uid:1000,profile:"server",devices:$keys[0],triggers:["local_profile_ready"],rearm:["once"],max_rules:4}]}' > /etc/rustusb/automation.json
    chmod 644 /etc/rustusb/automation.json
    [ "$(stat -c '%u:%a:%h' /etc/rustusb/automation.json)" = 0:644:1 ]
    cp /etc/rustusb/automation.json /tmp/automation-policy.json
    AUTOMATION_ENABLED=1
    agent_start 1
    jq -e '.schema_version==3 and .jobs==[] and .automation.rules==[]' /var/lib/rustusb/agent/state.json > /dev/null
    echo AUTOMATION_POLICY_ENABLED
}
automatic_start_job() {
    [ "$ROLE" = server ] && [ "$1" = server ]
    if [ "$AGENT_EPOCH" = 1 ]; then
        agent rule server create --name 'QEMU fixed Native pool' --trigger local-profile-ready --settle-seconds 2 --enabled > /tmp/rule-create.jsonl 2> /tmp/rule-create.log
        jq -s -e 'length==2 and .[0].event=="rule_mutation_intent" and .[1].response.result=="rule_mutation_applied" and .[0].request_id==.[1].response.request_id and .[1].response.replayed==false and .[1].response.outcome.revision==1' /tmp/rule-create.jsonl > /dev/null
        RULE_ID=$(jq -sr '.[1].response.outcome.rule_id' /tmp/rule-create.jsonl)
        MUTATION_ID=$(jq -sr '.[0].request_id' /tmp/rule-create.jsonl)
        agent rule server retry "$MUTATION_ID" > /tmp/rule-create-retry.jsonl
        jq -s -e 'length==2 and .[0].event=="rule_mutation_query" and .[1].response.replayed==true and .[1].response.outcome.revision==1' /tmp/rule-create-retry.jsonl > /dev/null
    else
        agent rules server > /tmp/rules-restarted.json
        jq -e '.response.rules|length==1 and all(.[];.enabled and .paused_by_user and .revision==2 and .status=="paused_by_user")' /tmp/rules-restarted.json > /dev/null
        # More than the minimum settle period; no explicit rearm has committed.
        sleep 3
        cp /var/lib/rustusb/agent/state.json /tmp/agent-paused-restart.json
        jq -e '(.jobs|length)==1 and (.automation.origins|length)==1 and (.automation.rules|length)==1 and .automation.rules[0].paused_by_user' /tmp/agent-paused-restart.json > /dev/null
        agent rule server enable "$RULE_ID" --expected-revision 2 > /tmp/rule-enable.jsonl
        jq -s -e 'length==2 and .[0].event=="rule_mutation_intent" and .[1].response.result=="rule_mutation_applied" and .[1].response.outcome.revision==3 and .[1].response.replayed==false' /tmp/rule-enable.jsonl > /dev/null
    fi
    for auto_poll in $(seq 1 300); do
        agent rules server > "/tmp/rules-fired-$AGENT_EPOCH.json"
        if jq -e --argjson revision "$((AGENT_EPOCH*2-1))" '.response.rules|length==1 and all(.[];.last_origin!=null and .last_origin.revision==$revision and .last_job!=null)' "/tmp/rules-fired-$AGENT_EPOCH.json" > /dev/null; then break; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    AUTO_START_ID=$(jq -er '.response.rules[0].last_origin.request_id' "/tmp/rules-fired-$AGENT_EPOCH.json")
    # This real RPC only reads the Start already admitted by the background rule.
    agent start server --retry --request-id "$AUTO_START_ID" > "/tmp/start-server-$AGENT_EPOCH.json"
    jq -e '.schema_version==1 and .journal_healthy and .response.result=="started" and .response.replayed and .response.job.operator_uid==1000' "/tmp/start-server-$AGENT_EPOCH.json" > /dev/null
    agent rule-events server > "/tmp/rule-events-$AGENT_EPOCH.json"
    cp /var/lib/rustusb/agent/state.json "/tmp/agent-auto-admitted-$AGENT_EPOCH.json"
    echo "AUTOMATION_START_OBSERVED epoch=$AGENT_EPOCH original_request=$AUTO_START_ID"
}
