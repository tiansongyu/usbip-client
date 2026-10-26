# RustUSB 技术架构

[存储接入前隔离设计](research/storage-arrival-quarantine-design.md)仍为proposed；[四组最终QEMU机制验证](research/storage-arrival-quarantine-validation.md)采用专用新HCD先拒绝设备/接口授权，再绑定精确Native代际。它没有产品入口；HCD重建、旧USBFS能力、持久策略与完整交接仍待设计/验收，存储blockers保持。

新增[只读存储检查](research/storage-inspection-design.md)：Linux固定sysfs/procfs观察、严格root-only helper请求、一个actual blocking worker配额及原connection/shutdown总join，绕过mutation/journal锁；独立schema1 metadata不改Device/Session/持久记录，也不发USB能力。现有Native/USBFS存储拒绝保持。

E63[每个授权导入代际的PM策略](research/native-import-power-validation.md)已接两Native路径：只对已钉住的local VHCI generation单次写delay=-1、原FD读回/active与identity复核；secure沿原blocking owner/Observed/shutdown，legacy owned锁随实际worker且typed partial receipt/同锁补偿，helper保留未释放/冲突metadata。无新USB FD、wire/schema或授权，原NULL+gone释放判据不变；永久kernel/sysfs阻塞与其他await点的原取消协调限制保持。

新增[Debian/systemd 安装设计](research/debian-installation-design.md)及[两Ubuntu amd64完整验证](research/debian-installation-validation.md)：包不携活跃配置/状态，root helper与固定非零broker分开；Type=exec非ready，mixed TERM/no SIGKILL/infinite stop、agent先于helper实际结束，替换前核真实unit/cgroup。固定路径只创建缺失叶，不递归chown、不清旧记录；无默认授权/启动，UID与配置/凭据/journal跨purge保留。新unit下Native/USBFS I/O、arm64安装、永久阻塞/断电及硬件仍待验收。

状态：已实现结构与分阶段设计约束；2026-10-24。实现范围及验证结果以工作日志为准。

## 路线

Phase 1 使用 Linux 原生数据通路，Rust 负责发现、校验、权限和操作编排。可选 Rust 后端的同一 USB/IP engine 支持固定虚拟 HID/CDC 和实验性 Linux nusb/USBFS adapter；物理 adapter 的设备来源是 helper 授权的已打开 FD。两条物理路线互斥，同一设备不能同时交给 `usbip_host` 与 USBFS。客户端使用 Linux VHCI，不自行重写内核 USB 请求转发。

```mermaid
flowchart LR
  CLI[普通用户 CLI / TUI / 原生GUI预览] --> Core[共享应用接口与类型]
  Core --> Scan[只读 sysfs 扫描]
  Core --> List[有界 USB/IP DEVLIST]
  Core --> IPC[本机 Unix socket]
  IPC --> Helper[root helper / UID 与策略校验]
  Helper --> Native[固定 usbip 操作]
  Native --> Host[usbip_host]
  Native --> VHCI[vhci_hcd]
  Host <-->|Legacy USB/IP TCP 经外部受管 VPN| VHCI
  Core --> Rust[可选 Rust 共享 USB/IP engine]
  Rust <-->|USB/IP TCP| VHCI
  Virtual[固定虚拟 HID / CDC] --> Rust
  Helper -->|SCM_RIGHTS 固定设备 FD| Physical[普通用户 nusb / USBFS adapter]
  Physical --> Rust
```

上图是旧管理模式：安全管理和 USB 数据通道分离，需要管理员预先建立可信 VPN/隧道与防火墙，不得自动以默认全地址监听启动 usbipd。新增安全模式的稳定key目录使用独立认证连接；IMPORT和其后的URB共享另一条TLS stream。Native两端helper以匿名K/R连接内核，Rust虚拟/USBFS直接在共享engine接受TLS，没有另开的明文listener。具体所有权、协议和范围见[实施契约](research/secure-usbip-integration.md)及[六项实际验收](research/secure-usbip-validation.md)。

## 工作区与依赖方向

| crate | 职责 | 允许依赖 |
| --- | --- | --- |
| rustusb-core | 领域模型、输入类型、状态、错误与后端 trait | serde、thiserror；不含系统写操作 |
| rustusb-logging | 有界typed NDJSON、checked文件轮转、实际writer/reader线程所有权与受限页读取 | core、serde/serde_json、tracing/subscriber、Tokio、rustix、thiserror；不持USB能力、不回业务WAL |
| rustusb-discovery | 共用普通用户本地扫描、kernel提示/周期协调、有限最新快照与实际观察worker停止 | core、linux、serde、Tokio、rustix；service原样re-export，Native CLI不启整套TLS/job依赖 |
| rustusb-protocol | 有界标准管理帧、稳定key前导/目录映射与安全客户端codec | core、serde、serde_json、Tokio |
| rustusb-linux | sysfs 设备/接口/端点扫描、指纹、能力与存储检查 | core、sha2；阻塞读取在调用边界隔离 |
| rustusb-native | 固定 usbip 命令、内核状态、强代际与匿名kernel通道 | core、protocol、serde、Tokio、tracing、rustix |
| rustusb-daemon | root helper、root 管理的策略、OS 身份和 IPC；客户端库 | 前述 crate |
| rustusb-service | 非root job/session owner、profile/action授权、本机应用RPC、持久状态/基础audit及精确Root证明协调 | core、protocol、daemon、native、transport、Tokio；Rust engine可选 |
| rustusb-tui | 可选普通用户六页Ratatui、有限输入/渲染与实际观察器停止 | core、discovery、service的既有公开API；不含helper mutation/TLS private loader |
| rustusb-cli | clap 命令与日志 | 前述公开服务接口，不直接写 sysfs |
| rustusb-rust-backend | 共享有界 USB/IP engine、两个虚拟测试 profile、可选 Linux USBFS adapter | core、protocol、transport、Tokio；`physical-nusb` 才引入固定本地 nusb 和 rustix |
| rustusb-transport | 有界mTLS stream、证书principal/device ACL、可信配置；接入安全broker及USB/IP engine | core、rustls、tokio-rustls、Tokio、sha2、serde、serde_json、rustix、thiserror；rcgen仅测试 |

