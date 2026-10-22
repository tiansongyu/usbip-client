//! Device supervision outlives the network capability and owns all USB buffers.

use super::*;
use nusb::{
    Endpoint, Interface, RawCompletion, RawControlTransfer,
    descriptors::TransferType,
    transfer::{Buffer, Bulk, ControlIn, ControlOut, ControlType, In, Interrupt, Out, Recipient},
};
use std::{
    future::{Future, poll_fn},
    pin::Pin,
};
use tokio::time::Instant;

enum UsbEndpoint {
    BulkIn(Endpoint<Bulk, In>),
    BulkOut(Endpoint<Bulk, Out>),
    InterruptIn(Endpoint<Interrupt, In>),
    InterruptOut(Endpoint<Interrupt, Out>),
}

impl UsbEndpoint {
    fn submit(&mut self, request: &mut Submit) -> std::result::Result<(), nusb::Error> {
        let buffer = if request.direction == 1 {
            Buffer::new(request.length as usize)
        } else {
            std::mem::take(&mut request.data).into()
        };
        let short = request.direction == 1 && request.flags & 1 != 0;
        let zero = request.flags & 0x40 != 0;
        match self {
            Self::BulkIn(endpoint) => endpoint.submit_raw(buffer, short, zero),
            Self::BulkOut(endpoint) => endpoint.submit_raw(buffer, short, zero),
            Self::InterruptIn(endpoint) => endpoint.submit_raw(buffer, short, zero),
            Self::InterruptOut(endpoint) => endpoint.submit_raw(buffer, short, zero),
        }
    }
    fn poll(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<std::result::Result<RawCompletion, nusb::Error>> {
        match self {
            Self::BulkIn(endpoint) => endpoint.poll_complete_raw(cx),
            Self::BulkOut(endpoint) => endpoint.poll_complete_raw(cx),
            Self::InterruptIn(endpoint) => endpoint.poll_complete_raw(cx),
            Self::InterruptOut(endpoint) => endpoint.poll_complete_raw(cx),
        }
    }
    fn cancel(&mut self) -> bool {
        match self {
            Self::BulkIn(endpoint) => endpoint.cancel_one_raw(),
            Self::BulkOut(endpoint) => endpoint.cancel_one_raw(),
            Self::InterruptIn(endpoint) => endpoint.cancel_one_raw(),
            Self::InterruptOut(endpoint) => endpoint.cancel_one_raw(),
        }
    }
    async fn clear(&mut self) -> std::result::Result<(), nusb::Error> {
        match self {
            Self::BulkIn(endpoint) => endpoint.clear_halt().await,
            Self::BulkOut(endpoint) => endpoint.clear_halt().await,
            Self::InterruptIn(endpoint) => endpoint.clear_halt().await,
            Self::InterruptOut(endpoint) => endpoint.clear_halt().await,
        }
    }
}

struct Record {
    queued: Queued,
    cancel: bool,
    cancel_attempted: bool,
    reconfigured: bool,
    kind: u8,
}
struct Lane {
    interface: u8,
    endpoint: UsbEndpoint,
    active: Option<Record>,
    queue: std::collections::VecDeque<Record>,
}

#[derive(Clone, Copy)]
enum Change {
    Configuration(u8),
    Alternate(u8, u8),
    Clear(u8),
}

struct Barrier {
    record: Record,
    change: Change,
}
struct Configured {
    config: u8,
    interfaces: BTreeMap<u8, Interface>,
    error: Option<Error>,
    fatal: bool,
}
enum Update {
    None,
    Configuration(Configured),
    Alternate(u8, u8),
    Endpoint(u8, Lane),
}
struct SpecialResult {
    completion: Completion,
    update: Update,
}
enum ControlAction {
    Raw(RawControlTransfer),
    Special(Pin<Box<dyn Future<Output = SpecialResult> + Send>>),
}
struct ActiveControl {
    record: Record,
    action: ControlAction,
    change: Option<Change>,
}
enum Ready {
    Endpoint(u8, RawCompletion),
    ControlRaw(RawCompletion),
    ControlSpecial(SpecialResult),
}

struct Worker {
    slot: Arc<Slot>,
    signals: Arc<Signals>,
    events: mpsc::Sender<TransferEvent>,
    interfaces: BTreeMap<u8, Interface>,
    alternates: BTreeMap<u8, u8>,
    config: u8,
    lanes: BTreeMap<u8, Lane>,
    controls: std::collections::VecDeque<Record>,
    control: Option<ActiveControl>,
    barrier: Option<Barrier>,
    gone: bool,
    deferred: std::collections::VecDeque<Queued>,
    cancellation_intents: BTreeSet<TransferId>,
    owned_original: BTreeSet<u8>,
    configuration_changed: bool,
    halted: bool,
}

fn errno(error: &nusb::Error) -> i32 {
    error
        .os_error()
        .and_then(|value| i32::try_from(value).ok())
        .map(|value| -value)
        .unwrap_or(match error.kind() {
            nusb::ErrorKind::Busy => -16,
            nusb::ErrorKind::Disconnected => -19,
            nusb::ErrorKind::PermissionDenied => -13,
            nusb::ErrorKind::NotFound => -2,
            nusb::ErrorKind::Unsupported => -95,
            _ => -5,
        })
}

fn core_errno(error: &Error) -> i32 {
    match error {
        Error::Io(error) => -error.raw_os_error().unwrap_or(5),
        _ => -5,
    }
}

fn completion(raw: RawCompletion) -> Completion {
    Completion {
        status: raw.status,
        actual_length: raw.actual_len as u32,
        data: raw.data,
    }
}

fn cancel_won(accepted: bool, reconfigured: bool, status: i32) -> bool {
    accepted && !reconfigured && matches!(status, -2 | -104)
}

fn host_state_intent(setup: &[u8; 8]) -> bool {
    matches!((setup[0], setup[1]), (0, 9) | (1, 11) | (2, 1))
}

async fn release_all(interfaces: BTreeMap<u8, Interface>) -> Result<()> {
    let mut failed = false;
    // Every release is offloaded by nusb's Blocking wrapper and awaited. Do not
    // early-return and drop the remaining interfaces on a Tokio worker.
    for interface in interfaces.into_values() {
        if interface.release().await.is_err() {
            failed = true;
        }
    }
    if failed {
        Err(Error::State(
            "one or more physical interfaces were not released".into(),
        ))
    } else {
        Ok(())
    }
}

async fn acquire(slot: &Arc<Slot>, config: u8, original_alternates: bool) -> Configured {
    let mut interfaces = BTreeMap::new();
    let numbers = match observe::interface_numbers(&slot.usb, config) {
        Ok(numbers) => numbers,
        Err(error) => {
            return Configured {
                config,
                interfaces,
                error: Some(error),
                fatal: true,
            };
        }
    };
    if numbers.is_empty()
        && let Err(error) = super::safety::before_claim(slot, config, BTreeSet::new()).await
    {
        return Configured {
            config,
            interfaces,
            error: Some(error),
            fatal: true,
        };
    }
    for number in numbers {
        if let Err(error) =
            super::safety::before_claim(slot, config, interfaces.keys().copied().collect()).await
        {
            return Configured {
                config,
                interfaces,
                error: Some(error),
                fatal: true,
            };
        }
        let interface = match slot
            .usb
            .detach_and_claim_interface_no_reattach(number)
            .await
        {
            Ok(interface) => interface,
            Err(error) => {
                return Configured {
                    config,
                    interfaces,
                    error: Some(Error::Io(std::io::Error::from_raw_os_error(-errno(&error)))),
                    fatal: true,
                };
            }
        };
        if original_alternates {
            let alt = slot
                .source
                .interfaces
                .iter()
                .find(|interface| interface.number == number)
                .map_or(0, |interface| interface.alternate_setting);
            if alt != 0
                && let Err(error) = interface.set_alt_setting(alt).await
            {
                interfaces.insert(number, interface);
                return Configured {
                    config,
                    interfaces,
                    error: Some(Error::Io(std::io::Error::from_raw_os_error(-errno(&error)))),
                    fatal: true,
                };
            }
        }
        interfaces.insert(number, interface);
    }
    Configured {
        config,
        interfaces,
        error: None,
        fatal: false,
    }
}

impl Worker {
    fn endpoint(&mut self, address: u8) -> Result<(UsbEndpoint, u8, u8)> {
        let config = self
            .slot
            .usb
            .configurations()
            .find(|config| config.configuration_value() == self.config)
            .ok_or_else(|| Error::Unsupported("physical device is unconfigured".into()))?;
        let mut found = None;
        for interface in config.interface_alt_settings() {
            let number = interface.interface_number();
            if interface.alternate_setting() != self.alternates.get(&number).copied().unwrap_or(0) {
                continue;
            }
            for endpoint in interface
                .endpoints()
                .filter(|endpoint| endpoint.address() == address)
            {
                if found.is_some() {
                    return Err(Error::UnsafeDevice(
                        "physical endpoint belongs to multiple interfaces".into(),
                    ));
                }
                found = Some((number, endpoint.transfer_type()));
            }
        }
        let (number, kind) = found.ok_or_else(|| {
            Error::InvalidInput(
                "physical endpoint does not exist in selected alternate setting".into(),
            )
        })?;
        let interface = self
            .interfaces
            .get(&number)
            .ok_or_else(|| Error::State("physical endpoint interface is not claimed".into()))?;
        let endpoint = match (kind, address & 0x80 != 0) {
            (TransferType::Bulk, true) => UsbEndpoint::BulkIn(
                interface
                    .endpoint(address)
                    .map_err(|_| Error::State("could not open physical bulk IN endpoint".into()))?,
            ),
            (TransferType::Bulk, false) => {
                UsbEndpoint::BulkOut(interface.endpoint(address).map_err(|_| {
                    Error::State("could not open physical bulk OUT endpoint".into())
                })?)
            }
            (TransferType::Interrupt, true) => {
                UsbEndpoint::InterruptIn(interface.endpoint(address).map_err(|_| {
                    Error::State("could not open physical interrupt IN endpoint".into())
                })?)
            }
            (TransferType::Interrupt, false) => {
                UsbEndpoint::InterruptOut(interface.endpoint(address).map_err(|_| {
                    Error::State("could not open physical interrupt OUT endpoint".into())
                })?)
            }
            _ => {
                return Err(Error::Unsupported(
                    "physical ISO and non-EP0 control endpoints are unsupported".into(),
                ));
            }
        };
        Ok((
            endpoint,
            number,
            if kind == TransferType::Bulk { 2 } else { 3 },
        ))
    }

