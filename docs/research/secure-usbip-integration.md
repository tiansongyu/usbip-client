# 安全 USB/IP 产品接口实施契约

起点clean `056341c`，2026-10-23。上一goal turn为实际进展：TLS基础库、匿名内核通道QEMU与完整证据已交付。本文件细化[总设计](secure-transport-design.md)的产品接入；整体仍为proposed / 实施中，不表示Phase3、硬件G1/G2或原完整目标已完成。Native与Rust两后端都必须保留，不能用管理API、TLS echo或测试probe代替真实设备数据。

## 线协议与稳定设备身份

标准DEVLIST只提供临时BusID和描述符，没有本项目的稳定设备key。安全端口使用已有ALPN `rustusb-usbip/1`，TLS完成后先读取**4字节大端长度+至多4096字节严格JSON前导**，schema_version=1，拒绝未知/重复字段及非法key：

```json
{"schema_version":1,"op":"catalog"}
{"schema_version":1,"op":"import","device_key":"virtual:hid"}
```

前导后仍读取标准USB/IP DEVLIST或IMPORT请求；操作必须与前导匹配。Catalog在同一次已授权设备快照上生成两份对应数据：先返回有界目录映射帧，再返回标准DEVLIST回复，随后结束连接。映射最多16项、8192字节，包含唯一的typed DeviceKey及唯一BusID；客户端必须逐项匹配同连接DEVLIST，拒绝缺失、多余、重复或不一致映射。不能从VID/PID、第一台设备或BusID自行推导稳定指纹。

Import使用新TLS连接，发送import(key)前导和实际选中BusID的标准IMPORT请求。服务端在对应真实registry锁/固定slot内，**同时核对key、BusID、认证peer权限和设备状态，之后才claim**。失败不取得设备、不发原始URB，回复受限标准失败。成功回复标准IMPORT并flush，之后该TLS stream直接承载全部URB；不改用明文连接、不另开普通后端listener。DEVLIST和IMPORT是分别认证的两类连接，不把两操作强行拼成一条USB/IP会话。

`rustusb-core`拥有共享DeviceKey/VirtualProfile，支持canonical字符串和有界serde，保留physical/native/virtual命名空间及现精确指纹语法；transport重导出原公共路径，证书/ACL类型留在transport。protocol::secure负责前导/目录codec及客户端管理读写，使用既有标准USB/IP描述符解析，不把未验证JSON当RemoteDevice。

约定公共codec：`read_prelude`/`write_prelude`，`read_catalog_mapping`/`write_catalog_mapping`；类型`SecureOperation::{Catalog,Import{device_key}}`与`DeviceMapping{device_key,bus_id}`。操作各有5秒合作截止与输入上限，写入write_all+flush位于同一截止；应用总发现截止还涵盖DNS、TCP和TLS。IMPORT读取只消费固定回复，不预读并丢弃紧随其后的URB。错误不回传网络输入、证书或私钥。

## Rust engine

新增独立`SecureServerConfig`和`ServerHandle::bind_tls`/`bind_physical_tls`。安全listener只进行TCP→TlsServer.accept→同一generic管理/URB actor；默认loopback，明确配置可监听指定外部地址，不伪造trusted_network=true绕过旧验证。旧明文API/命令继续遵守既有外部安全网契约，不被新模式偷偷启动。

内部ConnectionAccess区分legacy与真实VerifiedPeer。catalog返回private RegisteredDevice{key,metadata}；Virtual在共享State锁中按固定profile核key，USBFS按不可变slot.source.id核key，授权必须先于leased/claiming及supervisor启动。合法但未授权设备不在目录中，也不能用猜测BusID导入。旧public backend直接方法仍按明确legacy API使用，不从网络接受自报principal。

listener先取得16任务permit再spawn，真实TLS stream另保留同server实例16permit到Drop。tokio::io::split的两half都由同一连接任务树持有，网络关闭后USB监督/cleanup继续按现契约收尾；外层任务permit覆盖清理。读帧、写入、半包与cleanup边界不因TLS回退，完整响应write_all+flush共用截止。信号路径保留prepare取消、获得server后总await cleanup及stdout背压语义。

## 可信配置与进程角色

新增同步有界`transport::config::load_server/load_client`，CLI经spawn_blocking调用；内存DER API保持可用。server配置含schema_version、非0 broker_uid、listen、DER CA/chain/key路径、1..16稳定devices池和principal pin/device规则；规则只能引用池内设备。client配置含schema_version、非0 broker_uid、固定server_id、core::Peer endpoint（DNS/IPv4/IPv6）、独立server_name、固定device key及DER凭据。只允许实际broker UID加载；CLI不能覆盖端点、凭据、设备池、ALPN或校验规则。

