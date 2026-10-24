#!/bin/sh
set -eu
BACKEND=native
ROOT_FILE=/tmp/rustusb-prepare-saved-proof-binding-20261024/fixture-native/live.json
check_target_proof() {
    if [ "$BACKEND" = native ]; then
        jq -e --slurpfile sessions /tmp/rustusb-prepare-saved-proof-binding-20261024/fixture-native/sessions.json '
          . as $root | ($sessions[0]|length)==2 and all($sessions[0][];
          . as $s | .attempts[0] as $a |
          all($root.exports[];.lease_id!=$a.resource_id) and
          any($root.terminals[].terminal;.resource_id==$a.resource_id and .kind=="native_export" and .owner_uid==2000 and .correlation==$a.correlation and .device_key==$s.device_key and .proof=="native_export_restored"))' "$ROOT_FILE" > /dev/null
    else
        jq -e --slurpfile sessions /tmp/rustusb-prepare-saved-proof-binding-20261024/fixture-native/sessions.json '
          . as $root | ($sessions[0]|length)==2 and all($sessions[0][];
          . as $s | .attempts[0] as $a |
          all($root.records[];.lease_id!=$a.resource_id) and
          any($root.terminals[];.resource_id==$a.resource_id and .kind=="usbfs_grant" and .owner_uid==2000 and .correlation==$a.correlation and .device_key==$s.device_key and .proof=="physical_baseline_restored"))' "$ROOT_FILE" > /dev/null
    fi
}
check_target_proof "$@"
