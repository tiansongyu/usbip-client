# Rust backend 逐会话 monitor/control/metrics 候选

2026-10-23，clean8e6baf5，只读proposal；未改源/编译/QEMU。范围是实际engine观察/定向stop接点，不声称这本身完成持久SessionManager或审计。

## 1. 推荐：backend自有有界registry，不await service callback

core只放无Tokio依赖的DTO、typed `LocalSessionId`、phase/outcome/metrics枚举；backend拥有实际registry、轻量锁、watch版本、每连接stop和生命周期记录。service保存`SessionRef { job_id, local_session_id }`路由，避免backend引入全局业务ID/agent依赖。ServerHandle仍唯一拥有listener/task tree；`ServerControl`/monitor只持状态/Weak，不能持lease、TLS stream或延长设备生命周期。

```rust
// rustusb-rust-backend public candidate; exact naming to align with core/service.
pub struct SessionMonitor { /* bounded registry, no device capabilities */ }
pub struct ServerControl { /* Weak<Registry>, no ServerHandle/lease */ }
pub struct MonitorSnapshot { pub revision: u64, pub sessions: Vec<ServerSession> }
pub struct MonitorBatch { pub next: u64, pub events: Vec<SessionEvent> }
pub enum StopRequest { Requested, AlreadyClosing, AlreadyTerminal, Unknown }

impl SessionMonitor {
    pub fn new(limits: MonitorLimits) -> Result<Self>;
    pub fn control(&self) -> ServerControl;
    pub fn snapshot(&self) -> Result<MonitorSnapshot>;
    pub fn events_after(&self, cursor: u64, limit: usize) -> Result<MonitorBatch>;
    pub fn subscribe_revision(&self) -> tokio::sync::watch::Receiver<u64>;
    pub fn acknowledge_terminal(&self, id: LocalSessionId, revision: u64) -> Result<()>;
}
impl ServerControl {
    pub fn request_disconnect(&self, id: LocalSessionId) -> Result<StopRequest>;
}
impl ServerHandle {
    pub async fn bind_tls_monitored(config: SecureServerConfig, backend: VirtualBackend,
        tls: TlsServer, monitor: SessionMonitor) -> Result<Self>;
    pub async fn bind_physical_tls_monitored(config: SecureServerConfig, backend: PhysicalBackend,
        tls: TlsServer, monitor: SessionMonitor) -> Result<Self>;
}
```

monitor在bind前创建、service启动消费/关联后再bind；一个monitor只绑定一次server实例，拒绝复用到另一个handle。旧四个bind方法走`None`，旧调用方不必observe或ack，保留原API和协议。service在自己授权层检查job/owner/action，不能暴露“猜u64就能断任意server”。ID在同handle内单调不复用、溢出拒绝；job identity区分重启。

不建议`core::SessionObserver -> BoxFuture<TrackedSession>`直接侵入lease路径：manager actor若正在等backend stop结果，同时backend又await其事件ack，会循环等待；断开/flush/cleanup也会受audit磁盘速度支配。普通watch只提供“需要重拉”的通知，不能保证每个状态都被消费。registry保留固定生命周期事实槽，event与snapshot同revision锁更新，锁中不await、不做I/O、不回调用户代码。

资源候选：active<=现16连接；每session预留固定事实槽（Claiming、Started、可选Connected、Closing、terminal），terminal archive<=64且只在明确ack后回收。archive满则拒绝新observed IMPORT admission；**cleanup所需槽必须预留，绝不因事件队列满而阻止Closing/terminal**。metrics可合并，无须每个字节生成event。watch关闭/消费任务慢不能丢lease或把清理当成功；runtime收到monitor故障应发stop并await真实job cleanup。monitor被观察方丢弃不是root权限或资源完成凭据。

这仍不是WAL。若root要求每个入站IMPORT在claim前durable application Intent，单独定义有界AdmissionGate并在claim前等待ack，stop通道先安装；gate失败/掉线不claim。不要把普通monitor Started事件“稍后写盘”称为先durable，也不要在持lease后await manager才能关闭。本slice可先保持helper既有durable安全记录+service明确observability审计级别，AdmissionGate需另接受。

## 2. 生命周期的准确接点

| 事实 | 源码位置/时机 | 不能声称 |
| --- | --- | --- |
| Claiming（candidate ID） | server.rs handshake解析IMPORT并核协议类型后，进入backend.claim前；包含requested key与verified principal，尚未授设备 | 不是成功owner；不得因catalog可见就跳过claim内原子ACL |
| Started | claim实际返回lease，先放`claimed=Some(CloseOnDrop(lease))`，再publish；尚未编码/写成功IMPORT回复 | 不能先发Started再安置lease；后续编码/flush失败也必须Closing/terminal |
| Connected | 成功IMPORT response的write_all+flush在同deadline完成后，进入URB actor前 | 仅server accepted IMPORT；不是远端VHCI已枚举/驱动ready。DTO应有`confirmation=ImportReplyFlushed` |
| Closing | stop/EOF/帧错误/Gone/flush失败触发，结束网络reader/writer，再`lease.begin_close()` | stop已接受不代表设备已释放；原operation错误不得被cleanup覆盖 |
| Closed{Verified} | `poll_cleanup`真实Ok后；固定snapshot保留final counters和stop/error原因 | 不能由future Drop、timeout、listener abort、设备catalog available猜出 |
| CleanupUnconfirmed | cleanup timeout/error/panic/cancel导致没有真实Ok；phaseterminal只指本连接已结束，resource仍Uncertain | 不能返回cleanup_verified、不能把资源变成Available或将其当恢复成功 |

