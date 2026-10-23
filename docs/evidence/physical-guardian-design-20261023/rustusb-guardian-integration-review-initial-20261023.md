# USBFS guardian 跨进程恢复：独立集成设计审查

状态：**draft / 设计建议，非产品实现或运行验收**。2026-10-23。开始时 clean `60be074086a164fdd852119a80c26b6949e7646e`；审查期间 root 仅追加机制实验设计。本 agent 不修改仓库、不 build、不运行 QEMU。已读核心开发 skill、技术设计 skill、架构、异常恢复设计与相关 helper/backend/vendor 源码；按用户限定只写本报告，不生成额外组件 skill。

## 结论与当前切片

在当前“获准服务是可信整设备 FD 使用者”的模型下，**不必先撤回整 FD 接口或把所有 USB 请求搬进 helper 才能实现有意义的受限自动恢复**。可由 helper 保留独立 open 的恢复句柄 B，普通服务持有 A，辅以执行前持久许可、单步结果确认、进程代际观察及保守的条件恢复。B 不能替 A 取消/释放请求；它只能在既有 A 使用者停止、实际接口可用、持久变更范围可解释时操作。

这不是“PID 死亡 → CONNECT → 删 journal”。当前 schema 1 只有 owner 和原基线，没有可信的实际变更范围，**不能直接启用自动恢复**。完整自动恢复需下文 Prepare/Complete 契约；当前 root 已选择的独立 B / A 副本 / C 争用机制实验是合理的前置切片，其成功也不能替代生产恢复实现。

恢复成功必须同时具备：同一旧代际完整原 config/interface/alt/driver 恢复、已知执行能力结束、记录被独立验证后退休，以及后续新授权的真实设备 I/O。仅拒绝启动、CONNECT 返回 0、PID 消失或新进程 snapshot 干净均不足够。

## 已核对的源码事实

| 接点 | 当前事实 | 集成影响 |
| --- | --- | --- |
| `daemon/linux.rs:125–172` | `open_physical` 在 FD 交付前 `reserve`，但当前只打开 A | B 必须是真正第二次 open，不能保留 A 的 dup 作为恢复 FD |
| `daemon/linux.rs:85–103` | Handoff 先 drop grant FD，后移除 tracking | 此顺序必须保留；B 单独追踪，不能借此延长 A 生命周期 |
| `daemon/journal.rs:77–86` | Record 只有 lease/owner/baseline；Store 有 reserve/get/retire | 增加严格版本化、单 pending 变更和 checkpoint；不能从 schema 1 猜出已修改范围 |
| `daemon/physical_lifecycle.rs:69–92, 209–225` | 现有 ended-owner 与 verify 只支持管理员协调/只读退休 | PID/starttime 判断不能独自授权 USB 写操作；自动恢复需额外状态机 |
| `daemon/physical.rs:119–165` | `open_current` 要求当前设备 eligible，扫描当前状态生成 receipt | 重启恢复不能调用它来建立“新 original”；需要只按旧 receipt 验证的私有恢复 reopen |
| `backend/physical.rs:33–41, 124–134` | PhysicalSource/Slot 没有 lease 或持久协调对象 | 需注入有限生命周期控制接口，不能只在 CLI 外圈记录一次 IMPORT |
| `backend/physical/worker.rs:123–139` | `owned_original`、`configuration_changed`、当前 config/alt 只在内存 | 进程死亡会丢失“哪个接口由我动过、最后配置是什么”的证据 |
| `worker.rs:213–288` | acquire 逐接口 detach-and-claim，随后可能设 alt | 必须在第一次可能 detach 前持久 Prepare；不能仅在成功 claim 后记录 |
| `worker.rs:796–881` | config 先 release，再 setconfig、观察、重新 acquire；alt 也是独立阻塞动作 | 每个可能改变 host state 的子步骤必须被已落盘范围覆盖 |
| `worker.rs:1039–1163` | 正常 restore 依赖上述内存范围并检查 generation/driver | 可复用规则，不能直接在新进程伪造 Worker 并调用恢复 |
| `observe.rs:152–181` | CONNECT 后按接口重观察；同一 driver probe 可绑定 sibling | guardian 必须逐次重读完整接口状态，不能复用旧快照 |
| `vendor/.../device.rs:119–195` | `from_fd` 读 descriptors、猜 active config、注册设备事件 | B 休眠时保留裸 OwnedFd；不要初始化成可传输的 nusb Device 然后称它纯观察句柄 |
| `vendor/.../usbfs.rs:51–67` | 当前 detach-and-claim 明确排除名为 usbfs 的驱动，但可解绑其他驱动 | B 恢复路径禁止调用此 API；普通 CLAIM 才提供不解绑现有 owner 的拒绝语义 |

