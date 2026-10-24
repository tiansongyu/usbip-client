# 分阶段开发与验收计划

本计划以真实 Linux USB 设备级重定向为目标。阶段严格按证据推进：文档、编译、模拟测试、真实设备枚举和日常可靠使用是不同完成条件。所有未附运行记录的条目都属于**规划 / 未验证**；不会因为创建目录、实现命令入口或依赖具有某项能力而自动完成阶段。

## 1. 第一阶段技术边界

首期目标平台是 Ubuntu 22.04 / 24.04、x86_64 / aarch64。Linux Native 优先复用 `usbip_host`、`vhci_hcd`、`usbip` 和 `usbipd`，Rust 管理设备发现、策略、生命周期与诊断，不重写成熟内核数据通路。Rust 后端走用户态 USB 设备访问和独立 USB/IP 服务路线，不能与 usbip_host 叠加成同一设备数据路径。客户端首先使用 Linux VHCI；Windows 单独研究。

CLI 和应用核心普通权限运行，Unix socket root helper 只执行固定且设备级授权的操作。Phase 1 依赖管理员提供的可信 VPN / 受管通道，不把管理 API 加密当成 USB 数据加密。存储设备默认拒绝导出；完成排他、安全释放和保守恢复机制后才能扩大范围。Phase 0 / 1 不实现 GUI，不创建无实际职责的空 crate。

## 2. 需求追踪与证据格式

每项验收记录须包含代码 commit、日期、执行者、OS / 内核、架构、Rust 与原生工具版本、设备身份（脱敏）、后端、网络路径、命令、退出码、日志 / 抓包位置、预期与实际、清理结果及遗留问题。记录中分别使用 `已通过`、`失败`、`未执行`、`不适用（含理由）`；不能把未执行写成通过。

发现矩阵必须与支持矩阵分开。发现某个 USB 类、列出描述符或看到已有驱动，只证明枚举和识别；支持声明还要求真实重定向、设备功能、异常处理和对应设备实例的证据。每次修改更新工作记录，重要技术结论关联上游版本、实际文件和测试，源码参考和许可证清单不可仅引用 README。

## 3. Phase 0：源码调研和设计

交付物：

1. 固定 Linux 与 `jiegec/usbip` 上游 commit，审查用户态工具、内核驱动和协议文档，记录源码位置与差距。
2. 分析 Rust 上游协议范围、Tokio 会话、rusb / nusb 路线、虚拟 HID / CDC ACM、QEMU、transfer types、Composite / alternate settings、错误处理、长度界限、阻塞、热插拔及独占恢复。
3. 生成源码复用清单、依赖与许可证清单、协议报告和设备矩阵，不把 GPL 用户态源码无评估地复制到 MIT 代码。
4. 定义架构与可编译 API：所有权、异步 / Send / Sync、错误、取消、状态事件、平台边界，详见架构文档。
5. 安全威胁模型、阶段 gate、测试操作手册、项目 `AGENTS.md` 和实际可发现的开发 Skill。

**G0 gate：** 每项关键技术判断有具体源文件 / 上游文档依据；所有能力标明源码证据、动态测试或未验证；可复用范围与许可证边界明确；Native 与 Rust 路径分离；平台、权限、存储、网络和硬件风险不留隐含默认。未形成这些产物前不以 GUI 或大规模 crate 骨架替代调研。

## 4. Phase 1：最小可运行系统

实现顺序：

1. 建立稳定 Rust 2024 Cargo workspace、锁定依赖和 MSRV；先验证工具链及依赖兼容性。
2. 读取 sysfs / 描述符，发现 VID/PID、名称、序列号、Bus ID、地址、USB 版本 / 速度、类、接口 / 端点、驱动、绑定 / 共享状态以及明确的未知 / 忙状态。
3. `doctor` 检查 OS、内核配置 / 模块、原生工具、helper、权限及受管网络前提；缺依赖时给可操作的错误。
4. 实现 root allowlist、Unix socket helper、参数校验和有界请求；固定工具调用和设备级策略，不自动扩大 root 权限。
5. 完成设备导出 / 取消导出、远端发现、导入、断开、会话与状态查询、基础 `tracing` 日志；返回结构化错误。
6. 设备独占与最小恢复：只管理本工具拥有的资源；明确失败 / 取消 / 拔出时的清理结果；存储和未知高风险接口保守拒绝。
7. 提供 CLI 实际帮助与命令文档。目标命令为 `devices [--json]`、`export`、`unexport`、`discover`、`attach`、`detach`、`sessions`、`status`、`doctor`；`peers`、`logs`、`config`、`benchmark` 在有实际功能时加入，不伪造占位成功。

