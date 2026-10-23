# Phase 3 应用服务 / 会话 / 审计：只读实施建议

状态：proposed；代码基线 `8e6baf5ab64a42f6698f15c8928dcdd6591e4570`。读取当前 AGENTS、核心 skill、架构、安全接入设计及相关源码；接口契约写法参考 writing-technical-design，按任务范围不增加组件 skill 或仓库设计文件。本次没有构建、QEMU、USB 操作或仓库修改。下述建议不是现有功能或完成验收声明。

**建议先抽取现有实际生命周期为一个长寿命、专用非 root broker 的应用 actor。CLI/TUI 只是其客户端；helper 继续拥有特权、原始授权基线与内核清理证明。无需先替换所有后端 trait，也不要把现有 JSONL stdout 当持久权威会话库。**

## 已有可复用能力

| 代码入口 | 能直接复用的契约 | 不能推断的能力 |
| --- | --- | --- |
| `rustusb_daemon::prepare_native_import` → `NativeImportHandle`（`native_channel.rs:72,118`） | 一次 `take_relay`；`commit` 返回逻辑 target+channel UUID+真实 local/kernel receipt；私有 actor 串行 RPC；`close().await`；`invalidated()` | Drop/失效通知不是 cleanup receipt；控制 actor 的任务完成没有公开 join handle；收到 commit receipt 前公开 handle 无 channel ID getter |
| `prepare_native_export` → `NativeExportHandle` / `open_stub` → `NativeStubHandle`（`native_export.rs:138,221,271`） | 原注册快照、强原 attr、同进程单 slot、stub commit/close、完整 unexport 恢复 | `published_device` 是 preexport 快照；stub USED 不证明远端完成枚举；无跨 PID 领养/迁移 API；export lease ID 没有公开 getter |
| `open_secure_physical` / `PhysicalGrant`（`lib.rs:642`、`physical.rs:25`） | helper 在 FD 交付前持久化原基线/OS owner；返回 lease ID、OwnedFd、代际 receipt；已有 `FinishPhysical` | FD 关闭不代表所有 dup 已销毁；授权不是可撤销租约；断开 helper 不撤回 FD |
| `PhysicalBackend::from_sources/snapshot/close`（`rustusb-rust-backend/src/physical.rs:198,262,281`）及 `ServerHandle` | 有真实 worker、缓冲/URB 监督、终态 snapshot、join/独占 registry 检查 | snapshot 是缓存观察；零计数不是旧进程恢复；close 可能返回未决，不得再标 Available |
| `NativePending/ReconcileNative`、`PhysicalPending/ReconcilePhysical`（`daemon/lib.rs:59,103`） | root-only、有界分页、只读检查后退休；不执行管理员恢复写入 | 普通 broker/TUI 当前无权读取完整 pending；不能调用 reconcile 给活跃原 owner 转移所有权 |
| `SessionState::transition`（`core/lib.rs:269`） | 已有九态转换合法性检查 | 没有 session repository、操作 ID、事件序列、持久状态机驱动；不是现成的 SessionManager |

现有关键流程已经在 CLI 私有模块实现：`secure_client.rs:111`、`native_server.rs:94`、`physical_server.rs:44`。其中 stdout、Unix signal 和生命周期编排仍耦合；TUI 目前无法作为库调用并订阅强类型状态。`daemon/linux.rs:283` 的 Status/Sessions 主要是内核快照与 legacy ManagedState，不是安全 client/server 作业及 exports/stubs 的统一目录。

## 必须保留的归属与停止语义

