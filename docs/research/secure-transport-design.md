# Phase3 实际 USB 数据通道安全设计

2026-10-23，起点clean `a7406d7`；状态 **proposed / 实施研究**，尚未实现或验收TLS。上一goal turn为实际进展：clean4565f7a三项恢复边界PASS/6guest及四平台268次软件验证已交付。完整原始目标、G1/G2/Phase2未完成项保留；本设计推进后续必须提供的真实USB/IP认证与加密，不把硬件或通用恢复标通过。

沿用现有docs/research、固定源码审计及项目skill，不为技术选型另建无关逐组件skills。正式架构选择保持proposed；本轮先核证关键内核能力与实际依赖，再形成可实施接口，不以通用TLS echo或管理API的安全代替USB数据通路。

## 必须成立的最终路径

- 客户端和服务端分别验证证书链、有效期、用途与配置的服务身份；设备访问由经验证的principal与稳定设备key决定。每次DEVLIST与IMPORT都认证，IMPORT成功后的全部URB仍在同一认证TLS连接，不切回公网明文。
- 本地普通用户不能抢本地代理连接或提交自报remote principal扩大root权限。helper信任的是受保护UDS上真实OS服务身份；TLS认证/设备ACL由受信任专用非root服务账户执行，交互CLI不持该服务的最大设备池/凭据权限。
- Native和Rust两个后端都保留：Rust engine直接接受经认证stream；Native仍由Linux usbip_host/vhci_hcd传URB，研究匿名socketpair与固定sysfs FD交付，避免公开本地明文TCP listener。已有或外部usbipd旁路须在部署和启动中明确拒绝/隔离，IPv4及IPv6均实际验证。
- Root helper不解析完整URB或成为整个TLS应用；限制固定动作/路径、类型化metadata、OS角色、FD数量/类型/所有权和实际kernel generation。普通服务的认证信息不能当作未经约束的root RPC字段。
- 并发/握手/读写/缓冲有上限，TLS写帧在同一截止内write_all+flush，取消和信号沿现有真实任务回收契约。完整Phase3还包括会话状态机、错误/审计/断线处理、凭据撤销/轮换与故障矩阵，不能只实现握手。

## 已核事实与候选

