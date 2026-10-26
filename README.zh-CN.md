# RustUSB

E61新增[ARM64完整冷安装验收](docs/research/arm64-installed-validation.md)：Ubuntu22.04/24.04实际aarch64来宾均通过安装、普通用户GUI关闭、真实fsync升级等待、重启、remove/purge与保留状态；四QEMU0、无forced，均42不同断言。软件1114/731输入与cold锁clean76b4；匹配原生CI0316包及同源控制字段升版单独记账。ARM USB数据I/O、真实硬件和完整阶段仍未验收。

此前E58的[Ubuntu 预览安装包与 systemd 指南](docs/debian-installation.md)：基础 CLI/TUI/helper/非 root agent 与可选 GUI 分包，默认不启服务/不授设备。clean fded558a 完整 root1076+GUI18/451输入通过；Ubuntu22.04/24.04 amd64 两项官方原镜像完整安装、升级、真重启、remove/purge 与原生GUI WMclose0通过，4 QEMU0、无forced，见[验收](docs/research/debian-installation-validation.md)。五原失败、两开发续跑分账保留；真实硬件、arm64安装态及整个Phase5仍未完成。

新增本地只读`devices --watch`与共享观察库：clean c0346d6完整872次软件/206稳定输入，clean f66da6b默认及all各一个Linux QEMU guest均通过（实际UID2000内核事件、driver变化与同端口新代际），两个构建共六个真实进程信号/输出用例通过。见[英文使用指南](docs/local-device-monitor.md)和[验收](docs/research/local-device-monitor-validation.md)。这两项不计为USB/IP双端转发或真实硬件通过，旧14paired保持原E40版本与范围。

[English](README.md) · 中文

基于 Linux USB/IP 的远程 **USB 设备级重定向**工具，Rust 管理设备、权限和连接，Linux 内核执行真实 USB 请求转发。

当前为**分阶段开发预览**：Linux Native 管理、可选 Rust 服务和受限 helper 已实现；阶段状态和实际测试结果见 [工作日志](docs/work-log.md)。真实物理设备兼容性尚未验证，Windows、完整GUI/安装、存储安全共享及完整安全验收仍在 [开发计划](docs/roadmap.md) 中。发现某类设备不代表已验证它可正确重定向。

可选[安全 broker 模式](docs/secure-guide.md)以双向 TLS 1.3 承载目录、IMPORT及后续URB；[应用服务](docs/application-service-guide.md)独立持有job，按OS UID授权profile/action并提供连接定向停止与有界持久audit。[数据连接观察](docs/data-link-health.md)在显式schema2下提供同一TLS数据连接的probe/RTT，响应不等于USB进展或设备释放。

