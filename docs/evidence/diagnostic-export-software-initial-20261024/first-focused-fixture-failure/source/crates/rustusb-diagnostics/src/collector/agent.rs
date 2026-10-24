//! One retained private-runtime observer; all requests are read-only metadata.

use super::*;
use rustusb_service::{
    diagnostics::{
        DiagnosticFailure, DiagnosticHeader, DiagnosticItems, DiagnosticKind, DiagnosticPage,
    },
    model::Failure,
    rpc::{LogReadFailure, Request, Response, ResponseEnvelope},
};

type ReadResult<T> = std::result::Result<T, Interrupted>;

pub(super) async fn collect(
    mut builder: Builder,
    source: AgentSource,
    mut stop: watch::Receiver<bool>,
    started: Instant,
) -> Builder {
    let uid = rustix::process::getuid().as_raw();
    if builder.set_agent_scope(&source.profile, uid).is_err() {
        for stage in [Stage::Agent, Stage::Health, Stage::Logs] {
            finish_stage(
                &mut builder,
                stage,
                Interrupted::Failed(CollectionFailure::InvalidInput),
                started,
            );
        }
        return builder;
    }
    let mut recording_unhealthy = false;
    let first = request(
        &source,
        Request::BeginDiagnostic {
            profile: source.profile.clone(),
        },
        &mut stop,
        started,
    )
    .await;
    match first {
        Ok(envelope) => {
            recording_unhealthy |= !envelope.journal_healthy;
            match envelope.response {
                Response::DiagnosticStarted { header } => {
                    let history = if let Err(error) =
                        builder.begin_agent(&header, &source.profile, uid, elapsed(started))
                    {
                        Err(Interrupted::Failed(core_failure(&error)))
                    } else {
                        committed_pages(
                            &mut builder,
                            &source,
                            &header,
                            &mut stop,
                            started,
                            &mut recording_unhealthy,
                        )
                        .await
                    };
                    match history {
                        Ok(()) => builder.set_outcome(
                            Stage::Agent,
                            SectionOutcome::Complete,
                            None,
                            elapsed(started),
                        ),
                        Err(reason) => finish_stage(&mut builder, Stage::Agent, reason, started),
                    }
                    // Always attempt metadata retirement after a returned token.
                    // Do not race this attempt with cancellation: call_path's
                    // bounded network await and actual manifest tasks are owned.
                    let ended = rustusb_service::ipc::call_path(
                        &source.manifest,
                        Request::EndDiagnostic {
                            profile: source.profile.clone(),
                            snapshot: header.snapshot,
                        },
                    )
                    .await;
                    match ended {
                        Ok(ResponseEnvelope {
                            journal_healthy,
                            response: Response::DiagnosticEnded {},
                            ..
                        }) => {
                            recording_unhealthy |= !journal_healthy;
                        }
                        _ => finish_stage(
                            &mut builder,
                            Stage::Agent,
                            Interrupted::Failed(CollectionFailure::Unconfirmed),
                            started,
                        ),
                    }
                }
                Response::Error { code } => {
                    let reason = service_failure(code);
                    // An authorization refusal is not an invitation to issue
                    // two more requests or inspect a private store directly.
                    for stage in [Stage::Agent, Stage::Health, Stage::Logs] {
                        finish_stage(&mut builder, stage, reason, started);
                    }
                    return builder;
                }
                Response::DiagnosticError { code } => finish_stage(
                    &mut builder,
                    Stage::Agent,
                    diagnostic_failure(code),
                    started,
                ),
                _ => finish_stage(
                    &mut builder,
                    Stage::Agent,
                    Interrupted::Failed(CollectionFailure::Corrupt),
                    started,
                ),
            }
        }
        Err(reason) => finish_stage(&mut builder, Stage::Agent, reason, started),
    }

    let health = health_pages(
        &mut builder,
        &source,
        &mut stop,
        started,
        &mut recording_unhealthy,
    )
    .await;
    match health {
        Ok(()) => builder.set_outcome(
            Stage::Health,
            SectionOutcome::Complete,
            None,
            elapsed(started),
        ),
        Err(reason) => finish_stage(&mut builder, Stage::Health, reason, started),
    }
    let logs = log_pages(
        &mut builder,
        &source,
        &mut stop,
        started,
        &mut recording_unhealthy,
    )
    .await;
    match logs {
        Ok(()) => builder.set_outcome(
            Stage::Logs,
            SectionOutcome::Complete,
            None,
            elapsed(started),
        ),
        Err(reason) => finish_stage(&mut builder, Stage::Logs, reason, started),
    }
    if recording_unhealthy {
        finish_stage(
            &mut builder,
            Stage::Agent,
            Interrupted::Failed(CollectionFailure::Persistence),
            started,
        );
    }
    builder
}

