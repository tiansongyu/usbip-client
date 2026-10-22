# Linux 官方 USB/IP 源码审计

本文供 RustUSB Native Backend 的实现者及安全审查者使用。结论来自实际源码，不能代替 Ubuntu 双机和真实 USB 设备测试。**Native Backend 应复用官方工具的管理操作和内核 URB 数据通路；不得把 `tools/usb/usbip` 当成完整实现，也不得把 `usbip attach` 退出成功当成设备已经枚举。**

## 审计基线与证据范围

- 主基线：`torvalds/linux` commit **`7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614`**。通过 GitHub API 解析 master 后固定，按目录下载 `tools/usb/usbip`、`drivers/usb/usbip`、协议文档及许可证；没有克隆整个 Linux 仓库。
- 补充核对：Linux v5.15 commit `8bb7eca972ad531c9b149c0a51ab43a417385813`、v6.8 commit `e8f897f4afef0031fe618a8e94127a0934896aba`。`git ls-remote` 核实了两个 tag 的解引用 commit。已阅读两版 attach/unbind/usbipd/configure/vhci_sysfs/stub_dev/UAPI 的相关实现；这些是上游基础版，不是 Ubuntu 当前维护分支。
- 当前源码快照位于 `/tmp/rustusb-upstream-linux`，是审计缓存，不属于产品交付依赖。源码证据以本文 commit 永久链接为准。配套 `scripts/audit-linux-upstream.sh` 可重新获取一组固定文件并打印哈希；它只做 HTTP GET 和本地审计文件写入，不操作 USB、内核模块或网络监听。
- 已验证：源码路径、管理协议格式、sysfs 输入/输出及清理实现。未验证：本机模块加载、真实设备导出、Ubuntu 22.04/24.04 双机枚举、ISO 实时质量、硬件吞吐和跨内核兼容。不要把后文的“代码具备路径”升级为产品支持声明。

## 1. 用户态与内核边界

| 组件 | 实际职责 | RustUSB 的复用方式 |
| --- | --- | --- |
| `usbip` CLI | 本地 bind/unbind；TCP DEVLIST/IMPORT 握手；将已连接 socket 的 fd 写入 VHCI；detach；展示端口 | 通过固定可执行文件和独立 argv 调用，独立扫描 sysfs 验证结果 |
| `usbipd` | 接受 TCP，刷新导出列表，处理 DEVLIST/IMPORT，把 socket fd 交给服务端内核 | 作为管理员配置的服务使用；不可假设默认监听安全 |
| `usbip_host` 模块 / `usbip-host` 驱动 | 占用真实设备；`stub_rx/stub_tx` 接收、执行、完成 URB；持有 socket | 直接复用，不在 Rust 中重新实现物理 URB 路径 |
| `vhci_hcd` | 客户端虚拟主控制器及根集线器；本地原生驱动提交 URB，收取远程完成 | 第一阶段唯一导入实现；Linux 专用 |
| `usbip_core` | 内核共用报文转换、收发、ISO 和事件处理 | 由前述模块依赖加载 |
| `usbip-vudc` | Linux 内核 USB gadget 虚拟 UDC 路径 | 独立能力；不等同于 jiegec 的 Rust 虚拟设备 |

依据：[usbip 命令分派][cli]、[usbipd 请求处理][daemon-requests]、[服务端 socket 接管][stub-fd]、[VHCI attach][vhci-attach]。模块名与 sysfs 驱动名中的 `-`、`_` 不应混为一个字符串；探测时分别核对已加载模块和驱动目录，而不是只查 `PATH`。

数据通路是：客户端原生 USB 驱动 → VHCI → 内核 TCP USB/IP → 服务端 `usbip-host` → 本地主控制器 → 物理设备。Rust 管理 API 的认证或 TLS 不会改变这条 TCP 路径。jiegec/rusb/nusb 则是另一条用户态设备访问路径，同一物理设备不得同时由两种后端占有。

## 2. bind / unbind：不是原子事务

官方 `usbip bind --busid BUSID` 的实际顺序：

1. 查找 USB 设备并拒绝 VHCI 导入设备，防止循环导出。
2. 拒绝 USB hub；读取当前**设备级**驱动，向其 `unbind` 写 Bus ID。
3. 向 `/sys/bus/usb/drivers/usbip-host/match_busid` 写 `add BUSID`。
4. 向 `/sys/bus/usb/drivers/usbip-host/bind` 写 Bus ID。

