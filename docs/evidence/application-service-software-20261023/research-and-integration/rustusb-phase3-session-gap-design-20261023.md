# Phase3 会话、审计与健康管理：只读差距和候选接口

2026-10-23，基线 clean `8e6baf5ab64a42f6698f15c8928dcdd6591e4570`。状态：**proposal，未获架构接受，未实现/运行**。读原始目标存档 `docs/requirements.md`（4.2–4.5、4.7–4.11、统一后端接口）、AGENTS/core skill、architecture/security/roadmap/acceptance、secure设计及实际源码；不是将新范围缩成内存snapshot。用户指定只读/tmp，因此不按设计skill模板额外创建逐组件skill或仓库文档。

建议下一切片先实现一个真正持有会话资源的普通权限应用runtime，以及CLI适配和持久操作/审计契约；专用非root agent是较合适的部署候选，但仍需接受。它不能被单独称为Phase3完成。心跳数据链、重启后的真实协调/重新I/O、网络授权负向和部署验证分别需要后续实证。

## 1. 当前事实和仍缺什么

| 原需求 | 已有可依赖事实 | Phase3缺口 |
| --- | --- | --- |
| 身份、设备授权、完整数据加密 | clean b83c931三后端×双栈六QEMU/12guest0；真实TLS软件负向、专用broker/helper角色 | QEMU当前同一合法principal获授两设备；未穷尽未授权证书/设备、并发首次IMPORT、吊销/轮换/生产部署 |
| 统一SessionManager | core/lib.rs:269九状态enum和transition；DeviceDiscovery/ExportBackend/ImportBackend有trait | 无实际SessionManager、应用sessionID/operationID、服务级会话注册表和跨前端控制；状态enum没有驱动当前业务 |
| 会话监控与取消 | secure_client.rs:118 attach未来持有TLS/relay/helperhandle；native_server.rs持export、connection任务树；合作信号实际await cleanup | 单个CLI进程就是owner；前端退出等于停止连接；无独立取消请求/操作结果查询/多前端订阅；stdout输出不是权威事件或持久操作状态 |
| 重启协调 | helper Native持久Prepared/Intent/Observed、原export/强receipt，root-only pending/reconcile；USBFS受限opt-in恢复另有证据 | 没有应用恢复epoch、非root服务有界读取自身helper状态API、持久会话与真实资源关联；Native新进程不得接管旧TLS/FD；重启进程本身不是恢复 |
| 心跳/RTT/异常断线 | EOF、helper失效和部分截止；原始URB空闲连接允许无限等 | 无主动数据通道心跳、blackhole判定、健康状态和RTT语义；不能以有HID pending或独立catalog成功当成该连接健康 |
| 字节/带宽 | Native TLS relay实际经过用户态；Rust engine也有统一网络读写点 | client copy_bidirectional的返回值被忽略且只在成功结束给总数；无实时/错误路径累计；不能以payload fixture35B当吞吐或全部wire字节 |
| 事件/日志/审计 | tracing与有限CLI JSON、helper资源journal | 缺统一关联ID、序号/断档、持久状态变更记录、日志轮转/上限/诊断脱敏；资源journal不是完整业务审计 |

参考证据仍按原版本记：本地412=159+233+20/123输入，精确f9558fc四CI每格412；六QEMU锁b83c931/125输入。新服务变更以后必须重跑有意义的对应路径，不能继承为新实现通过。

## 2. 应用服务候选与权限

**推荐候选**：新增有真实实现的 `rustusb-service` library，拥有一个长期runtime；专用broker UID的 `rustusb-agent`（可先是现CLI的新子命令，不必立刻加新binary crate）托管runtime。CLI/TUI/GUI使用同一typed client经保护UDS调用。前端仅是请求者/订阅者，不拥有USB/TLS句柄；前端断开默认不取消已接受的操作。明确foreground命令才在请求结束时显式stop并等待结果。

- root管理named profile与前端UID→profile/actions规则；前端只能传ProfileId/SessionId/OperationId和有界筛选，不能传任意credential路径、endpoint、device pool或自报remote principal。
- agent以kernel SO_PEERCRED识别前端，分别记录initiating UID与verified remote principal；统一broker UID不能让所有普通UI用户共享其全部设备池。helper仍只信既有专用broker OS角色/root最大许可，不被UI授权替代。
- Native export/stub同OS进程owner约束保持。首实现agent内任务持有handles；若后续改worker子进程，必须重新明确prepare/open_stub/close的实际PID与监督，不拿父PID自动代替。
- root-owned父目录内预建/明确管理UDS，默认不暴露TCP管理API；限制32前端连接、每UID配额、每请求64KiB、每页<=64条/64KiB、5秒帧截止（具体常量待设计接受）。同UID仍是信任域，不能宣称可防任意同broker UID恶意进程。

