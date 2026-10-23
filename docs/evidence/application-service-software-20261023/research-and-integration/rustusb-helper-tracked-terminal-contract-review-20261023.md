# Helper tracked correlation / terminal proof 契约：只读建议

Proposed，源码基线 `8e6baf5ab64a42f6698f15c8928dcdd6591e4570`。没有修改仓库、编译或执行 QEMU/USB。目标是证明**特定旧 helper 能力的终态**，不把应用历史变成资源所有权，也不扩大 root reconcile 或自动恢复权限。

## 结论

建议新增 tracked API，保留旧 API 与 wire 请求完全不变；tracked admission 在现有同一受保护 journal 内持久写 correlation。若要求退休和 terminal proof 原子完成，采用 Native 私有 disk 2、physical 私有 disk 3，比旁置 proof 文件更小且可靠。旧格式严格读取且只在首次 tracked admission 时升级；旧 API `None` 不强迫未使用此功能的旧 store 升级。

Native EOF 后成功清理并退休是**现有实现**：`native_channel.rs:430` 在 shutdown/观察成功后 retire，再以 `cleanup.and(operation)` 返回 EOF 错误；`native_export.rs:461,524` 同样先完整恢复后 retire；stub 的 `:638` 先 shutdown 并清除持久 stub，再合并原 operation。新工作是留下 durable proof，并非新增“凭 EOF 清理”权限。

## 1. 最小关联和 API

```rust
// Caller-provided bookkeeping, never an OS identity or bearer capability.
struct RootCorrelation {
    instance_id: CanonicalUuid,
    root: RootScope, // Job { id } 或 Session { id }，恰好一个
    attempt_id: CanonicalUuid,
}
fn prepare_native_import_tracked(socket, target, correlation) -> NativeImportHandle;
fn prepare_native_export_tracked(socket, device_id, correlation) -> NativeExportHandle;
fn open_stub_tracked(&self, correlation) -> NativeStubHandle;
fn open_secure_physical_tracked(socket, device_id, correlation) -> PhysicalGrant;
fn observe_operation(socket, correlation, selector) -> OperationObservation;
// 上述签名是候选 async 接口略写；旧入口内部转发 correlation=None。
```

- 每个**helper 能力尝试**必须独立 attempt：同一个 server job 的两设备 export、每次新 stub、每个 physical grant 都各有不同 attempt；root/job 可相同。不能整次多设备作业共用一个精确查询键。
- 只允许固定 canonical UUID/有限 tagged scope；保存前验证，禁止任意备注、路径、网络 principal、序列号。OS owner 仍由 SO_PEERCRED + PID/starttime/boot 获取（Native `native_state.rs:62`；physical `physical_lifecycle.rs:63`）。`Root` 不意味着调用方可以指定 root 身份。
- 应用在调用前持久写 Start/attempt，丢失成功应答后才能以原 correlation 查询。tracked request 必须在变更/FD 交付前持久保留（现 Native import reserve `native_channel.rs:351`；export `native_export.rs:355`；physical reserve `linux.rs:181`），不可在 commit 成功后才补 ID。
- 新 `Request::*Tracked` 变体比给严格旧请求加字段更明确；既有 public wrapper 与旧 JSON 不变。增加可发现的支持版本；新 helper 不支持时直接拒绝 tracked 功能，不回退 untracked。旧 `journal_schema_version:1` 生命周期 IPC 和旧 Pending record 形状不因私有磁盘升级改变。
- 在 active/retained-terminal 中重复同 correlation 必须拒绝新变更，提示查询；同键换 target/kind 也拒绝。**不是重放句柄、重开 FD 或隐式认领。**跨两个 store 的唯一性可以由 typed selector 命名空间明确区分；不要为了 bookkeeping 假造跨 journal 原子事务。若要求全局唯一，应在全局 mutation guard 下同时查两个 store，明确这是 admission 检查而非两文件提交。
- 有限 tombstone 不可能提供永远的 exactly-once 去重。被淘汰后的 Unknown 不能触发相同 attempt 自动重发；明确的新尝试用新 ID，仍受原 pending/safety 授权约束。若要求永久拒绝 replay，需另设计有界注册实例/单调高水位，不能在本切片暗称已有。

## 2. broker-scoped 只读查询

```rust
enum OperationObservation {
    Unknown,
    Pending { capability_id, phase, live_in_this_helper },
    Terminal { proof: TerminalProof },
}
// 不可信/损坏/读取失败返回 typed Err；绝不能当 Unknown。
```

请求还带固定 `selector`（kind + DeviceKey；import 增 server_id，stub 可增 parent lease ID），先验证当前 broker 角色/当前精确池，再查 exact correlation；查询仅返回 `owner.uid == SO_PEERCRED.uid` 的记录。无匹配、无当前池权限、别的 UID、legacy None、被淘汰统一 Unknown，不泄露存在性、计数、owner PID 或 target。未授权的角色本身仍统一 PermissionDenied。不能提供“所有 UUID 列表”作为普通 broker 查询。

