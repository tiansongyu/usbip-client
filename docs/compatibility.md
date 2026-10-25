# 兼容性与支持证据

新增[E46持久设置/终端确认](research/tui-settings-actions-validation.md)：950软件/235源，四Native双栈pair/8guest0与58实际PTY/原三轮I/O通过；不扩大USB设备/平台、GUI/完整G4/G3或硬件兼容范围。

新增[E45终端预览](research/tui-validation.md)：937软件与Native双栈两pair/20实际PTY及原三轮I/O通过，不扩大USB设备/平台兼容，也不完成G4、硬件或完整G3。

新增[E44离线CRL](research/offline-crl-validation.md)：clean fcb软件927/226源，clean e441六raw CRL/六默认安全及一v2诊断共13paired/26guest0；元数据/凭据撤销不扩大USB设备兼容。v2诊断未加载CRL、安装态managed/v2 CRL专项及硬件/完整G3保持未验收；旧47 IPv6和开发FAIL保留。

新增[E43认证远程目录观察](research/remote-catalog-watch-validation.md)：common service供agent/direct TLS两watch入口，clean ec91软件903/223输入、clean633两Native/raw1双栈paired QEMU/4guest0，原三轮HID/FTDI/完整Rootproof保留，每pair新4 CLI场景与69核验。v2仅实际loopback验证；busy目录隐藏/恢复/导出逐个stop属于元数据可见性，不证明物理热插拔代际或新设备兼容。原首软件gate/两QEMU FAIL保持，真实硬件/ISO/aarch64 USB内核及完整G3/后续阶段仍未验收。

此前[E35有界日志](research/rotating-logs-validation.md)：723软件/177输入映射f3cf8、本地同CLI新6logging/旧4signal2BP及89ef四原生CI通过；新增文件轮转/授权读页不扩大USB设备兼容范围。新增clean48c9794一项installed日志QEMU/三epoch/2guest0通过，同CLI6cc/helperbb0；首44b1 hostFAIL保持，原23矩阵仍归1582/旧ELF。State/Protocol与cleanup独立保留，不称cleanEOF。真实硬件、ISO及aarch64 USB内核支持继续未验证。

此前[E34准备记录停止](research/prepare-recording-shutdown-validation.md)：667软件/167输入映射，clean `1582f06` 二十三QEMU/46guest0，同CLI72adb291…/helperb4af4b5f…。八新场景覆盖Native/USBFS Resource记录门、accepted initializer syscall-entry与Native Resource/Available局部StopSession；六数据链/九旧回归同源另账。仅固定模拟设备，不扩展真实硬件、ISO、内核内部active或aarch64 USB支持；旧FAIL与未运行矩阵保持原裁决。

此前[E33数据链健康](research/data-link-health-validation.md)：626次软件执行、165输入映射，与clean `531e0c3` 十五QEMU/30guest0分账；六新managed Native/USBFS/virtual双栈含18个定向断流阶段及同一serial/CDC原连接I/O，九旧回归同源/ELF。virtual此新场景为HID枚举闲置与CDC echo，不是新键盘注入；Native/managedUSBFS pending/inflight、真实G1/G2及ISO仍未测。[精确531四原生CI](https://github.com/tiansongyu/usbip-client/actions/runs/40029976284)每格626/39rows及原信号/背压通过，原530 CI保留历史。

此前[E32共享owner](research/shared-owner-validation.md)：匹配2638a8c输入的530次软件，以及clean b45c2e8的九项QEMU/18guest0，涵盖六前台、两Native agent与原USBFS TERM，同一产品源/ELF。下面保留E31版本归属：软件196/279/20共495次与四原生平台CI归clean`eaa1936`；六前台双栈回归同版通过。Native agent两family的actual配置/权限采证在clean`cb08672`补强并通过，产品150源/ELF仍等eaa；主选八场景16guest，原两eaa agent PASS另存，不算新增设备兼容。详见[E31报告](research/application-service-validation.md)。该E31时点managed virtual/USBFS agent仅软件覆盖；E33已补其双栈QEMU子步骤；这些结果不扩大下表真实硬件/ISO/aarch64 USB内核支持范围。

新增[安全broker验收](research/secure-usbip-validation.md)：clean b83c931在Linux6.8 x86_64双QEMU来宾完成Native HID/FTDI、virtual HID/CDC、USBFS HID/FTDI各自IPv4和IPv6的实际mTLS目录/IMPORT/URB，两周期I/O、SIGTERM清理和重用共六场景/12guest0。软件412次与四平台CI是独立软件证据；它不证明真实硬件、aarch64 USB内核、其他内核、ISO或任意跨配置兼容。

2026-10-23：目标 Linux Ubuntu22.04/24.04 × x86_64/aarch64。37c2d61四格[原生软件CI](https://github.com/tiansongyu/usbip-client/actions/runs/39895914376)全部通过，各自native Rust host、默认89项/allfeatures114项及vendor19项（共222）、fmt/Clippy/release/Rustdoc、只读smoke及两项stdout背压回归均有[持久证据](evidence/publication-native-fixture-20261023.json)。历史虚拟服务75/76项和Native59项CI分别保留[记录](evidence/publication-virtual-20261022.json)与[Native记录](evidence/ci-native-matrix-2026-10-22.json)。USB内核互操作仍仅在22.04宿主/6.8 x86_64 guest的QEMU模拟设备上验证；真实硬件、24.04内核USB互操作、aarch64 USB内核路径均未验证，不能宣称完整设备兼容矩阵通过。此为当时平台记录；现TUI及原生GUI有分开锁定的软件/QEMU预览证据，Windows/macOS仍未验证。

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


## E47终端故障范围


终端E47见[未知启动/失联验收](research/tui-failure-retry-validation.md)：clean4ee6be3e完整952软件/235源，两故障与两原E46正常动作Native双栈pair/8guest0、76实际UID1000 PTY通过。实际Start超时后同agent恢复，UI只确认原UUID RetryStart；精确job current/stale/current，首次Profiles失联不再保留Current或旧journal/health。原38477360两guest0/19PTY但整体FAIL保留，不改其裁决；原264核验/三轮I/O/完整Rootproof不弱化。SIGSTOP不证明wire-loss或永久OS阻塞；UI热插拔/权威拒绝、完整G4/G3/硬件与后续阶段仍待完成。


E48为x86_64/Ubuntu6.8.0-106内核的TUI观察/权限证据及当前Native回归；两构建的CLI/复制但未执行的standalone helper分别记账。956软件、8guest0及56实际PTY不扩大USB类、真实硬件、ARM实际终端、GUI或Windows兼容范围。 见[验收与审计](research/tui-hotplug-permission-validation.md)。


原生GUI预览验证了Ubuntu22.04宿主/6.8 x86_64 guest的真实WebKit运行时、热插拔、Read/Audit-only拒绝/原UUID与偏好和宽窄布局，见[E49验收](research/native-gui-validation.md)。后续[E50双栈正向操作](research/gui-native-actions-validation.md)补固定Native QEMU HID/FTDI的GUI确认Start/精确Stop、非空远程目录及关闭窗口后的实际I/O。四平台GUI软件CI与实际USB内核/clean安装分开，不扩大USB设备类型、硬件、ARM USB或Windows兼容矩阵。
