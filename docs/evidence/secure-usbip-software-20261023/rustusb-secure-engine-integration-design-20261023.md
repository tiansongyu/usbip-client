# Rust engine 的 mTLS 与设备 ACL 接入建议

日期：2026-10-23。状态：**proposed，供本轮实施分工**。只读基线 `056341c30d15157d941566c90193c9d384b51d45`，读取时 Git clean。本报告未修改仓库、未运行 Cargo/QEMU，也未把建议当作集成结果。

使用仓库 `rustusb-development` 与用户 `writing-technical-design` 的接口/来源审查方法。按用户和项目范围，仅保留本报告及固定现有源码依据，不创建逐组件 skills、不另选 TLS 库。TLS 基础仍为已锁定 rustls 0.23.46 / tokio-rustls 0.26.6，Tokio 为 Cargo.lock 的 1.54.1。

## 1. 推荐裁决

增加一个直接 mTLS 的 Rust server 入口，认证完成后把 **AuthenticatedStream 本体**交给现有 generic USB/IP connection/actor。虚拟 HID/CDC 与 USBFS 继续共用该 engine；不建立第二个 loopback/TCP 明文服务，不通过原 `bind` 再套通用 TCP 转发代理。

授权不能放在 server 的“刚才列表里看见某 BusID”逻辑中。`ServingBackend` 接收同一连接的私有访问上下文；DEVLIST 逐设备过滤；IMPORT 在实际 registry 项的锁/固定 Slot 上重新核其稳定 key，在修改 lease 状态、启动 supervisor 或执行 USB 前完成授权。授权返回的 lease 与 key 必须来自同一项，不通过 BusID 再次查表取得另一项。

协议保持：**DEVLIST 是独立的认证连接，回复后结束；IMPORT 是另一条认证连接，成功回复和之后全部 URB 在这同一条 TLS stream 上。** 现 `read_management`/`handshake` 每连接只允许一个 management 请求。不能为“一条安全通道”的措辞改成 DEVLIST→IMPORT 的新协议，也不能在 IMPORT 后切回 plaintext。

无需改 `rustusb-transport` 已验证构造边界：`VerifiedPeer` 仍只能由标准 PKI、ALPN、pin mapping 成功生成；`AuthenticatedStream` 不提供 `get_mut`、`into_inner`、`Deref` 或释放 permit 的捷径。客户端 TLS 握手成功仍不代表服务器批准某设备，必须读取 IMPORT 回复。

## 2. 当前源码接点

| 文件 / 行 | 已核事实 | 接入要求 |
| --- | --- | --- |
| `server.rs:168` | accept 先检查 peer，再获取 16 个连接 permit 之一，之后 spawn；JoinSet 全部拥有 | secure 路径复用任务树及外层 permit，TLS 握手不在 accept loop 串行 await |
| `server.rs:215` | `write_frame` 仅 `write_all`、五秒 timeout | 同一个 timeout 内依次 `write_all`、`flush` |
| `server.rs:222` | DEVLIST 未过滤；IMPORT 调 `claim(bus_id)` | 传访问上下文至 registry；拒绝也用有界且 flush 的 management reply |
| `server.rs:262` | `TcpStream::into_split` 是主要具体 TCP 依赖 | TCP_NODELAY 在接受 TCP 后设；connection 改 generic `AsyncRead+AsyncWrite+Unpin+Send` |
| `server.rs:280` | reader future 与 actor 同任务，mpsc 容量 2，partial frame 不重建 | 保留这一所有权与调度结构，不给 TLS reader 另起 detached task |
| `server.rs:301` | 先关网络，再发起并等待 USB cleanup | TLS 两 half 同时结束；外层 connection permit 留到 cleanup 返回 |
| `session.rs:37` | 私有 ServingBackend 只有列表、异步 claim、settle | 是加入统一授权参数的最小边界 |
| `device.rs:395` | 虚拟设备在共享 State mutex 内匹配 BusID、更新 epoch/leased | 在该锁内从真实 `Profile` 取 key，并在任何状态更新之前授权 |
| `physical.rs:389` | 先选固定 Arc<Slot>，再 tasks/state 锁内标 claiming、启动 supervisor | 用这个 Slot 的 source.id/key 授权；保持同一 Arc、不按 BusID 重开/重选 |
| `physical/observe.rs:352` | 初始化已有 FD、元数据及 sysfs/node receipt；不 claim；初始化含 descriptor I/O | 解析 key 最好在初始化的 USB/阻塞工作之前；解析失败拒绝而非 fallback |
| `physical/safety.rs:32` | 每次实际 interface claim 前还核 generation、当前 fingerprint/完整接口安全 | 继续保留；TLS key 不是代际凭据，不能取代这些检查 |
| `transport/tls.rs:157` | `accept` 五秒，返回 stream 持 server 16-permit | 完整认证成功后才允许进入 USB/IP parser |
| `transport/acl.rs:224` | peer 只读 principal/pin、精确 allows(DeviceKey) | 不增加网络可填写的 principal、wildcard、临时 BusID key |

