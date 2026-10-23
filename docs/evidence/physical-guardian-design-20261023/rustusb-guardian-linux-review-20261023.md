# 独立 USBFS 恢复句柄 B：Linux v6.8 源码审查

2026-10-23。RustUSB 代码基线 `60be074086a164fdd852119a80c26b6949e7646e`，开始时工作树干净；本审查只读，归档时 root 正在修改恢复设计/工作日志，不把当时文档脏树改写成代码或运行期脏树。没有运行 Cargo、QEMU、ptrace、USB、模块或网络实验。本文是机制结论与可执行实验建议，**不是自动恢复已通过**。

Linux 固定为 `v6.8`，tag object `90d1f30371ae3337beb01666b226320728d35c70` 指向 commit `e8f897f4afef0031fe618a8e94127a0934896aba`。已重新核缓存五份源码的完整 Git blob/长度/SHA256，新读 message.c、usb.c 也逐 Git blob 核对。没有宣称验证 tag 签名。Ubuntu 来宾 `6.8.0-106-generic` 是另外的发行版构建，运行结果须另记。

## 能实际推进的最小路径

B 应是 helper 对同一已核代际字符节点的**第二次独立 open**，不是旧 A 的 dup。普通 guardian 可以持有未包装的 `OwnedFd B`，仅在恢复时通过已有 safe `nusb::Device::from_fd` 包装，避免把 A 的 URB 指针/事件队列带入新进程，也避免过早缓存 B 的 active configuration。网络数据和 USB/IP parser 继续留在普通 worker；guardian 不必整体 root。

第一项机制实验可先验证已有原配置、原 alternate0 的 HID/FTDI：确认旧工作进程终止及已知 A 引用退休后，B 不发 DISCONNECT，不发 DISCONNECT_CLAIM，不为“试占用”额外 claim。对每个原先绑定且当前为 None 的接口，经 scope 前后夹读后 CONNECT，再核完整原 driver/config/alternate/receipt。当前已等于 original 时跳过；任何不同非空 driver 或 usbfs owner 都拒绝修改。原记录保留到独立完整复核成功。原驱动本来 None 的接口保持 None。

这个建议依赖现有的可信服务/受控引用边界，不新增“任意 FD 副本可被撤销”的保证。若要明确掌握最后一次已知 A close 的时机，实验可由 guardian 暂持一个 A 的控制副本 R：worker 被回收后，先关闭 R，再用独立 B。R 只是显式延长旧 A 生命周期的对照/控制引用，不是独立恢复句柄。生产是否采用它尚待设计，不由本报告选定。

## 内核提供的真实护栏

