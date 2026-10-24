# RustUSB — 基于 Linux USB/IP 的现代化远程 USB 共享平台

## 1. 你的角色

你是一名资深 Rust 系统软件工程师、Linux 内核工程师、USB 协议专家、网络安全工程师和软件架构师。

请帮助我开发一个完整、现代化、稳定、安全、易用的开源远程 USB 共享工具，暂定名称为 **RustUSB**。

项目的长期目标是：

**实现类似 VirtualHere 的远程 USB 设备共享能力，但使用 Rust 开发，优先支持 Linux，并逐步支持其他操作系统。**

项目必须真正实现 USB 设备级重定向，而不只是文件传输或网络磁盘挂载。

例如：

1. 家里电脑插入一个真实 USB U 盘。
2. 家里电脑运行 RustUSB Server。
3. 公司电脑运行 RustUSB Client。
4. 两台电脑通过安全网络连接。
5. 公司电脑能够识别该 USB 设备，如同 USB 设备直接插入公司电脑。
6. 公司电脑可以使用原生 USB 驱动访问设备。
7. 支持安全断开、状态监控和必要的异常恢复。

所有设备连接与数据访问必须得到两端用户或管理员授权，不允许绕过企业 USB 安全策略。

---

## 2. 必须研究的开源项目

### 项目 A：Linux USB/IP 官方实现

仓库：

https://github.com/torvalds/linux

重点研究：

- `tools/usb/usbip/`
- `drivers/usb/usbip/`
- `Documentation/usb/usbip_protocol.rst`

重点分析：

- `usbipd`
- `usbip attach / detach`
- `usbip bind / unbind`
- `usbip list`
- `usbip_host`
- `vhci_hcd`
- Linux sysfs USB/IP 接口
- USB/IP TCP 连接管理
- URB 提交和完成流程
- 设备绑定与驱动切换
- 网络断开后的内核行为

不要错误地将 `tools/usb/usbip` 当作完整的内核实现。

### 项目 B：jiegec/usbip

仓库：

https://github.com/jiegec/usbip

这是本项目最重要的 Rust 参考实现之一。

重点研究：

- `src/lib.rs`
- `src/usbip_protocol.rs`
- `src/host.rs`
- `src/device.rs`
- `src/interface.rs`
- `src/endpoint.rs`
- `src/setup.rs`
- `examples/`
- `scripts/qemu/`

研究目标：

1. 确认其 USB/IP 协议支持范围。
2. 分析 Rust 服务端的架构设计。
3. 研究使用 Tokio 管理异步 TCP 会话的方法。
4. 研究通过 `rusb` 和 `nusb` 共享真实 USB 设备的方法。
5. 研究模拟 HID 和 CDC ACM USB 设备的方法。
6. 检查每种 USB Transfer Type 的实际支持情况。
7. 分析复用现有 crate 和自行编写适配层的成本。
8. 检查仓库许可证、依赖、版本、维护状态与现有测试。

必须专门审查以下情况：

- 是否正确处理 USB Control / Bulk / Interrupt / Isochronous 传输。
- 是否支持 Composite USB Device。
- 是否正确处理备用接口设置。
- 是否存在被忽略的 USB I/O 错误。
- 是否存在 `unwrap()`、`panic!()`、`unimplemented!()` 等服务端崩溃风险。
- 是否正确限制恶意 USB/IP 数据包长度。
- 是否能处理设备热插拔。
- 是否能正确恢复设备独占状态。
- 是否存在阻塞操作占用异步执行线程的问题。

禁止仅根据 README 就认定全部功能已经实现。

必须查看实际源码，并通过测试确认关键能力。

### 其他参考项目

可以补充研究：

- `dorssel/usbipd-win`
- `vadimgrn/usbip-win2`
- `jiegec/usbip` 的相关 Issue、PR、测试代码
- Rust `rusb`、`nusb`、`tokio`、`rustls`、`ratatui` 官方文档

不允许未经评估就复制大量 GPL 授权的 Linux 用户态源码到独立的 MIT 项目中。必须建立第三方依赖和许可证清单。

---

## 3. 核心技术决策

第一阶段支持：

- Linux Server
- Linux Client
- Ubuntu 22.04 / 24.04
- x86_64
- aarch64

支持两种 USB 共享后端。

### Backend A：Linux Native