替代：仅抽出library而每CLI自持runtime，能复用代码但不能满足后续多前端共享长期会话；把TLS/业务全部放root helper会扩大秘密/网络解析与权限面；以子进程wrapper只拼stdout无法提供durable操作语义。故后两者不推荐，单进程library可作为迁移步骤但不是最终SessionManager。

## 3. 最小有意义的公共接口

以下为候选Rust形状，不声称已可编译；typed ID有长度/格式边界，字段私有通过validated constructor建立。请求人身份只能由IPC适配层创建，不能由JSON自报。平台特定操作仍在现backend/helper层。

```rust
pub struct SessionManager { /* Arc<Runtime>, bounded command channel */ }
pub struct OperationTicket { pub operation_id: OperationId, pub session_id: SessionId }
pub struct EventCursor { pub service_epoch: EpochId, pub sequence: u64 }
pub enum CancelOutcome { Accepted, AlreadyTerminal, CleanupPending }
pub enum ReconcileOutcome { Released, StillOwned, Uncertain, External }

impl SessionManager {
    pub async fn discover(&self, caller: &Caller, profile: ProfileId) -> Result<CatalogSnapshot>;
    pub async fn start(&self, caller: &Caller, request: StartRequest) -> Result<OperationTicket>;
    pub async fn stop(&self, caller: &Caller, session: SessionId, request_id: RequestId) -> Result<OperationTicket>;
    pub async fn cancel(&self, caller: &Caller, operation: OperationId) -> Result<CancelOutcome>;
    pub async fn operation(&self, caller: &Caller, operation: OperationId) -> Result<OperationSnapshot>;
    pub async fn sessions(&self, caller: &Caller, page: PageRequest) -> Result<SessionPage>;
    pub async fn subscribe(&self, caller: &Caller, after: Option<EventCursor>) -> Result<EventSubscription>;
    pub async fn reconcile(&self, caller: &Caller, session: SessionId) -> Result<OperationTicket>;
    pub async fn shutdown(self) -> Result<ShutdownReport>; // await actual task/cleanup ownership
}
```

`StartRequest`只含named profile、明确session intent、client RequestId；RequestId按caller/动作/规范化输入摘要去重，相同ID不同参数拒绝，bounded去重记录不能无限增长。返回ticket表示**已持久接受**，不是已导入。取消只发出请求；`Cancelled { cleanup: Verified }`须等真实cleanup结果，UI断开、future Drop或超时不能伪造成功。

`SessionSnapshot`至少含稳定SessionId、AttemptId、operation、role(import/export)、profile与DeviceKey、原resource关联ID、state/revision、desired_state、resource_observation、transport_health、typed_error、cleanup_outcome、metrics及其采样时点。不要把所有维度压成一个enum：设备Available/exported与会话Connected不同；server stub USED不证明远端VHCI枚举，server只能报告实际证据层级。

core九状态作为受验证的业务转换约束保留，真正转移由actor产生：Discovered→Available（元数据+policy）；export用Exporting→Available{exported}；import经Connecting→Connected（client已verified receipt）；stop/error→Disconnecting，只有资源实况确认后Disconnected。Error不能替代“资源仍可能存在”；Recovering只协调再到Available/Disconnected，重新连必须新Connecting/AttemptId，不从磁盘或PID直接恢复Connected。

旧legacy sessions可在统一查询中作为 `External/LegacyObserved` 展示，源事实/权限明确；不为统一UI把外部端口变成本工具可stop资源，也不把不具备的metrics填0。

## 4. 持久操作、事件、审计和重启

持久业务store独立于helper Native/USBFS资源journal，二者不能互相覆盖。建议单writer actor+有界同步持久worker，有限schema/record/frame/总磁盘预算，持久化前校验。保存操作Intent、参数摘要/请求人、资源关联、状态revision与结果；不保存private key、原USB内容或原序列号。先durable Intent再变更；实际helper prepared channel映射durable后再commit；观察/cleanup结果真实获得后再durable Terminal并发布。

