# RustUSB 技术架构

状态：Phase 0 设计草案；2026-10-22。实现范围及验证结果以工作日志为准。

## 路线

Phase 1 使用 Linux 原生数据通路，Rust 负责发现、校验、权限和操作编排。后续 Rust 后端独立实现设备服务端，复用同一领域模型和应用接口。客户端使用 Linux VHCI，不自行重写内核 USB 请求转发。

```mermaid
flowchart LR
  CLI[普通用户 CLI / 后续 TUI GUI] --> Core[共享应用接口与类型]
  Core --> Scan[只读 sysfs 扫描]
  Core --> List[有界 USB/IP DEVLIST]
  Core --> IPC[本机 Unix socket]
  IPC --> Helper[root helper / UID 与策略校验]
  Helper --> Native[固定 usbip 操作]
  Native --> Host[usbip_host]
  Native --> VHCI[vhci_hcd]
  Host <-->|USB/IP TCP 经受管 VPN 或后续 TLS 代理| VHCI
  Rust[后续 Rust 后端 / rusb nusb 虚拟设备] -.-> VHCI
```

安全管理和 USB 数据通道分离。Phase 1 需要管理员预先建立可信 VPN/隧道与防火墙；没有声称内建加密。不得自动以默认全地址监听启动 usbipd。

## 工作区与依赖方向

| crate | 职责 | 允许依赖 |
| --- | --- | --- |
| rustusb-core | 领域模型、输入类型、状态、错误与后端 trait | serde、thiserror；不含系统写操作 |
| rustusb-protocol | 有界 DEVLIST 编解码、异步远端发现 | core、Tokio |
| rustusb-linux | sysfs 设备/接口/端点扫描、指纹、能力与存储检查 | core、sha2；阻塞读取在调用边界隔离 |
| rustusb-native | 固定 usbip 命令、超时、内核状态读取 | core、linux、Tokio |
| rustusb-daemon | root helper、root 管理的策略、OS 身份和 IPC；客户端库 | 前述 crate |
| rustusb-cli | clap 命令与日志 | 前述公开服务接口，不直接写 sysfs |
| rustusb-rust-backend | 有界用户态 USB/IP 服务和两个虚拟测试 profile | core、protocol、Tokio；CLI 通过 rust-backend feature 选择，无物理 USB 依赖 |

没有为未来 TUI/GUI 建立空壳 crate。Rust 虚拟服务端实施设计见 [研究记录](research/rust-virtual-backend-design.md)；这不代表完整物理后端或 G2 已完成。

## 具体接口契约

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

可编译契约位于 `rustusb-core`；`NativeBackend` 实现 ExportBackend/ImportBackend，并提供 `attach_session` 返回已验证端口、`detach_session` 在同一锁内复核身份。`rustusb-daemon::Request` 是受控特权接口，`request` 是普通前端的共享入口；没有为概念名额外建立空 SessionManager/PrivilegedOperations trait。helper 内部管理已认领会话，`sessions` 返回内核快照和是否受管。

| 操作 | 当前截止/资源边界 | 超时语义 |
| --- | --- | --- |
| DEVLIST | CLI总1–120秒，默认10秒；Native预查15秒 | 关闭socket，不进入内核导入 |
| IPC输入/输出 | 各5秒；单帧64KiB、32连接 | 错误/关闭，不开始无效请求 |
| helper变更排队 | 30秒 | 未取得锁则不开始变更 |
| native工具 | 15秒；stdout/stderr各64KiB | kill+最多2秒reap，重读内核，明确不确定副作用 |
| 内核settle | 10秒轮询；快照5秒/单属性2秒 | 不把命令成功当成状态成功 |
| 普通客户端等应答 | 120秒 | 已接受helper变更可继续完成，检查status/sessions后再操作 |

完整九状态业务状态机当前提供已测的转换类型；热插拔事件流、操作ID/进度事件、所有状态的持久超时记录、重启后未知端口认领和存储恢复仍属于Phase3，未冒充已实现。后续状态事件应包含操作ID、非敏感设备ID、from/to、稳定错误码和单调时间，使用有界事件队列；丢事件后客户端重新拉取权威快照。

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

本轮不引入完整持久会话日志、自动存储恢复或Phase3心跳状态机。内核ERROR状态身份不明时继续明确要求协调；安全拒绝不能被写成自动恢复完成。

### QEMU 验证矩阵

保留默认双guest HID两周期，增加独立场景：RustUSB export→官方Linux client、官方bind/usbipd→RustUSB client，以及受管连接被内核外部释放→直接RustUSB重连→外部释放→同port外部重新导入(旧helper必须拒绝控制)。每项要求实际输入/字节传输、驱动及资源释放，未知外部资源仍由官方工具清理。

可选QEMU usb-serial经官方6.2源码核实为FTDI FT232BM(0403:6001，厂商类、Bulk)，使用ftdi_sio并测试双向固定序号字节；它不是CDC ACM证据。只有HID reconnect场景测试代际；串口覆盖两后端端点互操作和两周期释放。三个guest多客户端争用、netem延迟/丢包、完整CDC/Rust服务端仍在原计划中，不能由本轮矩阵替代。测试只用临时VM、私有socketpair/Unix chardev；不触宿主USB、内核模块或公共网络。

## Phase1补充验收：多客户端与传输故障（实施设计）

2026-10-22，基线67c83d5。争用harness以三个独立guest内核验证单设备独占：server经两个独立私有socketpair接口分别连接client A/B；A成功导入并实际输入时，B的导入必须明确失败且无新增端口，A仍能实际输入；A释放后B可成功导入、实际输入、释放，最后server unexport恢复本地驱动。普通UID管理路径和各端授权不能用guest root直接导入代替。新测试使用独立脚本，保持此前七项harness可复现。

故障harness在已完成实际输入的连接上，由guest管理员精确中止唯一ESTABLISHED USB/IP TCP（只限隔离来宾）；核对内核端口变为NULL、旧sysfs代际消失、server AVAILABLE后，由RustUSB直接重连再验证实际输入与释放。杀usbipd进程、carrier down和本地usbip detach各有不同语义，不互相冒充。Linux普通TCP错误通常SHUTDOWN|RESET而非持久ERROR；若观测到UNKNOWN/ERROR，保留原始状态并拒绝猜测清理。此项不证明WAN黑洞检测、心跳或自动恢复存储写入。

软件平台验证扩展为Ubuntu22.04/24.04 × x86_64/aarch64原生CI，运行前以uname验证实际架构并记录系统/工具链；采用[官方标准运行器](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)，结果待实际四job完成。CI不运行物理USB变更；aarch64编译/单元/只读smoke通过也不证明ARM USB/IP内核路径兼容。