优先复用：

- Linux `usbip_host`
- Linux `vhci_hcd`
- 原生 `usbip` 用户态工具与 `usbipd`

目标是最大化 Linux 原生兼容性，尽量避免重复实现成熟的内核数据通路。

### Backend B：Rust USB/IP

参考或直接使用 `jiegec/usbip`：

- Rust 实现协议服务端
- `rusb` / `nusb` 访问 USB 设备
- 支持虚拟 USB 设备
- 用于设备模拟、扩展与实验性 USB 后端

这两个后端应实现统一的抽象接口。

必须明确：`jiegec/usbip` 的用户态物理设备共享与 Linux `usbip_host` 内核共享是不同实现路线，不能把二者错误叠加为同一数据路径。

客户端第一阶段使用 Linux `vhci_hcd`。

Windows 支持作为独立阶段开发，不能宣称 Linux VHCI 可以直接用于 Windows。

---

## 4. 必须实现的功能

### 4.1 本地 USB 设备发现

自动发现：

- USB 存储设备
- USB 串口设备
- HID
- USB 摄像头
- USB 音频设备
- USB JTAG / Debug Probe
- USB 网卡
- Composite Device
- 其他 USB 类设备

展示：

- VID / PID
- Manufacturer
- Product
- Serial Number
- Bus ID
- Device Address
- USB 版本
- USB 速度
- USB Class / Subclass / Protocol
- Interface 和 Endpoint 信息
- 驱动名称
- 当前绑定状态
- 是否已被共享
- 是否正在被占用
- 是否具备共享条件

必须区别“发现设备”和“已验证可以正确重定向”。

不能将所有 USB 设备类型都标为已支持。

### 4.2 远程设备共享

实现：

- 启用设备共享
- 取消设备共享
- 远程设备列表查询
- 远程设备导入
- 远程设备断开
- 单设备独占
- 多设备同时共享
- 多客户端管理
- 设备访问权限
- 会话状态监控

默认一台物理 USB 设备同一时间只能分配给一个客户端，除非设备本身明确支持其他共享语义。

禁止两个主机同时独立修改同一个 USB 存储文件系统。

### 4.3 设备热插拔

支持：

- USB 设备插入事件
- USB 设备拔出事件
- 设备重新枚举
- 远程设备列表刷新
- 本地设备元信息变化
- USB 总线号变化

不能只用 Bus ID 标识持久设备。

应设计持久设备标识机制，综合 VID/PID、序列号、物理端口拓扑等信息，同时处理无序列号与同型号多设备的歧义。

### 4.4 网络连接

支持：

- IPv4
- IPv6
- 局域网
- VPN 网络
- 主机名连接
- 连接超时
- 心跳
- 网络延迟检测
- 会话监控
- 带宽统计
- 异常断线检测
- 多客户端连接

安全优先：

- 默认不直接暴露未加密 USB/IP 3240 端口到公网。
- 支持经认证的安全隧道。
- 安全通道与原生 USB/IP 数据通路必须在架构上明确分离。
- 如采用 TLS 隧道，设计本地 TCP 转发代理，并验证 USB/IP 长连接的正确转发。
- 如采用 Rust 实现的加密通道，使用成熟 TLS 库，不自行设计加密算法。

需要分别测试控制连接和实际 USB 数据连接。

### 4.5 断线恢复

必须设计清晰的状态机：

- Discovered
- Available
- Exporting
- Connecting
- Connected
- Disconnecting
- Disconnected
- Error
- Recovering

设计要求：

- 明确状态切换条件。
- 每个状态都有合理超时和错误信息。
- 支持取消操作。
- 资源清理必须幂等。
- 操作失败不能留下永久占用的设备。
- 服务进程重启后能够协调系统中的已有设备状态。

重要限制：

对于 USB Mass Storage，不能在网络断开后无条件自动重新挂载并继续写入。

必须考虑脏缓存、文件系统一致性和未完成的写入请求。

存储设备默认采用保守恢复策略：确认状态、安全重新枚举、必要时要求人工介入。

### 4.6 USB 存储安全

导出之前检测：

- 设备是否存在已挂载分区
- 是否存在活动文件句柄
- 是否被系统服务使用
- 是否包含多个 LUN 或多个分区
- 是否存在加密文件系统
- 是否适合进行安全导出

