//! Pinned PCI xHCI default-denial operations for an authorized administrator.
//!
//! These synchronous methods require an actual blocking owner. They neither
//! authorize USB devices nor establish storage ownership or USBFS revocation.
use rustix::fs::{Dir, Mode, OFlags};
use rustusb_core::{BusId, Error, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs::{self, File},
    io::{Read, Seek, SeekFrom, Write},
    os::unix::fs::MetadataExt,
    path::{Component, Path, PathBuf},
};

const READ: OFlags = OFlags::RDONLY
    .union(OFlags::NOFOLLOW)
    .union(OFlags::CLOEXEC)
    .union(OFlags::NONBLOCK);
const DIRECTORY: OFlags = READ.union(OFlags::DIRECTORY);
const MAX_ENTRIES: usize = 256;
const MAX_DEVICES: usize = 64;

/// Canonical PCI domain:bus:device.function, never a filesystem path.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct PciAddress(String);
impl PciAddress {
    /// Validate lowercase fixed-width PCI notation, including slot/function ranges.
    pub fn new(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        let b = value.as_bytes();
        let valid = b.len() == 12
            && b.iter().enumerate().all(|(i, v)| match i {
                4 | 7 => *v == b':',
                10 => *v == b'.',
                _ => v.is_ascii_digit() || (b'a'..=b'f').contains(v),
            })
            && u8::from_str_radix(&value[8..10], 16).is_ok_and(|v| v <= 31)
            && b[11] <= b'7';
        if !valid {
            return Err(Error::InvalidInput(
                "expected canonical PCI dddd:bb:dd.f address".into(),
            ));
        }
        Ok(Self(value))
    }
    /// Borrow the validated address.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl TryFrom<String> for PciAddress {
    type Error = Error;
    fn try_from(v: String) -> Result<Self> {
        Self::new(v)
    }
}
impl From<PciAddress> for String {
    fn from(v: PciAddress) -> Self {
        v.0
    }
}
impl std::str::FromStr for PciAddress {
    type Err = Error;
    fn from_str(v: &str) -> Result<Self> {
        Self::new(v)
    }
}
impl std::fmt::Display for PciAddress {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Administrator selection; PCI location and IDs are not authenticated hardware identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selector {
    /// Canonical location.
    pub address: PciAddress,
    /// PCI vendor.
    pub vendor_id: u16,
    /// PCI device.
    pub device_id: u16,
    /// PCI subsystem vendor.
    pub subsystem_vendor_id: u16,
    /// PCI subsystem device.
    pub subsystem_device_id: u16,
}
/// Identity of a pinned object in this kernel, not a durable capability.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObjectIdentity {
    /// Filesystem device number.
    pub device: u64,
    /// Inode number.
    pub inode: u64,
}
impl ObjectIdentity {
    fn of(f: &File) -> Result<Self> {
        let m = f.metadata()?;
        Ok(Self {
            device: m.dev(),
            inode: m.ino(),
        })
    }
}
/// One current logical root hub and its original policy attributes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RootObservation {
    /// Numeric USB bus, not persistent identity.
    pub bus: u16,
    /// USB speed in Mbit/s.
    pub speed: u32,
    /// Original hub directory.
    pub identity: ObjectIdentity,
    /// Original device-default attribute.
    pub device_attribute: ObjectIdentity,
    /// Original interface-default attribute.
    pub interface_attribute: ObjectIdentity,
    /// Observed device authorization default.
    pub device_default: u8,
    /// Observed interface authorization default.
    pub interface_default: u8,
}
/// One unauthorized direct-root-port USB generation observed while checking topology.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArrivalObservation {
    /// Current Bus ID, never an authorization.
    pub bus_id: BusId,
    /// Current directory identity.
    pub identity: ObjectIdentity,
}
/// Bounded current kernel observation; not a USB or storage grant.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Inspection {
    /// Exact observed selection.
    pub selector: Selector,
    /// Current kernel boot UUID.
    pub boot_id: String,
    /// Strict boot-command-line default-deny prerequisite.
    pub boot_default_deny: bool,
    /// Current global default; -1/0/1/2 are distinct values.
    pub global_default: i8,
    /// Original PCI directory.
    pub pci: ObjectIdentity,
    /// Complete pair of xHCI root hubs, sorted by bus.
    pub roots: Vec<RootObservation>,
    /// Unauthorized direct arrivals; an unsafe or unknown topology is an error.
    pub arrivals: Vec<ArrivalObservation>,
}