关键失败路径：第 3 步失败时原驱动已经解除；第 4 步失败只尝试 `del BUSID`，不恢复原驱动。不能把子进程错误当成“系统未改变”。源码还提醒驱动解绑可能阻塞；执行超时不保证内核 write 已撤销。依据：[usbip_bind.c L59–179][bind]、[match_busid 表实现 L167–197][match]。

官方 `usbip unbind --busid BUSID` 是：验证目前属于 `usbip-host` → 写 `usbip-host/unbind` → `match_busid` 写 `del BUSID` → `usbip-host/rebind` 写 Bus ID。[用户态顺序][unbind]与[内核 rebind][rebind]表明最终调用 `device_attach()` 重新探测设备。**这是重新探测，不是按保存的每个 interface driver 精确回滚；也不证明存储文件系统可以安全重新挂载。**

RustUSB 需要额外完成：

- 操作前保存设备身份、设备/接口绑定和挂载风险快照，重新核对当前 Bus ID 指向同一设备；持久 ID 不能只用 Bus ID。
- 对正在导出且 `usbip_status=2` 的设备拒绝普通 unexport；先协调客户端正常释放，不把强制断开藏在普通操作中。
- bind/unbind 无论成功、错误、超时还是取消，都重新扫描真实驱动状态；记录“已解绑但未恢复”等部分成功状态。只对可证明由本次操作造成且对象身份仍匹配的变化执行幂等补偿。
- 驱动重新探测和具体 class driver 的恢复分别验证。设备已拔出应记录为失去对象，不能对后来占用同 Bus ID 的设备继续补偿。
- 存储设备需另做所有 mount namespace、块设备后代、swap、device mapper、活动访问等风险检查。官方 bind 不会替 RustUSB 安全卸载或检查文件系统；不可从其可绑定推导可安全共享。

## 3. sysfs 接口与 socket 所有权

下表是当前源码行为；实际系统应动态发现 VHCI 控制器目录，不硬编码唯一控制器或端口数量。

| 位置 | 内容/语义 | 注意事项 |
| --- | --- | --- |
| `/sys/bus/usb/drivers/usbip-host/match_busid` | `add BUSID` / `del BUSID` | 官方按精确 Bus ID 写，不允许用户提供任意 sysfs 路径 |
| `/sys/bus/usb/drivers/usbip-host/{bind,unbind,rebind}` | Bus ID 字符串 | `rebind` 触发重新探测，非原驱动快照恢复 |
| `/sys/bus/usb/devices/BUSID/usbip_status` | `1` AVAILABLE、`2` USED、`3` ERROR | 存在不代表设备已授权供任意远端访问 |
| `/sys/bus/usb/devices/BUSID/usbip_sockfd` | 非负 fd：接管已连接流 socket；`-1`：请求断开 | 必须是**执行 write 的同一进程 fd 表**中的 fd；有内核状态检查 |
| VHCI 根目录 `/attach` | `port sockfd devid speed`，四个十进制整数 | `devid=(busnum<<16)\|devnum`；选正确 HS/SS hub；成功返回写入字节数 |
| VHCI 根目录 `/detach` | 十进制端口号 | 异步触发清理；write 完成不等于端口已恢复空闲 |
| VHCI 根目录 `/nports`、`/status`、`/status.N` | 总端口数、各控制器的端口状态表 | 多 controller 时需读所有 status 属性，不只 status |

依据：[服务端 fd 交接 L38–141][stub-fd]、[VHCI attach L303–453][vhci-attach]、[detach L185–273][vhci-detach]、[用户态 VHCI 属性构造 L358–424][vhci-user]、[多控制器状态刷新 L111–135][vhci-refresh]。源码 attach 注释写“returns 0”，但函数实际成功分支为 `return count`；以实现为准。

内核 `sockfd_lookup()` 获取引用，用户态成功交接后可以 close 自己的 fd，内核连接继续存在。因此不能让子进程 `echo` 写父进程 fd，也不能认为关闭管理 CLI、Rust 守护进程或 usbipd 就会拆除已接管的连接。服务端显式 `-1` 和客户端 detach 都是状态机操作，必须验证结果。

