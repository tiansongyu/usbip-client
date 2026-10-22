# Native 双 QEMU 互操作与恢复矩阵

本记录扩展 [HID 基线](native-qemu.md)，验证 RustUSB 管理层与发行版 USB/IP 工具的互操作，以及外部释放后的受管会话协调。所有 USB 设备均由 QEMU 创建；操作发生在隔离来宾内。没有物理 USB、Rust USB/IP 服务端、CDC ACM、WAN 或 TLS 的通过声明。

## 设备和源码依据

本机 QEMU 为 `6.2.0 (Debian 1:6.2+dfsg-2ubuntu6.31)`。`qemu-system-x86_64 -device help` 列出 `usb-kbd` 和 `usb-serial`，`-device usb-serial,help` 提供 `chardev` / `always-plugged`。官方 [v6.2.0 dev-serial.c](https://gitlab.com/qemu-project/qemu/-/blob/v6.2.0/hw/usb/dev-serial.c) 明确模拟 FTDI FT232BM，VID/PID `0403:6001`，一个厂商类 `0xff` 接口、两个 Bulk 端点。**它不是 CDC ACM**；测试同时核对实际来宾的 VID/PID 和 `ftdi_sio`，不把产品名称当驱动支持证据。

串口设备使用以下 QEMU 参数：

```text
-device qemu-xhci,id=usb
-chardev socket,id=serial,path=<temporary>/ftdi.sock,server=on,wait=off
-device usb-serial,bus=usb.0,chardev=serial,always-plugged=on
```

Unix chardev 的双向 socket 参数见固定版本 [qemu-options.hx](https://gitlab.com/qemu-project/qemu/-/blob/v6.2.0/qemu-options.hx)。`always-plugged=on` 保持 USB 设备附着，避免测试进程连接时机变成热插拔。源码的 OUT 路径使用 `qemu_chr_fe_write_all`，可能阻塞 QEMU 执行线程，因此 runner 全程读取 host chardev，只发送短的有序测试帧，给读写设截止；本测试不对持续吞吐、UART 流控、长时稳定性作结论。QMP `ringbuf-write` 写入的是输出环形缓冲，不是向串口 RX 注入，不能作为同等的双向测试方法。

来宾使用 `usbserial` → `ftdi_sio` 模块依赖顺序。串口以 raw / no-echo / 115200 配置；每周期注入 `RUSTUSB-RX-<cycle>-0001:0123456789abcdef\n`，客户端逐字节比较完整输入，并发送 `RUSTUSB-TX-<cycle>-0001:fedcba9876543210\n`。runner 对两帧完整输出、长度、顺序、额外字节进行核对，记录输出 SHA256。内容全部由测试明确生成，不采集用户 USB 数据。

## 场景与断言

入口为 `scripts/qemu/run-native-qemu.py`。不带参数仍执行 managed HID 的两个完整周期；每个普通场景也执行两个周期。

| 场景 | 导出/导入/清理路径 | 必需断言 |
| --- | --- | --- |
| `managed` | UID1000 RustUSB export/attach/detach/unexport，经两端 root helper | 客户端唯一受管 VHCI、正确 VID/PID 与原生 class driver、每周期实际功能、detach 后内核无会话且设备目录移除、server usb/class driver 恢复、重新连接 |
| `linux-client` | RustUSB export → guest root 官方 usbip list/attach/port/detach → RustUSB unexport | 原生 list 和 port 包含所选 Bus ID/endpoint；helper 将外部会话标为 unmanaged、拒绝 detach，拒绝后会话及实际功能仍在；原管理工具负责释放 |
| `linux-server` | guest root 官方 usbip bind/usbipd → UID1000 RustUSB discover/attach/detach → 官方 unbind | helper 拒绝接管外部导出，拒绝后绑定及 AVAILABLE 状态不变；客户端实际功能、释放和 server 原生 driver 恢复 |
| `reconnect`，仅 HID | managed import → 官方 detach → RustUSB 直接 reattach → 官方 detach → 官方 attach 同端口/同路径 → helper 拒绝控制 → 官方 detach | 第一周期旧内核连接及 sysfs 已消失；第二周期无需旧 RustUSB detach 清缓存即可认领并产生实际输入；第三周期同 port/local Bus ID/VID/PID 的新代际为 unmanaged，旧 helper detach 拒绝且仍能产生输入；最终 server 正常 unexport |

所有场景还要求 UID1001 被 helper 拒绝。外部操作仅由来宾 root 测试脚本执行，不给产品 CLI 提权。两台来宾的 e1000 NIC 仅经继承的 Unix socketpair 互连；没有宿主 tap、TCP listener、USB passthrough、模块加载或 usbipd。

最终矩阵在两端各执行三次真实 `--json doctor`：helper 启动前 `missing`，启动后 `available` 且 OS root peer 已验证，UID1001 只读诊断为 `permission_denied` 且已验证 root peer。doctor 命令退出0只表示完成观察，不表示该 UID 获得设备权限。另核对固定 usbip version 可用；usbipd 版本探测只接受有版本的 `available` 或无版本的 `requires_root`，记录实际状态。Ubuntu 本机对应内核的 ELF 工具普通 UID 执行 `usbipd --version` 实测 stdout 提供2.0版本、stderr 同时提示 not running as root、exit0，因此应分类 available；不能仅因 stderr 提示就声称版本探测失败。server host sysfs 可用但其 helper 无 VHCI，client VHCI 可用。三份完整 JSON 保存在 serial 证据内，schema 与内建加密/网络安全/真实硬件的未验证标志均逐项断言。旧产物开发运行在新 doctor 加入前执行，其脚本指纹独立保留，不混作该项证据。

```bash
python3 scripts/qemu/run-native-qemu.py
python3 scripts/qemu/run-native-qemu.py --scenario linux-client --device hid
python3 scripts/qemu/run-native-qemu.py --scenario linux-server --device hid
python3 scripts/qemu/run-native-qemu.py --scenario managed --device ftdi-serial
python3 scripts/qemu/run-native-qemu.py --scenario linux-client --device ftdi-serial
python3 scripts/qemu/run-native-qemu.py --scenario linux-server --device ftdi-serial
python3 scripts/qemu/run-native-qemu.py --scenario reconnect
```

默认先执行 locked release 构建；`--skip-build` 只供开发复用已核对的产物。环境与内核复制权限要求沿用 [原测试说明](native-qemu.md)。每个 evidence 目录保存原始两端 serial、QEMU stderr 和 summary。summary 记录场景、设备、commit/dirty、源码/脚本/二进制、Cargo.lock、内核镜像/initramfs/官方工具 SHA256、QEMU 版本和 guest 退出码；运行中源码变化不能 PASS。shell 自检、host 实际输入注入、严格输出比较、必需断言标记和两台 QEMU 正常退出同时满足才通过。

## 旧版本真实回归

冻结 Rust 源码为已发布 `21e98f9644974c8dd204e6f476b52d14715cb629`，在独立 `/tmp` 本地 clone 中复制本轮矩阵脚本，执行 locked release 构建及 reconnect。工作树 dirty 仅表示新测试脚本，不混入后续产品修复。编出的 CLI/helper SHA256 与原发布产物完全一致；确切来源和脚本指纹见 [summary](../evidence/qemu-reconnect-old-20261022/summary.json)，两端完整日志保存在同目录。

结果 **FAIL**：cycle1 实际 KEY_1、官方 detach、内核 sessions=0、旧设备目录消失均通过。cycle2 RustUSB 直接 attach 返回 `new import used an already recorded port`。失败后的权威 sessions 却已有 port0/status6/local_bus_id=1-1/managed=true，内核日志显示 USB device number 从2变3，证明新连接已经产生而旧缓存错误匹配。该结果复现旧缓存错误与新副作用，不属于脚本环境失败，也不代表整个矩阵已执行。

为先验证测试脚本，在同一冻结 clone 复用刚构建且已核对 SHA256 的旧产物，另执行以下开发测试；它们使用 `--skip-build`，不作为修复后的发布证据。

| 开发场景 | 结果和证据 |
| --- | --- |
| managed FTDI 两周期 | [PASS](../evidence/qemu-ftdi-harness-old-20261022/summary.json)：每方向每周期35字节，host70字节完整有序输出、两次 ftdi_sio 恢复、两 guest exit0 |
| Linux client HID 两周期 | [PASS](../evidence/qemu-linux-client-harness-old-20261022/summary.json)：原生 list/attach/port/detach、外部会话拒绝控制、两次实际输入和资源释放 |
| Linux server HID 两周期 | [PASS](../evidence/qemu-linux-server-harness-old-20261022/summary.json)：原生 bind/unbind、外部导出拒绝接管、两次实际输入和驱动恢复 |

## 当前版本结果

最终冻结提交为 **`b4cbfc3bd6c63eb22d8296bfa835767586aa345b`**（产品实现提交 `a4d258c`）。以下7项全部执行默认 `cargo build --workspace --release --locked`、exit0，启动时工作树均 clean。先执行 managed HID 验证 doctor，再以最多3组独立 guest 并行执行其余6项；所有 evidence 最初写入隔离 `/tmp` 目录，全部测试终结后才一次复制入仓库。复制前后每个原始文件 SHA256 相等，完整汇总见 [矩阵索引](../evidence/qemu-matrix-20261022.json)。

| 命令参数（入口同上） | 结果及持久 evidence | 实际覆盖 |
| --- | --- | --- |
| `--scenario managed --device hid` | [PASS / exit0](../evidence/qemu-matrix-20261022-managed-hid/summary.json) | 2周期 KEY_1、受管 export/attach/detach/unexport、usb/usbhid 恢复 |
| `--scenario linux-client --device hid` | [PASS / exit0](../evidence/qemu-matrix-20261022-linux-client-hid/summary.json) | 2周期原生 list/attach/port/detach、外部会话拒绝控制、实际输入和释放 |
| `--scenario linux-server --device hid` | [PASS / exit0](../evidence/qemu-matrix-20261022-linux-server-hid/summary.json) | 2周期原生 bind/unbind、helper 拒绝接管外部导出、实际输入和驱动恢复 |
| `--scenario managed --device ftdi-serial` | [PASS / exit0](../evidence/qemu-matrix-20261022-managed-ftdi-serial/summary.json) | 2周期双向精确字节、ftdi_sio、受管清理及驱动恢复 |
| `--scenario linux-client --device ftdi-serial` | [PASS / exit0](../evidence/qemu-matrix-20261022-linux-client-ftdi-serial/summary.json) | 官方客户端、外部会话拒绝控制、2周期双向精确字节及清理 |
| `--scenario linux-server --device ftdi-serial` | [PASS / exit0](../evidence/qemu-matrix-20261022-linux-server-ftdi-serial/summary.json) | 官方服务端、外部导出拒绝接管、2周期双向精确字节及恢复 |
| `--scenario reconnect --device hid` | [PASS / exit0](../evidence/qemu-matrix-20261022-reconnect-hid/summary.json) | 3次实际 KEY_1；外部 detach 后 RustUSB 直接认领同端口；再外部重导入同端口/路径/remote ID，新 receipt 为 unmanaged，旧 helper detach 明确拒绝且输入仍在；最终由原管理方释放 |

14个 guest 均正常退出0。三个 FTDI 场景各自两周期、每方向每周期35字节；host 输出各为精确70字节，顺序、长度和无额外输出均核对；这是短功能验证，不能当成长时稳定性或吞吐结果。reconnect 已修复旧版本真实 FAIL，并通过新代际拒绝控制的独立周期；它仍是外部本地释放测试，不是 WAN 故障验收。

独立复核不仅看 PASS 标记，还从7项原始 serial 中解析 **42份 doctor JSON**，再次逐项确认 helper missing/available/permission_denied、OS peer 验证、角色所需 sysfs、server helper 无 VHCI/client 有 VHCI、两个工具的实际明确状态、schema 和未验证 flags。全部 usbipd 版本探测实际为 `available`，未将普通 UID 的 exit0 误写成权限已经获批。

7项源码、脚本、锁文件和产品二进制指纹完全一致，运行中源码没有改变。整体源码/脚本 SHA256 为 `66f3db9acbf7d2fab307805d6988cb402a0119b1eeb9d4e26bbbc801955a8097`；CLI 为 `fc1cc5ebb9ecd8fd8094438137bc3577c59a5876c3faba978abcb8586b9b82a7`，helper 为 `cb59c5fec9fe53a6a42f8de4c256c58cbe3b4264b43e11b312795e19455a9dad`。同一内核镜像和官方工具哈希也一致；initramfs 分别记录各次实际归档哈希，不宣称含时间戳的归档逐字节可复现。结果归档后的修改仅为文档和 evidence。

## 仍未覆盖

两个隔离 Linux 来宾不等于两台真实 Ubuntu 的物理设备验收；Ubuntu24.04/aarch64、真实设备类别及完整 Phase1 硬件 gate 仍未完成。现有上游虚拟 CDC 实验也不等于本项目 Native CDC 测试。

本轮不运行三来宾多客户端争用、netem 高延迟/丢包、WAN 断网或完整热插拔矩阵。后续可用 server 两个独立 socketpair 连接两个不同 client guest，验证另一客户端 IMPORT 被拒、原会话继续功能、释放后另一方可成功导入。单一 client 重复 attach 不能代替多主机争用。

QMP [v6.2.0 set_link](https://gitlab.com/qemu-project/qemu/-/blob/v6.2.0/qapi/net.json) 可改变模拟 NIC 链路，但命令成功不能证明 TCP 已超时；官方接口也注明有设备可能不发链路通知。Linux [v6.8 IP sysctl](https://www.kernel.org/doc/html/v6.8/networking/ip-sysctl.html) 的默认 TCP keepalive 等待以小时/分钟计。本轮外部 usbip detach 是明确的本地内核释放故障，不能描述成已实现几秒 WAN 断线检测、心跳或自动恢复。后续 netem 操作只允许来宾的专用实验接口，并核对注入参数、真实 RTT/丢弃计数与清理。