1. 新 agent 若直接取得能力，必须以 root policy 中的 `secure_broker_uid` 运行；该 UID 非 0 且与交互 `allowed_uids` 分离。TUI 不取得 TLS key、USB FD、Native handle，也不能让任意本地用户通过新 agent 绕过现安全设备池。新控制 UDS 需明确 OS-peer ACL 和固定 profile/device 操作；不能直接复用 legacy allowed_uids 当安全动作授权，也不能接受任意命令/路径。
2. 应用 actor 自己拥有完整作业树：client 的 handle+TLS/R relay+复制任务；Native server 的 listener+export handles+所有 connection/stub 任务；USBFS 的所有 lease ID+sources/backend+server/worker 树。观察者只收到可复制的非敏感值。不能为 UI snapshot 保留 PhysicalBackend clone，导致 `close` 的独占前提永远不成立。
3. 在进入可能获得能力的 prepare 前，将 operation 放入 actor-owned registry。前端取消等待或断开 UDS，只丢失观察者，不 abort 此 owning task。明确 Stop 设置 cooperative stop；已接受 prepare 的真实返回必须被接住并入账，然后清理。`secure_client.rs:133` 和 `native_server.rs:145` 已保留这条边界。`physical_server.rs:61` 当前可取消初始化并报告未决；新长寿命 agent 应保留、await 实际初始化任务，不让被丢弃的 `spawn_blocking` 在 agent 内持有未跟踪 FD。
4. client 先停字节流、join 仅负责网络复制的任务，再 `handle.close().await`；保留 operation 与 cleanup 两个结果（`secure_client.rs:176`）。Native server 停 listener、通知连接、join 所有拥有 helper transaction 的任务，再 close 所有 exports（`native_server.rs:255`）。USBFS 必须先 server shutdown，再 backend.close（真实 supervisor/descriptor 结束），最后逐个 FinishPhysical，仍尝试剩余 leases（`physical_server.rs:190,210,233`）。不能对这个整体 shutdown future 加一个到期后丢弃的 timeout。
5. helper 的实际 Native mutation/cleanup 闭包已经持 FD/锁到结束（`native_channel.rs:285,408,430`）；USBFS restore 同样实际持 guard 并反复等待而不 abort（`linux.rs:579`）。应用“停止超时”只能呈现 `Stopping / cleanup unconfirmed`，不能释放资源占位或启动替代作业。
6. 如果第一步只监督现 CLI 子进程：helper owner 是**子进程** UID/PID/starttime/boot，不是父 agent。父 agent 可发既有固定 shutdown 控制或 SIGTERM 并 reap，不能凭 lease ID 代调 FinishPhysical/OpenNativeStub。`native_export.rs:556` 比较整个 OS owner；`linux.rs:222` 要求原 physical owner。wait0 是进程事实，还须对应终态清理回执；SIGKILL/丢日志只能记未确认。重启后的 agent 不能 attach 到旧 PID 就声称拥有旧 UDS/FD/worker。

## 重启与权威来源

- Native store 重开把所有 records/exports 标 unresolved（`native_state.rs:431`），任一未决会阻止新 Native reserve（`:520,569`）。这是当前**全 Native 池**保守拒绝，不是仅同设备；应用层不得把 Busy 提示成另一个设备仍可开。root reconcile 要求 owner 已结束，且原 generation 已 gone/恢复，或前次 boot 已结束；Observed import 核原 receipt，Intent 核 unidentified-released（`:651`）。没有自动接管或重连。
- USBFS 未决按 device 阻止新 grant（`linux.rs:142`）。Finish 要求原 owner；root reconcile 独立核原基线或明确 gone。仅 opt-in 记录且当前策略允许、same boot、owner ended、handoff 已结束，才可能由 helper 独立 B CONNECT 恢复；每 helper 实例每 lease 最多一次尝试（`:535,606`）。同 broker UID 的新 PID 也不是旧 owner。
- 新 agent 启动时，本地未完成 operation 历史全部先标 `OutcomeUnknown/NeedsReconciliation`；再关联当前 helper/内核观察。不能因本地表为空、旧 PID 不存在、helper pending 为空或新服务 snapshot=0，回填旧操作 `cleanup_verified=true`。journal 退休后没有永久历史，空列表只能说明此刻没有记录。
- 普通 agent 当前只能获 Status/Sessions 和自己持有的 receipt；root-only pending 不能假装已可用于 TUI 自动协调。可先如实显示“需管理员协调”，随后单独设计只读 broker-owned pending summary。该 API 只提供观察，不给 Finish/close/adopt 权限；旧 PID 的记录即便同 UID 可见也仍不可控制。

## 最小新增 API 建议（候选，不是当前接口）

