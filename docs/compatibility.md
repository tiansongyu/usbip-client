# 兼容性与支持证据

2026-10-22：目标 Linux Ubuntu22.04/24.04 × x86_64/aarch64。四格[原生软件CI](https://github.com/tiansongyu/usbip-client/actions/runs/39774177011)全部通过，各自native Rust host、默认75项/allfeatures76项测试、fmt/Clippy/release/Rustdoc、只读smoke及两项stdout背压回归均有[持久证据](evidence/publication-virtual-20261022.json)。历史Native59项CI另有[记录](evidence/ci-native-matrix-2026-10-22.json)。USB内核互操作仍仅在22.04宿主/6.8 x86_64 guest的QEMU模拟设备上验证；真实硬件、24.04内核USB互操作、aarch64 USB内核路径均未验证，不能宣称完整设备兼容矩阵通过。Windows、macOS、TUI、GUI均为后续阶段。

| 设备类型 | sysfs 可发现 | Linux Native 候选路径 | jiegec 后端审计边界 | RustUSB 真实设备验证 |
| --- | --- | --- | --- | --- |
| 存储 | 是 | 内核可转发；Phase 1 保守拒绝导出 | 错误传播与多LUN需验证 | 未执行 |
| 串口 CDC/厂商类 | 是 | 待硬件验证 | 虚拟 CDC 有上游 QEMU 证据 | 未执行 |
| HID | 是 | 待硬件验证，禁止自动解绑正在用的输入设备 | 虚拟键盘有上游 QEMU 证据 | 未执行 |
| JTAG/Debug Probe | 是 | 待硬件验证 | 控制/批量并不等于具体探针已支持 | 未执行 |
| UVC 摄像头 | 是 | 内核具备 ISO 路线但带宽/时序未验证 | 无实际 ISO 路径 | 未执行 |
| USB 音频 | 是 | 待 ISO/反馈端点硬件验证 | 无实际 ISO 路径 | 未执行 |
| USB 网卡 | 是 | 解绑可能中断管理网络，需独立连接 | 未验证 | 未执行 |
| Composite | 是，逐接口列出 | 整台设备独占，所有接口一起审查 | interface/alternate 重建有局限 | 未执行 |
| Hub/Root Hub | 可见但不作为普通共享目标 | helper 拒绝 | 不纳入支持承诺 | 不适用 |
| 其他类 | 是 | 逐设备验证 | 不作通用兼容声明 | 未执行 |

“可发现”只说明读取描述符/元信息；“候选路径”不是验证通过。各类正常/异常操作、内核版本、固件、VID/PID（不含敏感序列号）、传输模式和失败原因须在真实硬件报告中逐项记录。

当前存储导出拒绝属于安全门，不满足未来存储共享功能验收。必须先实现跨挂载命名空间/多LUN/加密映射/打开句柄/系统服务使用检查和明确释放流程，再扩大范围；不能用一个 --force 绕过。

本项目Native模拟HID验证：最终干净提交97c2914在两QEMU guest、Linux6.8/KVM中完成两周期导出、发现、导入、VHCI/usbhid枚举、真实evdev按键、断开、取消共享和本地驱动恢复，见[报告](research/native-qemu.md)和[最终证据](evidence/qemu-native-20261022T101910Z/summary.json)。仅验证QEMU键盘0627:0001，不将其扩大为任意物理HID、其他USB类或安全WAN支持。

本轮扩展在干净b4cbfc3完成[七项Native矩阵](research/native-qemu-matrix.md)：三方向互操作（RustUSB→RustUSB、RustUSB→官方client、官方server→RustUSB）各HID/FTDI两周期，另有HID外部释放后直接重连及同端口新代际拒绝控制，14guest均exit0。模拟FTDI0403:6001使用ftdi_sio，两周期每方向各35字节；它是厂商类Bulk设备，不是CDC ACM，不能推断任意串口、流控、持续吞吐或物理设备支持。doctor的工具/模块/helper真实诊断和未授权UID分类在两端验证；网络安全与硬件标志仍明确false。

补充的[三机争用](research/native-qemu-contention.md)和[主动TCP中止](research/native-qemu-transport-abort.md)在干净b487144/相同Native二进制上通过：一台QEMU键盘被A持有时B被明确拒绝，A继续输入，释放后B接手；两次实际SOCK_DESTROY后内核NULL、旧节点移除、自有记录清理和再次导入均通过，五guest exit0。它不是两个fresh attach的同时竞速，也不证明WAN黑洞检测、自动重连存储或真实设备。

可选Rust虚拟后端在干净cfeffd3的[独立双QEMU测试](research/userspace-qemu.md)通过：普通UID1000服务，无usbip_host或QEMU USB设备；真实Linux6.8 x86_64 VHCI同时枚举1234:0001键盘/usbhid和1234:0002标准双接口CDC/cdc_acm，两周期真实按下/释放、57600/8控制状态和精确串口注入/回显、busy/UNLINK/畸形包/释放均验证。此两个Full-Speed测试profile不改变上表真实硬件未执行的状态，也不代表任意HID/CDC、物理rusb/nusb、ISO或通用Composite/alternate兼容。
