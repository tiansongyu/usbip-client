//! CLI-only signals/stdin/stdout; the service library owns every device task.

use super::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{Error, Result};
use rustusb_service::{
    foreground::{self, Control, Event, ForegroundOptions},
    jobs::LoadedJob,
};
use std::{
    io::{BufRead, Read},
    path::PathBuf,
};
use tokio::sync::{mpsc, watch};

fn read_line(reader: &mut impl BufRead) -> Result<Option<Vec<u8>>> {
    let mut line = Vec::with_capacity(256);
    let count = reader.take(4097).read_until(b'\n', &mut line)?;
    if count > 4096 {
        return Err(Error::InvalidInput(
            "local control line exceeds 4096 bytes".into(),
        ));
    }
    if count == 0 { Ok(None) } else { Ok(Some(line)) }
}
fn start_input(sender: mpsc::Sender<Control>, native_only: bool) -> Result<()> {
    // This detached std thread owns stdin and finite messages only. An OS read
    // may stay blocked; it never owns a server, grant or async runtime worker.
    std::thread::Builder::new()
        .name("foreground-test-stdin".into())
        .spawn(move || {
            let input = std::io::stdin();
            let mut reader = input.lock();
            loop {
                let control = match read_line(&mut reader) {
                    Ok(Some(line)) => foreground::parse_control(&line).unwrap_or(Control::Invalid),
                    Ok(None) => {
                        let _ = sender.blocking_send(Control::Eof);
                        break;
                    }
                    Err(_) => {
                        let _ = sender.blocking_send(Control::Invalid);
                        let _ = sender.blocking_send(Control::Eof);
                        break;
                    }
                };
                let control = if native_only
                    && matches!(control, Control::HidKey { .. } | Control::CdcEmit { .. })
                {
                    Control::Invalid
                } else {
                    control
                };
                let invalid = matches!(control, Control::Invalid);
                if sender.blocking_send(control).is_err() {
                    break;
                }
                if native_only && invalid {
                    let _ = sender.blocking_send(Control::Eof);
                    break;
                }
            }
        })?;
    Ok(())
}

pub(super) async fn run(
    job: LoadedJob,
    socket: PathBuf,
    control_stdin: bool,
    mut signals: ShutdownSignals,
) -> Result<()> {
    let native_only = matches!(&job, LoadedJob::NativeServer(_));
    let mut output = EventOutput::new()?;
    let (mut frontend, connection) = foreground::channel();
    let (stop, receiver) = watch::channel(false);
    let mut owner = tokio::spawn(foreground::run(
        job,
        ForegroundOptions {
            helper_socket: socket,
            stop: receiver,
            connection,
        },
    ));
    let mut input_started = false;
    let mut events_open = true;
    let mut signals_open = true;
    let mut adapter_error = None;
    let mut outcome = loop {
        tokio::select! {
            biased;
            result=&mut owner=>break result.map_err(|_|Error::State("foreground owner task ended without a cleanup receipt".into()))?,
            signal=signals.recv(),if signals_open=>{
                stop.send_replace(true);
                if let Err(error)=signal{adapter_error=Some(error);signals_open=false;}
            },
            delivery=frontend.events.recv(),if events_open=>{
                let Some(delivery)=delivery else{events_open=false;continue;};
                let ready=matches!(&delivery.event,Event::Ready{..});
                let value=match delivery.event.to_json(){Ok(value)=>value,Err(error)=>{delivery.complete(Err(error));continue;}};
                let delivered=tokio::select! {
                    biased;
                    signal=signals.recv(),if signals_open=>{
                        stop.send_replace(true);
                        if let Err(error)=signal{adapter_error=Some(error);signals_open=false;}
                        // Dropping only the delivery receipt is safe. The owner
                        // sees its sticky stop and still runs actual cleanup.
                        drop(delivery);continue;
                    },
                    result=output.event(value)=>result,
                };
                let delivered_ok=delivered.is_ok();delivery.complete(delivered);
                if delivered_ok&&ready&&control_stdin&&!input_started {
                    input_started=true;
                    if let Err(error)=start_input(frontend.controls.clone(),native_only){adapter_error=Some(error);stop.send_replace(true);}
                }
            },
        }
    };
    let final_event = outcome.final_event.take();
    let result = outcome.into_result();
    match (result, adapter_error) {
        (Err(owner), Some(frontend)) => {
            return Err(Error::State(format!(
                "owner: {owner}; frontend: {frontend}"
            )));
        }
        (Err(error), None) | (Ok(()), Some(error)) => return Err(error),
        (Ok(()), None) => {}
    }
    if output.in_flight {
        return Ok(());
    }
    let Some(event) = final_event else {
        return Ok(());
    };
    tokio::select! {
        biased;
        signal=signals.recv(),if signals_open=>signal,
        result=output.event(event.to_json()?)=>result,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stdin_bound_counts_newline_and_stops_without_echoing_tail() -> Result<()> {
        assert_eq!(read_line(&mut std::io::Cursor::new(b""))?, None);
        assert_eq!(
            read_line(&mut std::io::Cursor::new(b"{}\n"))?,
            Some(b"{}\n".to_vec())
        );
        assert_eq!(
            read_line(&mut std::io::Cursor::new(vec![b'x'; 4096]))?.map(|v| v.len()),
            Some(4096)
        );
        assert!(read_line(&mut std::io::Cursor::new(vec![b'x'; 4097])).is_err());
        Ok(())
    }
}
