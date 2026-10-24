//! Synchronous, explicitly authorized CONNECT-only original-driver restoration.
//!
//! The helper scheduler owns policy, handoff exclusion, the actual mutation
//! guard and journal retirement. No function here changes configuration,
//! alternate settings, claims, or the saved baseline.

use crate::{
    journal::{Baseline, Record, RecoveryAuthorization},
    physical_lifecycle,
};
use rustusb_core::{Error, Result};
use std::os::fd::AsFd;

/// Require the original boot and distinguish ended from live/unknown owners.
/// A matching process tuple, including a zombie, is not considered ended.
pub(crate) fn owner_ended(record: &Record) -> Result<bool> {
    physical_lifecycle::owner_ended_same_boot(record)
}

fn scope(original: &Baseline, current: &Baseline) -> Result<()> {
    if original.device_id != current.device_id
        || original.bus_id != current.bus_id
        || original.receipt != current.receipt
        || original.configuration != current.configuration
        || original.interfaces.len() != current.interfaces.len()
    {
        return Err(Error::State(
            "automatic physical recovery scope changed; original record retained".into(),
        ));
    }
    for (expected, actual) in original.interfaces.iter().zip(&current.interfaces) {
        if expected.number != actual.number
            || expected.alternate != actual.alternate
            || matches!(actual.driver.as_deref(), Some("usbfs" | "usbip-host"))
            || (actual.driver != expected.driver
                && !(expected.driver.is_some() && actual.driver.is_none()))
        {
            return Err(Error::State(
                "automatic physical recovery found a changed interface, alternate or external driver; record retained".into(),
            ));
        }
    }
    Ok(())
}

// The same sequence runs in production and callback fixtures. Both observations
// cover every interface; a successful sibling probe makes later CONNECTs skip.
fn restore_drivers(
    original: &Baseline,
    mut observe: impl FnMut() -> Result<Baseline>,
    mut connect: impl FnMut(u8) -> Result<()>,
) -> Result<()> {
    scope(original, &observe()?)?;
    for expected in &original.interfaces {
        let before = observe()?;
        scope(original, &before)?;
        let actual = before
            .interfaces
            .iter()
            .find(|interface| interface.number == expected.number)
            .ok_or_else(|| Error::State("original physical interface disappeared".into()))?;
        if actual.driver == expected.driver {
            continue;
        }

        let connected = connect(expected.number);
        let verified = observe().and_then(|after| {
            scope(original, &after)?;
            if !after.interfaces.iter().any(|interface| {
                interface.number == expected.number && interface.driver == expected.driver
            }) {
                return Err(Error::State(
                    "CONNECT did not restore the original physical driver; record retained".into(),
                ));
            }
            Ok(())
        });
        match (connected, verified) {
            (Ok(()), Ok(())) => {}
            (Err(error), Ok(())) => {
                let errno = match error {
                    Error::Io(error) => error.raw_os_error(),
                    _ => None,
                };
                tracing::warn!(
                    interface = expected.number,
                    errno,
                    "CONNECT reported failure but the original physical driver was independently observed"
                );
            }
            (Err(error), Err(_)) | (Ok(()), Err(error)) => return Err(error),
        }
    }
    if observe()? != *original {
        return Err(Error::State(
            "complete original physical baseline did not converge; record retained".into(),
        ));
    }
    Ok(())
}

