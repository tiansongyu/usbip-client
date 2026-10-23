# 受限应用服务、会话状态与审计实施契约

2026-10-23，起点 clean `8e6baf5`。状态 **proposed / 实施中**。上一goal turn为实际进展：安全数据链六QEMU、412软件和入口文档发布。原Phase0–7完整目标不缩减；本项落实Phase3的真实应用服务、会话控制、状态和基础审计，供后续CLI/TUI/GUI共用。真实硬件、完整Phase3及后续阶段不因本设计而完成。

## 已核事实与采用方式

- `core::SessionState`目前只有九态转换规则，没有SessionManager实现。
- 安全client/server由CLI私有future直接持有；另一个前端不能查询/停止某个导入。Rust `ServerHandle`仅有全停，Native只有发布快照，均不是实际连接注册表。
- helper已持有真正Native import/export/stub能力、USBFS grant/Finish、未决磁盘记录和root协调。新PID不继承旧PID的Finish/导出归属；同broker UID仍是同一信任域。
- Native EOF在同一helper持有原K时，已执行真实shutdown/原代际清理，成功后退休；operation错误与cleanup结果分开。USBFS EOF不能证明交付的FD已释放，保留原Finish/显式恢复/root协调规则。
- 复用锁定的Tokio1.54.1/rustix1.1.5/serde/sha2，不新增密码实现。已读实际官方crate源码：`JoinSet::shutdown`会abort，新的owner supervisor须发合作stop并逐个join；UDS的peer_cred是授权来源。三个官方固定版本docs.rs页面本次无法访问，来源索引记录失败并锁实际安装源码SHA，不捏造在线核验。

采用实际非空`rustusb-service`库，直接拥有真实runner、连接任务、helper handles和清理树；新`rustusb agent`是其非root托管入口。先实现托管runner，现前台安全命令保留原JSON、信号与测试控制语义；将前台命令提取为同runner的适配器继续待做，不因本项而宣称两入口已消除重复。GUI/TUI以后调用同一应用客户端；不解析CLI子进程输出来管理资源。

## 身份、配置和本机控制

root管理的agent schema1配置固定非0 `broker_uid`、helper socket、control/data目录、最多16个profile以及operator规则。profile为固定ID→现可信server/client配置路径及backend；启动时全部读取/验证，任何失败均在取得设备前返回。只有broker UID加载私钥并运行agent。Native-only功能不无条件引入Rust engine/nusb。

operator规则为OS UID、允许profile集合及read/start/stop/discover/audit动作。代理请求只含typed profile/job/session/request ID、分页cursor；不接受配置路径、端点、设备池、证书、principal声明、FD或任意helper Request。ID不是bearer授权，每次先核OS UID与profile/action。root管理员可以查看；同broker UID内部代码属于既有信任域。旧helper allowed_uids不会自动取得agent权限。

control目录由管理员创建为broker所有、0711，所有祖先root保护且不可group/other写，socket固定`control.sock`；0666连接权限不代替SO_PEERCRED授权。目录内独立0600 lock以safe flock排除相同endpoint的重复agent；data目录broker所有0700并独立锁。只在持有endpoint锁、确认原socket类型/owner/inode且无活跃listener时清理自己的stale socket。客户端验证整个父链、socket身份、实际peer UID及连接前后身份，不盲信文件名。

UDS并发16，帧至多65536B，读取/响应合作截止5s，分页最多16项，慢/断开的UI不能拥有或取消真实job。启动返回持久化后的operation/job ID；UI退出仅结束该请求。相同UID/profile/request ID在有界保留窗口内返回原操作。显式`RetryStart`/CLI `--retry --request-id`在窗口外返回request_expired且不启动；普通Start表示新意图，不能拿它猜过期旧操作，不承诺无限去重。

候选命令为`agent run/profiles/start/stop/sessions/events/audit/discover`，固定`--config`只选择root可信manifest。旧`--socket`管理命令契约保持，不把它偷偷改为broker控制。生产RPC不暴露HID tap、CDC inject或stdin测试输入。

## job、设备session与实际owner

