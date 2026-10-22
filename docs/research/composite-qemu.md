# USBFS 多接口 CDC ECM 与 alternate 的隔离 QEMU 验证

2026-10-22。状态：clean提交`22d172d84779d92fe51f5a1c6f9c99e5ff747049`的最终ECM、原物理normal/hot回归均PASS，六个guest均exit0。设计与恢复修复见[专门设计](usbfs-composite-qemu-design.md)，已有HID/FTDI结果见[原物理报告](physical-qemu.md)。本轮不将单个CDC功能的两个接口外推为任意混合Composite、任意配置、ISO或真实硬件兼容性。

## 固定模型与网络边界

本机QEMU6.2.0 `usb-net`可用，help仅提供netdev/mac等通用属性，没有ECM配置开关。上游v6.2.0描述符index0对应RNDIS配置**值2**，index1对应CDC ECM配置**值1**。设备为FullSpeed、EP0=64、VID/PID0525:a4a2。ECM IF0为COMM02/06/00、Interrupt IN81/MPS16；IF1为DATA0a，alt0没有端点，alt1提供Bulk82/02/MPS64。RNDIS值2的IF1则只有带Bulk端点的alt0；不混淆配置索引、配置值和alternate。

两个来宾的管理网络仍为192.0.2.0/24。第二条继承的Unix socketpair两端都交给server QEMU，一端连接usb-net backend，另一端连接第二e1000。server peer网卡198.18.0.1/30，client导入的USB网卡198.18.0.2/30；server本地USB网卡保持没有IPv4地址。两地址在不同来宾，精确UDP数据必须经过client cdc_ether/VHCI、USB/IP、server普通UID USBFS、QEMU USB-net与第二e1000。按USB sysfs路径/MAC识别网卡，检查client对应连接路由。无host TAP、slirp、宿主USB/模块/网络修改或host网络listener。

最终harness为这个Full-Speed模型选择`piix3-usb-uhci`，`uhci-hcd`在当前内核为builtin；旧physical/虚拟harness的xHCI不改。raw probe期间peer网卡显式down，避免IPv6控制流量改变合法pending观察，真实释放后才up并启动普通UID peer。两个私有data netdev分别捕获合成测试PCAP，不涉及宿主流量。server来宾另加载usbmon、挂载debugfs并只订阅测试USB bus；启动reader后才放行raw probe，结束或失败均kill+wait。USBMON是显式测试诊断，不能混同生产默认日志。

root仅在来宾加载usbnet/cdc_ether/mii、设置固定网卡、节点root0600和精确physical_device_ids策略。普通UID1000/零cap运行physical-server和UDP peer；UID1000/1001直接usbfs open均须拒绝，UID1001明确OpenPhysical拒绝仍单独检查。两个原接口均为cdc_ether、原配置1及alt0/1，在每次释放后独立sysfs复核同代际恢复。

## 独立probe与内核契约

新增三个文件，不修改旧九场景、原physical或userspace脚本：`run-composite-qemu.py`、`guest-composite-init.sh`、`composite-wire-probe.rs`。独立Rust probe只用std，不调用产品codec。

