use std::{
    collections::{BTreeMap, VecDeque},
    ffi::c_void,
    fs::File,
    io::{Read, Seek},
    mem::ManuallyDrop,
    sync::{
        atomic::{AtomicU8, Ordering},
        Arc, Mutex, MutexGuard, Weak,
    },
    task::{Context, Poll},
    time::{Duration, Instant},
};

use log::{debug, error, warn};
use rustix::{
    event::epoll::EventFlags,
    fd::{AsFd, AsRawFd, FromRawFd, OwnedFd},
    fs::Timespec,
    io::Errno,
    time::{timerfd_create, timerfd_settime, Itimerspec, TimerfdFlags, TimerfdTimerFlags},
};
use slab::Slab;

use super::{
    errno_to_transfer_error, events,
    usbfs::{self, Urb},
    TransferData,
};

#[cfg(not(target_os = "android"))]
use super::{
    enumeration::{SysfsError, SysfsErrorKind},
    SysfsPath,
};

use crate::{
    bitset::EndpointBitSet,
    descriptors::{
        parse_concatenated_config_descriptors, ConfigurationDescriptor, DeviceDescriptor,
        EndpointDescriptor, TransferType, DESCRIPTOR_LEN_DEVICE,
    },
    maybe_future::{blocking::Blocking, MaybeFuture},
    transfer::{
        internal::{
            notify_completion, take_completed_from_queue, Idle, Notify, Pending, TransferFuture,
        },
        request_type, Buffer, Completion, ControlIn, ControlOut, ControlType, Direction, Recipient,
        TransferError,
    },
    DeviceInfo, Error, ErrorKind, Speed,
};

#[derive(PartialEq, Eq, PartialOrd, Ord)]
struct TimeoutEntry {
    deadline: Instant,
    urb: *mut Urb,
}

unsafe impl Send for TimeoutEntry {}
unsafe impl Sync for TimeoutEntry {}

static DEVICES: Mutex<Slab<Weak<LinuxDevice>>> = Mutex::new(Slab::new());

pub(crate) struct LinuxDevice {
    fd: OwnedFd,
    events_id: usize,

    /// Read from the fd, consists of device descriptor followed by configuration descriptors
    descriptors: Vec<u8>,

    #[cfg(not(target_os = "android"))]
    sysfs: Option<SysfsPath>,

    active_config: AtomicU8,

    timerfd: OwnedFd,
    timeouts: Mutex<BTreeMap<TimeoutEntry, ()>>,
}

impl LinuxDevice {
    #[cfg(not(target_os = "android"))]
    pub(crate) fn from_device_info(
        d: &DeviceInfo,
    ) -> impl MaybeFuture<Output = Result<Arc<LinuxDevice>, Error>> {
        use rustix::fs::{Mode, OFlags};

        let busnum = d.busnum();
        let devnum = d.device_address();
        let sysfs_path = d.path.clone();

        Blocking::new(move || {
            let path = std::path::PathBuf::from(format!("/dev/bus/usb/{busnum:03}/{devnum:03}"));
            let fd = rustix::fs::open(&path, OFlags::RDWR | OFlags::CLOEXEC, Mode::empty())
                .map_err(|e| {
                    match e {
                        Errno::NOENT => {
                            Error::new_os(ErrorKind::Disconnected, "device not found", e)
                        }
                        Errno::PERM | Errno::ACCESS => {
                            Error::new_os(ErrorKind::PermissionDenied, "permission denied", e)
                        }
                        e => Error::new_os(ErrorKind::Other, "failed to open device", e),
                    }
                    .log_debug()
                })?;

            Self::create_inner(fd, Some(sysfs_path))
        })
    }

    #[cfg(target_os = "android")]
    pub(crate) fn from_device_info(
        _d: &DeviceInfo,
    ) -> impl MaybeFuture<Output = Result<Arc<LinuxDevice>, Error>> {
        Blocking::new(move || unimplemented!())
    }

    pub(crate) fn from_fd(
        fd: OwnedFd,
    ) -> impl MaybeFuture<Output = Result<Arc<LinuxDevice>, Error>> {
        Blocking::new(move || {
            debug!("Wrapping fd {} as usbfs device", fd.as_raw_fd());
            Self::create_inner(
                fd,
                #[cfg(not(target_os = "android"))]
                None,
            )
        })
    }

    pub(crate) fn create_inner(
        fd: OwnedFd,
        #[cfg(not(target_os = "android"))] sysfs: Option<SysfsPath>,
    ) -> Result<Arc<LinuxDevice>, Error> {
        let descriptors = read_all_from_fd(&fd).map_err(|e| {
            Error::new_io(ErrorKind::Other, "failed to read descriptors", e).log_error()
        })?;

        let Some(_) = DeviceDescriptor::new(&descriptors) else {
            return Err(Error::new(ErrorKind::Other, "invalid device descriptor"));
        };

        #[cfg(not(target_os = "android"))]
        let active_config: u8 = if let Some(sysfs) = sysfs.as_ref() {
            match sysfs.read_attr("bConfigurationValue") {
                Ok(v) => v,
                // Linux returns an empty string when the device is unconfigured.
                // We'll assume all parse errors are the empty string.
                Err(SysfsError(_, SysfsErrorKind::Parse(_))) => 0,

                Err(e) => {
                    warn!("failed to read sysfs bConfigurationValue: {e}");
                    return Err(Error::new(
                        ErrorKind::Other,
                        "failed to read sysfs bConfigurationValue",
                    ));
                }
            }
        } else {
            guess_active_configuration(&fd, &descriptors)
        };

        #[cfg(target_os = "android")]
        let active_config = guess_active_configuration(&fd, &descriptors);

        let timerfd = timerfd_create(
            rustix::time::TimerfdClockId::Monotonic,
            TimerfdFlags::CLOEXEC | TimerfdFlags::NONBLOCK,
        )
        .map_err(|e| Error::new_os(ErrorKind::Other, "failed to create timerfd", e).log_error())?;

        let arc = Arc::new_cyclic(|weak| {
            let events_id = DEVICES.lock().unwrap().insert(weak.clone());
            LinuxDevice {
                fd,
                events_id,
                descriptors,
                #[cfg(not(target_os = "android"))]
                sysfs,
                active_config: AtomicU8::new(active_config),
                timerfd,
                timeouts: Mutex::new(BTreeMap::new()),
            }
        });

        debug!(
            "Opened device fd={} with id {}",
            arc.fd.as_raw_fd(),
            arc.events_id
        );

        events::register_fd(
            arc.fd.as_fd(),
            events::Tag::Device(arc.events_id),
            EventFlags::OUT,
        )?;

        events::register_fd(
            arc.timerfd.as_fd(),
            events::Tag::DeviceTimer(arc.events_id),
            EventFlags::IN,
        )?;

        Ok(arc)
    }