**当前API缺口**：helper仅root能NativePending/reconcile，agent不能读root磁盘绕过。需新broker-scoped只读资源查询（按当前policy/其broker UID，不泄露其他租约），以及typed application correlation ID附着prepare记录，防“prepare成功但agent尚未存映射即死”的无法关联窗口。关联ID仅审计索引，不是授权token。旧record缺此字段保持manual/不可认领，严格式版本迁移不能静默合成owner。

启动Recovering读取durable intent与helper权威receipt并实查sysfs；自己的旧活会话不能靠读取记录恢复TLS密钥/FD。已释放才清算旧attempt；仍存在/来源不明则保留Uncertain阻新grant。自动重新连接只能是管理员明确允许的非存储profile，旧资源已证实释放、当前授权仍有效后，以新TLS认证、真实IMPORT/枚举创建新attempt，再做实际I/O验收。helper restart失去原FD能力时仍无通用Native自动恢复契约，必须另设计/实测；新agent单次restart+ready不计恢复成功。

事件为`{epoch, seq, operation_id, session_id, attempt_id, revision, from/to, cause, error_code, resource_refs, wall_time, monotonic_elapsed}`；单actor保证每session因果序。跨重启单调时钟不可直减，使用新epoch和wall时间做显示，不伪造统一monotonic时间。订阅bounded，有丢失则显式`Gap {oldest_available}`，客户端重新拿**带同一cursor边界的快照**，不能静默漏失。broadcast只做live通知，durable事件/查询由store提供，不把Tokio ring当审计历史。

审计与debug tracing分离：操作接受/授权结果/资源关联/转移/取消/清理/结束形成有界append记录及轮转manifest；调试级别不能关闭必要审计。日志磁盘满时拒绝新变更，仍优先执行已有资源cleanup并显式AuditDegraded，不能因审计失败拒绝释放或虚称durable终态。agent-own存储只抵御其他UID，不能宣称防同broker/root篡改；特权事实以root helper记录为权威并关联ID，强防篡改留给另行明确的日志信任模型。

## 5. 心跳、RTT与带宽的诚实语义

**wire v1不具备heartbeat槽位。** IMPORT后是标准raw USB/IP，不能插自定义ping，也不能发任意URB做心跳（有设备副作用）。独立authenticated控制连接可测control RTT/peer进程存活，但同一个数据TCP黑洞时仍可能pong，不能当数据链心跳完成。

候选比较：A)保持v1，显示EOF/错误与`health=Unknown`，可加独立controlRTT（明确不是data健康）；B)经协商引入新ALPN/协议版本的有界tunnel frame，在同一TLS连接上承载Data/Ping/Pong/Close，再给内层engine/kernel呈现完整有序USB/IP字节流。**若要求真正数据连接空闲心跳，推荐B作为单独切片**；不能静默改v1或靠TLS库内部细节。每帧固定header+<=64KiB payload、有限buffer、one outstanding unpredictable ping id、严格nonce/seq/epoch与超时、control公平调度、半包截止；未知frame和版本拒绝。拥塞/流控可能阻止pong，超时表示可观察通道健康失败，不证明远端已释放资源。stop仍走真实cleanup。

RTT公开为`application_ping_rtt`，从单调发起到对应pong，不称USB请求RTT或网络纯RTT。timer漏tick使用skip避免恢复后洪泛；interval、deadline、miss阈值由受保护policy设置，不能把每个用户UI任意值变成资源攻击。

字节计数在TLS解密后的IMPORT后数据边界统计实际成功read/write，不计重复poll和失败write请求长度；Native无需窥视URBpayload即可统计 `usbip_wire_rx/tx_bytes`。管理字节、TLS ciphertext和USBpayload分别命名，不混加。用AsyncRead/Write wrapper、私有原stream+permit不外露，atomic/snapshot提供实时累计，即使pump报错/abort也保留已观察值。Native helper内核内部URB指标仍Unavailable。吞吐为同一epoch两个成功采样间的delta bytes/elapsed；声明是否计header，旧样本显式stale，不能以0表示不支持或以fixture I/O宣称性能基准。

## 6. 实施接点与最小实证

接点：core新增typed session/operation/error/event/metric模型；service library从CLI secure_client/native_server/secure_server抽出真正生命周期逻辑；CLI只参数/输出/UDS client。protocol/transport承载版本化应用IPC/可选heartbeat tunnel；backend server在授权/claim/receipt/cleanup实际点发typed事件，不读published snapshot猜状态；helper只增加有界授权查询/关联和明确必要的reconcile能力，保留现锁、FD、owner与schema安全。旧secure foreground可作为service library适配器保留，旧legacy协议不退化。