- 动态DEVLIST/IMPORT，读取真实完整设备及两个配置描述符，检查两接口/三个ECM alternate描述、端点类型及MPS。
- SET_CONFIGURATION值1/2/0及GET_CONFIGURATION、GET_INTERFACE真实回读；值2的alt0 Bulk IN可真实pending/取消，值1的alt0不存在该端点，返回明确-32策略失败；不将该失败冒称真实硬件STALL。
- 配置或alternate与后到请求放在同一TCP write，核对新映射。两个旧Bulk IN先保持pending，再alt1→0；旧active/queued请求应退休-108，后到IN按空map拒绝，不能继续旧端点。
- 非法alt2明确-32且GET_INTERFACE仍为0；随后选择alt1。真实厂商Control和过短Interrupt IN触发模型-32，后续同连接控制读取仍成功。
- UHCI的短Interrupt IN还须独立USBMON交叉验证：wire严格`status=-32/actual=1`，同URB tag/pipe的USBMON S长度1、C状态-32/长度1及本次opaque buffer逐字节一致；不硬编码buffer应为00。这是当前QEMU UHCI/Linux组合的kernel-reported长度保留，不能称设备实际传了1B或真实硬件partial-error已通过。
- QEMU ECM模型不实现endpoint CLEAR_FEATURE，真实失败须保留-32，不能为测试通过伪造clear成功；本轮不提供成功clearhalt恢复证据。
- host-state UNLINK只接受queued取消赢、没有旧completion且实际配置保持1，或不可取消ioctl完成赢、原成功completion与UNLINK0且配置读回2。记录实际winner，不声称一次运行覆盖全部竞态。向TCP写入SET_CONFIGURATION帧后立即断开，只证明此提交方式下重新available；没有同步确认server已接受请求或ioctl已进入active窗口，该窗口仍待专门确定性测试。
- 另以真实pending端点EOF、unsupported ISO frame拒绝及重新available验证有界清理。ISO未做真实等时传输，也不因此整机禁用混合设备的其他端点。

两次内核周期分别由UID1000 RustUSB helper管理导入及guest root官方usbip直接导入。每次确认config1、两个cdc_ether接口、alt0/1、对应网卡路由，再发送含cycle和固定nonce的UDP请求；普通UID peer返回不同固定内容，客户端逐字节核对。两端都记录精确长度/周期，server快照两方向Bulk计数必须增长。占用IMPORT须返回status2。官方导入显示unmanaged，RustUSB detach明确拒绝后仍完成精确I/O；分别由原所有者释放，不用helper冒领官方端口。

每次释放要求client sessions0及旧USB目录消失、server重新available；server pending/queued/inflight/buffer均0，cleanup_verified=true、原配置/alternate/两个驱动恢复。最终有界shutdown、两guest exit0。默认生产server stderr及JSON事件中另搜索本次固定请求/响应nonce前缀并要求不存在；仅证明该默认日志级别，不外推全部sink或完整日志审计。

## 执行与证据

```sh
python3 scripts/qemu/run-composite-qemu.py --evidence /tmp/rustusb-composite-dev01
```

默认locked all-features release构建，确保CLI/helper物理能力都启用；probe以Rust2024、`-O -D warnings`编译。模块若非builtin且缺失则预检失败，记录实际模块hash；记录Git commit/dirty、所有Rust与vendor来源、三个新文件及共享builder指纹、guest实际CLI/helper副本、kernel/initramfs/probe/native工具hash。运行前后源码或二进制变化一律不能PASS。开发FAIL完整保留，不覆盖日志；最终证据需另在clean提交默认构建执行。

## 开发结果与全部失败记录

所有运行均默认locked all-features构建、ordinary宿主UID1000、QEMU6.2.0/KVM、Linux6.8.0-106-generic。dev01–07为FAIL，dev08为PASS；准确起始commit/dirty和每次三脚本快照见[聚合索引](../evidence/composite-development-20261022.json)，不把dirty开发PASS改写成最终冻结结果。产品CLI/helper在八次运行中SHA256始终分别为`c033d3931a159ad579977331140a6d327bd86939bfdd19cf794f304527d04db9`和`5d40d8e39286ab722a1abe7a09d1c68e0da9c5f3a7d2273f783b09f95cd64ae0`；没有为适配模型修改Rust产品或缩短原UDP nonce。

