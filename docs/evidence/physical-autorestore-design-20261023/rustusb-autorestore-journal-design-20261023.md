# CONNECT-only 自动恢复：最小权限与 journal 版本方案（建议）

2026-10-23；只读设计，基于 clean `c8f2e905fa68ca80a5a707c63a95d652c1f1ac32`。没有修改仓库、构建、运行 Cargo/QEMU 或执行恢复。本文仅定权限、持久格式与 IPC 接点；恢复执行器的监督与 USB 操作另行实现。

## 1. 建议结论

采用**私有磁盘 schema 2 + 每条记录必填恢复授权枚举**，保留既有 IPC durable lifecycle protocol 1。管理员通过新的精确 device-ID allowlist 授权“租约结束后按完整原基线恢复原 driver”；授权只在创建新 grant 时固化，恢复前还必须重新检查当前有效策略。旧 schema 1 的每一条记录永远解释为 `ManualOnly`，当前策略后来加入该设备也不能追认旧记录。

默认没有 opt-in 时，新目录及已有 v1 文件仍写 schema 1，原 CLI、原 journal QEMU 与正常 Finish 不变。第一次写入明确 opt-in 的新 grant 时，使用原原子提交流程将整个文件转为 schema 2；旧记录增加显式 `manual_only`，其原 owner/baseline/lease 不变。之后即使文件为空，也不自动降回 v1。旧 helper 遇到 v2 拒绝启动，这是启用新功能之后的明确降级边界。

无需为这个受限路径新增逐次 USB 操作 Prepare/Complete，不必收回当前可信服务的整设备 FD 接口。新枚举的授权对象是整条可信原基线，不声称只撤销服务确实做过的每次变更；跨 configuration/alternate 恢复仍不在本枚举内。

## 2. Policy：默认空，精确设备，独立于导出许可

候选字段：

```rust
#[serde(default)]
pub physical_connect_restore_device_ids: Vec<String>,
```

配置片段：

```json
{
  "physical_device_ids": ["usb-<64 lowercase hexadecimal digits>"],
  "physical_connect_restore_device_ids": ["usb-<the same exact fingerprint>"]
}
```

这里的占位符是说明，不是可直接加载的有效指纹。沿用 root 管理、`deny_unknown_fields`、64 KiB 策略读取上限。新增列表最多 1024 条，每个 ID 按现有格式校验，拒绝重复，且必须为 `physical_device_ids` 的子集；因此也继承与 Native export IDs 不相交的约束。不新增全局 `auto_recover: true`，不把 physical export 许可等同恢复许可。

增加两个小的内部接口即可：

```rust
fn physical_recovery_authorization(&self, id: &str) -> Result<RecoveryAuthorization>;
fn authorize_physical_recovery(&self, record: &Record) -> Result<()>;
```

前者在已通过现有 UID/physical/trusted-network 校验的新 grant 创建路径中决定记录值；列表没有此 ID 时返回 `ManualOnly`。后者必须同时满足：

1. 持久记录为 `ConnectOriginalDrivers`，不能从当前策略推导或升级 `ManualOnly`。
2. 当前有效策略仍允许原 `owner.uid`、原 device ID、trusted network，且 ID 仍在恢复 allowlist 中。
3. 原记录与执行时系统身份/代际等条件另由恢复器核验；策略允许不等于可执行。