TLS Rust 对象的底层 TCP fd 承载加密记录，内核 USB/IP 接收器只理解明文 USB/IP，不能直接交接该 fd。安全隧道应使用本地 TCP 代理：VHCI/usbip-host 连接本地明文端点，代理持有长连接并在对等端之间执行认证、TLS 和双向转发。必须覆盖 DEVLIST 的短连接和 IMPORT 后同 socket 的持续 URB 流量；不能在握手后切回公网直连。

## 4. attach / detach / status 的精确行为

attach 先进行 TCP IMPORT，检查版本/opcode/status，读取完整设备记录并核对回包 Bus ID；再查找速度匹配的空闲 VHCI 端口，把 socket fd 交内核，关闭本进程 fd。随后官方 CLI 才将 host、port、remote Bus ID 写入 `/var/run/vhci_hcd/portN`。[usbip_attach.c L81–198][attach]

重要陷阱：

- **内核 attach 成功后，状态文件写入失败仍返回命令失败；此时连接可能活着。** 出错必须核对 sysfs 状态，不可盲目重试并占用第二端口。
- `/var/run/vhci_hcd/portN` 只是用户态元数据，不是活跃会话的权威源。记录文件可能残留、缺失、截断；进程重启后以内核状态为主，远端身份无法确定时保留 Unknown。
- `usbip detach` 在向内核写 detach **之前**删除记录文件；写失败可能导致活跃会话丢失该记录。[usbip_detach.c L32–94][detach-cli]
- CLI `portnum` 是 `uint8_t`，经 `atoi` 赋值；`256` 等输入可能回绕。调用工具前必须严格解析，限定 `0..=255`，同时核对 nports 与目标会话。若真实系统有更多端口，不允许截断，应拒绝当前工具适配能力并报告限制。
- 官方导入库对端口的 API 也使用 `uint8_t`；Rust 类型不能无检查 cast。内核接口接受 `u32` 并自行校验，不意味着用户态工具支持同样的范围。[VHCI 库 L358–424][vhci-user]
- 内核只允许在 NULL 端口 attach，冲突会 `EBUSY`；单进程锁不能阻止管理员在旁边执行 usbip。操作后只认领确定匹配的变化，不凭“多了一行”认领别人的会话。

VHCI 状态来自 [UAPI 枚举][uapi]：`4=NULL`、`5=NOTASSIGNED`、`6=USED`、`7=ERROR`。`5` 表示已交接而地址尚未分配，不能报告已经连接可用。当前表头为 `hub port sta spd dev sockfd local_busid`；dev 为十六进制 remote devid，其他数值列需按实际格式解析，`sockfd` 是历史用户 fd 数值，不是可继续操作的当前有效句柄。[状态输出 L43–66][vhci-status]

验收 `Connected` 至少需要：本次导入匹配的端口进入 USED、`local_busid` 对应 sysfs 设备出现、VID/PID/身份与预期一致；类驱动工作和真实 I/O 另行验证。退出码和空闲端口变少都不够。

## 5. USB/IP 协议兼容性报告

| 项目 | 固定源码结论 | 实现要求 |
| --- | --- | --- |
| 版本 | 工具包显示 `usbip-utils 2.0`；wire version 是 **`0x0111`（v1.1.1）** | 不把工具版本 2.0 写进 wire header |
| 管理头 | 8 字节：BE u16 version、BE u16 opcode、BE u32 status | 精确读取，校验预期 opcode/version/status |
| DEVLIST | request `0x8005`；reply `0x0005`，随后 BE u32 ndev | ndev、累计字节数、每设备接口数均受限 |
| IMPORT | request `0x8003` + 32 字节 Bus ID；reply `0x0003` + 312 字节设备记录（成功时） | 出错回复只有头时不得继续等待设备记录 |
| 设备记录 | path 256、Bus ID 32、busnum/devnum/speed 各 u32、VID/PID/bcd 各 u16、6 个 u8，合计312字节 | 固定宽度、NUL字符串、数值范围校验 |
| DEVLIST 接口 | 每个4字节：class/subclass/protocol/padding | 不包含完整 USB endpoint 或 alternate-setting 描述符 |
| URB | CMD_SUBMIT=1、CMD_UNLINK=2、RET_SUBMIT=3、RET_UNLINK=4；公共头20字节，最大联合体28字节，固定总头48字节 | 支持并发、多笔未完成、正确 seqnum 关联 |
| ISO | 每包16字节 offset/length/actual_length/status；传输数据和 ISO 描述符顺序有规定 | 单独验证 packet 数、偏移、长度、总长度及整型溢出 |
| 字节序 | USB/IP 数值字段用网络大端；USB Control setup 本身为原始8字节 USB 小端结构 | 不要对原始setup再做USB/IP大端转换 |
| 取消 | 成功 UNLINK 后，原 URB 不再有 RET_SUBMIT；已完成后再 UNLINK 可返回0 | 不把取消实现为永久等待两个回复 |
| 非使用操作 | 头文件虽定义 DEVINFO、CRYPKEY、EXPORT、UNEXPORT，但 usbipd dispatcher 不执行它们 | 不以存在常量宣称远程绑定或加密已支持 |

