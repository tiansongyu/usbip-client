# Phase 3 Native FD 接入研究：无公开明文入口的 VHCI 桥接

2026-10-23。状态：**源码研究与候选建议，尚未接受架构，未编译、未运行 QEMU 或 USB**。重读 AGENTS、核心 skill、现有 Native/helper、架构及 `/tmp/rustusb-phase3-boundary-notes-20261023.md`。本报告没有修改仓库或复制 GPL 实现；只记录接口事实与独立设计建议。

## 1. 核心结论

固定 Linux **v6.8 / `e8f897f4afef0031fe618a8e94127a0934896aba`** 的 VHCI attach 入口只要求 FD 对应 `SOCK_STREAM`，没有要求 AF_INET、AF_INET6 或 IPPROTO_TCP。收发及 shutdown 链经通用 socket operations，所审 AF_UNIX stream 路径具备 connected、MSG_WAITALL 和 SHUT_RDWR 行为。因此：

1. **优先实验匿名 AF_UNIX SOCK_STREAM pair**：helper 创建两个端点，保留内核端，仅经受保护 UDS 把 relay 端交给已授权的普通 TLS 服务。完全不创建客户端明文监听端口。
2. 只有目标 Ubuntu 内核/LSM/实际 USB 运行发现具体不兼容时，才考虑 **helper 自建、匹配两端后关闭 listener 的私有 TCP pair**。不能根据变量名 `tcp_socket` 或注释把 TCP 判为必需。
3. 以上是源码兼容候选，不是 AF_UNIX 已运行通过。接入认证、设备授权及目标内核实验仍是硬门槛。

**TLS 的底层网络 FD 不能直接交给 VHCI。** 普通用户态 TLS 的会话状态和解密缓冲不属于裸 TCP FD；VHCI 需要明文 USB/IP。需要独立 pair 连接 kernel 与 TLS relay，relay 执行双向 TLS 解密/加密。这里没有选择或宣称 kTLS 支持。

## 2. 固定内核的真实约束

下列链接全部固定到同一 v6.8 commit，避免把 `/tmp/rustusb-upstream-linux/` 主目录的另一个 commit 当作 v6.8。

