# opt-in CONNECT-only guardian：最小实现接点与生命周期审查

2026-10-23；代码基线 `c8f2e905fa68ca80a5a707c63a95d652c1f1ac32`，开始时 clean。本审查只读，没有修改产品、运行 Cargo/QEMU/USB 或重新执行机制实验。已读 AGENTS、核心 skill、架构、physical-crash-recovery-design 末两类授权契约、正式 da764d0 机制报告及 nusb 实际实现。这里是候选实现设计，尚未实现或验收。

## 推荐结论

采用 **helper 单一 tracked 后台 coordinator + 恢复时独立重开 B + 一个同步 blocking closure 持有 OwnedMutexGuard + Linux borrowed-FD CONNECT 薄包装**。不构建 nusb::Device，不长期持 B，不 dup A，不抢占、不改配置、不改 alternate、不自动加载模块。该路径可复用已有 USBFS ioctl 包装，不新增 Rust unsafe，也不增加新的依赖版本/package。

权限采用本轮明确的“owner 结束后恢复已授权整台设备原基线”语义，限定同 boot、同代际、同 configuration、完整同接口集和同 alternate。它不同于“只撤销 worker 实际做过的变更”：CONNECT-only 全基线权限不必为每个 claim 建事务日志，但必须在新持久记录中明确授权、受当前策略继续允许，并保留无法辨别管理员主动解绑造成相同 None 状态的边界。旧 schema1 记录永远 manual；不能因新策略 opt-in 就追授其主动恢复权。

## 1. 最小代码接点

| 位置 | 建议的窄变更 |
| --- | --- |
| `vendor/nusb/src/platform/linux_usbfs/mod.rs` + `src/lib.rs` | Linux-only safe borrowed-FD CONNECT，调用既有 `usbfs::attach_kernel_driver`；以 `std::io::Result<()>` 保留真实 errno。不增加 Device/Interface/URB API。同步调用并明确“不验证实际驱动或恢复完整基线”。 |
| `rustusb-daemon/Cargo.toml` | Linux optional workspace nusb，由 physical-usb feature 启用；复用既有0.2.8 path/version，不影响默认 Native 依赖边。不让 root helper依赖整个 Rust USB/IP server。 |
| `journal.rs` | 明确新记录恢复权限/版本；读schema1转换为manual，原记录不自动升级权限。schema2严格字段/原子写/锁/poison边界保持。 |
| `physical_lifecycle.rs` | 将 owner-ended 与 auto严格scope预检做成同步函数；复用当前完整scanner/receipt观察，允许“原Some→当前None”，其余不一致均拒绝。保留原只读 verify，勿让现 ReconcilePhysical 悄然变成恢复操作。 |
| `physical.rs::open_current` | 重用固定路径打开 B 的校验；得到新grant后，其 receipt 必须与持久baseline逐字段相等。这里只是新独立open，不reserve新租约、不交付FD、不重新capture original。 |
| `linux.rs::App` / `serve` | state 改 `Arc<TokioMutex<ManagedState>>`；tracked coordinator、stop通道与唯一in-flight JoinHandle；Finish/Reconcile/Open仍通过同一state锁。明确所有退出分支都stop并join，不只处理正常SIGTERM。 |

薄包装可表达为 `connect_kernel_driver(fd: BorrowedFd<'_>, interface: u8) -> std::io::Result<()>`。它只是现有安全包装的转接，不接受设备路径或任意 ioctl。既有 Setter 返回成功单位值，不暴露 device_attach 的正数0/1；生产日志只能记录实际 Ok/errno，不能伪造实验C probe中的 `rc=1`。成功绑定仍用完整sysfs复核裁决。

## 2. owner监测与 helper 重启

最低复杂度可以直接复用现 `/proc` 观察，单coordinator每个有界周期读取最多256记录：

1. 只挑 durable explicit-auto 且当前策略仍允许的记录；manual/schema1直接跳过。
2. 自动路径先要求当前boot==owner.boot。旧boot不能利用现 `require_ended_owner` 的“boot不同即Ok”分支进入恢复，也不自动退休gone；保留root acknowledge-gone路径。
3. 同一PID/starttime仍存在即等待，不恢复。`/proc`明确 NotFound 或已换starttime表示原进程已结束。读取失败/乱码/权限失败是unknown，保持pending。
4. 现函数把同tuple僵尸也当存在，保守等待其被父进程回收。这符合“owner真正结束”的安全下界；若需要不等回收即恢复，再加pidfd并验证线程组结束，不把单个leader的Z状态视为所有线程结束。
5. pidfd不是第一切片必需项。如果采用现safe rustix `pidfd_open` + Tokio AsyncFd，则须前后夹读boot/PID/starttime，错误仍unknown；它只辅助稳定观察，不替代设备代际或引用退休。

