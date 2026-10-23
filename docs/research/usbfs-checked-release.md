# USBFS checked release：错误传播与 QEMU 验证

2026-10-23。两轮旧产品基线均为 **FAIL**，已重现真实 `RELEASEINTERFACE=-EINVAL` 被吞掉的行为。产品修复已合入 `4e81a11`；首轮 clean 候选矩阵为 10 PASS、1 FAIL；第二轮在第六项启动阶段失败，后五项未运行，第三轮亦为10 PASS、1 FAIL，完整冻结验收仍待完成。实现与验证契约见[设计](usbfs-checked-release-design.md)。完整[开发索引](../evidence/checked-release-development-20261023.json)记录 25 份产物的长度、SHA256、来源和缺项。

## 测试路径与来源

沿用[host-state syscall-entry harness](usbfs-host-state-races.md)，仅新增 `--operation release`。两个一次性 x86_64 QEMU 6.2.0 来宾使用 Linux `6.8.0-106-generic`、FullSpeed UHCI ECM 和私有 socketpair。普通 UID1000、零 capability 的实际 server 经独立 root helper 获得 root0600 USBFS 节点 FD；来宾管理进程和 tracer 执行受限干预。没有操作宿主 USB、模块、ptrace 或网络配置。

A 是 `SET_CONFIGURATION(2)`，门停在首个 `USBDEVFS_RELEASEINTERFACE` 的 syscall-entry，尚未进入该 ioctl 的内核处理程序。B 是可排队的 `SET_CONFIGURATION(1)`；UNLINK 模式通过 B 的取消回复形成 worker witness，EOF 模式先确认 B 已被接受。后到 C 是端点 0x82 的 64B IN。既有 8 秒门、ARMED 后 3 秒 RPC、预算和所有权断言保持。