托管远程目录的网络阶段由service监督的独立JoinSet持有，最多两个entry；慢peer不占单session/WAL监督器，成功审计仍回原监督器提交后回应。lost RPC不提前还槽，外部stop/命令EOF停止并总join原网络owner，不能abort framed driver。见[设计](research/catalog-supervisor-design.md)及[E42验收](research/catalog-supervisor-validation.md)。真实WAL提交仍可等待，不承诺永久FS/DNS阻塞下有界退出。

共享`service::catalog`供agent/direct TLS的连续认证目录观察：最多两个actual本地owner、一个observer内不重叠poll、完成后2..120秒轮询/失败退避、完整最新metadata/resync及stable-key差异。订阅者不持凭据/socket/quota；permission/protocol等终态清当前view，IO失败保旧为stale；busy可见性不当硬件generation。direct沿原FIN/driverjoin，agent停止本地RPC不取消已接受服务端任务；原成功audit每poll保留，增加WAL成本。见[设计](research/remote-catalog-watch-design.md)与[E43验收](research/remote-catalog-watch-validation.md)，CLI/TUI/GUI复用同接口，不以stdout管理设备。

已新增有实际职责的可选`rustusb-tui`及CLI `tui` feature，见[前端契约](research/tui-design.md)与[验收范围](research/tui-validation.md)；共享state/worker/preferences已抽出`rustusb-frontend`，独立`apps/rustusb-gui`复用它，根Native工作区无GTK/Tauri依赖。见[GUI契约](research/native-gui-design.md)和[验收范围](research/native-gui-validation.md)。CLI 的 `rust-backend` feature 选择虚拟服务；`physical-nusb` 另启用物理 adapter 和 daemon 的 `physical-usb` FD 授权接口。默认 Native 和纯虚拟构建不承担 nusb 依赖。设计分别见[虚拟服务端](research/rust-virtual-backend-design.md)与[物理 adapter](research/rust-physical-backend-design.md)；物理路径正在验证，完整 G2 尚未完成。

安全broker已接Native、虚拟及USBFS：`secure-transport`可单独构建Native CLI，`rust-backend`同时启用安全虚拟服务，`physical-nusb`启USBFS。clean b83c931六个双guest场景覆盖实际IPv4/IPv6及I/O/清理/复用，412次软件与精确f9558fc四平台CI另有版本记录；完整Phase3、真实硬件仍未完成。配置中的trusted_network声明、库存在或doctor成功都不能代替该路径的实际验收。

## 具体接口契约

`service::automation`的[E52授权基础](research/automatic-sharing-foundation-validation.md)已接入[E53固定profile运行时](research/automatic-sharing-runtime-validation.md)：显式root policy、严格disk3、独立CRUD/CAS/retry/audit、origin+Start同事务、WAL外manual Stop sticky抑制与实际observer/network/USB owner总join。Native本地自动启动已有双栈QEMU真实I/O；完整固定pool及逐次ACL复核保留，无policy仍不启观察/自动准入且旧disk2保持。物理远端缺all-alt/config receipt而拒绝自动import；dynamic pool/GUI规则编辑/安装等仍未完成，整体设计仍draft。[E54](research/automatic-sharing-rust-qemu-validation.md)已补Virtual远端规则与USBFS本地规则的双栈v2实际故障/I/O/Rootproof验证，产品Rust与ELF保持E53；物理远端自动import限制继续保留。

具体可编译签名见各 crate 公共 API。设计约束：

```rust,ignore
trait DeviceDiscovery: Send + Sync {
    fn scan(&self) -> Result<Vec<Device>, Error>;
}
trait ExportBackend: Send + Sync {
    fn export(&self, bus_id: BusId)
        -> impl Future<Output = Result<(), Error>> + Send;
    fn unexport(&self, bus_id: BusId)
        -> impl Future<Output = Result<(), Error>> + Send;
}
trait ImportBackend: Send + Sync {
    fn attach(&self, peer: Peer, bus_id: BusId)
        -> impl Future<Output = Result<(), Error>> + Send;
    fn detach(&self, port: VhciPort)
        -> impl Future<Output = Result<(), Error>> + Send;
}
```

借用 self 仅存活于 future，后端为 Send + Sync；共享状态持有 Arc，特权变更串行，网络发现独立有界执行。外部超时与取消不是事务回滚：变更命令被取消后重读内核，保留可操作错误。Phase 1 不声称提供跨主机事务。

## 设备标识与状态

Bus ID 是当前寻址，不是持久身份。持久候选 ID 将 VID/PID、可用序列号和控制器/物理端口拓扑编码后散列；不在日志输出原始序列号。无序列号时身份仅绑定物理端口；同型号替换不能被可靠区分，管理员须重新核验，不能宣称硬件密码学身份。