```rust
// 应用层；与 helper Request/原始 journal schema 分离。
struct OperationTicket { operation_id: OperationId, session_id: SessionId, epoch: AgentEpoch }
struct StopReceipt { operation_id: OperationId, session_id: SessionId } // accepted only
struct SessionView {
    session_id: SessionId, operation_id: OperationId, epoch: AgentEpoch,
    revision: u64, role: SessionRole, device_key: Option<DeviceKey>,
    state: SessionState, evidence: LifecycleEvidence,
    last_failure: Option<OperationFailure>,
}
// 固定管理员注册的 ProfileId；不接收任意配置路径/程序/原始FD。
enum AppCommand { StartClient { profile: ProfileId }, StartServer { profile: ProfileId } }
impl Application {
    async fn submit(&self, command: AppCommand) -> Result<OperationTicket>;
    async fn stop(&self, session: SessionId) -> Result<StopReceipt>;
    async fn snapshot(&self, after: Option<SessionId>) -> Result<SessionPage>;
    async fn observe(&self, cursor: EventCursor) -> Result<EventPage>;
    async fn shutdown(self) -> Result<ShutdownReport>; // 总 await actual owning jobs
}
```

`submit` 的 ticket 只确认 actor 已登记并负责完成/失败/清理；不等于连接成功。丢失响应后可按受控 request ID 查询，不能自动重复 Start。Stop 幂等，返回 accepted 后通过 snapshot/event 等真实终态。保留有限 operation tombstones 与 epoch，旧 epoch 不静默重放；重启后的历史只能标旧结果/未知，不能新造 cleanup 证明。

`LifecycleEvidence` 必须分开 `transport_closed`、`helper_channel_closed_verified`、`physical_finish_retired`、`kernel_observation` 与 `unconfirmed`。不能把 server ready 当 Connected、stub USED 当远端枚举完成。现有 core Connected 注释是“内核已枚举”；开始统一 server 会话前要明确 role-specific 契约/证据，不能直接改映射掩盖这个差别。listener/job、export registration 和每个远端数据 session 是父子记录，不是一个全局状态格。

**两个小型 helper API 补点可后续独立决定：**
- 给 handles 增只读 `channel_id()` / `lease_id()`（在 prepare 后即可关联，非 bearer capability）；close 增带 ID 与明确 postcondition 的 typed receipt，避免应用只能从 `Result<()>` 推导审计条目。无需序列化或转移句柄。
- 若需 agent 重启可见未决，新增严格授权、分页、脱敏 `BrokerPending` observation，而非放开 root NativePending/PhysicalPending 或现 Reconcile。当前失效 watch 只有 bool；应用可先记 `ControlEndedUnknown`，不得猜原因，日后再加 typed terminal reason。

## 统一状态与基础审计实际缺口

1. 当前 helper `Response` 只有 ok/data/error 字符串（`lib.rs:267`）；稳定 error_category 只在 tracing 中。新应用 error_code 不能解析人类字符串。先在 typed in-process 层提供稳定类别；若扩 helper wire，需显式兼容策略与测试。审计同时保存 operation failure 和 cleanup failure，不覆盖原始因果。
2. 日志有 helper caller_uid/operation/success、Native registered/retired 和 physical restore phase，但没有端到端 operation/session ID、统一 verified remote principal/设备决策、序列/gap/留存接口。现 EventOutput 是 CLI 的 2 条队列/<64KiB/5 秒 stdout 通道（`cli/event_output.rs:8`），不宜原样充当 TUI 事件总线。
3. 最小共享事件：`{schema, agent_epoch, sequence, operation_id, session_id, parent_id, role, DeviceKey, server_id, from, to, code, cleanup_evidence, monotonic_elapsed, utc}`。只用已验证的脱敏 principal ID；禁止 raw USB payload、descriptor serial、key、原始证书/路径 stderr。单调时间仅同 epoch 有序，跨 boot 不直接相减。
4. 源码内真正拥有 lease 的节点发事件；callback 不拥有 FD/handle、不 await 慢 UI。有界队列满时明确 gap/revision，UI 重新取 snapshot。actor 自己更新当前状态后再发布，UI 消费不能决定设备是否清理。需要审计持久化时独立受限 writer/rotation；审计写失败可停止接新动作并报告 degraded，但不得阻止已有 cleanup，也不能宣称该日志防篡改。
5. 推荐首切片：抽取一个共享应用生命周期入口（先安全 client，再 server；最终仍保留三 backend），保持现 CLI 是适配器；新增强类型 snapshot/stop/event 与 lost-observer/stop-during-prepare/slow-subscriber 测试。然后决定 broker 进程 IPC 与只读 pending 展示。先不增加 GUI/TUI、心跳协议、自动重连、后台服务安装或大规模 trait 重写。

以上为源码审查建议；此前 clean b83c931 六组 QEMU 仅证明已归档正向路径，不替代新 actor/IPC/重启/事件丢失验收，不把 Phase 3 或完整阶段 0–7标完成。
