# 兼容性与支持证据

2026-10-24最新应用服务切片：软件196/279/20共495次与四原生平台CI归clean`eaa1936`；六前台双栈回归同版通过。Native agent两family的actual配置/权限采证在clean`cb08672`补强并通过，产品150源/ELF仍等eaa；主选八场景16guest，原两eaa agent PASS另存，不算新增设备兼容。详见[E31报告](research/application-service-validation.md)。managed virtual/USBFS agent仅软件覆盖；这些结果不扩大下表真实硬件/ISO/aarch64 USB内核支持范围。

新增[安全broker验收](research/secure-usbip-validation.md)：clean b83c931在Linux6.8 x86_64双QEMU来宾完成Native HID/FTDI、virtual HID/CDC、USBFS HID/FTDI各自IPv4和IPv6的实际mTLS目录/IMPORT/URB，两周期I/O、SIGTERM清理和重用共六场景/12guest0。软件412次与四平台CI是独立软件证据；它不证明真实硬件、aarch64 USB内核、其他内核、ISO或任意跨配置兼容。

2026-10-23：目标 Linux Ubuntu22.04/24.04 × x86_64/aarch64。37c2d61四格[原生软件CI](https://github.com/tiansongyu/usbip-client/actions/runs/39895914376)全部通过，各自native Rust host、默认89项/allfeatures114项及vendor19项（共222）、fmt/Clippy/release/Rustdoc、只读smoke及两项stdout背压回归均有[持久证据](evidence/publication-native-fixture-20261023.json)。历史虚拟服务75/76项和Native59项CI分别保留[记录](evidence/publication-virtual-20261022.json)与[Native记录](evidence/ci-native-matrix-2026-10-22.json)。USB内核互操作仍仅在22.04宿主/6.8 x86_64 guest的QEMU模拟设备上验证；真实硬件、24.04内核USB互操作、aarch64 USB内核路径均未验证，不能宣称完整设备兼容矩阵通过。Windows、macOS、TUI、GUI均为后续阶段。

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

新增可选USBFS/nusb0.2.8 adapter在同一Linux6.8 x86_64 QEMU环境完成[正常/热拔场景](research/physical-qemu.md)：QEMU HID0627:0001为High-Speed/EP0=64、FTDI0403:6001为Full-Speed/EP0=8；普通UID1000只经helper获取root0600节点FD，client实际usbhid/ftdi_sio枚举与I/O。干净37bc6b4的正常两轮与热拔时FTDI继续、重插不继承授权均通过，原虚拟HID/CDC回归也通过；同一提交的[四格原生CI](evidence/publication-physical-20261022.json)各89/103/14项与stdout背压全通过。它验证真实usbfs调用上的模拟USB设备，不改变上表真实硬件未执行、ISO/任意Composite/alternate未验证的状态。

最新配置安全/多接口恢复在同一干净22d172d完成[三项最终回归](research/composite-qemu.md)：CDC ECM usb-net 0525:a4a2/Full-Speed/EP0=64，UHCI下真实USBFS→VHCI→cdc_ether，两次精确UDP请求38B/响应39B、两接口原驱动/config1/alt0,1恢复；原HID/FTDI正常与热拔回归也通过，6guest均exit0。QEMU6.2 xHCI搭配该ECM模型仍有网络帧拼接FAIL，原抓包完整保留；成功CLEAR_HALT、真实设备partial-error、任意混合Composite、无原驱动且非零alternate恢复和ISO未验证。


最新[checked release修复](research/usbfs-checked-release.md)在同一clean6e809a1的十一项QEMU通过：8个host-state场景64wire检查，ECM19与HID/FTDI正常/热拔各15，共113项/22guest0。真实RELEASEINTERFACE=-22传播、后续接口继续释放、失败后不执行SET_CONFIGURATION及原状态恢复均有证据；两次旧产品FAIL和三轮候选FAIL原样保留。这仍只覆盖固定模型，不改变上表真实设备未执行状态。

可选Rust服务的[Unix合作停止](research/server-signal-shutdown.md)已在clean934358f完成222次软件测试、ready/blocked × SIGINT/SIGTERM四个本地child用例，以及TERM、原虚拟HID/CDC、USBFS HID/FTDI正常与热拔四项QEMU，8guest均exit0。原虚拟回归为`rust-backend`构建，另外三项为all-features，ELF哈希分别记录；这不扩大USB设备或平台兼容声明。四平台CI39899971271已通过，每格222次测试执行、原两背压及新增四信号场景通过。SIGTERM保证限Unix的上述Rust服务；非Unix仅保留Ctrl-C源码路径，Windows USB、SIGKILL/崩溃恢复、持久会话和真实G1/G2仍未完成。


新增[持久物理授权与协调](research/physical-journal-recovery.md)在clean933e720通过五项QEMU：journal184host检查、原物理正常/热拔各15probe、ECM19probe、TERM52host检查，十guest均0；本地四信号和两背压通过。软件default89/allfeatures142/vendor19共250次、全部质量gate通过，检查输入与最终提交相同。新journal包括管理员恢复后的第三轮实际HID/FTDI I/O，原同代际基线/未决记录跨helper重启保留；不扩大设备类型/平台支持，不提供自动驱动恢复、FD撤销或断电耐久实测。真实硬件G1/G2及完整Phase2/Phase3仍未完成。

同一933e720的[CI39908004666](evidence/publication-journal-933e720-20261023.json)四个原生OS/arch均通过，每格250次测试、两背压及四信号；这不提供aarch64 USB内核或新增硬件证据。
