use crate::{Options, state::*};
use crossterm::{
    cursor::{Hide, Show},
    event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers},
    execute,
    terminal::{self, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    Frame, Terminal, TerminalOptions, Viewport,
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Wrap},
};
use rustusb_core::{Error, Result};
use std::{
    io::{self, IsTerminal, Write},
    time::Duration,
};
use tokio::sync::{mpsc, watch};

const PAGES: [&str; 6] = [
    "Local",
    "Remote",
    "Connections",
    "System",
    "Logs",
    "Settings",
];
#[derive(Clone)]
struct Row {
    key: String,
    label: String,
    detail: String,
    target: Option<Mutation>,
    attention: bool,
}
#[derive(Clone)]
struct Confirm {
    action: Mutation,
    label: String,
    accepted: bool,
}
struct App {
    page: usize,
    selected: [Option<String>; 6],
    search: String,
    searching: bool,
    reverse: bool,
    attention: bool,
    jobs: bool,
    details: bool,
    detail_scroll: u16,
    confirm: Option<Confirm>,
    help: bool,
    sequence: u64,
    waiting: Option<u64>,
    message: String,
    options: Options,
}
impl App {
    fn new(options: Options) -> Self {
        Self {
            page: 0,
            selected: Default::default(),
            search: String::new(),
            searching: false,
            reverse: false,
            attention: false,
            jobs: false,
            details: false,
            detail_scroll: 0,
            confirm: None,
            help: false,
            sequence: 0,
            waiting: None,
            message: "No operation requested".into(),
            options,
        }
    }
    fn pending(&mut self, snapshot: &Snapshot) -> bool {
        if self.waiting.is_some_and(|id| snapshot.acknowledged >= id) {
            self.waiting = None;
            self.message = snapshot.status.clone();
        }
        self.waiting.is_some()
    }
    fn send(&mut self, sender: &mpsc::Sender<Envelope>, command: Command) {
        if self.waiting.is_some() {
            self.message = "Previous request still pending; navigation remains available".into();
            return;
        }
        let Some(sequence) = self.sequence.checked_add(1) else {
            self.message = "Frontend command sequence exhausted".into();
            return;
        };
        match sender.try_send(Envelope { sequence, command }) {
            Ok(()) => {
                self.sequence = sequence;
                self.waiting = Some(sequence);
                self.message = "Request pending".into();
            }
            Err(_) => {
                self.message = "Frontend worker busy/unavailable; no new request submitted".into()
            }
        }
    }
    fn rows(&self, snapshot: &Snapshot) -> Vec<Row> {
        let mut rows = match self.page {
            0 => snapshot
                .local
                .as_ref()
                .and_then(|s| s.devices.as_ref())
                .map(|devices| {
                    devices
                        .iter()
                        .take(4096)
                        .map(|d| {
                            let device = &d.device;
                            Row {
                                key: format!(
                                    "{}:{}:{}",
                                    device.id, d.stamp.sysfs_inode, d.stamp.device_number
                                ),
                                label: format!(
                                    "{}  {:04x}:{:04x}  {}  [{}]",
                                    device.bus_id,
                                    device.vendor_id,
                                    device.product_id,
                                    device.product.as_deref().unwrap_or("USB device"),
                                    if device.exported {
                                        "exported"
                                    } else if device.export_blockers.is_empty() {
                                        "eligible, unverified"
                                    } else {
                                        "blocked"
                                    }
                                ),
                                detail: json(device),
                                target: None,
                                attention: !device.export_blockers.is_empty(),
                            }
                        })
                        .collect()
                })
                .unwrap_or_default(),
            1 => snapshot
                .remote
                .as_ref()
                .and_then(|s| s.devices.as_ref())
                .map(|devices| {
                    devices
                        .iter()
                        .map(|d| Row {
                            key: d.device_key.to_string(),
                            label: format!(
                                "{}  {}  {:04x}:{:04x}",
                                d.device_key,
                                d.device.bus_id,
                                d.device.vendor_id,
                                d.device.product_id
                            ),
                            detail: json(d),
                            target: None,
                            attention: false,
                        })
                        .collect()
                })
                .unwrap_or_default(),
            2 if self.jobs => snapshot
                .jobs
                .iter()
                .map(|j| Row {
                    key: j.id.to_string(),
                    label: format!("{:?}  {}  error={:?}", j.state, j.id, j.operation_error),
                    detail: json(j),
                    target: Some(Mutation::StopJob(j.id.clone())),
                    attention: j.operation_error.is_some(),
                })
                .collect(),
            2 => snapshot
                .sessions
                .iter()
                .map(|s| {
                    let health = snapshot
                        .health
                        .iter()
                        .find(|h| h.session_id == s.id && h.instance_id == s.instance_id);
                    Row {
                        key: s.id.to_string(),
                        label: format!(
                            "{:?} {:?} {}  cleanup={:?}",
                            s.role, s.state, s.device_key, s.cleanup
                        ),
                        detail: format!(
                            "{}\n\nIndependently sampled link health:\n{}",
                            json(s),
                            health
                                .map(json)
                                .unwrap_or_else(|| "Not observed (no assumed healthy RTT)".into())
                        ),
                        target: Some(Mutation::StopSession(s.id.clone())),
                        attention: s.operation_error.is_some()
                            || s.cleanup == Some(rustusb_service::model::Cleanup::Unconfirmed),
                    }
                })
                .collect(),
            3 => vec![
                row(
                    "agent",
                    &snapshot.status,
                    &format!(
                        "Journal healthy (last response): {:?}\nProfiles readable: {}\nSelected profile: {}\nJobs page: {}\nSessions page: {}\nSnapshots are independent samples. No UI state proves device cleanup.\nUI exit leaves service jobs running.",
                        snapshot.journal,
                        snapshot.profiles.len(),
                        snapshot.profile.as_deref().unwrap_or("none"),
                        snapshot.jobs_status,
                        snapshot.sessions_status
                    ),
                ),
                row(
                    "local",
                    &format!("Local: {}", snapshot.local_status),
                    "Read-only kernel observer. Discovery is not redirection support. Device details may include the local serial; they are never sent to a log.",
                ),
                row(
                    "remote",
                    &format!("Remote: {}", snapshot.remote_status),
                    "Authenticated fixed-profile catalog. Busy visibility does not prove unplug. A remote catalog is not a USB enumeration receipt.",
                ),
                row(
                    "scope",
                    "Acceptance: hardware and full G3 remain open",
                    "This terminal frontend adds no device/platform compatibility guarantee. Preferences can be saved explicitly. Administrator policy and TLS material are managed separately.",
                ),
            ],
            4 => snapshot
                .audit
                .iter()
                .map(|e| Row {
                    key: e.sequence.to_string(),
                    label: format!(
                        "#{}  {:?}  state={:?}  failure={:?}",
                        e.sequence, e.action, e.state, e.failure
                    ),
                    detail: json(e),
                    target: None,
                    attention: e.failure.is_some(),
                })
                .collect(),
            _ => {
                let mut rows = vec![
                    row(
                        "preferences",
                        &format!("Preferences: {}", snapshot.preferences_status),
                        &format!(
                            "{}\nChanged from saved: {}\n[w] save / [l] reload / [d] defaults. Never edits administrator policy, credentials or device authorization.",
                            snapshot.preferences_status,
                            snapshot.saved_options != Some(self.options)
                        ),
                    ),
                    row(
                        "refresh",
                        &format!("Auto refresh: {}  [a]", self.options.auto_refresh),
                        "Pause/resume profile polling and remote discovery. Local kernel observation continues. Service jobs are unaffected.",
                    ),
                    row(
                        "interval",
                        &format!(
                            "Refresh interval: {} seconds  [-/+]; bounded 2..120",
                            self.options.refresh_seconds
                        ),
                        "Delay after completion; slow reads cannot overlap. Remote errors retain the shared observer's bounded backoff.",
                    ),
                    row(
                        "theme",
                        &format!("Monochrome: {}  [m]", self.options.monochrome),
                        "Colors never carry state by themselves. Use w to save checked preferences; launch flags override them without saving.",
                    ),
                    row(
                        "start",
                        "Start fixed profile [e] / resolve original start [e]",
                        "Confirmation applies to the whole administrator-selected configuration, including every configured device. Browsing a row does not select a device for import or export.",
                    ),
                ];
                rows.extend(snapshot.profiles.iter().map(|p|row(&p.id,&format!("Profile {}  {:?}  [{}]",p.id,p.backend,if Some(&p.id)==snapshot.profile.as_ref(){"selected"}else{"[/] to select"}),"Readable fixed profile; actual Start/Stop/Discover/Audit authorization is checked independently by the service.")));
                rows
            }
        };
        let search = self.search.to_lowercase();
        rows.retain(|r| {
            (!self.attention || r.attention)
                && (search.is_empty()
                    || r.label.to_lowercase().contains(&search)
                    || r.key.to_lowercase().contains(&search))
        });
        rows.sort_by(|a, b| a.label.cmp(&b.label).then_with(|| a.key.cmp(&b.key)));
        if self.reverse {
            rows.reverse();
        }
        rows
    }
    fn selection(&mut self, rows: &[Row]) -> Option<usize> {
        let selected = self.selected[self.page]
            .as_ref()
            .and_then(|key| rows.iter().position(|r| &r.key == key))
            .or((!rows.is_empty()).then_some(0));
        self.selected[self.page] = selected.map(|i| rows[i].key.clone());
        selected
    }
    fn page_kind(&self) -> PageKind {
        if self.page == 4 {
            PageKind::Audit
        } else if self.jobs {
            PageKind::Jobs
        } else {
            PageKind::Sessions
        }
    }
    fn key(&mut self, key: KeyEvent, snapshot: &Snapshot, sender: &mpsc::Sender<Envelope>) -> bool {
        if key.kind != KeyEventKind::Press {
            return false;
        }
        if key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL) {
            return true;
        }
        if self.searching {
            match key.code {
                KeyCode::Esc | KeyCode::Enter => self.searching = false,
                KeyCode::Backspace => {
                    self.search.pop();
                }
                KeyCode::Char(c) if self.search.chars().count() < 80 && !c.is_control() => {
                    self.search.push(c)
                }
                _ => {}
            }
            return false;
        }
        if self.help {
            match key.code {
                KeyCode::Esc | KeyCode::Char('?') | KeyCode::Enter => self.help = false,
                KeyCode::Char('q') => return true,
                _ => {}
            }
            return false;
        }
        if self.confirm.is_some() {
            match key.code {
                KeyCode::Esc | KeyCode::Char('n') => self.confirm = None,
                KeyCode::Left | KeyCode::Right | KeyCode::Tab => {
                    if let Some(c) = &mut self.confirm {
                        c.accepted = !c.accepted;
                    }
                }
                KeyCode::Enter => {
                    if let Some(confirm) = self.confirm.take()
                        && confirm.accepted
                    {
                        self.send(sender, Command::Mutate(confirm.action));
                    }
                }
                _ => {}
            }
            return false;
        }
        if self.details {
            match key.code {
                KeyCode::Esc | KeyCode::Enter => {
                    self.details = false;
                    self.detail_scroll = 0;
                }
                KeyCode::Down | KeyCode::Char('j') => {
                    self.detail_scroll = self.detail_scroll.saturating_add(1)
                }
                KeyCode::Up | KeyCode::Char('k') => {
                    self.detail_scroll = self.detail_scroll.saturating_sub(1)
                }
                KeyCode::Char('q') => return true,
                _ => {}
            }
            return false;
        }
        match key.code {
            KeyCode::Char('q') => return true,
            KeyCode::Char('?') => self.help = true,
            KeyCode::Char('/') => self.searching = true,
            KeyCode::Esc => {
                self.search.clear();
                self.attention = false;
            }
            KeyCode::Char('s') => self.reverse = !self.reverse,
            KeyCode::Char('f') => self.attention = !self.attention,
            KeyCode::Char('v') if self.page == 2 => {
                self.jobs = !self.jobs;
                self.selected[2] = None;
            }
            KeyCode::Tab | KeyCode::BackTab | KeyCode::Char('1'..='6') => {
                let previous = self.page;
                self.page = match key.code {
                    KeyCode::Char(c) => (c as usize) - ('1' as usize),
                    KeyCode::BackTab => (self.page + 5) % 6,
                    _ => (self.page + 1) % 6,
                };
                self.search.clear();
                self.attention = false;
                if (previous == 1) != (self.page == 1) {
                    self.send(sender, Command::Remote(self.page == 1));
                }
            }
            KeyCode::Down | KeyCode::Char('j') | KeyCode::Up | KeyCode::Char('k') => {
                let rows = self.rows(snapshot);
                if let Some(i) = self.selection(&rows) {
                    let next = if matches!(key.code, KeyCode::Up | KeyCode::Char('k')) {
                        i.saturating_sub(1)
                    } else {
                        (i + 1).min(rows.len() - 1)
                    };
                    self.selected[self.page] = Some(rows[next].key.clone());
                }
            }
            KeyCode::Enter => {
                self.details = true;
                self.detail_scroll = 0;
            }
            KeyCode::Char('r') => self.send(sender, Command::Refresh),
            KeyCode::Char('n') if matches!(self.page, 2 | 4) => {
                self.send(sender, Command::Next(self.page_kind()))
            }
            KeyCode::Char('b') if matches!(self.page, 2 | 4) => {
                self.send(sender, Command::First(self.page_kind()))
            }
            KeyCode::Char('[') | KeyCode::Char(']') => {
                if !snapshot.profiles.is_empty() {
                    let current = snapshot
                        .profiles
                        .iter()
                        .position(|p| Some(&p.id) == snapshot.profile.as_ref())
                        .unwrap_or(0);
                    let len = snapshot.profiles.len();
                    let next = if key.code == KeyCode::Char('[') {
                        (current + len - 1) % len
                    } else {
                        (current + 1) % len
                    };
                    self.send(sender, Command::Profile(snapshot.profiles[next].id.clone()));
                }
            }
            KeyCode::Char('a') => {
                let mut options = self.options;
                options.auto_refresh = !options.auto_refresh;
                self.send(sender, Command::Options(options));
            }
            KeyCode::Char('-' | '+' | '=') => {
                let mut options = self.options;
                options.refresh_seconds = if key.code == KeyCode::Char('-') {
                    options.refresh_seconds.saturating_sub(1).max(2)
                } else {
                    (options.refresh_seconds + 1).min(120)
                };
                self.send(sender, Command::Options(options));
            }
            KeyCode::Char('m') => {
                let mut options = self.options;
                options.monochrome = !options.monochrome;
                self.send(sender, Command::Options(options));
            }
            KeyCode::Char('w') if self.page == 5 => self.send(sender, Command::SaveSettings),
            KeyCode::Char('l') if self.page == 5 => self.send(sender, Command::ReloadSettings),
            KeyCode::Char('d') if self.page == 5 => {
                self.send(sender, Command::Options(Options::default()))
            }
            KeyCode::Char('e') if self.waiting.is_none() => {
                if let Some(intent) = &snapshot.unresolved {
                    self.confirm = Some(Confirm {
                        action: Mutation::Start(intent.profile.clone()),
                        label: format!(
                            "Resolve ONLY original start {} for profile {}. No new start if retry history expired.",
                            intent.request_id, intent.profile
                        ),
                        accepted: false,
                    });
                } else if let Some(profile) = &snapshot.profile {
                    let backend = snapshot
                        .profiles
                        .iter()
                        .find(|p| &p.id == profile)
                        .map(|p| p.backend);
                    self.confirm = Some(Confirm {
                        action: Mutation::Start(profile.clone()),
                        label: format!(
                            "Start WHOLE fixed profile {profile} ({backend:?})? ALL configured devices apply. Selected device row has no effect. Job continues after UI exit."
                        ),
                        accepted: false,
                    });
                }
            }
            KeyCode::Char('x') if self.page == 2 && self.waiting.is_none() => {
                let rows = self.rows(snapshot);
                if let Some(i) = self.selection(&rows)
                    && let Some(action) = &rows[i].target
                {
                    self.confirm = Some(Confirm {
                        action: action.clone(),
                        label: format!(
                            "Request cooperative stop of {}? Accepted stop is NOT proof of device release. Other sessions may be affected when stopping a job.",
                            rows[i].key
                        ),
                        accepted: false,
                    });
                }
            }
            _ => {}
        }
        false
    }
}
fn row(key: &str, label: &str, detail: &str) -> Row {
    Row {
        key: key.into(),
        label: label.into(),
        detail: detail.into(),
        target: None,
        attention: false,
    }
}
fn json<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| "Metadata encoding unavailable".into())
}
fn detail(value: &str) -> String {
    value
        .lines()
        .take(256)
        .map(|line| text(line, 512))
        .collect::<Vec<_>>()
        .join("\n")
}
fn viewport(columns: u16, rows: u16) -> Rect {
    Rect::new(0, 0, columns.clamp(1, 240), rows.clamp(1, 100))
}
fn popup(area: Rect) -> Rect {
    let width = area.width.saturating_sub(4).clamp(1, 96).min(area.width);
    let height = area.height.saturating_sub(2).max(1).min(area.height);
    Rect::new(
        area.x + (area.width - width) / 2,
        area.y + (area.height - height) / 2,
        width,
        height,
    )
}
fn draw(frame: &mut Frame, app: &mut App, snapshot: &Snapshot) {
    let area = frame.area();
    let color = |c| {
        if app.options.monochrome {
            Color::Reset
        } else {
            c
        }
    };
    let base = Style::default()
        .fg(color(Color::Rgb(248, 250, 252)))
        .bg(color(Color::Rgb(15, 23, 42)));
    let focus = Style::default()
        .fg(color(Color::Cyan))
        .add_modifier(Modifier::BOLD);
    frame.render_widget(Block::default().style(base), area);
    if area.width < 28 || area.height < 8 {
        frame.render_widget(
            Paragraph::new("RustUSB\nResize to 28x8\nq / Ctrl-C quits").style(base),
            area,
        );
        return;
    }
    let sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Min(2),
            Constraint::Length(1),
            Constraint::Length(1),
        ])
        .split(area);
    let tabs = PAGES
        .iter()
        .enumerate()
        .map(|(i, name)| {
            if i == app.page {
                format!("[{} {name}]", i + 1)
            } else {
                format!("{} {name}", i + 1)
            }
        })
        .collect::<Vec<_>>()
        .join("  ");
    frame.render_widget(
        Paragraph::new(format!("RustUSB | {tabs}")).style(focus),
        sections[0],
    );
    frame.render_widget(
        Paragraph::new(text(
            &format!(
                "Profile: {}  | auto={} {}s | {} | {}",
                snapshot.profile.as_deref().unwrap_or("none"),
                app.options.auto_refresh,
                app.options.refresh_seconds,
                if app.waiting.is_some() {
                    "REQUEST PENDING"
                } else {
                    "ready"
                },
                snapshot.status
            ),
            240,
        )),
        sections[1],
    );
    let rows = app.rows(snapshot);
    let selected = app.selection(&rows);
    let wide = sections[2].width >= 110;
    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints(if wide {
            vec![Constraint::Percentage(55), Constraint::Percentage(45)]
        } else {
            vec![Constraint::Percentage(100)]
        })
        .split(sections[2]);
    let status = match app.page {
        0 => snapshot.local_status.as_str(),
        1 => snapshot.remote_status.as_str(),
        2 if app.jobs => snapshot.jobs_status.as_str(),
        2 => snapshot.sessions_status.as_str(),
        4 => snapshot.audit_status.as_str(),
        _ => "",
    };
    let title = format!(
        "{}{} | {} rows | {}",
        PAGES[app.page],
        if app.page == 2 && app.jobs {
            " / Jobs"
        } else {
            ""
        },
        rows.len(),
        status
    );
    let items: Vec<ListItem> = if rows.is_empty() {
        vec![ListItem::new(
            "No displayed rows; check freshness, filters and permissions",
        )]
    } else {
        rows.iter()
            .map(|r| ListItem::new(text(&r.label, 512)))
            .collect()
    };
    let mut list = ListState::default();
    list.select(selected);
    frame.render_stateful_widget(
        List::new(items)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(text(&title, 240)),
            )
            .highlight_style(focus.add_modifier(Modifier::REVERSED))
            .highlight_symbol("> "),
        body[0],
        &mut list,
    );
    let selected_detail = selected
        .map(|i| detail(&rows[i].detail))
        .unwrap_or_else(|| "No selected metadata".into());
    if wide {
        frame.render_widget(
            Paragraph::new(selected_detail.clone())
                .block(
                    Block::default()
                        .borders(Borders::ALL)
                        .title("Details [Enter] to scroll"),
                )
                .wrap(Wrap { trim: false }),
            body[1],
        );
    }
    frame.render_widget(
        Paragraph::new(text(
            &format!(
                "{} /{}{} | {}",
                if app.searching { "SEARCH" } else { "Search" },
                app.search,
                if app.attention {
                    " [attention filter]"
                } else {
                    ""
                },
                app.message
            ),
            240,
        )),
        sections[3],
    );
    frame.render_widget(Paragraph::new("Tab/1-6 pages  [/] profile  / search  s sort  f filter  r refresh  Enter detail  ? help  q quit").style(focus),sections[4]);
    if app.details || app.help || app.confirm.is_some() {
        let modal = popup(area);
        frame.render_widget(Clear, modal);
        let (title, content) = if let Some(c) = &app.confirm {
            (
                "Confirm",
                format!(
                    "{}\n\n{}\n\nLeft/Right or Tab to select, Enter confirms; Esc cancels",
                    c.label,
                    if c.accepted {
                        " Cancel     [CONFIRM] "
                    } else {
                        "[Cancel]     Confirm"
                    }
                ),
            )
        } else if app.help {
            ("Keyboard", "1-6 / Tab / Shift-Tab: pages\n[/]: previous/next readable profile\nj/k or arrows: stable selection\n/: search current page; Esc: clear filters\nf: attention filter; s: reverse label sort\nEnter: details; arrows scroll; Esc closes\nv: sessions/jobs on Connections\nn: next service page; b: first page (Connections/Logs)\nr: refresh agent metadata\na: pause/resume polling; +/-: 2..120 seconds; m: monochrome\ne: start WHOLE fixed profile / retry unresolved original\nx: stop selected session/job (Connections)\nq / Ctrl-C / external INT/TERM: exit UI, leave service jobs\nSettings: w saves, l reloads, d restores in-memory defaults. No root or TLS policy editing.\nFiltering/sorting apply only to the displayed finite service page.".into())
        } else {
            ("Details [Esc closes]", selected_detail)
        };
        frame.render_widget(
            Paragraph::new(detail(&content))
                .style(base)
                .block(
                    Block::default()
                        .borders(Borders::ALL)
                        .border_style(focus)
                        .title(title),
                )
                .wrap(Wrap { trim: false })
                .scroll((if app.details { app.detail_scroll } else { 0 }, 0)),
            modal,
        );
    }
}

