//! Keep peer-controlled third-party diagnostics out of embedding subscribers.
//!
//! h2 has no production self-spawned tasks in the pinned release. All its calls,
//! future polls, ready-value mappings and destruction stay inside this boundary.

use std::{
    future::Future,
    pin::Pin,
    sync::OnceLock,
    task::{Context, Poll},
};

pub(crate) fn call<T>(f: impl FnOnce() -> T) -> T {
    static DISPATCH: OnceLock<tracing::Dispatch> = OnceLock::new();
    let dispatch = DISPATCH
        .get_or_init(|| tracing::Dispatch::new(tracing::subscriber::NoSubscriber::default()));
    tracing::dispatcher::with_default(dispatch, f)
}

/// Handles cannot escape for destruction outside the quiet dispatch.
pub(crate) struct Quiet<T>(Option<T>);

impl<T> Quiet<T> {
    pub(crate) fn new(value: T) -> Self {
        Self(Some(value))
    }

    pub(crate) fn with<R>(&self, f: impl FnOnce(&T) -> R) -> Option<R> {
        call(|| self.0.as_ref().map(f))
    }

    pub(crate) fn with_mut<R>(&mut self, f: impl FnOnce(&mut T) -> R) -> Option<R> {
        call(|| self.0.as_mut().map(f))
    }
}

impl<T> Drop for Quiet<T> {
    fn drop(&mut self) {
        call(|| drop(self.0.take()));
    }
}

pub(crate) struct QuietFuture<F: Future, M> {
    future: Option<Pin<Box<F>>>,
    map: M,
}

impl<F: Future, M> QuietFuture<F, M> {
    pub(crate) fn new(future: F, map: M) -> Self {
        Self {
            future: Some(Box::pin(future)),
            map,
        }
    }
}

impl<F, M, T> Future for QuietFuture<F, M>
where
    F: Future,
    M: FnMut(F::Output) -> T + Unpin,
{
    type Output = T;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<T> {
        let this = self.get_mut();
        call(|| {
            let Some(future) = this.future.as_mut() else {
                return Poll::Pending;
            };
            match future.as_mut().poll(cx) {
                Poll::Pending => Poll::Pending,
                Poll::Ready(output) => {
                    let mapped = (this.map)(output);
                    drop(this.future.take());
                    Poll::Ready(mapped)
                }
            }
        })
    }
}

impl<F: Future, M> Drop for QuietFuture<F, M> {
    fn drop(&mut self) {
        call(|| drop(self.future.take()));
    }
}