/// Restore only missing original drivers within the exact saved scope.
///
/// The caller must authorize current policy and hold the helper mutation guard
/// in the actual blocking task for this entire call. This function owns and
/// drops one independent B, then verifies the full original state twice. It
/// never retires a journal record or acknowledges a gone generation.
pub(crate) fn restore(record: &Record) -> Result<()> {
    if record.recovery != RecoveryAuthorization::ConnectOriginalDrivers {
        return Err(Error::Permission(
            "physical grant does not authorize automatic original-driver recovery".into(),
        ));
    }
    if !owner_ended(record)? {
        return Err(Error::State(
            "original physical owner still exists; automatic recovery deferred".into(),
        ));
    }
    let original = &record.baseline;
    let (current, device) = physical_lifecycle::observe_current_device(original)?;
    scope(original, &current)?;
    if current == *original {
        physical_lifecycle::verify(record, false)?;
        return Ok(());
    }

    let independent = crate::physical::open_current(device)?;
    if independent.receipt != original.receipt {
        return Err(Error::State(
            "independent physical recovery descriptor has a different generation".into(),
        ));
    }
    let result = restore_drivers(
        original,
        || physical_lifecycle::observe_current(original),
        |number| nusb::connect_kernel_driver(independent.fd.as_fd(), number).map_err(Error::Io),
    );
    // Even failure must close this task's B. There are no nusb Device, event
    // thread, transfer, or callback references which could keep it alive.
    drop(independent);
    result?;
    physical_lifecycle::verify(record, false)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::{OriginalInterface, Owner};
    use rustusb_core::{BusId, PhysicalDeviceReceipt};
    use std::cell::RefCell;

    fn baseline() -> Result<Baseline> {
        Ok(Baseline {
            device_id: format!("usb-{}", "a".repeat(64)),
            bus_id: BusId::new("1-1")?,
            receipt: PhysicalDeviceReceipt {
                sysfs_device: 1,
                sysfs_inode: 2,
                node_device: 3,
                node_inode: 4,
                usb_device_number: 2,
                bus_number: 1,
            },
            configuration: 1,
            interfaces: vec![
                OriginalInterface {
                    number: 0,
                    alternate: 0,
                    driver: Some("cdc_ether".into()),
                },
                OriginalInterface {
                    number: 1,
                    alternate: 1,
                    driver: Some("cdc_ether".into()),
                },
            ],
        })
    }

    fn missing(original: &Baseline) -> Baseline {
        let mut current = original.clone();
        for interface in &mut current.interfaces {
            interface.driver = None;
        }
        current
    }

    #[test]
    fn exact_scope_allows_only_missing_original_drivers() -> Result<()> {
        let original = baseline()?;
        scope(&original, &original)?;
        scope(&original, &missing(&original))?;
        let mut cases = Vec::new();
        let mut value = missing(&original);
        value.configuration = 2;
        cases.push(value);
        let mut value = missing(&original);
        value.receipt.sysfs_inode += 1;
        cases.push(value);
        let mut value = missing(&original);
        value.interfaces.pop();
        cases.push(value);
        let mut value = missing(&original);
        value.interfaces[1].alternate = 0;
        cases.push(value);
        let mut value = missing(&original);
        value.interfaces[1].number = 2;
        cases.push(value);
        for driver in ["usbfs", "usbip-host", "different_driver"] {
            let mut value = missing(&original);
            value.interfaces[1].driver = Some(driver.into());
            cases.push(value);
        }
        for value in cases {
            let mut calls = Vec::new();
            assert!(
                restore_drivers(
                    &original,
                    || Ok(value.clone()),
                    |number| {
                        calls.push(number);
                        Ok(())
                    }
                )
                .is_err()
            );
            assert!(calls.is_empty());
        }
        let mut unbound = original.clone();
        unbound.interfaces[0].driver = None;
        let mut claimed = unbound.clone();
        claimed.interfaces[0].driver = Some("cdc_ether".into());
        assert!(scope(&unbound, &claimed).is_err());
        Ok(())
    }

    #[test]
    fn already_restored_and_original_none_do_not_connect() -> Result<()> {
        let mut original = baseline()?;
        original.interfaces[1].driver = None;
        let mut calls = Vec::new();
        restore_drivers(
            &original,
            || Ok(original.clone()),
            |number| {
                calls.push(number);
                Ok(())
            },
        )?;
        assert!(calls.is_empty());
        Ok(())
    }

    #[test]
    fn sibling_probe_reobserves_full_state_and_skips_second_connect() -> Result<()> {
        let original = baseline()?;
        let current = RefCell::new(missing(&original));
        let mut calls = Vec::new();
        restore_drivers(
            &original,
            || Ok(current.borrow().clone()),
            |number| {
                calls.push(number);
                *current.borrow_mut() = original.clone();
                Ok(())
            },
        )?;
        assert_eq!(calls, vec![0]);
        Ok(())
    }

    #[test]
    fn real_connect_errno_is_retained_until_actual_original_binding() -> Result<()> {
        let original = baseline()?;
        let unavailable = missing(&original);
        let error = restore_drivers(
            &original,
            || Ok(unavailable.clone()),
            |_| Err(std::io::Error::from_raw_os_error(16).into()),
        );
        assert!(matches!(error, Err(Error::Io(error)) if error.raw_os_error() == Some(16)));
        let current = RefCell::new(missing(&original));
        restore_drivers(
            &original,
            || Ok(current.borrow().clone()),
            |_| {
                *current.borrow_mut() = original.clone();
                Err(std::io::Error::from_raw_os_error(16).into())
            },
        )?;
        Ok(())
    }

    #[test]
    fn successful_ioctl_with_wrong_driver_or_scope_is_not_restoration() -> Result<()> {
        let original = baseline()?;
        for replace_generation in [false, true] {
            let current = RefCell::new(missing(&original));
            let mut calls = Vec::new();
            let result = restore_drivers(
                &original,
                || Ok(current.borrow().clone()),
                |number| {
                    calls.push(number);
                    let mut value = current.borrow_mut();
                    if replace_generation {
                        value.receipt.node_inode += 1;
                    } else {
                        value.interfaces[0].driver = Some("different_driver".into());
                    }
                    Ok(())
                },
            );
            assert!(result.is_err());
            assert_eq!(calls, vec![0]);
        }
        Ok(())
    }

    #[test]
    fn losing_an_earlier_driver_before_final_check_does_not_converge() -> Result<()> {
        let original = baseline()?;
        let current = RefCell::new(missing(&original));
        let result = restore_drivers(
            &original,
            || Ok(current.borrow().clone()),
            |number| {
                let mut value = current.borrow_mut();
                value.interfaces[usize::from(number)].driver = Some("cdc_ether".into());
                if number == 1 {
                    value.interfaces[0].driver = None;
                }
                Ok(())
            },
        );
        assert!(result.is_err());
        Ok(())
    }

    #[test]
    fn legacy_manual_authorization_fails_before_any_os_device_observation() -> Result<()> {
        let record = Record {
            correlation: None,
            lease_id: "00000000-0000-0000-0000-000000000001".into(),
            owner: Owner {
                uid: 1000,
                pid: 0,
                start_time: 0,
                boot_id: String::new(),
            },
            baseline: baseline()?,
            recovery: RecoveryAuthorization::ManualOnly,
        };
        assert!(matches!(restore(&record), Err(Error::Permission(_))));
        Ok(())
    }
}