    fn enqueue(&mut self, queued: Queued) -> Result<()> {
        if queued.request.endpoint != 0
            && (self.barrier.is_some()
                || self
                    .control
                    .as_ref()
                    .is_some_and(|control| control.change.is_some())
                || self
                    .controls
                    .iter()
                    .any(|record| host_state_intent(&record.queued.request.setup)))
        {
            self.deferred.push_back(queued);
            return Ok(());
        }
        let kind = if queued.request.endpoint == 0 { 0 } else { 255 };
        let mut record = Record {
            queued,
            cancel: false,
            cancel_attempted: false,
            reconfigured: false,
            kind,
        };
        if self.signals.closing.load(Ordering::Acquire) || self.halted {
            return Ok(());
        }
        if record.queued.request.direction == 0 && record.queued.request.flags & 1 != 0 {
            return self.finish(record, error_completion(-22), false);
        }
        if record.queued.request.endpoint == 0 {
            self.controls.push_back(record);
            return Ok(());
        }
        let address = record.queued.request.endpoint as u8
            | if record.queued.request.direction == 1 {
                0x80
            } else {
                0
            };
        if !self.lanes.contains_key(&address) {
            match self.endpoint(address) {
                Ok((endpoint, interface, kind)) => {
                    record.kind = kind;
                    self.lanes.insert(
                        address,
                        Lane {
                            endpoint,
                            interface,
                            active: None,
                            queue: Default::default(),
                        },
                    );
                }
                Err(error) => {
                    return self.finish(
                        record,
                        error_completion(if matches!(error, Error::Unsupported(_)) {
                            -95
                        } else {
                            -32
                        }),
                        false,
                    );
                }
            }
        }
        if let Some(lane) = self.lanes.get_mut(&address) {
            record.kind = match lane.endpoint {
                UsbEndpoint::BulkIn(_) | UsbEndpoint::BulkOut(_) => 2,
                _ => 3,
            };
            lane.queue.push_back(record);
        }
        Ok(())
    }