## 3. 可以直接落地的接口

以下是建议签名，不是当前 API。`rustusb-core::Result` 用于 engine，transport 错误在 listener 边界映射为固定类别，不拼 DER、私钥、网络请求正文。

```rust
// Public, in server.rs; old ServerConfig and bind APIs retain their explicit legacy behavior.
#[derive(Debug, Clone)]
pub struct SecureServerConfig {
    pub listen: std::net::SocketAddr,
    // Optional pre-TLS traffic filter. Empty means PKI/ACL is the remote identity gate.
    // Validate <=64 concrete unicast addresses. Never interpret these IPs as principals.
    pub allowed_peers: Vec<std::net::IpAddr>,
}
impl SecureServerConfig {
    pub fn validate(&self) -> rustusb_core::Result<()>;
}
impl ServerHandle {
    pub async fn bind_tls(
        config: SecureServerConfig,
        backend: VirtualBackend,
        tls: rustusb_transport::TlsServer,
    ) -> rustusb_core::Result<Self>;

    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    pub async fn bind_physical_tls(
        config: SecureServerConfig,
        backend: PhysicalBackend,
        tls: rustusb_transport::TlsServer,
    ) -> rustusb_core::Result<Self>;
}

// Private to rustusb-rust-backend, for example session.rs or a small access.rs.
#[derive(Clone)]
pub(crate) enum ConnectionAccess {
    TrustedNetwork, // Constructed only by the explicit legacy listen branch.
    Authenticated(rustusb_transport::VerifiedPeer),
}
impl ConnectionAccess {
    pub(crate) fn allows(&self, key: &rustusb_transport::DeviceKey) -> bool;
}

// The exact checked key travels together with the actual claimed resource.
// This also gives the engine an audit binding without looking BusID up again.
pub(crate) struct Claimed<L: SessionLease> {
    pub(crate) key: rustusb_transport::DeviceKey,
    pub(crate) lease: L,
}

pub(crate) trait ServingBackend: Clone + Send + Sync + 'static {
    type Lease: SessionLease;
    fn remote_devices(
        &self, available_only: bool, access: &ConnectionAccess,
    ) -> rustusb_core::Result<Vec<rustusb_protocol::RemoteDevice>>;
    fn claim(
        &self, bus_id: &str, access: &ConnectionAccess,
    ) -> impl Future<Output = rustusb_core::Result<Claimed<Self::Lease>>> + Send;
    fn settle_closed(&self)
        -> impl Future<Output = rustusb_core::Result<()>> + Send;
}

// No public API takes arbitrary plaintext S together with a free-standing peer.
async fn authenticated_connection<B, S>(
    stream: rustusb_transport::AuthenticatedStream<S>, backend: B,
) -> rustusb_core::Result<()>
where B: ServingBackend, S: AsyncRead + AsyncWrite + Unpin + Send;

// Only internal explicit TLS or legacy branches can call this primitive.
async fn connection<B, S>(
    stream: S, backend: B, access: ConnectionAccess,
) -> rustusb_core::Result<()>
where B: ServingBackend, S: AsyncRead + AsyncWrite + Unpin + Send;
```

`Claimed` 是一个很小的私有返回包装，不需要新的公共 schema 或 SessionLease transfer 方法。收到它后立即把 `lease` 放入现 `CloseOnDrop`，再创建审计/编码回复；字段 key 在本会话始终不变。既有公开 `VirtualBackend::claim` / `remote_devices` 可保持签名，以明确 `TrustedNetwork` 委托新增私有 `claim_with_access` / `remote_devices_with_access`，避免破坏本地调用者及 legacy 测试。公开本地持有 backend 的调用者仍属于可信应用进程；这个库 API 不是同 UID 不可信插件沙箱。

