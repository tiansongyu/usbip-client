# Phase 0 差距和复用决策

审计固定日期 2026-10-22。依据：[Linux 实现](linux-usbip-audit.md)、[Rust 实现](rust-backend-audit.md)。所有“采用”指本阶段技术路线，不代表长期功能验收已通过。

| 用户目标 | Linux Native 现有能力 | jiegec 已审查能力 | RustUSB 必须补齐 |
| --- | --- | --- | --- |
| 真实设备级转发 | 内核stub/VHCI执行URB | rusb/nusb用户态路线，错误/描述符有缺口 | Phase 1先封装原生，真实硬件验收 |
| Control/Bulk/Interrupt/ISO | 内核均有处理路径，仍需具体设备验证 | 前三者有路径；ISO未实际转发 | 不混淆后端支持；逐类型测试 |
| Composite/alternate | 内核使用真实设备与描述符 | 重建描述符，interface编号/alt存在局限 | 真实设备所有接口安全评估 |
| 发现/热插拔/持久ID | sysfs反映当前，Bus ID可变化 | 一次枚举/手工add-remove | topology+VID/PID+serial候选指纹；事件协调后续 |
| 独占/清理 | 内核持有设备/socket；工具失败非原子 | 正常EOF归还，但panic/写错/重复IMPORT可泄漏 | 前后内核核对；不猜测回滚；未知资源拒绝自动接管 |
| 安全远程访问 | 无内置认证/加密 | 无内置认证/加密且解析资源无界 | Phase1受管网络；Phase3真正USB数据TLS代理 |
| 存储安全 | bind不检测挂载/句柄/服务 | 物理I/O错误被吞，不能承担写入安全 | Phase1拒绝；后续多LUN/mount namespace/dm/句柄/释放 |
| 普通用户体验 | root工具和原生CLI | 示例不是权限服务 | 普通CLI + UID认证/设备allowlist helper |
| 生命周期/监控 | sysfs可读状态，无完整应用会话语义 | 缺可靠取消与异步I/O隔离 | 状态机、事件、审计、心跳/RTT/字节计数 |
| 日常产品 | 组成工具不是完整应用 | 实验服务器/模拟设备 | 分阶段TUI/GUI/安装/长测；不能短路硬件gate |

## 可复用清单和成本

1. 直接复用发行版 `usbip_host`、`vhci_hcd`、`usbip`、`usbipd`，GPL组件保持独立；Rust只用固定argv和公开线协议/sysfs读取。无需复制Linux源文件或翻译成Rust。
2. Rust上游MIT的USB/IP公开类型、描述符构造和HID/CDC测试思路可参考；若将来实际复制，保留原版权/MIT文本，并逐文件登记。Phase0当时无复制或依赖引入；当前已按MIT选择性复用HID描述符并保留版权，见[第三方清单](../third-party.md)与[虚拟设计](rust-virtual-backend-design.md)，仍未直接依赖上游crate。
3. Phase2首先复用其QEMU模拟设备测试而非直接开启物理host示例。现有库的解析分配、cleanup和I/O语义需要内部修复，外层TLS或trait包装不能解决。建议维护小型修复分支/上游PR，但提交上游消息须另按用户授权处理。
4. 实验性后端至少须修复审计R01–R12、补错误映射与取消、隔离阻塞I/O、严格描述符/alt语义。工作量显著超出一个adapter，不给未经实现验证的工期保证。
5. 不采用纯用户态自行实现Linux客户端控制器；第一阶段复用VHCI，Windows驱动另立阶段。

## 调研闭环

上游Rust30项测试通过；上游QEMU模拟HID和CDC真实Linux枚举通过；release adversarial probes重现6项缺陷，并确认8MiB无上限接受。它们是上游证据，详细命令/环境/局限见审计。Linux采用固定主提交，并补核5.15和6.8源码；未加载宿主模块或解绑真实设备。

Phase0产物索引：架构/API `../architecture.md`、协议 `../protocol.md`、安全 `../security.md`、许可证 `../third-party.md`、设备矩阵 `../compatibility.md`、计划 `../roadmap.md`、测试 `../testing.md`、项目规范根AGENTS.md和项目Skill。Phase1的软件和硬件gate分别追踪。