    fn finish(&mut self, record: Record, mut result: Completion, raw: bool) -> Result<()> {
        let cancelled = cancel_won(record.cancel, record.reconfigured, result.status);
        if record.reconfigured && matches!(result.status, -2 | -104) {
            result.status = -108;
        }
        let direction = record.queued.request.direction;
        self.slot.update(|state| {
            state.usbfs_reaped += u64::from(raw);
            state.cancelled += u64::from(cancelled);
            match (record.kind, direction) {
                (0, _) => state.counters.controls += 1,
                (2, 1) => state.counters.bulk_in += 1,
                (2, _) => state.counters.bulk_out += 1,
                (3, 1) => state.counters.interrupt_in += 1,
                (3, _) => state.counters.interrupt_out += 1,
                _ => {}
            }
            state.counters.errors += u64::from(result.status != 0);
            if direction == 1 {
                state.counters.bytes_in += u64::from(result.actual_length);
            } else {
                state.counters.bytes_out += u64::from(result.actual_length);
            }
        })?;
        let id = record.queued.id;
        // The receiver can retire immediately on another runtime thread.
        // Release request-owned OUT storage before publishing its terminal.
        drop(record);
        if !self.signals.closing.load(Ordering::Acquire) {
            let event = if cancelled {
                TransferEvent::Cancelled {
                    id,
                    completion: result,
                }
            } else {
                TransferEvent::Completed {
                    id,
                    completion: result,
                }
            };
            if self.events.try_send(event).is_err() {
                self.signals.close();
            }
        }
        Ok(())
    }

    fn cancellations(&mut self) -> Result<()> {
        let requests = {
            let mut cancel = self
                .signals
                .cancel
                .lock()
                .map_err(|_| Error::State("physical cancel lock poisoned".into()))?;
            std::mem::take(&mut *cancel)
        };
        self.cancellation_intents.extend(requests);
        {
            let live = self.signals.permits()?;
            self.cancellation_intents.retain(|id| live.contains_key(id));
        }
        let requests = &self.cancellation_intents;
        let closing = self.signals.closing.load(Ordering::Acquire);
        let mut removed = Vec::new();
        for lane in self.lanes.values_mut() {
            let mut keep = Default::default();
            while let Some(mut record) = lane.queue.pop_front() {
                if closing || requests.contains(&record.queued.id) {
                    record.cancel = true;
                    removed.push(record);
                } else {
                    std::collections::VecDeque::push_back(&mut keep, record);
                }
            }
            lane.queue = keep;
            if let Some(record) = lane.active.as_mut()
                && (closing || requests.contains(&record.queued.id))
                && !record.cancel_attempted
            {
                record.cancel = lane.endpoint.cancel();
                record.cancel_attempted = true;
            }
        }
        let mut keep = Default::default();
        while let Some(mut record) = self.controls.pop_front() {
            if closing || requests.contains(&record.queued.id) {
                record.cancel = true;
                removed.push(record);
            } else {
                std::collections::VecDeque::push_back(&mut keep, record);
            }
        }
        self.controls = keep;
        let mut deferred = Default::default();
        while let Some(queued) = self.deferred.pop_front() {
            if closing || requests.contains(&queued.id) {
                removed.push(Record {
                    queued,
                    cancel: true,
                    cancel_attempted: true,
                    reconfigured: false,
                    kind: 255,
                });
            } else {
                std::collections::VecDeque::push_back(&mut deferred, queued);
            }
        }
        self.deferred = deferred;
        if self
            .barrier
            .as_ref()
            .is_some_and(|barrier| closing || requests.contains(&barrier.record.queued.id))
            && let Some(mut barrier) = self.barrier.take()
        {
            barrier.record.cancel = true;
            removed.push(barrier.record);
        }
        if let Some(control) = self.control.as_mut()
            && (closing || requests.contains(&control.record.queued.id))
            && !control.record.cancel_attempted
        {
            if let ControlAction::Raw(transfer) = &mut control.action {
                control.record.cancel = transfer.cancel();
            }
            control.record.cancel_attempted = true;
        }
        for record in removed {
            self.finish(record, error_completion(-104), false)?;
        }
        Ok(())
    }

