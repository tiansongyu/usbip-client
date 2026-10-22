# Rust 用户态虚拟服务端的 QEMU 内核互操作验证

2026-10-22。本报告验证可选 Rust 虚拟 HID Keyboard、标准双接口 CDC ACM 服务端与真实 Linux VHCI 的互操作。它与原有 Native 九场景分开；两个固定虚拟 profile 不能证明物理 rusb/nusb、任意 Composite/alternate、ISO、TLS 或完整 Phase2 已完成。设计见 [Rust 虚拟后端设计](rust-virtual-backend-design.md)，固定上游缺陷依据见 [源码审计](rust-backend-audit.md)。

## 隔离环境与复现

运行器只启动两个一次性 x86_64 来宾，使用继承的 Unix socketpair 连接各自 e1000，地址为 server `192.0.2.1`、client `192.0.2.2`，两边 IP forwarding 都为0。没有 host TCP listener、tap、宿主网络配置、宿主 USB 透传或宿主模块加载；QEMU 也没有添加 USB 设备。内核文件及模块仅从宿主读取复制到临时 initramfs。

server 来宾 root 仅管理网络和启动测试；实际 `rustusb virtual-server` 从启动到退出均为 UID1000，控制 FIFO 的写入者也是 UID1000。运行前后核对同一 PID 的 `/proc` starttime 和四项 UID，server 不加载 `usbip_host` 或 `vhci_hcd`。client 普通 UID1000 CLI 经已有 root helper 调用官方 usbip，client 内核加载 VHCI、usbhid、cdc_acm；UID1001 的管理请求必须被 helper 拒绝。

本轮环境是 Ubuntu x86_64、Linux `6.8.0-106-generic`、QEMU `6.2.0`，实际加速方式、完整版本和二进制 SHA256 逐次记录在 `summary.json`。需要匹配内核、真实 ELF usbip/usbipd 工具、busybox、cpio、jq、setpriv、Rust 和 QEMU；CDC 模块不存在且未明确编入内核时直接报错。kernel-release 参数必须为单个安全 token。若 `/boot` 文件权限限制，运行器仅以已有 sudo 权限复制指定内核到临时目录。

```sh
python3 scripts/qemu/run-userspace-qemu.py \
  --evidence /tmp/rustusb-userspace-evidence
```

默认执行 `cargo build --workspace --release --locked --features rustusb-cli/rust-backend`，并独立用 `rustc --edition=2024 -O -D warnings` 编译 std-only wire probe。`--skip-build` 仅供开发，不能作为最终证据。已有 evidence 目录会被拒绝覆盖。运行期 Rust/manifests/lockfile、旧共享构建脚本、三个新脚本或 release binary 指纹变化都会使整体 FAIL。

## 实际断言

两个 Full-Speed 虚拟设备为 `1-1` HID `1234:0001`、`1-2` CDC ACM `1234:0002`。这些 VID/PID 仅为隔离 fixture，未声称商用分配。CDC 的75B配置描述符含标准通信/数据接口及 CDC 功能描述符，真实 Linux `cdc_acm` 绑定并创建 `ttyACM0`；它与旧 Native harness 的 QEMU FTDI `0403:6001` 串口是不同设备。

1. 独立 probe 不复用产品 codec。核对1B descriptor上界、interface255 的 `-EPIPE` stall、CDC OUT/受限 IN FIFO、11种畸形连接的拒绝和后续 DEVLIST 两设备恢复。完整非法头保留写端，要求服务端主动关闭；只有故意截断的握手和数据头使用半关闭表明 EOF。畸形集包括未知版本、IMPORT 前 SUBMIT、截断握手、重复 IMPORT、65537B OUT、负长度编码、非法 direction/endpoint、错误 devid、ISO count 和截断数据头。
2. HID 显式 SET_IDLE0 后，200ms 内无输入的 IN 必须保持 pending。UNLINK 回复必须为 `-ECONNRESET`，随后 SET_IDLE1 使错误保留的请求具备4ms重复报告条件，再观察75ms不得出现旧 completion；后续 descriptor 和迟到 UNLINK 继续准确完成。观察窗口是有界回归证据，不是无限时间保证。
3. 同一 server 进程经畸形测试后仍 UID1000、lease/pending0。真实 client 内核同时持有两个 profile，两次额外原始 IMPORT 分别得到准确 busy status2，然后原会话继续实际 I/O；这证明已持有时的独占，不声称两个新客户端同时竞速。
4. 两个 attach/detach cycle 各自创建真实 evdev 和 ttyACM。evdev 订阅在控制注入前由父 shell 打开，核对第一轮 KEY_1、第二轮 KEY_2 的按下及释放；持续读取时两份 helper receipt 保持相同。
5. tty 关闭本地 echo，设置57600 baud、8 data bits，server snapshot 必须实际收到与默认115200不同的 line coding 和 DTR/RTS=3。每轮 server 主动注入35B含轮次帧，client逐字节比较；client再发35B不同帧，收到 server OUT→IN echo 后逐字节比较。双方仅记录计数、结果、SHA256，不记录这些 USB 数据内容。
6. 每轮 client detach 后先有界等待远端两设备重新可发现，再核对 server lease/pending/队列0、configuration0，以及 client sessions0、两个旧 sysfs 设备移除。第二轮正常重连并重复完整 I/O，最后正常 shutdown，server stopped 快照仍无 lease/pending。

