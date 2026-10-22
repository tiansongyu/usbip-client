# RustUSB

基于 Linux USB/IP 的远程 **USB 设备级重定向**工具，Rust 管理设备、权限和连接，Linux 内核执行真实 USB 请求转发。

当前为 **Phase 1 开发预览**：已完成源码调研并实现 Native 路线；阶段状态和实际测试结果见 [工作日志](docs/work-log.md)。真实物理设备兼容性尚未验证，Windows、内建 TLS、TUI、GUI、存储安全共享仍在 [开发计划](docs/roadmap.md) 中。发现某类设备不代表已验证它可正确重定向。

## 构建与检查

目标系统 Ubuntu 22.04/24.04，x86_64/aarch64；当前验证环境和未测项见 [兼容性](docs/compatibility.md)。需要稳定 Rust ≥1.97（2024 Edition）和匹配运行内核的发行版 `usbip` / `usbipd`。Ubuntu 通常由 `linux-tools-common`、`linux-tools-$(uname -r)` 提供工具；模块由对应内核包提供。

```bash
cargo build --workspace --release --locked
scripts/check.sh
target/release/rustusb devices
target/release/rustusb devices --json
target/release/rustusb doctor
```

设备 ID 使用 VID/PID、序列号（若有）和物理控制器/端口拓扑的 SHA256 候选指纹。Bus ID 只是当前地址。无序列号的同型号设备在同一物理端口替换无法可靠区分；指纹不是硬件身份认证。JSON 设备详情含序列号，分享前自行脱敏，默认日志不输出它。

## Native 共享流程

CLI 普通权限运行；只有受限的 `rustusb-helper` 作为系统管理员运行。helper 不启动 `usbipd`、不改防火墙、不加载模块。开始前，两端管理员建立受管 VPN/安全隧道和 IPv4/IPv6 防火墙，只让授权对端访问 USB/IP 端口。原生 USB/IP 自身没有认证或加密，`trusted_network: true` 只是管理员确认，不会创建安全通道。

1. 管理员确认指定测试设备和双方授权，先运行 `rustusb devices --json` 获取 `usb-…` ID。不要选择生产设备、正在使用的输入设备或管理网络网卡。Phase 1 拒绝存储、Hub、不完整元数据及无法证明安全的设备；不会强制卸载。
2. 两端管理员安装 `target/release/rustusb` 与 `target/release/rustusb-helper` 到 root 管理的目录（例如 `/usr/local/bin`），加载各端需要的 `usbip_host` / `vhci_hcd`。按 [策略示例](examples/policy.json) 建立 `/etc/rustusb/policy.json`，仅加入批准 UID、设备指纹和远端 host/port。
3. 管理员创建 `/run/rustusb`（root 所有、0755）和 `/etc/rustusb`（root 所有、0755），策略文件 root 所有、0600。所有祖先目录不得允许组/其他用户写入。运行：

   ```bash
   sudo /usr/local/bin/rustusb-helper \
     --config /etc/rustusb/policy.json \
     --socket /run/rustusb/control.sock
   ```

4. 服务端管理员在确认网络隔离后启动发行版 `usbipd`。它默认监听全部地址，**没有监听地址参数**；仅加 `-4` 或改端口不构成隔离。不会由 RustUSB 自动启动。
5. 普通用户在服务端和客户端分别执行：

   ```bash
   # 服务端：只允许root策略中批准的指纹
   rustusb export usb-<64位小写指纹>

   # 客户端：host和port必须与其helper的策略完全匹配
   rustusb discover 192.0.2.1 --port 3240 --json
   rustusb attach 192.0.2.1 1-2 --port 3240
   rustusb sessions
   rustusb status
   # 用sessions返回的端口替换0
   rustusb detach 0

   # 服务端确认对端已经释放后
   rustusb unexport usb-<64位小写指纹>
   ```

示例地址 `192.0.2.1` 仅用于文档/隔离仿真。请使用实际受管地址和扫描得到的Bus ID。所有命令支持 `--json`，日志写 stderr，`--log-json` 启用 JSON tracing，`RUST_LOG=info` 配置级别。普通用户不使用 `sudo rustusb` 代替 helper。

helper 的所有允许 UID 共同管理已批准设备和本实例建立的连接；这不是多租户隔离。仅能自动断开本运行实例准确认领的端口；重启后未知端口会显示但拒绝自动接管，需管理员核对。设备操作超时或取消不代表没有副作用；请检查 `sessions/status` 和内核，避免盲目重试。卸载/重启不自动强制断开未知设备。详见 [安全边界](docs/security.md)。

## 测试与研究

```bash
# 上游源码审计：固定commit，普通测试与对抗性probe
scripts/audit-rust-upstream.sh
# DEVLIST解析器短时ASan fuzz，需要单独安装nightly和cargo-fuzz
scripts/fuzz-smoke.sh
```

QEMU 仿真用于验证本项目导出、发现、导入、Linux 枚举、实际输入、断开与重连；运行方法和结果见 [测试手册](docs/testing.md)。仿真与真实物理 USB 测试分别记录。

- [架构与接口](docs/architecture.md)、[协议](docs/protocol.md)、[源码差距](docs/research/gap-analysis.md)
- [Linux 官方实现审计](docs/research/linux-usbip-audit.md)、[jiegec Rust 实现审计](docs/research/rust-backend-audit.md)
- [许可证与依赖](docs/third-party.md)、[完整需求](docs/requirements.md)、[项目开发规范](AGENTS.md)

RustUSB 自有代码 MIT；外部 Linux 工具/驱动仍按各自 GPL 许可提供。没有把 GPL 实现复制为 MIT 源码。
