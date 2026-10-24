//! Bounded Linux USB/IP DEVLIST client. URB transport remains in the native kernel backend.

pub mod connect;
pub mod secure;
pub mod server;

use rustusb_core::{BusId, Error, Peer, Result};
use serde::{Deserialize, Serialize};
use std::{collections::HashSet, time::Duration};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};

/// USB/IP protocol version used by Linux.
pub const VERSION: u16 = 0x0111;
/// Maximum device count accepted from an untrusted server.
pub const MAX_DEVICES: usize = 256;
/// Maximum interface count accepted per device.
pub const MAX_INTERFACES: usize = 32;
/// Canonical OP_REQ_DEVLIST header in network byte order.
pub const DEVLIST_REQUEST: [u8; 8] = [0x01, 0x11, 0x80, 0x05, 0, 0, 0, 0];

/// Remote interface class tuple advertised by DEVLIST, without endpoint descriptors.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RemoteInterface {
    /// Interface class.
    pub class: u8,
    /// Interface subclass.
    pub subclass: u8,
    /// Interface protocol.
    pub protocol: u8,
}

/// Remote device metadata, not a claim of tested compatibility or access permission.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RemoteDevice {
    /// Validated remote bus ID.
    pub bus_id: BusId,
    /// Remote kernel path, untrusted and never used for local filesystem access.
    pub path: String,
    /// Remote bus number.
    pub bus_number: u32,
    /// Remote USB address.
    pub device_number: u32,
    /// Linux USB speed enum (not Mbit/s).
    pub speed: u32,
    /// Vendor ID.
    pub vendor_id: u16,
    /// Product ID.
    pub product_id: u16,
    /// Device release BCD, not USB specification version.
    pub device_version: u16,
    /// Device class.
    pub class: u8,
    /// Device subclass.
    pub subclass: u8,
    /// Device protocol.
    pub protocol: u8,
    /// Configuration in the peer's published descriptor snapshot, not live local kernel state.
    pub configuration: u8,
    /// Number of advertised configurations.
    pub num_configurations: u8,
    /// Interface tuples in the current configuration.
    pub interfaces: Vec<RemoteInterface>,
}

fn u16be(bytes: &[u8]) -> u16 {
    u16::from_be_bytes([bytes[0], bytes[1]])
}
fn u32be(bytes: &[u8]) -> u32 {
    u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]])
}
fn wire_string(bytes: &[u8]) -> Result<String> {
    let end = bytes
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| Error::Protocol("unterminated string".into()))?;
    let s = std::str::from_utf8(&bytes[..end])
        .map_err(|_| Error::Protocol("invalid UTF-8 string".into()))?;
    if s.chars().any(char::is_control) {
        return Err(Error::Protocol("control character in string".into()));
    }
    Ok(s.to_owned())
}

fn device_header(data: &[u8; 312]) -> Result<(RemoteDevice, usize)> {
    let bus_id = BusId::new(wire_string(&data[256..288])?)
        .map_err(|_| Error::Protocol("invalid remote bus ID".into()))?;
    let count = usize::from(data[311]);
    if count > MAX_INTERFACES {
        return Err(Error::Protocol("interface count exceeds limit".into()));
    }
    Ok((
        RemoteDevice {
            bus_id,
            path: wire_string(&data[..256])?,
            bus_number: u32be(&data[288..292]),
            device_number: u32be(&data[292..296]),
            speed: u32be(&data[296..300]),
            vendor_id: u16be(&data[300..302]),
            product_id: u16be(&data[302..304]),
            device_version: u16be(&data[304..306]),
            class: data[306],
            subclass: data[307],
            protocol: data[308],
            configuration: data[309],
            num_configurations: data[310],
            interfaces: Vec::with_capacity(count),
        },
        count,
    ))
}

/// Parse one response with bounded allocations. Caller provides a deadline and closes on error.
pub async fn read_devlist(reader: &mut (impl AsyncRead + Unpin)) -> Result<Vec<RemoteDevice>> {
    let mut header = [0; 12];
    reader.read_exact(&mut header[..8]).await?;
    if u16be(&header[..2]) != VERSION || u16be(&header[2..4]) != 0x0005 {
        return Err(Error::Protocol("unexpected DEVLIST version/opcode".into()));
    }
    if u32be(&header[4..8]) != 0 {
        return Err(Error::Protocol("server refused DEVLIST".into()));
    }
    reader.read_exact(&mut header[8..12]).await?;
    let count = u32be(&header[8..12]) as usize;
    if count > MAX_DEVICES {
        return Err(Error::Protocol("device count exceeds limit".into()));
    }
    let mut result = Vec::with_capacity(count);
    let mut seen = HashSet::with_capacity(count);
    for _ in 0..count {
        let mut d = [0; 312];
        reader.read_exact(&mut d).await?;
        let (mut device, n) = device_header(&d)?;
        if !seen.insert(device.bus_id.clone()) {
            return Err(Error::Protocol("duplicate remote bus ID".into()));
        }
        for _ in 0..n {
            let mut intf = [0; 4];
            reader.read_exact(&mut intf).await?;
            device.interfaces.push(RemoteInterface {
                class: intf[0],
                subclass: intf[1],
                protocol: intf[2],
            });
        }
        result.push(device);
    }
    Ok(result)
}

