#!/bin/sh
# Disposable QEMU extension. Actual agent rule/Start replies only.
automatic_role() {
    [ "$ROLE:$BACKEND" = client:virtual ] || [ "$ROLE:$BACKEND" = server:usbfs ]
}
manifest() {
    plain_manifest
    if automatic_role; then
        jq '.operators += [{uid:1001,profiles:[.profiles[].id],actions:["read","start","stop","discover","audit"]}]' /etc/rustusb/agent.json > /tmp/automatic-manifest.json
        cp /tmp/automatic-manifest.json /etc/rustusb/agent.json
        cp /etc/rustusb/agent.json /tmp/agent-manifest.json
    fi
}
automatic_agent_exec() {
    if [ "${AUTOMATION_ENABLED:-0}" = 1 ]; then
        exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run --automation-policy /etc/rustusb/automation.json
    fi
    exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run
}
automatic_root_observation() {
    OBS_MARK=$1
    for OBS_KIND in native physical; do
        if [ "$OBS_KIND" = native ]; then OBS_PATH=/var/lib/rustusb/native/channels.json; else OBS_PATH=/var/lib/rustusb/physical/journal.json; fi
        [ ! -L "$OBS_PATH" ]
        if [ -f "$OBS_PATH" ]; then
            printf 'present\n' > "/tmp/automatic-root-$OBS_KIND-$OBS_MARK.status"
            cp "$OBS_PATH" "/tmp/automatic-root-$OBS_KIND-$OBS_MARK.json"
        else
            [ ! -e "$OBS_PATH" ]
            printf 'absent\n' > "/tmp/automatic-root-$OBS_KIND-$OBS_MARK.status"
        fi
    done
}
automatic_setup() {
    if ! automatic_role; then return; fi
    kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-no-policy
    jq -e '.schema_version==2 and .jobs==[] and .sessions==[]' /var/lib/rustusb/agent/state.json > /dev/null
    if [ "$ROLE" = client ]; then
        jq -n --slurpfile keys /tmp/keys.json '{schema_version:1,broker_uid:2000,grants:[
          {operator_uid:1000,profile:"hid",devices:[$keys[0][0]],triggers:["remote_device_available"],rearm:["once"],max_rules:1},
          {operator_uid:1000,profile:"serial",devices:[$keys[0][1]],triggers:["remote_device_available"],rearm:["once"],max_rules:1}]}' > /etc/rustusb/automation.json
        AUTOMATIC_TRIGGER=remote-device-available
        AUTOMATIC_DENIED_PROFILE=hid
    else
        jq -n --slurpfile keys /tmp/keys.json '{schema_version:1,broker_uid:2000,grants:[{operator_uid:1000,profile:"server",devices:$keys[0],triggers:["local_profile_ready"],rearm:["once"],max_rules:1}]}' > /etc/rustusb/automation.json
        AUTOMATIC_TRIGGER=local-profile-ready
        AUTOMATIC_DENIED_PROFILE=server
    fi
    chmod 644 /etc/rustusb/automation.json
    [ "$(stat -c '%u:%a:%h' /etc/rustusb/automation.json)" = 0:644:1 ]
    cp /etc/rustusb/automation.json /tmp/automation-policy.json
    jq -nc --arg mode "$(stat -c '%a' /etc/rustusb/automation.json)" --arg canonical "$(readlink -f /etc/rustusb/automation.json)" --argjson uid "$(stat -c '%u' /etc/rustusb/automation.json)" --argjson links "$(stat -c '%h' /etc/rustusb/automation.json)" '{uid:$uid,mode:$mode,links:$links,canonical:$canonical}' > /tmp/automation-policy-metadata.json
    AUTOMATION_ENABLED=1
    agent_start 1
    cp /var/lib/rustusb/agent/state.json /tmp/automatic-before-denied.json
    automatic_root_observation before-denied
    # UID1001 has all manual profile ACLs but no automation grant.
    if (exec 5<&-; /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json rule "$AUTOMATIC_DENIED_PROFILE" create --name 'No delegation' --trigger "$AUTOMATIC_TRIGGER" --enabled) > /tmp/automatic-denied.jsonl 2> /tmp/automatic-denied.log; then return 1; else AUTOMATIC_DENIED_RC=$?; fi
    [ "$AUTOMATIC_DENIED_RC" = 1 ]
    printf '%s\n' "$AUTOMATIC_DENIED_RC" > /tmp/automatic-denied.wait
    jq -s -e 'length==2 and .[0].event=="rule_mutation_intent" and .[1].response=={result:"error",code:"permission"}' /tmp/automatic-denied.jsonl > /dev/null
    cp /var/lib/rustusb/agent/state.json /tmp/automatic-after-denied.json
    jq -e '.schema_version==3 and .jobs==[] and .sessions==[] and .requests==[] and .automation.rules==[] and .automation.mutations==[] and .automation.origins==[] and .automation.events==[] and any(.audit[];.actor_uid==1001 and .action=="denied" and .failure=="permission")' /tmp/automatic-after-denied.json > /dev/null
    automatic_root_observation after-denied
    for OBS_KIND in native physical; do
        cmp "/tmp/automatic-root-$OBS_KIND-before-denied.status" "/tmp/automatic-root-$OBS_KIND-after-denied.status"
        if [ "$(cat "/tmp/automatic-root-$OBS_KIND-before-denied.status")" = present ]; then cmp "/tmp/automatic-root-$OBS_KIND-before-denied.json" "/tmp/automatic-root-$OBS_KIND-after-denied.json"; fi
    done
    echo "AUTOMATIC_NO_GRANT_DENIED role=$ROLE uid=1001 manual_acl=true automatic_grant=false"
}
start_profile() {
    if ! automatic_role; then plain_start_profile "$1" "$2"; return; fi
    AUTO_PROFILE=$1 AUTO_MARK=$2
    # Original IDs are stored in bounded fixture files, never shell expressions.
    if [ -f "/tmp/automatic-rule-$AUTO_PROFILE.id" ]; then
        AUTO_RULE_ID=$(cat "/tmp/automatic-rule-$AUTO_PROFILE.id")
        agent rules "$AUTO_PROFILE" > "/tmp/automatic-before-enable-$AUTO_PROFILE-$AUTO_MARK.json"
        AUTO_REVISION=$(jq -er --arg id "$AUTO_RULE_ID" '.response.rules[]|select(.id==$id and .status=="once_consumed" and .last_job.state=="finished")|.revision' "/tmp/automatic-before-enable-$AUTO_PROFILE-$AUTO_MARK.json")
        cp /var/lib/rustusb/agent/state.json "/tmp/automatic-before-enable-state-$AUTO_PROFILE-$AUTO_MARK.json"
        agent rule "$AUTO_PROFILE" enable "$AUTO_RULE_ID" --expected-revision "$AUTO_REVISION" > "/tmp/automatic-mutation-$AUTO_PROFILE-$AUTO_MARK.jsonl"
        AUTO_REVISION=$((AUTO_REVISION+1))
    else
        agent rule "$AUTO_PROFILE" create --name "QEMU $AUTO_PROFILE" --trigger "$AUTOMATIC_TRIGGER" --settle-seconds 2 --rearm once --enabled > "/tmp/automatic-mutation-$AUTO_PROFILE-$AUTO_MARK.jsonl"
        AUTO_RULE_ID=$(jq -ser '.[1].response.outcome.rule_id' "/tmp/automatic-mutation-$AUTO_PROFILE-$AUTO_MARK.jsonl")
        printf '%s\n' "$AUTO_RULE_ID" > "/tmp/automatic-rule-$AUTO_PROFILE.id"
        AUTO_REVISION=1
    fi
    jq -s -e --arg id "$AUTO_RULE_ID" --argjson rev "$AUTO_REVISION" 'length==2 and .[0].event=="rule_mutation_intent" and .[0].request_id==.[1].response.request_id and .[1].journal_healthy and .[1].response.result=="rule_mutation_applied" and .[1].response.replayed==false and .[1].response.outcome=={rule_id:$id,revision:$rev,deleted:false}' "/tmp/automatic-mutation-$AUTO_PROFILE-$AUTO_MARK.jsonl" > /dev/null
    AUTO_MUTATION_ID=$(jq -sr '.[0].request_id' "/tmp/automatic-mutation-$AUTO_PROFILE-$AUTO_MARK.jsonl")
    agent rule "$AUTO_PROFILE" retry "$AUTO_MUTATION_ID" > "/tmp/automatic-mutation-retry-$AUTO_PROFILE-$AUTO_MARK.jsonl"
    jq -s -e --arg id "$AUTO_RULE_ID" --argjson rev "$AUTO_REVISION" 'length==2 and .[0].event=="rule_mutation_query" and .[1].response.replayed and .[1].response.outcome=={rule_id:$id,revision:$rev,deleted:false}' "/tmp/automatic-mutation-retry-$AUTO_PROFILE-$AUTO_MARK.jsonl" > /dev/null
    for AUTO_POLL in $(seq 1 400); do
        agent rules "$AUTO_PROFILE" > "/tmp/automatic-fired-$AUTO_PROFILE-$AUTO_MARK.json"
        if jq -e --arg id "$AUTO_RULE_ID" --argjson rev "$AUTO_REVISION" '.response.rules|any(.id==$id and .last_origin.revision==$rev and .last_job!=null)' "/tmp/automatic-fired-$AUTO_PROFILE-$AUTO_MARK.json" > /dev/null; then break; fi
        kill -0 "$AGENT_PID"; sleep 0.05
    done
    AUTO_START_ID=$(jq -er --arg id "$AUTO_RULE_ID" --argjson rev "$AUTO_REVISION" '.response.rules[]|select(.id==$id and .last_origin.revision==$rev)|.last_origin.request_id' "/tmp/automatic-fired-$AUTO_PROFILE-$AUTO_MARK.json")
    agent start "$AUTO_PROFILE" --retry --request-id "$AUTO_START_ID" > "/tmp/start-$AUTO_PROFILE-$AUTO_MARK.json"
    jq -e '.schema_version==1 and .journal_healthy and .response.result=="started" and .response.replayed and .response.job.operator_uid==1000' "/tmp/start-$AUTO_PROFILE-$AUTO_MARK.json" > /dev/null
    cp /var/lib/rustusb/agent/state.json "/tmp/automatic-admitted-$AUTO_PROFILE-$AUTO_MARK.json"
    echo "AUTOMATIC_START_READBACK role=$ROLE profile=$AUTO_PROFILE mark=$AUTO_MARK revision=$AUTO_REVISION"
    echo "LINK_START_RETURNED role=$ROLE profile=$AUTO_PROFILE mark=$AUTO_MARK wait=0"
}
