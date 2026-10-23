# 同 USB 数据连接的心跳、RTT 与失联检测

2026-10-24，起点 clean `031f63d9b27fa8712a13089f95aa61c1c2c223aa`。状态 **proposed / 源码审查后实施**，尚无本项运行 PASS。上一 goal turn 为实际进展：共享 owner、530 软件、九项旧 QEMU、四原生 CI、英文入口和发布；本项继续原需求 4.4/4.5/4.11，不缩减完整 Phase0–7。真实硬件、日志轮转、Rust 无独立证明的崩溃协调及其它阶段仍未完成。

## 选择和边界

为实际 IMPORT/URB 所在的同一 TLS 连接增加显式 v2。继续使用已有 TLS1.3/mTLS、证书 principal/device ACL、helper 权限、共享应用 owner 和真实清理证明。每个 USB 连接一个 h2 连接、仅一个双向 stream，不把不同设备合并进一条 TLS；Catalog 使用自己的短连接，其响应不刷新任何数据连接的健康。

选择固定 MIT `h2 0.4.20`（2026-10-06，官方 tag commit `b0e890842ac16d3c76836fd4fcc06421acb15fa7`）提供成熟 framing、接收窗口和 PING/PONG，另用 `http`、`bytes` 的所需 API。`cargo info h2@0.4.20 --registry crates-io` 已确认发行版、MIT 和 MSRV1.63，只下载注册表缓存，未改 Cargo.lock；最终依赖仍须锁定并更新清单。运行环境继续稳定 Rust1.97.1/Edition2024、Tokio1.54.1、rustls0.23.46、tokio-rustls0.26.6。

替代方案：TCP keepalive/TCP_INFO只证明内核路径，不能替代所需的应用传输端点探测；独立控制连接 pong 与目标数据连接无必然关系；直接给 raw USB/IP 插入 ping 会破坏协议；简单 DATA/PING 自定义流若 reader 被满应用队列挡住，也无法保持控制进展。自行实现 credits/帧洪水防护会再造成熟库已有功能，因此不选。

H2 PONG 只证明该 TLS 上的远端传输端点响应，**不证明 USB completion、内核或 DATA 消费进展**。h2 公共 `Ping::opaque()` 使用库的固定 USER payload，只有一个 pending；应用序号是本地归属，不是假造 wire nonce。RTT 定义为本地单调时钟的 `send_ping` 排队开始到库报告匹配 PONG 的耗时，包含本地队列、调度及远端处理，不称 wire RTT、单向时延或 USB 延迟；不宣称能据此抵御持有合法私钥的恶意端点虚报。

## 版本与准入

- 原 `rustusb-usbip/1`、raw byte stream、旧内存构造器、schema1 配置和 JSON/EOF 行为保持。v1 明确报告 heartbeat/RTT unavailable，不显示虚构的 0 或 healthy。
- v2 使用独立 `rustusb-usbip/2` ALPN，双方显式 root 管理配置；只提供所要求的一种协议，不在失败时自动 downgrade。新增严格 schema2 配置；schema1 继续按冻结旧 DTO 解码，不能用可选字段暗中改变旧模式。公共 LoadedConfig 的旧构造形状尽量保持，由 TLS 配置持有所需模式/策略。
- v2 是私有 RustUSB 协议，复用 h2 framing 和有限 CONNECT 约定，不是通用 HTTP 代理/网站，不宣称替任意 HTTP 服务提供兼容。固定有界 CONNECT authority/method、stream ID1、状态200，禁止其它 stream/push。具体 URI API 以固定 `http`/h2 源码与实际测试冻结。
- TLS 验证和 principal 映射先于 h2；h2 网络准备先于原 USB/IP secure prelude、目录/IMPORT 和设备准入。成功的 CONNECT 不代表设备授权。所有原 device-key/Bus-ID/metadata 复核、managed durable admission 和 helper policy 仍需通过。
- TLS 仍16连接预算，permit 随真实网络 driver 保留至结束，不能从 IO 或 monitor 提取/绕过。DNS/TCP/TLS/目录/IMPORT 的现有总截止继续有效，另给 h2 preface/CONNECT 有界准备，绝不取消已发送的 helper mutation。

## 实际 owner 与接口方向

```text
accepted authenticated TLS + permit
    → bounded h2 LinkDriver（独立 task：Connection poll + probe timer）
       ↔ bounded LinkIo（原 USB/IP codec / VHCI R relay / Rust lease owner）
       → nonowning LinkMonitor（volatile facts only）
application owner → cooperative stop → join LinkDriver → actual helper/lease cleanup
                                                  → durable Closing/Finish
```

本轮冻结的公开接口（软件验证另见证据归档；本设计本身不作为QEMU或设备通过证据）：

```rust,ignore
pub enum LinkProtocol { RawV1, FramedV2 }
pub struct Link<T> { pub io: T, pub owner: LinkOwner, pub monitor: LinkMonitor }
// LinkIo: AsyncRead + AsyncWrite + Unpin + Send
// Public TLS factories require a real owned TcpStream; private software fixtures
// alone use generic IO. Public tuple values come from that socket.
impl LinkOwner {
    pub fn request_stop(&self);
    pub async fn join(self) -> Result<LinkOutcome>;
}
impl LinkMonitor {
    pub fn snapshot(&self) -> Result<LinkHealth>;
    pub fn changes(&self) -> tokio::sync::watch::Receiver<u64>;
    pub fn ended(&self) -> tokio::sync::watch::Receiver<bool>;
}
```

