# 需求逐项验收审计

审计日期：2026-10-24。依据为 [原始完整需求](requirements.md)、当前源码、持久测试输出和 Git 实况；不是按已实现部分重新定义目标。本文区分交付产物、源码行为、软件测试、仿真和真实硬件，后续阶段仍保留原范围。重要结论是：**Phase 0 研究与设计产物已交付，Phase 1 有可编译的软件实现和自动测试证据，本项目七项Native双QEMU矩阵在干净b4cbfc3全部通过，新增三机争用与主动TCP中止在干净b487144通过；可选Rust固定虚拟HID/CDC在干净cfeffd3的双QEMU实际功能和清理通过；USBFS adapter正常/热拔及原虚拟回归在干净37bc6b4全部通过；最新配置安全/多接口恢复在干净22d172d完成ECM、HID/FTDI正常和热拔三项最终回归，最新checked release在clean6e809a1的十一项QEMU通过，113case/22guest0，软件及37c2d61四平台CI每格222项通过；新增Unix信号合作停止E23在clean934358f的软件/本地四项/四QEMU通过，CI39899971271四原生平台每格222次测试及两背压/四信号场景通过；E30在clean b83c931完成virtual/USBFS/Native × IPv4/IPv6安全USB/IP六场景、12guest0，完整软件412次及精确f9558fc四平台CI每格412次通过。E31新增非root应用服务/managed admission/定向stop/持久audit，495软件与eaa四平台CI通过，六前台eaa及两Native agent cb08672补采双栈通过；主八场景16guest，原两agent PASS另保留。E32共享前台owner以匹配2638a8c输入完成530软件、在clean b45c2e8完成九QEMU/18guest0，原clean4d单组兼容FAIL保留。E33新增显式v2同数据链probe/RTT与strict健康历史，626软件/165输入映射及clean531e0c3十五QEMU/30guest0通过（六新18方向、九旧另账），e152/934失败原件保留；精确531四原生CI40029976284各626通过。E34准备记录停止以167输入映射完成667软件、在clean1582f06完成二十三QEMU/46guest0（八新prepare、六health与九旧分账），所有旧FAIL保持。双Ubuntu真实硬件、完整Phase3会话审计/恢复及长期功能仍未完成。**

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
| E13 | [USBFS物理adapter专项报告](research/physical-qemu.md)、[软件与vendor检查](evidence/physical-software-validation-20261022.json)、[最终索引](evidence/physical-validation-final-20261022.json) | 默认89/allfeatures103及全部Cargo gates、vendor14纯测试通过；干净37bc6b4默认locked的USBFS正常/热拔与原虚拟回归全部PASS，6guest exit0、普通UID/root0600、0/1/15B/取消/释放/driver恢复/新代际拒绝均有证据；全部开发及f42d最终FAIL保留，不替代真实硬件 |
| E14 | [USBFS出版及四平台CI记录](evidence/publication-physical-20261022.json)、[实际CI39786348508](https://github.com/tiansongyu/usbip-client/actions/runs/39786348508) | 37bc6b4普通push且远端一致，Ubuntu22.04/24.04 × x86_64/aarch64四job全部success；每格默认89/allfeatures103/vendor14合计206项、0失败/忽略及两项stdout背压通过；不是ARM USB内核或真实硬件验收 |
| E15 | [Composite配置/恢复设计](research/usbfs-composite-qemu-design.md)、[软件检查与96份输入SHA](evidence/composite-software-validation-20261022.json) | 192640c产品与软件输入一致；本机默认89/allfeatures113/vendor14共216项及完整Cargo gates通过；逐IF恢复和新配置claim/回滚前安全复核已实现；ECM开发PASS/全部FAIL见[报告](research/composite-qemu.md)，最终clean回归见E17 |
| E16 | [Composite四平台CI记录](evidence/publication-composite-20261022.json)、[实际CI39795249579](https://github.com/tiansongyu/usbip-client/actions/runs/39795249579) | d361ea3与192640c产品源码一致，Ubuntu22.04/24.04 × x86_64/aarch64四job均completed/success；每格89/113/14共216项、0失败/忽略及两项stdout背压通过；不代替USB内核或硬件验收 |
| E17 | [Composite最终报告](research/composite-qemu.md)、[最终三项索引](evidence/composite-final-20261022.json)、[root独立PCAP核对](evidence/composite-capture-final-verification-20261022.json) | 同一干净22d172d、三次默认locked all-features构建、6guest exit0；ECM 19probe和两周期精确38/39B UDP及双driver/config/alt恢复；原HID/FTDI正常与热拔各15probe，host精确70/140B，末pending/queued/inflight/预算0；不证明任意Composite或真实硬件 |
| E18 | [host-state测试设计](research/usbfs-host-state-race-design.md)、[最终报告](research/usbfs-host-state-races.md)、[最终六场景索引](evidence/host-state-final-20261023.json) | 未改生产代码/vendor/旧harness，真实Special blocking TID的syscall-entry门；configuration/alternate/clear × UNLINK/EOF在同一clean c921a27最终六场景PASS，48probe/12guest exit0，开发3FAIL保留，不称kernel内部active或真实硬件通过 |
| E19 | [host-state完整软件gate](evidence/host-state-software-validation-20261023.json)、[冻结提交四平台CI](evidence/publication-host-state-20261023.json)、[实际CI39886203010](https://github.com/tiansongyu/usbip-client/actions/runs/39886203010) | 本机及Ubuntu22.04/24.04 × x86_64/aarch64每格216次测试执行、0失败/忽略；四job实际completed/success，各两项4096B stdout背压通过；源码c921a27，原joblog/API响应已归档，不代替USB内核或真实硬件 |
| E20 | [checked release设计/报告](research/usbfs-checked-release.md)、[软件gate](evidence/checked-release-software-validation-20261023.json)、[最终11项索引](evidence/checked-release-final-v4-20261023.json) | 产品4e81a11修复真实release errno与全部await；软件89/114/19共222项；clean6e809a1串行11PASS/113case/22guest0，真实release-22/后续IF1完成/零SET_CONFIGURATION及恢复；ECM/物理正常热拔通过，旧负向及三轮候选FAIL保留，不称完整G2 |
| E21 | [checked release产品CI](evidence/publication-checked-release-20261023.json)、[测试工具CI](evidence/publication-checked-release-harness-20261023.json)、[实际39894149759](https://github.com/tiansongyu/usbip-client/actions/runs/39894149759) | Ubuntu22.04/24.04 × x86_64/aarch64原生4job均completed/success，每格222项0失败/忽略及两项4096B背压；后续冻结只改物理启动predicate，96软件输入相同，不代替USB内核或真实硬件 |
| E22 | [Native测试夹具修订](research/native-test-fixture-readiness.md)、[完整软件gate](evidence/native-fixture-full-software-validation-20261023.json)、[四平台CI](evidence/publication-native-fixture-20261023.json) | 37c2d61仅cfg(test)修订，生产Native字节相同；本机及四原生CI各222项0失败/忽略、各两背压通过，6e809旧CI3/1原样保留；重建ELF哈希不同，QEMU结果继续锁6e809原输入/产物，未重归属 |
| E23 | [Rust服务信号停止设计](research/server-signal-shutdown-design.md)、[报告](research/server-signal-shutdown.md)、[旧local负向](evidence/server-sigterm-old-local-development-20261023.json)、[旧QEMU负向](evidence/server-sigterm-old-qemu-development-20261023.json)、[修复后软件](evidence/server-signals-software-934358f-20261023/software.json)、[本地四项](evidence/local-server-signals-fixed-934358f-20261023/summary.json)、[QEMU四项](evidence/server-signals-fixed-validation-934358f-20261023.json) | 产品d39517b，clean934358f软件222项、本地4PASS、QEMU4PASS/8guest0；原虚拟与all-features的ELF各自归属，旧TERM -15/wait143及driver未恢复的FAIL保留；CI39899971271四原生平台每格222次测试及两背压/四信号场景通过。仅Unix合作SIGINT/SIGTERM，不证明重复信号、SIGKILL/崩溃、持久重启或硬件G1/G2 |
| E24 | [SIGKILL/首次重启设计](research/physical-crash-recovery-design.md)、[实际报告](research/physical-crash-recovery.md)、[原始证据](evidence/physical-crash-negative-1342a6a-20261023.json) | clean1342a6a唯一QEMU：恢复FAIL/收集完整，runner1/两guest0；55host检查51PASS/4恢复FAIL。旧进程wait137且20.06秒后两driver null；新进程无IMPORT、ready/stopped原driver皆null且cleanup_verified=true，wait0不证明旧基线恢复。产品未改、无自动恢复实现 |
| E25 | [持久授权报告](research/physical-journal-recovery.md)、[软件与源码审查](evidence/physical-journal-implementation-20261023/manifest.json)、[首轮FAIL](evidence/physical-journal-first-failure-e0ac478-20261023/manifest.json)、[最终运行](evidence/physical-journal-final-933e720-20261023/manifest.json) | 产品b75f14b；clean933e720新journal184host检查、原正常/热拔15+15probe、ECM19probe、TERM52host检查全部通过，十guest0、本地四信号和两背压通过。软件default89/allfeatures142/vendor19共250次及完整gate通过；[CI39908004666](evidence/publication-journal-933e720-20261023.json)四原生平台每格250次/两背压/四信号通过。验证未决基线跨helper重启/拒绝重授、正常退休、root只读协调及管理员恢复后第三轮实际I/O；不证明自动恢复/FD撤销/断电或硬件G1/G2 |
| E26 | [独立恢复句柄报告](research/usbfs-guardian-mechanism.md)、[最终原件](evidence/physical-guardian-final-da764d0-20261023/manifest.json)、[首轮FAIL](evidence/physical-guardian-first-failure-43d6d43-20261023/manifest.json) | 产品未改；clean da764d0双guest机制实验237host检查（原184+新增53）通过，真实wait137、dupA延长占用、独立B/C不抢占、B的CONNECT恢复完整原基线、probe wait0、原只读退休与第三轮真实I/O通过。C是同probe另一OFD；不是产品自动恢复/跨配置/任意FD撤销或硬件验收；首43d6d43测试脚本jq错误保留 |
| E27 | [原driver自动恢复报告](research/physical-driver-autorestore.md)、[软件](evidence/physical-autorestore-software-20261023/manifest.json)、[最终原件](evidence/physical-autorestore-final-2ab9976-20261023/manifest.json) | 产品e34e309，clean2ab9976两个产品正向161/205 host检查及五个原QEMU回归全PASS，14guest0；软件90/158/20共268次、本地四信号/两背压通过；[四平台CI39921583807](evidence/publication-autorestore-2ab9976-20261023.json)每格268次/信号/背压通过。同boot owner结束后自主恢复原driver、持久退休与第三轮真实I/O；helper重启活owner仍工作。默认off旧184保持；未完成撤销/旧producer迁移/外部占用负向、跨配置/FD撤销/硬件 |
| E28 | [恢复边界报告](research/physical-driver-autorestore-negatives.md)、[准备](evidence/physical-autorestore-negative-preparation-20261023/manifest.json)、[静态/审查](evidence/physical-autorestore-negative-static-20261023/manifest.json)、[实际原件](evidence/physical-autorestore-negative-final-4565f7a-20261023/manifest.json) | clean4565f7a三产品QEMU边界首次PASS，318/293/313 host检查、6guest0；155当前与109旧输入/旧ELF guard真，268次软件通过；[CI39928383971](evidence/publication-autorestore-negative-4565f7a-20261023.json)四原生平台每格268次/两背压/四信号通过。L真实933e720 helper产disk1不追授，尾部人工恢复+只读协调；P当前许可撤销不恢复，C先claim时每lease失败一次、release后同实例5秒不重试，P/C明确重启后产品恢复和第三真I/O。不是跨配置/FD撤销/硬件或完整G2 |
| E29 | [TLS基础与内核通道报告](research/secure-transport-foundation.md)、[软件](evidence/secure-transport-foundation-20261023/manifest.json)、[首FAIL](evidence/kernel-socketpair-first-failure-f4774a6-20261023/manifest.json)、[修复准备](evidence/kernel-socketpair-export-fix-20261023/manifest.json)、[实际PASS](evidence/kernel-socketpair-final-24a424b-20261023/manifest.json) | 非空mTLS/证书principal/device ACL基础库18项测试、全软件304次及精确f4774a6的[四平台CI](evidence/publication-secure-transport-f4774a6-20261023.json)每格304/背压/信号通过；该历史版本尚未接USB链。clean24a424b单guest真实匿名kernelstub↔VHCI HID/FTDI I/O、每方向35B、50host断言、guest0，109选定输入/ELF guard真，四端显式shutdown后NULL/Gone/AVAILABLE及普通helper原driver恢复。原f4774a6导出后采证FAIL不改判；不证明远端TLS、崩溃/热拔、产品FD API或G1/G2/G3 |
| E30 | [安全USB/IP实施与验收报告](research/secure-usbip-validation.md)、[初始化修复与412次软件门禁](evidence/secure-usbip-native-initialization-fix-20261023/manifest.json)、[最终六场景](evidence/secure-usbip-final-b83c931-20261023/manifest.json) | clean b83c931，virtual/USBFS/Native × IPv4/IPv6六PASS、12guest0；125选定输入/ELF/HEAD/clean guards真。真实mTLS DEVLIST及IMPORT后URB、两周期HID/每向35B serial、双栈3240拒绝、非broker拒绝、合作TERM/重用与完整原driver恢复；独立生命周期492/安全350个审计谓词复核通过（非USB case数）。软件159/233/20共412次及精确f9558fc CI39953971059四原生平台每格412/两背压/四信号通过；四原FAIL保留，不证明硬件G1/G2、ISO、跨配置/崩溃或完整Phase3 |
| E31 | [应用服务验收](research/application-service-validation.md)、[495软件](evidence/application-service-software-20261023/manifest.json)、[eaa八原运行/CI](evidence/application-service-validation-eaa1936-20261023/manifest.json)、[cb Native补采](evidence/application-agent-config-final-cb08672-20261024/manifest.json) | 产品2a0f8d7；eaa软件196/279/20共495、四平台CI39979747581及六前台/两Native agent双栈PASS；cb08672仅增强actual TLS配置/权限采证，两Native再PASS且150产品源/ELF等eaa。主八场景16guest，原两agent限定PASS另留。Native真实owner/定向stop/137精确Rootproof/重启I/O/完整baseline；不证明Rust agent QEMU、全G3、硬件、heartbeat/RTT或轮转 |
| E32 | [共享owner设计](research/shared-owner-design.md)、[英文验收](research/shared-owner-validation.md)、[最终软件](evidence/shared-owner-final-software-20261024/manifest.json)、[原兼容FAIL](evidence/shared-owner-compat-failure-4d700c2-20261024/manifest.json)、[九项QEMU原件](evidence/shared-owner-final-b45c2e8-20261024/manifest.json) | 产品7f007a9/b45c2e8，530软件/39result/152输入逐最终Gitblob；clean b45c2e8九run/18guest0、192总输入保持/同ELF，六安全前台、两Native agent与旧USBFS TERM，独立raw生命周期/安全复核。[CI39999926362](https://github.com/tiansongyu/usbip-client/actions/runs/39999926362)在测试修订2638a8c四原生平台每格530/五TLS/原信号背压全通过，原b45两CI FAIL保留。Foreground无伪WAL，Managed不降级；ACK/EOF/原JSON与信号背压保留。旧4d单组FAIL不重分类；initializer内部、任意WAL停顿有界清理与硬件/fullG3仍未验收 |
| E33 | [v2设计](research/data-link-health-design.md)、[英文操作指南](data-link-health.md)、[英文验收](research/data-link-health-validation.md)、[626软件](evidence/data-link-management-software-20261024/manifest.json)、[最终十五QEMU](evidence/data-link-final-531e0c3-20261024/manifest.json) | 产品85f2bb1/e29cc11及6d933d2管理收尾修复；626次/39result/0failignored，165输入逐Gitblob映射；clean531十五QEMU/30guest0，六新18方向及九旧分账；原e152 Catalog/934 readiness FAIL保留，硬件/ISO/完整Phase3未完成 |
| E34 | [准备记录设计](research/prepare-recording-shutdown-design.md)、[英文验收](research/prepare-recording-shutdown-validation.md)、[667软件](evidence/prepare-recording-final-software-20261024/manifest.json)、[最终二十三QEMU](evidence/prepare-recording-final-1582f06-20261024/manifest.json) | 产品8ec570a；actualdirtyb1软件280/367/20共667、39rows、167输入逐Gitblob；clean1582f06二十三QEMU/46guest0，八新prepare/局部stop、六health18方向、九旧同ELF另账；CI40044605684四原生each667/39通过；旧产品五QEMU FAIL、2222首采证FAIL及22未运行均保留；不完成硬件/内核active/全Phase3 |
| E35 | [日志设计](research/rotating-logs-design.md)、[英文指南](logging.md)、[英文验收](research/rotating-logs-validation.md)、[723软件](evidence/rotating-logs-software-20261024/manifest.json)、[本地进程](evidence/rotating-logs-local-runtime-20261024/manifest.json)、[四原生CI](evidence/rotating-logs-ci-89ef420-20261024/manifest.json) | 产品f3cf8d4；actualdirty48a4软件308/395/20共723、43rows零失败忽略，177输入逐Gitblob；cleanf3cf8同CLI本地新6logging/旧4signal/2BP；精确89ef CI40053603593四格each723/新6/旧4/2通过，原41fce四setupFAIL保留。clean48c9794一项installed日志QEMU/三epoch/2guest0通过，实际CLI6cc/helperbb0；首44b1 hostFAIL/2guest0保持；不完成硬件/完整Phase3 |
| E36 | [诊断设计](research/diagnostic-export-design.md)、[英文指南](diagnostics.md)、[英文验收](research/diagnostic-export-validation.md)、[797软件](evidence/diagnostic-export-stderr-correction-software-20261024/manifest.json)、[本地进程](evidence/diagnostic-export-local-runtime-8351577-20261024/manifest.json)、[QEMU](evidence/diagnostic-export-qemu-final-d9ebe2e-20261024/manifest.json)、[四原生CI](evidence/diagnostic-export-ci-d9ebe2e-20261024/manifest.json) | 产品dff及835；797为dirty2ca/192选定source-build输入映射835/d9，clean835本地13组19child，d9四格各797/新9组13child；default4仅本地x86。clean d9ebe2e一个Native-v4 paired run/一epoch两I/O/2guest0，同WAL门下完整导出已提交seq46并排除draft47，原资源与完整cleanup证明保留；原2ca exit101与bde启动FAIL不重判，不完成硬件/完整G3 |
| E37 | [认证负向设计](research/secure-negative-qemu-design.md)、[英文验收](research/secure-negative-qemu-validation.md)、[harness](../scripts/qemu_authentication/README.md)、[797软件/首FAIL](evidence/authentication-software-and-first-qemu-failure-3d7e126-20261024/manifest.json)、[六QEMU](evidence/authentication-qemu-final-25870c6-20261024/manifest.json) | Rust192源不变；clean3d7软件343/434/20共797；clean25870c6三backend×双栈六paired run/12guest0，每pair同guest/helper两epoch四拒绝屏障两真实I/O。各44目标负向client退出记录、direct IMPORT4/duplicate2、旧server实际退出/基线恢复后新PID读取退休oldpin策略/B成功；四原hostFAIL保留，raw-v1 startup退休非热吊销/CRL/fullG3 |
| E38 | [服务端安全观测](research/server-security-observations-validation.md) | clean c097cf8软件348/440/20=808，六认证观测paired与独立Native-v4/v2诊断回归通过，共14guest0；194软件输入，计数volatile/config scope、Read授权绕WAL，不是持久安全审计、设备释放或完整G3 |
| E39 | [认证准入限速](research/authentication-admission-validation.md) | clean47f软件356/448/20=824、196输入；clean d619十三paired/26guest0：六限速、六默认配置及独立Native-v4/v2诊断回归；loaded配置共享GCRA与独立Read观测，不承诺按来源公平/完整DoS/G3 |
| E40 | [主机名与双栈回退](research/dual-stack-connect-validation.md) | clean f3bf579软件365/457/20=842、198稳定输入；clean67d十四paired/28guest0（六raw主机名、单Native/v2主机名、六默认及独立v2诊断）；正确NSS旧source196/old ELF对照0d仍timeout，六历史FAIL保留；非wire-DNS/VPN/性能或完整G3 |
| E41 | [本地设备观察](research/local-device-monitor-validation.md)、[英文指南](local-device-monitor.md) | clean c034软件380/472/20=872、206稳定输入；clean f66默认/all各一单guest PASS、真实UID2000/kernel hints/sysfs/driver/新代际/worker join。两构建6新process与旧4signal2BP通过，原INT负向/两QEMU FAIL保留；不计双端USB/IP、硬件、ENOBUFS或完整G3 |
| E42 | [托管目录与监督器隔离](research/catalog-supervisor-validation.md) | clean b675626软件385/477/20=882、220输入稳定；五新实际loopback/store测试、两Native managed双栈QEMU/4guest0、原4signal/2BP通过。保留旧产品red FAIL及开发编译FAIL；v2专项只属loopback，不完成硬件或完整G3 |
| E43 | [认证远程目录观察](research/remote-catalog-watch-validation.md)、[英文指南](remote-catalog-watch.md) | clean ec91软件394/489/20=903、223输入稳定；clean633两个Native双栈paired QEMU/4guest0，各原264+新69核验、原三轮I/O/Rootproof。四额外CLI场景/每pair及旧4signal/2BP/3local观察通过；首gate与两QEMU FAIL保留，不完成硬件/全部热插拔/完整G3 |
| E44 | [离线CRL](research/offline-crl-validation.md)、[英文指南](offline-crl.md) | clean fcb927软件/226源；clean e441六CRL、六默认及单无CRL v2诊断共13paired/26guest0。44新CLI wait/每CRL pair非USBcases，原pin保持而CRL撤销；旧47 IPv6 FAIL和开发compile保留，未完成installed managed/v2 CRL、硬件或完整G3 |

当前本机软件环境为Ubuntu22.04.5 x86_64、内核6.8.0-106-generic、Rust/Cargo1.97.1。QEMU见E20，历史软件/CI见E20/E21；测试夹具修订的软件/CI见E22；信号修复后的934358f软件、本地与QEMU见E23，CI39899971271已实际四平台通过；SIGKILL旧负向见E24，持久授权的933e720仿真、250次软件测试及四平台CI39908004666见E25，独立恢复句柄实验见E26，受限产品自动恢复与2ab9976的268次软件/七项QEMU见E27，旧producer/许可撤销/外部C三项边界见E28，TLS基础与匿名内核机制见E29，历史安全产品六矩阵与412次软件见E30，历史495软件/四CI及应用服务八场景（六前台eaa、两Native补采cb）见E31。E34的667软件/四原生CI及clean1582二十三QEMU保留原版；E35为723软件/177输入、同ELF本地6/4/2及精确89ef四原生CI，最新installed日志仿真一项/三epoch锁clean48c9794，首44b1 FAIL保持。E36为797软件/192选定source-build输入，clean835本地13组19child、精确d9四native CI各797/新9诊断组；clean d9ebe2e一paired/一epoch两I/O/2guest0。最新E37仍同192 Rust输入，在clean3d7实际运行797软件，clean25870c6六installed认证负向/启动凭据退休QEMU通过、12guest0；四原hostFAIL不重判。历史各项证据保留原版本。真实硬件、Ubuntu24.04内核USB互操作及aarch64 USB内核路径仍未验证。

## 2. Phase 0 九项产物

| 原要求产物 | 当前具体证据 | 判定与剩余边界 |
| --- | --- | --- |
| 功能差距分析 | [gap-analysis](research/gap-analysis.md)，对比两后端、USB 类、取消、存储、安全和产品能力 | 已交付；不能把“采用路线”视为已实现能力 |
| 可复用源码清单 | gap-analysis 的复用成本；两份上游审计的复用章节 | 已交付；Native 使用外部工具 / 模块；jiegec MIT HID描述符选择性复用并保留版权；其网络/物理crate未直接依赖 |
| 协议兼容性报告 | [protocol](protocol.md)、E01 / E02 的 DEVLIST / IMPORT / URB / ISO / UNLINK 与错误语义 | 已交付；Native 的 IMPORT 后数据通路委托 Linux；可选固定虚拟与USBFS服务共用有界SUBMIT/UNLINK engine，见E10/E13 |
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
| 4. 明确 Native / Rust 边界 | architecture、core、gap-analysis：已交付；Native 是内核数据通路，可选Rust虚拟/USBFS服务为独立路径，不叠加usbip_host |
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
| CLI | clap devices / doctor / discover / export / unexport / attach / detach / sessions / status，JSON 输出；feature可选virtual-server / physical-server，E35 logs及E36 diagnostics | 已实现并有 schema 测试 / 只读 smoke及各专项仿真；独立peers / config / benchmark等后续，非占位成功 |
| 基础日志 | CLI tracing 文本 / JSON stderr、helper安全类别、E35私有轮转/授权读取及E36闭合诊断导出 | 已实现所列增量；年龄/压缩、完整秘密审计与性能指标等继续未完成，不把有限历史当永久审计 |
| G1 软件 gate | E22本机完整软件检查及固定提交四平台CI | 默认89/allfeatures114项、vendor19纯测试及两组Clippy/release/Rustdoc通过；37c2d61四平台全部通过；历史结果保留，后续源码变更仍须重新验证 |
| G1-Q 用户要求的本项目 QEMU gate | E07七项矩阵、E09三机争用/双机传输中止，原始日志与完整指纹 | **干净提交b4cbfc3七项全部PASS**；三方向Native互操作各HID/FTDI两周期，reconnect三周期及新代际拒绝控制，14guest exit0；E09另5guest exit0，争用交接与真实TCPabort清理通过；doctor/UID/资源释放逐项核验 |
| G1 两台 Ubuntu 真实设备 gate | 原需求要求真实 USB 设备被客户端内核枚举并完成功能 | **未执行**；无物理测试设备和双机记录，仿真不能代替；Phase 1 不能据当前证据标全完成 |

Linux 模块加载、usbipd 启停、VPN 和防火墙目前由管理员管理。helper 不开放任意命令或服务名 RPC，也没有以模块需求让整个 CLI 默认 root。模块未加载时必须报告缺口；这些部署前提不应在交付说明中隐去。

## 5. 架构接口、状态、取消和所有权的真实程度

| 要求维度 | 已有实现 / 可验证依据 | 未实装或尚不充分的部分 |
| --- | --- | --- |
| 所有权、生命周期、Send / Sync | core 的 DeviceDiscovery、ExportBackend、ImportBackend 均 Send + Sync；future 借用后端；Native Arc 配置 / 共享 mutation 锁；helper Arc + Mutex | 完整 SessionManager / PrivilegedOperations trait 没有按长期方案落为独立抽象；当前 Request + App + Native 承担职责 |
| 类型和错误 | BusId / Host / Peer / VhciPort，类型化 Error / Result；严格 IPC wire；公共 API 文档及 Rustdoc | USBFS raw保留负errno、错误partial/actual与OUT实际长度，纯内存回归及QEMU短请求/真实取消已验证；不能据此推断全部真实设备/ISO；跨前端统一详细错误码体系后续 |
| 异步与资源边界 | sysfs 扫描 spawn_blocking；Tokio 有界网络与 IPC；Native 固定进程超时 / kill / reap / 有界输出 | spawn_blocking 取消不会终止底层系统读取；实际内核阻塞和断网生命周期仍需异常测试 |
| 状态机 | core九态；原helper提供内核快照；E31新agent以角色/receipt驱动连接状态、持久事件及无live owner的精确Root proof协调；E32统一前台与managed owner | 不是所有发现/热插拔均已接入；raw-v1无heartbeat/RTT，Rust无独立proof的崩溃记录仍未决 |
| 取消 | CLI Ctrl-C 报告取消和“已接受 helper 操作可能继续”；Native future drop 测试表明活动子进程被终止，未声称内核回滚 | 没有 Cancel RPC / 操作令牌 / 取消结果确认；客户端断开不撤销已接受的 helper 操作；不能称完整可取消业务事务 |
| 独占 | 设备级导出由内核占用、helper 变更串行化；只记录本实例成功绑定并复核的 export generation，拒绝接管未知绑定；native 只认领唯一匹配新增端口，detach 再校验原身份 | 锁不能排除管理员同时运行外部 usbip；未证明所有并发 / 多客户端 / 热插拔竞态已闭合；只凭失败操作前后差分无法证明绑定来源时不认领 |
| 清理与恢复 | 幂等 detach / unexport、命令失败后读内核、未知绑定 / 端口 fail-closed；fixture 验证部分成功与端口复用；本地sysfs dev/inode+USB地址receipt阻断新代际继承，attach前仅清理已释放旧记录；E07复现旧bug并验证直接重连及外部资源拒绝 | E09验证主动TCP中止后NULL/旧节点移除、自有记录清理和手动再次导入；旧legacy外部/未知资源仍要求管理员协调；E25–E28有USBFS持久授权及受限恢复，E30新Native通道/export持久intent和receipt、合作关闭后退休已验证；完整崩溃恢复与来源不明绑定仍不能自动处理，不满足“所有失败均自动释放”的长期目标 |
| 设备身份 | VID/PID + 可用序列号 + 控制器 / 端口拓扑散列，排除动态 Bus ID / address；fixture 验证重枚举 | 无序列号同型号同端口替换仍可能不可区分；不构成设备密码学身份或完整 hotplug 事件机制 |
| 平台边界 | 当前 Native 路径为 Linux；helper 非 Linux 明确 Unsupported；两Ubuntu × x86_64/aarch64原生软件CI通过(E08)，目标矩阵在compatibility | 非Linux全workspace构建、Ubuntu24.04内核USB互操作、aarch64 USB内核/物理设备、Windows驱动未完成 |

这些缺口不是通过补充文档即可变成已实现；后续阶段需要源码、故障测试和系统证据。尤其不能把九状态枚举、取消 future 和保守拒绝错误当作完整恢复服务。

## 6. 原文 4.1–4.11 长期功能追踪

| 原要求 | 当前已做 | 部分 / 后续和必需证明 |
| --- | --- | --- |
| 4.1 全类本地发现与元数据 | VID/PID、manufacturer / product / serial、Bus ID、address、USB version / speed、device / interface 类、active endpoint、驱动和绑定；错误 / blocker 显式 | 当前使用者 / 系统服务忙状态不能全面判定；所有 alternate setting 的完整展示后续；各设备“可发现”不能标“重定向支持” |
| 4.2 共享、取消、远端列表、导入 / 断开、独占、多设备、多客户端、权限、监控 | Native / helper / CLI 具备基本路径和设备 / peer allowlist，内核 / 软件独占 | E09已验证单设备被A持有时B拒绝、释放后B接手；E10验证两个固定虚拟profile并发，E13验证两个QEMU USBFS设备并发及拔出一个后另一个继续；真实多设备、两个首次导入同时竞速、长期调度和完整故障矩阵仍未完成；存储不共享 |
| 4.3 热插拔、重枚举、元信息 / 总线变化 | 重新扫描与候选持久 ID、代际复核、重枚举 fixture；E13的USBFS Gone监测和QMP拔插/新代际拒绝 | 仿真热拔已验证；自动授权新设备、通用列表推送及完整会话协调未实现；无序列号歧义依靠明确拓扑策略，非自动安全识别 |
| 4.4 IPv4 / IPv6 / LAN / VPN / 主机名 / 超时 / 心跳 / RTT / 带宽 / 异常 / 多客户端 | E33显式schema2/ALPN2同数据链probe/RTT与只读health，三backend双栈六新QEMU含18方向故障、另一原连接245B双向I/O、新Catalog/真实cleanup/reuse；旧九另账 | PONG非USB进展；VPN/WAN、DNS多地址、带宽基准、长期并发、pending URB/内核内部active与真实硬件仍待验收；应用bytes含协议头 |
| 4.5 九状态、超时、取消、幂等、失败释放、重启恢复 | E31实际job/会话owner与Root proof；E34独立sticky shutdown/晚注册继承、准备ACK下实际owner先清理、局部export不连关sibling、accepted initializer保留到真实返回；clean1582八新QEMU通过 | 完整状态/并发矩阵仍未完成；Rust虚拟export及Rust incoming无独立helper proof的崩溃session未自动协调；存储不自动恢复写入 |
| 4.6 存储挂载 / 句柄 / 服务 / 多 LUN / 分区 / 加密检查与安全释放 | 所有当前接口和缓存 inactive config 的存储判定；Phase 1 全面拒绝存储导出 | 尚未实现全面使用者 / 挂载命名空间 / dm / LUN 审计与排他导出；**拒绝存储是安全降级，不是完成存储共享功能** |
| 4.7 普通 UI、系统服务、最小特权层、UDS / OS ACL、严格输入 | 普通CLI/受限root helper；E30专用broker/独立池；E31非root agent、SO_PEERCRED逐profile/action ACL、实际operator拒绝且资源不变 | 同broker UID仍是整个helper池信任域；systemd隔离、完整多用户/规则矩阵与审计验收未完成；RPC不接受任意路径/端点/凭据/helper Request |
| 4.8 clap CLI 命令与一致性 | 默认Native；可选virtual/physical/secure命令；E31 agent run/profiles/start/stop-job/stop-session/jobs/sessions/events/audit/discover及安全retry；E32共享前台owner；E33 agent health；E35 logs目录/授权agent读页及follow；E36 diagnostics私有JSON/单profile导出 | peers/config/benchmark未实现；agent需root可信manifest和独立operator规则；帮助按feature区分；doctor含失败仍退出0 |
| 4.9 Ratatui TUI | 架构共用核心、roadmap G4 | 后续 Phase 4；页面、键盘、搜索 / 排序 / 自动刷新 / 确认 / 错误详情均未实现 |
| 4.10 GUI | 设计推迟到核心稳定，计划评估 Tauri；共用核心原则 | 后续 Phase 5；无 GUI；双端列表、延迟、访问控制、自动规则等未实现 |
| 4.11 tracing / JSON / 文本 / 级别 / 轮转 / 脱敏 / 诊断 / 指标 | E35可选私有typed NDJSON、最大2KiB记录/256队列/128MiB payload、checked轮转/重启/游标及profile Audit授权读页；业务commit后mirror且不持USB能力，实际owner先清理后join logger。默认stderr/级别保留；E36 bounded闭合JSON/alias/独立已提交视图与真实私有publication，信号/文件失败保留实际结果。E31 audit与E33受限同链RTT另账 | 文件日志不等于永久/不可变审计；年龄保留/压缩/全面秘密审计、吞吐及RTT性能基准待做；永久FS/kernel阻塞不承诺有界退出，raw1不编造probe |

## 7. 自动测试、互操作、异常、安全与性能范围

| 原要求测试类别 | 当前证据 | 状态和缺失 |
| --- | --- | --- |
| 单元：序列化 / 反序列化 / 端序 / 描述符 / ID / 状态 / 配置 / 认证 / 错误 / 超时 / 取消 | 最新E35默认308/allfeatures395/vendor20共723次，增加typed记录/真实文件/授权reader/commit mirror；E34原667另账；准备停顿/局部stop/晚注册与真实mTLS管理回归，旧E33的626另账；strict健康/FIN/EOF/元数据与原FAIL同函数回归；E32原530另账，共享owner/真实TLS背压与记录锁回归，E31原495归旧版；含managed admission/独立完成、可信配置、精确tracked proof/迁移、持久FS/容量/retry/角色状态及原协议测试；E30的412次保留原版 | 软件门禁通过；真实TLS软件用例不代替QEMU每个负向，描述符与取消覆盖仍有边界 |
| 三方向互操作 | E07：managed / linux-client / linux-server各HID和FTDI | Native三方向实际功能及清理通过；E10另验证自有虚拟Rust Server→RustUSB Native client/VHCI；独立官方CLI专场、物理及完整G2仍待验收 |
| QEMU HID / CDC / 描述符 / 断开 / 重连 / 异常包 | 上游HID/CDC有E02；本项目Native有E07；虚拟与USBFS有E10/E13 | 七项Native矩阵通过；FTDI不是CDC，E13新增USBFS HID/FTDI正常与热拔，并复跑本项目标准双接口CDC虚拟服务；完整网络/热插拔异常矩阵后续 |
| 真实 U 盘 / 串口 / HID / JTAG / 摄像头 | compatibility 与 testing 定义逐设备测试 | 全部未执行；存储拒绝；UVC / Audio / Composite / alt / ISO 各需独立证据 |
| 断网 / 高延迟 / 丢包 / 进程退出 / 拔出 / 客户端退出 / 争用 / 忙 / 权限 / 版本 | 软件fixture覆盖工具失败/timeout/drop/身份复用/busy/UID；E09真实SOCK_DESTROY与三guest争用；E13 USBFS QMP热拔；E33三backend双栈18个定向5-tuple黑洞阶段 | 已验证主动TCP中止、单设备交接、QEMU USBFS热拔/新代际拒绝和受限数据链黑洞；WAN/更广延迟丢包、完整进程崩溃、真实硬件拔插、同时竞速及完整异常矩阵仍未验证 |
| 未授权 / 非法设备 / 畸形消息 / 超长 / 认证失败 / 重复 / 恶意配置 / 日志 / 特权滥用 | E30真实mTLS/错误证书与unmapped pin、目录过滤/猜BusID拒绝、可信配置；六QEMU非broker及双栈3240拒绝、完整外链TLS检查；旧parser fuzz保留 | 证书热轮换/吊销、全面秘密扫描、完整IPC/配置/描述符fuzz和完整Phase3会话审计仍未完成；QEMU未重跑每个证书负向 |
| 性能：RTT / 吞吐 / P50 P95 P99 / CPU / 内存 / 并发 / 错误 / 恢复 | testing 与 roadmap G6 定义方案 | **无产品性能实测**；fuzz runs / RSS、QEMU启动时长和上游输入通过都不是 USB 性能基准 |

## 8. 四项 Cargo 与工程质量 gate

| 必需命令 / 约束 | 证据及判定 |
| --- | --- |
| `cargo fmt --all -- --check` | scripts/check.sh首项，E35实际xtrace完整脚本通过；成功无输出正常。后续源码修改需重跑 |
| `cargo clippy --workspace --all-targets -- -D warnings` | E35默认与all-features两组通过；自有工作区禁止unsafe/unwrap/expect/panic，vendor另记来源；lint不证明USB或安全语义 |
| `cargo test --workspace` | E35默认308/allfeatures395/vendor20共723次、43result、0失败/忽略，415 qualified观察名；177输入前后不变且逐f3cf8 Gitblob核，actualdirty48a4不冒充clean。两Clippy样式FAIL、原667/655/626/608/530保留原版；非硬件验收 |
| `cargo build --workspace --release` | E35默认/all-features release成功；installed QEMU按实际clean源默认locked all-features构建并收actual ELF；E34二十三项保留原1582/CLI72/helperb4，不重归属到新日志版本 |
| 公共 API 文档 | `RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps` 在check.sh中，E35两组通过；不代表全部规划能力已实现 |
| 工具链 / 依赖 / 许可 | 锁定稳定 1.97.1 和 2024；Cargo.lock / inventory / third-party；最终变更后更新清单；未声称完成发布合规认证 |
| 平台 / 资源 / 清理 | 有界输入、明确平台错误、阻塞隔离、固定进程、真实sysfs与保守未知资源处理；目标所有OS/架构及长期异常路径仍需独立gate，不用lint或绿色CI覆盖 |

历史Native轮次E03最终59项及全部质量检查通过。E07针对同一源码、干净提交b4cbfc3的七项QEMU矩阵全部PASS；Native源码及七项脚本在后续E08–E12、截至cfeffd3虚拟子步骤时保持未变。历史E13新增USBFS adapter，并在9b0da33最小修复Native成功detach后的变化快照观察；原失败及新增3项确定性回归保留，原七项脚本未改。历史Composite软件产品基线为192640c，三项QEMU基线为22d172d，四平台结果见E16；最新checked release产品4e81a11和最终11项QEMU的6e809a1见E20/E21；37bc6b4历史结果见E13/E14；不将旧矩阵声称为当前源码的完整重跑。

## 9. 用户追加的过程记录、commit、push 和 QEMU

| 用户要求 | 审计时事实 | 最终必须补足的证据 |
| --- | --- | --- |
| 合适 commit | 历史研究/实现/验收记录保留；最新USBFS helper/engine/CLI、IPC与Native修复、QEMU/证据分别提交，见work-log | 按产品、测试、验收证据提交；每次普通push后核对远端main，不强推 |
| 记录所有过程 | work-log保存研究、实施和失败修复；E13保留f42d最终FAIL与37bc最终PASS；E15–E17保留全部7次开发FAIL、dirty PASS与22d三项PASS；E20另保留两旧产品FAIL、三轮候选FAIL和最终11PASS，6e809的CI失败及cfg(test)修订独立保留 | 最后文档归档另行提交；失败记录与对应源码/脚本快照保留 |
| push 指定仓库 | origin fetch/push均为 `git@github.com:tiansongyu/usbip-client.git`；4e81a11/ab44a84/19ffa11/6e809a1/37c2d61普通push成功；历史远端核对见对应工作日志 | 最后文档提交后再次普通push与SHA核对；最新自身SHA以Git实际历史为准 |
| 使用 QEMU 仿真 | E07七项Native矩阵、E09争用/中止、E10虚拟服务，E13 USBFS正常/热拔及原虚拟回归，E17历史ECM回归及E20最终十一项 | 历史E20十一项锁clean6e809a1；E34二十三项锁clean1582f06、46guest0、同CLI/helper与source/binary/head/clean guards；各组范围不相加冒作独立case，不替代真实硬件 |

### 历史Native轮次裁决（历史Composite裁决见第13节，最新checked release见E20）

- 本轮G1-Q七项具体命令、summary、原始双端日志和二进制哈希均见[E07矩阵报告](research/native-qemu-matrix.md)；全部exit0，同一干净b4cbfc3、七次locked构建、14guest exit0。旧21e98f9重连FAIL与修复后的同场景PASS均保留。
- 历史Native轮次最终[软件输出](evidence/software-checks-phase1-runtime-2026-10-22.txt)：59项及fmt/Clippy/release/Rustdoc全部通过；产品a4d258c，QEMU基线b4cbfc3；测试端ETXTBSY排查/修复和20轮稳定性记录另附，生产命令未增重试。
- 历史[出版证据](evidence/publication-2026-10-22.json)保留；本轮commit/push、远端SHA核对与两Ubuntu CI结果见[本轮出版证据](evidence/publication-runtime-2026-10-22.json)。
- 两台真实 Ubuntu 与真实 USB 设备 G1：**未执行，不能因 QEMU 通过改为已完成**。

## 10. 长期阶段仍保持完整

Phase2已交付并验证固定虚拟服务子步骤(E10/E11)及USBFS adapter的QEMU子步骤(E13/E14)；真实物理硬件、ISO/通用Composite/alternate、上游R01–R12剩余缺口及完整互操作仍未完成；Phase 3 的身份认证、设备授权与完整数据加密已有E30软件和六QEMU子步骤证据，但完整会话恢复与审计、生产部署边界仍待完成；Phase 4 的完整 TUI；Phase 5 的 Tauri GUI、deb、systemd、安装 / 卸载 / 配置与体验；Phase 6 的真实设备兼容性和性能；Phase 7 的 Windows 客户端驱动、签名、安全和系统验证，均依原要求保留。上述未完成部分保持原阶段，不被已完成的研究、Native或QEMU自动测试/仿真所替代。

本次可证明的交付是研究设计、Native起步实现、可选虚拟/USBFS服务及各自已记录的软件和QEMU验证。在存储、多类真实设备、完整恢复、加密、UI、安装、性能和平台验证未完成前，不能称为已可日常使用的完整高质量远程 USB 产品。

## 11. 历史：cfeffd3可选虚拟服务子步骤裁决（2026-10-22）

- E10在干净cfeffd3的两guest默认locked构建通过；服务普通UID1000、无usbip_host/虚拟QEMU USB，客户端真实usbhid/cdc_acm；两并发设备、两周期实际I/O及清理有原始日志和SHA。固定两个Full-Speed profile，不外推物理类别兼容。
- E11最终ASan parser fuzz 3,615,249runs/31秒、cov562/ft718、无观测崩溃；不称为穷尽安全。stdout背压原FAIL与修复后SIGINT/5秒deadline lease释放均保留。
- 该时点软件默认75/allfeatures76tests、无失败/忽略，fmt/两组Clippy/release/严格rustdoc通过；Native源码和旧九场景脚本当时未修改，新增服务路径为feature opt-in。最新数量和Native修复见E13/E14及第12节。
- 真实G1仍未执行；G2物理/ISO/通用Composite/alternate、G3完整恢复/会话审计、TUI/GUI/安装/性能/Windows保持原完整目标；认证/TLS已有后续E30子步骤证据，不将整个G3标完成。

## 12. 历史：37bc6b4 USBFS adapter子步骤裁决（2026-10-22）

- E13在干净37bc6b4默认locked构建完成USBFS正常、热拔及原虚拟回归三项PASS，6guest均exit0。普通UID1000/零cap经helper取得root0600设备FD；HID/FTDI实际I/O、0/1/15B请求、取消/释放/原驱动恢复、热拔后另一设备继续及新代际不自动授权均有原始证据。QEMU模型不外推真实设备兼容性。
- f42d最终FAIL促成9b0da33的Native最小修复：仅成功detach后，在原截止内对变化快照只读重观测，不重复detach，不放宽稳定replacement拒绝。原失败、3项确定性回归和修复后最终PASS均保留。
- 该时点默认89/allfeatures103及vendor14纯测试、全部质量gate通过；E14四平台每格206项且两项stdout背压回归通过。物理adapter及虚拟服务仍为feature opt-in，未提供远端认证/TLS。
- 真实G1/G2硬件和第10节长期目标仍未完成；该子步骤的软件/仿真裁决不改变完整产品验收范围。

## 13. Composite配置与恢复子步骤裁决（2026-10-22）

E15软件gate、E16四平台CI与独立源码复核通过，新增两个接口由同一driver probe恢复的确定性回归，以及当前配置storage/块后代/外部占用拒绝检查。ECM双来宾dirty开发dev08已完成19probe和两个导入周期的实际38/39B UDP、USBMON/wire错误字段一致及原双driver恢复，全部7次开发FAIL保留。E17在同一干净22d172d默认locked构建完成ECM、HID/FTDI正常与热拔三项最终PASS，6guest exit0；ECM实际两周期精确38/39B UDP、USBMON/wire一致和双driver/config1/alt0,1恢复，root独立PCAP验证通过。真实Special blocking线程syscall-entry后的取消/EOF在E18同一clean c921a27最终六场景已通过；kernel内部active窗口尚未覆盖。原driver无绑定且为非零alternate的恢复、任意Composite/ISO、真实硬件和完整G2仍未完成。


## 14. 本轮checked release与证据结论

E20的clean6e809a1十一项QEMU全部通过，113case/22guest0，覆盖release两项真实-22传播、剩余IF1释放、零SET_CONFIGURATION入口及原六host-state和ECM/HID/FTDI回归。其他动作仍按其真实配置/alternate/clear errno验证，不把release故障预期套用于全部8项。

冻结提交的CI6e809a1实际3 PASS/1 FAIL，原日志和缺项完整保留；E22只修复既有Native测试夹具，37c2d61本机及四原生CI均实际222项通过。生产Native字节不变不意味着重建ELF哈希相同，原QEMU产物仍只归其真实6e809a1来源。完整Phase2、真实硬件G1/G2和后续安全/界面阶段继续保留原范围。


## 15. Unix信号合作停止子步骤

E23保留两项旧虚拟child TERM实际-15，以及旧USBFS QEMU startup/main各wait143。旧主服务退出后完整20.04秒观察中原driver均为null，尽管原代际/config1/alt0/root0600仍在且客户端已自主NULL；这证明当轮未完成合作恢复，不证明永久泄漏，无stopped也不能填造末资源/reap数。

产品d39517b仅修改4个CLI文件。clean934358f实际完整软件gate222项/31条result、138份冻结输入未变，本地四项信号与原两背压回归通过；TERM及原虚拟、物理正常/热拔四QEMU均PASS，8guest0。原虚拟的feature/ELF与另外三项不同，证据分别归属。CI39899971271四原生平台均通过，每格222次测试执行、原两背压及新增四信号场景通过；三项回归227项独立原始复核通过；原始证据及限制见E23报告。

listener前异步prepare可取消不代表已经运行的blocking内核读取被终止；重复信号不丢cleanup目前仅有源码审查。该E23时点不覆盖SIGKILL/abort、跨进程监督或持久重启协调；后续受限持久授权与原driver恢复分别见E25/E27。通用恢复、Windows USB、真实硬件及完整Phase2/Phase3继续未完成。


## 16. 持久物理授权与管理员协调子步骤

E25保留首clean e0ac478的harness同步FAIL和缺失证据边界，修订仅改变新测试的保存后握手/失败采证并补第三轮I/O，产品不变。clean933e720的144输入在七项串行运行前后不变，五QEMU各自host验收通过、十guest0；所有五项为同一all-features产物。新journal中普通服务正常关闭可清空记录，KILL137后的原记录跨helper重启不变且两次新服务明确拒绝，root仅在人工恢复实际原基线后退休记录，第三次实际I/O和正常关闭通过。

本轮只完成可信服务的持久授权生命周期子步骤。自动恢复所需的可信变更范围/独立监督、存储、完整会话/安全、UI与安装、性能和平台验证继续保持第10节原完整目标。

## 17. 安全数据链六场景补充裁决（E30）

同一clean b83c931的三后端×双栈六QEMU均整体PASS，12guest0；实际mTLS的DEVLIST和IMPORT后URB、HID/CDC或HID/FTDI两周期、双栈3240拒绝及合作停止恢复通过。四个历史FAIL各按原HEAD保留，尤其f9558fc两guest PASS而host抓包classifier FAIL不改判。完整来源、软件412次和旧396次归属见E30报告。独立生命周期与安全raw复核分别通过492/350个审计谓词（非USB case数），原件和独立解析脚本已归档。真实硬件G1/G2、ISO/跨配置/崩溃、完整Phase3会话审计和后续阶段继续未完成。

## 18. 应用服务与精确清理证明子步骤（E31）

`2a0f8d7`真实非root应用owner/managed admission/逐连接stop与有界持久audit已实现。clean`eaa1936`完整软件495次及四原生平台CI通过，六前台回归和两Native agent双栈均PASS；`cb08672`仅增强采证，两Native新clean再次PASS。主要选定八场景16guest0，额外保留原两agent PASS及其TLS JSON/pin/mode未采边界；不把补采文件伪作旧原件，也不把两次相同场景算新增兼容性。

Native agent在RPC返回后实际I/O、定向停HID但原serial继续、client KILL137后Root精确proof协调/旧ID保留与新attempt真实I/O、server TERM完整driverbaseline均有原件。新版实际TLS JSON/public DER/pin/policy/两epoch权限、双方PCAP及actual UID/ELF经独立复核；私钥仅metadata。完整来源与所有限制见[专项报告](research/application-service-validation.md)。

这是原G3的子步骤：本E31交付时旧前台未统一提取、同数据链heartbeat/RTT未完成，后续E32已统一owner、E33补充受限probe/RTT及定向黑洞证据；E31未完成的轮转后由E35补充；Rust无独立proof的崩溃session、长期审计、完整网络证书负向/轮换矩阵等仍未完成。物理硬件G1/G2、ISO及其余阶段不改为通过。

## 19. 共享前台与托管owner子步骤（E32）

见[英文验收](research/shared-owner-validation.md)。同一clean b45c2e8、同CLI/helper九项QEMU全部PASS/18guest0，六前台安全双栈、两Native agent与旧USBFS TERM；最终530软件及新mTLS五组合/旧本地四signal两背压通过。独立raw源码/生命周期与安全复核、首clean4d真实FAIL及原件均保留。前台与managed共用实际owner，但前台无伪session/WAL/correlation，输出ACK非持久准入；操作/清理/记录独立，Native解锁先于记录等待。该E32时点initializer总await只有源码约束，旧startup明确acceptance=false；后续准备记录停顿及accepted syscall-entry证据见E34，内核内部硬阻塞仍未验证。无硬件/ISO/完整G3或后续阶段通过声明。

## 20. 同数据链健康观察子步骤（E33）

见[英文验收](research/data-link-health-validation.md)。626软件165输入映射与clean531e0c3十五QEMU/30guest0通过；六新18方向、九旧分账。只丢实际health绑定5tuple且FD保留，另一原serial/CDC连接245B每向与新Catalog继续；至少一端真实ProbeTimeout与双方实际cleanup、fresh attempt、最后SIGTERM/Rootproof/完整baseline都成立。Native/USBFS实际QMP HID+FTDI，virtual仅HID枚举闲置与CDC echo。两次真实FAIL及静态原件、修复后同函数回归、独立方法复核均保留。

私有agent disk2严格迁移并预留history增长，不变旧Session/Metrics/IPC1；短management借用IO的FIN/EOF自然join避免flush后早停。PONG/RTT与USB进展分开，raw1 unavailable；Native/managedUSBFS pending/inflight、真实G1/G2、ISO、通用恢复和完整Phase3/后续阶段未由此完成。精确531的CI40029976284四原生job各626/39rows/0failignored，五TLS及原信号/背压通过；runner/tmp缺项按原件保留，不借旧530 CI冒充。


## 21. 准备期记录停止子步骤（E34）

见[英文验收](research/prepare-recording-shutdown-validation.md)。667软件在actualdirtyb1运行，167输入逐8ec/1582 Gitblob匹配；clean1582f06二十三QEMU/46guest0、同CLI/helper。八新场景分别核实际第二Resource fsync门下四global停止、accepted GET_CONFIGURATION entry下两global停止与Native Resource/Available两local停止；六health18方向及九旧回归另账。旧产品五QEMU负向、b1缺server stub屏障、2222首saved/live错配FAIL与22未运行均保留；采证修复只绑定对应saved原件，不放宽proof/timeout。

Native局部stop保留原HID真实I/O，client detach后先等精确原server stub退休/完整原profile，再停最后export并核同job/listener的真实空Catalog；原APP记录在实际close之后仍可能pending。initializer是已接受同一syscall-entry合作保留/返回后真实Finish，不是内核内部active硬阻塞。E34未包含的日志轮转后由E35补充；永久FS/kernel阻塞、无独立proof的Rust崩溃记录、真实G1/G2、ISO及完整Phase3/后续阶段仍未完成。


## 22. 有界进程日志子步骤（E35）

见[英文验收](research/rotating-logs-validation.md)与[操作指南](logging.md)。新的共享logger在设备前打开、实际USB owner清理后join；file-only typed NDJSON有独立writer、两实际reader与两queued请求、128MiB payload硬上限。业务成功commit之后才镜像audit，不能代替WAL/准入/Rootproof；profile Audit权限先于读盘，所有授权结果绕业务队列，RPC不接受路径。默认stderr保留且不由此保证任意trace导出脱敏。

723软件为actualdirty48a4/177输入映射f3cf8，本地cleanf3同CLI新6进程/旧4signal2BP通过，CI89ef四原生平台全部通过；原两样式失败、四cache前置FAIL与首QEMU hostFAIL分别保存。文件IO失败可在真实操作完成后导致最终exit1，不能重试时假设USB未操作。新增clean48c9794一个paired场景内三epoch PASS/2guest0，操作/清理独立错误与原44b1 FAIL保留，不以guest0覆盖hostFAIL。该时点的诊断导出由E36补充；年龄保留、完整秘密审计、永久硬阻塞、硬件G1/G2及完整Phase3/后续阶段仍未完成。

## 23. 私有诊断导出子步骤（E36）

见[英文验收](research/diagnostic-export-validation.md)与[操作指南](diagnostics.md)。共享rustusb-diagnostics输出4MiB内闭合JSON，以每次独立别名保留关系，禁止raw标识/路径/秘密。singleprofile Read+Audit逐请求核peer，真实commit/state替换/mirror后发布immutable view，授权Begin/Page/End及错误绕WAL；四实际snapshot/每UID一/30秒，Arc生命周期配额不可提前释放。health/log独立采样，观察无准入/恢复proof权力。私有0700父/0600 partial、真实write/hash/sync/NOREPLACE/dirsync及总await工作者，信号不丢已接受publication。

797软件实际dirty2ca、192选定source/build输入映射835及d9；clean835本地allfeatures九组13child/default四组6child和旧4signal/2BP通过。精确d9四native CI每格797及新九组通过，不把default四组外推。clean d9ebe2e一个paired run/一epoch/两guest0，在原state.next candidate1同PID/start/TID/FD/inode门下完成seq46导出、排除draft47及第二轮真实HID/FTDI I/O；六原Rootterm/fullbaseline/VHCI NULL/Gone通过。原incoming State/Protocol与Verified cleanup分别保留，非cleanEOF/已trace reset。原token End仅源码+后续同UID slotreuse，权限负向缺独立proc及NOREPLACE非并发rename的边界保持。

原dev Weak-monitor fixture失败、clean2ca实际exit101、两零child preflight、cleanbde首QEMU gate argc不匹配FAIL均留原件；835修实际returned stderr错误而非禁log绕过，生成C仅固定launch白名单变化。独立方法谓词不计USBcases，作者参与身份明示；旧723/793和旧23矩阵各锁原source。硬件G1/G2、完整G3、永久FS/kernel硬阻塞、通用Rust崩溃协调与后续阶段仍未完成。


## 24. Installed认证拒绝与启动凭据退休（E37）

见[英文验收](research/secure-negative-qemu-validation.md)。同clean25870c6/同CLI9708/helper7c69的Native、virtual、USBFS × IPv4/IPv6六paired run全部PASS，12guest0；每pair同guest/原helper内两credential epoch、四before/after屏障与两轮实际HID/Serial I/O。旧A stop/原server actualwait0/fullbaseline恢复后才新PID读取移除A pin且保B的策略，旧A拒绝/B真实I/O，不能以新进程资源0证明旧cleanup。raw-v1 startup退休非热吊销/CRL/OCSP/崩溃恢复。

各44条目标负向client退出记录包含错误cert/用途/CA、名称/信任、缺cert/ALPN和server直接未授权IMPORT4/authorized duplicate2；总264不是USBcases。fast负向无逐executedELF样本，原有broker/helper有实际proc/ELF；虚拟server本来无helper，核原client helper。混合TLS PCAP不套旧全正向classifier。原四hostFAIL（EOF类别、service projection、mapping104、004数值解析）分别留原件；同作者另写7042 raw/source谓词核3844原件/2342解码块恒同，不称独立实现审计或新case。clean3d7软件797/47rows同192选定source/build前后稳定且映射258，无Rust/外部依赖变动。硬件G1/G2、framed-v2专属负向、生产隔离/完整审计、认证耗尽与完整G3/后续阶段仍未完成。


## 25. 服务端安全观测（E38）

见[英文验收](research/server-security-observations-validation.md)。metadata-only SecurityMonitor区分TLS、认证后framing与outer限额；配置clone与job restart共享，agent instance标明epoch，不持stream/permit/USB owner。前台新control与agent固定Profile Read读取，授权成功/Unsupported绕WAL，拒绝沿durable，旧Session/Metrics/disk/诊断bundle不变。clean c097软件808/194稳定输入、4signal/2BP通过；同source/CLI六raw认证观测paired与一独立Native-v4/v2诊断回归全部通过/14guest0，原I/O/Rootproof/fullbaseline不放宽。安装态agent counter RPC、安装态v2负向计数、持久安全历史、硬件G1/G2及完整G3仍未验收。

## 26. 认证准入限速（E39）

见[英文验收](research/authentication-admission-validation.md)。默认burst64/125ms、root可信schema1/2严格策略，loaded配置clone/job共享非等待配额；失败/取消不退还，原有stream/URB不重新准入。独立metadata-only monitor及server_admission/agent Read接口不改旧TLS/Session/Metrics/disk/诊断schema。clean47f完整824软件、196稳定输入；同product/CLI的clean d619六限速/六默认/独立v2诊断共13paired、26guest0。本地4signal/2BP通过。首956 host采证时序FAIL保留；新receipt先于校验。安装态agent RPC、v2专项限速、公平/长期DoS/性能、硬件G1/G2及完整G3仍未验收。

## 27. 主机名与双栈连接（E40）

见[英文验收](research/dual-stack-connect-validation.md)。共享Rust connector保留OS首族偏好，64raw/16unique、四actual resolver工作，250ms正常/100ms失败间隔、inline候选仅TCP winner进原TLS/USB；原总deadline/stop/独立service name不改。clean f3bf579完整842软件、198输入稳定；同产品ELF clean67d十四QEMU/28guest0分七新主机名与七回归，本地4signal/2BP及2legacy CLI查询/超时通过。五早期不充分候选/设置FAIL与有效旧产品0d对照FAIL分开，原件不重判。Probe实际UID2000/ELF/两候选及root NSS策略有原件；不是逐process抓包归因。std/libc/OS分配/执行时间不由保留上限约束，started lookup不可假取消；wire-DNS/VPN/性能、其余v2 backend专项、硬件及完整G3/后续阶段仍未验收。

内容2aa9337正常SSH push，精确software40091124317四native job与Pages40091124270检查/部署均success；新增hostname两case step亦通过。只核primary API状态，不从中重算逐平台842。六canonical HTTPS资源200且逐Gitblob同，公开desktop/mobile DOM/键盘/响应图通过。详见报告[发布原件](evidence/dual-stack-connect-publication-2aa9337-20261024/manifest.json)；最终回执不改198产品输入/六site源，原未完成项保持。

## 28. 本地热插拔观察（E41）

共享rustusb-discovery由service原样re-export，默认CLI一次发现与devices --watch共用实现，两个实际worker配额不因awaiter取消提前归还。kernel group1提示+原scanner权威重扫，Current/Stale/Initializing、candidate ID/Bus ID/stamp分开；无成功库存为null，容量/读取失败保旧，不假删除。慢订阅者按完整快照resync，实际owner总join，stdout不持scanner。最终872软件锁c034/206源，默认/all两个单guest锁f66/213源，各真实UID2000/ELF/socket两采样、六点driver/代际、SIGTERM/wait、完整帧及最终虚拟设备清空。新本地6process和原4/2通过；宿主原始设备输出保持私有，公开闭合投影。详见报告与[原件](evidence/local-device-monitor-f66da6b-20261024/manifest.json)，不完成硬件/远程自动共享/永久OS阻塞、ENOBUFS或完整G3。

内容cacb170正常SSH push，software40101176031四native job120893459087/459290/459291/459372及Pages40101175998 check120893459117/deploy120893532285均completed/success；实际arch/fullgate/新增3observer step通过。只核primary API状态，不从中重算每格872。六canonical HTTPS逐Gitblob同，公开desktop/mobile DOM/键盘/图片通过；详见报告[发布原件](evidence/local-device-monitor-publication-cacb170-20261024/manifest.json)。最终回执不改206软件/213 QEMU输入及六site源，原硬件/G3/后续阶段保持未完成。

## 29. 托管远程目录任务（E42）

见[英文验收](research/catalog-supervisor-validation.md)。远程DNS/TCP/TLS/Catalog不再占用单session监督器；最多两个实际任务，包含未消费完成结果，调用者退出不释放槽。原UID/profile Discover授权先于容量，成功audit先于Catalog回应；网络任务不持helper/USB能力、不写WAL，外部stop与命令EOF合作停止并总join。clean b675626完整385/477/20共882软件、220选定输入稳定；五新loopback/store测试含真实v1/v2 mTLS/保持反向FIN/原store锁门。两Native managed IPv4/IPv6既有QEMU回归各264核验/三I/O轮，4guest0、212输入与四guard；原四signal/两背压通过。旧产品red FAIL与测试E0502编译FAIL保留，v2拥塞只属本地loopback，不外推installed矩阵、硬件或完整G3。

内容57c8750正常SSH push且远端ref一致；software40105141484四native job120905165402/165585/165731/166099与Pages40105141472 check120905165381/deploy120905234573均completed/success，primary实际arch/fullgate step成功。只核API状态，不从中重算每格882；六canonical HTTPS资源200且逐Git字节同，无新browser执行。详见[发布原件](evidence/catalog-supervisor-publication-57c8750-20261024/manifest.json)。最终receipt不改220软件/212QEMU输入与七site文件，原完整目标未完成项保持。

## 30. 认证远程目录自动观察（E43）

见[英文验收](research/remote-catalog-watch-validation.md)。shared service以同一owner/metadata API实现agent discover及secure-discover --watch，原one-shot不变；initializing/null、Current空成功、Stale保旧、terminal clear分开，stable key地址变化不授generation/能力。clean ec91软件903/223源，clean633两Native raw1双栈QEMU/4guest0各原264+新69核验与三I/O轮/完整Rootproof；同PID watcher观察2→HID-only1→stale1→恢复2→真实export stop1→0，四辅助CLI场景含direct TLS/TERM、实际满write-only FIFO/INT、关闭readerexit1、UID1002拒绝，fast拒绝无独立liveELF。旧4signal2BP3local观察通过；宿主.private仅私有保留。首Clippy gate及空jq/错误busy计数两QEMU FAIL不重判。v2仅loopback、启动取消仅源码修复；原硬件G1/G2/完整G3/Phase4–7继续未完成。

E43内容9e657637正常SSH push/远端main一致/clean；software40114160923四native job120931897631/897761/897823/897834及Pages40114161007 check120931897786/deploy120931982014均completed/success。软件step详情在primary响应为null/empty，不虚构逐step/full log/各平台903重算；六canonical HTTPS资源200并逐Git字节同，actual live CUA确认新文案/links/SVG，非pixel review。原件见[发布回执](evidence/remote-catalog-watch-publication-9e65763-20261024/manifest.json)。223/各221/四global源不变，原Phase0–7未完成项保持。

## 31. 离线证书撤销列表（E44）

见[英文验收](research/offline-crl-validation.md)。完整列表受root路径/1..16/每64KiB/总256KiB限制，拒同issuer重叠/不完整scope；标准WebPki全链及unknown-deny/expiry保留，Checked SystemTime共同窗口在每次新验证检查。agent全实例启动加载，job restart不重载，既有stream不热撤销。clean fcb927/226与clean e441十三paired/26guest0分账；六CRL各44新wait、原pin持续授权/旧实际PID文件替换仍允许/真实stop和完整基线后新实例拒绝。六默认保原密文classifier；单v2诊断无CRL不可冒称其installed负向。旧47 IPv6 wait104整体FAIL、初开发失败保留；新无ALPN空输入仍strict1和实际protocol mismatch，不放宽IO。原件/研究/失败/源码索引及同作者核对见报告，不据此完成硬件、热撤销、全部G3或后续阶段。
