//! Bounded virtual device state, explicit registration and exclusive RAII leases.

use crate::{
    descriptors::{self, Profile},
    wire::{Completion, Submit},
};
use rustusb_core::{
    BusId, Device, DeviceDiscovery, Endpoint, Error, ExportBackend, Interface, Result,
};
use rustusb_protocol::{RemoteDevice, RemoteInterface};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    fmt,
    sync::{Arc, Mutex, MutexGuard},
    time::{Duration, Instant},
};
use tokio::sync::Notify;

const QUEUE_BYTES: usize = 65_536;
const MAX_HID_REPORTS: usize = 64;
const MAX_CDC_CHUNKS: usize = 64;
const STALL: i32 = -32;
const UNSUPPORTED: i32 = -95;

/// The current CDC line-coding control state; virtual serial does not emulate baud timing.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct LineCoding {
    /// Requested data-terminal rate in bits per second.
    pub rate: u32,
    /// CDC encoding: 0 one stop bit, 1 one-and-a-half, 2 two.
    pub stop_bits: u8,
    /// CDC parity encoding 0..=4.
    pub parity: u8,
    /// Data bits: 5, 6, 7, 8, or 16.
    pub data_bits: u8,
}

impl Default for LineCoding {
    fn default() -> Self {
        Self {
            rate: 115_200,
            stop_bits: 0,
            parity: 0,
            data_bits: 8,
        }
    }
}

impl LineCoding {
    fn bytes(self) -> Vec<u8> {
        let mut bytes = self.rate.to_le_bytes().to_vec();
        bytes.extend([self.stop_bits, self.parity, self.data_bits]);
        bytes
    }
    fn parse(bytes: &[u8]) -> Option<Self> {
        let [a, b, c, d, stop_bits, parity, data_bits] = bytes else {
            return None;
        };
        if *stop_bits > 2 || *parity > 4 || !matches!(data_bits, 5 | 6 | 7 | 8 | 16) {
            return None;
        }
        Some(Self {
            rate: u32::from_le_bytes([*a, *b, *c, *d]),
            stop_bits: *stop_bits,
            parity: *parity,
            data_bits: *data_bits,
        })
    }
}

/// Cumulative, payload-free counters for one virtual profile.
#[derive(Debug, Default, Clone, Serialize)]
pub struct TransferCounters {
    /// Control requests examined, including stalls.
    pub control: u64,
    /// Successful bulk-IN completions.
    pub bulk_in: u64,
    /// Successful bulk-OUT completions.
    pub bulk_out: u64,
    /// Successful interrupt-IN completions.
    pub interrupt: u64,
    /// UNLINK commands observed.
    pub unlink: u64,
    /// UNLINK commands that actually removed a pending request.
    pub unlink_cancelled: u64,
    /// Accepted CDC bulk-OUT bytes, counted once at acceptance.
    pub cdc_out_bytes: u64,
    /// Completed CDC bulk-IN bytes.
    pub cdc_in_bytes: u64,
    /// Bytes explicitly injected by the local test controller.
    pub cdc_injected_bytes: u64,
    /// Accepted local HID key taps, each queued as down plus release.
    pub hid_taps: u64,
    /// Exclusive imported sessions started.
    pub sessions_started: u64,
}

/// One profile's registration, lease and non-sensitive transfer observations.
#[derive(Debug, Clone, Serialize)]
pub struct VirtualDeviceSnapshot {
    /// Fixed profile name: hid_keyboard or cdc_acm.
    pub profile: String,
    /// Fixed virtual bus ID, not a physical USB address.
    pub bus_id: BusId,
    /// Whether DEVLIST/IMPORT can expose this registered profile.
    pub exported: bool,
    /// Whether an exclusive imported session currently owns it.
    pub leased: bool,
    /// Number of requests the session actor currently holds pending.
    pub pending_urbs: usize,
    /// Selected USB configuration (0 or 1).
    pub configuration: u8,
    /// Number of complete keyboard reports waiting for an interrupt request.
    pub hid_queued_reports: usize,
    /// CDC bytes queued for the client, bounded to 64 KiB.
    pub cdc_queued_bytes: usize,
    /// CDC line coding for the current session.
    pub line_coding: LineCoding,
    /// Host-requested CDC DTR/RTS bits.
    pub line_state: u16,
    /// Whether the host requested an active break condition.
    pub break_active: bool,
    /// Local test CDC OUT-to-IN loopback is enabled.
    pub cdc_loopback: bool,
    /// Cumulative request/byte counters, preserved across sessions.
    pub counters: TransferCounters,
    /// SHA256 of accepted CDC OUT bytes, without exposing payload.
    pub cdc_out_sha256: String,
    /// SHA256 of completed CDC IN bytes, without exposing payload.
    pub cdc_in_sha256: String,
}

/// Versioned snapshot of the two fixed virtual test devices.
#[derive(Debug, Clone, Serialize)]
pub struct BackendSnapshot {
    /// This diagnostic schema's current version.
    pub schema_version: u32,
    /// Both profiles, including unregistered or leased ones.
    pub devices: Vec<VirtualDeviceSnapshot>,
}

struct UsbState {
    configured: u8,
    address: u8,
    halted: [bool; 32],
    reports: VecDeque<[u8; 8]>,
    current_report: [u8; 8],
    idle: u8,
    protocol: u8,
    leds: u8,
    last_report: Instant,
    serial: VecDeque<VecDeque<u8>>,
    serial_bytes: usize,
    line: LineCoding,
    line_state: u16,
    break_until: Option<Instant>,
    break_indefinite: bool,
    notification: Option<[u8; 10]>,
}

