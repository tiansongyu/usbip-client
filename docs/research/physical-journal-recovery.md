# 物理 USBFS 授权基线与重启协调

2026-10-23。产品提交 `b75f14b`；最终仿真冻结提交 `933e720531fd96b7a23616ed687d59b816e0ce09`。本轮实现并验证持久原基线、正常合作结束及管理员协调，**没有实现 SIGKILL 后自动恢复驱动或 FD 撤销**。真实设备 G1/G2 仍未验收。

## 行为变化

旧 [SIGKILL 负向](physical-crash-recovery.md)中的新进程会把空驱动状态当作原始基线。现在 helper 在交付 FD 前持久保存原配置、全部接口/alternate/driver、设备代际和 OS owner；同指纹存在未决记录时，新的授权被拒绝。包括同型号同端口重新枚举在内，新代际不能通过覆盖旧记录继续。

普通服务结束时，先 shutdown、等待自己的后台任务结束、保存末快照并释放 registry，再调用 `FinishPhysical`。helper 核对原 UID/PID/starttime/boot 和独立设备观察，确认原基线恢复后退休记录。原代际真实消失可以结束旧记录，但不会向替代设备写入旧状态。响应丢失重试的 `not_pending` 只表示记录不在表中，不冒充重复验证。

崩溃、交付不确定或初始化结果未知会保留记录。root 可用 `physical-pending` 分页读取，并在停止、回收旧 owner 和人工恢复设备后调用 `physical-reconcile`；该命令只核验并退休，不写 USB/sysfs。旧代际消失需显式 `--acknowledge-gone`。实际命令及目录要求见[使用指南](../rust-physical-server.md)。

账本采用 root0700 目录、0600 单链接固定文件、目录 FD 相对操作、NOFOLLOW、独占 flock、原子 rename 和文件/目录 fsync；256 个记录、每设备256接口、文件16MiB上限。现有目录缺 journal/lock、运行期删除/替换/截断/改写或不确定提交均拒绝继续使用。管理员删除整个目录或选另一个目录破坏历史不在可检测保证内。

FD 仍是可信服务的整设备能力。退休管理记录不是撤销 FD，也不证明任意外部副本消失。外部管理员/sysfs 的竞态、自动恢复所需的可信变更范围、断电耐久实验继续单列；设计先于代码写入[实施切片](physical-crash-recovery-design.md)。没有新增依赖或核心 unsafe。

## 软件检查和开发失败

完整 `bash scripts/check.sh` 实际退出0，default89、all-features142、vendor19，共250次测试执行、31组结果、0失败/忽略；fmt、两组 Clippy、release 和严格 rustdoc 通过。检查在候选工作树执行，98项软件输入前后相同，提交前又逐 staged blob 核验；最终 `933e720` 使用相同软件输入，后续只有测试脚本和文档变化。[软件及源码审查原件](../evidence/physical-journal-implementation-20261023/manifest.json)

开发过程保留首次编译接线错误、Store 测试临时目录继承组可写权限导致的正确拒绝，以及修正后的定向和完整日志。测试夹具显式设0700，生产权限检查没有放宽。新增检查覆盖严格字段、完整基线差异、owner代际、存储锁/损坏/容量/不确定提交、IPC分页、后台任务真实结束和逐授权清理。

初版新 harness 在 clean `e0ac478` 首次运行失败：客户端已完成两轮实际 I/O 和自主断连，但发送确认时服务端仍处20秒观察，重试先耗尽。后续 helper 重启/管理员协调未执行；wait137仅由冻结脚本的严格断言和后续可达性间接证明，单独 wait 与当前 sysfs 原件尚未最终输出，不能补造。原失败、19份原运行产物和独立复核保持 FAIL/不完整。[失败原件与修订检查](../evidence/physical-journal-first-failure-e0ac478-20261023/manifest.json)

修订使双方保存 primary 观察后再握手，每条状态/退出事实即时写入独立串口，并保留首次失败原因；原20秒观察、重试数、截止和 USB 断言不变。随后补齐管理员协调后的第三轮实际 I/O，不以 ready 替代传输。旧 crash/TERM harness 和产品未为此改动。

## 最终 QEMU 和本地回归

所有运行从 clean `933e720` 串行执行，普通宿主 UID1000，QEMU6.2.0、x86_64 KVM、guest kernel `6.8.0-106-generic`，无宿主 USB passthrough、宿主模块/驱动变更或公共监听。144项输入、HEAD和工作树在每次运行前后均未变化。五项 QEMU 的十个 guest 全部退出0，且各自 host 严格验收通过；guest退出0单独不能证明通过。[完整原件及命令](../evidence/physical-journal-final-933e720-20261023/manifest.json)

