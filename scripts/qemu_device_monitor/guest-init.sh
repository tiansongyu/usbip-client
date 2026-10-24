#!/bin/sh
# Only PID1 of the owned disposable VM may run these kernel/device operations.
set -eu
[ "$$" = 1 ] && [ "${RUSTUSB_QEMU_GUEST:-}" = 1 ] || exit 2
export PATH=/usr/bin:/bin:/sbin
mount -t proc proc /proc
case " $(cat /proc/cmdline) " in *' rustusb.monitor=local '*) ;; *) exit 2 ;; esac
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs tmpfs /tmp
mount -t tmpfs -o mode=0755 tmpfs /run
/bin/busybox stty -F /dev/ttyS1 raw -echo 115200 clocal -hupcl
exec >/dev/ttyS1 2>&1
WATCH_PID=
KERNEL_PID=
STAGE=boot
collect() {
    for file in /tmp/watch.jsonl /tmp/watch.stderr /tmp/kernel-events.log /tmp/kernel-events.stderr /tmp/actor-*.txt /tmp/point-*.json /tmp/stat-*.txt /tmp/*-wait.txt /tmp/independent-final.txt; do
        [ -f "$file" ] || continue
        name=$(basename "$file")
        echo "RUSTUSB_MONITOR_FILE_BEGIN name=$name bytes=$(wc -c < "$file") sha256=$(sha256sum "$file" | awk '{print $1}')"
        base64 "$file"
        echo "RUSTUSB_MONITOR_FILE_END name=$name"
    done
}
cleanup() {
    status=$?
    trap - EXIT
    if [ -n "$WATCH_PID" ] && [ -e "/proc/$WATCH_PID" ]; then
        kill -TERM "$WATCH_PID" 2>/dev/null || true
        set +e; wait "$WATCH_PID"; result=$?; set -e
        echo "$result" >/tmp/watch-cleanup-wait.txt
    fi
    if [ -n "$KERNEL_PID" ] && [ -e "/proc/$KERNEL_PID" ]; then
        kill -INT "$KERNEL_PID" 2>/dev/null || true
        set +e; wait "$KERNEL_PID"; result=$?; set -e
        echo "$result" >/tmp/kernel-wait.txt
    fi
    collect
    if [ "$status" = 0 ]; then echo 'RUSTUSB_MONITOR_RESULT: PASS'; else echo "RUSTUSB_MONITOR_RESULT: FAIL stage=$STAGE exit=$status"; fi
    sync
    poweroff -f
}
trap cleanup EXIT
echo 'RUSTUSB_MONITOR_CHANNEL: ttyS1 kernel=ttyS0'
echo "RUSTUSB_MONITOR_BOOT kernel=$(uname -r)"
for name in usb-common usbcore evdev hid hid-generic usbhid xhci-hcd xhci-pci-renesas xhci-pci; do
    [ ! -f "/modules/$name.ko" ] || insmod "/modules/$name.ko"
done
# No helper, USB/IP driver, network interface or USB device node is opened by the watcher.
[ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
/usr/bin/udevadm monitor --kernel --subsystem-match=usb --property >/tmp/kernel-events.log 2>/tmp/kernel-events.stderr &
KERNEL_PID=$!
/usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups env RUST_LOG=error /usr/bin/rustusb devices --watch --json >/tmp/watch.jsonl 2>/tmp/watch.stderr &
WATCH_PID=$!
wait_point() {
    name=$1
    minimum=$2
    predicate=$3
    for attempt in $(seq 1 200); do
        [ -e "/proc/$WATCH_PID" ] || return 1
        if jq -s -f /etc/monitor-point.jq /tmp/watch.jsonl >/tmp/candidate-point.json 2>/dev/null &&
           jq -e --argjson minimum "$minimum" ".begin.state == \"current\" and .end.revision > \$minimum and ($predicate)" /tmp/candidate-point.json >/dev/null; then
            cp /tmp/candidate-point.json "/tmp/point-$name.json"
            return 0
        fi
        sleep 0.05
    done
    return 1
}
actor() {
    name=$1
    {
        echo "PID=$WATCH_PID"
        cat "/proc/$WATCH_PID/status"
        cat "/proc/$WATCH_PID/stat"
        echo "EXE=$(readlink "/proc/$WATCH_PID/exe")"
        sha256sum "/proc/$WATCH_PID/exe"
        tr '\000' ' ' < "/proc/$WATCH_PID/cmdline"; echo
        for file in /proc/"$WATCH_PID"/fd/*; do
            echo "FD=$(basename "$file") TARGET=$(readlink "$file")"
            cat "/proc/$WATCH_PID/fdinfo/$(basename "$file")"
        done
        echo NETLINK_TABLE
        cat /proc/net/netlink
    } >"/tmp/actor-$name.txt"
    [ "$(awk '/^Uid:/{print $2 ":" $3 ":" $4 ":" $5}' "/proc/$WATCH_PID/status")" = 2000:2000:2000:2000 ]
    [ "$(awk '/^CapEff:/{print $2}' "/proc/$WATCH_PID/status")" = 0000000000000000 ]
}
stamp() {
    name=$1
    point=$2
    bus=$(jq -r '.devices[0].device.bus_id' "$point")
    {
        echo "BUS_ID=$bus"
        stat -Lc 'STAT_DEV=%d STAT_INODE=%i' "/sys/bus/usb/devices/$bus"
        echo "BUSNUM=$(cat "/sys/bus/usb/devices/$bus/busnum")"
        echo "DEVNUM=$(cat "/sys/bus/usb/devices/$bus/devnum")"
    } >"/tmp/stat-$name.txt"
}
STAGE=initial
wait_point empty -1 '.begin.device_count == 0'
actor initial
REV=$(jq -r '.end.revision' /tmp/point-empty.json)
echo 'RUSTUSB_MONITOR_REQUEST: add'
STAGE=add
wait_point added "$REV" '.begin.device_count == 1 and (.devices[0].device.interfaces | any(.class == 3 and .driver == "usbhid"))'
stamp first /tmp/point-added.json
BUS=$(jq -r '.devices[0].device.bus_id' /tmp/point-added.json)
IFACE="$BUS:1.0"
[ "$(cat "/sys/bus/usb/devices/$IFACE/bInterfaceClass")" = 03 ]
REV=$(jq -r '.end.revision' /tmp/point-added.json)
STAGE=unbind
printf '%s' "$IFACE" >/sys/bus/usb/drivers/usbhid/unbind
wait_point unbound "$REV" '.begin.device_count == 1 and (.devices[0].device.interfaces | any(.class == 3 and .driver == null))'
REV=$(jq -r '.end.revision' /tmp/point-unbound.json)
STAGE=rebind
printf '%s' "$IFACE" >/sys/bus/usb/drivers/usbhid/bind
wait_point rebound "$REV" '.begin.device_count == 1 and (.devices[0].device.interfaces | any(.class == 3 and .driver == "usbhid"))'
REV=$(jq -r '.end.revision' /tmp/point-rebound.json)
echo 'RUSTUSB_MONITOR_REQUEST: remove'
STAGE=remove
wait_point removed "$REV" '.begin.device_count == 0'
[ ! -e "/sys/bus/usb/devices/$BUS" ]
REV=$(jq -r '.end.revision' /tmp/point-removed.json)
echo 'RUSTUSB_MONITOR_REQUEST: readd'
STAGE=readd
wait_point readded "$REV" '.begin.device_count == 1 and (.devices[0].device.interfaces | any(.class == 3 and .driver == "usbhid"))'
stamp second /tmp/point-readded.json
actor before-stop
STAGE=stop
kill -TERM "$WATCH_PID"
set +e; wait "$WATCH_PID"; result=$?; set -e
echo "$result" >/tmp/watch-wait.txt
[ "$result" = 0 ] && [ ! -e "/proc/$WATCH_PID" ]
WATCH_PID=
echo 'RUSTUSB_MONITOR_REQUEST: final-remove'
STAGE=final-baseline
for attempt in $(seq 1 200); do [ ! -e "/sys/bus/usb/devices/$BUS" ] && break; sleep 0.05; done
[ ! -e "/sys/bus/usb/devices/$BUS" ]
find /sys/bus/usb/devices -maxdepth 1 -print >/tmp/independent-final.txt
[ ! -d /sys/module/usbip_host ] && [ ! -d /sys/module/vhci_hcd ]
STAGE=complete
