# USBFS 原驱动自动恢复：实施设计

2026-10-23，产品基线 `c8f2e90`；状态：实施计划，尚无本轮产品验收。上一轮是实际进展：[独立B/dupA/C机制](usbfs-guardian-mechanism.md)已通过，但测试probe不是产品恢复服务。本轮目标是在现有helper中实现明确授权的异常退出恢复，并完成无需guest管理员恢复命令介入的真实QEMU复用闭环。完整项目及跨配置恢复仍按原需求推进。

## 授权和兼容

- root策略新增 `physical_connect_restore_device_ids: Vec<String>`，默认空；最多1024条、严格设备ID、拒绝重复，必须为 `physical_device_ids` 的子集。原导出许可本身不包含自动恢复许可。当前有效策略继续采用既有“修改后重启helper”规则，不新增热重载；编辑文件不是即时撤销，已交付A也不可撤销。管理员改变设备用途前应停止helper并等待已有恢复操作真正结束。
- 新授权时记录不可变的 `RecoveryAuthorization::{ManualOnly, ConnectOriginalDrivers}`。后者只授权恢复已保存基线中原本非空、现在为空的driver，且原配置、完整接口与所有alternate必须始终一致；禁止SET_CONFIGURATION、SET_INTERFACE、reset、claim、detach、unbind或接管外部owner。该范围不能以后悄然扩展为跨配置恢复。
- 私有磁盘schema1仍按原严格DTO读取，内存一律为ManualOnly。schema2的每条record必须显式含recovery，缺失/null/未知值/未知字段均拒绝；header分派和具体record解析均有界，不经无界Value树。新目录全manual继续写schema1；首次新opt-in授权在交付FD前原子升到schema2，旧record显式manual，升级后不自动降回1。旧记录绝不因新策略补授权限。
- 保留OpenPhysical的durable lifecycle协议版本1、旧grant、Finish、只读Reconcile和正常服务功能。该协议号不等于私有磁盘版本。Pending用旧形状record DTO；schema2时可增加当前页的 `storage_schema_version` 与 `recovery_authorizations` 摘要，仍计入同一64KiB响应预算。Status明确区分legacy协议与新恢复能力。

## 恢复任务的所有权

helper维护一个跟踪、可合作停止的后台任务，周期性取得有界journal快照。它只考虑有持久ConnectOriginalDrivers许可的记录，并重新核对当前helper有效策略中的owner UID、trusted_network、设备导出及恢复许可。

每次真正执行恢复前取得helper原有变更锁。将其改为Arc Tokio Mutex，并让**实际spawn_blocking闭包拥有OwnedMutexGuard**，从选择record、owner/receipt检查，到全部CONNECT、最终核验和退休始终串行。等待future超时或调用方消失不能释放该锁、启动第二次写入或把设备标为可用。journal文件锁保护仍独立保留；不跨USB调用持有其std Mutex，使只读pending诊断仍可读取未决记录。

owner仍存在（含同tuple僵尸尚未回收）、handoff未结束时只延后，不进行USB操作。自动路径要求同boot，不能借用管理员require_ended_owner中的“boot变更即结束”分支去操作或自动退休旧记录。owner确已结束后每条lease每个helper实例最多执行一次恢复尝试；失败保留pending并记录有上下文的原因，人工只读协调或下一次明确helper重启再按当前权限判断。外部owner/未知状态不以自动detach或盲目重试解决。

关闭时停止安排新恢复任务，join实际已开始的任务后才释放helper资源；不abort正在USB ioctl中的blocking工作。长时间调用可以报告仍在等待，不能把计时器到期当作内核操作已终止。不可取消的内核硬阻塞仍为已知限制，不声称实现了进程隔离/能力撤销或严格有界退出。

## 独立B和受限算法

本轮在真正需要恢复时重新独立open B，不保留A的副本，也不为每个活grant常驻额外B。先核同boot、原owner已结束和完整当前状态；通过已有 `physical::open_current` 打开root控制节点，要求返回receipt与持久基线逐字段相等，再重读完整状态。此选择保留当前receipt强度，设备已消失/重插或不可确定时保留记录、不打开替代代际、不自动acknowledge-gone。