monitor 不持 TLS、h2 handle、permit、lease 或可停止能力；Drop 只请求合作停止，正常路径总 await owner。一次 PONG 不进8槽 lifecycle queue/WAL。真实进程/实际 TCP local/peer tuple 与应用 instance/session/attempt 绑定，tuple 仅来自实际 socket，不接受 RPC 自报。新连接有新 local connection identity；历史样本不因新 epoch 或墙钟变化变成 live。

LinkHealth 只保留有限固定类型字段：模式、connection identity、phase、local probe/pong seq、connection-relative monotonic elapsed/last probe/last pong、RTT microseconds、application byte/read/write progress ages、实际已观测的 write backpressure、typed end reason。不存原始 USB、任意错误文本、证书/密钥或 probe 历史列表。`Awaiting/Pending/Responsive/TimedOut/Ended` 与设备状态分开；无 RTT 必须 None。应用原 byte counters 保留 IMPORT 后原定义，不被 h2 控制字节替换；独立 LinkStats 统计此连接的应用字节（含 USB/IP management），不能混用两者。raw v1 的 stats/RTT 为 None，不能制造0。mutex poison/driver panic 由 Result 明确失败，不产生成功样本。

## Flush 和 END_STREAM 是实施门槛

`h2::SendStream::send_data` 入队以及 duplex flush(no-op) **不是**原 IMPORT 完整 write+flush。不能改名 Connected/evidence 来绕过既定语义，也不能用可越过 DATA 的 PING ACK 当 fence。

候选安全 `FlushTrackingIo` 包在 h2 encoder 与 TLS 之间：只流式解析本地 encoder 的9字节 header/length/stream ID，不保留或记录 DATA payload；client 先核24字节 preface，server 无 outgoing preface。仅在底层 TLS `poll_write` 实际返回 `Ok(n)` 后推进已接受的前 n 字节；最初可不实现 vectored IO。固定 h2 encoder 已核 DATA flags 初始0、无 padding、仅可设置 END_STREAM；依赖升级须重审。

`LinkIo.poll_flush` 的目标是它此前实际接受的全部 application DATA offset。只有底层 TLS `poll_flush` Ready(Ok) 才公布已 flush 的 DATA offset；目标未达时必须 Pending，PING/SETTINGS 的 flush 不能越过未发送 DATA 抬高 offset。部分 header/payload、取消后继续写、0 write、错误、reset 和 driver 结束均需正确处理/唤醒。计数溢出为确定错误，不能回绕后误确认。

零长度 END_STREAM 另有完整 frame accepted + TLS flush 的 FIN fence，不能因 byte offset 未变就提前 `poll_shutdown` 成功。原 foreground bidirectional drain 与 managed first-EOF 选择保留；本项不借改传输静默统一两策略。以上须由真实分段/阻塞 IO 和原 IMPORT/URB flush 回归证明后才能接设备。

## 流控、计时和日志

候选 bounds：单 stream，连接/stream receive window 65,535B，frame 至多16KiB，有限 send buffer64KiB，header list2KiB、HPACK table0、禁 push，reset/额外 stream 严格收敛。固定 h2 0.4.20 源码的 Auto small-DATA budget 为 max(connection target window/2, 25,600)，本配置为32,767；小于256B的非空 DATA 扣256-n，大帧归还n-256，RecvStream.poll_data/clear_recv_buffer 退还帧元数据（不是 WINDOW_UPDATE），非终结空 DATA 另有 lifetime100限制，终结帧豁免。保留上游Auto，并要求合法小 URB/恶意1B洪水验证，不能随手设很小阈值误杀 HID。发送必须按 reserve/poll_capacity 的实际信用限额，释放 receive credit 仅对应实际消费字节。

生产策略默认 interval5秒 / response timeout15秒；interval范围1..60秒，timeout至少2*interval且至多300秒；测试短策略仅私有cfg(test)，不得变成 RPC 任意覆盖。schema2 必需严格 data_link={protocol:"framed_v2",interval_millis,response_timeout_millis}；旧 schema1 多出该字段拒绝，v2缺该字段拒绝。只一个 pending；未收到有效库 PONG 不刷新活性；断开/timeout/counter溢出清晰终止。最终值和时序起点在实现 API freeze 时更新，不让 harness 加宽 deadline 掩盖违约。

固定 h2 目前会在 WARN 输出未知 ACK 的 Debug payload，默认 info 也可见。因此要在库内对全部 h2 API/poll/Drop 使用受控无输出 dispatch，或提供同等完整隔离，自己的日志只用 typed 固定类别。不能仅靠 EnvFilter 的 `h2=off`（更具体指令可覆盖），也不能只靠 CLI 保证嵌入者安全。quiet wrapper 必须在作用域内实际 drop 值/错误/handle，不能先 take 到外面再 drop。验证需先用 unquiet baseline 证明捕获器能抓未知 ACK，再检查 quiet 后 ASCII/Debug十进制/hex sentinel 均不泄漏，且自身 typed 审计仍正常。

