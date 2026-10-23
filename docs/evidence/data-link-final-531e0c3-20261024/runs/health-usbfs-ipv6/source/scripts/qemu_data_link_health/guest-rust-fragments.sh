#!/bin/sh
# Included after the common helper definitions, before the actual role workflow.
# No product input RPC or synthetic root cleanup receipt is introduced here.
[ "${LINK_COMMON:-}" = 1 ] || exit 2
case "$BACKEND" in virtual|usbfs) ;; *) exit 2 ;; esac
rust_server_setup() {
    STAGE=rust-server-prepare
    [ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
    if [ "$BACKEND" = usbfs ]; then
        for name in xhci-hcd xhci-pci-renesas xhci-pci; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
        # shellcheck disable=SC2034
        for device_poll in $(seq 1 100); do
            legacy --json devices > /tmp/devices.json
            if jq -e 'length==2 and all(.[]; .export_blockers==[])' /tmp/devices.json > /dev/null; then break; fi
            sleep 0.05
        done
        jq -e 'length==2 and all(.[]; .export_blockers==[])' /tmp/devices.json > /dev/null
        HID_ID=$(jq -er '.[]|select(.vendor_id==1575 and .product_id==1)|.id' /tmp/devices.json)
        SERIAL_ID=$(jq -er '.[]|select(.vendor_id==1027 and .product_id==24577)|.id' /tmp/devices.json)
        HID_BUS=$(jq -er --arg id "$HID_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
        SERIAL_BUS=$(jq -er --arg id "$SERIAL_ID" '.[]|select(.id==$id)|.bus_id' /tmp/devices.json)
        HID_KEY="physical:$HID_ID" SERIAL_KEY="physical:$SERIAL_ID"
        host_baseline /tmp/host-before.json
        jq -e 'length==2 and all(.[]; (.configuration|tonumber)==1 and (.interfaces|length)==1) and ([.[].interfaces[].driver]|sort)==["ftdi_sio","usbhid"]' /tmp/host-before.json > /dev/null
        publish /tmp/host-before.json
        for bus in "$HID_BUS" "$SERIAL_BUS"; do
            node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "/sys/bus/usb/devices/$bus/busnum")" "$(cat "/sys/bus/usb/devices/$bus/devnum")")
            [ -c "$node" ] && [ "$(stat -c '%u' "$node")" = 0 ]
            chmod 600 "$node"
            for uid in 1000 2000; do
                # shellcheck disable=SC2016
                if (exec 5<&-; /usr/bin/setpriv --reuid="$uid" --regid="$uid" --clear-groups /bin/busybox sh -c 'exec 6<> "$1"' sh "$node") > /tmp/direct-usbfs-denied.log 2>&1; then return 1; fi
            done
        done
        jq -n --arg hid "$HID_ID" --arg serial "$SERIAL_ID" '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_native_device_ids:[],secure_physical_device_ids:[$hid,$serial]}' > /etc/rustusb/policy.json
    else
        HID_KEY=virtual:hid SERIAL_KEY=virtual:cdc-acm
        legacy --json devices > /tmp/devices.json
        jq -e 'length==0' /tmp/devices.json > /dev/null
        jq -n '{allowed_uids:[1000],export_device_ids:[],physical_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_native_device_ids:[],secure_physical_device_ids:[]}' > /etc/rustusb/policy.json
    fi
    jq -nc --arg hid "$HID_KEY" --arg serial "$SERIAL_KEY" '[$hid,$serial]' > /tmp/keys.json
    echo "AGENT_KEYS $(cat /tmp/keys.json)"
    start_helper
    PIN=$(cat /etc/rustusb/client-pin.txt)
    jq -n --arg listen "$LISTEN" --arg pin "$PIN" --slurpfile keys /tmp/keys.json '{schema_version:2,data_link:{protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,listen:$listen,devices:$keys[0],principals:[{id:"qemu-client",pins:[$pin],devices:$keys[0]}],ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/server.der"],private_key:"/etc/rustusb/server-key.der"}' > /etc/rustusb/server.json
    chown 2000:2000 /etc/rustusb/server-key.der; chmod 600 /etc/rustusb/server-key.der
    jq -nc --arg backend "$BACKEND" '[{id:"server",backend:$backend,config:"/etc/rustusb/server.json"}]' > /tmp/agent-profiles.json
}
rust_client_setup() {
    STAGE=rust-client-prepare
    for name in usbip-core vhci-hcd; do [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"; done
    host_read; printf '%s\n' "$CONTROL" > /tmp/keys.json
    if [ "$BACKEND" = virtual ]; then
        jq -e '.==["virtual:hid","virtual:cdc-acm"]' /tmp/keys.json > /dev/null
    else
        jq -e 'length==2 and all(.[]; startswith("physical:usb-"))' /tmp/keys.json > /dev/null
    fi
    HID_KEY=$(jq -er '.[0]' /tmp/keys.json) SERIAL_KEY=$(jq -er '.[1]' /tmp/keys.json)
    jq -n --slurpfile keys /tmp/keys.json '{allowed_uids:[1000],export_device_ids:[],peers:[],trusted_network:false,secure_broker_uid:2000,secure_imports:[{server_id:"qemu-server",device_keys:$keys[0]}]}' > /etc/rustusb/policy.json
    start_helper
    chown 2000:2000 /etc/rustusb/client-key.der; chmod 600 /etc/rustusb/client-key.der
    for profile in hid serial; do
        if [ "$profile" = hid ]; then key=$HID_KEY; else key=$SERIAL_KEY; fi
        jq -n --arg host "$HOST" --arg key "$key" '{schema_version:2,data_link:{protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000},broker_uid:2000,server_id:"qemu-server",endpoint:{host:$host,port:3241},server_name:"rustusb-qemu.test",device:$key,ca_certificates:["/etc/rustusb/ca.der"],certificate_chain:["/etc/rustusb/client.der"],private_key:"/etc/rustusb/client-key.der"}' > "/etc/rustusb/client-$profile.json"
    done
    echo '[{"id":"hid","backend":"client","config":"/etc/rustusb/client-hid.json"},{"id":"serial","backend":"client","config":"/etc/rustusb/client-serial.json"}]' > /tmp/agent-profiles.json
}
# Absence is an actual observation, not an invented empty journal object.
rust_no_native() {
    [ ! -e /var/lib/rustusb/native/channels.json ] && [ ! -L /var/lib/rustusb/native/channels.json ]
    printf 'absent\n' > "/tmp/native-$1.status"
}
agent_snapshot() {
    cp /var/lib/rustusb/agent/state.json "/tmp/agent-$1.json"
    if [ "$ROLE" = client ]; then
        cp /var/lib/rustusb/native/channels.json "/tmp/root-$1.json"
        native_snapshot "/tmp/vhci-$1.txt"
    else
        rust_no_native "$1"
        if [ "$BACKEND" = usbfs ]; then
            cp /var/lib/rustusb/physical/journal.json "/tmp/root-$1.json"
            jq -e '.schema_version==3 and (.records|type)=="array" and (.terminals|type)=="array"' "/tmp/root-$1.json" > /dev/null
            host_baseline "/tmp/host-$1.json"
        else
            [ ! -e /var/lib/rustusb/physical/journal.json ] && [ ! -L /var/lib/rustusb/physical/journal.json ]
            printf 'absent\n' > "/tmp/physical-$1.status"
        fi
    fi
    echo "AGENT_SNAPSHOT role=$ROLE mark=$1"
}
rust_host_phase() {
    if [ "$BACKEND" = usbfs ]; then
        host_baseline "/tmp/host-$1.json"
        if [ "$2" = exported ]; then cmp /tmp/host-before.json "/tmp/host-$1.json"; fi
    fi
}
rust_server_final() {
    rust_no_native final
    if [ "$BACKEND" = usbfs ]; then
        host_baseline /tmp/host-restored.json
        cmp /tmp/host-before.json /tmp/host-restored.json
    else
        legacy --json devices > /tmp/devices-final.json
        cmp /tmp/devices.json /tmp/devices-final.json
    fi
}
rust_final_root_check() {
    if [ "$BACKEND" = usbfs ]; then
        jq -e '.schema_version==3 and .records==[] and (.terminals|length)==2' /tmp/root-final.json > /dev/null
    else
        [ "$(cat /tmp/native-final.status)" = absent ] && [ "$(cat /tmp/physical-final.status)" = absent ]
    fi
}
record_client_locals() {
    if [ "$BACKEND" = usbfs ]; then physical_client_locals "$1"; return; fi
    cp /var/lib/rustusb/native/channels.json "/tmp/client-receipts-$1.json"
    jq -e '.schema_version==2 and .exports==[] and (.records|length)==2 and all(.records[]; .owner.uid==2000 and .correlation!=null and .state.phase=="observed" and .state.receipt.session.status==6)' "/tmp/client-receipts-$1.json" > /dev/null
    HID_LOCAL=$(jq -er --arg key "$HID_KEY" '.records[]|select(.target.device_key==$key)|.state.receipt.session.local_bus_id' "/tmp/client-receipts-$1.json")
    SERIAL_LOCAL=$(jq -er --arg key "$SERIAL_KEY" '.records[]|select(.target.device_key==$key)|.state.receipt.session.local_bus_id' "/tmp/client-receipts-$1.json")
    [ "$HID_LOCAL" != "$SERIAL_LOCAL" ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idVendor")" = 1234 ] && [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/idProduct")" = 0001 ]
    [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/idVendor")" = 1234 ] && [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/idProduct")" = 0002 ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/speed")" = 12 ] && [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/speed")" = 12 ]
    [ "$(cat "/sys/bus/usb/devices/$HID_LOCAL/bNumInterfaces")" = 1 ] && [ "$(cat "/sys/bus/usb/devices/$SERIAL_LOCAL/bNumInterfaces")" = 2 ]
    # shellcheck disable=SC2034
    for local_poll in $(seq 1 150); do
        EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
        TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$SERIAL_LOCAL")" -name 'ttyACM*' | head -1)
        [ -z "$EVENT" ] || [ -z "$TTY" ] || break
        sleep 0.05
    done
    [ -n "$EVENT" ] && [ -n "$TTY" ]
    [ "$(basename "$(readlink "/sys/bus/usb/devices/$HID_LOCAL:1.0/driver")")" = usbhid ]
    [ "$(basename "$(readlink "/sys/bus/usb/devices/$SERIAL_LOCAL:1.0/driver")")" = cdc_acm ]
    EVENT=/dev/input/$(basename "$EVENT") TTY=/dev/$(basename "$TTY")
    jq -nc --arg hid "$HID_LOCAL" --arg serial "$SERIAL_LOCAL" --arg hid_generation "$(stat -Lc '%d:%i' "/sys/bus/usb/devices/$HID_LOCAL")" --arg serial_generation "$(stat -Lc '%d:%i' "/sys/bus/usb/devices/$SERIAL_LOCAL")" '{hid:$hid,serial:$serial,hid_generation:$hid_generation,serial_generation:$serial_generation}' > "/tmp/local-identities-$1.json"
}
actual_io() {
    if [ "$BACKEND" = usbfs ]; then
        physical_actual_io "$1" "$2"
        publish "/tmp/rx-$1.bin"; publish "/tmp/tx-$1.bin"
        return
    fi
    IO_ROUND=$1 IO_HID=$2
    stty -F "$TTY" raw -echo 57600 clocal -hupcl
    exec 4<> "$TTY"
    timeout 15 dd bs=1 count=35 <&4 > "/tmp/echo-$IO_ROUND.bin" 2> "/tmp/dd-echo-$IO_ROUND.log" & RX_PID=$!
    echo "AGENT_CLIENT_WAIT_INPUT round=$IO_ROUND hid=$IO_HID"
    host_expect "INPUT_$IO_ROUND"
    printf 'RUSTUSB-TX-%s-0001:fedcba9876543210\n' "$IO_ROUND" > "/tmp/tx-$IO_ROUND.bin"
    cat "/tmp/tx-$IO_ROUND.bin" >&4
    wait "$RX_PID"
    cmp "/tmp/tx-$IO_ROUND.bin" "/tmp/echo-$IO_ROUND.bin"
    exec 4<&-
    publish "/tmp/echo-$IO_ROUND.bin"; publish "/tmp/tx-$IO_ROUND.bin"
    echo "AGENT_CLIENT_IO round=$IO_ROUND hid=$IO_HID rx=35 tx=35 cdc_echo=true hid_key_observed=false"
}
