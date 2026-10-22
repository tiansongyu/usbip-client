#![no_main]
use libfuzzer_sys::fuzz_target;
use std::sync::OnceLock;

fuzz_target!(|data: &[u8]| {
    static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    let runtime = RUNTIME.get_or_init(|| {
        match tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()
        {
            Ok(runtime) => runtime,
            Err(error) => {
                eprintln!("fuzz runtime initialization failed: {error}");
                std::process::exit(2);
            }
        }
    });
    // Actual production management/data framing. In-memory slice reads reach EOF
    // immediately, so timers never cause per-input waits; no sockets or devices.
    let _ = runtime.block_on(rustusb_rust_backend::validate_server_frame(data));
});
