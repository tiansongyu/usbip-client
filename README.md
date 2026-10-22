# RustUSB

基于 Linux USB/IP 的远程 **USB 设备级重定向**工具，Rust 管理设备、权限和连接，Linux 内核执行真实 USB 请求转发。

当前为 **Phase 1 开发预览**：已完成源码调研并实现 Native 路线；阶段状态和实际测试结果见 [工作日志](docs/work-log.md)。真实物理设备兼容性尚未验证，Windows、内建 TLS、TUI、GUI、存储安全共享仍在 [开发计划](docs/roadmap.md) 中。发现某类设备不代表已验证它可正确重定向。

可选 Rust 用户态服务端已实现固定虚拟 HID/CDC ACM，通过 `rust-backend` feature 显式启用，见[虚拟服务指南](docs/rust-virtual-server.md)与干净cfeffd3的[双QEMU实际功能测试](docs/research/userspace-qemu.md)。

实验性 `physical-nusb` 后端通过受限 helper 的 FD 授权，在普通用户进程中使用真实 Linux USBFS，见[物理服务指南](docs/rust-physical-server.md)。干净提交37bc6b4的QEMU HID/FTDI正常、热拔及原虚拟HID/CDC回归全部通过，见[专项报告](docs/research/physical-qemu.md)。最新配置安全与多接口恢复修复的[软件检查](docs/evidence/composite-software-validation-20261022.json)默认89项/all-features113项和vendor14项通过，新的ECM仿真仍在执行。真实物理硬件、ISO、认证和TLS仍未验收，不代表完整Phase2已完成。

历史Native基线：59项软件测试及当轮Cargo质量检查通过；干净提交b4cbfc3的七项双QEMU测试通过，覆盖HID、FTDI双向串口、官方Linux工具三方向互操作、外部释放后重连与新代际保护。见[逐项验收](docs/acceptance.md)与[矩阵报告](docs/research/native-qemu-matrix.md)。

另在干净b487144完成[三机争用与交接](docs/research/native-qemu-contention.md)、[主动TCP中止后清理/重连](docs/research/native-qemu-transport-abort.md)，五个guest均正常退出。Ubuntu22.04/24.04 × x86_64/aarch64四格原生软件CI全部通过；这不替代ARM USB内核路径或真实硬件测试。

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

## 本机诊断

普通用户运行 `rustusb doctor` 查看逐项结果和处理建议；`rustusb doctor --json` 输出供脚本读取的结构化报告，当前 `schema_version` 为 `1`。诊断完成时退出码为 `0`，其中仍可能包含 `missing`、`not_loaded`、`unknown` 或失败结果；退出码不表示整体已经就绪。

各项观察独立报告：

- `native.usbip_host` / `native.vhci_hcd` 分别包含 `loaded`（已加载模块）、`sysfs`（有效驱动接口/状态）和 `availability`（匹配运行内核的模块文件或配置证据）。模块已安装不表示已加载，读取不到也不推断为内核禁用。
- `native.usbip` / `native.usbipd` 校验固定工具路径及其权限后，仅执行有截止和输出上限的 `/usr/bin/usbip version` / `/usr/bin/usbipd --version`。工具版本可用不表示导出/导入成功；某些发行版的 `usbipd` 会拒绝普通用户的版本请求，非零退出的 root 拒绝报告 `requires_root`。仅打印 root 提示但成功返回安全版本时仍为 `available`。
- `helper` 校验受保护的 Unix socket 路径和对端 root 身份，再发送只读 `Status` 请求；socket 文件存在不代表 helper 正常。helper 可响应、VHCI 可读取和管理员的可信网络声明也分别报告。脚本可结合 `required_for_export` / `required_for_import` 查看各角色所需项。

doctor 不加载模块、不启动服务、不绑定 USB，也不建立 USB/IP 会话；不会验证 VPN、TLS、远端可达性或真实 USB 枚举/I/O。完成诊断后仍需两端授权、设备/远端策略和单独的网络与设备测试。

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
   rustusb export 'usb-<64位小写指纹>'

   # 客户端：host和port必须与其helper的策略完全匹配
   rustusb discover 192.0.2.1 --port 3240 --json
   rustusb attach 192.0.2.1 1-2 --port 3240
   rustusb sessions
   rustusb status
   # 用sessions返回的端口替换0
   rustusb detach 0

   # 服务端确认对端已经释放后
   rustusb unexport 'usb-<64位小写指纹>'
   ```

示例地址 `192.0.2.1` 仅用于文档/隔离仿真。请使用实际受管地址和扫描得到的Bus ID。所有命令支持 `--json`，日志写 stderr，`--log-json` 启用 JSON tracing，`RUST_LOG=info` 配置级别。普通用户不使用 `sudo rustusb` 代替 helper。

helper 的所有允许 UID 共同管理已批准设备和本实例建立的连接；这不是多租户隔离。仅能自动断开本运行实例准确认领的端口、取消本实例成功创建并验证的导出绑定。重启后的未知端口和导出、外部工具建立的导出均拒绝自动接管，需管理员核对。设备操作失败、超时或取消不代表没有副作用，也不保证驱动或资源已经恢复；请检查 `devices/sessions/status` 和内核，避免盲目重试。失败后来源不明的绑定不会自动认领或撤销。卸载/重启不自动强制断开未知设备。详见 [安全边界](docs/security.md)。

## 测试与研究

```bash
# 上游源码审计：固定commit，普通测试与对抗性probe
scripts/audit-rust-upstream.sh
# DEVLIST解析器短时ASan fuzz，需要单独安装nightly和cargo-fuzz
scripts/fuzz-smoke.sh
```

QEMU 仿真用于验证本项目导出、发现、导入、Linux 枚举、实际输入、断开与重连；运行方法和结果见 [测试手册](docs/testing.md)。仿真与真实物理 USB 测试分别记录。

```bash
python3 scripts/qemu/run-native-qemu.py
# 官方Linux客户端互操作，改用模拟FTDI串口
python3 scripts/qemu/run-native-qemu.py --scenario linux-client --device ftdi-serial
# 外部释放后直接重连，以及同端口新连接的所有权保护
python3 scripts/qemu/run-native-qemu.py --scenario reconnect
# 独立三guest争用与释放后交接
python3 scripts/qemu/run-contention-qemu.py
# 已建立USB/IP TCP被精确中止后的清理与再次导入
python3 scripts/qemu/run-fault-qemu.py
```

该脚本创建两个一次性x86_64 Linux guest，以私有socketpair网络连接，使用QEMU模拟键盘或FTDI0403:6001串口（不是CDC ACM）。它不会直通宿主USB或在宿主加载USB/IP模块。需要QEMU、BusyBox、cpio、jq、setpriv、当前内核镜像/模块和匹配linux-tools；无法直接读取`/boot`时，仅复制测试内核到临时目录可能需要预先可用的`sudo -n`。完整日志保存在`docs/evidence/`，通过标记不代表真实硬件兼容或内建加密。

- [架构与接口](docs/architecture.md)、[协议](docs/protocol.md)、[源码差距](docs/research/gap-analysis.md)
- [Linux 官方实现审计](docs/research/linux-usbip-audit.md)、[jiegec Rust 实现审计](docs/research/rust-backend-audit.md)
- [许可证与依赖](docs/third-party.md)、[完整需求](docs/requirements.md)、[项目开发规范](AGENTS.md)

RustUSB 自有代码 MIT；外部 Linux 工具/驱动仍按各自 GPL 许可提供。没有把 GPL 实现复制为 MIT 源码。
