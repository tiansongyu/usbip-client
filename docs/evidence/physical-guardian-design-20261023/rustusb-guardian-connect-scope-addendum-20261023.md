# 补充裁决：先做有明确授权的 CONNECT-only 恢复是否成立

2026-10-23；针对 `/tmp/rustusb-guardian-integration-review-initial-20261023.md`（SHA256 `783641290e21e4789a18ca0eb98acfdd71ee9d169800c8d49a0b7871b8acc076`）的收窄与更正。当前只做机制实验，不实施产品恢复。

## 明确答案

**成立。未来 opt-in schema 2 若在交付 A 前明确持久授予“租约结束后，在同代际、相同 config/完整 alternate 下恢复原 driver”的权限，就可以先实现 CONNECT-only 自动恢复，不必在首次交付中改造每个 claim/control 的 durable Prepare/Complete。**

已有 schema 1 只有原基线和只读退休契约，没有授予 helper 自动 USB 写权限，不能静默升级为主动恢复。schema 2 的新增内容可以先是恢复授权契约本身，而不是完整 mutation log。它要明确是“恢复租约约定的原始状态”，而非“仅撤销已证明由本服务执行的每一项变更”。

我初版把完整跨配置/alternate 恢复的 provenance 要求套到所有恢复，范围过大。Prepare/Complete 应留给后续需要解释、恢复 config/alt 差异的切片，不能用它阻塞一个真正可成功且范围有限的原驱动恢复闭环。

## 最小新增记录与契约

```rust,ignore
enum RecoveryScope {
    ManualOnly,
    OriginalDriversIfConfigurationAndAlternatesMatch,
    // 后续才增加带 mutation checkpoint 的 config/alt 恢复模式。
}
struct RecoveryAuthorization {
    contract_version: u32,
    scope: RecoveryScope,
    owner: Owner,           // helper 捕获，不信任调用方自报
    baseline: Baseline,     // 已有的完整 config/IF/alt/driver/receipt
}
```

scope 由管理员策略与匹配的可信客户端协议共同选择，写入 root 账本并 fsync 后才交付 A。旧记录保持 ManualOnly；管理员显式迁移/授权必须独立记录，不能靠升级二进制的默认值改变历史权限。B 与 A 独立 open；helper 不保留 A 的额外引用。

无需每操作日志的原因：本模式不根据当前不同配置决定“切回哪一个”，不对任意 alternate 选值，也不解除已有 claim。唯一允许的写动作是对该完整原基线中的空接口请求 CONNECT；允许目标是 immutable baseline 中已有的 original driver 绑定。原 driver=None 的接口不调用 CONNECT。

A 可以继续使用现有多配置/alternate 功能。如果崩溃后的实际 config 或任一 alternate 与 baseline 不同，这个恢复模式只保留记录供人工协调；这不是关闭功能，而是声明自动恢复覆盖范围。若 A 已正常切回并确认到原 config/alt，最终状态满足门槛，则不用回溯全部历史就可以尝试 driver-only 恢复。不能据此宣称修复了跨配置/alt 崩溃恢复。

## CONNECT-only 的具体执行条件

1. 有明确的 v2 有限恢复授权，journal/锁健康，当前管理策略允许；原 owner 的 boot/PID/starttime 被独立确认终止，所有已知 A 交付引用已结束。进程仍活着、阻塞线程未结束或身份读取不确定时不写。
2. 用旧记录核验同 boot、sysfs/node receipt 和完整设备安全观察；无新 generation、存储或未知接口。不能重新扫描并覆盖 original。
3. **整个设备** current config、接口集合及所有 alternate 必须等于原基线。每个 current driver 只能是原 driver 或 None；任何 usbfs、usbip-host 或不同 driver 都拒绝整次主动修复。
4. 对 `original.driver=Some(D), current.driver=None` 的接口，先重新核对上述范围，再调用 B 的 CONNECT。对 `current.driver=Some(D)` 跳过；对 `original.driver=None` 不写，且仍要求它最后为 None。
5. 不用 CLAIM/RELEASE 作为生产恢复的前置探测。虽然本轮机制实验要测试这些 ioctl，但 RELEASE 可能重置非零 alt，不能混进“只 CONNECT”的承诺。B 不调用 DISCONNECT、DISCONNECT_CLAIM、RESET、SET_CONFIGURATION、SET_INTERFACE 或 sysfs unbind/bind。
6. 每次 CONNECT 后重新读取完整 IF/alt/driver，处理 driver probe 同时绑定 sibling 的事实。EBUSY 不等于成功；只有完整状态实际符合原基线才可继续/接受。失败不重试抢占。
7. B 的全部同步操作返回、已知任务结束、B 关闭后，再用现有独立完整 baseline 逻辑核验并退休。报告区分 attempted、verified、retired；任一步失败均保留 pending。
8. 必须接着完成新授权、客户端枚举与真实 I/O，才宣称有限自动恢复通过；不能只新增拒绝状态。

