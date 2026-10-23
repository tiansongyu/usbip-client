//! Bounded stable-key metadata preceding standard USB/IP on an authenticated stream.
//!
//! This module performs no TLS or endpoint authentication itself. Its caller must
//! supply the verified stream. Each catalog connection ends after DEVLIST; IMPORT
//! returns the same owned stream positioned exactly at the start of raw URBs.

use crate::{DEVLIST_REQUEST, RemoteDevice, VERSION, device_header, read_devlist, u16be, u32be};
use rustusb_core::{BusId, DeviceKey, Error, Result};
use serde::{
    Deserialize, Serialize,
    de::{self, DeserializeOwned, SeqAccess, Visitor},
};
use std::{
    collections::{HashMap, HashSet},
    fmt,
    time::Duration,
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

/// The only accepted secure metadata schema version.
pub const SCHEMA_VERSION: u32 = 1;
/// Maximum encoded request metadata, excluding its four-byte length.
pub const MAX_PRELUDE_BYTES: usize = 4096;
/// Maximum encoded stable-key catalog mapping, excluding its four-byte length.
pub const MAX_CATALOG_BYTES: usize = 8192;
/// Maximum registered devices visible on a secure catalog connection.
pub const MAX_CATALOG_ENTRIES: usize = 16;
/// One metadata or complete management exchange deadline, separate from TLS setup.
pub const MANAGEMENT_DEADLINE: Duration = Duration::from_secs(5);

/// Operation bound to the standard management frame following the metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SecureOperation {
    /// Retrieve a key-to-BusID mapping and matching standard DEVLIST snapshot.
    Catalog,
    /// Import only this stable key, with an independently checked standard BusID.
    Import {
        /// Exact configured backend-qualified key.
        device_key: DeviceKey,
    },
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum PreludeWire {
    Catalog {
        schema_version: u32,
    },
    Import {
        schema_version: u32,
        device_key: DeviceKey,
    },
}

/// One authenticated server mapping; the BusID is a current address, never the ACL key.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceMapping {
    /// Stable key authorized by the server's immutable policy.
    pub device_key: DeviceKey,
    /// Current standard USB/IP address of that exact registered slot.
    pub bus_id: BusId,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MappingWire {
    schema_version: u32,
    #[serde(deserialize_with = "bounded_mappings")]
    devices: Vec<DeviceMapping>,
}

fn bounded_mappings<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<DeviceMapping>, D::Error> {
    struct Mappings;
    impl<'de> Visitor<'de> for Mappings {
        type Value = Vec<DeviceMapping>;
        fn expecting(&self, output: &mut fmt::Formatter<'_>) -> fmt::Result {
            output.write_str("at most sixteen unique device mappings")
        }
        fn visit_seq<A: SeqAccess<'de>>(
            self,
            mut sequence: A,
        ) -> std::result::Result<Self::Value, A::Error> {
            let mut result = Vec::new();
            while result.len() < MAX_CATALOG_ENTRIES {
                let Some(value) = sequence.next_element()? else {
                    return Ok(result);
                };
                result.push(value);
            }
            if sequence.next_element::<de::IgnoredAny>()?.is_some() {
                return Err(de::Error::custom("catalog entry limit"));
            }
            Ok(result)
        }
    }
    deserializer.deserialize_seq(Mappings)
}

fn protocol(reason: &'static str) -> Error {
    Error::Protocol(reason.into())
}

async fn read_json<T: DeserializeOwned>(
    reader: &mut (impl AsyncRead + Unpin),
    maximum: usize,
) -> Result<T> {
    tokio::time::timeout(MANAGEMENT_DEADLINE, async {
        let mut length = [0_u8; 4];
        reader.read_exact(&mut length).await?;
        let length = u32::from_be_bytes(length) as usize;
        if length == 0 || length > maximum {
            return Err(protocol("secure metadata frame size"));
        }
        let mut bytes = vec![0; length];
        reader.read_exact(&mut bytes).await?;
        serde_json::from_slice(&bytes).map_err(|_| protocol("invalid secure metadata or fields"))
    })
    .await
    .map_err(|_| Error::Timeout("secure metadata read".into()))?
}

