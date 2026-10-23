# 产品自动恢复负向：只读预审

状态：2026-10-23，源基线 744d9bee6dff1e1ca624a9afc3c0b9803aa77d53；仅预审最小断言。尚未看到本轮正式负向设计/新 harness，没有 Cargo/QEMU/设备操作，没有仓库写入。当前读取源 SHA 在 rustusb-autorestore-negative-review-inputs-20261023.json。

## 共用前置与结果界限

- 真实服务从完整原 HID/FTDI driver、配置、完整接口/alt、node/sysfs receipt 获得两 grant；原 OS owner 的 UID/PID/start/boot 与原记录一致。先完成真实双向 FTDI/键盘 I/O 再造故障，不能仅 ready/snapshot 冒充负载。
- 服务一次 KILL 的发送者、原 PID/start、真实唯一 wait137 和 /proc 已不存在均留原始证据；没有 killed stopped/resource-zero 终态。
- helper 停止必须真实 wait0、socket 消失、原实例确实退出后才能改策略/切旧新二进制/让 C 进入。记录 PID/start 与 state path；磁盘目录锁和原记录不能被重建。新实例 ready/status、存活身份及完整日志保留。
- 每次观察保留原始 journal bytes+SHA、完整 sysfs/config/IF/alt/generation、helper identity 和 owner absent。纯观察窗先于会改变实验顺序的新授权尝试；检查不改 journal。末尾尝试新 grant 应因原未决记录拒绝，原 lease/owner/baseline/recovery 不变。
- 有界观察窗证明本次受测运行的行为，不宣称永不恢复或 syscall 级无写。采样数不是 USB case；三个拒绝场景不是第三轮恢复成功 I/O。若在末尾做人工恢复，只能独立标记为收尾，不能混入负向产品判定。

## L：真实旧 producer 的 schema 1 不追授

- 必须实际由 933e720531fd96b7a23616ed687d59b816e0ce09 helper 生成 pending；保存旧 Git/Cargo/构建来源和实际 payload/exe 哈希链。当前 helper 默认关闭所写的 schema 1 不是旧 producer 迁移证据。
- 旧 Policy deny_unknown_fields；旧 helper 的生产阶段配置不能含 physical_connect_restore_device_ids。若当前 CLI 连接旧 helper，明确为旧 helper producer/新 CLI consumer；当前 CLI 校验 durable protocol 1，并不要求新恢复字段。
- 原 schema 1 两记录无 recovery；停止旧 helper 后在同 state 目录启动当前 helper，当前策略明确 opt-in 两个 ID，其他授权相同，原 owner 已 reaped、同 boot、同代际、driver 全 null。
- 新 helper 应正常加载，不发生自动 attempt/failed/retired，完整观察窗两原记录和原 disk1 bytes 不变、原 driver 未恢复。pending 保留 IPC1 旧 record 形状；disk1 不应强求 storage_schema_version/recovery_authorizations 或显式 manual 字段。内存 ManualOnly 来自 journal.rs:131-142，绝不从策略推断。
- 这证明读取兼容和不追授；若没有随后新 opt-in grant 写盘，不声称已运行验证 mixed manual+opt-in 的 disk2 迁移。

## P：撤销恢复 allowlist 后新 helper 不恢复

- pending 必须先由当前 helper 在两 ID 的 opt-in 策略下持久生成 disk2、显式 connect_original_drivers；不能手工补 recovery。
- 最小无竞争顺序：owner 保持活着 → 停止并 wait0 原 helper → 仅把恢复 ID 列表改为空（UID/trusted/physical IDs 不变）→ 启动并确认新 helper → KILL/reap owner。也可以在 helper 停机期间先 reap，但绝不能让旧 opt-in helper 在 owner ended 时继续运行。
- 新 helper 的实际配置与 hash、PID/start、状态明确记录；编辑磁盘而不重启不等于撤销。
- 预期没有 physical_auto_restore 事件、driver null、disk2 原 bytes 与两 connect_original_drivers 原记录保持、pending authorization 摘要仍显示持久原授权。持久 opt-in 与当前策略不许可必须分别记录。
- 撤销列表为空时 linux.rs:1123 不启动 scheduler；此路径验证 restart 生效的禁用，不是逐 record failed 事件，也不证明热重载或已交付 FD 撤销。

## C：独立外部占用与单次失败后不重试

- opt-in 原记录、同 boot/完整原基线、原 owner KILL+reap；helper 必须停机。C 在这之后以两个独立 open 获得两个设备节点，每设备 IF0 仅做 plain CLAIMINTERFACE，严格 rc==0/errno 记录；不能用 DISCONNECT_CLAIM、detach、reset、配置/alt写或 CONNECT。
- C 的 PID/start/root身份、两节点 receipt/FD 观察及两个完整 usbfs claim 快照成立后才启动新 helper。无存活 A/dupA 被继承；新的 C 不是同 OFD 所有权重命名。这个负向不要求 kcmp 比较已不存在的 A。
- 每个旧 lease 应精确一次 attempt→failed、无 retired，完整原记录保持、C 身份/claims 未被抢占。实际首拒绝点通常是 physical_lifecycle.rs:observe_current_device 调用 physical.rs:56-68 ensure_eligible，产生 UnsafeDevice，早于 physical_recovery::scope；不要硬编码 scope 的 State 文案或要求 CONNECT/EBUSY。
- unsafe_device 是 Response::failure 另一日志行的 error_category；带 lease 的 failed.reason 为 Display 上下文。None errno 可能省略，不能要求字面 null。未到 CONNECT 属源约束，日志/外部状态属于观测，不互相冒充。
- 等两 lease 的 failed 都已观察后才 release；严格 RELEASEINTERFACE rc0，关闭两个 FD，真实唯一 wait0/进程 gone，sysfs 两 IF 变 null，receipt/config/alt 不变。继续同 helper PID/start 观察至少约5秒，原 records 不变且没有新 attempt/failed/retired；先失败再释放以及没重启是证明 attempted 去重的必要条件。
- 若实际 release 触发其他驱动行为，保存真实状态并报告 fixture 前置不满足，不伪造 null。该场景不要求也不证明 CONNECT 的内核 EBUSY。

## 当前结论

上述范围能在不扩大产品功能的前提下补三个产品负向缺口。必须修正的验收措辞只有 C 的拒绝位置：当前源码是资格/完整现场检查拒绝，不能断言一定运行到 scope 或 CONNECT。正式设计和新测试源码到达后再核对实现；此文不是 QEMU PASS。