| 操作 | 固定源码链 | 能保证什么 / 不能保证什么 |
| --- | --- | --- |
| 独立 open B | [devio.c:1032–1087](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L1032-L1087) `usbdev_open` | 每次分配单独 usb_dev_state，单独 ifclaimed/async lists；相同节点 metadata 不代表同一 OFD。 |
| 最后 A release | [devio.c:1089–1123](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L1089-L1123)，[660–700](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L660-L700) | 持 USB device lock，释放 A 的 claim，usb_kill_urb 排空 A 的全部异步 URB。没有保存或重绑用户态的 original driver，也不会替 B 清理。 |
| B 普通 CLAIMINTERFACE | [devio.c:779–850](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L779-L850) → [driver.c:538–596](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/driver.c#L538-L596) | 现有任意 driver 都 EBUSY，包括旧 A 或独立 C 的 usbfs claim；不会主动 detach。成功会改变接口归属，所以不是纯观察。 |
| B CONNECT | [devio.c:2316–2370](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L2316-L2370) | 只有未绑定接口才 device_attach；已有绑定 EBUSY。它不能指定 original driver；返回非负也不能代替实际绑定检查。 |
| SET_CONFIGURATION | [devio.c:1543–1589](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L1543-L1589) | 任意接口已被 driver/usbfs claim 就 EBUSY，含 B 自己；即使设同一配置也先检查。全未绑定时才能改变/重置配置。 |
| SET_INTERFACE | [devio.c:1526–1541](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L1526-L1541) | checkintf 会隐式 claim；若外部 driver 已绑定会失败，不绕过 ownership。但这是状态操作，非只读 probe。 |
| 释放非零 alternate | [driver.c:431–517](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/driver.c#L431-L517) | usbfs 没有 soft_unbind，unbind 通常回 alt0；B 临时 claim→setaltN→release 并不能通用保持 None+altN。不得吞实际 release 错误。 |

`usbdev_do_ioctl` 在 devio.c:2596 起取得 USB device lock；`usb_bus_type.need_parent_lock=true` 在 driver.c:2034–2039。这些内核条件使普通 claim/CONNECT/SETCONFIG 的占用检查和其相应动作有实际锁保护，而不是只依赖先前 sysfs 字符串。它们不构成永久的整设备用户态排他租约，也不防 root 在下一时刻主动改配置/driver。

不要用现有 `detach_and_claim_interface_no_reattach` 作为恢复的“防抢”操作：它只通过 EXCEPT_DRIVER usbfs 防止抢另一个 usbfs owner，仍可能 detach 新绑定的其他 kernel driver（devio.c:2460–2493）。B 恢复应使用普通 claim 或 CONNECT，且不忽略 EBUSY。

## A 的完成、B 的边界与外部 C

A/B 的 async list 不共享。B 不能 REAP A：async_getcompleted 在 devio.c:457 起只取给定 ps 的 completed list，proc_reapurb 在2101起沿该 ps 工作；processcompl 在2040起会使用原 userurb/userbuffer 地址。不能用另一个进程中的 nusb wrapper 接管旧 A 的 completion 指针。

还有一个不能用“接口已无 usbfs”掩盖的窗口：异步 EP0 Control 的 ifnum 初始为 -1，devio.c:1615–1687。按接口 destroy_async_on_interface 不包含这类设备级请求；完整 A usbdev_release 的 destroy_all_async 才覆盖它们。普通 B claim 成功只能证明那一接口当下没有其他绑定，不能单独证明所有旧设备级工作已结束。

固定退出链可缩小实际控制模型：kernel/exit.c:866→871→891 依次 exit_files、exit_task_work、exit_notify；fs/file_table.c:425–447 的最后 fput 通常安排当前 task_work，另有 delayed_fput fallback。受控固定 worker、已回收全部工作线程、无未跟踪转交的模型下，可建立已知 A 引用退休顺序；仍不能把一条 PID 消失事件推广为内核所有引用退休证明。挂在不可取消 ioctl 的线程未真正结束时，guardian 只能到期隔离并保留原记录，不能另起恢复任务假装它已返回。

外部独立 C 的 claim 不应通过 driver="usbfs" 被误认作 A；B 的普通 claim/CONNECT 会 EBUSY，SETCONFIG 同样 EBUSY。B 不能调用对 C 的 RELEASEINTERFACE：releaseintf 只测试 B 自己的 ifclaimed bit；未拥有时返回 EINVAL/ENOENT。不要把 GETDRIVER 或 sysfs driver 名字当作 OFD owner/PID 标识。

## 多配置、alternate 和驱动选择的真实限制

保留现有正常多配置/alternate 功能，不把全产品缩成固定对齐 profile。跨进程自动恢复需要补充现 journal v1 尚无的可信变更范围：实际已确认的 claim 集合、原接口 ownership、配置/alternate intent 与 terminal ACK、最后可解释配置。任何进程间记录都要在允许的状态操作之前 write-ahead，返回结果后 ACK；模糊窗口不能伪造完成。若 guardian/helper 自身失败，现有持久记录继续拒绝重用，退回明确管理员协调。

实际恢复次序应是：旧 A 退休 → 同代际/当前配置/安全扫描 → 若确有已授权配置变化且当前全接口未被占用，B SETCONFIG(original) → 重新扫描新配置的完整接口及危险 driver/块后代 → 每个原 IF 重新读取再恢复 → 完整原基线收敛 → 退休记录。不要先 claim 全接口再 SETCONFIG，内核会拒绝；也不要重复设置本已匹配的配置来当“reset”。

`usb_set_configuration`（message.c:1993起）在2186–2214注册接口并触发 probe；probe 可以 claim sibling、改 alternate。CONNECT 同样调用自动匹配，不能锁定 original driver。新注册匹配驱动或 quirk 可能先被 probe；事后拒绝记录退休并不能撤销这个 mutation。第一项 guest 实验可固定只加载已知 HID/FTDI 模块来验证该模型，不能据此承诺生产环境选择唯一原驱动。

固定原 driver 的 `/sys/bus/usb/drivers/<original>/bind` 概念上选择更精确，但它按接口路径字符串工作；预检 receipt 后到写入之间重插可能指向替代代际，缺少 B 的 ps→原 usb_device 锚。与此相对，B 在原设备断开后会经 connected 检查返回 ENODEV，不会被旧 Bus ID 转向新设备。本轮获取固定 drivers/base/bus.c 的 connector/web 均429，且本地无缓存；因此**不对该替代方案声称完成精确 bind_store 源码审计，也不选为本轮实现**。

原非零 alternate 最好由准确原 driver 的 probe 恢复并最终校验；原 None+非零 alternate 的通用释放后保持仍未解决。若实际已经匹配则无需改；若不匹配则隔离，不能 blanket 假成功，也不据一个 CDC ECM probe 成功外推任意设备。

## DROP_PRIVILEGES 不适合作为这一步的变更授权证明

- devio.c:2534–2550 单向缩小 future interface mask；claimintf 的 already-owned bit 在 mask 检查之前，已持有 claim 不会被撤销（779–795）。掩码输入是32位。
- 它禁止有 driver 时的 DISCONNECT_CLAIM，也令 proc_ioctl 的 CONNECT/DISCONNECT 全部 EACCES；因此把 B drop 掉后会挡住需要的 CONNECT，把长期 A drop 掉会影响以后恢复 kernel driver 后再次 IMPORT 的普通 detach 路径。
- check_ctrlrecip 的 vendor 请求直接通过、device recipient/EP0不依赖接口授权（878–934）；SETCONFIG 没有该 dropped 检查；RESET 的 dropped 检查仅针对其他 owner 的当前 claim（1499–1523）。不能把它当作 Control allowlist、配置变更日志或“所有能力已经退休”。

本轮建议不用它来制造一个虚假的窄能力边界。现有服务本就被信任持整设备 FD；实际改进是保留基线/已知操作范围、B 的非 detach 恢复和失败保持未决。

## 下一项最小机制实验，尚未执行

仅两个一次性 guest、固定 QEMU HID/FTDI、普通服务与 guest-only root helper，生产源码仍60be074。不恢复宿主 USB，不开放宿主网络。

1. helper 同代际独立 open A/B，记录 receipt、FD identity 与基线；A 实际 claim、HID按键和35B双向串口正常。B 普通 claim/CONNECT 必须 EBUSY；若测试原始 B RELEASE 则 EINVAL，不影响 A 的实际 I/O。B 的 FD identity 相同不是“独立”的证明，必须结合独立 open 路径与真实不共享 claim/completion 行为。
2. 保留 A 的 dup R 作对照，杀死真实 worker并wait137。R仍在时 B必须busy；关闭R后再做B恢复。B不reap A、不重发旧 URB；记录最后close前后sysfs、真实errno与客户端自主NULL。
3. 原配置/alt0下逐接口 B CONNECT，仅接受实际原driver完整收敛；新服务再真实HID/35B I/O并正常关闭。旧epoch末资源未知就保存未知，不用新epoch0替代。
4. 在恢复前让独立C claim一个接口，或恢复到已存在kernel driver。B的普通claim/CONNECT/SETCONFIG拒绝占用，C的实际I/O继续，记录未退休。禁止用DISCONNECT_CLAIM“让测试恢复”。
5. 多配置/alternate和DROP可作为后续独立机制补充：保留当前正常ECM回归；检查B自己claim时SETCONFIG EBUSY、release→alt0、CONNECT sibling重绑及逐IF重读。不能把这组实验当作已经实现跨进程完整journal/自动恢复。

现有 safe API 已足以覆盖 B 的 from_fd、普通 claim、set_configuration、set_alt_setting、release_checked、attach_kernel_driver；不需要新增 Rust unsafe 或把服务变root。若要测试原始无owner RELEASE/DROP 特性，可加小型guest-only syscall探针，独立记录其授权、源码和返回值，不让该探针进入产品核心。所有门等待和不可取消操作都必须有外层有限截止；截止只隔离/报告，不把底层 ioctl 当作已结束。
