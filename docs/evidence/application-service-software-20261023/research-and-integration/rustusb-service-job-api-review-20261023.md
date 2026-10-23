# rustusb-service job runners 与 backend monitor 接点

只读建议补充，基线8e6baf5，未实施/未构建/未运行设备。采用root最新决定的实际`rustusb-service`库，替代初稿临时名rustusb-app。与I对齐：backend自有有界registry/monitor，core仅ID与事实DTO，service直接拥有真实runner；不引入backend→service依赖或任意BoxFuture observer。

## 依赖与公开接口

```text
core(纯类型/serde，无Tokio/系统写)
   ↑ protocol, transport, native, daemon(helper客户端), rust-backend
   ↑ rustusb-service(真实runner/manager/config/UDS/audit)
   ↑ CLI与未来TUI/GUI
```

`rustusb-service`的Native安全功能不能无条件引入rust-backend/nusb，维持现secure-transport Native-only能力。可选rust-backend/physical-nusb仅门控对应具体Runtime variant，不造各后端空trait层。旧legacy CLI仍经同库内interactive facade以实际交互UID调用helper；broker UDS不得代转旧任意Request。

core建议只增加不携能力的`LocalSessionId(u64)`（作用域为一个ServerHandle）、`ConnectionSide`、`ServerSessionFact`、`CleanupObservation`和安全错误码。service的全局引用是`SessionRef { job_id, local_session_id }`，避免让backend依赖service Epoch/JobId。ID不是授权token，路由先核job/profile/controller ACL再调用该job真实monitor。

```rust,ignore
// backend crate，具体类型，无需纯core async trait或observer callback。
impl ServerHandle {
    pub fn monitor(&self) -> ServerMonitor;
}
impl ServerMonitor {
    pub fn snapshot(&self, after: Option<LocalSessionId>, limit: u16)
        -> Result<ServerSessionPage>;
    pub fn changes(&self) -> tokio::sync::watch::Receiver<u64>;
    pub fn events_after(&self, revision: u64, limit: u16)
        -> Result<ServerSessionEventPage>; // 带gap/oldest/next
    pub fn request_disconnect(&self, id: LocalSessionId)
        -> Result<DisconnectRequested>; // 不代表已完成
}
```

snapshot事实至少含id、requested/claimed DeviceKey及BusID（未知/未授权可省略）、side/backend、实际principal安全ID、phase、operation_error、cleanup结果、真实时间/seq。core的principal字符串只是观察DTO，不能转换成transport::VerifiedPeer或用于授权；仍只从实际TLS认证上下文产生。snapshot/monitor不暴露TLS stream/lease/FD，clone只持Weak/shared控制信息，不延长网络或设备owner。

有界registry建议涵盖所有active Import attempts与有限terminal历史；活动上限仍受原16连接permit，terminal ring如128项，事件ring如256项，view者lag显式resync。request_disconnect在独立watch/flag上生效，不排在满的事件/普通命令队列后；Unknown/已terminal/idempotent结果具体区分，不把未知ID当成功释放。

## 每条入站IMPORT必须是真实登记

1. 实际解析合法IMPORT请求后登记Attempt/Claiming与stop入口，不能等整个URB结束才补一条统计。网络认证失败/格式错误可用connection-attempt审计，不伪造设备会话。未知/无权设备不泄露metadata。
2. 原子key+BusID+VerifiedPeer/slot授权与claim位置不变。claim返回后，lease先挂现CloseOnDrop/真实连接owner，才发布Started/LeaseOwned，再写IMPORT回复。
3. 完整write+flush成功才发布ImportAccepted/Forwarding；这仅证明server接受导入并准备转发，不证明远端kernel已枚举。service要保留evidence，不能把Native stub USED误称设备就绪。
4. per-session Stop覆盖handshake/URB读写等待，但资源owner不能随被选掉的future丢弃。先结束两网络half/readers，再begin_close+真实poll_cleanup；正常/错误/Stop都向同monitor发布Closing和最终结果。
5. Closed/Verified只能来自真实cleanup Ok。错误/超时保留CleanupUnconfirmed，不能因网络EOF、JoinHandle取消或listener退出标成Verified。
6. 特别是claim中止：physical claim waiter被取消或原5s截止到期，实际supervisor可能仍拥有接口/FD。应join实际claim operation后清理，或明确Unconfirmed并保留worker/预算关联；不把targeted task abort当单session已核验。原whole-listener abort+backend settle不能无条件重用为单会话Closed证明。
7. listener Stop发所有per-session stop并join实际任务、settle所有owned资源；per-session Stop只动该owner，其他FTDI/设备/listener保持。Drop仅last-resort initiation，不是terminal receipt。

Native broker搬入service后复用同一core事实DTO/monitor接口：它自己已有真实Export/Stub/连接任务树，无需再让Native backend依赖service。Server job与每个stub会话分开；先stub.close确认再清reserved，所有stub结束后export.close确认完整原基线。

## 为什么不选BoxFuture observer

`trait Observer { fn observe(...) -> Pin<Box<dyn Future<...>>> }`即使放纯core避免crate cycle，也引入未知callback延迟/取消/错误、何时持lease/锁、观察者死去是否影响USB等新契约。若等待在claim/flush/cleanup关键路径，会把UI/审计背压变成设备生命周期控制；若不等待，又是假“可靠观察”并可能产生detached tasks。

