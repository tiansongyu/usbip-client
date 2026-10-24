#!/bin/sh
# Disposable guest-only repeated TLS client. Never use outside this fixture.
set -eu
[ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || exit 2
case " $(cat /proc/cmdline) " in *' rustusb.secure=1 '*) ;; *) exit 2 ;; esac
case " $(cat /proc/cmdline) " in *' rustusb.role=client '*) ;; *) exit 2 ;; esac
[ "$#" = 3 ] || exit 2
case "$1:$2:$3" in [12]:[1-8]:ipv4|[12]:[1-8]:ipv6) ;; *) exit 2 ;; esac
cycle=$1 worker=$2 family=$3
directory="/run/broker/rate-$cycle"
if [ "$family" = ipv4 ]; then peer=192.0.2.1:3241; else peer='[fd42:7275:7374::1]:3241'; fi
export OPENSSL_CONF=/dev/null
now() {
    read -r uptime rest < /proc/uptime
    seconds=${uptime%.*} fraction=${uptime#*.}
    case "$seconds" in ''|*[!0-9]*) exit 2 ;; esac
    case "$seconds" in 0|[1-9]*) ;; *) exit 2 ;; esac
    [ "${#seconds}" -le 6 ] || exit 2
    case "$fraction" in [0-9][0-9]) ;; *) exit 2 ;; esac
    fraction=${fraction#0}
    CENTIS=$((seconds*100 + fraction))
}
now; started=$CENTIS; deadline=$((started+800)); count=0
printf '%s\n' "$started" > "$directory/$worker.started"
while [ "$count" -lt 256 ]; do
    now; [ "$CENTIS" -lt "$deadline" ] || break
    before=$CENTIS; count=$((count+1))
    if timeout 7 /usr/bin/openssl s_client -quiet -tls1_3 -verify_return_error \
        -verify_hostname rustusb-qemu.test -servername rustusb-qemu.test \
        -CAfile /etc/rustusb/ca.pem -connect "$peer" \
        -cert /etc/rustusb/unmapped.pem -key /etc/rustusb/unmapped-key.pem \
        -alpn rustusb-usbip/1 < /dev/null >> "$directory/$worker.stdout" 2>> "$directory/$worker.stderr"; then rc=0; else rc=$?; fi
    now
    printf '%s %s %s %s\n' "$count" "$before" "$CENTIS" "$rc" >> "$directory/$worker.timeline"
    [ "$rc" = 0 ] || [ "$rc" = 1 ] || exit 1
    sleep 0.05
done
now; printf '%s\n' "$CENTIS" > "$directory/$worker.finished"
[ "$count" -gt 0 ] && [ "$CENTIS" -ge "$deadline" ]
