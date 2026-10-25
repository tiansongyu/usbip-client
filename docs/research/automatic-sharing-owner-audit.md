# 自动共享规则：owner/准入/观察接口研究

状态：源码审查与候选设计，非 accepted、非运行证据。2026-10-25；仓库读取版本 `c4f72f6e02120487077733a4c6eb046094db3248`。仅只读源码；未 build、未运行 USB/测试、未改项目文件。复用已锁定既有栈，不引新依赖或逐组件 skill。

## 已核事实

- 真正执行入口是 `Runtime::start(uid, profile, RequestId, retry)`（`crates/rustusb-service/src/runtime.rs:870`）：先核 Start 权限，旧 UUID 必须同 UID/profile；Retry 未找到只返回 RequestExpired。非 Finished 同 profile job 拒绝新准入（904）。job、request、Start audit 在同一 `persist` 后才注册 stop 并 spawn 实际 runner（925–965）。去重窗口仅256条（`store.rs:10,198`），不是永久 exactly-once。
- `JobContext::begin_session` / `SessionContext::attempt` 分别先持久 session/attempt 才允许取得资源（`context.rs:85,121`）。`Runtime::run` 持三个 JoinSet 并真正 drain（`runtime.rs:434`）；`Controls` 的 sticky shutdown 覆盖晚注册 job/session（102–124）。`finish`/`job_finished` 分开 operation 与 cleanup；Unconfirmed 令 job Recovering（1312、1394）。重启只依精确 kind/correlation/key/UID/resource 的 root terminal 协调（1482），不能以新 epoch 或资源0解锁。
- 一份 `DeviceMonitor` 可以供多规则订阅，进程最多2个实际 worker（`crates/rustusb-discovery/src/monitor.rs:220`）。只在 Current 完整扫描匹配；Stale 保旧、跳号要求 resync（129–190）。`ObservationStamp` 是 inode/address 观察戳，不认证硬件（`crates/rustusb-linux/src/observation.rs:8`）。fingerprint、stamp、Bus ID 不可代替 helper 重新授权/代际检查。
- 远程网络应复用监督器的两槽 Discover 路径（`runtime.rs:525–598`），成功 audit commit 后才交候选。不要 agent 自连 `CatalogSource::agent`：真实 SO_PEERCRED 会是 broker UID（`ipc.rs:218`），不是规则绑定的 operator UID。`CatalogSource::secure` 直接使用也不会自动产生该 UID 的 Discover audit。目录缺项可能只是设备忙，不能当物理拔出；`CatalogMonitor` 自身也仅2 actual owner（`catalog.rs:320`）。

## 实现候选与不变量

建议 agent-owned `RuleSupervisor` + Runtime 内有限 rule checkpoint；GUI 仅管理/观察受管理员许可的规则，关闭窗口不结束规则。共享一个 local monitor；远程 poll 使用原 discoveries 总2槽，完成后轮询/有限退避，人工请求保留公平配额，网络与扫描不在 WAL await/锁内。只保存有限最新 metadata、一个候选/规则，不逐事件堆队列。

候选生命周期：`Disabled → Waiting → Candidate → Admitting → Active → Waiting/Cooldown`；`PausedByUser`、`BlockedPermission`、`BlockedUnsafe`、`NeedsReconciliation` 单独可见。Local token 可用同 boot/key/stamp；远程 token 名为 AdvertisedEpoch，**不称物理 generation**。同观察代际只消费一次；Active 时不因自己导出后 blockers、自己占用使目录隐藏而再触发或停止。

必须将 rule ID/revision、root 固定 actor UID、trigger token、原 request/job ID 与 Start intent 在**同一个状态事务**关联；保留原私有 schema 严格迁移/容量预留，不以另写无关联文件替代。未知应答只查询原关联/RetryStart，过期转人工；crash intent 无 session 的原安全判断可复用，但有 attempt/Unconfirmed 继续阻断。规则删除/修改保 tombstone，不能丢旧关联。手动 Stop 必须同时持久 suppress 当前触发或 Pause，防止 job Finished 后条件仍真而立刻重启。

```rust,ignore
// 候选接口；尚不存在。
async fn admit_rule_start(&mut self, id: RuleId, trigger: TriggerToken)
    -> Result<RuleAttempt>; // 原 start 权限、事务与 owner 路径内执行
fn request_rule_pause(&self, id: &RuleId); // sticky；记录确认与资源 release 分开
```

仅在原 owner 结束、job Finished、全部旧 session 为 Verified/NotStarted 后，才能按明确策略为非存储设备生成 fresh attempt。Permission/配置/存储/不明确 I/O 结果不无限重试；有限 backoff 不能解除 Unconfirmed。停止服务先封新候选，再 stop/join 原 monitor、Discover、USB/recovery owner；不得 abort 或超时假 join。永久 DNS/FS/kernel 阻塞沿原边界说明。

## Backend 的实质限制

Native `run` 启动时遍历**完整固定** `config.devices`；任一准备失败会 close_all（`jobs/native.rs:689–744`），没有动态加设备 API。USBFS 同样预建固定 exports/grants（`jobs/rust_server.rs:598,698`），Virtual 则是固定虚拟 profile（1041）。一个设备命中不能声称只启动该设备。Whole-profile 条件规则可以作为明确的阶段增量；若要逐设备热插、同 listener 添加新设备且不打断 sibling，必须扩展动态 pool/owner 入口并验证，不能把一次 auto-start 标成该完整能力已交付。Client 一个 profile 固定一个远程 key，实际 runner 总会重新 Discover/IMPORT（`jobs/client.rs:82–121`）；rule metadata 不覆盖这次复核。存储（含 Composite、未知安全性）继续 fail closed，不自动重连/挂载/写恢复。

## 必须取得的验证证据

1. QEMU 中后台规则在 GUI 退出后对真实 add/reinsert 仅产生一次持久 Start，客户端实际枚举/I/O；重复 hint/重扫、同 Bus ID 替换与未授权 key 不重复/不越权。
2. 不同时间插两设备；如实现动态 pool，第二次加入时原 sibling 能力/I/O 保持。分别记录 Native/USBFS/Virtual 支持范围。
3. 同一数据连接 fault 后原精确释放/root proof，再 fresh attempt；目录 busy/stale 不冒充物理拔出；storage 全界面拒绝。
4. 手动 Pause/Stop、WAL/prepare gate、重启/未知 Start/去重窗口过期、规则变更/删除、容量耗尽与关停，均核原 UID/UUID/owner/ELF及实际 join；保留原负向。

置信度：上述源码/API/限制高；状态机和接口是待审候选。动态 pool、规则审计/状态 schema、具体公平/退避参数及实际 QEMU 结果均尚未实现/验证。