不能未经确认强行卸载正在使用的存储设备。

支持设备安全释放、卸载提示和错误恢复。

测试时优先使用测试 U 盘和只读模式，不使用真实生产数据。

### 4.7 权限系统

禁止默认让整个应用以 root 运行。

应分离：

- 普通用户 CLI/TUI
- 系统守护进程
- 受限制的特权操作层

特权操作至少包括：

- 加载必要的内核模块
- 绑定和解绑 USB 设备
- 创建和拆除 VHCI 连接
- 启停相关服务

优先使用 Unix Domain Socket 和操作系统提供的访问控制机制。

对输入的 Bus ID、端口、设备路径、主机名和配置字段进行严格校验。

避免 shell 注入、任意命令执行、任意文件访问和越权操作。

### 4.8 CLI

使用 `clap` 提供结构清晰的命令行接口。

预期命令形式：

```bash
rustusb devices
rustusb devices --json
rustusb export <device-id>
rustusb unexport <device-id>
rustusb peers
rustusb discover <host>
rustusb attach <host> <device-id>
rustusb detach <session-id>
rustusb sessions
rustusb status
rustusb doctor
rustusb logs
rustusb config
rustusb benchmark
```

具体命令可调整，但必须保持一致性、可测试性和良好的错误提示。

### 4.9 TUI

使用 Ratatui 实现终端可交互界面。

至少包含：

- 本地设备
- 远程设备
- 当前连接
- 系统状态
- 日志
- 设置

支持键盘快捷键、搜索、过滤、排序、自动刷新、错误详情以及确认对话框。

TUI 不得直接执行特权操作，必须调用统一应用服务接口。

### 4.10 GUI

GUI 放在 CLI 和核心服务稳定之后。

优先研究 Tauri 方案。

支持：

- 双端设备列表
- 连接与断开
- 搜索设备
- 网络延迟显示
- 连接状态
- 设备详细信息
- 自动共享规则
- 错误提示
- 安全访问控制

GUI 应与 CLI、TUI 共用一套核心逻辑，而不是重新实现通信或设备管理。

### 4.11 日志与可观测性

使用 Rust `tracing`。

记录：

- 设备发现
- 设备导出
- 设备导入
- 连接建立
- 状态变化
- 权限拒绝
- 错误原因
- 网络断线
- 资源清理
- 会话结束

支持：

- JSON 日志
- 人类可读日志
- 可配置日志级别
- 日志轮转
- 敏感字段脱敏
- 诊断信息导出

监控指标包括：

- 活动设备数量
- 活动会话数量
- 传输字节数
- 连接失败次数
- RTT
- 吞吐量
- 错误计数

禁止将原始 USB 数据、密钥和敏感存储内容默认写入日志。

---

## 5. 推荐工程架构

使用 Cargo Workspace 进行模块化开发。

建议仓库结构：

```text
rustusb/
├── Cargo.toml
├── Cargo.lock
├── README.md
├── LICENSE
├── AGENTS.md
├── crates/
│   ├── rustusb-core/
│   ├── rustusb-protocol/
│   ├── rustusb-linux/
│   ├── rustusb-native/
│   ├── rustusb-rust-backend/
│   ├── rustusb-transport/
│   ├── rustusb-security/
│   ├── rustusb-daemon/
│   ├── rustusb-client/
│   ├── rustusb-cli/
│   ├── rustusb-tui/
│   └── rustusb-gui/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── interoperability/
│   ├── hardware/
│   └── fixtures/
├── scripts/
├── packaging/
│   └── systemd/
├── docs/
│   ├── architecture.md
│   ├── protocol.md
│   ├── security.md
│   ├── compatibility.md
│   ├── testing.md
│   └── roadmap.md
└── .agents/
    └── skills/
        └── rustusb-development/
            └── SKILL.md
```

目录可以根据实际依赖关系调整，避免为了抽象而创建大量空 crate。

### 统一后端抽象

设计类似以下能力接口：

```rust
trait DeviceDiscovery {
    // 扫描 USB 设备并返回元数据
}

trait ExportBackend {
    // 导出/取消导出设备
}

trait ImportBackend {
    // 导入/断开远程设备
}

trait SessionManager {
    // 管理远程连接和会话生命周期
}

trait PrivilegedOperations {
    // 严格受控的系统特权操作
}
```

