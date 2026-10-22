# Native 传输断链与故障端口的所有权边界

2026-10-22，RustUSB 基线 `67c83d5`。本文供 Native 后端开发者和测试管理员使用，区分固定上游源码、当前产品行为与未来设计。**Linux 6.8 的普通 TCP 错误最终触发 VHCI reset，目标状态为 NULL；ERROR 既不表示已释放，也不能仅凭旧会话缓存安全接管。** 本文不修改 ERROR 清理行为，不声明新的故障仿真或真实设备测试通过。

## 1. 固定源码与生命周期

源码固定为 Linux v6.8 解引用 commit `e8f897f4afef0031fe618a8e94127a0934896aba`，通过官方 GitHub 内容 API 按该 commit 读取，并核对已审计的 v6.8 `vhci_sysfs.c` 字节一致。它是上游基础版；Ubuntu 维护内核的实际结果须由对应来宾日志另行证明。

| 固定源码 | 核对结果 |
| --- | --- |
| [vhci_rx.c，接收错误与 EOF](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/vhci_rx.c#L196-L226) | TCP 接收错误提交 `VDEV_EVENT_ERROR_TCP`；EOF 提交 `VDEV_EVENT_DOWN`。部分 idle EAGAIN 不立即进入故障处理。 |
| [usbip_common.h，事件位](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/usbip_common.h#L235-L257) | TCP error 和 DOWN 都包含 SHUTDOWN 与 RESET；MALLOC error 包含 SHUTDOWN 与 UNUSABLE，不能把两条路径混为一谈。 |
| [usbip_event.c，事件执行顺序](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/usbip_event.c#L70-L94) | 在该设备的 sysfs mutex 下，先 shutdown，再按事件位 reset 或标记 unusable。 |
| [vhci_hcd.c，shutdown/reset/unusable](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/vhci_hcd.c#L1011-L1096) | shutdown 关闭 socket、停止 RX/TX、释放 socket 引用并触发 root-hub disconnect；reset 清除设备引用和地址信息并设 NULL；unusable 设 ERROR。USB core 的设备节点移除仍需独立观察。 |
| [vhci_sysfs.c，状态输出](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/vhci_sysfs.c#L38-L60) | 仅 USED 输出 speed、remote devid、历史 sockfd 和 local Bus ID；ERROR、NULL、NOTASSIGNED 的这些列均输出零值。历史 sockfd 不能作为当前进程持有的 fd。 |
| [vhci_sysfs.c，detach 与 attach](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/vhci_sysfs.c#L179-L213)、[attach 状态检查](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/vhci_sysfs.c#L393-L440) | detach 对非 NULL 端口提交 DOWN；attach 只接受 NULL。工具具备复位 ERROR 的内核入口，不代表普通 helper 已能证明该端口仍归自己所有。 |

因此，故障测试应先证明实际承载 USB URB 的 TCP 连接已中止，再检查 NULL 与旧本地设备代际消失。仅杀掉 `usbipd` 管理进程不保证内核已接管的数据 socket 关闭；仅令虚拟网卡载波下降也不证明已有 TCP 收到 FIN/RST。黑洞链路可能等待内核 TCP 超时，不能据此承诺即时检测。上述结论与[既有 Linux 审计](linux-usbip-audit.md)的数据通路边界一致。

## 2. 当前 RustUSB 能够证明什么

[NativeSession](../../crates/rustusb-native/src/lib.rs) 保存 USED 时一致读取的本地 USB 地址和 sysfs 目录 `dev/inode` receipt；[helper](../../crates/rustusb-daemon/src/linux.rs) 仅保存本进程成功创建并复核的受管连接。

- USED：端口、远端设备标识、VID/PID、本地地址和 sysfs 代际都一致，才按已有所有权执行 detach。外部重新导入同一设备不能仅凭相同 Bus ID 或 VID/PID 继承旧所有权。
- NULL：本工具可以只读确认旧代际已消失，再完成旧记录清理；同路径出现新代际不等于旧代际仍存活。NULL 但旧节点尚未移除时继续有界等待或明确报告未完成。
- ERROR / 未知身份：真实状态继续展示，破坏性操作拒绝并要求管理员协调。ERROR 不作为空闲端口，也不作为成功释放；新 attach 不跳过所有权不明的受管记录。

这些是当前代码边界，实际 TCP 中止后的运行结果仍以独立 QEMU 证据为准。完整持久恢复、心跳、自动重连、存储恢复和真实设备兼容性没有由本文补齐。

## 3. 为什么旧 receipt 不能自动授权 ERROR reset

ERROR 行没有当前连接的设备身份。旧 sysfs receipt 描述的是之前观测到的 USB 设备对象，不是内核当前端口连接的不可复用编号。NULL 可以先于旧 USB 节点异步移除；外部管理员又可在 NULL 时重新导入。仅看到旧目录仍存在、同一端口当前为 ERROR，不能排除期间已发生其他连接生命周期。这是基于上述 ABI 的安全推论，不是已执行的竞态复现。

`/var/run/vhci_hcd/portN` 的主机、端口和 Bus ID 是用户态端点记录，不能证明内核连接存活或其代际；相同端点和同一设备也可被再次导入。仅比较其内容、记录文件 inode 或未曾在轮询中看到 NULL，都不足以授权清理外部替代连接。helper 的 mutation mutex 只约束本进程，不排斥管理员直接运行官方工具或写内核接口。

当前最小安全措施是保留明确的 ERROR/unknown 结果，由管理员确认并释放特定故障端口，而不是增加“曾经拥有此端口即可 reset”的分支。此选择保留了产品尚缺完整自动恢复的事实。

## 4. 管理员协调与未来自动清理条件

管理员处理未知或 ERROR 端口时，应先停止相关应用 I/O，保存 `rustusb --json sessions`、`usbip port`、内核日志和实际 sysfs 状态，并确认拟处理的端口及测试设备。只有管理员已确认目标和影响范围时，才使用发行版官方工具释放那个端口；不批量清空所有 VHCI 连接。工具成功后仍须确认 NULL、旧设备代际/节点移除以及未影响其他连接，再决定是否重新导入。服务端驱动恢复另行核验。存储的文件系统状态与未完成写入需要独立处理，不自动挂载或恢复写入。

未来若要让 helper 对 ERROR 自动执行破坏性清理，需要能证明“当前端口仍是本次受管连接”的更强契约。例如内核提供每次 attach 都更新、在 ERROR 中仍可读取的 connection generation，并提供在同一内核锁内比较 generation 后 detach 的原子操作；不匹配应明确失败且不改变端口。还需绑定当前启动与控制器身份，防止跨重启或控制器重建后的编号复用。这是待评估设计条件，Linux 6.8 的现有端口 detach ABI 未提供该比较操作，本项目也未实现或要求用户安装此类内核扩展。

持久 ownership journal 同样不能单独成为上述证明。即使未来由 root 保护并记录设备、peer、操作阶段和旧 receipt，重启后也须与权威内核状态重新协调；未知记录、未确认的副作用和外部资源仍应拒绝自动接管。当前 helper 不持久化 ownership，允许 UID 集合共同管理本实例批准的资源，尚非按用户隔离的多租户会话系统。

## 5. 本轮验证范围

本轮优先增加实际 TCP 中止与多客户端争用的隔离仿真，检查真实 I/O、错误传播、端口状态、旧节点移除、直接重连及最终清理；不为预设的 ERROR 状态修改正常 TCP 的预期。已有[代际与互操作矩阵](native-qemu-matrix.md)保留原始成功和失败证据。新的 harness、运行结果及平台矩阵按[测试手册](../testing.md)单独登记，本文不重复创建测试，也不把源码路径当成运行通过。
