//! Shared bounded standard management framing for Rust and Native TLS servers.
//! URB execution remains in the selected backend.

use crate::{MAX_INTERFACES, RemoteDevice, VERSION, u32be};
use rustusb_core::{BusId, Error, Result};
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncReadExt};

/// A standard USB/IP management request, after any authenticated secure prelude.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Management {
    /// Retrieve the authorized device snapshot.
    Devlist,
    /// Import this validated current address; stable-key authorization is separate.
    Import(BusId),
}

fn protocol(message: &str) -> Error {
    Error::Protocol(message.into())
}

/// Read exactly one DEVLIST/IMPORT request within five seconds.
/// Version, status, BusID grammar and every IMPORT padding byte are validated.
pub async fn read_management(reader: &mut (impl AsyncRead + Unpin)) -> Result<Management> {
    tokio::time::timeout(Duration::from_secs(5), async {
        let mut header = [0; 8];
        reader.read_exact(&mut header).await?;
        if u16::from_be_bytes([header[0], header[1]]) != VERSION || u32be(&header[4..]) != 0 {
            return Err(protocol("invalid management version or status"));
        }
        match u16::from_be_bytes([header[2], header[3]]) {
            0x8005 => Ok(Management::Devlist),
            0x8003 => {
                let mut field = [0; 32];
                reader.read_exact(&mut field).await?;
                let end = field
                    .iter()
                    .position(|byte| *byte == 0)
                    .ok_or_else(|| protocol("IMPORT bus ID is not terminated"))?;
                if field[end..].iter().any(|byte| *byte != 0) {
                    return Err(protocol("IMPORT bus ID padding is not zero"));
                }
                let bus_id = std::str::from_utf8(&field[..end])
                    .map_err(|_| protocol("IMPORT bus ID is not UTF-8"))?;
                let bus_id = BusId::new(bus_id).map_err(|_| protocol("invalid IMPORT bus ID"))?;
                Ok(Management::Import(bus_id))
            }
            _ => Err(protocol("management phase requires DEVLIST or IMPORT")),
        }
    })
    .await
    .map_err(|_| Error::Timeout("USB/IP management handshake".into()))?
}

fn put_string(output: &mut Vec<u8>, value: &str, width: usize) -> Result<()> {
    if value.len() >= width || value.chars().any(char::is_control) {
        return Err(protocol("local device string exceeds the wire field"));
    }
    output.extend_from_slice(value.as_bytes());
    output.resize(output.len() + width - value.len(), 0);
    Ok(())
}

fn device_bytes(output: &mut Vec<u8>, device: &RemoteDevice, interfaces: bool) -> Result<()> {
    if device.interfaces.len() > MAX_INTERFACES {
        return Err(protocol("local interface count exceeds limit"));
    }
    put_string(output, &device.path, 256)?;
    put_string(output, device.bus_id.as_str(), 32)?;
    for value in [device.bus_number, device.device_number, device.speed] {
        output.extend_from_slice(&value.to_be_bytes());
    }
    for value in [device.vendor_id, device.product_id, device.device_version] {
        output.extend_from_slice(&value.to_be_bytes());
    }
    output.extend_from_slice(&[
        device.class,
        device.subclass,
        device.protocol,
        device.configuration,
        device.num_configurations,
        device.interfaces.len() as u8,
    ]);
    if interfaces {
        for interface in &device.interfaces {
            output.extend_from_slice(&[interface.class, interface.subclass, interface.protocol, 0]);
        }
    }
    Ok(())
}

fn header(opcode: u16, status: u32) -> Vec<u8> {
    let mut output = Vec::new();
    output.extend_from_slice(&VERSION.to_be_bytes());
    output.extend_from_slice(&opcode.to_be_bytes());
    output.extend_from_slice(&status.to_be_bytes());
    output
}

/// Encode the standard DEVLIST snapshot for at most sixteen registered devices.
/// The caller must separately write/flush within its operation deadline.
pub fn devlist_reply(devices: &[RemoteDevice]) -> Result<Vec<u8>> {
    if devices.len() > 16 {
        return Err(protocol("local device count exceeds server limit"));
    }
    let mut output = header(5, 0);
    output.extend_from_slice(&(devices.len() as u32).to_be_bytes());
    for device in devices {
        device_bytes(&mut output, device, true)?;
    }
    Ok(output)
}

/// Encode a standard IMPORT response without an interface-tuple tail.
/// A successful response requires metadata; raw URBs start after its complete flush.
pub fn import_reply(status: u32, device: Option<&RemoteDevice>) -> Result<Vec<u8>> {
    let mut output = header(3, status);
    if status == 0 {
        device_bytes(
            &mut output,
            device.ok_or_else(|| protocol("successful IMPORT lacks a device"))?,
            false,
        )?;
    }
    Ok(output)
}
