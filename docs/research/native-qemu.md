# RustUSB Native 双 QEMU 验证

本测试验证本项目的 Native 路径，区别于 `jiegec/usbip` 的模拟协议服务端测试。它在两个隔离的 Linux guest 中运行 RustUSB CLI、root helper、发行版 usbip 工具和内核驱动，把 QEMU 提供的 USB 键盘从 server guest 重定向到 client guest；通过 QMP 注入按键，再检查客户端真实 evdev 输入。**QEMU 模拟 USB 设备通过，不代表物理设备验收通过。**

## 范围与环境

2026-10-22 已验证的环境是 Ubuntu 22.04.5 x86_64 宿主、`6.8.0-106-generic` guest 内核、KVM。两个 guest 使用同一份宿主内核和模块，没有 Ubuntu 24.04、aarch64、真实 USB、WAN、VPN/TLS、丢包、存储写入或长时性能的验证证据。脚本目前仅允许 x86_64 宿主。没有 `/dev/kvm` 权限时会选择 TCG，但本次通过证据来自 KVM，不能提前称 TCG 已测通过。

```text
server guest 192.0.2.1                     client guest 192.0.2.2
QEMU USB keyboard                         Linux usbhid → evdev KEY_1
       ↓                                           ↑
usbip_host → usbipd ── private socketpair ──→ vhci_hcd
       ↑                                           ↑
root helper ← UID1000 rustusb CLI          root helper ← UID1000 rustusb CLI
```

每个 guest 的 helper policy 均由 guest root 建立；UID1000 执行 CLI，允许名单只含该模拟设备或指定测试 peer。另以 UID1001 验证请求被拒绝。`trusted_network=true` 仅是这个隔离实验网络的授权条件，**没有建立 TLS 或身份认证通道**。

## 执行方法

先在普通用户会话准备以下命令与资源：

- `python3`、`cargo`、`qemu-system-x86_64`、静态 `busybox`、GNU `cpio`、`jq`、util-linux `setpriv`、`ldd`。
- `/boot/vmlinuz-<kernel>` 和匹配的 `/lib/modules/<kernel>`；其中需有 USB/IP、VHCI、HID、xHCI、e1000 驱动或相应内建配置。若模块使用 zstd 压缩，还需 `zstd`；xz/gzip 使用 Python 标准库解压。
- 与 guest 内核对应的真正 ELF `usbip`、`usbipd`。Ubuntu 的 `/usr/bin/usbip` 可能是版本选择 shell wrapper，脚本优先取 `/usr/lib/linux-tools/<kernel>/` 下的 ELF，并在 guest 固定放到 `/usr/bin/`，符合产品的固定命令路径校验。

```bash
# 默认以锁文件构建工作区 release，再启动两个guest
python3 scripts/qemu/run-native-qemu.py

# 指定已安装并具有匹配工具/模块树的内核
python3 scripts/qemu/run-native-qemu.py --kernel 6.8.0-106-generic
```

脚本拒绝 root 身份运行。宿主不会加载 USB/IP 模块、启动 usbipd、绑定设备或访问 `/dev/bus/usb`。两台 QEMU 的 NIC 通过继承的 Unix socketpair 连接；没有 tap、宿主 TCP listener、公网连接或物理 USB passthrough。QMP 仅是临时目录内的 Unix socket。initramfs 与 guest 数据都在临时目录中生成。

若宿主内核镜像不可读，脚本仅用 `sudo -n cp` 将内核复制到此次临时目录，再将临时副本设为可读；没有免密权限时会明确失败，**不保证无 sudo 环境可直接执行**。不应为测试放宽产品的目录/文件权限检查。initramfs 目录显式0755，归档UID/GID为0，运行期 `/run` 为0755、policy为0644。

`guest-init.sh` 仅可由 guest init 使用：第一条特权操作前要求 PID1 与内核提供的 `RUSTUSB_QEMU_GUEST=1`；挂载proc后立即核对 `rustusb.role=server/client`，然后才挂载其他文件系统。普通宿主调用实测 exit2，未执行 mount。不要直接在宿主运行此init脚本。