设备描述包含 USB 类/接口和端点、驱动、速度、绑定状态以及“可发现/导出条件/实测转发”分别标记。Composite 的存储判断覆盖所有接口，不只 bDeviceClass。

状态机：Discovered → Available → Exporting → Connecting → Connected → Disconnecting → Disconnected。所有操作可进入 Error，Error/Disconnected 可经 Recovering 重新协调到 Available/Disconnected；无条件自动重连存储被禁止。未加载模块、设备消失、权限拒绝和忙分别报告。内核 sysfs 是重启后的权威状态，内存会话记录只是补充。

## 取舍与待验证

- 原生 usbip 用独立子进程复用 GPL 工具，不引入其源代码。Phase 1 不自行实现 IMPORT 后 URB 流，减少协议和内核生命周期错误。
- DEVLIST 自行实现严格的公开线协议解析，计数/设备字符串/接口有界，独立模拟端到端测试。
- helper 不接受任意命令、路径或用户可写策略；权限失败不降级为直接 root CLI。
- 设备热拔出与 sysfs 变更有竞态；操作前后重新扫描并向用户报告不确定状态。精确恢复仍须真实内核故障测试。
- 两台 Ubuntu 枚举真实 USB 是 Phase 1 必需验收，本宿主编译和 mock 测试不能替代。

## Phase 1 实际接口与截止

可编译领域契约位于 `rustusb-core`；`NativeBackend` 实现 ExportBackend/ImportBackend，并提供 `attach_session` 返回已验证端口、`detach_session` 在同一锁内复核身份。`rustusb-daemon::Request` 是受控特权接口，旧`request`入口及helper的`sessions`保持内核快照/受管状态语义。新`rustusb-service`直接拥有安全job/设备session及清理树，普通operator通过固定profile的应用RPC访问，不能发送任意helper Request。Rust managed IMPORT先获得应用持久准入，再真实claim；Native每能力独立tracked attempt，helper退休与terminal proof同原子事务。没有为概念名建立空trait，前台与托管实际owner已统一于service，CLI仅适配信号/有界stdin/stdout；Foreground不伪造持久准入或tracked correlation，见[共享owner设计](research/shared-owner-design.md)及[验收](research/shared-owner-validation.md)。

| 操作 | 当前截止/资源边界 | 超时语义 |
| --- | --- | --- |
| DEVLIST | CLI总1–120秒，默认10秒；Native预查15秒 | 关闭socket，不进入内核导入 |
| IPC输入/输出 | 各5秒；单帧64KiB、32连接 | 错误/关闭，不开始无效请求 |
| helper变更排队 | 30秒 | 未取得锁则不开始变更 |
| native工具 | 15秒；stdout/stderr各64KiB | kill+最多2秒reap，重读内核，明确不确定副作用 |
| 内核settle | 10秒轮询；快照5秒/单属性2秒 | 不把命令成功当成状态成功 |
| 普通客户端等应答 | 120秒 | 已接受helper变更可继续完成，检查status/sessions后再操作 |

helper 可在读取请求前拒绝未授权 UID。普通客户端首次写入失败时，仅在同一已验证 root socket 上有界读取完整拒绝响应；不得重连重发，也不能把 EPIPE 本身解释为权限拒绝。物理 FD 的 ACK 和接收仍只发生在正常成功响应之后。

完整九状态业务状态机当前提供已测的转换类型；会话级热插拔/操作ID进度事件、所有状态的持久超时记录、重启后未知端口认领和存储恢复仍属于Phase3，未冒充已实现。后续状态事件应包含操作ID、非敏感设备ID、from/to、稳定错误码和单调时间，使用有界事件队列；丢事件后客户端重新拉取权威快照。

## 可选 Rust 服务：共享 engine 与 USBFS 生命周期

`ServerHandle::bind` 和 Linux 可选的 `bind_physical` 委托同一 listener、wire parser 与单会话 actor。内部 `ServingBackend` 负责列表、异步 claim 和关闭协调；`SessionLease` 负责拥有请求、观察真实 completion、请求取消、回复后退休及清理。虚拟设备通过 wrapper 接入，保留已有 pending IN、FIFO 和 UNLINK 语义。Control、IN、OUT 都进入统一活动表；内部递增 ID 独立于可回绕的 wire seqnum，回复上下文不复制 OUT 数据。

| 边界 | 当前实现 |
| --- | --- |
| 网络 | 最多16连接，取得 permit 后才建立任务；JoinSet 回收所有连接任务 |
| 会话与帧 | 每会话最多64请求；每传输最多64KiB；分配前检查方向、端点、长度与不支持的 ISO |
| 截止 | 握手、收到首字节后的半包及单次写入各5秒；已导入但无数据的空闲连接不按此超时 |
| 物理缓冲 | 全服务16MiB，覆盖排队、USB在途、取消待回收和待回复数据；回复完成或网络关闭并排空队列前不提前归还 |
| 物理调度 | 每端点一个真实在途请求，其余有界 FIFO；配置/alternate 变更先协调受影响请求，后续请求等待新状态 |
| 关闭 | 停止 accept，终止并回收网络任务，再等待 backend 最多10秒；未确认清理返回隔离错误，不标为可用 |

CLI的`virtual-server`和`physical-server`共用私有`ShutdownSignals`。Unix入口在第一次await、helper通信和listener创建前同步注册SIGINT与SIGTERM；注册失败不启动服务。物理prepare只取得/校验FD和backend，尚无listener或IMPORT/claim，可在信号到达时取消；已接受的helper工作仍可能结束，已运行的spawn_blocking读取也不会被终止，runtime仍可能等待它。短bind步骤直接await取得handle，避免丢弃已开始工作的server。

