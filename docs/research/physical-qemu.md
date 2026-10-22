# 真实 usbfs 用户态 adapter 的隔离 QEMU 验证

2026-10-22，正常两周期和热拔开发验证均已通过；最终干净提交验证仍待执行。本文对应 [物理后端设计](rust-physical-backend-design.md)，与 [纯虚拟 HID/CDC 验证](userspace-qemu.md)分开。设备仍由 QEMU 创建，不能替代真实硬件 G1/G2 验收。

## 设备、权限和执行边界

新增三个独立文件：`scripts/qemu/run-physical-qemu.py`、`guest-physical-init.sh`、`physical-wire-probe.rs`。复用 Native 的 initramfs/ELF 复制函数，旧九场景和纯虚拟脚本不改。两来宾使用继承的 Unix socketpair，无 host TCP listener、tap、宿主 USB 透传、宿主模块加载或网络配置。

server 来宾的 xHCI 固定连接 QEMU `usb-kbd` 和 `usb-serial`，真实来宾内核先枚举并绑定 `usbhid`、`ftdi_sio`。前者默认 High-Speed、VID/PID `0627:0001`、EP0=64；后者为 Full-Speed FT232BM、`0403:6001`、EP0=8、厂商类及两个 Bulk endpoint。FTDI不是CDC。速度、描述符、VID/PID和client驱动均独立核对，不继承纯虚拟设备的参数假设。

server 的两个指定 usbfs 字符节点保持 root 0600；UID1000和1001直接读写打开都必须失败。root helper 的 `physical_device_ids` 只允许这两个扫描fingerprint，Native `export_device_ids` 为空。普通 UID1000 `physical-server` 经 helper 接收 SCM_RIGHTS FD，再由 nusb 在 IMPORT 时真实 detach-and-claim。核对 server 进程四项UID1000、CapEff/CapPrm/CapAmb0、实际拥有对应 `/proc/PID/fd`；UID1001 的 physical-server 启动必须因 helper 权限拒绝而失败。另以UID1001通过有界std UnixStream直接尝试OpenPhysical请求，核对单帧JSON的准确`data/error/ok`字段、`ok=false`、`data=null`、明确caller拒绝及随后连接关闭。helper在读取请求前即可拒绝UID，因此未消费请求的Unix连接可能以reset终止；只有完整拒绝帧后的EOF或reset可接受，write失败或reset本身都不能作为授权拒绝证据。实际关闭方式另有marker。该probe不解析SCM_RIGHTS，ancillary拒绝由daemon安全接口单元测试覆盖。

两来宾的 root 测试协调器负责网络、模块、root策略和QEMU生命周期，server应用不以root运行。server不加载 `usbip_host` 或 VHCI。client UID1000通过已有helper和官方usbip导入到VHCI；UID1001不能取得管理权限。无 udev 宽泛节点授权或全应用提权。

## 命令与来源记录

```sh
python3 scripts/qemu/run-physical-qemu.py --evidence /tmp/rustusb-physical-normal
python3 scripts/qemu/run-physical-qemu.py --hot-unplug --evidence /tmp/rustusb-physical-hot
```

默认 locked `--all-features` release 构建，确保CLI physical-nusb与helper physical-usb同时启用；独立probe只用std，以Rust2024、`-O -D warnings`编译。`--skip-build`仅供开发，最终证据不能使用。运行器拒绝root宿主执行、非法kernel-release token或覆盖已有证据目录；guest init只允许PID1且具有显式QEMU标记。

记录commit、dirty、Cargo.lock、crate源码、vendor/nusb Rust与manifest、自身三个文件及共享构建脚本的指纹；另记录kernel/initramfs/原生工具/probe/CLI/helper SHA256。复制到guest内的CLI/helper再次比对来源，结束核对源码及release binary没有变化。证据写入独立目录，保留全部FAIL，最终冻结提交后在/tmp执行，统一归档原始字节。

## 正常两周期的严格断言

独立wire probe通过真实物理adapter完成动态 DEVLIST/IMPORT、1B descriptor上界、真实设备速度/EP0、未支持控制请求的 `-EPIPE` stall及后续存活。HID显式SET_IDLE0后IN必须pending；UNLINK须为`-ECONNRESET`，随后QMP KEY_3由新IN准确收到down/up，既不能出现旧seq回复，也不能被未回收的旧URB吞掉输入。再提交真实pending IN时关闭TCP，等待取消/reap、两设备重新可用和本地driver恢复。