独立 CDC probe 自身收发16B，单独列账。两轮内核串口合计 OUT70B、IN140B，其中主动注入70B、回显70B；整个进程累计 OUT86B、IN156B、注入70B。运行器独立计算所有已知测试帧顺序的 SHA256，与 server 累计摘要比较，防止只靠 marker 或长度误判。HID 累计两个 tap，每个均是完整8B down 加8B release；server 的 Control/Bulk/Interrupt 实际计数均须为正。

## 原始运行记录

每个目录保留已经产生的原始 serial、QEMU stderr、build.log、runner.log、summary，以及该次新脚本的准确快照。`artifacts.sha256` 覆盖其余每个 artifact；原始文件从 `/tmp` 逐字节复制，没有改写日志或覆盖失败。当前三脚本的语法、独立 Rust 编译、宿主误调用拒绝和非法 kernel token 拒绝另见 [静态检查](../evidence/qemu-userspace-static-20261022.txt)。

| 运行 | 结果 | 观察与处理 | 原始证据 |
| --- | --- | --- | --- |
| dev01 | FAIL | 新 Python runner 在 QEMU 尚未创建 serial 文件时立即读取，触发 FileNotFoundError；尚未开始 USB 断言。改为 Popen 前建立两个日志文件。 | [summary](../evidence/qemu-userspace-dev-01-20261022/summary.json)、[runner](../evidence/qemu-userspace-dev-01-20261022/runner.log) |
| dev02 | FAIL | 独立13个 wire PASS marker 完成，两设备真实枚举并 managed/status6；harness 把带前导空格的 bNumInterfaces 与字符串2比较。改为数值比较，保留两个接口的严格断言。 | [summary](../evidence/qemu-userspace-dev-02-20261022/summary.json)、[client](../evidence/qemu-userspace-dev-02-20261022/client-serial.log) |
| dev03 | PASS，开发工作树 | 两来宾 exit0，两设备同时持有、两轮 evdev down/up、CDC exact I/O及累计 SHA、清理均通过，运行期指纹一致。随后只加强 harness：远端释放 barrier、非默认 baud、UNLINK 条件触发观察、完整非法头主动关闭，以及负长度/endpoint16。 | [summary](../evidence/qemu-userspace-dev-03-20261022/summary.json)、[server](../evidence/qemu-userspace-dev-03-20261022/server-serial.log)、[client](../evidence/qemu-userspace-dev-03-20261022/client-serial.log) |
| dev04 | PASS，开发工作树 | 新 CLI stdout 背压修复后，强化的15个 wire PASS marker、两轮57600 line coding/DTR/RTS、远端释放 barrier及完整 I/O全部通过；两来宾 exit0，源码/脚本/二进制运行前后指纹相同。 | [summary](../evidence/qemu-userspace-dev-04-20261022/summary.json)、[server](../evidence/qemu-userspace-dev-04-20261022/server-serial.log)、[client](../evidence/qemu-userspace-dev-04-20261022/client-serial.log) |

最终 clean-commit 默认 locked-build 证据尚待产品与 harness 冻结后执行；开发 PASS 不冒充最终验收。当前原始失败都属于 harness，dev03 不要求产品行为修复。独立复核发现的 CLI stdout 背压处理由主任务另行修复并记录，dev04 已包含该修复。

Linux 对 bNumInterfaces 的 `%2d` 格式可在固定 [Linux v6.8 USB sysfs 源码](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/sysfs.c#L44)核对；wire 对照 [Linux USB/IP 公开协议](https://docs.kernel.org/usb/usbip_protocol.html)。仅参考规范和格式事实，没有把 GPL 源码复制进 Rust 实现。