建议枚举采用字符串而不是 bool：

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum RecoveryAuthorization {
    ManualOnly,
    ConnectOriginalDrivers,
}
```

`connect_original_drivers` 的契约固定为：同 boot、同 receipt/指纹、同 configuration、完整接口编号集合及各 alternate 一致；原 `Some(driver)` 目前为 null 的接口才允许 plain CONNECT；已经为原 driver 的接口不写；任何其他 driver/usbfs/usbip-host、配置/接口/alternate 不符均保留记录、拒绝写。原 driver 为 `None` 的接口必须仍为 `None`，不能借此绑定新 driver。禁止 detach、CLAIM/RELEASE 探针、reset、setconfig/setalt 或 sysfs bind/unbind。CONNECT 后须完整复核，不能把 syscall 成功当成恢复成功。CONNECT 不能指定驱动，这个枚举不承诺内核一定选择原驱动。

### “当前策略”的准确含义

当前 `App.policy` 只在 helper 启动读取，Status 明示 `policy_reload: restart required`。最小实现应保留并明确这一行为：每次恢复决定都重核**此 helper 当前有效的已加载策略**；重启后的新策略可否决旧 v2 记录，策略撤销不会改变记录历史授权。不要声称修改磁盘 policy 即时撤销正在运行 helper 的权限。

已与 root 确定：本切片保持 restart-required，不增加磁盘即时读取 gate 或完整策略热重载。管理员须先停止 helper，等待已开始的恢复工作退出，再改变设备用途或按新策略重启；编辑磁盘并非即时 revoke，旧服务 A 的既有 FD 也不会因此撤销。

## 3. 磁盘 schema：严格区分旧缺字段与新缺授权

v1 完全保持现有字段：

```json
{"schema_version":1,"records":[{"lease_id":"…","owner":{},"baseline":{}}]}
```

v2 每条记录增加必填标量：

```json
{"schema_version":2,"records":[
  {"lease_id":"…","owner":{},"baseline":{},"recovery":"manual_only"},
  {"lease_id":"…","owner":{},"baseline":{},"recovery":"connect_original_drivers"}
]}
```

示例省略 owner/baseline 的真实必填字段，只表达格式差异。不要对 v2 的 `recovery` 加 `serde(default)`，不要用 `Option<RecoveryAuthorization>`，不要接受 `null` 或未知值。旧 schema1 不应接受这个新字段——其 `RecordV1` 仍 `deny_unknown_fields`，避免攻击/误操作把 v2 内容伪装成 v1。

内部候选结构：

```rust
struct Record { lease_id: String, owner: Owner, baseline: Baseline,
                recovery: RecoveryAuthorization }
