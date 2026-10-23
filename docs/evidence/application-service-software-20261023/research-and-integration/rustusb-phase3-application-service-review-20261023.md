# Phase 3 共享应用服务：只读实施审查

状态：建议，未批准/未实现。基线 `8e6baf5ab64a42f6698f15c8928dcdd6591e4570`，审查开始工作区 clean。仅阅读源码、固定设计与验收记录；未修改仓库，未执行 Cargo、CLI、helper、QEMU 或 USB 操作。已读取 AGENTS、项目 rustusb-development 与 writing-technical-design skill；本任务明确只读，不新增框架、依赖或组件 skill。

## 推荐的实际切片

提取现有真实 job 到一个实际非空应用库（建议 `rustusb-app`，与提取同提交落地，避免先造空 crate），由普通 broker 服务拥有。CLI 前台模式和后续 TUI 调用同一库；交互用户通过另一个受保护 UDS 控制长期 broker。不要 spawn 现 CLI 然后解析 JSON，不要把测试 stdin 或 stdout JSONL 直接改名为业务服务。

交付必须同时含：真实 start/stop/取消、入站/出站会话登记与定向断开、九状态的实际转移/证据、过滤后的 snapshot/events、重启不确定状态协调及基础有界审计。只有 profile/status/echo RPC 不满足这个切片。完整 G3 仍还包括认证负向/轮换/撤销、断网等适用 gate，不能因该切片通过就标完成。

## 当前事实和必须保留的所有权

| 位置 | 实际行为 | 提取约束 |
|---|---|---|
| `secure_server.rs:38`、transport `config.rs:68/86` | signal 先注册；同步可信配置在 owned blocking task 中；实际非0 broker UID；pool namespace 在 helper 前核验 | 载入/命名空间/UID 检查仍先于任何授权能力；RPC 不提供 config/凭据/端点 override |
| `secure_client.rs:115` | 真目录→新 TLS IMPORT→prepare Native handle→先 relay 再 commit；信号/失效后 join relay、await helper close，分别保留 operation/cleanup error | supervisor 直接拥有真实 handle/relay task，不能因 RPC waiter 丢弃或 UI 断线丢资源；Connected 需真实 import receipt |
| `virtual_server.rs:132` | 真 backend/ServerHandle；event/control 失败仍 await shutdown | stdout 仅为前台适配；业务状态不得通过解析 ready/stopped 字符串形成 |
| `physical_server.rs:41/210/220` | 实际 helper FD、原 lease 列表、nusb 初始化、server shutdown→backend close→所有 Finish；初始化未真实结束时 pending | job 必须持全部 FD/lease 身份到真实结束；不要把被取消 initializer 当作已关闭；逐项 Finish 不因首失败跳过后项 |
| `native_server.rs:101/364/414` | export 原对象/attribute handle；IMPORT 授权先于 open_stub；commit只 USED；IMPORT flush 后 raw pump；先 stub.close 再 export.close | 接受的 helper frame/实际 mutation 不可直接取消；所有连接/watchers tracked+join；published registration 不等于 current cfg/IF |
| `event_output.rs:8` | mpsc2、64KiB event、5s 写/ack；std线程只持stdout，不持设备；取消留下 in_flight | 前台可因自身输出失败停止自身 job；长期服务的慢 UI/日志消费者不能因此停止业务 job |
| `virtual_server.rs:19/86`、Native `:511/535` | 4096B stdin 测试协议；EOF/shutdown全停；虚拟含 hid_key/cdc_emit | 这是明确本地测试能力，不是生产控制授权；不得复制任意 hex/HID 数据入口到跨 UID 业务 RPC |
| backend `server.rs:136/237` | handle目前仅 bind/local_addr/shutdown；private JoinSet无稳定会话ID/单连接停止。shutdown abort+join连接后等 backend settle | 新增真实 session hook/registry 与定向 stop；不能由聚合 backend snapshot 推断每条连接；Drop/abort不是已核验 cleanup |
| core `lib.rs:269` | 九状态和合法转换已有类型，未接完整持久业务状态 | 调用 transition 而非散写字符串；job、设备可用性、连接状态与 USB readiness 不混为一类 |

## 权限域和固定 profile

三域保持：交互 UI UID（例如1000）→非 root 专用 broker UID（例如2000）→root helper。helper 的 `secure_broker_uid` 与旧 `allowed_uids` 仍互斥，secure/legacy设备池仍互斥。普通 UI 不读 TLS key、不接收 K/R/USBFS FD、不调用任意路径或程序。