独立 RX pump 继续 poll_data 并写入固定65,535B接收管道，只有应用实际 poll_read 消费的单调增量才 release credit，避免 WAL/应用不读时把合法小帧元数据误当洪水。received 先发布后暴露字节，快照先读consumed再received；receive_pending_bytes 含 pump 在途帧与管道未消费字节，不等于管道精确占用或RSS。TLS transport_write_blocked 只取实际底层 write/flush/shutdown Pending，不能用 h2 credit blocked 冒充。

正常单 END_STREAM 保留接收缓存与反向半连接；两方向结束且缓存实际消费完才终止。真实 connection 完成但应用仍不消费缓存时，只给 response_timeout 的本地排空宽限，之后为 LocalDataStall，不冒称 peer ProbeTimeout。fatal/本地stop丢弃旧缓存并唤醒等待读写；结束样本冻结，driver ended 通知不等于设备 cleanup。

## 应用服务、持久化与实际取消

沿用 I 的服务侧候选：旧 Session/Metrics/Events 及 IPC1 输出不改；增加独立只读 `SessionHealth` 请求/响应、原 profile `Action::Read`、至多16项/60KiB，CLI `agent health`。旧 agent 明确拒绝未知操作，不猜健康或降级。live health 不需等待 WAL；store 若保留最终/历史样本，用 strict disk2 的 bounded private link_history，一一匹配原 session/instance，strict disk1 显式迁移 RawV1Unavailable/ExportNotApplicable，未知字段/关系拒绝、sticky不降级，prune 随原 session。重算保留容量，不用扩大阈值掩盖增长。

所有持资源 owner 都要独立观察 link ended：NativeIncoming 的 RelayStops/retained ACK、client sample 外 select、可能堵 kernel R 写的纯 relay，以及 Rust admission/claim/IMPORT flush/actor。纯 IO 可取消并join；已触发 claim/helper commit 仍等唯一实际 completion/私有 control actor，然后实际 close。Rust parsed-command 容量2队列不能屏蔽网络失联。先清理资源，再等原记录，timeout 仅 operation，不把 retained/pending ACK 当 terminal。已知准备 Resource ACK 无限停顿与 kernel 硬阻塞仍独立未决，不由本项宣称全部有界。

## 验证、来源与交付

1. transport/API gates：真实 h2 流控/1B洪水/合法小DATA、唯一 probe 与超时、伪/重复ACK范围、caller停止与丢弃、满窗口下控制进展、纯网络 owner 总join/permit回收；实际 TLS write/flush/FIN fence 的阻塞和部分写负向；日志捕获 baseline/quiet。
2. 配置/服务 gates：旧 schema/ALPN/JSON/EOF 原回归，v2严格配置/不downgrade、health权限/页面/历史/容量/迁移、持久 ACK 停顿而driver计时和实际关闭仍进展；Root proof按原kind/correlation/key/UID/resource，不伪造 USB healthy/恢复。
3. 完整 fmt/默认与all-features Clippy/tests/release/rustdoc/vendor；Native-only 仍不得引 Rust engine/nusb。新依赖/许可证清单和锁准确记录。
4. QEMU：先 Native/IPv4 与 USBFS/IPv4，精确 actual data tuple 的双向与两单向 blackhole（FD仍开、不发FIN/RST、不杀进程）；另一设备继续实际I/O、新Catalog成功，目标timeout、真实cleanup及fresh attempt重用。要覆盖三backend/双栈，最终须完整六组新矩阵；旧九组回归另账，不以最小两组代替其余。
5. root只在隔离guest做USB/sysfs/模块操作。宿主 valve 用两私有socketpair、有界Ethernet队列，只丢目标TCP flow；原完整PCAP重组器不能在有意缺包时放宽判定，新审计须核逐方向ingress/drop/forward原件和未丢的其它流。RTT不减两个guest时钟；Native没有pending计数不得猜，USBFS真实pending/inflight单列。

主要依据：当前项目固定源码及031 Gitblob；Tokio/rustls已有固定缓存；[h2 0.4.20固定源](https://github.com/hyperium/h2/tree/b0e890842ac16d3c76836fd4fcc06421acb15fa7)、其share/ping_pong/framed_write/data实现及 Cargo发行校验；原 [应用服务设计](application-service-design.md)、[共享owner设计](shared-owner-design.md)。N transport、I service/owner-wakeup、H QEMU只读研究原件与精确 SHA 将随交付归档，不把本设计当运行证据。沿用仓库目录与用户指示，不为套用设计技能额外创建逐组件skills/ADR或擅自标approved。

既有九组回归的agent verifier须严格验证新的私有disk2与一一对应link_history；旧IPC1和原设备/权限/清理断言保持。raw-v1的每个已观测样本必须是明确UnavailableRawV1、RTT/identity/stats均null，不因新API猜测心跳。此为格式升级验收，不修改旧记录或将旧运行冒充disk2。
