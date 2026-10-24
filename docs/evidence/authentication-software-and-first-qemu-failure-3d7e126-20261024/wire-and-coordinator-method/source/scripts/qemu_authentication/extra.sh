# Included only in generated disposable PID1; never execute on the host.
auth_policy() {
    local auth_epoch
    auth_epoch=$1
    jq -n --arg epoch "$auth_epoch" --slurpfile base /etc/rustusb/server.json \
        --rawfile old /etc/rustusb/client-pin.txt --rawfile new /etc/rustusb/replacement-pin.txt \
        --rawfile limited /etc/rustusb/limited-pin.txt --rawfile expired /etc/rustusb/expired-pin.txt \
        --rawfile badeku /etc/rustusb/badeku-pin.txt --rawfile outsider /etc/rustusb/outsider-pin.txt '
        def pin: rtrimstr("\n");
        $base[0] | .principals=([
          {id:"replacement",pins:[($new|pin)],devices:.devices},
          {id:"restricted",pins:[($limited|pin)],devices:[.devices[0]]},
          {id:"expired",pins:[($expired|pin)],devices:.devices},
          {id:"badeku",pins:[($badeku|pin)],devices:.devices},
          {id:"unknown-ca",pins:[($outsider|pin)],devices:.devices}]+
          (if $epoch=="1" then [{id:"old-client",pins:[($old|pin)],devices:.devices}] else [] end))' > /run/auth-policy.json
    cp /run/auth-policy.json /etc/rustusb/server.json
    chmod 644 /etc/rustusb/server.json
    cp /etc/rustusb/server.json "/tmp/auth-server-config-$auth_epoch.json"
}
auth_process() {
    local auth_pid auth_label
    auth_pid=$1 auth_label=$2
    kill -0 "$auth_pid"
    cat "/proc/$auth_pid/stat" > "/tmp/auth-$auth_label.stat"
    cat "/proc/$auth_pid/status" > "/tmp/auth-$auth_label.status"
    sha256sum "/proc/$auth_pid/exe" > "/tmp/auth-$auth_label.exe"
}
auth_state() {
    local auth_label auth_phase
    auth_label=$1 auth_phase=$2
    auth_process "$SERVER_PID" "server-$auth_label"
    auth_process "$HELPER_PID" "helper-$auth_label"
    snapshot "auth-$auth_label"
    if [ "$BACKEND" = native ]; then
        native_host_phase "auth-$auth_label" "$auth_phase"
        native_server_journal "auth-$auth_label" "$auth_phase"
    elif [ "$BACKEND" = usbfs ]; then
        cp /var/lib/rustusb/physical/journal.json "/tmp/auth-physical-$auth_label.json"
        host_baseline "/tmp/host-auth-$auth_label.json"
    fi
}
auth_server_barrier() {
    local auth_label auth_phase
    auth_label=$1 auth_phase=$2
    echo "AUTH_SERVER_WAIT label=$auth_label"
    host_expect "AUTH_BEGIN $auth_label"
    auth_state "before-$auth_label" "$auth_phase"
    echo "AUTH_SERVER_SAVED label=$auth_label"
    host_expect "AUTH_END $auth_label"
    auth_state "after-$auth_label" "$auth_phase"
    if [ "$BACKEND" = native ]; then
        cmp "/tmp/server-native-auth-before-$auth_label.json" "/tmp/server-native-auth-after-$auth_label.json"
        cmp "/tmp/host-auth-before-$auth_label.json" "/tmp/host-auth-after-$auth_label.json"
    elif [ "$BACKEND" = usbfs ]; then
        cmp "/tmp/auth-physical-before-$auth_label.json" "/tmp/auth-physical-after-$auth_label.json"
        cmp "/tmp/host-auth-before-$auth_label.json" "/tmp/host-auth-after-$auth_label.json"
    fi
    echo "AUTH_SERVER_CHECKED label=$auth_label"
}
auth_rotate() {
    STAGE=credential-retirement
    auth_process "$SERVER_PID" retiring-server
    auth_process "$HELPER_PID" retained-helper
    kill -TERM "$SERVER_PID"
    wait_exit "$SERVER_PID" retiring-server
    jq -se 'any(.[]; .event=="stopped") and all(.[]; .event!="error")' /tmp/server.events > /dev/null
    cp /tmp/server.events /tmp/auth-server-epoch1.events
    cp /tmp/server.log /tmp/auth-server-epoch1.log
    if [ "$BACKEND" != virtual ]; then
        host_baseline /tmp/auth-host-after-retire.json
        cmp /tmp/host-before.json /tmp/auth-host-after-retire.json
        if [ "$BACKEND" = native ]; then
            cp /var/lib/rustusb/native/channels.json /tmp/auth-native-retired.json
            jq -e '.records==[] and .exports==[]' /tmp/auth-native-retired.json > /dev/null
            cp /tmp/server-native-exported.json /tmp/auth-native-epoch1-exported.json
            cp /tmp/host-exported.json /tmp/auth-host-epoch1-exported.json
        else
            cp /var/lib/rustusb/physical/journal.json /tmp/auth-physical-retired.json
            jq -e '.records==[]' /tmp/auth-physical-retired.json > /dev/null
        fi
    fi
    auth_policy 2
    : > /tmp/server.events
    : > /tmp/server.log
    (exec 3<&- 5<&-; exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb secure-server --backend "$BACKEND" --config /etc/rustusb/server.json --control-stdin < /run/broker/control > /tmp/server.events 2> /tmp/server.log) &
    SERVER_PID=$!
    for attempt in $(seq 1 200); do
        jq -se 'any(.[]; .event=="ready" and .transport=="mutual_tls")' /tmp/server.events > /dev/null 2>&1 && break
        kill -0 "$SERVER_PID"; sleep 0.05
    done
    jq -se 'any(.[]; .event=="ready" and .transport=="mutual_tls")' /tmp/server.events > /dev/null
    if [ "$BACKEND" = native ]; then
        cp /var/lib/rustusb/native/channels.json /tmp/server-native-exported.json
        native_host_phase exported exported
        native_server_journal exported exported
        cp /tmp/server-native-exported.json /tmp/auth-native-epoch2-exported.json
        cp /tmp/host-exported.json /tmp/auth-host-epoch2-exported.json
    fi
    auth_process "$SERVER_PID" replacement-server
    auth_process "$HELPER_PID" retained-helper-after
    awk '{print $1 ":" $22}' /tmp/auth-retained-helper.stat > /run/auth-helper-before.identity
    awk '{print $1 ":" $22}' /tmp/auth-retained-helper-after.stat > /run/auth-helper-after.identity
    cmp /run/auth-helper-before.identity /run/auth-helper-after.identity
    cmp /tmp/auth-retained-helper.exe /tmp/auth-retained-helper-after.exe
    echo 'AUTH_ROTATION_COMPLETE old_wait=0 same_helper=true'
}
auth_client_configs() {
    local name file
    export OPENSSL_CONF=/dev/null
    for name in client replacement limited unmapped expired badeku outsider; do
        chown 2000:2000 "/etc/rustusb/$name-key.pem"
        chmod 600 "/etc/rustusb/$name-key.pem"
        [ "$name" = client ] || { chown 2000:2000 "/etc/rustusb/$name-key.der"; chmod 600 "/etc/rustusb/$name-key.der"; }
    done
    cp /etc/rustusb/client-hid.json /etc/rustusb/old-client.json
    for name in replacement limited unmapped expired badeku outsider; do
        jq --arg name "$name" '.certificate_chain=[("/etc/rustusb/"+$name+".der")]|.private_key=("/etc/rustusb/"+$name+"-key.der")' /etc/rustusb/old-client.json > "/etc/rustusb/auth-$name.json"
        cp "/etc/rustusb/auth-$name.json" "/tmp/auth-client-config-$name.json"
    done
    cp /etc/rustusb/old-client.json /tmp/auth-client-config-old.json
    if [ "$FAMILY" = ipv4 ]; then AUTH_PEER=192.0.2.1:3241; else AUTH_PEER='[fd42:7275:7374::1]:3241'; fi
    /usr/bin/openssl version -a > /tmp/auth-openssl-version.txt
    for file in /etc/rustusb/*.der /etc/rustusb/*-key.pem; do
        stat -c '%n %u %g %a %h %s' "$file" >> /tmp/auth-credential-metadata.txt
    done
}
auth_cli_refusal() {
    local auth_name auth_config auth_rc
    auth_name=$1 auth_config=$2
    if broker --json secure-discover --config "$auth_config" > "/tmp/auth-$AUTH_LABEL-$auth_name.stdout" 2> "/tmp/auth-$AUTH_LABEL-$auth_name.stderr"; then auth_rc=0; else auth_rc=$?; fi
    echo "$auth_rc" > "/tmp/auth-$AUTH_LABEL-$auth_name.wait"
    [ "$auth_rc" = 1 ] && [ ! -s "/tmp/auth-$AUTH_LABEL-$auth_name.stdout" ]
    grep -E 'TLS handshake failed|TLS certificate has no configured principal|early eof|unexpected end of file|Connection reset|connection reset|Broken pipe|broken pipe' "/tmp/auth-$AUTH_LABEL-$auth_name.stderr" > /dev/null
}
auth_wire_request() {
    local auth_operation auth_key auth_bus auth_file auth_json auth_length
    auth_operation=$1 auth_key=$2 auth_bus=$3 auth_file=$4
    if [ "$auth_operation" = catalog ]; then auth_json='{"schema_version":1,"op":"catalog"}'; else
        auth_json=$(jq -nc --arg key "$auth_key" '{schema_version:1,op:"import",device_key:$key}')
    fi
    auth_length=${#auth_json}
    [ "$auth_length" -gt 0 ] && [ "$auth_length" -lt 256 ]
    { printf '\000\000\000'; printf "\\$(printf '%03o' "$auth_length")"; printf '%s' "$auth_json"
      if [ "$auth_operation" = catalog ]; then printf '\001\021\200\005\000\000\000\000'; else
          printf '\001\021\200\003\000\000\000\000'; printf '%-32s' "$auth_bus" | tr ' ' '\000'
      fi
    } > "$auth_file"
}
auth_wire() {
    local auth_name auth_identity auth_alpn auth_input auth_rc
    auth_name=$1 auth_identity=$2 auth_alpn=$3 auth_input=$4
    set -- s_client -quiet -tls1_3 -verify_return_error -verify_hostname rustusb-qemu.test -servername rustusb-qemu.test -CAfile /etc/rustusb/ca.pem -connect "$AUTH_PEER"
    [ "$auth_identity" = none ] || set -- "$@" -cert "/etc/rustusb/$auth_identity.pem" -key "/etc/rustusb/$auth_identity-key.pem"
    [ "$auth_alpn" = none ] || set -- "$@" -alpn "$auth_alpn"
    if timeout 12 /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/openssl "$@" < "$auth_input" > "/tmp/auth-$AUTH_LABEL-$auth_name.reply" 2> "/tmp/auth-$AUTH_LABEL-$auth_name.log"; then auth_rc=0; else auth_rc=$?; fi
    echo "$auth_rc" > "/tmp/auth-$AUTH_LABEL-$auth_name.wait"
    [ "$auth_rc" = 0 ] || [ "$auth_rc" = 1 ]
}
auth_client_capture() {
    local auth_prefix
    auth_prefix=$1
    native_snapshot "$auth_prefix.txt"
    if [ -f /var/lib/rustusb/native/channels.json ]; then
        cp /var/lib/rustusb/native/channels.json "$auth_prefix.json"
        echo present > "$auth_prefix.presence"
    else
        echo absent > "$auth_prefix.presence"
    fi
}
auth_client_batch() {
    local auth_epoch name kind wire_identity wire_alpn auth_bus
    AUTH_LABEL=$1 auth_epoch=$2
    echo "AUTH_CLIENT_WAIT label=$AUTH_LABEL"
    host_expect "AUTH_GO $AUTH_LABEL"
    auth_client_capture "/tmp/auth-client-before-$AUTH_LABEL"
    for name in unmapped expired badeku outsider; do auth_cli_refusal "$name" "/etc/rustusb/auth-$name.json"; done
    jq '.server_name="wrong.test"' /etc/rustusb/client-hid.json > /etc/rustusb/auth-wrong-name.json
    jq '.ca_certificates=["/etc/rustusb/other-ca.der"]' /etc/rustusb/client-hid.json > /etc/rustusb/auth-wrong-trust.json
    cp /etc/rustusb/auth-wrong-name.json "/tmp/auth-$AUTH_LABEL-wrong-name-config.json"
    cp /etc/rustusb/auth-wrong-trust.json "/tmp/auth-$AUTH_LABEL-wrong-trust-config.json"
    auth_cli_refusal wrong-name /etc/rustusb/auth-wrong-name.json
    auth_cli_refusal wrong-trust /etc/rustusb/auth-wrong-trust.json
    if [ "$auth_epoch" = 2 ]; then auth_cli_refusal retired /etc/rustusb/old-client.json; fi
    auth_wire_request catalog ignored ignored "/tmp/auth-$AUTH_LABEL-catalog.request"
    for kind in absent-cert absent-alpn wrong-alpn; do
        case "$kind" in absent-cert) wire_identity=none; wire_alpn=rustusb-usbip/1;; absent-alpn) wire_identity=replacement; wire_alpn=none;; wrong-alpn) wire_identity=replacement; wire_alpn=unrelated/1;; esac
        auth_wire "$kind" "$wire_identity" "$wire_alpn" "/tmp/auth-$AUTH_LABEL-catalog.request"
        [ ! -s "/tmp/auth-$AUTH_LABEL-$kind.reply" ]
        [ "$(cat "/tmp/auth-$AUTH_LABEL-$kind.wait")" = 1 ]
    done
    auth_bus=$(jq -er --arg key "$SERIAL_KEY" '.[]|select(.device_key==$key)|.device.bus_id' /tmp/catalog-initial.json)
    auth_wire_request import "$SERIAL_KEY" "$auth_bus" "/tmp/auth-$AUTH_LABEL-unauthorized.request"
    auth_wire unauthorized limited rustusb-usbip/1 "/tmp/auth-$AUTH_LABEL-unauthorized.request"
    printf '\001\021\000\003\000\000\000\004' > /run/auth-denied.expected
    cmp "/tmp/auth-$AUTH_LABEL-unauthorized.reply" /run/auth-denied.expected
    if [ "${AUTH_LABEL#held-}" != "$AUTH_LABEL" ]; then
        auth_wire duplicate replacement rustusb-usbip/1 "/tmp/auth-$AUTH_LABEL-unauthorized.request"
        printf '\001\021\000\003\000\000\000\002' > /run/auth-busy.expected
        cmp "/tmp/auth-$AUTH_LABEL-duplicate.reply" /run/auth-busy.expected
    else
        broker --json secure-discover --config /etc/rustusb/auth-limited.json > "/tmp/auth-$AUTH_LABEL-limited.json"
        jq -e --arg hid "$HID_KEY" 'length==1 and .[0].device_key==$hid' "/tmp/auth-$AUTH_LABEL-limited.json" > /dev/null
    fi
    auth_client_capture "/tmp/auth-client-after-$AUTH_LABEL"
    cmp "/tmp/auth-client-before-$AUTH_LABEL.txt" "/tmp/auth-client-after-$AUTH_LABEL.txt"
    cmp "/tmp/auth-client-before-$AUTH_LABEL.presence" "/tmp/auth-client-after-$AUTH_LABEL.presence"
    if [ "$(cat "/tmp/auth-client-before-$AUTH_LABEL.presence")" = present ]; then
        cmp "/tmp/auth-client-before-$AUTH_LABEL.json" "/tmp/auth-client-after-$AUTH_LABEL.json"
    fi
    echo "AUTH_CLIENT_DONE label=$AUTH_LABEL"
    host_expect "AUTH_CHECKED $AUTH_LABEL"
}