新增 root-owned 启动 manifest，有限 `ProfileId -> kind/backend + existing trusted config path + allowed controller UID/actions`。例：client profile固定 qemu/lab server alias和一个DeviceKey；publisher profile固定Native/USBFS/virtual之一及有限device pool。实际私钥/TLS/端点仍由现 load_server/load_client加载，不能通过请求替换；manifest载入还核两者broker_uid相同。先严格 namespace 和全配置，再任何设备动作。Profile配置最多有限数量，默认拒绝，配置修改仅合作停止/重启生效。

控制 action 最小 `view/start/stop/audit`，按 UID+profile逐请求核验，分页/事件/审计也过滤；未知与无权 profile对外使用一致拒绝。不要把 helper旧 `allowed_uids` 自动视为所有 secure profile 的控制者。Start权限不应隐含 Stop其它profile或读取其远端/设备详情。一个profile若授权多个UID共同控制，应明确为共享管理域；不能暗称多租户隔离。记录每个操作真实 actor UID。

**同 UID边界不可伪造：** 当前 helper按broker UID授权最大池；有意以同broker UID运行的其它CLI仍属同信任域，可直接使用该UID权限。app profile ACL只隔离其它OS UID。若需求升级为不信任同broker UID进程，就要另立独立UID/每UID helper池或更强OS隔离设计；不能以程序名、路径、PID或“只有本服务应调用”假装解决。最低可行部署继续使用专用无登录UID，不允许普通用户以该UID执行任意命令。

Legacy交互操作保留调用者自己的OS身份：共用库的UI facade可对 `devices` 只读扫描、对旧export/attach直接使用既有helper客户端；secure managed操作才走broker UDS。不要让broker转发任意legacy Request或接受 `act_as_uid`；SO_PEERCRED会变成broker身份，既违反旧策略也形成混淆代理。新的长期服务最初只提供固定 secure profile；旧命令语义继续保留。

## 可落地的库/控制 API

建议采用具体 enum/owner，不先造可插拔大框架。公开前端只有不含能力的客户端与DTO；真实认证caller为服务内部不可伪造类型，禁止 `caller_uid:u32` 从JSON或公开constructor取得。

```rust,ignore
struct ControlClient; // 只连接 root 配置的 locator，验证实际 peer UID
impl ControlClient {
    async fn profiles(&self, cursor: Option<Cursor>) -> Result<ProfilePage>;
    async fn start(&self, profile: ProfileId, request: RequestId) -> Result<OperationView>;
    async fn stop_job(&self, job: JobId, request: RequestId) -> Result<OperationView>;
    async fn disconnect(&self, session: SessionId, request: RequestId) -> Result<OperationView>;
    async fn snapshot(&self, cursor: Option<Cursor>) -> Result<SnapshotPage>;
    async fn events(&self, epoch: ServiceEpoch, after: u64, limit: u16) -> Result<EventPage>;
    async fn audit(&self, cursor: Option<AuditCursor>, limit: u16) -> Result<AuditPage>;
}
struct AppRuntime; // 直接拥有 registry + tracked真实job任务
// 仅启动程序取得 LoadedBrokerProfiles；它不来自控制请求。
async fn serve(loaded: LoadedBrokerProfiles, stop: ShutdownReceiver) -> Result<ShutdownOutcome>;
```

这里是候选接口，不是已有public API。ProfileId/RequestId/JobId/SessionId/游标均是有界类型，不作路径；身份和授权不依赖ID不可猜。`(实际UID, RequestId)`去重，重复同参数返回同Operation，换参数同key返回冲突。有限最近完成记录；明确淘汰/epoch语义，不能无限幂等表。

Start快速返回已授权登记的operation/job ID，Preparing/Connecting后续由实际监督器更新；不等待USB枚举再让RPC一直挂起。RPC超时/EOF只丢响应，不取消已接受的job；明确Stop才请求停止。TUI退出/慢订阅不应全停服务。旧前台命令可在进程内用同runtime，保持CtrlC/TERM停止自身job和现输出背压语义；新的managed命令明确job独立于一次控制连接。

Snapshot至少含epoch/seq/时间、profile/job、side/backend、稳定DeviceKey/logicalserver、状态及观察证据、分离的operation_error和cleanup_error、cleanup `Verified/Unconfirmed/NotStarted`、last_observed/stale、ownership/current-helper引用、可选真实计数。不把不存在/读失败变成空列表或0计数。Native不可观测USB payload指标返回Unavailable；raw relay字节可计数但需注明包含USB/IP头，与USBFS payload计数不同。RTT不可虚构。