为了进程重启后查旧结果，**只读查询可以跨同 UID 的旧 PID**；instance/correlation 不是认证。Finish/OpenNativeStub/close 的原 owner 检查保持不变（`linux.rs:222`、`native_export.rs:556`）。当前 policy 撤销或换 broker UID 后，旧 proof 对当前查询不可见，root 协调能力不增加。same UID 原本就是同一 broker 信任域，此接口不声称隔离该 UID 内的恶意进程。

Unknown 只表示本次受控查询无可报告记录；不能变成 available、cleanup_verified 或“从未开始”。store poisoning/文件被替换/损坏是明确 unavailable/error（现 `native_state.rs:451`、`journal.rs:545`）。Pending 和 Terminal 都是观察，不授权新操作；新 grant 仍独立重新检查全部条件。

## 3. 同文件磁盘结构与迁移

```text
NativeDiskV2 {
  schema_version: 2,
  records: [ImportRecordV2 { old_fields, correlation: RootCorrelation|null }],
  exports: [ExportRecordV2 { old_fields, correlation: ...,
             stub: StubRecordV2 { old_fields, correlation: ... }|null }],
  next_terminal_seq: u64,
  terminals: [TerminalProof]
}
PhysicalDiskV3 {
  schema_version: 3,
  records: [RecordV3 { lease_id, owner, baseline, recovery, correlation: ... }],
  next_terminal_seq: u64,
  terminals: [TerminalProof]
}
```

版本明确路由到各自严格 serde 类型：旧 Native1 不接受新字段；旧 physical1/2 不接受新字段；新版本的 nullable correlation 字段必须明确存在，不能靠 `default` 将损坏的新 record 伪装成旧 record。数组数量和总字节上限在读取/编码均受控。现 Native 单文件原子实现见 `native_state.rs:474`；physical 见 `journal.rs:685` 起。

必须保留：

- Physical V1 → 内存 recovery=ManualOnly、correlation=None；V2 的 recovery 逐值保留。升级到 V3 不从当前 opt-in policy 修改任何旧 record 的 recovery，也不补造 correlation/terminal（现 `journal.rs:134` 的 V1 转换是关键）。旧记录只由原验证路径处理。
- Native V1 → correlation=None；原 Prepared/Intent/Observed、owner、完整 registration/bound/stub 不变；重开全部 unresolved 和全池保守阻塞保持（`native_state.rs:431,520,569`）。
- 新 tracked admission 是明确功能启用点，第一次升级与新 pending reserve 在**同次 commit**；已有旧 unresolved 必须继续阻塞，不能先为绕过它升级/清空。无 tracked 功能时 Native 仍写1，physical 仍按原规则写1或2。升级后即便 terminals 全淘汰，也不自动降级。
- 最小范围只给 tracked 记录生成可按 correlation 查询的 proof。旧 API None 保持原行为；旧记录的未来真实验证可以另行记 root 审计，但不得合成旧时间的 tracked 成功。已升级 store 内的 legacy None 仍不能经 broker correlation 查询。
- 升级不承诺旧 helper 回滚可读：旧 helper 对新版本须 fail closed，不删除 state、不自动降级。旧 producer 构建与真实 legacy migration 验收继续有价值；新 loader 不能要求用 current producer 重建旧记录。
- 生命周期 IPC1 与私有 disk version 分离。现 physical pending 只在 storage==2 时添加 recovery 摘要（`linux.rs:670`）；实现V3时必须显式处理≥2而仍返回旧 IPC1 record 形状，不能遗漏V3已有的 recovery 范围。

单独 sidecar 无法用现一个 rename 保证“删除 pending + 保存 proof”原子；除非增加 WAL/恢复协议，否则不建议，不能用两个连续 fsync 冒称原子。

## 4. Durable proof 的准确含义和原子边界

建议字段：`proof_schema=1, sequence, correlation, capability_kind/id, parent_id?, owner(OS tuple), selector, verifier_boot_id, proof_kind, completion_cause`。必要时增加**版本化原 receipt/baseline digest**以锁定代际；不导出原始 USB 数据、serial 或秘密。query 可以投影掉 PID/路径等非必要字段。proof 是 helper 作出的受保护本地持久断言，不是签名远端证书，也不是未来持续空闲或 FD 已全部撤销的证明。

Proof kind 至少分开：
- Native import `NoKernelWrite` 与 `PortNullAndOriginalLocalGone`；
- Native stub `NoKernelWrite`、`OriginalStubAvailable`、`OriginalGenerationGone`；
- Native export `OriginalBaselineRestored`、`OriginalGenerationGone`；
- root Native reconciliation 的 `PriorBootEnded`、Prepared 未写、Intent/Observed 已释放各自观察依据；不能把 prior boot 结束写成原driver已恢复；
- physical `OriginalBaselineRestored` 或 `OriginalGenerationGone`，再用 cause 区分原 owner cooperative Finish、root reconcile、受限 opt-in CONNECT 恢复。旧 physical verify 已返回 restored/gone（`physical_lifecycle.rs:253`）。