**G1 软件 gate：** fmt / clippy / test / release build 全通过；设备解析、协议字节序 / 截断 / 上限、ID、配置、错误、超时 / 取消、helper 授权和命令参数均有自动测试；非 Linux 编译路径或平台错误明确；不使用 core `unwrap()` / `expect()` 隐藏外部错误。

**G1-Q 仿真 gate（用户要求，独立验收）：** 用两个隔离的 QEMU Ubuntu / Linux 来宾运行本项目实际 RustUSB CLI、root helper、原生工具和内核模块，通过可控虚拟 USB 设备验证发现 → 授权 export → 远端 discover → attach → 客户端 VHCI 枚举与实际 I/O → detach → unexport。记录来宾内核、QEMU、设备模型、网络、安全边界和日志。上游 `jiegec/usbip` 自带 QEMU 测试、只跑本项目 parser、单个来宾和 mock 都不能替代本项目双来宾全链路 gate。仿真通过不改写真实设备支持矩阵。

**G1 硬件 gate（必须）：** 两台测试 Ubuntu 通过受管安全网络，服务器导出至少一种真实、非存储测试 USB 设备，客户端经 RustUSB 管理的 Native 路径由 Linux 内核枚举，原生驱动完成实际功能操作；验证 detach / unexport、重新连接和清理。记录两端内核、工具、设备及网络路径。单主机、容器、mock、编译或 `usbip list` 通过均不能替代这一条件。

**阶段状态规则：** 可交付“Phase 1 代码和自动检查完成、QEMU 仿真通过、双机硬件验收待执行”（仅在相应证据存在时），但只有 G1 软件与硬件 gate 均通过才称 Phase 1 完成。目标矩阵中未测的 OS / 架构仍标未验证。

## 5. Phase 2：可选 Rust USB/IP 后端

集成经过审查的 `jiegec/usbip`，保留统一发现 / 导出 / 导入 / 会话接口；选择性 feature 启用，不让 Native 默认路径承担实验依赖。实现虚拟设备、物理设备共享和阻塞 USB I/O 隔离，处理取消、热插拔和 exclusive claim 的释放。

当前子步骤：依据[复用决策](research/rust-virtual-backend-design.md)选择性使用MIT HID描述符、自有有界服务/会话引擎，未直接依赖存在已审计缺陷的上游crate。固定虚拟HID和标准CDC在干净cfeffd3双QEMU的真实VHCI/evdev/ttyACM两周期通过，详见[报告](research/userspace-qemu.md)。共享engine下新增固定nusb0.2.8 USBFS adapter、helper精确FD授权、真实终态清理及原驱动核对；干净37bc6b4的QEMU HID/FTDI正常、热拔及原虚拟回归均通过，最终记录见[专项报告](research/physical-qemu.md)。ISO/通用Composite/alternate、真实硬件及完整G2仍未完成。真实G1仍保持未执行，用户选择QEMU没有被改写为物理通过。

最新[Composite配置/恢复子步骤](research/usbfs-composite-qemu-design.md)已实现逐IF重观察及新配置claim/恢复前scanner复核，本机[216次软件测试执行及完整质量gate](evidence/composite-software-validation-20261022.json)和[四平台CI](evidence/publication-composite-20261022.json)通过；ECM模型及原HID/FTDI正常、热拔在同一干净22d172d默认locked构建的[最终三项回归](research/composite-qemu.md)全部通过，6guest正常退出。7次开发FAIL和dirty PASS保留，不外推真实设备支持；kernel内部不可取消ioctl阻塞窗口、无原驱动且非零alternate恢复仍需专门验证。

