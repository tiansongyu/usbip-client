# Candidate collector only; called after each agent has loaded its fixed config.
# Paths below are fixed by this disposable harness, never accepted from RPC.
config_evidence() {
    CONFIG_MARK=$1
    CONFIG_FILES='/etc/rustusb/agent.json /etc/rustusb/ca.der /etc/rustusb/server.der /etc/rustusb/client.der'
    if [ "$ROLE" = server ]; then
        CONFIG_FILES="$CONFIG_FILES /etc/rustusb/server.json"
    else
        CONFIG_FILES="$CONFIG_FILES /etc/rustusb/client-hid.json /etc/rustusb/client-serial.json"
    fi
    : > "/tmp/config-metadata-$CONFIG_MARK.jsonl"
    for config_path in / /etc /etc/rustusb /run /run/agent /var /var/lib /var/lib/rustusb /var/lib/rustusb/agent $CONFIG_FILES "/etc/rustusb/$ROLE-key.der"; do
        [ ! -L "$config_path" ] && [ -e "$config_path" ]
        config_copy=''
        if [ -d "$config_path" ]; then
            config_kind=directory
        elif [ "$config_path" = "/etc/rustusb/$ROLE-key.der" ]; then
            [ -f "$config_path" ]
            config_kind=private_key_metadata_only
            # Deliberately no cat/cp/hash/read of private key bytes.
        else
            [ -f "$config_path" ]
            config_kind=public_file
            config_copy="actual-config-$CONFIG_MARK-$(basename "$config_path")"
            cp "$config_path" "/tmp/$config_copy"
        fi
        jq -nc --arg path "$config_path" --arg kind "$config_kind" --arg copy "$config_copy" \
            --arg canonical "$(readlink -f "$config_path")" --arg mode "$(stat -c '%a' "$config_path")" \
            --argjson uid "$(stat -c '%u' "$config_path")" --argjson gid "$(stat -c '%g' "$config_path")" \
            --argjson links "$(stat -c '%h' "$config_path")" --argjson bytes "$(stat -c '%s' "$config_path")" \
            --argjson device "$(stat -c '%d' "$config_path")" --argjson inode "$(stat -c '%i' "$config_path")" \
            '{path:$path,kind:$kind,canonical:$canonical,uid:$uid,gid:$gid,mode:$mode,links:$links,bytes:$bytes,device:$device,inode:$inode,captured_file:(if $copy=="" then null else $copy end)}' >> "/tmp/config-metadata-$CONFIG_MARK.jsonl"
    done
    jq -s 'sort_by(.path)' "/tmp/config-metadata-$CONFIG_MARK.jsonl" > "/tmp/config-metadata-$CONFIG_MARK.json"
    echo "AGENT_CONFIG_CAPTURED role=$ROLE epoch=$CONFIG_MARK private_key_bytes=false"
}
