use super::*;
use ratatui::backend::TestBackend;
use rustusb_core::application::RequestId;
use rustusb_service::rpc::{ProfileInfo, Request, Response};

#[test]
fn untrusted_text_never_becomes_terminal_control_and_dimensions_are_bounded()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    assert_eq!(
        text("hello\x1b]52;c;attack\x07\n\u{202e}界", 20),
        "hello]52;c;attack界"
    );
    assert_eq!(text("abcdef", 3), "abc");
    for (w, h) in [(0, 0), (1, 1), (2, 1), (80, 24), (65535, 65535)] {
        let bounded = viewport(w, h);
        assert!(bounded.width <= 240 && bounded.height <= 100);
        let mut terminal = Terminal::new(TestBackend::new(bounded.width, bounded.height))?;
        let snapshot = Snapshot::new(Options::default());
        let mut app = App::new(snapshot.options);
        terminal.draw(|f| draw(f, &mut app, &snapshot))?;
    }
    Ok(())
}
#[test]
fn confirmation_defaults_to_cancel_and_repeated_events_do_not_submit() {
    let mut snapshot = Snapshot::new(Options::default());
    snapshot.profile = Some("test".into());
    let mut app = App::new(snapshot.options);
    let (tx, mut rx) = mpsc::channel(1);
    let press = |c| KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE);
    app.key(press('e'), &snapshot, &tx);
    assert!(app.confirm.is_some());
    app.key(
        KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        &snapshot,
        &tx,
    );
    assert!(rx.try_recv().is_err());
    app.key(press('e'), &snapshot, &tx);
    app.key(
        KeyEvent::new(KeyCode::Right, KeyModifiers::NONE),
        &snapshot,
        &tx,
    );
    let mut repeat = press('e');
    repeat.kind = KeyEventKind::Repeat;
    app.key(repeat, &snapshot, &tx);
    assert!(rx.try_recv().is_err());
    app.key(
        KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        &snapshot,
        &tx,
    );
    assert!(matches!(
        rx.try_recv().map(|v| v.command),
        Ok(Command::Mutate(Mutation::Start(_)))
    ));
    app.key(press('e'), &snapshot, &tx);
    assert!(app.confirm.is_none());
    app.key(
        KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        &snapshot,
        &tx,
    );
    assert!(rx.try_recv().is_err());
    snapshot.acknowledged = 1;
    assert!(!app.pending(&snapshot));
}
#[test]
fn unknown_start_retries_only_exact_original_even_after_history_expired() -> Result<()> {
    let mut snapshot = Snapshot::new(Options::default());
    let id = RequestId::new("00000000-0000-4000-8000-000000000001")?;
    assert!(matches!(
        snapshot.mutation_request(Mutation::Start("first".into()), Some(id.clone())),
        Some(Request::Start { .. })
    ));
    snapshot.mutation_response(Response::RequestExpired {
        request_id: id.clone(),
    });
    assert!(snapshot.unresolved.is_some());
    match snapshot.mutation_request(Mutation::Start("other".into()), None) {
        Some(Request::RetryStart {
            profile,
            request_id,
        }) => {
            assert_eq!(profile, "first");
            assert_eq!(request_id, id);
        }
        other => return Err(Error::State(format!("wrong retry {other:?}"))),
    }
    Ok(())
}
#[test]
fn stable_selection_survives_order_change_and_unicode_details_render_all_pages()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    let mut snapshot = Snapshot::new(Options::default());
    snapshot.profiles = vec![ProfileInfo {
        id: "profile-a".into(),
        backend: rustusb_service::config::Backend::Client,
    }];
    snapshot.profile = Some("profile-a".into());
    snapshot.status = "界 é ready".into();
    let mut terminal = Terminal::new(TestBackend::new(120, 30))?;
    let mut app = App::new(snapshot.options);
    app.page = 5;
    let rows = app.rows(&snapshot);
    app.selected[5] = Some("profile-a".into());
    assert_eq!(
        rows[app
            .selection(&rows)
            .ok_or_else(|| Error::State("missing selection".into()))?]
        .key,
        "profile-a"
    );
    app.reverse = true;
    let rows = app.rows(&snapshot);
    assert_eq!(
        rows[app
            .selection(&rows)
            .ok_or_else(|| Error::State("lost selection".into()))?]
        .key,
        "profile-a"
    );
    for width in [28, 60, 120] {
        terminal.resize(Rect::new(0, 0, width, 30))?;
        for (page, name) in PAGES.iter().enumerate() {
            app.page = page;
            terminal.draw(|f| draw(f, &mut app, &snapshot))?;
            let rendered = terminal
                .backend()
                .buffer()
                .content()
                .iter()
                .map(|c| c.symbol())
                .collect::<String>();
            assert!(rendered.contains("RustUSB"));
            assert!(rendered.contains(name));
        }
    }
    app.page = 3;
    app.details = true;
    terminal.draw(|f| draw(f, &mut app, &snapshot))?;
    Ok(())
}
#[test]
fn search_is_bounded_and_control_c_exits_even_in_search() {
    let snapshot = Snapshot::new(Options::default());
    let mut app = App::new(snapshot.options);
    let (tx, _) = mpsc::channel(1);
    app.searching = true;
    for _ in 0..100 {
        app.key(
            KeyEvent::new(KeyCode::Char('界'), KeyModifiers::NONE),
            &snapshot,
            &tx,
        );
    }
    assert_eq!(app.search.chars().count(), 80);
    assert!(app.key(
        KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
        &snapshot,
        &tx
    ));
}

