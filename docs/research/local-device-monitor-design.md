# 本地设备热插拔观察服务

2026-10-24，起点 clean `43f3ff4`，状态：**proposed / 实施中**。补齐原需求4.1/4.3和后续前端共用的本地设备观察；不改变设备授权、导出、会话或恢复。上一轮双栈修复及发布是实际进展；原Phase0–7和硬件gate保持。

## 现状与选择

`rustusb devices`只调用一次SysfsDiscovery；共享service没有本地设备观察API。Linux scanner已经有长度/遍历上限和存储保守blockers，不能重新编写一份较弱的枚举器。Bus ID是地址，现有fingerprint是包含控制器/端口拓扑的候选身份，也不能证明相同硬件或同一插入代际。

采用锁定rustix1.1.5的安全NETLINK_KOBJECT_UEVENT group1 socket作为**重扫提示**，实际状态继续来自原scanner。socket先bind再做初始扫描；所有USB action（含interface和driver变更）都触发有界debounce后的重扫。另有固定周期协调，以处理通知丢失、kernel add早于用户态初始化和没有相应通知的属性变化。周期协调是补偿，不能替代实际事件接收或声称捕获所有瞬时插拔。

| 组件 | 决策与固定依据 |
| --- | --- |
| 内核来源 | Linux v5.15/v6.8 `lib/kobject_uevent.c`与`net/netlink/af_netlink.c`：group1，内核portid0，NONROOT_RECV。只接受recvfrom返回的真实来源pid0/groups1，不信payload中的身份 |
| socket/等待 | rustix1.1.5 `net::{socket_with,bind,recvfrom}`和`event::{poll,PollFd,Timespec}`；CLOEXEC/NONBLOCK、固定buffer、TRUNC/ENOBUFS显式失连续性 |
| 运行时 | 独立实际blocking owner，safe poll同时等待netlink与私有唤醒通道；不在Tokio worker执行扫描/阻塞poll。保留真实join及worker配额至工作结束 |
| 状态发布 | 复用Tokio1.54.1 watch的有界最新快照，慢订阅者只保留有限metadata；revision跳号要求重新同步，不假装完整事件历史 |
| 扫描 | 复用SysfsDiscovery，增加只读sysfs dev/inode和USB bus/address观察戳；不是PhysicalDeviceReceipt、root证明或授权能力 |

研究按writing-technical-design明确要求委派两个只读组件任务，原件先保存在`/tmp/rustusb-hotplug-{udev,netlink}-research-20261024`；最终归档只用显式清单，排除probe target/ELF。沿用仓库目录和用户约定，不额外生成逐组件/global skills或自动接受ADR。

## 未采用的方案

- udev0.9.3：MIT，默认monitor非Send/Sync，iterator把接收错误压为None；可在同线程立即读errno，但依赖额外libudev及特定适配。udevd处理后的group2在最小QEMU未必存在。只读探针确认monitor/线程生命周期，未注入宿主事件。
- tokio-udev0.10.0：进一步隐藏接收errno，不能直接作为loss-aware来源。
- Tokio1.54.1的旧AsyncFd::new实际编译报告deprecated/unsound；替代register要求unsafe，与核心约束冲突。不能仅为通过构建放宽lint。
- 现有nusb hotplug：监听udev group2，只add/remove usb_device，丢接收错误；Native不应因此引入实验nusb依赖。
- 仅定时轮询或仅处理event payload：前者没有事件来源，后者不是当前设备权威事实，均不满足本设计。

固定上游依据：