取得server后，信号、控制EOF和输出错误都汇入同一次`server.shutdown().await`，cleanup错误优先。`EventOutput`先于bind创建；被信号中断的在途stdout不再重写stopped，清理完成后的末输出也可被信号中断。两种监听保留到返回，因此重复信号不通过外层select丢弃cleanup；这是源码所有权约束，未作为重复信号运行验收。非Unix保留Ctrl-C路径；helper daemon、SIGKILL/abort、跨进程监督及持久重启协调不在本次实现范围。设计及运行边界见[信号停止报告](research/server-signal-shutdown.md)。

Linux 物理 adapter 使用固定 nusb 0.2.8 本地许可源码的小扩展，保留原 usbfs 提交、取消和回收实现。扩展暴露真实 Control/端点 status、actual 和 partial 数据，允许合法的原始 IN 长度；不以扩大缓冲后丢弃字节代替正确传输。依赖版本、差异和许可见[第三方记录](third-party.md)。ISO 明确不支持；HID/FTDI 或固定虚拟 profile 的结果不证明任意 Composite、alternate、物理 CDC、摄像头或音频可用。

物理注册只接受 `PhysicalSource` 中已打开的 FD、设备元数据和代际 receipt，CLI 不接受任意 USB 路径。root helper 按调用 UID、`physical_device_ids`、可信网络声明及当前全部接口安全状态授权，打开固定 `/dev/bus/usb/BBB/DDD` 字符节点。该列表与 Native 的 `export_device_ids` 必须不相交，旧策略缺少物理列表时默认空。节点类型、权限、设备号及节点/sysfs 的 dev/inode、USB 地址前后匹配，并使用 NOFOLLOW/CLOEXEC。受保护 UDS 先交付有界 JSON，收到单字节 ACK 后才通过 SCM_RIGHTS 交付一个 FD；接收方复核 root peer、FD 数量、标记、截断和 receipt。

打开能力不等于 claim：服务在 IMPORT 时独占当前设备的全部已验证接口。slot 经 `available → claiming → leased → closing`，成功清理才回到 `available`；无法确认进入 `quarantined`，真实消失进入 `gone`。queued UNLINK 不提交硬件；active UNLINK 等真实 terminal 裁决，取消赢才回复 -104 且不再回复原 SUBMIT，完成赢则保留原 errno/partial 并回复 UNLINK0。取消不能撤回已送出的部分 OUT 数据。

显式释放使用本地nusb扩展的Linux `Interface::release_checked()`：共享引用返回Busy，最后owner只尝试一次真实RELEASEINTERFACE，保留errno且析构不重发；portable release/Drop仍维持原契约。worker逐项await所有接口并保留首错，释放失败不执行SET_CONFIGURATION；临时alternate claim也显式checked释放。ioctl成功仍不等于原状态恢复，独立config/alternate/driver/grant核验保持。实现与最终QEMU范围见[checked release报告](research/usbfs-checked-release.md)。

每个新接口 detach/claim 前，物理可选 feature 复用 `rustusb-linux` scanner 重新检查当前配置的设备身份、完整接口集合、存储驱动/块后代和外部占用；此前已取得的 usbfs 接口仅按本次持有集合放行。扫描前后核对配置和代际，配置 0 不执行接口 claim。释放自有接口后、恢复配置或驱动前也重新检查，不能在新配置已经出现危险驱动时盲目回滚。该检查不能预知所有 inactive vendor quirk 或消除外部管理员的 TOCTOU。

EOF、写失败、Drop 和 shutdown 只发起关闭。监督器保留真实请求直到回收，释放实际取得的接口；恢复 alternate 和驱动仅限本会话确证取得的原接口，不接管新外部占用。每个原接口恢复前重新观察，内核 probe 已同步恢复 sibling 或原 alternate 时不重复 claim/CONNECT；CONNECT 错误也只有实际原驱动已在同代际/原配置恢复才能收敛。仅当本会话修改过配置且当前仍匹配最后确认状态并通过安全检查时，才可恢复原配置。原配置、alternate、驱动和代际须独立核对；超时或不确定时保持隔离，不能用丢弃 future 冒充取消。热拔会关闭对应 TCP，即使没有活动 URB；旧 slot 不重新打开 Bus ID，重插需要重新核验并获取新代际 FD。

FD 是整台设备的写能力，helper 不介入后续每个 USB 请求，策略变更或关闭 helper socket 无法撤销已经交付的 FD。它不是逐请求沙箱，也没有实现可撤销的持久租约；新增的持久授权记录只协调可信服务的合作结束。部署边界见[安全模型](security.md)，使用方法见[物理服务指南](rust-physical-server.md)。干净37bc6b4的USBFS正常/热拔及原虚拟回归均已通过；软件检查不能代替双端实际 I/O、驱动恢复、热拔及真实硬件验收，最新结果见[工作日志](work-log.md)。

[SIGKILL/首次重启负向](research/physical-crash-recovery.md)已证实旧版本重启会采用空驱动为新 original。新增的 schema 1 物理授权账本在 FD 交付前保存完整原配置/接口/alternate/driver、代际与 OS owner，同指纹未决记录阻止新 grant。helper 在 root 管理的目录内用独占锁和原子文件/目录 fsync 保存，损坏和不确定提交拒绝继续使用。普通服务 shutdown 后 join 自有后台任务并释放 registry，`FinishPhysical` 验证原 OS owner 和实际原基线再退休记录；未知交付或初始化取消仍需协调。管理员只读 `PhysicalPending`/`ReconcilePhysical` 可检查恢复后的同代际或明确确认旧代际已消失，不执行恢复写入。具体契约见[实施切片](research/physical-crash-recovery-design.md)，使用方法见[指南](rust-physical-server.md)。

