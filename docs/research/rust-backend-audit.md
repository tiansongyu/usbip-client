# jiegec/usbip Rust 后端源码审计

审计日期：2026-10-22。读者：RustUSB 核心与安全层维护者。结论是将该库用于受限的实验后端和虚拟设备测试，物理设备日常共享优先采用 Linux Native 后端。下文区分 **代码事实**、**本机实测**、**上游报告**和**设计建议**；发现设备、构建通过、模拟设备工作，都不等于真实设备兼容性已验证。

## 1. 固定审计对象与方法

- 仓库：[jiegec/usbip](https://github.com/jiegec/usbip)。审计提交：[61938f8c48ed01c508faaacfa7cb3fc3810b5ae0](https://github.com/jiegec/usbip/commit/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0)，提交时间 2026-09-04 +0800。克隆位置 `/tmp/rustusb-upstream-jiegec`，未将上游源码复制进 RustUSB。
- `Cargo.toml` 声明版本 `0.9.0`、Edition 2024、MIT；[v0.9.0 标签](https://github.com/jiegec/usbip/tree/5561aa4abf372f5d3eb05c65b1474a9489b735f8) 是较早的 `5561aa4abf372f5d3eb05c65b1474a9489b735f8`。**同名版本号不等于同一源码**：HEAD 比该标签新增 QEMU、demo 和 HID 控制请求修复，`num-derive` 也改变。本文 QEMU 结论属于审计提交，不能自动归于 crates.io 0.9.0。
- 已逐一阅读任务指定的 `lib.rs`、`usbip_protocol.rs`、`host.rs`、`device.rs`、`interface.rs`、`endpoint.rs`、`setup.rs`，并阅读 `cdc.rs`、`hid.rs`、`consts.rs`、`util.rs`、全部 4 个示例、全部 3 个 QEMU 脚本和 CI。
- 源码固定链接中的 `#L…` 是审计时的精确位置。复现实验见 [`scripts/audit-rust-upstream.sh`](../../scripts/audit-rust-upstream.sh)。脚本构建上游并在独立临时 crate 中调用其公开 API；不会修改上游源码，不接触物理 USB 设备。`RUN_QEMU=1` 才执行虚拟机测试，QEMU 脚本可能用 sudo 读取内核镜像，未加载宿主 USB/IP 模块、未绑定或导入宿主设备。

## 2. 架构和两条数据路径

**代码事实**：`UsbIpServer` 用两个 Tokio `RwLock` 保存 available `Vec<UsbDevice>` 和 used `HashMap<busid, UsbDevice>`；每个 TCP 会话由 `tokio::spawn` 独立处理。协议解析器接收 DEVLIST、IMPORT、SUBMIT、UNLINK；设备对象生成描述符，再分派给同步 `UsbDeviceHandler` / `UsbInterfaceHandler`。[服务器状态](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L42-L56)、[会话循环](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L391-L570)、[处理器 API](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/interface.rs#L18-L44)。

```text
实验 Rust 后端：远端 vhci_hcd → USB/IP TCP → Tokio handler
              → UsbDevice 描述符/请求分派 → rusb/libusb 或 nusb → 本地 USB
虚拟设备：    远端 vhci_hcd → USB/IP TCP → Tokio handler → 内存 HID / CDC handler
Native 后端：远端 vhci_hcd → USB/IP TCP → 本地 usbip_host 内核驱动 → 本地 USB
```

Rust 用户态后端没有在数据路径里调用 `usbip_host`；将同一设备先 bind 到 `usbip_host` 再交给 rusb/nusb 会产生所有权冲突。Native 与 Rust 后端只能按设备互斥选择。该库提供服务端，没有 Linux VHCI 管理客户端；RustUSB 客户端第一阶段仍应使用 Linux 原生 `usbip attach/detach` 与 `vhci_hcd`。

## 3. USB/IP 协议范围与语义缺口

| 项目 | 源码事实 | 本次验证与边界 |
| --- | --- | --- |
| 版本与控制请求 | `USBIP_VERSION=0x0111`；解析 DEVLIST/IMPORT，返回对应 reply | 上游序列化、未知版本、未知命令测试通过；本机 QEMU 的 Linux usbip 可 list/attach 模拟设备 |
| 字节序 | USB/IP 多字节整数按网络大端；USB SETUP value/index/length 按小端 | 现有序列化测试与实际枚举通过；不等于完整畸形输入校验 |
| SUBMIT | 顺序处理一个 URB 并等待发送响应，忽略 transfer flags、start frame、interval 等执行语义 | 模拟控制、bulk IN、interrupt IN 工作；没有独立完成队列与真实 URB 并发调度 |
| UNLINK | 忽略 `unlink_seqnum` 对实际 I/O 的取消，只返回 status=0 | 序列化测试通过；没有证据证明实际取消，也无法及时中断同步 USB I/O |
| ISO 描述符 | 收取 `16 * number_of_packets` 字节，但 session 不传给设备处理器，响应始终空 ISO 描述符、0 packets | 只能认定能解析部分线格式，不能认定 ISO 传输实现 |
| 错误状态 | RET_SUBMIT 错误统一 status=1，未建立 Linux 负 errno 映射；USB I/O 错误常被吞掉 | 缺失 stall、timeout、no-device、cancelled、short-transfer 的保真映射 |
| 状态与字段校验 | 无强制 IMPORT→SUBMIT 状态门；direction/status 多处仅 debug_assert，未验证 devid、ep 范围和 seqnum 生命周期 | release 对抗性 probe 重现未 IMPORT 即 SUBMIT 的 panic |

依据：[协议常量/解析](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/usbip_protocol.rs#L18-L247)、[SETUP](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/setup.rs#L20-L29)、[SUBMIT/UNLINK 分派](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L462-L541)、[错误响应](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/usbip_protocol.rs#L467-L499)。协议要求的参照为 [Linux USB/IP protocol](https://docs.kernel.org/usb/usbip_protocol.html)。

## 4. Transfer Type 与描述符审查

| 类型/能力 | rusb 路径 | nusb 路径 | 可作出的支持声明 |
| --- | --- | --- | --- |
| Control | 同步 read/write_control，1s timeout | control_in/out `.wait()`，1s timeout；长度 u32→u16 强转 | 有代码路径；复杂 standard/class/vendor 请求、错误与取消尚未验证 |
| Bulk | 同步 read/write_bulk | `transfer_blocking` | 有代码路径；物理 U 盘、串口、JTAG 没有本次硬件通过证据 |
| Interrupt | 同步 read/write_interrupt | `transfer_blocking` | 有代码路径；仅模拟 HID 的 KEY_1 本次通过 |
| Isochronous | 没有分支；落入 `Ok(vec![])` | 没有分支；落入 `Ok(vec![])` | **未实现，禁止宣称支持 UVC/音频 ISO** |
| Composite | 收集多个 interface，但仅每个 interface 的第一个 alt descriptor | 同样；初始化即 claim 所有 interface | 只有多接口容器，不足以支持任意 composite 设备 |
| Alternate settings | 源码明确 `ignore alternate settings` | 源码明确忽略 | **未完整实现**；没有 alt 状态、endpoint 表切换或 `set_alt_setting` 调用 |

实际 I/O 分支：[rusb interface](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/host.rs#L17-L81)、[rusb device](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/host.rs#L105-L142)、[nusb interface](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/host.rs#L170-L286)、[nusb device](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/host.rs#L318-L386)。初始化：[nusb](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L58-L168)、[rusb](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L197-L277)。

**代码事实**：服务器为真实设备也重建 Device、Configuration、BOS、String 描述符，没有透明转发全部原始描述符。Configuration 内 interface number 由 Vec enumerate 重新编号、alt 固定 0、配置供电字段固定；nusb class-specific descriptor 直接空向量。SET_CONFIGURATION 在设备层被本地应答，没有调用物理 set configuration；SET_INTERFACE 仅作为普通 interface control 转发，没有更新当前 endpoint 集合。物理 interface number 不连续、IAD、多个 configuration、alt endpoints、SuperSpeed companion 描述符均无正确性保证。[描述符重建](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/device.rs#L299-L400)、[SET_CONFIGURATION 和 interface 分派](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/device.rs#L471-L534)。

**代码事实**：`device_bcd`、`usb_version` 存 major/minor/patch，但序列化只写 minor 与 major 两字节，丢失 BCD 低字节内 patch 位的正确组合。nusb 初始化未设置 `usb_version`、EP0 max packet 固定16、`dev_num=0`，speed 直接 `nusb::Speed as u32`。nusb 枚举从 Low=0 开始，与 Linux `enum usb_device_speed`（Low=1，Super=5）不同；必须显式映射，不可依赖 cast。[版本结构与序列化](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/device.rs#L1-L36)、[设备线格式](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/device.rs#L233-L259)、[nusb Speed](https://github.com/kevinmehall/nusb/blob/v0.2.8/src/enumeration.rs#L456-L473)。本次未连接物理 nusb 设备，以上属于源码判定。

## 5. 必须修复的安全性和生命周期问题

| 编号 | 发现、影响 | 证据与验证等级 |
| --- | --- | --- |
| R01 | OUT 长度直接 `vec![0; transfer_buffer_length]`；ISO 长度直接 `16 * number_of_packets`；无上限、总预算、连接数或读取期限。可被未认证客户端用于内存/连接耗尽 | [parser L185–215](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/usbip_protocol.rs#L185-L215)。**实测**接受并分配8MiB；未执行4GiB/OOM破坏性测试，4GiB级可能分配来自代码分析 |
| R02 | 未 IMPORT 就 SUBMIT 对 None unwrap | [lib L470](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L470)。**release probe 重现 task panic**；默认 Tokio 下通常终止该任务，不应夸大为必然整个进程退出 |
| R03 | Control interface index 用 `interfaces[index & 0xff]` 直接索引，低8位仍可越界 | [device L475/L514](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/device.rs#L471-L525)。**实测**单接口设备 index255 panic，随后设备仍不可用 |
| R04 | cleanup 只在下一次解析失败处执行；write `?`、panic、任务 abort 跳过 cleanup | [lib L397–405](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L397-L405)、[L459](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L459)。**实测**IMPORT response BrokenPipe 后 available数为0；正常 EOF 则恢复1 |
| R05 | 同会话第二次 IMPORT 先清空 current id，不释放前一个 used entry；可永久占用一个或多个设备直到重建 server | [lib L432–459](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L432-L459)。**实测**成功IMPORT→不存在的IMPORT→EOF，前一设备未归还 |
| R06 | rusb read错误变空成功，write错误 `.ok()` 忽略；nusb completion错误类似，OUT completion结果直接丢弃 | host.rs 各分支见上表。**代码事实**，未实测物理 stall/no-device；会误报USB写成功，存储不可接受 |
| R07 | libusb同步读取、nusb `.wait()`/`transfer_blocking`、std Mutex均在 async request路径内，单设备1s超时可占Tokio worker | [host L27–29](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/host.rs#L27-L29)、[nusb L240–282](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/host.rs#L240-L282)。**代码事实**，未测吞吐/CPU |
| R08 | `transfer_buffer_length` 只写在trait约定中，无统一响应限制；CDC/HID忽略它，设备描述符只按SETUP wLength截断 | [trait L26](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/interface.rs#L23-L34)、[CDC L53–94](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/cdc.rs#L53-L94)。**实测**请求1字节的bulk IN返回16；请求1字节Device descriptor返回18 |
| R09 | 初始化 nusb claim/first descriptor/speed unwrap；未知control type或recipient `unimplemented!`；虚拟设备未处理控制请求也可能 `unimplemented!`；Mutex poisoning可传播panic | [lib L80–83](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L80-L83)、[host L186–222](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/host.rs#L186-L222)、[device L489–534](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/device.rs#L489-L534)。**代码事实**；不能把所有unwrap都视为网络可利用：固定20字节数组切片的try_into unwrap是结构上安全的 |
| R10 | `remove_device` 先锁available再读used，而IMPORT/cleanup先锁used再锁available；不同任务可能锁顺序反转。used read锁还跨I/O await持有，慢连接可影响其他设备管理 | [remove L365–375](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L365-L375)、[handler L416–459](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L416-L459)。**代码推断**，未编写确定性调度复现，不宣称已经观测死锁 |
| R11 | 无认证、设备ACL、TLS、重放保护、受限权限API；host示例共享能打开的所有设备且监听0.0.0.0:3240 | [host example](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/examples/host.rs#L7-L11)。**代码事实**，只能置于RustUSB认证隧道及显式设备allowlist之后 |
| R12 | `debug!`/`trace!`包含原始req/resp，CDC甚至info打印USB bulk内容；不能直接作为RustUSB默认日志 | [host L26](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/host.rs#L26)、[lib L483–501](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/lib.rs#L483-L501)、[CDC L71–76](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/src/cdc.rs#L71-L76)。**代码事实** |

### 热插拔、物理独占与恢复

**代码事实**：`new_from_host` 做一次 rusb 枚举；`add_device/remove_device` 是手动列表操作，没有订阅热插拔事件、重新枚举协调或持久身份。生成 busid 包含 bus/address/port，nusb还写入固定0字段，都不能用作持久设备ID。拔出后原handle仍留在列表，I/O错误可能被当作成功空包。已有 [`#60`](https://github.com/jiegec/usbip/issues/60) 是同类上游报告，但被stale关闭不等于实现已补齐。

**代码事实**：会话独占通过available→used移动实现，上游`only_single_connection_allowed_to_device`测试验证了正常路径。它不能证明本地驱动/其他进程物理独占。rusb调用`set_auto_detach_kernel_driver(true)`并吞掉错误，但库内没有`claim_interface`调用；其自动detach开关不等于已经detach/claim。nusb初始化直接`claim_interface`，而不是Linux上的`detach_and_claim_interface`，本地驱动占用会导致unwrap panic。nusb/rusb handles会一直保存在server设备对象中，TCP结束仅移动对象，没有按会话release/re-attach。库的Drop能力也不等于RustUSB所需的会话级恢复。[nusb官方claim/detach区别](https://github.com/kevinmehall/nusb/blob/v0.2.8/src/device.rs#L87-L112)、[rusb DeviceHandle 的Drop](https://github.com/a1ien/rusb/blob/v0.9.4/src/device_handle.rs#L119-L132)。本次未卸载任何真实驱动，未验证物理资源恢复。

## 6. 现有测试、本机实测和环境

环境：Ubuntu 22.04.5 LTS x86_64；宿主与QEMU kernel `6.8.0-106-generic`；Rust `1.97.1` stable、Cargo `1.97.1`；QEMU使用KVM。本次测试均为模拟设备或内存socket，未连接测试U盘、串口、HID、JTAG、摄像头；未测Ubuntu24.04或aarch64。

| 命令/场景 | 实际结果 | 覆盖范围 |
| --- | --- | --- |
| `cargo test --all-features` | **30 passed，0 failed**；2 doctests ignored | 编解码、旧版本/未知命令拒绝、无效string index、普通独占与EOF归还、动态列表和基本描述符 |
| `cargo fmt -- --check` | exit0 | 上游代码格式 |
| `cargo clippy --all-features --all-targets -- -D warnings` | exit0 | 当前依赖/工具链静态lint，不证明I/O语义 |
| `./scripts/qemu/run-qemu-test.sh --build --dump-log` | exit0；`SERIAL_TEST: PASS`，字节`61`；`KEYBOARD_TEST: PASS`，KEY_1 down；`TEST_RESULT: PASS` | 真Linux VHCI成功枚举上游模拟HID与CDC ACM，读到串口数据和输入事件 |
| `scripts/audit-rust-upstream.sh`（release probe） | exit0，**重现6项缺陷**，验证正常EOF归还，验证8MiB分配 | R02、R03、R04、R05、R08两例；此处脚本成功表示复现预期缺陷，不是通过安全验收 |

QEMU还观测到 `usb 1-1: seqnum max` 及大量 `the urb (seqnum …) was already given back`。输入功能通过不能掩盖空interrupt响应导致的忙轮询风险，不能将这次结果描述为性能、长时稳定性、取消语义或断线恢复通过。QEMU脚本只测试list/attach与单向串口字符/按键；没有测试detach/reconnect、异常包、物理设备、双主机网络或存储一致性。[guest验收逻辑](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/scripts/qemu/init.sh#L113-L150)。输出`VERIFY_SERIAL=2`/`VERIFY_KEYBOARD=2`同时计入shell xtrace与echo，不能理解成做了两次测试。

首次完整QEMU日志位置 `/tmp/rustusb-upstream-jiegec/.qemu-serial.log`，SHA256 `855ded6a502b13b9f195928c45bd5f4559ed168dc7349bb5c8d17bbe141f4153`。上游首次解析的Cargo.lock SHA256 `9949289f5a0fc20ef87d471e91610e1ef6b79783bdb60ee40f48dba8dfb31339`。永久摘要见[测试记录](../evidence/rust-upstream-2026-10-22.json)、[release probe结果](../evidence/rust-upstream-probes-2026-10-22.txt)、[QEMU通过与异常诊断节选](../evidence/rust-upstream-qemu-2026-10-22.txt)。这些是本次证据，后续复现会因时间戳/解析依赖而变化。审计脚本将保留完整本次运行目录并输出路径，便于另行归档。RustUSB自身的双guest Native验证独立记录于[Native QEMU报告](native-qemu.md)，不混为同一测试。

```bash
# 无硬件测试；新建临时clone，校验固定commit并运行上游测试/对抗性probe
scripts/audit-rust-upstream.sh
# 额外运行虚拟Linux；需要qemu/busybox/cpio/usbip与可用内核模块树
RUN_QEMU=1 scripts/audit-rust-upstream.sh
# 复用已检查的clone
UPSTREAM_DIR=/tmp/rustusb-upstream-jiegec AUDIT_DIR=/tmp/rustusb-audit-evidence \
  scripts/audit-rust-upstream.sh
```

## 7. 许可证、依赖、版本与维护

**代码事实**：[LICENSE](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/LICENSE) 为MIT，版权2020–2025 Jiajie Chen；复用大段源码须保留版权及许可。它并不使Linux GPL源码变成MIT。Linux用户态工具应作为发行版外部程序调用，内核模块由发行版管理，不将Linux实现源码复制进MIT工程。

直接依赖在本次Cargo解析中的结果如下；版本约束见[Cargo.toml](https://github.com/jiegec/usbip/blob/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/Cargo.toml)。上游不提交Cargo.lock且未声明rust-version，RustUSB如果集成必须固定锁文件、补全SBOM与原生库许可证，不能把这张摘要当作完整发布清单。

| 依赖 | 上游约束 → 本次解析 | SPDX / 说明 |
| --- | --- | --- |
| usbip | 审计git commit，manifest 0.9.0 | MIT |
| tokio | 1.22.0 → 1.54.1 | MIT；本次metadata MSRV1.85 |
| rusb | 0.9.3 → 0.9.4 | MIT；依赖libusb1-sys |
| nusb | 0.2.1 → 0.2.8 | Apache-2.0 OR MIT；MSRV1.85 |
| log | 0.4.17 → 0.4.34 | MIT OR Apache-2.0 |
| num-traits | 0.2.15 → 0.2.19 | MIT OR Apache-2.0 |
| num-derive | 0.5.1 → 0.5.1 | MIT OR Apache-2.0；v0.9.0标签仍是0.4.2 |
| serde（可选） | 1.0 → 1.0.229 | MIT OR Apache-2.0 |
| env_logger（开发） | 0.11.7 → 0.11.11 | MIT OR Apache-2.0 |
| libusb1-sys（间接） | 0.7.0 | crate MIT；实际libusb原生库另有LGPL-2.1-or-later义务，不能被crate许可证覆盖 |

截至审计日，GitHub API返回仓库未archived/disabled，master最后push `2026-09-03T18:11:20Z`，1个open issue（#63）和1个open PR（#65）；crates.io API返回最新0.9.0发布于`2026-07-21T15:40:43Z`。动态页面缓存显示的数量有差异，以这次API快照及git对象为准。[仓库API](https://api.github.com/repos/jiegec/usbip)、[crate API](https://crates.io/api/v1/crates/usbip)、[上游CI](https://github.com/jiegec/usbip/tree/61938f8c48ed01c508faaacfa7cb3fc3810b5ae0/.github/workflows)。这证明近期有维护活动，不证明响应SLA或生产成熟度。

| 上游记录 | 审计日状态 | RustUSB如何解释 |
| --- | --- | --- |
| [#63 interrupt频率](https://github.com/jiegec/usbip/issues/63) / [PR#65延迟完成](https://github.com/jiegec/usbip/pull/65) | issue与PR均open | 需要pending URB、通知与真实UNLINK取消；尚未合入审计提交，不提前采用其作者测试结果 |
| [PR#64 真实串口两类panic](https://github.com/jiegec/usbip/pull/64) | 已合入，修复在审计提交 | 上游报告CH9102连接通过，不是RustUSB本次硬件验证；字符串读取失败已降级，但其他panic仍存在 |
| [#44 U盘/摄像头不可用](https://github.com/jiegec/usbip/issues/44)、[#55 FTDI等复杂设备](https://github.com/jiegec/usbip/issues/55)、[#60 热插拔](https://github.com/jiegec/usbip/issues/60) | closed，API state_reason=not_planned；含stale bot消息 | **关闭不代表修复**，仍须按当前源码和专门硬件测试判定 |
| [#2 interface index](https://github.com/jiegec/usbip/issues/2) | closed | 历史修复只mask低8位；本次index255仍会越界，不能照搬关闭状态 |

## 8. 最大化复用的现实路径与Phase2门槛

1. **可直接参考**：协议字段/网络字节序测试向量、内存模拟设备构造方式、Linux VHCI下模拟HID/CDC的QEMU测试流程。当前RustUSB不复制这些实现，若后续复制MIT片段，逐项记录出处和许可。
2. **可在隔离实验中作为依赖**：固定git提交的模拟设备服务；限制loopback监听、设备allowlist、实验性功能标志和独立进程资源配额。TCP代理或TLS只能提供通道安全，无法修复解析器OOM、panic和USB语义。
3. **适配层必须拥有的语义**：严格长度/连接/在途URB预算；明确session状态；RAII式统一cleanup；typed USB错误→Linux errno；单设备租约与本地驱动恢复；持久身份与热插拔协调；cancellation与seqnum匹配；避免await持锁与阻塞worker；日志默认不输出payload。
4. **仅加包装不够**：长度分配在解析器内、cleanup在内部状态机、错误在host层已丢失、同步trait无法延迟URB完成。这些需要上游修复/小型维护分支或重新设计受限引擎，不能声称一个外部trait wrapper就解决。应先向上游提交可重复的缺陷测试，评估修复和维护成本，再决定依赖或维护分支。
5. **不进入默认支持矩阵**：ISO、任意composite/alt、摄像头/音频、存储写入、Windows直通、未测试真实型号。Native后端的可用性也不能传递成Rust后端兼容性。

Phase2必过门槛：R01–R12逐项验证并处理；畸形包/超长字段fuzz；并发IMPORT/UNLINK/EOF/写错误/abort测试；所有实际USB错误语义验证；虚拟设备detach/reconnect QEMU；USB串口/HID/JTAG/只读测试U盘至少逐型号跑真实Linux互操作；设备拔出、进程退出、会话中断后本地独占恢复；明确禁用ISO/alt未实现路径并向用户展示原因。任何一项未过都保持实验性标识。以上是开发与验收计划，除第6节明确列出的结果外，尚未完成。