async fn write_json(
    writer: &mut (impl AsyncWrite + Unpin),
    value: &impl Serialize,
    maximum: usize,
) -> Result<()> {
    let bytes = serde_json::to_vec(value).map_err(|_| protocol("secure metadata encoding"))?;
    if bytes.is_empty() || bytes.len() > maximum {
        return Err(protocol("secure metadata frame size"));
    }
    tokio::time::timeout(MANAGEMENT_DEADLINE, async {
        writer
            .write_all(&(bytes.len() as u32).to_be_bytes())
            .await?;
        writer.write_all(&bytes).await?;
        writer.flush().await?;
        Ok(())
    })
    .await
    .map_err(|_| Error::Timeout("secure metadata write".into()))?
}

/// Read a strict, versioned, bounded prelude within one five-second deadline.
pub async fn read_prelude(reader: &mut (impl AsyncRead + Unpin)) -> Result<SecureOperation> {
    match read_json(reader, MAX_PRELUDE_BYTES).await? {
        PreludeWire::Catalog {
            schema_version: SCHEMA_VERSION,
        } => Ok(SecureOperation::Catalog),
        PreludeWire::Import {
            schema_version: SCHEMA_VERSION,
            device_key,
        } => Ok(SecureOperation::Import { device_key }),
        _ => Err(protocol("secure metadata version")),
    }
}

/// Encode and flush one bounded prelude; no standard management bytes are appended.
pub async fn write_prelude(
    writer: &mut (impl AsyncWrite + Unpin),
    operation: &SecureOperation,
) -> Result<()> {
    let wire = match operation {
        SecureOperation::Catalog => PreludeWire::Catalog {
            schema_version: SCHEMA_VERSION,
        },
        SecureOperation::Import { device_key } => PreludeWire::Import {
            schema_version: SCHEMA_VERSION,
            device_key: device_key.clone(),
        },
    };
    write_json(writer, &wire, MAX_PRELUDE_BYTES).await
}

fn validate_mappings(devices: &[DeviceMapping]) -> Result<()> {
    if devices.len() > MAX_CATALOG_ENTRIES {
        return Err(protocol("secure catalog entry limit"));
    }
    let mut keys = HashSet::new();
    let mut addresses = HashSet::new();
    for device in devices {
        if !keys.insert(&device.device_key) || !addresses.insert(&device.bus_id) {
            return Err(protocol("duplicate secure catalog key or address"));
        }
    }
    Ok(())
}

/// Read at most sixteen unique mappings from one bounded, versioned frame.
pub async fn read_catalog_mapping(
    reader: &mut (impl AsyncRead + Unpin),
) -> Result<Vec<DeviceMapping>> {
    let wire: MappingWire = read_json(reader, MAX_CATALOG_BYTES).await?;
    if wire.schema_version != SCHEMA_VERSION {
        return Err(protocol("secure catalog version"));
    }
    validate_mappings(&wire.devices)?;
    Ok(wire.devices)
}

/// Write and flush the stable-key mapping for the exact DEVLIST snapshot that follows.
pub async fn write_catalog_mapping(
    writer: &mut (impl AsyncWrite + Unpin),
    devices: &[DeviceMapping],
) -> Result<()> {
    validate_mappings(devices)?;
    write_json(
        writer,
        &MappingWire {
            schema_version: SCHEMA_VERSION,
            devices: devices.to_vec(),
        },
        MAX_CATALOG_BYTES,
    )
    .await
}

/// Matched catalog entry from the authenticated mapping and standard descriptor response.
#[derive(Debug, Clone, Serialize)]
pub struct CatalogDevice {
    /// Exact backend-qualified authorization key.
    pub device_key: DeviceKey,
    /// Standard USB/IP metadata parsed with the normal bounded wire parser.
    pub device: RemoteDevice,
}

fn validate_address(device: &RemoteDevice) -> Result<()> {
    if !(1..=65535).contains(&device.bus_number)
        || !(1..=255).contains(&device.device_number)
        || !(1..=6).contains(&device.speed)
        || device.path.len() >= 256
        || device.path.chars().any(char::is_control)
        || device.interfaces.len() > crate::MAX_INTERFACES
        || device.num_configurations == 0
    {
        return Err(protocol("secure remote device addressing or speed"));
    }
    Ok(())
}