持久记录不是 FD 撤销或自动崩溃恢复：helper 不保留旧 USBFS OFD 来冒充独立恢复 open，原会话实际修改范围仍由后端内存管理。默认策略不自动恢复，不能仅凭 PID 消失清记录。显式opt-in的同配置/alternate CONNECT-only候选实现见下文。软件和 QEMU 证据分别记账，旧负向结果保持。

[独立句柄机制实验](research/usbfs-guardian-mechanism.md)在clean da764d0验证了独立B、A副本延长占用、另一OFD C的非抢占拒绝和B恢复后真实复用；实现位于guest-only测试工具，未改变上述产品架构或schema1权限契约。

[原驱动恢复实施设计](research/physical-driver-autorestore-design.md)新增精确设备许可和磁盘schema2的不可变恢复范围，旧record保持manual；生命周期IPC仍为1。单后台任务在原owner确已结束后独立open B，实际blocking闭包拥有helper全局变更锁，依次完整观察/CONNECT/关闭B/严格核验/持久退休。后台不跨USB调用持有journal mutex，pending仍可读；合作退出停止新工作并join已开始工作。默认关闭、每helper实例每lease最多一次尝试，任何不确定保留记录。clean2ab9976已通过两个产品自动恢复QEMU正向及五个原回归，见[报告](research/physical-driver-autorestore.md)；后续[clean4565f7a边界验收](research/physical-driver-autorestore-negatives.md)完成固定模型的撤销/旧producer迁移/外部占用三组合；仍不外推真实硬件、跨配置/alternate、非claim FD或完整恢复矩阵。

## 技术依据

