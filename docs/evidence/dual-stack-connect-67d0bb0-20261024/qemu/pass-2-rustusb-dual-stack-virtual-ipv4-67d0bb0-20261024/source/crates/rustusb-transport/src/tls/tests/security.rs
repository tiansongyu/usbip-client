//! Real handshake outcomes and nonowning metadata lifetime; no USB claims.
use super::*;

#[tokio::test]
async fn security_counts_actual_outcomes_and_outlives_configuration_without_permits() -> TestResult
{
    let fixture = Fixture::new()?;
    let (server, client) = fixture.endpoints()?;
    let clone = server.clone();
    let monitor = clone.security_monitor();
    let budget = Arc::downgrade(&server.limit);
    drop(connected(&server, &client).await?);

    let (left, right) = duplex(64);
    drop(right);
    assert!(matches!(
        clone.accept(left).await,
        Err(Error::HandshakeFailed)
    ));

    let other = fixture
        .ca
        .material(ExtendedKeyUsagePurpose::ClientAuth, false)?;
    let unmapped = TlsClient::new(fixture.ca.roots()?, other.identity()?)?;
    let (left, right) = duplex(2048);
    let (accepted, _) = tokio::join!(server.accept(left), unmapped.connect("server.test", right));
    assert!(matches!(accepted, Err(Error::UnmappedCertificate)));

    let (left, _right) = duplex(64);
    assert!(matches!(
        server
            .accept_with_deadline(left, Duration::from_millis(20))
            .await,
        Err(Error::Timeout)
    ));
    let (left, _right) = duplex(64);
    let mut pending = Box::pin(clone.accept(left));
    poll_fn(|cx| {
        assert!(pending.as_mut().poll(cx).is_pending());
        Poll::Ready(())
    })
    .await;
    assert_eq!(monitor.snapshot().tls.in_flight, 1);
    drop(pending);
    let snapshot = monitor.snapshot();
    assert_eq!(
        snapshot.tls,
        crate::TlsCounters {
            attempts: 5,
            authenticated: 1,
            handshake_failed: 1,
            unmapped_certificate: 1,
            timed_out: 1,
            cancelled: 1,
            ..Default::default()
        }
    );
    assert!(snapshot.consistent && !snapshot.saturated);
    assert_eq!(snapshot.framing, crate::FramingCounters::default());
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    let bytes = serde_json::to_vec(&snapshot)?;
    assert!(bytes.len() < 1024);
    assert_eq!(
        serde_json::from_slice::<crate::SecuritySnapshot>(&bytes)?,
        snapshot
    );
    drop((server, clone, client, fixture));
    assert!(
        budget.upgrade().is_none(),
        "metadata retained the TLS budget"
    );
    assert_eq!(monitor.snapshot(), snapshot);
    Ok(())
}
