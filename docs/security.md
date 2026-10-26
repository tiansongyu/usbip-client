# 安全模型与发布门槛

[存储检查](storage-inspection.md)只允许UID0读取目标相关的有限占用metadata。它不接受路径、不打开存储或USB节点，不停止服务/卸载/刷新/改驱动；单actual worker总await。proc非原子、隐藏namespace、内核引用与未探测加密等边界始终返回，RO/空usage/扫描结束不成为导出permit。见[设计与来源](research/storage-inspection-design.md)。

E63[导入PM边界](research/native-import-power-validation.md)：操作仅在新授权且完整匹配的USED/local generation后执行，固定单组件NOFOLLOW/根owner/权限/type/同filesystem与前后原dev/inode/address/VID/PID核验；不写全局/源设备/替代代际/旧journal。PM失败保留operation与实际compensation/Observed owner，active不代Root释放证明。后续管理员改策略、system sleep/remote wakeup与永久kernel阻塞未保证；不新增设备能力或权限。

本文面向实现者、部署管理员和安全审查者。它记录 RustUSB 的**已实现边界、设计约束和待验证控制**，不是安全认证或完整验收清单。实际实现、测试环境和遗留风险须在工作记录中逐项登记。首期范围是 Ubuntu 22.04 / 24.04、Linux x86_64 / aarch64。Native、固定虚拟 HID/CDC 和实验性 USBFS 已有隔离 QEMU 证据；新增安全broker的实际目录、IMPORT与URB通路通过IPv4/IPv6仿真，见[验收报告](research/secure-usbip-validation.md)。真实硬件、Windows及完整安全gate仍须独立验收。

## 1. 资产、前提和边界

[E32共享owner](research/shared-owner-validation.md)保持Foreground untracked与Managed durable/tracked的明确边界；stdout delivery ACK不是持久准入。实际stub close后释放独占锁，再等待原记录ACK，不丢原future；后续[E34](research/prepare-recording-shutdown-validation.md)补充Resource记录停顿下的实际清理，永久FS/kernel硬阻塞仍不承诺有界退出。新增应用服务的权限、状态与恢复切片见[契约](research/application-service-design.md)及[E31验收](research/application-service-validation.md)。非root agent直接持有实际job；本机RPC按SO_PEERCRED逐profile/action授权，不接受端点/路径/凭据/FD或任意helper请求。broker UID仍是整个helper池的信任域，profile ACL不隔离同UID任意进程。Root proof须kind/correlation/key/UID/resource全匹配，活owner不能被只读observer提前退休；新进程资源0和PID死亡均非旧释放证明。

Native agent双栈仿真已有定向停止、KILL137协调与新attempt实际I/O；actual TLS JSON/public DER/pin/权限与PCAP分别采证。审计为有界最小字段ring，私钥内容不采集；不等于永久归档/轮转。raw-v1无同数据链heartbeat/RTT，Rust无独立proof的崩溃session仍未决，证书负向/轮换等完整gate继续未完成。

保护资产包括 USB 设备控制权、存储数据一致性、客户端内核、主机 root 权限、设备访问策略、认证凭据、设备序列号和审计记录。合法用途要求设备所有者和两端管理员授权，并服从组织 USB 安全策略。网络对端、USB 描述符、USB 数据、CLI 参数、配置和本机普通用户均不能视为可信输入。

默认不信任公网、局域网其他成员或远端 USB 设备固件。可信 VPN 只保护传输及网络成员身份，不保证接入设备或对端主机无恶意。远端设备进入客户端 USB 驱动后仍扩大内核攻击面；设备级授权不能替代内核补丁和组织设备准入。root、内核或设备固件完全失陷超出本工具能够自保的范围，必须在部署风险中明确记录。

| 信任边界 | 跨界输入 / 权限 | 主要攻击 | 强制控制与证明方式 |
| --- | --- | --- | --- |
| 普通 CLI / TUI → 应用服务 | 用户意图、设备 ID、会话 ID | 越权导出、绕过确认、重复操作 | 统一应用服务、类型化请求、操作授权、幂等键；负向集成测试 |
| 应用服务 → root helper | Unix socket 请求、内核操作 | 命令注入、任意文件写入、混淆代理 | socket 文件权限 + 内核 peer credentials + root 配置策略；非法请求不调用外部命令 |
| root helper → usbip / sysfs / 固定 usbfs 节点 | Bus ID、固定动作、进程环境、设备 FD | 选项注入、PATH 劫持、符号链接、设备替换 | 固定程序/路径、枚举动作、参数白名单、可信文件权限、执行前后代际核对 |
| root helper → 普通 USBFS 服务 | SCM_RIGHTS FD、设备元数据、代际 receipt | FD 混淆、跨设备授权、把整设备能力误作沙箱 | root peer、单 FD/标记/截断检查、节点和代际匹配；明确能力范围与不可即时撤销 |
| 远端 → USB/IP TCP | 列表、描述符、URB、长度字段 | 内存耗尽、畸形包、客户端内核漏洞 | 可信网络、连接和字节上限、超时、协议 fuzz、内核更新；不虚称 Native 路径由 Rust 校验全部 URB |
| 物理设备 → 本机和远端驱动 | 描述符、接口、热插拔 | 恶意 HID、伪造序列号、重新枚举 | root 设备策略、接口完整检查、歧义拒绝、代际身份；新代际不继承旧能力 |
| 设备状态 → 持久状态文件 | 会话、导出记录、恢复意图 | 旧状态越权、崩溃永久占用 | 系统实况优先、记录版本、原子写入、启动协调、保守恢复 |
| 配置 / 凭据 → 服务 | 策略、证书、密钥 | 配置注入、密钥泄露、弱权限 | 严格 schema、root 拥有且非组/其他可写、拒绝符号链接、敏感字段脱敏 |