| 源码 | 已核事实 |
| --- | --- |
| [vhci_sysfs.c:308](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/vhci_sysfs.c#L308) `attach_store` | 输入四个数：port、**写入者进程 FD 表中的 socket FD**、remote devid、speed。360 行 sockfd_lookup，366 行只检查 SOCK_STREAM，423 行保留 socket 引用；不检查 family/protocol，也不替用户态证明已连接或经过 TLS。 |
| [net/socket.c:533](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/net/socket.c#L533) `sockfd_lookup` | fget 查当前任务 FD 表，再确认 socket。JSON 中另一个进程的整数 FD 无效；必须在实际 sysfs 写入进程持有 OwnedFd。成功后内核持引用，关闭用户态副本不是 detach。 |
| [usbip_common.c:302](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/usbip_common.c#L302) `usbip_recv` | 使用 kvec/内核 iterator，实际调用 **sock_recvmsg(MSG_WAITALL)**。不要误写成此 RX 直接调用 kernel_recvmsg。 |
| [vhci_tx.c:139](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/vhci_tx.c#L139) 与 219 | SUBMIT/UNLINK 使用 kernel_sendmsg；所审 VHCI 链没有 TCP_NODELAY、inet 地址或 TCP family 操作。 |
| [net/socket.c:728](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/net/socket.c#L728)、1043、3668 | send、recv、shutdown 分派至 socket->ops，并存在 LSM 检查；INET 优化没有取消其他 socket ops 的 fallback。 |
| [af_unix.c:831](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/net/unix/af_unix.c#L831)、2642、2921 | unix_stream_ops 提供 sendmsg/recvmsg/shutdown；接收要求连接状态并处理 MSG_WAITALL，shutdown 处理 SHUT_RDWR 和唤醒 peer。这里的 TCP_ESTABLISHED 是复用状态枚举，不证明 socket 属于 TCP。所审链无 PF_KTHREAD 禁令。 |
| [af_unix.c:1663](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/net/unix/af_unix.c#L1663)、686 | socketpair 直接连接两端并在创建时初始化 peer credentials。**传 FD 后，pair 的 SO_PEERCRED 不是当前接收者身份**；不能用它代替控制 UDS 的身份验证。 |
| [vhci_hcd.c:1011](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/vhci_hcd.c#L1011) | shutdown 调 kernel_sock_shutdown(SHUT_RDWR)，停止 RX/TX 后 sockfd_put。relay 结束应触发内核断链；是否完成须观察 NULL 和旧 sysfs generation 消失。 |

`status.sockfd` 只是 attach 时写入的整数，可能随后关闭/复用；不是跨进程恒定 FD 身份或恢复授权。继续使用完整 NativeSession 的本地 USB address/generation 判定所有权。

v6.8 `valid_args`（vhci_sysfs.c:273–293）明确列出 low/full/high/wireless/super，未包含 super-plus。新路径应核实际目标的能力，不能由当前用户态 `speed <= 6` 推断此版本 kernel 接受 6。保持现有 port/devid/长度边界，不借新接口扩大未验证速度或总线范围。

## 3. 推荐的最小 helper 所有权流程（待接受）

建议由 helper **创建** pair，避免把接口变为“授权普通 UID 提供任意 socket，由 root 导入”。既有 RPC、root policy 和 Native mutation 锁是接点，不需要重写 USB 内核客户端。

1. 对受保护控制 UDS 校验 SO_PEERCRED，区分普通请求用户与受信 TLS relay 角色。按配置的逻辑 peer ID、允许设备和本次有界请求授权；不接受任意程序、路径或转发目的地。
2. helper 创建匿名、CLOEXEC 的独立 data pair，保留 K，交付 R 给指定 relay。每次请求只交一个 FD，有限 pending 数量和期限，绑定控制连接、UID/PID/starttime、一次性请求 ID。未进入 kernel 前的失败只关闭 pair/远端会话。
3. 复用现有 **JSON 描述 → 单字节 ACK → SCM_RIGHTS+marker** 的分阶段思想，接收必须 CMSG_CLOEXEC、恰一个 FD、无未知 ancillary、无 CTRUNC/TRUNC。不能把控制 UDS 自身作为 data FD；不能让临时 BufReader 提前吃掉 ancillary marker，也不能复用旧 physical character-device 校验来验证 socket。
4. relay 对远端完成真正 TLS 认证并按本次 peer/device 请求建立通路。helper 或明确受信的协议组件完成有截止的 IMPORT，并精确消费其 header/device reply，不能留 BufReader read-ahead 字节在用户空间后把 FD交 kernel。DEVLIST 是另一连接，也必须经同一认证策略。
5. 取得现有 helper mutation 锁，重新核对策略/授权、原 managed port 清理、空闲端口与 expected remote devid/speed/VIDPID。通过一个单独、明确的 Native FD 接口，将 **helper 实际持有的 K** 写入固定受控 VHCI attach 属性。OwnedFd 与锁由实际执行 blocking 操作的任务持有，不因外层等待取消而释放/复用 FD。
6. 内核已取得引用后，完成唯一新增端口、remote devid、USB VIDPID、local address 和 generation 复核；再认领 session。用户态 K 副本可以关闭，但这不是释放 kernel 引用或完成 cleanup。只对刚取得完整 receipt 的连接做失败补偿；不确定状态不认领、不猜测 detach 其他端口。
7. relay EOF/取消/错误必须关闭对应数据通道并驱动原清理路径，最终仍看 Native NULL+旧 generation gone。若控制连接死亡但数据 FD 仍存活，不能假定数据通道自动关闭；需明确拥有的 session supervisor/关闭策略。保留旧“helper 重启后未知端口不自动接管”的边界。

这不是最终 RPC 命名或已批准 API。可采用有界 prepare/commit 两阶段，或严格单连接状态机，但不能在等待 TLS 时无限持有全局变更锁。建议 Native 新入口消费 OwnedFd 与已验证的 remote metadata；helper 是授权者，Native 仍只执行固定内核操作。

**信任边界必须显式接受**：AF_UNIX/TCP 的 FD 本身不能证明远端证书。若 TLS 服务是普通用户态组件，helper 要么把受限服务角色及其认证结果纳入 TCB，要么自身独立验证所需认证证据；任意 allowed UID 的 JSON `authenticated=true` 不够。不能让普通本地用户借一个 loopback 端口使用他人客户端证书权限。原现有 shared allowed-UID 策略不是新的 per-principal 远端设备 ACL。

## 4. TCP pair 仅作为备用

如实际目标拒绝 AF_UNIX，使用 helper 自己创建 TCP listener、主动连接端 C 和接受端 A；不让 relay 连一个公开的“临时导入端口”。必须同时满足：

- listener 只绑定 loopback、端口0，不开启复用选项；helper 保持 C 存活，从实际 socket 取得两个地址。
- accept 不能信首个连接：A.peer == C.local 且 A.local == C.peer，并检查 family/stream/TCP、非 listener、已连接与错误状态。陌生连接立即关闭，accept 次数和整段截止有界；耗尽则整个导入失败，尚未 kernel attach。
- **匹配后先关闭 listener，再交付任一 data FD、再发送任何 USB/IP 字节**。本地 UID即使扫描到端口也只能造成有界失败，不能拿到将被导入的匹配连接。地址不是密码/远端认证；四元组也不是可跨关闭重用的持久身份。
- 两端都由 helper 创建并登记，随后只通过已授权的 UDS交 R；不要仅验证调用者给出的 TCP remote address。保持实际 FD、tuple、请求 ID、被授权 relay 与逻辑远端的关联。

TCP 方案增加 listener/backlog/accept 竞态和本地 DoS 面。当前没有理由仅因注释写 TCP 就接受这些额外成本。

## 5. 逻辑身份和当前代码的必要变化

当前 `Peer{host,port}` 是 plain endpoint；`rustusb_protocol::discover` 在 `lib.rs:144` 新建 plain TcpStream。`NativeBackend::attach_session`（native lib.rs:233）先 discover，再执行固定 `/usr/bin/usbip attach --remote ...`；它不能接已有 FD。官方 v6.8工具也自己 TCP connect、IMPORT，再写 VHCI，最后关闭自己的 socket 副本（usbip_attach.c:174–198）；没有已审的 `--fd` 接口。

现有 Native `record_matches`（lib.rs:426）依赖官方工具创建的 `host port busid` 文件。新 FD 路径不应伪造 localhost 记录使它通过，也不应删除现有普通 Native 的核验。建议明确区分：

- 工具路径 receipt：原 peer + 官方 endpoint record + kernel/local generation。
- 安全 FD路径 receipt：配置的逻辑 peer ID、认证服务身份/证书 principal、请求/tunnel ID、授权设备 ID及当前 remote bus/devid、实际本地 NativeSession receipt。pair tuple/fd inode/cookie仅为本地通道证据，不是逻辑 remote。

可在原 module 添加窄的 socket attach 分支与类型，不必新建空 transport/security 框架。现有 `physical.rs:211/243` 已有 safe rustix SCM_RIGHTS模式，`linux.rs:841` 有控制 UDS peercred，`connect_trusted:970` 有 protected-parent/root socket验证，`ManagedState.ports` 和 `Request::Detach`已有代际所有权规则。安全 Native 路径不应被迫依赖 optional nusb/physical-usb 才能传 socket。

现有 rustix **锁版本1.1.5** 的 safe socketpair、getsockname/getpeername、socket_type/domain/protocol/acceptconn/error/cookie 与标准 OwnedFd/UnixStream 可以表达该设计；不是新增 unsafe 的理由。具体 Cargo feature 边与 API变更仍待实施设计核对。socket flags 属于具体 endpoint/OFD，不能对共享副本随意 F_SETFL 破坏另一方行为；在提交 kernel 前停止用户态读取并明确其 blocking/timeout 状态。

此外，**client FD安全不解决 Native server 的设备代际授权**。代理先按 Bus ID授权、usbipd 随后重新解析 IMPORT 存在跨重枚举窗口；后验检查能停止后续 URB，但不能承诺零错误 claim 副作用。固定原 sysfs 属性FD的服务端handoff是需另核的候选，尚未证明；另一审查者负责该 server/ACL 设计。原始 wildcard usbipd及其他 host root启动的可达实例也必须隔离，不能只藏新 gateway 的 upstream。

## 6. 最小可验证实验与拒绝条件

建议先做一个明确命名的 **guest-only family/FD机制实验**：真实 IMPORT 后由普通 relay连接 data pair 与既有测试server，helper从自己FD表将 K写 VHCI；完成至少HID输入和FTDI双向字节、枚举receipt、关流、NULL及旧节点消失。记录两个pair端点及接收进程FD身份、内核版本/LSM、actual write errno、源/ELF来源；不以 attach write成功代替USB I/O。

该初实验可不含TLS以单独证明family机制，但只能称机制实验。下一产品验收才包含每连接证书认证、设备ACL、DEVLIST与URB全流TLS、外部抓包、无本地/远端明文绕过、坏身份/非法设备/背压与中止；不得把先前plain实验当作安全通过。

必须拒绝：非socket、非stream、未连接或listening FD；错误/多FD/截断ancillary；未知或跨UID/PID请求；过期/重复commit；错误逻辑peer/device；IMPORT metadata变更或非法速度；无可证明新端口/代际；通道/认证状态不确定。纯逻辑/本地fixture、实际guest机制、TLS产品验证、硬件分别记账。

## 7. 来源与可复核原件

内核8个文件由独立子审查者经官方GitHub固定commit取得，并核Contents API Gitblob与原字节SHA；我对关键接口重新读取。目录 `/tmp/rustusb-vhci-family-source-20261023/`，清单 `provenance.json`；窄审查 `/tmp/rustusb-vhci-family-audit-20261023.json`。另外2个官方工具文件位于 `/tmp/rustusb-phase3-native-fd-source-20261023/`；初次gh Contents GET的429失败原件保留，随后通过GitHub连接器取固定commit原文，不把失败请求算来源成功。

下面的blob均由原始字节重算；内核8份另与官方API blob相符。工具2份来自固定commit原文，未把429响应当作blob元数据成功。没有复制这些GPL代码进入MIT仓库。

| 固定源文件 | Gitblob | SHA256 |
| --- | --- | --- |
| `drivers/usb/usbip/vhci_sysfs.c` | `e2847cd3e6e363767a6dbf411f24bb628eb29566` | `8c3a861a5a474f8688779fa1127c6b31a78a330c3918e0844feb1eda82886e34` |
| `drivers/usb/usbip/vhci_hcd.c` | `82650c11e4516125777dc55e4719e398fbbb644c` | `043dc9e555ed4d4f1ecd606e43bed83a9240bb3a8ba6a19de5f437c8a4308c4e` |
| `drivers/usb/usbip/vhci_rx.c` | `7f2d1c241559face9a47f8606e0cd8f237ba2ed5` | `451c19bf5cbb67351147568458602315cfa7a044dbcbb94b0dec93dfb545d22e` |
| `drivers/usb/usbip/vhci_tx.c` | `0ae40a13a9fea89650c4200a359530dd7c954281` | `881fde895b554f5508391ca845ab1b28ed3452db763738cd4481863d2481a303` |
| `drivers/usb/usbip/usbip_common.c` | `a2b2da1255dda0c1b282171ead393dc9ac0a9c68` | `1b52782e1e302a70b1c986c1a6d984bfc5a1c7348621b2ad8a97ad38df757ee7` |
| `drivers/usb/usbip/usbip_common.h` | `282efca64a01208cf5b189e177ffec53ba9aea00` | `19d22e2f8642c2fdbdd913a7e523ca487e40f04d6cc920e35de69f9f232d5b20` |
| `net/socket.c` | `ed3df2f749bfa8ea50bfb6943e11d8cb9b2c641e` | `cc57798df63eb35dd519b315398d72f3db0ecfd37208c8b993b82433deba6385` |
| `net/unix/af_unix.c` | `0748e7ea5210e7d597acf87fc6caf1ea2156562e` | `a5ecafbd6a071f271687bfffd0b2976a0788b3cc463397afbe2f780c97db67e9` |
| `tools/usb/usbip/src/usbip_attach.c` | `531a415538f91cb5431374c034835c362dcfe707` | `58ce9bc16edd5e09b31b00775908990f3f8dcb4394191a76646d9652b502c5e9` |
| `tools/usb/usbip/libsrc/vhci_driver.c` | `8159fd98680b4d729ce69b58335a3834855babda` | `108bec4432de00d8d1234255332904509dd1a026cd3101bbc85b349cffdab98c` |

本地产品源码读取HEAD：`a7406d7080f6b3d9a73d1ea36a16d300eee0eb55`；报告时仅研究，不承诺工作树clean。当前接点SHA：

- `AGENTS.md`：`9d334b68590ddfcddb82e1d5920413e2e44d6ea17fa909fcb69e9496fcb7b21a`
- `.agents/skills/rustusb-development/SKILL.md`：`10ef52f9f77ca2da0eca60f9fee0f7b7082a6eb4bf0f658cf0e887f581ba2380`
- `docs/architecture.md`：`2f750e317e3464cfcca5aa6e687747fbc1c100b5b6d2cb9a9fcd479b58b64bc1`
- `crates/rustusb-native/src/lib.rs`：`b6465ddde887ce698a9c20232ab03df73df61505a5f4bcf9e25d2273a5735876`
- `crates/rustusb-daemon/src/linux.rs`：`373aace67ecb6f63d891005c2e20f777832d128461cb6e0bf2172e99f21b7c35`
- `crates/rustusb-daemon/src/physical.rs`：`3e53315a738289671ee2d78d676ccb245cb15e68a155477b402ee7318670e930`
- `crates/rustusb-daemon/src/lib.rs`：`ab28cb8419f5bb2a07c855688bc9d1789d33edc1364b2a06b6be84051938f599`
- `crates/rustusb-protocol/src/lib.rs`：`0f39a11540d1f940d8605680a75b5ced684005d40f02c505e6343de52057b7bb`
- `Cargo.lock`：`629eb355ed917f7d213cffcd6cd93630ec37c479095b70689d6abcd4a0918e57`

边界说明：无仓库写入、Cargo、QEMU、ptrace、宿主USB/模块/网络操作；只读源码、官方固定源GET及/tmp报告。架构最终接受与实验授权仍由主任务决定。