| 阶段 | 必须实际软件检查 | 有意义的QEMU判据（均为计划，未执行） |
| --- | --- | --- |
| S-A 统一owner与多前端 | 真UDS多进程caller认证、两UI同snapshot/cursor、错误profile/UID拒绝、重复requestID、subscriber背压/Gap；真实TLS/IMPORT保持；临时store故障/截断/权限 | 一个agent持会话，两独立UI查询控制；杀UI不影响HID/serial真实I/O；另UI停止后NULL/Gone/finaljournal/driver恢复；agent TERM总await；至少Native与USBFS一项，virtual纯软件/后续回归 |
| S-B 持久状态/取消 | 各转移deadline、prepare前/后/commit中取消、durable映射前后故障注入、operation与cleanup双错误、task/FD/permit预算不提前退休 | 真helper排队或初始化窗口取消，未提交不碰设备，已提交一直持有/等待清理；两客户端同时首次IMPORT只一实际owner，败者不能抢新代际 |
| S-C 重启协调 | 旧schema/坏尾/跨boot/新epoch、已结束owner与活owner、External/Uncertain保留、幂等退休；程序重启后读取真实状态而非默认空 | client agent KILL并真实wait137；helper自己关闭原K/内核NULL/Gone和旧record退休后新agent重新认证/IMPORT，再真实HID/35B双向I/O。另server KILL/重启恢复完整原baseline+新I/O；helper重启丢FD场景单列，若只能拒绝则只能负向PASS，不计恢复成功 |
| S-D 心跳/metrics | exact-version framedstream duplex、数据乱分片+control交错/超限/未知/半包、不会污染原USBIP；paused-time超时是逻辑测试；实际buffer/error计数核对 | 两guest只blackhole数据外链，控制串口保持；空闲HID pending和活跃serial均超时进入Disconnecting/实际释放，恢复网络后明确新attempt真I/O；IPv4与IPv6；独立控制通道仍通但data黑洞负例防假健康。采样指标与已知wire边界字节独立匹配，不拿TLSciphertext长度代替 |
| S-E 审计与安全收口 | 各事件关联/序列/Gap、audit磁盘满/轮转/时间跳变/秘密marker诊断扫描；真实TLS未授cert/device、撤销/重启policy、重复连接 | 真实不同principal且设备池不完全相同：目录隐藏、猜key/BusID拒绝且helper/kernel资源不变；模拟agent审计盘满拒新start但已连cleanup仍成功；helper重启/证书更新窗口分开验证 |

每个切片先新专项，再按变更面重跑既有三backend×双栈六矩阵；不要一开始组合所有故障使首因无法定位。普通软件测试不操作USB，特权故障仅两guest，宿主控制和外链隔离且失败即时留原件。最终要同时看状态/审计、真实sysfs/journal、实际I/O和进程退出；单纯内存session出现Connected或restart发ready不算。

## 7. 不可省略的完成边界和来源

原G3还包含S1–S6适用项、完整认证负向/轮换/审计、启动协调和异常生命周期。S6目标OS/内核/架构隔离与设备验证、真实G1/G2不能由本宿主QEMU补成通过。UI只是下一阶段消费者，本切片不创建TUI/GUI或把静态网站当UI交付。存储仍拒绝，绝不自动恢复写入。以上候选需root选择实施切片和接受设计后再改源。

技术依据优先固定本地源码：Cargo.lock锁Tokio1.54.1，实际registry `io/util/copy_bidirectional.rs:44–76`说明EOF半关闭、成功后返回累计且错误可能丢弃buffer；`sync/broadcast.rs:27–63`说明ring覆盖与Lagged，不能用作durable审计；`task/blocking.rs:106–118`说明已启动blocking任务不能abort。对应官方入口：[copy_bidirectional](https://docs.rs/tokio/1.54.1/tokio/io/fn.copy_bidirectional.html)、[broadcast](https://docs.rs/tokio/1.54.1/tokio/sync/broadcast/index.html)、[spawn_blocking](https://docs.rs/tokio/1.54.1/tokio/task/fn.spawn_blocking.html)。本次web工具无法访问这三个版本页面，结论由本地固定发行源码与SHA核对；不声称取得网页正文或做过运行实验。