SecureServerConfig 默认建议仅 loopback/ephemeral 用于库测试，产品地址由严格 broker 配置显式给出。secure 非-loopback 不需要把 `trusted_network=true` 作为 TLS 身份认证的替代；可额外要求 exact peers 做防御，但其授权含义必须独立。helper 当前的最大池及角色政策由 root 的专用 broker 设计决定，不由这个网络配置扩大。

如果 root 希望第一提交少引入一个返回类型，可以保留 `Result<Lease>` 并在 SessionLease 增 `device_key()` 返回创建时保存的 key；安全条件完全相同。**不能**用 claim 完成后重新查公开列表推导 key，不能把事后 key 核验当作副作用前授权。

### listener 的具体控制流

1. config/TLS credentials/policy 在 listener 创建前完成验证；无认证降级。TLS 参数加载失败则没有 listener、也没有新 FD grant。
2. `listener.accept()` 后可做 IP filter、TCP_NODELAY；`try_acquire_owned` 外层 16 个 connection permit 后才 `JoinSet.spawn`。
3. spawn 内 `tls.accept(stream).await`。内部独立 TLS 预算也为 16，clone 共享。错误直接结束此任务；绝不 `connection(plain, TrustedNetwork)` fallback。
4. 成功的 `AuthenticatedStream` 交私有 `authenticated_connection`，仅这里从它的 `.peer().clone()` 创建 `ConnectionAccess::Authenticated`；紧接着 move 整个 stream 入 generic connection。
5. 只调用一次 `tokio::io::split(stream)`。两个 half 不离开这个连接任务；DEVLIST/IMPORT parser、RET_IMPORT 与 imported actor 使用这些同源 half。
6. Secure API 只 bind 一处 TLS listener。保留 legacy `bind` 是显式兼容，不代表 secure mode 可同时启动第二个明文入口。产品配置须选择互斥 mode；Native usbipd 的双栈旁路另由 Native/部署切片拒绝或隔离。

不建议本轮新增公开 `serve<S>(stream:S, peer:VerifiedPeer)`：即使 VerifiedPeer 不能自造，调用者仍可从一条已认证流复制它，再附到另一条 plaintext 流。若以后需对接统一外层 accept/broker，公开入口也应只接 `AuthenticatedStream<S>`，且仍由一个拥有 JoinSet/permit 的 owner 负责 join。

## 4. 稳定 key、原子 claim 和会话绑定

### 虚拟 registry

key 必须来自 `State.profile`，不是 `metadata.bus_id` 字符串：`Hid → DeviceKey::virtual_profile(Hid)`，`Cdc → ...CdcAcm`。在 `shared.lock()` 下同时观察 profile、exported、leased、epoch。

- DEVLIST：只有 exported 且 access.allows(profile key) 且满足 available_only 才复制 metadata。
- IMPORT：在匹配的实际 State 锁内先计算 key、检查 access，拒绝后 epoch/leased/counters/USB 状态全部不变；之后才检查/转换可用性、增加 epoch 并创建 DeviceLease。
- denied key 与未知 BusID 对网络都给现有通用非成功 status 4、无设备正文；authorized busy 继续 status 2。授权检查须先于对该设备 busy/gone 的可见错误，以免向无权客户端泄漏占用状态。
- 所返回 Claimed.key 与该锁内创建的 DeviceLease 同源。原有虚拟 epoch 约束、exclusive import、configuration/reset/UNLINK 不变。

### USBFS registry

推荐给 `Slot` 增私有 `key: DeviceKey`，在 `observe::initialize` 解构 source 后立刻 `DeviceKey::physical(&device.id)`；先严格验证格式，之后继续原 initialize。`Device.id` 是 helper 核过的候选稳定指纹，不是密码学硬件身份，也不包含动态 BusID 授权语义。

