//! Bounded USB/IP framing, independent of any device implementation.

use rustusb_core::{Error, Result};
pub(crate) use rustusb_protocol::server::{devlist_reply, import_reply};
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncReadExt};

pub(crate) const FRAME_DEADLINE: Duration = Duration::from_secs(5);
pub(crate) const MAX_TRANSFER: usize = 65_536;
// Only flags supported by the virtual non-ISO profiles. NO_TRANSFER_DMA_MAP
// and DIR_IN do not confer any physical memory or DMA capability here.
pub(crate) const KNOWN_FLAGS: u32 = 0x0000_03c5;

#[derive(Debug)]
pub(crate) enum Management {
    Devlist,
    Import(String),
}

/// Fully bounded request, with USB SETUP bytes preserved in their native endian order.
#[derive(Debug, Clone)]
pub(crate) struct Submit {
    pub seqnum: u32,
    pub devid: u32,
    pub direction: u32,
    pub endpoint: u32,
    pub flags: u32,
    pub length: u32,
    pub start_frame: i32,
    pub num_packets: i32,
    pub interval: i32,
    pub setup: [u8; 8],
    pub data: Vec<u8>,
}

/// Device completion. OUT transfers carry an actual length but no response payload.
#[derive(Debug, Clone)]
pub(crate) struct Completion {
    pub status: i32,
    pub actual_length: u32,
    pub data: Vec<u8>,
}

/// Response metadata deliberately excludes the owned OUT transfer buffer.
#[derive(Debug, Clone, Copy)]
pub(crate) struct ReplyContext {
    pub seqnum: u32,
    pub direction: u32,
    pub endpoint: u32,
    pub flags: u32,
    pub length: u32,
    pub setup: [u8; 8],
}

impl From<&Submit> for ReplyContext {
    fn from(request: &Submit) -> Self {
        Self {
            seqnum: request.seqnum,
            direction: request.direction,
            endpoint: request.endpoint,
            flags: request.flags,
            length: request.length,
            setup: request.setup,
        }
    }
}

#[derive(Debug)]
pub(crate) enum DataCommand {
    Submit(Submit),
    Unlink { seqnum: u32, target: u32 },
}

fn protocol(message: &str) -> Error {
    Error::Protocol(message.into())
}

fn u32be(bytes: &[u8]) -> u32 {
    u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]])
}

pub(crate) async fn read_management(reader: &mut (impl AsyncRead + Unpin)) -> Result<Management> {
    Ok(
        match rustusb_protocol::server::read_management(reader).await? {
            rustusb_protocol::server::Management::Devlist => Management::Devlist,
            rustusb_protocol::server::Management::Import(bus_id) => {
                Management::Import(bus_id.to_string())
            }
        },
    )
}

/// Wait indefinitely for an idle imported peer, then bound the whole partial frame.
/// This is called by a dedicated reader task, never cancelled and resumed mid-frame.
pub(crate) async fn read_data(
    reader: &mut (impl AsyncRead + Unpin),
    expected_devid: u32,
) -> Result<Option<DataCommand>> {
    let mut header = [0; 48];
    if reader.read(&mut header[..1]).await? == 0 {
        return Ok(None);
    }
    tokio::time::timeout(FRAME_DEADLINE, async {
        reader.read_exact(&mut header[1..4]).await?;
        let command = u32be(&header[..4]);
        if !matches!(command, 1 | 2) {
            return Err(protocol("imported phase requires SUBMIT or UNLINK"));
        }
        reader.read_exact(&mut header[4..]).await?;
        let seqnum = u32be(&header[4..8]);
        let devid = u32be(&header[8..12]);
        let direction = u32be(&header[12..16]);
        let endpoint = u32be(&header[16..20]);
        if devid != expected_devid || direction > 1 || endpoint > 15 {
            return Err(protocol("URB device, direction or endpoint is invalid"));
        }
        match command {
            1 => {
                let length = u32be(&header[24..28]);
                let start_frame = u32be(&header[28..32]) as i32;
                let num_packets = u32be(&header[32..36]) as i32;
                let interval = u32be(&header[36..40]) as i32;
                let flags = u32be(&header[20..24]);
                // Unsupported execution or physical DMA semantics fail before allocation.
                if length > MAX_TRANSFER as u32
                    || !matches!(num_packets, 0 | -1)
                    || !matches!(start_frame, 0 | -1)
                    || interval < 0
                    || flags & !KNOWN_FLAGS != 0
                    || direction == 0 && flags & 0x200 != 0
                {
                    return Err(protocol(
                        "unsupported ISO metadata or transfer resource limit",
                    ));
                }
                let mut setup = [0; 8];
                setup.copy_from_slice(&header[40..48]);
                if endpoint == 0 && u32::from(setup[0] >> 7) != direction {
                    return Err(protocol("control direction differs from USB SETUP"));
                }
                let mut data = if direction == 0 {
                    vec![0; length as usize]
                } else {
                    Vec::new()
                };
                if direction == 0 {
                    reader.read_exact(&mut data).await?;
                }
                Ok(Some(DataCommand::Submit(Submit {
                    seqnum,
                    devid,
                    direction,
                    endpoint,
                    flags,
                    length,
                    start_frame,
                    num_packets,
                    interval,
                    setup,
                    data,
                })))
            }
            2 => {
                if direction != 0 || endpoint != 0 || header[24..].iter().any(|byte| *byte != 0) {
                    return Err(protocol("UNLINK direction, endpoint or padding is invalid"));
                }
                Ok(Some(DataCommand::Unlink {
                    seqnum,
                    target: u32be(&header[20..24]),
                }))
            }
            _ => Err(protocol("imported phase requires SUBMIT or UNLINK")),
        }
    })
    .await
    .map_err(|_| Error::Timeout("USB/IP partial data frame".into()))?
}