新增[host-state确定性测试](research/usbfs-host-state-race-design.md)使用仅来宾ptrace门，实际生产worker和nusb抵达syscall-entry后处理UNLINK/EOF；三动作各两模式在同一clean `c921a27`最终六场景通过，48项协议检查、12guest正常退出；开发六PASS与三FAIL另存。它覆盖真实blocking线程入口后的生命周期，不证明kernel USB handler内部阻塞或真实硬件故障；所有开发FAIL保留。

最新[checked release子步骤](research/usbfs-checked-release.md)已修复真实释放errno被portable best-effort契约掩盖的问题；本机及[37c2d61四平台软件](evidence/publication-native-fixture-20261023.json)每格222项通过，同一clean6e809a1的11项QEMU最终PASS，113case/22guest0，旧产品和三轮候选FAIL均保留。不是完整G2完成。

Unix SIGINT/SIGTERM合作停止已在clean934358f通过软件、本地四场景与四项QEMU，见[报告](research/server-signal-shutdown.md)，原旧负向保留。退出异常的首项[QEMU负向](research/physical-crash-recovery.md)已在clean1342a6a完成：SIGKILL后driver未恢复，首次无IMPORT重启采用空驱动新基线，恢复FAIL保持。[持久授权子步骤](research/physical-journal-recovery.md)已在clean933e720完成正常退休、跨helper重启保留原基线/拒绝新授权、管理员恢复后第三轮实际I/O，五QEMU/十guest与本地信号/背压通过、软件250项及[四平台CI39908004666](evidence/publication-journal-933e720-20261023.json)每格250次/信号/背压通过。后续[明确授权原driver恢复](research/physical-driver-autorestore.md)已在clean2ab9976完成两个产品正向及五个原QEMU回归、268次软件与本地信号/背压。后续clean4565f7a已完成[旧schema真实producer迁移、许可撤销、独立C外部占用三产品负向](research/physical-driver-autorestore-negatives.md)；下一优先切片为实际USB数据通道认证/加密设计，保留Phase2未完成功能与硬件gate；跨配置仍draft，不能只保存FD副本或凭PID死亡宣称恢复。范围[复核修正版](evidence/phase2-scope-review-rev2-20261023.json)明确现有ECM cycle2已有官方直接usbip互操作证据，撤回重复测试建议；ISO、无原驱动非零alternate、真正kernel内部挂起、成功clear/真实partial及硬件仍单列未完成。

**G2 gate：** 每个上游局限形成回归项，逐项验证 Control、Bulk、Interrupt、Isochronous、Composite、alternate settings、错误传播、长度上限、panic 路径、hotplug、claim / release 和阻塞 Tokio worker。分别运行 Rust Server → Linux `usbip attach`、Linux `usbipd` → Rust Client、Rust Server → Rust Client。QEMU HID 和 CDC ACM 完成描述符枚举、实际输入 / 串口传输、断开 / 重连和异常包。物理后端至少一类真实设备完成双机功能测试；缺陷未解决的类型明确保持不支持 / 实验状态。

自动恢复的前置[独立句柄机制实验](research/usbfs-guardian-mechanism.md)已在clean da764d0通过：237检查、两guest0、实际旧副本/另一OFD占用拒绝、B恢复原driver及第三轮I/O；产品未改，初次jq脚本FAIL保留。其后E27已完成opt-in同配置/alternate产品正向；其后E28完成旧schema真实producer迁移、许可撤销及外部占用三组合；通用跨配置仍需可信变更事务，下一软件优先为Phase3数据通道安全设计。不把机制或受限正向当成G2完成。

## 6. Phase 3：安全远程连接和恢复

增加身份认证、设备级授权、安全隧道、会话状态机、超时 / 心跳、错误分类、可取消操作、启动协调和审计。支持 IPv4、IPv6、主机名、局域网及 VPN；RTT、字节计数和带宽只在可观测时提供。安全通道与原生数据平面明确分离；内置 TLS 使用成熟库和本地转发代理，处理列表及长连接。