- keys 使用 `physical:usb-<64 lower hex>`，不能接受 `native:` 或 `virtual:` 来授权 USBFS。
- DEVLIST 在固定 Slot 上检查 key，再按 state 过滤；无需为列表另扫全系统或请求新的 FD。
- IMPORT 选中 `Arc<Slot>` 后，在任何 `claiming`/`leased` 更新或 supervisor spawn 前 `access.allows(&slot.key)`；最好在取得该 slot.state guard 后立即检查。否认不创建 USB 工作、不改原 snapshot、不产生 driver detach。
- immutable `Arc<Vec<Arc<Slot>>>` 无自动热替换，所选 Arc 一直传给 supervisor。`gone` 仍是原代际 gone，不按相同 BusID/指纹复活它。更换代际需要新 explicit helper grant / 新 registry，并在新 IMPORT 重做 ACL。
- worker 的原 `validate_grant`、`safety::before_claim`、FD/node/sysfs receipt、完整当前接口及安全检查继续运行；这些是 key 之外的设备代际/安全保证。
- physical registry 现已限制 BusID 不重复。建议也在 `from_sources` 拒绝重复 key，避免一条 policy key 意外代表同一 registry 中多个条目；这不是自动合并或移植设备。

反例：A 只允许 HID；列表过滤隐藏 CDC，但攻击者直接发 IMPORT `1-2`，当前 unrestricted `claim(bus_id)` 仍会成功。另一个反例是把早前 A-key/BusID 列表缓存后，registry 用 B-key 替换同 BusID；preflight 列表检查加二次 BusID claim 会把 B 交给只允许 A 的 principal。上述 locked profile / fixed Slot 授权关闭这两条路径。

设备 key 与连接 principal/pin、实际 lease epoch 或 physical receipt 一起形成会话的应用内绑定。URB 阶段不接受新 BusID/principal/key、不重选设备；已有 `devid` 比对及 SessionLease 固定资源继续约束命令。policy 是 startup snapshot：编辑文件不即时撤销现有 TLS stream，停止/join 旧 broker、协调真实 USB cleanup 后再载入新 policy。不要声称 CRL、热撤销或任意 FD 撤销已实现。

## 5. 写入、取消、任务和 permit

```rust
async fn write_frame(writer: &mut (impl AsyncWrite + Unpin), frame: &[u8]) -> Result<()> {
    tokio::time::timeout(wire::FRAME_DEADLINE, async {
        writer.write_all(frame).await?;
        writer.flush().await?;
        Ok::<_, std::io::Error>(())
    }).await.map_err(|_| Error::Timeout("USB/IP response write/flush".into()))??;
    Ok(())
}
```

必须是一次 timeout，不能给 write_all 和 flush 各五秒。所有 management、SUBMIT、UNLINK 回复共用这个函数。`lease.retire(id)` 继续位于回复整个 write+flush 成功之后；partial/failed flush 不代表字节送达，也不能提前释放 response-held USB buffer budget。失败交现有 network close / supervised cleanup，不能重发可能部分写出的帧。

继续用 mpsc(2) reader future + 单 actor；TLS record fragmentation 不改变 USB/IP 有界 parser。不要把单帧 `read_exact` 放进每次 actor select 创建的新 future，否则半包可能在 completion wake 时丢失进度。管理五秒、收到 URB 首字节后的半包五秒、已导入空闲等待不限期的现语义保持。

两个预算用途不同：外层 16-permit 包住握手、management、URB、该任务等待 cleanup 的全过程；transport 内 16-permit 随 AuthenticatedStream 一直活到其 Drop。generic split 的两 half 用同一 Arc 持有它，因此不能在握手后提前丢 TLS permit。连接 teardown 时先结束作用域内 reader/actor，再 drop writer，使两 half 和 TLS stream 实际销毁；TLS permit此时可归还，而外层 permit仍保护正在监督的 USB cleanup。不要为了保留 TLS permit 而把坏网络连接拖到硬件清理结束。

正常完整回复已有 flush，不应把 TLS close-notify 当 USB cleanup 成功证明；异常断开/取消允许直接 Drop，由对端观察 TLS error/EOF。若未来加 graceful TLS `shutdown()`，必须有界、不能让其失败或取消跳过 `begin_close` / `poll_cleanup`，也不能用一个等 close-notify 的新长期任务留住 reader。此切片无需为 clean-close 增加新的后台任务。