关键攻击路径为：未授权用户取得 socket → 绕过设备策略 → 特权绑定；恶意网络对端 → 伪造长度 / URB → 内存或内核攻击；热插拔替换 → 复用旧 Bus ID → 导出新设备；存储检查后自动挂载 → 在用设备被解绑 → 数据损坏。任何一条路径都须有独立负向测试，不能用一次成功 attach 代替安全验证。

## 2. 权限分离与特权接口

CLI、未来 TUI / GUI 和普通应用服务使用普通账户。只有最小 root helper 以 root 身份执行受控操作或授权设备 FD；取得 FD 的 USBFS 服务以普通账户执行设备操作。不得以“需要加载模块”为由让整个应用常驻 root。helper 不监听 TCP，不接受脚本、命令行字符串、任意可执行路径、任意 sysfs 路径或任意环境变量。

当前 helper 仅读取固定系统状态，绑定 / 解绑批准的 Native 设备，连接 / 拆除受管 VHCI 会话，并在可选 `physical-usb` feature 下交付批准设备的 USBFS FD。加载 `usbip_host` / `vhci_hcd`、启停 `usbipd` 和防火墙仍由管理员负责；以后如引入服务控制 RPC，也只能操作编译期固定单元，不能接受任意服务名。操作层不执行 `sh -c`，不使用来自用户的 `PATH` 选择程序，不传递 `LD_PRELOAD`、动态库搜索目录或不必要环境。超时必须终止并回收子进程；超时后要读取系统实况，不能把“子进程被杀”当作回滚完成。

Unix socket 置于 root 管理且普通用户不可替换的运行目录。初期 socket 可连接不表示可执行操作：连接建立后读取内核提供的 UID / GID / PID，并以 root 配置中的 allowed UID 做授权；不相信请求中的用户名或 UID。Linux `SO_PEERCRED` 提供连接时的对端凭据。后续可收紧专用组 ACL，但不能移除逐请求身份和策略检查。[Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)

root 拥有的设备 allowlist 是 helper 的授权依据，普通用户配置不能扩大它。当前策略区分 `allowed_uids`、Native `export_device_ids`、物理 `physical_device_ids`、可导入目标 host / port 和显式 `trusted_network` 确认；两个设备列表必须不相交，物理列表缺失时默认空。没有可信网络确认则拒绝 attach 和物理 FD 授权。默认拒绝；不存在规则、文件权限不安全、无法确认身份或配置损坏时拒绝变更。后续可再细化 UID / 动作 / 设备的逐条授权，不把初期全局 UID 集合描述成逐用户规则。所有 allowed UID 共同管理策略批准的受管资源，这一共享管理权必须告知管理员；不得因此允许 detach 不属于本工具的任意 VHCI 端口。

### 实验性 USBFS FD 能力

`OpenPhysical` 只接受精确设备指纹及必需的 journal schema 版本，不接受设备路径。helper 先检查 UID、物理 allowlist、可信网络声明和当前完整接口安全状态，拒绝存储、Hub、未知/不完整接口以及 `usbfs` / `usbip-host` 已占用的设备；再打开固定 `/dev/bus/usb/BBB/DDD`。路径父链受 root 保护，节点须为匹配设备号的 root 字符设备，使用 NOFOLLOW/CLOEXEC，节点/sysfs 的 dev/inode、地址和元数据前后匹配。普通服务不需要改变节点所有者，也不直接打开调用者指定路径。

客户端验证受保护 UDS 路径和 root `SO_PEERCRED`。helper 先发送有界 JSON 描述，收到单字节 ACK 后才用 SCM_RIGHTS 发送一个 FD；接收方拒绝错误标记、多 FD、截断或未知 ancillary，检查 CLOEXEC、读写模式、节点与代际 receipt。UID 可在 helper 读取请求前被拒绝；首次发送失败时仅允许在同一已验证 socket 上有界读取完整 `ok=false` 拒绝，不重发操作，不把 EPIPE 当作权限证明。拒绝路径不进入 ACK/FD 阶段。