/// Query mapping and DEVLIST on one already-authenticated owned stream, then close it.
/// A single five-second deadline covers the entire management exchange.
pub async fn discover<S: AsyncRead + AsyncWrite + Unpin>(
    mut stream: S,
) -> Result<Vec<CatalogDevice>> {
    tokio::time::timeout(MANAGEMENT_DEADLINE, async {
        write_prelude(&mut stream, &SecureOperation::Catalog).await?;
        stream.write_all(&DEVLIST_REQUEST).await?;
        stream.flush().await?;
        let mapping = read_catalog_mapping(&mut stream).await?;
        let devices = read_devlist(&mut stream).await?;
        if devices.len() != mapping.len() {
            return Err(protocol("secure catalog and DEVLIST differ"));
        }
        let mut keys: HashMap<_, _> = mapping
            .into_iter()
            .map(|entry| (entry.bus_id, entry.device_key))
            .collect();
        let mut result = Vec::with_capacity(devices.len());
        for device in devices {
            validate_address(&device)?;
            let device_key = keys
                .remove(&device.bus_id)
                .ok_or_else(|| protocol("secure catalog address mismatch"))?;
            result.push(CatalogDevice { device_key, device });
        }
        if !keys.is_empty() {
            return Err(protocol("unmatched secure catalog mapping"));
        }
        Ok(result)
    })
    .await
    .map_err(|_| Error::Timeout("secure USB/IP discovery".into()))?
}