helper启动应先验证策略、打开并锁住Store、成功绑定受保护listener，再启动coordinator并接收请求。避免listener绑定失败却已开始恢复。重启后重新枚举Store即可，不需要持久化B；同boot且原owner已结束的明确auto记录可以继续严格重开流程。开始时还是原owner活着，就继续监测，绝不创建新original。

复用原owner字段隐含 helper 与 `/proc` 继续处于相同PID/UID观察命名空间；跨容器/命名空间重启不在当前host-helper部署证明内，不能靠数字PID推断新部署中的owner。若今后支持该部署，应在新记录绑定观察命名空间身份或明确拒绝。

## 3. 恢复时重开 B 的等价性与真实差别

源 `physical.rs:119` 的 open_current 已有 root固定节点、NOFOLLOW/CLOEXEC/NONBLOCK、node前/open后/sysfs前后校验。调用时用当前scanner结果，但必须再做：

- 记录boot与当前boot相同；device fingerprint、BusId、USB地址、完整接口、安全blockers符合保存基线。
- `grant.receipt == record.baseline.receipt`，包括 sysfs filesystem/inode、node rdev/inode、bus/address全部字段，不只是VID/PID。
- 打开前及打开后各独立完整scope观察；任一变化关闭B并保留原记录。
- 后续每个CONNECT始终使用这个B，而非再按BusId找设备。断开后旧FD仍锚原usb_device，返回ENODEV，不转向替代设备。

在该同代际范围内，“恢复时新open”同样建立独立 usb_dev_state；不弱于已有physical grant的代际强度。与grant时长期持B的差别是：不能保留已拔出设备的旧open，也不保持跨helper进程的B身份。这不减少当前scope，因为gone/replacement本来就不主动恢复。好处是活owner期间无额外USBFS FD/GET_CONFIGURATION/事件线程，每次最多一个短期B，helper重启不用传承旧FD。

receipt仍是现有Linux对象代际观察，不是加密硬件身份，也不承诺inode永不复用或消除外部root热插拔TOCTOU；本设计不降低、不外推现有强度。节点被管理员重建即使物理设备相同，也按receipt不符拒绝，不能为“恢复成功率”放宽。

## 4. 一个真实任务持有整个恢复事务

最小同步阶段，所有权关系应为：

```text
coordinator --owns--> JoinHandle<blocking result>
blocking closure --owns--> OwnedMutexGuard<App.state>
                 --owns--> exact Record clone + short-lived B
                 --locks briefly--> Store stdMutex (read/check or retire only)
```

先异步取得 `state.clone().lock_owned()`；若此时stop可以安全退出，因为尚未提交任何USB工作。实际 `spawn_blocking(move || ...)` **拥有这个guard**，不是只让外层async函数持有它。closure内依次：

1. 短锁Store，重新取得相同lease ID并比较完整Record，确认mode授权；释放Store锁。
2. 查handoffs无该lease，确认helper发送A的FD已drop；重新判断owner已结束且boot同一。
3. 验证当前策略。沿用“策略重启加载”的规则时每次按 `App.policy` 当前生效快照重审，不沿用grant时许可。若产品要求磁盘修改立即禁止恢复，则在此安全重读现root配置并明确恢复路径的reload语义；不应无说明地混用两者。
4. 严格scope预检；若已完整恢复，可以纯观察结案，不必打开B。
5. 未完整恢复才open_current取得独立B，并逐字段receipt核对，重读scope。
6. 每个interface之前先重读**整台设备完整scope**：原driver已匹配则跳过；原Some且当前None才允许CONNECT；原None仅允许当前None；任意别的非空driver（含usbfs/usbip-host）拒绝。不以目标IF看起来空闲掩盖其他IF被C占用。
7. 调一次CONNECT。保留真实errno，随后独立重读整scope。同步probe可能同时绑定siblings，因此每次重新观察，不复用旧driver数组。
8. 仅允许缺失原驱动逐步收敛，configuration、IF集合、alternate、generation始终严格相同。最后两次完整基线匹配；关闭自己的B，确保同步closure没有任何未来USB写操作。
9. 最后短锁Store重新确认Record未变并原子retire。任意观察/策略/USB/持久化失败都保留pending，不能改baseline。

