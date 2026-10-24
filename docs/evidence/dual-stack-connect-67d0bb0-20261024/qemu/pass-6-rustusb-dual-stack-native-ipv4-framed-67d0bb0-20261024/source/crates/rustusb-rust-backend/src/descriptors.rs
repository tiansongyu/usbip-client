//! Fixed, independently encoded USB descriptors for isolated virtual test devices.
//!
//! Layouts follow USB Chapter 9, HID 1.11 and CDC ACM. The HID report format is
//! adapted from jiegec/usbip commit 61938f8c48ed01c508faaacfa7cb3fc3810b5ae0
//! (`src/hid.rs`, MIT, Copyright (c) 2020-2025 Jiajie Chen); this profile adds
//! the standard keyboard LED output items. The complete MIT copyright and
//! permission notice is retained at `LICENSES/jiegec-usbip-MIT.txt`.

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Profile {
    Hid,
    Cdc,
}

// Standard 8-byte keyboard input and one-byte five-LED output, no report IDs.
pub(crate) const HID_REPORT: [u8; 63] = [
    0x05, 0x01, 0x09, 0x06, 0xa1, 0x01, 0x05, 0x07, 0x19, 0xe0, 0x29, 0xe7, 0x15, 0x00, 0x25, 0x01,
    0x75, 0x01, 0x95, 0x08, 0x81, 0x02, 0x95, 0x01, 0x75, 0x08, 0x81, 0x01, 0x95, 0x05, 0x75, 0x01,
    0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02, 0x95, 0x01, 0x75, 0x03, 0x91, 0x01, 0x95, 0x06,
    0x75, 0x08, 0x15, 0x00, 0x25, 0x65, 0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00, 0xc0,
];

pub(crate) const HID_CLASS: [u8; 9] = [9, 0x21, 0x11, 0x01, 0, 1, 0x22, 63, 0];

pub(crate) fn configuration(profile: Profile) -> Vec<u8> {
    match profile {
        Profile::Hid => [
            &[9, 2, 34, 0, 1, 1, 0, 0x80, 50][..],
            &[9, 4, 0, 0, 1, 3, 1, 1, 0],
            &HID_CLASS,
            &[7, 5, 0x81, 3, 8, 0, 10],
        ]
        .concat(),
        Profile::Cdc => vec![
            9, 2, 75, 0, 2, 1, 0, 0x80, 50, 8, 0x0b, 0, 2, 2, 2, 0,
            0, // IAD: interfaces 0 and 1, CDC ACM.
            9, 4, 0, 0, 1, 2, 2, 0, 0, 5, 0x24, 0, 0x20, 0x01, // CDC 1.20 Header.
            5, 0x24, 1, 0, 1, // Call Management, data interface 1.
            4, 0x24, 2, 0x06, // Line coding/control state and SEND_BREAK.
            5, 0x24, 6, 0, 1, // Union master 0, slave 1.
            7, 5, 0x81, 3, 16, 0, 10, // Serial-state notification.
            9, 4, 1, 0, 2, 0x0a, 0, 0, 0, 7, 5, 0x02, 2, 64, 0, 0, 7, 5, 0x82, 2, 64, 0, 0,
        ],
    }
}

pub(crate) fn descriptor(profile: Profile, kind: u8, index: u8, language: u16) -> Option<Vec<u8>> {
    match (kind, index) {
        (1, 0) => {
            let (class, subclass, protocol, product) = match profile {
                Profile::Hid => (0, 0, 0, 1),
                Profile::Cdc => (0xef, 2, 1, 2),
            };
            // 1234:0001/0002 are isolated test identifiers, not an allocated VID.
            Some(vec![
                18, 1, 0x00, 0x02, class, subclass, protocol, 64, 0x34, 0x12, product, 0, 0, 1, 1,
                2, 3, 1,
            ])
        }
        (2, 0) => Some(configuration(profile)),
        (3, 0) => Some(vec![4, 3, 9, 4]),
        (3, 1..=3) if language == 0x0409 || language == 0 => {
            let text = match index {
                1 => "RustUSB Virtual Test",
                2 if profile == Profile::Hid => "RustUSB Virtual Keyboard",
                2 => "RustUSB Virtual CDC ACM",
                3 if profile == Profile::Hid => "virtual-hid-1",
                3 => "virtual-cdc-2",
                _ => return None,
            };
            let mut bytes = vec![2 + (text.len() as u8) * 2, 3];
            for character in text.encode_utf16() {
                bytes.extend(character.to_le_bytes());
            }
            Some(bytes)
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_profiles_have_exact_lengths_and_endpoint_layouts() {
        for (profile, length, interfaces) in [(Profile::Hid, 34, 1), (Profile::Cdc, 75, 2)] {
            let bytes = configuration(profile);
            assert_eq!(bytes.len(), length);
            assert_eq!(
                usize::from(u16::from_le_bytes([bytes[2], bytes[3]])),
                length
            );
            assert_eq!(bytes[4], interfaces);
            let mut offset = 0;
            let mut actual_interfaces = 0;
            let mut endpoints = Vec::new();
            while offset < bytes.len() {
                let size = usize::from(bytes[offset]);
                assert!(size >= 2 && offset + size <= bytes.len());
                if bytes[offset + 1] == 4 {
                    actual_interfaces += 1;
                }
                if bytes[offset + 1] == 5 {
                    endpoints.push(bytes[offset + 2]);
                }
                offset += size;
            }
            assert_eq!(actual_interfaces, interfaces);
            assert_eq!(
                endpoints,
                if profile == Profile::Hid {
                    vec![0x81]
                } else {
                    vec![0x81, 0x02, 0x82]
                }
            );
        }
        assert_eq!(HID_REPORT.len(), usize::from(HID_CLASS[7]));
    }
}