impl Default for UsbState {
    fn default() -> Self {
        Self {
            configured: 0,
            address: 0,
            halted: [false; 32],
            reports: VecDeque::new(),
            current_report: [0; 8],
            idle: 0,
            protocol: 1,
            leds: 0,
            last_report: Instant::now(),
            serial: VecDeque::new(),
            serial_bytes: 0,
            line: LineCoding::default(),
            line_state: 0,
            break_until: None,
            break_indefinite: false,
            notification: None,
        }
    }
}

struct State {
    profile: Profile,
    metadata: RemoteDevice,
    exported: bool,
    leased: bool,
    epoch: u64,
    pending: usize,
    reset_pending: usize,
    usb: UsbState,
    counters: TransferCounters,
    out_hash: Sha256,
    in_hash: Sha256,
}

struct Shared {
    state: Mutex<State>,
    notify: Notify,
}
impl Shared {
    fn lock(&self) -> Result<MutexGuard<'_, State>> {
        self.state.lock().map_err(|_| {
            Error::State("virtual device state lock is poisoned; restart required".into())
        })
    }
    fn wake(&self) {
        self.notify.notify_waiters();
        self.notify.notify_one();
    }
}

/// Shared ordinary-user registry for exactly two isolated virtual USB profiles.
/// Construction explicitly registers both devices; no physical USB is accessed.
#[derive(Clone)]
pub struct VirtualBackend {
    devices: Arc<Vec<Arc<Shared>>>,
}
impl fmt::Debug for VirtualBackend {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("VirtualBackend").finish_non_exhaustive()
    }
}

impl VirtualBackend {
    /// Register a Full-Speed keyboard and CDC ACM using isolated test VID 1234.
    /// These IDs are not claimed to be registered or suitable for commercial use.
    pub fn new() -> Result<Self> {
        let mut devices = Vec::new();
        for (profile, bus_id, number) in [(Profile::Hid, "1-1", 1), (Profile::Cdc, "1-2", 2)] {
            let interfaces = match profile {
                Profile::Hid => vec![RemoteInterface {
                    class: 3,
                    subclass: 1,
                    protocol: 1,
                }],
                Profile::Cdc => vec![
                    RemoteInterface {
                        class: 2,
                        subclass: 2,
                        protocol: 0,
                    },
                    RemoteInterface {
                        class: 10,
                        subclass: 0,
                        protocol: 0,
                    },
                ],
            };
            let (class, subclass, protocol) = if profile == Profile::Hid {
                (0, 0, 0)
            } else {
                (0xef, 2, 1)
            };
            let metadata = RemoteDevice {
                bus_id: BusId::new(bus_id)?,
                path: format!("/virtual/rustusb/{bus_id}"),
                bus_number: 1,
                device_number: number,
                speed: 2,
                vendor_id: 0x1234,
                product_id: number as u16,
                device_version: 0x0100,
                class,
                subclass,
                protocol,
                configuration: 1,
                num_configurations: 1,
                interfaces,
            };
            devices.push(Arc::new(Shared {
                state: Mutex::new(State {
                    profile,
                    metadata,
                    exported: true,
                    leased: false,
                    epoch: 0,
                    pending: 0,
                    reset_pending: 0,
                    usb: UsbState::default(),
                    counters: TransferCounters::default(),
                    out_hash: Sha256::new(),
                    in_hash: Sha256::new(),
                }),
                notify: Notify::new(),
            }));
        }
        Ok(Self {
            devices: Arc::new(devices),
        })
    }

    /// Observe both profiles without exposing keyboard or serial payloads.
    pub fn snapshot(&self) -> Result<BackendSnapshot> {
        let mut devices = Vec::new();
        for shared in self.devices.iter() {
            let state = shared.lock()?;
            devices.push(VirtualDeviceSnapshot {
                profile: profile_name(state.profile).into(),
                bus_id: state.metadata.bus_id.clone(),
                exported: state.exported,
                leased: state.leased,
                pending_urbs: state.pending,
                configuration: state.usb.configured,
                hid_queued_reports: state.usb.reports.len(),
                cdc_queued_bytes: state.usb.serial_bytes,
                line_coding: state.usb.line,
                line_state: state.usb.line_state,
                break_active: state.usb.break_indefinite
                    || state
                        .usb
                        .break_until
                        .is_some_and(|until| until > Instant::now()),
                cdc_loopback: true,
                counters: state.counters.clone(),
                cdc_out_sha256: format!("{:x}", state.out_hash.clone().finalize()),
                cdc_in_sha256: format!("{:x}", state.in_hash.clone().finalize()),
            });
        }
        Ok(BackendSnapshot {
            schema_version: 1,
            devices,
        })
    }

    /// Queue one USB HID usage (4..=0x65) and an eight-byte release atomically.
    /// USB usage 30/31 represents the keyboard keys 1/2, not ASCII input.
    /// Success means queued for an imported session, not verified remote input.
    pub fn hid_key(&self, key: u8) -> Result<()> {
        if !(4..=0x65).contains(&key) {
            return Err(Error::InvalidInput(
                "keyboard usage must be 4..=0x65".into(),
            ));
        }
        let shared = &self.devices[0];
        let mut state = shared.lock()?;
        if !state.leased {
            return Err(Error::State("keyboard is not imported".into()));
        }
        if state.usb.reports.len() > MAX_HID_REPORTS - 2 {
            return Err(Error::State("keyboard input queue is full".into()));
        }
        let mut down = [0; 8];
        down[2] = key;
        state.usb.reports.extend([down, [0; 8]]);
        state.counters.hid_taps = state.counters.hid_taps.saturating_add(1);
        drop(state);
        shared.wake();
        Ok(())
    }

