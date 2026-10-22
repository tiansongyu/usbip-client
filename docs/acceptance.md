# 需求逐项验收审计

审计日期：2026-10-22。依据为 [原始完整需求](requirements.md)、当前源码、持久测试输出和 Git 实况；不是按已实现部分重新定义目标。本文区分交付产物、源码行为、软件测试、仿真和真实硬件，后续阶段仍保留原范围。重要结论是：**Phase 0 研究与设计产物已交付，Phase 1 有可编译的软件实现和自动测试证据，本项目七项Native双QEMU矩阵在干净b4cbfc3全部通过，新增三机争用与主动TCP中止在干净b487144通过；可选Rust固定虚拟HID/CDC在干净cfeffd3的双QEMU实际功能和清理通过；双Ubuntu真实设备、安全远程产品及长期功能仍未完成。**

状态约定：`已交付` 表示文档 / 源码存在且已核对；`软件已验证` 表示适用自动测试有结果；`部分` 表示功能或证据尚不覆盖原要求；`待验收` 表示有实现或计划但缺必需运行证据；`后续阶段` 表示明确尚未实现。文档存在、测试通过和产品支持互不替代。

## 1. 审计证据与适用版本

| 编号 | 权威证据 | 它能够证明什么 / 限制 |
| --- | --- | --- |
| E01 | [Linux 源码审计](research/linux-usbip-audit.md)；主 commit `7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614`，补核 v5.15 / v6.8 | 工具、内核、协议和许可证的固定源码事实；不证明 Ubuntu 维护内核或真实设备兼容 |
| E02 | [Rust 上游审计](research/rust-backend-audit.md)；commit `61938f8c48ed01c508faaacfa7cb3fc3810b5ae0`；[运行摘要](evidence/rust-upstream-2026-10-22.json) | 上游 30 项测试通过、模拟 HID / CDC QEMU 结果、6 项缺陷复现；不是本项目 Rust 后端通过 |
| E03 | [历史Native最终软件输出](evidence/software-checks-phase1-runtime-2026-10-22.txt) 与 [检查脚本](../scripts/check.sh)；[历史41项输出](evidence/software-checks-final-2026-10-22.txt) | fmt、Clippy、59项测试、release、Rustdoc全部通过；产品提交a4d258c、冻结QEMU基线b4cbfc3；首次失败及夹具ETXTBSY证据保留 |
| E04 | [DEVLIST fuzz 摘要](evidence/fuzz-devlist-2026-10-22.json)、[target](../fuzz/fuzz_targets/devlist.rs)、[脚本](../scripts/fuzz-smoke.sh) | ASan 3,412,047 次 / 21 秒未观察到崩溃；不证明穷尽安全、IPC / 内核 URB 或性能 |
| E05 | [工作日志](work-log.md)、Git `log / status / remote / ls-remote` | 过程、失败、修复、提交与远端事实；工作日志不能覆盖与实际命令相反的结论 |
| E06 | [本项目双 QEMU 脚本](../scripts/qemu/run-native-qemu.py)、[来宾脚本](../scripts/qemu/guest-init.sh)、[首次基线PASS](evidence/qemu-native-20261022T101910Z/summary.json) 与历史 FAIL 记录 | 首次干净提交97c2914完整PASS；这是历史HID基线，后续矩阵以E07为准；历史FAIL保留 |
| E07 | [本轮Native互操作/重连矩阵](research/native-qemu-matrix.md)，七个summary及双端原始日志；[旧bug证据](evidence/qemu-reconnect-old-20261022/summary.json) | 冻结b4cbfc3，七次clean/locked构建，HID与FTDI实际功能、所有权负向、doctor及释放验证，14guest exit0；不覆盖CDC/真实硬件/WAN |
| E08 | [原生架构CI证据](evidence/ci-native-matrix-2026-10-22.json)，[实际运行](https://github.com/tiansongyu/usbip-client/actions/runs/39768009618) | bf08652的Ubuntu22.04/24.04 × x86_64/aarch64四job全部success，各59项/0失败/0忽略，uname及Rust host证明原生架构；不证明USB内核/物理功能 |
| E09 | [三guest争用](research/native-qemu-contention.md)、[主动TCP中止](research/native-qemu-transport-abort.md)，各自最终summary和原始日志、[聚合索引](evidence/qemu-native-contention-transport-20261022.json) | 干净b487144/默认locked构建，5guest exit0；busy wire+原owner持续输入+handoff，两次SOCK_DESTROY后的NULL/旧代际消失和直接重连；不证明黑洞/自动恢复/完整调度 |
| E10 | [虚拟Rust服务QEMU](research/userspace-qemu.md)、[最终索引](evidence/virtual-validation-final-20261022.json)、[软件检查](evidence/software-checks-virtual-20261022.txt) | cfeffd3 clean/locked，两guest exit0、两设备并发/两周期evdev与CDC实际I/O/busy/UNLINK/畸形/清理；默认75/allfeatures76tests及全部Cargo gate通过；两个虚拟profile不代替物理/ISO/完整G2 |
| E11 | [最终server parser fuzz](evidence/server-fuzz-final-20261022.json)、[原始输出](evidence/server-fuzz-final-20261022.txt)、[stdout背压最终回归](evidence/virtual-backpressure-final-20261022.json)与保留原FAIL | 同一clean cfeffd3，ASan3,615,249runs/31秒无观测崩溃；满stdout不drain仍SIGINT/5秒deadline释放lease；不是全部调度、物理、任意stderr或安全证明 |
| E12 | [可选后端出版/CI证据](evidence/publication-virtual-20261022.json)、[实际运行39774177011](https://github.com/tiansongyu/usbip-client/actions/runs/39774177011) | cfeffd3已普通push/remote一致；四原生OS/架构job全部success，每格默认75/allfeatures76、0失败/忽略、两项stdout背压回归；不代替ARM内核USB或物理验证 |
| E13 | [USBFS物理adapter专项报告](research/physical-qemu.md)、[软件与vendor检查](evidence/physical-software-validation-20261022.json) | 默认89/allfeatures103及全部Cargo gates、vendor14纯测试通过；普通UID/root0600授权、HID/FTDI实际USBFS正常两周期与热拔开发场景通过，全部开发FAIL保留；最终clean验证另行登记，不替代真实硬件 |

当前本机软件环境为 Ubuntu 22.04.5 x86_64、内核 6.8.0-106-generic、稳定 Rust / Cargo 1.97.1。Ubuntu22.04/24.04 × x86_64/aarch64四格原生软件CI均已通过，当前可选后端四格结果见E12；真实硬件、Ubuntu24.04内核USB互操作及aarch64 USB内核路径仍未验证。

## 2. Phase 0 九项产物

| 原要求产物 | 当前具体证据 | 判定与剩余边界 |
| --- | --- | --- |
| 功能差距分析 | [gap-analysis](research/gap-analysis.md)，对比两后端、USB 类、取消、存储、安全和产品能力 | 已交付；不能把“采用路线”视为已实现能力 |
| 可复用源码清单 | gap-analysis 的复用成本；两份上游审计的复用章节 | 已交付；Native 使用外部工具 / 模块；jiegec MIT HID描述符选择性复用并保留版权；其网络/物理crate未直接依赖 |
| 协议兼容性报告 | [protocol](protocol.md)、E01 / E02 的 DEVLIST / IMPORT / URB / ISO / UNLINK 与错误语义 | 已交付；Native 的 IMPORT 后数据通路委托 Linux；可选固定虚拟服务已独立实现有界SUBMIT/UNLINK，见E10 |
| 许可证分析 | [third-party](third-party.md)、[依赖清单](dependency-inventory.json)、[锁文件](../Cargo.lock)、上游逐文件许可说明 | 已交付工程分析；不等同最终发布合规审查或已满足捆绑 GPL / LGPL 义务 |
| USB 设备支持矩阵 | [compatibility](compatibility.md)、测试手册的逐型号字段 | 已交付；真实设备各类仍为未执行，存储拒绝不能改成支持 |
| 技术架构 | [architecture](architecture.md)，两条数据路径、7 crate 依赖、普通用户与 root 边界 | 已交付；选用 Native，不将用户态后端与 usbip_host 叠加 |
| 具体接口设计 | architecture 契约；[core](../crates/rustusb-core/src/lib.rs) 的可编译 trait / 类型；[daemon](../crates/rustusb-daemon/src/lib.rs) 的 Request / Response | 已交付起步接口；完整取消令牌、事件、持久会话和恢复协调未实装，见第 5 节 |
| 安全威胁模型 | [security](security.md) 信任边界、攻击路径、权限、输入、TOCTOU、存储、网络、状态、密钥、日志、fuzz、S1–S6 | 已交付设计；没有把所有规划控制声明为已实现 / 已实测 |
| 详细开发计划 | [roadmap](roadmap.md) Phase 0–7、G0–G7、独立 G1-Q、共同完成门槛；[testing](testing.md) | 已交付；后续阶段 gate 未被当前软件检查替代 |

指定 Linux 范围已核对 `tools/usb/usbip`、`drivers/usb/usbip`、协议文档与相关 UAPI，分别说明用户态和内核角色。指定 jiegec 范围覆盖 `lib / protocol / host / device / interface / endpoint / setup`、examples、QEMU、两种物理设备路线、全部 transfer types、Composite、alternate settings、USB 错误、panic 风险、长度、热插拔、独占与阻塞。源码判断与实际 probe 的等级在 E01 / E02 中分别标明。

## 3. “现在开始执行”八项要求

| 原文第 12 节 | 当前证据与判定 |
| --- | --- |
| 1. 调研两个指定项目的具体实现 | E01 / E02：已交付且有实际源码与上游动态实验；未只读 README |
| 2. 检查代码架构与协议兼容性 | 两份审计、protocol、gap-analysis：已交付，明确上游 Rust ISO / alt / 错误与清理缺口 |
| 3. 分析最大化复用 jiegec | Rust 审计“复用路径”与 R01–R12 gate：已交付；不在未修复时直接用于默认物理设备服务 |
| 4. 明确 Native / Rust 边界 | architecture、core、gap-analysis：已交付；Native 是内核数据通路，Rust 后端为独立后续路线 |
| 5. 现实可实现的 Rust 架构 | 7 个有实际职责的 workspace crate 和公开 API：已交付；没有空 GUI / TUI / Rust-backend crate 冒充实现 |
| 6. 完整开发计划及验收标准 | roadmap / testing / security：已交付，保留所有阶段、物理 gate 与用户追加仿真要求 |
| 7. 架构文档、项目规范和 AI Skill | [AGENTS](../AGENTS.md)、[项目 Skill](../.agents/skills/rustusb-development/SKILL.md) 及 docs：已交付；通过显式入口避免假定全部 Agent 自动发现相同路径 |
| 8. 完成调研后开始 Phase 1 | 已创建 Native workspace / CLI / helper / tests 并有软件检查；属于“已开始并形成可测试实现”，不能等同 Phase 1 全 gate 完成 |

## 4. Phase 1 功能和 gate

| Phase 1 原要求 | 实现依据 | 当前证据、状态与缺口 |
| --- | --- | --- |
| Rust Workspace | 根 Cargo.toml、Cargo.lock、rust-toolchain.toml、`crates/` 七模块（CLI依赖Rust后端为feature opt-in） | 软件已验证；2024 Edition + 稳定 Rust 1.97.1；其他工具链不在已测范围 |
| 本地设备扫描 | `rustusb-linux` 的 SysfsDiscovery / cached descriptors；`tests/discovery.rs` 八项 | 软件已验证；宿主只读扫描记录 6 台，发现不等于设备支持 |
| Linux 能力检测 | 共享Native/helper诊断与CLI doctor，人类/JSON schema1 | E03/E07通过：固定可信工具有界version、模块loaded/availability/sysfs独立、保护socket/root peer只读Status；exit0是观察完成，不代表VPN、授权或USB I/O已验证 |
| 设备导出 | helper UID / device allowlist / 重新扫描 → Native export → bind 前后确认 | 软件 / fixture 已验证；本项目最终双QEMU gate已通过；未实测物理设备；存储明确拒绝 |
| 远端设备发现 | protocol `discover` / 有界 DEVLIST，CLI `discover` | 软件已验证：真实 loopback TCP、截止时间、畸形 / 截断 / 精确边界；真实网络路径单独验收 |
| 远端设备导入 | root peer 策略及 trusted_network → Native attach_session → VHCI 核验 | 软件 / fixture 已验证；仅唯一已枚举匹配端口认领；本项目最终双QEMU已验证，物理未验证 |
| 设备断开 | helper 受管端口 → native detach_session 比对原身份 → 确认NULL及旧设备generation已移除 | 软件已验证端口复用拒绝 / 幂等 / 真实状态等待；重启未知端口不能由普通 CLI 接管 |
| CLI | clap devices / doctor / discover / export / unexport / attach / detach / sessions / status，JSON 输出 | 已实现并有 schema 测试 / 只读 smoke；peers / logs / config / benchmark 后续，非占位成功 |
| 基础日志 | CLI tracing 文本 / JSON stderr、helper UID / 动作 / 结果 / 安全错误类别、Native 状态核验日志 | 已实现基础部分；审计完整性、轮转、保留策略、指标、诊断包与隐私全面测试后续 |
| G1 软件 gate | E03 四项 Cargo 检查及 Rustdoc | E13默认89/allfeatures103项及两组Clippy/release/Rustdoc通过；历史虚拟与Native结果分别保留，后续变更仍须重新验证 |
| G1-Q 用户要求的本项目 QEMU gate | E07七项矩阵、E09三机争用/双机传输中止，原始日志与完整指纹 | **干净提交b4cbfc3七项全部PASS**；三方向Native互操作各HID/FTDI两周期，reconnect三周期及新代际拒绝控制，14guest exit0；E09另5guest exit0，争用交接与真实TCPabort清理通过；doctor/UID/资源释放逐项核验 |
| G1 两台 Ubuntu 真实设备 gate | 原需求要求真实 USB 设备被客户端内核枚举并完成功能 | **未执行**；无物理测试设备和双机记录，仿真不能代替；Phase 1 不能据当前证据标全完成 |

Linux 模块加载、usbipd 启停、VPN 和防火墙目前由管理员管理。helper 不开放任意命令或服务名 RPC，也没有以模块需求让整个 CLI 默认 root。模块未加载时必须报告缺口；这些部署前提不应在交付说明中隐去。

## 5. 架构接口、状态、取消和所有权的真实程度

| 要求维度 | 已有实现 / 可验证依据 | 未实装或尚不充分的部分 |
| --- | --- | --- |
| 所有权、生命周期、Send / Sync | core 的 DeviceDiscovery、ExportBackend、ImportBackend 均 Send + Sync；future 借用后端；Native Arc 配置 / 共享 mutation 锁；helper Arc + Mutex | 完整 SessionManager / PrivilegedOperations trait 没有按长期方案落为独立抽象；当前 Request + App + Native 承担职责 |
| 类型和错误 | BusId / Host / Peer / VhciPort，类型化 Error / Result；严格 IPC wire；公共 API 文档及 Rustdoc | USBFS raw保留负errno、错误partial/actual与OUT实际长度，纯内存回归及QEMU短请求/真实取消已验证；不能据此推断全部真实设备/ISO；跨前端统一详细错误码体系后续 |
| 异步与资源边界 | sysfs 扫描 spawn_blocking；Tokio 有界网络与 IPC；Native 固定进程超时 / kill / reap / 有界输出 | spawn_blocking 取消不会终止底层系统读取；实际内核阻塞和断网生命周期仍需异常测试 |
| 状态机 | core 列出九种 SessionState 并测试合法转移；Native 从内核观察导出 / 端口状态 | **helper 尚未把九状态接入完整会话状态机**；没有持久状态事件流、心跳、连接健康 / Recovering 调度 |
| 取消 | CLI Ctrl-C 报告取消和“已接受 helper 操作可能继续”；Native future drop 测试表明活动子进程被终止，未声称内核回滚 | 没有 Cancel RPC / 操作令牌 / 取消结果确认；客户端断开不撤销已接受的 helper 操作；不能称完整可取消业务事务 |
| 独占 | 设备级导出由内核占用、helper 变更串行化；只记录本实例成功绑定并复核的 export generation，拒绝接管未知绑定；native 只认领唯一匹配新增端口，detach 再校验原身份 | 锁不能排除管理员同时运行外部 usbip；未证明所有并发 / 多客户端 / 热插拔竞态已闭合；只凭失败操作前后差分无法证明绑定来源时不认领 |
| 清理与恢复 | 幂等 detach / unexport、命令失败后读内核、未知绑定 / 端口 fail-closed；fixture 验证部分成功与端口复用；本地sysfs dev/inode+USB地址receipt阻断新代际继承，attach前仅清理已释放旧记录；E07复现旧bug并验证直接重连及外部资源拒绝 | E09验证主动TCP中止后NULL/旧节点移除、自有记录清理和手动再次导入；没有持久export/port ownership journal，重启后的外部/旧资源只报告并要求管理员协调；失败后出现来源不明绑定仍需人工处理，不满足“所有失败均自动释放”的长期目标；断线自动恢复未实现 |
| 设备身份 | VID/PID + 可用序列号 + 控制器 / 端口拓扑散列，排除动态 Bus ID / address；fixture 验证重枚举 | 无序列号同型号同端口替换仍可能不可区分；不构成设备密码学身份或完整 hotplug 事件机制 |
| 平台边界 | 当前 Native 路径为 Linux；helper 非 Linux 明确 Unsupported；两Ubuntu × x86_64/aarch64原生软件CI通过(E08)，目标矩阵在compatibility | 非Linux全workspace构建、Ubuntu24.04内核USB互操作、aarch64 USB内核/物理设备、Windows驱动未完成 |

这些缺口不是通过补充文档即可变成已实现；后续阶段需要源码、故障测试和系统证据。尤其不能把九状态枚举、取消 future 和保守拒绝错误当作完整恢复服务。

## 6. 原文 4.1–4.11 长期功能追踪

| 原要求 | 当前已做 | 部分 / 后续和必需证明 |
| --- | --- | --- |
| 4.1 全类本地发现与元数据 | VID/PID、manufacturer / product / serial、Bus ID、address、USB version / speed、device / interface 类、active endpoint、驱动和绑定；错误 / blocker 显式 | 当前使用者 / 系统服务忙状态不能全面判定；所有 alternate setting 的完整展示后续；各设备“可发现”不能标“重定向支持” |
| 4.2 共享、取消、远端列表、导入 / 断开、独占、多设备、多客户端、权限、监控 | Native / helper / CLI 具备基本路径和设备 / peer allowlist，内核 / 软件独占 | E09已验证单设备被A持有时B拒绝、释放后B接手；E10已验证两个固定虚拟profile并发；物理多设备、两个首次导入同时竞速、长期多客户端调度、持续监控和完整故障矩阵仍未完成；存储不共享；没有两主机同时写同文件系统的许可 |
| 4.3 热插拔、重枚举、元信息 / 总线变化 | 重新扫描与候选持久 ID、代际复核、重枚举 fixture | 自动事件订阅 / 列表推送及完整会话协调未实现；无序列号歧义依靠明确拓扑策略，非自动安全识别 |
| 4.4 IPv4 / IPv6 / LAN / VPN / 主机名 / 超时 / 心跳 / RTT / 带宽 / 异常 / 多客户端 | Host 支持 IPv4 / IPv6 / DNS，网络总超时；外置安全网络设计；DEVLIST 与真实数据路径区分 | 实际平台与 IPv6 / VPN 矩阵、心跳 / RTT / 带宽 / 内置安全隧道、两条连接加密证据待做；管理 API 不能冒充数据加密 |
| 4.5 九状态、超时、取消、幂等、失败释放、重启恢复 | 领域九状态、各边界截止、部分幂等与命令前后核验，未知资源拒绝自动处理 | 完整应用状态机 / 取消 / 并发 / 重启协调未实现；存储断网后不自动恢复写入 |
| 4.6 存储挂载 / 句柄 / 服务 / 多 LUN / 分区 / 加密检查与安全释放 | 所有当前接口和缓存 inactive config 的存储判定；Phase 1 全面拒绝存储导出 | 尚未实现全面使用者 / 挂载命名空间 / dm / LUN 审计与排他导出；**拒绝存储是安全降级，不是完成存储共享功能** |
| 4.7 普通 UI、系统服务、最小特权层、UDS / OS ACL、严格输入 | 普通 CLI、受限 root helper、SO_PEERCRED、root policy、固定 argv / 可信工具、无 shell、有限报文 / 并发 | 无安装后的 systemd 单元隔离实测、完整 per-user / per-action租约；当前 allowed UID 集合共同管理批准的资源；不开放任意文件或服务控制 |
| 4.8 clap CLI 命令与一致性 | 默认9个实际子命令，可选virtual-server另1个；全局 JSON / socket / timeout / log-json；变更错误非零，doctor诊断结果含失败仍退出0 | peers / logs / config / benchmark 未实现，命令形式已按 Native 实际 port / Bus ID 调整，帮助文档应保持一致 |
| 4.9 Ratatui TUI | 架构共用核心、roadmap G4 | 后续 Phase 4；页面、键盘、搜索 / 排序 / 自动刷新 / 确认 / 错误详情均未实现 |
| 4.10 GUI | 设计推迟到核心稳定，计划评估 Tauri；共用核心原则 | 后续 Phase 5；无 GUI；双端列表、延迟、访问控制、自动规则等未实现 |
| 4.11 tracing / JSON / 文本 / 级别 / 轮转 / 脱敏 / 诊断 / 指标 | 基础 tracing、CLI stderr JSON / 文本与级别；helper 安全类别、UID / 操作 / 结果；禁止原始 USB / 密钥默认日志 | helper JSON 配置、轮转 / 保留、完整脱敏回归、诊断包、活动量 / 字节 / RTT / 吞吐 / 错误指标后续；Native 不可观测指标不得编造 |

## 7. 自动测试、互操作、异常、安全与性能范围

| 原要求测试类别 | 当前证据 | 状态和缺失 |
| --- | --- | --- |
| 单元：序列化 / 反序列化 / 端序 / 描述符 / ID / 状态 / 配置 / 认证 / 错误 / 超时 / 取消 | 历史Native E03：core2、protocol4、scanner8、native28、daemon16、CLI1共59；当前E10加backend16，默认75/allfeatures76项 | 软件快照通过；认证仅本机 UID / policy，非远端认证；描述符非完整用户态 USB 实现；取消 / 状态覆盖仅目前边界 |
| 三方向互操作 | E07：managed / linux-client / linux-server各HID和FTDI | Native三方向实际功能及清理通过；E10另验证自有虚拟Rust Server→RustUSB Native client/VHCI；独立官方CLI专场、物理及完整G2仍待验收 |
| QEMU HID / CDC / 描述符 / 断开 / 重连 / 异常包 | 上游HID/CDC有E02；本项目HID/FTDI、外部释放/同端口新代际有E07 | 七项Native矩阵通过；FTDI不是CDC，E10本项目标准双接口CDC实际I/O/控制与畸形probe已通过；完整网络/热插拔异常矩阵后续 |
| 真实 U 盘 / 串口 / HID / JTAG / 摄像头 | compatibility 与 testing 定义逐设备测试 | 全部未执行；存储拒绝；UVC / Audio / Composite / alt / ISO 各需独立证据 |
| 断网 / 高延迟 / 丢包 / 进程退出 / 拔出 / 客户端退出 / 争用 / 忙 / 权限 / 版本 | 软件fixture覆盖工具失败/timeout/drop/身份复用/busy/UID；protocol版本拒绝；E09真实SOCK_DESTROY与三guest争用 | 已验证两次主动TCP中止和单设备交接；黑洞、延迟/丢包、进程崩溃、USB热拔、同时竞速及完整异常矩阵仍未验证 |
| 未授权 / 非法设备 / 畸形消息 / 超长 / 认证失败 / 重复 / 恶意配置 / 日志 / 特权滥用 | helper strict schema / UID / policy / metadata / 帧边界；DEVLIST 严格解析和 E04 | 尚无远端认证 / TLS / replay gate、全面日志秘密扫描与完整 IPC / 配置 / 描述符 fuzz；未测试项保持未完成 |
| 性能：RTT / 吞吐 / P50 P95 P99 / CPU / 内存 / 并发 / 错误 / 恢复 | testing 与 roadmap G6 定义方案 | **无产品性能实测**；fuzz runs / RSS、QEMU启动时长和上游输入通过都不是 USB 性能基准 |

## 8. 四项 Cargo 与工程质量 gate

| 必需命令 / 约束 | 证据及判定 |
| --- | --- |
| `cargo fmt --all -- --check` | scripts/check.sh 首项，E03 脚本整体通过；成功无输出正常。最终代码修改后需重跑 |
| `cargo clippy --workspace --all-targets -- -D warnings` | E03 `Finished dev` 且整体退出 0；工作区 forbid unsafe / deny unwrap_used / expect_used / panic。它不证明协议与硬件语义正确 |
| `cargo test --workspace` | E10默认75/allfeatures76项通过、0失败/0忽略；E03保留历史59项，各test名称和范围可核对；无硬件通过结论 |
| `cargo build --workspace --release` | E03 release 成功；编译产物不是双机 USB 验收 |
| 公共 API 文档 | `RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps` 已加至 check.sh，E03 通过；不代表所有架构能力均实装 |
| 工具链 / 依赖 / 许可 | 锁定稳定 1.97.1 和 2024；Cargo.lock / inventory / third-party；最终变更后更新清单；未声称完成发布合规认证 |
| 平台 / 资源 / 清理 | 有界输入、明确平台错误、阻塞隔离、固定进程、真实 sysfs 与保守未知资源处理 | 目标所有 OS / 架构及长期异常路径仍需独立 gate，不用 lint 或绿色 CI 覆盖 |

历史Native轮次E03最终59项及全部质量检查通过。E07针对同一源码、干净提交b4cbfc3的七项QEMU矩阵全部PASS；该Native源码及七项脚本保持未变；此后新增四格原生CI(E08)、独立争用/传输中止harness(E09)、有实际职责的可选Rust虚拟服务及其E10/E11验证与四平台E12，并保留原始文档/证据。b487144的四job CI运行39769296031也已completed/success，见后续出版记录。

## 9. 用户追加的过程记录、commit、push 和 QEMU

| 用户要求 | 审计时事实 | 最终必须补足的证据 |
| --- | --- | --- |
| 合适 commit | 历史研究/实现/验收记录保留；本轮a4d258c产品修复与诊断、b4cbfc3互操作/故障脚本和软件证据 | 按产品、测试、验收证据提交；每次普通push后核对远端main，不强推 |
| 记录所有过程 | work-log 保存环境、上游研究、试验、编译 / 测试失败及修复；evidence 保留软件 / fuzz / QEMU 失败日志 | 补最后 QEMU 裁决、最终软件检查、commit 与 push 结果；失败记录保留而非删成“全绿” |
| push 指定仓库 | origin fetch/push均为 `git@github.com:tiansongyu/usbip-client.git`；b4cbfc3普通push exit0，ls-remote与本地一致 | 最终验收证据提交后再次普通push与SHA核对；本轮publication记录实际结果 |
| 使用 QEMU 仿真 | E07七项隔离双guest、UID1000 CLI/root helper、官方工具三方向、QMP HID/Unix chardev FTDI | 同一干净b4cbfc3、七次默认locked构建、14guest exit0、指纹一致；旧版本重连真实FAIL单独保留 |

### 最终裁决追加区（必须由实际运行结果填写）

- 本轮G1-Q七项具体命令、summary、原始双端日志和二进制哈希均见[E07矩阵报告](research/native-qemu-matrix.md)；全部exit0，同一干净b4cbfc3、七次locked构建、14guest exit0。旧21e98f9重连FAIL与修复后的同场景PASS均保留。
- 历史Native轮次最终[软件输出](evidence/software-checks-phase1-runtime-2026-10-22.txt)：59项及fmt/Clippy/release/Rustdoc全部通过；产品a4d258c，QEMU基线b4cbfc3；测试端ETXTBSY排查/修复和20轮稳定性记录另附，生产命令未增重试。
- 历史[出版证据](evidence/publication-2026-10-22.json)保留；本轮commit/push、远端SHA核对与两Ubuntu CI结果见[本轮出版证据](evidence/publication-runtime-2026-10-22.json)。
- 两台真实 Ubuntu 与真实 USB 设备 G1：**未执行，不能因 QEMU 通过改为已完成**。

## 10. 长期阶段仍保持完整

Phase2已交付并验证固定虚拟服务子步骤(E10/E11)；物理设备、ISO/通用Composite/alternate、上游R01–R12物理/认证缺口及全部互操作仍未完成；Phase 3 的远端身份认证、真正数据加密、设备授权、完整会话恢复与审计；Phase 4 的完整 TUI；Phase 5 的 Tauri GUI、deb、systemd、安装 / 卸载 / 配置与体验；Phase 6 的真实设备兼容性和性能；Phase 7 的 Windows 客户端驱动、签名、安全和系统验证，均依原要求保留。上述未完成部分保持原阶段，不被已完成的研究、Native或两个虚拟profile自动测试/仿真所替代。

本次可证明的交付应以实际最终证据表达为“完成研究设计并建立有安全边界的 Native 起步实现，报告已通过与未通过的 gate”。在存储、多类设备、完整恢复、加密、UI、安装、性能和平台验证未完成前，不能称为已可日常使用的完整高质量远程 USB 产品。

## 11. 可选虚拟服务子步骤最终裁决（2026-10-22）

- E10在干净cfeffd3的两guest默认locked构建通过；服务普通UID1000、无usbip_host/虚拟QEMU USB，客户端真实usbhid/cdc_acm；两并发设备、两周期实际I/O及清理有原始日志和SHA。固定两个Full-Speed profile，不外推物理类别兼容。
- E11最终ASan parser fuzz 3,615,249runs/31秒、cov562/ft718、无观测崩溃；不称为穷尽安全。stdout背压原FAIL与修复后SIGINT/5秒deadline lease释放均保留。
- 软件当前默认75/allfeatures76tests、无失败/忽略，fmt/两组Clippy/release/严格rustdoc通过。Native数据通路和旧九场景脚本未修改，当前生产新增路径为feature opt-in。
- 真实G1仍未执行；G2物理/ISO/通用Composite/alternate、G3认证/TLS/完整恢复、TUI/GUI/安装/性能/Windows保持原完整目标，未标完成。