/// Import a selected stable key on a new authenticated stream, returning that same stream.
/// Only the fixed IMPORT reply is consumed; any immediately following URB bytes remain.
/// A changed descriptor/address is rejected and requires explicit rediscovery.
pub async fn import_device<S: AsyncRead + AsyncWrite + Unpin>(
    mut stream: S,
    selected: &CatalogDevice,
) -> Result<(S, RemoteDevice)> {
    let device = tokio::time::timeout(MANAGEMENT_DEADLINE, async {
        validate_address(&selected.device)?;
        write_prelude(
            &mut stream,
            &SecureOperation::Import {
                device_key: selected.device_key.clone(),
            },
        )
        .await?;
        let mut request = [0_u8; 40];
        request[..8].copy_from_slice(&[0x01, 0x11, 0x80, 0x03, 0, 0, 0, 0]);
        let bus = selected.device.bus_id.as_str().as_bytes();
        request[8..8 + bus.len()].copy_from_slice(bus);
        stream.write_all(&request).await?;
        stream.flush().await?;
        let mut header = [0_u8; 8];
        stream.read_exact(&mut header).await?;
        if u16be(&header[..2]) != VERSION || u16be(&header[2..4]) != 3 || u32be(&header[4..]) != 0 {
            return Err(protocol("secure IMPORT refused or invalid reply"));
        }
        let mut bytes = [0_u8; 312];
        stream.read_exact(&mut bytes).await?;
        let (mut actual, count) = device_header(&bytes)?;
        let expected = &selected.device;
        if actual.bus_id != expected.bus_id
            || actual.path != expected.path
            || actual.bus_number != expected.bus_number
            || actual.device_number != expected.device_number
            || actual.speed != expected.speed
            || actual.vendor_id != expected.vendor_id
            || actual.product_id != expected.product_id
            || actual.device_version != expected.device_version
            || actual.class != expected.class
            || actual.subclass != expected.subclass
            || actual.protocol != expected.protocol
            || actual.configuration != expected.configuration
            || actual.num_configurations != expected.num_configurations
            || count != expected.interfaces.len()
        {
            return Err(protocol(
                "secure IMPORT changed the selected device metadata",
            ));
        }
        actual.interfaces = expected.interfaces.clone();
        Ok(actual)
    })
    .await
    .map_err(|_| Error::Timeout("secure USB/IP import".into()))??;
    Ok((stream, device))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::VirtualProfile;
    use tokio::io::{BufWriter, duplex};

    fn frame(bytes: &[u8]) -> Vec<u8> {
        let mut result = (bytes.len() as u32).to_be_bytes().to_vec();
        result.extend(bytes);
        result
    }

    fn base_device(bus: &str) -> [u8; 312] {
        let mut data = [0; 312];
        data[..4].copy_from_slice(b"/usb");
        data[256..256 + bus.len()].copy_from_slice(bus.as_bytes());
        data[288..292].copy_from_slice(&1_u32.to_be_bytes());
        data[292..296].copy_from_slice(&2_u32.to_be_bytes());
        data[296..300].copy_from_slice(&3_u32.to_be_bytes());
        data[300..302].copy_from_slice(&0x1234_u16.to_be_bytes());
        data[302..304].copy_from_slice(&0xabcd_u16.to_be_bytes());
        data[304..306].copy_from_slice(&0x0100_u16.to_be_bytes());
        data[309..312].copy_from_slice(&[1, 1, 1]);
        data
    }

    fn devlist(bus: &str) -> Vec<u8> {
        let mut data = vec![1, 0x11, 0, 5, 0, 0, 0, 0, 0, 0, 0, 1];
        data.extend(base_device(bus));
        data.extend([3, 1, 1, 0]);
        data
    }

    async fn selected() -> Result<CatalogDevice> {
        let mut devices = read_devlist(&mut devlist("1-2").as_slice()).await?;
        let device = devices
            .pop()
            .ok_or_else(|| protocol("missing test device"))?;
        Ok(CatalogDevice {
            device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
            device,
        })
    }

    #[tokio::test]
    async fn metadata_roundtrip_really_flushes_a_buffered_writer() -> Result<()> {
        let (left, mut right) = duplex(32);
        let mut buffered = BufWriter::with_capacity(8192, left);
        let operation = SecureOperation::Import {
            device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
        };
        let (sent, received) = tokio::join!(
            write_prelude(&mut buffered, &operation),
            read_prelude(&mut right)
        );
        sent?;
        assert_eq!(received?, operation);
        Ok(())
    }

    #[tokio::test]
    async fn prelude_rejects_lengths_truncation_versions_duplicates_and_unknown_fields() {
        for prefix in [0_u32, (MAX_PRELUDE_BYTES + 1) as u32, u32::MAX] {
            assert!(
                read_prelude(&mut prefix.to_be_bytes().as_slice())
                    .await
                    .is_err()
            );
        }
        for bytes in [
            r#"{"op":"catalog"}"#,
            r#"{"schema_version":2,"op":"catalog"}"#,
            r#"{"schema_version":1,"op":"catalog","principal":"forged"}"#,
            r#"{"schema_version":1,"schema_version":1,"op":"catalog"}"#,
            r#"{"schema_version":1,"op":"catalog","op":"catalog"}"#,
            r#"{"schema_version":1,"op":"import","device_key":"1-2"}"#,
            r#"{"schema_version":1,"op":"import","device_key":"virtual:unknown"}"#,
            r#"{"schema_version":1,"op":"catalog"}{}"#,
        ] {
            assert!(
                read_prelude(&mut frame(bytes.as_bytes()).as_slice())
                    .await
                    .is_err(),
                "invalid metadata accepted"
            );
        }
        let valid = frame(br#"{"schema_version":1,"op":"catalog"}"#);
        for length in 0..valid.len() {
            assert!(read_prelude(&mut &valid[..length]).await.is_err());
        }
    }

    #[tokio::test]
    async fn catalog_mapping_limits_and_unique_bindings_are_enforced() -> Result<()> {
        let devices = (1..=16)
            .map(|index| {
                Ok(DeviceMapping {
                    device_key: DeviceKey::physical(&format!("usb-{index:064x}"))?,
                    bus_id: BusId::new(format!("1-{index}"))?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        let mut output = Vec::new();
        write_catalog_mapping(&mut output, &devices).await?;
        assert_eq!(read_catalog_mapping(&mut output.as_slice()).await?, devices);
        assert!(
            write_catalog_mapping(&mut Vec::new(), &[devices[0].clone(), devices[0].clone()])
                .await
                .is_err()
        );
        for changed in [
            serde_json::json!({"schema_version":2,"devices":[]}),
            serde_json::json!({"schema_version":1,"devices":[],"extra":true}),
            serde_json::json!({"schema_version":1,"devices":[{"device_key":"virtual:hid","bus_id":"1-2","extra":true}]}),
            serde_json::json!({"schema_version":1,"devices":[{"device_key":"virtual:hid","bus_id":"1-2"},{"device_key":"virtual:cdc-acm","bus_id":"1-2"}]}),
            serde_json::json!({"schema_version":1,"devices":vec![devices[0].clone();17]}),
        ] {
            let bytes = serde_json::to_vec(&changed).map_err(|_| protocol("test JSON encoding"))?;
            assert!(
                read_catalog_mapping(&mut frame(&bytes).as_slice())
                    .await
                    .is_err()
            );
        }
        for length in [0_u32, (MAX_CATALOG_BYTES + 1) as u32, u32::MAX] {
            assert!(
                read_catalog_mapping(&mut length.to_be_bytes().as_slice())
                    .await
                    .is_err()
            );
        }
        Ok(())
    }

    #[tokio::test]
    async fn client_rejects_a_catalog_without_matching_standard_devices() -> Result<()> {
        for mapped_bus in ["1-2", "1-3"] {
            let (mut server, client) = duplex(2048);
            let mappings = vec![DeviceMapping {
                device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
                bus_id: BusId::new(mapped_bus)?,
            }];
            let serve = async {
                assert_eq!(read_prelude(&mut server).await?, SecureOperation::Catalog);
                let mut request = [0; 8];
                server.read_exact(&mut request).await?;
                assert_eq!(request, DEVLIST_REQUEST);
                write_catalog_mapping(&mut server, &mappings).await?;
                server.write_all(&devlist("1-2")).await?;
                server.flush().await?;
                Ok::<(), Error>(())
            };
            let (sent, result) = tokio::join!(serve, discover(client));
            sent?;
            if mapped_bus == "1-2" {
                assert_eq!(result?.len(), 1);
            } else {
                assert!(result.is_err());
            }
        }
        Ok(())
    }

    #[tokio::test]
    async fn import_consumes_exact_reply_and_preserves_the_same_streams_urb_bytes() -> Result<()> {
        let selected = selected().await?;
        let (mut server, client) = duplex(2048);
        let serve = async {
            assert_eq!(
                read_prelude(&mut server).await?,
                SecureOperation::Import {
                    device_key: selected.device_key.clone()
                }
            );
            let mut request = [0; 40];
            server.read_exact(&mut request).await?;
            assert_eq!(&request[..8], &[1, 0x11, 0x80, 3, 0, 0, 0, 0]);
            assert_eq!(&request[8..12], b"1-2\0");
            server.write_all(&[1, 0x11, 0, 3, 0, 0, 0, 0]).await?;
            server.write_all(&base_device("1-2")).await?;
            server.write_all(b"raw-urb").await?;
            server.flush().await?;
            Ok::<(), Error>(())
        };
        let consume = async {
            let (mut stream, actual) = import_device(client, &selected).await?;
            assert_eq!(actual.vendor_id, selected.device.vendor_id);
            assert_eq!(actual.interfaces.len(), 1);
            let mut bytes = [0; 7];
            stream.read_exact(&mut bytes).await?;
            assert_eq!(&bytes, b"raw-urb");
            Ok::<(), Error>(())
        };
        let (sent, result) = tokio::join!(serve, consume);
        sent?;
        result
    }

    #[tokio::test]
    async fn import_rejects_refusal_or_changed_selected_metadata() -> Result<()> {
        for refused in [true, false] {
            let selected = selected().await?;
            let (mut server, client) = duplex(2048);
            let serve = async {
                read_prelude(&mut server).await?;
                let mut request = [0; 40];
                server.read_exact(&mut request).await?;
                if refused {
                    server.write_all(&[1, 0x11, 0, 3, 0, 0, 0, 4]).await?;
                } else {
                    server.write_all(&[1, 0x11, 0, 3, 0, 0, 0, 0]).await?;
                    let mut bytes = base_device("1-2");
                    bytes[300] ^= 1;
                    server.write_all(&bytes).await?;
                }
                server.flush().await?;
                Ok::<(), Error>(())
            };
            let (sent, received) = tokio::join!(serve, import_device(client, &selected));
            sent?;
            assert!(received.is_err());
        }
        Ok(())
    }

    #[tokio::test]
    async fn silent_connected_metadata_peer_reaches_the_real_production_deadline() {
        let (_peer, mut reader) = duplex(32);
        assert!(matches!(
            read_prelude(&mut reader).await,
            Err(Error::Timeout(_))
        ));
    }
}