证据：[configure.ac L4–5][configure]、[管理结构定义][network-h]、[设备结构 L104–129][device-layout]、[头接收校验 L162–207][network-check]、[内核 URB/ISO 结构 L95–227][urb-headers]、[协议文档][protocol]、[usbipd dispatcher][daemon-requests]。

原生 server 的 status 码定义为 0=OK、1=不可用/通用失败、2=busy、3=device error、4=no device、5=unexpected response；不可把所有非零当成“重试即可”。固定 master 的 DEVLIST 排除 USED 设备，但 Ubuntu 的打包版本可能不同；列表中缺席不能证明物理拔出或权限撤销。[status 定义][device-layout]、[DEVLIST 过滤][daemon-requests]

## 6. URB、四种传输与 Composite

服务端 `get_pipe()` 基于真实 endpoint 描述符区分 Control、Bulk、Interrupt、Isochronous；ISO 有真实分配和收发路径，packet 数上限为1024。随后分配 buffer/URB、接收 OUT 数据、接收 ISO 描述符、调用 `usb_submit_urb()`，完成回调送入发送队列。**因此 Native 有全部四类传输实现路径，但不能据此保证任何 UVC/音频设备在任意 RTT 下稳定。**[endpoint 路由][stub-pipe]、[提交路径][stub-submit]、[完成及发送][stub-tx]、[ISO 实现][iso]

Composite 是**整设备**独占导出，不是同时让 server 保留 HID、client 拿存储的接口拆分服务。协议列表携带接口类别，数据平面中 endpoint/control 仍属于同一物理设备。SET_INTERFACE 会通过 `usb_set_interface()` 调整备用设置；SET_CONFIGURATION、clear halt、设备 reset 也有专门处理。[特殊 Control 请求 L15–202][special]

安全上不能因为复用内核就忽略远端输入。该版本内核针对 ISO 回包 packet 数和重排偏移做了边界验证；旧内核或发行版是否回移这些修复需单独核对，不能由 Ubuntu 发行号推出。RustUSB 的管理解析器、隧道会话和实验 Rust 数据平面仍需各自设置长度/并发/内存/时间上限。本文未对所有历史 CVE 或全部内核报文路径作安全证明。

## 7. 断网、进程退出与热拔插

- TCP 接收/发送错误产生 USB/IP error event。通用事件工作队列按 shutdown → reset → unusable 的顺序处理，不是管理进程自行继续传输。[event handler L62–98][events]、[事件标志 L235–257][urb-headers]
- 服务端 shutdown：shutdown socket、停止 RX/TX 线程、释放 socket 引用、清理 URB 和 unlink；reset 调用真实 `usb_reset_device()`，成功回到 AVAILABLE，失败变 ERROR。**仍可能绑定 `usbip-host`，不会自动归还本地 class driver。**[stub_dev.c L153–249][stub-cleanup]
- 客户端 shutdown：shutdown socket、停止线程、释放引用、清理 unlink、触发 root-hub disconnect；随后 reset 清空设备引用、speed/devid，状态回 NULL。[vhci_hcd.c L1039–1126][vhci-cleanup]
- USB 物理 disconnect 走驱动 disconnect/removal 路径并提交 REMOVED。Bus ID 复用后是新观察对象；管理层必须处理拔出、重枚举及身份歧义。[stub disconnect][stub-disconnect]
- `SO_KEEPALIVE` 被客户端设置，但源码保留 heartbeat TODO；10秒 `ppoll` timeout 是 usbipd 的监听循环计时，**不是活跃 USB 数据连接心跳，也不是10秒断网保证**。[TCP connect L261–302][connect]、[usbipd loop L551–577][daemon-listen]
- 黑洞网络无 FIN/RST 时，内核 TCP 需要时才能感知超时；应用必须另定义限时和受控 detach。不能承诺即时故障检测。