CONNECT返回Ok但仍None，不等于成功。可做有限的只读收敛等待，不重发同一CONNECT。CONNECT错误只有在同scope下实际该原driver已经准确出现、且最终整基线严格匹配时，才可按“实际已收敛”记录；不能笼统吞EBUSY。外部C仍usbfs时必须保留记录，不detach它。

锁顺序保持 `state → briefly Store`，不要在等待state/owner/USB期间持Store stdMutex。这样PhysicalPending/Status仍能读取未决记录。简单复用全局state会让一个卡住的CONNECT阻塞包括Native在内的其他mutation；这是此最小方案的明确可用性代价，不可用提前释放锁掩盖它。未来才考虑更细的per-device状态，而不在第一切片引入大框架。

## 5. 超时、取消、Finish 与退场

coordinator必须保留实际JoinHandle。超时不是任务结束：报告 `timed_out_still_in_flight`，继续保留handle及closure拥有的guard/B，不能再调度该lease、让Finish/新grant穿过，不能删除记录。底层真正返回以后才能释放互斥和决定结果。不要timeout后drop整个operation future、abort blocking handle或启动替代恢复。

普通Finish、root只读Reconcile、Open和guardian全部使用相同state互斥。谁先获得锁谁完成；另一路拿到锁后重新读Record，已退休只返回not_pending。手动Reconcile仍不执行CONNECT。A handoff tracking也要复核；不能因为owner死了就忽略仍在写/发送的helper任务。

helper shutdown：先停止accept，发stop禁止新attempt，join已有普通请求与guardian真实任务。stop在等待owner或取锁阶段可以结束；一旦blocking工作已提交，shutdown必须等其真正返回。所有 `serve` 错误出口也要走stop/join，避免 `accepted?` 提前返回时遗落后台恢复。stop到达两次CONNECT之间可以停止后续动作、保留部分恢复的pending；不能中止正在执行的ioctl。

硬阻塞驱动probe/ioctl仍可能令graceful shutdown和Tokio runtime等待；外层10秒只提供错误/隔离观察，不提供强杀内核调用能力。已持有Store flock的helper不能被另一个helper绕过；helper被强制杀死时不承诺旧内核操作已结束。持久记录和独占锁是拒绝重用边界，不把它们称为FD撤销。

每记录应有明确有限的自动attempt策略。遇到活owner/当前外部占用可只做观察并延后；真正CONNECT失败后不要每250ms无限重probe。建议每helper运行期一次有界mutation attempt，失败后保留原因待管理员协调；helper重启可按新当前策略重新评估同一明确授权记录。这一retry语义须写入产品契约，不能靠loop偶然实现。

## 6. 为什么不建议 helper 使用 nusb::Device::from_fd

它是safe API，但不是零副作用包装：

- `linux_usbfs/device.rs:119–160,569–625`：seek/read全部描述符；from_fd无sysfs路径时执行50ms GET_CONFIGURATION，失败会猜第一份配置。该猜值不能用作恢复授权；严格独立sysfs仍必需。
- `:163–205`：创建timerfd、在全局DEVICES注册Weak、启动/加入nusb-events epoll线程。B独立所以不会reap A，但引入了本任务不需要的事件资源。
- `events.rs:60–91` 与 `device.rs:630–636`：Drop注销epoll并移除Weak，事件线程可能临时升级Arc。没有消费Device并同步取回FD的现成public close API，不能仅凭drop声称所有B引用立即关闭。
- 构造失败在Arc前会随OwnedFd关闭；Arc后常规Err会Drop注销/关闭。但首次epoll创建失败时，Drop的unregister_fd内部会对未初始化OnceCell unwrap，可能把本来Err变成vendor panic；thread spawn也有unwrap。不能对root恢复路径宣称所有初始化失败都仅以Result正常返回。
- `maybe_future.rs:81–167`：`.await`会创建Tokio spawn_blocking任务，drop awaiter不结束实际工作。若选该备选方案，应把整个from_fd.wait/CONNECT/Drop放在外层单个tracked blocking closure，不能层层可取消await。

因此薄CONNECT包装不仅少代码，也避免描述符读取、配置猜测、全局事件线程及上述失败/退场复杂性。已有 `usbfs.rs:88` 的实现保持原样；仅增加公开safe转接，文档、补丁和70文件provenance照现流程更新。

## 7. 必须拒绝与保留的功能

