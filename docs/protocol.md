# USB/IP 协议与互操作边界

状态：设计及源码审计；实现覆盖见测试记录。USB/IP wire version 0x0111，TCP 默认 3240；USB/IP 头和描述符记录的多字节整数字段使用网络字节序。原始USB Control setup的8字节保持USB规定的小端格式，不做USB/IP大端转换。

DEVLIST 请求公共头8字节：version u16、opcode 0x8005 u16、status u32=0。响应 opcode 0x0005、status=0，接 u32 设备数。每个设备312字节：path[256]、busid[32]、busnum/devnum/speed 各u32、VID/PID/bcdDevice各u16、class/subclass/protocol/configuration/numConfigurations/numInterfaces 各u8。每个接口4字节（class/subclass/protocol/padding）。这里 bcdDevice 是设备发布版本，不是 USB bcdUSB。

RustUSB DEVLIST 解析限制设备数量256、接口数量每设备32、字符串必须 NUL 终结且有效 UTF-8，busid 必须符合物理总线编号语法，重复 busid、非零状态、错误版本/opcode、截断响应均报错。整个 DNS/connect/write/read 操作受总超时保护；丢弃 future 关闭 socket，无无限申请或无限等待。

IMPORT 请求 opcode 0x8003，携 busid[32]；成功应答 opcode 0x0003 加设备描述。随后保持**同一 TCP 连接**转发48字节基本头的 CMD_SUBMIT/CMD_UNLINK、RET_SUBMIT/RET_UNLINK 及 payload/ISO 描述符。Phase 1 委托 Linux 工具和内核处理此通路；没有将 DEVLIST 测试等同 URB 转发验证。

| 路径 | Phase 1 实现策略 | 验收证据 |
| --- | --- | --- |
| Linux usbipd → RustUSB discover | 独立有界 DEVLIST 客户端 | 本地协议模拟测试；真实 usbipd 测试单独记录 |
| Linux usbipd → RustUSB attach | helper 调用原生 usbip attach → VHCI | 必须两端真实设备枚举 |
| RustUSB export → Linux usbip attach | helper bind → 管理员控制 usbipd | 必须真实设备绑定与断开 |
| RustUSB → RustUSB | 同一 Native 数据通路 | 必须双端硬件及故障验收 |
| Rust backend → Linux VHCI | 同一有界引擎下的虚拟HID/CDC与可选Linux USBFS adapter | 虚拟profile在干净cfeffd3通过，见[报告](research/userspace-qemu.md)；USBFS的QEMU HID/FTDI与热拔开发场景通过，见[专项报告](research/physical-qemu.md)；真实硬件、ISO未验收 |

无额外 RustUSB 字段混入公开 USB/IP 流。身份认证和加密通过外层安全传输实现，不私改 Linux 线协议。TLS 必须转发 DEVLIST 连接、IMPORT 握手以及握手后完整双向长连接；将管理 API 套 TLS 不能保护 URB 数据。

原生源码依据见 [Linux 审计](research/linux-usbip-audit.md)，Rust 实现差异见 [Rust 后端审计](research/rust-backend-audit.md)。

共享服务只接受一次IMPORT，随后严格SUBMIT/UNLINK；48B请求先验证方向、devid、endpoint、64KiB长度、flags0x03c5、non-ISO count0/-1与frame0/-1再分配。回复basic devid/direction/ep归零，IN受URB和控制wLength上界、OUT只返回actual_length。SHORT_NOT_OK的短IN保留实际数据并返回-EREMOTEIO；idle IN保持pending。取消赢时UNLINK返回-ECONNRESET且无原RET_SUBMIT；完成赢时保留原completion并回复UNLINK0。11种畸形连接、1B控制及受限CDC IN有虚拟独立probe证据，见[虚拟设计](research/rust-virtual-backend-design.md)。

USBFS adapter复用nusb原提交/取消/回收，保留原负errno、错误时actual/IN数据及OUT实际长度；不把错误变成空成功。0/1/15B原请求不按MPS向上取整，Control SHORT_NOT_OK与Bulk OUT ZERO_PACKET按合法方向处理。每端点一个真实inflight与有界FIFO；UNLINK等待真实终态，网络写完或已丢弃响应后才退还预算。配置/alternate/clear-halt按host-state barrier处理，状态不明则隔离；这些实现路径不等于任意Composite/alternate或ISO已通过，详见[物理设计](research/rust-physical-backend-design.md)。远端认证与加密仍未实现。