job为一个固定profile的运行任务；server job可拥有多个export设备session和入站import session。全局引用含instance/job/session身份，backend局部ID只在所属ServerHandle内有效。每个helper能力尝试有独立attempt ID；多设备export和每个stub不得共用一个attempt。

所有权路径：

```text
service supervisor → job runner → connection/lease/relay tasks → actual close/cleanup
                               → helper typed handle/control UDS
UI → authorized application request / snapshots / bounded events
```

`stop`只发合作请求并返回accepted，完成由实际owner receipt裁决。manager本身不await长USB任务而阻塞其他请求；它保留owner task到真实结束。取得句柄可能已经发生的prepare不能被select丢弃；先接收/入账，再依据stop关闭。网络纯读写任务可停止并join，内核/usbfs的blocking任务必须保留其guard/FD直到真正结束。

Rust backend增加自身有界`ServerMonitor`与不持能力的`ServerControl`；Native runner复用相同事实DTO。bind前安装managed模式，旧bind API保留。接口包括snapshot、changes、events_after/ack、request_disconnect；局部ID、revision、gap/oldest/next明确。monitor不暴露FD/lease/VerifiedPeer构造能力。

每个IMPORT在claim前预留固定事实槽和stop入口。managed模式须先获得已持久化的application admission确认，超时/拒绝不claim；这与普通watch观察不同。已授权peer/key仍须在真实slot内原子复查，WAL不替代设备权限。最多16 active连接及有界未ack终态槽；槽满在claim前拒绝新导入，不能为腾位置丢旧终态或阻挡实际cleanup。事件槽保留至manager确认，watch只是唤醒。

真实接点为：Preparing/Claiming → lease已归连接owner → IMPORT完整write+flush → Forwarding → 网络两half退场 → begin_close → 实际cleanup完成。server Connected证据是本地接受IMPORT/通道建立，不等于远端VHCI枚举；client Connected只在实际VHCI完整receipt后成立。

claim期间stop必须等待该次attempt的实际ready/cleanup或保持Unconfirmed与真实worker关联。不能把被取消的claim future、全backend settle或其他设备完成当作该session已清理。per-session stop不关闭另一设备、listener或其他client。

## 状态与指标

复用九态但区分角色和证据。export设备从Exporting到Available只在实际注册/绑定与listener准备完毕后发生；入站连接从Connecting到Connected只在完整IMPORT回复flush后，client则在VHCI实际枚举后。停止进入Disconnecting，Disconnected只接受实际清理证明或明确未开始任何资源的证据。错误保留operation与cleanup两个结果；未确认终态不能报Available/cleanup_verified。

Device可用性、server占用和client状态是不同维度。stop_requested与当前已观测state分开，不能先猜Disconnected再等worker。软超时可报告Error/cleanup pending，owner任务仍保留；后来真实核验完成经Recovering再到Disconnected。重启不重新发布旧Connected，也不自动重连存储。

实时统计按成功read/partial write计数并在错误/停止后保留，明确为IMPORT后USB/IP application字节（含协议头），非USB payload或TLS ciphertext。计数饱和可见，采样用单调时钟；不记录原始内容。活动数、失败数和耗时有明确scope，跨epoch历史未决不被新进程live_resources=0掩盖。

原raw USB/IP v1不能插入ping。独立TLS控制pong不证明数据链存活。本项不会生成假的RTT/healthy值；raw模式显示heartbeat unavailable。完整目标中的同数据链heartbeat/RTT需要后续独立版本化、有界data/control framing及真实data-only blackhole验收，保留为未完成项，不用本次状态API替代。

## 持久操作、审计与重启

data store为单writer、safe flock、0700目录和0600 regular/nlink1文件，固定祖先与FD身份检查。初始实现采用有界原子状态文件：active操作/会话、最近审计事件和request去重在同一事务；写临时文件、fsync、rename、目录fsync后才ack。序号不回绕，分页报告oldest/gap。snapshot上限2MiB，active记录最多512、事件最多1024、近期终态128及request窗口256；新start/session/attempt编码只准入到1MiB，预留后半给有限字段的actual ID、计数、terminal与完整audit ring。写前容量拒绝不把健康store毒化；真实写入不确定才拒绝后续准入。canonical剪裁同一份状态进入磁盘和内存，不能删除仍有保留child引用的Finished job。不会把有界历史保留说成永久审计归档或已完成日志轮转。