同一TCP write还连续发送有效SET_CONFIGURATION及其后HID IN，要求配置先成功，后到IN继续正确pending并可取消，不能被错误地按旧配置stall或以`-ESHUTDOWN`退休。这样实际覆盖状态变更期间到达的端点请求，而非只按同步顺序逐条测试。

另对FTDI真实Bulk测试原长度0/1/15，避免只验证8B HID和EP0而漏掉上游端点API的MPS限制。0B OUT必须actual0；1B IN保持pending并取消，若返回EINVAL则失败。0B IN记录实际观察：合法零长度完成，或pending后取消，均不能变成InvalidArgument。随后host专用标记只注入13B固定probe数据，15B IN必须精确收到2B FTDI状态头和13B内容。该13B与正常串口70B RX分开列账，零长度OUT不增加host TX。模型在请求长度不超过2或没有RX数据时NAK，因此不虚构“空状态包”或本模型未产生的overflow；1B descriptor同时测试SETUP=18/URB=1的clamp和SETUP=1/URB=1的一致请求。

随后两个正常周期同时导入HID/FTDI，快照和sysfs接口driver均必须为usbfs。client打开evdev订阅及tty，关闭tty本地echo，持有串口fd并建立接收reader后才发布输入标记。每轮额外原始IMPORT两个占用设备必须准确返回busy status2，原会话仍继续完成KEY_1/KEY_2按下及释放、35B带轮次RX逐字节比较和35B不同TX。host全程读取Unix chardev，最终严格核对TX顺序、精确70B、SHA256及无额外字节；没有虚拟设备的echo替代路径。

每轮detach后核对client sessions0、两个旧sysfs设备消失；远端可发现作为有界释放barrier。server快照必须 `available`、leasedfalse、pending/queued/inflight0、buffer预算0、cleanup_verifiedtrue、current_drivers与original_drivers准确相同；sysfs独立确认usbhid/ftdi_sio重新绑定。第二周期必须重新导入并重复实际功能，最后正常shutdown、exit0且相同清理断言成立。逻辑marker或逻辑pending0单独不能表示真实USB取消/恢复完成。

## 可选热拔阶段

`--hot-unplug`在两正常周期后额外导入两设备，保持真实HID IN订阅。FTDI完成一帧实际I/O，再由host QMP删除HID；必须记录 `DEVICE_DELETED`，server旧USB设备/节点消失、slot Gone及真实请求回收，client VHCI释放且旧设备目录消失。FTDI的receipt保持相同并继续另一帧精确I/O，再单独释放、恢复ftdi_sio。此模式host累计严格比较四帧TX共140B。

同一QMP端口重新添加HID后必须是新devnum/node，节点仍root0600；旧server slot仍Gone，远端只能发现FTDI，证明没有自动继承旧FD授权。此阶段不声称已经执行新的显式CLI/helper授权；重新注册需要新的授权请求和新generation receipt。被拔出的旧设备不要求恢复不存在的driver，剩余FTDI必须实际恢复；全局真实请求与预算仍须归零。

QMP命令回复与异步事件分开保存，不能把device_del返回成功当成拔出完成。FTDI输入也只在client完成配置和reader准备后注入：client驱动的FTDI RESET会清RX；服务端原样保留FTDI USB包的两字节状态头，由client驱动解释，不能静默转换为CDC数据。

## 开发运行及保留的失败

四次运行均使用默认locked全feature构建、QEMU6.2.0/KVM、Linux6.8.0-106-generic、普通宿主UID1000及两个隔离来宾。它们是在`2fde196`之后尚未提交的开发源码上执行，`working_tree_dirty=true`；运行期间源码与release二进制保持冻结。每个目录保留build/runner日志、两端完整serial、QEMU stderr、summary及准确三脚本快照，并有逐artifact SHA256索引。来宾poweroff的exit0不代替断言，前两次仍准确记为FAIL。

