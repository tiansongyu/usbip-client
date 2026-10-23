# Native 服务端匿名 socket pair 附录（研究候选）

2026-10-23。独立附录，**不覆盖** `/tmp/rustusb-phase3-native-fd-design-20261023.md`。固定 Linux v6.8 / `e8f897f4afef0031fe618a8e94127a0934896aba`，未接受架构、未编译或运行 QEMU/USB。只读源码与官方固定源；无 GPL 实现进入 MIT 仓库。

## 结论

**服务端 kernel stub 同样存在匿名 AF_UNIX SOCK_STREAM pair 的源码兼容候选。** `usbip_sockfd_store` 只核 `SOCK_STREAM`，RX/TX 与客户端所审相同通用 socket operations 链。由受控 broker 处理有界 DEVLIST/IMPORT 管理协议，helper 将自有 pair 内核端写入已授权的 `usbip_sockfd` 属性，之后 broker 在 relay 端与 mTLS 流之间转发原始 URB，可以在设计上不运行 usbipd，也不创建任何明文 backend TCP listener。这仍需实际来宾验证，不能由入口放行推导已完成安全功能。

这不是重写 kernel USB/IP、nusb adapter 或完整 usbipd：保留 `usbip_host` 的实际 URB/USB 引擎，仅实现已有管理帧的授权、受限导出对象元信息及 socket handoff。Native 和 USBFS 不同时操作同一设备。

## 固定源码事实