fn require(ok: bool, message: &str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(Error::State(message.into()))
    }
}
fn trusted(f: &File, uid: u32, device: u64, directory: bool) -> Result<()> {
    let m = f.metadata()?;
    require(
        m.uid() == uid
            && m.dev() == device
            && m.mode() & 0o022 == 0
            && if directory { m.is_dir() } else { m.is_file() },
        "untrusted controller policy object",
    )
}
fn child(parent: &File, name: &str, flags: OFlags, uid: u32) -> Result<File> {
    require(
        !name.is_empty() && !name.contains('/') && name != "." && name != "..",
        "invalid internal controller component",
    )?;
    let f = File::from(
        rustix::fs::openat(parent, name, flags, Mode::empty()).map_err(std::io::Error::from)?,
    );
    trusted(
        &f,
        uid,
        parent.metadata()?.dev(),
        flags.contains(OFlags::DIRECTORY),
    )?;
    Ok(f)
}
fn chain(root: &File, relative: &Path, uid: u32) -> Result<File> {
    let components: Vec<_> = relative.components().collect();
    require(
        !components.is_empty() && components.len() <= 32,
        "controller path depth exceeds bound",
    )?;
    let mut f = root.try_clone()?;
    for c in components {
        let Component::Normal(n) = c else {
            return Err(Error::State("invalid controller path component".into()));
        };
        f = child(
            &f,
            n.to_str()
                .ok_or_else(|| Error::State("non-ASCII controller path".into()))?,
            DIRECTORY,
            uid,
        )?;
    }
    Ok(f)
}
// Keep every ancestor, not just the final directory. Reopening is a check of
// the original chain; it never replaces a retained capability.
struct DirectoryChain {
    nodes: Vec<(String, File)>,
}
impl DirectoryChain {
    fn open(root: &File, relative: &Path, uid: u32) -> Result<Self> {
        let mut parent = root.try_clone()?;
        let mut nodes = Vec::new();
        for component in relative.components() {
            require(nodes.len() < 32, "controller path depth exceeds bound")?;
            let Component::Normal(name) = component else {
                return Err(Error::State("invalid controller path component".into()));
            };
            let name = name
                .to_str()
                .ok_or_else(|| Error::State("invalid controller path encoding".into()))?
                .to_owned();
            let next = child(&parent, &name, DIRECTORY, uid)?;
            parent = next.try_clone()?;
            nodes.push((name, next));
        }
        require(!nodes.is_empty(), "empty controller directory chain")?;
        Ok(Self { nodes })
    }
    fn check(&self, root: &File, uid: u32) -> Result<()> {
        let mut parent = root;
        for (name, original) in &self.nodes {
            trusted(original, uid, root.metadata()?.dev(), true)?;
            require(
                ObjectIdentity::of(original)?
                    == ObjectIdentity::of(&child(parent, name, DIRECTORY, uid)?)?,
                "controller ancestor generation changed",
            )?;
            parent = original;
        }
        Ok(())
    }
}
fn text(file: &mut File, limit: usize) -> Result<String> {
    file.seek(SeekFrom::Start(0))?;
    let mut bytes = Vec::new();
    Read::by_ref(file)
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)?;
    require(
        bytes.len() <= limit && bytes.is_ascii(),
        "controller attribute exceeds bound or is not ASCII",
    )?;
    if bytes.last() == Some(&b'\n') {
        bytes.pop();
    }
    require(
        !bytes.contains(&b'\n') && !bytes.contains(&b'\r') && !bytes.contains(&0),
        "invalid controller attribute line",
    )?;
    String::from_utf8(bytes)
        .map_err(|_| Error::State("invalid controller attribute encoding".into()))
}
fn attr(dir: &File, name: &str, uid: u32) -> Result<String> {
    text(&mut child(dir, name, READ, uid)?, 128)
}
fn decimal(s: &str) -> Result<u32> {
    require(
        !s.is_empty() && s.bytes().all(|v| v.is_ascii_digit()) && (s == "0" || !s.starts_with('0')),
        "noncanonical controller decimal",
    )?;
    s.parse()
        .map_err(|_| Error::State("controller number exceeds bound".into()))
}
fn hex(dir: &File, name: &str, digits: usize, uid: u32) -> Result<u32> {
    let s = attr(dir, name, uid)?;
    let h = s
        .strip_prefix("0x")
        .ok_or_else(|| Error::State("invalid PCI hexadecimal attribute".into()))?;
    require(
        h.len() == digits
            && h.bytes()
                .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v)),
        "invalid PCI hexadecimal value",
    )?;
    u32::from_str_radix(h, 16).map_err(|_| Error::State("PCI identifier exceeds bound".into()))
}
fn entries(dir: &File) -> Result<Vec<String>> {
    let iter = Dir::read_from(dir).map_err(std::io::Error::from)?;
    let mut out = Vec::new();
    for (i, e) in iter.enumerate() {
        require(i < MAX_ENTRIES, "controller directory entry limit reached")?;
        let e = e.map_err(std::io::Error::from)?;
        let n = e
            .file_name()
            .to_str()
            .map_err(|_| Error::State("invalid controller entry encoding".into()))?;
        if n != "." && n != ".." {
            out.push(n.to_owned());
        }
    }
    out.sort();
    Ok(out)
}
fn uuid(s: &str) -> bool {
    s.len() == 36
        && s.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
            }
        })
}
fn boot_default(cmd: &str) -> Result<bool> {
    require(
        cmd.len() <= 16384
            && cmd.is_ascii()
            && !cmd
                .bytes()
                .any(|b| matches!(b, b'"' | b'\'' | b'\\') || b < 32 || b == 127),
        "unsupported or ambiguous boot command line",
    )?;
    let mut found = false;
    for token in cmd
        .split(' ')
        .filter(|v| !v.is_empty())
        .take_while(|v| *v != "--")
    {
        let key = token.split_once('=').map_or(token, |(k, _)| k);
        if key.replace('-', "_") == "usbcore.authorized_default" {
            require(
                !found && token == "usbcore.authorized_default=0",
                "ambiguous or non-denying USB boot policy",
            )?;
            found = true;
        }
    }
    Ok(found)
}

