# USBFS 异常退出与重启协调设计

2026-10-23；基线 `e0e6118d85ca5641c8f33f700fbf9bd7668729f9`。状态：**draft；第一项现状验证已完成，恢复 FAIL**，见[实际报告](physical-crash-recovery.md)。本文件不声明已实现 SIGKILL 恢复。需求来自 [4.5 断线恢复和异常测试](../requirements.md)，已有合作停止证据见 [SIGTERM 报告](server-signal-shutdown.md)。

## 当前问题与本轮范围

`physical_server::run` 在普通用户进程中持有 USBFS adapter。helper 校验策略、设备和代际后通过 SCM_RIGHTS 交付设备 FD，交付后不保留租约或恢复日志。`physical::Slot` 和 worker 在内存中保存原配置、驱动、alternate、已取得接口及配置变更信息。正常关闭会等待请求终态并校验恢复；进程被 SIGKILL 后无法执行这段清理。

当前重新启动通过 helper 重新扫描，`observe::initialize` 从此刻的设备元数据建立新基线。原计划据此实测旧进程死亡后驱动是否仍为空、首次新服务是否将其作为 original 并报告 `cleanup_verified`；现已在下述第一项 QEMU 验收中观察到两者，详见实际报告。不能由此推断永久泄漏，也不能把新进程的干净快照当成旧设备已经恢复。

本轮已按计划新增独立的 QEMU 负向验收，冻结现有产品代码，保存强制退出及首次重启的真实结果；后续基于结果细化跨进程方案。现有 SIGTERM harness 和已验收的产品路径保持原样。后续实现须先解决恢复授权与持久状态边界，不能只添加一个信号 handler。

## 已核对的机制

- Linux v6.8 `drivers/usb/core/devio.c` 的 `usbdev_open` 为每次 open 创建 `usb_dev_state`；`usbdev_release` 释放该 open 的 claim 并销毁异步请求。`releaseintf` 不包含应用层保存的原配置、alternate 和驱动恢复事务。[固定上游源码](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/devio.c)
- SCM_RIGHTS 共享 open file description，语义相当于向另一进程 dup。因此监督者仅保留转交 FD 的副本，会延长旧 USBFS 状态的存活，不能同时假定 child 死亡触发最后 close。独立 open 才产生另一份 USBFS 状态；它不能替旧 open 释放其 claim。[unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)
- pidfd 可以稳定观察特定进程，不能证明该进程曾持有的所有 FD 已无其他引用；也不能代表设备代际、接口归属或恢复授权。对普通 child 的 wait/reap 与对设备的实际观察必须分别记账。[pidfd_open(2)](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)

以上是上游源码与 API 语义；来宾使用的 Ubuntu `6.8.0-106-generic` 是另一构建，运行行为以 QEMU 原始证据为准。未向 MIT 产品复制 GPL 内核代码，未增加依赖。

两份[独立只读设计审查和来源清单](../evidence/physical-crash-design-20261023/manifest.json)已保留，均认为可开始下述负向测试，尚不足以实施自动恢复。Linux v6.8 tag 解析至 `e8f897f4afef0031fe618a8e94127a0934896aba`；五份相关源码的实际 Git blob、长度和 SHA256 已核对。源码引用针对该固定版本，未宣称验证 tag 签名。`PhysicalGrant` 当前没有完整的原配置字段，未来 helper 持久基线需要新增可信观察，不能直接序列化现有 grant 代替。

## 候选方案与拒绝条件

| 方案 | 作用 | 当前问题 |
| --- | --- | --- |
| 只在退出时执行 Drop / signal handler | 合作停止 | SIGKILL 无法执行；不满足异常退出恢复 |
| 普通父进程监督固定 worker | 可观察自己的 child，保持应用非 root | 父进程也会死亡；需安全交付、独立恢复能力和跨进程状态同步 |
| helper 跟踪租约并保留独立恢复能力 | 与现有授权边界一致 | 必须证明旧能力已结束、恢复操作仍有授权，并保留 helper 自身重启后的未决状态 |
| 仅持久 journal、重启时盲目恢复 | 保存原基线 | 文件存在不证明操作完成；不能依据旧 Bus ID 或 PID 接管当前设备 |