    fn validate_control(&self, request: &Submit) -> std::result::Result<Option<Change>, i32> {
        let setup = request.setup;
        let value = u16::from_le_bytes([setup[2], setup[3]]);
        let index = u16::from_le_bytes([setup[4], setup[5]]);
        let length = u16::from_le_bytes([setup[6], setup[7]]);
        if request.flags & 0x40 != 0 || request.direction == 0 && request.flags & 1 != 0 {
            return Err(-22);
        }
        if request.direction == 0
            && (request.data.len() != usize::from(length) || request.length != u32::from(length))
        {
            return Err(-22);
        }
        if setup[0] & 0x60 == 0x60 || setup[0] & 0x1f > 3 {
            return Err(-95);
        }
        match setup[0] & 0x1f {
            1 if !self.interfaces.contains_key(&(index as u8)) => return Err(-32),
            2 if index as u8 & 0x70 != 0
                || index as u8 & 0x0f != 0 && !self.endpoint_exists(index as u8) =>
            {
                return Err(-32);
            }
            _ => {}
        }
        if setup[0] & 0x60 != 0 {
            return Ok(None);
        }
        match (setup[0], setup[1]) {
            (0, 5) => Err(-95), // VHCI owns the imported address; never move this physical device.
            (0, 9) if value <= 255 && index == 0 && length == 0 => {
                if value != 0
                    && !self
                        .slot
                        .usb
                        .configurations()
                        .any(|config| config.configuration_value() == value as u8)
                {
                    return Err(-32);
                }
                Ok(Some(Change::Configuration(value as u8)))
            }
            (1, 11) if value <= 255 && index <= 255 && length == 0 => {
                let exists = self
                    .slot
                    .usb
                    .configurations()
                    .find(|config| config.configuration_value() == self.config)
                    .is_some_and(|config| {
                        config.interface_alt_settings().any(|interface| {
                            interface.interface_number() == index as u8
                                && interface.alternate_setting() == value as u8
                        })
                    });
                if !exists {
                    return Err(-32);
                }
                Ok(Some(Change::Alternate(index as u8, value as u8)))
            }
            (2, 1) if value == 0 && index <= 255 && index & 0x0f != 0 && length == 0 => {
                Ok(Some(Change::Clear(index as u8)))
            }
            (0, 9) | (1, 11) => Err(-32),
            _ => Ok(None),
        }
    }

    fn endpoint_exists(&self, address: u8) -> bool {
        self.slot
            .usb
            .configurations()
            .find(|config| config.configuration_value() == self.config)
            .is_some_and(|config| {
                config.interface_alt_settings().any(|interface| {
                    interface.alternate_setting()
                        == self
                            .alternates
                            .get(&interface.interface_number())
                            .copied()
                            .unwrap_or(0)
                        && interface
                            .endpoints()
                            .any(|endpoint| endpoint.address() == address)
                })
            })
    }

    fn raw_control(&self, request: &Submit) -> std::result::Result<RawControlTransfer, i32> {
        let control_type = match (request.setup[0] >> 5) & 3 {
            0 => ControlType::Standard,
            1 => ControlType::Class,
            2 => ControlType::Vendor,
            _ => return Err(-95),
        };
        let recipient = match request.setup[0] & 0x1f {
            0 => Recipient::Device,
            1 => Recipient::Interface,
            2 => Recipient::Endpoint,
            3 => Recipient::Other,
            _ => return Err(-95),
        };
        let value = u16::from_le_bytes([request.setup[2], request.setup[3]]);
        let index = u16::from_le_bytes([request.setup[4], request.setup[5]]);
        if request.direction == 1 {
            let length = u32::from(u16::from_le_bytes([request.setup[6], request.setup[7]]))
                .min(request.length) as u16;
            Ok(self.slot.usb.control_in_raw(
                ControlIn {
                    control_type,
                    recipient,
                    request: request.setup[1],
                    value,
                    index,
                    length,
                },
                Duration::from_secs(5),
                request.flags & 1 != 0,
            ))
        } else {
            self.slot
                .usb
                .control_out_raw(
                    ControlOut {
                        control_type,
                        recipient,
                        request: request.setup[1],
                        value,
                        index,
                        data: &request.data,
                    },
                    Duration::from_secs(5),
                )
                .map_err(|error| errno(&error))
        }
    }

    fn affected(change: Change, address: u8, lane: &Lane) -> bool {
        match change {
            Change::Configuration(_) => true,
            Change::Alternate(interface, _) => lane.interface == interface,
            Change::Clear(endpoint) => address == endpoint,
        }
    }