安全broker已接入Native、virtual和USBFS的真实目录/IMPORT/URB路径，使用强制mTLS/TLS1.3/ALPN、leaf-pin principal、稳定device ACL及root可信配置。clean b83c931六项双guest IPv4/IPv6 QEMU全部通过，每项两个I/O/清理/重用周期；软件412次及精确f9558fc四平台CI另记，见[验收报告](research/secure-usbip-validation.md)。旧命令仍需外部安全网络，完整G3的会话状态、恢复、认证负向/轮换/审计等gate继续未完成，不以这些正向验收替代完整阶段。

**G3 gate：** [安全模型](security.md) S1–S6 的适用项全部有证据；无认证、错误 / 过期 / 撤销凭据、非法设备、重放 / 重复连接和并发争用全部拒绝；外部抓包证明 DEVLIST 和 IMPORT 后的 URB 均走加密通道，直连 3240 在 IPv4 / IPv6 被阻断。状态机覆盖 Discovered / Available / Exporting / Connecting / Connected / Disconnecting / Disconnected / Error / Recovering，所有关键路径有超时、取消、幂等清理和重启恢复测试。不能在故障后无条件恢复存储写入。

应用服务增量见[实施契约](research/application-service-design.md)、[英文操作指南](application-service-guide.md)及[E31验收](research/application-service-validation.md)：非root agent直接持有真实job、typed会话事实和Root tracked proof，managed admission、独立连接stop与有界原子audit已实现；软件196/279/20共495次、四原生CI及两Native agent双栈/六前台回归通过，版本和补强采证分别记账。后续[E32共享owner](research/shared-owner-validation.md)以匹配2638a8c的输入完成530软件、在clean b45c2e8完成九项QEMU/18guest0，消除前台owner重复并保留两模式边界。E32时点的raw-v1同链heartbeat/RTT、日志轮转及Rust无独立proof崩溃协调均未完成；后续v2及日志增量见下文，不宣告完整G3。

新增[E33数据链v2](research/data-link-health-validation.md)显式schema2/ALPN2提供同TLS数据连接probe/RTT及独立只读health/epoch历史，626软件与clean531十五QEMU通过（六新18方向、九旧另账）。raw1仍Unavailable，PONG不证明USB进展；短management有界FIN/EOF自然join、actualcleanup独立。此E33增量未完成日志轮转；后续日志见E35，无独立root proof的Rust崩溃协调、生产/证书轮换安全矩阵及真实硬件继续保留。

新增[E34准备记录停止](research/prepare-recording-shutdown-validation.md)使应用journal ACK停顿不阻挡已收到owner的真实清理；Native局部stop保留sibling及同job空Catalog，agent shutdown在persist之外sticky传播。667软件通过，仿真与CI仅按专项报告实测版本记账；accepted initializer始终保留到真实返回，不承诺任意永久FS/kernel阻塞下有界退出，也不完成完整G3。

新增[E35有界进程日志](research/rotating-logs-validation.md)实现私有checked NDJSON轮转/授权页读取，业务commit后镜像audit，实际USB owner清理后才join logger；723软件/177输入、实际本地6/4/2及精确89ef四原生CI通过。clean48c9794一项installed仿真/三epoch通过，首44b1 verifier期望FAIL与原transport error保留，旧23不重归属。有限operational history不等于不可变永久audit；该时点的诊断导出由下述E36补充，年龄保留/完整安全矩阵、无独立proof的Rust崩溃协调与真实硬件继续未完成。

新增[E36私有诊断导出](research/diagnostic-export-validation.md)实现闭合JSON、独立别名与checked私有文件publication；Read+Audit单profile固定已提交视图绕WAL，health/log独立采样。797软件/192选定source-build输入、本地clean835的13组/19child及d9四原生CI通过；clean d9ebe2e一项双端QEMU/一epoch两轮HID/FTDI真实I/O，在同一原WAL门下完成导出与第二轮后才放门，完整原Rootproof/baseline通过。默认四组只属本地x86，原2ca exit101及bde gate启动FAIL保留，旧软件和QEMU不重归属；硬件、永久阻塞与完整G3/后续阶段继续未完成。