`SessionLease`加private `fn device_key(&self)->DeviceKey`，值从实际成功claim的registry slot/profile带出；PhysicalLease直接slot.key，VirtualLease在claim时携带profile key。不要claim后再查可见catalog反推key，也不以客户端expected key自我证明授权。principal来自VerifiedPeer，不用socket地址或自报JSON；未认证legacy标Legacy/Unavailable而不合成principal。

停止标志安装在candidate registry登记时，`request_disconnect`直接sticky watch/send_replace，不排入可能被manager的事件处理堵住的邮箱。管理层返回Requested，再按snapshot/operation等待terminal；重复stop幂等，不可影响相同BusID的新localSessionId。

对**已取得lease**，外层select围住IMPORT回复flush及整个imported_connection；stop到来退出该网络future（reader child目前是嵌入future，drop即同步结束），随后由仍持lease的connection做begin_close/poll_cleanup。不能用单纯AbortHandle作为正常定向stop，否则真实cleanup await和Closed通知都会被跳过。已有partial OUT副作用仍如实保留。

## 3. claim进行中是实现必须明确的分支

`physical.rs:421–462`在锁内先置claiming/leased并spawn supervisor，随后才await oneshot ready；当前5秒claim timeout可能drop waiter，但worker仍持真实USB状态。故不能对整个handshake套select、stop后直接发Closed。

最小保守实现：candidate进入claim后，stop/超时只请求关闭并保留`CleanupUnconfirmed`，不宣称verified。要实现本次job要求的“逐session stop等实际清理”，推荐补**private per-claim completion ticket**：它绑定本次slot/attempt、ready/取消和supervisor真实terminal，不依赖全backend settle。stop时关闭ready等待/发cancel触发既有监督关闭，ticket继续观察该worker所有真实请求、release与恢复结果；成功再terminal Verified，失败/截止Unconfirmed。Virtual ticket可立即产生成功lease及对应cleanup。

ticket可用私有Poll/Future trait实现，不要求core依赖Tokio；必须在实际claim锁内固定attempt identity。仅通过BusID/current slot state观察不够：新claim或另一会话可能让available/leased变化。`backend.settle_closed()`等待所有slot，不能拿它作定向session完成，其他正常会话不得被一个stop断开。

listener normal shutdown也应合作向全部连接发stop、join实际任务，再走已有backend.settle_closed兜底；现server.rs:279的abort_all会使observer漏terminal。外部ServerHandle Drop/任务panic无法await，sync RAII guard只能记录`AbortedCleanupUnconfirmed`并触发原CloseOnDrop，不能伪造Closed。background worker/quota直到实际terminal仍归监督器；deadline不释放其资源预算。

## 4. 实时指标与错误结果

统一字段定义为**IMPORT成功flush之后的USB/IP应用字节**：`app_rx_bytes`=已交付内层parser/relay的明文USB/IP字节；`app_tx_bytes`=AsyncWrite实际接受的明文USB/IP字节，包含USB/IP头，不含前导/DEVLIST/IMPORT管理回复、TLS record开销，不等于USB有效payload或对端已收到/设备已执行的字节。方向相对server；client指标另外注明方向，不能累加双边变成两倍吞吐。

在TLS解密层、握手后启用`CountedRead/CountedWrite`（私有inner，不提供into_inner/Deref逃逸）；read用ReadBuf新增长度，write只计`Poll::Ready(Ok(n))`的n，包括write_all最终失败前已完成部分。flush不加字节，Pending/Err不加，vectored write同样处理。计数用饱和u64并显式overflow标记，sample含单调elapsed/validity；无需每次poll加registry大锁。reader可能读入尚未完整解析的半帧，所以label不能写“已完成URB字节”。

停止/失败仍保留live atomics的最终采样；不能依赖copy_bidirectional成功返回tuple才保存，也不能在`?`或abort时丢掉已计数。Rust engine用同一wrapper；N抽出的Native/client pump共享相同定义。cleanup snapshot附`operation_error`与`cleanup_error`两个分类字段。吞吐只是两个采样delta/elapsed；RTT与heartbeat本API没有实现，不返回伪0。

## 5. 必要而窄的验证

1. 真TLS导入固定virtual：Started先于reply成功，Connected仅flush后；pending IN时定向stop→EOF→ClosedVerified，第二设备会话仍真实I/O，再次import得到新ID；原无monitor测试保持。
2. 可控writer部分write/flush失败、stop在flush阻塞期间：lease先归owner，Connected不得出现；Closing和实际cleanup完成，错误与字节准确（不能只写镜像实现的事件序列单测）。
3. fake private claim worker真正gate其terminal：claim中stop后仍保留candidate/quota和Unconfirmed，直到该attempt实际完成才允许Verified；另一session活跃不阻此结果。随后QEMU USBFS实际claim/取消窗口与driver恢复验证，fake不代替USB。
4. observer不drain、archive满、watch lag/receiver drop、manager正在处理停止时仍能直接发stop；cleanup无等待manager，新增admission有界拒绝；no-monitor旧API不因没ack拒绝正常会话。
5. live计数测试真实分片/短write、Pending/error、EOF、cancel、counter overflow；原IMPORT后紧邻URB不预读丢弃、TLS permit完整寿命保持。Native与Rust两路径采样scope一致。
6. drop/panic/cleanup timeout只能Unconfirmed；shutdown正常路径要真实join，记录Closed不能来自Drop。软件通过后root唯一串行QEMU，以实际I/O/完整基线及journal终态验证；本轮未运行。

主要读取：backend server.rs(136,238,299,397,431,483)、session.rs(78,100,139)、physical.rs(401,421,462,507)、device.rs(417,456,588)，CLI secure_client/native_server已与N交叉对齐。核心结论与N一致：bounded registry/Weak control优先；AdmissionGate/WAL不能靠monitor名义补齐；claim未返回时仍有真实worker所有权。
