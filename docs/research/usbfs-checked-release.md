# USBFS checked release：旧产品负向 QEMU 证据

2026-10-23。两轮旧产品基线均为 **FAIL**，已重现真实 `RELEASEINTERFACE=-EINVAL` 被吞掉的行为。本报告尚不包含修复后或 clean 最终验收；实现与验证契约见[设计](usbfs-checked-release-design.md)。完整[开发索引](../evidence/checked-release-development-20261023.json)记录 25 份产物的长度、SHA256、来源和缺项。

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
