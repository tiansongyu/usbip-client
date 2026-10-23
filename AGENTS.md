# RustUSB 开发约定

目标：Rust 管理的真实 USB 设备重定向，Linux 优先。任务原文见 `docs/requirements.md`，分阶段执行，不将长期规划标成已完成。

修改 USB/IP 核心、设备操作、协议解析或特权代码前，必须先读取 `.agents/skills/rustusb-development/SKILL.md`，再读相关设计和源码审计。此显式入口确保不依赖特定 Agent 的技能自动发现机制。Codex 项目技能位于 `.agents/skills/`；其他 Agent 应通过本文件显式读取，不假定通用自动加载。

沿用用户级 UI/UX 和技术文档技能选择偏好。当前 Native、Rust 虚拟profile及实验性 USBFS adapter 已有 QEMU 证据；USBFS正常/热拔与原虚拟回归在干净37bc6b4通过；最新配置安全/多接口恢复在干净22d172d完成ECM、HID/FTDI正常及热拔三项回归，host-state syscall-entry取消/EOF六场景在干净c921a27最终通过；checked release修复与8个host-state/ECM/物理正常热拔在同一干净6e809a1的11项QEMU全部通过，113case/22guest0，软件89/114/19共222项及37c2d61四平台CI通过（后者仅Native测试夹具修订，QEMU结果仍锁6e809a1），见`docs/research/usbfs-checked-release.md`；内核内部active窗口及真实设备 G1/G2 仍未完成。物理设计见 `docs/research/rust-physical-backend-design.md`，不将整个 Phase2 标成完成。无 GUI/TUI 视觉实现；技术文档以代码和固定上游版本为依据。

可选Rust服务的Unix SIGINT/SIGTERM合作停止见 `docs/research/server-signal-shutdown.md`：产品修复d39517b，clean934358f软件222项、本地四项与四项QEMU通过；QEMU的虚拟与all-features构建产物分别记账，CI39899971271四原生平台均通过，每格222次测试执行、原两背压及新增四信号场景通过。修改此路径须保留listener前异步prepare取消、取得server后总await cleanup及输出背压语义。重复信号不丢cleanup目前为源码约束；SIGKILL/崩溃、kernel硬阻塞初始化、Windows USB和硬件G1/G2继续未完成。

SIGKILL与首次重启的独立负向见 `docs/research/physical-crash-recovery.md`：clean1342a6a恢复FAIL、收集完整，原driver未恢复且新epoch采用null基线。跨进程方案仍draft；不得把新进程cleanup_verified/资源0当作旧恢复，不得仅dup旧FD或凭PID死亡清记录。先解决可信原基线、未决授权与正常记录退休/管理员协调，再实现恢复。

## 每次交付

- 先检查当前文件、Git 状态与 `docs/work-log.md`，不覆盖其他贡献者工作。
- 设计和范围变化先更新文档，再修改代码。记录来源、验证命令、结果与未完成的硬件验证。
- 稳定 Rust、2024 Edition；禁止核心路径 `unsafe`、`unwrap()`、`expect()`、`panic!()`。公共 API 写文档，错误保留上下文。
- 完成阶段执行 `cargo fmt --all -- --check`、`cargo clippy --workspace --all-targets -- -D warnings`、`cargo test --workspace`、`cargo build --workspace --release`。
- 按有意义的研究/实现/修复单元提交。用户已授权提交并最终推送到 `git@github.com:tiansongyu/usbip-client.git`。不强推、不删除远端历史。
- 保留需求逐项验收证据；缺少真实两端 USB 枚举不能宣告 Phase 1 验收通过。