1. 原基线已完整恢复时只做独立完整复核，再退休；不发送多余CONNECT。
2. 否则要求同device ID、Bus ID、节点/sysfs receipt、配置、完整接口number/alternate；原driver为None的接口必须仍为None。原driver非空的接口仅允许现在为None或同一原driver。任何usbfs、usbip-host、不同kernel driver、存储/块后代或扫描不完整均拒绝整次写入。
3. 对当前缺失的原driver逐接口CONNECT；每次前后重新观察**全部**接口和代际，避免同一driver probe已恢复sibling后重复操作。观察到不允许的变化立即停止，保留原记录；不通过unbind“回滚”新出现的驱动。
4. CONNECT由内核选择匹配driver，不能指定原driver；错误只在独立观察已满足预期时收敛，任何不同driver都保留未决。不得把非负返回代替完整原基线恢复。
5. 关闭本次独立B，再进行严格完整基线复核，成功才从journal退休。记录、owner和原基线不被新观察覆盖。新授权继续走原完整安全检查并生成新lease。

这些检查不证明全系统不存在其他设备FD或EP0请求，也不隔绝外部root的并发操作。依据仍是明确的整基线恢复授权与获授权服务的可信使用约定。它不能分辨“服务解绑”与“管理员故意解绑”的相同终态；管理员须按上述停止/协调规则撤销自动恢复用途。

## safe调用与模块接点

不使用nusb::Device::from_fd作为恢复包装：它会读取descriptor、发送GET_CONFIGURATION并可能猜配置，还会创建timerfd和事件线程引用。改为在固定vendor/nusb暴露一个Linux-only、借用已验证FD的safe CONNECT薄入口，直接调用已有usbfs::attach_kernel_driver；不新增unsafe或其他ioctl。接口明确为同步阻塞调用，保留原errno，不提供完整恢复或FD身份保证。同步更新既有补丁说明、精确diff、来源清单和依赖边；daemon仅physical-usb feature在Linux依赖已有固定nusb版本。

- `journal.rs`：严格双版本DTO、RecoveryAuthorization、Record.recovery、Snapshot及sticky原子写入；原Store操作保持。
- `physical_lifecycle.rs` / 新 `physical_recovery.rs`：严格同boot/owner与完整状态观察、允许差异判断、独立B及CONNECT序列；纯逻辑与注入观察测试验证拒绝/顺序。
- `linux.rs` / Policy：新授权许可、旧IPC投影、单后台任务、OwnedMutexGuard、handoff协调和有界诊断。正常Finish和管理员Reconcile继续只读。
- CLI/后端worker的正常配置、alternate、信号和cleanup路径不为此删减能力。

## 必需验证

软件覆盖：默认策略/子集/重复ID；旧schema1保持manual、原子升级/重载/缺字段/损坏与不降级；IPC旧形状和v2分页预算；活owner/boot变化/代际替换/完整接口或alternate变化/外部driver拒绝；仅缺失原driver可恢复、sibling绑定后重观察、错误与部分进展不退休；实际blocking任务持锁、停止不丢任务。执行完整 `scripts/check.sh`，包括项目要求的fmt、Clippy、test和release及严格rustdoc。

新QEMU正向不复用原journal“必须不恢复”的裁决：只复用双guest网络、合成HID/FTDI、I/O和采证基础，使用独立正向verifier。至少验证普通服务真实I/O后一次KILL/wait137→helper自主恢复原基线并退休→新grant真实I/O，以及helper重启时owner活着不恢复、其后owner结束可恢复。成功路径不能运行测试probe的CONNECT、sysfs bind或管理员reconcile代替产品。原默认策略journal184断言作为独立回归，保留旧记录/未授权/外部占用负向，以及原正常、热拔、ECM和信号回归的适用范围。

测试实现、源码冻结、失败、修复、实际版本与未完成场景分别记录；未取得真实双端硬件证据仍不完成G1/G2。