/// Query a remote server. A single deadline covers DNS, connect, write and the full response.
/// Dropping closes all TCP candidates; started system lookup may outlive its waiter,
/// retaining its actual-work quota. This path requires an administrator-managed trusted network.
pub async fn discover(peer: &Peer, timeout: Duration) -> Result<Vec<RemoteDevice>> {
    tokio::time::timeout(timeout, async {
        let mut stream = connect::connect(peer).await?;
        stream.set_nodelay(true)?;
        stream.write_all(&DEVLIST_REQUEST).await?;
        read_devlist(&mut stream).await
    })
    .await
    .map_err(|_| Error::Timeout("USB/IP discovery".into()))?
}

#[cfg(test)]
mod tests {
    use super::*;
    fn response(count: u32) -> Vec<u8> {
        let mut data = vec![1, 0x11, 0, 5, 0, 0, 0, 0];
        data.extend(count.to_be_bytes());
        data
    }
    fn device() -> [u8; 312] {
        let mut d = [0; 312];
        d[..4].copy_from_slice(b"/usb");
        d[256..259].copy_from_slice(b"1-2");
        d[288..292].copy_from_slice(&1_u32.to_be_bytes());
        d[292..296].copy_from_slice(&2_u32.to_be_bytes());
        d[296..300].copy_from_slice(&3_u32.to_be_bytes());
        d[300..302].copy_from_slice(&0x1234_u16.to_be_bytes());
        d[302..304].copy_from_slice(&0xabcd_u16.to_be_bytes());
        d[311] = 1;
        d
    }
    #[tokio::test]
    async fn parses_network_byte_order_and_interfaces() -> Result<()> {
        let mut data = response(1);
        data.extend(device());
        data.extend([3, 1, 2, 0]);
        let got = read_devlist(&mut data.as_slice()).await?;
        assert_eq!(got[0].vendor_id, 0x1234);
        assert_eq!(got[0].product_id, 0xabcd);
        assert_eq!(got[0].interfaces[0].class, 3);
        assert_eq!(got[0].device_number, 2);
        Ok(())
    }

    #[tokio::test]
    async fn accepts_exact_device_and_interface_limits() -> Result<()> {
        let mut data = response(MAX_DEVICES as u32);
        for index in 0..MAX_DEVICES {
            let mut d = device();
            d[256..288].fill(0);
            let id = format!("{}-{}", 1 + index / 255, 1 + index % 255);
            d[256..256 + id.len()].copy_from_slice(id.as_bytes());
            d[311] = MAX_INTERFACES as u8;
            data.extend(d);
            for _ in 0..MAX_INTERFACES {
                data.extend([3, 1, 2, 0]);
            }
        }
        let devices = read_devlist(&mut data.as_slice()).await?;
        assert_eq!(devices.len(), MAX_DEVICES);
        assert!(devices.iter().all(|d| d.interfaces.len() == MAX_INTERFACES));
        Ok(())
    }
    #[tokio::test]
    async fn rejects_malicious_lengths_headers_strings_duplicates_and_truncation() {
        assert!(
            read_devlist(&mut response(u32::MAX).as_slice())
                .await
                .is_err()
        );
        let mut h = response(0);
        h[0] = 2;
        assert!(read_devlist(&mut h.as_slice()).await.is_err());
        let mut h = response(0);
        h[3] = 3;
        assert!(read_devlist(&mut h.as_slice()).await.is_err());
        let mut h = response(0);
        h[7] = 1;
        assert!(read_devlist(&mut h.as_slice()).await.is_err());
        let mut d = device();
        d[311] = 255;
        let mut data = response(1);
        data.extend(d);
        assert!(read_devlist(&mut data.as_slice()).await.is_err());
        let mut d = device();
        d[256..288].fill(b'a');
        let mut data = response(1);
        data.extend(d);
        data.extend([0; 4]);
        assert!(read_devlist(&mut data.as_slice()).await.is_err());
        let mut data = response(2);
        for _ in 0..2 {
            data.extend(device());
            data.extend([0; 4]);
        }
        assert!(read_devlist(&mut data.as_slice()).await.is_err());
        let mut data = response(1);
        data.extend(device());
        data.extend([0; 4]);
        for len in 0..data.len() {
            assert!(
                read_devlist(&mut data[..len].as_ref()).await.is_err(),
                "length {len}"
            );
        }
    }
    #[tokio::test]
    async fn actual_tcp_request_and_deadline() -> Result<()> {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let port = std::num::NonZeroU16::new(listener.local_addr()?.port())
            .ok_or_else(|| Error::State("zero port".into()))?;
        let server = tokio::spawn(async move {
            let (mut s, _) = listener.accept().await?;
            let mut req = [0; 8];
            s.read_exact(&mut req).await?;
            assert_eq!(req, DEVLIST_REQUEST);
            s.write_all(&response(0)).await
        });
        let peer = Peer {
            host: rustusb_core::Host::new("127.0.0.1")?,
            port,
        };
        assert!(discover(&peer, Duration::from_secs(1)).await?.is_empty());
        server.await.map_err(|e| Error::State(e.to_string()))??;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let port = std::num::NonZeroU16::new(listener.local_addr()?.port())
            .ok_or_else(|| Error::State("zero port".into()))?;
        let server = tokio::spawn(async move {
            let (_s, _) = listener.accept().await?;
            std::future::pending::<()>().await;
            Ok::<(), std::io::Error>(())
        });
        let peer = Peer {
            host: rustusb_core::Host::new("127.0.0.1")?,
            port,
        };
        assert!(matches!(
            discover(&peer, Duration::from_millis(20)).await,
            Err(Error::Timeout(_))
        ));
        server.abort();
        Ok(())
    }
}