存储故障恢复必须停在保守状态：确认是否存在未完成写入/客户端脏缓存，检查身份并重新枚举，必要时人工处理文件系统。内核设备 reset/AVAILABLE 不提供远程文件系统一致性保证；不得自动重新挂载并恢复写入。

## 8. usbipd 监听、安全和 CLI 契约

默认 TCP 为3240。usbipd 的地址解析用 `AI_PASSIVE`，传入 host=NULL，未指定 `-4/-6` 时两者都启用；因此默认监听所有可用 IPv4/IPv6 地址。其参数只有地址族选择、daemon/debug、pid、端口、device、help/version，**没有 `--bind`/`--listen-address`/loopback 选项**。[监听实现][daemon-listen]、[完整参数][daemon-args]、[默认端口][connect]

不能为本地 TLS 代理直接运行 `usbipd --bind 127.0.0.1`。可接受的部署设计是管理员显式提供经审查的防火墙/网络命名空间隔离，将真正的3240数据服务只开放给本地代理，或置于已授权 VPN 内并验证路径。端口改成自定义值仍是全接口监听，不构成访问控制。libwrap 是否启用取决于构建且没有设备级身份授权，不是 mTLS。[libwrap 构建配置][configure]、[接受连接][daemon-listen]

可依赖的 argv 形式：

```text
usbip version
usbip --tcp-port PORT list --remote HOST
usbip --tcp-port PORT attach --remote HOST --busid BUSID
usbip bind --busid BUSID
usbip unbind --busid BUSID
usbip detach --port PORT
usbip port
usbipd --version
```

全局 `--tcp-port` 必须出现在子命令之前，`usbip` 无全局 `--version`；`usbipd` 才有。`usbip list --parsable` 的 parsable 标志用于本地/gadget 分支，远程列表仍是人类文本，不应当成可靠 JSON/结构化 API。[命令解析 L143–190][cli]、[list 分支 L326–365][list]

RustUSB 原生适配层应固定可信 executable 绝对路径、禁止 shell、清理危险环境、限制输出字节、设置超时/取消、单操作串行化、记录 argv 的类型化摘要而不记录敏感描述符数据。守护进程允许的特权操作应是固定枚举，不接收任意命令、环境、路径或任意服务名。

## 9. 可复用清单和许可证边界

| 材料 | 源码许可证据 | RustUSB 策略 |
| --- | --- | --- |
| `usbip`、`usbipd` 独立系统工具 | bind/unbind/usbipd `.c` 为 `GPL-2.0-or-later`；部分头如 network/common/vhci 为 `GPL-2.0` | 通过进程调用，保留为发行版外部依赖；不复制/翻译实现进入 MIT 自有源码 |
| 内核 `stub_*`、`vhci_*`、`usbip_common` | 多数 `GPL-2.0+`，个别文件依自身 SPDX | 使用现有模块，不打包修改版内核代码 |
| `include/uapi/linux/usbip.h` | `GPL-2.0 WITH Linux-syscall-note` | 可依据文档独立描述接口/常量；如实际引入头文件，保留其许可和来源 |
| wire 布局与 sysfs ABI | 官方协议文档和上述实际实现 | 独立写安全 Rust parser/adapter；记录来源、测试字节布局；不粘贴 GPL C函数后改语法 |
| 未来定制官方工具/内核 patch | 继承相关 GPL 文件约束 | 独立维护 patch、完整许可/版权与相应源码交付；发布前单独审核 |

[Linux COPYING][copying]、[官方许可规则][license-rules]及[Linux-syscall-note][syscall-note]明确区分正常内核系统调用和 UAPI 例外。该例外不能扩大解释为任意复制 `tools/usb/usbip/libsrc` 都可变成 MIT。本文采取保守工程边界；“独立进程”不是把衍生代码换个进程便自动免除义务的承诺。第三方发布清单还应单列 Ubuntu linux-tools 包、原始作者、精确包版本、来源 URL、许可证、是否分发/修改、对应源码获取方式。

