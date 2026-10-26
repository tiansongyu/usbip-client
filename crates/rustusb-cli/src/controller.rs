//! Explicit root-only administrator policy requests. No generated mutation IDs.
use clap::{Args as ClapArgs, Subcommand};
use rustusb_core::{Error, Result, application::RequestId};
use rustusb_daemon::controller::{Change, Command, Mutation, PolicyId};
use rustusb_native::controller::{PciAddress, Selector};

#[derive(Debug, ClapArgs)]
pub(crate) struct Args {
    #[command(subcommand)]
    command: Action,
}
#[derive(Debug, Subcommand)]
enum Action {
    /// Observe a PCI xHCI controller; does not change authorization.
    Inspect { address: PciAddress },
    /// Read historical records and writer status; no live USB authority.
    List,
    /// Query the original request after a lost response, without retrying a write.
    Query {
        #[arg(long)]
        policy_id: PolicyId,
        #[arg(long)]
        request_id: RequestId,
    },
    /// Persist intent and set fixed defaults to deny; requires boot-time device denial.
    Enroll {
        #[command(flatten)]
        identity: Identity,
        /// Exact selector JSON copied from inspect; not a path or program.
        #[arg(long,value_parser=parse_selector)]
        selector: Selector,
    },
    /// Explicitly reverify existing intent for this helper and current boot.
    Reconcile {
        #[command(flatten)]
        identity: Identity,
    },
    /// Retire intent on an empty controller; never restores allow defaults.
    Retire {
        #[command(flatten)]
        identity: Identity,
    },
}
#[derive(Debug, Clone, ClapArgs)]
struct Identity {
    /// Administrator-selected durable policy UUID.
    #[arg(long)]
    policy_id: PolicyId,
    /// Explicit request UUID; retain it for query/replay after a lost response.
    #[arg(long)]
    request_id: RequestId,
    /// Exact global revision returned by list/query.
    #[arg(long)]
    expected_revision: u64,
    /// Actual boot UUID returned by inspect.
    #[arg(long)]
    expected_boot: RequestId,
}
fn parse_selector(s: &str) -> Result<Selector> {
    if s.len() > 1024 {
        return Err(Error::InvalidInput(
            "controller selector exceeds 1KiB".into(),
        ));
    }
    serde_json::from_str(s)
        .map_err(|_| Error::InvalidInput("invalid exact controller selector JSON".into()))
}
impl Args {
    pub(crate) fn request(&self) -> Result<Command> {
        let (identity, mutation) = match &self.command {
            Action::Inspect { address } => {
                return Ok(Command::Inspect {
                    address: address.clone(),
                });
            }
            Action::List => return Ok(Command::List {}),
            Action::Query {
                policy_id,
                request_id,
            } => {
                return Ok(Command::Query {
                    policy_id: policy_id.clone(),
                    request_id: request_id.clone(),
                });
            }
            Action::Enroll { identity, selector } => (
                identity,
                Mutation::Enroll {
                    selector: selector.clone(),
                    expected_boot: identity.expected_boot.as_str().into(),
                },
            ),
            Action::Reconcile { identity } => (
                identity,
                Mutation::Reconcile {
                    expected_boot: identity.expected_boot.as_str().into(),
                },
            ),
            Action::Retire { identity } => (
                identity,
                Mutation::Retire {
                    expected_boot: identity.expected_boot.as_str().into(),
                },
            ),
        };
        Ok(Command::Change {
            change: Change {
                policy_id: identity.policy_id.clone(),
                request_id: identity.request_id.clone(),
                expected_revision: identity.expected_revision,
                mutation,
            },
        })
    }
}