`ServerHandle::shutdown` 继续 drop listener→abort_all→join_all→bounded backend.settle_closed；abort 会 drop CloseOnDrop 并启动清理，physical supervisor 继续实际持有 URB/FD。shutdown 期限到达仍 quarantine，不宣称资源释放。CLI 保留信号前注册、prepare 可取消、短 bind 直接 await、取得 handle 后 cleanup 总 await，以及即使 stdout 阻塞/错误也不能跳过 backend.close+Finish 的已有流程。

## 6. 有意义的实际 TLS / 协议验证

这些是本轮实施建议，不是已运行结果。证书用 rcgen 只在 tests 内存生成，服务与客户端都走现真实 rustls 配置，不能用任意构造的 VerifiedPeer、identity-only stub 或 TLS echo 代替 engine 测试。

1. **真实 loopback mTLS DEVLIST + IMPORT + URB**：client A pin仅 HID，B pin仅 CDC。A 的 TLS DEVLIST 只有 HID、B 只有 CDC；各自新 TLS IMPORT、SET_CONFIGURATION、GET_DESCRIPTOR及实际 HID pending IN→本地hid_key→两个report；CDC OUT→IN精确字节。验证整个 IMPORT→RET_SUBMIT 使用同一个 TlsClient stream，没有另开明文连接。
2. **直接未授权 IMPORT、授权优先于 busy**：A 直接IMPORT CDC（从未 DEVLIST也须拒绝）；B 占有 CDC 时 A 仍得到相同拒绝类别，无metadata、无 session计数增加、无排队/lease变化；A/B合法设备继续正常I/O。关闭B后A依然无权；不能把 availability 当权限。
3. **命名空间与身份隔离**：只给 physical/native key 的合法principal对虚拟同名BusID列表为空、IMPORT拒绝；invalid/unknown client cert、未映射pin、missing client cert/ALPN 不得进入backend claim或产生任何USB/IP成功字节。基础transport已覆盖PKI细节，engine集成至少保留若干真实拒绝到“没有设备副作用”的贯通测试。
4. **明文直连 secure port**：给该 TLS listener 发送真实 DEVLIST/IMPORT明文字节；拒绝/超时且backend无新lease，不出现USB/IP正常reply。不允许 silent fallback，也不允许测试创建第二个plaintextlistener。
5. **TLS下 UNLINK/fragmentation**：配置CDC后留pending IN，分段写OUT并跨actor wake，真实回复字节有序且只一次；pending HID IN→UNLINK成功只RET_UNLINK(-104)，之后新request成功；seq wrap/错误devid/management-after-import拒绝及清理依旧成立。复用有意义旧测试helper为generic AsyncRead/Write，不用把旧全部软件计数重复包装成新USBcase。
6. **write+flush同截止与退休**：一个可控AsyncWrite在write成功后阻塞flush；验证总deadline而非两个deadline、flush失败不retire、close仍发生。可以采用dev-only tokio test-util暂停时钟；这项是writer契约单测，不能冒充真实TLS网络验收。另真实TLS测试在client停止读时让server写足够数据，验证bounded任务/cleanup；有限 payload buffer不能凭一帧写完就宣称背压覆盖。
7. **budget贯通**：16个真实TLS认证stream（可以停在management首字节前）占满连接预算，第17条无法使用设备；关闭/超时后新的合法TLS管理和IMPORT可用。还要把一条保持在imported idle，证实不是只数handshake；server shutdown能结束TLS握手、partial management、pending URB三类任务。
8. **真实TLS EOF/异常/信号及第三次可用**：已IMPORT且有pending请求时断TLS/截断帧，必须无新claim、无late completion写另一连接，现backend.snapshot恢复；模拟supervised硬件cleanup未结束时仍busy/quarantine，reap结束才可重用。精确区分fake delayed backend软件夹具与实际USBFS结果。
9. **实际USBFS QEMU后续门槛**：现真实helper发两个固定USBFS capability、TLS A仅允许HID而直接请求FTDI；前后完整driver/config/alt、lease/claim计数及真实设备I/O证明拒绝无变更；合法principal之后FTDI/HID真实I/O、EOF/shutdown恢复原基线、第三轮新TLS复用。热拔/G1G2仍按单独范围记账，不由unit测试替代。
10. **代际/BusID负向**：基于固定slot的实际USBFS热拔路径确认旧session不能转入新代际；软件可用明确标注的 mutable registry fixture在DEVLIST后换key但保持BusID，确认IMPORT必须重新把access带入claim，不复用旧list授权。该fixture只验证engine/trait契约，不能当作已测硬件G1G2；实际虚拟registry授权/计数测试仍必须用生产VirtualBackend。