**该 FD 是整台设备的写能力。** helper 不逐条代理 URB，不能限制拿到 FD 的进程只发某些控制请求，也不能通过关闭 socket、修改策略或重启 helper 撤销已交付 FD。信任获得能力的普通服务进程是本轮明确前提；它不是逐请求沙箱、可撤销租约或远端用户身份授权。Native/物理 allowlist 互斥只防止本工具策略中的交叉控制，不能阻止外部管理员或其他进程改动设备。策略变更须重启 helper，并单独协调已有能力和正在使用的设备。


物理授权另有有界、root-only 持久账本：FD 交付前预留同指纹独占记录，保存完整原基线和 SO_PEERCRED UID/PID、`/proc/PID/stat` starttime、boot ID；UUID 不是 bearer token。schema 1 的 `FinishPhysical` 仅接受原 OS owner 的合作结束声明，helper 独立复核同代际完整状态后退休管理记录；这是可信服务生命周期协议，不证明外部 FD 副本不存在，也不撤销能力。root-only `PhysicalPending` 和 `ReconcilePhysical` 不受普通 allowed UID 的共享管理权影响；协调要求旧 owner 已结束，同代际原状态已恢复，或管理员明确确认旧代际消失，绝不对替代设备写入旧基线。

状态路径只在 root helper 启动参数中指定，采用受保护目录 FD 相对操作、NOFOLLOW、0600 单链接文件、独占 flock、原子 rename 及文件/目录 fsync。上限 256 个未决设备、每设备 256 个接口、16 MiB 文件；管理列表按 64 KiB IPC 边界分页。损坏/缺失/权限变化或不确定提交失败不当作空表，拒绝继续使用；不防御管理员删除整个目录或选择另一目录绕过记录。完整多配置/alternate 能力保持，自动崩溃恢复和断电实验仍未完成。[proc_pid_stat(5)](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)、[fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html)说明进程代际字段和目录持久化要求；这些接口本身不提供 USB 恢复证明。