    /// Queue a bounded serial transfer for an imported CDC client.
    /// A chunk boundary terminates a short IN transfer; an exact packet multiple
    /// shorter than the requested URB represents termination including a ZLP.
    /// Empty application input adds no packet; a zero-length USB URB still works.
    pub fn cdc_emit(&self, bytes: &[u8]) -> Result<()> {
        let shared = &self.devices[1];
        let mut state = shared.lock()?;
        if !state.leased {
            return Err(Error::State("CDC device is not imported".into()));
        }
        if bytes.len() > QUEUE_BYTES || !serial_fits(&state.usb, bytes.len()) {
            return Err(Error::State("CDC input queue limit exceeded".into()));
        }
        if !bytes.is_empty() {
            state.usb.serial.push_back(bytes.iter().copied().collect());
            state.usb.serial_bytes += bytes.len();
        }
        state.counters.cdc_injected_bytes = state
            .counters
            .cdc_injected_bytes
            .saturating_add(bytes.len() as u64);
        drop(state);
        shared.wake();
        Ok(())
    }

    /// Return registered metadata; `available_only` hides exclusively imported devices.
    pub fn remote_devices(&self, available_only: bool) -> Result<Vec<RemoteDevice>> {
        let mut result = Vec::new();
        for shared in self.devices.iter() {
            let state = shared.lock()?;
            if state.exported && (!available_only || !state.leased) {
                result.push(state.metadata.clone());
            }
        }
        Ok(result)
    }

    /// Atomically claim a registered device, returning its exclusive session lease.
    /// Unknown/unregistered IDs are InvalidInput; an existing lease is State.
    pub fn claim(&self, bus_id: &str) -> Result<DeviceLease> {
        for shared in self.devices.iter() {
            let mut state = shared.lock()?;
            if state.metadata.bus_id.as_str() != bus_id {
                continue;
            }
            if !state.exported {
                return Err(Error::InvalidInput(
                    "virtual device is not registered".into(),
                ));
            }
            if state.leased {
                return Err(Error::State("virtual device is already imported".into()));
            }
            state.epoch = state
                .epoch
                .checked_add(1)
                .ok_or_else(|| Error::State("virtual lease generation exhausted".into()))?;
            state.leased = true;
            state.pending = 0;
            state.reset_pending = 0;
            state.usb = UsbState::default();
            state.counters.sessions_started = state.counters.sessions_started.saturating_add(1);
            return Ok(DeviceLease {
                shared: Arc::clone(shared),
                epoch: state.epoch,
                metadata: state.metadata.clone(),
            });
        }
        Err(Error::InvalidInput("unknown virtual bus ID".into()))
    }
}

impl DeviceDiscovery for VirtualBackend {
    fn scan(&self) -> Result<Vec<Device>> {
        let mut result = Vec::new();
        for shared in self.devices.iter() {
            let state = shared.lock()?;
            let mut interfaces = Vec::new();
            for (index, interface) in state.metadata.interfaces.iter().enumerate() {
                let endpoints = match (state.profile, index) {
                    (Profile::Hid, _) => vec![Endpoint {
                        address: 0x81,
                        attributes: 3,
                        max_packet_size: 8,
                        interval: 10,
                    }],
                    (Profile::Cdc, 0) => vec![Endpoint {
                        address: 0x81,
                        attributes: 3,
                        max_packet_size: 16,
                        interval: 10,
                    }],
                    _ => vec![
                        Endpoint {
                            address: 0x02,
                            attributes: 2,
                            max_packet_size: 64,
                            interval: 0,
                        },
                        Endpoint {
                            address: 0x82,
                            attributes: 2,
                            max_packet_size: 64,
                            interval: 0,
                        },
                    ],
                };
                interfaces.push(Interface {
                    number: index as u8,
                    alternate_setting: 0,
                    class: interface.class,
                    subclass: interface.subclass,
                    protocol: interface.protocol,
                    driver: None,
                    endpoints,
                });
            }
            result.push(Device {
                id: format!("virtual-{}", profile_name(state.profile)),
                bus_id: state.metadata.bus_id.clone(),
                vendor_id: state.metadata.vendor_id,
                product_id: state.metadata.product_id,
                manufacturer: Some("RustUSB Virtual Test".into()),
                product: Some(profile_name(state.profile).into()),
                serial: None,
                device_address: state.metadata.device_number,
                usb_version: "2.00".into(),
                speed: "12".into(),
                class: state.metadata.class,
                subclass: state.metadata.subclass,
                protocol: state.metadata.protocol,
                topology: format!("virtual:{}", state.metadata.bus_id),
                driver: None,
                interfaces,
                exported: state.exported,
                occupied: Some(state.leased),
                export_blockers: vec![],
            });
        }
        Ok(result)
    }
}

impl ExportBackend for VirtualBackend {
    async fn export(&self, bus_id: BusId) -> Result<()> {
        self.registration(&bus_id, true)
    }
    async fn unexport(&self, bus_id: BusId) -> Result<()> {
        self.registration(&bus_id, false)
    }
}
impl VirtualBackend {
    fn registration(&self, bus_id: &BusId, exported: bool) -> Result<()> {
        for shared in self.devices.iter() {
            let mut state = shared.lock()?;
            if &state.metadata.bus_id != bus_id {
                continue;
            }
            if !exported && state.leased {
                return Err(Error::State(
                    "cannot unregister an imported virtual device".into(),
                ));
            }
            state.exported = exported;
            return Ok(());
        }
        Err(Error::InvalidInput("unknown virtual bus ID".into()))
    }
}

