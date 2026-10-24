//! Complete offline CRL metadata admission; rustls still authenticates each list.

use crate::{Error, Result};
use rustls::{
    pki_types::{CertificateRevocationListDer, UnixTime},
    time_provider::TimeProvider,
};
use std::{collections::BTreeSet, sync::Arc, time::SystemTime};
use x509_parser::{
    extensions::X509Extension, prelude::FromDer, revocation_list::CertificateRevocationList,
};

pub(crate) const MAX_CRLS: usize = 16;
pub(crate) const MAX_CRL_BYTES: usize = 64 * 1024;
pub(crate) const MAX_CRL_TOTAL: usize = 256 * 1024;

pub(crate) struct Revocation {
    pub(crate) der: Vec<CertificateRevocationListDer<'static>>,
    pub(crate) clock: Arc<CrlClock>,
}

// The upstream default clock can panic on a pre-epoch OS clock. An unavailable
// clock here must be a TLS error, never a fabricated time or an application panic.
#[derive(Debug)]
struct CheckedSystemClock;

impl TimeProvider for CheckedSystemClock {
    fn current_time(&self) -> Option<UnixTime> {
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .ok()
            .map(UnixTime::since_unix_epoch)
    }
}

#[derive(Debug)]
pub(crate) struct CrlClock {
    inner: Arc<dyn TimeProvider>,
    valid_from: u64,
    valid_until: u64,
}

impl TimeProvider for CrlClock {
    fn current_time(&self) -> Option<UnixTime> {
        let now = self.inner.current_time()?;
        (self.valid_from <= now.as_secs() && now.as_secs() < self.valid_until).then_some(now)
    }
}

fn extensions_are_complete(extensions: &[X509Extension<'_>]) -> Result<()> {
    let mut seen = BTreeSet::new();
    for extension in extensions {
        if !seen.insert(extension.oid.as_bytes()) || extension.parsed_extension().error().is_some()
        {
            return Err(Error::InvalidCredentials);
        }
        // No delta, scoped/indirect CRL, certificateIssuer entry or delta URL
        // interpretation. These would require different overlap/coverage rules.
        if matches!(
            extension.oid.to_id_string().as_str(),
            "2.5.29.27" | "2.5.29.28" | "2.5.29.29" | "2.5.29.46"
        ) {
            return Err(Error::InvalidInput("complete direct CRL required"));
        }
    }
    Ok(())
}

impl Revocation {
    #[cfg(test)]
    pub(crate) fn replace_clock_for_test(&mut self, inner: Arc<dyn TimeProvider>) -> Result<()> {
        let clock =
            Arc::get_mut(&mut self.clock).ok_or(Error::InvalidInput("shared test CRL clock"))?;
        clock.inner = inner;
        Ok(())
    }

    pub(crate) fn new(bytes: Vec<Vec<u8>>) -> Result<Self> {
        if bytes.is_empty() || bytes.len() > MAX_CRLS {
            return Err(Error::InvalidInput("CRL count"));
        }
        if bytes
            .iter()
            .any(|b| b.is_empty() || b.len() > MAX_CRL_BYTES)
            || bytes.iter().map(Vec::len).sum::<usize>() > MAX_CRL_TOTAL
        {
            return Err(Error::InvalidInput("CRL size"));
        }
        let mut issuers = BTreeSet::new();
        let mut valid_from = 0;
        let mut valid_until = u64::MAX;
        for der in &bytes {
            let (rest, crl) =
                CertificateRevocationList::from_der(der).map_err(|_| Error::InvalidCredentials)?;
            if !rest.is_empty() {
                return Err(Error::InvalidCredentials);
            }
            if !issuers.insert(crl.issuer().as_raw().to_vec()) {
                return Err(Error::InvalidInput("duplicate CRL issuer"));
            }
            extensions_are_complete(crl.extensions())?;
            for revoked in crl.iter_revoked_certificates() {
                extensions_are_complete(revoked.extensions())?;
            }
            let start = u64::try_from(crl.last_update().timestamp())
                .map_err(|_| Error::InvalidCredentials)?;
            let end = crl.next_update().ok_or(Error::InvalidCredentials)?;
            let end = u64::try_from(end.timestamp()).map_err(|_| Error::InvalidCredentials)?;
            if start >= end {
                return Err(Error::InvalidCredentials);
            }
            valid_from = valid_from.max(start);
            valid_until = valid_until.min(end);
        }
        let clock = Arc::new(CrlClock {
            inner: Arc::new(CheckedSystemClock),
            valid_from,
            valid_until,
        });
        if clock.current_time().is_none() {
            return Err(Error::InvalidCredentials);
        }
        Ok(Self {
            der: bytes
                .into_iter()
                .map(CertificateRevocationListDer::from)
                .collect(),
            clock,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    use x509_parser::{asn1_rs::Oid, extensions::ParsedExtension};

    #[derive(Debug)]
    struct FixedClock(Option<u64>);

    impl TimeProvider for FixedClock {
        fn current_time(&self) -> Option<UnixTime> {
            self.0
                .map(|s| UnixTime::since_unix_epoch(Duration::from_secs(s)))
        }
    }

    #[test]
    fn guarded_clock_preserves_absence_and_exact_freshness_boundaries() {
        for (now, accepted) in [
            (None, false),
            (Some(9), false),
            (Some(10), true),
            (Some(19), true),
            (Some(20), false),
        ] {
            let clock = CrlClock {
                inner: Arc::new(FixedClock(now)),
                valid_from: 10,
                valid_until: 20,
            };
            assert_eq!(clock.current_time().is_some(), accepted);
        }
    }

    #[test]
    fn extension_metadata_rejects_duplicate_and_non_complete_crl_policies()
    -> std::result::Result<(), Box<dyn std::error::Error>> {
        let number = X509Extension::new(
            Oid::from(&[2, 5, 29, 20])?,
            false,
            &[2, 1, 1],
            ParsedExtension::Unparsed,
        );
        assert!(extensions_are_complete(&[number.clone(), number]).is_err());
        for oid in [27, 28, 29, 46] {
            let extension = X509Extension::new(
                Oid::from(&[2, 5, 29, oid])?,
                false,
                &[],
                ParsedExtension::Unparsed,
            );
            assert!(extensions_are_complete(&[extension]).is_err());
        }
        Ok(())
    }
}