#[test]
fn agent_outage_retains_exact_rows_but_clears_health_and_marks_each_page_stale()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    use rustusb_core::{DeviceKey, SessionState, VirtualProfile, application::*};
    use rustusb_service::{
        health::{HealthAvailability, SessionHealth},
        model::{AuditAction, AuditEvent, Evidence, Job, JobState, Metrics, Session, SessionRole},
    };
    let job_id = JobId::new("00000000-0000-4000-8000-000000000001")?;
    let session_id = SessionId::new("00000000-0000-4000-8000-000000000002")?;
    let epoch = InstanceId::new("00000000-0000-4000-8000-000000000003")?;
    let mut snapshot = Snapshot::new(Options::default());
    snapshot.jobs = vec![Job {
        id: job_id.clone(),
        instance_id: epoch.clone(),
        profile: "test".into(),
        operator_uid: 1000,
        state: JobState::Running,
        stop_requested: false,
        operation_error: None,
    }];
    snapshot.sessions = vec![Session {
        id: session_id.clone(),
        instance_id: epoch.clone(),
        job_id: job_id.clone(),
        profile: "test".into(),
        role: SessionRole::Import,
        device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
        principal: None,
        state: SessionState::Connected,
        evidence: Evidence::LocalEnumeration,
        stop_requested: false,
        terminal: false,
        operation_error: None,
        cleanup: None,
        metrics: Metrics::default(),
        attempts: vec![],
    }];
    snapshot.audit = vec![AuditEvent {
        sequence: 7,
        actor_uid: 1000,
        profile: Some("test".into()),
        job_id: Some(job_id.clone()),
        session_id: Some(session_id.clone()),
        action: AuditAction::Start,
        failure: None,
        state: None,
        evidence: None,
        cleanup: None,
    }];
    snapshot.health = vec![SessionHealth {
        session_id: session_id.clone(),
        instance_id: epoch,
        role: SessionRole::Import,
        availability: HealthAvailability::Live,
        link: None,
    }];
    snapshot.journal = Some(true);
    snapshot.local_status = "local independent".into();
    snapshot.remote_status = "remote independent".into();
    snapshot.next_job = Some(job_id.clone());
    snapshot.next_session = Some(session_id.clone());
    snapshot.next_audit = Some(8);
    snapshot.agent_stale();
    assert_eq!(snapshot.jobs[0].id, job_id);
    assert_eq!(snapshot.sessions[0].id, session_id);
    assert!(!snapshot.sessions[0].terminal);
    assert_eq!(snapshot.audit[0].sequence, 7);
    assert!(snapshot.health.is_empty());
    assert_eq!(snapshot.journal, None);
    assert_eq!(snapshot.local_status, "local independent");
    assert_eq!(snapshot.remote_status, "remote independent");
    assert_eq!(snapshot.next_job, Some(job_id));
    assert_eq!(snapshot.next_session, Some(session_id));
    assert_eq!(snapshot.next_audit, Some(8));
    let mut app = App::new(snapshot.options);
    for width in [60, 120] {
        let mut terminal = Terminal::new(TestBackend::new(width, 18))?;
        for (page, jobs) in [(2, false), (2, true), (4, false)] {
            app.page = page;
            app.jobs = jobs;
            terminal.draw(|f| draw(f, &mut app, &snapshot))?;
            let rendered = terminal
                .backend()
                .buffer()
                .content()
                .iter()
                .map(|c| c.symbol())
                .collect::<String>();
            assert!(rendered.contains("Stale: agent unavailable"));
            assert!(!rendered.contains("Current page"));
        }
    }
    Ok(())
}