这些类型应由**做出成功裁决的那个函数**返回，不在退休后再次猜测分支。现 `ImportChannel::shutdown()` 与 `StubChannel::shutdown()` 都返回 `()` 且“未写”也成功（`native/channel.rs:830`、`native/export_channel.rs:616`）；可新增 typed-observation sibling/internal 函数，旧函数 wrapper 丢弃观察以保持兼容。parent export root reconcile 一次退休含 unresolved stub 时，只可给 child `ResolvedWithParent { basis }`，不能合成“本 helper 执行了 child K shutdown”的假事实。

新存储入口用 `retire_verified(id, proof)`：从 active 取原不可变 owner/target/correlation，校验 proof 与记录匹配；同一编码/临时文件 fsync/rename/目录 fsync 同时删除 active 并加入 terminal。stub 清空 `parent.stub` 与 child proof 也必须同次 `commit_all`。禁止任意字符串让普通调用者提供自己的 proof。成功应答必须晚于完成持久事务；rename/fsync 不确定继续 poison，不返回 cleanup verified。重开可以读实际落盘版本继续查询，不靠应用 EndLog。

Native/physical 的现存 `retire` 调用点包括正常 close/Finish、root reconcile、physical opt-in restore，必须统一接入；不能只覆盖正常 Finish，遗漏后台退休导致 app 仍丢证据。旧 `FinishPhysical` 缺记录时返回 `not_pending`（`linux.rs:217`），这不是 proof；新查询缺记录保持 Unknown。重复调用 tracked finish 如需返回 proof，仍先走原 owner授权，不把只读同UID查询权限扩展为 finish 权限。

**有界保留**：例如每 store 256 个 `tracked-active + retained-terminal` 槽位（独立于旧 pending 上限），admission 预留该 tracked 能力将来所需的 proof 字节/槽位；需要容量时仅在 admission 淘汰最旧 terminal，永不淘汰 active，不让 cleanup 因“terminal 表满”卡住。Native parent 与 active stub 各占一槽。序列 checked-add，不回绕；固定字段长度和编码上界让退休可落盘。磁盘 I/O 失败仍保留未决。有限窗口外只能 Unknown，因此此方案解决“Finish 后 app EndLog 前崩溃且 proof 仍保留”的窗口，不保证无限离线后的历史可查；若需更长保证，另加明确 ACK/留存策略并给容量满时拒绝新 admission 的规则。

## 5. Native EOF 与 USBFS EOF 的硬边界

同 helper 的 Native 控制 EOF / 超时 / 协议错误不是成功操作，但 helper 仍持原 K/强 attr/实际 worker/锁。只要现真实 shutdown 验证成功，可以写 `completion_cause=control_eof|helper_stop|operation_error`、`proof_kind=...verified` 并退休，原 operation failure 仍保留。无需、也不能用 PID 死亡替代验证；失败则 pending/unresolved 不变。helper重启已丢 K 时继续原 root-only read-only reconcile，绝不按 correlation 重建 K、重写 attr或接管旧端口。

USBFS 不同：FD已交给原进程且可能dup，helper无撤销能力。EOF/进程死亡/agent历史 End 不能写 terminal。只有原 owner Finish 的独立基线/gone核验、原 root reconcile，或原 opt-in恢复实际独立B/关闭/核验/退休可写 proof。`physical_recovery` 的同boot/原owner结束/当前policy/原授权/完整同cfg+IF+alt/每实例单次尝试全部保持；correlation只是旁注。

## 6. 必要针对性验收（后续实施，不是本报告执行）

- 真实旧 Native1/physical1/2 loader fixture，legacy physical producer升级路径：完整旧owner/baseline/recovery不变，None、无追授、默认格式不升级、损坏新字段拒绝。
- tracked reserve之前/之后、FD发送未知、真实 Finish后应答丢失、app EndLog前退出：查询分别 Unknown/Pending/Terminal，不自动重放；同UID新PID可读但不能Finish/Stub。
- Native EOF during prepare/commit/active、原K shutdown失败、replacement/Intent不完整：只有真实既有postcondition成功入proof；旧失败保持pending；stub与parent原子退休分别核。
- 同一提交 fail-before-rename / after-rename / dir-fsync、状态文件替换、proof容量满/淘汰/重开：不出现 active消失且无proof的已确认成功，不在corrupt时返回Unknown。
- 未知/外池/他UID/旧None indistinguishable；root reconcile权限与条件不变；policy撤销后query不泄露。USBFS EOF负向不得退休；opt-in仍用已验证原边界。

仅上述设计审查，不声称新 API/磁盘格式已经实现，不完成 Phase3 或 G1/G2/G3。