struct Restore {
    raw: bool,
    alternate: bool,
}
impl Restore {
    fn finish(&mut self) -> io::Result<()> {
        let mut first = None;
        if self.raw {
            if let Err(e) = terminal::disable_raw_mode() {
                first = Some(e);
            } else {
                self.raw = false;
            }
        }
        // Attempt each independent restoration even after an earlier failure.
        if let Err(e) = execute!(io::stdout(), Show)
            && first.is_none()
        {
            first = Some(e);
        }
        if self.alternate {
            match execute!(io::stdout(), LeaveAlternateScreen) {
                Ok(()) => self.alternate = false,
                Err(e) => {
                    if first.is_none() {
                        first = Some(e)
                    }
                }
            }
        }
        if let Err(e) = io::stdout().flush()
            && first.is_none()
        {
            first = Some(e);
        }
        first.map_or(Ok(()), Err)
    }
}
impl Drop for Restore {
    fn drop(&mut self) {
        if self.raw || self.alternate {
            let _ = self.finish();
        }
    }
}

pub(crate) fn run(
    views: watch::Receiver<Snapshot>,
    sender: mpsc::Sender<Envelope>,
    stop: watch::Receiver<bool>,
) -> Result<()> {
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        return Err(Error::InvalidInput(
            "TUI requires terminal stdin and stdout".into(),
        ));
    }
    let mut restore = Restore {
        raw: false,
        alternate: false,
    };
    let operation = (|| -> Result<()> {
        if *stop.borrow() {
            return Ok(());
        }
        let size = terminal::window_size()?;
        restore.raw = true;
        terminal::enable_raw_mode()?;
        restore.alternate = true;
        execute!(io::stdout(), EnterAlternateScreen, Hide)?;
        let backend = CrosstermBackend::new(io::stdout());
        let mut terminal = Terminal::with_options(
            backend,
            TerminalOptions {
                viewport: Viewport::Fixed(viewport(size.columns, size.rows)),
            },
        )?;
        let mut app = App::new(views.borrow().options);
        let mut revision = u64::MAX;
        let mut dirty = true;
        while !*stop.borrow() {
            let snapshot = views.borrow().clone();
            app.pending(&snapshot);
            app.options = snapshot.options;
            if app.waiting.is_none() && snapshot.remote_page != (app.page == 1) {
                app.send(&sender, Command::Remote(app.page == 1));
            }
            if revision != snapshot.revision {
                revision = snapshot.revision;
                dirty = true;
            }
            if dirty {
                terminal.draw(|frame| draw(frame, &mut app, &snapshot))?;
                dirty = false;
            }
            if event::poll(Duration::from_millis(100))? {
                match event::read()? {
                    Event::Key(key) => {
                        if app.key(key, &snapshot, &sender) {
                            break;
                        }
                        dirty = true;
                    }
                    Event::Resize(columns, rows) => {
                        terminal.resize(viewport(columns, rows))?;
                        dirty = true;
                    }
                    _ => {}
                }
            }
        }
        // Avoid Terminal's best-effort hidden-cursor Drop error path.
        terminal.show_cursor()?;
        Ok(())
    })();
    let restoration = restore.finish().map_err(Error::Io);
    match (operation, restoration) {
        (Err(first), Err(second)) => Err(Error::State(format!(
            "terminal failed: {first}; restoration failed: {second}"
        ))),
        (Err(error), _) | (_, Err(error)) => Err(error),
        _ => Ok(()),
    }
}

#[cfg(test)]
mod tests;