`--skip-build` 仅适合开发时复用已核对的二进制；发布证据使用默认 `cargo build --workspace --release --locked`。`--timeout` 是有界运行总观察期限，默认180秒，允许30–1800秒；单次内部等待也有期限。脚本结束会回收其QEMU进程。

## 断言与证据

两个周期均要求：

1. Server只读发现恰好一个模拟设备，导出条件完整，指纹进入root policy；UID1000调用helper导出。
2. Client使用本项目DEVLIST解析器发现该设备；UID1000经helper调用Native attach；核对唯一新VHCI端口、VID/PID、devid和本机连接记录。
3. 实际出现usbhid与evdev；宿主QMP注入数字1；client采样到`EV_KEY / KEY_1 / value=1`。
4. Client detach后sessions为0且该USB设备的sysfs目录消失。
5. Server unexport后设备driver回到`usb`，interface driver回到`usbhid`；remote DEVLIST变为0。
6. 第一周期后重新export/attach，再执行完整第二周期。

观察到PASS标记只是必要条件。当前runner还逐项核对两周期输入、移除和本地驱动恢复标记、QMP注入次数，并要求两台QEMU正常退出0。`summary.json`记录commit、工作树是否dirty、Cargo.lock SHA256、两个脚本SHA256、Rust源文件/manifest整体指纹、CLI/helper二进制SHA256、guest退出码及错误诊断。源码在guest运行中变化会导致测试失败。它不会用一次DEVLIST成功证明URB数据连接成功。

原始server/client serial日志、QEMU stderr与summary保存到 `docs/evidence/qemu-native-<UTC timestamp>/`，不会只保留 `/tmp` 路径或孤立hash。日志中的USB字符串与按键均来自明确创建的模拟键盘，不是宿主真实设备内容。

## 首轮五次运行记录

下表保留测试发现和修复过程。只有第五次完成整条链路；它的二进制是中间版本，后续产品修复必须在最终提交上重新验证，不得用它代替发布版本结果。

| 运行/证据 | 实际结果 | 原因和处理 |
| --- | --- | --- |
| [10:02:56](../evidence/qemu-native-20261022T100256Z/summary.json) | FAIL，未完成导出 | BusyBox ash的standalone applet命中简化版setpriv，不支持`--reuid`。修为显式`/usr/bin/setpriv`，不改变产品权限控制 |
| [10:03:52](../evidence/qemu-native-20261022T100352Z/summary.json) | FAIL，helper正确拒绝启动 | `/run`新挂载tmpfs默认模式过宽；改为`mode=0755`，并规范整个initramfs目录权限；同时设置两台guest独立MAC，消除IPv6重复地址诊断 |
| [10:04:43](../evidence/qemu-native-20261022T100443Z/summary.json) | FAIL，已经完成导出、attach与HID枚举 | BusyBox timeout结束采样返回143，脚本错误地仅接受GNU的124。改为由后续实际evdev内容判断采样成功，不以timeout工具差异作为结果 |
| [10:05:55](../evidence/qemu-native-20261022T100555Z/summary.json) | FAIL，两周期HID输入均通过，末尾移除断言失败 | VHCI端口先变NULL，USB sysfs目录异步稍后移除；揭示detach完成语义需要更强条件。脚本增加有界的独立devicegone验证，产品层后续补旧sysfs generation清理，不把瞬时portNULL当全部完成 |
| [10:07:08](../evidence/qemu-native-20261022T100708Z/summary.json) | **PASS，完整两个周期** | UID1001拒绝、UID1000全链路、两次KEY_1、两次detach移除、两次unexport后usb/usbhid恢复均有原始日志。该次commit基线`ef12209`、工作树dirty；确切binary SHA256见summary |

这些结果证明本项目Native实现已通过有真实Linux内核参与的模拟设备端到端测试。仍需独立的指定物理测试设备、两端管理员授权和不同发行版/架构验证，才能满足物理兼容性和Phase1完整硬件验收。