async fn request(
    source: &AgentSource,
    request: Request,
    stop: &mut watch::Receiver<bool>,
    started: Instant,
) -> ReadResult<ResponseEnvelope> {
    if let Some(reason) = interruption(stop, started) {
        return Err(reason);
    }
    let remaining = BUDGET.saturating_sub(started.elapsed());
    tokio::select! {
        biased;
        () = cancelled(stop) => Err(Interrupted::Cancelled),
        result = tokio::time::timeout(remaining, rustusb_service::ipc::call_path(&source.manifest, request)) => {
            match result {
                Ok(Ok(envelope)) => Ok(envelope),
                Ok(Err(error)) => Err(Interrupted::Failed(core_failure(&error))),
                Err(_) => Err(Interrupted::Failed(CollectionFailure::Timeout)),
            }
        }
    }
}

async fn cancelled(stop: &mut watch::Receiver<bool>) {
    loop {
        if *stop.borrow_and_update() {
            return;
        }
        if stop.changed().await.is_err() {
            // Dropping the sender does not create an authorization or stop.
            // A caller that needs cancellation explicitly sends true.
            std::future::pending::<()>().await;
        }
    }
}

async fn committed_pages(
    builder: &mut Builder,
    source: &AgentSource,
    header: &DiagnosticHeader,
    stop: &mut watch::Receiver<bool>,
    started: Instant,
    unhealthy: &mut bool,
) -> ReadResult<()> {
    for (kind, total) in [
        (DiagnosticKind::Jobs, header.counts.jobs),
        (DiagnosticKind::Sessions, header.counts.sessions),
        (DiagnosticKind::Audit, header.counts.audit),
    ] {
        let mut after = 0;
        // Include an explicit terminal empty page for a zero-row section.
        loop {
            let envelope = request(
                source,
                Request::DiagnosticPage {
                    profile: source.profile.clone(),
                    snapshot: header.snapshot.clone(),
                    kind,
                    after,
                },
                stop,
                started,
            )
            .await?;
            *unhealthy |= !envelope.journal_healthy;
            let page = match envelope.response {
                Response::DiagnosticPage { page } => page,
                Response::DiagnosticError { code } => return Err(diagnostic_failure(code)),
                Response::Error { code } => return Err(service_failure(code)),
                _ => return Err(Interrupted::Failed(CollectionFailure::Corrupt)),
            };
            validate_page(&page, header, kind, after, total)?;
            let next = page.next_after;
            builder
                .push_items(page.items)
                .map_err(|error| Interrupted::Failed(core_failure(&error)))?;
            match next {
                Some(next) => after = next,
                None => break,
            }
        }
    }
    Ok(())
}

fn validate_page(
    page: &DiagnosticPage,
    header: &DiagnosticHeader,
    kind: DiagnosticKind,
    after: u16,
    total: u16,
) -> ReadResult<()> {
    let (actual_kind, count) = match &page.items {
        DiagnosticItems::Jobs { jobs } => (DiagnosticKind::Jobs, jobs.len()),
        DiagnosticItems::Sessions { sessions } => (DiagnosticKind::Sessions, sessions.len()),
        DiagnosticItems::Audit { events } => (DiagnosticKind::Audit, events.len()),
    };
    let consumed = usize::from(after).checked_add(count);
    let valid_end = consumed.is_some_and(|end| {
        end <= usize::from(total)
            && match page.next_after {
                Some(next) => count > 0 && usize::from(next) == end && next < total,
                None => end == usize::from(total),
            }
    });
    if page.snapshot != header.snapshot
        || page.committed_sequence != header.committed_sequence
        || page.after != after
        || page.total != total
        || actual_kind != kind
        || count > 16
        || !valid_end
    {
        return Err(Interrupted::Failed(CollectionFailure::Corrupt));
    }
    Ok(())
}

