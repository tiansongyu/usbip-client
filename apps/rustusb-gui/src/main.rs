//! Ordinary-user native RustUSB console. USB ownership remains in the service.
mod bridge;

use clap::Parser;
use rustusb_core::{Error, Result};
use rustusb_frontend::{Options, Settings, state::Snapshot};
use rustusb_service::config::Manifest;
use std::{
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};
use tauri::Manager;
use tokio::sync::watch;

#[derive(Parser)]
#[command(
    version,
    about = "Native ordinary-user console for a provisioned RustUSB agent"
)]
struct Args {
    /// Protected public agent manifest; does not contain private TLS keys.
    #[arg(long)]
    manifest: PathBuf,
    /// Checked user preferences directory (not an agent configuration).
    #[arg(long)]
    settings_directory: Option<PathBuf>,
}

fn main() -> std::process::ExitCode {
    match run() {
        Ok(code) => std::process::ExitCode::from(if code == 0 { 0 } else { 1 }),
        Err(error) => {
            eprintln!("rustusb-gui: {error}");
            std::process::ExitCode::FAILURE
        }
    }
}
fn run() -> Result<i32> {
    let args = Args::parse();
    if rustix::process::geteuid().is_root() {
        return Err(Error::Permission("GUI must run as an ordinary user".into()));
    }
    let runtime = tokio::runtime::Runtime::new()?;
    let (stop, receiver) = watch::channel(false);
    let (signal_stop, signal_receiver) = watch::channel(false);
    // Register before any blocking manifest/preferences reads.
    let (mut interrupt, mut terminate) = {
        let _entered = runtime.enter();
        (
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?,
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?,
        )
    };
    let signal_sender = stop.clone();
    let signals = runtime.spawn(async move {
        tokio::select! {
            _ = interrupt.recv() => {},
            _ = terminate.recv() => {},
            _ = bridge::stopped(signal_receiver) => return,
        }
        signal_sender.send_replace(true);
    });
    // Keep these tasks and their actual handles alive even if startup is interrupted.
    let directory = args.settings_directory.or_else(Settings::default_directory);
    let loaded = runtime
        .block_on(runtime.spawn_blocking(move || {
            Ok::<_, Error>((
                Manifest::read(&args.manifest)?,
                directory.map(Settings::load).transpose()?,
            ))
        }))
        .map_err(|_| Error::State("GUI startup reader failed".into()))
        .and_then(|v| v);
    let (manifest, settings) = match loaded {
        Ok(value) => value,
        Err(error) => {
            signal_stop.send_replace(true);
            runtime
                .block_on(signals)
                .map_err(|_| Error::State("signal task failed".into()))?;
            return Err(error);
        }
    };
    if *receiver.borrow() {
        signal_stop.send_replace(true);
        runtime
            .block_on(signals)
            .map_err(|_| Error::State("signal task failed".into()))?;
        return Ok(0);
    }
    let options = settings
        .as_ref()
        .map_or_else(Options::default, Settings::options);
    let mut initial = Snapshot::new(options);
    if let Some(settings) = &settings {
        initial.saved_options = settings.saved();
        initial.preferences_status = if settings.has_saved() {
            "Loaded checked preferences"
        } else {
            "No saved preferences"
        }
        .into();
    }
    let (views, observations) = watch::channel(initial);
    let (sender, requests) = tokio::sync::mpsc::channel(1);
    let worker_stop = receiver.clone();
    let service = runtime.spawn(rustusb_frontend::worker::run(
        Arc::new(manifest),
        requests,
        views,
        worker_stop,
        settings,
    ));
    let bridge = bridge::Bridge::new(sender, observations, stop.clone());
    let allowed_exit = Arc::new(AtomicBool::new(false));
    tauri::async_runtime::set(runtime.handle().clone());
    let (app_sender, app_receiver) = tokio::sync::oneshot::channel::<tauri::AppHandle>();
    let exit_guard = allowed_exit.clone();
    let coordinator = runtime.spawn(async move {
        let code = match service.await {
            Ok(Ok(())) => 0,
            Ok(Err(error)) => {
                eprintln!("rustusb-gui: {error}");
                1
            }
            Err(error) => {
                eprintln!("rustusb-gui worker: {error}");
                1
            }
        };
        exit_guard.store(true, Ordering::Release);
        eprintln!("rustusb-gui: local worker joined; agent jobs continue independently");
        if let Ok(handle) = app_receiver.await {
            handle.exit(code);
        }
        code
    });
    let app = tauri::Builder::default()
        .manage(bridge)
        .invoke_handler(tauri::generate_handler![
            bridge::gui_snapshot,
            bridge::gui_action,
            bridge::gui_detail
        ])
        .setup(move |app| {
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("RustUSB")
            .inner_size(1180.0, 760.0)
            .min_inner_size(760.0, 560.0)
            .on_navigation(bridge::local_origin)
            .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
            .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .map_err(|error| Error::State(format!("native GUI startup: {error}")));
    let result = match app {
        Ok(app) => {
            let _ = app_sender.send(app.handle().clone());
            app.run_return(move |handle, event| match event {
                tauri::RunEvent::WindowEvent {
                    event: tauri::WindowEvent::CloseRequested { api, .. },
                    ..
                } if !allowed_exit.load(Ordering::Acquire) => {
                    api.prevent_close();
                    if let Some(state) = handle.try_state::<bridge::Bridge>() {
                        state.request_stop();
                    }
                }
                tauri::RunEvent::ExitRequested { api, .. }
                    if !allowed_exit.load(Ordering::Acquire) =>
                {
                    api.prevent_exit();
                    if let Some(state) = handle.try_state::<bridge::Bridge>() {
                        state.request_stop();
                    }
                }
                _ => {}
            })
        }
        Err(error) => {
            drop(app_sender);
            eprintln!("rustusb-gui: {error}");
            1
        }
    };
    stop.send_replace(true);
    let joined = runtime
        .block_on(coordinator)
        .map_err(|_| Error::State("GUI coordinator failed".into()))?;
    signal_stop.send_replace(true);
    runtime
        .block_on(signals)
        .map_err(|_| Error::State("signal task failed".into()))?;
    Ok(if joined == 0 { result } else { joined })
}
