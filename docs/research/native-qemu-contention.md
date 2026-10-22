# Native 三 QEMU 客户端争用与交接

本测试用一个 server 和两个具有独立 Linux 内核的 client guest，验证已由 A 持有的模拟 USB 键盘不能再被 B 导入，B 的失败请求不改变 A 的连接，A 正常释放后 B 能接手并实际输入。它不是两个首次 attach 的同时竞速测试，也不证明物理设备、存储一致性、Rust USB/IP 服务端、WAN 或加密通道。

## 拓扑与授权

```text
client-a 192.0.2.2 ── private socketpair A ── 192.0.2.1 server
                                                     QEMU usb-kbd
client-b 192.0.3.2 ── private socketpair B ── 192.0.3.1 server
```

server 有两个独立 e1000，A/B 各有一个；每个 socketpair 的 fd 只传给对应 guest。两条链路不桥接，三端均断言 `ip_forward=0`，没有宿主 tap、TCP listener、外部网络或 USB passthrough。server 只创建 `qemu-xhci` 和 `usb-kbd`，不加载宿主模块，不绑定宿主 USB。guest root 配置各自模块、实验网卡和最小 helper policy；设备导出、A/B 受管导入及释放均由 UID1000 CLI 经 root helper 完成。三端另断言 UID1001 的具体授权拒绝，doctor 不冒称网络安全或真实硬件已验证。

## 判定与来源

1. B 在 A 持有前读取一次 DEVLIST，保存模拟设备 Bus ID；A 经自己的 helper 导入，核对唯一受管 VHCI、`0627:0001` 与 `usbhid`。
2. A 在父 shell 先打开 evdev 并建立采样文件，再把已打开 fd 交后台 reader；先接收 QMP 数字1，reader 持续到 B 的拒绝完成后。
3. server 核对 `usbip_status=2`，通知 B 发起争用。B 实际执行 UID1000 RustUSB attach，要求非0、内核 sessions 仍为0，并记录具体拒绝原因。若发行版 DEVLIST 隐藏 USED 设备，本项目观察到的是 `requested device is absent from remote export list`，不是声称 CLI 收到了 busy。
4. B 独立发送公开线协议的40字节 IMPORT 请求，严格比较完整8字节回复 `01 11 00 03 00 00 00 02`：version `0x0111`、IMPORT reply `0x0003`、status `2 / ST_DEV_BUSY`。另执行官方 `usbip attach`，要求其明确 `Device busy`、非0且 B 无 VHCI 端口。状态2不是仅从失败退出码推测。
5. server 核对仍为 USED；A 重读完整 session，与原 attach receipt 完全相等，仍受管；同一 reader 实际接收数字2。这证明 B 失败请求后 A 的真实 I/O 和所有权继续有效。
6. A 经 helper detach，核对 sessions=0、旧 USB sysfs 目录消失。server 变 AVAILABLE 后通知 B；B 以 UID1000 RustUSB attach，实际接收数字3，再正常 detach、核对 sessions=0/设备移除。
7. server 正常 unexport，核对设备 driver=usb、interface driver=usbhid。A/B 最后分别 DEVLIST=0；所有 guest 必须正常退出0。

Linux 来源固定为 v6.8 commit `e8f897f4afef0031fe618a8e94127a0934896aba`：[usbip_export_device 的 USED→ST_DEV_BUSY](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/tools/usb/usbip/libsrc/usbip_host_common.c)、[usbipd IMPORT 回应与 DEVLIST USED 过滤](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/tools/usb/usbip/src/usbipd.c)。这些是源码依据；Ubuntu 实际工具行为以本次 guest 原始日志为准。测试只构造公开协议请求，不复制 Linux GPL 用户态实现进入 MIT 产品源码。

## 执行与证据

```bash
python3 scripts/qemu/run-contention-qemu.py
```

前提沿用 [Native 双 QEMU](native-qemu.md) 的 x86_64、内核/模块/ELF usbip 工具、BusyBox/cpio/jq/setpriv。默认 locked release 构建；`--skip-build` 仅供开发复用已核验产物。`--kernel` 选定匹配内核，`--timeout` 为30–1800秒，默认180秒；普通宿主误执行 guest init 会在 mount 前拒绝。未获得读取内核镜像权限时仅沿用原 runner 的临时内核复制流程，不操作宿主 USB 或网络。

新入口 `run-contention-qemu.py` import 原 `run-native-qemu.py` 的 payload/ELF依赖复制、模块处理、QMP 函数；原七项脚本没有修改。共享 initramfs 建成后，只在临时 root 中替换 `/init` 为 `guest-contention-init.sh` 并重新归档。summary 同时记录四份新/复用脚本哈希，组合指纹覆盖所有 Rust/manifest/锁文件及四脚本，运行中任何覆盖内容变化不能 PASS。

证据目录保存三个原始 serial、三个 QEMU stderr、summary 和可用的 runner stdout/stderr。summary 记录 commit/dirty、默认构建标志、锁文件/源码/脚本/CLI/helper/内核镜像/initramfs/官方工具 SHA256、QEMU版本、三个特定输入注入、三端断言及退出码。runner 不仅看总 PASS，还检查 busy wire header、CLI 管理拒绝、A receipt/I/O不变、B交接输入、两客户端移除、server driver恢复等必需证据。

## 开发运行

| 运行 | 结果与证据 | 原因/处理 |
| --- | --- | --- |
| 01 | [FAIL](../evidence/qemu-contention-dev-20261022-01/summary.json) | UID、B RustUSB absent、完整 wire busy、官方 busy、A 同 receipt 数字1/2、A释放均通过，B也已导入并枚举。harness 后台cat尚未创建采样文件，前台wc读文件立即失败；原日志保留 `can't open /tmp/input.bin`。修为父shell先建文件并打开evdev，再交已打开fd给reader，不改产品 |
| 02 | [PASS / exit0](../evidence/qemu-contention-dev-20261022-02/summary.json) | 父shell先建立采样文件和evdev订阅后，三端全部断言通过；B RustUSB明确观察not_advertised、独立wire status2、官方Device busy；A两次输入与同receipt、B交接输入和三端清理通过。此为dirty开发版本，最终另行冻结复测 |

两个开发目录也各自保存当次新 runner/init 脚本快照，逐文件 SHA256 与各自 summary 相等；01/02 的采样器差异因此可以直接审阅。快照用于来源核查，执行仍使用仓库 `scripts/qemu/` 入口。

## 最终冻结复测

待 root 提交冻结脚本及本轮源码后执行，开发运行不能替代最终 clean 证据。

## 故障复用边界

两个 server NIC 分别具有显式 QEMU id `link-a-nic` / `link-b-nic`，后续可以 QMP `set_link` 精确作用于一条模拟链路；返回成功不能证明 TCP 已断开或出现有界故障检测。server 对指定模拟 Bus ID 写 `usbip_sockfd=-1` 属主动关闭内核 socket，不是 WAN 黑洞。Linux 6.8 普通 TCP reset/EOF 通常触发 SHUTDOWN|RESET 并最终 NULL，不能要求必定持久 ERROR；ERROR/UNKNOWN 身份无法确认时应保留状态并明确拒绝猜测清理。杀 `usbipd` 管理进程也不能证明已交给内核的数据 socket 结束。网络故障另有独立 harness 和实测，本争用报告不替代它。