放门前，guest root 仅对获授权 QEMU IF0 写入 `/sys/bus/usb/drivers/usbfs/unbind`。独立观察必须确认 IF0 由 usbfs 变为 None，IF1 仍为 usbfs、alt1，配置仍为 1；代际、节点、server FD 和预算保持。该变化单独记录为 `release-fault`，不冒称产品自行提前释放。Linux 6.8 的 [driver_disconnect](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/devio.c#L714) 清除真实 claim 位，随后 [releaseintf](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/devio.c#L812) 因位已清除返回 `-EINVAL`，没有修改寄存器、伪造 errno 或替换生产 USB 操作。

两次均使用默认 `cargo build --workspace --release --locked --all-features`。运行时 Git HEAD 为 `6845ebc2ff2baafe6423643085e75efa012bf4a4`，工作树包含新增测试分支，属于 dirty 开发证据。共同[源码快照元数据](../evidence/qemu-checked-release-old-product-source-20261023/source.json)在 dev01 前捕获，记录生产来源等于 `54da6151defe57a9a10de2f321ca1d13c0d8bbe3`；四个精确脚本与两份 summary 的 SHA 均一致。**dev02 复用该共同快照，没有另行捕获其运行前快照。**

## 两次原始失败

| 运行 | 原始结果 | 实际观察及裁决 |
| --- | --- | --- |
| [dev01 UNLINK summary](../evidence/qemu-checked-release-old-product-unlink-dev01-20261023/summary.json) | FAIL；probe 完成 4 个 case | server 的 EXIT marker 记录真实 -22；client 对 seq100 实际收到 status0/actual0，严格预期 -22，因而在 `active_completion_wins` 失败。 |
| [dev02 EOF summary](../evidence/qemu-checked-release-old-product-eof-dev02-20261023/summary.json) | FAIL；probe 完成 5 个 case | trap 留存的 34 条 trace 前缀显示目标 release=-22，随后另一 release=0，再出现 SET_CONFIGURATION entry/exit0 和新配置 claim。adapter 的 snapshot-25 报告 configuration2，触发 RESTORED 的配置1严格断言失败；client 随后收到 short frame。 |

dev01 的 [client 串口](../evidence/qemu-checked-release-old-product-unlink-dev01-20261023/client-serial.log)保留完整预期/实际 wire 错误；[server 串口](../evidence/qemu-checked-release-old-product-unlink-dev01-20261023/server-serial.log)保留持门、取消 witness、故障干预和 EXIT marker。host 在发现 client FAIL 后按既有逻辑等待约 0.5 秒提前收尾；没有持久完整 trace、beacons、stopped 或最终资源清理证据，不能补造。

dev02 的 [server 串口](../evidence/qemu-checked-release-old-product-eof-dev02-20261023/server-serial.log)保留 trap 的 trace、snapshot 和此前保存的 sysfs 文件。snapshot-25 是 adapter 观察；其中 configuration2 **不是独立 sysfs2 证据**。trap 的 `/tmp/sysfs.json` 仍为先前 `release-fault` 的配置1、IF0 None/IF1 usbfs 记录。该轮在严格 RESTORED 检查处退出，缺少最终 stopped、tracer finished、资源0和完整恢复结果；不能据此声称永久恢复失败。[client short frame](../evidence/qemu-checked-release-old-product-eof-dev02-20261023/client-serial.log)是下游失败，不能替代服务端根因。

两份 summary 的 QEMU guest exit code 虽均为 0，整轮仍为 FAIL；失败收尾不证明 ordinary server/tracer 正常完成。两轮均未执行到运行后 source/binary guard，相关 after 字段缺失，不能声称该 guard 通过。串口在 trap 中重复打印的 probe JSON 只是同一结果的两份副本，不重复计算 case。

[独立负向复核](../evidence/checked-release-old-product-independent-review-20261023.json)直接检查原始产物，69/69项复核通过，原两轮裁决仍为FAIL；另存[源码与设计复核](../evidence/checked-release-design-independent-review-20261023.json)及[归档核对](../evidence/checked-release-development-archive-verification-20261023.json)。

## 归档与剩余验收

两个原始运行目录及各自外部 runner log、共同四脚本/source.json、两份静态记录逐字节复制；四个目录各附 `artifacts.sha256`。原始 CRLF、ANSI、空 stderr 和失败内容均保留，未补写运行时产物。索引中的 21 份来源复制和 4 份新生成清单共 25 项；聚合索引本身不自引用。

[静态记录](../evidence/qemu-checked-release-static-20261023/host-state-release-harness-static-20261023.json)明确是已观察 tool 结果的派生转录及独立构建物哈希，没有重构 raw 编译日志；独立 ELF 不归档。新解析器对[旧六项 clean 原日志的只读回归](../evidence/qemu-checked-release-static-20261023/host-state-release-old-six-parser-20261023.json)通过 48 个 case，不代表重新运行 QEMU，也不能替代本轮修复验证。

后续须用修复产品证明原 -22 的 wire 传播、剩余 IF1 显式释放继续完成、全程零 SET_CONFIGURATION 入口、后到 C 使用实际空 map，以及原双 CDC driver/config1/alt0,1 和全部资源的真实恢复；EOF 场景不要求关闭连接上出现 A 回复。完成开发与软件 gate 后，还须在同一 clean 提交执行新两项和既定回归。checked ioctl 的结果不等于完整状态恢复；本轮不证明原无 driver/非零 alternate 通用恢复、内核内部 active 窗口、真实硬件或整个 Phase2。


## 产品修复与软件验证

`4e81a1195b871d605a7f334f2183231ee6ceea7c` 为固定 nusb 增加 Linux-only safe `Interface::release_checked()`。它保留真实 RELEASEINTERFACE/CONNECT errno；最后 owner 在调用前解除析构重试，共享引用返回 Busy 且不发 ioctl。原 portable release/Drop 契约保留。worker 全部逐项 await 并保留首错；release 失败不执行 SET_CONFIGURATION，随后重新观察实际配置。原 alternate 临时 claim 同样 checked 释放，即使 select 失败也等待并记录 release。独立确认设备消失的既有 Gone 分支不声称原驱动恢复。

[独立产品复核](../evidence/checked-release-product-independent-review-20261023.json)的 217 项通过，包含原 70 文件来源、许可证、精确 patch 重建及未增加 unsafe（346 token）。[完整软件 gate](../evidence/checked-release-software-validation-20261023.json)在 clean `4e81a11` 执行 `bash scripts/check.sh`，exit0；默认89、all-features114、vendor19，共222次执行，0失败/忽略，fmt、两组Clippy、release及workspace严格rustdoc通过，96份软件输入前后不变。

补充 vendor tokio-only 严格 rustdoc 在修复前后均因相同三个未启用 futures_io 链接失败，原始日志保留。按固定 crate 的 docs.rs metadata 使用 all-features 后严格 rustdoc 通过；未修改原链接或放宽检查。[四平台原生 CI](../evidence/publication-checked-release-20261023.json)实际全部 completed/success，Ubuntu22.04/24.04 × x86_64/aarch64 每格同样222次及两项背压检查通过，[原日志与API](../evidence/ci-checked-release-4e81a11-20261023/artifacts.sha256)按原字节归档。CI不证明设备转发。

## 第一轮 clean 候选矩阵：10 PASS、1 FAIL

同一 clean `4e81a11` 串行运行十一项，运行期间没有仓库写入或并行构建；[原 sequence](../evidence/checked-release-candidate-sequence-20261023.json)和[106份归档索引](../evidence/checked-release-candidate-20261023.json)保留整体 FAIL。

- 新 release UNLINK/EOF 及原 configuration/alternate/clear 六项全部 PASS，共64项 wire case、16个guest exit0。真实首release=-22，后续IF1 release=0，首次 reclaim 前仅有这一次后续release；全trace没有SET_CONFIGURATION入口。UNLINK收到A=-22/UNLINK0；EOF不要求已关闭连接回复。最终同代际原cfg1/alt0,1/双cdc_ether及资源0恢复。
- ECM回归 PASS，19项probe，两个周期均实际UDP38B请求/39B响应。独立私有PCAP解码、IPv4/UDP校验和、USBMON同tag的模型STALL -32/1 opaque数据及恢复通过。
- 原HID/FTDI normal回归 PASS，15项probe与70B精确串口输出。
- hot回归严格 FAIL：server原ttyS0的 `RUSTUSB_PHYSICAL_REINSERT_STALE` 标记被内核 `hid-generic` printk 从中间插断。两guest exit0及可观察的Gone/FTDI继续/140B/资源0不替代host缺完整marker的裁决；不拼接原字节、不补填未到达的after字段。

[独立原始证据复核](../evidence/checked-release-candidate-independent-review-20261023.json)913项检查通过，85份原始输入哈希不变；这里通过的是复核完整性，运行结果仍为10/1。旧FTDI runner未持久原始host payload，独立复核仅依host计数/SHA及guest精确内容断言，不假造额外raw解码。

## 物理证据通道修正

只修改物理QEMU runner与guest init：ttyS0继续收集完整kernel console，ttyS1记录程序输出；host在两通道持续检查原FAIL及四类内核完整性错误，来宾退出后再次检查晚到日志。每角色须有唯一精确通道beacon，所有旧marker、USB断言、字节匹配和超时保持。

[双UART开发索引](../evidence/physical-dual-serial-development-20261023.json)的 dirty `4e81a11` 热拔测试完整 PASS（15项probe、140B精确输出、两guest exit0），是开发验证，不能替代接下来的clean十一项最终矩阵。[独立开发复核](../evidence/physical-dual-serial-dev01-independent-review-20261023.json)113/113通过；首轮FAIL保持原样，本修订不修改生产Rust代码。


## 第二轮 clean 候选矩阵与启动诊断

物理证据通道修订和前述全部证据已提交并push `ab44a8445d35d9c738f6441c9a42c924c35b0107`。在该clean提交预捕全部12脚本，串行v2矩阵前5项PASS（release两项、configuration两项、alternate UNLINK；41项wire检查）；第6项alternate/EOF在 `discovery_import` 以EAGAIN11失败、cases0、elapsed3.540519493秒。[v2索引](../evidence/checked-release-candidate-v2-20261023.json)和[原sequence](../evidence/checked-release-candidate-v2-sequence-20261023.json)保留FAIL/5 PASS/1 FAIL/5 NOT_RUN，剩余clear两项、ECM、normal、hot没有运行，不补造证据。

[独立失败诊断](../evidence/checked-release-alternate-eof-candidate-v2-independent-diagnosis-20261023.json)38项事实/来源检查通过，运行裁决仍FAIL。原stage同时覆盖DEVLIST与IMPORT多个I/O，不能确定哪次调用失败。READY已收到，ARM/A尚未发送；server末尾cdc_ether unregister不能证明IMPORT完成。原runner仅等0.5秒便收尾，缺完整server trace和after guard，均不补填。

[两次诊断开发索引](../evidence/host-state-diagnostic-development-20261023.json)保留32份产物。随后只改测试诊断：独立probe为各次DEVLIST/IMPORT I/O记录START/终态/耗时/真实errno，原Result直接返回，不重试、不输出payload，仍共用原frame2秒。host首次看到FAIL后锁定失败，最多30秒且受原overall期限约束地等待peer现有trap输出；不会重新进入PASS验证。4项实际runner轮询AST模拟检查和2项Rust诊断包装测试通过。

有诊断的dirty单项dev01（仍固定nanosleep1ms）完整PASS；IMPORT header等待1.519263924秒。tracer现将fork之后的parent独立阻塞SIGCHLD，空闲时用 `sigtimedwait` 等待通知、上限仍1毫秒，保留全部waitpid状态、原signal转发、控制轮询、256事件batch与门8秒。child不继承新mask，信号只负责唤醒，不代替真实stop/exit。语义依据为Linux man-pages的 [sigtimedwait](https://man7.org/linux/man-pages/man2/sigwaitinfo.2.html) 和 [ptrace](https://man7.org/linux/man-pages/man2/ptrace.2.html)。

该优化后的dirty单项dev02也完整PASS，IMPORT header等待9.122247毫秒。两次各7项probe、两guest exit0、恢复和source/binary guards通过；这只是两个观察值，**不是性能基准，也不能倒推旧v2失败的准确调用或唯一根因**。产品源码与clean4e81a11软件gate一致，未修改任何wire2/RPC3/gate8通过期限。[独立开发复核](../evidence/host-state-diagnostic-dev01-dev02-independent-review-20261023.json)208/208通过，包含真实trace配对和三文件源码审查。准备提交冻结后再执行distinct v3完整矩阵。


## 第三轮与物理启动快照修正

同一clean `19ffa119107e169765789ccdd071e860fca195af` 的v3十一项已全部结束，前10PASS；最后hot在select-simulated-devices前置FAIL。[v3原始索引](../evidence/checked-release-candidate-v3-20261023.json)保留整体FAIL。该轮8项host-state共64wire检查、ECM19和normal15通过；热拔未开始，不能按guest0补算15case。

原始server-evidence.log的devices.json确有HID和FTDI，但FTDI interfaces为空且有cached_descriptors_unreadable/bNumInterfaces等4个blocker。原loop仅按设备数2提前退出，随后原最终完整断言拒绝；helper/IMPORT尚未执行，不是已观察的USB转发故障。

只将物理guest启动loop的break改为原最终完整predicate：设备数2且全部export_blockers为空。原100次、50ms间隔和最终断言均保留，产品scanner和授权不改。实际失败快照、明确标注的synthetic ready/单设备/空列表四个fixture通过，旧谓词会提前结束，新谓词继续等待。[启动开发索引](../evidence/physical-startup-development-20261023.json)中的dirty hot dev03完整PASS（15case、140B精确FTDI、两guest0），[独立复核](../evidence/physical-startup-hot-dev03-independent-review-20261023.json)113/113通过。随后提交冻结distinct v4完整矩阵，不覆盖v3失败。

`19ffa11` 的[四平台CI](../evidence/publication-checked-release-harness-20261023.json)也实际全部completed/success，每格222项、0失败/忽略及两项4096B stdout背压通过；[完整原log/API](../evidence/ci-checked-release-harness-19ffa11-20261023/artifacts.sha256)已保留。这不改写其QEMU矩阵FAIL。
