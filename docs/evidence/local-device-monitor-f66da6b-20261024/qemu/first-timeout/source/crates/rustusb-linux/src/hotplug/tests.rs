use super::*;

#[test]
fn only_actual_kernel_source_and_complete_bounded_messages_trigger_scans() {
    let device = b"add@/devices/test/usb1/1-2\0ACTION=add\0DEVPATH=/devices/test/usb1/1-2\0SUBSYSTEM=usb\0DEVTYPE=usb_device\0SEQNUM=42\0";
    assert_eq!(classify(device, device.len(), true), Notice::Usb);
    assert_eq!(classify(device, device.len(), false), Notice::Ignored);
    assert_eq!(classify(device, device.len() + 1, false), Notice::Ignored);
    assert_eq!(
        classify(device, device.len() + 1, true),
        Notice::Resync(NotificationLoss::Truncated)
    );
    assert_eq!(
        classify(&device[..device.len() - 1], device.len() - 1, true),
        Notice::Resync(NotificationLoss::Malformed)
    );
    assert_eq!(
        classify(b"", 0, true),
        Notice::Resync(NotificationLoss::Malformed)
    );
    let duplicate = b"add@/x\0ACTION=add\0DEVPATH=/x\0SUBSYSTEM=usb\0SUBSYSTEM=usb\0";
    assert_eq!(
        classify(duplicate, duplicate.len(), true),
        Notice::Resync(NotificationLoss::Malformed)
    );
    let mismatched = b"add@/x\0ACTION=remove\0DEVPATH=/x\0SUBSYSTEM=usb\0";
    assert_eq!(
        classify(mismatched, mismatched.len(), true),
        Notice::Resync(NotificationLoss::Malformed)
    );
    let no_action = b"add@/x\0DEVPATH=/x\0SUBSYSTEM=usb\0";
    assert_eq!(
        classify(no_action, no_action.len(), true),
        Notice::Resync(NotificationLoss::Malformed)
    );
}

#[test]
fn usb_interfaces_and_all_actions_trigger_without_udev_or_global_sequence_assumptions() {
    for action in [
        "add", "remove", "change", "bind", "unbind", "move", "online", "offline",
    ] {
        let message = format!(
            "{action}@/devices/usb1/1-2/1-2:1.0\0ACTION={action}\0DEVPATH=/devices/usb1/1-2/1-2:1.0\0SUBSYSTEM=usb\0DEVTYPE=usb_interface\0SEQNUM=999999999\0"
        );
        assert_eq!(
            classify(message.as_bytes(), message.len(), true),
            Notice::Usb
        );
    }
    let other = b"change@/x\0ACTION=change\0DEVPATH=/x\0SUBSYSTEM=block\0";
    assert_eq!(classify(other, other.len(), true), Notice::Ignored);
    let large = format!(
        "add@/x\0ACTION=add\0DEVPATH=/x\0SUBSYSTEM=usb\0X={}\0",
        "x".repeat(4096)
    );
    assert_eq!(
        classify(large.as_bytes(), large.len(), true),
        Notice::Resync(NotificationLoss::Malformed)
    );
    let many = format!(
        "add@/x\0ACTION=add\0DEVPATH=/x\0SUBSYSTEM=usb\0{}",
        "X=x\0".repeat(129)
    );
    assert_eq!(
        classify(many.as_bytes(), many.len(), true),
        Notice::Resync(NotificationLoss::Malformed)
    );
}

#[test]
fn real_ordinary_user_source_is_nonblocking_cloexec_and_closes() -> io::Result<()> {
    use std::os::fd::AsRawFd;
    let source = KernelUsbEvents::open()?;
    let fd = source.as_fd().as_raw_fd();
    let actual = std::fs::read_link(format!("/proc/self/fd/{fd}"))?;
    assert!(source.receive_buffer_bytes() <= 256 * 1024);
    assert!(rustix::io::fcntl_getfd(&source)?.contains(rustix::io::FdFlags::CLOEXEC));
    assert!(rustix::fs::fcntl_getfl(&source)?.contains(rustix::fs::OFlags::NONBLOCK));
    // Other kernel activity is allowed, but receiving a finite batch never blocks.
    for _ in 0..64 {
        if source.receive()?.is_none() {
            break;
        }
    }
    drop(source);
    assert_ne!(
        std::fs::read_link(format!("/proc/self/fd/{fd}")).ok(),
        Some(actual)
    );
    Ok(())
}