fn profile_name(profile: Profile) -> &'static str {
    match profile {
        Profile::Hid => "hid_keyboard",
        Profile::Cdc => "cdc_acm",
    }
}
fn serial_fits(usb: &UsbState, count: usize) -> bool {
    count <= QUEUE_BYTES.saturating_sub(usb.serial_bytes)
        && (count == 0 || usb.serial.len() < MAX_CDC_CHUNKS)
}
fn complete(status: i32, data: Vec<u8>, actual: u32) -> Completion {
    Completion {
        status,
        actual_length: actual,
        data,
    }
}
fn success(data: Vec<u8>) -> Completion {
    let length = data.len() as u32;
    complete(0, data, length)
}
fn stalled() -> Completion {
    complete(STALL, Vec::new(), 0)
}

/// Exclusive virtual device session. Drop clears USB configuration and all queued
/// session data, returns the device to the registry, and wakes pending observers.
pub struct DeviceLease {
    shared: Arc<Shared>,
    epoch: u64,
    metadata: RemoteDevice,
}
impl fmt::Debug for DeviceLease {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("DeviceLease")
            .field("bus_id", &self.metadata.bus_id)
            .field("epoch", &self.epoch)
            .finish_non_exhaustive()
    }
}
impl DeviceLease {
    /// Clone metadata validated when this exclusive lease was created.
    pub fn device(&self) -> RemoteDevice {
        self.metadata.clone()
    }
    fn state(&self) -> Result<MutexGuard<'_, State>> {
        let state = self.shared.lock()?;
        if !state.leased || state.epoch != self.epoch {
            return Err(Error::State(
                "virtual device lease is no longer current".into(),
            ));
        }
        Ok(state)
    }
    pub(crate) fn notify(&self) -> &Notify {
        &self.shared.notify
    }
    pub(crate) fn set_pending_count(&self, count: usize) -> Result<()> {
        self.state()?.pending = count;
        Ok(())
    }
    pub(crate) fn record_unlink(&self, cancelled: bool) -> Result<()> {
        let mut state = self.state()?;
        state.counters.unlink = state.counters.unlink.saturating_add(1);
        if cancelled {
            state.counters.unlink_cancelled = state.counters.unlink_cancelled.saturating_add(1);
        }
        Ok(())
    }
    pub(crate) fn submit(&self, request: &Submit) -> Result<Option<Completion>> {
        self.transfer(request, true)
    }
    pub(crate) fn poll_pending(&self, request: &Submit) -> Result<Option<Completion>> {
        self.transfer(request, false)
    }
    fn transfer(&self, request: &Submit, initial: bool) -> Result<Option<Completion>> {
        let mut state = self.state()?;
        // The single session actor polls every existing pending request before
        // accepting another command. A successful SET_CONFIGURATION therefore
        // retires this exact preceding batch without consuming new input.
        if !initial && state.reset_pending > 0 {
            state.reset_pending -= 1;
            return Ok(Some(complete(-108, Vec::new(), 0)));
        }
        if request.length as usize > QUEUE_BYTES
            || request.direction > 1
            || request.endpoint > 15
            || request.devid != ((self.metadata.bus_number << 16) | self.metadata.device_number)
        {
            return Err(Error::Protocol(
                "virtual transfer metadata is invalid".into(),
            ));
        }
        if !matches!(request.num_packets, 0 | -1)
            || !matches!(request.start_frame, 0 | -1)
            || request.interval < 0
        {
            return Ok(Some(complete(UNSUPPORTED, Vec::new(), 0)));
        }
        // Accept the Linux metadata/control flags, but reject execution semantics
        // not supplied by these two non-ISO profiles. 0x200 is URB_DIR_IN.
        if request.flags & !crate::wire::KNOWN_FLAGS != 0
            || (request.direction == 0 && request.flags & 0x200 != 0)
            || (request.flags & 0x40 != 0 && (request.direction != 0 || request.endpoint != 2))
        {
            return Ok(Some(complete(UNSUPPORTED, Vec::new(), 0)));
        }
        let result = if request.endpoint == 0 {
            if initial {
                state.counters.control = state.counters.control.saturating_add(1);
            }
            Some(control(&mut state, request))
        } else {
            endpoint(&mut state, request)
        };
        let result = result.map(|mut result| {
            if request.direction == 1
                && result.status == 0
                && result.actual_length < request.length
                && request.flags & 1 != 0
            {
                result.status = -121;
            }
            result
        });
        drop(state);
        if result.is_some() {
            self.shared.wake();
        }
        Ok(result)
    }
}
impl Drop for DeviceLease {
    fn drop(&mut self) {
        if let Ok(mut state) = self.shared.state.lock()
            && state.leased
            && state.epoch == self.epoch
        {
            state.usb = UsbState::default();
            state.pending = 0;
            state.reset_pending = 0;
            state.leased = false;
        }
        self.shared.wake();
    }
}

#[derive(Clone, Copy)]
struct Setup {
    kind: u8,
    request: u8,
    value: u16,
    index: u16,
    length: u16,
}
impl Setup {
    fn new(bytes: [u8; 8]) -> Self {
        Self {
            kind: bytes[0],
            request: bytes[1],
            value: u16::from_le_bytes([bytes[2], bytes[3]]),
            index: u16::from_le_bytes([bytes[4], bytes[5]]),
            length: u16::from_le_bytes([bytes[6], bytes[7]]),
        }
    }
}

