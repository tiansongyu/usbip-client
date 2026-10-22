#![no_main]
use libfuzzer_sys::fuzz_target;
use std::{
    future::Future,
    task::{Context, Waker},
};

fuzz_target!(|data: &[u8]| {
    // In-memory &[u8] reads are immediately ready: no network, timer, or runtime.
    // All wire counts and allocations are bounded by the production parser.
    let mut input = data;
    let mut future = std::pin::pin!(rustusb_protocol::read_devlist(&mut input));
    let _ = future
        .as_mut()
        .poll(&mut Context::from_waker(Waker::noop()));
});