systemd 安装阶段应对普通服务启用只读文件系统、禁止新增权限、最小地址族及能力集合等隔离；helper 的系统访问只能按实际操作放行。当前 helper 不加载模块；未来若引入该操作，须重新验证 `ProtectKernelModules` 等限制。`ProtectSystem` 不能替代 socket ACL，也不能撤销已传递的 USBFS FD。每个单元须在目标 Ubuntu 的 systemd 版本上验证实际限制和功能，不能把配置文本当成有效隔离。[systemd.exec 官方说明](https://www.freedesktop.org/software/systemd/man/250/systemd.exec.html)

## 3. 输入、解析和资源限额

所有边界采用 allowlist 和有界类型。以下是接口设计要求，最终上限必须在代码常量、配置参考和测试中一致。

| 字段 | 校验要求 |
| --- | --- |
| Bus ID | 仅 Linux USB 设备拓扑格式，例如 `1-2`、`1-2.3`；限制总长度、段数和数值；拒绝接口后缀、`/`、空白、控制字符、前导选项、路径穿越和 Unicode 混淆 |
| VID / PID / 端点 | 宽度有界整数；端点和方向按描述符校验；拒绝溢出、截断和未知枚举 |
| 设备路径 | 由可信 sysfs 根和校验后的 Bus ID 推导；调用者不能提供绝对路径；校验真实目标位于预期设备树 |
| 主机 / 端口 | IPv4、IPv6、合规主机名分别解析；端口为 1–65535；拒绝命令参数拼接；DNS 解析结果也必须符合策略 |
| 设备 / 会话 ID | 不透明有界标识，不能作为文件名或命令片段直接使用；每次映射到当前代际设备或受管会话 |
| IPC / 配置 | 限制报文、字符串、数组、字段数和文件总大小；明确版本；拒绝未知动作和不合规字段；读取和响应均设截止时间 |
| USB/IP | 检查版本、状态、端序、固定字段宽度、接口数量和长度乘法；分配前校验上限；未完整消息不得更新会话 |

对于 Native 后端，内核和原生工具处理数据连接，RustUSB 只对自己实现的解析入口负责；不可声称 Rust 的内存安全替代 Linux USB/IP 协议栈安全。可选 Rust 虚拟与 USBFS adapter 复用唯一 engine：最多16连接、每会话64请求、每传输64KiB；方向、端点、长度和不支持的 ISO 在分配前检查。握手、首字节后的半包及写入各5秒，已导入的空闲连接不因此超时。物理缓冲另有全服务16MiB配额，覆盖排队、实际在途、取消待回收及回复持有的数据；未完成响应写入或网络关闭并排空前不能释放预算。连接任务和 watchdog 均随所有者回收。慢速请求、半包、无换行 IPC、持续失败认证和连接洪泛还须按部署需要验证限速；当前配额不等于完整抗拒绝服务证明。日志也须限长，避免错误循环填满磁盘。

## 4. 设备身份、独占和存储一致性

持久身份以 VID/PID、序列号、接口集合、物理端口拓扑组成候选描述，不把 Bus ID 当成永久设备身份。序列号可以缺失、重复或伪造，物理端口也会变更，因此身份只用于匹配管理员意图，不是密码学认证。缺少序列号或同型号设备歧义时要求 root 策略绑定具体拓扑；无法唯一确定时拒绝自动导出。热插拔和重新枚举生成新代际，旧请求与旧锁不能继续应用。

一个物理设备默认只允许一个客户端；Composite Device 以完整物理设备为独占单元，不按接口偷偷拆分共享。导出、导入和清理须串行化到设备级并协调外部 `usbip` 状态。发现已有外部占用时不得强行接管。锁文件只解决应用间并发，不能证明内核、桌面 automounter 或其他进程没有操作设备。

USBFS adapter 仅在 IMPORT 时 claim 全部已验证接口；同一端点最多一个真实在途请求，其余有界 FIFO。配置/alternate 操作协调受影响请求，后续端点请求等待状态确认。部分 claim 失败也只清理本会话实际取得的接口。恢复 alternate 时不能 detach 新绑定的驱动；恢复原配置仅允许本会话确曾修改且当前仍为最后确认配置，随后独立核对原配置、alternate、驱动和代际。观察失败或外部改动进入 quarantine，不以历史元数据接管现有所有者。

配置变更后不能沿用旧 active 配置的安全快照。每个 detach/claim 前复用只读 scanner 检查同一设备 ID、配置/代际、完整接口、全部 export blockers 和当前驱动；只有本会话实际持有的 usbfs 接口可继续存在。释放后的恢复动作也受同一检查约束，新存储驱动/块后代或外部占用不因“恢复”而被强制解绑或改配置。CDC 等驱动的 probe 可能同步绑定多个接口，逐接口重观察后允许已实际恢复的原绑定收敛，不能仅忽略 EBUSY。该检查不撤销 SET_CONFIGURATION 已触发的内核 probe，不是 inactive vendor quirk 的预先完整识别，也不提供通用原子排他保证。

设备代际由已打开能力、节点/sysfs receipt 和 USB 地址共同约束。热拔使旧 slot 进入 Gone 并关闭其 TCP，不能因 Bus ID、VID/PID 或候选指纹相同而重新打开节点。重插需管理员重新核验并显式注册新代际 FD；这不提供硬件密码学身份或自动重授权。

导出 USB Mass Storage 前，必须检查所有接口、LUN、分区及其下游块设备：mountinfo、活动文件使用、swap、device-mapper / LUKS、LVM、RAID、holders、loop 关系和系统服务使用。只检查 `/proc/mounts` 的一个设备节点不充分；权限不足或看不见其他挂载命名空间时不能得出空闲结论。不得强制卸载，不执行 `umount -f`、`fuser -k` 或自动关闭加密卷。

检查和绑定之间存在 TOCTOU：检查后可能被 automounter 打开或设备被替换。设计要求在设备锁下重新读取身份、代际和所有忙状态，并在实际绑定前复核；这仍不能给通用桌面系统提供原子“无人使用”保证。**因此 Phase 1 默认拒绝存储设备导出，包括带存储接口的 Composite Device；只有完成受管维护窗口、禁止自动挂载及可证明的排他占用机制后，才评审放行。** 不得以 `--force` 绕过这一缺口。未知接口或无法完整检查时同样保守拒绝。

后续存储验收先用可丢弃测试盘和只读访问。断网、进程崩溃或设备突然拔出后，不自动重新挂载或继续写入。管理员须确认本地与远端均已停止使用、检查文件系统、必要时重新枚举；未完成写入和缓存是否持久化不能仅从 TCP 断开状态推断。安全释放必须先由客户端停止应用 I/O 并完成卸载，再 detach，最后 unexport；任何步骤失败保留需人工处理状态。

## 5. 网络、认证和密钥

旧Native数据路径为 `vhci_hcd ↔ TCP ↔ usbip_host`，独立于管理 API。USB/IP 列表查询使用独立连接，导入成功后的连接继续承载 URB；因此只保护设备列表或管理 API 不能保护实际 USB 数据。[Linux USB/IP 协议](https://cdn.kernel.org/doc/html/latest/usb/usbip_protocol.html)

Phase 1 只在管理员已建立的可信 VPN / 受管隧道上使用，明确记录通道边界；不实现内置远端认证，不宣称原生 3240 已被 RustUSB 加密。`usbipd` 可能监听通配地址，部署必须先以防火墙限定 VPN 接口和允许的对端，再启动服务。IPv4 和 IPv6 都要验证；不能仅因 IPv4 规则正确就默认 IPv6 安全。`doctor` 仅查询本机组件和已验证身份的helper，不探测远端监听、路由、防火墙或 VPN。

旧可选Rust服务默认仅监听 `127.0.0.1:3240`；非loopback须同时提供可信测试网声明和非空精确peer IP allowlist，CLI在请求设备FD前校验该条件。IP allowlist和helper本机UID认证都不等于远端USB/IP身份认证或加密；旧命令的DEVLIST与IMPORT后URB仍须外部受管网络保护。

可选`secure-*`模式使用rustls/Tokio、TLS1.3、强制mTLS及ALPN，root可信配置固定PKI服务名/端点/设备池，leaf pin映射principal及设备ACL。目录在同一授权快照上返回稳定key↔BusID映射与标准DEVLIST；IMPORT在真实slot内同时核对key、BusID及ACL后才取得设备，成功回复flush后同一TLS stream承载URB。Native使用helper创建的匿名K/R，无可抢占的本地TCP入口；Rust engine直接接受TLS，不另开明文后端listener。六个实际QEMU外链均同时验证两族3240拒绝；这不代替生产网络部署检查。见[安全配置指南](secure-guide.md)与[实施契约](research/secure-usbip-integration.md)。

专用非0 broker UID与旧allowed_uids分离，安全Native/USBFS池和旧池互斥；同broker UID属于同一信任域。helper依赖SO_PEERCRED，不信网络自报principal；Native export/stub进一步绑定同OS进程。仅交付R并立即关闭helper的R副本，helper保留自己的K用于精确shutdown；实际blocking任务持guard至真实结束。持久Intent/原代际未确认时保留并阻止接管，不凭PID死亡删除。信任授权服务合作结束不提供全局FD撤销，SIGKILL/异常退出的安全broker恢复仍须另验收。

配置/CA/chain及祖先目录由root保护，私钥root或目标broker所有且0600、运行身份须实际可读。加载有界、拒绝symlink/hardlink/非regular及读取中变化；启动后不可变，轮换须合作停止/重启。成熟库提供链/有效期/用途/服务名验证，禁止跳过校验；证书撤销、完整轮换/认证限速矩阵及业务状态审计仍未全部验收，不将所测正向通路标成完整G3。

[E37 installed负向](research/secure-negative-qemu-validation.md)在clean25870c6的三backend/双栈六QEMU通过：同guest/helper内合作停止后实际新server PID读取移除旧leaf pin的策略，旧A拒绝、B实际I/O与完整清理；每pair四free/held屏障保原资源、直接IMPORT4/duplicate2及证书/名称/信任/ALPN负向有原件。264条目标client退出记录不是USBcase，raw-v1 startup退休不是热撤销/CRL/OCSP或SIGKILL恢复；TLS1.3映射拒绝可保IO/errno104而非cleanEOF。完整G3与生产隔离/审计、认证耗尽等仍未完成。

密钥生成使用操作系统随机源，不自研密码协议。服务端私钥只允许服务身份读取，客户端凭据按账户隔离；不放入仓库、命令行、默认日志或诊断包。部署须有首次信任、吊销、过期、轮换、备份权限和丢失恢复流程。认证失败重试使用限速；重放或重复导入不能创建第二个所有者。TLS 不能消除恶意 USB 设备及内核漏洞风险。

## 6. 状态机、取消与重启协调

以下为必须实现并测试的目标状态，不代表已落地：

| 当前状态 | 允许事件和目标 | 超时 / 失败处理 |
| --- | --- | --- |
| Discovered | 元数据校验和策略通过 → Available | 身份不明 / 不具条件 → Error，给出原因 |
| Available | 获得设备锁并授权 export → Exporting | 取消保留 Available |
| Exporting | 系统确认绑定完成 → Available（已导出标记）；已有导出目标发起导入 → Connecting | 失败检查实况后回滚或 Error，不盲目解绑其他所有者 |
| Connecting | 远端授权、import 和 VHCI 状态确认 → Connected | 截止时间或取消 → Disconnecting / Error |
| Connected | 用户断开、拔出、网络故障 → Disconnecting | 检测超时只触发协调，不能假定设备已释放 |
| Disconnecting | 确认所有受管资源释放 → Disconnected | 清理失败 → Error，保留受管标识供重试 |
| Disconnected | 明确重连请求先 → Recovering，重新核验后经 Available → Connecting | 不自动恢复存储写入；设备发现/移除是独立维度 |
| Error | 分类错误、重新读系统实况 → Recovering | 不无限高速重试，不隐藏原始错误；人工终止也先核对实况 |
| Recovering | 确认身份、策略、所有权和内核状态后 → Available / Disconnected | 无法证明安全 → Error，要求管理员介入；当前类型不允许直接跳到Connected |

设备可用性与会话连接状态是两个维度：`Available` 可携带 exported 标记；不得为了画单一状态图而将“已导出”误认为“已连接”。当前core类型已校验上述转换约束，但尚未完整接入持久业务状态机。每次转移记录事件、操作 ID、旧 / 新状态、错误类别与时间，以及取消令牌和恢复记录，是Phase3目标；当前操作截止与并发边界见架构文档，不把未来事件流视为已实现。

重复 export / unexport / detach / cancel 必须安全且幂等。先确认 ownership，再只清理本工具创建的端口、绑定和代理。落盘记录不是事实来源：启动时读取 sysfs、VHCI 端口、usbip_host 和会话记录，报告外部资源，不能清空所有 VHCI 端口或接管所有共享设备。进程重启、记录截断、断电和清理重试均须有故障注入测试。状态恢复不能在不确定时给出 Connected。

USBFS 的当前局部状态为 Available/Claiming/Leased/Closing/Quarantined/Gone，不等于上述完整业务状态机已接入。Control/IN/OUT 的取消请求都须等待真实 terminal：queued 请求可不触碰硬件地取消；active 请求由实际完成裁决。取消赢回复 UNLINK -104 且不再回复原 SUBMIT；完成赢保留原 errno 和 partial，再回复 UNLINK0。OUT 的部分副作用不可因取消而隐藏。EOF、写失败、Drop 和 shutdown 仅发起关闭，监督器继续拥有真实请求与缓冲，等待回收、接口释放和原驱动核验。shutdown 最多等待10秒；超时或不确定须报告隔离，不能标 Available，也不能用 `spawn_blocking` future 被丢弃冒充底层调用已取消。真实消失可报告 Gone，无需恢复不存在的驱动，但不能据此授权重插设备。

## 7. 日志、指标与诊断

使用 `tracing` 记录发现、权限判定、导入 / 导出、连接、状态转移、取消、网络故障、清理和结束，支持 JSON / 人类可读格式。日志包含可关联的操作 / 会话 ID、后端、结果和安全错误码；设备序列号、地址和用户信息按隐私策略脱敏。禁止默认记录原始 URB、HID 按键、存储内容、证书私钥、token 或完整认证请求。

可选[E35文件日志](logging.md)已实现按字节/文件数轮转、有限256队列、2KiB typed记录及0700/0600 checked路径；硬上限128MiB segment payload，文件metadata另计。已成功commit的audit才镜像，镜像可丢失、不能确认准入或cleanup。文件投影只收封闭码/允许的标量，不格式化未知Debug，不持久化原始message/error、USB数据、序列号、任意路径或凭据。旧stderr tracing的任意trace输出不由此自动成为可公开的脱敏导出。

ReadLogs先核Unix peer及profile Audit权限，游标不授予权限；只返该profile typed audit与caller自身unscoped denial，root可读unscoped typed管理audit而非通用diagnostic。RPC不接收日志路径或写配置；root helper文件仅本地root读取。独立实际worker/配额在取消后保留，日志失败不改变业务WAL健康、USB副作用或Rootproof；真实owner清理后才join logger，非零日志退出不得误作USB未操作。具体软件/进程/仿真证据按[专项验收](research/rotating-logs-validation.md)来源记账。

年龄保留、压缩、诊断导出、全面秘密扫描及永久文件系统阻塞仍待独立工作。诊断导出必须再次过滤配置和凭据。指标记录活动设备 / 会话数、连接失败次数、错误数、RTT、字节和吞吐量；Native 后端无法可靠观测的指标显示不可用，不能用估算冒充实测。安全测试须搜索日志、崩溃输出和诊断包中的预置秘密标记。

## 8. 安全 gate 与剩余限制

当前 Phase 1 helper 只在本进程内记录经过 Native 后端确认的端口身份和本实例成功创建且核对代际的导出绑定；所有配置中的 allowed UID 共同管理这些资源。它没有持久化的逐用户租约，重启后的已有端口/导出、外部工具建立的导出均不自动认领，普通 CLI 不能释放这些未知资源；管理员须先核对内核实况并人工协调。操作失败后即使出现新绑定，也不凭前后差分认领或撤销来源不明的资源，驱动/资源完整恢复尚未验收。已有 socket 路径也不会自动删除，避免误替换仍活跃的 helper。这是明确的保守降级，**不等于完整断线恢复或重启协调已验收**。模块加载、usbipd 生命周期、防火墙和 VPN 仍由管理员负责，`trusted_network` 只记录前提确认。

受管导入的身份还包含本地USB地址和sysfs目录dev/inode代际，快照前后复核属性及VHCI行；这些是本次内核实例内的凭据，不能跨重启持久认证，也不能抵御特权管理员修改系统。外部释放后，下一次attach仅在端口NULL且旧代际消失时移除旧记录。相同端口、Bus ID、VID/PID的新外部连接不继承旧所有权；ERROR或未知身份阻止新导入，需管理员协调。doctor的版本子进程和只读Status均有资源截止，仅输出受限安全版本及固定诊断文字，不回传任意工具stderr、策略内容或设备标识。

实验性物理 adapter 的边界与实现细节见[架构](architecture.md#可选-rust-服务共享-engine-与-usbfs-生命周期)和[实施设计](research/rust-physical-backend-design.md)。干净37bc6b4的USBFS正常/热拔及原虚拟回归均已通过；完整 G2、真实硬件兼容性、ISO、任意 Composite/alternate、持久可撤销授权及 R11 认证均未完成。固定虚拟 profile 的成功也不能填补这些验收项，结果以[工作日志](work-log.md)和[验收记录](acceptance.md)为准。

| Gate | 必须提供的证据 | 未通过的处理 |
| --- | --- | --- |
| S1 特权边界 | 非管理 UID、未授权设备、危险参数、配置权限错误、超大 IPC、符号链接及重复请求测试 | 禁止发布特权功能 |
| S2 存储保护 | 单 / 多 LUN、分区、挂载、swap、holders、加密卷、权限不足与 TOCTOU 场景 | 默认拒绝该类设备 |
| S3 网络路径 | 外部接口抓包和防火墙证据，同时覆盖 DEVLIST 与 IMPORT / URB、IPv4 / IPv6、直连绕过 | 不宣称安全远程连接 |
| S4 生命周期 | 取消、拔出、进程终止、网络故障、重启协调、重复清理及双客户端争用 | 不宣称可靠恢复 |
| S5 解析与依赖 | fuzz 输入边界、超大长度、依赖 / 许可证清单、工具版本和回归用例 | Rust 后端保持实验状态 |
| S6 实机隔离 | 目标 Ubuntu / 内核 / 架构的 helper 策略、systemd 限制与设备测试记录 | 对应组合标记未验证 |

详细执行和结果格式见 [测试手册](testing.md)，分阶段发布门槛见 [开发计划](roadmap.md)。当前文档不证明任何 gate 已通过。不能因编译、单元测试或模拟 USB 设备成功而宣称真实硬件重定向、安全存储写入、摄像头 / 音频等时序敏感设备或 Windows 已支持。


服务端现在提供[有界安全计数](research/server-security-observations-validation.md)：只含loaded TLS配置的固定数值/枚举/标记，区分TLS、framing与outer限额。agent逐实际UID/profile Read授权，授权全部结果绕WAL、拒绝仍durable；无peer地址、pin、principal、USB内容或原始错误文本。它是volatile观测，不是持久安全审计、实时撤销、设备cleanup或完整G3证明。安装态负向计数只验raw-v1六组合；framed-v2与agent IPC的软件测试/独立诊断回归不冒充安装态counter验收。

## 有界认证准入

[认证准入限速验收](research/authentication-admission-validation.md)覆盖默认64初始/125ms补充与root可选严格策略。每个已加载TLS服务配置的clone/job共用Governor direct gate，连接permit先于rate decision，verifier/IO在其后；失败/取消不退配额，原有设备数据流不重复检查。新计数只有policy/固定数值/一致性，无peer/key/principal；授权读取不等WAL。六raw-v1 QEMU在实际拒绝期间完成原HID/串口I/O，并在补充后成功新认证；六默认配置及独立v2诊断回归分账。不是按来源公平、持续攻击/性能或公网部署资格；安装态agent/v2专项耗尽、持久安全历史与完整G3仍未完成。

## 主机名回退与身份边界

[主机名验收](research/dual-stack-connect-validation.md)不把DNS映射当身份：仅选TCP winner，原service_name/PKI/ALPN/pin/设备ACL和helper授权保持，TLS失败不换地址重试或降级。请求不能覆盖endpoint。四actual resolver工作/64raw/16unique及候选inline释放约束RustUSB资源；真实lookup可超过waiter生命周期，不能假称取消或bounded libc。六raw与一Native/v2 hostname仿真只用guest hosts/multi-on策略和实际UID2000探针，不外推wire-DNS/VPN/长期DoS/性能、其他v2专项或完整G3。

本地观察E41见[设计](research/local-device-monitor-design.md)与[验收](research/local-device-monitor-validation.md)：kernel实际source pid0/groups1只产生重扫提示，root可合成通知但不能由通知授予设备能力；OS用户读取自身可见sysfs，无新helper/agent宽权限RPC。candidate/Bus ID/观察stamp不认证硬件或继承授权，Current不等于可导出/兼容。stale/容量错误不伪空，worker配额至实际完成，stdout metadata不持USB能力。显式devices JSON含serial须脱敏；本地测试raw只留.private且公开清单排除。两个单guest与六新process不完成ENOBUFS/永久OS阻塞或完整安全G3。

托管目录E42见[验收](research/catalog-supervisor-validation.md)：两个actual read任务不持USB/helper能力或写WAL，原OS UID/profile Discover授权先于容量；endpoint/凭据不可由RPC覆盖。丢RPC不abort已接受网络owner，stop/EOF总join，成功audit仍提交后才回应。容量与unhealthy journal沿旧state类别拒绝；该界限不保证公平/长期DoS安全，真实WAL/DNS硬阻塞与完整G3继续未验收。

认证远程观察E43见[验收](research/remote-catalog-watch-validation.md)：operator只用公开可信manifest/固定profile，真实UID逐poll Discover授权，endpoint/私钥无RPC覆盖。direct源保原PKI/ALPN/ACL、原FIN/driverjoin；订阅仅metadata，不持source凭据/socket/配额。权限/协议等终态清当前view并停止，既有generic TLS handshake Permission不等于特定证书错误；旧copy不能强擦除。agent本地observer join不证明已接受远端read/独立resolver/USB清理，旧服务端actualowner/成功audit规则保留。两本地actualowner与退避非公平/长期DoS保证，busy目录不授能力或物理generation。完整G3/硬件/后续阶段不变。

离线CRL E44见[设计](research/offline-crl-design.md)、[指南](offline-crl.md)与[验收](research/offline-crl-validation.md)：可选完整DER列表受root文件路径限制，present null/空拒绝；1..16/每64KiB/总256KiB，单raw issuer唯一，拒delta/IDP/indirect。x509-parser仅给有界元数据，标准WebPki保持fullchain/unknown-deny/expiry及PKI/name/pin/ACL；Checked SystemTime共同窗口在每次新验证检查，未用列表到期也拒新连接。无热更新/已有stream撤销/在线fetch或持久CRL序号抗回滚；agent stop/start单job不重载，须全实例合作退出再启动。新采证无ALPN握手使用保存空输入，仍strict exit1/无reply/实际protocol mismatch，不新增104例外。原FAIL、硬件与完整G3保持。

## 普通用户终端边界

[TUI](tui.md)只读public manifest、普通UID调用原profile RPC；无TLS私钥、helper变更或shell。确认不能替代实际授权；未知Start保原UUID且只RetryStart，过期不证明旧操作未开始。stop accepted与实际cleanup分开，退出仅停止/总joinmetadata owner而不停止服务job。[E45](research/tui-validation.md)不完成完整G3/G4或硬件。

## 终端偏好隔离（E46）

[E46](research/tui-settings-actions-validation.md)的用户JSON仅interval/polling/monochrome，strict1/4KiB；不是认证或设备规则，UI仍逐实际UID/profile经原RPC。文件caller0700/0600/NOFOLLOW/nlink1/独立OFD锁/有限read/own临时sync与重核rename；不覆盖未知JSON，publication错误可不确定。启动load不创建目录，所有accepted blocking FS工作真实join，无永久阻塞/所有power-loss保证。确认操作实际Native双栈通过，不能扩大为lost-ACK UI/全部G4/硬件或Root权限。


终端E47见[未知启动/失联验收](research/tui-failure-retry-validation.md)：clean4ee6be3e完整952软件/235源，两故障与两原E46正常动作Native双栈pair/8guest0、76实际UID1000 PTY通过。实际Start超时后同agent恢复，UI只确认原UUID RetryStart；精确job current/stale/current，首次Profiles失联不再保留Current或旧journal/health。原38477360两guest0/19PTY但整体FAIL保留，不改其裁决；原264核验/三轮I/O/完整Rootproof不弱化。SIGSTOP不证明wire-loss或永久OS阻塞；UI热插拔/权威拒绝、完整G4/G3/硬件与后续阶段仍待完成。


E48使用真实OS peer的Read/Audit-only固定profile：Start与原UUID RetryStart均返回Permission并留下两durable denied、零准入；UI显示拒绝且退出保守报告未决身份，不能以确认或窗口关闭推断释放。三偏好重载只读受检用户文件，root/JSON/nonTTY启动被拒；没有授予编辑管理员策略/凭据的权限。 见[验收与审计](research/tui-hotplug-permission-validation.md)。

## Storage arrival experiment boundary

The [Native arrival experiment](research/storage-arrival-quarantine-validation.md) tests a fresh device under preconfigured device/interface authorization denial on a dedicated guest controller. It does not fence privileged external changes or previously delegated writable USBFS descriptors; HCD recreation/reboot may reset defaults. Source isolation, client cache-command result, transport closure and physical durability are separate claims. Product storage admission remains denied. The two blocked failed guests and their forced disposal are retained as failures.