当前推荐分步建立 **未完成租约的拒绝重用边界，再实现受限恢复**，仍待独立审查。记录必须先于可能改变设备的授权/操作；事务含未确认结果时保持隔离。新授权不得覆盖旧基线。原生后端与物理后端继续互斥，不让 root helper 承载网络 USB 数据解析。

第一步的持久记录至少包含版本、boot identity、设备指纹和代际 receipt、原配置、完整接口的原 alternate/driver、授权主体及交付阶段。仅记录恢复必需的非敏感元数据，不保存原始 USB 数据、序列号或密钥。记录上限、可信目录/无符号链接、原子替换、fsync、损坏/丢失处理和多进程互斥必须先定义；内存 mutex 不能解决第二个 helper 或 helper 重启。发送 FD 前持久预留，发送或 ACK 结果不确定时不得删除记录。

正常结束如何安全退休记录也是产品实现的前置问题：状态重新匹配不能证明能力已经撤销，PID 消失也不能排除其他进程持有副本。不能交付一个永远拒绝再次授权、却没有正常退休/管理员协调路径的半成品。拒绝逻辑应针对未决授权记录，不得一概拒绝原本合法的未绑定接口，也不得为了简化恢复而删除已验证的多配置/alternate 功能。

自动恢复至少需要：同一设备代际、可信原基线、确定的自有操作范围、旧请求不再活动、当前配置可解释、无外部新 claim/驱动。存储、危险配置、代际改变、外部占用、缺失或损坏记录、未决配置变更均拒绝自动修改。所有 ioctl 后还需独立观察实际结果。独立 FD、持久日志和 pidfd 各自只解决部分问题，不把三者组合直接当成可撤销能力。

完整跨进程 API、write-ahead/ACK 时序、helper 重启和并发授权尚未定稿；不在未验证时建立空 crate 或声明架构决策已接受。

## 第一项 QEMU 验收契约

1. 两个一次性 guest，私有 socketpair；仅 guest 加载模块和操作合成 HID / FTDI，宿主普通 UID，不访问宿主 USB。沿用真实 KEY1 down/up 与 FTDI 35 字节双向 I/O、root0600、普通 UID1000 授权及 UID1001 拒绝。
2. 信号前保存两个真实 IMPORT、每设备 pending/inflight、保留预算、独立 sysfs claim、设备代际及 `/proc` FD 身份。对自己的普通服务核 PID/starttime/exe/UID/capabilities，且 stdin 保持打开。
3. 仅对该服务发送一次 SIGKILL；实际 wait137 属于预期强制退出事实，不能要求它 exit0，也不能据此声称恢复成功。继续完整观察同代际配置/alternate/driver；缺失 stopped 就保存缺失，不伪造末快照或 reap 数。
4. 客户端在任何显式 detach 前保存自主断连、VHCI 状态和旧设备代际消失。对两个 guest 的收集均完成后再判断产品恢复是否通过。
5. 保存上述原件后，普通 UID1000 用同一显式 device-id 和现有 helper 首次重启服务。无新 IMPORT，单独记录启动/拒绝、真实 ready 中 original/current/cleanup_verified、实际 FD 与身份及外部 sysfs；若已启动，再合作停止并记录 wait、stopped 与停止后 sysfs。初始原基线始终保留，不被重启基线覆盖。
6. 证据收集完成与产品恢复通过分开。旧实现的恢复断言若失败，最终保持 FAIL；重启行为按实际观察报告，不把预期失败转成恢复 PASS。源码、构建、二进制、模块和前后校验均记录。

后续产品修复需补充 supervisor/helper 自身死亡、FD 转交残留引用、授权中断、journal 损坏/磁盘错误、外部 claim、重插代际拒绝、配置/alternate 操作中断及可验证恢复后再导入。第一项负向不替代这些验收、真实硬件 G1/G2 或原项目后续阶段。

## 2026-10-23：持久授权记录的实施切片

