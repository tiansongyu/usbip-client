# Rust USBFS 物理后端实施设计

2026-10-22，设计基线 `037a9c7`。状态：实验性实现与软件/QEMU子步骤已验证，干净37bc6b4最终记录见[专项报告](physical-qemu.md)。原完整需求与真实硬件 G1/G2 保留；QEMU 模拟设备经过真实 usbfs 用户态接口可验证 adapter，但不改写成物理硬件验收。

## 路线与固定依据

继续复用唯一的 USB/IP wire/parser、listener 和会话 actor，新增真实用户态设备 adapter。Native `usbip_host` 路线独立；同一设备不能同时由两条路线控制。可选 `physical-nusb` feature 才引入 nusb，默认 Native 和原虚拟功能不承担物理依赖。

选择 [nusb 0.2.8](https://github.com/kevinmehall/nusb/tree/c80218d342740321190436bc45304ebbbf16bef0)，缓存crate的vcs为c80218d342740321190436bc45304ebbbf16bef0，MIT OR Apache-2.0。它已有异步usbfs提交/取消/回收和安全OwnedFd入口；rusb同步接口会在错误时丢失partial长度，不能以spawn_blocking+timeout冒充取消。nusb公开便利API同样有缺口：Control先检查错误再返回数据、OUT不返回actual；公开Endpoint要求IN长度为MPS倍数且只提供cancel_all；Drop control只放弃观察，未立即discard。Linux错误类别也被折叠，不能正确重建原USB/IP errno。

因此维护固定完整许可的 `vendor/nusb` 小扩展：仅Linux safe API暴露raw Control handle的cancel和真实terminal、原status/actual/data；Endpoint raw提交原长度（含0/1/15）及SHORT_NOT_OK/ZERO_PACKET，raw回收保留errno。复用原有usbfs底层，不在项目核心重写unsafe ioctl或复制GPL。原跨平台API不改变；每项差异和源码哈希单独登记、测试。不降级到旧版本逃避约束，不roundup后私吞额外字节。

## 统一会话契约

内部 `ServingBackend` 提供可用列表和异步claim，`SessionLease` 提供start、cancel-safe事件poll、取消请求、开始关闭和cleanup结果。公开虚拟bind保持兼容，物理bind委托同一engine，不另复制网络实现。活动表覆盖Control、IN和OUT；wire seqnum只用于关联，内部ID独立递增，回复上下文不复制OUT payload。

物理每个端点先保持一个真实inflight，其余进入有界FIFO。queued UNLINK可直接取消；active UNLINK须等待真实内核终态。取消赢才回复-ECONNRESET且不回原RET_SUBMIT；完成赢则保留原completion（含错误/partial）并回复UNLINK0。取消不是回滚，OUT可能已有部分字节到达设备。端点地址与描述符映射经过验证，不能把任意索引直接传到底层。

预算：16连接、64请求/session、64KiB/transfer；物理缓冲额外有全服务16MiB上限，覆盖排队、内核inflight、取消待回收及待回复数据。清理/取消信号使用独立通道，满队列也必须可执行。IN使用原请求长度；失败仍保留实际数据长度。ISO明确不支持，不生成假成功。真实Composite/alternate/configuration能力按已实现路径逐项验证，不由HID/FTDI自动推出。

## 权限与设备代际

普通server不直接接受任意设备路径，不默认root。新增可选helper操作按root策略的`physical_device_ids`校验UID、精确fingerprint、当前全部接口/配置安全和设备代际后，只打开固定`/dev/bus/usb/BBB/DDD`字符节点；拒绝storage/hub/unknown、usbip-host/usbfs已有占用、不明驱动状态。节点和sysfs的dev/inode、地址及设备描述前后匹配，使用NOFOLLOW/CLOEXEC并校验字符设备号。

经受保护UDS和SCM_RIGHTS传递一个OwnedFd及receipt；客户端验证helper root身份、帧/FD数量和截断标志。复用rustix安全ancillary接口，不新增核心unsafe。该FD授予整台已授权设备的写能力，不冒称逐请求沙箱或可即时撤销的授权。策略物理ID与Native导出ID必须不相交，避免本工具两路径交叉控制；更换策略需管理员重启。已有FD的权限撤销和持久租约属于后续安全会话范围，不能声称完整远端授权已实现。

server从该FD初始化nusb，校验实际descriptor/速度/地址与receipt；仅IMPORT时claim全部已验证接口。缓存原driver和代际，部分claim失败必须释放已取得接口。默认不RESET，不重新打开BusID复用的新节点；拔出后旧slot变Gone，重插需要重新显式授权/注册，不继承旧generation。

## 所有权与关闭

slot状态至少Available/Claiming/Leased/Closing/Quarantined/Gone。设备和真实请求归有界监督器所有，网络lease只是能力；EOF、写失败、Drop、shutdown只发起关闭，不能马上标Available。依次请求cancel、收齐terminal、释放接口、独立核对原driver恢复；nusb Drop中的日志不是恢复证明。仍在底层阻塞的初始化/配置调用不能因future超时而被视为结束。

shutdown等待有截止；超过截止返回明确未清理/隔离状态，监督器仍保留资源并拒绝再次IMPORT。设备真实消失作为Gone报告，不要求不存在的设备恢复driver。无存储自动恢复或自动重新授权。正常请求与取消竞态均保留真实errno/actual，不能把未知错误变成空成功。

## 验证次序

1. 库扩展先编译与纯内存状态/字节测试，列出原API缺口和精确diff；不调用宿主USB。
2. 共享engine用受控异步fake adapter验证R06 errno/partial、R07单线程runtime响应、queued/active Control/IN/OUT取消两种胜出、写失败/EOF/Drop和cleanup quarantine；重跑原虚拟tests/probe。
3. helper测试严格schema/UID/物理ID/两后端互斥、FD数量/标志/代际/node类型/路径/TOCTOU拒绝。不向未授权UID发FD。
4. 两隔离QEMU：server真实usbfs/nusb读取QEMU键盘与FTDI，普通UID/零capability；client由helper/VHCI/usbhid/ftdi_sio枚举。两设备并发、两周期真实QMP按键down/up和每方向精确35B、busy/UNLINK/EOF、全部URB回收及原driver恢复。FTDI保留USB包状态头，不作虚拟echo；等待tty配置/reader后才注入数据。
5. 热拔一设备时另一设备继续I/O，记录QMP DEVICE_DELETED和sysfs消失；重插不自动继承授权。QEMU具体速度/EP0来自设备，不能复用纯虚拟FullSpeed/64B假设。
6. 默认/all-features完整Cargo gates、严格rustdoc、许可证清单、既有四平台CI；冻结clean commit后最终QEMU与原虚拟场景复跑，失败原样保留，合适commit并普通push。

依据：[nusb固定源码](https://github.com/kevinmehall/nusb/tree/c80218d342740321190436bc45304ebbbf16bef0)、[Linux6.8 usbfs权限/提交实现](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/devio.c)、[公开USB/IP协议](https://docs.kernel.org/usb/usbip_protocol.html)。Linux仅参考协议和行为，不复制其GPL实现到MIT核心。
