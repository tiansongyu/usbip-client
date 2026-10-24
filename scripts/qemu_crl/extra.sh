# Included in disposable PID1 only. No host execution.
auth_policy() {
    crl_original_auth_policy "$1"
    cp "/etc/rustusb/crl-epoch$1.der" /etc/rustusb/active-crl.der
    jq --rawfile blocked /etc/rustusb/crl-blocked-pin.txt --rawfile rotated /etc/rustusb/crl-rotated-pin.txt '
        .certificate_revocation_lists=["/etc/rustusb/active-crl.der"] |
        .principals += [{id:"crl-blocked",pins:[($blocked|rtrimstr("\n"))],devices:.devices},
                        {id:"crl-rotated",pins:[($rotated|rtrimstr("\n"))],devices:.devices}]' /etc/rustusb/server.json > /run/crl-server.json
    cp /run/crl-server.json /etc/rustusb/server.json
    cp /etc/rustusb/server.json "/tmp/auth-server-config-$1.json"
}
auth_client_configs() {
    local name
    for name in hid serial; do
        jq '.certificate_revocation_lists=["/etc/rustusb/crl-clean.der"]' "/etc/rustusb/client-$name.json" > /run/crl-client.json
        cp /run/crl-client.json "/etc/rustusb/client-$name.json"
    done
    crl_original_auth_client_configs
    for name in crl-blocked crl-rotated; do
        chown 2000:2000 "/etc/rustusb/$name-key.der"
        chmod 600 "/etc/rustusb/$name-key.der"
        jq --arg name "$name" '.certificate_chain=[("/etc/rustusb/"+$name+".der")]|.private_key=("/etc/rustusb/"+$name+"-key.der")' /etc/rustusb/old-client.json > "/etc/rustusb/$name.json"
    done
    for name in server bad-signature expired future malformed; do
        jq --arg name "$name" '.certificate_revocation_lists=[("/etc/rustusb/crl-"+$name+".der")]' /etc/rustusb/auth-replacement.json > "/etc/rustusb/crl-$name.json"
    done
    jq '.certificate_revocation_lists=["/etc/rustusb/crl-clean.der","/etc/rustusb/crl-epoch2.der"]' /etc/rustusb/auth-replacement.json > /etc/rustusb/crl-overlap.json
    jq '.certificate_revocation_lists=[]' /etc/rustusb/auth-replacement.json > /etc/rustusb/crl-empty.json
    jq '.certificate_revocation_lists=null' /etc/rustusb/auth-replacement.json > /etc/rustusb/crl-null.json
    stat -c '%n %u %g %a %h %s' /etc/rustusb/crl*.der > /tmp/crl-credential-metadata.txt
}
crl_resolved_security() {
    local crl_label crl_attempt
    crl_label=$1
    for crl_attempt in $(seq 1 100); do
        auth_security_snapshot "crl-$crl_label-$crl_attempt"
        if jq -e '.tls.in_flight==0 and .framing.in_flight==0' "/tmp/auth-security-crl-$crl_label-$crl_attempt.json" > /dev/null; then
            cp "/tmp/auth-security-crl-$crl_label-$crl_attempt.json" "/tmp/crl-security-$crl_label.json"
            return 0
        fi
        sleep 0.05
    done
    return 1
}
auth_server_barrier() {
    local crl_label crl_phase
    crl_label=$1 crl_phase=$2
    crl_original_auth_server_barrier "$crl_label" "$crl_phase"
    echo "CRL_SERVER_WAIT label=$crl_label"
    host_expect "CRL_BEGIN $crl_label"
    if [ "$crl_label" = held-1 ]; then
        cp /etc/rustusb/crl-epoch2.der /etc/rustusb/active-crl.der
    fi
    sha256sum /etc/rustusb/active-crl.der > "/tmp/crl-active-$crl_label.sha256"
    auth_state "crl-before-$crl_label" "$crl_phase"
    crl_resolved_security "before-$crl_label"
    echo "CRL_SERVER_SAVED label=$crl_label"
    host_expect "CRL_END $crl_label"
    auth_state "crl-after-$crl_label" "$crl_phase"
    crl_resolved_security "after-$crl_label"
    echo "CRL_SERVER_CHECKED label=$crl_label"
}
crl_run() {
    local name config expected rc
    name=$1 config=$2 expected=$3
    cp "$config" "/tmp/crl-$CRL_LABEL-$name.config.json"
    if broker --json secure-discover --config "$config" > "/tmp/crl-$CRL_LABEL-$name.stdout" 2> "/tmp/crl-$CRL_LABEL-$name.stderr"; then rc=0; else rc=$?; fi
    echo "$rc" > "/tmp/crl-$CRL_LABEL-$name.wait"
    [ "$rc" = "$expected" ]
    if [ "$expected" = 0 ]; then
        jq -e 'type=="array"' "/tmp/crl-$CRL_LABEL-$name.stdout" > /dev/null
    else
        [ ! -s "/tmp/crl-$CRL_LABEL-$name.stdout" ] && [ -s "/tmp/crl-$CRL_LABEL-$name.stderr" ]
        case "$name" in
            expired|future|malformed|overlap|empty|null)
                grep 'permission denied: secure client configuration:' "/tmp/crl-$CRL_LABEL-$name.stderr" > /dev/null ;;
            server|bad-signature)
                grep 'permission denied: secure server authentication failed' "/tmp/crl-$CRL_LABEL-$name.stderr" > /dev/null ;;
            blocked|rotated)
                grep -E 'peer closed connection without sending TLS close_notify|Connection reset by peer \(os error 104\)|permission denied: secure server authentication failed|received fatal alert: CertificateRevoked' "/tmp/crl-$CRL_LABEL-$name.stderr" > /dev/null ;;
        esac
    fi
}
auth_client_batch() {
    local crl_epoch name
    CRL_LABEL=$1 crl_epoch=$2
    crl_original_auth_client_batch "$CRL_LABEL" "$crl_epoch"
    echo "CRL_CLIENT_WAIT label=$CRL_LABEL"
    host_expect "CRL_GO $CRL_LABEL"
    auth_client_capture "/tmp/crl-client-before-$CRL_LABEL"
    crl_run blocked /etc/rustusb/crl-blocked.json 1
    if [ "$crl_epoch" = 1 ]; then crl_run rotated /etc/rustusb/crl-rotated.json 0; else crl_run rotated /etc/rustusb/crl-rotated.json 1; fi
    for name in server bad-signature expired future malformed overlap empty null; do
        crl_run "$name" "/etc/rustusb/crl-$name.json" 1
    done
    crl_run positive /etc/rustusb/auth-replacement.json 0
    auth_client_capture "/tmp/crl-client-after-$CRL_LABEL"
    cmp "/tmp/crl-client-before-$CRL_LABEL.txt" "/tmp/crl-client-after-$CRL_LABEL.txt"
    cmp "/tmp/crl-client-before-$CRL_LABEL.presence" "/tmp/crl-client-after-$CRL_LABEL.presence"
    if [ "$(cat "/tmp/crl-client-before-$CRL_LABEL.presence")" = present ]; then
        cmp "/tmp/crl-client-before-$CRL_LABEL.json" "/tmp/crl-client-after-$CRL_LABEL.json"
    fi
    echo "CRL_CLIENT_DONE label=$CRL_LABEL"
    host_expect "CRL_CHECKED $CRL_LABEL"
}
