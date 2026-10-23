# USBFS 异常退出与重启协调设计

2026-10-23；基线 `e0e6118d85ca5641c8f33f700fbf9bd7668729f9`。状态：**draft，先验证现状**。本文件不声明已实现 SIGKILL 恢复。需求来自 [4.5 断线恢复和异常测试](../requirements.md)，已有合作停止证据见 [SIGTERM 报告](server-signal-shutdown.md)。

## 当前问题与本轮范围

`physical_server::run` 在普通用户进程中持有 USBFS adapter。helper 校验策略、设备和代际后通过 SCM_RIGHTS 交付设备 FD，交付后不保留租约或恢复日志。`physical::Slot` 和 worker 在内存中保存原配置、驱动、alternate、已取得接口及配置变更信息。正常关闭会等待请求终态并校验恢复；进程被 SIGKILL 后无法执行这段清理。

当前重新启动通过 helper 重新扫描，`observe::initialize` 从此刻的设备元数据建立新基线。因此，需要实测：旧进程死亡后驱动是否仍为空，以及首次新服务是否把该状态作为 original 并报告 `cleanup_verified`。不能由源码推断永久泄漏，也不能把新进程的干净快照当成旧设备已经恢复。

本轮先新增独立的 QEMU 负向验收，冻结现有产品代码，保存强制退出及首次重启的真实结果；基于结果细化跨进程方案。现有 SIGTERM harness 和已验收的产品路径保持原样。后续实现须先解决恢复授权与持久状态边界，不能只添加一个信号 handler。

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