新增[有界文件日志与读取](docs/logging.md)支持私有typed NDJSON、按大小/文件数轮转和profile授权页面，日志失败与USB操作/业务audit独立。[专项验收](docs/research/rotating-logs-validation.md)记录723次软件执行（308/395/20）、177输入映射、本地新六进程场景与旧四信号/两背压；[精确89ef四原生CI](https://github.com/tiansongyu/usbip-client/actions/runs/40053603593)各项通过，原41fce缺cache前置FAIL保留。新增installed日志仿真在clean `48c9794` 的一个双端场景/三epoch通过（2guest0），核权限/轮转/真实HID与FTDI I/O及logger锁未放前的原USB清理；首44b1验证器期望FAIL保留，实际State/Protocol与cleanup独立记录。

此前[准备记录停止验收](docs/research/prepare-recording-shutdown-validation.md)的clean `1582f06` 二十三QEMU/46guest0继续归其原CLI/helper：八prepare、六health含18方向与九旧回归分账。应用记录ACK停顿不阻挡已收到owner先清理，但永久FS/kernel阻塞仍不承诺有界退出。此前[数据链](docs/research/data-link-health-validation.md)、[共享owner](docs/research/shared-owner-validation.md)与旧产品/采证失败均保持版本和原件；真实硬件与完整Phase3仍未完成。

可选 Rust 用户态服务端已实现固定虚拟 HID/CDC ACM，通过 `rust-backend` feature 显式启用，见[虚拟服务指南](docs/rust-virtual-server.md)与干净cfeffd3的[双QEMU实际功能测试](docs/research/userspace-qemu.md)。

实验性 `physical-nusb` 后端通过受限 helper 的 FD 授权，在普通用户进程中使用 Linux USBFS，见[物理服务指南](docs/rust-physical-server.md)。最新[checked release 修复与验收](docs/research/usbfs-checked-release.md)保留真实释放错误，失败后不执行配置切换；同一干净 `6e809a1` 的 11 项 QEMU 全部通过，含 8 个取消/断线场景、ECM 与 HID/FTDI 正常/热拔回归，共113项协议检查、22个guest正常退出。完整软件检查及37c2d61的[四平台CI](docs/evidence/publication-native-fixture-20261023.json)每格222项通过。该历史矩阵未覆盖认证/TLS；后续实际安全通路见上方新验收。真实硬件、内核内部挂起、ISO与完整Phase2仍未完成。

可选 `virtual-server` / `physical-server` 已加入 Unix `SIGINT` / `SIGTERM` 合作停止，见[信号停止报告](docs/research/server-signal-shutdown.md)。修复 `d39517b` 在干净 `934358f` 的222次软件测试、四项本地信号用例和四项QEMU回归通过，8个guest正常退出；各构建feature和ELF来源分别记录。四平台[CI39899971271](docs/evidence/publication-server-signals-934358f-20261023.json)已通过，每格222次测试执行、原两项背压及新增四种信号场景均通过。该改动不提供SIGKILL、崩溃或持久重启恢复；真实设备G1/G2仍未完成。

新增 [SIGKILL/首次重启负向验收](docs/research/physical-crash-recovery.md)在 clean `1342a6a` 完整收集证据，恢复结果为 FAIL：进程退出和客户端断连后，服务端原驱动未恢复；重启会以空驱动建立新基线。新进程的 `cleanup_verified` 不能证明旧会话恢复。该历史失败保持；后续受限自动恢复见下面的新报告。新增[持久授权基线与管理员协调](docs/research/physical-journal-recovery.md)已在 clean `933e720` 验证：未决记录跨 helper 重启保留并拒绝新授权，正常结束可退休，管理员恢复后第三轮真实 HID/FTDI I/O 通过。本轮完整软件250次测试、五项QEMU/十guest、本地四信号和两背压全部通过；四平台[CI39908004666](docs/evidence/publication-journal-933e720-20261023.json)每格250次测试及信号/背压通过。保护与显式协调不等于自动恢复。

新增[明确授权原驱动自动恢复](docs/research/physical-driver-autorestore.md)：产品e34e309，clean2ab9976两个产品正向与五个原QEMU回归通过，14guest0；软件268次及本地信号/背压通过。默认关闭、旧记录manual、仅同配置/alternate原driver CONNECT；随后clean4565f7a的[撤销/旧producer迁移/外部占用三项QEMU边界](docs/research/physical-driver-autorestore-negatives.md)首次PASS/6guest0；真实硬件仍待验收。

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

最新[主机名与双栈回退验收](docs/research/dual-stack-connect-validation.md)：clean `f3bf579` 完整842次软件执行，clean `67d0bb0` 十四paired QEMU/28guest0（六raw主机名、一Native/v2主机名、六默认配置与独立v2诊断回归）。实际UID2000探针确认两个候选，旧连接器在相同修正配置下仍超时，新版本完成原I/O和清理；六历史hostFAIL分开保留。本机4signal/2BP及2CLI查询/超时通过，未把hosts-file测试当成wire-DNS/VPN/性能或真实硬件证明。

新增[私有诊断导出](docs/diagnostics.md)：`rustusb diagnostics --output /受保护的私有目录/result.json --json`，闭合JSON与每次捕获独立别名保留关系，不复制原始标识/路径/秘密。agent模式须同profile Read+Audit，导出已提交视图，health/log独立采样。[专项验收](docs/research/diagnostic-export-validation.md)记录797次软件执行（343/434/20）、192选定source/build输入、本地13组/19child、[d9四原生CI](https://github.com/tiansongyu/usbip-client/actions/runs/40067501829)及clean `d9ebe2e` 一项双guest仿真/一epoch两轮真实HID/FTDI I/O。导出与第二轮I/O均在同一原WAL syscall暂停时完成，随后原资源清理通过；原2ca进程exit101与bde仿真启动FAIL保留。默认四组只属本地x86，不外推CI；硬件与完整G3继续未完成。

新增[installed认证负向与启动pin退休验收](docs/research/secure-negative-qemu-validation.md)：clean `25870c6` 的Native/virtual/USBFS × IPv4/IPv6六paired run全部通过，12guest0；每pair同guest/helper内两credential epoch、四拒绝前后屏障及两轮实际HID/Serial I/O。各44条目标负向client退出记录包含错误证书/用途/名称/信任、缺cert/ALPN、直接未授权IMPORT4/duplicate2及旧pin移除后拒绝；264条进程记录不计USBcases。clean3d7软件797次仍同192源；前四hostFAIL均保留。这是raw-v1合作停止后startup pin退休，不是热吊销/CRL、崩溃恢复或完整G3。

新的[认证准入限速](docs/research/authentication-admission-validation.md)默认初始64次、每125ms补充一次，按已加载TLS配置共享，可由root配置。干净`47fcb75`通过824次软件测试；干净`d619b22`通过13次双QEMU（六项限速、六项默认配置及一项独立v2诊断回归），26个guest退出0。所测限速期间原有HID/串口I/O保持正常；全局配额不承诺按来源公平或完整抗拒绝服务。

此前的[服务端观测版本](docs/research/server-security-observations-validation.md)在干净`c097cf8`通过808次软件测试和七次双QEMU：六组认证拒绝/计数组合，以及独立的framed-v2诊断回归。前台control与`agent server-security PROFILE`提供有界TLS/framing/限额计数；它们按已加载配置累计、服务重启归零，不是持久审计或USB清理证明。

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

示例地址 `192.0.2.1` 仅用于文档/隔离仿真。请使用实际受管地址和扫描得到的Bus ID。所有命令支持 `--json`；默认日志写 stderr，`--log-json` 启用 JSON tracing，`RUST_LOG=info` 配置级别；可选 `--log-directory` 切换到私有文件sink，与 `--log-json` 互斥。普通用户不使用 `sudo rustusb` 代替 helper。

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


原生Linux [GUI预览](docs/gui.md)现已实现，复用CLI/TUI应用服务与headless worker；[真实QEMU窗口证据](docs/research/native-gui-validation.md)覆盖只读热插拔、权限拒绝/原UUID、偏好及宽窄布局。[双栈正向操作](docs/research/gui-native-actions-validation.md)另验GUI确认Start/精确Stop、非空远程目录及原生窗口关闭后的HID/FTDI I/O。[v2 GUI链路验证](docs/research/gui-link-health-validation.md)另验同数据链RTT、故障历史与刷新时的展开/焦点/滚动保持。[固定profile自动规则](docs/automatic-sharing.md)已接CLI/服务及Native本地双栈QEMU；[GUI规则编辑](docs/research/gui-automatic-rules-validation.md)已验双栈固定profile CRUD、真实IPC超时/renderer reload/原UUID查询与独立job；[显式settled回执](docs/research/settled-rule-decisions-validation.md)另验真实CAS拒绝、超时/reload/服务重启后原查询及原草稿恢复；[跨进程请求恢复](docs/frontend-recovery.md)已保存确认过的Start/规则原UUID、协议/payload与最后已发草稿，仅显式原查询、不自动重发；[双栈实际进程崩溃验收](docs/research/frontend-intent-recovery-validation.md)保留完整旧GUI/TUI回归。未确认表单、断电/安装态重启、动态pool、物理远端自动导入及完整GUI/安装/硬件仍未完成。英文README保留产品入口与真实截图。