    pub(crate) fn handle_usb_epoll(id: usize) {
        let device = DEVICES.lock().unwrap().get(id).and_then(|w| w.upgrade());
        if let Some(device) = device {
            device.handle_events();
        }
    }

    fn handle_events(&self) {
        debug!("Handling events for device {}", self.events_id);
        match usbfs::reap_urb_ndelay(&self.fd) {
            Ok(urb) => {
                let transfer_data: *mut TransferData = unsafe { &(*urb) }.usercontext.cast();

                {
                    let transfer = unsafe { &*transfer_data };
                    debug_assert!(transfer.urb_ptr() == urb);
                    debug!(
                        "URB {:?} for ep {:x} completed, status={} actual_length={}",
                        transfer.urb_ptr(),
                        transfer.urb().endpoint,
                        transfer.urb().status,
                        transfer.urb().actual_length
                    );

                    if let Some(deadline) = transfer.deadline {
                        let mut timeouts = self.timeouts.lock().unwrap();
                        timeouts.remove(&TimeoutEntry { deadline, urb });
                        self.update_timeouts(timeouts, Instant::now());
                    }
                };

                // SAFETY: pointer came from submit via kernel and we're now done with it
                unsafe { notify_completion::<super::TransferData>(transfer_data) }
            }
            Err(Errno::AGAIN) => {}
            Err(Errno::NODEV) => {
                debug!("Device {} disconnected", self.events_id);

                // epoll returns events continuously on a disconnected device, and REAPURB
                // only returns ENODEV after all events are received, so unregister to
                // keep the event thread from spinning because we won't receive further events.
                // The drop impl will try to unregister again, but that's ok.
                events::unregister_fd(self.fd.as_fd());
            }
            Err(e) => {
                error!("Unexpected error {e} from REAPURBNDELAY");
            }
        }
    }

    pub(crate) fn handle_timer_epoll(id: usize) {
        let device = DEVICES.lock().unwrap().get(id).and_then(|w| w.upgrade());
        if let Some(device) = device {
            device.handle_timeouts();
        }
    }

    fn handle_timeouts(&self) {
        debug!("Handling timeouts for device {}", self.events_id);
        let now = Instant::now();

        rustix::io::read(self.timerfd.as_fd(), &mut [0u8; 8]).ok();

        let mut timeouts = self.timeouts.lock().unwrap();
        while let Some(entry) = timeouts.first_entry() {
            if entry.key().deadline > now {
                break;
            }

            let urb = entry.remove_entry().0.urb;

            unsafe {
                match usbfs::discard_urb(&self.fd, urb) {
                    Ok(()) => debug!("Cancelled URB {urb:?} after timeout"),
                    Err(e) => debug!("Failed to cancel timed out URB {urb:?}: {e}"),
                }
            }
        }

        self.update_timeouts(timeouts, now);
    }

    fn update_timeouts(&self, timeouts: MutexGuard<BTreeMap<TimeoutEntry, ()>>, now: Instant) {
        const TIMESPEC_ZERO: Timespec = Timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };

        let next = if let Some((TimeoutEntry { deadline, .. }, _)) = timeouts.first_key_value() {
            let duration = deadline
                .checked_duration_since(now)
                .unwrap_or(Duration::from_nanos(1));
            log::debug!("Next timeout in {duration:?}");
            Timespec {
                tv_sec: duration.as_secs() as i64,
                tv_nsec: duration.subsec_nanos() as i64,
            }
        } else {
            TIMESPEC_ZERO
        };