- [Linux v6.8 kobject事件](https://github.com/torvalds/linux/blob/v6.8/lib/kobject_uevent.c)。
- [Linux v5.15 kobject事件](https://github.com/torvalds/linux/blob/v5.15/lib/kobject_uevent.c)。
- [rustix1.1.5 net](https://docs.rs/rustix/1.1.5/rustix/net/index.html)及[event](https://docs.rs/rustix/1.1.5/rustix/event/index.html)。
- [udev固定monitor实现](https://github.com/Smithay/udev-rs/blob/ba521299ca8462e4b09df577fc46fbcacb439c87/src/monitor.rs)。

## 所有权与公共契约

新增实际`rustusb-discovery`应用观察库，由`rustusb_service::discovery`原样re-export；CLI一次扫描及`devices --watch`直接调用同一库，未来TUI/GUI使用同一API，不解析CLI子进程。Native CLI不因此启用整个可选TLS/job服务依赖。它观察当前OS用户能读取的本地sysfs，不代理broker权限，不新增宽权限agent RPC，不接收任意设备写操作。

```rust,ignore
pub async fn scan() -> Result<Vec<Device>>;
pub struct DeviceMonitor { /* actual worker, cooperative stop, latest view */ }
impl DeviceMonitor {
    pub async fn open() -> Result<Self>;
    pub fn subscribe(&self) -> DeviceSubscription;
    pub fn request_stop(&self);
    pub async fn shutdown(self) -> Result<()>; // actual accepted worker joined
}
pub struct DeviceSubscription { /* bounded watch receiver, last revision */ }
impl DeviceSubscription {
    pub async fn next(&mut self) -> Result<DeviceUpdate>;
}
```

进程全局至多两个本地发现实际worker，非等待准入；每个monitor整个运行期持有配额，one-shot扫描配额持于实际blocking闭包。取消awaiter或丢句柄不能提前归还。Drop只请求合作停止；显式shutdown必须await原worker。scan/read永久OS阻塞不能假取消；此时保留真实工作/配额，不能以超时假称退出。观察服务不持USB FD、helper capability或业务WAL锁。

每个monitor有独立instance与单调revision；订阅者首读或跳过revision必须按完整快照重新同步。元数据快照明确Initializing/Current/Stale；扫描失败保留上一成功快照及固定错误码，首次失败没有伪造的空设备列表，不输出Removed。成功空快照才代表本次扫描未见设备。恢复后重新同步。快照不是原子内核事务，当前设备还可能在输出后改变。

观察戳至少包含同一次扫描前后吻合的sysfs dev/inode及USB bus/address；变化中的扫描重试，不发布混合代际。fingerprint、Bus ID和观察戳分别表达候选持久身份、当前地址和当前观察代际，均不授权复用。比较可区分Added/Removed/MetadataChanged/GenerationChanged，不能声称知道一次变化的物理原因；同型号无serial替换仍有固有限制。

## 边界与流控

- 数据报固定16KiB，字段/数量有界；先核实际来源，再解析。非USB事件只忽略，不因全局SEQNUM跳号声称丢包。
- 每轮最多64个数据报，stop/wake优先；50ms debounce及至少100ms扫描启动间隔有最早截止，不因持续事件推迟到永远。周期协调2s；socket收缓冲请求64KiB并记录实际值，拒绝异常超大结果。
- 截断、ENOBUFS或真实来源的畸形消息要求重新同步；不保留payload/原始环境，也不记录潜在serial/path。其他真实I/O错误保留上下文，不能转成空目录。
- 快照最多256设备、单设备编码48KiB、合计4MiB；超过限额报告Stale/容量错误，不截掉设备冒充删除。原scanner保留自身遍历/属性限制；这些界不声称限制内核/OS内部资源。
- 最新快照是metadata，不保留全部USB事件。订阅者持有多少返回Arc由其调用代码负责；服务内不为每消费者无限排队。

CLI保持原`devices [--json]`一次扫描兼容，新增`devices --watch`。JSON流按snapshot_begin/device/device_change/snapshot_end分帧，每帧小于现有64KiB输出上限；只有完整end才提交这一整批，退出/输出中断的partial不能当空快照。首次/跳号/stale恢复标明resync。默认文本显示观察变化，JSON设备元数据可能含serial，与原显式devices JSON一样只输出给调用者；不复制到tracing或文件日志。

信号在取得monitor前注册；stdout慢/关闭/错误不能阻止monitor合作stop及原worker实际join。沿用有界EventOutput，不重发仍在途stdout，不用末尾输出成功证明扫描退出。重复signal只sticky stop，不能丢已开始scan/join。

## 验证计划与未完成项

1. 字节parser实际来源/截断/边界、非USB/interface/actions、错误/丢失及有限drain；fixture scanner观察戳/地址复用/读取失败/不混代。
2. 服务差异/首快照/慢订阅跳号、失败保旧、不假删除、容量与恢复；真实blocking门证明取消不释放配额且shutdown等实际完成；真实socket/wake/close不等于hotplug通过。
3. 实际CLI JSON整批、人类输出、INT/TERM/双signal、真正stdout背压/关闭，实际wait/worker退出。完整default/all-feature fmt/Clippy/tests/release/rustdoc与Native-only依赖隔离。
4. 新干净QEMU普通UID观察真实内核USB add/remove/reinsert、同候选新代际和驱动/接口变化，原始通知与快照分账；真实kernel事件不能用发送伪数据报或fixture代替。另按scanner变更范围保留原真实USB重定向/热拔回归。
5. 记录全部失败、来源/ELF/命令/清理与未运行项，正常commit/push；未验证前不修改支持矩阵为已支持。原硬件G1/G2、wire-DNS/VPN、完整G3、TUI/GUI/安装/性能/Windows继续未完成。

## 首次实现检查后的结构修订

实际Linux测试与service观察定向测试通过，默认CLI编译101：原rustusb-service只在secure-transport启用，默认入口不能直接引用它。不能为修复编译而移除默认watch功能，也不应无条件把TLS/job依赖带进Native。将已有完整观察实现移至职责明确的rustusb-discovery库，service原样re-export、CLI直接依赖同一实现；不是新增空crate或第二扫描器。新增内部包不引入外部crate版本。

源码复核另发现：stamp读取某属性NotFound时，只能在设备目录本身也已消失的情况下略过；目录仍在但devnum/busnum缺失必须拒绝完整扫描，不能伪报Removed。先增加实际回归复现，再修正这一判断。

编码复核：最多512条变化若集中于begin帧会超过既有64KiB上限；变化改用独立device_change帧，begin/end给定计数。丢失分别给定QueueOverflow/Truncated/Malformed固定原因计数，不把来源未知的非USB跳号当丢失。初次全目标Clippy101（manual_map/collapsible_if）保留后按style修正。