| 运行 | 结果与准确原因/观察 | 原始结果与全部SHA |
| --- | --- | --- |
| dev01 | FAIL于原状态前置断言；尚未helper/IMPORT，缺少细分上下文，保留后由dev02定位。 | [summary](../evidence/qemu-composite-dev01-20261022/summary.json)、[SHA](../evidence/qemu-composite-dev01-20261022/artifacts.sha256) |
| dev02 | FAIL，实际`bNumInterfaces= 2`。同BusyBox的`tr -d '[:space:]'`保留空白；新guest改显式ASCII空白删除，仍严格比较2/config/alt。设备和产品无故障证据。 | [summary](../evidence/qemu-composite-dev02-20261022/summary.json)、[SHA](../evidence/qemu-composite-dev02-20261022/artifacts.sha256) |
| dev03 | FAIL于xHCI下首个UDP接收超时；15项raw及原配置/alt/双驱动恢复已过，内核cdc_ether也已枚举，尚无实际UDP内容PASS。 | [summary](../evidence/qemu-composite-dev03-20261022/summary.json)、[SHA](../evidence/qemu-composite-dev03-20261022/artifacts.sha256) |
| dev04 | FAIL，保留相同内容/5s而增加两PCAP、ARP/routes/carrier及失败snapshot。两端carrier1，USBFS OUT增长；PCAP证实ARP42B被拼在IPv6帧尾，根因指向QEMU模型/xHCI包粒度不匹配。 | [summary](../evidence/qemu-composite-dev04-20261022/summary.json)、[SHA](../evidence/qemu-composite-dev04-20261022/artifacts.sha256) |
| dev05 | 改为UHCI后FAIL于短IRQ的旧actual0假设；前七项raw通过，下一轮只增加错误字段上下文，不直接放宽。 | [summary](../evidence/qemu-composite-dev05-20261022/summary.json)、[SHA](../evidence/qemu-composite-dev05-20261022/artifacts.sha256) |
| dev06 | FAIL明确显示seq43 `-32/actual1/data_length1`，仍保持旧断言；等待独立kernel观察。 | [summary](../evidence/qemu-composite-dev06-20261022/summary.json)、[SHA](../evidence/qemu-composite-dev06-20261022/artifacts.sha256) |
| dev07 | 故意保留actual0断言再准确FAIL；USBMON同tag独立确认`S len1`、`C -32 len1 =00`。由此收敛模型特定预期，不宣称设备真实传1B。 | [summary](../evidence/qemu-composite-dev07-20261022/summary.json)、[分析](../evidence/qemu-composite-dev07-20261022/usbmon-analysis.json)、[SHA](../evidence/qemu-composite-dev07-20261022/artifacts.sha256) |
| dev08 | PASS，两guest exit0，19项probe；RustUSB和官方直接导入各一周期、精确UDP、所有权拒绝不影响I/O、两次原配置/alt/双驱动恢复及末资源0均通过。 | [summary](../evidence/qemu-composite-dev08-20261022/summary.json)、[SHA](../evidence/qemu-composite-dev08-20261022/artifacts.sha256) |

静态六项命令与结果、BusyBox准确二进制SHA及`printf`/`tr`/`od`复现保存在[静态目录](../evidence/qemu-composite-static-20261022/artifacts.sha256)。每次修订后另重跑Python编译、shell语法、rustfmt及standalone rustc `-D warnings`。全部原始日志、FAIL/PASS、PCAP、脚本快照逐字节归档，独立解析产物另标，不重写原始bytes。

### xHCI模型诊断与控制器选择

dev04中三个client-origin捕获的Ethernet frame长132/218/202B，分别可完整拆成`IPv6 90 + ARP42`、`IPv6 90 + IPv6 86 + ARP42`及`IPv6 90 + IPv6 70 + ARP42`。ARP源198.18.0.2、目标198.18.0.1，未形成独立Ethernet frame；server peer RX0、client ARP incomplete。与QEMU6.2 ECM OUT仅在USBPacket长度小于64时flush的实现一致，不能把carrier、路由或USBFS成功提交当作实际Ethernet数据通过。可重放的[独立解析脚本](../evidence/qemu-composite-dev04-20261022/packet-analysis.py)及[JSON](../evidence/qemu-composite-dev04-20261022/packet-analysis.json)保留精确PCAP SHA和每段边界。

据此仅新harness切换Full-Speed UHCI，产品原始长度/flags路径、38B请求/39B响应、5秒接收上限和所有USB/IP断言均保持。该环境选择不证明QEMU xHCI已修复，也不缩减长期xHCI/真实硬件兼容目标。