配置与CA/chain由root管理，所有父目录root拥有且不可被group/other写。DER private key可为root或目标broker拥有的0600 regular文件，父链仍由root保护；root0600对broker不可读时如实失败，不提权或改权限。NOFOLLOW/CLOEXEC/NONBLOCK、fstat、regular/nlink1、有限路径深度/大小及完整读取前后身份检查；读错只给固定字段上下文，不包含秘密内容。不同文件读取不是原子配置重载，本版本配置启动后不可变，修改需合作停止/重启。

专用无登录broker账户与交互allowed_uids分离。root helper新增独立默认空broker角色和安全设备/远端池；broker UID非0且不得与旧allowed_uids重叠，安全池与旧Native/USBFS池不交叉。旧Export/OpenPhysical/Attach不得取得安全池。新安全USBFS授权复用原journal/Finish和实际恢复，不把trusted_network声明置true冒充认证。root仍只信本机SO_PEERCRED角色，不信网络传入的principal；同broker UID的进程属于同一信任域，不宣称命令名提供隔离。

## Native客户端与服务端通道

共享客户端先经mTLS目录找到配置key，再在新mTLS连接完成IMPORT，核真实回复元数据，随后通过专用helper角色创建VHCI通道。新路径独立于旧官方usbip record，不制造localhost身份。

候选daemon API为`prepare_native_import(socket, SecureImportTarget{server_id,device_key,remote}) -> NativeImportHandle`；handle提供一次性`take_relay`、`commit`、`close`及失效通知。helper创建匿名K/R，只通过SCM_RIGHTS交付R；发送成功立即关闭helper的R副本，保留自己K以便精确shutdown。broker先启动R↔已认证TLS泵，再commit kernel K，避免等待枚举与尚未启动relay相互死锁。receipt包含独立channel ID、逻辑remote、原内核socket身份及真实本地代际。

一个持续受保护控制UDS监督prepare/handoff/commit/finish；断开或Drop触发helper自己的K shutdown和实际内核清理，不能按猜测port detach，更不能清理外部替换会话。实际blocking写任务持有FD、强receipt和全局mutation guard到真实结束，取消future不是终止syscall。未确认的副作用保留未决状态；重启不认领来源不明资源。持久记录、只读协调与故障策略须在对应接口启用前固定，不能凭PID死亡/内存清空宣称恢复。

Native服务端还必须提供授权export lease及stub channel：导出前读取真实wire元数据与原基线，导出后预开原kernfs attr并强核代际；commit只确认stub USED，不等待远端VHCI枚举。先写并flush IMPORT成功帧，再开启kernel raw泵，URB不能抢在管理回复前。结束先核AVAILABLE、再按export所有权恢复完整原driver；cfg/IF按已验证Native阶段变化，不与硬件代际混为恒等字段。现单guest机制只支持这些候选，不替代产品两端的生命周期验收。

具体候选API：`prepare_native_export(socket, device_id) -> NativeExportHandle`，handle提供device_key、published_device、open_stub、close和invalidated；`NativeStubHandle`与import handle同样提供一次take_relay、commit、close及失效通知。发布元数据是**导出前真实注册描述符快照**，不是实时actconfig；导出后的实际空配置/空interface及后续USED/AVAILABLE状态独立记录。无法匹配已发布配置或确认完整原driver恢复时保持未决，不猜造描述符或虚构恢复。完整跨配置兼容性仍需单独验收。

Native broker和Rust engine复用protocol::server的管理帧解码与回复编码，避免再复制一套DEVLIST/IMPORT parser；Rust engine的URB actor保持原实现，Native raw URB继续由内核完成。新CLI Native安全服务只依赖secure-transport feature，virtual/USBFS选项分别按原features启用；没有对应实际API的后端不显示成功占位。

## 实施顺序与证据

并行实现共享key/前导、Rust engine TLS/原子ACL、可信配置、helper角色与Native客户端通道；随后连接CLI和Native stub服务端，保持同一最终完整设计，不把先完成的后端缩成最终目标。任何未实现API保持明确proposed，不建立空crate或假的命令成功输出。

软件验证包括真实TLS上的前导+标准DEVLIST/IMPORT/URB、精确设备隐藏与猜BusID拒绝、错key/BusID/重复/恶意前导、flush/超时/配额/EOF/信号与cleanup；特权测试只在可销毁QEMU，绝不写宿主USB/sysfs。最终外链覆盖虚拟/USBFS/Native与实际IPv4/IPv6，抓包区分控制链和外链，验证DEVLIST及IMPORT后真实HID/serial URB均加密、直连3240无旁路、本地非broker不能抢入口。真实硬件、ISO、通用恢复及Phase4–7继续保留原验收要求。