async fn health_pages(
    builder: &mut Builder,
    source: &AgentSource,
    stop: &mut watch::Receiver<bool>,
    started: Instant,
    unhealthy: &mut bool,
) -> ReadResult<()> {
    let mut after = None;
    let mut count = 0;
    for _ in 0..32 {
        let envelope = request(
            source,
            Request::SessionHealth {
                profile: source.profile.clone(),
                after: after.clone(),
            },
            stop,
            started,
        )
        .await?;
        *unhealthy |= !envelope.journal_healthy;
        let (sessions, next) = match envelope.response {
            Response::SessionHealth {
                sessions,
                next_after,
            } => (sessions, next_after),
            Response::Error { code } => return Err(service_failure(code)),
            _ => return Err(Interrupted::Failed(CollectionFailure::Corrupt)),
        };
        count += sessions.len();
        let ordered = sessions
            .iter()
            .try_fold(after.as_ref(), |previous, row| {
                if previous.is_some_and(|id| id >= &row.session_id) {
                    None
                } else {
                    Some(Some(&row.session_id))
                }
            })
            .is_some();
        if sessions.len() > 16
            || count > 512
            || !ordered
            || next
                .as_ref()
                .is_some_and(|id| sessions.last().is_none_or(|row| &row.session_id != id))
        {
            return Err(Interrupted::Failed(CollectionFailure::Corrupt));
        }
        builder
            .observe_health(&sessions, elapsed(started))
            .map_err(|error| Interrupted::Failed(core_failure(&error)))?;
        match next {
            None => return Ok(()),
            Some(next) => after = Some(next),
        }
    }
    Err(Interrupted::Failed(CollectionFailure::Limit))
}

async fn log_pages(
    builder: &mut Builder,
    source: &AgentSource,
    stop: &mut watch::Receiver<bool>,
    started: Instant,
    unhealthy: &mut bool,
) -> ReadResult<()> {
    let mut after = None;
    for _ in 0..LOG_PAGES {
        let envelope = request(
            source,
            Request::ReadLogs {
                profile: source.profile.clone(),
                after: after.clone(),
                limit: 16,
            },
            stop,
            started,
        )
        .await?;
        *unhealthy |= !envelope.journal_healthy;
        let page = match envelope.response {
            Response::Logs { page } => page,
            Response::LogError { code } => return Err(Interrupted::Failed(log_code(code))),
            Response::Error { code } => return Err(service_failure(code)),
            _ => return Err(Interrupted::Failed(CollectionFailure::Corrupt)),
        };
        builder
            .observe_logs(&page, elapsed(started))
            .map_err(|error| Interrupted::Failed(core_failure(&error)))?;
        if !page.more {
            return Ok(());
        }
        if page.next_after.is_none() || page.next_after == after {
            return Err(Interrupted::Failed(CollectionFailure::Corrupt));
        }
        after = page.next_after;
    }
    Err(Interrupted::Failed(CollectionFailure::Limit))
}

fn service_failure(code: Failure) -> Interrupted {
    Interrupted::Failed(match code {
        Failure::Permission => CollectionFailure::Permission,
        Failure::InvalidInput => CollectionFailure::InvalidInput,
        Failure::Io => CollectionFailure::Io,
        Failure::Timeout => CollectionFailure::Timeout,
        Failure::State => CollectionFailure::State,
        Failure::Unsupported => CollectionFailure::Unsupported,
        Failure::Cancelled => return Interrupted::Cancelled,
        Failure::Persistence => CollectionFailure::Persistence,
    })
}

fn diagnostic_failure(code: DiagnosticFailure) -> Interrupted {
    Interrupted::Failed(match code {
        DiagnosticFailure::Busy => CollectionFailure::Busy,
        DiagnosticFailure::Expired => CollectionFailure::Expired,
        DiagnosticFailure::InvalidCursor => CollectionFailure::Corrupt,
        DiagnosticFailure::Unavailable => CollectionFailure::Unavailable,
        DiagnosticFailure::EncodingLimit => CollectionFailure::Limit,
    })
}

fn log_code(code: LogReadFailure) -> CollectionFailure {
    match code {
        LogReadFailure::InvalidInput => CollectionFailure::InvalidInput,
        LogReadFailure::Permission => CollectionFailure::Permission,
        LogReadFailure::Corrupt => CollectionFailure::Corrupt,
        LogReadFailure::Io => CollectionFailure::Io,
        LogReadFailure::Busy => CollectionFailure::Busy,
        LogReadFailure::Disabled => CollectionFailure::Disabled,
        LogReadFailure::Unsupported => CollectionFailure::Unsupported,
        LogReadFailure::Worker => CollectionFailure::Worker,
        LogReadFailure::LostRecords => CollectionFailure::Unconfirmed,
    }
}