        timerfd_settime(
            self.timerfd.as_fd(),
            TimerfdTimerFlags::empty(),
            &Itimerspec {
                it_interval: TIMESPEC_ZERO,
                it_value: next,
            },
        )
        .inspect_err(|e| {
            log::error!("Failed to set timerfd: {e}");
        })
        .ok();
    }

    pub(crate) fn device_descriptor(&self) -> DeviceDescriptor {
        DeviceDescriptor::new(&self.descriptors).unwrap()
    }

    pub(crate) fn configuration_descriptors(
        &self,
    ) -> impl Iterator<Item = ConfigurationDescriptor<'_>> {
        parse_concatenated_config_descriptors(&self.descriptors[DESCRIPTOR_LEN_DEVICE as usize..])
    }

    pub(crate) fn active_configuration_value(&self) -> u8 {
        #[cfg(not(target_os = "android"))]
        if let Some(sysfs) = self.sysfs.as_ref() {
            match sysfs.read_attr("bConfigurationValue") {
                Ok(v) => {
                    self.active_config.store(v, Ordering::SeqCst);
                    return v;
                }
                Err(SysfsError(_, SysfsErrorKind::Parse(_))) => {
                    self.active_config.store(0, Ordering::SeqCst);
                    return 0;
                }
                Err(e) => {
                    error!("Failed to read sysfs bConfigurationValue: {e}, using cached value");
                }
            }
        }
        self.active_config.load(Ordering::SeqCst)
    }

    pub(crate) fn set_configuration(
        self: Arc<Self>,
        configuration: u8,
    ) -> impl MaybeFuture<Output = Result<(), Error>> {
        Blocking::new(move || {
            usbfs::set_configuration(&self.fd, configuration).map_err(|e| match e {
                Errno::INVAL => Error::new_os(ErrorKind::NotFound, "configuration not found", e),
                Errno::BUSY => Error::new_os(ErrorKind::Busy, "device is busy", e),
                Errno::NODEV => Error::new_os(ErrorKind::Disconnected, "device disconnected", e),
                _ => Error::new_os(ErrorKind::Other, "failed to set configuration", e),
            })?;
            self.active_config.store(configuration, Ordering::SeqCst);
            Ok(())
        })
    }

    pub(crate) fn reset(self: Arc<Self>) -> impl MaybeFuture<Output = Result<(), Error>> {
        Blocking::new(move || {
            usbfs::reset(&self.fd).map_err(|e| match e {
                Errno::BUSY => Error::new_os(ErrorKind::Busy, "device is busy", e),
                Errno::NODEV => Error::new_os(ErrorKind::Disconnected, "device disconnected", e),
                _ => Error::new_os(ErrorKind::Other, "failed to reset device", e),
            })
        })
    }

    pub fn control_in(
        self: Arc<Self>,
        data: ControlIn,
        timeout: Duration,
    ) -> impl MaybeFuture<Output = Result<Vec<u8>, TransferError>> {
        let t = TransferData::new_control_in(data);
        TransferFuture::new(self, t, |device, t| device.submit_timeout(t, timeout)).map(|t| {
            t.status()?;
            Ok(t.control_in_data().to_owned())
        })
    }

    pub fn control_out(
        self: Arc<Self>,
        data: ControlOut,
        timeout: Duration,
    ) -> impl MaybeFuture<Output = Result<(), TransferError>> {
        let t = TransferData::new_control_out(data);
        TransferFuture::new(self, t, |device, t| device.submit_timeout(t, timeout)).map(|t| {
            t.status()?;
            Ok(())
        })
    }

    fn handle_claim_interface_result(
        self: Arc<Self>,
        interface_number: u8,
        result: Result<(), Errno>,
        reattach: bool,
    ) -> Result<Arc<LinuxInterface>, Error> {
        result.map_err(|e| {
            match e {
                Errno::INVAL | Errno::NOENT => {
                    Error::new_os(ErrorKind::NotFound, "interface not found", e)
                }
                Errno::BUSY => Error::new_os(ErrorKind::Busy, "interface is busy", e),
                Errno::NODEV => Error::new_os(ErrorKind::Disconnected, "device disconnected", e),
                _ => Error::new_os(ErrorKind::Other, "failed to claim interface", e),
            }
            .log_error()
        })?;
        debug!(
            "Claimed interface {interface_number} on device id {dev}",
            dev = self.events_id
        );
        Ok(Arc::new(LinuxInterface {
            device: self,
            interface_number,
            release: InterfaceRelease::new(reattach),
            state: Mutex::new(Default::default()),
        }))
    }

    pub(crate) fn claim_interface(
        self: Arc<Self>,
        interface_number: u8,
    ) -> impl MaybeFuture<Output = Result<Arc<LinuxInterface>, Error>> {
        Blocking::new(move || {
            let result = usbfs::claim_interface(&self.fd, interface_number);
            self.handle_claim_interface_result(interface_number, result, false)
        })
    }

    pub(crate) fn detach_and_claim_interface(
        self: Arc<Self>,
        interface_number: u8,
    ) -> impl MaybeFuture<Output = Result<Arc<LinuxInterface>, Error>> {
        Blocking::new(move || {
            let result = usbfs::detach_and_claim_interface(&self.fd, interface_number);
            self.handle_claim_interface_result(interface_number, result, true)
        })
    }

    pub(crate) fn detach_and_claim_interface_no_reattach(
        self: Arc<Self>,
        interface_number: u8,
    ) -> impl MaybeFuture<Output = Result<Arc<LinuxInterface>, Error>> {
        Blocking::new(move || {
            let result = usbfs::detach_and_claim_interface(&self.fd, interface_number);
            self.handle_claim_interface_result(interface_number, result, false)
        })
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn detach_kernel_driver(
        self: &Arc<Self>,
        interface_number: u8,
    ) -> Result<(), Error> {
        usbfs::detach_kernel_driver(&self.fd, interface_number).map_err(|e| match e {
            Errno::INVAL => Error::new_os(ErrorKind::NotFound, "interface not found", e),
            Errno::NODEV => Error::new_os(ErrorKind::Disconnected, "device disconnected", e),
            Errno::NODATA => Error::new_os(ErrorKind::Other, "no kernel driver attached", e),
            _ => Error::new_os(ErrorKind::Other, "failed to detach kernel driver", e),
        })
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn attach_kernel_driver(
        self: &Arc<Self>,
        interface_number: u8,
    ) -> Result<(), Error> {
        usbfs::attach_kernel_driver(&self.fd, interface_number).map_err(|e| match e {
            Errno::INVAL => Error::new_os(ErrorKind::NotFound, "interface not found", e),
            Errno::NODEV => Error::new_os(ErrorKind::Disconnected, "device disconnected", e),
            Errno::BUSY => Error::new_os(ErrorKind::Busy, "kernel driver already attached", e),
            _ => Error::new_os(ErrorKind::Other, "failed to attach kernel driver", e),
        })
    }

    pub(crate) fn submit(&self, transfer: Idle<TransferData>) -> Pending<TransferData> {
        let len = transfer.urb().buffer_length;
        let pending = transfer.pre_submit();
        let urb = pending.urb_ptr();

        // SAFETY: We got the urb from `Idle<TransferData>`, which always points to
        // a valid URB with valid buffers, which is not already pending
        unsafe {
            let ep = (*urb).endpoint;
            (*urb).usercontext = pending.as_ptr().cast();
            if let Err(e) = usbfs::submit_urb(&self.fd, urb) {
                // SAFETY: Transfer was not submitted. We still own the transfer
                // and can write to the URB and complete it in place of the handler.
                let u = &mut *urb;
                debug!("Failed to submit URB {urb:?}: {len} bytes on ep {ep:x}: {e} {u:?}");
                u.actual_length = 0;
                u.status = e.raw_os_error();
                notify_completion::<super::TransferData>(pending.as_ptr().cast());
            } else {
                debug!("Submitted URB {urb:?}: {len} bytes on ep {ep:x}");
            }
        };

        pending
    }

    fn submit_timeout(
        &self,
        mut transfer: Idle<TransferData>,
        timeout: Duration,
    ) -> Pending<TransferData> {
        let urb = transfer.urb_ptr();
        let now = Instant::now();
        let deadline = now + timeout;
        transfer.deadline = Some(deadline);

        // Hold the lock across `submit`, so that it can't complete before we
        // insert the timeout entry.
        let mut timeouts = self.timeouts.lock().unwrap();

        let r = self.submit(transfer);

        // This can only be false if submit failed, because we hold the timeouts lock
        // and would block the completion handler.
        if !r.is_complete() {
            timeouts.insert(TimeoutEntry { deadline, urb }, ());
            self.update_timeouts(timeouts, now);
        }

        r
    }

    pub(crate) fn cancel(&self, transfer: &mut Pending<TransferData>) -> bool {
        let urb = transfer.urb_ptr();
        unsafe {
            if let Err(e) = usbfs::discard_urb(&self.fd, urb) {
                debug!("Failed to cancel URB {urb:?}: {e}");
                false
            } else {
                debug!("Requested cancellation of URB {urb:?}");
                true
            }
        }
    }

    pub(crate) fn speed(&self) -> Option<Speed> {
        usbfs::get_speed(&self.fd)
            .inspect_err(|e| log::error!("USBDEVFS_GET_SPEED failed: {e}"))
            .ok()
            .and_then(|raw_speed| match raw_speed {
                1 => Some(Speed::Low),
                2 => Some(Speed::Full),
                3 => Some(Speed::High),
                // 4 is wireless USB, but we don't support it
                5 => Some(Speed::Super),
                6 => Some(Speed::SuperPlus),
                _ => None,
            })
    }
}

fn read_all_from_fd(fd: &OwnedFd) -> Result<Vec<u8>, std::io::Error> {
    let mut file = unsafe { ManuallyDrop::new(File::from_raw_fd(fd.as_raw_fd())) };
    file.seek(std::io::SeekFrom::Start(0))?;
    let mut buf = Vec::new();
    file.read_to_end(&mut buf)?;
    Ok(buf)
}

/// Try a request to get the active configuration or fall back to a guess.
fn guess_active_configuration(fd: &OwnedFd, descriptors: &[u8]) -> u8 {
    request_configuration(fd).unwrap_or_else(|()| {
        let mut config_descriptors =
            parse_concatenated_config_descriptors(&descriptors[DESCRIPTOR_LEN_DEVICE as usize..]);

        if let Some(config) = config_descriptors.next() {
            config.configuration_value()
        } else {
            error!("no configurations for device fd {}", fd.as_raw_fd());
            0
        }
    })
}

/// Get the active configuration with a blocking request to the device.
fn request_configuration(fd: &OwnedFd) -> Result<u8, ()> {
    const REQUEST_GET_CONFIGURATION: u8 = 0x08;

    let mut dst = [0u8; 1];
    let r = usbfs::control(
        fd,
        usbfs::CtrlTransfer {
            bRequestType: request_type(Direction::In, ControlType::Standard, Recipient::Device),
            bRequest: REQUEST_GET_CONFIGURATION,
            wValue: 0,
            wIndex: 0,
            wLength: dst.len() as u16,
            timeout: 50,
            data: &mut dst[0] as *mut u8 as *mut c_void,
        },
    );

    match r {
        Ok(1) => {
            let active_config = dst[0];
            debug!("Obtained active configuration for fd {} from GET_CONFIGURATION request: {active_config}", fd.as_raw_fd());
            Ok(active_config)
        }
        Ok(n) => {
            warn!("GET_CONFIGURATION request returned unexpected length: {n}, expected 1");
            Err(())
        }
        Err(e) => {
            warn!(
                "GET_CONFIGURATION request failed: {:?}",
                errno_to_transfer_error(e)
            );
            Err(())
        }
    }
}

impl Drop for LinuxDevice {
    fn drop(&mut self) {
        debug!("Closing device {}", self.events_id);
        events::unregister_fd(self.fd.as_fd());
        events::unregister_fd(self.timerfd.as_fd());
        DEVICES.lock().unwrap().remove(self.events_id);
    }
}

pub(crate) struct LinuxInterface {
    pub(crate) interface_number: u8,
    pub(crate) device: Arc<LinuxDevice>,
    release: InterfaceRelease,
    state: Mutex<InterfaceState>,
}

/// One-shot cleanup shared by checked release and best-effort destruction.
/// The callbacks keep lifecycle tests independent of USB descriptors and FDs.
struct InterfaceRelease {
    pending: bool,
    reattach: bool,
}

#[derive(Debug)]
struct InterfaceReleaseError {
    reattach: bool,
    errno: Errno,
}

impl InterfaceReleaseError {
    fn into_error(self) -> Error {
        let kind = match self.errno {
            Errno::NODEV => ErrorKind::Disconnected,
            Errno::BUSY => ErrorKind::Busy,
            Errno::PERM | Errno::ACCESS => ErrorKind::PermissionDenied,
            _ => ErrorKind::Other,
        };
        Error::new_os(
            kind,
            if self.reattach {
                "failed to reattach kernel driver after interface release"
            } else {
                "failed to release interface"
            },
            self.errno,
        )
    }
}

impl InterfaceRelease {
    fn new(reattach: bool) -> Self {
        Self {
            pending: true,
            reattach,
        }
    }

    fn run(
        &mut self,
        release: impl FnOnce() -> Result<(), Errno>,
        reattach: impl FnOnce() -> Result<(), Errno>,
    ) -> Result<(), InterfaceReleaseError> {
        if !std::mem::replace(&mut self.pending, false) {
            return Ok(());
        }
        release().map_err(|errno| InterfaceReleaseError {
            reattach: false,
            errno,
        })?;
        if self.reattach {
            reattach().map_err(|errno| InterfaceReleaseError {
                reattach: true,
                errno,
            })?;
        }
        Ok(())
    }
}

#[cfg(target_os = "linux")]
fn consume_unique_interface<T>(
    interface: Arc<T>,
    release: impl FnOnce(T) -> Result<(), Error>,
) -> Result<(), Error> {
    match Arc::into_inner(interface) {
        Some(interface) => release(interface),
        None => Err(Error::new(ErrorKind::Busy, "interface is still in use")),
    }
}

#[derive(Default)]
struct InterfaceState {
    endpoints: EndpointBitSet,
    alt_setting: u8,
}

impl LinuxInterface {
    pub fn control_in(
        &self,
        data: ControlIn,
        timeout: Duration,
    ) -> impl MaybeFuture<Output = Result<Vec<u8>, TransferError>> {
        self.device.clone().control_in(data, timeout)
    }

    pub fn control_out(
        &self,
        data: ControlOut,
        timeout: Duration,
    ) -> impl MaybeFuture<Output = Result<(), TransferError>> {
        self.device.clone().control_out(data, timeout)
    }

    pub fn get_alt_setting(&self) -> u8 {
        self.state.lock().unwrap().alt_setting
    }

    pub fn set_alt_setting(
        self: Arc<Self>,
        alt_setting: u8,
    ) -> impl MaybeFuture<Output = Result<(), Error>> {
        Blocking::new(move || {
            let mut state = self.state.lock().unwrap();
            if !state.endpoints.is_empty() {
                return Err(Error::new(
                    ErrorKind::Busy,
                    "can't change alternate setting while endpoints are in use",
                ));
            }
            usbfs::set_interface(&self.device.fd, self.interface_number, alt_setting).map_err(
                |e| match e {
                    Errno::INVAL => {
                        Error::new_os(ErrorKind::NotFound, "alternate setting not found", e)
                    }
                    Errno::NODEV => {
                        Error::new_os(ErrorKind::Disconnected, "device disconnected", e)
                    }
                    _ => Error::new_os(ErrorKind::Other, "failed to set alternate setting", e),
                },
            )?;
            debug!(
                "Set interface {} alt setting to {alt_setting}",
                self.interface_number
            );
            state.alt_setting = alt_setting;
            Ok(())
        })
    }

    pub fn endpoint(
        self: &Arc<Self>,
        descriptor: EndpointDescriptor,
    ) -> Result<LinuxEndpoint, Error> {
        let address = descriptor.address();
        let ep_type = descriptor.transfer_type();
        let max_packet_size = descriptor.max_packet_size();

        let mut state = self.state.lock().unwrap();

        if state.endpoints.is_set(address) {
            return Err(Error::new(ErrorKind::Busy, "endpoint already in use"));
        }
        state.endpoints.set(address);

        Ok(LinuxEndpoint {
            inner: Arc::new(EndpointInner {
                address,
                ep_type,
                interface: self.clone(),
                notify: Notify::new(),
            }),
            max_packet_size,
            pending: VecDeque::new(),
            idle_transfer: None,
        })
    }

    pub fn release(self: Arc<Self>) -> impl MaybeFuture<Output = Result<(), Error>> {
        Blocking::new(move || {
            if let Some(this) = Arc::into_inner(self) {
                // Preserve the portable best-effort contract. Call
                // release_checked when the actual Linux result is required.
                drop(this);
                Ok(())
            } else {
                return Err(Error::new(ErrorKind::Busy, "interface is still in use"));
            }
        })
    }

    #[cfg(target_os = "linux")]
    pub fn release_checked(self: Arc<Self>) -> impl MaybeFuture<Output = Result<(), Error>> {
        Blocking::new(move || {
            consume_unique_interface(self, |mut this| {
                this.release_inner()
                    .map_err(InterfaceReleaseError::into_error)
            })
        })
    }

    fn release_inner(&mut self) -> Result<(), InterfaceReleaseError> {
        let device = &self.device;
        let number = self.interface_number;
        self.release.run(
            || {
                let result = usbfs::release_interface(&device.fd, number);
                debug!(
                    "Released interface {number} on device {}: {result:?}",
                    device.events_id
                );
                result
            },
            || {
                let result = usbfs::attach_kernel_driver(&device.fd, number);
                debug!(
                    "Reattached kernel drivers for interface {number} on device {}: {result:?}",
                    device.events_id
                );
                result
            },
        )
    }
}

#[cfg(all(test, target_os = "linux"))]
mod interface_release_tests {
    use super::*;

    struct Fixture {
        release: InterfaceRelease,
        calls: Arc<Mutex<Vec<&'static str>>>,
        release_result: Result<(), Errno>,
        reattach_result: Result<(), Errno>,
    }

    impl Fixture {
        fn new(
            reattach: bool,
            release_result: Result<(), Errno>,
            reattach_result: Result<(), Errno>,
        ) -> (Self, Arc<Mutex<Vec<&'static str>>>) {
            let calls = Arc::new(Mutex::new(Vec::new()));
            (
                Self {
                    release: InterfaceRelease::new(reattach),
                    calls: calls.clone(),
                    release_result,
                    reattach_result,
                },
                calls,
            )
        }

        fn run(&mut self) -> Result<(), Error> {
            let calls = &self.calls;
            self.release
                .run(
                    || {
                        calls.lock().map_err(|_| Errno::IO)?.push("release");
                        self.release_result
                    },
                    || {
                        calls.lock().map_err(|_| Errno::IO)?.push("connect");
                        self.reattach_result
                    },
                )
                .map_err(InterfaceReleaseError::into_error)
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = self.run();
        }
    }

    #[test]
    fn checked_release_errors_survive_without_drop_retry_or_connect() {
        for error in [Errno::INVAL, Errno::NODEV, Errno::IO, Errno::INTR] {
            let (fixture, calls) = Fixture::new(true, Err(error), Ok(()));
            let result = consume_unique_interface(Arc::new(fixture), |mut fixture| fixture.run());
            assert_eq!(
                result.as_ref().err().and_then(Error::os_error),
                Some(error.raw_os_error() as u32)
            );
            assert!(calls.lock().is_ok_and(|calls| *calls == ["release"]));
        }
    }

    #[test]
    fn checked_success_runs_release_and_selected_reattach_exactly_once() {
        for reattach in [false, true] {
            let (fixture, calls) = Fixture::new(reattach, Ok(()), Ok(()));
            let result = consume_unique_interface(Arc::new(fixture), |mut fixture| fixture.run());
            assert!(result.is_ok());
            assert!(calls.lock().is_ok_and(|calls| {
                *calls
                    == if reattach {
                        vec!["release", "connect"]
                    } else {
                        vec!["release"]
                    }
            }));
        }
    }

    #[test]
    fn checked_connect_error_keeps_errno_and_does_not_repeat_either_call() {
        let (fixture, calls) = Fixture::new(true, Ok(()), Err(Errno::BUSY));
        let result = consume_unique_interface(Arc::new(fixture), |mut fixture| fixture.run());
        assert_eq!(
            result.as_ref().err().map(Error::kind),
            Some(ErrorKind::Busy)
        );
        assert_eq!(
            result.as_ref().err().and_then(Error::os_error),
            Some(Errno::BUSY.raw_os_error() as u32)
        );
        assert!(result.as_ref().err().is_some_and(|error| error
            .to_string()
            .contains("reattach kernel driver after interface release")));
        assert!(calls
            .lock()
            .is_ok_and(|calls| *calls == ["release", "connect"]));
    }

    #[test]
    fn shared_arc_is_busy_without_release_until_the_last_owner_drops() {
        let (fixture, calls) = Fixture::new(false, Ok(()), Ok(()));
        let interface = Arc::new(fixture);
        let endpoint_reference = interface.clone();
        let result = consume_unique_interface(interface, |mut fixture| fixture.run());
        assert_eq!(
            result.as_ref().err().map(Error::kind),
            Some(ErrorKind::Busy)
        );
        assert!(calls.lock().is_ok_and(|calls| calls.is_empty()));
        drop(endpoint_reference);
        assert!(calls.lock().is_ok_and(|calls| *calls == ["release"]));
    }

    #[test]
    fn legacy_drop_remains_best_effort_on_release_and_connect_failure() {
        for (release, connect, expected) in [
            (Err(Errno::NODEV), Ok(()), vec!["release"]),
            (Ok(()), Err(Errno::BUSY), vec!["release", "connect"]),
        ] {
            let (fixture, calls) = Fixture::new(true, release, connect);
            // This is the existing portable release's last-owner Drop path.
            let result = consume_unique_interface(Arc::new(fixture), |fixture| {
                drop(fixture);
                Ok(())
            });
            assert!(result.is_ok());
            assert!(calls.lock().is_ok_and(|calls| *calls == expected));
        }
    }
}

impl Drop for LinuxInterface {
    fn drop(&mut self) {
        let _ = self.release_inner();
    }
}

pub(crate) struct LinuxEndpoint {
    inner: Arc<EndpointInner>,

    pub(crate) max_packet_size: usize,

    /// A queue of pending transfers, expected to complete in order
    pending: VecDeque<Pending<super::TransferData>>,

    idle_transfer: Option<Idle<TransferData>>,
}

struct EndpointInner {
    interface: Arc<LinuxInterface>,
    address: u8,
    ep_type: TransferType,
    notify: Notify,
}

impl LinuxEndpoint {
    pub(crate) fn endpoint_address(&self) -> u8 {
        self.inner.address
    }

    pub(crate) fn pending(&self) -> usize {
        self.pending.len()
    }

    pub(crate) fn cancel_all(&mut self) {
        // Cancel transfers in reverse order to ensure subsequent transfers
        // can't complete out of order while we're going through them.
        for transfer in self.pending.iter_mut().rev() {
            self.inner.interface.device.cancel(transfer);
        }
    }

    fn get_transfer(&mut self) -> Idle<TransferData> {
        self.idle_transfer.take().unwrap_or_else(|| {
            Idle::new(
                self.inner.clone(),
                super::TransferData::new(self.inner.address, self.inner.ep_type),
            )
        })
    }

    pub(crate) fn submit(&mut self, data: Buffer) {
        let mut transfer = self.get_transfer();
        transfer.urb_mut().flags = 0;
        transfer.set_buffer(data);
        self.pending
            .push_back(self.inner.interface.device.submit(transfer));
    }

    pub(crate) fn submit_err(&mut self, data: Buffer, error: TransferError) {
        assert_eq!(error, TransferError::InvalidArgument);
        let mut transfer = self.get_transfer();
        transfer.set_buffer(data);
        transfer.urb_mut().status = Errno::INVAL.raw_os_error();
        self.pending.push_back(transfer.simulate_complete());
    }

    pub(crate) fn poll_next_complete(&mut self, cx: &mut Context) -> Poll<Completion> {
        self.inner.notify.subscribe(cx);
        if let Some(mut transfer) = take_completed_from_queue(&mut self.pending) {
            let completion = transfer.take_completion();
            self.idle_transfer = Some(transfer);
            Poll::Ready(completion)
        } else {
            Poll::Pending
        }
    }

    pub(crate) fn wait_next_complete(&mut self, timeout: Duration) -> Option<Completion> {
        self.inner.notify.wait_timeout(timeout, || {
            take_completed_from_queue(&mut self.pending).map(|mut transfer| {
                let completion = transfer.take_completion();
                self.idle_transfer = Some(transfer);
                completion
            })
        })
    }

    pub(crate) fn clear_halt(&self) -> impl MaybeFuture<Output = Result<(), Error>> {
        let inner = self.inner.clone();
        Blocking::new(move || {
            let endpoint = inner.address;
            debug!("Clear halt, endpoint {endpoint:02x}");
            usbfs::clear_halt(&inner.interface.device.fd, endpoint).map_err(|e| match e {
                Errno::NODEV => Error::new_os(ErrorKind::Disconnected, "device disconnected", e),
                _ => Error::new_os(ErrorKind::Other, "failed to clear halt", e),
            })
        })
    }

    pub(crate) fn allocate(&self, len: usize) -> Result<Buffer, Errno> {
        Buffer::mmap(&self.inner.interface.device.fd, len).inspect_err(|e| {
            warn!(
                "Failed to allocate zero-copy buffer of length {len} for endpoint {}: {e}",
                self.inner.address
            );
        })
    }
}

impl Drop for LinuxEndpoint {
    fn drop(&mut self) {
        if !self.pending.is_empty() {
            debug!(
                "Dropping endpoint {:02x} with {} pending transfers",
                self.inner.address,
                self.pending.len()
            );
            self.cancel_all();
        }
    }
}

impl AsRef<Notify> for EndpointInner {
    fn as_ref(&self) -> &Notify {
        &self.notify
    }
}

impl Drop for EndpointInner {
    fn drop(&mut self) {
        let mut state = self.interface.state.lock().unwrap();
        state.endpoints.clear(self.address);
    }
}

/// Linux-only raw completion; status is zero or the original negative errno.
#[derive(Debug)]
pub struct RawCompletion {
    /// Kernel status, normalized to negative errno for submit errors.
    pub status: i32,
    /// Actual bytes transferred, including on failure.
    pub actual_len: usize,
    /// IN bytes only; OUT is empty.
    pub data: Vec<u8>,
}

/// An owned physical control request; cancellation must still be reaped.
pub struct RawControlTransfer {
    transfer: TransferFuture<RawControlContext, TransferData>,
    direction: Direction,
}

enum RawControlContext {
    Device(Arc<LinuxDevice>),
    Interface(Arc<LinuxInterface>),
}

impl RawControlContext {
    fn device(&self) -> &Arc<LinuxDevice> {
        match self {
            Self::Device(device) => device,
            Self::Interface(interface) => &interface.device,
        }
    }
}

impl RawControlTransfer {
    /// Request cancellation and report whether DISCARDURB accepted it.
    /// Continue awaiting this same handle: acceptance does not mean that
    /// cancellation won or that kernel-owned memory has already been reclaimed.
    pub fn cancel(&mut self) -> bool {
        self.transfer
            .cancel_with(|context, pending| context.device().cancel(pending))
            .unwrap_or(false)
    }
}

impl Drop for RawControlTransfer {
    fn drop(&mut self) {
        self.cancel();
    }
}

impl std::future::Future for RawControlTransfer {
    type Output = RawCompletion;
    fn poll(mut self: std::pin::Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let direction = self.direction;
        std::pin::Pin::new(&mut self.transfer)
            .poll(cx)
            .map(|mut transfer| raw_control_completion(&mut transfer, direction))
    }
}

impl LinuxInterface {
    pub(crate) fn control_in_raw(
        self: &Arc<Self>,
        data: ControlIn,
        timeout: Duration,
        short_not_ok: bool,
    ) -> RawControlTransfer {
        let mut data = TransferData::new_control_in(data);
        data.urb_mut().flags = u32::from(short_not_ok);
        RawControlTransfer {
            transfer: TransferFuture::new(
                RawControlContext::Interface(self.clone()),
                data,
                |context, transfer| context.device().submit_timeout(transfer, timeout),
            ),
            direction: Direction::In,
        }
    }
    pub(crate) fn control_out_raw(
        self: &Arc<Self>,
        data: ControlOut,
        timeout: Duration,
    ) -> RawControlTransfer {
        RawControlTransfer {
            transfer: TransferFuture::new(
                RawControlContext::Interface(self.clone()),
                TransferData::new_control_out(data),
                |context, transfer| context.device().submit_timeout(transfer, timeout),
            ),
            direction: Direction::Out,
        }
    }
}

impl LinuxDevice {
    pub(crate) fn control_in_raw(
        self: &Arc<Self>,
        data: ControlIn,
        timeout: Duration,
        short_not_ok: bool,
    ) -> RawControlTransfer {
        let mut data = TransferData::new_control_in(data);
        data.urb_mut().flags = u32::from(short_not_ok);
        RawControlTransfer {
            transfer: TransferFuture::new(
                RawControlContext::Device(self.clone()),
                data,
                |context, transfer| context.device().submit_timeout(transfer, timeout),
            ),
            direction: Direction::In,
        }
    }
    pub(crate) fn control_out_raw(
        self: &Arc<Self>,
        data: ControlOut,
        timeout: Duration,
    ) -> RawControlTransfer {
        RawControlTransfer {
            transfer: TransferFuture::new(
                RawControlContext::Device(self.clone()),
                TransferData::new_control_out(data),
                |context, transfer| context.device().submit_timeout(transfer, timeout),
            ),
            direction: Direction::Out,
        }
    }
}

impl LinuxEndpoint {
    pub(crate) fn cancel_one_raw(&mut self) -> bool {
        if self.pending.len() != 1 {
            return false;
        }
        let Some(transfer) = self.pending.front_mut() else {
            return false;
        };
        if transfer.is_complete() {
            return false;
        }
        self.inner.interface.device.cancel(transfer)
    }
    pub(crate) fn submit_raw(&mut self, data: Buffer, flags: u32) {
        let mut transfer = self.get_transfer();
        transfer.set_buffer(data);
        transfer.urb_mut().flags = flags;
        self.pending
            .push_back(self.inner.interface.device.submit(transfer));
    }
    pub(crate) fn poll_next_complete_raw(&mut self, cx: &mut Context<'_>) -> Poll<RawCompletion> {
        self.inner.notify.subscribe(cx);
        if let Some(mut transfer) = take_completed_from_queue(&mut self.pending) {
            if transfer.urb().actual_length < 0
                || transfer.urb().actual_length > transfer.urb().buffer_length
            {
                transfer.urb_mut().actual_length = 0;
                transfer.urb_mut().status = -75;
            }
            let raw_status = transfer.urb().status;
            let completion = transfer.take_completion();
            let is_in = self.inner.address & 0x80 != 0;
            self.idle_transfer = Some(transfer);
            Poll::Ready(RawCompletion {
                status: if raw_status > 0 {
                    -raw_status
                } else {
                    raw_status
                },
                actual_len: completion.actual_len,
                data: if is_in {
                    completion.buffer.into_vec()
                } else {
                    Vec::new()
                },
            })
        } else {
            Poll::Pending
        }
    }
}

fn raw_control_completion(transfer: &mut TransferData, direction: Direction) -> RawCompletion {
    let limit = transfer.urb().buffer_length.saturating_sub(8);
    if transfer.urb().actual_length < 0 || transfer.urb().actual_length > limit {
        transfer.urb_mut().actual_length = 0;
        transfer.urb_mut().status = -75;
    }
    let status = transfer.urb().status;
    RawCompletion {
        status: if status > 0 { -status } else { status },
        actual_len: transfer.urb().actual_length as usize,
        data: if direction == Direction::In {
            transfer.control_in_data().to_vec()
        } else {
            Vec::new()
        },
    }
}

#[cfg(test)]
mod raw_completion_tests {
    use super::*;

    fn control(direction: Direction, actual: i32, status: i32) -> TransferData {
        let mut transfer = TransferData::new(direction as u8, TransferType::Control);
        let mut buffer = Buffer::new(12);
        buffer.extend_from_slice(&[0; 8]);
        buffer.extend_from_slice(&[11, 22, 33, 44]);
        transfer.set_buffer(buffer);
        transfer.urb_mut().actual_length = actual;
        transfer.urb_mut().status = status;
        transfer
    }

    #[test]
    fn control_in_preserves_errno_and_partial_bytes_without_setup() {
        let mut transfer = control(Direction::In, 3, -71);
        let completed = raw_control_completion(&mut transfer, Direction::In);
        assert_eq!(completed.status, -71);
        assert_eq!(completed.actual_len, 3);
        assert_eq!(completed.data, [11, 22, 33]);
    }

    #[test]
    fn control_out_keeps_actual_bytes_on_error_without_echoing_payload() {
        let mut transfer = control(Direction::Out, 2, -32);
        let completed = raw_control_completion(&mut transfer, Direction::Out);
        assert_eq!(completed.status, -32);
        assert_eq!(completed.actual_len, 2);
        assert!(completed.data.is_empty());
        let mut failed_submit = control(Direction::Out, 0, 13);
        assert_eq!(
            raw_control_completion(&mut failed_submit, Direction::Out).status,
            -13
        );
    }

    #[test]
    fn invalid_kernel_length_is_rejected_before_accessing_control_buffer() {
        for actual in [-1, 5] {
            let mut transfer = control(Direction::In, actual, 0);
            let completed = raw_control_completion(&mut transfer, Direction::In);
            assert_eq!(completed.status, -75);
            assert_eq!(completed.actual_len, 0);
            assert!(completed.data.is_empty());
        }
    }
}
