#!/bin/sh
# Disposable single-guest mechanism test; no network, TLS, or product attach API.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || exit 2
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.kernel_socketpair_test=1 '*) ;; *) exit 2 ;; esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts /run/rustusb /run/pair-test /etc/rustusb /var/run/vhci_hcd
chmod 700 /run/pair-test
mount -t devpts devpts /dev/pts
STAGE=boot
cleanup() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "RUSTUSB_PAIR_RESULT: FAIL stage=$STAGE exit=$status"
        for file in /tmp/helper.log /tmp/probe.jsonl /tmp/probe.stderr /tmp/devices.json /tmp/doctor.json /tmp/probe-refusal.json /tmp/snapshots.jsonl /tmp/sessions.json /tmp/identity.json /tmp/waits.jsonl /tmp/io.json /tmp/export-hid.json /tmp/export-ftdi.json /tmp/unexport-hid.json /tmp/unexport-ftdi.json /tmp/snapshot-progress.txt /tmp/physical.jsonl /tmp/interfaces.jsonl /tmp/pair-1-1-configuration.raw /tmp/pair-1-1-count.raw /tmp/pair-1-2-configuration.raw /tmp/pair-1-2-count.raw /tmp/vhci-status.txt; do
            [ ! -f "$file" ] || { echo "DIAGNOSTIC $file"; cat "$file"; }
        done
        dmesg | tail -80
    fi
    sync
    poweroff -f
}
trap cleanup EXIT
[ -c /dev/ttyS1 ]
stty -F /dev/ttyS1 raw -echo 115200 clocal -hupcl
exec > /dev/ttyS1 2>&1
echo 'RUSTUSB_PAIR_EVIDENCE_CHANNEL: PASS device=ttyS1 kernel_console=ttyS0'
echo "RUSTUSB_PAIR_BOOT kernel=$(uname -r) scope=single_guest_anonymous_kernel_socketpair"
for name in usb-common usbcore xhci-hcd xhci-pci-renesas xhci-pci evdev hid hid-generic usbhid usbserial ftdi_sio usbip-core usbip-host vhci-hcd; do
    [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
done
[ -d /sys/module/usbip_host ] && [ -d /sys/module/vhci_hcd ]
unpriv() { /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/rustusb "$@"; }
trim() { tr -d ' \t\r\n'; }
STAGE=baseline
for attempt in $(seq 1 100); do
    unpriv --json devices > /tmp/devices.json
    jq -e 'length==2 and all(.[];(.export_blockers|length)==0)' /tmp/devices.json > /dev/null && break
    sleep 0.05
done
jq -e 'length==2 and all(.[];(.export_blockers|length)==0) and any(.[];.bus_id=="1-1" and .vendor_id==1575 and .product_id==1) and any(.[];.bus_id=="1-2" and .vendor_id==1027 and .product_id==24577)' /tmp/devices.json > /dev/null
HID_ID=$(jq -r '.[]|select(.bus_id=="1-1")|.id' /tmp/devices.json)
FTDI_ID=$(jq -r '.[]|select(.bus_id=="1-2")|.id' /tmp/devices.json)
[ -n "$HID_ID" ] && [ -n "$FTDI_ID" ] && [ "$HID_ID" != "$FTDI_ID" ]
for node in /dev/bus/usb/001/002 /dev/bus/usb/001/003; do
    [ -c "$node" ] && [ ! -L "$node" ] && [ "$(stat -c %u "$node")" = 0 ]
    chmod 600 "$node"
done
snapshot() {
    mark=$1
    : > /tmp/physical.jsonl
    for bus in 1-1 1-2; do
        base=/sys/bus/usb/devices/$bus
        generation=$(stat -L -c '%d:%i' "$base")
        address=$(cat "$base/devnum" | trim)
        printf 'stage=%s bus=%s step=read-configuration\n' "$mark" "$bus" > /tmp/snapshot-progress.txt
        cat "$base/bConfigurationValue" > "/tmp/pair-$bus-configuration.raw"
        cat "$base/bNumInterfaces" > "/tmp/pair-$bus-count.raw"
        real=$(readlink -f "$base")
        busnum=$(cat "$base/busnum" | trim)
        vendor=$(cat "$base/idVendor" | trim)
        product=$(cat "$base/idProduct" | trim)
        speed=$(cat "$base/speed" | trim)
        driver=$(readlink "$base/driver" | sed 's#.*/##')
        usbip_status=null
        [ ! -f "$base/usbip_status" ] || usbip_status=$(cat "$base/usbip_status" | trim)
        : > /tmp/interfaces.jsonl
        printf 'stage=%s bus=%s step=read-interfaces configuration_bytes=%s count_bytes=%s\n' "$mark" "$bus" "$(wc -c < "/tmp/pair-$bus-configuration.raw")" "$(wc -c < "/tmp/pair-$bus-count.raw")" > /tmp/snapshot-progress.txt
        for interface in /sys/bus/usb/devices/"$bus":*; do
            # No match is legitimate only with the separately verified empty active configuration.
            [ -e "$interface" ] || [ -L "$interface" ] || continue
            [ -d "$interface" ]
            ifdriver=$(readlink "$interface/driver" 2>/dev/null | sed 's#.*/##' || true)
            alternate=$(cat "$interface/bAlternateSetting" | trim)
            ifnumber=$(cat "$interface/bInterfaceNumber" | trim)
            ifclass=$(cat "$interface/bInterfaceClass" | trim)
            ifsubclass=$(cat "$interface/bInterfaceSubClass" | trim)
            ifprotocol=$(cat "$interface/bInterfaceProtocol" | trim)
            jq -Mcn --arg name "$(basename "$interface")" --arg number "$ifnumber" --argjson alt "$alternate" --arg driver "$ifdriver" --arg class "$ifclass" --arg subclass "$ifsubclass" --arg protocol "$ifprotocol" \
                '{name:$name,number_hex:$number,alternate:$alt,class_hex:$class,subclass_hex:$subclass,protocol_hex:$protocol,driver:(if $driver=="" then null else $driver end)}' >> /tmp/interfaces.jsonl
        done
        printf 'stage=%s bus=%s step=validate-snapshot\n' "$mark" "$bus" > /tmp/snapshot-progress.txt
        jq -Mcn --arg bus "$bus" --arg generation "$generation" --argjson address "$address" --arg path "$real" --argjson busnum "$busnum" --arg vendor "$vendor" --arg product "$product" --arg speed "$speed" --rawfile cfg "/tmp/pair-$bus-configuration.raw" --rawfile count "/tmp/pair-$bus-count.raw" --arg driver "$driver" --argjson status "$usbip_status" --slurpfile interfaces /tmp/interfaces.jsonl \
            'def unsigned: if test("^[ \t\r\n]*[0-9]+[ \t\r\n]*$") then tonumber else error("invalid active configuration number") end;
             (if $cfg=="" and $count=="" and ($interfaces|length)==0 then null
              else ($cfg|unsigned) as $c | ($count|unsigned) as $n |
                if $c>=1 and $c<=255 and $n>=1 and $n<=255 and $n==($interfaces|length) then $c else error("incomplete active configuration/interface set") end end) as $configuration |
             {bus_id:$bus,generation:$generation,address:$address,sysfs_path:$path,bus_number:$busnum,vendor:$vendor,product:$product,speed:$speed,configuration:$configuration,device_driver:$driver,usbip_status:$status,interfaces:$interfaces}' >> /tmp/physical.jsonl
    done
    cat /sys/devices/platform/vhci_hcd.0/status > /tmp/vhci-status.txt
    cat /proc/net/tcp > /tmp/tcp.txt
    if [ -f /proc/net/tcp6 ]; then cat /proc/net/tcp6 > /tmp/tcp6.txt; else : > /tmp/tcp6.txt; fi
    cat /proc/net/unix > /tmp/unix.txt
    [ "$(ls /sys/class/net)" = lo ]
    [ "$(wc -l < /tmp/tcp.txt)" -eq 1 ]
    [ "$(wc -l < /tmp/tcp6.txt)" -le 1 ]
    jq -Mcn --arg mark "$mark" --slurpfile devices /tmp/physical.jsonl --rawfile status /tmp/vhci-status.txt --rawfile tcp /tmp/tcp.txt --rawfile tcp6 /tmp/tcp6.txt --rawfile unix /tmp/unix.txt \
        '{event:"snapshot",stage:$mark,physical:$devices,vhci_status:$status,tcp:$tcp,tcp6:$tcp6,unix:$unix,network_interfaces:["lo"]}' >> /tmp/snapshots.jsonl
    printf 'RUSTUSB_PAIR_SNAPSHOT '; tail -n 1 /tmp/snapshots.jsonl
}
snapshot original
jq -se 'length==2 and .[0].interfaces==[{name:"1-1:1.0",number_hex:"00",alternate:0,class_hex:"03",subclass_hex:"01",protocol_hex:"01",driver:"usbhid"}] and .[1].interfaces==[{name:"1-2:1.0",number_hex:"00",alternate:0,class_hex:"ff",subclass_hex:"ff",protocol_hex:"ff",driver:"ftdi_sio"}] and all(.[];.configuration==1 and .device_driver=="usb") and .[0].speed=="480" and .[1].speed=="12"' /tmp/physical.jsonl > /dev/null
cp /tmp/physical.jsonl /tmp/original-physical.jsonl
jq -n --arg hid "$HID_ID" --arg ftdi "$FTDI_ID" '{allowed_uids:[1000],export_device_ids:[$hid,$ftdi],peers:[],trusted_network:true}' > /etc/rustusb/policy.json
chmod 644 /etc/rustusb/policy.json
/usr/bin/rustusb-helper > /tmp/helper.log 2>&1 & HELPER_PID=$!
for attempt in $(seq 1 100); do [ ! -S /run/rustusb/control.sock ] || break; sleep 0.05; done
[ -S /run/rustusb/control.sock ]
: > /tmp/installed-binaries.jsonl
for name in rustusb rustusb-helper kernel-socketpair-probe; do
    jq -Mcn --arg name "$name" --arg sha "$(sha256sum "/usr/bin/$name" | cut -d' ' -f1)" --argjson length "$(wc -c < "/usr/bin/$name")" '{key:$name,value:{length:$length,sha256:$sha}}' >> /tmp/installed-binaries.jsonl
done
jq -s 'from_entries' /tmp/installed-binaries.jsonl > /tmp/installed-binaries.json
jq -e --slurpfile actual /tmp/installed-binaries.json '.==$actual[0]' /etc/pair-test-binaries.json > /dev/null
[ "$(readlink "/proc/$HELPER_PID/exe")" = /usr/bin/rustusb-helper ]
jq -Mcn --slurpfile installed /tmp/installed-binaries.json --arg sha "$(sha256sum "/proc/$HELPER_PID/exe" | cut -d' ' -f1)" '{installed:$installed[0],helper_executed_sha256:$sha}' > /tmp/binaries.json
printf 'RUSTUSB_PAIR_BINARIES '; cat /tmp/binaries.json
unpriv --json doctor > /tmp/doctor.json
jq -e '.helper.status=="available" and .helper.peer_verified and .helper.kernel_available and .built_in_encryption==false and .network_security_verified==false' /tmp/doctor.json > /dev/null
[ "$(/usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups id -u)" = 1000 ]
if /usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups /usr/bin/kernel-socketpair-probe > /tmp/probe-refusal.json 2>&1; then refusal=0; else refusal=$?; fi
[ "$refusal" = 1 ]
jq -se 'length==1 and .[0].event=="error" and .[0].error=="guest root required"' /tmp/probe-refusal.json > /dev/null
printf 'RUSTUSB_PAIR_GUARD_REFUSAL '; cat /tmp/probe-refusal.json
STAGE=helper-export
unpriv --json export "$HID_ID" > /tmp/export-hid.json
unpriv --json export "$FTDI_ID" > /tmp/export-ftdi.json
snapshot exported
jq -se 'length==2 and all(.[];.device_driver=="usbip-host" and .usbip_status==1 and .configuration==null and .interfaces==[])' /tmp/physical.jsonl > /dev/null
echo 'RUSTUSB_PAIR_EXPORT: PASS uid=1000 devices=2 via=existing_helper'
STAGE=probe
mkfifo -m 600 /run/pair-test/control
: > /tmp/probe.jsonl
: > /tmp/probe.stderr
(exec 0<> /run/pair-test/control; exec /usr/bin/kernel-socketpair-probe) > /tmp/probe.jsonl 2> /tmp/probe.stderr & PROBE_PID=$!
PROBE_START=$(awk '{print $22}' "/proc/$PROBE_PID/stat")
DRAINED=0
drain_probe() {
    lines=$(wc -l < /tmp/probe.jsonl)
    if [ "$lines" -gt "$DRAINED" ]; then
        sed -n "$((DRAINED + 1)),${lines}p" /tmp/probe.jsonl | while IFS= read -r line; do printf 'RUSTUSB_PAIR_PROBE %s\n' "$line"; done
        DRAINED=$lines
    fi
    if [ "$lines" -gt 0 ]; then sed -n "1,${lines}p" /tmp/probe.jsonl > /tmp/probe-complete-lines.jsonl; else : > /tmp/probe-complete-lines.jsonl; fi
}
for attempt in $(seq 1 400); do
    drain_probe
    if jq -se 'any(.[];.event=="error")' /tmp/probe-complete-lines.jsonl > /dev/null; then exit 1; fi
    jq -se 'any(.[];.event=="ready")' /tmp/probe-complete-lines.jsonl > /dev/null && break
    kill -0 "$PROBE_PID"
    sleep 0.05
done
jq -se 'length>0 and ([.[]|select(.event=="ready")]|length)==1 and ([.[]|select(.event=="attached")]|length)==2' /tmp/probe-complete-lines.jsonl > /dev/null
[ "$(readlink "/proc/$PROBE_PID/exe")" = /usr/bin/kernel-socketpair-probe ]
[ "$(awk '/^Uid:/ {print $2 ":" $3 ":" $4 ":" $5}' "/proc/$PROBE_PID/status")" = 0:0:0:0 ]
for fd in "/proc/$PROBE_PID/fd/"*; do printf '%s %s\n' "$(basename "$fd")" "$(readlink "$fd")"; done > /tmp/probe-fds.txt
jq -Mcn --argjson pid "$PROBE_PID" --argjson start "$PROBE_START" --arg sha "$(sha256sum "/proc/$PROBE_PID/exe" | cut -d' ' -f1)" --rawfile fds /tmp/probe-fds.txt --arg fifo "$(stat -c '%u:%a:%F' /run/pair-test/control)" \
    '{event:"probe_identity",pid:$pid,starttime:$start,executed_sha256:$sha,uids:[0,0,0,0],fds:$fds,control_fifo:$fifo}' > /tmp/identity.json
printf 'RUSTUSB_PAIR_IDENTITY '; cat /tmp/identity.json
HID_LOCAL=$(jq -sr '.[]|select(.event=="attached" and .original.bus_id=="1-1")|.local.bus_id' /tmp/probe-complete-lines.jsonl)
FTDI_LOCAL=$(jq -sr '.[]|select(.event=="attached" and .original.bus_id=="1-2")|.local.bus_id' /tmp/probe-complete-lines.jsonl)
[ -n "$HID_LOCAL" ] && [ -n "$FTDI_LOCAL" ] && [ "$HID_LOCAL" != "$FTDI_LOCAL" ]
snapshot attached
unpriv --json sessions > /tmp/sessions.json
jq -e 'length==2 and all(.[];.managed==false and .session.status==6)' /tmp/sessions.json > /dev/null
printf 'RUSTUSB_PAIR_SESSIONS '; jq -Mc . /tmp/sessions.json
EVENT=$(find "$(readlink -f "/sys/bus/usb/devices/$HID_LOCAL")" -name 'event*' -type d | head -1)
TTY=$(find "$(readlink -f "/sys/bus/usb/devices/$FTDI_LOCAL")" -name 'ttyUSB*' -type d | head -1)
[ -n "$EVENT" ] && [ -n "$TTY" ]
: > /tmp/input.bin
exec 3< "/dev/input/$(basename "$EVENT")"
cat <&3 > /tmp/input.bin & INPUT_PID=$!
exec 3<&-
TTY=/dev/$(basename "$TTY")
stty -F "$TTY" raw -echo 115200 clocal -hupcl
exec 4<> "$TTY"
timeout 8 dd bs=1 count=35 <&4 > /tmp/rx.bin 2>/tmp/dd.log & RX_PID=$!
STAGE=real-io
echo 'RUSTUSB_PAIR_WAIT_INPUT key=1'
echo 'RUSTUSB_PAIR_WAIT_SERIAL bytes=35'
if wait "$RX_PID"; then rx_code=0; else rx_code=$?; fi
[ "$rx_code" = 0 ]
printf 'RUSTUSB-RX-1-0001:0123456789abcdef\n' > /tmp/expected.bin
[ "$(wc -c < /tmp/rx.bin)" = 35 ] && cmp /tmp/rx.bin /tmp/expected.bin
printf 'RUSTUSB-TX-1-0001:fedcba9876543210\n' > /tmp/tx.bin
cat /tmp/tx.bin >&4
KEY_SEEN=false
for attempt in $(seq 1 100); do
    bytes=$(wc -c < /tmp/input.bin)
    if [ "$bytes" -gt 0 ] && [ "$((bytes % 24))" = 0 ] && od -An -tu4 -w24 -v /tmp/input.bin | awk 'NF==6 && $5==131073 && $6==1 {down=1} NF==6 && $5==131073 && $6==0 {up=1} END {exit !(down&&up)}'; then KEY_SEEN=true; break; fi
    sleep 0.05
done
[ "$KEY_SEEN" = true ]
input_hex=$(od -An -tx1 -v /tmp/input.bin | tr -d ' \n')
rx_hex=$(od -An -tx1 -v /tmp/rx.bin | tr -d ' \n')
tx_hex=$(od -An -tx1 -v /tmp/tx.bin | tr -d ' \n')
jq -Mcn --arg input "$input_hex" --arg rx "$rx_hex" --arg tx "$tx_hex" --arg hid "$HID_LOCAL" --arg ftdi "$FTDI_LOCAL" \
    '{event:"actual_io",hid_local:$hid,ftdi_local:$ftdi,input_event_hex:$input,rx_hex:$rx,tx_hex:$tx,key_code:2,serial_bytes_each_direction:35}' > /tmp/io.json
printf 'RUSTUSB_PAIR_IO '; cat /tmp/io.json
snapshot before-stop
STAGE=explicit-shutdown
[ "$(awk '{print $22}' "/proc/$PROBE_PID/stat")" = "$PROBE_START" ]
printf 'stop\n' > /run/pair-test/control
if wait "$PROBE_PID"; then probe_code=0; else probe_code=$?; fi
jq -Mcn --argjson pid "$PROBE_PID" --argjson start "$PROBE_START" --argjson code "$probe_code" '{event:"probe_wait",pid:$pid,starttime:$start,wait_code:$code,command:"stop"}' >> /tmp/waits.jsonl
printf 'RUSTUSB_PAIR_WAIT '; tail -n 1 /tmp/waits.jsonl
drain_probe
[ "$probe_code" = 0 ]
jq -se '([.[]|select(.event=="disconnected" and .fds_still_open==true)]|length)==1 and ([.[]|select(.event=="complete" and .result=="PASS")]|length)==1 and all(.[];.event!="error")' /tmp/probe-complete-lines.jsonl > /dev/null
[ ! -e "/sys/bus/usb/devices/$HID_LOCAL" ] && [ ! -e "/sys/bus/usb/devices/$FTDI_LOCAL" ]
exec 4>&-
if wait "$INPUT_PID"; then input_code=0; else input_code=$?; fi
snapshot disconnected
unpriv --json sessions > /tmp/sessions.json
jq -e 'length==0' /tmp/sessions.json > /dev/null
STAGE=helper-unexport
unpriv --json unexport "$HID_ID" > /tmp/unexport-hid.json
unpriv --json unexport "$FTDI_ID" > /tmp/unexport-ftdi.json
for attempt in $(seq 1 100); do
    hid_driver=$(readlink /sys/bus/usb/devices/1-1:1.0/driver 2>/dev/null | sed 's#.*/##' || true)
    ftdi_driver=$(readlink /sys/bus/usb/devices/1-2:1.0/driver 2>/dev/null | sed 's#.*/##' || true)
    if [ "$hid_driver" = usbhid ] && [ "$ftdi_driver" = ftdi_sio ]; then break; fi
    sleep 0.05
done
snapshot restored
cmp /tmp/physical.jsonl /tmp/original-physical.jsonl
echo 'RUSTUSB_PAIR_UNEXPORT: PASS uid=1000 devices=2 original_drivers=true'
STAGE=complete
echo 'RUSTUSB_PAIR_RESULT: PASS single_guest=true tls=false remote_security=false'