start intent必须先落盘再启动真实runner；每个managed入站IMPORT在claim前完成admission落盘。状态变化、控制者UID、profile/session/attempt、principal安全ID、原因码和cleanup证据写审计；无USB内容、序列号、密钥、完整证书或任意原始错误字符串。消息/枚举字段有界。磁盘满/损坏/不可写时拒绝新操作，清理优先继续；已取得能力的worker不能因记录失败被drop。

重启将旧非终态标Recovering，保留旧instance/owner与证据；不接管旧handle，不按PID死亡或新进程资源0退休。helper扩展tracked API：`BrokerCorrelation {instance_id,session_id,attempt_id}`，同broker UID+精确correlation只读查询active/verified terminal/Unknown；无新reconcile权限。当前pool之外、旧None、历史淘汰和未知统一Unknown，不泄露外池；损坏必须报错。

terminal proof与原record退休必须同一journal原子提交。Native disk1→2，physical disk1/2→3按严格旧decoder迁移；旧记录correlation=None，physical1仍ManualOnly，physical2 recovery授权逐值保留，不按当前policy追授。旧API无tracked admission且store未升级时保持原格式，升级不降级。每个能力attempt独立，重复tracked prepare在每store的kind+UID+exact correlation命名空间拒绝新mutation并引导精确query；不承诺跨store全局exactly-once。

proof区分未触发内核写、实际NULL/old generation gone、原driver/config/IF恢复、原硬件gone、旧boot等，不能合并成盲目success。Native EOF已有真实K cleanup后可记录证明，operation EOF仍为错误；USBFS只有合作Finish、已授权恢复或root核验符合原约束时才留证明。有限terminal容量在admission预留，明确retention/Unknown，不承诺永久去重。旧None不能由新agent冒充已恢复。

agent根据root端的精确证明更新无live job owner的未决记录（含同epoch已结束owner）；没有证明仍Recovering，显示需要管理员协调。所有runner均在能力申请前持久begin_session，因此零session的intent-only旧job可确定无USB申请并结束；不能由此推论存在attempt的session已释放。已预留却未发送的attempt/无handle失败没有证明仍Unconfirmed。没有独立Root proof的Rust虚拟/入站session崩溃恢复仍有保守未决限制。新attempt不自动意味着旧attempt恢复。移除profile前须先协调；原件不删，但当前RPC按manifest profile授权，移除的历史profile需root读protected state诊断。应用store损坏拒绝准入，不静默清空；人工协调不删除来源不明的内核状态。

## 提取与验证顺序

1. 固定core IDs/事实DTO和helper tracked/terminal语义；实现严格迁移/分页/权限，不写宿主USB。
2. backend managed monitor/admission、定向stop及该claim completion；Native与Rust实际托管runner进入service，旧前台保持JSON/信号/背压，统一提取单独验收。
3. service actor、可信manifest、operator/action ACL、持久intent/状态/审计、UDS客户端与CLI agent；实际设备操作只由现helper完成。
4. 软件测试真实FS故障、UI断开/重试、准入/stop竞态、事件滞后、盘满、slot/错误/cleanup、旧格式权限与秘密扫描；执行完整fmt/Clippy/tests/release/rustdoc和feature构建。
5. clean双QEMU实际验证托管client/server三路：UI退出而I/O继续、定向stop另一设备继续、operator/未知profile拒绝且资源不变、restart按原Root证明协调、新attempt真实I/O、审计与实际状态对应。保留原六TLS/IPv4/IPv6正常回归、信号与背压。新测试的能力/版本/ELF分别记账，保留首FAIL。

本设计不将服务运行结果外推真实硬件、不把日志存在当作完整审计验收，也不跳过原始需求的heartbeat、轮转、性能、安装或Windows工作。整体goal继续active。