struct Environment {
    sys_path: PathBuf,
    proc_path: PathBuf,
    sys: File,
    proc: File,
    uid: u32,
    sys_chains: Vec<DirectoryChain>,
    proc_chain: DirectoryChain,
}
impl Environment {
    fn open(sys_path: &Path, proc_path: &Path, uid: u32, real: bool) -> Result<Self> {
        let sys = File::from(
            rustix::fs::open(sys_path, DIRECTORY, Mode::empty()).map_err(std::io::Error::from)?,
        );
        let proc = File::from(
            rustix::fs::open(proc_path, DIRECTORY, Mode::empty()).map_err(std::io::Error::from)?,
        );
        trusted(&sys, uid, sys.metadata()?.dev(), true)?;
        trusted(&proc, uid, proc.metadata()?.dev(), true)?;
        if real {
            require(
                rustix::fs::fstatfs(&sys)
                    .map_err(std::io::Error::from)?
                    .f_type
                    == 0x62656572
                    && rustix::fs::fstatfs(&proc)
                        .map_err(std::io::Error::from)?
                        .f_type
                        == 0x9fa0,
                "controller policy requires real sysfs and procfs",
            )?;
        }
        let sys_chains = [
            "bus/pci/devices",
            "bus/usb/devices",
            "bus/pci/drivers/xhci_hcd",
            "bus/usb/drivers/usb",
            "bus/usb/drivers/hub",
            "module/usbcore/parameters",
        ]
        .into_iter()
        .map(|p| DirectoryChain::open(&sys, Path::new(p), uid))
        .collect::<Result<Vec<_>>>()?;
        let proc_chain = DirectoryChain::open(&proc, Path::new("sys/kernel/random"), uid)?;
        Ok(Self {
            sys_path: sys_path.into(),
            proc_path: proc_path.into(),
            sys,
            proc,
            uid,
            sys_chains,
            proc_chain,
        })
    }
    fn production() -> Result<Self> {
        Self::open(Path::new("/sys"), Path::new("/proc"), 0, true)
    }
    fn anchors(&self) -> Result<()> {
        for (original, path) in [(&self.sys, &self.sys_path), (&self.proc, &self.proc_path)] {
            trusted(original, self.uid, original.metadata()?.dev(), true)?;
            let current = File::from(
                rustix::fs::open(path, DIRECTORY, Mode::empty()).map_err(std::io::Error::from)?,
            );
            require(
                ObjectIdentity::of(original)? == ObjectIdentity::of(&current)?,
                "sysfs/procfs mount generation changed",
            )?;
        }
        for chain in &self.sys_chains {
            chain.check(&self.sys, self.uid)?;
        }
        self.proc_chain.check(&self.proc, self.uid)
    }
    fn boot(&self) -> Result<(String, bool, i8)> {
        self.anchors()?;
        let cmd = text(&mut child(&self.proc, "cmdline", READ, self.uid)?, 16384)?;
        let random = chain(&self.proc, Path::new("sys/kernel/random"), self.uid)?;
        let id = attr(&random, "boot_id", self.uid)?;
        require(uuid(&id), "invalid kernel boot UUID")?;
        let params = chain(&self.sys, Path::new("module/usbcore/parameters"), self.uid)?;
        let global = attr(&params, "authorized_default", self.uid)?;
        require(
            matches!(global.as_str(), "-1" | "0" | "1" | "2"),
            "invalid global USB authorization default",
        )?;
        let global = global
            .parse()
            .map_err(|_| Error::State("invalid global USB default".into()))?;
        // Recheck fixed mount points as well as their retained original descriptors.
        require(
            ObjectIdentity::of(&self.sys)? == identity_path(&self.sys_path)?
                && ObjectIdentity::of(&self.proc)? == identity_path(&self.proc_path)?,
            "sysfs/procfs mount generation changed",
        )?;
        Ok((id, boot_default(&cmd)?, global))
    }
    fn resolve(&self, alias: &Path) -> Result<(PathBuf, File)> {
        let path = fs::canonicalize(alias)?;
        let relative = path
            .strip_prefix(&self.sys_path)
            .map_err(|_| Error::State("controller alias escapes sysfs".into()))?;
        require(
            relative.starts_with("devices"),
            "controller alias is outside physical devices",
        )?;
        let file = chain(&self.sys, relative, self.uid)?;
        require(
            ObjectIdentity::of(&file)? == identity_path(alias)?,
            "controller alias changed while opening",
        )?;
        Ok((path, file))
    }
    fn pin(self, address: &PciAddress) -> Result<PinnedController> {
        // Pin both alias directories before interpreting their kernel-owned links.
        let _pci_aliases = chain(&self.sys, Path::new("bus/pci/devices"), self.uid)?;
        let _usb_aliases = chain(&self.sys, Path::new("bus/usb/devices"), self.uid)?;
        let alias = self.sys_path.join("bus/pci/devices").join(address.as_str());
        let (path, pci) = self.resolve(&alias)?;
        require(
            path.file_name().and_then(|n| n.to_str()) == Some(address.as_str()),
            "PCI alias/address disagreement",
        )?;
        let driver = fs::canonicalize(path.join("driver"))?;
        require(
            driver == self.sys_path.join("bus/pci/drivers/xhci_hcd")
                && hex(&pci, "class", 6, self.uid)? == 0x0c0330,
            "only dedicated PCI xHCI controllers are supported",
        )?;
        let selector = Selector {
            address: address.clone(),
            vendor_id: hex(&pci, "vendor", 4, self.uid)? as u16,
            device_id: hex(&pci, "device", 4, self.uid)? as u16,
            subsystem_vendor_id: hex(&pci, "subsystem_vendor", 4, self.uid)? as u16,
            subsystem_device_id: hex(&pci, "subsystem_device", 4, self.uid)? as u16,
        };
        let mut roots = Vec::new();
        for name in entries(&pci)? {
            if let Some(number) = name.strip_prefix("usb") {
                let bus = decimal(number)?;
                require(bus > 0 && bus <= 65535, "invalid USB root number")?;
                let dir = child(&pci, &name, DIRECTORY, self.uid)?;
                let root_alias = self.sys_path.join("bus/usb/devices").join(&name);
                let (resolved, alias_dir) = self.resolve(&root_alias)?;
                require(
                    resolved == path.join(&name)
                        && ObjectIdentity::of(&dir)? == ObjectIdentity::of(&alias_dir)?,
                    "root hub alias mismatch",
                )?;
                require(
                    attr(&dir, "busnum", self.uid)? == bus.to_string()
                        && attr(&dir, "devnum", self.uid)? == "1"
                        && attr(&dir, "devpath", self.uid)? == "0"
                        && attr(&dir, "bDeviceClass", self.uid)? == "09"
                        && attr(&dir, "authorized", self.uid)? == "1",
                    "invalid xHCI root hub identity",
                )?;
                let speed = decimal(&attr(&dir, "speed", self.uid)?)?;
                let hub = child(&dir, &format!("{bus}-0:1.0"), DIRECTORY, self.uid)?;
                let da = child(&dir, "authorized_default", READ, self.uid)?;
                let ia = child(&dir, "interface_authorized_default", READ, self.uid)?;
                roots.push(RootPin {
                    bus: bus as u16,
                    speed,
                    dir,
                    hub,
                    device: da,
                    interface: ia,
                });
            }
        }
        roots.sort_by_key(|r| r.bus);
        require(
            roots.len() == 2
                && roots[0].bus != roots[1].bus
                && ObjectIdentity::of(&roots[0].dir)? != ObjectIdentity::of(&roots[1].dir)?,
            "expected complete two-hub xHCI topology",
        )?;
        require(
            roots.iter().filter(|r| r.speed == 480).count() == 1
                && roots
                    .iter()
                    .filter(|r| matches!(r.speed, 5000 | 10000 | 20000))
                    .count()
                    == 1,
            "unknown xHCI logical root roles",
        )?;
        let physical_chain = DirectoryChain::open(
            &self.sys,
            path.strip_prefix(&self.sys_path)
                .map_err(|_| Error::State("invalid physical path".into()))?,
            self.uid,
        )?;
        let mut pin = PinnedController {
            env: self,
            path,
            pci,
            selector,
            roots,
            physical_chain,
            initial: None,
            #[cfg(test)]
            observation_hook: None,
        };
        pin.observe()?;
        Ok(pin)
    }
}
fn identity_path(p: &Path) -> Result<ObjectIdentity> {
    let m = fs::metadata(p)?;
    Ok(ObjectIdentity {
        device: m.dev(),
        inode: m.ino(),
    })
}
struct RootPin {
    bus: u16,
    speed: u32,
    dir: File,
    hub: File,
    device: File,
    interface: File,
}