完整内核与 Rust 审计将写入 `research/`。Rust 2024 起点为 [1.85](https://doc.rust-lang.org/edition-guide/rust-2024/index.html)，当前构建使用安装的稳定 1.97.1；最终依赖版本以 Cargo.lock 为准。
[Tokio timeout](https://docs.rs/tokio/latest/tokio/time/fn.timeout.html) 通过丢弃 future 取消；[process Command](https://docs.rs/tokio/latest/tokio/process/struct.Command.html) 默认不终止子进程，因此实现必须 kill/reap，不能只包 timeout。
[clap derive](https://docs.rs/clap/latest/clap/_derive/) 用于一致输入解析，[tracing subscriber](https://docs.rs/tracing-subscriber/latest/tracing_subscriber/fmt/index.html) 提供人类可读/JSON 日志。上述版本在锁文件生成后记录，不用 latest 代替测试版本。

## Phase 1 后续：运行诊断与内核代际协调（实施设计）

2026-10-22，基线21e98f9。上一轮交付是实质进展；当前仍按Phase1推进，不以物理设备缺席跳过可自动完成的工作，也不将后续阶段标成已实现。本节先定义本轮变更，运行结果见工作日志和独立证据。

### 运行诊断

原doctor只有Path.exists布尔值，无法证明发行版wrapper有匹配工具或socket为可信服务。新增 `rustusb_native::diagnostics::diagnose_native()`，返回可序列化的system、usbip_host、vhci_hcd、usbip、usbipd诊断。内核模块的已加载、有效sysfs和对应内核的可用模块/config证据分开；读取不到只能是unknown，不推断缺失。工具仅允许固定的`/usr/bin/usbip version`与`/usr/bin/usbipd --version`，校验可执行文件及原/canonical父链权限，固定argv与环境、读取/执行截止、输出上限、kill/reap。每个探针总截止3秒，stdout/stderr各8KiB，kill后最多2秒reap；成功退出且安全版本输出才能available，非零退出的root拒绝为requires_root，不启动服务。当前helper不加载模块。

helper诊断属于共享客户端接口：复用已有root路径/类型/权限和SO_PEERCRED验证后调用只读Status，总截止8秒，区分missing、untrusted、unreachable、timeout、permission_denied、available等结果。CLI组装两份诊断，不直接写sysfs或调用管理命令。doctor与helper Status的JSON均声明schema_version=1；旧helper无此版本会报告unexpected_response，需同时升级。NativeSession新增字段允许旧JSON反序列化为None，但无代际receipt不能授权detach。字段附可操作建议；可用组件不等于角色整体ready，角色所需组件分别列出。doctor退出0仅表示完成观察。网络、VPN、TLS、真实硬件仍明确未被doctor验证；`trusted_network`只表示管理员声明。

### 会话代际与直接重连

现有NativeSession仅有可复用的端口/local_busid/remote_devid/VIDPID，不能区分同一设备在同一端口的新连接。新增本地设备地址及sysfs目录的dev/inode代际receipt，只有状态USED且前后元信息来自同一代际才返回。Native与helper的身份比较必须包含receipt；旧ownership不得控制外部重新导入的同型号同地址设备。

在新attach产生副作用前，helper协调缓存的旧受管记录：以权威内核快照为依据，只有端口已NULL且旧设备代际已消失才移除旧记录；保留未知/仍活跃资源。对NULL端口的detach只观察旧代际，不影响同路径新代际。新attach成功后保留冲突防护；若发生本地记录冲突，使用本次已核验receipt安全清理新连接并报告结果，不能先创建内核会话再无清理地拒绝认领。

Native detach命令成功后，只对同一status属性两次已成功解析但内容不同的快照，在原settle截止内只读重观测，不重发命令。公开port_states和操作前核验仍对这种变化报错；其他读取/解析/代际错误仍立即失败，稳定的新USED身份立即拒绝。报告清理成功仍须同端口NULL与原设备代际消失，不能以“读取中变化”当成已释放。

本轮不引入完整持久会话日志、自动存储恢复或Phase3心跳状态机。内核ERROR状态身份不明时继续明确要求协调；安全拒绝不能被写成自动恢复完成。

### QEMU 验证矩阵

保留默认双guest HID两周期，增加独立场景：RustUSB export→官方Linux client、官方bind/usbipd→RustUSB client，以及受管连接被内核外部释放→直接RustUSB重连→外部释放→同port外部重新导入(旧helper必须拒绝控制)。每项要求实际输入/字节传输、驱动及资源释放，未知外部资源仍由官方工具清理。

可选QEMU usb-serial经官方6.2源码核实为FTDI FT232BM(0403:6001，厂商类、Bulk)，使用ftdi_sio并测试双向固定序号字节；它不是CDC ACM证据。只有HID reconnect场景测试代际；串口覆盖两后端端点互操作和两周期释放。三个guest多客户端争用、netem延迟/丢包、完整CDC/Rust服务端仍在原计划中，不能由本轮矩阵替代。测试只用临时VM、私有socketpair/Unix chardev；不触宿主USB、内核模块或公共网络。

## Phase1补充验收：多客户端与传输故障（实施设计）

2026-10-22，基线67c83d5。争用harness以三个独立guest内核验证单设备独占：server经两个独立私有socketpair接口分别连接client A/B；A成功导入并实际输入时，B的导入必须明确失败且无新增端口，A仍能实际输入；A释放后B可成功导入、实际输入、释放，最后server unexport恢复本地驱动。普通UID管理路径和各端授权不能用guest root直接导入代替。新测试使用独立脚本，保持此前七项harness可复现。

故障harness在已完成实际输入的连接上，由guest管理员精确中止唯一ESTABLISHED USB/IP TCP（只限隔离来宾）；核对内核端口变为NULL、旧sysfs代际消失、server AVAILABLE后，由RustUSB直接重连再验证实际输入与释放。杀usbipd进程、carrier down和本地usbip detach各有不同语义，不互相冒充。Linux普通TCP错误通常SHUTDOWN|RESET而非持久ERROR；若观测到UNKNOWN/ERROR，保留原始状态并拒绝猜测清理。此项不证明WAN黑洞检测、心跳或自动恢复存储写入。

软件平台验证扩展为Ubuntu22.04/24.04 × x86_64/aarch64原生CI，运行前以uname验证实际架构并记录系统/工具链；采用[官方标准运行器](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)，结果待实际四job完成。CI不运行物理USB变更；aarch64编译/单元/只读smoke通过也不证明ARM USB/IP内核路径兼容。


### 显式framed-v2数据链

[E60关闭契约](research/framed-management-race-design.md)与[验收](research/framed-management-race-validation.md)：server在唯一stream1 active期间开始graceful禁止新stream；原EOS/实际消费/credit/FIN flush后继续poll唯一原connection。外置于TrackingIo的ClosingIo在真实write shutdown后保同transport读至TLS peer EOF，64KiB bounded discard/1KiB/每poll32读预算，不声称尾部h2语义验证；UnexpectedEof/reset及原stop/deadline保留。原Root设备清理独立，raw1不改。新旧preview混合FAIL，需协调两端升级后才重启导入，不能凭同ALPN推断兼容；E59原加密间歇原因未证明。

[设计](research/data-link-health-design.md)与[验收](research/data-link-health-validation.md)记录可选schema2/ALPN2。每个实际TCP/TLS连接仅一h2数据stream，driver独立poll控制帧/接收pump，metadata monitor不持设备或permit；flow credit只返还实际应用消费。真TLS DATA/FIN flush fence保留IMPORT证据，不以入队/PONG替代。Catalog和完整拒绝回复双方有界FIN/EOF并保IO至自然join；成功IMPORT继续同流URB。Raw1保持默认且heartbeat/RTT不可用。

应用旧Session/Metrics/IPC1保持，新只读SessionHealth逐profile Read授权；私有disk2严格迁移/容量预留及epoch历史，probe不逐次fsync。所有actualowner停转/清理先于保留ACK等待，不把driver结束或新进程资源0当Root恢复证明。完整Phase3、pendingURB/内核active窗口和真实G1/G2未由软件626或QEMU十五项自动完成。

## 准备期停止与记录等待（E34）

[设计](research/prepare-recording-shutdown-design.md)与[英文验收](research/prepare-recording-shutdown-validation.md)区分实际资源、操作结果与原记录事务。Native每个已收到export在Resource ACK前注册唯一actual-close watcher；后续设备的Begin/Attempt/Resource与Available记录不阻挡此前owner关闭。局部StopSession只关闭目标，Catalog/直接IMPORT立即拒绝已停目标，保留其他export；全局stop或真实资源失败才关闭所有owner。全部局部停完仍保留原job/listener及空Catalog。

USBFS先释放全部sources或实际server/backend，完成所有已收到grant的Finish，再等原ACK；accepted initializer保留至真实返回。client远端IMPORT后的Attempt等待被停止/断链中断时先join网络，晚ACK不触发新helper申请。共享私有pending future仅持metadata，不能持FD、stream、permit或helper能力；原Begin/Attempt的注册结果不能丢弃。

agent shutdown由独立supervisor路径在Runtime persist之外sticky fanout；晚注册job/session继承stop。实际owner总await后仍等待原记录，故设备已释放时job仍可能pending。保守reserved-unsent仍Unconfirmed，不造NotStarted/Verified，也不借此关闭safe sibling。永久文件系统/内核阻塞、内核内部active窗口与真实硬件仍单列限制。

## 有界进程日志（E35）

[设计](research/rotating-logs-design.md)、[操作指南](logging.md)与[验收](research/rotating-logs-validation.md)区分可选文件日志、业务audit事务与实际USB所有权。CLI/helper同步外层main在Tokio/设备之前打开日志，async操作和实际owner清理结束后才关闭新读入、drain并join真实logger线程。默认stderr保留；`--log-directory`选择file-only NDJSON并与`--log-json`互斥。无stderr tee，也不把logger文本写入机器stdout。

record最大2KiB、固定256队列、默认4MiB×8文件、硬上限8MiB×16（128MiB payload）。0700叶/0600单链接regular文件与受保护祖先逐FD核验；writer持生命周期锁，每次access锁独立open而非dup。整条记录轮转，UUID/epoch/ordinal及完整行offset游标校验；旧残尾保留并报gap，错误不静默重开。投递不做FS、不等队列容量；满队列/IO失败和最终exit与USB操作结果分开。

业务Store成功commit并替换state后，新增audit序列才投递typed镜像；镜像无持久准入、Rootproof或恢复权力。ReadLogs按真实SO_PEERCRED及profile Audit授权，先授权后读盘；所有已授权结果包括Busy/disabled/error绕Runtime队列，拒绝沿旧durable denial路径。两实际reader与两queued请求的配额不因RPC取消释放；每页最多扫描256KiB/16记录，过滤空页仍可推进游标。RPC不能选择路径/保留参数或读取helper日志。logger只持日志文件/metadata，永久FS阻塞仍可能阻止实际join，不能用超时虚称取消。

## 私有诊断与已提交视图（E36）

[设计](research/diagnostic-export-design.md)、[操作指南](diagnostics.md)与[验收](research/diagnostic-export-validation.md)记录rustusb-diagnostics闭合schema1/4MiB产物。每次独立alias只保留metadata关系；本地固定Native/helper观察不扫描设备，agent模式仅用public manifest而不读broker凭据。操作/cleanup/linkend独立保留，health/log非同一原子视图。

service在Store实际commit/state替换/旧audit镜像之后发布immutable metadata；授权Begin/Page/End及错误绕WAL，拒绝仍durable。每请求双Read+Audit和真实peer/profile校验，root也只own-unscopedDenied。四实际Arc snapshot、每UID一/30秒，Weak UID索引防止End时仍copy提前复用配额；每页16row/60000payload，过滤后offset严格校验。快照无USB能力/permit/文件路径。

exporter先实际打开受保护caller0700父与fresh0600 partial，再收集与真实write/hash/fsync/NOREPLACE/dirsync，拒绝覆盖。Native child kill后实际reap，所有probe/agent/local logs/file worker保留owner总join，专用runtime保留内部TokioFS工作。60秒预算仅停止新stage，永久OS阻塞仍可拖延退出；信号与publication race保实际结果。默认stderr返回错误由metadata latch捕获，禁subscriber递归打印，actualcleanup后报告loggingIO；不承诺永久stderr阻塞/任意formatter panic安全。


## 服务端安全观测

[设计](research/server-security-observations-design.md)和[验收](research/server-security-observations-validation.md)限定volatile loaded-TLS-configuration scope；clone共享计数，TLS/独立framing/outer budget分开，Drop取消、饱和与一致性显式。短metadata锁不做日志/FS/await，不持凭据、stream、permit或USB owner；计数不参与准入或cleanup。前台server_security control与agent固定Profile Read读取使用单独variant，授权所有结果绕WAL、拒绝仍durable，service instance明确重启。旧Session/Metrics/disk/诊断bundle不变；此接口不是持久安全审计或前epoch恢复证明。

## TLS认证准入配额

[设计](research/authentication-admission-design.md)与[验收](research/authentication-admission-validation.md)定义独立于durable USB准入的时间配额。固定Governor0.10.4/std-only、显式MonotonicClock；先try连接permit，再原子metadata锁内check+计数，随后原TLS阶段。refusal不读写IO、不等待、不持USB owner；失败不退款，已建立数据流不重复过门。loaded配置共享实际gate，独立monitor只持数值，poison拒绝新准入；旧TLS/framing/outer计数方程不改。Read授权新RPC/control绕WAL，拒绝仍durable。

## 主机名与TCP候选所有权

[设计](research/dual-stack-connect-design.md)和[验收](research/dual-stack-connect-validation.md)定义`rustusb_protocol::connect`，供Rust DEVLIST与共享secure client调用。IP字面量绕DNS；四permit由实际blocking lookup closure持有，取消等待者不释放尚在运行的工作。OS结果64raw/16unique，保留偏好并交错族；inline TCP futures按250/100ms有界启动，胜出前丢弃全部loser，无spawn dial task或第二次TLS/IMPORT。调用者原总deadline与stop保持；只有winner进入独立server_name验证。外部分发usbip工具保留自己的连接策略；不声称std/libc内部分配/永久OS阻塞有界或完整RFC8305。

## 本地热插拔观察（实施中）

见[实施设计](research/local-device-monitor-design.md)。实际rustusb-discovery库复用原scanner，service::discovery仅re-export；CLI一次扫描及devices --watch共用这一实现。kernel group1事件只触发重扫，当前地址/候选fingerprint/只读sysfs观察戳分开。Initializing/Stale不冒充成功空库存，丢失或慢订阅者按完整快照重新同步。两个真实blocking owner配额保留到实际完成；stdout不拥有扫描器，显式shutdown总join。本地观察已通过c034的872软件及f66两个单guest事件验收，见[报告](research/local-device-monitor-validation.md)，不以原E40双端I/O代替；不增加设备授权/重连或root能力。

离线CRL E44将完整DER列表接入两TLS角色/两schema的共享TrustRoots，元数据由固定x509-parser解析，真实链/签名/全链撤销和unknown拒绝仍交标准WebPki。Checked SystemTime的共同thisUpdate/nextUpdate窗口在每次新证书验证取时，不改原TLS1.3/pin/ACL或禁用resumption/early-data。配置/agent启动加载快照及job clone共用；完整agent重启才重载，已有连接不热撤销。设计和范围见[离线CRL](research/offline-crl-design.md)与[验收](research/offline-crl-validation.md)，不新增helper/USB能力。

## 前端用户设置

`rustusb-tui::Settings`只保存strict schema1/4KiB的三偏好，非agent/helper配置，不存endpoint/密钥/权限/自动Start。XDG或显式caller目录采用现有Rustix checked descriptor-relative操作、caller0700/0600与原子替换；load/save/reload由真实blocking task持有并总join。CLI启动覆盖不暗存，w/l/d显式操作；完整约束与确切Source/PTY见[E46](research/tui-settings-actions-validation.md)。agent/RPC/WAL/USB所有权不变。


终端E47见[未知启动/失联验收](research/tui-failure-retry-validation.md)：clean4ee6be3e完整952软件/235源，两故障与两原E46正常动作Native双栈pair/8guest0、76实际UID1000 PTY通过。实际Start超时后同agent恢复，UI只确认原UUID RetryStart；精确job current/stale/current，首次Profiles失联不再保留Current或旧journal/health。原38477360两guest0/19PTY但整体FAIL保留，不改其裁决；原264核验/三轮I/O/完整Rootproof不弱化。SIGSTOP不证明wire-loss或永久OS阻塞；UI热插拔/权威拒绝、完整G4/G3/硬件与后续阶段仍待完成。


终端详情滚动现在随有效选择identity变化复位，相同identity重排保留。独立本地观察、agent业务页和偏好文件各保边界；不新增USB能力。E48已逐项审计文档化G4的软件/仿真范围，管理员策略和后续GUI/安装另行维护。 见[验收与审计](research/tui-hotplug-permission-validation.md)。


GUI仍经同一headless frontend/应用服务操作，不另持USB资源。E50的[双栈正向核验](research/gui-native-actions-validation.md)通过真实确认Start/精确Stop、非空Remote和WM_DELETE_WINDOW后原owner实际USB I/O验证该边界；测试-only Xlib不进入产品依赖/API。raw-v1显示RTT unavailable，v2 GUI RTT与完整Phase5另验。


GUI E51仅修改本地显示：以profile/instance/session/connection匹配当前DOM的展开/scroll并沿原focus key恢复，不积累metadata缓存。history/phase/end与operation/cleanup保持独立，见[验收](research/gui-link-health-validation.md)。自动规则的[后续设计](research/automatic-sharing-design.md)为draft：显式root delegation、同事务准入和动态pool/owner缺口待实现，普通Start/GUI偏好不默授后台自动准入。

E55 [GUI规则](research/gui-automatic-rules-validation.md)通过共享frontend按Settings请求独立Rules/RuleEvents；原TUI默认RPC不增。有限bridge只接typed spec/当前identity与canonical decimal revision，原mutation UUID/payload在Rust保留到精确Applied，Start/Stop独立，issued IPC总await及observer实际join保持。未知/拒绝/Expired仅原Query不重发；通用Error的settled负向/安全协调与进程崩溃持久ID仍未实现，不以此完成整个自动规则或Phase5。

E56 [持久规则决策](research/settled-rule-decisions-validation.md)在独立root2入口显式升级strict disk4；保原root1/disk2/3及positive journal。独立256条拒绝表与正向UUID不交叠，actor先核已commit正向，仅healthy已完成precommit失败经原Store原子持久后返回Rejected；writer未知不能伪拒绝，commit后registry错误仍读原正/负结果。support为Read metadata非授权；frontend保原protocol/UUID/payload，精确Applied或持久Rejected才解锁，显式默认Cancel恢复原profile/revision而不rebase/submit。Error/Expired、issued IPC总await、独立Stop及实际join不变；整个前端崩溃持久化、dynamic/物理远端receipt/完整Phase5仍未完成。

E57 [前端跨进程请求恢复](research/frontend-intent-recovery-validation.md)把原Start/规则UUID、rule protocol/payload及最后已发receipt/draft写入独立XDG state/strict1的16KiB闭合slots；expected endpoint摘要/真实UID/broker绑定，原checked0700/0600文件与fresh OFD锁共用，生命周期持锁且blocking工作真实join。实际原子持久后才新IPC、实际本地记录后才清UI slot，失败保原identity；重启只恢复metadata并由用户确认原query，不重发/不rebase。原service disk/RPC/权限/USB owner不变，独立Stop不受local writer挡住；未确认renderer草稿、泛化Expired安全清除、断电/安装态reboot及完整Phase5仍不宣称完成。