不必重跑所有18个TLS基础测试的等价复制；新证据应针对真实 USB/IP engine 的权利、phase、流与清理耦合。原native/虚拟/USBFS回归、四软件检查与现背压/信号验收仍由root统一执行。

## 7. 文件分工及外部安全前提

建议 backend 实施者 owns `rustusb-rust-backend/src/{server,session,device,physical,lib}.rs`、`physical/observe.rs`、新增tls test文件和本crate Cargo。`server.rs`、trait和两个adapter必须作为一个接口单元协调；避免两个agent并发改 ServingBackend。root统一Cargo.toml/lock及build。transport现API足够，该集成不用改基础TLS构造/释放边界。

最小新生产依赖是 rustusb-rust-backend→rustusb-transport（后者已存在workspace），rcgen仍仅backend dev-dependency；无需再加TLS实现或复制ring配置。可非optional依赖简化API与feature矩阵；legacy网络行为依旧通过明确API维持，不能把“依赖链接了TLS”称为所有旧server自动加密。

专用非root broker、可信配置/证书加载、client FD→VHCI、Native stub→relay 与helper角色由另外切片负责。已与native_diagnostics对齐：现helper所有allowed_uids共享最大设备池，不可让交互UID复用broker身份/secret/securepool；专用broker UID应与interactive allowed_uids分离，secure池与legacy可直接取的池互斥或显式拒绝legacy动作。helper不能相信自报principal；信任边界来自protectedUDS的真实OS身份，broker才验证TLS并施加ACL。同broker UID其他进程本来就是同一OS信任域，不能用子命令名假装隔离。

`docs/security.md:91`尚写“客户端loopback TCP代理”，已与新anonymous-channel候选不同；产品接入时应同步改为当前接受的实际方案，不能把loopback端口/保密端口当本地UID认证。`architecture.md`工作区表、shared engine说明，backend crate顶层doc与README能力表需在实测后准确更新。Native旁路、两端配置角色、可信文件权限和外链IPv4/IPv6 PCAP仍是完整安全路径的独立交付门槛。

本报告给出的是能落地的 Rust server 接入。完成这些软件测试本身仍不能宣称真实远端内核USB链、Native安全路径、双栈不可旁路或Phase3完成；后续必须让实际kernel客户端经认证TLS、同连接URB及安全helper匿名FD通道完成真实枚举/I/O。

## 固定来源

所有结论先依据本仓库 clean056341c 源码和既有设计。Tokio split 持两个Arc half的实现、tokio-rustls flush/shutdown行为从本机已下载且Cargo.lock固定的官方crate源码读取，无需新增依赖查询；没有援引非官方教程。下面哈希由只读脚本生成，供root归档。