    fn schedule(&mut self) -> Result<()> {
        if self.signals.closing.load(Ordering::Acquire) || self.halted {
            return Ok(());
        }
        if self.control.is_none()
            && self.barrier.is_none()
            && let Some(record) = self.controls.pop_front()
        {
            match self.validate_control(&record.queued.request) {
                Err(status) => self.finish(record, error_completion(status), false)?,
                Ok(Some(change)) => self.barrier = Some(Barrier { record, change }),
                Ok(None) => match self.raw_control(&record.queued.request) {
                    Ok(transfer) => {
                        self.control = Some(ActiveControl {
                            record,
                            action: ControlAction::Raw(transfer),
                            change: None,
                        })
                    }
                    Err(status) => self.finish(record, error_completion(status), false)?,
                },
            }
        }
        if self.barrier.is_none()
            && !self
                .control
                .as_ref()
                .is_some_and(|control| control.change.is_some())
        {
            let deferred = std::mem::take(&mut self.deferred);
            for queued in deferred {
                self.enqueue(queued)?;
            }
        }
        if let Some(barrier) = &self.barrier {
            let change = barrier.change;
            let mut flushed = Vec::new();
            for (&address, lane) in self.lanes.iter_mut() {
                if !Self::affected(change, address, lane) {
                    continue;
                }
                while let Some(record) = lane.queue.pop_front() {
                    flushed.push(record);
                }
                if let Some(record) = lane.active.as_mut()
                    && !record.reconfigured
                {
                    record.reconfigured = lane.endpoint.cancel();
                }
            }
            for record in flushed {
                self.finish(record, error_completion(-108), false)?;
            }
            if self.lanes.iter().all(|(&address, lane)| {
                !Self::affected(change, address, lane) || lane.active.is_none()
            }) && let Some(barrier) = self.barrier.take()
            {
                self.begin_change(barrier)?;
            }
        }
        let mut failures = Vec::new();
        for (&address, lane) in self.lanes.iter_mut() {
            if self
                .barrier
                .as_ref()
                .is_some_and(|barrier| Self::affected(barrier.change, address, lane))
            {
                continue;
            }
            if lane.active.is_none()
                && let Some(mut record) = lane.queue.pop_front()
            {
                match lane.endpoint.submit(&mut record.queued.request) {
                    Ok(()) => lane.active = Some(record),
                    Err(error) => failures.push((record, errno(&error))),
                }
            }
        }
        for (record, status) in failures {
            self.finish(record, error_completion(status), false)?;
        }
        Ok(())
    }

    fn begin_change(&mut self, barrier: Barrier) -> Result<()> {
        let action: Pin<Box<dyn Future<Output = SpecialResult> + Send>> = match barrier.change {
            Change::Configuration(config) => {
                self.configuration_changed = true;
                self.lanes.clear();
                let interfaces = std::mem::take(&mut self.interfaces);
                let slot = self.slot.clone();
                let last_confirmed = self.config;
                Box::pin(async move {
                    let mut failed = release_all(interfaces).await.err();
                    if failed.is_none()
                        && let Err(error) = slot.usb.set_configuration(config).await
                    {
                        failed = Some(Error::Io(std::io::Error::from_raw_os_error(-errno(&error))));
                    }
                    let observed = {
                        let paths = slot.paths.clone();
                        tokio::task::spawn_blocking(move || observe::configuration(&paths)).await
                    };
                    let actual = match observed {
                        Ok(Ok(value)) => value,
                        _ => {
                            return SpecialResult {
                                completion: error_completion(
                                    failed.as_ref().map_or(-5, core_errno),
                                ),
                                update: Update::Configuration(Configured {
                                    config: last_confirmed,
                                    interfaces: BTreeMap::new(),
                                    error: Some(Error::State(
                                        "physical configuration became unknown after operation"
                                            .into(),
                                    )),
                                    fatal: true,
                                }),
                            };
                        }
                    };
                    let mut acquired = acquire(&slot, actual, false).await;
                    let status = failed
                        .as_ref()
                        .or(acquired.error.as_ref())
                        .map_or(0, core_errno);
                    if acquired.error.is_none() {
                        acquired.error = failed;
                    }
                    SpecialResult {
                        completion: error_completion(status),
                        update: Update::Configuration(acquired),
                    }
                })
            }
            Change::Alternate(number, alt) => {
                self.lanes.retain(|_, lane| lane.interface != number);
                let interface = self
                    .interfaces
                    .get(&number)
                    .ok_or_else(|| {
                        Error::State("alternate-setting interface is not claimed".into())
                    })?
                    .clone();
                Box::pin(async move {
                    let status = interface
                        .set_alt_setting(alt)
                        .await
                        .as_ref()
                        .err()
                        .map_or(0, errno);
                    SpecialResult {
                        completion: error_completion(status),
                        update: if status == 0 {
                            Update::Alternate(number, alt)
                        } else {
                            Update::None
                        },
                    }
                })
            }
            Change::Clear(address) => {
                if !self.lanes.contains_key(&address) {
                    let (endpoint, interface, _) = self.endpoint(address)?;
                    self.lanes.insert(
                        address,
                        Lane {
                            endpoint,
                            interface,
                            active: None,
                            queue: Default::default(),
                        },
                    );
                }
                let mut lane = self
                    .lanes
                    .remove(&address)
                    .ok_or_else(|| Error::State("clear-halt endpoint disappeared".into()))?;
                Box::pin(async move {
                    let status = lane.endpoint.clear().await.as_ref().err().map_or(0, errno);
                    SpecialResult {
                        completion: error_completion(status),
                        update: Update::Endpoint(address, lane),
                    }
                })
            }
        };
        self.control = Some(ActiveControl {
            record: barrier.record,
            action: ControlAction::Special(action),
            change: Some(barrier.change),
        });
        Ok(())
    }

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Ready> {
        if let Some(control) = self.control.as_mut() {
            match &mut control.action {
                ControlAction::Raw(transfer) => {
                    if let Poll::Ready(result) = Pin::new(transfer).poll(cx) {
                        return Poll::Ready(Ready::ControlRaw(result));
                    }
                }
                ControlAction::Special(operation) => {
                    if let Poll::Ready(result) = operation.as_mut().poll(cx) {
                        return Poll::Ready(Ready::ControlSpecial(result));
                    }
                }
            }
        }
        for (&address, lane) in self.lanes.iter_mut() {
            if lane.active.is_none() {
                continue;
            }
            match lane.endpoint.poll(cx) {
                Poll::Ready(Ok(result)) => return Poll::Ready(Ready::Endpoint(address, result)),
                Poll::Ready(Err(error)) => {
                    return Poll::Ready(Ready::Endpoint(
                        address,
                        RawCompletion {
                            status: errno(&error),
                            actual_len: 0,
                            data: Vec::new(),
                        },
                    ));
                }
                Poll::Pending => {}
            }
        }
        Poll::Pending
    }