新增[E37 installed认证拒绝与启动凭据退休](research/secure-negative-qemu-validation.md)在同一clean25870c6完成Native/virtual/USBFS × IPv4/IPv6六paired run/12guest0，每pair同guest/helper内两credential epoch、四前后拒绝屏障、原I/O/真实server退出/基线恢复后才启动B策略。各44条目标负向client退出记录含server直接IMPORT4/duplicate2及旧pin拒绝，264不是USBcases；clean3d7软件797/192源保持。四原hostFAIL不可重判，仅raw-v1 startup pin退休，不完成热撤销/CRL、认证耗尽、完整生产隔离/审计或G3；原硬件与后续阶段保持。

新增[E40主机名/双栈回退](research/dual-stack-connect-validation.md)：clean f3软件842/198输入，clean67d十四paired/28guest0，正确两候选旧版本对照仍超时；四actual DNS工作、64raw/16unique、inline候选及原deadline/TLS身份保持。本机4signal/2BP/2CLI及所有历史FAIL分账；wire-DNS/VPN/性能、硬件与完整G3继续未完成，不跳过原Phase4–7。

## 7. Phase 4：完整 TUI

已提供[六页可选普通用户预览](tui.md)，[E45](research/tui-validation.md)记录937软件与两Native双栈/20实际PTY/原I/O清理回归。后续[E46](research/tui-settings-actions-validation.md)补充持久三偏好及Native双栈installed确认操作，950软件/四paired/58真实PTY；UI专项异常/未知启动应答等仍未完成，G4不标通过。之前G1/G2/完整G3门槛保留，不以开发前端重判历史验收。

使用 Ratatui，经统一应用服务展示本地设备、远端设备、当前连接、系统状态、日志和设置。支持键盘快捷键、焦点、搜索、过滤、排序、自动刷新、错误详情和危险操作确认；热插拔 / 会话事件由核心驱动，不直接执行特权命令。加载相关 UI Skill 并沿用已确认视觉规范。

**G4 gate：** 全部页面使用同一核心接口；无 root TUI；键盘完整可达，窄终端 / resize 不崩溃，刷新保留选择；拔插、断网和权限拒绝有明确反馈；确认对话框不能成为 helper 授权替代品；CLI 既有测试保持通过。

## 8. Phase 5：GUI 与安装体验

评估 Tauri 后再实施 GUI，与 CLI / TUI 共用核心；提供双端设备列表、连接 / 断开、搜索、延迟、状态、设备详情、自动共享规则、错误和访问控制。制作 `.deb`、systemd 服务、配置参考、安装 / 卸载脚本、升级与回滚指引。root helper 的部署和策略默认拒绝；卸载不随意解绑不属于本工具的资源。

**G5 gate：** 干净 Ubuntu 安装、升级、卸载、重装和重启验证；普通用户 GUI 不具 root 权限；包文件权限和单元隔离有效；自动共享规则遵守设备代际、权限和存储限制；安装示例能按步骤复现；移除程序后策略、凭据和数据的保留 / 删除行为明确且可验证。

## 9. Phase 6：性能与兼容性

扩大存储、Serial、HID、JTAG / Debug Probe、UVC、USB Audio、网卡、Composite 和其他类设备测试。支持声明精确到设备、固件、接口、USB 速度、后端、OS / 内核和网络条件。优先修复协议语义与资源问题，再优化复制和并发，不为吞吐破坏 URB 时序或错误语义。

**G6 gate：** 基准记录 RTT、吞吐、P50 / P95 / P99 延迟、CPU、RSS、并发数、错误率和恢复表现；同时记录本地直连 / Native 基线、数据集、采样窗口和原始结果。存储测试覆盖一致性与保守恢复；Serial / HID / JTAG 测稳定性和延迟；摄像头 / 音频逐项验证带宽、帧时序、Isochronous 与 alternate settings。没有实测结果的指标只列测试目标，不给伪造数值。