/// Owns the original controller, root hubs and policy attributes until dropped.
/// This is not Clone/Serialize and cannot be reconstructed from an old receipt.
pub struct PinnedController {
    env: Environment,
    path: PathBuf,
    pci: File,
    selector: Selector,
    roots: Vec<RootPin>,
    physical_chain: DirectoryChain,
    initial: Option<Inspection>,
    #[cfg(test)]
    observation_hook: Option<Box<dyn FnOnce() -> Result<()> + Send>>,
}
impl PinnedController {
    /// Pin a current administrator-selected controller under real sysfs/procfs.
    pub fn open(address: &PciAddress) -> Result<Self> {
        Environment::production()?.pin(address)
    }
    /// Original canonical PCI path, for comparing retained helper ownership scope.
    pub fn canonical_path(&self) -> &Path {
        &self.path
    }
    fn observe(&mut self) -> Result<Inspection> {
        let first = self.observe_once()?;
        #[cfg(test)]
        if let Some(hook) = self.observation_hook.take() {
            hook()?;
        }
        let last = self.observe_once()?;
        require(
            first == last,
            "controller topology or policy changed during observation",
        )?;
        Ok(last)
    }
    fn observe_once(&mut self) -> Result<Inspection> {
        self.physical_chain.check(&self.env.sys, self.env.uid)?;
        let (boot_id, boot_default_deny, global_default) = self.env.boot()?;
        let uid = self.env.uid;
        trusted(&self.pci, uid, self.env.sys.metadata()?.dev(), true)?;
        require(
            hex(&self.pci, "class", 6, uid)? == 0x0c0330,
            "PCI controller class changed",
        )?;
        let names: Vec<_> = entries(&self.pci)?
            .into_iter()
            .filter(|n| n.starts_with("usb"))
            .collect();
        let expected: BTreeSet<_> = self.roots.iter().map(|r| format!("usb{}", r.bus)).collect();
        require(
            names.into_iter().collect::<BTreeSet<_>>() == expected,
            "controller root set changed",
        )?;
        require(
            ObjectIdentity::of(&self.pci)?
                == identity_path(
                    &self
                        .env
                        .sys_path
                        .join("bus/pci/devices")
                        .join(self.selector.address.as_str()),
                )?
                && ObjectIdentity::of(&self.pci)? == identity_path(&self.path)?,
            "PCI controller generation changed",
        )?;
        require(
            fs::canonicalize(self.path.join("driver"))?
                == self.env.sys_path.join("bus/pci/drivers/xhci_hcd"),
            "controller driver changed",
        )?;
        for (name, expected) in [
            ("vendor", self.selector.vendor_id),
            ("device", self.selector.device_id),
            ("subsystem_vendor", self.selector.subsystem_vendor_id),
            ("subsystem_device", self.selector.subsystem_device_id),
        ] {
            require(
                hex(&self.pci, name, 4, uid)? == u32::from(expected),
                "PCI selector changed",
            )?;
        }
        let mut roots = Vec::new();
        let mut arrivals = Vec::new();
        for r in &mut self.roots {
            trusted(&r.dir, uid, self.pci.metadata()?.dev(), true)?;
            require(
                attr(&r.dir, "busnum", uid)? == r.bus.to_string()
                    && attr(&r.dir, "devnum", uid)? == "1"
                    && attr(&r.dir, "devpath", uid)? == "0"
                    && attr(&r.dir, "bDeviceClass", uid)? == "09"
                    && attr(&r.dir, "authorized", uid)? == "1"
                    && decimal(&attr(&r.dir, "speed", uid)?)? == r.speed,
                "root hub metadata changed",
            )?;
            let alias = self
                .env
                .sys_path
                .join("bus/usb/devices")
                .join(format!("usb{}", r.bus));
            require(
                ObjectIdentity::of(&r.dir)? == identity_path(&alias)?
                    && ObjectIdentity::of(&r.dir)?
                        == identity_path(&self.path.join(format!("usb{}", r.bus)))?,
                "root hub generation changed",
            )?;
            for (name, f) in [
                ("authorized_default", &r.device),
                ("interface_authorized_default", &r.interface),
            ] {
                require(
                    ObjectIdentity::of(f)? == ObjectIdentity::of(&child(&r.dir, name, READ, uid)?)?,
                    "controller policy attribute changed",
                )?;
            }
            let device_default = decimal(&text(&mut r.device, 8)?)?;
            let interface_default = decimal(&text(&mut r.interface, 8)?)?;
            require(
                device_default <= 2 && interface_default <= 1,
                "unknown root policy value",
            )?;
            roots.push(RootObservation {
                bus: r.bus,
                speed: r.speed,
                identity: ObjectIdentity::of(&r.dir)?,
                device_attribute: ObjectIdentity::of(&r.device)?,
                interface_attribute: ObjectIdentity::of(&r.interface)?,
                device_default: device_default as u8,
                interface_default: interface_default as u8,
            });
            let hub_name = format!("{}-0:1.0", r.bus);
            require(
                ObjectIdentity::of(&r.hub)?
                    == ObjectIdentity::of(&child(&r.dir, &hub_name, DIRECTORY, uid)?)?,
                "root hub interface generation changed",
            )?;
            require(
                attr(&r.hub, "bInterfaceClass", uid)? == "09"
                    && fs::canonicalize(
                        self.path
                            .join(format!("usb{}", r.bus))
                            .join(&hub_name)
                            .join("driver"),
                    )? == self.env.sys_path.join("bus/usb/drivers/hub"),
                "root interface is not owned by the hub driver",
            )?;
            for n in entries(&r.dir)? {
                if n == hub_name {
                    continue;
                }
                if !n.starts_with(&format!("{}-", r.bus)) {
                    let st = rustix::fs::statat(
                        &r.dir,
                        n.as_str(),
                        rustix::fs::AtFlags::SYMLINK_NOFOLLOW,
                    )
                    .map_err(std::io::Error::from)?;
                    require(
                        rustix::fs::FileType::from_raw_mode(st.st_mode)
                            != rustix::fs::FileType::Directory
                            || matches!(n.as_str(), "power" | "ep_00"),
                        "unexpected root hub descendant",
                    )?;
                    continue;
                }
                require(
                    arrivals.len() < MAX_DEVICES && !n.contains('.') && !n.contains(':'),
                    "unsupported nested or changing USB topology",
                )?;
                let bus_id = n.parse::<BusId>()?;
                let d = child(&r.dir, &n, DIRECTORY, uid)?;
                let class = attr(&d, "bDeviceClass", uid)?;
                require(
                    class.len() == 2
                        && class
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
                    "invalid arrival class",
                )?;
                require(
                    attr(&d, "authorized", uid)? == "0" && class != "09",
                    "authorized target or downstream hub blocks controller enrollment",
                )?;
                require(
                    fs::canonicalize(
                        self.path
                            .join(format!("usb{}", r.bus))
                            .join(&n)
                            .join("driver"),
                    )? == self.env.sys_path.join("bus/usb/drivers/usb"),
                    "non-generic USB owner blocks controller enrollment",
                )?;
                require(
                    matches!(attr(&d, "bConfigurationValue", uid)?.as_str(), "" | "0"),
                    "configured target blocks controller enrollment",
                )?;
                for part in entries(&d)? {
                    let st = rustix::fs::statat(
                        &d,
                        part.as_str(),
                        rustix::fs::AtFlags::SYMLINK_NOFOLLOW,
                    )
                    .map_err(std::io::Error::from)?;
                    require(
                        !part.contains(':')
                            && !part.starts_with("host")
                            && !part.starts_with("target")
                            && part != "block"
                            && (rustix::fs::FileType::from_raw_mode(st.st_mode)
                                != rustix::fs::FileType::Directory
                                || matches!(part.as_str(), "power" | "ep_00")),
                        "local USB interface or unexpected descendant blocks controller enrollment",
                    )?;
                }
                let alias = self.env.sys_path.join("bus/usb/devices").join(&n);
                require(
                    ObjectIdentity::of(&d)? == identity_path(&alias)?,
                    "USB arrival generation changed",
                )?;
                arrivals.push(ArrivalObservation {
                    bus_id,
                    identity: ObjectIdentity::of(&d)?,
                });
            }
        }
        self.physical_chain.check(&self.env.sys, uid)?;
        require(
            (boot_id.clone(), boot_default_deny, global_default) == self.env.boot()?,
            "boot policy changed during controller observation",
        )?;
        arrivals.sort_by(|a, b| a.bus_id.as_str().cmp(b.bus_id.as_str()));
        Ok(Inspection {
            selector: self.selector.clone(),
            boot_id,
            boot_default_deny,
            global_default,
            pci: ObjectIdentity::of(&self.pci)?,
            roots,
            arrivals,
        })
    }
    /// Classify a retained original USB path without consulting a replacement Bus ID.
    /// Malformed or unlocated historical scope is an error, never an unrelated owner.
    pub fn contains_original_device(&self, path: &Path) -> Result<bool> {
        Ok(original_controller(path, &self.env.sys_path)? == self.path)
    }
    /// Recover scope only for the exact saved, same-boot physical generation.
    /// This reads metadata, never the USB node or export-eligibility scanner.
    pub fn contains_physical_generation(
        &self,
        bus: &BusId,
        receipt: &rustusb_core::PhysicalDeviceReceipt,
        boot: &str,
    ) -> Result<bool> {
        let check = || -> Result<bool> {
            require(
                self.env.boot()?.0 == boot,
                "physical owner belongs to another boot",
            )?;
            let alias = self.env.sys_path.join("bus/usb/devices").join(bus.as_str());
            let (path, dir) = self.env.resolve(&alias)?;
            let chain = DirectoryChain::open(
                &self.env.sys,
                path.strip_prefix(&self.env.sys_path)
                    .map_err(|_| Error::State("invalid physical scope".into()))?,
                self.env.uid,
            )?;
            let expected = ObjectIdentity {
                device: receipt.sysfs_device,
                inode: receipt.sysfs_inode,
            };
            for _ in 0..2 {
                chain.check(&self.env.sys, self.env.uid)?;
                require(
                    ObjectIdentity::of(&dir)? == expected
                        && identity_path(&alias)? == expected
                        && fs::canonicalize(&alias)? == path,
                    "physical owner generation is absent or replaced",
                )?;
                require(
                    decimal(&attr(&dir, "busnum", self.env.uid)?)? == u32::from(receipt.bus_number)
                        && decimal(&attr(&dir, "devnum", self.env.uid)?)?
                            == receipt.usb_device_number,
                    "physical owner address changed",
                )?;
            }
            let selected = self.contains_original_device(&path)?;
            require(self.env.boot()?.0 == boot, "physical owner boot changed")?;
            Ok(selected)
        };
        check().map_err(|_| {
            Error::State(
                "unknown historical physical controller scope; reconcile the original grant first"
                    .into(),
            )
        })
    }
    /// Read the original controller again, rejecting identity drift. No policy is changed.
    pub fn inspect(&mut self) -> Result<Inspection> {
        self.observe()
    }
    /// Require an exact selector/boot and a current pre-arrival device-denial policy.
    pub fn prepare(&mut self, selector: &Selector, boot: &str) -> Result<Inspection> {
        self.initial = None;
        let s = self.observe()?;
        require(
            &s.selector == selector && s.boot_id == boot,
            "controller selection or expected boot changed",
        )?;
        require(
            s.boot_default_deny
                && s.global_default == 0
                && s.roots.iter().all(|r| r.device_default == 0),
            "boot and current device-default denial are required; earlier exposure cannot be repaired by arming",
        )?;
        self.initial = Some(s.clone());
        Ok(s)
    }
    /// Write only fixed default-denial values, checking the original generation throughout.
    /// Failure may leave a partial denial; no automatic allow-value rollback is attempted.
    pub fn arm(&mut self) -> Result<Inspection> {
        let before = self
            .initial
            .take()
            .ok_or_else(|| Error::State("controller not prepared".into()))?;
        require(
            before == self.observe()?,
            "controller state changed before arming",
        )?;
        for index in 0..self.roots.len() {
            let current = self.observe()?;
            require(
                current.boot_default_deny
                    && current.global_default == 0
                    && current.roots.iter().all(|r| r.device_default == 0)
                    && current.arrivals == before.arrivals,
                "controller policy or arrivals changed while arming",
            )?;
            let r = &self.roots[index];
            for (name, identity) in [
                ("authorized_default", ObjectIdentity::of(&r.device)?),
                (
                    "interface_authorized_default",
                    ObjectIdentity::of(&r.interface)?,
                ),
            ] {
                let mut writer = child(
                    &r.dir,
                    name,
                    READ.difference(OFlags::RDONLY).union(OFlags::RDWR),
                    self.env.uid,
                )?;
                require(
                    ObjectIdentity::of(&writer)? == identity,
                    "controller write target replaced",
                )?;
                write_zero(&mut writer)?;
                require(
                    text(&mut writer, 8)? == "0",
                    "controller policy readback failed",
                )?;
            }
        }
        let after = self.observe()?;
        require(
            after.boot_id == before.boot_id
                && after.boot_default_deny
                && after.global_default == 0
                && after.arrivals == before.arrivals
                && after
                    .roots
                    .iter()
                    .all(|r| r.device_default == 0 && r.interface_default == 0),
            "controller arming verification failed",
        )?;
        Ok(after)
    }
}
fn original_controller(path: &Path, sys: &Path) -> Result<PathBuf> {
    let relative = path
        .strip_prefix(sys.join("devices"))
        .map_err(|_| Error::State("unknown original controller scope".into()))?;
    let mut controller = sys.join("devices");
    let mut components = relative.components();
    while let Some(component) = components.next() {
        let Component::Normal(name) = component else {
            return Err(Error::State("invalid original controller scope".into()));
        };
        let name = name
            .to_str()
            .ok_or_else(|| Error::State("invalid original controller encoding".into()))?;
        if let Some(number) = name.strip_prefix("usb") {
            require(
                decimal(number)? > 0 && controller != sys.join("devices"),
                "invalid original USB root scope",
            )?;
            let mut count = 0;
            for child in components {
                let Component::Normal(child) = child else {
                    return Err(Error::State("invalid original USB path".into()));
                };
                child
                    .to_str()
                    .ok_or_else(|| Error::State("invalid original USB path".into()))?
                    .parse::<BusId>()?;
                count += 1;
            }
            require(count > 0 && count <= 8, "incomplete original USB path")?;
            return Ok(controller);
        }
        controller.push(name);
    }
    Err(Error::State("original USB root scope is missing".into()))
}
fn write_zero(writer: &mut impl Write) -> Result<()> {
    require(
        writer.write(b"0\n")? == 2,
        "short controller policy store; partial effects require reconciliation",
    )
}

#[cfg(test)]
mod tests;