backend本地先登记事实/seq，再非阻塞watch通知，manager主动读取有限事实/历史，更容易保证authority与gap。纯core可留将来很小的sync事实sink，但本轮没必要先加trait。若root明确要求**每个入站claim前持久application admission ack**，须另立有界AdmissionGate：登记stop后/claim前one-shot决策、严格截止、取消与拒绝都不claim。普通monitor不是WAL，不能假称提供该保证。当前root helper本身的能力intent/基线持久化保持权威；基础审计应明确可靠性边界而非暗加回调框架。

## service具体runner形状

```rust,ignore
// JobId/ProfileId来自manager；内容不来自RPC任意参数。
struct JobContext { id: JobId, stop: JobStopReceiver, facts: JobFacts }
enum LoadedJob {
    SecureClient { config: LoadedClientConfig, helper: FixedHelper },
    SecureServer { config: LoadedServerConfig, backend: ServerBackend, helper: FixedHelper },
    // 明确foreground兼容入口；不作为broker公开RPC任意legacy override。
    LegacyVirtual { /* 现类型化ServerConfig */ },
    LegacyPhysical { /* 现固定设备选择+ServerConfig */ },
}
async fn run_job(job: LoadedJob, context: JobContext) -> JobOutcome;
struct JobOutcome {
    operation_error: Option<SafeError>, cleanup: CleanupObservation,
    final_snapshot: Option<JobSnapshot>,
}
```

细分实现文件直接搬现secure_client、native_server、virtual_server、physical_server的真实IO，不用subprocessCLI或JSON反序列化状态。manager保存真实task+stop；task内部唯一拥有handles/FD/lease列表/网络树。不要同时把同一资源一份在registry、一份在runner。UI请求仅得DTO和ID。

加载也共用`load_profile(..., stop)`：现trusted load在owned blocking task，实际非0UID/namespace先核，取消时join文件/crypto任务；库不依赖clap或全局stdin/stdout。agent与CLI都在任何加载/prepare之前注册各自一次OS信号，再将StopReceiver传下。不要每个job注册SIGINT/SIGTERM，避免一个进程多job竞争信号/全局stdio。

manager的Start在授权+有限registry登记后返回OperationRef，spawn并tracked实际runner。RPC waiter取消不abort它；job结尾无论操作/输出失败均收尾。外部Stop通过watch进入runner，不能`select!{ signal => return }`直接丢run_job。已接受的Native helper frame要等实际reply再close；USBFS initializer实际未结束则Unconfirmed，不提前Finish。operation/cleanup同时失败必须分别保留，不能`cleanup?`吞掉原操作上下文。

### 保持旧foreground契约

旧命令成为适配器，不是新业务实现：

1. register现ShutdownSignals→共用load→建立tracked runner。
2. adapter负责现stdout JSON字段/次序、EventOutput5s与in_flight、唯一stdin std线程和strict4096B parser；runner从不读取全局stdin/写stdout。
3. Native snapshot仍published_registration_not_live_configuration；virtual/physical原ready/snapshot/ack/stopped形状不变，secure attach仍connected/disconnected和真实receipt；不要为了新统一DTO破坏现6QEMU消费字段。
4. adapter选到signal或自身输出失败时send_stop，然后await真实runner；不drop/abort owner。已blocked/in_flight不重复写最终事件。清理已验证才将现stopped/disconnected JSON输出，失败按原stderr/no-json-fallback规则返回。
5. stdin Snapshot是typed query，Shutdown ack只代表已请求停止，仍await terminal；EOF保持旧foreground停止自身job。hid_key/cdc_emit是显式local虚拟测试capability，保留旧命令测试，不出现在生产跨UID RPC。
6. 可用少量typed TestControl + oneshot reply连接旧stdin适配器，caller取消不重发副作用；不要通过runner.await输出ack。标准线程只持stdin/channel或stdout，不能持任何资源owner。

managed agent相反：观察者stdout/UDS断开或事件write超时只关闭该viewer，不能停止job。JobFacts保存可重取snapshot和有限事件；slow consumer得到gap。旧前台输出失败可停止自身job，是adapter策略，不是manager全局策略。

## 根授权/状态/审计一并推进

rootmanifest限定ProfileId→LoadedJob路径/角色/controllerUID/actions，RPC无path/key/endpoint/DER/FD/helper passthrough。公开客户端不接受自报UID；服务实际UDS peercred决定内部caller。broker UID最大池不因UI身份而整体转授；同brokerUID依旧同信任域，不能声称程序名提供额外隔离。

Job阶段Starting/Running/Stopping/Stopped与领域九状态及USB readiness分开；manager只从真实runner/backend事实转移，并持久化安全job摘要+基础审计（不授权旧能力）。server Forwarding不假冒client枚举，helper当前cfg/IF也不与preexport metadata混同。重启旧job进Recovering/Unknown，不直接Connected；目前root Pending协调需要root，若UI要确切展示须新增broker-scoped只读摘要，不开放retire/reconcile/旧PID Finish。

最少验证既包括原6run/信号/背压不回退，也包括真UDS profile ACL、一个server内两真实设备会话定向停一条仍保另一条I/O、Stop duringclaim/flush/blockedoutput、慢事件消费者、双错误/未决恢复及审计轮转/secret扫描。只做status或手写fake observer通过不能替代这些实际owner路径。