本次先实现可信基线和合作结束协议，自动恢复仍未实现。现有安全模型已信任取得整设备 FD 的服务；因此正常结束不要求证明系统中所有外部 FD 副本消失。结束声明只表示该可信服务停止使用并释放其已知能力，helper 独立核验原配置、完整接口/alternate/driver、boot 与代际后退休管理记录；它不是能力撤销证明。

- helper 在交付任何 FD 前，将随机 lease ID、OS peer UID/PID/starttime/boot ID，以及不含原始设备字符串的基线原子落盘。默认 `/var/lib/rustusb/physical`，root helper 可用 `--physical-state-dir` 指定测试/部署目录，前端不得指定路径。记录上限 256，文件上限 16 MiB，完整接口上限 256。固定文件、root 目录链、NOFOLLOW、0600、独占锁、文件及目录 fsync；损坏/缺失文件或不确定提交失败均拒绝继续授权，不能当成空表。首次创建新目录可初始化；管理员删除整个状态目录不在可检测保证内，不能这样清理未决记录。
- 同一 device ID 的未决记录阻止后续 grant，包括同指纹的新代际；helper 重启加载原记录。记录从 FD 授权前开始，与后续 IMPORT 不混同。FD ACK/发送/客户端校验失败保留未决，不用未发送的猜测清记录。
- 新 `FinishPhysical { lease_id }` 只接受原 OS owner；客户端先 shutdown、等待自己已知后台任务结束、保存末快照并关闭 registry，再请求结束。helper 只读复核同代际完整基线。已经物理消失的旧代际可以完成旧记录，但不操作替代设备。响应丢失后的 absent 仅报告 not_pending，不冒充再次核验。helper 自有发送 FD 必须在允许 finish 前释放。
- `PhysicalPending` 和 `ReconcilePhysical { lease_id, acknowledge_gone }` 为 root-only 管理接口；后者只核验并退休，不写 sysfs、不自动 bind、不接受任意路径。仍存在的设备必须同代际且完整恢复；旧代际消失需显式 acknowledge_gone，替代代际不接受旧恢复记录。下一次授权仍须重新读策略、扫描和生成新 receipt，并不声称每次重插都需管理员修改 allowlist。
- setup 部分失败会尝试结束已收到的 grant；交付中取消/未知结果保留未决供管理员核验。stdout 失败和重复信号不能跳过已开始的清理与 finish。cleanup 或独立观察失败保留记录及明确错误，不悄然采用新的 original。

软件测试覆盖持久重载/原子失败、重复/并发/损坏/权限/锁、owner 与完整基线拒绝；新增独立 QEMU 场景验证正常再次启动、SIGKILL 后跨 helper 重启持续拒绝、管理员恢复前拒绝与恢复后再授权。旧负向证据和断言保持不变；initramfs 的进程重启不代表断电或 OS 重启耐久验证。完整多配置/alternate 能力保留，不通过限制设备功能消除恢复问题。

验收收尾补全：新的 journal 场景在管理员恢复并退休旧记录后，再执行第三轮真实 HID/FTDI I/O、确认活动请求、正常关闭及记录退休；只有中间 normal reopen 保留无 IMPORT 对照。这样证明协调后可重新传输，而不以 ready 代替实际复用。首轮失败与其未执行的后续步骤仍原样保留。

## 2026-10-23：独立恢复句柄的机制实验

产品基线 `60be074`，软件仍为已验收的持久记录实现。本切片先验证恢复所需的内核机制，**不实现或宣称产品自动崩溃恢复**。现有 journal 负向、管理恢复路径和产品代码保持不变；新测试只在一次性 guest 中增加 root 测试 probe。它不是可安装的管理员恢复工具，不接收网络命令，也不用于宿主设备。

固定 Linux v6.8 的七份源码身份、只读集成审查及其范围澄清、实验计划见[设计证据](../evidence/physical-guardian-design-20261023/manifest.json)。内核源码保留在研究缓存，仓库只记录引用和校验信息；未复制 GPL 实现进产品。109 个跟踪的产品输入与 `933e720` 逐字节一致。