### UHCI kernel-reported IRQ长度及通过范围

QEMU短IRQ路径在复制数据前STALL；UHCI错误路径保留TD的原实际长度编码，Linux解码并先累加再返回EPIPE，造成当前组合上报1。USBMON独立确认的opaque byte与wire完全一致，adapter没有清零或将其改成成功。dev08实际buffer为00，只是本次观察；[独立离线脚本](../evidence/qemu-composite-dev08-20261022/independent-analysis.py)及[JSON](../evidence/qemu-composite-dev08-20261022/independent-analysis.json)同时交叉检查同tag IRQ和两份data PCAP，不把该字节称为真实设备数据。

dev08准确起始为dirty `d361ea3c99bdafc8c8dd0b10304258a98c537a47`，19条probe中host-state UNLINK实际观察为queued取消赢、配置仍1；不可取消ioctl完成赢仍需专门的确定性回归，不能外推本轮已跑到该竞态。两份data PCAP各独立含四个准确UDP frame：cycle1请求38B/响应39B，cycle2相同长度但各自nonce不同，对应Ethernet长度80/81B，没有以小于MPS内容绕过模型。

末snapshot为available/cleanup_verified，配置1、两个cdc_ether；sysfs独立确认alt0/1和同generation。pending/queued/inflight/buffer均0，累计真实usbfs_reaped87。生产默认stderr/JSON未发现固定nonce前缀。本轮仍未验证成功CLEAR_HALT、真实设备partial、任意混合Composite、原无driver且非零alt的完整恢复、ISO实际传输、长期会话或性能。

root另以[独立PCAP校验器](../evidence/verify-composite-captures.py)核对两份捕获的完整Ethernet/IP/UDP长度、校验和及两周期四组精确内容：[dev08验证通过](../evidence/composite-capture-dev08-verification-20261022.json)，[dev04负向验证拒绝](../evidence/composite-capture-negative-verification-20261022.json)。当前产品四平台软件CI见[发布核对](../evidence/publication-composite-20261022.json)。

## 最终clean验证与原physical回归

root在clean提交`22d172d84779d92fe51f5a1c6f9c99e5ff747049`依次执行ECM、原physical normal、原physical hot；各summary均记录`working_tree_dirty=false`。三项均实际执行默认`cargo build --workspace --release --locked --all-features`，没有skip-build；运行中源码和二进制变化会使runner拒绝PASS。三项的guest实际CLI/helper副本与构建产物逐项相等，三项产物也都等于前述`c033d393…`/`5d40d8e3…`。完整commit、构建命令、各harness指纹、二进制SHA与全部产物见[最终聚合索引](../evidence/composite-final-20261022.json)及[原始顺序记录](../evidence/composite-final-sequence-20261022.json)。

| 最终场景 | 实际结果与范围 | 原始结果与全部SHA |
| --- | --- | --- |
| ECM | PASS，双guest exit0，19条probe含15条lifecycle/汇总、两次busy和两次available；RustUSB managed与官方usbip unmanaged各一个周期，两个cdc_ether接口/配置1/alt0,1、精确UDP、所有权拒绝后的I/O及原状态恢复均通过。 | [summary](../evidence/qemu-composite-final-20261022/summary.json)、[SHA](../evidence/qemu-composite-final-20261022/artifacts.sha256) |
| 原physical normal | PASS，双guest exit0、15条probe，两周期真实来宾HID按下/释放及FTDI每周期35B双向内容；host收到70B且精确相等，两设备原驱动及末资源恢复。沿用旧harness，未修改脚本。 | [summary](../evidence/qemu-physical-normal-composite-regression-final-20261022/summary.json)、[SHA](../evidence/qemu-physical-normal-composite-regression-final-20261022/artifacts.sha256) |
| 原physical hot | PASS，双guest exit0、15条probe；正常两周期之外，QMP拔HID后FTDI同receipt继续精确I/O，重新插入的新代际不自动授权；host收到140B且精确相等，HID为Gone、FTDI恢复原驱动、末资源0。沿用旧harness，未修改脚本。 | [summary](../evidence/qemu-physical-hot-composite-regression-final-20261022/summary.json)、[SHA](../evidence/qemu-physical-hot-composite-regression-final-20261022/artifacts.sha256) |