| 路径／函数 | 事实 |
| --- | --- |
| [stub_dev.c:42–143](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/stub_dev.c#L42) `usbip_sockfd_store` | dev_get_drvdata 缺失时 ENODEV。非 -1 分支在 sysfs_lock/ud.lock 下检查 AVAILABLE；74 行从实际写入进程查 socket FD，80 行只检查 STREAM，无 family/protocol/显式 connected 限制。创建 RX/TX，保存 socket 引用并置 USED 后返回。失败常折叠为 EINVAL，不能凭 errno 编造“已证明 busy”或原错误细类。 |
| [stub_rx.c:279–294,629–687](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/stub_rx.c#L629) | 641 行 `usbip_recv` 使用同一 kvec+sock_recvmsg(MSG_WAITALL) 通用链。验证 devid 与 USED；此处只理解 SUBMIT/UNLINK，**不能把 OP_REQ_IMPORT 或管理回复写进 pair**。错误/短 header 添加连接错误事件。 |
| [stub_tx.c:316,391](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/stub_tx.c#L316) | RET_SUBMIT/RET_UNLINK 使用 kernel_sendmsg；所审链没有 TCP-only 地址或 sockopt 操作。Unix stream generic ops 的依据沿用原报告固定 net/socket.c、af_unix.c。 |
| [stub_dev.c:153–241](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/stub_dev.c#L153) | 关闭顺序为 shutdown(SHUT_RDWR)、停 RX/TX、sockfd_put、清理真实 URB/unlink；reset 成功才设 AVAILABLE，失败为 ERROR。关闭属性 FD本身不执行上述清理，关闭用户 socket 副本也不等于释放 kernel 持有的引用。 |
| [usbip_event.c:73–86](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/usbip_event.c#L73)、usbip_common.h:241–245 | `-1` 仅添加 SDEV_EVENT_DOWN；异步事件先 shutdown，再 reset。连接 EOF/错误同样进入异步事件。**写 -1 返回不等于线程/URB/设备已清理。** |
| [stub_dev.c:257–298,429–536](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/stub_dev.c#L257) | sdev 持 USB device 引用；devid 在 stub 分配时取 busnum/devnum，连接中不跟随 devnum 改变。driver 的 dev_groups 包含 usbip_sockfd；disconnect 清 drvdata 并排空连接。新设计应对地址/导出 epoch变化拒绝，不盲目拼接新的 devid。 |

## 预打开属性固定的是什么

**应称固定原 kernfs node，不称永久持有 old kobject active。**

- 固定 [kernfs/file.c:590–704](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/fs/kernfs/file.c#L590)：open 保存 `of->kn`，返回前释放临时 active。
- [kernfs/file.c:293–339](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/fs/kernfs/file.c#L293)：每次 write 对原 `of->kn` 重新 get_active，节点失活就 ENODEV，尚未进入 store。
- [sysfs/file.c:127–136](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/fs/sysfs/file.c#L127)：通过该原节点的 parent priv 获取 kobject并调用 store；不按当前 Bus ID重新查设备。
- [kernfs/dir.c:1438–1477](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/fs/kernfs/dir.c#L1438) 先令节点失活，再 drain 已开始的回调；[drivers/base/dd.c:561–569,1241–1277](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/base/dd.c#L561) 在 driver remove/disconnect 前删除 dev_groups。

所以：旧属性删除后即使相同路径/Bus ID生成新设备或重新绑定新 attribute，**旧 FD 不会转向新节点**，后续 write 会失败；不得遇 ENODEV 就按旧 Bus ID重开。已在删除前取得 active、进入 store 的调用则可先完成，remove 等它返回。这个机制不保持 USB 设备存活、不阻止 hotplug/unexport、不排除所有旧对象副作用，后核仍必要；硬阻塞 store 也可能延后移除。

## 推荐的窄 handoff（待接受）

1. helper 仅接受它明确拥有的 Native export；按当前 policy/principal/device ACL重新核准，并捕获真实 sysfs directory dev/inode、bus/address、绑定与导出 epoch。**当前 `ManagedState.exports` 只存 Device，`same_device_generation`（daemon/linux.rs:696）仅比较 id/busid/address/topology/VIDPID，没有 dev/inode，不能直接当强 receipt。** 新 secure-export 应在真实授权/导出时建立强 receipt，不把未知或旧弱记录自动升级为安全所有权。
2. 在匹配该 receipt 的原目录下打开固定 `usbip_sockfd` 属性，夹读目录/属性 FD身份、当前usbip-host绑定、完整安全元信息及 AVAILABLE。保留原属性 FD，后续不按 caller路径或 Bus ID重开。开 FD不是预留设备；并发占用最终仍由 store 的 AVAILABLE检查裁决。
3. helper 自建匿名 data pair，内核端和属性 FD始终留在实际写入任务；relay端经受保护 UDS/SO_PEERCRED角色+单FD SCM_RIGHTS交受信 broker，绑定一次性请求/lease与完整导出 receipt。该 FD本身不证明证书；mTLS认证结果与服务角色的 TCB必须明确。
4. broker 在每条 TLS管理连接上过滤 DEVLIST、授权 IMPORT；由 helper 的当前强 receipt返回有界 canonical wire metadata。IMPORT成功回复必须等 helper 实际 handoff成功，随后才进入 URB relay。若回复写失败/客户端取消，关闭本次 pair并等待它自己的 kernel channel清理；不能遗留 USED。
5. 在长时间认证后再次核准同代际、绑定、状态和授权，以实际 OwnedFd整数对原属性 FD作一次有界小写入，要求完整字节数；short/error不重发。实际 blocking任务持有 FD与变更所有权直到 write及后核；外层 timeout/取消不代表系统调用结束。保留真实写错误和不确定状态。
6. 持有本次 channel的真实 socket所有权，关闭它来中止本次连接；**不要仅凭当前 status=USED向属性写 -1**，因为它会关闭当时的任意 USED连接，可能属于并发 legacy/root owner。只有确证本session仍拥有该连接的协议才可使用 -1。异步清理要求同代际 AVAILABLE或明确Gone及真实对端断开；不把粗粒度状态当持久连接ID。

有界 metadata至少需要 canonical path/busid、busnum/devnum/devid、Linux speed、VID/PID、**bcdDevice而非bcdUSB**、当前configuration、configuration数及完整 active interface class tuples。现 core Device不含全部字段，不能凭填0或复制usb_version拼回复；需固定sysfs/descriptor来源、长度/数量上限及前后代际一致读。沿用现 MAX_DEVICES256、MAX_INTERFACES32及256/32字节wire字段限额，超限拒绝而非截断。DEVLIST和IMPORT必须绑定相同授权device/export epoch；候选指纹不是硬件密码学身份。

这个候选消除 generic usbipd 的“代理按BusID授权→usbipd重新按BusID导入”重解析窗口，靠的是**原属性 FD与新强 export receipt一起使用**。不应仅凭旧Device比较或一个文件路径许诺同等保证。

## 部署与实测边界

不运行本架构的 usbipd/plain backend listener并不自动隔离机器上另一个 legacy usbipd：其可访问同一全局 usbip_host导出。安全模式需管理员停止/禁用所有可达明文实例，或建立可证明的运行隔离与IPv4/IPv6防绕过；外部root仍属可信管理员，不能宣称防恶意root重新开放。只扫描一次端口或仅写trusted_network=true不能当强隔离。

下一实验应分别证明：server stub和client VHCI匿名pair真实HID/FTDI I/O；保存原attr FD后unbind/rebind或重插，旧FD真实ENODEV且新节点无副作用；broker EOF/IMPORT回复失败后RX/TX/URB清理与再次可用；外部占用不被 -1终止。先核family/attr机制再做全TLS产品验收，任何机制PASS都不是完整G3或硬件通过。没有执行这些实验。

## 独立原件

- stub5份官方固定commit镜像：`/tmp/rustusb-phase3-stub-fd-source-20261023/googlesource-provenance.json`（HTTP200原base64、解码原字节与计算Gitblob/SHA）。GitHub429与kernel.org访问失败另存，不混作成功源码。
- sysfs代际独立审查：`/tmp/rustusb-sysfs-generation-audit-20261023.json`，SHA256 `8fcb42f283255dbab254027db9d2b7cd7c5b096ac2d5b170c9171cf8f7dc0173`；其中5份固定源含kernfs/sysfs/driver-core，精确行号、Gitblob与SHA完整保存。
- generic socket/AF_UNIX8份原件沿用 `/tmp/rustusb-vhci-family-source-20261023/provenance.json`。原client报告保持原字节。

| stub固定文件 | 计算Gitblob | SHA256 |
| --- | --- | --- |
| `drivers/usb/usbip/stub_dev.c` | `ce625b1ce9a5125c43655acc46c160d227d37686` | `646534a85036d9cf98a19d976dd8b65e95405f4d749a5d2688cc3ce8a456dcb3` |
| `drivers/usb/usbip/stub_rx.c` | `fc01b31bbb875d1b200c68ba32f219168035a532` | `7bc5dc2e4fbe40f36488720ecc4890820b625ff80011825dfcbc5b409fa63e9f` |
| `drivers/usb/usbip/stub_tx.c` | `b1c2f6781cb33046ae39a8692aee0f453d93fdb3` | `509e31fdf17788208a6500f2bded31b489f059234e1f18c3c19f5050a24ea5d6` |
| `drivers/usb/usbip/usbip_event.c` | `26513540bcdbdd4ce19bf7b6e1f0d954e46f0deb` | `903ac8ad6dfc59ff6d63bfc3b553bd7c29813a67ece0a1941a1b8796540f26b6` |
| `drivers/usb/usbip/stub.h` | `d11270560c2477870374bfc8d694de9a294f6d31` | `48f52b13f01942172e7c8ee1fd58f09b416d90ff7b99d95eb9c05e6bfb606b8d` |