```json
[
  {
    "path": "AGENTS.md",
    "length": 5951,
    "sha256": "abc09c8455825dcc9d2c5b57015c50e61f626b52234a26d5dfabbdae3151b79d"
  },
  {
    "path": ".agents/skills/rustusb-development/SKILL.md",
    "length": 3719,
    "sha256": "10ef52f9f77ca2da0eca60f9fee0f7b7082a6eb4bf0f658cf0e887f581ba2380"
  },
  {
    "path": "docs/architecture.md",
    "length": 23560,
    "sha256": "4de5e58bfadcf29b35f88620189ff35bb415cf3034431b4906a146e608175ecd"
  },
  {
    "path": "docs/security.md",
    "length": 25700,
    "sha256": "247d345170823874b217b7cb6ebe717fb3b85554b98b6eaf31cc3a669c9178c8"
  },
  {
    "path": "docs/research/secure-transport-design.md",
    "length": 12449,
    "sha256": "ce25adea6a943052acc1b42bdf172fb570f751e4b4a8616483b0fe38709d8f61"
  },
  {
    "path": "docs/work-log.md",
    "length": 110209,
    "sha256": "15191003b9958aee586eb7573f98562eb4fb4682909afb702724f3ae57c400df"
  },
  {
    "path": "Cargo.toml",
    "length": 1493,
    "sha256": "72872513bc354a613a6425957cca535f9cfc911a2d0daaddfa0e803651129a96"
  },
  {
    "path": "Cargo.lock",
    "length": 34182,
    "sha256": "80d21fd5934187072aa3d8b115ca095015b1e39206049336032c168c5808d39d"
  },
  {
    "path": "crates/rustusb-rust-backend/Cargo.toml",
    "length": 677,
    "sha256": "23a386c9dd4b477f8005bfd7adee9c971ad944508e4f53f8db9f07a27402fcd4"
  },
  {
    "path": "crates/rustusb-rust-backend/src/server.rs",
    "length": 52043,
    "sha256": "4fc5491fff5e952ed1b808e9293489925d9db3b279384703d4fa941026f3c532"
  },
  {
    "path": "crates/rustusb-rust-backend/src/session.rs",
    "length": 7489,
    "sha256": "d7fe7367f6a1f870268eea22f51bd665b2e4eb90b653ee0499e9bc8d4caa578a"
  },
  {
    "path": "crates/rustusb-rust-backend/src/device.rs",
    "length": 46211,
    "sha256": "28d794af0311c8e2214ab127d9458ec6ec4a07e9bd7b6531d02b550967f5b82e"
  },
  {
    "path": "crates/rustusb-rust-backend/src/physical.rs",
    "length": 28931,
    "sha256": "3d16abe180ad15b317431abf3a1c1a4f9412d67bb14e3b6535beb2551992c976"
  },
  {
    "path": "crates/rustusb-rust-backend/src/physical/observe.rs",
    "length": 31142,
    "sha256": "f71bbc75dad0301c2e578c95a4c8aa5da2b03d037ac3c347ba4e245b9c6d100d"
  },
  {
    "path": "crates/rustusb-rust-backend/src/physical/safety.rs",
    "length": 11175,
    "sha256": "7e341177c4d3bbd9b29ffb01c8fe1dd8e78bdba87b0a333d7ed0df17cfb507d2"
  },
  {
    "path": "crates/rustusb-rust-backend/src/wire.rs",
    "length": 14164,
    "sha256": "e34c8c9d2662d677ff4ba51fefcec1f087e61b8fd800c0caf31b4326b8119374"
  },
  {
    "path": "crates/rustusb-rust-backend/src/lib.rs",
    "length": 670,
    "sha256": "054f9718dbd9689795e910882ee4a52269682047988db72557114cd4c5b07d3e"
  },
  {
    "path": "crates/rustusb-cli/src/virtual_server.rs",
    "length": 10010,
    "sha256": "dfcdf4c2ace5449807aee1b44f589e281cfa5daa22b0428b19f7d7be8594a5cb"
  },
  {
    "path": "crates/rustusb-cli/src/physical_server.rs",
    "length": 9876,
    "sha256": "c3b4e917b67a297ff321239b71ae26be28efd8c41cbe231574acb78ab67d2d7f"
  },
  {
    "path": "crates/rustusb-transport/src/acl.rs",
    "length": 8198,
    "sha256": "f02ebecc37fc37f6e16c9d1c834c79063e7bd0339592d68f904cd337985e2b81"
  },
  {
    "path": "crates/rustusb-transport/src/tls.rs",
    "length": 13397,
    "sha256": "0048c9f2f76ce64cf135f6c430cd996b80e249fe152a70440d99dee8ee38f716"
  },
  {
    "path": "crates/rustusb-transport/src/lib.rs",
    "length": 2588,
    "sha256": "df63a60e6fe75b569af0f12e2d022dca912b07107f723816ca47153c18920d58"
  },
  {
    "path": "crates/rustusb-transport/src/tls/tests.rs",
    "length": 20526,
    "sha256": "93079a1e56fd00ed890dd45213ec8628138ddd5ddced5d907dc7ef67a5e35eb4"
  },
  {
    "path": "/home/ubuntu/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/tokio-1.54.1/src/io/split.rs",
    "length": 4693,
    "sha256": "c938755f089a2b132aa0808f001a6bc1036154b7a59ef00fad69fac70fc234ca"
  },
  {
    "path": "/tmp/rustusb-phase3-tls-research-20261023/source/tokio-rustls-0.26.6/src/server.rs",
    "length": 18834,
    "sha256": "c1c3e997c730c0ed0e9825b2043aff241f8e5680e29b7ce57a9a07ce5e483a57"
  }
]
```
