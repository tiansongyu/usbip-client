#!/bin/sh
# Only executed by the independent guest flock owner. No USB operations.
set -eu
[ "$(id -u)" = 0 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || exit 2
case " $(cat /proc/cmdline) " in *' rustusb.process_logging=1 '*) ;; *) exit 2 ;; esac
[ "$#" = 1 ] && [ "${#1}" = 36 ]
exec 6<> /run/log-gate/release
cat /proc/uptime > /tmp/gate-actual-held.uptime
printf 'LOG_GATE_HELD nonce=%s\n' "$1"
# BusyBox ash bounded read. The removable obstruction always has a hard limit.
# shellcheck disable=SC3045
IFS= read -r -t 180 received <&6
[ "$received" = "$1" ]
cat /proc/uptime > /tmp/gate-actual-released.uptime
printf 'LOG_GATE_RELEASED nonce=%s\n' "$1"