| 运行 | 实际结果与范围 |
| --- | --- |
| 新 journal | 184项 host 检查通过；正常退休/重开、KILL137、跨 helper 重启持续拒绝、管理员恢复前拒绝/恢复后退休、第三轮实际 I/O |
| 原物理正常 | 15项 probe，通过真实控制/Bulk/Interrupt、取消、EOF和两周期释放 |
| 原物理热拔 | 15项 probe，热拔旧slot关闭、另一设备继续工作、同端口重插不自动采用旧slot、正常结束 |
| 原 ECM | 19项 probe，多配置、配置0、alternate、原IF1 alt1、两个 cdc_ether 接口和两周期实际UDP；含官方客户端 cycle2 |
| 原 SIGTERM | 52项 host 检查，prepare取消与主服务合作停止、实际 wait0、原基线恢复与末资源核验 |
| 本地信号 | ready/blocked × SIGINT/SIGTERM 四项通过 |
| 原背压 | 两项4096B不drain场景通过；未读取末快照不冒充快照资源证明 |

新 journal 的实际顺序：

1. 普通 UID1000取得两条记录；原配置1、alt0、HID `usbhid` 与 FTDI `ftdi_sio` 基线与独立 sysfs/receipt 一致。普通 UID 无法读取 root 账本。
2. 第一轮 KEY1 和 FTDI 双向各35字节、两设备真实活动请求后，正常关闭 wait0、原基线恢复且记录为空。无 IMPORT 的重开获得新的 lease ID，关闭后仍为空。
3. 第二轮 KEY2 与各35字节完成，PID1075/start549 的两个真实 lease 活动时发送一次 SIGKILL。实际 wait137、旧最终资源仍不可观察；同代际原配置/alt保留但驱动为 null，原记录不变。
4. 第一次重启明确因未决记录退出1且无ready；helper从PID270/start286合作退出0并以PID13893/start2729重启，加载相同记录，第二次服务重启仍明确拒绝。
5. UID1000/1001 的管理查询/协调均严格拒绝；root 在原状态未恢复时也被拒绝。guest管理员显式绑定原驱动，独立 sysfs 与最初状态一致后，两个 root reconcile 才返回 restored/retired。
6. 第三轮重新授权成功，KEY3 和各35字节真实传输、活动请求、正常关闭 wait0，原状态恢复且记录再次为空。三轮各方向共105字节；host TX 原始文件及 RX/evdev hex 均保留。

所有五项 QEMU 使用同一 all-features CLI `6c56ade7fd7f93fe8e331aa16197c3dee24d95d0eadc2be9a7e19c73dc70c0b6` 和 helper `14f33dade1371d6227fa9dc74b5b76bae7d8dfa89b31c34dd133206f75eedf3f`，并核对 guest 复制身份。旧正常/热拔/TERM harness的 FTDI 证据仍以其实际保存的计数、摘要、精确断言为界，不能称存在未保存的独立原始 capture；ECM PCAP另有原件。

## 四平台 CI 与独立复核

[CI39908004666](https://github.com/tiansongyu/usbip-client/actions/runs/39908004666) 在精确 `933e720` 的 Ubuntu22.04/24.04 × x86_64/aarch64 四个原生平台均 completed/success，每格250次测试执行、两背压和四信号场景通过。四份连接器解码日志、官方 run/jobs 状态、解析器及746项核验按实际内容保存；本工作流只打印每场景摘要和产物hash，没有上传其单独原始文件。[CI证据](../evidence/publication-journal-933e720-20261023.json)

独立 journal 原件解析418/418通过，原四QEMU回归解析575/575通过，后者直接解析两份ECM PCAP并核IPv4/UDP校验和。旧正常/热拔/ECM/TERM脚本没有逐grant磁盘dump，相关Finish正常退休由冻结成功路径与wait0/stopped支持，不冒称直接读取了其账本。独立parser曾误排除helper即时WAIT副本、以及额外要求ECM stderr为空；这些审查假设已按实际原件纠正，初版保留，不写成产品或QEMU失败。[复核原件](../evidence/physical-journal-final-933e720-20261023/manifest.json)

## 交付边界

这证明可信进程生命周期中的正常退休、失败保护和明确管理员协调后实际复用；没有将拒绝新授权写成自动设备恢复。helper 的进程重启不代表断电/OS重启实测。旧 FD 任意转交、原driver=None且非零alternate的实际设备、ISO、内核内部硬阻塞、更多真实错误与硬件G1/G2，以及后续TLS/TUI/GUI/Windows仍不因本轮通过而完成。