fn control(state: &mut State, urb: &Submit) -> Completion {
    let setup = Setup::new(urb.setup);
    if u32::from(setup.kind >> 7) != urb.direction
        || (urb.direction == 0
            && (urb.data.len() != usize::from(setup.length)
                || urb.length != u32::from(setup.length)))
    {
        return stalled();
    }
    let mut reply = match (setup.kind, setup.request) {
        (0x80, 6) => descriptors::descriptor(
            state.profile,
            (setup.value >> 8) as u8,
            setup.value as u8,
            setup.index,
        )
        .map(success)
        .unwrap_or_else(stalled),
        (0x00, 5) if setup.value <= 127 && setup.index == 0 && setup.length == 0 => {
            state.usb.address = setup.value as u8;
            success(vec![])
        }
        (0x80, 8) if setup.value == 0 && setup.index == 0 => success(vec![state.usb.configured]),
        (0x00, 9) if setup.value <= 1 && setup.index == 0 && setup.length == 0 => {
            let address = state.usb.address;
            state.reset_pending = state.pending;
            state.usb = UsbState::default();
            state.usb.address = address;
            state.usb.configured = setup.value as u8;
            success(vec![])
        }
        (0x80, 0) if setup.value == 0 && setup.index == 0 => success(vec![0, 0]),
        (0x81, 0) if valid_interface(state.profile, setup.index) && setup.value == 0 => {
            success(vec![0, 0])
        }
        (0x82, 0) if setup.value == 0 => {
            if let Some(endpoint) = valid_endpoint(state.profile, setup.index) {
                success(vec![u8::from(state.usb.halted[endpoint]), 0])
            } else {
                stalled()
            }
        }
        (0x02, 1 | 3) if setup.value == 0 && setup.length == 0 => {
            if let Some(endpoint) = valid_endpoint(state.profile, setup.index) {
                state.usb.halted[endpoint] = setup.request == 3;
                success(vec![])
            } else {
                stalled()
            }
        }
        (0x81, 10) if valid_interface(state.profile, setup.index) && setup.value == 0 => {
            success(vec![0])
        }
        (0x01, 11)
            if valid_interface(state.profile, setup.index)
                && setup.value == 0
                && setup.length == 0 =>
        {
            success(vec![])
        }
        _ if state.profile == Profile::Hid => hid_control(&mut state.usb, setup, &urb.data),
        _ if state.profile == Profile::Cdc => cdc_control(&mut state.usb, setup, &urb.data),
        _ => stalled(),
    };
    if urb.direction == 1 {
        reply
            .data
            .truncate(usize::min(usize::from(setup.length), urb.length as usize));
        reply.actual_length = reply.data.len() as u32;
    } else if reply.status == 0 {
        reply.data.clear();
        reply.actual_length = urb.data.len() as u32;
    }
    reply
}
fn valid_interface(profile: Profile, index: u16) -> bool {
    index == 0 || (profile == Profile::Cdc && index == 1)
}
fn valid_endpoint(profile: Profile, address: u16) -> Option<usize> {
    match address {
        0x81 => Some(17),
        0x02 if profile == Profile::Cdc => Some(2),
        0x82 if profile == Profile::Cdc => Some(18),
        _ => None,
    }
}
fn hid_control(usb: &mut UsbState, setup: Setup, data: &[u8]) -> Completion {
    if setup.index != 0 {
        return stalled();
    }
    match (setup.kind, setup.request, setup.value) {
        (0x81, 6, 0x2100) => success(descriptors::HID_CLASS.to_vec()),
        (0x81, 6, 0x2200) => success(descriptors::HID_REPORT.to_vec()),
        (0xa1, 1, 0x0100) => success(usb.current_report.to_vec()),
        (0xa1, 1, 0x0200) => success(vec![usb.leds]),
        (0x21, 9, 0x0200) if data.len() == 1 => {
            usb.leds = data[0] & 0x1f;
            success(vec![])
        }
        (0xa1, 2, 0) => success(vec![usb.idle]),
        (0x21, 10, value) if value & 0xff == 0 && setup.length == 0 => {
            usb.idle = (value >> 8) as u8;
            success(vec![])
        }
        (0xa1, 3, 0) => success(vec![usb.protocol]),
        (0x21, 11, 0 | 1) if setup.length == 0 => {
            usb.protocol = setup.value as u8;
            success(vec![])
        }
        _ => stalled(),
    }
}
fn cdc_control(usb: &mut UsbState, setup: Setup, data: &[u8]) -> Completion {
    if setup.index != 0 {
        return stalled();
    }
    match (setup.kind, setup.request) {
        (0x21, 0x20) if setup.value == 0 => {
            if let Some(line) = LineCoding::parse(data) {
                usb.line = line;
                success(vec![])
            } else {
                stalled()
            }
        }
        (0xa1, 0x21) if setup.value == 0 => success(usb.line.bytes()),
        (0x21, 0x22) if setup.value & !3 == 0 && setup.length == 0 => {
            usb.line_state = setup.value;
            notification(usb);
            success(vec![])
        }
        (0x21, 0x23) if setup.length == 0 => {
            usb.break_indefinite = setup.value == 0xffff;
            usb.break_until = if setup.value == 0 || setup.value == 0xffff {
                None
            } else {
                Some(Instant::now() + Duration::from_millis(u64::from(setup.value)))
            };
            notification(usb);
            success(vec![])
        }
        _ => stalled(),
    }
}
fn notification(usb: &mut UsbState) {
    let active_break =
        usb.break_indefinite || usb.break_until.is_some_and(|until| until > Instant::now());
    let bits = if usb.line_state & 1 != 0 { 3 } else { 0 } | if active_break { 4 } else { 0 };
    usb.notification = Some([0xa1, 0x20, 0, 0, 0, 0, 2, 0, bits, 0]);
}