struct RecordV1 { lease_id: String, owner: Owner, baseline: Baseline }
struct JournalV1 { schema_version: u32, records: BoundedRecords<RecordV1> }
struct JournalV2 { schema_version: u32, records: BoundedRecords<Record> }
// Store 私有 schema_version: u32，只允许验证后的 1 或 2。
```

实际代码可以复用当前 `bounded_vec`/`deserialize_with`，不必引入泛型公共容器。两个 journal 与两个 record 都严格拒绝未知字段、重复 JSON key、缺字段。`OriginalInterface.driver` 继续使用现有 `required_driver`：显式 null 合法，省略 driver 不合法。owner、receipt、接口、driver 名称与跨 record 重复 lease/device 校验全部保留。

### 保留分配上限的双遍 loader

不要先把整个文件反序列化为 `serde_json::Value`，也不要使用会先缓冲全部内容的 untagged enum 来猜版本。仍先保留现有 16 MiB 文件/读取上限，然后：

1. 第一遍用严格小 header 类型解析 `schema_version: u32` 和 `records: serde::de::IgnoredAny`；两个字段均必填，未知/重复字段拒绝。`IgnoredAny` 验证 JSON 但不分配整个 records 树；不要关闭 serde 默认递归深度限制。
2. 仅版本 1/2 可分派；未知版本直接拒绝。
3. 第二遍按选定的 `JournalV1` 或 `JournalV2` 解析，records 仍在第 257 个元素前拒绝，interfaces 在第 257 个元素前拒绝；字段缺失、类型错误、重复 key、未知字段均在各严格 DTO 拒绝。
4. 全部记录验证至局部 map 后再发布 Store 状态。v1 到内部 `Record` 的转换在此唯一位置显式赋 `ManualOnly`，不读取 Policy。
5. 缓存原始 bytes 与实际磁盘 schema；load 本身不重写文件，也不改变原 inode/bytes 对外篡改检测。

这样“v1 没有 recovery”与“v2 缺 recovery”不会被同一个默认值混淆。内存上限仍由 16 MiB 文件、256 records、每记录256 interfaces 和原字段验证共同限制。

## 4. Store 接口、迁移与失败契约

候选新增/调整，不需要更换安全存储实现：

```rust
pub(crate) struct Snapshot { pub schema_version: u32, pub records: Vec<Record> }
fn snapshot(&self) -> Result<Snapshot>;  // healthy 后一次锁内复制完整有界表
fn reserve(&mut self, record: Record) -> Result<()>;
fn get(&self, lease_id: &str) -> Result<Option<Record>>;
fn retire(&mut self, lease_id: &str) -> Result<bool>;
```

`records()` 可保留为兼容内部调用，但 pending/recovery 扫描应拿一次 `snapshot()`，避免分别读取 schema 与 records 得到不同提交的视图。全表最多256 records，不能扫描时从磁盘或 sysfs 无限追加记录。不要公开“set recovery”或“升级某旧 lease 的权限”API。`Store::reserve` 的参数只能来自 helper 实际捕获的 owner/baseline 与当前策略决定，RPC 客户端不能提供 owner、baseline、恢复许可或配置路径。

`commit(next_records, next_schema)` 的版本选择：

| 现磁盘 | 下一表 | 写入 |
| --- | --- | --- |
| 新目录/v1 | 全部 ManualOnly | v1，旧格式 |
| v1 | 含首个 ConnectOriginalDrivers | v2；所有旧记录显式 ManualOnly |
| v2 | 任意，包括空表 | v2，不自动降级 |

v1 输出只能从全部 ManualOnly 内部记录生成 `RecordV1` 投影；若有非 manual 而调用 v1 序列化，必须错误，不能静默丢授权。v2 则序列化全部显式 recovery。版本与记录必须在同一次 temp write → file fsync → rename → directory fsync 中提交；不能分文件先写权限再写 baseline，也不能写文件后重新按当前策略补权限。

只有原子持久提交成功后才发布新的内存 schema/records/bytes/identity，再允许 FD handoff。任何不确定写入仍 poison Store，拒绝所有读/授权/恢复，直到受控重新打开。切换 schema 的目录 fsync 失败也不能交付 FD；重启后实际文件可能已是 v2，完整解析后照原授权处理，不能假定失败等于回滚。

旧记录的实际迁移路径是：保持 manual → 原 owner Finish 或管理员只读 Reconcile 复核并退休 → 后续全新的 grant 按当前策略决定许可。无“补签旧 lease”命令。移除恢复 allowlist 后已记录的授权保持历史值，但执行 gate 拒绝；不能为了停用恢复将 journal 降级、改写或清空。

## 5. IPC 与诊断：不绑死私有磁盘版本

- `OpenPhysical { journal_schema_version: 1, device_id }` 保持原形状和值。源码注释已定义其为 durable lifecycle protocol；修订注释说明其**不是私有磁盘文件版本**。不因磁盘 v2 要求旧客户端发2。
- `GrantDescription` 有 `deny_unknown_fields`，保持 `lease_id/device/receipt` 三字段。不在旧 grant 响应增加 recovery 字段，否则旧客户端实际会在 FD ACK 前拒绝解析、留下未决授权。
- `PhysicalGrant`、现有服务启动/Finish 调用不需为了记录许可而改接口。管理员 opt-in 决定新 grant 的恢复行为；旧客户端仍正常拿 FD/合作结束。未 opt-in 的默认行为完全保留。
- `FinishPhysical` 与 `ReconcilePhysical` 仍只读核验并退休；不得让既有管理员 reconcile 请求暗中执行 CONNECT。自动恢复执行器是内部单独入口，不增加普通用户 force/restore RPC。

现 `pending_page` 直接序列化私有 `Record`，此处必须改为明确 **PendingRecordV1 投影**，保持 `records` 内原 `lease_id/owner/baseline` 形状，不能把新增内部字段自动泄漏进现有 v1 记录契约。

可选最小诊断方案（不增加 RPC/CLI 子命令）：磁盘是 v2 时，在既有 `PhysicalPending` 响应增加顶层 `storage_schema_version: 2` 与 `recovery_authorizations`，后者只含**当前页**每个 lease 的授权枚举；v1 默认响应保持字节结构语义不变。

```json
{"schema_version":1,"records":["旧形状记录"],"next_after":null,
 "storage_schema_version":2,
 "recovery_authorizations":[{"lease_id":"…","authorization":"manual_only"}]}