## 真实任务树与会话事件

Registry拥有一个明确supervisor perjob；实际Runtime enum可以分别包含现SecureImport、NativePublisher、RustVirtualPublisher、USBFS publisher所有者。manager不在一个大锁里await长操作；短锁只登记/转移/拷贝DTO，真实IO在owner任务里。RPC handlers从不持设备FD、TLS对象或JoinHandle。caller取消不丢owner。Stop用独立watch/标志，不能因普通请求队列满而无法停止；重复Stop返回已有Disconnecting/terminal，不重复写attach/detach。

Server job与入站 session必须分开：一个发布job可以仍Ready/Available且包含多条Connecting/Connected会话。Rust engine在真正authenticated IMPORT/lease-owner建立处产生sessionID/事件，并保留每连接stop入口；Native在真实selected export→stub→IMPORT flush/pump处同样登记。principal只来自VerifiedPeer，绝不接受自报principal。授权校验仍在原registry/slot锁内，事件钩子不能搬移或削弱它。

定向断开要实际关闭该连接网络并走既有lease/stub cleanup，不关整个listener/其它设备。Rust现whole-listener abort+settle路径不能简单变成“按IDabort即Closed”：须保留每会话最后cleanup结果或可等待terminal监督事件；USBFS实际worker仍持预算/请求直到terminal和network retirement。Native已接受helper操作要等真实reply再stub.close。外层permit覆盖实际cleanup。

九状态逐动作接线：发现校验Discovered→Available；publisher绑定Exporting→Available(exported)；导入Connecting；实际receipt后Connected；用户停止/EOF/拔出/故障进入Disconnecting，核验资源才Disconnected；失败Error且附cleanup状态；明确重试先Recovering并重新观察/授权。不得恢复记录后直接Connected。当前Connected注释偏客户端内核枚举，server侧USED/IMPORT flush不证明客户端枚举：实施前明确server-side connection evidence与`usb_readiness=Unknown`，不要静默扩大枚举含义，也不要把发布Ready叫Connected。

必须保留prepare取消差异：Native export需join已接受helper操作再关闭已知handle；USBFS initializer取消若底层仍持FD，必须Pending/Unconfirmed，不提前Finish。signal不是resource cancellation。Shutdown停止admission→向所有jobs发stop→等actualcleanup→关闭出口/审计。软截止后可报告quarantine并保持owner/只读状态；不得仅丢future/ServerHandle就宣称Stopped。实际kernel硬阻塞仍不承诺固定时限可结束，管理员SIGKILL是另外的既有崩溃/未决边界。

## UDS、流控和输出

应用服务socket独立于root helper，例如root准备 `/run/rustusb/broker-2000/`（末目录broker拥有0755或root预授权socketactivation；祖先root保护、无group/other写），服务socket归broker。mode0666+逐请求SO_PEERCRED或root管理控制组0660均可；单凭socket权限不能替代profile ACL。客户端只读root-owned locator得到expected brokerUID/path，核祖先/末目录允许的唯一owner、socket类型/owner、实际peerUID以及前后inode；UI不加载需要brokerUID/privatekey的server配置。

不要复用helper `connect_trusted` 的rootpeer==0判断给普通服务；只复用经审查的安全思路。服务实例持独占lock和socket inode；已有endpoint默认拒绝，合作关闭仅删除本实例inode。若做崩溃后的stale自动回收，须独占锁+已记录旧inode+无活owner/监听的明确证据，未知替换socket拒绝；不能无条件unlink。

协议用schema1+strict JSON、有界frame/计数/5s合作IO截止、有限连接和请求；禁止接受原始helper Request、FD、任意命令/文件/端点/凭据。订阅建议有界事件ring+epoch/seq/cursor与gap/resync标志；慢消费者关闭自己的连接或重取snapshot，不能阻塞USB、丢关键状态却无gap，或不断增内存。限制活动jobs/accepted sessions/observer数，并保留底层TLS16、engine16、helper32与各journal/USB预算；不能按profile无限乘出未控资源。

原EventOutput保留为前台适配，不在job关键路径等UI ack。生产UDS不接受hid_key/cdc_emit；旧 `--control-stdin`测试路径可保留本进程显式capability且只限虚拟，不能当成外部授权RPC。