    fn completed(&mut self, ready: Ready) -> Result<()> {
        match ready {
            Ready::Endpoint(address, raw) => {
                let record = self
                    .lanes
                    .get_mut(&address)
                    .and_then(|lane| lane.active.take())
                    .ok_or_else(|| {
                        Error::State("physical endpoint completion has no owner".into())
                    })?;
                self.finish(record, completion(raw), true)
            }
            Ready::ControlRaw(raw) => {
                let control = self.control.take().ok_or_else(|| {
                    Error::State("physical control completion has no owner".into())
                })?;
                self.finish(control.record, completion(raw), true)
            }
            Ready::ControlSpecial(result) => {
                let mut fatal = false;
                match result.update {
                    Update::None => {}
                    Update::Alternate(number, alt) => {
                        self.alternates.insert(number, alt);
                    }
                    Update::Endpoint(address, lane) => {
                        self.lanes.insert(address, lane);
                    }
                    Update::Configuration(configured) => {
                        self.config = configured.config;
                        self.interfaces = configured.interfaces;
                        if self.config == self.slot.original_configuration {
                            self.owned_original.extend(self.interfaces.keys().copied());
                        }
                        self.alternates =
                            self.interfaces.keys().map(|&number| (number, 0)).collect();
                        self.slot
                            .update(|state| state.configuration = self.config)?;
                        fatal = configured.fatal;
                    }
                }
                let control = self.control.take().ok_or_else(|| {
                    Error::State("physical host-state completion has no owner".into())
                })?;
                if fatal {
                    // Preserve the real error on the wire before disconnecting.
                    // No further hardware work is scheduled in this uncertain state.
                    self.halted = true;
                    *self
                        .signals
                        .close_after_retire
                        .lock()
                        .map_err(|_| Error::State("physical terminal lock poisoned".into()))? =
                        Some(control.record.queued.id);
                    self.slot.update(|state| {
                        state.state = "quarantined".into();
                        state.cleanup_verified = false;
                    })?;
                }
                self.finish(control.record, result.completion, false)
            }
        }
    }