现Rust engine的handshake/URB actor已使用generic AsyncRead/AsyncWrite；TcpStream依赖集中在listener/connection。现write_frame只有write_all，TLS接入必须补flush。[tokio-rustls0.26.6文档](https://docs.rs/tokio-rustls/0.26.6/tokio_rustls/)明确其写入有内部缓冲。相关源码为`crates/rustusb-rust-backend/src/server.rs`，当前Native attach则先plain DEVLIST再运行官方attach工具并核原工具record，不能把localhost伪造成真正remote身份。

固定Linux v6.8 `e8f897f4afef0031fe618a8e94127a0934896aba`的VHCI入口只要求SOCK_STREAM，RX/TX/关闭走socket ops；服务端stub初核亦如此。匿名AF_UNIX pair是优先机制候选，尚无运行证明。socketpair的SO_PEERCRED反映创建时身份，不是之后SCM_RIGHTS接收者；认证必须依赖真正的控制UDS及角色。

Native server的`usbip_sockfd`候选应先open原kernfs属性节点、捕获夹读的sysfs/attr身份，再写root自己持有的socket FD。open不是永久active或kobject生存保证；删除后write应失败而不解析新同名BusID，仍须完整后核。现Native helper的旧exports map只存Device，不能冒充strong sysfs inode receipt。新secure-export需要独立强receipt和生命周期契约。

### 控制面与端点类型（待实验后细化）

```rust
// Proposed concepts; these are not current public APIs.
struct VerifiedPrincipal { configured_id: PrincipalId, leaf_sha256: [u8; 32] }
struct AuthorizedDevices { stable_keys: Vec<DeviceKey> }
enum DeviceKey { PhysicalFingerprint(String), VirtualProfile(ProfileId), NativeExport(String) }
struct LogicalRemote { server_id: ServerId, device_key: DeviceKey }
struct LocalKernelChannel { /* owned anonymous stream FD, no listening address */ }
```

principal由TLS证书验证及root管理映射生成，不能由网络或CLI直接构造。ROOT配置的专用broker UID与普通CLI权限分离；客户端逻辑remote receipt与本地kernel channel分开，不能虚构官方endpoint record。Native kernel attach的早期URB意味着relay须在等待完整枚举前可工作；FD handoff/ACK、实际sysfs write、枚举确认与失败清理必须是一条拥有任务/FD的事务，不能先等枚举再交付唯一relay端造成死锁。

服务端Native候选为helper授权精确export→原attr FD及匿名pair→broker认证管理帧/ACL→kernelstub raw stream；若该能力实际不成立，备用方案是受控私有netns中的usbipd后端，外侧TLS socket activation进入专用broker。公共loopback端口或端口保密都不是本地UID认证。任何备用方案必须提供实际双栈不可绕过证据后才启用。

## 当前依赖研究

官方registry在2026-10-23返回稳定未yank的rustls0.23.46（MSRV1.71）、tokio-rustls0.26.6（MSRV1.71）、pki-types1.15.1、pemfile2.2.0及测试证书候选rcgen0.14.10（MSRV1.88）；都需以实际Cargo.lock/许可证/已用API再核后引入，此处为引入前的版本研究；后续候选依赖与实现状态见末节。[Rustls版本文档](https://docs.rs/rustls/0.23.46/rustls/)说明TLS1.2/1.3和明确crypto provider选择，不能把TLS TCP底层FD直接交给只认识明文USB/IP的内核。

候选使用TLS1.3、私有CA、强制client证书、明确ALPN、禁止0RTT与未定义的恢复ticket语义；provider/证书principal pin、撤销/轮换流程、资源成本与角色配置仍需源核及实际测试。不创建自研密码算法，不使用dangerous跳过验证接口，不记录私钥/原始USB内容。

## 第一项必须执行的机制实验

在**一个可销毁QEMU guest**内加载usbip_host及vhci_hcd，普通UID通过现helper导出固定合成HID/FTDI。独立guest-only Rust probe只在root和专属启动标记下运行，对每个已导出原代际预open usbip_sockfd，创建匿名UnixStream::pair，把一端写入stub，另一端写入固定VHCI控制器的空port。实际HID key-down/up与FTDI双向数据须经kernelstub→匿名pair→VHCI路径，不运行usbipd、TCP代理或TLS。

该实验只证明内核两端是否支持该socket family及实际数据，不能证明两机网络、认证、加密、产品FD RPC或Phase3已完成。它不是新的正式用户导入API。仅在机制确认后实现typed helper/broker路径。

probe在任何USB/sysfs设备操作前检查guest root/两固定启动标记，限定两模拟VID/PID/bus/devnum、原配置/IF/alt及root节点/原sysfs代际；拒绝已有占用。选完整status中两个初始NULL的HS port，持有真实FD完成实际write，观察本地新代际/VIDPID和远端devid，不能写假的官方record。抓取两端/proc FD与sysfs原件和精确源码/ELF/内核。

单guest双kernel端都持FD reference，probe进程退出/Drop本身不保证连接终止；正常实验须显式shutdown(SHUT_RDWR)并等异步USBIP down/本地代际消失，随后由原helper普通unexport恢复完整原driver。不能把一次sysfs负数write返回当清理已完成。未来两机broker角色中每个pair只有一端交kernel，helper必须在成功handoff后释放自己relay副本，broker死亡才能产生相应EOF；这些是后续独立生命周期验收。

## 后续实现与最终验收

1. 根据机制真实裁决固定FD/receipt/handoff/恢复接口，设计root持久会话和shared-port所有权，保留未知状态拒绝接管。
2. 添加实际非空transport/security实现及必需依赖，复用Rust engine genericstream和Native kernel通道，建立专用broker与受保护CLI接口。每个实现单元先补具体设计再写代码，不把候选类型当已实现接口。
3. QEMU外链与控制链分开，全外链PCAP对应DEVLIST、IMPORT和实际HID/serial URB；虚拟/USBFS/Native三后端的IPv4与真实IPv6共六覆盖单元。bad/expired/revoked身份、设备ACL、重复导入、本地UID抢入口、直连旁路、EOF/信号/背压与第三轮复用逐项验收。
4. 按源码/版本/feature记录软件fmt/Clippy/test/release/rustdoc、真实QEMU和CI；S3/G3与完整会话/审计目标逐项证明前保持未通过。真实硬件、ISO、跨cfg/alt、Windows及后续TUI/GUI/安装/性能保持原目标，不在本机制实验中缩小或标完成。

## 并行的TLS基础模块实施契约（proposed candidate）

内核实验期间可独立实施非空`crates/rustusb-transport`基础模块：真实rustls/tokio-rustls双向TLS、不可伪造的认证结果、受限principal/device-key策略和握手资源上限。该模块本身没有设备、监听器、root RPC或CLI，不代表已建成USB/IP安全路径；后续必须实际接入两个backend及helper/broker并完成外链验收。

候选固定rustls=0.23.46、tokio-rustls=0.26.6，显式关闭默认features，选择rustls已内置并文档支持的ring provider与std，只构建TLS1.3；使用显式provider，不依赖全局默认安装。目标是Linux x86_64/aarch64的现有workspace接口，FIPS不是本需求，不能冒称具备；aws-lc默认provider作为仍可重审的替代。证书测试只用rcgen=0.14.10/ring在内存生成，生产库不包含测试私钥、PEM解析或自研密码算法。正式采用仍待编译/许可证及各平台CI证据。

基础契约：

- CA roots/证书链/DER私钥由调用者按可信配置加载；模块拒绝空/损坏、过大输入和不匹配key，不自行选择任意路径或放宽文件权限。整个文件信任边界将在broker接入中实现，不能因低层API接受内存DER就宣称文件权限已验收。
- 标准证书链、时间、EKU及server-name验证成功之后，再以完整叶DER SHA256映射root管理principal；pin不能替代PKI验证。严格拒绝未知/重复pin、重复principal、越界列表及不合法稳定device key。虚拟profile使用明确命名空间，物理/Native使用现有精确指纹，不能由临时BusID授权。
- TLS1.3、强制client cert、固定ALPN `rustusb-usbip/1`；禁止0RTT、client resumption与server tickets，不能调用dangerous证书跳过验证。低层client握手完成只表示验证了server，不表示server已批准设备访问；拒绝可能表现为之后的EOF/alert，实际管理回复才是授权结果。
- 同一server实例最多16个在途/已认证连接，先取得permit再创建握手任务，返回stream整个生命周期保留permit，Drop才释放；不可暴露裸inner stream让caller提前丢permit。握手默认5秒、TLS写缓冲显式64KiB；不把此数字当成整个TLS进程RSS上限。原生Rustls握手重组源码上限65535，仍需实际资源测试。
- 认证stream实现标准AsyncRead/AsyncWrite，字段和认证构造私有，提供只读身份/允许device集合；engine以后在DEVLIST及claim前使用相同predicate。它不是“任意caller附上自报principal”的通用转发授权。
- 单元测试跑真实双向TLS与双向payload/flush，核协议/ALPN/主体映射/ACL，badCA、badDNS、missing-client-cert、expired、unmapped/revoked pin、重复/畸形策略、超时、连接permit直到Drop、公开Debug/错误不泄露私钥。构造I/O测试不替代TCP、USB或外链PCAP。


[研究原件索引](../evidence/secure-transport-design-20261023/manifest.json)保存Native FD/原属性语义、principal/ACL和完整QEMU候选方案，以及官方registry和精确crate checksum资料。Linux源正文只在临时研究目录，仓库保留精确固定源码引用/哈希，未复制GPL实现进入MIT代码。