## 重启协调与基本审计

应用状态不是第二份root能力授权表。应用持久记录只存epoch/job/profile/requester/转移/已收到helper IDs与最后已知事实；不存FD号作能力、TLS key、原始USB数据、完整认证请求。不从旧app记录重新构造K/R或USBFS来源。首次启动读取真实helper/kernel观察；旧Running/Connected变Recovering/Unknown，旧epoch与新job分开。

当前broker可只读Status/Sessions（linux.rs:282），但NativePending/Reconcile和PhysicalPending/Reconcile是root协调；Finish还要求原UID/PID/starttime/boot，见physical_lifecycle.rs:63。不能让新PID拿旧ID伪装原owner。若完整UI要显示当前root未决状态，最小增量是helper **broker-scoped只读pending摘要**：核broker UID+当前policy/所属key/serverpool，分页返回安全ID/kind/phase/owner-ended/需管理员，不暴露其它角色基线；不赋予retire/reconcile/重获FD权限。现root只读协调和opt-in受限CONNECT恢复仍各自负责实际退休。应用文件损坏/丢失不能清空root记录或假定设备空闲。

基础审计独立于stdout和普通debug日志，使用已有serde_json/标准FS与有界writer即可，不引入DB或日志框架。每记录有schema/epoch/seq/UTC+单调时间、operation/job/session/profile、真实actorUID、server principal（仅真实VerifiedPeer）、旧/新状态、固定错误码、分离operation/cleanup结果。安全设备key/逻辑server可关联；默认不记录地址、序列号、证书DER/privatekey、输入JSON、URB/HID/串口内容，亦不把低熵USB内容hash当安全审计字段。认证失败需限频/聚合，不能日志填盘。

审计目录路径由root启动配置固定，祖先root保护，末目录可broker0700供0600文件有界轮转；这是broker信任域内可写审计，不宣称防同UID篡改。或部署由管理员管理journald落盘/轮转，但不能把未配置的stderr当已持久审计。关键admission审计ack失败则拒绝新mutation；已有jobs的Stop/cleanup绝不能被满日志/慢subscriber卡住。writer超时/磁盘满要显式AuditDegraded/Gaps，保留cleanup事件事实和错误，不把“已发送队列”叫落盘成功。审计日志不得反过来授权恢复。

不要往IMPORT后的USB/IP字节流插心跳或应用控制帧。可单独受控管理探测测管理路径延迟，明确不是USB RTT/驱动健康；TCP keepalive/检测阈值需另测真实blackhole语义，不能给合法长期pending IN设任意空闲即断。断网、凭据撤销/轮换及持续认证失败矩阵仍须完成，不能用本轮会话API替代。

## 需要实际验证的最小矩阵

1. 抽取后现四本地信号/两stdout背压、六secure双guest及旧native/虚拟/USBFS回归保持；信号注册先于prepare，cleanup owner不因presentation失败丢失。
2. 真实普通UID控制UDS：允许UID1000只获profileA，拒绝UID1001/未知profile/另一profile的view/start/stop/audit，无helper/TLS副作用；私有fakeUID单测只能验证逻辑，不能替代guest SO_PEERCRED证据。
3. 同request重复/冲突、并发start、stop duringprepare、RPC半包/超长/断线、慢subscriber/gap、配额满；已有job不因观察者退出而关闭，ack不冒充最终完成。
4. 两个实际导入会话活动时按SessionId只停HID，FTDI继续真实双向数据；两Rust及Native server侧真实连接都可列表/事件/定向断开，资源/预算/原driver核验及错误分别可见。
5. 实际网络EOF/热拔/错误触发九状态，partial initialization/cleanup failure保持Error+Unconfirmed，不发假Disconnected；真实job end后才释放registry预算。
6. Broker服务SIGTERM/SIGINT drain全部ownedjobs；SIGKILL后新epoch不接管旧能力，UI读取scoped pending/只读协调结果，root未决仍阻止新grant；不伪造自动恢复。
7. 审计序列/操作关联、固定错误与清理双结果、轮转上限/损坏/磁盘满/慢writer、秘密标记扫描；失败不能绕过授权或阻止cleanup。暂停/断开后Explicit Recovering再重新授权，无自动恢复存储写入。

建议先把上述作为一项完整可运行slice设计并分配实际owner，再开始代码；本审查不授权或预先宣布任何新模块/接口已完成。