CONNECT 的内核条件检查解决的是“接口当前已有 driver/usbfs 时不会被它抢走”，不解决全系统 FD 撤销。A 的未知副本不属于当前可信服务契约；机制实验特意证明 A 副本维持 claim 时 B 会失败。不能从该拒绝反例推出所有受限恢复不可能，也不能从成功 CONNECT 推出没有任何 EP0/设备 FD 存在。

## 两个必须说明的不可辨识反例

### 1. 空接口不携带“谁解绑”的历史

相同 baseline 的两条历史可能有完全相同的 schema1/终态：

- A 真正 IMPORT、detach/claim，随后 KILL，最后 close 留下 driver=None。
- A 拿到授权但从未 IMPORT；管理员因维护故意把原 driver 解绑，随后 A 结束。config/alt/receipt 仍与 baseline 相同，driver=None。

仅靠 baseline 无法分辨。这是否阻止恢复，取决于授权语义：

- 若承诺“只撤销本服务实际实施的更改”，不能猜。至少要有持久的开始使用/接口范围材料，而一般的 Prepare 也不能证明之后相同的外部 unbind 没发生。
- 若明确承诺“整设备租约结束时恢复原基线的驱动绑定”，这两者都落在事先授予的恢复范围；管理员做 out-of-band 维护前须先禁用/协调该租约的恢复。该管理前提需要文档和策略表达，不能宣称检测了所有外部意图。

因此“未知解绑原因”不是所有 guardian 的绝对禁止条件，而是契约选择。现有 trusted-service + 管理员受管环境允许第二种有限契约；任意并行 root 操作不在可强制排他的范围内。

### 2. CONNECT 不能指名原 driver

baseline 是 driver D，当前为空；如果期间有另一匹配 driver E 被注册，CONNECT 可能由内核 probe 绑定 E。内核不会因为我们记着 D 就只选 D；事后失败判断也不能撤销已经发生的绑定。

有限恢复必须说明它**尝试触发正常 driver probe，并且只在最终确实恢复 D 时判成功**。不吻合就保留记录且不主动解绑 E。若要求“任何一次自动动作都绝不可能激活非原 driver”，CONNECT API 本身不满足，需要额外限制驱动环境或选择更严格的绑定机制并单独审查。当前固定 QEMU 模块环境适合验证成功路径，不代表任意生产设备都能保证选中 D。

另外 driver probe 自己可能改变 alternate；CONNECT-only 是用户态仅发这一个类别的命令，不是承诺内核 probe 没有内部副作用。最终 full-baseline 检查不可省略。

## 最小成功与失败范围

首个生产成功目标可限现有 HID/FTDI 原 config1/alt0：真实活动请求后 KILL，helper 仅用 B CONNECT 恢复 usbhid/ftdi_sio、独立验证后退休，并完成下一轮 KEY 与精确双向串口 I/O。helper 重启时 A 尚存活应拒绝写；A 结束后同 v2 许可仍可驱动这一有限恢复。无需为此先改动每个配置/alternate 操作。

必须有 driver占用、代际替换、旧schema1、owner仍在、config/alt不符及CONNECT错误的负向，不执行抢占。ECM 的 alt1 若在最后 release 后已变为0，本模式会拒绝；该结果不能被写成 ECM 自动恢复支持。后续跨配置/alt切片再引入可信 mutation checkpoint，并解决非零alt释放重置问题。

当前仍优先运行独立机制实验：它不改变 schema1、仍由 root 测试 probe 做恢复，然后走旧只读 reconcile；其正向证据不能宣称产品已经自动恢复。
