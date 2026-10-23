# RustUSB 开发约定

目标：Rust 管理的真实 USB 设备重定向，Linux 优先。任务原文见 `docs/requirements.md`，分阶段执行，不将长期规划标成已完成。

修改 USB/IP 核心、设备操作、协议解析或特权代码前，必须先读取 `.agents/skills/rustusb-development/SKILL.md`，再读相关设计和源码审计。此显式入口确保不依赖特定 Agent 的技能自动发现机制。Codex 项目技能位于 `.agents/skills/`；其他 Agent 应通过本文件显式读取，不假定通用自动加载。

沿用用户级 UI/UX 和技术文档技能选择偏好。当前 Native、Rust 虚拟profile及实验性 USBFS adapter 已有 QEMU 证据；USBFS正常/热拔与原虚拟回归在干净37bc6b4通过；最新配置安全/多接口恢复在干净22d172d完成ECM、HID/FTDI正常及热拔三项回归，host-state syscall-entry取消/EOF六场景在干净c921a27最终通过；checked release修复与8个host-state/ECM/物理正常热拔在同一干净6e809a1的11项QEMU全部通过，113case/22guest0，软件89/114/19共222项及37c2d61四平台CI通过（后者仅Native测试夹具修订，QEMU结果仍锁6e809a1），见`docs/research/usbfs-checked-release.md`；内核内部active窗口及真实设备 G1/G2 仍未完成。物理设计见 `docs/research/rust-physical-backend-design.md`，不将整个 Phase2 标成完成。无 GUI/TUI 视觉实现；技术文档以代码和固定上游版本为依据。

可选Rust服务的Unix SIGINT/SIGTERM合作停止见 `docs/research/server-signal-shutdown.md`：产品修复d39517b，clean934358f软件222项、本地四项与四项QEMU通过；QEMU的虚拟与all-features构建产物分别记账，CI39899971271四原生平台均通过，每格222次测试执行、原两背压及新增四信号场景通过。修改此路径须保留listener前异步prepare取消、取得server后总await cleanup及输出背压语义。重复信号不丢cleanup目前为源码约束；SIGKILL/崩溃、kernel硬阻塞初始化、Windows USB和硬件G1/G2继续未完成。

SIGKILL与首次重启的独立负向见 `docs/research/physical-crash-recovery.md`：clean1342a6a恢复FAIL、收集完整，原driver未恢复且新epoch采用null基线。持久授权基线、合作Finish和root只读协调已实现于b75f14b，clean933e720五项QEMU/十guest及本地四信号/两背压通过，软件250次检查及CI39908004666四原生平台每格250次/信号/背压通过，见`docs/research/physical-journal-recovery.md`。原失败保留；后续opt-in实现见下文，跨配置恢复仍draft。不得把新进程cleanup_verified/资源0当作旧恢复，不得仅dup旧FD或凭PID死亡清记录；信任获授权服务的合作退休不提供FD撤销。

## 每次交付

Phase3前置基线见`docs/research/secure-transport-foundation.md`：当时18项transport/304软件与匿名kernel机制，不重归属后续产品结果。当前安全broker已接Native、virtual和USBFS：clean b83c931六项双guest IPv4/IPv6 QEMU全PASS，12guest0、125输入/ELF guards真；完整软件412及精确f9558fc四平台CI各412通过，见`docs/research/secure-usbip-validation.md`。四个开发FAIL保留。Native USED早于device_add，须保留首次row/目录代际固定和有界初始化等待，不能 blanket retry/覆盖旧receipt；cfg/IF仍是phase状态。修改broker必须保留专用UID/互斥设备池、同TLS管理+URB、helper只交R/自持K、实际blockingguard/cleanup及持久未决不接管。完整G1/G2/G3、真实硬件和后续阶段未完成。

独立恢复句柄机制在clean da764d0双QEMU通过237项检查（原journal184+新增53）、两guest0和恢复后第三轮真实HID/FTDI I/O，见`docs/research/usbfs-guardian-mechanism.md`。guest-only probe证明独立B与dupA/C的占用差异和CONNECT恢复，未修改产品或实现自动恢复；首43d6d43的jq测试脚本FAIL保留。机制PASS不等于跨配置、FD撤销或硬件验收。

显式opt-in原driver恢复已实现于e34e309，clean2ab9976两个产品正向和五个原QEMU回归通过，14guest0、软件90/158/20共268次及本地四信号/两背压通过，CI39921583807四原生平台每格268次/信号/背压通过，见`docs/research/physical-driver-autorestore.md`。默认空许可、旧record manual、私有disk1/2与IPC1分开；仅sameboot/原owner ended/完整同cfg+IF+alt/独立B CONNECT，实际blocking任务持变更锁到核验退休，停止须join真实任务。旧producer迁移、许可撤销、外部C占用已在clean4565f7a三QEMU首次PASS/6guest0（318/293/313 host检查），见`docs/research/physical-driver-autorestore-negatives.md`。L始终manual并人工尾部；P/C在明确重启后产品恢复，C释放后同实例不重试。CI39928383971四原生平台每格268次/两背压/四信号通过，精确归属4565f7a。产品111输入/ELF保持；通用恢复/FD撤销、ISO/跨cfg/alt及完整Phase2仍未完成，该历史交付后的安全设计与产品接入结果见上方Phase3新基线。

共享非root应用服务见`docs/research/application-service-design.md`、`docs/application-service-guide.md`与`docs/research/application-service-validation.md`：产品在2a0f8d7，eaa1936软件495/四原生平台CI及六前台/两Native agent QEMU通过；cb08672只增强actual TLS配置/public DER/pin/权限采证，两Native双栈新clean通过，产品150源/ELF仍等eaa。helper tracked Native disk2/physical disk3保留精确terminal；managed IMPORT须持久admission后claim，stop请求不等于cleanup、softdeadline不得退休实际owner。原前台统一提取、无独立helper proof的Rust崩溃session协调、raw-v1 heartbeat/RTT与日志轮转仍未完成，不宣告完整Phase3。修改这些路径先读上述契约，保留operation/cleanup分离及Root kind/correlation/key/UID/resource全匹配。

英文README为产品入口，中文README保留详细概览，Native操作/诊断见`docs/native-guide.md`。静态项目展示站在`website/`，维护和GitHub Pages发布见`docs/website.md`；它不是Phase5 GUI，也不扩大设备/平台/安全验收范围。README与展示站任务可使用用户全局`readme-site` skill。

- 先检查当前文件、Git 状态与 `docs/work-log.md`，不覆盖其他贡献者工作。
- 设计和范围变化先更新文档，再修改代码。记录来源、验证命令、结果与未完成的硬件验证。
- 稳定 Rust、2024 Edition；禁止核心路径 `unsafe`、`unwrap()`、`expect()`、`panic!()`。公共 API 写文档，错误保留上下文。
- 完成阶段执行 `cargo fmt --all -- --check`、`cargo clippy --workspace --all-targets -- -D warnings`、`cargo test --workspace`、`cargo build --workspace --release`。
- 按有意义的研究/实现/修复单元提交。用户已授权提交并最终推送到 `git@github.com:tiansongyu/usbip-client.git`。不强推、不删除远端历史。
- 保留需求逐项验收证据；缺少真实两端 USB 枚举不能宣告 Phase 1 验收通过。