固定 Linux v6.8（commit `e8f897f4afef0031fe618a8e94127a0934896aba`）的相关结论：

- `usbdev_open` 每次创建独立 USBFS 状态；`usbdev_release` 释放该 open 的接口并销毁异步请求。独立 B 不拥有 A 的 claim/URB。[devio.c:1032、1089](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L1032)
- SET_CONFIGURATION 在任何接口仍被 driver/usbfs 占用时拒绝；CONNECT 对已有 driver 返回 EBUSY，在空闲时交由 driver core 探测匹配驱动，不能指定“原 driver”。[devio.c:1543、2316](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L1543)
- **释放接口可能重置非零 alternate 为 0**：USB interface unbind 路径如此处理，且 RELEASEINTERFACE/最后 close 可进入该路径。[driver.c:431–517、612](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/driver.c#L431)
- SCM_RIGHTS 传的是同一 open file description 的引用；pidfd 可稳定观察进程，并不证明不存在转交给其他进程的 FD 副本。[unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)、[pidfd_open(2)](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)

以上是固定上游行为，不等同于 Ubuntu guest `6.8.0-106-generic` 运行结果。未复制 GPL 代码进产品。B 的 open 本身仍会参与内核设备打开/电源管理；“休眠”只表示不 claim、不提交 USB 传输、不改配置/驱动。

## 信任模型：何时需要收回整 FD

保留 A 的方案有明确前提：受信任服务不把 A 交给未知进程、不让未记录的执行路径改 host state，所有自有后台任务遵守 prepare/complete 及关闭协议。这个前提延续现有 trusted-service 模型，并不宣称对恶意获准客户端实施细粒度沙箱。helper 的 policy 可另设明确 guardian 自动恢复开关；默认拒绝自动升级历史记录。管理员禁止恢复时，记录保留，不能改用“之前允许过”绕过当前管理决定。

若目标变成“任意用户进程取得 A 后仍可强制撤销/回收”“允许任意 fork/SCM_RIGHTS 转交但仍保证恢复”“不能信任客户端提交完整变更记录”，则必须改变数据平面：不给一般前端 A，把 USB 执行放入固定的受监督 worker，通过有界消息调用；所有后代/FD 传递都纳入约束。仅增加一个父进程仍不够，父进程也会死亡，子进程仍可泄漏 FD，helper 仍需 journal 和恢复权限。

受监督子进程对隔离硬阻塞 ioctl 很有价值，但不是当前受信任模型完成固定范围恢复的必需前提。本轮不应为这项架构升级把整个网络协议解析搬入 root helper。若 helper 的恢复 ioctl 进入不可取消的内核窗口，超时只能标隔离并保留任务/FD/记录，不能将 `spawn_blocking` 的 future 丢弃算作完成。需要“helper 在硬阻塞时仍可独立升级/退出”的强可运维承诺时，应再增加固定恢复子进程，并持久记录其 owner；仍须 kill/reap 后才允许下一执行者。

## 持久化具体范围，而非“我碰过这个设备”

建议 journal v2 在 immutable baseline 外保存一个有界当前 checkpoint 和最多一个 pending 变更，避免无界流水账。类型示意不是已编译 API：

```rust,ignore
struct RecoveryContract {
    version: u32,                 // GuardianV1，不是 journal schema 的替身
    descriptor_digest: [u8; 32], // 全配置/接口/alt 描述符摘要；无 payload/serial
    mode: RecoveryMode,          // ManualOnly 或 GuardedAutomatic
}
struct Checkpoint {
    epoch: u64,                  // 同 grant 内多次 IMPORT 分开
    sequence: u64,
    observed: HostState,         // 配置 + 完整 IF/alt/driver 集合
    touched_original: InterfaceSet,
    last_confirmed_configuration: u8,
}
enum Mutation {
    Claim { configuration: u8, interface: u8, expected_driver: Option<DriverName> },
    Release { configuration: u8, interface: u8 },
    SelectConfiguration { from: u8, to: u8 },
    SelectAlternate { configuration: u8, interface: u8, from: u8, to: u8 },
    ReconnectOriginal { interface: u8 },
}
struct PendingMutation {
    epoch: u64,
    sequence: u64,
    operation: Mutation,
    independently_observed_before: HostState,
    permitted_effects: BoundedEffectSet,
}
enum RecoveryPhase {
    Reserved, Armed, Steady, MutationPrepared,
    Closing, OwnerEnded, Recovering, Verified,
    NeedsAdministrator, GoneObserved,
}
```

`DriverName` 是受限 basename，Mutation 不接收 sysfs 路径、任意 ioctl 编号或 shell 命令。driver 的 None 必须显式存在。configuration/interface/alternate 需要符合同 receipt 的描述符和当前 safety scanner；claim 范围不是整个设备永远可随意修改的授权。

Prepare/Complete 契约：

1. helper 核对 lease、OS owner、epoch、连续 seq、无其他 pending、当前 policy 与旧 receipt，独立观察 before；把 MutationPrepared 原子写入并 fsync 后才返回 permit。
2. A 收到 permit 后只执行该已定义步骤。应用取消、RPC EOF、ACK 超时不能让未获 permit 的操作继续；已获得 permit 但结果不明的步骤保留 pending。
3. A 报告 syscall 结果；helper 独立观察 after，验证它属于允许效果，并持久 Complete/checkpoint 后返回确认。A 不能只凭自报 syscall 0 建立恢复范围。
4. 下一 host-state 操作不能跨越未确认 Complete。相同 seq 的重试返回既有事务状态，不能重放 ioctl；乱序、不同参数同 seq、溢出均拒绝。
5. 初始整设备 claim、配置切换后的重新 claim、正常 restore 都必须覆盖。`configuration_changed=true` 一个布尔值或“IMPORT 开始”一次标记不够。
6. USB payload 不进 journal。普通 IN/OUT、HID 内容、串口数据不落盘；恢复不回滚设备内部状态、已发送 OUT 副作用或任意 vendor 命令效果。host-state 以外导致重枚举的变化由 receipt 检测并拒绝旧恢复。

可按“复合操作”的有限步骤编码以减少 IPC，但在每个可能崩溃的位置必须知道哪些效果已承诺、哪些仍未知。第一正式切片建议**未知 SET_CONFIGURATION/SET_INTERFACE 结果不自动修复**；先恢复已确认 checkpoint 的崩溃。这并不禁用正常多配置/alt 功能，也不是用只拒绝代替恢复：已确认活动租约的 KILL 必须真正恢复并重新 I/O。

为什么不能只在操作后记账：detach 成功但 claim/结果确认失败时，已经改变原驱动；配置切换也可能改变完整接口集合。当前 `owned_original` 仅从已取得 interface 扩展，不能作为崩溃前完整修改范围。write-ahead 记录覆盖不确定性，Complete 才提供自动恢复的可靠起点。

## A / B 生命周期与 helper 重启

| 事件 | A 的约束 | B / journal 的约束 |
| --- | --- | --- |
| 授权 | 在任何可变更操作前有 durable baseline 和 recovery contract | helper 对同 generation 独立 open A、B；两次 fstat/receipt 复核；先 reserve 再交付 A |
| 交付不确定 | 收到 A 也不能绕过 Arm/Prepare；取消不等于交付失败 | pending 保留；helper 发送端 A 引用必须及时关闭；B 单独保存 |
| A 正常使用 | 一台设备一个变更序列；多个请求只在现有 URB 规则内并发 | B 不 claim、不探测 endpoint、不 CONNECT；只做 sysfs/owner 观察 |
| 正常结束 | 先停 listener/session、终止并 join 已知任务、所有 ioctl 回来、释放 A，再 Finish | Finish 与 recovery 同一 per-lease gate；完整 baseline 复核；关闭 B 后退休；禁止它与自动恢复同时写 |
| A SIGKILL | 不制造 stopped、末资源 0 或用户态完成证明 | pidfd/boot/starttime 只是启动协调的条件；还需 handoff 完毕、checkpoint 可解释、旧 claim 不再阻塞、完整 generation/safety 检查 |
| helper 合作退出 | 可通过长连接 Quiesce 请求 A 停止；无法完成时留下不确定状态 | 停新授权，收束已接受操作；不能 abort 已运行恢复；无法安全完成则保留 record，不宣称恢复 |
| helper KILL/重启，A 仍存活 | supervision EOF 后停止新 host-state 操作并进入受控关闭；无法与新 helper 协调时最终退出并保留记录 | 新 helper 持有唯一 Store 锁后加载旧记录；owner 仍活着时绝不启动 B 的恢复，也不把旧数据当成新 baseline |
| helper 重启，A 已结束 | 已知 A 全部使用者遵守同一信任契约 | 只按持久记录重新 open B，验证同 boot/receipt；未知 pending 操作或旧 schema 1 进入人工路径 |
| helper 在恢复中死亡 | 不能认为其恢复 ioctl 已完成 | v2 必须记录 recovery executor owner 与 pending step；旧 executor 未终止/锁未释放则禁止新恢复；未知步骤保留，不能盲目重放 |
| 热拔、同端口替换、OS 重启 | 旧 lease 不自动继承给新设备 | 旧 record 标 GoneObserved；不向新代际写 old config/alt/driver，跨 boot 不自动套用旧 receipt |

建议每个物理服务有一条认证长连接，用于 guardian epoch/许可/EOF 通知，仍以现有 root socket 的身份检查为基础；不能只在下一次操作时才知道 helper 已死。一个连接管理其最多16个 lease 即可，不做每个 URB 的 RPC。即使 EOF 丢失，新的 helper 也必须因旧 owner 活着而拒绝恢复，避免 A/B 同写。

关闭/释放是特殊终止行为：服务在 helper 丢失时仍必须取消和回收自己的 URB，并关闭自己已知 FD，不为“等日志 ACK”永久持有设备。不得在失联后启动未授权的配置恢复/驱动 detach；最终由持久 checkpoint 决定新 helper 能否恢复。正常恢复流程的每个主动 host-state 写仍走明确事务，不能借 cleanup 名义绕过。

`open_receipt_for_recovery` 必须私有：由 journal 指定 bus/address、sysfs/node inode/device、boot 和摘要，检查前后相同；不能接受客户端设备路径。`open_current` 当前会拒绝 usbfs 占用且以当前 scan 为新授权，不能直接拿来替代这条路径。B 不可跨重启持久化；持久的是恢复授权与判断材料。

## 条件恢复算法

1. 取得有界 per-device 恢复 gate，持久进入 Recovering，记录本次执行者代际。独立确认原 owner 已结束；若其仍有线程/阻塞调用、身份观察失败、handoff 仍在进行，停止。辅助进程的 FD 副本仍存活不因主 PID 死亡变成自有能力结束。
2. 核同 boot、sysfs/node receipt、设备描述符/配置安全、完整接口集合与已完成 checkpoint。schema 1、corrupt journal、未知 Prepare、未知配置或当前权限禁止，均保持 NeedsAdministrator。
3. 检查当前 driver。任何 usbfs/usbip-host 或非预期 driver 都不执行 DISCONNECT/UNBIND。普通 `CLAIMINTERFACE` 的 EBUSY 是保护结果，不是授权去抢占的理由；只有 B 实际成功 claim 的接口可由 B release。
4. 若 current config 等于 original，只在已持久证明触碰过的接口上恢复。已匹配 original driver/alt 的接口跳过；原 driver 存在而 alt 不符时，不能把它解绑来纠正，应隔离。
5. 若 current config 不同，只接受持久确认的最后自有 config，且当前所有接口都未绑定、没有未知 busy/safety 状态，再由 B 执行原 config 的 SET_CONFIGURATION。**有任何已绑定 driver 时不 detach 以腾空配置**。因此 A 在切换后、重新 claim 前死亡并留下内核自动 probe 的 driver 时，可以明确需要人工处理。
6. 配置设置后重新扫描全接口；不要沿用切换前的数量和类型。安全检查失败或自动 probe 出非原 driver 即停止；不能因为“这是刚才 setconfig 造成的”就强行接管正在用的 driver。
7. 对需要纠正的 alternate，只能 plain claim 空闲接口，set alt，显式 checked release，随后再次读取真实 alt；0 也是基线值，不能只处理 original 非零。**claim→set非零→release 可能又重置为0**；不能提前把 desired 值写进“恢复完成”。
8. 对原 driver 非空且当前为空的受管接口，用 CONNECT 请求内核 probe；每一次后重读全 IF，因为 sibling 可能同时绑定。CONNECT 不选定原 driver，返回0仍需原 driver/config/全部 alt 比较。若 probe 得到别的 driver，保留记录，禁止自动解绑该新 driver 进行“重试”。
9. 最终独立完整基线检查两次，确认 B 自己的 claim 已 checked release、所有已知恢复任务结束并关闭 B，才记录 Verified 并退休。中途 write/fsync/观察失败不能报恢复成功。
10. 新 grant 仍走当前 policy 和全新 receipt；自动恢复不是自动重新连接客户端，更不是自动恢复应用写入。成功报告不虚构旧 A 的用户态 URB 回收计数。

对 ECM，原 alt1 可能由 `cdc_ether` probe 重新建立，必须最终检查两个接口与 alt0/alt1。对原 driver=None 且 alt≠0 的基线，B 释放时可能破坏目标 alt；在没有专门机制及真实证据前，不承诺这类自动恢复。这个限制已经是现有未验收边界，应明确保留，不改写为通用 alternate 支持。

对不受管管理员同时写 sysfs 的竞态，没有用户态快照可建立全系统原子排他。这里保证的是本工具不会主动解绑观测到的外部 owner，且最终状态不符就保留记录；不承诺抵御恶意 root 的瞬时同名替换。即便把 A 放进子进程也不会自动消除这类系统级竞态。

## 建议代码接点与资源边界

- `rustusb-core`：增加有界的 mutation/observation/contract 公共数据类型，解析严格、driver None 必填；不加 USB 写调用。
- `daemon/journal.rs`：v2 严格 loader、schema1 ManualOnly 显式迁移、`prepare/complete/mark_recovery/verify/retire` 原子更新。保留现有 flock、目录链、fsync、损坏 poison 和上限。不要写 unbounded event list。
- `daemon/physical_lifecycle.rs`：拆出只读 generation/current-state/owner/pidfd 复核；现有 verify 完整基线判定可保留。
- `daemon/physical.rs`：复用严格 open 的 node 验证，增加仅由 journal 驱动的独立 recovery open；A/B 用不同类型封装以防误 dup/误传。
- `daemon/linux.rs`：App 持有 GuardianManager 的有界任务与 B，启动加载记录、监听 owner、处理 prepare/complete；所有 Finish/admin/auto 操作共享 per-lease gate，避免全局 state mutex 跨长 ioctl 锁住全部 Native 操作。
- `backend/physical.rs` 的 PhysicalSource/Slot：带 lease epoch 和 `Arc<dyn PhysicalLifecycleControl>`。trait 放在 backend/core 的无 daemon 依赖层；CLI 用 daemon 客户端实现桥接，避免反向依赖或把 root 路径传进 worker。
- `backend/physical/worker.rs`：acquire、begin_change、restore 的 mutation 点接入控制器。仅发起 prepare 的 future 取消可以取消排队；实际 blocking ioctl 一旦开始仍须等真实终态。
- `cli/physical_server.rs`：检查 guardian contract/schema；prepare/控制断开和信号不丢既有 cleanup；close/Finish 时序继续严守。未启用 guardian 的旧 helper 不伪造自动恢复支持。
- 复用 vendored nusb 的 checked API 或新增极小 Linux 恢复 facade，**不要复制 unsafe ioctl 到核心 crate**。当前 daemon 的 physical-usb 不依赖 nusb；若加入 optional dependency，明确 feature/默认构建影响和依赖清单，不能偷偷让默认 Native 引入实验后端。

建议继续沿用256条记录、16MiB journal、完整接口256上限、每服务16设备；每设备仅一个 pending mutation、一个 recovery executor。有界恢复并发（例如4，待负载验证），一次实际超时后进入隔离并保留唯一任务/permit，不能每次 retry 再 spawn。Prepare/Complete 只覆盖低频 host-state，不能对每个64KiB USB数据包 fsync。

## 最小有意义的验证分层

### 本轮机制实验（产品保持不变）

当前追加的六步实验有正确目标：B 在第二轮 IMPORT 前独立 open；使用 pidfd_getfd 获得 A 真 dup 并由 kcmp 证明同 OFD，B 与 A 不同；一次 SIGKILL 的真实 wait137；A 副本存活时 B plain CLAIM/CONNECT EBUSY，关闭副本后 B claim/release成功；C 外部占用仍阻止 B，C 不被移除；回到旧 journal 跨 helper 重启拒绝，再由 B CONNECT 恢复完整原状态、关闭 probe、root只读 reconcile、第三轮真实 HID/FTDI I/O。

这是“独立恢复句柄可做条件操作”的机制证据，仍是测试管理员协调，不是产品自动恢复。kcmp/权限失败必须实报失败；不得把 `/proc/PID/fd/N` 的重新 open 当 dup。每个 lease/receipt、kcmp return、一次 wait、真实 errno、B/C关闭、生成guest脚本唯一锚都要保存。旧 journal 的失败保护、权限拒绝、两次重启拒绝和第三轮数据断言须保持。

### 后续生产 guardian 的首组成功场景

1. HID+FTDI：普通服务真实活动 I/O 后 KILL，guardian 无管理员 bind/CONNECT 命令介入地恢复原 usbhid/ftdi_sio、完整config/alt，退出/退休后新 owner 完成另一轮 KEY/精确双向串口字节。
2. ECM：原 config1、两接口、alt0/1；完成且持久确认的 SET_INTERFACE 后 KILL，恢复后两个原 cdc_ether 和原 alt1，随后新实际 UDP 往返。
3. 配置恢复：完成且持久确认切到 config0 后 KILL；guardian 恢复原 config1/完整原接口与driver/alt，再次真实 I/O。不要以始终config1的测试宣称跨配置恢复。
4. helper 被杀/重启：A 活着时新 helper 不写 B；A 关闭/结束后按同一 durable checkpoint 自动恢复并重用。另测 guardian 正常完成记录退休，不造成永久拒绝。

每组均需要原 baseline、持久 Prepare/Complete、真实 owner 身份和退出、恢复 ioctl/errno、独立 sysfs、最终退休及新 epoch 实际传输；不能用 marker/ready 代替。QEMU host普通UID、私有网络、无宿主USB沿用现有规范。

### 必要负向与回归

- unknown config/alt Prepare：分别在持久前、持久ACK后但ioctl前、ioctl完成但Complete前中止，必须无越权恢复；记录不被抹掉。
- A dup 残留、C claim、外部 driver、同端口新 generation、boot变更：不 detach、不把旧基线写入替代设备。
- helper恢复步骤中被杀、journal缺失/损坏/fsync失败、旧schema1、owner身份/PID复用、ACK重试：不产生第二恢复执行者或新original。
- 正常关闭、无IMPORT关闭、SIGINT/TERM、stdout背压、原正常/热拔/ECM多配置能力保持。非零alt无driver、内核内部硬阻塞、更多真实partial错误、真实硬件G1/G2单独未完成。

## 交付判断

可以推进当前仅 QEMU 的独立 B 机制实验；不能因此直接把 schema1 改为自动恢复。下一生产切片应先实现并审查 durable mutation contract，再做至少一项真实自动恢复并重用的正向闭环，同时保留未知状态的拒绝路径。若受信任 A 仍符合契约，helper guardian 是可实施的渐进路线；是否转为完全受监督 USB worker 由更强的能力撤销/硬阻塞隔离目标决定，不能把它当作解决外部 root 竞态的万能条件。

## 实际检查的产品源码 SHA256

| 文件 | SHA256 |
| --- | --- |
| `crates/rustusb-daemon/src/journal.rs` | `52d4e5fa87863326bc32d6d7eba8b14d4ed27e8199dbf60b4f4a6989b2f21730` |
| `crates/rustusb-daemon/src/linux.rs` | `97ecd3229eeed5e8191a5f57a756e2326d6aa6100c76ea1e82b005a81bfd6f1a` |
| `crates/rustusb-daemon/src/physical.rs` | `3e53315a738289671ee2d78d676ccb245cb15e68a155477b402ee7318670e930` |
| `crates/rustusb-daemon/src/physical_lifecycle.rs` | `ad2c67a46c4e013165ec5f365ae85ca5de1edf070d5543c98d21a7f3912d2f0b` |
| `crates/rustusb-daemon/src/lib.rs` | `3b5f064cb54798051003d1a7097dec671322f402c4e8889adb0f9e90e1c993bf` |
| `crates/rustusb-rust-backend/src/physical.rs` | `3d16abe180ad15b317431abf3a1c1a4f9412d67bb14e3b6535beb2551992c976` |
| `crates/rustusb-rust-backend/src/physical/worker.rs` | `bc9e0b159b1af66a631cd46995b6a2c51ab66745ec714ed64ec63f21db3c587f` |
| `crates/rustusb-rust-backend/src/physical/observe.rs` | `f71bbc75dad0301c2e578c95a4c8aa5da2b03d037ac3c347ba4e245b9c6d100d` |
| `crates/rustusb-rust-backend/src/physical/safety.rs` | `7e341177c4d3bbd9b29ffb01c8fe1dd8e78bdba87b0a333d7ed0df17cfb507d2` |
| `crates/rustusb-cli/src/physical_server.rs` | `c3b4e917b67a297ff321239b71ae26be28efd8c41cbe231574acb78ab67d2d7f` |
| `vendor/nusb/src/platform/linux_usbfs/device.rs` | `43c46fe259198e942c6b2986f06b290a0fa81feeb62d74add537e43b39e6855e` |
| `vendor/nusb/src/platform/linux_usbfs/usbfs.rs` | `14003b5f2f95d6481baa7459f6a1f67a051a36ef22ce64773ce4f1ca1958d011` |

固定上游 Git blob：devio.c `3beb6a862e8084d97c619aacd32a624f1f6db3ce`；driver.c `e01b1913d02bf68aa3dd3ad4e3a30adb11585c5f`。通过 GitHub 连接器按上述固定 commit 读取；raw URL 的 Web 获取超时后没有伪称成功，改用连接器已实际取回。