最终ECM两份PCAP各1710B，各自包含两周期的38B请求/39B响应；其Ethernet帧长仍为80/81B。将dev08的[离线解析脚本](../evidence/qemu-composite-final-20261022/independent-analysis.py)原字节复制到最终目录并重新执行，生成[本次独立分析](../evidence/qemu-composite-final-20261022/independent-analysis.json)：四个UDP报文的顺序、端点及内容分别精确匹配；本次短IRQ同tag USBMON S/C的`-32/actual1`和opaque buffer与wire逐字节一致。该结果仍只证明kernel-reported长度/字节保留，不证明模型实际传输1B。

root另用前述独立PCAP校验器重新检查本次两份捕获的完整Ethernet/IP/UDP边界、校验和及精确内容，[最终验证JSON](../evidence/composite-capture-final-verification-20261022.json)为PASS，不以summary中的marker替代实际报文字节。第二独立只读复核的[78项检查](../evidence/composite-final-independent-review-20261022.json)也全部PASS；复核不运行Cargo、QEMU或设备操作。

本次host-state UNLINK实际为`cancel_won`且配置读回1；不可取消ioctl完成赢以及active ioctl期间EOF的确定性窗口仍待fixture覆盖。最终ECM真实`usbfs_reaped=88`，pending/queued/inflight/buffer均0，available/cleanup_verified、原配置1、alt0/1及两个原cdc_ether驱动恢复。三项最终snapshot的待处理资源均0；热拔HID不再存在，故不声称恢复不存在设备的驱动。

23份原始运行产物及runner日志逐字节等于`/tmp`原件；仅ECM补充冻结commit的准确三脚本Git blob快照和离线分析。每目录附SHA清单，最终索引共覆盖33份产物（含原始sequence及生成的清单/分析）。原7次FAIL和dirty dev08全部保持，不覆盖或改写为最终证据。真实硬件G1/G2、任意混合Composite、原无driver的非零alt恢复、成功CLEAR_HALT、真实partial-error、ISO实际传输及长期性能仍未验证。

## 固定来源

- [QEMU v6.2.0 USB-net](https://github.com/qemu/qemu/blob/v6.2.0/hw/usb/dev-network.c)：配置/端点、ECM短包帧边界、真实Control/Interrupt错误及RNDIS差异。
- [QEMU v6.2.0描述符控制](https://github.com/qemu/qemu/blob/v6.2.0/hw/usb/desc.c)：配置值与SET/GET_INTERFACE。
- [QEMU v6.2.0 socket backend](https://github.com/qemu/qemu/blob/v6.2.0/net/socket.c)：接收已连接SOCK_STREAM FD，独立endpoint使用无跨进程限制。
- [QEMU v6.2.0 UHCI](https://github.com/qemu/qemu/blob/v6.2.0/hw/usb/hcd-uhci.c)及[Linux v6.8 UHCI长度解码](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/host/uhci-hcd.h)、[完成处理](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/host/uhci-q.c)：本次kernel-reported partial的模型/controller来源，另以USBMON实测约束。
- [Linux v6.8 CDC Ethernet](https://github.com/torvalds/linux/blob/v6.8/drivers/net/usb/cdc_ether.c)及[usbnet](https://github.com/torvalds/linux/blob/v6.8/drivers/net/usb/usbnet.c)：控制probe会认领数据接口，选择带Bulk IN/OUT的alternate。
- [Linux v6.8配置选择](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/generic.c)：RNDIS的编译配置判断与后续标准CDC配置选择，不能用是否已加载rndis_host推断所选配置。

以上Linux实现仅参考行为与协议，不复制GPL源码进入MIT核心。实际发行版kernel/QEMU补丁以运行summary与描述符为准。