这些只是概念接口。

AI 必须进一步设计可编译的 Rust API，包括：

- 所有权
- 异步执行
- 生命周期
- Send/Sync
- 错误类型
- 取消语义
- 状态事件
- 平台兼容性

不允许把示例 trait 当作已经完整的工程接口。

---

## 6. 性能要求

需要同时考虑：

- 低延迟
- 高吞吐量
- 低 CPU 占用
- 低内存占用
- 多设备并发
- USB 设备响应稳定性

特别注意：

USB/IP 是 USB 请求转发，不是普通文件传输协议。

网络 RTT 可能显著影响频繁的小 USB 传输。

不同设备的性能表现也不同：

- USB Mass Storage：注重吞吐、随机读写及一致性。
- USB Serial：注重延迟与稳定性。
- HID：注重事件响应。
- UVC：注重带宽、帧时序与 Isochronous 支持。
- JTAG：注重请求响应延迟及连接可靠性。

不能为了提升性能而破坏原始 USB 协议语义。

需要定义性能基准，至少记录：

- RTT
- 吞吐量
- P50 / P95 / P99 延迟
- CPU 占用
- 内存占用
- 并发会话数
- 错误率
- 断线恢复表现

必须区分“测试目标”与“实测结果”。

不得虚构性能数据。

---

## 7. 测试体系

必须建立自动化测试体系。

### 单元测试

测试：

- 协议序列化
- 协议反序列化
- 网络字节序
- USB 描述符处理
- 设备 ID
- 状态机
- 配置文件
- 认证
- 错误处理
- 超时和取消

### 协议互操作测试

验证：

1. RustUSB Server → Linux `usbip attach`
2. Linux `usbipd` → RustUSB Client
3. RustUSB Server → RustUSB Client

第三项允许通过 Linux VHCI 后端实现，不必自行重写内核客户端。

### QEMU 测试

参考 jiegec/usbip 的 QEMU 测试方法。

使用虚拟设备测试：

- HID Keyboard
- CDC ACM Serial
- USB 描述符枚举
- 断开连接
- 重连
- 异常协议包

### 硬件测试

使用真实测试设备：

- 普通 U 盘
- USB 串口
- USB HID
- USB JTAG
- USB 摄像头

对不支持的设备应明确标注原因。

### 异常测试

模拟：

- 断网
- 高延迟
- 丢包
- 服务进程异常退出
- USB 设备突然拔出
- 客户端异常退出
- 多客户端争用
- 设备忙
- 权限不足
- 协议版本不兼容

### 安全测试

必须包含：

- 未授权连接
- 非法设备访问
- 畸形 USB/IP 消息
- 超大长度字段
- 认证失败
- 重放或重复连接尝试
- 恶意配置
- 日志敏感信息检测
- 特权接口滥用

对网络输入解析层进行 fuzz testing。

---

## 8. 开发阶段

请严格按阶段完成。

### Phase 0：源码调研和设计

先阅读指定的两个上游项目。

输出：

- 功能差距分析
- 可复用源码清单
- 协议兼容性报告
- 许可证分析
- USB 设备支持矩阵
- 技术架构
- 具体接口设计
- 安全威胁模型
- 详细开发计划

这一阶段不要急于实现 GUI。

### Phase 1：最小可运行系统

实现：

- Rust Workspace
- 本地设备扫描
- Linux 后端能力检测
- 设备导出
- 远程设备发现
- 远程设备导入
- 设备断开
- CLI
- 基础日志

验收条件：

在两台测试 Ubuntu 系统之间，能够通过 RustUSB 管理 USB/IP 连接，使客户端 Linux 内核成功枚举至少一种真实测试 USB 设备。

### Phase 2：jiegec/usbip 后端集成

加入：

- 可选 Rust 服务端
- 虚拟 USB 设备
- 真实物理设备共享
- 统一后端接口
- 与 Linux 官方 USB/IP 的互操作测试

必须对发现的上游局限逐项验证。

不得在测试不通过时声称已支持相关 USB 设备类型。

### Phase 3：安全远程连接

实现：

- 身份认证
- 加密隧道
- 设备级授权
- 会话管理
- 状态机
- 断线处理
- 基础审计日志