```

这里授权摘要只陈述持久授权，不声称当前可恢复/恢复已运行。现 CLI 的 `Response.data` 是 `serde_json::Value`，可显示这些额外顶层字段；不需要复杂状态框架。独立消费者若自行严格拒绝额外顶层字段，其兼容性不能凭现 CLI 推断；文档将其定义为 opt-in 功能的 additive diagnostics。若维护者承诺任何未知旧消费者顶层也必须完全相同，则省略新增摘要，或另设只读版本化请求；不改变现有 records。

分页预算必须计入 record 投影、对应 recovery 摘要、游标、Response 包装和换行的**合计**，仍限64 KiB。不能先按旧record算满一页再追加所有256条权限摘要。单条超页拒绝，不输出截断 JSON；空页/next_after 规则不变。

Status 中原 `physical_journal_schema_version=1` 若保留，应明确为 legacy lifecycle IPC version；如果要显示实际磁盘版本，使用另外的 `physical_journal_storage_schema_version`，不能把值2塞入旧协议字段暗示客户端必须升级。原 status schema1、Native 与 doctor 解析保持。

## 6. 最小测试集

权限：旧 policy 缺新字段默认空；精确ID、重复/超限/非subset/Native冲突拒绝；新 grant 仅显式 ID 得到 ConnectOriginalDrivers；owner UID、trusted network、physical ID 或恢复 ID 任一撤销，执行 gate 不允许。新策略包含旧记录 ID 仍不能升级 ManualOnly。覆盖 helper 重启后加载撤销策略；文档明确磁盘编辑在重启前不生效。

持久化：原 v1 fixture 严格加载且原缺driver测试保留；v1伪造recovery字段拒绝；v2缺/null/未知授权、未知/重复字段、未知版本、缺records、重复lease/device、257 records/interfaces与16 MiB上限拒绝。测试从v1新增manual仍v1、从v1首opt-in混合迁移保持旧元数据及manual、重载v2、全部退休后仍v2、旧helper拒绝v2；失败后不交FD、不发布新缓存、Store poison、重启按实际持久文件处理。

IPC：用旧形状请求/GrantDescription fixture 验证 durable protocol1 仍可用，不允许客户送recovery/owner/baseline；默认 Pending 与旧字段完全相同；v2摘要与当页记录一一对应；接近64KiB/最大driver名/256接口时总响应仍有界。旧 Finish/Reconcile 不调用写设备接口。

恢复 gate：用注入的观察/操作接口验证同配置/完整IF/alt/代际、当前driver三分支与policy交集；ManualOnly、旧boot/新代际、活动原owner/活动handoff、外部usbfs/新driver、配置/alt不符时零CONNECT；成功路径每次写后重观测，完整基线前不退休。只把零写拒绝测试当拒绝覆盖，不当自动恢复成功。

QEMU 至少新增**产品** opt-in kill → helper自动CONNECT → 外部完整基线 → 旧record退休 → 新grant真实HID/FTDI I/O；同时保留默认旧journal负向与旧软件回归归属。添加一项旧v1记录+新opt-in策略的实际拒绝/保留对照，以及重启加载v2但撤销恢复许可的保留对照。普通/合作关闭仍应通过。跨配置/alt、热拔新代际、外部占用等覆盖按执行器风险安排，不用把本报告扩成新的全量机制harness。

## 7. 必要修改范围与源码依据

| 文件/接点（当前行号） | 必要修改 |
| --- | --- |
| `crates/rustusb-daemon/src/lib.rs:223` Policy；`:285` physical authorization | 新默认空allowlist、subset/上限验证、记录许可/当前执行许可函数；OpenPhysical protocol1不变 |
| `crates/rustusb-daemon/src/journal.rs:77` Record；`:476` load；`:571` commit；`:593`序列化 | recovery枚举、严格V1/V2 DTO、bounded分派、sticky磁盘版本、有界snapshot及原子迁移 |
| `crates/rustusb-daemon/src/linux.rs:125` open；`:463` pending_page | 新grant由helper策略选许可并在handoff前持久；pending投影与总页预算；恢复扫描读取bounded snapshot |
| `crates/rustusb-daemon/src/linux.rs:175` Finish/Reconcile；`:579` read_policy；`:852`启动 | Finish保持只读/原owner；重启策略gate；保持原secure policy reader及restart-required，不加热重载 |
| `crates/rustusb-daemon/src/physical.rs:48` GrantDescription | 保持wire字段，新增兼容性测试，不必修改结构 |
| `crates/rustusb-daemon/src/physical_lifecycle.rs:70,150,219` | 恢复器复用身份/完整baseline观察；与只读verify分离。不要把要求当前eligible的通用导出路径误当完整恢复状态分类器 |
| 文档/示例policy/QEMU新增产品验收 | 明示管理员基线恢复授权、旧记录不可补授、重启生效及已开始恢复退出边界、私有schema2降级限制 |

首切片不需要改 USB/IP wire、core receipt、backend已有多配置执行、vendor USB实现或新增依赖，仅为 journal权限/兼容问题更不应重写这些模块。执行器若需safe CONNECT封装，另按现有特权/unsafe边界决定接点，不能由本文存储建议暗中引入。

## 8. 并行实施固定接点（与 root 最新分工对齐）

为避免引入大型 schema 框架，journal 文件内只新增必需 DTO、一个授权枚举、一个小 Snapshot；Store 内直接保留经验证的 `schema_version: u32`，无需通用迁移注册器、状态事件框架或第二权限文件。

- **journal agent：仅 `journal.rs`。** `RecoveryAuthorization`（`ManualOnly` / `ConnectOriginalDrivers`，snake_case），`Record.recovery` 为必填枚举；`Snapshot { schema_version: u32, records: Vec<Record> }`；`Store::snapshot()` 返回同一次检查下的版本与有界记录副本；原 `reserve/get/records/has_device/retire` 签名和作用保留。`records()` 可包装 snapshot，不要求其他调用方立即迁移。`Store` 根据 sticky schema 与 next records 自动选择写入格式，不让调用者提供磁盘版本。需要显示单独版本时可加 `schema_version(&self) -> Result<u32>`，仍检查 healthy，但不复制记录表。
- **root：Policy/RPC/linux。** grant 创建时在 `Record` 初始化中明确填 recovery；所有其他非新grant测试构造填 ManualOnly。Pending 使用本地借用 `PendingRecordV1<'a> { lease_id: &'a str, owner: &'a Owner, baseline: &'a Baseline }` 投影，按 snapshot.schema_version 决定摘要；不要求 journal 暴露其私有磁盘DTO。后台恢复只筛选持久 `ConnectOriginalDrivers` 再交给当前有效 Policy gate。
- **native agent：恢复 module/lifecycle/vendor 薄 CONNECT 入口。** 只消费稳定 `Record`，不自行补授权或写 Store 字段。完整恢复和关闭所有已知后台工作后，由串行器调用 Store.retire；失败保持原记录及 recovery。

这个 API 不要求客户端请求“更高权限版本”，不改变现有 FD receipt、GrantDescription 或 Finish wire。产品 opt-in 的唯一根源是管理员策略创建的新持久授权。