## 10. Phase 7：Windows

独立审查 `usbipd-win`、`usbip-win2` 的驱动路线、许可证、维护状态、签名、安装、权限、系统兼容性和安全风险。不能假定 Linux `vhci_hcd` 可在 Windows 运行；原生内核驱动也不因为跨平台 Rust API 自动出现。

**G7 gate：** 明确受支持 Windows 版本 / 架构、驱动来源和签名；独立威胁模型；干净系统安装 / 卸载 / 升级与双机互操作；设备和异常矩阵实际通过；禁止要求用户关闭平台安全机制作为常规安装步骤。完成前 Windows USB 直通保持未实现。

## 11. 每阶段共同完成标准

每阶段必须执行以下真实命令并保留退出码和输出，不能只在文档中列命令：

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo build --workspace --release
```

核心公共 API 有文档；外部输入有长度 / 资源上限；关键业务有有效测试；错误不静默丢弃；异步 worker 不执行长时间阻塞 I/O；资源确定性释放；依赖和许可证清单随变更更新；所有功能注明平台 / 内核限制。过程记录覆盖变更目的、测试、失败原因、修复、遗留项和对应 commit。任何未通过的硬件、安全或互操作 gate 都不能被绿色 CI 覆盖。


服务端观测增量E38见[验收](research/server-security-observations-validation.md)：clean c097cf8软件808/194稳定输入，六raw认证计数paired与一独立Native-v4/v2诊断回归通过，共14guest0。前台及agent精确Read接口已实现，TLS/framing/outer限额按loaded配置累计；volatile计数不代替持久安全审计、设备释放或完整G3，安装态agent counter/v2负向counter仍待验收。原Phase0–7与TUI/GUI/真实硬件范围不变。

认证准入增量E39见[验收](research/authentication-admission-validation.md)：clean47f软件824/196稳定输入，clean d619六限速/六默认/独立v2诊断共13paired与26guest0。默认64/125ms、root可选严格策略、loaded配置共享、独立Read观测已实现。按来源公平、长期DoS/性能、安装态agent/v2专项耗尽、硬件与完整G3仍未验收；不越过原Phase4–7。

本地设备观察E41见[验收](research/local-device-monitor-validation.md)：普通用户共用发现库、kernel触发与周期协调、只读代际/完整快照及signal总join已实现；872软件/206源锁c034，默认/all两单guest锁f66，六新进程与原4/2通过。不是两端USB/IP新矩阵、远程自动共享、全量事件历史、硬件或完整G3，后续TUI/GUI继续共用同库，原Phase0–7 gate不缩减。

托管目录隔离E42见[验收](research/catalog-supervisor-validation.md)：clean b675626完整882软件/220输入、两个Native managed双栈QEMU/4guest0与原4signal2BP通过。网络不占session监督器，两实际任务至真实close/join，授权和成功audit契约不变；v2保持FIN只属loopback，真实WAL/DNS硬阻塞、硬件/完整G3及原Phase4–7仍保留。

远程目录观察E43见[验收](research/remote-catalog-watch-validation.md)：要求4.3的既有认证可见目录自动刷新已增量实现，common service供两watch入口；clean ec91软件903/223源、clean633两个Native paired双栈QEMU/4guest0与旧4/2/3通过，保留首软件及两采证FAIL。busy与ACL/export可见性不同于物理热拔；v2/地址变化只属loopback，不据此完成全部4.3、硬件/G3或Phase4–7。

离线CRL E44见[验收](research/offline-crl-validation.md)：两角色/两TLS schema支持有界完整列表、全链实际撤销/unknown-deny和每次新证书验证的共同时间窗口。clean fcb软件927/226；clean e441六CRL/六默认/一v2诊断共13paired/26guest0，旧47IPv6 FAIL和开发compile失败保留。静态新握手撤销不等于已建立连接热撤销/OCSP/在线下载/CRL序号抗回滚；agent全实例重启才重载，v2诊断不冒称安装态CRL负向。硬件G1/G2/完整G3/后续Phase4–7不变。