实验复用双 guest journal 场景的真实三轮 HID/FTDI I/O、原基线、授权记录、真实 wait137、重启拒绝及只读 reconcile 断言。独立 B 指再次 open 同一受验证字符节点，A 指服务取得的 USBFS open file description；`pidfd_getfd` 得到的 A 副本作为反例。打开 `/proc/PID/fd/N` 不视为 dup。实际 `kcmp(KCMP_FILE)` 必须证明 A 副本与 A 相同、B 与 A 不同；权限/内核支持失败就是实验失败，不能跳过后标通过。

1. 在 killed epoch 的 IMPORT 前打开 B，保留完整 node/sysfs receipt 与原配置、alternate、driver。B 不执行 detach、reset 或 SET_CONFIGURATION。
2. 已有真实 I/O、pending/inflight 和严格服务身份后取得 A 的副本。只对该普通服务发送一次 SIGKILL 并记录实际 wait137。在 A 副本仍存在时，B 的 plain CLAIMINTERFACE 和 CONNECT 应以 EBUSY 拒绝；同时保留 sysfs 中实际 usbfs 占用。该反例明确 PID 死亡不能替代最后 FD 关闭。
3. 明确关闭测试 probe 唯一持有的 A 副本后，用同一个 B 做 plain CLAIMINTERFACE / RELEASEINTERFACE，核验实际成功与独立完整 sysfs。成功只说明本次接口可以取得，不证明系统中不存在任意设备 FD、EP0 请求或未覆盖的内核操作。
4. 用另一独立 C 显式占用接口，检查 B 的 CLAIM 与 CONNECT 再次 EBUSY，且 C 的占用不被移除。关闭 C 后才继续。恢复路径禁止使用 DISCONNECT、DISCONNECT_CLAIM 或 sysfs unbind 绕过占用。
5. 回到原 journal 的未恢复/null-driver 观察与跨 helper 重启拒绝。原 guest 管理员两次 sysfs bind 改为由长期保留的 B 执行 CONNECT；记录逐次真实 return/errno，随后仍要求同代际完整原配置/alternate/driver 恢复。CONNECT 让内核选择匹配驱动，并不能指定原 driver；本实验固定加载的模块与事后完整核验不构成任意生产设备的驱动选择保证。
6. 关闭 probe 的 B，记录其实际退出状态，再由 root 只读 reconcile 退休记录，完成原第三轮真实 I/O 和正常结束。probe 不直接修改 journal，不将新 epoch 的资源归零当作旧 epoch 的末状态。

为避免复制既有大段 guest 测试，新 runner 在 initramfs 组装时对旧 guest 的6个唯一锚生成变体；每个锚严格要求恰好一次，保存生成的 init 原字节/哈希，并将旧脚本、新 runner、probe 与生成过程全部计入来源校验。旧脚本本身不改。以上是运行前契约；首43d6d43失败与修订后的clean da764d0机制通过，见[实际报告](usbfs-guardian-mechanism.md)，不据此标产品自动恢复完成。

后续产品方案仍需区分两类授权契约。若管理员明确允许“租约结束后恢复整台设备的原基线”，同配置、同完整接口和同 alternate 下的 CONNECT-only 恢复范围可以由可信基线界定，不必把每次 claim 都改成持久事务；它仍不能区分服务解绑与管理员故意解绑的相同终态，必须明确恢复期间的管理员协调规则、当前策略与旧记录迁移，不能让原只读 schema 1 静默获得主动恢复权限。若只允许撤销服务实际做过的变更，则还需要执行前持久许可和完成确认；单靠日志也不能证明外部管理员没有产生相同变化。两条路线仍为设计建议，尚未实现。

跨配置/alternate 恢复还需可信变更意图、完成确认、未决操作和 helper 重启后的授权；不能由原基线猜出最后配置或实际修改范围，也不能将 CONNECT 的条件拒绝当成整设备能力撤销。保留现有多配置功能；跨配置恢复、helper 崩溃、外部新驱动、热拔替换和内核内部未完成操作另行验收。