注意：原生 USB/IP 的 TCP 连接不会因为引入一个 Rust 管理 API 就自动加密。必须验证真正承载 USB 数据的连接也经过安全通道。

### Phase 4：完整 TUI

实现终端设备管理界面，包括热插拔、连接状态、设备过滤、日志浏览和配置管理。

### Phase 5：GUI 和安装体验

实现：

- Tauri GUI
- `.deb` 安装包
- systemd 服务
- 配置文件管理
- 安装与卸载脚本
- 文档和使用示例

### Phase 6：性能和兼容性优化

扩大 USB 设备支持范围，建立性能测试与真实硬件兼容性报告。

### Phase 7：Windows 支持

独立研究 Windows USB/IP 客户端驱动。

在完成驱动兼容性、签名、安全和测试评估之前，不把 Windows USB 直通作为已实现功能。

---

## 9. 创建项目级 AI Skill

请在仓库中创建：

`AGENTS.md`

以及：

`.agents/skills/rustusb-development/SKILL.md`

其中必须记录：

- 本项目的目标和范围
- Linux USB/IP 技术架构
- Rust 编码规范
- 上游源码参考与边界
- 系统权限使用原则
- 内核接口使用规范
- 协议兼容性要求
- 安全原则
- 异步并发规范
- 错误处理规范
- 单元测试和集成测试流程
- 硬件测试安全原则
- 文档更新规范

每次开发 USB/IP 核心代码之前，AI 应首先参考这些规范。

按照所用 AI Agent 的实际 Skill 发现机制组织文件，不要假定所有 Agent 都自动加载相同路径。

---

## 10. 编码与工程质量要求

- 使用稳定版 Rust。
- 遵循 Rust 2024 Edition，但需要先确认依赖与工具链兼容性。
- 尽量避免 `unsafe`。
- 使用类型化错误，不要吞掉重要错误。
- 避免在核心逻辑中使用 `unwrap()` 和 `expect()`。
- 对外部输入设置长度及资源使用上限。
- 异步代码中避免长时间阻塞 Tokio Worker。
- 避免不必要的内存复制。
- 不使用未经充分验证的自定义加密算法。
- 每个公共 API 应提供文档。
- 关键业务模块必须有测试。
- 保证 CLI、TUI 和 GUI 共用核心服务。
- 对系统资源进行确定性清理。
- 所有特权操作必须经过受控接口。
- 所有功能必须说明其操作系统和内核版本限制。

每个阶段完成后执行：

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo build --workspace --release
```

如果测试环境无法访问 USB 硬件，必须清楚说明未完成硬件验证，不能将编译成功当作实际 USB 直通成功。

---

## 11. AI Agent 工作规范

你不是一次性代码生成器，而是项目的长期主工程师。

工作时遵循以下规则：

1. 先检查仓库现状，再修改代码。
2. 优先复用已经验证可靠的开源组件。
3. 不重复实现成熟的内核功能。
4. 每个阶段先提出设计，再落实到代码。
5. 遇到问题先定位原因，不要不断添加临时补丁。
6. 不要为了消除编译错误而删除核心功能。
7. 不要留下无法编译的占位代码却标记为已完成。
8. 不要因为缺少真实硬件而跳过所有可自动执行的协议测试。
9. 不要声称实现了未经验证的设备支持。
10. 每次修改后记录已完成内容、测试结果和遗留问题。
11. 定期检查是否有不必要的模块、重复代码和过度抽象。
12. 保持软件架构可维护、可测试、可扩展。

优先交付真正可以运行的最小版本，再扩展完整功能。

---

## 12. 现在开始执行

首先执行 Phase 0。

请：

1. 调研 Linux 官方 USB/IP 和 jiegec/usbip 的具体实现。
2. 检查两者的代码架构和协议兼容性。
3. 分析如何最大化复用 jiegec/usbip。
4. 明确 Native Backend 和 Rust Backend 的边界。
5. 设计一套现实可实现的 Rust 软件架构。
6. 输出完整的开发计划和验收标准。
7. 创建必要的架构文档、项目规范和 AI Skill。
8. 在完成调研后开始 Phase 1 的最小可运行实现。

最终目标是开发一款**真正可用于日常远程 USB 设备共享的高质量开源工具**，而不只是演示 USB/IP 协议的实验项目。

请从源码审计和架构设计开始，所有重要技术判断都提供依据。