自动拒绝：manual旧记录、当前策略撤销、owner活着/未知、boot不同、handoff仍在途、receipt或fingerprint变化、gone/replacement、configuration/完整IF集合/alternate不符、危险driver/块后代、任意不匹配非空driver、观察错误、CONNECT不收敛、持久化不确定，以及已有真实恢复任务仍未终态。

这不删除正常多配置/alternate能力：现physical worker/server照常运行与合作清理。只是owner死后若当前状态超出明确CONNECT-only权限，就保留旧记录供管理员处理。例如CDC原alt1被最后USBFS release恢复为alt0时，本切片不偷偷SET_INTERFACE；不能拿HID/FTDI通过冒充它已支持。若新driver在CONNECT自动probe中被选中，事后校验会拒绝退休，但不能撤销该probe副作用；现机制实验的固定模块环境不代表生产唯一驱动选择。

## 8. 最小有意义验证建议，尚未执行

- 纯fixture：schema1在新opt-in策略下仍零CONNECT；活owner、boot/receipt/config/IF/alt差异、外部usbfs/其他driver均零mutation；原None保持None。
- 基线逐步收敛：第一个CONNECT同时绑定siblings，后续重读跳过；错误后实际原driver匹配才能收敛；未匹配保留原errno/pending；不调用setconfig/claim/detach。
- 实际blocking ownership fixture：同步栅栏确认closure开始，再取消/超时outer future；Finish、新grant和第二恢复取不到guard，释放栅栏后才完成。测试用可靠IPC，不用短sleep赌时序。
- Store在CONNECT后损坏/retire fsync失败：保留poison与pending语义，不能回报可重新grant。
- helper重启加载明确auto记录，旧owner仍在时等待，旧owner已结束且scope完整时恢复；当前策略禁用时零操作；old schema1保持原管理员路径。
- QEMU产品验收：普通服务实际I/O后KILL，helper自行CONNECT、严格原基线及record退休后再真实I/O；独立C阻止恢复、不抢占；helper重启与拒绝/允许各留原件。继续保留da764d0机制证据和旧FAIL，不替换其归属。

上述验证不是已经运行的结果；没有扩大到跨配置自动恢复、任意FD副本撤销、容器、存储或硬件G1/G2。

## 本次实际源码身份

固定Linux v6.8核对沿用[前轮报告](/tmp/rustusb-guardian-linux-review-20261023.md)与仓库已归档的七份Gitblob身份。下面是本次读到的实际本地文件；不是新构建结果。

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| `crates/rustusb-daemon/src/linux.rs` | 60427 | `97ecd3229eeed5e8191a5f57a756e2326d6aa6100c76ea1e82b005a81bfd6f1a` |
| `crates/rustusb-daemon/src/physical_lifecycle.rs` | 12458 | `ad2c67a46c4e013165ec5f365ae85ca5de1edf070d5543c98d21a7f3912d2f0b` |
| `crates/rustusb-daemon/src/physical.rs` | 14006 | `3e53315a738289671ee2d78d676ccb245cb15e68a155477b402ee7318670e930` |
| `crates/rustusb-daemon/src/journal.rs` | 39018 | `52d4e5fa87863326bc32d6d7eba8b14d4ed27e8199dbf60b4f4a6989b2f21730` |
| `vendor/nusb/src/platform/linux_usbfs/device.rs` | 46376 | `43c46fe259198e942c6b2986f06b290a0fa81feeb62d74add537e43b39e6855e` |
| `vendor/nusb/src/platform/linux_usbfs/events.rs` | 4631 | `41bb15c95986744c66d8c14863c16a8266d144818306e55fe4b41f86365efa46` |
| `vendor/nusb/src/platform/linux_usbfs/usbfs.rs` | 7670 | `14003b5f2f95d6481baa7459f6a1f67a051a36ef22ce64773ce4f1ca1958d011` |
| `vendor/nusb/src/maybe_future.rs` | 8277 | `b0ffdd4b9b6bbd3862bac7cef86b7545a1c0d905b2295ebc0079cfa13878dc3e` |
| `vendor/nusb/RUSTUSB-PROVENANCE.json` | 18409 | `ce9db7b1a06ca38da3dd39d6e105a38e6dba5158f598dd091ac09e72198a9593` |

归档时工作树状态（其他作者可在并行写设计，本审查未写仓库）：

```text
 M docs/work-log.md
?? docs/research/physical-driver-autorestore-design.md
```