    fn active(&self) -> bool {
        self.control.is_some() || self.lanes.values().any(|lane| lane.active.is_some())
    }
    fn counts(&self, incoming: usize) -> Result<()> {
        let queued = incoming
            + self.controls.len()
            + self.deferred.len()
            + usize::from(self.barrier.is_some())
            + self
                .lanes
                .values()
                .map(|lane| lane.queue.len())
                .sum::<usize>();
        let inflight = self
            .lanes
            .values()
            .filter(|lane| lane.active.is_some())
            .count()
            + usize::from(
                self.control
                    .as_ref()
                    .is_some_and(|control| matches!(control.action, ControlAction::Raw(_))),
            );
        self.slot.update(|state| {
            state.queued_urbs = queued;
            state.usbfs_inflight = inflight;
        })
    }
}

async fn restore(worker: &mut Worker) -> Result<()> {
    worker.lanes.clear();
    let released = release_all(std::mem::take(&mut worker.interfaces)).await;
    if !observe::verify(&worker.slot).await? {
        worker.gone = true;
        return Ok(());
    }
    released?;
    let actual = {
        let paths = worker.slot.paths.clone();
        tokio::task::spawn_blocking(move || observe::configuration(&paths))
            .await
            .map_err(|_| Error::State("physical configuration observer failed".into()))??
    };
    super::safety::before_claim(&worker.slot, actual, BTreeSet::new()).await?;
    if actual != worker.slot.original_configuration {
        if !worker.configuration_changed || actual != worker.config {
            return Err(Error::State(
                "an external configuration change prevents safe physical restoration".into(),
            ));
        }
        worker
            .slot
            .usb
            .set_configuration(worker.slot.original_configuration)
            .await
            .map_err(|error| Error::Io(std::io::Error::from_raw_os_error(-errno(&error))))?;
    }
    // Restore nondefault alternates before attaching drivers, with all earlier
    // requests already terminal. A failed operation remains quarantined.
    for original in &worker.slot.source.interfaces {
        if original.alternate_setting != 0 && worker.owned_original.contains(&original.number) {
            let paths = worker.slot.paths.clone();
            let receipt = worker.slot.receipt.clone();
            let config = worker.slot.original_configuration;
            let number = original.number;
            let alternate = original.alternate_setting;
            let restored = tokio::task::spawn_blocking(move || {
                observe::alternate_matches(&paths, &receipt, config, number, alternate)
            })
            .await
            .map_err(|_| Error::State("physical alternate restoration observer failed".into()))??;
            if restored {
                continue;
            }
            let interface = worker
                .slot
                .usb
                .claim_interface(original.number)
                .await
                .map_err(|_| Error::State("could not reclaim original alternate setting".into()))?;
            let selected = interface.set_alt_setting(original.alternate_setting).await;
            let released = interface.release().await;
            selected
                .map_err(|_| Error::State("could not restore original alternate setting".into()))?;
            released
                .map_err(|_| Error::State("could not release restored alternate setting".into()))?;
        }
    }
    let originals = worker.slot.lock()?.original_drivers.clone();
    let numbers = originals
        .iter()
        .map(|driver| driver.interface)
        .collect::<Vec<_>>();
    let slot = worker.slot.clone();
    let owned_original = worker.owned_original.clone();
    tokio::task::spawn_blocking(move || {
        observe::reconnect_drivers(
            &slot.paths,
            &slot.receipt,
            slot.original_configuration,
            &originals,
            &owned_original,
            |number| {
                slot.usb.attach_kernel_driver(number).map_err(|_| {
                    Error::State("could not reattach the original physical driver".into())
                })
            },
        )
    })
    .await
    .map_err(|_| Error::State("physical driver restoration task failed".into()))??;
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        if !observe::verify(&worker.slot).await? {
            worker.gone = true;
            return Ok(());
        }
        let current = observe::observe_drivers(
            &worker.slot,
            worker.slot.original_configuration,
            numbers.clone(),
        )
        .await?;
        let matched = current == worker.slot.lock()?.original_drivers;
        worker
            .slot
            .update(|state| state.current_drivers = current)?;
        if matched && observe::validate_grant(&worker.slot).await? {
            break;
        }
        if Instant::now() >= deadline {
            return Err(Error::State(
                "original physical driver bindings were not restored".into(),
            ));
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    worker
        .slot
        .update(|state| state.configuration = worker.slot.original_configuration)
}

pub(super) async fn supervise(
    slot: Arc<Slot>,
    budget: Arc<Semaphore>,
    ready: oneshot::Sender<Result<PhysicalLease>>,
) {
    let (disconnect, disconnect_receipt) = oneshot::channel();
    let signals = Arc::new(Signals {
        closing: AtomicBool::new(false),
        network_closed: AtomicBool::new(false),
        cancel: Mutex::new(BTreeSet::new()),
        permits: Mutex::new(BTreeMap::new()),
        notify: Notify::new(),
        disconnect: Mutex::new(Some(disconnect)),
        close_after_retire: Mutex::new(None),
    });
    let finished = Arc::new(AtomicBool::new(false));
    let mut watchdogs = tokio::task::JoinSet::new();
    {
        let slot = slot.clone();
        let signals = signals.clone();
        watchdogs.spawn(async move {
            tokio::time::sleep(CLEANUP_DEADLINE).await;
            let _ = slot.update(|state| {
                if state.state == "claiming" {
                    state.state = "quarantined".into();
                    state.cleanup_verified = false;
                    signals.close();
                }
            });
        });
    }
    let (sender, mut receiver) = mpsc::channel(MAX_REQUESTS);
    let (events, incoming_events) = mpsc::channel(MAX_REQUESTS);
    let (cleanup, cleanup_receipt) = oneshot::channel();
    let mut worker = Worker {
        slot: slot.clone(),
        signals: signals.clone(),
        events,
        interfaces: BTreeMap::new(),
        alternates: BTreeMap::new(),
        config: slot.original_configuration,
        lanes: BTreeMap::new(),
        controls: Default::default(),
        control: None,
        barrier: None,
        gone: false,
        deferred: Default::default(),
        cancellation_intents: BTreeSet::new(),
        owned_original: BTreeSet::new(),
        configuration_changed: false,
        halted: false,
    };
    let validated = observe::validate_grant(&slot).await;
    if matches!(validated, Ok(false)) {
        let _ = slot.update(|state| {
            state.state = "gone".into();
            state.leased = false;
            state.cleanup_verified = true;
        });
        watchdogs.abort_all();
        while watchdogs.join_next().await.is_some() {}
        let _ = ready.send(Err(Error::InvalidInput(
            "physical generation is gone before IMPORT".into(),
        )));
        return;
    }
    if let Err(error) = validated {
        let _ = slot.update(|state| {
            state.state = "quarantined".into();
            state.cleanup_verified = false;
        });
        watchdogs.abort_all();
        while watchdogs.join_next().await.is_some() {}
        let _ = ready.send(Err(error));
        return;
    }
    let acquired = acquire(&slot, slot.original_configuration, true).await;
    watchdogs.abort_all();
    while watchdogs.join_next().await.is_some() {}
    worker.interfaces = acquired.interfaces;
    worker
        .owned_original
        .extend(worker.interfaces.keys().copied());
    worker.alternates = slot
        .source
        .interfaces
        .iter()
        .map(|interface| (interface.number, interface.alternate_setting))
        .collect();
    let mut claim_error = acquired.error;
    if slot.lock().is_ok_and(|state| state.state == "quarantined") {
        claim_error = Some(Error::Timeout(
            "physical claim exceeded its supervised deadline".into(),
        ));
    }
    if claim_error.is_none() {
        let current = observe::observe_drivers(
            &slot,
            worker.config,
            worker.interfaces.keys().copied().collect(),
        )
        .await;
        match current {
            Ok(current) => {
                let owned = current
                    .iter()
                    .all(|driver| driver.driver.as_deref() == Some("usbfs"));
                let _ = slot.update(|state| {
                    state.current_drivers = current;
                });
                if !owned {
                    claim_error = Some(Error::State(
                        "physical interface claims were not independently observed as usbfs".into(),
                    ));
                }
            }
            Err(error) => claim_error = Some(error),
        }
        if claim_error.is_none() {
            match observe::verify(&slot).await {
                Ok(true) => {
                    let _ = slot.update(|state| state.state = "leased".into());
                }
                _ => {
                    claim_error = Some(Error::State(
                        "physical generation changed after interface claims".into(),
                    ))
                }
            }
        }
    }
    let lease = PhysicalLease {
        slot: slot.clone(),
        signals: signals.clone(),
        sender,
        events: incoming_events,
        cleanup: cleanup_receipt,
        disconnect: disconnect_receipt,
        budget,
        cleanup_complete: None,
    };
    let claim_failed = claim_error.is_some();
    if let Some(error) = claim_error {
        let _ = ready.send(Err(error));
        drop(lease);
    } else if ready.send(Ok(lease)).is_err() {
        signals.close();
    }
    if claim_failed {
        signals.close();
    }
    let mut close_started = false;
    let mut next_observation = Instant::now() + Duration::from_millis(250);
    loop {
        while let Ok(queued) = receiver.try_recv() {
            if worker.enqueue(queued).is_err() {
                signals.close();
            }
        }
        // A completion already visible from REAPURB wins before a new cancel.
        while let Poll::Ready(ready) = poll_fn(|cx| Poll::Ready(worker.poll_ready(cx))).await {
            if worker.completed(ready).is_err() {
                signals.close();
            }
        }
        if receiver.is_closed() {
            signals.close();
        }
        if signals.closing.load(Ordering::Acquire) && !close_started {
            close_started = true;
            let _ = slot.update(|state| {
                if state.state != "quarantined" {
                    state.state = "closing".into();
                }
            });
            let slot = slot.clone();
            let finished = finished.clone();
            let signals = signals.clone();
            watchdogs.spawn(async move {
                tokio::time::sleep(CLEANUP_DEADLINE).await;
                if !finished.load(Ordering::Acquire) {
                    let _ = slot.update(|state| {
                        if !finished.load(Ordering::Acquire) {
                            state.state = "quarantined".into();
                            state.cleanup_verified = false;
                            signals.close();
                        }
                    });
                }
            });
        }
        if worker.cancellations().is_err() || worker.schedule().is_err() {
            signals.close();
        }
        let _ = worker.counts(receiver.len());
        if signals.closing.load(Ordering::Acquire) && !worker.active() {
            break;
        }
        if Instant::now() >= next_observation {
            match observe::verify(&slot).await {
                Ok(true) => {}
                Ok(false) => {
                    worker.gone = true;
                    signals.close();
                }
                Err(_) => signals.close(),
            }
            next_observation = Instant::now() + Duration::from_millis(250);
        }
        tokio::select! {
            ready = poll_fn(|cx| worker.poll_ready(cx)) => { if worker.completed(ready).is_err() { signals.close(); } },
            queued = receiver.recv() => match queued { Some(queued) => { if worker.enqueue(queued).is_err() { signals.close(); } }, None => signals.close() },
            _ = signals.notify.notified() => {},
            _ = tokio::time::sleep(Duration::from_millis(25)) => {},
        }
    }
    receiver.close();
    while receiver.try_recv().is_ok() {}
    let result = restore(&mut worker).await;
    // A completed response may still be in write_frame. Only begin_close/Drop
    // confirms that its network future and reply buffer no longer need budget.
    while !signals.network_closed.load(Ordering::Acquire) {
        let notification = signals.notify.notified();
        tokio::pin!(notification);
        notification.as_mut().enable();
        if signals.network_closed.load(Ordering::Acquire) {
            break;
        }
        notification.await;
    }
    finished.store(true, Ordering::Release);
    watchdogs.abort_all();
    while watchdogs.join_next().await.is_some() {}
    let _ = signals.permits().map(|mut permits| permits.clear());
    let mut quarantined = true;
    let _ = slot.update(|state| {
        finished.store(true, Ordering::Release);
        quarantined = state.state == "quarantined";
        let success = result.is_ok() && !quarantined;
        state.pending_urbs = 0;
        state.queued_urbs = 0;
        state.usbfs_inflight = 0;
        state.state = if worker.gone {
            "gone"
        } else if success {
            "available"
        } else {
            "quarantined"
        }
        .into();
        state.leased = !worker.gone && !success;
        state.cleanup_verified = result.is_ok();
    });
    let result = if quarantined && result.is_ok() {
        Err(Error::State(
            "physical ownership is quarantined after an uncertain operation or cleanup deadline; explicitly reauthorize before reuse".into(),
        ))
    } else {
        result
    };
    let _ = cleanup.send(result);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn physical_errno_and_partial_bytes_survive_completion_mapping() {
        for status in [-32, -71, -75, -19, -110, -2] {
            let result = completion(RawCompletion {
                status,
                actual_len: 3,
                data: vec![0x11, 0x22, 0x33],
            });
            assert_eq!(result.status, status);
            assert_eq!(result.actual_length, 3);
            assert_eq!(result.data, [0x11, 0x22, 0x33]);
        }
        assert!(!cancel_won(true, false, -110));
        assert!(!cancel_won(false, false, -2));
        assert!(!cancel_won(true, true, -104));
        assert!(!cancel_won(true, false, 0));
        assert!(cancel_won(true, false, -2));
        assert!(cancel_won(true, false, -104));
    }

    #[test]
    fn queued_host_state_intents_defer_later_endpoint_routing() {
        for (recipient, request) in [(0, 9), (1, 11), (2, 1)] {
            let mut setup = [0; 8];
            setup[0] = recipient;
            setup[1] = request;
            let mut pending = std::collections::VecDeque::from([setup]);
            // The earlier control has not started; its intent alone is a barrier.
            assert!(pending.iter().any(host_state_intent));
            pending.pop_front();
            assert!(!pending.iter().any(host_state_intent));
        }
        assert!(!host_state_intent(&[0x21, 1, 0, 0, 0, 0, 0, 0]));
        assert!(!host_state_intent(&[0x80, 6, 0, 1, 0, 0, 1, 0]));
    }
}