| 开发运行 | 结果 | 实际观察与修复边界 | 原始证据 |
| --- | --- | --- | --- |
| normal dev01 | FAIL | UID1001在请求读取前已被helper拒绝；CLI写入EPIPE覆盖了缓冲的明确拒绝。产品随后修复同连接上读取有界失败响应，不重试操作。此时尚未IMPORT设备。 | [summary](../evidence/qemu-physical-normal-dev01-20261022/summary.json)、[全部SHA](../evidence/qemu-physical-normal-dev01-20261022/artifacts.sha256) |
| normal dev02 | FAIL | CLI已准确显示caller拒绝；独立OpenPhysical probe等待读到EOF时遇到reset而失败。只修harness：完整有界单帧后允许EOF/reset并记录实际方式，继续严格检查JSON拒绝。此时尚未IMPORT设备。 | [summary](../evidence/qemu-physical-normal-dev02-20261022/summary.json)、[全部SHA](../evidence/qemu-physical-normal-dev02-20261022/artifacts.sha256) |
| normal dev03 | PASS，两guest exit0 | 15个probe结果全PASS，两周期真实evdev及FTDI；host精确收到70B，释放与driver恢复均通过。OpenPhysical观察到`reset_after_frame`。 | [summary](../evidence/qemu-physical-normal-dev03-20261022/summary.json)、[全部SHA](../evidence/qemu-physical-normal-dev03-20261022/artifacts.sha256) |
| hot dev01 | PASS，两guest exit0 | 重复正常两周期后实际删除HID，保留FTDI同receipt继续I/O；重新添加HID不自动继承授权。host精确收到140B。 | [summary](../evidence/qemu-physical-hot-dev01-20261022/summary.json)、[全部SHA](../evidence/qemu-physical-hot-dev01-20261022/artifacts.sha256) |

normal dev03与hot dev01使用相同CLI/helper二进制SHA256，分别为`50399de4a02e62a115ebf3beaa8f5c0929ccfd2d10fe22e3d156b6ff3eb7ae16`和`45901b13944cc6436f0165026c7b633dd422a46a74a1df658def132ab7f11f0f`。两次均实际观察FTDI零长度IN保持pending并取消；15B IN准确含2B状态及13B专用payload。normal的内核tty累计RX/TX各70B；hot各140B，专用13B不混入tty账。host TX SHA256分别为`a40e2368e405f8bcc8b832ee7d42b3d471b41363046704a77e0834444ca36cb5`和`7d2284a6df1220f6daa02362b9cc884b2aca1a86386c045f8157f27b090c5e2c`，且关闭尾部无额外字节。

normal末尾真实usbfs累计reaped为HID42、FTDI69；两周期释放和最终shutdown均pending/queued/inflight/buffer归零，恢复usbhid/ftdi_sio。hot拔出前HID仍有真实inflight1，`DEVICE_DELETED`后Gone/inflight0/reaped56，client旧设备消失；FTDI在拔前与拔后各完成35B双向内容，最终reaped103、恢复ftdi_sio。Gone槽位中的driver名称是最后已知快照，不表示不存在的设备仍绑定usbfs；不对已拔除设备要求driver恢复。

## 静态检查与后续验收

[第四次静态原始结果](../evidence/qemu-physical-static-04-20261022.txt)包括宿主误调用和非法kernel token的exit2拒绝；[首次静态结果](../evidence/qemu-physical-static-20261022.txt)及[当时源码快照](../evidence/qemu-physical-static-source-01-20261022/artifacts.sha256)保留，[第二次](../evidence/qemu-physical-static-02-20261022.txt)增加直接OpenPhysical拒绝检查，[第三次](../evidence/qemu-physical-static-03-20261022.txt)增加配置与后到IN的pipeline检查，第四次要求QEMU退出后继续读chardev至EOF。两次开发失败修复后再次执行Python编译、shell语法、rustfmt及独立rustc `-D warnings`，均通过；准确修订源码在各运行的`source/`保留。

开发PASS尚不作为最终干净提交验收。待产品与harness提交冻结后，还须在干净HEAD默认locked构建重跑normal、hot，并运行原纯虚拟userspace场景作回归。nusb扩展、helperFD授权、shared engine和CLI的产品测试由各自证据记录，不能替代实际USBFS运行；QEMU设备也不能替代真实硬件。

- [固定 nusb c80218d Linux实现](https://github.com/kevinmehall/nusb/tree/c80218d342740321190436bc45304ebbbf16bef0/src/platform/linux_usbfs)：open/claim、显式reset、取消/reap、接口恢复和FD能力边界。
- [QEMU v6.2.0 FTDI模型](https://github.com/qemu/qemu/blob/v6.2.0/hw/usb/dev-serial.c)：FT232BM、描述符、状态头、RX清空及可能阻塞的chardev OUT。
- [QEMU v6.2.0 HID模型](https://github.com/qemu/qemu/blob/v6.2.0/hw/usb/dev-hid.c)：默认USB2键盘及实际描述符。
- [QMP v6.2.0 热移除](https://github.com/qemu/qemu/blob/v6.2.0/qapi/qdev.json)：device_del与DEVICE_DELETED的区别。
- [Linux v6.8 usbfs实现](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/devio.c)：FD权限、接口claim/release、disconnect和回收；仅参考行为，没有复制GPL实现。