## 10. Native 与 Rust Backend 的差距及验收门

| 能力 | Linux 原生已有实现 | RustUSB 尚须提供/验证 |
| --- | --- | --- |
| 设备级 USB 重定向 | 内核完整 URB 数据通路和 VHCI | 双机真实设备枚举及真实I/O证据 |
| 独占 | 服务端状态与锁拒绝二次占用 | 并发管理操作、权限、分配策略和争用测试 |
| 热插拔 | 内核驱动与 disconnect 事件 | 用户可见刷新、持久身份和歧义处理 |
| Control/Bulk/Interrupt/ISO | 已有代码路径 | 每类设备、alternate setting、RTT/丢包实测矩阵 |
| 网络安全 | 原始 USB/IP TCP，无默认现代认证加密 | mTLS/授权/隔离、控制与实际数据连接验证 |
| 恢复 | socket、URB、VHCI端口的内核清理 | 应用状态收敛、幂等补偿、驱动恢复、存储人工策略 |
| 状态/诊断 | sysfs 与 CLI 文本 | 有界解析、事件、结构化日志和未知状态 |
| CLI权限 | 写 sysfs 依赖操作系统权限 | 普通用户前端 + 受限特权服务和本地凭据授权 |

Phase 1 工程验证应覆盖 wire 固定字节、分片/截断/超长回复、无设备/忙/版本错误、fake sysfs多controller状态、恶意Bus ID/hostname/port、shell注入、工具超时/非零后副作用、detach异步完成、记录文件缺失。双机 Ubuntu 验收另需证明：导出前空闲 → bind成功且driver正确 → remote DEVLIST → IMPORT和VHCI USED → 正确VID/PID及class driver → 测试设备I/O → 安全detach → server解除导出并恢复本地驱动；同时核对有权限拒绝和断网清理。没有真实设备时这些硬件 gate 应保持未完成。

## 固定源码索引

[cli]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip.c#L41-L190
[bind]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip_bind.c#L59-L179
[unbind]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip_unbind.c#L30-L97
[match]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_main.c#L167-L197
[rebind]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_main.c#L199-L274
[attach]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip_attach.c#L81-L198
[detach-cli]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip_detach.c#L32-L94
[vhci-user]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/libsrc/vhci_driver.c#L358-L424
[vhci-refresh]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/libsrc/vhci_driver.c#L111-L135
[vhci-attach]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/vhci_sysfs.c#L303-L453
[vhci-detach]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/vhci_sysfs.c#L185-L273
[vhci-status]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/vhci_sysfs.c#L43-L183
[stub-fd]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_dev.c#L19-L143
[configure]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/configure.ac
[network-h]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip_network.h#L21-L161
[network-check]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip_network.c#L162-L207
[device-layout]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/libsrc/usbip_common.h#L25-L129
[urb-headers]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/usbip_common.h#L95-L257
[protocol]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/Documentation/usb/usbip_protocol.rst
[stub-pipe]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_rx.c#L335-L397
[stub-submit]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_rx.c#L460-L648
[stub-tx]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_tx.c
[iso]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/usbip_common.c#L465-L902
[special]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_rx.c#L15-L202
[uapi]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/include/uapi/linux/usbip.h#L1-L26
[events]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/usbip_event.c#L62-L98
[stub-cleanup]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_dev.c#L153-L249
[vhci-cleanup]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/vhci_hcd.c#L1039-L1126
[stub-disconnect]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/drivers/usb/usbip/stub_dev.c#L386-L424
[connect]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip_network.c#L23-L302
[daemon-requests]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbipd.c#L91-L289
[daemon-listen]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbipd.c#L308-L583
[daemon-args]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbipd.c#L586-L685
[list]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/tools/usb/usbip/src/usbip_list.c#L326-L365
[copying]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/COPYING
[license-rules]: https://docs.kernel.org/process/license-rules.html
[syscall-note]: https://github.com/torvalds/linux/blob/7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614/LICENSES/exceptions/Linux-syscall-note