pub(crate) fn submit_reply(request: &ReplyContext, mut completion: Completion) -> Result<Vec<u8>> {
    if completion.status > 0 {
        return Err(protocol(
            "device completion must use zero or negative Linux errno",
        ));
    }
    let limit = if request.endpoint == 0 {
        request.length.min(u32::from(u16::from_le_bytes([
            request.setup[6],
            request.setup[7],
        ])))
    } else {
        request.length
    };
    if request.direction == 1 {
        if completion.actual_length > limit
            || completion.actual_length as usize != completion.data.len()
        {
            return Err(protocol(
                "device returned an invalid IN completion; data cannot be silently truncated",
            ));
        }
        if completion.status == 0
            && request.flags & 1 != 0
            && completion.actual_length < request.length
        {
            completion.status = -121; // EREMOTEIO: the caller prohibited a short IN transfer.
        }
    } else if !completion.data.is_empty() || completion.actual_length > request.length {
        return Err(protocol("device returned an invalid OUT completion"));
    }
    let mut output = vec![0; 48];
    output[..4].copy_from_slice(&3_u32.to_be_bytes());
    output[4..8].copy_from_slice(&request.seqnum.to_be_bytes());
    output[20..24].copy_from_slice(&completion.status.to_be_bytes());
    output[24..28].copy_from_slice(&completion.actual_length.to_be_bytes());
    output.extend_from_slice(&completion.data);
    Ok(output)
}

pub(crate) fn unlink_reply(seqnum: u32, status: i32) -> [u8; 48] {
    let mut output = [0; 48];
    output[..4].copy_from_slice(&4_u32.to_be_bytes());
    output[4..8].copy_from_slice(&seqnum.to_be_bytes());
    output[20..24].copy_from_slice(&status.to_be_bytes());
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncWriteExt;

    fn frame(length: u32, packets: i32) -> [u8; 48] {
        let mut frame = [0; 48];
        frame[..4].copy_from_slice(&1_u32.to_be_bytes());
        frame[8..12].copy_from_slice(&0x10001_u32.to_be_bytes());
        frame[12..16].copy_from_slice(&1_u32.to_be_bytes());
        frame[16..20].copy_from_slice(&1_u32.to_be_bytes());
        frame[24..28].copy_from_slice(&length.to_be_bytes());
        frame[32..36].copy_from_slice(&packets.to_be_bytes());
        frame
    }

    #[tokio::test]
    async fn rejects_resource_and_phase_fields_before_reading_payload() {
        for (length, packets) in [(65_537, 0), (u32::MAX, 0), (0, 1), (0, i32::MAX)] {
            assert!(
                read_data(&mut frame(length, packets).as_slice(), 0x10001)
                    .await
                    .is_err()
            );
        }
        let mut invalid = frame(8, -1);
        invalid[12..16].copy_from_slice(&2_u32.to_be_bytes());
        assert!(read_data(&mut invalid.as_slice(), 0x10001).await.is_err());
        assert!(
            read_data(&mut frame(8, -1).as_slice(), 0x10002)
                .await
                .is_err()
        );
        for size in 1..48 {
            assert!(
                read_data(&mut frame(8, -1)[..size].as_ref(), 0x10001)
                    .await
                    .is_err()
            );
        }
    }

    #[tokio::test]
    async fn complete_idle_connection_waits_but_partial_frame_has_a_single_owner() -> Result<()> {
        let (mut sender, mut receiver) = tokio::io::duplex(128);
        assert!(
            tokio::time::timeout(Duration::from_millis(20), read_data(&mut receiver, 0x10001))
                .await
                .is_err()
        );
        sender.write_all(&frame(8, -1)).await?;
        assert!(matches!(
            read_data(&mut receiver, 0x10001).await?,
            Some(DataCommand::Submit(_))
        ));
        Ok(())
    }

    #[test]
    fn inconsistent_or_overlong_completion_is_rejected_without_silent_truncation() {
        let context = ReplyContext {
            seqnum: 1,
            direction: 1,
            endpoint: 2,
            flags: 0,
            length: 1,
            setup: [0; 8],
        };
        for completion in [
            Completion {
                status: 0,
                actual_length: 2,
                data: vec![1, 2],
            },
            Completion {
                status: -75,
                actual_length: 1,
                data: vec![1, 2],
            },
            Completion {
                status: 1,
                actual_length: 0,
                data: vec![],
            },
        ] {
            assert!(submit_reply(&context, completion).is_err());
        }
    }
}