fn endpoint(state: &mut State, urb: &Submit) -> Option<Completion> {
    let address = (urb.endpoint as u16) | if urb.direction == 1 { 0x80 } else { 0 };
    let Some(index) = valid_endpoint(state.profile, address) else {
        return Some(stalled());
    };
    if state.usb.configured != 1 || state.usb.halted[index] {
        return Some(stalled());
    }
    if urb.direction == 1 && urb.length == 0 {
        return Some(success(vec![]));
    }
    match (state.profile, address) {
        (Profile::Hid, 0x81) => {
            if urb.length < 8 {
                return Some(complete(-90, vec![], 0));
            }
            let report = if let Some(report) = state.usb.reports.pop_front() {
                report
            } else if state.usb.idle != 0
                && state.usb.last_report.elapsed()
                    >= Duration::from_millis(u64::from(state.usb.idle) * 4)
            {
                state.usb.current_report
            } else {
                return None;
            };
            state.usb.current_report = report;
            state.usb.last_report = Instant::now();
            state.counters.interrupt = state.counters.interrupt.saturating_add(1);
            Some(success(report.to_vec()))
        }
        (Profile::Cdc, 0x81) => {
            if state
                .usb
                .break_until
                .is_some_and(|until| until <= Instant::now())
            {
                state.usb.break_until = None;
                notification(&mut state.usb);
            }
            if urb.length < 10 {
                return Some(complete(-90, vec![], 0));
            }
            let packet = state.usb.notification.take()?;
            state.counters.interrupt = state.counters.interrupt.saturating_add(1);
            Some(success(packet.to_vec()))
        }
        (Profile::Cdc, 0x02) => {
            if urb.data.len() != urb.length as usize {
                return Some(stalled());
            }
            if !serial_fits(&state.usb, urb.data.len()) {
                // The session actor only permits pending IN requests. Refuse
                // this OUT atomically rather than acknowledge dropped bytes.
                return Some(complete(-28, vec![], 0));
            }
            if !urb.data.is_empty() {
                state
                    .usb
                    .serial
                    .push_back(urb.data.iter().copied().collect());
                state.usb.serial_bytes += urb.data.len();
            }
            state.counters.bulk_out = state.counters.bulk_out.saturating_add(1);
            state.counters.cdc_out_bytes = state
                .counters
                .cdc_out_bytes
                .saturating_add(urb.data.len() as u64);
            state.out_hash.update(&urb.data);
            Some(complete(0, vec![], urb.length))
        }
        (Profile::Cdc, 0x82) => {
            let front = state.usb.serial.front_mut()?;
            let count = usize::min(urb.length as usize, front.len());
            let data: Vec<u8> = front.drain(..count).collect();
            if front.is_empty() {
                state.usb.serial.pop_front();
            }
            state.usb.serial_bytes -= count;
            state.counters.bulk_in = state.counters.bulk_in.saturating_add(1);
            state.counters.cdc_in_bytes = state.counters.cdc_in_bytes.saturating_add(count as u64);
            state.in_hash.update(&data);
            Some(success(data))
        }
        _ => Some(stalled()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(device: u32, direction: u32, endpoint: u32, length: u32, data: &[u8]) -> Submit {
        Submit {
            seqnum: 1,
            devid: (1 << 16) | device,
            direction,
            endpoint,
            flags: if direction == 1 { 0x200 } else { 0 },
            length,
            start_frame: 0,
            num_packets: 0,
            interval: 0,
            setup: [0; 8],
            data: data.to_vec(),
        }
    }
    // Keep all six SETUP fields and the independent URB capacity explicit in
    // boundary fixtures rather than concealing malformed combinations.
    #[allow(clippy::too_many_arguments)]
    fn control_request(
        device: u32,
        kind: u8,
        code: u8,
        value: u16,
        index: u16,
        length: u16,
        capacity: u32,
        data: &[u8],
    ) -> Submit {
        let mut submit = request(device, u32::from(kind >> 7), 0, capacity, data);
        submit.setup = [
            kind,
            code,
            value as u8,
            (value >> 8) as u8,
            index as u8,
            (index >> 8) as u8,
            length as u8,
            (length >> 8) as u8,
        ];
        submit
    }
    fn result(value: Option<Completion>) -> Result<Completion> {
        value.ok_or_else(|| Error::State("expected completed fixture transfer".into()))
    }
    fn configure(lease: &DeviceLease, number: u32) -> Result<()> {
        assert_eq!(
            result(lease.submit(&control_request(number, 0, 9, 1, 0, 0, 0, &[]))?)?.status,
            0
        );
        Ok(())
    }

    #[tokio::test]
    async fn registration_exclusivity_drop_and_discovery_are_real_state_changes() -> Result<()> {
        let backend = VirtualBackend::new()?;
        assert_eq!(backend.scan()?.len(), 2);
        assert_eq!(backend.remote_devices(true)?.len(), 2);
        let lease = backend.claim("1-1")?;
        assert!(matches!(backend.claim("1-1"), Err(Error::State(_))));
        assert_eq!(backend.remote_devices(true)?.len(), 1);
        assert!(backend.unexport(BusId::new("1-1")?).await.is_err());
        configure(&lease, 1)?;
        backend.hid_key(30)?;
        lease.set_pending_count(3)?;
        drop(lease);
        let snapshot = backend.snapshot()?;
        assert!(!snapshot.devices[0].leased);
        assert_eq!(snapshot.devices[0].pending_urbs, 0);
        assert_eq!(snapshot.devices[0].configuration, 0);
        assert_eq!(snapshot.devices[0].hid_queued_reports, 0);
        backend.unexport(BusId::new("1-1")?).await?;
        assert_eq!(backend.remote_devices(true)?.len(), 1);
        assert!(matches!(backend.claim("1-1"), Err(Error::InvalidInput(_))));
        backend.export(BusId::new("1-1")?).await?;
        assert_eq!(backend.remote_devices(true)?.len(), 2);
        assert!(matches!(backend.claim("2-1"), Err(Error::InvalidInput(_))));
        Ok(())
    }

    #[test]
    fn control_lengths_configuration_and_invalid_interface_never_panic() -> Result<()> {
        let backend = VirtualBackend::new()?;
        let lease = backend.claim("1-1")?;
        let descriptor = control_request(1, 0x80, 6, 0x0100, 0, 18, 1, &[]);
        let reply = result(lease.submit(&descriptor)?)?;
        assert_eq!(reply.data, vec![18]);
        assert_eq!(reply.actual_length, 1);
        for length in [0, 1, 8, 9, 34, 255] {
            let reply = result(lease.submit(&control_request(
                1,
                0x80,
                6,
                0x0200,
                0,
                length,
                u32::from(length),
                &[],
            ))?)?;
            assert_eq!(reply.status, 0);
            assert_eq!(reply.data.len(), usize::from(length).min(34));
        }
        for index in [1, 255, 256, 65535] {
            assert_eq!(
                result(lease.submit(&control_request(
                    1,
                    0x81,
                    6,
                    0x2200,
                    index,
                    255,
                    255,
                    &[]
                ))?)?
                .status,
                STALL
            );
        }
        configure(&lease, 1)?;
        assert_eq!(
            result(lease.submit(&control_request(1, 0x80, 8, 0, 0, 1, 1, &[]))?)?.data,
            vec![1]
        );
        assert_eq!(
            result(lease.submit(&control_request(1, 1, 11, 1, 0, 0, 0, &[]))?)?.status,
            STALL
        );
        assert_eq!(
            result(lease.submit(&control_request(1, 0x80, 6, 0x0600, 0, 10, 10, &[]))?)?.status,
            STALL
        );
        Ok(())
    }

    #[test]
    fn keyboard_pending_reports_release_control_and_bounded_atomic_queue() -> Result<()> {
        let backend = VirtualBackend::new()?;
        let lease = backend.claim("1-1")?;
        configure(&lease, 1)?;
        let input = request(1, 1, 1, 8, &[]);
        assert!(lease.submit(&input)?.is_none());
        for bad in [0, 1, 2, 3, 0x66, 255] {
            assert!(backend.hid_key(bad).is_err());
        }
        backend.hid_key(30)?;
        let down = result(lease.poll_pending(&input)?)?;
        assert_eq!(down.data, [0, 0, 30, 0, 0, 0, 0, 0]);
        assert_eq!(result(lease.submit(&input)?)?.data, [0; 8]);
        assert!(lease.submit(&input)?.is_none());
        for _ in 0..32 {
            backend.hid_key(31)?;
        }
        assert!(backend.hid_key(31).is_err());
        assert_eq!(backend.snapshot()?.devices[0].hid_queued_reports, 64);
        assert_eq!(
            result(lease.submit(&control_request(1, 0x21, 9, 0x0200, 0, 1, 1, &[5]))?)?.status,
            0
        );
        assert_eq!(
            result(lease.submit(&control_request(1, 0xa1, 1, 0x0200, 0, 1, 1, &[]))?)?.data,
            vec![5]
        );
        assert_eq!(
            result(lease.submit(&control_request(1, 0x21, 11, 0, 0, 0, 0, &[]))?)?.status,
            0
        );
        assert_eq!(
            result(lease.submit(&control_request(1, 0xa1, 3, 0, 0, 1, 1, &[]))?)?.data,
            vec![0]
        );
        assert_eq!(
            result(lease.submit(&control_request(1, 0x21, 11, 2, 0, 0, 0, &[]))?)?.status,
            STALL
        );
        Ok(())
    }

    #[test]
    fn explicit_idle_rate_repeats_current_report_and_cancellation_does_not_consume_input()
    -> Result<()> {
        let backend = VirtualBackend::new()?;
        let lease = backend.claim("1-1")?;
        configure(&lease, 1)?;
        let input = request(1, 1, 1, 8, &[]);
        assert!(lease.submit(&input)?.is_none());
        lease.record_unlink(true)?;
        backend.hid_key(30)?;
        // The session actor removes the cancelled request; input is only
        // consumed by a different pending/submitted request that is completed.
        assert_eq!(result(lease.submit(&input)?)?.data[2], 30);
        assert_eq!(result(lease.submit(&input)?)?.data, [0; 8]);
        lease.submit(&control_request(1, 0x21, 10, 0x0100, 0, 0, 0, &[]))?;
        lease.state()?.usb.last_report = Instant::now() - Duration::from_millis(5);
        assert_eq!(result(lease.poll_pending(&input)?)?.data, [0; 8]);
        assert!(lease.poll_pending(&input)?.is_none());
        assert_eq!(backend.snapshot()?.devices[0].counters.unlink_cancelled, 1);
        Ok(())
    }

    #[test]
    fn reconfiguration_retires_pending_and_clears_queues_without_consuming_new_data() -> Result<()>
    {
        let backend = VirtualBackend::new()?;
        let lease = backend.claim("1-1")?;
        configure(&lease, 1)?;
        let input = request(1, 1, 1, 8, &[]);
        assert!(lease.submit(&input)?.is_none());
        lease.set_pending_count(1)?;
        backend.hid_key(30)?;
        configure(&lease, 1)?;
        assert_eq!(backend.snapshot()?.devices[0].hid_queued_reports, 0);
        backend.hid_key(31)?;
        assert_eq!(result(lease.poll_pending(&input)?)?.status, -108);
        lease.set_pending_count(0)?;
        assert_eq!(result(lease.submit(&input)?)?.data[2], 31);
        let cdc = backend.claim("1-2")?;
        configure(&cdc, 2)?;
        backend.cdc_emit(b"old configuration")?;
        cdc.submit(&control_request(2, 0, 9, 0, 0, 0, 0, &[]))?;
        assert_eq!(backend.snapshot()?.devices[1].cdc_queued_bytes, 0);
        configure(&cdc, 2)?;
        assert!(cdc.submit(&request(2, 1, 2, 64, &[]))?.is_none());
        Ok(())
    }

    #[test]
    fn cdc_line_coding_control_lines_break_and_notification_are_stateful() -> Result<()> {
        let backend = VirtualBackend::new()?;
        let lease = backend.claim("1-2")?;
        configure(&lease, 2)?;
        let line = [0x80, 0x25, 0, 0, 2, 2, 7];
        assert_eq!(
            result(lease.submit(&control_request(2, 0x21, 0x20, 0, 0, 7, 7, &line))?)?
                .actual_length,
            7
        );
        assert_eq!(
            result(lease.submit(&control_request(2, 0xa1, 0x21, 0, 0, 7, 7, &[]))?)?.data,
            line
        );
        assert_eq!(
            result(lease.submit(&control_request(2, 0xa1, 0x21, 0, 0, 7, 1, &[]))?)?.data,
            vec![0x80]
        );
        assert_eq!(
            result(lease.submit(&control_request(2, 0xa1, 0x21, 0, 1, 7, 7, &[]))?)?.status,
            STALL
        );
        assert_eq!(
            result(lease.submit(&control_request(2, 0x21, 0x20, 0, 0, 6, 6, &line[..6]))?)?.status,
            STALL
        );
        lease.submit(&control_request(2, 0x21, 0x22, 3, 0, 0, 0, &[]))?;
        let notification = request(2, 1, 1, 16, &[]);
        assert_eq!(
            result(lease.submit(&notification)?)?.data,
            [0xa1, 0x20, 0, 0, 0, 0, 2, 0, 3, 0]
        );
        assert!(lease.submit(&notification)?.is_none());
        lease.submit(&control_request(2, 0x21, 0x23, 65535, 0, 0, 0, &[]))?;
        assert_eq!(result(lease.submit(&notification)?)?.data[8], 7);
        assert!(backend.snapshot()?.devices[1].break_active);
        lease.submit(&control_request(2, 0x21, 0x23, 0, 0, 0, 0, &[]))?;
        assert!(!backend.snapshot()?.devices[1].break_active);
        Ok(())
    }

    #[test]
    fn cdc_bulk_short_exact_packet_zero_length_limits_and_hashes() -> Result<()> {
        let backend = VirtualBackend::new()?;
        let lease = backend.claim("1-2")?;
        configure(&lease, 2)?;
        let input = request(2, 1, 2, 128, &[]);
        assert!(lease.submit(&input)?.is_none());
        assert_eq!(
            result(lease.submit(&request(2, 0, 2, 0, &[]))?)?.actual_length,
            0
        );
        assert!(lease.submit(&input)?.is_none());
        for length in [1, 63, 64, 65, 127, 128] {
            let bytes = vec![0x5a; length];
            let output = request(2, 0, 2, length as u32, &bytes);
            let ack = result(lease.submit(&output)?)?;
            assert_eq!(ack.actual_length, length as u32);
            assert!(ack.data.is_empty());
            let first = result(lease.submit(&request(2, 1, 2, 1, &[]))?)?;
            assert_eq!(first.data, vec![0x5a]);
            if length > 1 {
                assert_eq!(
                    result(lease.submit(&request(2, 1, 2, 256, &[]))?)?.data,
                    vec![0x5a; length - 1]
                );
            }
            assert!(lease.submit(&input)?.is_none());
        }
        backend.cdc_emit(&vec![7; QUEUE_BYTES])?;
        assert!(backend.cdc_emit(&[1]).is_err());
        let before_overflow = backend.snapshot()?.devices[1].counters.cdc_out_bytes;
        let overflow = result(lease.submit(&request(2, 0, 2, 1, &[1]))?)?;
        assert_eq!(overflow.status, -28);
        assert_eq!(overflow.actual_length, 0);
        assert_eq!(
            backend.snapshot()?.devices[1].counters.cdc_out_bytes,
            before_overflow
        );
        assert_eq!(
            result(lease.submit(&request(2, 1, 2, 0, &[]))?)?.actual_length,
            0
        );
        assert_eq!(backend.snapshot()?.devices[1].cdc_queued_bytes, QUEUE_BYTES);
        assert_eq!(
            result(lease.submit(&request(2, 1, 2, QUEUE_BYTES as u32, &[]))?)?.actual_length,
            QUEUE_BYTES as u32
        );
        assert_eq!(
            result(lease.submit(&request(2, 0, 2, 1, &[1]))?)?.actual_length,
            1
        );
        let mut short = request(2, 1, 2, 64, &[]);
        short.flags |= 1;
        let short_reply = result(lease.submit(&short)?)?;
        assert_eq!(short_reply.status, -121);
        assert_eq!(short_reply.data, vec![1]);
        let snapshot = backend.snapshot()?;
        assert!(
            snapshot.devices[1].counters.cdc_in_bytes > snapshot.devices[1].counters.cdc_out_bytes
        );
        assert_eq!(snapshot.devices[1].cdc_out_sha256.len(), 64);
        assert_eq!(snapshot.devices[1].cdc_in_sha256.len(), 64);
        assert!(
            !serde_json::to_string(&snapshot)
                .map_err(|e| Error::State(e.to_string()))?
                .contains("payload")
        );
        Ok(())
    }
}
